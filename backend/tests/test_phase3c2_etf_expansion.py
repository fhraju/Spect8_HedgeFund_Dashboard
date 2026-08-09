from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.domain import Bar, Timeframe
from backend.app.market_data.closed_bar import ClosedBarDetector
from backend.app.market_data.credit_budget import (
    CreditBudgetExhausted,
    DailyCreditBudgetGuard,
)
from backend.app.market_data.daily_aggregator import ActualDataNewYorkDailyAggregator
from backend.app.market_data.exchange_aggregator import ExchangeSessionH4Aggregator
from backend.app.market_data.models import (
    ExposureCategory,
    HealthState,
    InstrumentKind,
    ProviderHealth,
    SessionProfileKind,
)
from backend.app.market_data.multi_provider import MultiInstrumentTwelveDataProvider
from backend.app.market_data.rate_limiter import SlidingWindowRateLimiter
from backend.app.market_data.registry import (
    ALL_INSTRUMENT_IDS,
    DEFAULT_ENABLED_INSTRUMENT_IDS,
    DISABLED_DIRECT_MARKET_IDS,
    ETF_INSTRUMENT_IDS,
    PHASE3C1_ENABLED_INSTRUMENT_IDS,
    TARGET_INSTRUMENT_IDS,
    CanonicalInstrumentRegistry,
    twelve_data_instruments,
)
from backend.app.market_data.twelve_data_provider import HttpResponse
from backend.app.market_data.universe_validation import (
    InstrumentUniverseValidator,
    etf_candidate_definitions,
)
from backend.app.market_data.us_equity_calendar import (
    EquityH1Disposition,
    classify_etf_h1_open,
    us_regular_session,
)
from backend.app.repository import SQLiteProjectionRepository
from backend.app.tools.reproducibility import reproduce_checkpoint, validate_checksums


UTC = timezone.utc


def _bar(instrument_id: str, opened: datetime, value: str = "100") -> Bar:
    return Bar(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        open_time=opened,
        close_time=opened + timedelta(hours=1),
        open=Decimal(value),
        high=Decimal(value) + 1,
        low=Decimal(value) - 1,
        close=Decimal(value) + Decimal("0.25"),
        provider="TWELVE_DATA",
        is_complete=True,
        volume=Decimal("1000"),
        synthetic=False,
        forward_filled=False,
    )


def test_registry_preserves_phase3c_targets_and_current_visible_universe() -> None:
    registry = CanonicalInstrumentRegistry(twelve_data_instruments())
    assert len(TARGET_INSTRUMENT_IDS) == 25
    assert TARGET_INSTRUMENT_IDS[:12] == PHASE3C1_ENABLED_INSTRUMENT_IDS
    assert TARGET_INSTRUMENT_IDS[12:] == ETF_INSTRUMENT_IDS
    assert tuple(item.instrument_id for item in registry.all()) == ALL_INSTRUMENT_IDS
    assert len(registry.all()) == 50
    assert len({item.instrument_id for item in registry.all()}) == 50
    assert tuple(item.instrument_id for item in registry.enabled()) == DEFAULT_ENABLED_INSTRUMENT_IDS
    assert len(registry.enabled()) == 29
    assert len(registry.pollable()) == 26
    assert not registry.by_id("TLT_US_ETF").enabled
    assert all(not registry.by_id(item).enabled for item in DISABLED_DIRECT_MARKET_IDS)
    for instrument_id in ETF_INSTRUMENT_IDS:
        item = registry.by_id(instrument_id)
        assert item.instrument_kind is InstrumentKind.ETF
        assert item.is_proxy
        assert item.provider_symbol == item.display_symbol
        assert item.session_profile is SessionProfileKind.US_EQUITY_REGULAR
        assert item.exposure_category is not ExposureCategory.CURRENCY
        assert item.provider_instrument_type == "ETF"
        if instrument_id != "TLT_US_ETF":
            assert item.validation_status == "LIVE_VALIDATED"
            assert item.price_precision is not None


def test_us_calendar_handles_holiday_early_close_and_dst() -> None:
    assert us_regular_session(date(2026, 7, 3)) is None  # Independence Day observed
    early = us_regular_session(date(2026, 11, 27))
    assert early is not None and early.early_close
    assert early.close_utc == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    assert len(early.valid_h1_opens) == 3
    before_dst = us_regular_session(date(2026, 3, 6))
    after_dst = us_regular_session(date(2026, 3, 9))
    assert before_dst and after_dst
    assert before_dst.open_utc.hour == 14
    assert after_dst.open_utc.hour == 13


def test_shortened_final_fragment_is_not_an_h1_bar() -> None:
    session = us_regular_session(date(2026, 8, 5))
    assert session is not None
    final_fragment = session.open_utc + timedelta(hours=6)
    assert classify_etf_h1_open(
        final_fragment, as_of=session.close_utc + timedelta(hours=1)
    ) is EquityH1Disposition.STRUCTURAL_PARTIAL
    assert classify_etf_h1_open(
        session.valid_h1_opens[-1], as_of=session.close_utc + timedelta(hours=1)
    ) is EquityH1Disposition.VALID_COMPLETED


def test_exchange_h4_requires_four_contiguous_bars_from_one_session() -> None:
    first = us_regular_session(date(2026, 8, 4))
    second = us_regular_session(date(2026, 8, 5))
    assert first and second
    aggregator = ExchangeSessionH4Aggregator()
    four = tuple(_bar("SPY_US_ETF", value) for value in first.valid_h1_opens[:4])
    result = aggregator.aggregate(four, as_of=first.close_utc + timedelta(hours=1))
    assert len(result.bars) == 1
    assert len(result.buckets[0].source_bars) == 4
    three = aggregator.aggregate(four[:3], as_of=first.close_utc + timedelta(hours=1))
    assert not three.bars
    crossed = (*four[:3], _bar("SPY_US_ETF", second.valid_h1_opens[0]))
    assert not aggregator.aggregate(crossed, as_of=second.close_utc + timedelta(hours=1)).bars
    mixed = (*four[:3], _bar("QQQ_US_ETF", first.valid_h1_opens[3]))
    assert not aggregator.aggregate(mixed, as_of=first.close_utc + timedelta(hours=1)).bars


def test_sparse_daily_uses_actual_bars_and_creates_no_empty_weekend_or_holiday() -> None:
    friday = us_regular_session(date(2026, 7, 2))
    monday = us_regular_session(date(2026, 7, 6))
    assert friday and monday
    bars = tuple(
        _bar("SPY_US_ETF", opened)
        for session in (friday, monday)
        for opened in session.valid_h1_opens
    )
    result = ActualDataNewYorkDailyAggregator().aggregate(
        bars, as_of=monday.close_utc + timedelta(days=1)
    )
    assert len(result.bars) == 2
    assert [item.source_bar_count for item in result.sessions] == [6, 6]
    assert all(not item.bar.synthetic and not item.bar.forward_filled for item in result.sessions)


def test_closed_bar_detector_treats_exchange_closure_as_expected() -> None:
    first = us_regular_session(date(2026, 7, 2))
    second = us_regular_session(date(2026, 7, 6))
    assert first and second
    bars = (
        _bar("SPY_US_ETF", first.valid_h1_opens[-1]),
        _bar("SPY_US_ETF", second.valid_h1_opens[0]),
    )
    issues = ClosedBarDetector._stream_issues(
        bars,
        Timeframe.H1,
        "SIGNAL",
        SessionProfileKind.US_EQUITY_REGULAR,
    )
    assert "MISSING_SIGNAL_CANDLE" not in issues


class _FakeTime:
    def __init__(self, base: datetime = datetime(2026, 8, 6, 12, tzinfo=UTC)) -> None:
        self.seconds = 0.0
        self.base = base

    def monotonic(self) -> float:
        return self.seconds

    def sleep(self, seconds: float) -> None:
        self.seconds += seconds

    def wall(self) -> datetime:
        return self.base + timedelta(seconds=self.seconds)


class _ETFTransport:
    def __init__(self, registry: CanonicalInstrumentRegistry) -> None:
        self.registry = registry
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, path: str, params: dict[str, str], headers: dict[str, str], **_: object) -> HttpResponse:
        assert "Authorization" in headers
        self.calls.append((path, dict(params)))
        instrument = next(
            item for item in self.registry.all() if item.provider_symbol == params["symbol"]
        )
        if path == "/etfs/list":
            payload = {
                "status": "ok",
                "result": {"count": 1, "list": [{
                    "symbol": instrument.provider_symbol,
                    "name": instrument.display_name,
                    "mic_code": instrument.mic_code,
                    "country": "United States",
                }]},
            }
        else:
            values: list[dict[str, str]] = []
            current = date(2026, 8, 5)
            while len(values) < 220:
                session = us_regular_session(current)
                if session:
                    for opened in session.valid_h1_opens:
                        values.append({
                            "datetime": opened.isoformat().replace("+00:00", "Z"),
                            "open": "100.00",
                            "high": "101.00",
                            "low": "99.00",
                            "close": "100.25",
                            "volume": "1000",
                        })
                    values.append({
                        "datetime": (session.open_utc + timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
                        "open": "100.00", "high": "101.00", "low": "99.00", "close": "100.25", "volume": "500",
                    })
                current -= timedelta(days=1)
            payload = {
                "status": "ok",
                "meta": {
                    "symbol": instrument.provider_symbol,
                    "interval": "1h",
                    "exchange": instrument.exchange,
                    "mic_code": instrument.mic_code,
                    "exchange_timezone": "America/New_York",
                },
                "values": list(reversed(values)),
            }
        return HttpResponse(200, {}, json.dumps(payload).encode())


@pytest.mark.parametrize("instrument_id", ETF_INSTRUMENT_IDS)
def test_each_etf_exact_listing_runs_real_h1_and_full_pipeline(instrument_id: str) -> None:
    registry = CanonicalInstrumentRegistry(twelve_data_instruments())
    fake = _FakeTime()
    limiter = SlidingWindowRateLimiter(
        monotonic=fake.monotonic,
        sleep=fake.sleep,
        wall_clock=fake.wall,
    )
    transport = _ETFTransport(registry)
    definitions = tuple(
        item for item in etf_candidate_definitions()
        if item.canonical_instrument_id == instrument_id
    )
    result = InstrumentUniverseValidator(
        api_key="ignored-test-key",
        registry=registry,
        limiter=limiter,
        transport=transport,
        wall_clock=fake.wall,
        bootstrap_outputsize=180,
        definitions=definitions,
    ).validate((instrument_id,))[0]
    assert [call[0] for call in transport.calls] == ["/etfs/list", "/time_series"]
    assert result.provider_instrument_type == "ETF"
    assert result.h1_request_status == "OK"
    assert result.current_plan_access == "GRANTED"
    assert result.structurally_partial_candle_count > 0
    assert result.validation_decision == "ENABLE"
    assert result.pipeline_checks["passed"] is True
    assert result.pipeline_checks["no_synthetic_bars"] is True
    assert result.pipeline_checks["no_forward_filled_bars"] is True


def test_credit_budget_persists_usage_and_preserves_reserve(tmp_path: Path) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "budget.sqlite3")
    repository.initialize()
    clock = _FakeTime()
    guard = DailyCreditBudgetGuard(
        repository,
        daily_limit=5,
        operational_budget=3,
        reserve=2,
        clock=clock.wall,
    )
    for _ in range(3):
        reservation = guard.reserve_request("/time_series", "validation")
        guard.finalize_request(reservation, status="HTTP_200", http_status=200)
    with pytest.raises(CreditBudgetExhausted):
        guard.reserve_request("/time_series", "scheduled")
    status = guard.status()
    assert status.estimated_credits_used == 3
    assert status.estimated_operational_remaining == 0
    assert status.estimated_total_remaining == 2
    assert status.reserve_preserved


class _NoCallProvider:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_completed_bars(self, _: datetime) -> tuple[()]:
        self.calls += 1
        return ()

    def health(self, as_of: datetime) -> ProviderHealth:
        return ProviderHealth("TWELVE_DATA", HealthState.DATA_UNAVAILABLE, as_of, None, None, "unused", False)

    def telemetry(self):
        return type("T", (), {
            "network_attempts": 0, "successful_requests": 0, "failed_requests": 0,
            "rate_limit_responses": 0, "network_timeouts": 0, "cache_hits": 0,
            "duplicate_triggers_prevented": 0,
            "series_attempts": {item.value: 0 for item in Timeframe},
            "completed_discoveries": {item.value: 0 for item in Timeframe},
            "provider_errors_or_retries": 0,
        })()


def test_repeated_startup_skips_current_etf_without_credit_use(tmp_path: Path) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "fresh.sqlite3")
    repository.initialize()
    source = CanonicalInstrumentRegistry(twelve_data_instruments()).by_id("SPY_US_ETF")
    instrument = replace(
        source,
        enabled=True,
        validation_status="LIVE_VALIDATED",
        point_size=Decimal("0.01"),
        price_precision=2,
    )
    session = us_regular_session(date(2026, 8, 6))
    assert session
    repository.persist_canonical_bars((_bar(instrument.instrument_id, session.valid_h1_opens[-1]),))
    stub = _NoCallProvider()
    provider = MultiInstrumentTwelveDataProvider(
        "ignored-test-key",
        (instrument,),
        providers={instrument.instrument_id: stub},
        repository=repository,
    )
    as_of = session.close_utc + timedelta(minutes=5)
    assert provider.fetch_completed_bars(as_of) == ()
    assert provider.fetch_completed_bars(as_of) == ()
    assert stub.calls == 0
    assert provider.instrument_health(instrument.instrument_id, as_of).state is HealthState.HEALTHY


def test_phase3c1_twelve_checkpoint_reproduces_offline(tmp_path: Path) -> None:
    root = Path(__file__).parent / "fixtures" / "reproducibility" / "phase_3c1_12_instruments"
    validate_checksums(root)
    result = reproduce_checkpoint(
        fixture_root=root,
        database_path=tmp_path / "checkpoint.sqlite3",
    )
    assert result["checkpoint_name"] == "phase_3c1_12_instruments"
    assert result["instrument_count"] == 12
    assert result["evaluation_count"] == 24
    assert result["event_count"] == 145
    assert result["network_calls"] == 0


def test_phase3c2_twenty_four_checkpoint_reproduces_offline(tmp_path: Path) -> None:
    root = Path(__file__).parent / "fixtures" / "reproducibility" / "phase_3c2_24_instruments"
    validate_checksums(root)
    result = reproduce_checkpoint(
        fixture_root=root,
        database_path=tmp_path / "checkpoint.sqlite3",
    )
    assert result["checkpoint_name"] == "phase_3c2_24_instruments"
    assert result["instrument_count"] == 24
    assert result["evaluation_count"] == 48
    assert result["event_count"] == 288
    assert result["network_calls"] == 0
