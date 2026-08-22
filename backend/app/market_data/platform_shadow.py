"""Default-off PostgreSQL Platform shadow composition."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ..domain import FilterMode, Timeframe
from ..engine.current_daily_filter import build_daily_filter_snapshot
from ..engine.current_w1_filter import build_w1_filter_snapshot
from ..engine.models import (
    CURRENT_D1_FILTER_V2,
    CURRENT_W1_FILTER_V1,
    StrategyRequest,
)
from ..repository import SQLiteProjectionRepository
from ..service import WalkingSkeletonService
from .platform_adapter import (
    PLATFORM_PROVIDER_ID,
    SPECT8_PLATFORM_BOOTSTRAP_LIMITS,
    PlatformCanonicalBar,
    PlatformCanonicalReadGateway,
    PlatformIncrementalProcessor,
    PlatformInstrumentHistory,
    PlatformReadBatch,
    Spect8CanonicalReadServiceGateway,
    bid_bars,
    build_platform_history,
    platform_instrument_id,
)
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from .registry import CanonicalInstrumentRegistry, twelve_data_instruments

APPROVED_PLATFORM_SHADOW_INSTRUMENTS = ("EUR_USD", "GBP_USD", "USD_JPY")


@dataclass(frozen=True, slots=True)
class ShadowEvaluationEvidence:
    instrument_id: str
    timeframe: str
    filter_mode: str
    strategy_version: str
    signal_close_time: datetime
    idempotency_key: str
    replayed: bool
    events_created: int


@dataclass(frozen=True, slots=True)
class PlatformShadowRunResult:
    watermark_canonical_bar_id: int
    instrument_master_checksum: str
    session_calendar_checksum: str
    timezone_data_version: str
    bootstrap_counts: tuple[tuple[str, int, int, int, int, int], ...]
    evaluations: tuple[ShadowEvaluationEvidence, ...]
    consumed: int
    replayed_inputs: int
    revisions_detected: int


class PlatformShadowRuntime:
    """Evaluate isolated Platform projections and checkpoint canonical provenance last."""

    def __init__(
        self,
        gateway: PlatformCanonicalReadGateway,
        repository: SQLiteProjectionRepository,
        service: WalkingSkeletonService,
        *,
        backend: object | None = None,
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._service = service
        self._backend = backend
        self._processor = PlatformIncrementalProcessor(gateway, repository)
        instruments = twelve_data_instruments(APPROVED_PLATFORM_SHADOW_INSTRUMENTS)
        self._registry = CanonicalInstrumentRegistry(instruments)

    @classmethod
    def from_database_url(
        cls,
        database_url: str,
        repository: SQLiteProjectionRepository,
        service: WalkingSkeletonService,
    ) -> PlatformShadowRuntime:
        try:
            from hedgefund_market_data.pipeline import (  # type: ignore[import-not-found]
                PostgreSQLSpect8CanonicalReadService,
            )
        except ImportError as error:
            raise RuntimeError(
                "hedgefund-market-data must be installed for Platform shadow mode"
            ) from error
        backend = PostgreSQLSpect8CanonicalReadService.from_database_url(database_url)
        return cls(
            Spect8CanonicalReadServiceGateway(backend),
            repository,
            service,
            backend=backend,
        )

    def run_once(
        self,
        *,
        available_as_of: datetime,
        instrument_ids: tuple[str, ...] = APPROVED_PLATFORM_SHADOW_INSTRUMENTS,
    ) -> PlatformShadowRunResult:
        if not instrument_ids or any(
            instrument_id not in APPROVED_PLATFORM_SHADOW_INSTRUMENTS
            for instrument_id in instrument_ids
        ):
            raise ValueError("shadow instruments must be a non-empty approved subset")
        batch = self._gateway.read(
            tuple(platform_instrument_id(item) for item in instrument_ids),
            available_as_of=available_as_of,
            after_canonical_bar_id=None,
            limits=SPECT8_PLATFORM_BOOTSTRAP_LIMITS,
        )
        stable = self._pin_first_consumed_versions(batch)
        evidence: list[ShadowEvaluationEvidence] = []
        evaluation_keys: dict[str, list[str]] = {item: [] for item in instrument_ids}
        counts: list[tuple[str, int, int, int, int, int]] = []
        for instrument_id in instrument_ids:
            history = build_platform_history(stable, instrument_id)
            history.assert_bootstrap_ready()
            counts.append(
                (
                    instrument_id,
                    len(history.m30),
                    len(history.h1),
                    len(history.h4),
                    len(history.d1),
                    len(history.w1),
                )
            )
            for timeframe in (Timeframe.H1, Timeframe.H4):
                for mode in (FilterMode.MICRO, FilterMode.MACRO):
                    request = self._request(history, timeframe, mode)
                    outcome = self._service.process_request(request)
                    evaluation_keys[instrument_id].append(outcome.idempotency_key)
                    evidence.append(
                        ShadowEvaluationEvidence(
                            instrument_id=instrument_id,
                            timeframe=timeframe.value,
                            filter_mode=mode.value,
                            strategy_version=request.strategy_version,
                            signal_close_time=request.signal_bars[-1].close_time,
                            idempotency_key=outcome.idempotency_key,
                            replayed=outcome.replayed,
                            events_created=outcome.events_created,
                        )
                    )
        processed = self._processor.process(
            instrument_ids,
            available_as_of=available_as_of,
            process_bar=lambda _bar, mapped: tuple(evaluation_keys[mapped]),
        )
        return PlatformShadowRunResult(
            watermark_canonical_bar_id=processed.new_watermark,
            instrument_master_checksum=batch.instrument_master_checksum,
            session_calendar_checksum=batch.session_calendar_checksum,
            timezone_data_version=batch.timezone_data_version,
            bootstrap_counts=tuple(counts),
            evaluations=tuple(evidence),
            consumed=processed.consumed,
            replayed_inputs=processed.replayed,
            revisions_detected=processed.revisions_detected,
        )

    def close(self) -> None:
        close = getattr(self._backend, "close", None)
        if callable(close):
            close()

    def _pin_first_consumed_versions(
        self, batch: PlatformReadBatch
    ) -> PlatformReadBatch:
        replacements: dict[str, int] = {}
        for bar in bid_bars(batch.bars):
            consumed = self._repository.platform_consumed_identity(bar.logical_identity)
            if (
                consumed is not None
                and int(consumed["canonical_bar_id"]) != bar.canonical_bar_id
            ):
                replacements[bar.logical_identity] = int(consumed["canonical_bar_id"])
        if not replacements:
            return batch
        exact = self._gateway.read_canonical_ids(
            tuple(sorted(set(replacements.values())))
        )
        by_id = {bar.canonical_bar_id: bar for bar in exact}
        pinned: list[PlatformCanonicalBar] = []
        for bar in batch.bars:
            canonical_id = replacements.get(bar.logical_identity)
            pinned.append(by_id[canonical_id] if canonical_id is not None else bar)
        return replace(batch, bars=tuple(pinned))

    def _request(
        self,
        history: PlatformInstrumentHistory,
        timeframe: Timeframe,
        mode: FilterMode,
    ) -> StrategyRequest:
        signal_history = history.h1 if timeframe is Timeframe.H1 else history.h4
        signal_bars = tuple(signal_history[-30:])
        trigger_close = signal_bars[-1].close_time
        h1_source = tuple(bar for bar in history.h1 if bar.close_time <= trigger_close)
        daily_bars = tuple(bar for bar in history.d1 if bar.close_time <= trigger_close)
        if len(daily_bars) < 6:
            raise ValueError("Platform shadow evaluation lacks six eligible D1 inputs")
        instrument = replace(
            self._registry.by_id(history.spect8_instrument_id),
            provider_id=PLATFORM_PROVIDER_ID,
            session_timezone="America/New_York",
            candle_boundary_convention=PROFILE_ID,
        )
        strategy_version = (
            CURRENT_D1_FILTER_V2 if mode is FilterMode.MICRO else CURRENT_W1_FILTER_V1
        )
        daily_snapshot = None
        weekly_snapshot = None
        if mode is FilterMode.MICRO:
            daily_snapshot = build_daily_filter_snapshot(
                provider=PLATFORM_PROVIDER_ID,
                instrument=history.spect8_instrument_id,
                as_of_h1_close=trigger_close,
                h1_bars=h1_source,
                completed_d1_bars=daily_bars,
            )
        else:
            weekly_snapshot = build_w1_filter_snapshot(
                provider=PLATFORM_PROVIDER_ID,
                instrument=history.spect8_instrument_id,
                as_of_h1_close=trigger_close,
                h1_bars=h1_source,
            )
        return StrategyRequest(
            case_id=(
                f"platform-shadow:{history.spect8_instrument_id}:"
                f"{timeframe.value}:{mode.value}:{trigger_close.isoformat()}"
            ),
            strategy_id=strategy_version,
            timeframe=timeframe,
            evaluation_time=trigger_close + timedelta(microseconds=1),
            signal_bars=signal_bars,
            daily_bars=daily_bars,
            instrument=instrument.to_strategy_metadata(),
            strategy_version=strategy_version,
            daily_filter_snapshot=daily_snapshot,
            filter_mode=mode,
            w1_filter_snapshot=weekly_snapshot,
        )
