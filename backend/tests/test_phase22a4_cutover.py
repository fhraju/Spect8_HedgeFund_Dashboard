from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.main import create_app
from backend.app.market_data.models import ProviderIdentity
from backend.app.market_data.platform_adapter import (
    SPECT8_PLATFORM_BOOTSTRAP_LIMITS,
    InsufficientPlatformHistoryError,
    PlatformCanonicalBar,
    PlatformReadBatch,
    PlatformSeriesAvailability,
    build_platform_history,
)
from backend.app.market_data.platform_authority import (
    PlatformAuthorityRuntime,
    PlatformStaleError,
    PlatformUnavailableError,
    _expected_latest_forex_h1_close,
    _live_streaming_readiness,
)
from backend.app.market_data.session_boundaries import (
    NEW_YORK,
    new_york_session_close,
)
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
NOW = datetime(2026, 8, 5, 10, tzinfo=UTC)


class Gateway:
    def __init__(self, batches: list[PlatformReadBatch] | None = None) -> None:
        self.batches = batches or []
        self.calls: list[dict[str, object]] = []

    def read(self, instrument_ids: tuple[str, ...], **kwargs: object) -> PlatformReadBatch:
        self.calls.append({"instrument_ids": instrument_ids, **kwargs})
        return self.batches.pop(0)

    def read_canonical_ids(
        self, _canonical_bar_ids: tuple[int, ...]
    ) -> tuple[PlatformCanonicalBar, ...]:
        return ()


def canonical(
    canonical_bar_id: int,
    timeframe: str,
    open_time: datetime,
    duration: timedelta,
) -> PlatformCanonicalBar:
    return PlatformCanonicalBar(
        canonical_bar_id=canonical_bar_id,
        instrument_id="FX_EUR_USD",
        timeframe=timeframe,
        price_type="BID",
        open_time=open_time,
        close_time=open_time + duration,
        open=Decimal("1.1000"),
        high=Decimal("1.1010"),
        low=Decimal("1.0990"),
        close=Decimal("1.1005"),
        volume=Decimal(100),
        volume_type="TICK",
        quality_status="VALID",
        source_provider_id="TEST_IG",
        policy_id="TEST_POLICY",
        policy_version="1",
        version_number=1,
        semantic_hash=f"hash-{canonical_bar_id}",
        semantic_available_at=open_time + duration,
    )


def is_market_h1_open(value: datetime) -> bool:
    local = value.astimezone(NEW_YORK)
    wall = local.time().replace(tzinfo=None)
    return (
        (local.weekday() == 6 and wall >= time(17))
        or local.weekday() in (0, 1, 2, 3)
        or (local.weekday() == 4 and wall < time(17))
    )


def bootstrap_batch(
    *,
    now: datetime = NOW,
    latest_h1_close: datetime | None = None,
) -> PlatformReadBatch:
    latest = latest_h1_close or now
    opens: list[datetime] = []
    cursor = latest - timedelta(hours=1)
    while len(opens) < SPECT8_PLATFORM_BOOTSTRAP_LIMITS["H1"]:
        if is_market_h1_open(cursor):
            opens.append(cursor)
        cursor -= timedelta(hours=1)
    opens.sort()
    bars: list[PlatformCanonicalBar] = []
    canonical_id = 1
    for open_time in opens:
        bars.append(canonical(canonical_id, "H1", open_time, timedelta(hours=1)))
        canonical_id += 1

    close_date = latest.astimezone(NEW_YORK).date()
    daily_closes: list[datetime] = []
    while len(daily_closes) < SPECT8_PLATFORM_BOOTSTRAP_LIMITS["D1"]:
        close_time = new_york_session_close(close_date)
        if close_date.weekday() < 5 and close_time <= latest:
            daily_closes.append(close_time)
        close_date -= timedelta(days=1)
    for close_time in sorted(daily_closes):
        local_date = close_time.astimezone(NEW_YORK).date()
        open_time = new_york_session_close(local_date - timedelta(days=1))
        bars.append(canonical(canonical_id, "D1", open_time, close_time - open_time))
        canonical_id += 1
    for index in range(SPECT8_PLATFORM_BOOTSTRAP_LIMITS["M30"]):
        bars.append(
            canonical(
                canonical_id,
                "M30",
                latest - timedelta(minutes=30 * (index + 1)),
                timedelta(minutes=30),
            )
        )
        canonical_id += 1
    for index in range(SPECT8_PLATFORM_BOOTSTRAP_LIMITS["W1"]):
        open_time = latest - timedelta(
            days=7 * (SPECT8_PLATFORM_BOOTSTRAP_LIMITS["W1"] - index)
        )
        bars.append(canonical(canonical_id, "W1", open_time, timedelta(days=7)))
        canonical_id += 1
    availability = tuple(
        PlatformSeriesAvailability(
            instrument_id="FX_EUR_USD",
            timeframe=timeframe,
            price_type="BID",
            returned_rows=sum(bar.timeframe == timeframe for bar in bars),
            latest_close_time=max(
                (bar.close_time for bar in bars if bar.timeframe == timeframe),
                default=None,
            ),
            valid=True,
        )
        for timeframe in SPECT8_PLATFORM_BOOTSTRAP_LIMITS
    )
    return PlatformReadBatch(
        bars=tuple(bars),
        availability=availability,
        watermark_canonical_bar_id=canonical_id - 1,
        available_as_of=now,
        instrument_master_checksum="instrument-master-v1",
        session_calendar_checksum="session-calendar-v1",
        timezone_data_version="tzdata-v1",
    )


def repository(tmp_path: Path) -> SQLiteProjectionRepository:
    value = SQLiteProjectionRepository(tmp_path / "spect8.sqlite3")
    value.initialize()
    return value


def runtime(
    tmp_path: Path, gateway: Gateway
) -> tuple[PlatformAuthorityRuntime, SQLiteProjectionRepository]:
    repo = repository(tmp_path)
    value = PlatformAuthorityRuntime(
        gateway,
        repo,
        WalkingSkeletonService(Spect8StrategyEvaluator(), None, repo),
        ("EUR_USD",),
        stale_after_seconds=7200,
        poll_seconds=300,
    )
    return value, repo


@pytest.mark.parametrize("source", ["TWELVE_DATA", "MARKET_DATA_PLATFORM"])
def test_authoritative_source_is_explicitly_selectable(source: str, tmp_path: Path) -> None:
    settings = Settings(
        repository_root=ROOT,
        database_path=tmp_path / "source.sqlite3",
        internal_api_key="test",
        market_data_source=source,
        market_data_provider="twelve_data",
        twelve_data_api_key="fake",
        enabled_instrument_ids=("EUR_USD",),
        market_data_platform_database_url="postgresql+psycopg://reader:secret@host/db",
    )
    settings.validate()
    assert settings.market_data_source == source


def test_invalid_source_and_unmapped_platform_scope_fail_clearly(tmp_path: Path) -> None:
    base = Settings(
        repository_root=ROOT,
        database_path=tmp_path / "invalid.sqlite3",
        internal_api_key="test",
        market_data_provider="replay",
    )
    with pytest.raises(ValueError, match="SPECT8_MARKET_DATA_SOURCE"):
        replace(base, market_data_source="AUTO").validate()
    with pytest.raises(ValueError, match="no approved mapping for: XAU_USD"):
        replace(
            base,
            market_data_source="MARKET_DATA_PLATFORM",
            enabled_instrument_ids=("EUR_USD", "XAU_USD"),
            market_data_platform_database_url="postgresql+psycopg://reader:x@host/db",
        ).validate()


def test_platform_authority_is_the_only_active_runtime_when_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class FakeAuthority:
        identity = ProviderIdentity(
            "MARKET_DATA_PLATFORM", "Platform", "test", False
        )

        def run_once(self, *, available_as_of: datetime) -> dict[str, str]:
            calls.append(f"run:{available_as_of.tzinfo is not None}")
            return {"source": "MARKET_DATA_PLATFORM"}

        def status(self) -> dict[str, object]:
            return {
                "active_source": "MARKET_DATA_PLATFORM",
                "connection_state": "HEALTHY",
                "freshness_state": "HEALTHY",
                "last_processed_canonical_timestamp": None,
                "last_error": None,
                "running": False,
            }

        def close(self) -> None:
            calls.append("close")

    fake = FakeAuthority()
    monkeypatch.setattr(
        "backend.app.main.PlatformAuthorityRuntime.from_database_url",
        lambda *_args, **_kwargs: fake,
    )
    application = create_app(
        Settings(
            repository_root=ROOT,
            database_path=tmp_path / "platform-app.sqlite3",
            internal_api_key="test",
            auto_seed_synthetic=False,
            market_data_source="MARKET_DATA_PLATFORM",
            market_data_provider="replay",
            enabled_instrument_ids=("EUR_USD",),
            market_data_platform_database_url=(
                "postgresql+psycopg://reader:secret@host/db"
            ),
            polling_enabled=False,
        )
    )
    with TestClient(application) as client:
        health = client.get("/health").json()["data"]
        assert application.state.provider is fake
        assert application.state.coordinator is None
        assert application.state.platform_shadow_runtime is None
        assert health["authority"]["configured_source"] == "MARKET_DATA_PLATFORM"
        assert health["authority"]["active_source"] == "MARKET_DATA_PLATFORM"
    assert calls == ["run:True", "close"]


def test_platform_postgresql_unavailable_fails_closed(tmp_path: Path) -> None:
    class UnavailableGateway(Gateway):
        def read(self, *_args: object, **_kwargs: object) -> PlatformReadBatch:
            raise ConnectionError("credential-free test failure")

    authority, repo = runtime(tmp_path, UnavailableGateway())
    with pytest.raises(
        PlatformUnavailableError, match="reader is unavailable"
    ) as caught:
        authority.run_once(available_as_of=NOW)
    assert caught.value.__cause__ is None
    assert repo.processed_count() == 0
    assert authority.status()["active_source"] is None


def test_missing_required_bootstrap_history_fails_startup(tmp_path: Path) -> None:
    bar = canonical(1, "H1", NOW - timedelta(hours=1), timedelta(hours=1))
    batch = PlatformReadBatch(
        bars=(bar,),
        availability=(),
        watermark_canonical_bar_id=1,
        available_as_of=NOW,
        instrument_master_checksum="im",
        session_calendar_checksum="sc",
        timezone_data_version="tz",
    )
    authority, repo = runtime(tmp_path, Gateway([batch]))
    with pytest.raises(InsufficientPlatformHistoryError, match="bootstrap history"):
        authority.run_once(available_as_of=NOW)
    assert repo.platform_integration_state() is None


def test_stale_platform_blocks_confirmed_evaluation(tmp_path: Path) -> None:
    batch = bootstrap_batch(now=NOW, latest_h1_close=NOW - timedelta(hours=3))
    authority, repo = runtime(tmp_path, Gateway([batch]))
    with pytest.raises(PlatformStaleError, match="canonical H1 is stale"):
        authority.run_once(available_as_of=NOW)
    assert authority.status()["freshness_state"] == "STALE"
    assert repo.processed_count() == 0


def test_freshness_policy_does_not_expect_a_sunday_h1_before_it_closes() -> None:
    sunday_before_first_close = datetime(
        2026, 8, 23, 17, 30, tzinfo=NEW_YORK
    ).astimezone(UTC)
    assert _expected_latest_forex_h1_close(sunday_before_first_close) == (
        new_york_session_close(date(2026, 8, 21))
    )


def test_stopped_collector_is_degraded_even_when_strategy_history_is_ready() -> None:
    status = _live_streaming_readiness(
        None,
        ("EUR_USD",),
        as_of=NOW,
        stale_after_seconds=7200,
    )

    assert status["streaming_state"] == "NOT_READY"
    assert status["partial_data_state"] == "NOT_READY"
    assert status["overall_live_readiness"] == "DEGRADED"
    assert status["live_instruments"]["EUR_USD"]["state"] == "NOT_READY"


def test_live_readiness_uses_canonical_m30_not_native_bootstrap_freshness() -> None:
    old_close = NOW - timedelta(hours=4)
    batch = bootstrap_batch(now=NOW, latest_h1_close=old_close)
    status = _live_streaming_readiness(
        batch,
        ("EUR_USD",),
        as_of=NOW,
        stale_after_seconds=7200,
    )

    assert status["streaming_state"] == "NOT_READY"
    assert status["live_instruments"]["EUR_USD"] == {
        "state": "NOT_READY",
        "latest_canonical_m30_timestamp": old_close.isoformat().replace("+00:00", "Z"),
        "expected_latest_m30_timestamp": NOW.isoformat().replace("+00:00", "Z"),
        "lag_seconds": 14400,
    }


def test_recent_canonical_m30_is_streaming_ready_but_partial_remains_fail_closed() -> None:
    batch = bootstrap_batch(now=NOW)
    status = _live_streaming_readiness(
        batch,
        ("EUR_USD",),
        as_of=NOW,
        stale_after_seconds=7200,
    )

    assert status["streaming_state"] == "READY"
    assert status["partial_data_state"] == "NOT_READY"
    assert status["overall_live_readiness"] == "DEGRADED"


def test_healthy_first_activation_bootstraps_and_persists_source_provenance(
    tmp_path: Path,
) -> None:
    batch = bootstrap_batch()
    authority, repo = runtime(tmp_path, Gateway([batch]))
    result = authority.run_once(available_as_of=NOW)
    assert result.bootstrapped is True
    assert result.evaluations_created == 4
    assert result.watermark_canonical_bar_id == batch.watermark_canonical_bar_id
    assert {item["provider"] for item in repo.statuses()} == {
        "MARKET_DATA_PLATFORM"
    }
    assert all(
        ":MARKET_DATA_PLATFORM:" in item["idempotency_key"]
        for item in repo.events()
    )
    for timeframe in ("H1", "H4", "D1"):
        assert {
            bar.provider
            for bar in repo.canonical_bar_objects(
                "MARKET_DATA_PLATFORM", "EUR_USD", timeframe
            )
        } == {"MARKET_DATA_PLATFORM"}


def test_twelve_to_platform_switch_suppresses_same_close_evaluations(
    tmp_path: Path,
) -> None:
    batch = bootstrap_batch()
    authority, repo = runtime(tmp_path, Gateway([batch]))
    h1_close = max(bar.close_time for bar in batch.bars if bar.timeframe == "H1")
    h4_close = build_platform_history(batch, "EUR_USD").h4[-1].close_time
    with sqlite3.connect(repo.database_path) as connection:
        for strategy_id in (
            "MICRO_DAILY_FILTER_CURRENT_D1_V2",
            "MACRO_WEEKLY_FILTER_CURRENT_W1_V1",
        ):
            for timeframe, close_time in (("H1", h1_close), ("H4", h4_close)):
                close_value = close_time.isoformat().replace("+00:00", "Z")
                connection.execute(
                    """INSERT INTO instrument_status (
                           strategy_id, provider, instrument_id, timeframe,
                           status_json, updated_at, synthetic
                       ) VALUES (?, 'TWELVE_DATA', 'EUR_USD', ?, ?, ?, 0)""",
                    (
                        strategy_id,
                        timeframe,
                        '{"signal_bar_close_time":"' + close_value + '"}',
                        close_value,
                    ),
                )
        connection.commit()
    before = repo.statuses()
    result = authority.run_once(available_as_of=NOW)
    assert result.evaluations_created == 0
    assert result.duplicate_evaluations_prevented == 4
    assert repo.processed_count() == 0
    assert repo.statuses() == before
    assert repo.platform_integration_state() is not None


def test_restart_reads_after_watermark_and_replay_is_idempotent(tmp_path: Path) -> None:
    first = bootstrap_batch()
    new_bar = canonical(
        first.watermark_canonical_bar_id + 1,
        "H1",
        NOW,
        timedelta(hours=1),
    )
    incremental = PlatformReadBatch(
        bars=(new_bar,),
        availability=(
            PlatformSeriesAvailability(
                "FX_EUR_USD", "H1", "BID", 1, new_bar.close_time, True
            ),
        ),
        watermark_canonical_bar_id=new_bar.canonical_bar_id,
        available_as_of=new_bar.close_time,
        instrument_master_checksum="instrument-master-v1",
        session_calendar_checksum="session-calendar-v1",
        timezone_data_version="tzdata-v1",
    )
    gateway = Gateway([first, incremental])
    authority, repo = runtime(tmp_path, gateway)
    authority.run_once(available_as_of=NOW)
    before = len(repo.events())
    resumed = authority.run_once(available_as_of=new_bar.close_time)
    assert gateway.calls[1]["after_canonical_bar_id"] == first.watermark_canonical_bar_id
    assert resumed.previous_watermark == first.watermark_canonical_bar_id
    assert resumed.watermark_canonical_bar_id == new_bar.canonical_bar_id
    assert len(repo.events()) >= before
    consumed = repo.platform_consumed_identity(new_bar.logical_identity)
    assert consumed is not None


def test_platform_to_twelve_rollback_preserves_strategy_and_platform_provenance(
    tmp_path: Path,
) -> None:
    batch = bootstrap_batch()
    authority, repo = runtime(tmp_path, Gateway([batch]))
    authority.run_once(available_as_of=NOW)
    events_before = repo.events()
    state_before = repo.platform_integration_state()
    settings = Settings(
        repository_root=ROOT,
        database_path=repo.database_path,
        internal_api_key="test",
        auto_seed_synthetic=False,
        market_data_source="TWELVE_DATA",
        market_data_provider="twelve_data",
        twelve_data_api_key="fake",
        enabled_instrument_ids=("EUR_USD",),
        polling_enabled=False,
        provider_discovery_enabled=False,
    )
    application = create_app(settings)
    with TestClient(application) as client:
        assert client.get("/health").json()["data"]["authority"] == {
            "configured_source": "TWELVE_DATA",
            "active_source": "TWELVE_DATA",
            "platform": None,
        }
    assert repo.events() == events_before
    assert repo.platform_integration_state() == state_before


def test_platform_shadow_cannot_be_enabled_as_authority(tmp_path: Path) -> None:
    settings = Settings(
        repository_root=ROOT,
        database_path=tmp_path / "shadow.sqlite3",
        internal_api_key="test",
        market_data_source="MARKET_DATA_PLATFORM",
        market_data_provider="replay",
        enabled_instrument_ids=("EUR_USD",),
        market_data_platform_database_url="postgresql+psycopg://reader:x@host/db",
        market_data_platform_shadow_enabled=True,
    )
    with pytest.raises(ValueError, match="shadow mode cannot be enabled"):
        settings.validate()
