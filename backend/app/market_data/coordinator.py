from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from ..domain import Bar
from ..engine.models import StrategyRequest
from ..repository import SQLiteProjectionRepository
from ..service import ProcessingOutcome, WalkingSkeletonService
from .clock import Clock
from .closed_bar import ClosedBarDetector
from .interfaces import MarketDataProvider
from .models import (
    CanonicalInstrument,
    HealthState,
    NormalizationResult,
    ProviderClosedBar,
    ProviderHealth,
    RawProviderCandle,
)
from .normalizer import CandleNormalizer
from .registry import CanonicalInstrumentRegistry


@dataclass(frozen=True, slots=True)
class PollOutcome:
    source_case_id: str
    idempotency_key: str | None
    replayed: bool
    events_created: int
    health_state: HealthState
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PollResult:
    checked_at: datetime
    provider_health: ProviderHealth
    outcomes: tuple[PollOutcome, ...]
    canonical_bars_inserted: int


class MarketDataCoordinator:
    """Provider-neutral deterministic polling and projection orchestration."""

    def __init__(
        self,
        provider: MarketDataProvider,
        registry: CanonicalInstrumentRegistry,
        normalizer: CandleNormalizer,
        detector: ClosedBarDetector,
        service: WalkingSkeletonService,
        repository: SQLiteProjectionRepository,
        clock: Clock,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self._normalizer = normalizer
        self._detector = detector
        self._service = service
        self._repository = repository
        self._clock = clock

    def poll_once(self) -> PollResult:
        now = self._clock.now()
        provider_health = self.provider.health(now)
        raw_closed = self.provider.fetch_completed_bars(now)
        prepared: list[tuple[ProviderClosedBar, Bar]] = []
        outcomes: list[PollOutcome] = []
        inserted = 0
        poll_state = provider_health.state

        for closed in raw_closed:
            instrument = self.registry.get(
                closed.candle.provider_id,
                closed.instrument_id,
            )
            normalized = self._normalizer.normalize(
                closed.candle, instrument
            )
            if normalized.candle is None:
                poll_state = HealthState.QUARANTINED
                outcomes.append(
                    PollOutcome(
                        source_case_id=closed.source_id,
                        idempotency_key=None,
                        replayed=False,
                        events_created=0,
                        health_state=HealthState.QUARANTINED,
                        issues=normalized.issues,
                    )
                )
                continue
            prepared.append((closed, normalized.candle))

        trigger_issues = self._detector.batch_issues(
            [candle for _, candle in prepared]
        )
        if trigger_issues:
            poll_state = HealthState.QUARANTINED
            outcomes.extend(
                PollOutcome(
                    source_case_id=closed.source_id,
                    idempotency_key=None,
                    replayed=False,
                    events_created=0,
                    health_state=HealthState.QUARANTINED,
                    issues=trigger_issues,
                )
                for closed, _ in prepared
            )
            prepared = []

        for closed, trigger in prepared:
            instrument = self.registry.get(
                closed.candle.provider_id,
                closed.instrument_id,
            )
            history = self.provider.fetch_required_history(closed, now)
            signal_bars, signal_issues = self._normalize_many(
                history.signal_bars, instrument
            )
            daily_bars, daily_issues = self._normalize_many(
                history.daily_bars, instrument
            )
            issues = tuple(
                sorted(set((*signal_issues, *daily_issues)))
            )
            validation = self._detector.validate_history(
                signal_bars,
                daily_bars,
                history.timeframe,
                trigger.close_time,
            )
            issues = tuple(sorted(set((*issues, *validation.issues))))
            if issues:
                state = (
                    HealthState.INSUFFICIENT_HISTORY
                    if any(issue.startswith("INSUFFICIENT_") for issue in issues)
                    else HealthState.QUARANTINED
                )
                poll_state = state
                outcomes.append(
                    PollOutcome(
                        source_case_id=closed.source_id,
                        idempotency_key=None,
                        replayed=False,
                        events_created=0,
                        health_state=state,
                        issues=issues,
                    )
                )
                continue

            canonical_history = (
                *validation.signal_bars,
                *validation.daily_bars,
            )
            inserted += self._repository.persist_canonical_bars(
                tuple(canonical_history)
            )
            request = StrategyRequest(
                case_id=history.source_id,
                strategy_id=instrument.strategy_id,
                timeframe=history.timeframe,
                evaluation_time=history.evaluation_time,
                signal_bars=validation.signal_bars,
                daily_bars=validation.daily_bars,
                instrument=instrument.to_strategy_metadata(),
            )
            projection = self._service.process_request(request)
            outcomes.append(
                self._outcome(projection, provider_health.state)
            )

        final_health = replace(provider_health, state=poll_state)
        final_health = self._apply_recovery(final_health)
        self._repository.update_provider_health(final_health)
        if final_health.state is HealthState.RECOVERED:
            outcomes = [
                replace(outcome, health_state=HealthState.RECOVERED)
                if not outcome.issues
                else outcome
                for outcome in outcomes
            ]
        return PollResult(
            checked_at=now,
            provider_health=final_health,
            outcomes=tuple(outcomes),
            canonical_bars_inserted=inserted,
        )

    def current_health(self) -> dict[str, object] | None:
        return self._repository.provider_health(
            self.provider.identity.provider_id
        )

    def _normalize_many(
        self,
        candles: tuple[RawProviderCandle, ...],
        instrument: CanonicalInstrument,
    ) -> tuple[tuple[Bar, ...], tuple[str, ...]]:
        normalized: list[Bar] = []
        issues: list[str] = []
        for raw in candles:
            result: NormalizationResult = self._normalizer.normalize(
                raw, instrument
            )
            if result.candle is not None:
                normalized.append(result.candle)
            issues.extend(result.issues)
        return tuple(normalized), tuple(sorted(set(issues)))

    def _apply_recovery(self, health: ProviderHealth) -> ProviderHealth:
        previous = self._repository.provider_health(health.provider_id)
        unhealthy = {
            HealthState.STALE.value,
            HealthState.DATA_UNAVAILABLE.value,
            HealthState.INSUFFICIENT_HISTORY.value,
            HealthState.QUARANTINED.value,
        }
        if (
            health.state is HealthState.HEALTHY
            and previous is not None
            and previous["state"] in unhealthy
        ):
            return replace(
                health,
                state=HealthState.RECOVERED,
                detail="Replay market data recovered to a healthy state.",
            )
        return health

    @staticmethod
    def _outcome(
        projection: ProcessingOutcome,
        health_state: HealthState,
    ) -> PollOutcome:
        return PollOutcome(
            source_case_id=projection.source_case_id,
            idempotency_key=projection.idempotency_key,
            replayed=projection.replayed,
            events_created=projection.events_created,
            health_state=health_state,
            issues=(),
        )
