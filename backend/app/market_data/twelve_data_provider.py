from __future__ import annotations

import http.client
import json
import socket
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlencode

from ..domain import Timeframe
from ..engine.strategy import STRATEGY_ID
from .closed_bar import (
    MIN_DAILY_HISTORY,
    MIN_SIGNAL_HISTORY,
    TIMEFRAME_STEP,
)
from .credit_budget import CreditBudgetExhausted, DailyCreditBudgetGuard
from .models import (
    CanonicalInstrument,
    HealthState,
    MarketDataProviderError,
    ProviderClosedBar,
    ProviderErrorCode,
    ProviderHealth,
    ProviderHistory,
    ProviderIdentity,
    ProviderProfile,
    RawProviderCandle,
    TimestampSemantics,
    InstrumentKind,
)
from .rate_limiter import SlidingWindowRateLimiter
from .session_boundaries import is_expected_forex_weekend_gap
from .us_equity_calendar import (
    EquityH1Disposition,
    classify_etf_h1_open,
    is_expected_us_equity_gap,
    latest_expected_etf_h1_close,
    session_for_instant,
)

PROVIDER_ID = "TWELVE_DATA"
PROVIDER_SYMBOL = "EUR/USD"
SUPPORTED_TIMEFRAMES = (Timeframe.H1, Timeframe.H4, Timeframe.D1)
DAILY_AGGREGATION_H1_BARS = (24 * 21) + 1
INTERVALS = {
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
}
MAX_CATCHUP_BARS = 168
ADAPTER_VERSION = "1.1"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class TwelveDataHttpTransport(Protocol):
    def get(
        self,
        path: str,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> HttpResponse:
        ...


class TransportTimeoutError(TimeoutError):
    pass


class TransportConnectionError(ConnectionError):
    pass


class StdlibTwelveDataHttpTransport:
    """Small HTTPS transport with independently bounded connect/read timeouts."""

    def __init__(self, host: str = "api.twelvedata.com") -> None:
        self._host = host

    def get(
        self,
        path: str,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> HttpResponse:
        connection = http.client.HTTPSConnection(
            self._host,
            timeout=connect_timeout,
        )
        try:
            connection.connect()
            if connection.sock is not None:
                connection.sock.settimeout(read_timeout)
            target = f"{path}?{urlencode(params)}"
            connection.request("GET", target, headers=dict(headers))
            response = connection.getresponse()
            body = response.read()
            return HttpResponse(
                status_code=response.status,
                headers={key: value for key, value in response.getheaders()},
                body=body,
            )
        except (socket.timeout, TimeoutError) as error:
            raise TransportTimeoutError("Twelve Data request timed out.") from error
        except (OSError, http.client.HTTPException) as error:
            raise TransportConnectionError("Twelve Data connection failed.") from error
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class ProviderDiagnostics:
    received_count: int = 0
    completed_count: int = 0
    forming_filtered_count: int = 0
    duplicate_count: int = 0
    gap_count: int = 0
    out_of_order_count: int = 0
    structurally_partial_count: int = 0
    outside_regular_session_count: int = 0


@dataclass(frozen=True, slots=True)
class ProviderTelemetry:
    network_attempts: int
    successful_requests: int
    failed_requests: int
    rate_limit_responses: int
    network_timeouts: int
    cache_hits: int
    duplicate_triggers_prevented: int
    series_attempts: dict[str, int]
    completed_discoveries: dict[str, int]
    requests_used_this_minute: int
    estimated_credits_used_today: int
    last_h1_request: datetime | None
    next_expected_h1_close: datetime | None
    duplicate_bars_ignored: int
    forming_bars_excluded: int
    provider_errors_or_retries: int


@dataclass(frozen=True, slots=True)
class ProviderRequestObservation:
    started_at_utc: datetime
    finished_at_utc: datetime
    status: str


class TwelveDataProvider:
    """Twelve Data REST adapter for the Phase 2C EUR/USD pilot."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: TwelveDataHttpTransport | None = None,
        instrument: str = PROVIDER_SYMBOL,
        timeframes: tuple[Timeframe, ...] = SUPPORTED_TIMEFRAMES,
        connect_timeout: float = 3.0,
        read_timeout: float = 10.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.25,
        max_retry_after_seconds: float = 60.0,
        max_catchup_bars: int = MAX_CATCHUP_BARS,
        sleep: Callable[[float], None] = time.sleep,
        canonical_instrument: CanonicalInstrument | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        bootstrap_latest_h4: bool = False,
        credit_budget: DailyCreditBudgetGuard | None = None,
        request_category: str = "runtime",
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError(
                "TWELVE_DATA_API_KEY is required for the twelve_data provider."
            )
        if canonical_instrument is not None:
            if canonical_instrument.provider_id != PROVIDER_ID:
                raise ValueError("canonical instrument provider must be TWELVE_DATA")
            instrument = canonical_instrument.provider_symbol
        elif instrument != PROVIDER_SYMBOL:
            raise ValueError("Use canonical_instrument for scanner symbols.")
        if set(timeframes) != set(SUPPORTED_TIMEFRAMES):
            raise ValueError("Phase 2C requires exactly H1, H4 and D1.")
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("HTTP timeouts must be positive.")
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("max_attempts must be between 1 and 5.")
        if backoff_seconds < 0 or max_retry_after_seconds < 0:
            raise ValueError("retry delays cannot be negative.")
        if not 24 <= max_catchup_bars <= 720:
            raise ValueError("max_catchup_bars must be between 24 and 720.")

        self._api_key = api_key
        self._transport = transport or StdlibTwelveDataHttpTransport()
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._max_retry_after_seconds = max_retry_after_seconds
        self._sleep = sleep
        self._rate_limiter = rate_limiter
        self._request_observations: list[ProviderRequestObservation] = []
        self._bootstrap_latest_h4 = bootstrap_latest_h4
        self._credit_budget = credit_budget
        self._request_category = request_category
        self._max_catchup_bars = max_catchup_bars
        self._identity = ProviderIdentity(
            provider_id=PROVIDER_ID,
            display_name="Twelve Data",
            adapter_version=ADAPTER_VERSION,
            synthetic=False,
        )
        self._instrument = canonical_instrument or CanonicalInstrument(
            instrument_id=PROVIDER_SYMBOL,
            provider_id=PROVIDER_ID,
            provider_symbol=PROVIDER_SYMBOL,
            display_name="Euro / US Dollar",
            asset_class="FOREX",
            point_size=Decimal("0.00001"),
            tick_size=None,
            price_precision=5,
            tick_value_usd=None,
            conversion_rate_to_usd=None,
            contract_min=None,
            contract_max=None,
            contract_step=None,
            minimum_stop_distance_points=None,
            quote_currency="USD",
            profit_currency="USD",
            session_timezone="UTC",
            candle_boundary_convention=(
                "Twelve Data intraday UTC; D1 aggregated at 17:00 America/New_York"
            ),
            available_timeframes=SUPPORTED_TIMEFRAMES,
            strategy_id=STRATEGY_ID,
            display_symbol=PROVIDER_SYMBOL,
            enabled=True,
            synthetic=False,
        )
        self._cache: dict[
            tuple[Timeframe, datetime, int], tuple[RawProviderCandle, ...]
        ] = {}
        self._latest_series: dict[Timeframe, tuple[RawProviderCandle, ...]] = {}
        self._last_fetch_as_of: dict[Timeframe, datetime] = {}
        self._last_trigger_close: dict[Timeframe, datetime] = {}
        self._diagnostics: dict[Timeframe, ProviderDiagnostics] = {}
        self._state = HealthState.DATA_UNAVAILABLE
        self._detail = "No Twelve Data request has completed."
        self._latest_completed_close: datetime | None = None
        self._network_attempts = 0
        self._successful_requests = 0
        self._failed_requests = 0
        self._rate_limit_responses = 0
        self._network_timeouts = 0
        self._cache_hits = 0
        self._duplicate_triggers_prevented = 0
        self._series_attempts = {
            timeframe.value: 0 for timeframe in SUPPORTED_TIMEFRAMES
        }
        self._completed_discoveries = {
            timeframe.value: 0 for timeframe in SUPPORTED_TIMEFRAMES
        }
        self._request_times: list[datetime] = []
        self._successful_request_times: list[datetime] = []

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def discover_instruments(self) -> tuple[CanonicalInstrument, ...]:
        return (self._instrument,)

    def provider_profile(self) -> ProviderProfile:
        return ProviderProfile(
            provider_name=PROVIDER_ID,
            adapter_version=ADAPTER_VERSION,
            timestamp_semantics=TimestampSemantics.INTERVAL_START,
            native_timeframes=(Timeframe.H1, Timeframe.H4),
        )

    def map_symbol(self, canonical_instrument: str) -> str:
        if canonical_instrument not in {
            self._instrument.instrument_id,
            self._instrument.provider_symbol,
            self._instrument.display_symbol,
        }:
            raise ValueError(
                f"Twelve Data adapter does not support {canonical_instrument}"
            )
        return self._instrument.provider_symbol

    def fetch_raw_candles(
        self,
        instrument: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[RawProviderCandle, ...]:
        self.map_symbol(instrument)
        return self.fetch_historical_bars(timeframe, start, end)

    @staticmethod
    def normalize_timestamp(
        provider_timestamp: str,
        semantics: TimestampSemantics,
        timeframe: Timeframe,
    ) -> tuple[datetime, datetime]:
        value = TwelveDataProvider._parse_utc(provider_timestamp)
        step = TIMEFRAME_STEP[timeframe]
        if semantics in (
            TimestampSemantics.OPEN_TIME,
            TimestampSemantics.INTERVAL_START,
        ):
            return value, value + step
        if semantics in (
            TimestampSemantics.CLOSE_TIME,
            TimestampSemantics.INTERVAL_END,
        ):
            return value - step, value
        raise ValueError("Twelve Data timestamp semantics must be explicit")

    def report_health(self, as_of: datetime) -> ProviderHealth:
        return self.health(as_of)

    def diagnostics(self, timeframe: Timeframe) -> ProviderDiagnostics:
        return self._diagnostics.get(timeframe, ProviderDiagnostics())

    def telemetry(self) -> ProviderTelemetry:
        reference = self._request_times[-1] if self._request_times else None
        minute_start = reference.replace(second=0, microsecond=0) if reference else None
        day = reference.date() if reference else None
        last_h1 = self._last_fetch_as_of.get(Timeframe.H1)
        return ProviderTelemetry(
            network_attempts=self._network_attempts,
            successful_requests=self._successful_requests,
            failed_requests=self._failed_requests,
            rate_limit_responses=self._rate_limit_responses,
            network_timeouts=self._network_timeouts,
            cache_hits=self._cache_hits,
            duplicate_triggers_prevented=(self._duplicate_triggers_prevented),
            series_attempts=dict(self._series_attempts),
            completed_discoveries=dict(self._completed_discoveries),
            requests_used_this_minute=sum(
                value >= minute_start for value in self._request_times
            )
            if minute_start
            else 0,
            estimated_credits_used_today=sum(
                value.date() == day for value in self._successful_request_times
            )
            if day
            else 0,
            last_h1_request=last_h1,
            next_expected_h1_close=(
                last_h1.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                if last_h1
                else None
            ),
            duplicate_bars_ignored=sum(
                value.duplicate_count for value in self._diagnostics.values()
            ),
            forming_bars_excluded=sum(
                value.forming_filtered_count for value in self._diagnostics.values()
            ),
            provider_errors_or_retries=self._failed_requests,
        )

    def set_resume_cursor(
        self, timeframe: Timeframe, close_time: datetime | None
    ) -> None:
        if timeframe not in (Timeframe.H1, Timeframe.H4):
            raise ValueError("resume cursor supports only H1 and H4")
        if close_time is None:
            return
        self._last_trigger_close[timeframe] = self._aware_utc(close_time)

    def set_request_category(self, category: str) -> None:
        self._request_category = category

    def fetch_smoke_bars(
        self,
        timeframe: Timeframe,
        as_of: datetime,
    ) -> tuple[RawProviderCandle, ...]:
        """Fetch one bounded series for the explicitly invoked live smoke test."""

        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError("unsupported Twelve Data timeframe")
        if timeframe is Timeframe.D1:
            raise ValueError(
                "provider-native D1 smoke is disabled; validate reconstructed D1"
            )
        checked_at = self._aware_utc(as_of)
        required = (
            MIN_DAILY_HISTORY if timeframe is Timeframe.D1 else MIN_SIGNAL_HISTORY
        )
        return self._fetch_series(timeframe, checked_at, required + 1)

    def fetch_historical_bars(
        self,
        timeframe: Timeframe,
        start_close: datetime,
        end_close: datetime,
    ) -> tuple[RawProviderCandle, ...]:
        """Fetch one immutable, date-bounded completed historical series.

        The bounds apply to derived UTC close times.  Authentication remains in
        the request header, and no authenticated URL is constructed or exposed.
        """

        if timeframe is Timeframe.D1:
            raise ValueError(
                "provider-native D1 is disabled; aggregate canonical D1 from H1"
            )
        if timeframe not in (Timeframe.H1, Timeframe.H4):
            raise ValueError("unsupported Twelve Data timeframe")
        start = self._aware_utc(start_close)
        end = self._aware_utc(end_close)
        if start >= end:
            raise ValueError("historical start must be before end")
        step = TIMEFRAME_STEP[timeframe]
        expected = int((end - start) / step) + 2
        if expected > 5_000:
            raise ValueError("historical request exceeds 5000 candles")
        params = {
                "symbol": self._instrument.provider_symbol,
                "interval": INTERVALS[timeframe],
                "start_date": self._provider_datetime(start - step),
                "end_date": self._provider_datetime(end),
                "outputsize": str(expected),
                "order": "desc",
                "timezone": "UTC",
                "format": "JSON",
            }
        self._add_market_identity(params)
        payload = self._request_json(params, end)
        candles = self._parse_payload(payload, timeframe, end)
        return tuple(
            candle
            for candle in candles
            if start <= self._parse_utc(candle.raw_close_time) < end
        )

    def fetch_completed_bars(
        self,
        as_of: datetime,
    ) -> tuple[ProviderClosedBar, ...]:
        checked_at = self._aware_utc(as_of)
        triggers: list[ProviderClosedBar] = []
        # The certified Forex strategy runtime polls H1 only. Canonical H4 and
        # New-York-close D1 are derived locally from this single source stream.
        for timeframe in (Timeframe.H1,):
            if not self._needs_refresh(timeframe, checked_at):
                continue
            outputsize = MIN_SIGNAL_HISTORY + self._max_catchup_bars
            if timeframe is Timeframe.H1:
                outputsize = max(
                    outputsize,
                    DAILY_AGGREGATION_H1_BARS + self._max_catchup_bars,
                )
            candles = self._fetch_series(
                timeframe,
                checked_at,
                outputsize,
            )
            if not candles:
                continue
            cursor = self._last_trigger_close.get(timeframe)
            if cursor is None:
                candidates = (candles[-1],)
            else:
                candidates = tuple(
                    candle
                    for candle in candles
                    if self._parse_utc(candle.raw_close_time) > cursor
                )
                if candidates:
                    first_close = self._parse_utc(candidates[0].raw_close_time)
                    if (
                        first_close > cursor + TIMEFRAME_STEP[timeframe]
                        and not (
                            self._instrument.instrument_kind is InstrumentKind.ETF
                            and is_expected_us_equity_gap(
                                cursor - TIMEFRAME_STEP[timeframe],
                                first_close - TIMEFRAME_STEP[timeframe],
                            )
                        )
                    ):
                        error = MarketDataProviderError(
                            ProviderErrorCode.MISSING_CANDLE,
                            HealthState.INSUFFICIENT_HISTORY,
                            "Catch-up window does not cover the resume cursor.",
                        )
                        self._record_error(error)
                        raise error
                elif self._parse_utc(candles[-1].raw_close_time) <= cursor:
                    self._duplicate_triggers_prevented += 1
            for terminal in candidates:
                terminal_close = self._parse_utc(terminal.raw_close_time)
                self._last_trigger_close[timeframe] = terminal_close
                triggers.append(
                    ProviderClosedBar(
                        source_id=self._source_id(terminal),
                        instrument_id=self._instrument.instrument_id,
                        evaluation_time=checked_at,
                        candle=terminal,
                    )
                )
            self._completed_discoveries[timeframe.value] += len(candidates)
        if (
            self._bootstrap_latest_h4
            and self._instrument.instrument_kind is not InstrumentKind.ETF
        ):
            expected_h4 = self._expected_completed_close(Timeframe.H4, checked_at)
            h4_cursor = self._last_trigger_close.get(Timeframe.H4)
            h1_series = self._latest_series.get(Timeframe.H1, ())
            source = next(
                (
                    candle
                    for candle in reversed(h1_series)
                    if self._parse_utc(candle.raw_close_time) == expected_h4
                ),
                None,
            )
            if source is not None and (h4_cursor is None or expected_h4 > h4_cursor):
                h4_open = expected_h4 - TIMEFRAME_STEP[Timeframe.H4]
                h4_candle = replace(
                    source,
                    timeframe=Timeframe.H4,
                    raw_open_time=self._iso_utc(h4_open),
                    open_time_utc=h4_open,
                    close_time_utc=expected_h4,
                    source_timeframe=Timeframe.H1,
                    source_id=(
                        f"twelve_data:{self._instrument.instrument_id}:H4:"
                        f"{self._iso_utc(expected_h4)}"
                    ),
                )
                self._last_trigger_close[Timeframe.H4] = expected_h4
                triggers.append(
                    ProviderClosedBar(
                        source_id=h4_candle.source_id or self._source_id(h4_candle),
                        instrument_id=self._instrument.instrument_id,
                        evaluation_time=checked_at,
                        candle=h4_candle,
                    )
                )
                self._completed_discoveries[Timeframe.H4.value] += 1
        if (
            self._bootstrap_latest_h4
            and self._instrument.instrument_kind is InstrumentKind.ETF
        ):
            h4_cursor = self._last_trigger_close.get(Timeframe.H4)
            h1_series = self._latest_series.get(Timeframe.H1, ())
            source = next(
                (
                    candle
                    for candle in reversed(h1_series)
                    if (
                        (session := session_for_instant(candle.open_time_utc or self._parse_utc(candle.raw_open_time)))
                        is not None
                        and (candle.open_time_utc or self._parse_utc(candle.raw_open_time))
                        in session.valid_h1_opens
                        and (
                            session.valid_h1_opens.index(
                                candle.open_time_utc
                                or self._parse_utc(candle.raw_open_time)
                            )
                            + 1
                        )
                        % 4
                        == 0
                    )
                ),
                None,
            )
            if source is not None:
                expected_h4 = self._parse_utc(source.raw_close_time)
                if h4_cursor is None or expected_h4 > h4_cursor:
                    h4_open = expected_h4 - TIMEFRAME_STEP[Timeframe.H4]
                    h4_candle = replace(
                        source,
                        timeframe=Timeframe.H4,
                        raw_open_time=self._iso_utc(h4_open),
                        open_time_utc=h4_open,
                        close_time_utc=expected_h4,
                        source_timeframe=Timeframe.H1,
                        source_id=(
                            f"twelve_data:{self._instrument.instrument_id}:H4:"
                            f"{self._iso_utc(expected_h4)}"
                        ),
                    )
                    self._last_trigger_close[Timeframe.H4] = expected_h4
                    triggers.append(
                        ProviderClosedBar(
                            source_id=h4_candle.source_id or self._source_id(h4_candle),
                            instrument_id=self._instrument.instrument_id,
                            evaluation_time=checked_at,
                            candle=h4_candle,
                        )
                    )
                    self._completed_discoveries[Timeframe.H4.value] += 1
        return tuple(
            sorted(
                triggers,
                key=lambda item: (
                    self._parse_utc(item.candle.raw_close_time),
                    item.candle.timeframe.value,
                ),
            )
        )

    def fetch_required_history(
        self,
        closed_bar: ProviderClosedBar,
        as_of: datetime,
    ) -> ProviderHistory:
        checked_at = self._aware_utc(as_of)
        self._validate_closed_bar(closed_bar)
        trigger_close = self._parse_utc(closed_bar.candle.raw_close_time)
        daily: tuple[RawProviderCandle, ...] = ()
        history_size = DAILY_AGGREGATION_H1_BARS + self._max_catchup_bars
        daily_source = tuple(
            candle
            for candle in self._fetch_series(
                Timeframe.H1,
                checked_at,
                history_size,
            )
            if self._parse_utc(candle.raw_close_time) <= trigger_close
        )[-DAILY_AGGREGATION_H1_BARS:]
        signal = (
            daily_source[-MIN_SIGNAL_HISTORY:]
            if closed_bar.candle.timeframe is Timeframe.H1
            else ()
        )
        return ProviderHistory(
            source_id=closed_bar.source_id,
            instrument_id=self._instrument.instrument_id,
            timeframe=closed_bar.candle.timeframe,
            evaluation_time=checked_at,
            signal_bars=signal,
            daily_bars=daily,
            daily_source_bars=daily_source,
        )

    def health(self, as_of: datetime) -> ProviderHealth:
        checked_at = self._aware_utc(as_of)
        state = self._state
        detail = self._detail
        latest = self._latest_completed_close
        freshness = (
            max(0, int((checked_at - latest).total_seconds()))
            if latest is not None
            else None
        )
        if self._instrument.instrument_kind is InstrumentKind.ETF:
            expected_latest = latest_expected_etf_h1_close(checked_at)
            stale = expected_latest is not None and latest is not None and latest < expected_latest
        else:
            stale = latest is not None and checked_at - latest > timedelta(hours=2)
        if latest is not None and state is HealthState.HEALTHY and stale:
            state = HealthState.STALE
            detail = "Latest completed Twelve Data candle is stale."
        return ProviderHealth(
            provider_id=PROVIDER_ID,
            state=state,
            checked_at=checked_at,
            latest_completed_close=latest,
            freshness_seconds=freshness,
            detail=detail,
            synthetic=False,
        )

    def _fetch_series(
        self,
        timeframe: Timeframe,
        as_of: datetime,
        outputsize: int,
    ) -> tuple[RawProviderCandle, ...]:
        if timeframe is Timeframe.D1:
            raise ValueError(
                "provider-native D1 is disabled; aggregate canonical D1 from H1"
            )
        cache_key = (timeframe, as_of, outputsize)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache_hits += 1
            return cached
        latest_series = self._latest_series.get(timeframe)
        if (
            latest_series is not None
            and len(latest_series) >= outputsize
            and (
                self._last_fetch_as_of.get(timeframe) == as_of
                or not self._needs_refresh(timeframe, as_of)
            )
        ):
            self._cache_hits += 1
            return latest_series[-outputsize:]
        self._series_attempts[timeframe.value] += 1
        params = {
                "symbol": self._instrument.provider_symbol,
                "interval": INTERVALS[timeframe],
                "outputsize": str(outputsize),
                "order": "desc",
                "timezone": "UTC",
                "format": "JSON",
            }
        self._add_market_identity(params)
        payload = self._request_json(params, as_of)
        candles = self._parse_payload(payload, timeframe, as_of)
        previous = self._latest_series.get(timeframe)
        if previous:
            merged = {candle.raw_close_time: candle for candle in (*previous, *candles)}
            retained = tuple(
                sorted(
                    merged.values(),
                    key=lambda candle: self._parse_utc(candle.raw_close_time),
                )
            )[-max(len(previous), outputsize) :]
        else:
            retained = candles
        result = retained[-outputsize:]
        self._cache[cache_key] = result
        self._latest_series[timeframe] = retained
        self._last_fetch_as_of[timeframe] = as_of
        while len(self._cache) > 12:
            self._cache.pop(next(iter(self._cache)))
        if result:
            latest = self._parse_utc(result[-1].raw_close_time)
            if (
                self._latest_completed_close is None
                or latest > self._latest_completed_close
            ):
                self._latest_completed_close = latest
            self._state = HealthState.HEALTHY
            self._detail = "Twelve Data returned completed candles."
        elif self._state is HealthState.HEALTHY:
            self._detail = "No completed candle was available for the request."
        else:
            self._state = HealthState.DATA_UNAVAILABLE
            self._detail = "Twelve Data returned no completed candles."
        return result

    def _request_json(
        self,
        params: Mapping[str, str],
        as_of: datetime,
    ) -> Mapping[str, Any]:
        for attempt in range(1, self._max_attempts + 1):
            if self._rate_limiter is not None:
                self._rate_limiter.acquire(self._instrument.instrument_id)
            request_started = datetime.now(timezone.utc)
            reservation_id: int | None = None
            if self._credit_budget is not None:
                try:
                    reservation_id = self._credit_budget.reserve_request(
                        "/time_series",
                        self._request_category if attempt == 1 else "retry",
                        started_at=request_started,
                    )
                except CreditBudgetExhausted as caught:
                    error = MarketDataProviderError(
                        ProviderErrorCode.CREDIT_BUDGET,
                        HealthState.STALE,
                        str(caught),
                    )
                    self._record_error(error)
                    raise error from None
            self._network_attempts += 1
            self._request_times.append(as_of)
            try:
                response = self._transport.get(
                    "/time_series",
                    params,
                    {"Authorization": f"apikey {self._api_key}"},
                    connect_timeout=self._connect_timeout,
                    read_timeout=self._read_timeout,
                )
            except TransportTimeoutError:
                if self._credit_budget is not None and reservation_id is not None:
                    self._credit_budget.finalize_request(
                        reservation_id, status="TIMEOUT", http_status=None
                    )
                self._request_observations.append(
                    ProviderRequestObservation(
                        request_started, datetime.now(timezone.utc), "TIMEOUT"
                    )
                )
                self._failed_requests += 1
                self._network_timeouts += 1
                error = MarketDataProviderError(
                    ProviderErrorCode.TIMEOUT,
                    HealthState.DATA_UNAVAILABLE,
                    "Twelve Data request timed out.",
                    retryable=True,
                )
                if attempt < self._max_attempts:
                    self._sleep(self._backoff_seconds * attempt)
                    continue
                self._record_error(error)
                raise error from None
            except TransportConnectionError:
                if self._credit_budget is not None and reservation_id is not None:
                    self._credit_budget.finalize_request(
                        reservation_id, status="CONNECTION_ERROR", http_status=None
                    )
                self._request_observations.append(
                    ProviderRequestObservation(
                        request_started,
                        datetime.now(timezone.utc),
                        "CONNECTION_ERROR",
                    )
                )
                self._failed_requests += 1
                error = MarketDataProviderError(
                    ProviderErrorCode.TEMPORARY_UNAVAILABLE,
                    HealthState.DATA_UNAVAILABLE,
                    "Twelve Data is temporarily unavailable.",
                    retryable=True,
                )
                if attempt < self._max_attempts:
                    self._sleep(self._backoff_seconds * attempt)
                    continue
                self._record_error(error)
                raise error from None

            if self._credit_budget is not None and reservation_id is not None:
                self._credit_budget.finalize_request(
                    reservation_id,
                    status=f"HTTP_{response.status_code}",
                    http_status=response.status_code,
                    headers=response.headers,
                )
            self._request_observations.append(
                ProviderRequestObservation(
                    request_started,
                    datetime.now(timezone.utc),
                    f"HTTP_{response.status_code}",
                )
            )
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                self._failed_requests += 1
                if response.status_code == 429:
                    self._rate_limit_responses += 1
                code = (
                    ProviderErrorCode.RATE_LIMIT
                    if response.status_code == 429
                    else ProviderErrorCode.TEMPORARY_UNAVAILABLE
                )
                detail = (
                    "Twelve Data rate limit reached."
                    if response.status_code == 429
                    else "Twelve Data is temporarily unavailable."
                )
                error = MarketDataProviderError(
                    code,
                    HealthState.DATA_UNAVAILABLE,
                    detail,
                    retryable=True,
                )
                if attempt < self._max_attempts:
                    self._sleep(self._retry_delay(response.headers, attempt, as_of))
                    continue
                self._record_error(error)
                raise error

            if response.status_code in (401, 403):
                self._failed_requests += 1
                error = MarketDataProviderError(
                    ProviderErrorCode.AUTHENTICATION,
                    HealthState.DATA_UNAVAILABLE,
                    "Twelve Data authentication failed.",
                )
                self._record_error(error)
                raise error
            if not 200 <= response.status_code <= 299:
                self._failed_requests += 1
                error = MarketDataProviderError(
                    ProviderErrorCode.VALIDATION,
                    HealthState.DATA_UNAVAILABLE,
                    "Twelve Data rejected the request.",
                )
                self._record_error(error)
                raise error

            try:
                decoded = json.loads(response.body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._failed_requests += 1
                error = self._malformed_error()
                self._record_error(error)
                raise error from None
            if not isinstance(decoded, dict):
                self._failed_requests += 1
                error = self._malformed_error()
                self._record_error(error)
                raise error
            provider_code = self._provider_error_code(decoded)
            if provider_code is not None:
                self._failed_requests += 1
                if provider_code == 429:
                    self._rate_limit_responses += 1
                if provider_code in (429, 500, 502, 503, 504):
                    code = (
                        ProviderErrorCode.RATE_LIMIT
                        if provider_code == 429
                        else ProviderErrorCode.TEMPORARY_UNAVAILABLE
                    )
                    error = MarketDataProviderError(
                        code,
                        HealthState.DATA_UNAVAILABLE,
                        (
                            "Twelve Data rate limit reached."
                            if provider_code == 429
                            else "Twelve Data is temporarily unavailable."
                        ),
                        retryable=True,
                    )
                    if attempt < self._max_attempts:
                        self._sleep(self._retry_delay(response.headers, attempt, as_of))
                        continue
                    self._record_error(error)
                    raise error
                error = MarketDataProviderError(
                    (
                        ProviderErrorCode.AUTHENTICATION
                        if provider_code in (401, 403)
                        else ProviderErrorCode.VALIDATION
                    ),
                    HealthState.DATA_UNAVAILABLE,
                    (
                        "Twelve Data authentication failed."
                        if provider_code in (401, 403)
                        else "Twelve Data rejected the request."
                    ),
                )
                self._record_error(error)
                raise error
            self._successful_requests += 1
            self._successful_request_times.append(as_of)
            return decoded
        raise AssertionError("bounded retry loop exhausted unexpectedly")

    def _add_market_identity(self, params: dict[str, str]) -> None:
        if self._instrument.mic_code:
            params["mic_code"] = self._instrument.mic_code
        elif self._instrument.exchange:
            params["exchange"] = self._instrument.exchange

    def request_observations(self) -> tuple[ProviderRequestObservation, ...]:
        return tuple(self._request_observations)

    def _parse_payload(
        self,
        payload: Mapping[str, Any],
        timeframe: Timeframe,
        as_of: datetime,
    ) -> tuple[RawProviderCandle, ...]:
        meta = payload.get("meta")
        values = payload.get("values")
        if (
            payload.get("status") != "ok"
            or not isinstance(meta, dict)
            or meta.get("symbol") != self._instrument.provider_symbol
            or meta.get("interval") != INTERVALS[timeframe]
            or not isinstance(values, list)
        ):
            self._raise_data_error(self._malformed_error())
        if (
            self._instrument.instrument_kind is not InstrumentKind.ETF
            and self._instrument.exchange
            and self._normalize_identity(
            meta.get("exchange")
            ) != self._normalize_identity(self._instrument.exchange)
        ):
            self._raise_data_error(self._malformed_error())
        if self._instrument.mic_code and str(meta.get("mic_code") or "") != str(
            self._instrument.mic_code
        ):
            self._raise_data_error(self._malformed_error())
        try:
            self._validate_exchange_timezone(meta)
        except ValueError:
            self._raise_data_error(self._malformed_error())
        if not values:
            self._diagnostics[timeframe] = ProviderDiagnostics()
            return ()

        parsed: list[tuple[datetime, Mapping[str, Any]]] = []
        for value in values:
            if not isinstance(value, dict) or not isinstance(
                value.get("datetime"), str
            ):
                self._raise_data_error(self._malformed_error())
            try:
                open_time = self._parse_utc(value["datetime"])
                self._validate_prices(value)
            except (ValueError, TypeError, InvalidOperation):
                self._raise_data_error(self._malformed_error())
            parsed.append((open_time, value))

        original_opens = [item[0] for item in parsed]
        parsed.sort(key=lambda item: item[0])
        opens = [item[0] for item in parsed]
        duplicate_count = len(opens) - len(set(opens))
        out_of_order_count = int(original_opens != opens)
        if duplicate_count:
            self._diagnostics[timeframe] = ProviderDiagnostics(
                received_count=len(values),
                duplicate_count=duplicate_count,
                out_of_order_count=out_of_order_count,
            )
            self._raise_data_error(
                MarketDataProviderError(
                    ProviderErrorCode.DUPLICATE_CANDLE,
                    HealthState.QUARANTINED,
                    "Twelve Data returned duplicate candles.",
                )
            )
        step = TIMEFRAME_STEP[timeframe]
        gap_count = int(
            any(
                current - previous != step
                and not (
                    timeframe in (Timeframe.H1, Timeframe.H4)
                    and is_expected_forex_weekend_gap(
                        previous + step,
                        current,
                    )
                )
                and not (
                    self._instrument.instrument_kind is InstrumentKind.ETF
                    and timeframe is Timeframe.H1
                    and is_expected_us_equity_gap(previous, current)
                )
                for previous, current in zip(opens, opens[1:])
            )
        )
        if gap_count:
            self._diagnostics[timeframe] = ProviderDiagnostics(
                received_count=len(values),
                gap_count=gap_count,
                out_of_order_count=out_of_order_count,
            )
            self._raise_data_error(
                MarketDataProviderError(
                    ProviderErrorCode.MISSING_CANDLE,
                    HealthState.QUARANTINED,
                    "Twelve Data returned an unexpected candle gap.",
                )
            )

        candles: list[RawProviderCandle] = []
        forming = 0
        structurally_partial = 0
        outside_regular_session = 0
        for open_time, value in parsed:
            close_time = open_time + step
            if (
                self._instrument.instrument_kind is InstrumentKind.ETF
                and timeframe is Timeframe.H1
            ):
                disposition = classify_etf_h1_open(open_time, as_of=as_of)
                if disposition is EquityH1Disposition.FORMING:
                    forming += 1
                    continue
                if disposition is EquityH1Disposition.STRUCTURAL_PARTIAL:
                    structurally_partial += 1
                    continue
                if disposition is not EquityH1Disposition.VALID_COMPLETED:
                    outside_regular_session += 1
                    continue
            elif close_time >= as_of:
                forming += 1
                continue
            candles.append(
                RawProviderCandle(
                    provider_id=PROVIDER_ID,
                    provider_symbol=self._instrument.provider_symbol,
                    timeframe=timeframe,
                    raw_open_time=str(value["datetime"]),
                    raw_close_time=self._iso_utc(close_time),
                    open=str(value["open"]),
                    high=str(value["high"]),
                    low=str(value["low"]),
                    close=str(value["close"]),
                    volume=(
                        str(value["volume"])
                        if value.get("volume") not in (None, "")
                        else None
                    ),
                    is_complete=True,
                    session_timezone="UTC",
                    provider_name=PROVIDER_ID,
                    canonical_instrument=self._instrument.instrument_id,
                    source_timeframe=timeframe,
                    provider_timestamp=str(value["datetime"]),
                    timestamp_semantics=TimestampSemantics.INTERVAL_START,
                    open_time_utc=open_time,
                    close_time_utc=close_time,
                    source_id=(
                        f"twelve_data:{self._instrument.instrument_id}:"
                        f"{timeframe.value}:"
                        f"{self._iso_utc(close_time)}"
                    ),
                    received_at=as_of,
                    provider_metadata={
                        "interval": INTERVALS[timeframe],
                        "ingestion_run_id": (f"twelve_data:{self._iso_utc(as_of)}"),
                    },
                    adapter_version=ADAPTER_VERSION,
                )
            )
        self._diagnostics[timeframe] = ProviderDiagnostics(
            received_count=len(values),
            completed_count=len(candles),
            forming_filtered_count=forming,
            out_of_order_count=out_of_order_count,
            structurally_partial_count=structurally_partial,
            outside_regular_session_count=outside_regular_session,
        )
        return tuple(candles)

    @staticmethod
    def _validate_exchange_timezone(meta: Mapping[str, Any]) -> None:
        value = meta.get("exchange_timezone")
        if value is None:
            return
        if not isinstance(value, str) or not value:
            raise ValueError("invalid provider exchange timezone metadata")
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("invalid provider exchange timezone metadata") from error

    @staticmethod
    def _normalize_identity(value: object) -> str:
        return " ".join(str(value or "").strip().lower().split())

    @staticmethod
    def _validate_prices(value: Mapping[str, Any]) -> None:
        fields = ("open", "high", "low", "close")
        if any(field not in value or value[field] in (None, "") for field in fields):
            raise ValueError("missing OHLC")
        prices = tuple(Decimal(str(value[field])) for field in fields)
        if any(not price.is_finite() or price <= 0 for price in prices):
            raise ValueError("invalid OHLC")
        open_price, high, low, close = prices
        if low > high or low > open_price or low > close:
            raise ValueError("invalid OHLC")
        if high < open_price or high < close:
            raise ValueError("invalid OHLC")
        volume = value.get("volume")
        if volume not in (None, ""):
            parsed_volume = Decimal(str(volume))
            if not parsed_volume.is_finite() or parsed_volume < 0:
                raise ValueError("invalid volume")

    @staticmethod
    def _provider_error_code(payload: Mapping[str, Any]) -> int | None:
        if payload.get("status") != "error":
            return None
        try:
            return int(payload.get("code", 400))
        except (TypeError, ValueError):
            return 400

    def _retry_delay(
        self,
        headers: Mapping[str, str],
        attempt: int,
        as_of: datetime,
    ) -> float:
        retry_after = next(
            (value for key, value in headers.items() if key.lower() == "retry-after"),
            None,
        )
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    when = parsedate_to_datetime(retry_after)
                    delay = (self._aware_utc(when) - as_of).total_seconds()
                except (TypeError, ValueError):
                    delay = self._backoff_seconds * attempt
            return max(0.0, min(delay, self._max_retry_after_seconds))
        return min(
            self._backoff_seconds * attempt,
            self._max_retry_after_seconds,
        )

    def _raise_data_error(self, error: MarketDataProviderError) -> None:
        self._record_error(error)
        raise error

    def _record_error(self, error: MarketDataProviderError) -> None:
        self._state = error.health_state
        self._detail = str(error)

    def _needs_refresh(self, timeframe: Timeframe, as_of: datetime) -> bool:
        series = self._latest_series.get(timeframe)
        if not series:
            return True
        latest = self._parse_utc(series[-1].raw_close_time)
        return latest < self._expected_completed_close(timeframe, as_of)

    def _expected_completed_close(self, timeframe: Timeframe, as_of: datetime) -> datetime:
        checked_at = TwelveDataProvider._aware_utc(as_of)
        if timeframe is Timeframe.H1:
            if self._instrument.instrument_kind is InstrumentKind.ETF:
                return latest_expected_etf_h1_close(checked_at) or checked_at
            return checked_at.replace(minute=0, second=0, microsecond=0)
        if timeframe is Timeframe.D1:
            raise ValueError(
                "provider-native D1 is disabled; aggregate canonical D1 from H1"
            )
        current = checked_at.replace(minute=0, second=0, microsecond=0)
        while current.hour not in {1, 5, 9, 13, 17, 21}:
            current -= timedelta(hours=1)
        return current

    @staticmethod
    def _malformed_error() -> MarketDataProviderError:
        return MarketDataProviderError(
            ProviderErrorCode.MALFORMED_RESPONSE,
            HealthState.QUARANTINED,
            "Twelve Data returned a malformed response.",
        )

    @staticmethod
    def _source_id(candle: RawProviderCandle) -> str:
        close = TwelveDataProvider._parse_utc(candle.raw_close_time)
        return (
            f"twelve_data:{candle.canonical_instrument or candle.provider_symbol}:"
            f"{candle.timeframe.value}:"
            f"{TwelveDataProvider._iso_utc(close)}"
        )

    def _validate_closed_bar(self, closed_bar: ProviderClosedBar) -> None:
        if (
            closed_bar.instrument_id != self._instrument.instrument_id
            or closed_bar.candle.provider_id != PROVIDER_ID
            or closed_bar.candle.provider_symbol != self._instrument.provider_symbol
            or closed_bar.candle.timeframe not in (Timeframe.H1, Timeframe.H4)
        ):
            raise ValueError("closed bar is outside the Phase 2C provider scope")

    @staticmethod
    def _parse_utc(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("provider clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _iso_utc(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _provider_datetime(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
