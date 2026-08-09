from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from backend.app.market_data.rate_limiter import SlidingWindowRateLimiter
from backend.app.market_data.multi_provider import MultiInstrumentTwelveDataProvider
from backend.app.market_data.registry import (
    ADDITIONAL_FOREX_INSTRUMENT_IDS,
    ALL_INSTRUMENT_IDS,
    CANDIDATE_INSTRUMENT_IDS,
    DEFAULT_ENABLED_INSTRUMENT_IDS,
    DISABLED_DIRECT_MARKET_IDS,
    ETF_INSTRUMENT_IDS,
    LEGACY_DIRECT_MARKET_IDS,
    SCANNER_UNAVAILABLE_INSTRUMENT_IDS,
    TARGET_INSTRUMENT_IDS,
    CanonicalInstrumentRegistry,
    twelve_data_instruments,
)
from backend.app.market_data.session_awareness import (
    GapClassification,
    MarketSessionProfile,
    classify_h1_gap,
)
from backend.app.market_data.twelve_data_provider import HttpResponse
from backend.app.market_data.universe_validation import (
    InstrumentUniverseValidator,
    sanitized_report,
)


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0
        self.epoch = datetime(2026, 8, 5, 18, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds

    def wall(self) -> datetime:
        return self.epoch + timedelta(seconds=self.value)


class DiscoveryTransport:
    def __init__(self, *, plan_restricted: bool = False) -> None:
        self.plan_restricted = plan_restricted
        self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

    def get(
        self,
        path: str,
        params: dict[str, str],
        headers: dict[str, str],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> HttpResponse:
        del connect_timeout, read_timeout
        self.calls.append((path, dict(params), dict(headers)))
        if path == "/commodities" and params.get("symbol") == "XAG/USD":
            payload = {
                "status": "ok",
                "data": [
                    {
                        "symbol": "XAG/USD",
                        "name": "Silver Spot",
                        "category": "Precious Metal",
                        "description": "Spot silver per troy ounce.",
                    }
                ],
            }
            return HttpResponse(200, {}, json.dumps(payload).encode())
        if path in {"/commodities", "/cryptocurrencies", "/indices", "/bonds", "/symbol_search"}:
            return HttpResponse(200, {}, b'{"status":"ok","data":[]}')
        if self.plan_restricted:
            return HttpResponse(
                403,
                {},
                b'{"status":"error","message":"upgrade your plan"}',
            )
        first = datetime(2026, 7, 15, 0, tzinfo=timezone.utc)
        values = [
            {
                "datetime": (first + timedelta(hours=index)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "open": "30.100",
                "high": "30.300",
                "low": "30.000",
                "close": "30.200",
            }
            for index in range(509)
        ]
        payload = {
            "status": "ok",
            "meta": {
                "symbol": "XAG/USD",
                "interval": "1h",
                "exchange": "Commodity",
                "mic_code": "",
                "exchange_timezone": "UTC",
                "type": "Commodity",
            },
            "values": values,
        }
        return HttpResponse(200, {}, json.dumps(payload).encode())


def _validator(plan_restricted: bool = False):
    fake = FakeTime()
    limiter = SlidingWindowRateLimiter(
        monotonic=fake.monotonic,
        sleep=fake.sleep,
        wall_clock=fake.wall,
    )
    transport = DiscoveryTransport(plan_restricted=plan_restricted)
    validator = InstrumentUniverseValidator(
        api_key="secret-not-for-report",
        registry=CanonicalInstrumentRegistry(twelve_data_instruments()),
        limiter=limiter,
        transport=transport,
        wall_clock=fake.wall,
    )
    return validator, limiter, transport


class ScanProvider:
    def __init__(self, instrument_id: str, limiter: SlidingWindowRateLimiter, calls: list[str]) -> None:
        self.instrument_id = instrument_id
        self.limiter = limiter
        self.calls = calls
        self.attempts = 0

    def fetch_completed_bars(self, as_of: datetime) -> tuple[()]:
        del as_of
        self.limiter.acquire(f"scan:{self.instrument_id}")
        self.calls.append(self.instrument_id)
        self.attempts += 1
        return ()

    def health(self, as_of: datetime):
        from backend.app.market_data.models import HealthState, ProviderHealth

        return ProviderHealth(
            provider_id="TWELVE_DATA",
            state=HealthState.HEALTHY,
            checked_at=as_of,
            latest_completed_close=as_of,
            freshness_seconds=0,
            detail="Healthy.",
            synthetic=False,
        )

    def telemetry(self):
        from types import SimpleNamespace
        from backend.app.domain import Timeframe

        counts = {item.value: 0 for item in Timeframe}
        return SimpleNamespace(
            network_attempts=self.attempts,
            successful_requests=self.attempts,
            failed_requests=0,
            rate_limit_responses=0,
            network_timeouts=0,
            cache_hits=0,
            duplicate_triggers_prevented=0,
            series_attempts=counts,
            completed_discoveries=counts,
            provider_errors_or_retries=0,
        )


def test_registry_preserves_history_and_exposes_requested_current_universe() -> None:
    registry = CanonicalInstrumentRegistry(twelve_data_instruments())
    assert tuple(item.instrument_id for item in registry.all()) == ALL_INSTRUMENT_IDS
    assert tuple(item.instrument_id for item in registry.enabled()) == DEFAULT_ENABLED_INSTRUMENT_IDS
    assert tuple(item.instrument_id for item in registry.enabled()[:10]) == ALL_INSTRUMENT_IDS[:10]
    assert tuple(item.instrument_id for item in registry.enabled()[10:12]) == (
        "BTC_USD",
        "ETH_USD",
    )
    assert "TLT_US_ETF" not in DEFAULT_ENABLED_INSTRUMENT_IDS
    assert len(registry.enabled()) == 29
    assert len(registry.pollable()) == 26
    assert tuple(item.instrument_id for item in registry.all()[:25]) == TARGET_INSTRUMENT_IDS
    assert tuple(item.instrument_id for item in registry.all()[25:40]) == LEGACY_DIRECT_MARKET_IDS
    assert tuple(item.instrument_id for item in registry.all()[40:50]) == ADDITIONAL_FOREX_INSTRUMENT_IDS
    assert len(registry.enabled()) <= 30
    assert len({item.instrument_id for item in registry.all()}) == 50
    assert [item.registry_order for item in registry.all()] == list(range(1, 51))
    assert {item.asset_class for item in registry.all()[25:40]} == {
        "PRECIOUS_METAL",
        "EQUITY_INDEX",
        "CURRENCY_INDEX",
        "ENERGY",
        "INDUSTRIAL_METAL",
        "VOLATILITY",
        "INTEREST_RATE",
    }
    assert all(
        registry.by_id(item).enabled and not registry.by_id(item).polling_enabled
        for item in SCANNER_UNAVAILABLE_INSTRUMENT_IDS
    )


def test_unvalidated_candidate_cannot_be_enabled() -> None:
    try:
        twelve_data_instruments(("EUR_USD", "VIX"))
    except ValueError as error:
        assert "without live validation" in str(error)
    else:
        raise AssertionError("unvalidated VIX must stay disabled")


def test_validation_discovery_h1_and_report_share_one_limiter() -> None:
    validator, limiter, transport = _validator()
    results = validator.validate()
    silver = results[0]
    assert silver.canonical_instrument_id == "XAG_USD"
    assert silver.resolved_provider_symbol == "XAG/USD"
    assert silver.validation_status == "H1_VALIDATED"
    assert silver.returned_candle_count == 509
    assert silver.point_size == "0.1"
    assert all(item.validation_status == "DISCOVERY_UNAVAILABLE" for item in results[1:])
    starts = limiter.starts()
    assert all(right - left >= 8 for left, right in zip(starts, starts[1:]))
    report = sanitized_report(results, limiter)
    assert len(transport.calls) == report["request_count"]
    encoded = json.dumps(report)
    assert "secret-not-for-report" not in encoded
    assert report["h1_validated_count"] == 1


def test_plan_inaccessible_market_remains_disabled() -> None:
    validator, _, _ = _validator(plan_restricted=True)
    silver = validator.validate()[0]
    assert silver.validation_status == "PLAN_RESTRICTED"
    assert silver.current_plan_access == "DENIED"


def test_session_aware_gap_classifies_closure_without_synthetic_fill() -> None:
    previous = datetime(2026, 8, 4, 20, tzinfo=timezone.utc)
    current = datetime(2026, 8, 5, 13, tzinfo=timezone.utc)
    profile = MarketSessionProfile(
        instrument_id="SP_500",
        continuous_forex_weekday=False,
        expected_closures_utc=(
            (previous + timedelta(hours=1), current),
        ),
    )
    assert classify_h1_gap(previous, current, profile) == GapClassification.EXPECTED_MARKET_CLOSURE
    assert (
        classify_h1_gap(
            previous,
            previous + timedelta(hours=3),
            MarketSessionProfile("SP_500", False, ()),
        )
        == GapClassification.UNEXPECTED_MISSING_DATA
    )


def test_forex_gap_behavior_remains_weekend_only() -> None:
    profile = MarketSessionProfile("EUR_USD", True)
    friday = datetime(2026, 8, 7, 20, tzinfo=timezone.utc)
    sunday = datetime(2026, 8, 9, 21, tzinfo=timezone.utc)
    assert classify_h1_gap(friday, sunday, profile) == GapClassification.EXPECTED_MARKET_CLOSURE
    assert (
        classify_h1_gap(friday, friday + timedelta(hours=2), profile)
        == GapClassification.UNEXPECTED_MISSING_DATA
    )


def test_twenty_five_market_cycle_is_fair_non_bursting_and_under_rolling_limit() -> None:
    fake = FakeTime()
    limiter = SlidingWindowRateLimiter(
        monotonic=fake.monotonic,
        sleep=fake.sleep,
        wall_clock=fake.wall,
    )
    instruments = tuple(
        replace(
            item,
            enabled=True,
            provider_symbol=item.provider_symbol or f"VALIDATED:{item.instrument_id}",
            point_size=item.point_size or Decimal("0.01"),
            price_precision=item.price_precision if item.price_precision is not None else 2,
            validation_status="LIVE_VALIDATED",
        )
        for item in twelve_data_instruments()
        if item.instrument_id in TARGET_INSTRUMENT_IDS
    )
    calls: list[str] = []
    providers = {
        item.instrument_id: ScanProvider(item.instrument_id, limiter, calls)
        for item in instruments
    }
    provider = MultiInstrumentTwelveDataProvider(
        "test-key",
        instruments,
        limiter=limiter,
        providers=providers,
    )
    provider.fetch_completed_bars(datetime(2026, 8, 5, tzinfo=timezone.utc))
    starts = [item.timestamp() for item in limiter.request_starts_utc()]
    starts = [value - starts[0] for value in starts]
    assert calls == list(TARGET_INSTRUMENT_IDS)
    assert starts == [float(value) for value in range(0, 200, 8)]
    assert all(right - left >= 8 for left, right in zip(starts, starts[1:]))
    assert all(
        sum(start <= value < start + 60 for value in starts) <= 8
        for start in starts
    )
    provider.fetch_completed_bars(datetime(2026, 8, 5, 1, tzinfo=timezone.utc))
    assert calls[25] == ALL_INSTRUMENT_IDS[1]
