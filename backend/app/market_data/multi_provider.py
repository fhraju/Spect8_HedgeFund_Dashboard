from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..domain import FilterMode, Timeframe
from ..engine.models import CURRENT_D1_FILTER_V2, CURRENT_W1_FILTER_V1
from .models import (
    CanonicalInstrument,
    HealthState,
    MarketDataProviderError,
    ProviderClosedBar,
    ProviderHealth,
    ProviderHistory,
    ProviderIdentity,
)
from .rate_limiter import SlidingWindowRateLimiter
from .credit_budget import DailyCreditBudgetGuard
from .scheduler import RoundRobinScanScheduler
from .twelve_data_provider import TwelveDataHttpTransport, TwelveDataProvider
from .polling_policy import instrument_needs_poll
from ..repository import SQLiteProjectionRepository


@dataclass(frozen=True, slots=True)
class MultiProviderTelemetry:
    network_attempts: int
    successful_requests: int
    failed_requests: int
    rate_limit_responses: int
    network_timeouts: int
    cache_hits: int
    duplicate_triggers_prevented: int
    series_attempts: dict[str, int]
    completed_discoveries: dict[str, int]
    request_starts_utc: list[str]
    last_scan_sequence: list[str]
    provider_errors_or_retries: int


class MultiInstrumentTwelveDataProvider:
    """Fair enabled-universe facade over isolated per-instrument adapters."""

    def __init__(
        self,
        api_key: str,
        instruments: tuple[CanonicalInstrument, ...],
        *,
        transport: TwelveDataHttpTransport | None = None,
        limiter: SlidingWindowRateLimiter | None = None,
        max_requests_per_minute: int = 8,
        min_interval_seconds: float = 8.0,
        max_retries_per_instrument: int = 2,
        stale_after_seconds: int = 7200,
        providers: dict[str, Any] | None = None,
        credit_budget: DailyCreditBudgetGuard | None = None,
        repository: SQLiteProjectionRepository | None = None,
    ) -> None:
        if not instruments:
            raise ValueError("scanner requires at least one instrument")
        if max_retries_per_instrument < 0 or max_retries_per_instrument > 2:
            raise ValueError("scanner retries must be between 0 and 2")
        self._instruments = instruments
        self._visible = tuple(item for item in instruments if item.enabled)
        self._enabled = tuple(
            item for item in self._visible if item.polling_enabled
        )
        self._unavailable = {
            item.instrument_id: item
            for item in self._visible
            if not item.polling_enabled
        }
        self._limiter = limiter or SlidingWindowRateLimiter(
            max_requests=max_requests_per_minute,
            # A small monotonic guard keeps independently observed UTC starts
            # at or above the configured eight-second boundary.
            min_interval_seconds=min_interval_seconds + 0.1,
        )
        self._scheduler = RoundRobinScanScheduler()
        self._max_retries = max_retries_per_instrument
        self._stale_after_seconds = stale_after_seconds
        self._providers = providers or {
            item.instrument_id: TwelveDataProvider(
                api_key,
                transport=transport,
                canonical_instrument=item,
                max_attempts=1,
                rate_limiter=self._limiter,
                bootstrap_latest_h4=True,
                credit_budget=credit_budget,
            )
            for item in self._enabled
        }
        missing = {item.instrument_id for item in self._enabled}.difference(
            self._providers
        )
        if missing:
            raise ValueError(
                f"missing instrument providers: {', '.join(sorted(missing))}"
            )
        self._scan_failures: dict[str, MarketDataProviderError] = {}
        self._repository = repository
        self._startup_complete = False
        self._skipped_fresh: set[str] = set()
        self._persisted_latest: dict[str, datetime] = {}
        self._last_sequence: tuple[str, ...] = ()
        self._overlap_prevented = 0
        self._identity = ProviderIdentity(
            provider_id="TWELVE_DATA",
            display_name="Twelve Data",
            adapter_version="2.0-scanner",
            synthetic=False,
        )

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def discover_instruments(self) -> tuple[CanonicalInstrument, ...]:
        return self._instruments

    def fetch_completed_bars(self, as_of: datetime) -> tuple[ProviderClosedBar, ...]:
        results: list[ProviderClosedBar] = []
        eligible = self._eligible_instruments(as_of)
        with self._scheduler.cycle(eligible) as ordered:
            if ordered is None:
                self._overlap_prevented += 1
                return ()
            self._last_sequence = tuple(item.instrument_id for item in ordered)
            queue = deque((item.instrument_id, 0) for item in ordered)
            while queue:
                instrument_id, retry = queue.popleft()
                provider = self._providers[instrument_id]
                setter = getattr(provider, "set_request_category", None)
                if callable(setter):
                    setter(
                        "retry"
                        if retry
                        else (
                            "bootstrap" if not self._startup_complete else "scheduled"
                        )
                    )
                try:
                    found = provider.fetch_completed_bars(as_of)
                except MarketDataProviderError as error:
                    self._scan_failures[instrument_id] = error
                    if error.retryable and retry < self._max_retries:
                        queue.append((instrument_id, retry + 1))
                    continue
                self._scan_failures.pop(instrument_id, None)
                results.extend(found)
        self._startup_complete = True
        return tuple(results)

    def _eligible_instruments(self, as_of: datetime) -> tuple[CanonicalInstrument, ...]:
        if self._repository is None:
            return self._enabled
        eligible: list[CanonicalInstrument] = []
        self._skipped_fresh.clear()
        self._persisted_latest.clear()
        for instrument in self._enabled:
            latest_text = self._repository.latest_canonical_close(
                instrument.provider_id,
                instrument.instrument_id,
                Timeframe.H1.value,
            )
            latest = (
                datetime.fromisoformat(latest_text.replace("Z", "+00:00")).astimezone(
                    timezone.utc
                )
                if latest_text
                else None
            )
            evaluation_caught_up = True
            for timeframe in (Timeframe.H1, Timeframe.H4):
                canonical = self._repository.latest_canonical_close(
                    instrument.provider_id,
                    instrument.instrument_id,
                    timeframe.value,
                )
                if canonical is None:
                    continue
                for strategy_id in (CURRENT_D1_FILTER_V2, CURRENT_W1_FILTER_V1):
                    evaluated = self._repository.latest_evaluation_close(
                        instrument.provider_id,
                        instrument.instrument_id,
                        timeframe.value,
                        strategy_id,
                    )
                    if evaluated is None or evaluated < canonical:
                        evaluation_caught_up = False
                        break
                if not evaluation_caught_up:
                    break
            if not evaluation_caught_up or instrument_needs_poll(
                instrument,
                latest_completed_close=latest,
                as_of=as_of,
            ):
                eligible.append(instrument)
            elif latest is not None:
                self._skipped_fresh.add(instrument.instrument_id)
                self._persisted_latest[instrument.instrument_id] = latest
        return tuple(eligible)

    def fetch_required_history(
        self, closed_bar: ProviderClosedBar, as_of: datetime
    ) -> ProviderHistory:
        return self._providers[closed_bar.instrument_id].fetch_required_history(
            closed_bar, as_of
        )

    def fetch_required_history_for_filter_mode(
        self,
        closed_bar: ProviderClosedBar,
        as_of: datetime,
        filter_mode: FilterMode,
    ) -> ProviderHistory:
        provider = self._providers[closed_bar.instrument_id]
        mode_fetch = getattr(provider, "fetch_required_history_for_filter_mode", None)
        if callable(mode_fetch):
            return mode_fetch(closed_bar, as_of, filter_mode)
        return provider.fetch_required_history(closed_bar, as_of)

    def set_filter_mode(self, filter_mode: FilterMode) -> None:
        for provider in self._providers.values():
            setter = getattr(provider, "set_filter_mode", None)
            if callable(setter):
                setter(filter_mode)

    def set_resume_cursor(
        self,
        instrument_id: str,
        timeframe: Timeframe,
        close_time: datetime | None,
    ) -> None:
        provider = self._providers.get(instrument_id)
        if provider is not None:
            provider.set_resume_cursor(timeframe, close_time)

    def instrument_health(self, instrument_id: str, as_of: datetime) -> ProviderHealth:
        unavailable = self._unavailable.get(instrument_id)
        if unavailable is not None:
            return ProviderHealth(
                provider_id=self.identity.provider_id,
                state=HealthState.DATA_UNAVAILABLE,
                checked_at=as_of,
                latest_completed_close=None,
                freshness_seconds=None,
                detail=(
                    f"{unavailable.display_name} is scanner-visible but unavailable "
                    "from the configured provider; no request was made."
                ),
                synthetic=False,
            )
        if instrument_id in self._skipped_fresh:
            latest = self._persisted_latest[instrument_id]
            return ProviderHealth(
                provider_id=self.identity.provider_id,
                state=HealthState.HEALTHY,
                checked_at=as_of,
                latest_completed_close=latest,
                freshness_seconds=max(0, int((as_of - latest).total_seconds())),
                detail="Persisted completed candle is current; provider request skipped.",
                synthetic=False,
            )
        health = self._providers[instrument_id].health(as_of)
        instrument = next(
            item for item in self._enabled if item.instrument_id == instrument_id
        )
        if (
            health.latest_completed_close is not None
            and instrument_needs_poll(
                instrument,
                latest_completed_close=health.latest_completed_close,
                as_of=as_of,
            )
            and health.state in {HealthState.HEALTHY, HealthState.RECOVERED}
        ):
            return ProviderHealth(
                provider_id=health.provider_id,
                state=HealthState.STALE,
                checked_at=health.checked_at,
                latest_completed_close=health.latest_completed_close,
                freshness_seconds=health.freshness_seconds,
                detail="Latest completed candle is stale for this instrument.",
                synthetic=False,
            )
        return health

    def health(self, as_of: datetime) -> ProviderHealth:
        health = [
            self.instrument_health(item.instrument_id, as_of) for item in self._enabled
        ]
        priority = {
            HealthState.QUARANTINED: 5,
            HealthState.DATA_UNAVAILABLE: 4,
            HealthState.INSUFFICIENT_HISTORY: 3,
            HealthState.STALE: 2,
            HealthState.RECOVERED: 1,
            HealthState.HEALTHY: 0,
        }
        worst = max(health, key=lambda item: priority[item.state])
        latest = max(
            (
                item.latest_completed_close
                for item in health
                if item.latest_completed_close
            ),
            default=None,
        )
        return ProviderHealth(
            provider_id=self.identity.provider_id,
            state=worst.state,
            checked_at=as_of,
            latest_completed_close=latest,
            freshness_seconds=(
                max(0, int((as_of - latest).total_seconds())) if latest else None
            ),
            detail=(
                f"{sum(item.state in {HealthState.HEALTHY, HealthState.RECOVERED} for item in health)}"
                f"/{len(health)} pollable instruments healthy."
            ),
            synthetic=False,
        )

    def scan_failures(self) -> dict[str, MarketDataProviderError]:
        return dict(self._scan_failures)

    def telemetry(self) -> MultiProviderTelemetry:
        values = [provider.telemetry() for provider in self._providers.values()]
        series = {timeframe.value: 0 for timeframe in Timeframe}
        discoveries = {timeframe.value: 0 for timeframe in Timeframe}
        for value in values:
            for key in series:
                series[key] += value.series_attempts[key]
                discoveries[key] += value.completed_discoveries[key]
        return MultiProviderTelemetry(
            network_attempts=sum(value.network_attempts for value in values),
            successful_requests=sum(value.successful_requests for value in values),
            failed_requests=sum(value.failed_requests for value in values),
            rate_limit_responses=sum(value.rate_limit_responses for value in values),
            network_timeouts=sum(value.network_timeouts for value in values),
            cache_hits=sum(value.cache_hits for value in values),
            duplicate_triggers_prevented=sum(
                value.duplicate_triggers_prevented for value in values
            )
            + self._overlap_prevented,
            series_attempts=series,
            completed_discoveries=discoveries,
            request_starts_utc=[
                value.isoformat().replace("+00:00", "Z")
                for value in self._limiter.request_starts_utc()
            ],
            last_scan_sequence=list(self._last_sequence),
            provider_errors_or_retries=sum(
                value.provider_errors_or_retries for value in values
            ),
        )

    @property
    def limiter(self) -> SlidingWindowRateLimiter:
        return self._limiter

    def provider_for(self, instrument_id: str) -> TwelveDataProvider:
        return self._providers[instrument_id]
