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
from .platform_adapter import (
    PLATFORM_PROVIDER_ID,
    SPECT8_PLATFORM_BOOTSTRAP_LIMITS,
    PlatformCanonicalReadGateway,
    PlatformIncrementalProcessor,
    PlatformReadBatch,
    Spect8CanonicalReadServiceGateway,
    bid_bars,
    build_platform_history,
    first_accepted_versions,
    platform_instrument_id,
    to_spect8_bar,
)
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from .registry import CanonicalInstrumentRegistry, twelve_data_instruments
from .session_boundaries import NEW_YORK, NEW_YORK_CLOSE_TIME

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
    ) -> None:
        if not instrument_ids or any(
            item not in APPROVED_PLATFORM_AUTHORITY_INSTRUMENTS
            for item in instrument_ids
        ):
            raise ValueError("Platform authority instruments must be an approved subset")
        self._gateway = gateway
        self._repository = repository
        self._service = service
        self._instrument_ids = instrument_ids
        self._stale_after_seconds = stale_after_seconds
        self._poll_seconds = poll_seconds
        self._backend = backend
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
            backend = PostgreSQLSpect8CanonicalReadService.from_database_url(database_url)
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
        )

    def run_once(self, *, available_as_of: datetime | None = None) -> PlatformAuthorityRunResult:
        now = (available_as_of or SystemClock().now()).astimezone(timezone.utc)
        state = self._repository.platform_integration_state()
        previous_watermark = int(state["watermark_canonical_bar_id"]) if state else 0
        try:
            batch = self._gateway.read(
                tuple(platform_instrument_id(item) for item in self._instrument_ids),
                available_as_of=now,
                after_canonical_bar_id=(previous_watermark or None),
                limits=SPECT8_PLATFORM_BOOTSTRAP_LIMITS,
            )
        except Exception:  # noqa: BLE001 - fail closed at the reader boundary
            self._connection_state = "UNAVAILABLE"
            self._freshness_state = "UNAVAILABLE"
            self._last_error = "Market Data Platform PostgreSQL reader is unavailable"
            raise PlatformUnavailableError(self._last_error) from None
        self._connection_state = "HEALTHY"

        if batch.bars and batch.watermark_canonical_bar_id <= previous_watermark:
            self._freshness_state = "UNAVAILABLE"
            self._last_error = "Platform canonical watermark cannot progress"
            raise PlatformUnavailableError(self._last_error)

        histories = self._startup_gate(batch, first_activation=state is None, now=now)
        candidates = self._prepare_histories(batch, histories)
        evaluation_keys: dict[tuple[str, datetime], list[str]] = {}
        evaluations_created = 0
        duplicates = 0
        last_evaluation: datetime | None = None
        for instrument_id, timeframe, close_time in candidates:
            keys, created, replayed, evaluated_at = self._evaluate(
                instrument_id, timeframe, close_time
            )
            evaluation_keys.setdefault((instrument_id, close_time), []).extend(keys)
            evaluations_created += created
            duplicates += replayed
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
                or self._repository.latest_provider_evaluation_time(PLATFORM_PROVIDER_ID)
            ),
        )
        self._last_result = result
        self._last_error = None
        self._freshness_state = "HEALTHY"
        return result

    def _startup_gate(
        self,
        batch: PlatformReadBatch,
        *,
        first_activation: bool,
        now: datetime,
    ) -> dict[str, object]:
        histories: dict[str, object] = {}
        if first_activation:
            for instrument_id in self._instrument_ids:
                history = build_platform_history(batch, instrument_id)
                history.assert_bootstrap_ready()
                histories[instrument_id] = history
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
                    for timeframe in ("H1", "H4", "D1")
                }
                required = {"H1": 1_177, "H4": 30, "D1": 10}
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
            latest = availability.latest_close_time if availability is not None else None
            if latest is None and first_activation:
                history = histories[instrument_id]
                latest = history.h1[-1].close_time  # type: ignore[attr-defined]
            if latest is None:
                stored = self._repository.latest_canonical_close(
                    PLATFORM_PROVIDER_ID, instrument_id, "H1"
                )
                latest = (
                    datetime.fromisoformat(stored.replace("Z", "+00:00"))
                    if stored is not None
                    else None
                )
            if latest is None:
                raise PlatformUnavailableError(
                    f"{instrument_id} canonical H1 availability is unreadable"
                )
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
        self, batch: PlatformReadBatch, histories: dict[str, object]
    ) -> tuple[tuple[str, Timeframe, datetime], ...]:
        first_activation = bool(histories)
        new_h1_closes: dict[str, set[datetime]] = {
            item: set() for item in self._instrument_ids
        }
        if first_activation:
            for instrument_id, raw_history in histories.items():
                history = raw_history
                self._repository.persist_canonical_bars(
                    (*history.h1, *history.h4, *history.d1)  # type: ignore[attr-defined]
                )
                new_h1_closes[instrument_id].add(
                    history.h1[-1].close_time  # type: ignore[attr-defined]
                )
        else:
            translated = tuple(
                to_spect8_bar(bar)
                for bar in first_accepted_versions(batch.bars)
                if bar.timeframe in {"H1", "D1"}
            )
            self._repository.persist_canonical_bars(translated)
            for bar in translated:
                if bar.timeframe is Timeframe.H1:
                    new_h1_closes[bar.instrument_id].add(bar.close_time)

        candidates: list[tuple[str, Timeframe, datetime]] = []
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
            if first_activation:
                candidates.append((instrument_id, Timeframe.H1, max(closes)))
                if h4_result.bars:
                    candidates.append(
                        (instrument_id, Timeframe.H4, h4_result.bars[-1].close_time)
                    )
            else:
                candidates.extend(
                    (instrument_id, Timeframe.H1, close_time)
                    for close_time in sorted(closes)
                )
                candidates.extend(
                    (instrument_id, Timeframe.H4, bar.close_time)
                    for bar in h4_result.bars
                    if bar.close_time in closes
                )
        return tuple(sorted(set(candidates), key=lambda item: (item[2], item[0], item[1].value)))

    def _evaluate(
        self, instrument_id: str, timeframe: Timeframe, close_time: datetime
    ) -> tuple[tuple[str, ...], int, int, datetime | None]:
        signal = self._repository.canonical_bar_objects(
            PLATFORM_PROVIDER_ID, instrument_id, timeframe.value
        )
        signal = tuple(bar for bar in signal if bar.close_time <= close_time)[-30:]
        h1 = self._repository.canonical_bar_objects(
            PLATFORM_PROVIDER_ID, instrument_id, "H1"
        )
        h1 = tuple(bar for bar in h1 if bar.close_time <= close_time)
        daily = self._repository.canonical_bar_objects(
            PLATFORM_PROVIDER_ID, instrument_id, "D1"
        )
        daily = tuple(bar for bar in daily if bar.close_time <= close_time)[-10:]
        if len(signal) < 30 or len(daily) < 6:
            raise PlatformUnavailableError(
                f"{instrument_id} {timeframe.value} evaluation history is insufficient"
            )
        instrument = self.registry.by_id(instrument_id)
        keys: list[str] = []
        created = 0
        replayed = 0
        evaluated_at: datetime | None = None
        for mode in (FilterMode.MICRO, FilterMode.MACRO):
            strategy_version = (
                CURRENT_D1_FILTER_V2 if mode is FilterMode.MICRO else CURRENT_W1_FILTER_V1
            )
            latest = self._repository.latest_authoritative_evaluation_close(
                instrument_id, timeframe.value, strategy_version
            )
            if latest is not None and datetime.fromisoformat(
                latest.replace("Z", "+00:00")
            ) >= close_time:
                replayed += 1
                continue
            daily_snapshot = None
            weekly_snapshot = None
            if mode is FilterMode.MICRO:
                daily_snapshot = build_daily_filter_snapshot(
                    provider=PLATFORM_PROVIDER_ID,
                    instrument=instrument_id,
                    as_of_h1_close=close_time,
                    h1_bars=h1,
                    completed_d1_bars=daily,
                )
                self._repository.persist_daily_filter_snapshot(daily_snapshot)
            else:
                weekly_snapshot = build_w1_filter_snapshot(
                    provider=PLATFORM_PROVIDER_ID,
                    instrument=instrument_id,
                    as_of_h1_close=close_time,
                    h1_bars=h1,
                )
                self._repository.persist_w1_filter_snapshot(weekly_snapshot)
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
                daily_bars=daily,
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
            outcome = self._service.process_request(request)
            keys.append(outcome.idempotency_key)
            created += int(not outcome.replayed)
            replayed += int(outcome.replayed)
            evaluated_at = evaluation_time
        return tuple(keys), created, replayed, evaluated_at

    def _stored_last_processed(self) -> datetime | None:
        values = tuple(
            self._repository.latest_canonical_close(
                PLATFORM_PROVIDER_ID, item, "H1"
            )
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
                    self._freshness_state = "UNAVAILABLE"
                    if self._last_error is None:
                        self._last_error = "Platform authoritative cycle failed closed"
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    continue
        finally:
            self._running = False

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        result = self._last_result
        return {
            "running": self._running,
            "configured_authoritative_source": PLATFORM_PROVIDER_ID,
            "active_source": PLATFORM_PROVIDER_ID if result is not None else None,
            "connection_state": self._connection_state,
            "freshness_state": self._freshness_state,
            "watermark_canonical_bar_id": (
                result.watermark_canonical_bar_id if result else None
            ),
            "last_processed_canonical_timestamp": primitive(
                result.last_processed_canonical_timestamp if result else None
            ),
            "last_successful_evaluation_timestamp": primitive(
                result.last_successful_evaluation_timestamp if result else None
            ),
            "last_error": self._last_error,
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
    if local.weekday() == 4 and local.time().replace(tzinfo=None) >= NEW_YORK_CLOSE_TIME:
        return datetime.combine(local.date(), NEW_YORK_CLOSE_TIME, NEW_YORK).astimezone(
            timezone.utc
        )
    return now.replace(minute=0, second=0, microsecond=0)
