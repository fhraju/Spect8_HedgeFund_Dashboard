from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ..domain import Bar, Timeframe
from ..engine.models import StrategyRequest
from ..engine.models import CURRENT_D1_FILTER_V2, DailyFilterSnapshot
from ..engine.current_daily_filter import (
    DailyFilterUnavailableError,
    build_daily_filter_snapshot,
)
from ..repository import SQLiteProjectionRepository
from ..service import ProcessingOutcome, WalkingSkeletonService
from .clock import Clock
from .closed_bar import DAILY_ATR_INPUT_HISTORY, ClosedBarDetector
from .daily_aggregator import NewYorkDailyAggregator
from .forex_profile import (
    BrokerAlignedH4Aggregator,
    is_broker_h4_close,
    is_valid_market_h1,
    is_valid_market_h4,
    market_h1_bars,
    PROFILE_ID,
)
from .interfaces import MarketDataProvider
from .models import (
    CanonicalInstrument,
    HealthState,
    MarketDataProviderError,
    NormalizationResult,
    ProviderClosedBar,
    ProviderHealth,
    RawProviderCandle,
    ProviderErrorCode,
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
        self._daily_aggregator = NewYorkDailyAggregator()
        self._h4_aggregator = BrokerAlignedH4Aggregator()
        self._service = service
        self._repository = repository
        self._clock = clock

    def poll_once(self) -> PollResult:
        now = self._clock.now()
        self._seed_resume_cursors()
        provider_health = self.provider.health(now)
        profile_enabled = not self.provider.identity.synthetic
        try:
            raw_closed = self.provider.fetch_completed_bars(now)
        except MarketDataProviderError as error:
            if error.code in (
                ProviderErrorCode.MISSING_CANDLE,
                ProviderErrorCode.DUPLICATE_CANDLE,
            ):
                for instrument in self.registry.all():
                    self._repository.persist_quality_issue(
                        provider=instrument.provider_id,
                        instrument_id=instrument.instrument_id,
                        timeframe=Timeframe.H1,
                        issue_code=error.code.value,
                        detail=str(error),
                        construction_profile_version=PROFILE_ID,
                        created_at=now,
                    )
            failed_health = self.provider.health(now)
            self._repository.update_provider_health(failed_health)
            return PollResult(
                checked_at=now,
                provider_health=failed_health,
                outcomes=(),
                canonical_bars_inserted=0,
            )
        provider_health = self.provider.health(now)
        prepared: list[tuple[ProviderClosedBar, Bar]] = []
        outcomes: list[PollOutcome] = []
        inserted = 0
        override_state: HealthState | None = None

        for closed in raw_closed:
            instrument = self.registry.get(
                closed.candle.provider_id,
                closed.instrument_id,
            )
            normalized = self._normalizer.normalize(closed.candle, instrument)
            if normalized.candle is None:
                override_state = HealthState.QUARANTINED
                for issue in normalized.issues:
                    self._repository.persist_quality_issue(
                        provider=instrument.provider_id,
                        instrument_id=instrument.instrument_id,
                        timeframe=closed.candle.timeframe,
                        issue_code=issue,
                        detail="Provider candle failed canonical normalization.",
                        construction_profile_version=PROFILE_ID,
                        ingestion_run_id=(
                            str(closed.candle.provider_metadata.get("ingestion_run_id"))
                            if closed.candle.provider_metadata
                            and closed.candle.provider_metadata.get("ingestion_run_id")
                            else None
                        ),
                        created_at=closed.evaluation_time,
                    )
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

        trigger_issues = self._detector.batch_issues([candle for _, candle in prepared])
        if trigger_issues:
            override_state = HealthState.QUARANTINED
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

        if profile_enabled:
            # H4 evaluation boundaries are derived from completed H1 closes.
            # Provider-native H4 remains comparison-only and never triggers or
            # supplies a strategy candle.
            derived: list[tuple[ProviderClosedBar, Bar]] = []
            for closed, trigger in prepared:
                if trigger.timeframe is not Timeframe.H1:
                    continue
                derived.append((closed, trigger))
                if is_broker_h4_close(trigger.close_time):
                    h4_trigger = replace(
                        trigger,
                        timeframe=Timeframe.H4,
                        open_time=trigger.close_time - timedelta(hours=4),
                        raw_open_time=(trigger.close_time - timedelta(hours=4))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    )
                    h4_closed = replace(
                        closed,
                        source_id=(
                            f"{closed.candle.provider_id.lower()}:"
                            f"{closed.instrument_id}:H4:"
                            f"{trigger.close_time.isoformat().replace('+00:00', 'Z')}"
                        ),
                        candle=replace(
                            closed.candle,
                            timeframe=Timeframe.H4,
                            raw_open_time=h4_trigger.raw_open_time or "",
                        ),
                    )
                    derived.append((h4_closed, h4_trigger))
            prepared = derived

        history_cache: dict[tuple[str, str, datetime], object] = {}
        h4_cache: dict[tuple[str, str, datetime], object] = {}
        snapshot_cache: dict[tuple[str, str, datetime], DailyFilterSnapshot] = {}
        for closed, trigger in prepared:
            if profile_enabled and (
                (trigger.timeframe is Timeframe.H1 and not is_valid_market_h1(trigger))
                or (
                    trigger.timeframe is Timeframe.H4
                    and not is_valid_market_h4(trigger)
                )
            ):
                outcomes.append(
                    PollOutcome(
                        source_case_id=closed.source_id,
                        idempotency_key=None,
                        replayed=False,
                        events_created=0,
                        health_state=provider_health.state,
                        issues=("EXPECTED_MARKET_CLOSURE",),
                    )
                )
                continue
            instrument = self.registry.get(
                closed.candle.provider_id,
                closed.instrument_id,
            )
            try:
                cache_key = (
                    instrument.provider_id,
                    instrument.instrument_id,
                    trigger.close_time,
                )
                cached_history = history_cache.get(cache_key)
                if profile_enabled and cached_history is not None:
                    history = replace(
                        cached_history,
                        source_id=closed.source_id,
                        timeframe=trigger.timeframe,
                    )
                else:
                    history = self.provider.fetch_required_history(closed, now)
                    if profile_enabled and trigger.timeframe is Timeframe.H1:
                        history_cache[cache_key] = history
            except MarketDataProviderError as error:
                override_state = error.health_state
                outcomes.append(
                    PollOutcome(
                        source_case_id=closed.source_id,
                        idempotency_key=None,
                        replayed=False,
                        events_created=0,
                        health_state=error.health_state,
                        issues=(error.code.value,),
                    )
                )
                continue
            signal_bars, signal_issues = self._normalize_many(
                history.signal_bars, instrument
            )
            daily_bars, daily_issues = self._normalize_many(
                history.daily_bars, instrument
            )
            daily_source, daily_source_issues = self._normalize_many(
                history.daily_source_bars, instrument
            )
            if profile_enabled:
                daily_source = market_h1_bars(daily_source)
                if trigger.timeframe is Timeframe.H1 and not daily_source_issues:
                    # Persist the completed canonical H1 before any derived
                    # timeframe state or evaluation references it.
                    inserted += self._repository.persist_canonical_bars((trigger,))
            aggregation_issues: tuple[str, ...] = ()
            if daily_source:
                # Native signal H4 and native provider D1 are comparison-only.
                # Production signal/D1 streams are derived from validated H1.
                signal_issues = ()
                daily_issues = ()
                if profile_enabled and history.timeframe is Timeframe.H1:
                    signal_bars = tuple(
                        bar
                        for bar in daily_source
                        if bar.close_time <= trigger.close_time
                    )[-30:]
                    # Update H4 before the partial D1 and shared snapshot. A
                    # completing H4 evaluation reuses this exact aggregate.
                    h4 = self._h4_aggregator.aggregate(
                        daily_source, as_of=history.evaluation_time
                    )
                    h4_cache[cache_key] = h4
                    for issue in h4.issues:
                        self._repository.persist_quality_issue(
                            provider=instrument.provider_id,
                            instrument_id=instrument.instrument_id,
                            timeframe=Timeframe.H4,
                            issue_code=issue.code.value,
                            detail=issue.detail,
                            bucket_open=issue.bucket_open,
                            bucket_close=issue.bucket_close,
                            construction_profile_version=PROFILE_ID,
                            ingestion_run_id=daily_source[-1].ingestion_run_id,
                            created_at=history.evaluation_time,
                        )
                    completing_h4 = tuple(
                        bar for bar in h4.bars if bar.close_time == trigger.close_time
                    )
                    if completing_h4:
                        inserted += self._repository.persist_canonical_bars(
                            completing_h4
                        )
                elif profile_enabled:
                    h4 = h4_cache.get(cache_key)
                    if h4 is None:
                        h4 = self._h4_aggregator.aggregate(
                            daily_source, as_of=history.evaluation_time
                        )
                    signal_bars = tuple(
                        bar for bar in h4.bars if bar.close_time <= trigger.close_time
                    )[-30:]
                aggregation = self._daily_aggregator.aggregate(
                    daily_source,
                    as_of=history.evaluation_time,
                )
                daily_bars = tuple(
                    bar
                    for bar in aggregation.bars
                    if bar.close_time <= trigger.close_time
                )[-DAILY_ATR_INPUT_HISTORY:]
                aggregation_issues = tuple(
                    issue.code.value for issue in aggregation.issues
                )
                for issue in aggregation.issues:
                    self._repository.persist_quality_issue(
                        provider=instrument.provider_id,
                        instrument_id=instrument.instrument_id,
                        timeframe=Timeframe.D1,
                        issue_code=issue.code.value,
                        detail=issue.detail,
                        bucket_open=issue.session_start,
                        bucket_close=issue.session_end,
                        construction_profile_version=PROFILE_ID,
                        ingestion_run_id=(
                            daily_source[-1].ingestion_run_id if daily_source else None
                        ),
                        created_at=history.evaluation_time,
                    )
            issues = tuple(
                sorted(
                    set(
                        (
                            *signal_issues,
                            *daily_issues,
                            *daily_source_issues,
                            *aggregation_issues,
                        )
                    )
                )
            )
            validation = self._detector.validate_history(
                signal_bars,
                daily_bars,
                history.timeframe,
                trigger.close_time,
            )
            issues = tuple(sorted(set((*issues, *validation.issues))))
            snapshot: DailyFilterSnapshot | None = None
            if profile_enabled and not issues:
                snapshot = snapshot_cache.get(cache_key)
                if snapshot is None:
                    try:
                        snapshot = build_daily_filter_snapshot(
                            provider=instrument.provider_id,
                            instrument=instrument.instrument_id,
                            as_of_h1_close=trigger.close_time,
                            h1_bars=daily_source,
                            completed_d1_bars=daily_bars,
                        )
                    except DailyFilterUnavailableError as error:
                        issues = (str(error),)
                    else:
                        snapshot_cache[cache_key] = snapshot
                        self._repository.persist_daily_filter_snapshot(snapshot)
            if issues:
                state = (
                    HealthState.INSUFFICIENT_HISTORY
                    if any(issue.startswith("INSUFFICIENT_") for issue in issues)
                    else HealthState.QUARANTINED
                )
                override_state = state
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
                strategy_id=(
                    CURRENT_D1_FILTER_V2 if profile_enabled else instrument.strategy_id
                ),
                timeframe=history.timeframe,
                evaluation_time=history.evaluation_time,
                signal_bars=validation.signal_bars,
                daily_bars=validation.daily_bars,
                instrument=instrument.to_strategy_metadata(),
                strategy_version=(
                    CURRENT_D1_FILTER_V2
                    if profile_enabled
                    else "SPECT8_MICRO_DAILY_V1_0_3"
                ),
                daily_filter_snapshot=snapshot,
            )
            projection = self._service.process_request(request)
            outcomes.append(self._outcome(projection, provider_health.state))

        provider_health = self.provider.health(now)
        final_health = replace(
            provider_health,
            state=override_state or provider_health.state,
        )
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

    def _seed_resume_cursors(self) -> None:
        setter = getattr(self.provider, "set_resume_cursor", None)
        if not callable(setter):
            return
        for instrument in self.registry.all():
            for timeframe in (Timeframe.H1, Timeframe.H4):
                value = self._repository.latest_evaluation_close(
                    instrument.provider_id,
                    instrument.instrument_id,
                    timeframe.value,
                )
                setter(
                    timeframe,
                    (
                        datetime.fromisoformat(value.replace("Z", "+00:00"))
                        if value is not None
                        else None
                    ),
                )

    def current_health(self) -> dict[str, object] | None:
        return self._repository.provider_health(self.provider.identity.provider_id)

    def _normalize_many(
        self,
        candles: tuple[RawProviderCandle, ...],
        instrument: CanonicalInstrument,
    ) -> tuple[tuple[Bar, ...], tuple[str, ...]]:
        normalized: list[Bar] = []
        issues: list[str] = []
        for raw in candles:
            result: NormalizationResult = self._normalizer.normalize(raw, instrument)
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
                detail="Market data recovered to a healthy state.",
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
