from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .dispatcher import DeterministicEventDispatcher
from .domain import (
    BarClosedEvent,
    DomainEvent,
    EventType,
    FilterResult,
    InstrumentStatus,
    LevelsResult,
    SignalResult,
    primitive,
)
from .engine.models import CandidateResult, StrategyEvaluation, StrategyRequest
from .engine.strategy import StrategyEvaluator
from .repository import SQLiteProjectionRepository


class CaseInputLoader(Protocol):
    def load(self, case_id: str) -> StrategyRequest: ...


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    source_case_id: str
    idempotency_key: str
    replayed: bool
    events_created: int


@dataclass(frozen=True, slots=True)
class EvaluatedProjection:
    bar_event: BarClosedEvent
    filter_result: FilterResult
    signal_result: SignalResult
    levels_result: LevelsResult | None
    levels_results: tuple[LevelsResult, ...]
    status: InstrumentStatus
    evaluation: StrategyEvaluation


class WalkingSkeletonService:
    def __init__(
        self,
        evaluator: StrategyEvaluator,
        case_loader: CaseInputLoader | None,
        repository: SQLiteProjectionRepository,
    ) -> None:
        self._evaluator = evaluator
        self._case_loader = case_loader
        self._repository = repository

    def process_case(self, case_id: str) -> ProcessingOutcome:
        return self.process_request(self._load_case(case_id))

    def process_request(self, request: StrategyRequest) -> ProcessingOutcome:
        evaluated = self.evaluate_request(request)
        events = self._event_trace(evaluated)
        created = self._repository.persist_projection(evaluated.status, events)
        return ProcessingOutcome(
            source_case_id=request.case_id,
            idempotency_key=evaluated.bar_event.idempotency_key,
            replayed=not created,
            events_created=len(events) if created else 0,
        )

    def process_cases(self, case_ids: tuple[str, ...]) -> list[ProcessingOutcome]:
        return [self.process_case(case_id) for case_id in case_ids]

    def evaluate_case(self, case_id: str) -> EvaluatedProjection:
        return self.evaluate_request(self._load_case(case_id))

    def _load_case(self, case_id: str) -> StrategyRequest:
        if self._case_loader is None:
            raise RuntimeError("no direct case loader is configured")
        return self._case_loader.load(case_id)

    def evaluate_request(self, request: StrategyRequest) -> EvaluatedProjection:
        evaluation = self._evaluator.evaluate(request)
        if (
            evaluation.data_status != "READY"
            or evaluation.classification is None
            or evaluation.indicators is None
            or evaluation.signal_bar is None
        ):
            raise ValueError(
                f"{request.case_id}: strategy result cannot be projected"
            )

        classification = evaluation.classification
        filter_result = FilterResult(
            buy_matched=classification.buy_filter_matched,
            sell_matched=classification.sell_filter_matched,
            daily_buy_level=evaluation.indicators.daily_buy_level,
            daily_sell_level=evaluation.indicators.daily_sell_level,
        )
        signal_result = SignalResult(
            technical_buy=classification.technical_buy_signal,
            technical_sell=classification.technical_sell_signal,
            confirmed_buy=classification.confirmed_buy,
            confirmed_sell=classification.confirmed_sell,
        )
        levels_results = tuple(
            self._levels(candidate) for candidate in evaluation.candidates
        )
        levels_result = levels_results[0] if len(levels_results) == 1 else None
        bar_event = BarClosedEvent(
            strategy_id=request.strategy_id,
            bar=evaluation.signal_bar,
            occurred_at=request.evaluation_time,
            source_case_id=request.case_id,
        )
        status = InstrumentStatus(
            strategy_id=request.strategy_id,
            provider=request.instrument.provider,
            instrument_id=request.instrument.instrument_id,
            timeframe=request.timeframe,
            source_case_id=request.case_id,
            synthetic=True,
            data_status=evaluation.data_status,
            dashboard_state=classification.dashboard_state,
            filter_result=filter_result,
            signal_result=signal_result,
            levels_result=levels_result,
            levels_results=levels_results,
            signal_bar_close_time=evaluation.signal_bar.close_time,
            last_update=request.evaluation_time,
            idempotency_key=bar_event.idempotency_key,
        )
        return EvaluatedProjection(
            bar_event=bar_event,
            filter_result=filter_result,
            signal_result=signal_result,
            levels_result=levels_result,
            levels_results=levels_results,
            status=status,
            evaluation=evaluation,
        )

    @staticmethod
    def _levels(candidate: CandidateResult) -> LevelsResult:
        sizing = candidate.position_size
        return LevelsResult(
            direction=candidate.direction,
            entry_reference=candidate.entry_reference,
            raw_stop=candidate.raw_strategy_stop,
            display_stop=candidate.provider_adjusted_stop,
            target=candidate.target_3r,
            target_risk_usd=sizing.target_risk_usd,
            contract_size=sizing.display_size,
            contract_status=sizing.contract_status,
        )

    @staticmethod
    def _event_trace(adapted: EvaluatedProjection) -> tuple[DomainEvent, ...]:
        bar_event = adapted.bar_event
        collected: list[DomainEvent] = []
        dispatcher = DeterministicEventDispatcher()
        dispatcher.subscribe(collected.append)

        def emit(event_type: EventType, payload: dict[str, Any]) -> None:
            dispatcher.dispatch(
                DomainEvent(
                    event_type=event_type,
                    sequence=len(collected) + 1,
                    idempotency_key=bar_event.idempotency_key,
                    occurred_at=bar_event.occurred_at,
                    instrument_id=bar_event.bar.instrument_id,
                    timeframe=bar_event.bar.timeframe,
                    source_case_id=bar_event.source_case_id,
                    synthetic=True,
                    payload=payload,
                )
            )

        emit(
            EventType.BAR_CLOSED,
            {
                "bar": primitive(bar_event.bar),
                "strategy_id": bar_event.strategy_id,
            },
        )
        emit(EventType.FILTER_EVALUATED, primitive(adapted.filter_result))
        emit(
            (
                EventType.FILTER_MATCHED
                if adapted.filter_result.buy_matched
                or adapted.filter_result.sell_matched
                else EventType.FILTER_NOT_MATCHED
            ),
            {
                "buy_matched": adapted.filter_result.buy_matched,
                "sell_matched": adapted.filter_result.sell_matched,
            },
        )
        emit(EventType.SIGNAL_EVALUATED, primitive(adapted.signal_result))
        confirmed_directions = adapted.signal_result.confirmed_directions
        emit(
            (
                EventType.SIGNAL_CONFIRMED
                if confirmed_directions
                else EventType.SIGNAL_NOT_CONFIRMED
            ),
            {
                "direction": (
                    confirmed_directions[0].value
                    if len(confirmed_directions) == 1
                    else "BOTH"
                    if len(confirmed_directions) == 2
                    else None
                ),
                "directions": [
                    direction.value for direction in confirmed_directions
                ],
            },
        )
        if adapted.levels_results:
            payload = (
                primitive(adapted.levels_results[0])
                if len(adapted.levels_results) == 1
                else {"levels_results": primitive(adapted.levels_results)}
            )
            emit(EventType.LEVELS_CALCULATED, payload)
        emit(
            EventType.STATUS_PROJECTED,
            {
                "dashboard_state": adapted.status.dashboard_state,
                "source_case_id": adapted.status.source_case_id,
            },
        )
        return tuple(collected)
