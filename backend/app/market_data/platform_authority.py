"""Fail-closed authoritative Market Data Platform runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from typing import Any

from ..domain import FilterMode, Timeframe, primitive
from ..engine.current_daily_filter import build_daily_filter_snapshot
from ..engine.current_w1_filter import build_w1_filter_snapshot
from ..engine.models import CURRENT_D1_FILTER_V2, CURRENT_W1_FILTER_V1, StrategyRequest
from ..repository import SQLiteProjectionRepository
from ..service import WalkingSkeletonService
from .clock import SystemClock
from .forex_profile import BrokerAlignedH4Aggregator
from .models import ProviderIdentity
from .partial_snapshot import CurrentBarSnapshot, broker_partial_h4_from_h1_snapshots
from .platform_adapter import (
    PLATFORM_PROVIDER_ID,
    SPECT8_PLATFORM_BOOTSTRAP_LIMITS,
    SPECT8_PLATFORM_REPLAY_LIMITS,
    PlatformCanonicalReadGateway,
    PlatformIncrementalProcessor,
    PlatformInstrumentHistory,
    PlatformReadBatch,
    Spect8CanonicalReadServiceGateway,
    bid_bars,
    build_platform_history,
    first_accepted_versions,
    platform_instrument_id,
    to_current_bar_snapshot,
    to_spect8_bar,
)
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from .registry import CanonicalInstrumentRegistry, twelve_data_instruments
from .session_boundaries import NEW_YORK, NEW_YORK_CLOSE_TIME
from .signal_lifecycle import SignalLifecycleService, SignalSnapshot

APPROVED_PLATFORM_AUTHORITY_INSTRUMENTS = ("EUR_USD", "GBP_USD", "USD_JPY")


class PlatformAuthorityError(RuntimeError):
    """A hard startup/runtime gate failure that must never trigger fallback."""


class PlatformUnavailableError(PlatformAuthorityError):
    """PostgreSQL or its read-only canonical reader is unavailable."""


class PlatformStaleError(PlatformAuthorityError):
    """Canonical H1 has fallen behind the conservative confirmed-bar policy."""


@dataclass(frozen=True, slots=True)
class PlatformAuthorityRunResult:
    connection_state: str
    freshness_state: str
    previous_watermark: int
    watermark_canonical_bar_id: int
    bootstrapped: bool
    consumed: int
    replayed_inputs: int
    revisions_detected: int
    evaluations_created: int
    duplicate_evaluations_prevented: int
    last_processed_canonical_timestamp: datetime | None
    last_successful_evaluation_timestamp: datetime | None
    bars_replayed: int = 0
    signal_events_evaluated: int = 0
    confirmed_signals_reconstructed: int = 0
    replay_events_unsupported: int = 0
    replay_limitations: tuple[str, ...] = ()


class PlatformAuthorityRuntime:
    """Own exactly one authoritative Platform read/evaluation/checkpoint cycle."""

    identity = ProviderIdentity(
        provider_id=PLATFORM_PROVIDER_ID,
        display_name="Market Data Platform PostgreSQL",
        adapter_version="22A-4",
        synthetic=False,
    )

    def __init__(
        self,
        gateway: PlatformCanonicalReadGateway,
        repository: SQLiteProjectionRepository,
        service: WalkingSkeletonService,
        instrument_ids: tuple[str, ...],
        *,
        stale_after_seconds: int,
        poll_seconds: int,
        backend: object | None = None,
        signal_lifecycle: SignalLifecycleService | None = None,
    ) -> None:
        if not instrument_ids or any(
            item not in APPROVED_PLATFORM_AUTHORITY_INSTRUMENTS
            for item in instrument_ids
        ):
            raise ValueError(
                "Platform authority instruments must be an approved subset"
            )
        self._gateway = gateway
        self._repository = repository
        self._service = service
        self._instrument_ids = instrument_ids
        self._stale_after_seconds = stale_after_seconds
        self._poll_seconds = poll_seconds
        self._backend = backend
        self._signal_lifecycle = signal_lifecycle
        self._processor = PlatformIncrementalProcessor(gateway, repository)
        instruments = tuple(
            replace(item, provider_id=PLATFORM_PROVIDER_ID, synthetic=False)
            for item in twelve_data_instruments(instrument_ids)
            if item.instrument_id in instrument_ids
        )
        self.registry = CanonicalInstrumentRegistry(instruments)
        self._stop = asyncio.Event()
        self._running = False
        self._last_result: PlatformAuthorityRunResult | None = None
        self._last_error: str | None = None
        self._connection_state = "UNAVAILABLE"
        self._freshness_state = "UNAVAILABLE"
        self._historical_state = "NOT_READY"
        self._live_readiness = _live_streaming_readiness(
            None,
            instrument_ids,
            as_of=SystemClock().now(),
            stale_after_seconds=stale_after_seconds,
        )
        self._current_partials: dict[tuple[str, Timeframe], CurrentBarSnapshot] = {}
        self._current_histories: dict[str, PlatformInstrumentHistory] = {}
        self._startup_replay_complete = False
        self._startup_replay_report: dict[str, Any] = {
            "complete": False,
            "bars_replayed": 0,
            "signal_events_evaluated": 0,
            "confirmed_signals_reconstructed": 0,
            "current_signals_restored": 0,
            "evaluations_created": 0,
            "duplicate_evaluations_prevented": 0,
            "replay_events_unsupported": 0,
            "limitations": (),
        }
        self._forming_evaluation_report: dict[str, Any] = {
            "state": "NOT_READY",
            "as_of": None,
            "candidates": 0,
            "evaluations_completed": 0,
            "signals_matched": 0,
            "limitations": ("authoritative partial evaluation has not run",),
        }

    @classmethod
    def from_database_url(
        cls,
        database_url: str,
        repository: SQLiteProjectionRepository,
        service: WalkingSkeletonService,
        instrument_ids: tuple[str, ...],
        *,
        stale_after_seconds: int,
        poll_seconds: int,
        signal_lifecycle: SignalLifecycleService | None = None,
    ) -> PlatformAuthorityRuntime:
        try:
            from hedgefund_market_data.pipeline import (  # type: ignore[import-not-found]
                PostgreSQLSpect8CanonicalReadService,
            )
        except ImportError as error:
            raise PlatformUnavailableError(
                "hedgefund-market-data must be installed for Platform authority"
            ) from error
        try:
            backend = PostgreSQLSpect8CanonicalReadService.from_database_url(
                database_url
            )
        except Exception:  # noqa: BLE001 - sanitize optional reader failures
            raise PlatformUnavailableError(
                "Market Data Platform PostgreSQL reader is unavailable"
            ) from None
        return cls(
            Spect8CanonicalReadServiceGateway(backend),
            repository,
            service,
            instrument_ids,
            stale_after_seconds=stale_after_seconds,
            poll_seconds=poll_seconds,
            backend=backend,
            signal_lifecycle=signal_lifecycle,
        )

    def run_once(
        self, *, available_as_of: datetime | None = None
    ) -> PlatformAuthorityRunResult:
        now = (available_as_of or SystemClock().now()).astimezone(timezone.utc)
        state = self._repository.platform_integration_state()
        previous_watermark = int(state["watermark_canonical_bar_id"]) if state else 0
        startup_replay = not self._startup_replay_complete
        try:
            batch = self._gateway.read(
                tuple(platform_instrument_id(item) for item in self._instrument_ids),
                available_as_of=now,
                after_canonical_bar_id=(
                    None if startup_replay else (previous_watermark or None)
                ),
                limits=SPECT8_PLATFORM_REPLAY_LIMITS,
            )
        except Exception:  # noqa: BLE001 - fail closed at the reader boundary
            self._connection_state = "UNAVAILABLE"
            self._freshness_state = "UNAVAILABLE"
            self._last_error = "Market Data Platform PostgreSQL reader is unavailable"
            raise PlatformUnavailableError(self._last_error) from None
        self._connection_state = "HEALTHY"
        self._current_partials = {
            (snapshot.instrument_id, snapshot.timeframe): snapshot
            for snapshot in (
                to_current_bar_snapshot(item)
                for item in batch.partial_bar_snapshots
                if item.price_type == "BID"
            )
        }
        self._live_readiness = _live_streaming_readiness(
            batch,
            self._instrument_ids,
            as_of=now,
            stale_after_seconds=self._stale_after_seconds,
        )

        if (
            not startup_replay
            and batch.bars
            and batch.watermark_canonical_bar_id <= previous_watermark
        ):
            self._freshness_state = "UNAVAILABLE"
            self._last_error = "Platform canonical watermark cannot progress"
            raise PlatformUnavailableError(self._last_error)

        first_activation = state is None
        histories = self._startup_gate(
            batch, first_activation=first_activation, now=now
        )
        self._current_histories = histories
        candidates = self._prepare_histories(
            batch,
            histories,
            first_activation=first_activation,
            startup_replay=startup_replay,
        )
        evaluation_keys: dict[tuple[str, datetime], list[str]] = {}
        evaluations_created = 0
        duplicates = 0
        last_evaluation: datetime | None = None
        confirmed_reconstructed = 0
        replay_limitations: list[str] = []
        for instrument_id, mode, timeframe, close_time in candidates:
            keys, created, replayed, evaluated_at, confirmed, limitation = (
                self._evaluate(
                    instrument_id,
                    mode,
                    timeframe,
                    close_time,
                    filter_history=histories[instrument_id],
                )
            )
            evaluation_keys.setdefault((instrument_id, close_time), []).extend(keys)
            evaluations_created += created
            duplicates += replayed
            confirmed_reconstructed += confirmed
            if limitation is not None:
                replay_limitations.append(limitation)
            if evaluated_at is not None:
                last_evaluation = max(last_evaluation or evaluated_at, evaluated_at)

        processed = self._processor.process_batch(
            batch,
            process_bar=lambda bar, mapped: tuple(
                evaluation_keys.get((mapped, bar.close_time), ())
            ),
        )
        last_processed = max(
            (
                bar.close_time
                for bar in bid_bars(batch.bars)
                if bar.timeframe in {"H1", "D1"}
            ),
            default=self._stored_last_processed(),
        )
        result = PlatformAuthorityRunResult(
            connection_state="HEALTHY",
            freshness_state="HEALTHY",
            previous_watermark=processed.previous_watermark,
            watermark_canonical_bar_id=processed.new_watermark,
            bootstrapped=state is None,
            consumed=processed.consumed,
            replayed_inputs=processed.replayed,
            revisions_detected=processed.revisions_detected,
            evaluations_created=evaluations_created,
            duplicate_evaluations_prevented=duplicates,
            last_processed_canonical_timestamp=last_processed,
            last_successful_evaluation_timestamp=(
                last_evaluation
                or self._repository.latest_provider_evaluation_time(
                    PLATFORM_PROVIDER_ID
                )
            ),
            bars_replayed=len(
                {
                    (instrument, timeframe, close)
                    for instrument, _, timeframe, close in candidates
                }
            ),
            signal_events_evaluated=len(candidates),
            confirmed_signals_reconstructed=confirmed_reconstructed,
            replay_events_unsupported=len(replay_limitations),
            replay_limitations=tuple(replay_limitations),
        )
        self._last_result = result
        self._last_error = None
        self._freshness_state = "HEALTHY"
        if startup_replay:
            self._startup_replay_report = {
                "complete": True,
                "bars_replayed": result.bars_replayed,
                "signal_events_evaluated": result.signal_events_evaluated,
                "confirmed_signals_reconstructed": (
                    result.confirmed_signals_reconstructed
                ),
                "current_signals_restored": (
                    len(self._signal_lifecycle.current_confirmed(now))
                    if self._signal_lifecycle is not None
                    else 0
                ),
                "evaluations_created": result.evaluations_created,
                "duplicate_evaluations_prevented": (
                    result.duplicate_evaluations_prevented
                ),
                "replay_events_unsupported": result.replay_events_unsupported,
                "limitations": result.replay_limitations,
            }
        self._startup_replay_complete = True
        self.forming_signals(as_of=now)
        return result

    def forming_signals(self, *, as_of: datetime) -> tuple[SignalSnapshot, ...]:
        """Evaluate authoritative current partials through the existing lifecycle."""

        if (
            self._signal_lifecycle is None
            or self._connection_state != "HEALTHY"
            or self._freshness_state != "HEALTHY"
            or self._historical_state != "READY"
            or not self._startup_replay_complete
            or self._live_readiness.get("overall_live_readiness") != "LIVE_READY"
        ):
            self._forming_evaluation_report = {
                "state": "NOT_READY",
                "as_of": primitive(as_of),
                "candidates": 0,
                "evaluations_completed": 0,
                "signals_matched": 0,
                "limitations": ("authoritative live-readiness gate is not ready",),
            }
            return ()
        now = as_of.astimezone(timezone.utc)
        results: list[SignalSnapshot] = []
        evaluations_completed = 0
        limitations: list[str] = []
        candidate_count = 0
        for instrument_id in self._instrument_ids:
            m30 = self._current_partials.get((instrument_id, Timeframe.M30))
            h1 = self._current_partials.get((instrument_id, Timeframe.H1))
            candidates: list[tuple[str, str, CurrentBarSnapshot]] = []
            if m30 is not None:
                candidates.append((FilterMode.MICRO.value, Timeframe.M30.value, m30))
            if h1 is not None:
                candidates.extend(
                    (
                        (FilterMode.MICRO.value, Timeframe.H1.value, h1),
                        (FilterMode.MACRO.value, Timeframe.H1.value, h1),
                    )
                )
                h1_history = self._repository.canonical_bar_objects(
                    PLATFORM_PROVIDER_ID, instrument_id, "H1"
                )
                h4_values = broker_partial_h4_from_h1_snapshots(h1_history, h1, now)
                if h4_values and not h4_values[-1].is_complete:
                    bar = h4_values[-1]
                    candidates.append(
                        (
                            FilterMode.MACRO.value,
                            Timeframe.H4.value,
                            CurrentBarSnapshot(
                                instrument_id=instrument_id,
                                timeframe=Timeframe.H4,
                                bar_start=bar.open_time,
                                bar_end=bar.close_time,
                                as_of=h1.as_of,
                                open=bar.open,
                                high=bar.high,
                                low=bar.low,
                                close=bar.close,
                                source_provider_id=PLATFORM_PROVIDER_ID,
                                component_ids=bar.source_candle_ids,
                                provenance="SPECT8_BROKER_PARTIAL_H4_FROM_PLATFORM_H1_V1",
                            ),
                        )
                    )
            for mode, timeframe, snapshot in candidates:
                candidate_count += 1
                directions, limitation = self._forming_directions(
                    instrument_id,
                    FilterMode(mode),
                    Timeframe(timeframe),
                    snapshot,
                )
                if limitation is not None:
                    limitations.append(limitation)
                    continue
                evaluations_completed += 1
                for direction in directions:
                    forming = self._signal_lifecycle.evaluate_forming(
                        instrument_id=instrument_id,
                        mode=mode,
                        timeframe=timeframe,
                        snapshot=snapshot,
                        as_of=now,
                        platform_healthy=True,
                        evaluated_direction=direction,
                        direction_was_evaluated=True,
                    )
                    if forming is not None:
                        results.append(forming)
        self._forming_evaluation_report = {
            "state": "READY" if evaluations_completed else "NOT_READY",
            "as_of": primitive(now),
            "candidates": candidate_count,
            "evaluations_completed": evaluations_completed,
            "signals_matched": len(results),
            "limitations": tuple(limitations),
        }
        return tuple(results)

    def _forming_directions(
        self,
        instrument_id: str,
        mode: FilterMode,
        timeframe: Timeframe,
        snapshot: CurrentBarSnapshot,
    ) -> tuple[tuple[str, ...], str | None]:
        """Run frozen strategy math with one real incomplete final bar, without writes."""

        history = self._current_histories.get(instrument_id)
        if history is None:
            return (), f"{instrument_id} historical forming context is unavailable"
        signal = self._repository.canonical_bar_objects(
            PLATFORM_PROVIDER_ID, instrument_id, timeframe.value
        )
        signal = tuple(bar for bar in signal if bar.close_time <= snapshot.as_of)[-29:]
        h1 = self._repository.canonical_bar_objects(
            PLATFORM_PROVIDER_ID, instrument_id, "H1"
        )
        h1 = tuple(bar for bar in h1 if bar.close_time <= snapshot.as_of)
        filter_daily = tuple(
            bar for bar in history.d1 if bar.close_time <= snapshot.as_of
        )[-10:]
        strategy_daily = self._repository.canonical_bar_objects(
            PLATFORM_PROVIDER_ID, instrument_id, "D1"
        )
        strategy_daily = tuple(
            bar for bar in strategy_daily if bar.close_time <= snapshot.as_of
        )[-10:]
        if filter_daily and all(
            "TEMPORARY_NATIVE_IG_FILTER_BOOTSTRAP" in bar.provider_adapter_version
            for bar in filter_daily
        ):
            strategy_daily = filter_daily
        weekly = tuple(bar for bar in history.w1 if bar.close_time <= snapshot.as_of)[
            -10:
        ]
        if not h1 or any(
            count < minimum
            for count, minimum in (
                (len(signal), 29),
                (len(filter_daily), 6),
                (len(strategy_daily), 6),
                (len(weekly), 6),
            )
        ):
            return (), (
                f"{instrument_id} {mode.value} {timeframe.value} forming history "
                "is insufficient"
            )
        filter_close = h1[-1].close_time
        daily_snapshot = None
        weekly_snapshot = None
        strategy_version = (
            CURRENT_D1_FILTER_V2 if mode is FilterMode.MICRO else CURRENT_W1_FILTER_V1
        )
        try:
            if mode is FilterMode.MICRO:
                daily_snapshot = build_daily_filter_snapshot(
                    provider=PLATFORM_PROVIDER_ID,
                    instrument=instrument_id,
                    as_of_h1_close=filter_close,
                    h1_bars=h1,
                    completed_d1_bars=filter_daily,
                )
            else:
                weekly_snapshot = build_w1_filter_snapshot(
                    provider=PLATFORM_PROVIDER_ID,
                    instrument=instrument_id,
                    as_of_h1_close=filter_close,
                    h1_bars=h1,
                    completed_w1_bars=weekly,
                )
        except ValueError as error:
            return (), (
                f"{instrument_id} {mode.value} {timeframe.value} forming filter: "
                f"{error}"
            )
        request = StrategyRequest(
            case_id=(
                f"platform-forming:{instrument_id}:{timeframe.value}:"
                f"{mode.value}:{snapshot.bar_start.isoformat()}:{snapshot.as_of.isoformat()}"
            ),
            strategy_id=strategy_version,
            timeframe=timeframe,
            evaluation_time=snapshot.as_of + timedelta(microseconds=1),
            signal_bars=(*signal, snapshot.to_spect8_bar()),
            daily_bars=strategy_daily,
            instrument=replace(
                self.registry.by_id(instrument_id),
                session_timezone="America/New_York",
                candle_boundary_convention=PROFILE_ID,
            ).to_strategy_metadata(),
            strategy_version=strategy_version,
            daily_filter_snapshot=daily_snapshot,
            filter_mode=mode,
            w1_filter_snapshot=weekly_snapshot,
            evaluation_kind="FORMING",
        )
        try:
            evaluation = self._service.evaluate_request(request).evaluation
        except ValueError as error:
            return (), (
                f"{instrument_id} {mode.value} {timeframe.value} forming strategy: "
                f"{error}"
            )
        classification = evaluation.classification
        if classification is None:
            return (), (
                f"{instrument_id} {mode.value} {timeframe.value} forming "
                "classification is unavailable"
            )
        return (
            tuple(
                direction
                for direction, matched in (
                    ("BUY", classification.confirmed_buy),
                    ("SELL", classification.confirmed_sell),
                )
                if matched
            ),
            None,
        )

    def _startup_gate(
        self,
        batch: PlatformReadBatch,
        *,
        first_activation: bool,
        now: datetime,
    ) -> dict[str, PlatformInstrumentHistory]:
        histories = {
            instrument_id: build_platform_history(batch, instrument_id)
            for instrument_id in self._instrument_ids
        }
        if first_activation:
            for instrument_id, history in histories.items():
                history.assert_bootstrap_ready()
        elif self._repository.platform_integration_state() is None:
            raise PlatformUnavailableError("durable Platform watermark is unreadable")
        else:
            for instrument_id in self._instrument_ids:
                persisted_counts = {
                    timeframe: len(
                        self._repository.canonical_bar_objects(
                            PLATFORM_PROVIDER_ID, instrument_id, timeframe
                        )
                    )
                    for timeframe in ("H1", "H4")
                }
                required = {
                    timeframe: SPECT8_PLATFORM_BOOTSTRAP_LIMITS[timeframe]
                    for timeframe in ("H1", "H4")
                }
                missing = {
                    timeframe: (persisted_counts[timeframe], minimum)
                    for timeframe, minimum in required.items()
                    if persisted_counts[timeframe] < minimum
                }
                if missing:
                    detail = ", ".join(
                        f"{timeframe}={actual}/{minimum}"
                        for timeframe, (actual, minimum) in missing.items()
                    )
                    raise PlatformUnavailableError(
                        f"{instrument_id} durable bootstrap history is unreadable: {detail}"
                    )
                history = histories[instrument_id]
                if not history.d1:
                    history = replace(
                        history,
                        d1=self._repository.canonical_bar_objects(
                            PLATFORM_PROVIDER_ID, instrument_id, "D1"
                        )[-SPECT8_PLATFORM_BOOTSTRAP_LIMITS["D1"] :],
                    )
                if not history.w1:
                    history = replace(
                        history,
                        w1=self._repository.canonical_bar_objects(
                            PLATFORM_PROVIDER_ID, instrument_id, "W1"
                        )[-SPECT8_PLATFORM_BOOTSTRAP_LIMITS["W1"] :],
                    )
                histories[instrument_id] = history
                if (
                    len(history.d1) < SPECT8_PLATFORM_BOOTSTRAP_LIMITS["D1"]
                    or len(history.w1) < SPECT8_PLATFORM_BOOTSTRAP_LIMITS["W1"]
                ):
                    raise PlatformUnavailableError(
                        f"{instrument_id} temporary filter bootstrap history is unreadable"
                    )

        self._historical_state = "READY"

        latest_by_instrument: dict[str, datetime] = {}
        for instrument_id in self._instrument_ids:
            platform_id = platform_instrument_id(instrument_id)
            availability = next(
                (
                    item
                    for item in batch.availability
                    if item.instrument_id == platform_id
                    and item.timeframe == "H1"
                    and item.price_type == "BID"
                ),
                None,
            )
            if availability is not None and not availability.valid:
                raise PlatformUnavailableError(
                    f"{instrument_id} canonical H1 availability is invalid"
                )
            candidates: list[datetime] = []
            if availability is not None and availability.latest_close_time is not None:
                candidates.append(availability.latest_close_time)
            history = histories[instrument_id]
            if history.h1:
                candidates.append(history.h1[-1].close_time)
            stored = self._repository.latest_canonical_close(
                PLATFORM_PROVIDER_ID, instrument_id, "H1"
            )
            if stored is not None:
                candidates.append(datetime.fromisoformat(stored.replace("Z", "+00:00")))
            if not candidates:
                raise PlatformUnavailableError(
                    f"{instrument_id} canonical H1 availability is unreadable"
                )
            latest = max(candidates)
            latest_by_instrument[instrument_id] = latest.astimezone(timezone.utc)

        expected = _expected_latest_forex_h1_close(now)
        stale = {
            instrument_id: int((expected - latest).total_seconds())
            for instrument_id, latest in latest_by_instrument.items()
            if expected - latest > timedelta(seconds=self._stale_after_seconds)
        }
        if stale:
            detail = ", ".join(
                f"{item} lag={seconds}s" for item, seconds in sorted(stale.items())
            )
            self._last_error = f"Platform canonical H1 is stale: {detail}"
            self._freshness_state = "STALE"
            raise PlatformStaleError(self._last_error)
        self._freshness_state = "HEALTHY"
        return histories

    def _prepare_histories(
        self,
        batch: PlatformReadBatch,
        histories: dict[str, PlatformInstrumentHistory],
        *,
        first_activation: bool,
        startup_replay: bool,
    ) -> tuple[tuple[str, FilterMode, Timeframe, datetime], ...]:
        new_h1_closes: dict[str, set[datetime]] = {
            item: set() for item in self._instrument_ids
        }
        if first_activation:
            for instrument_id, history in histories.items():
                canonical_d1 = tuple(
                    to_spect8_bar(bar)
                    for bar in first_accepted_versions(batch.bars)
                    if bar.timeframe == "D1"
                    and platform_instrument_id(instrument_id) == bar.instrument_id
                )
                persistent_filters = tuple(
                    bar
                    for bar in (
                        *history.d1,
                        *history.w1,
                    )
                    if "TEMPORARY_NATIVE_IG_FILTER_BOOTSTRAP"
                    not in bar.provider_adapter_version
                )
                self._repository.persist_canonical_bars(
                    (
                        *(to_spect8_bar(bar) for bar in history.m30),
                        *history.h1,
                        *history.h4,
                        *canonical_d1,
                        *persistent_filters,
                    )
                )
                new_h1_closes[instrument_id].add(history.h1[-1].close_time)
        else:
            translated = tuple(
                to_spect8_bar(bar)
                for bar in first_accepted_versions(batch.bars)
                if bar.timeframe in {"M30", "H1", "D1"}
            )
            self._repository.persist_canonical_bars(translated)
            for bar in translated:
                if bar.timeframe is Timeframe.H1:
                    new_h1_closes[bar.instrument_id].add(bar.close_time)

        candidates: list[tuple[str, FilterMode, Timeframe, datetime]] = []
        replay_day = batch.available_as_of.astimezone(NEW_YORK).date()
        for instrument_id in self._instrument_ids:
            h1 = self._repository.canonical_bar_objects(
                PLATFORM_PROVIDER_ID, instrument_id, "H1"
            )
            if not h1:
                continue
            h4_result = BrokerAlignedH4Aggregator().aggregate(
                h1[-SPECT8_PLATFORM_BOOTSTRAP_LIMITS["H1"] :],
                as_of=batch.available_as_of,
            )
            self._repository.persist_canonical_bars(h4_result.bars)
            closes = new_h1_closes[instrument_id]
            if startup_replay:
                series = {
                    Timeframe.M30: self._repository.canonical_bar_objects(
                        PLATFORM_PROVIDER_ID, instrument_id, "M30"
                    ),
                    Timeframe.H1: h1,
                    Timeframe.H4: h4_result.bars,
                }
                modes = {
                    Timeframe.M30: (FilterMode.MICRO,),
                    Timeframe.H1: (FilterMode.MICRO, FilterMode.MACRO),
                    Timeframe.H4: (FilterMode.MACRO,),
                }
                for timeframe, bars in series.items():
                    for index, bar in enumerate(bars):
                        if (
                            index >= 29
                            and bar.close_time <= batch.available_as_of
                            and bar.close_time.astimezone(NEW_YORK).date() == replay_day
                        ):
                            candidates.extend(
                                (instrument_id, mode, timeframe, bar.close_time)
                                for mode in modes[timeframe]
                            )
            else:
                candidates.extend(
                    (instrument_id, mode, Timeframe.H1, close_time)
                    for close_time in sorted(closes)
                    for mode in (FilterMode.MICRO, FilterMode.MACRO)
                )
                candidates.extend(
                    (instrument_id, FilterMode.MACRO, Timeframe.H4, bar.close_time)
                    for bar in h4_result.bars
                    if bar.close_time in closes
                )
                new_m30 = tuple(
                    to_spect8_bar(bar)
                    for bar in first_accepted_versions(batch.bars)
                    if bar.instrument_id == platform_instrument_id(instrument_id)
                    and bar.timeframe == "M30"
                )
                candidates.extend(
                    (instrument_id, FilterMode.MICRO, Timeframe.M30, bar.close_time)
                    for bar in new_m30
                )
        return tuple(
            sorted(
                set(candidates),
                key=lambda item: (item[3], item[0], item[2].value, item[1].value),
            )
        )

    def _evaluate(
        self,
        instrument_id: str,
        mode: FilterMode,
        timeframe: Timeframe,
        close_time: datetime,
        *,
        filter_history: PlatformInstrumentHistory,
    ) -> tuple[tuple[str, ...], int, int, datetime | None, int, str | None]:
        signal = self._repository.canonical_bar_objects(
            PLATFORM_PROVIDER_ID, instrument_id, timeframe.value
        )
        signal = tuple(bar for bar in signal if bar.close_time <= close_time)[-30:]
        h1 = self._repository.canonical_bar_objects(
            PLATFORM_PROVIDER_ID, instrument_id, "H1"
        )
        h1 = tuple(bar for bar in h1 if bar.close_time <= close_time)
        filter_daily = tuple(
            bar for bar in filter_history.d1 if bar.close_time <= close_time
        )[-10:]
        strategy_daily = self._repository.canonical_bar_objects(
            PLATFORM_PROVIDER_ID, instrument_id, "D1"
        )
        strategy_daily = tuple(
            bar for bar in strategy_daily if bar.close_time <= close_time
        )[-10:]
        if filter_daily and all(
            "TEMPORARY_NATIVE_IG_FILTER_BOOTSTRAP" in bar.provider_adapter_version
            for bar in filter_daily
        ):
            strategy_daily = filter_daily
        weekly = tuple(
            bar for bar in filter_history.w1 if bar.close_time <= close_time
        )[-10:]
        if (
            len(signal) < 30
            or not h1
            or len(filter_daily) < 6
            or len(strategy_daily) < 6
            or len(weekly) < 6
        ):
            return (
                (),
                0,
                0,
                None,
                0,
                (
                    f"{instrument_id} {mode.value} {timeframe.value} "
                    f"{close_time.isoformat()}: evaluation history is insufficient"
                ),
            )
        instrument = self.registry.by_id(instrument_id)
        keys: list[str] = []
        created = 0
        replayed = 0
        evaluated_at: datetime | None = None
        confirmed = 0
        strategy_version = (
            CURRENT_D1_FILTER_V2 if mode is FilterMode.MICRO else CURRENT_W1_FILTER_V1
        )
        daily_snapshot = None
        weekly_snapshot = None
        filter_close = h1[-1].close_time
        try:
            if mode is FilterMode.MICRO:
                daily_snapshot = build_daily_filter_snapshot(
                    provider=PLATFORM_PROVIDER_ID,
                    instrument=instrument_id,
                    as_of_h1_close=filter_close,
                    h1_bars=h1,
                    completed_d1_bars=filter_daily,
                )
                self._repository.persist_daily_filter_snapshot(daily_snapshot)
            else:
                weekly_snapshot = build_w1_filter_snapshot(
                    provider=PLATFORM_PROVIDER_ID,
                    instrument=instrument_id,
                    as_of_h1_close=filter_close,
                    h1_bars=h1,
                    completed_w1_bars=weekly,
                )
                self._repository.persist_w1_filter_snapshot(weekly_snapshot)
        except ValueError as error:
            return (
                (),
                0,
                0,
                None,
                0,
                (
                    f"platform-authority:{instrument_id}:{timeframe.value}:"
                    f"{mode.value}:{close_time.isoformat()}: {error}"
                ),
            )
        evaluation_time = close_time + timedelta(microseconds=1)
        request = StrategyRequest(
            case_id=(
                f"platform-authority:{instrument_id}:{timeframe.value}:"
                f"{mode.value}:{close_time.isoformat()}"
            ),
            strategy_id=strategy_version,
            timeframe=timeframe,
            evaluation_time=evaluation_time,
            signal_bars=signal,
            daily_bars=strategy_daily,
            instrument=replace(
                instrument,
                session_timezone="America/New_York",
                candle_boundary_convention=PROFILE_ID,
            ).to_strategy_metadata(),
            strategy_version=strategy_version,
            daily_filter_snapshot=daily_snapshot,
            filter_mode=mode,
            w1_filter_snapshot=weekly_snapshot,
        )
        try:
            evaluated = self._service.evaluate_request(request)
        except ValueError as error:
            return (), 0, 0, None, 0, str(error)
        outcome = self._service.process_evaluated(request, evaluated)
        keys.append(outcome.idempotency_key)
        created += int(not outcome.replayed)
        replayed += int(outcome.replayed)
        evaluated_at = evaluation_time
        classification = evaluated.evaluation.classification
        if classification is not None and self._signal_lifecycle is not None:
            completed_bar = evaluated.evaluation.signal_bar
            if completed_bar is not None:
                for direction, matched in (
                    ("BUY", classification.confirmed_buy),
                    ("SELL", classification.confirmed_sell),
                ):
                    if matched:
                        before = len(self._signal_lifecycle.all_confirmed())
                        self._signal_lifecycle.confirm(
                            instrument_id=instrument_id,
                            mode=mode.value,
                            timeframe=timeframe.value,
                            completed_bar=completed_bar,
                            as_of=evaluation_time,
                            direction=direction,
                            strategy_version=strategy_version,
                        )
                        confirmed += int(
                            len(self._signal_lifecycle.all_confirmed()) > before
                        )
        return tuple(keys), created, replayed, evaluated_at, confirmed, None

    def _stored_last_processed(self) -> datetime | None:
        values = tuple(
            self._repository.latest_canonical_close(PLATFORM_PROVIDER_ID, item, "H1")
            for item in self._instrument_ids
        )
        parsed = tuple(
            datetime.fromisoformat(item.replace("Z", "+00:00"))
            for item in values
            if item is not None
        )
        return max(parsed, default=None)

    async def run(self) -> None:
        self._stop.clear()
        self._running = True
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.to_thread(self.run_once)
                except Exception:  # noqa: BLE001 - keep fail-closed polling alive
                    if self._freshness_state != "STALE":
                        self._freshness_state = "UNAVAILABLE"
                    if self._last_error is None:
                        self._last_error = "Platform authoritative cycle failed closed"
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._poll_seconds
                    )
                except TimeoutError:
                    continue
        finally:
            self._running = False

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        result = self._last_result
        partial_and_streaming_ready = (
            self._live_readiness.get("overall_live_readiness") == "LIVE_READY"
        )
        evaluator_ready = self._signal_lifecycle is not None
        overall_live_readiness = (
            "LIVE_READY"
            if (
                self._connection_state == "HEALTHY"
                and self._freshness_state == "HEALTHY"
                and self._historical_state == "READY"
                and self._startup_replay_complete
                and evaluator_ready
                and self._forming_evaluation_report["state"] == "READY"
                and partial_and_streaming_ready
            )
            else "DEGRADED"
        )
        return {
            "running": self._running,
            "configured_authoritative_source": PLATFORM_PROVIDER_ID,
            "active_source": PLATFORM_PROVIDER_ID if result is not None else None,
            "connection_state": self._connection_state,
            "freshness_state": self._freshness_state,
            "historical_state": self._historical_state,
            "signal_evaluator_state": "READY" if evaluator_ready else "NOT_READY",
            "micro_state": "READY" if evaluator_ready else "NOT_READY",
            "macro_state": "READY" if evaluator_ready else "NOT_READY",
            "forming_evaluator_state": self._forming_evaluation_report["state"],
            "forming_evaluation": self._forming_evaluation_report,
            "watermark_canonical_bar_id": (
                result.watermark_canonical_bar_id if result else None
            ),
            "last_processed_canonical_timestamp": primitive(
                result.last_processed_canonical_timestamp if result else None
            ),
            "last_successful_evaluation_timestamp": primitive(
                result.last_successful_evaluation_timestamp if result else None
            ),
            "startup_replay": self._startup_replay_report,
            "last_error": self._last_error,
            **self._live_readiness,
            "overall_live_readiness": overall_live_readiness,
        }

    def close(self) -> None:
        close = getattr(self._backend, "close", None)
        if callable(close):
            close()


def _expected_latest_forex_h1_close(as_of: datetime) -> datetime:
    """Latest expected close, pinning the standard weekend closure."""

    now = as_of.astimezone(timezone.utc)
    local = now.astimezone(NEW_YORK)
    if local.weekday() == 5:
        close_date = local.date() - timedelta(days=1)
        return datetime.combine(close_date, NEW_YORK_CLOSE_TIME, NEW_YORK).astimezone(
            timezone.utc
        )
    if local.weekday() == 6 and local.time().replace(tzinfo=None) < time(18):
        close_date = local.date() - timedelta(days=2)
        return datetime.combine(close_date, NEW_YORK_CLOSE_TIME, NEW_YORK).astimezone(
            timezone.utc
        )
    if (
        local.weekday() == 4
        and local.time().replace(tzinfo=None) >= NEW_YORK_CLOSE_TIME
    ):
        return datetime.combine(local.date(), NEW_YORK_CLOSE_TIME, NEW_YORK).astimezone(
            timezone.utc
        )
    return now.replace(minute=0, second=0, microsecond=0)


def _live_streaming_readiness(
    batch: PlatformReadBatch | None,
    instrument_ids: tuple[str, ...],
    *,
    as_of: datetime,
    stale_after_seconds: int,
) -> dict[str, Any]:
    """Report canonical advancement and current persisted partials separately."""

    expected_h1 = _expected_latest_forex_h1_close(as_of)
    current = as_of.astimezone(timezone.utc)
    current_h1 = current.replace(minute=0, second=0, microsecond=0)
    current_m30 = current.replace(
        minute=30 if current.minute >= 30 else 0,
        second=0,
        microsecond=0,
    )
    expected = current_m30 if expected_h1 == current_h1 else expected_h1
    instruments: dict[str, dict[str, Any]] = {}
    for instrument_id in instrument_ids:
        platform_id = platform_instrument_id(instrument_id)
        availability = next(
            (
                item
                for item in (batch.availability if batch is not None else ())
                if item.instrument_id == platform_id
                and item.timeframe == "M30"
                and item.price_type == "BID"
            ),
            None,
        )
        latest = (
            availability.latest_close_time.astimezone(timezone.utc)
            if availability is not None and availability.latest_close_time is not None
            else None
        )
        lag_seconds = (
            max(0, int((expected - latest).total_seconds()))
            if latest is not None
            else None
        )
        ready = bool(
            availability is not None
            and availability.valid
            and latest is not None
            and lag_seconds is not None
            and lag_seconds <= stale_after_seconds
        )
        current_m30_open = current_m30
        current_h1_open = current_h1
        partials = {
            item.timeframe: item
            for item in (batch.partial_bar_snapshots if batch is not None else ())
            if item.instrument_id == platform_id and item.price_type == "BID"
        }
        m30_partial = partials.get("M30")
        h1_partial = partials.get("H1")
        partial_m30_ready = bool(
            m30_partial is not None
            and m30_partial.status == "PARTIAL"
            and m30_partial.bar_start == current_m30_open
            and m30_partial.as_of <= current
            and (current - m30_partial.as_of).total_seconds() <= stale_after_seconds
        )
        partial_h1_ready = bool(
            h1_partial is not None
            and h1_partial.status == "PARTIAL"
            and h1_partial.bar_start == current_h1_open
            and h1_partial.as_of <= current
            and (current - h1_partial.as_of).total_seconds() <= stale_after_seconds
        )
        partial_ready = partial_m30_ready and partial_h1_ready
        instruments[instrument_id] = {
            "state": "READY" if ready else "NOT_READY",
            "latest_canonical_m30_timestamp": primitive(latest),
            "expected_latest_m30_timestamp": primitive(expected),
            "lag_seconds": lag_seconds,
            "partial_state": "READY" if partial_ready else "NOT_READY",
            "partial_m30": primitive(m30_partial),
            "partial_h1": primitive(h1_partial),
        }
    streaming_ready = bool(instruments) and all(
        item["state"] == "READY" for item in instruments.values()
    )
    partial_ready = bool(instruments) and all(
        item["partial_state"] == "READY" for item in instruments.values()
    )
    return {
        "streaming_state": "READY" if streaming_ready else "NOT_READY",
        "partial_data_state": "READY" if partial_ready else "NOT_READY",
        "overall_live_readiness": (
            "LIVE_READY" if streaming_ready and partial_ready else "DEGRADED"
        ),
        "live_instruments": instruments,
    }
