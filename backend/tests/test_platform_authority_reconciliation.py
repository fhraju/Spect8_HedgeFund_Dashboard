"""Selective-merge reconciliation tests for platform_authority.py.

Pins the approved runtime behaviors being ported into tracked source:
1. cycle-overlap guard fails closed without overlapping processing,
2. native H1 bootstrap skips already-present opens (no duplicates),
3. multi-gateway watermark precedence (per-authority stored watermarks;
   None on startup),
4. FormingRuntimeMixin (not a Unified duplicate) serves forming requests.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.market_data.platform_authority import (
    PlatformAuthorityRuntime,
    PlatformUnavailableError,
    UnifiedPlatformAuthorityRuntime,
)


class StopAfterCapture(RuntimeError):
    pass


def _repo(tmp_path: Path, name: str = "test.sqlite3"):
    from backend.app.repository import SQLiteProjectionRepository
    repo = SQLiteProjectionRepository(tmp_path / name)
    repo.initialize()
    return repo


def _service(repo):
    from backend.app.engine.strategy import Spect8StrategyEvaluator
    from backend.app.service import WalkingSkeletonService
    return WalkingSkeletonService(Spect8StrategyEvaluator(), None, repo)


def _runtime(repo, instrument_ids=("NZD_JPY",)):
    return PlatformAuthorityRuntime(
        gateway=object(),
        repository=repo,
        service=_service(repo),
        instrument_ids=instrument_ids,
        stale_after_seconds=3600,
        poll_seconds=30,
    )


def test_cycle_overlap_fails_closed_without_processing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    runtime = _runtime(repo)
    lock = getattr(runtime, "_cycle_lock", None)
    assert lock is not None, "overlap guard missing"
    called = []
    runtime._gateway = None

    class _Gateway:
        def read(self, *args, **kwargs):
            called.append(True)
            raise AssertionError("no processing during overlap")

    runtime._gateway = _Gateway()
    lock.acquire()
    try:
        with pytest.raises(PlatformUnavailableError, match="overlapped"):
            runtime.run_once(available_as_of=datetime(2026, 9, 18, 6, tzinfo=UTC))
    finally:
        lock.release()
    assert called == []


def test_native_bootstrap_skips_existing_opens(tmp_path: Path, monkeypatch) -> None:
    from backend.app.market_data.platform_adapter import (
        PlatformNativeBootstrapBar,
        PlatformReadBatch,
    )

    repo = _repo(tmp_path)
    runtime = _runtime(repo)
    T = datetime(2026, 9, 2, 21, 0, tzinfo=UTC)

    def bar(open_time, bar_id):
        return PlatformNativeBootstrapBar(
            bootstrap_bar_id=bar_id, instrument_id="FX_NZD_JPY", timeframe="H1",
            price_type="BID", open_time=open_time, close_time=open_time + timedelta(hours=1),
            open=Decimal("1.1"), high=Decimal("1.2"), low=Decimal("1.0"),
            close=Decimal("1.15"), volume=Decimal(10), volume_type="UNKNOWN",
            source_provider_id="IG_DEMO", provenance="NATIVE_IG_HISTORICAL_BOOTSTRAP",
            observed_at=open_time + timedelta(hours=1),
            raw_snapshot_time="raw", raw_snapshot_time_utc="raw-utc")

    batch = PlatformReadBatch(
        bars=(), availability=(), watermark_canonical_bar_id=0,
        available_as_of=T + timedelta(hours=2), instrument_master_checksum="a",
        session_calendar_checksum="b", timezone_data_version="tz",
        native_bootstrap_bars=(bar(T, 1), bar(T + timedelta(hours=1), 2)),
        partial_bar_snapshots=())
    histories: dict = {}
    runtime._prepare_histories(batch, histories, first_activation=False, startup_replay=False)

    persists = []
    original = repo.persist_canonical_bars
    monkeypatch.setattr(repo, "persist_canonical_bars",
                        lambda bars: (persists.append(tuple(bars)), original(bars))[1])
    lookups = []
    original_lookup = repo.existing_open_times
    monkeypatch.setattr(repo, "existing_open_times",
                        lambda *a: (lookups.append(a), original_lookup(*a))[1])
    # Second identical run: everything already present.
    runtime._prepare_histories(batch, histories, first_activation=False, startup_replay=False)
    assert lookups, "native bootstrap must consult existing opens"
    rows = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "NZD_JPY", "H1")
    opens = [b.open_time for b in rows]
    assert len(opens) == len(set(opens)), "duplicate canonical H1 persisted"


def _unified(repo, gateway):
    return UnifiedPlatformAuthorityRuntime(
        gateways={"IG_DEMO": gateway},
        repository=repo,
        service=_service(repo),
        instrument_ids=("NZD_JPY",),
        instrument_to_authority={"NZD_JPY": "IG_DEMO"},
        stale_after_seconds=3600,
        poll_seconds=30,
    )


class _CaptureMulti:
    def __init__(self):
        self.after_ids = None

    def read(self, *args, **kwargs):
        self.after_ids = kwargs.get("after_canonical_bar_ids")
        raise StopAfterCapture()


def _seed_authority_state(repo, authority, watermark):
    import sqlite3
    with sqlite3.connect(repo.database_path) as db:
        db.execute(
            "INSERT OR REPLACE INTO platform_authority_state "
            "(authority, watermark_canonical_bar_id, instrument_master_checksum,"
            " session_calendar_checksum, timezone_data_version, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (authority, watermark, "a", "b", "tz", "2026-09-18T06:00:00Z"))


def test_multi_gateway_watermark_precedence(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    multi = _CaptureMulti()
    runtime = _unified(repo, object())
    runtime._multi = multi
    runtime._startup_replay_complete = True
    _seed_authority_state(repo, "IG_DEMO", 54130)
    with pytest.raises(PlatformUnavailableError):
        runtime.run_once(available_as_of=datetime(2026, 9, 18, 6, tzinfo=UTC))
    assert multi.after_ids == {"IG_DEMO": 54130}


def test_multi_gateway_startup_uses_no_watermark(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    multi = _CaptureMulti()
    runtime = _unified(repo, object())
    runtime._multi = multi
    assert runtime._startup_replay_complete is False
    with pytest.raises(PlatformUnavailableError):
        runtime.run_once(available_as_of=datetime(2026, 9, 18, 6, tzinfo=UTC))
    assert multi.after_ids == {"IG_DEMO": None}


def test_forming_authority_is_mixin_not_duplicate() -> None:
    from backend.app.market_data.forming_runtime import FormingRuntimeMixin
    from backend.app.market_data.isolated_platform import IsolatedPlatformAuthorityRuntime
    assert "cached_forming_signals" not in UnifiedPlatformAuthorityRuntime.__dict__
    assert "forming_diagnostics" not in UnifiedPlatformAuthorityRuntime.__dict__
    assert (IsolatedPlatformAuthorityRuntime.cached_forming_signals
            is FormingRuntimeMixin.cached_forming_signals)
    assert (IsolatedPlatformAuthorityRuntime.forming_diagnostics
            is FormingRuntimeMixin.forming_diagnostics)
