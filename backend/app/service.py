from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .dispatcher import DeterministicEventDispatcher
from .domain import DomainEvent, EventType, primitive
from .golden_adapter import AdaptedGoldenCase, FrozenExpectedResultAdapter
from .repository import SQLiteProjectionRepository


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    source_case_id: str
    idempotency_key: str
    replayed: bool
    events_created: int


class WalkingSkeletonService:
    def __init__(
        self,
        adapter: FrozenExpectedResultAdapter,
        repository: SQLiteProjectionRepository,
    ) -> None:
        self._adapter = adapter
        self._repository = repository

    def process_case(self, case_id: str) -> ProcessingOutcome:
        adapted = self._adapter.load(case_id)
        events = self._event_trace(adapted)
        created = self._repository.persist_projection(adapted.status, events)
        return ProcessingOutcome(
            source_case_id=case_id,
            idempotency_key=adapted.bar_event.idempotency_key,
            replayed=not created,
            events_created=len(events) if created else 0,
        )

    def process_cases(self, case_ids: tuple[str, ...]) -> list[ProcessingOutcome]:
        return [self.process_case(case_id) for case_id in case_ids]

    @staticmethod
    def _event_trace(adapted: AdaptedGoldenCase) -> tuple[DomainEvent, ...]:
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
        emit(
            (
                EventType.SIGNAL_CONFIRMED
                if adapted.signal_result.confirmed_direction is not None
                else EventType.SIGNAL_NOT_CONFIRMED
            ),
            {
                "direction": (
                    adapted.signal_result.confirmed_direction.value
                    if adapted.signal_result.confirmed_direction
                    else None
                )
            },
        )
        if adapted.levels_result is not None:
            emit(EventType.LEVELS_CALCULATED, primitive(adapted.levels_result))
        emit(
            EventType.STATUS_PROJECTED,
            {
                "dashboard_state": adapted.status.dashboard_state,
                "source_case_id": adapted.status.source_case_id,
            },
        )
        return tuple(collected)
