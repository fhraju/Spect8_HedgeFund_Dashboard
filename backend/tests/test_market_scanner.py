from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.dashboard_api import scanner_snapshot
from backend.app.domain import Bar, Timeframe
from backend.app.engine.models import CURRENT_D1_FILTER_V2
from backend.app.main import create_app
from backend.app.market_data.models import (
    HealthState,
    MarketDataProviderError,
    ProviderErrorCode,
    ProviderHealth,
)
from backend.app.market_data.multi_provider import (
    MultiInstrumentTwelveDataProvider,
)
from backend.app.market_data.models import RawProviderCandle
from backend.app.market_data.normalizer import CandleNormalizer
from backend.app.market_data.rate_limiter import SlidingWindowRateLimiter
from backend.app.market_data.registry import (
    ALL_INSTRUMENT_IDS,
    CANDIDATE_INSTRUMENT_IDS,
    DEFAULT_ENABLED_INSTRUMENT_IDS,
    DISABLED_DIRECT_MARKET_IDS,
    ETF_INSTRUMENT_IDS,
    TARGET_INSTRUMENT_IDS,
    CanonicalInstrumentRegistry,
    twelve_data_instruments,
)
from backend.app.market_data.scheduler import RoundRobinScanScheduler
from backend.app.repository import SQLiteProjectionRepository


class FakeTime:
    def __init__(self) -> None:
        self.current = 0.0

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds

    def wall(self) -> datetime:
        return datetime.fromtimestamp(self.current, timezone.utc)


def test_default_registry_contains_target_twenty_five_and_twenty_four_enabled() -> None:
    instruments = twelve_data_instruments()
    registry = CanonicalInstrumentRegistry(instruments)
    assert tuple(item.instrument_id for item in registry.all()) == ALL_INSTRUMENT_IDS
    assert tuple(item.instrument_id for item in registry.enabled()) == DEFAULT_ENABLED_INSTRUMENT_IDS
    assert len(TARGET_INSTRUMENT_IDS) == 25
    assert len({item.instrument_id for item in instruments}) == 38
    assert all(item.enabled and item.provider_symbol for item in instruments[:10])
    assert tuple(item.instrument_id for item in registry.enabled()[10:12]) == (
        "BTC_USD",
        "ETH_USD",
    )
    assert all(item.exchange == "Binance" for item in registry.enabled()[10:12])
    assert len(registry.enabled()) == 24
    assert registry.by_id("TLT_US_ETF").enabled is False
    assert all(
        registry.by_id(item).enabled
        for item in ETF_INSTRUMENT_IDS
        if item != "TLT_US_ETF"
    )
    assert all(not registry.by_id(item).enabled for item in DISABLED_DIRECT_MARKET_IDS)
    assert registry.by_id("XAU_USD").asset_class == "METAL"
    assert all(
        item.asset_class == "FOREX"
        for item in instruments[:10]
        if item.instrument_id != "XAU_USD"
    )


def test_registry_can_disable_without_reordering() -> None:
    registry = CanonicalInstrumentRegistry(
        twelve_data_instruments(("GBP_USD", "XAU_USD"))
    )
    assert tuple(item.instrument_id for item in registry.enabled()) == (
        "GBP_USD",
        "XAU_USD",
    )


def test_xau_h1_uses_the_approved_canonical_profile() -> None:
    instrument = CanonicalInstrumentRegistry(twelve_data_instruments()).by_id("XAU_USD")
    result = CandleNormalizer().normalize(
        RawProviderCandle(
            provider_id="TWELVE_DATA",
            provider_symbol="XAU/USD",
            timeframe=Timeframe.H1,
            raw_open_time="2026-08-05T09:00:00Z",
            raw_close_time="2026-08-05T10:00:00Z",
            open="4200",
            high="4210",
            low="4190",
            close="4205",
            volume=None,
            is_complete=True,
            session_timezone="UTC",
        ),
        instrument,
    )
    assert result.candle is not None
    assert result.candle.construction_profile_version == "IC_MARKETS_NY_CLOSE_FOREX_V1"


def test_global_limiter_enforces_spacing_and_rolling_window() -> None:
    fake = FakeTime()
    limiter = SlidingWindowRateLimiter(
        max_requests=8,
        min_interval_seconds=8,
        monotonic=fake.monotonic,
        sleep=fake.sleep,
        wall_clock=fake.wall,
    )
    starts = [limiter.acquire() for _ in range(10)]
    assert starts == [float(value) for value in range(0, 80, 8)]
    assert all(right - left >= 8 for left, right in zip(starts, starts[1:]))
    assert all(
        sum(start <= value < start + 60 for value in starts) <= 8 for start in starts
    )


def test_multiple_request_tasks_cannot_bypass_global_limiter() -> None:
    fake = FakeTime()
    limiter = SlidingWindowRateLimiter(
        monotonic=fake.monotonic,
        sleep=fake.sleep,
        wall_clock=fake.wall,
    )
    starts: list[float] = []

    def request() -> None:
        starts.append(limiter.acquire())

    threads = [Thread(target=request) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    starts.sort()
    assert starts == [float(value) for value in range(0, 64, 8)]


def test_scheduler_rotates_deterministically_and_prevents_overlap() -> None:
    instruments = twelve_data_instruments()
    scheduler = RoundRobinScanScheduler()
    with scheduler.cycle(instruments) as first:
        assert first is not None
        assert first[0].instrument_id == "EUR_USD"
        with scheduler.cycle(instruments) as overlap:
            assert overlap is None
    with scheduler.cycle(instruments) as second:
        assert second is not None
        assert second[0].instrument_id == "GBP_USD"
        assert {item.instrument_id for item in second} == set(
            DEFAULT_ENABLED_INSTRUMENT_IDS
        )


class FakeInstrumentProvider:
    def __init__(
        self,
        instrument_id: str,
        calls: list[str],
        limiter: SlidingWindowRateLimiter,
        *,
        fail_once: bool = False,
    ) -> None:
        self.instrument_id = instrument_id
        self.calls = calls
        self.limiter = limiter
        self.fail_once = fail_once
        self.attempts = 0

    def fetch_completed_bars(self, as_of: datetime) -> tuple[()]:
        del as_of
        self.limiter.acquire()
        self.calls.append(self.instrument_id)
        self.attempts += 1
        if self.fail_once and self.attempts == 1:
            raise MarketDataProviderError(
                ProviderErrorCode.TIMEOUT,
                HealthState.DATA_UNAVAILABLE,
                "Instrument timeout.",
                retryable=True,
            )
        return ()

    def health(self, as_of: datetime) -> ProviderHealth:
        return ProviderHealth(
            provider_id="TWELVE_DATA",
            state=HealthState.HEALTHY,
            checked_at=as_of,
            latest_completed_close=as_of,
            freshness_seconds=0,
            detail="Healthy.",
            synthetic=False,
        )

    def telemetry(self) -> SimpleNamespace:
        counts = {value.value: 0 for value in Timeframe}
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


def test_retry_is_rate_limited_and_requeued_after_other_instruments() -> None:
    selected_ids = ("EUR_USD", "GBP_USD", "USD_JPY")
    instruments = twelve_data_instruments(selected_ids)
    fake = FakeTime()
    limiter = SlidingWindowRateLimiter(
        monotonic=fake.monotonic,
        sleep=fake.sleep,
        wall_clock=fake.wall,
    )
    calls: list[str] = []
    providers = {
        instrument_id: FakeInstrumentProvider(
            instrument_id,
            calls,
            limiter,
            fail_once=instrument_id == "EUR_USD",
        )
        for instrument_id in selected_ids
    }
    provider = MultiInstrumentTwelveDataProvider(
        "test-key",
        instruments,
        limiter=limiter,
        max_retries_per_instrument=2,
        providers=providers,
    )
    provider.fetch_completed_bars(datetime(2026, 8, 5, tzinfo=timezone.utc))
    assert calls == ["EUR_USD", "GBP_USD", "USD_JPY", "EUR_USD"]
    assert limiter.starts() == (0.0, 8.0, 16.0, 24.0)
    assert provider.scan_failures() == {}


def test_sqlite_partitioning_and_instrument_error_recovery(tmp_path: Path) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "scanner.sqlite3")
    repository.initialize()
    close = datetime(2026, 8, 5, 10, tzinfo=timezone.utc)

    def bar(instrument_id: str, value: str) -> Bar:
        return Bar(
            instrument_id=instrument_id,
            timeframe=Timeframe.H1,
            open_time=close.replace(hour=9),
            close_time=close,
            open=Decimal(value),
            high=Decimal(value) + Decimal("0.1"),
            low=Decimal(value) - Decimal("0.1"),
            close=Decimal(value),
            provider="TWELVE_DATA",
            is_complete=True,
            synthetic=False,
        )

    assert (
        repository.persist_canonical_bars(
            (bar("EUR_USD", "1.1"), bar("GBP_USD", "2.1"))
        )
        == 2
    )
    assert repository.persist_canonical_bars((bar("EUR_USD", "1.1"),)) == 0
    persisted = repository.canonical_bars()
    assert {(item["instrument_id"], item["close"]) for item in persisted} == {
        ("EUR_USD", "1.1"),
        ("GBP_USD", "2.1"),
    }

    failed = ProviderHealth(
        provider_id="TWELVE_DATA",
        state=HealthState.DATA_UNAVAILABLE,
        checked_at=close,
        latest_completed_close=None,
        freshness_seconds=None,
        detail="GBP/USD failed.",
        synthetic=False,
    )
    repository.update_instrument_health("GBP_USD", failed, error_code="VALIDATION")
    repository.update_instrument_health("EUR_USD", replace(failed, detail="EUR ok"))
    healthy = replace(
        failed,
        state=HealthState.HEALTHY,
        latest_completed_close=close,
        freshness_seconds=0,
        detail="EUR/USD healthy.",
    )
    repository.update_instrument_health("EUR_USD", healthy)
    assert (
        repository.instrument_health("TWELVE_DATA", "GBP_USD")["latest_error_code"]
        == "VALIDATION"
    )
    eur = repository.instrument_health("TWELVE_DATA", "EUR_USD")
    assert eur["latest_error_code"] is None
    assert eur["last_success_at"] == "2026-08-05T10:00:00Z"


def test_scanner_api_returns_enabled_typed_ordered_bootstrap_rows(
    tmp_path: Path,
) -> None:
    settings = Settings(
        repository_root=Path(__file__).resolve().parents[2],
        database_path=tmp_path / "live.sqlite3",
        historical_replay_database_path=tmp_path / "replay.sqlite3",
        internal_api_key="scanner-internal-key",
        market_data_provider="twelve_data",
        twelve_data_api_key="provider-secret-that-must-not-leak",
        auto_seed_synthetic=False,
        market_data_runtime_enabled=False,
        market_scan_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/scanner",
            headers={"X-Spect8-Internal-Key": "scanner-internal-key"},
        )
    assert response.status_code == 200
    payload = response.json()
    rows = payload["data"]["instruments"]
    assert [item["instrument_id"] for item in rows] == list(
        DEFAULT_ENABLED_INSTRUMENT_IDS
    )
    assert all(item["data_status"] == "BOOTSTRAPPING" for item in rows)
    assert all(item["H1"]["signal_status"] == "WAITING" for item in rows)
    assert all(item["current_filter"] == {
        "status": "WAITING",
        "as_of_h1_close_time": None,
        "snapshot_id": None,
        "source": "WAITING",
    } for item in rows)
    assert len(rows) == 24
    spy = next(item for item in rows if item["instrument_id"] == "SPY_US_ETF")
    assert spy["instrument_kind"] == "ETF"
    assert spy["exposure_category"] == "US_LARGE_CAP_EQUITY"
    assert spy["is_proxy"] is True
    assert spy["proxy_for"] == "SP_500"
    assert spy["provider_symbol"] == "SPY"
    assert spy["provider_exchange"] == "NYSE Arca"
    assert payload["data"]["credit_budget"]["reserve_preserved"] is True
    assert "provider-secret-that-must-not-leak" not in response.text


def test_scanner_current_filter_uses_latest_completed_h1_snapshot() -> None:
    instrument = twelve_data_instruments()[0]

    class Repository:
        def statuses(self):
            common = {
                "provider": instrument.provider_id,
                "instrument_id": instrument.instrument_id,
                "strategy_version": CURRENT_D1_FILTER_V2,
                "signal_result": {"confirmed_buy": False, "confirmed_sell": False},
                "daily_filter_snapshot_id": "older-evaluation-snapshot",
            }
            return [
                {
                    **common,
                    "timeframe": "H1",
                    "filter_result": {"buy_matched": True, "sell_matched": False},
                    "signal_bar_close_time": "2026-08-05T06:00:00Z",
                },
                {
                    **common,
                    "timeframe": "H4",
                    "filter_result": {"buy_matched": False, "sell_matched": False},
                    "signal_bar_close_time": "2026-08-05T05:00:00Z",
                },
            ]

        def instrument_health(self, provider, instrument_id):
            return None

        def latest_candle_timestamps(self, provider, instrument_id):
            return {}

        def latest_daily_filter_snapshot(
            self, provider, instrument_id, strategy_version
        ):
            return {
                "snapshot_id": "latest-completed-h1-snapshot",
                "as_of_h1_close_time_utc": "2026-08-05T07:00:00Z",
                "final_classification": "SELL",
                "buy_matched": False,
                "sell_matched": True,
            }

    data = scanner_snapshot(
        Repository(),
        (instrument,),
        datetime(2026, 8, 5, 7, 1, tzinfo=timezone.utc),
    )
    row = data.instruments[0]

    assert row.H1.filter_status == "BUY"
    assert row.H4.filter_status == "NONE"
    assert row.current_filter.status == "SELL"
    assert row.current_filter.snapshot_id == "latest-completed-h1-snapshot"
    assert row.current_filter.as_of_h1_close_time == datetime(
        2026, 8, 5, 7, tzinfo=timezone.utc
    )


def test_scanner_migration_adds_query_indexes(tmp_path: Path) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "migration.sqlite3")
    repository.initialize()
    repository.initialize()
    import sqlite3

    with sqlite3.connect(repository.database_path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    assert {
        "idx_canonical_bars_instrument_time",
        "idx_status_instrument_strategy_time",
        "idx_snapshots_instrument_time",
        "idx_events_instrument_time",
        "idx_credit_ledger_provider_time",
    }.issubset(names)
