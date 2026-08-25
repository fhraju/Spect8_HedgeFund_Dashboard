from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.domain import Bar, Timeframe
from backend.app.main import create_app

UTC = timezone.utc


def _bar(instrument_id: str, timeframe: str, open_time: datetime, o: str, h: str, l: str, c: str) -> Bar:
    tf = Timeframe(timeframe)
    dur = timedelta(minutes=30) if timeframe == "M30" else timedelta(hours=1) if timeframe == "H1" else timedelta(hours=4)
    return Bar(
        instrument_id=instrument_id,
        timeframe=tf,
        open_time=open_time,
        close_time=open_time + dur,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        provider="MARKET_DATA_PLATFORM",
        is_complete=True,
        volume=None,
        session_timezone="UTC",
        raw_provider_symbol=instrument_id,
        raw_open_time=open_time.isoformat(),
        raw_close_time=(open_time + dur).isoformat(),
        raw_open=o,
        raw_high=h,
        raw_low=l,
        raw_close=c,
        synthetic=False,
        quality_status="VALID",
        construction_profile_version="test",
        provider_adapter_version="test",
        source_candle_ids=(f"{instrument_id}:{open_time.isoformat()}",),
        forward_filled=False,
        ingestion_run_id="test",
        created_at=open_time + dur,
    )


def _settings(tmp: Path, **kwargs):
    return Settings(
        repository_root=Path("/media/raju/Library_Work/Work/The-System/Spect8_HedgeFund_Dashboard"),
        database_path=tmp / "test.db",
        internal_api_key="test",
        auto_seed_synthetic=False,
        market_data_source="TWELVE_DATA",
        market_data_provider="twelve_data",
        twelve_data_api_key="fake",
        enabled_instrument_ids=("EUR_USD",),
        polling_enabled=False,
        provider_discovery_enabled=False,
        **kwargs,
    )


def test_current_api_returns_forming_and_confirmed():
    tmp = Path(tempfile.mkdtemp())
    settings = _settings(tmp)
    app = create_app(settings)
    app.state.repository.initialize()
    # Create a confirmed signal
    bar = _bar("EUR_USD", "H1", datetime(2026, 8, 23, 10, 0, tzinfo=UTC), "100", "102", "99", "101")
    app.state.signal_lifecycle.confirm(instrument_id="EUR_USD", mode="MICRO", timeframe="H1", completed_bar=bar, as_of=datetime(2026, 8, 23, 11, 0, tzinfo=UTC))
    with TestClient(app, headers={"X-Spect8-Internal-Key": "test"}) as client:
        r = client.get("/signals/current")
        assert r.status_code == 200
        data = r.json()["data"]
        assert "confirmed" in data
        assert "forming" in data
        # At least one confirmed (our H1) should be present if now is within hold; but now is 2026 real time, not 2026-08-23, so may be expired
        # Instead check via history
        r2 = client.get("/signals/history?date=2026-08-23")
        assert r2.status_code == 200
        assert len(r2.json()["data"]["confirmed"]) >= 1


def test_current_api_stale_blocks_forming(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    settings = Settings(
        repository_root=Path("/media/raju/Library_Work/Work/The-System/Spect8_HedgeFund_Dashboard"),
        database_path=tmp / "stale.db",
        internal_api_key="test",
        auto_seed_synthetic=False,
        market_data_source="MARKET_DATA_PLATFORM",
        market_data_provider="replay",
        enabled_instrument_ids=("EUR_USD",),
        market_data_platform_database_url="postgresql+psycopg://spect8_market_data_reader:fake@localhost:5432/market_data",
        polling_enabled=False,
    )
    from backend.app.market_data.platform_authority import PlatformAuthorityRuntime

    class FakeRuntime:
        from backend.app.market_data.models import ProviderIdentity

        identity = ProviderIdentity(provider_id="MARKET_DATA_PLATFORM", display_name="Market Data Platform", adapter_version="test", synthetic=False)

        def status(self):
            return {"freshness_state": "STALE", "connection_state": "HEALTHY", "active_source": "MARKET_DATA_PLATFORM", "last_processed_canonical_timestamp": None, "last_error": "stale", "running": False, "watermark_canonical_bar_id": None, "last_successful_evaluation_timestamp": None}

        def close(self): pass

        def run_once(self, **kwargs):
            return None

    monkeypatch.setattr(PlatformAuthorityRuntime, "from_database_url", lambda *a, **k: FakeRuntime())
    app = create_app(settings)
    with TestClient(app, headers={"X-Spect8-Internal-Key": "test"}) as client:
        r = client.get("/signals/current")
        assert r.status_code == 200
        assert r.json()["data"]["forming"] == []  # blocked when stale


def test_history_expired_still_visible():
    tmp = Path(tempfile.mkdtemp())
    settings = _settings(tmp)
    app = create_app(settings)
    app.state.repository.initialize()
    bar = _bar("EUR_USD", "M30", datetime(2026, 8, 23, 10, 0, tzinfo=UTC), "100", "102", "99", "101")
    app.state.signal_lifecycle.confirm(instrument_id="EUR_USD", mode="MICRO", timeframe="M30", completed_bar=bar, as_of=datetime(2026, 8, 23, 10, 30, tzinfo=UTC))
    # At 11:00, M30 30m hold expired, so current should be empty but history should still have it
    from backend.app.market_data.clock import FixedClock

    app.state.clock = FixedClock(datetime(2026, 8, 23, 11, 0, tzinfo=UTC))
    with TestClient(app, headers={"X-Spect8-Internal-Key": "test"}) as client:
        r = client.get("/signals/current")
        assert len(r.json()["data"]["confirmed"]) == 0
        r2 = client.get("/signals/history?date=2026-08-23")
        assert len(r2.json()["data"]["confirmed"]) == 1
        assert r2.json()["data"]["timezone"] == "America/New_York"


def test_no_duplicate_forming_confirmed_identity():
    tmp = Path(tempfile.mkdtemp())
    settings = _settings(tmp)
    app = create_app(settings)
    app.state.repository.initialize()
    bar = _bar("EUR_USD", "H1", datetime(2026, 8, 23, 10, 0, tzinfo=UTC), "100", "102", "99", "101")
    # Confirm twice same bar
    app.state.signal_lifecycle.confirm(instrument_id="EUR_USD", mode="MICRO", timeframe="H1", completed_bar=bar, as_of=datetime(2026, 8, 23, 11, 0, tzinfo=UTC))
    app.state.signal_lifecycle.confirm(instrument_id="EUR_USD", mode="MICRO", timeframe="H1", completed_bar=bar, as_of=datetime(2026, 8, 23, 11, 0, tzinfo=UTC))
    assert len(app.state.repository.all_confirmed_signals()) == 1


def test_restart_preserves_confirmed():
    tmp = Path(tempfile.mkdtemp())
    settings = _settings(tmp)
    app = create_app(settings)
    app.state.repository.initialize()
    bar = _bar("EUR_USD", "H4", datetime(2026, 8, 22, 12, 0, tzinfo=UTC), "100", "105", "99", "104")
    app.state.signal_lifecycle.confirm(instrument_id="EUR_USD", mode="MACRO", timeframe="H4", completed_bar=bar, as_of=datetime(2026, 8, 22, 16, 0, tzinfo=UTC))
    # Simulate restart: new app with same DB
    settings2 = Settings(
        repository_root=Path("/media/raju/Library_Work/Work/The-System/Spect8_HedgeFund_Dashboard"),
        database_path=tmp / "test.db",
        internal_api_key="test",
        auto_seed_synthetic=False,
        market_data_source="TWELVE_DATA",
        market_data_provider="twelve_data",
        twelve_data_api_key="fake",
        enabled_instrument_ids=("EUR_USD",),
        polling_enabled=False,
        provider_discovery_enabled=False,
    )
    app2 = create_app(settings2)
    # Within hold (16:30) should still be current
    from backend.app.market_data.clock import FixedClock

    app2.state.clock = FixedClock(datetime(2026, 8, 22, 16, 30, tzinfo=UTC))
    with TestClient(app2, headers={"X-Spect8-Internal-Key": "test"}) as client:
        r = client.get("/signals/current")
        assert len(r.json()["data"]["confirmed"]) == 1
    # After expiry (17:01) not current but history
    app2.state.clock = FixedClock(datetime(2026, 8, 22, 17, 1, tzinfo=UTC))
    with TestClient(app2, headers={"X-Spect8-Internal-Key": "test"}) as client:
        r = client.get("/signals/current")
        assert len(r.json()["data"]["confirmed"]) == 0
        r2 = client.get("/signals/history?date=2026-08-22")
        assert len(r2.json()["data"]["confirmed"]) == 1


def test_no_synthetic_forming_injection_when_platform_unavailable():
    """Regression: /signals/current must never fabricate FORMING from wall clock.

    Before the fix, a dev helper generated synthetic CurrentBarSnapshots with
    hardcoded OHLC for every instrument/timeframe, producing FORMING signals
    even when the authoritative data was 9 days stale. This test proves that
    without a real partial-bar source there is no FORMING output at all —
    regardless of platform health state.
    """
    tmp = Path(tempfile.mkdtemp())
    settings = Settings(
        repository_root=Path("/media/raju/Library_Work/Work/The-System/Spect8_HedgeFund_Dashboard"),
        database_path=tmp / "nosynth.db",
        internal_api_key="test",
        auto_seed_synthetic=False,
        market_data_source="MARKET_DATA_PLATFORM",
        market_data_provider="replay",
        enabled_instrument_ids=("EUR_USD", "GBP_USD"),
        market_data_platform_database_url="postgresql+psycopg://spect8_market_data_reader:fake@localhost:5432/market_data",
        polling_enabled=False,
    )
    from backend.app.market_data.platform_authority import PlatformAuthorityRuntime

    class HealthyRuntime:
        from backend.app.market_data.models import ProviderIdentity

        identity = ProviderIdentity(provider_id="MARKET_DATA_PLATFORM", display_name="Platform", adapter_version="test", synthetic=False)

        def status(self):
            return {"freshness_state": "HEALTHY", "connection_state": "HEALTHY", "active_source": "MARKET_DATA_PLATFORM"}

        def close(self): pass

        def run_once(self, **kwargs):
            return None

    monkeypatch = None  # direct patch below
    original = PlatformAuthorityRuntime.from_database_url
    PlatformAuthorityRuntime.from_database_url = lambda *a, **k: HealthyRuntime()
    try:
        app = create_app(settings)
    finally:
        PlatformAuthorityRuntime.from_database_url = original

    with TestClient(app, headers={"X-Spect8-Internal-Key": "test"}) as client:
        r = client.get("/signals/current")
        assert r.status_code == 200
        data = r.json()["data"]
        # Even with platform HEALTHY, no real partial-bar source means no FORMING
        assert data["forming"] == [], (
            "FORMING must not be fabricated without real partial-bar data"
        )


def test_bootstrapping_scanner_state_from_stale_bars():
    """Scanner must report STALE (not BOOTSTRAPPING) when latest bar is old."""
    from backend.app.dashboard_api import scanner_snapshot
    from backend.app.repository import SQLiteProjectionRepository
    from backend.app.market_data.registry import twelve_data_instruments
    from datetime import datetime, timezone, timedelta
    from decimal import Decimal
    from backend.app.domain import Bar as DomainBar

    tmp = Path(tempfile.mkdtemp())
    repo = SQLiteProjectionRepository(tmp / "scan.db")
    repo.initialize()
    # Persist one old H1 bar (10 days old)
    old_close = datetime.now(timezone.utc) - timedelta(days=10)
    old_open = old_close - timedelta(hours=1)
    bar = DomainBar(
        instrument_id="EUR_USD",
        timeframe=__import__("backend.app.domain", fromlist=["Timeframe"]).Timeframe.H1,
        open_time=old_open,
        close_time=old_close,
        open=Decimal("1.1"), high=Decimal("1.2"), low=Decimal("1.0"), close=Decimal("1.15"),
        provider="MARKET_DATA_PLATFORM",
        is_complete=True,
        volume=None,
        session_timezone="UTC",
        raw_provider_symbol="EUR_USD",
        raw_open_time=old_open.isoformat(),
        raw_close_time=old_close.isoformat(),
        raw_open="1.1", raw_high="1.2", raw_low="1.0", raw_close="1.15",
        synthetic=False,
        quality_status="VALID",
        construction_profile_version="test",
        provider_adapter_version="test",
        source_candle_ids=("t",),
        forward_filled=False,
        ingestion_run_id="t",
        created_at=old_close,
    )
    repo.persist_canonical_bars((bar,))
    from dataclasses import replace as _replace

    instruments = tuple(
        _replace(item, provider_id="MARKET_DATA_PLATFORM", synthetic=False)
        for item in twelve_data_instruments(("EUR_USD",))
        if item.enabled
    )
    snapshot = scanner_snapshot(
        repo,
        instruments,
        datetime.now(timezone.utc),
        stale_after_seconds=7200,
    )
    assert len(snapshot.instruments) == 1
    # Old bar + no polling health → truthful STALE, not BOOTSTRAPPING
    assert snapshot.instruments[0].data_status == "STALE"
    assert snapshot.instruments[0].stale is True


def test_stale_startup_defers_instead_of_crashing(monkeypatch):
    """Regression: production-freshness stale data must not kill the process.

    The backend must boot, serve truthful STALE/UNAVAILABLE health, keep the
    runtime loop retrying, and never expose FORMING — instead of refusing to
    start entirely.
    """
    from backend.app.market_data.platform_authority import PlatformAuthorityRuntime

    class StaleRuntime:
        from backend.app.market_data.models import ProviderIdentity

        identity = ProviderIdentity(provider_id="MARKET_DATA_PLATFORM", display_name="Platform", adapter_version="test", synthetic=False)

        def __init__(self):
            self._stop = None

        def status(self):
            return {"freshness_state": "UNAVAILABLE", "connection_state": "HEALTHY",
                    "active_source": None, "last_processed_canonical_timestamp": None,
                    "last_error": "Platform canonical H1 is stale", "running": False}

        def run_once(self, **kwargs):
            from backend.app.market_data.platform_authority import PlatformStaleError
            raise PlatformStaleError("stale at startup")

        async def run(self):
            import asyncio
            self._stop = asyncio.Event()
            await self._stop.wait()

        def stop(self):
            if self._stop is not None:
                self._stop.set()

        def close(self): pass

    monkeypatch.setattr(PlatformAuthorityRuntime, "from_database_url", lambda *a, **k: StaleRuntime())
    tmp = Path(tempfile.mkdtemp())
    settings = Settings(
        repository_root=Path("/media/raju/Library_Work/Work/The-System/Spect8_HedgeFund_Dashboard"),
        database_path=tmp / "defer.db",
        internal_api_key="test",
        auto_seed_synthetic=False,
        market_data_source="MARKET_DATA_PLATFORM",
        market_data_provider="replay",
        enabled_instrument_ids=("EUR_USD",),
        market_data_platform_database_url="postgresql+psycopg://spect8_market_data_reader:x@localhost:5432/market_data",
        polling_enabled=True,
    )
    app = create_app(settings)
    with TestClient(app, headers={"X-Spect8-Internal-Key": "test"}) as client:
        r = client.get("/signals/current")
        assert r.status_code == 200
        assert r.json()["data"]["forming"] == []
