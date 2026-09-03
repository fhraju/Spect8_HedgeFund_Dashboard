from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from backend.app.domain import Bar, Timeframe
from backend.app.market_data.platform_adapter import (
    PlatformCanonicalBar,
    PlatformNativeBootstrapBar,
    to_spect8_bar,
    to_spect8_native_bootstrap_bar,
)
from backend.app.repository import SQLiteProjectionRepository

UTC = timezone.utc


def _canonical(
    cid: int, open_time: datetime, instrument: str = "FX_EUR_USD", open_val: str = "1.1"
) -> PlatformCanonicalBar:
    return PlatformCanonicalBar(
        canonical_bar_id=cid,
        instrument_id=instrument,
        timeframe="H1",
        price_type="BID",
        open_time=open_time,
        close_time=open_time + timedelta(hours=1),
        open=Decimal(open_val),
        high=Decimal("1.2"),
        low=Decimal("1.0"),
        close=Decimal("1.15"),
        volume=Decimal(10),
        volume_type="TICK",
        quality_status="VALID",
        source_provider_id="TEST",
        policy_id="POLICY",
        policy_version="1",
        version_number=1,
        semantic_hash=f"hash-{cid}",
        semantic_available_at=open_time + timedelta(hours=1, seconds=1),
    )


def _native(
    bid: int, open_time: datetime, instrument: str = "FX_EUR_USD", open_val: str = "1.1"
) -> PlatformNativeBootstrapBar:
    return PlatformNativeBootstrapBar(
        bootstrap_bar_id=bid,
        instrument_id=instrument,
        timeframe="H1",
        price_type="BID",
        open_time=open_time,
        close_time=open_time + timedelta(hours=1),
        open=Decimal(open_val),
        high=Decimal("1.2"),
        low=Decimal("1.0"),
        close=Decimal("1.15"),
        volume=Decimal(10),
        volume_type="UNKNOWN",
        source_provider_id="IG_DEMO",
        provenance="NATIVE_IG_HISTORICAL_BOOTSTRAP",
        observed_at=open_time + timedelta(hours=1),
        raw_snapshot_time="raw",
        raw_snapshot_time_utc="raw-utc",
    )


def _repo(tmp_path: Path) -> SQLiteProjectionRepository:
    repo = SQLiteProjectionRepository(tmp_path / "test.sqlite3")
    repo.initialize()
    return repo


def test_native_only_h1_propagates_incrementally(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    T = datetime(2026, 9, 2, 21, 0, tzinfo=UTC)
    before = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
    from backend.app.market_data.platform_adapter import to_spect8_bar

    repo.persist_canonical_bars((to_spect8_bar(_canonical(1, before)),))
    assert any(b.open_time == before for b in repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1"))
    assert not any(b.open_time == T for b in repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1"))
    bar = to_spect8_native_bootstrap_bar(_native(999, T))
    repo.persist_canonical_bars((bar,))
    after = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    assert any(b.open_time == T for b in after)


def test_canonical_duplicate_wins(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    T = datetime(2026, 9, 2, 21, 0, tzinfo=UTC)
    from backend.app.market_data.platform_adapter import (
        to_spect8_bar,
        to_spect8_native_bootstrap_bar,
    )

    canon_bar = to_spect8_bar(_canonical(1, T, open_val="1.1"))
    repo.persist_canonical_bars((canon_bar,))
    native_bar = to_spect8_native_bootstrap_bar(_native(999, T, open_val="9.9"))
    repo.persist_canonical_bars((native_bar,))
    after = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    assert len(after) == 1
    stored = next(b for b in after if b.open_time == T)
    assert stored.open == Decimal("1.1")
    assert "canonical" in stored.provider_adapter_version.lower()
    assert stored.open != Decimal("9.9")


def test_repeated_processing_is_idempotent(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    T = datetime(2026, 9, 2, 21, 0, tzinfo=UTC)
    bar = to_spect8_native_bootstrap_bar(_native(999, T))
    repo.persist_canonical_bars((bar,))
    first = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    repo.persist_canonical_bars((bar,))
    second = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    assert len(first) == len(second)


def test_native_fix_enables_d1_and_w1_builders(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    T = datetime(2026, 9, 2, 21, 0, tzinfo=UTC)
    bar = to_spect8_native_bootstrap_bar(_native(999, T))
    repo.persist_canonical_bars((bar,))
    assert any(b.open_time == T for b in repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1"))
    repo.persist_canonical_bars((bar,))
    assert len([b for b in repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1") if b.open_time == T]) == 1


def test_repository_native_to_canonical_exact(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    T = datetime(2026, 9, 2, 21, 0, tzinfo=UTC)
    from backend.app.market_data.platform_adapter import to_spect8_bar

    native_bar = to_spect8_native_bootstrap_bar(_native(999, T, open_val="9.9"))
    repo.persist_canonical_bars((native_bar,))
    before = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    assert len([b for b in before if b.open_time == T]) == 1
    assert before[0].open == Decimal("9.9")
    assert "NATIVE_IG_HISTORICAL_BOOTSTRAP" in before[0].provider_adapter_version
    canon_bar = to_spect8_bar(_canonical(1, T, open_val="1.1"))
    repo.persist_canonical_bars((canon_bar,))
    after = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    assert len([b for b in after if b.open_time == T]) == 1
    stored = next(b for b in after if b.open_time == T)
    assert stored.open == Decimal("1.1")
    assert "canonical" in stored.provider_adapter_version.lower()
    assert stored.open != Decimal("9.9")


def test_repository_canonical_to_native_exact(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    T = datetime(2026, 9, 2, 21, 0, tzinfo=UTC)
    from backend.app.market_data.platform_adapter import to_spect8_bar

    canon_bar = to_spect8_bar(_canonical(1, T, open_val="1.1"))
    repo.persist_canonical_bars((canon_bar,))
    native_bar = to_spect8_native_bootstrap_bar(_native(999, T, open_val="9.9"))
    repo.persist_canonical_bars((native_bar,))
    after = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    assert len([b for b in after if b.open_time == T]) == 1
    stored = next(b for b in after if b.open_time == T)
    assert stored.open == Decimal("1.1")
    assert "canonical" in stored.provider_adapter_version.lower()


def test_repository_native_idempotent_exact(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    T = datetime(2026, 9, 2, 21, 0, tzinfo=UTC)
    bar = to_spect8_native_bootstrap_bar(_native(999, T, open_val="9.9"))
    repo.persist_canonical_bars((bar,))
    first = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    repo.persist_canonical_bars((bar,))
    second = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    assert len(first) == len(second) == 1
    assert second[0].open == Decimal("9.9")


def test_repository_canonical_idempotent_exact(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    T = datetime(2026, 9, 2, 21, 0, tzinfo=UTC)
    from backend.app.market_data.platform_adapter import to_spect8_bar

    bar = to_spect8_bar(_canonical(1, T, open_val="1.1"))
    repo.persist_canonical_bars((bar,))
    first = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    repo.persist_canonical_bars((bar,))
    second = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    assert len(first) == len(second) == 1
    assert second[0].open == Decimal("1.1")


def test_canonical_with_native_substring_is_canonical(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    T = datetime(2026, 9, 2, 21, 0, tzinfo=UTC)
    native_bar = to_spect8_native_bootstrap_bar(_native(999, T, open_val="9.9"))
    repo.persist_canonical_bars((native_bar,))
    canon_with_native_text = Bar(
        instrument_id="EUR_USD",
        timeframe=Timeframe.H1,
        open_time=T,
        close_time=T + timedelta(hours=1),
        open=Decimal("1.1"),
        high=Decimal("1.2"),
        low=Decimal("1.0"),
        close=Decimal("1.15"),
        provider="MARKET_DATA_PLATFORM",
        is_complete=True,
        volume=Decimal(10),
        session_timezone="UTC",
        raw_provider_symbol="FX_EUR_USD",
        raw_open_time=T.isoformat().replace("+00:00", "Z"),
        raw_close_time=(T + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        raw_open="1.1",
        raw_high="1.2",
        raw_low="1.0",
        raw_close="1.15",
        synthetic=False,
        quality_status="VALID",
        construction_profile_version="IC_MARKETS_NY_CLOSE_FOREX_V1",
        provider_adapter_version="NATIVE_CANONICAL_POLICY",
        source_timeframe=Timeframe.H1,
        source_candle_ids=("MDP:1:hash",),
        forward_filled=False,
        expected_closure_before=False,
        ingestion_run_id="mdp-canonical-1",
        created_at=T + timedelta(hours=1, seconds=1),
    )
    repo.persist_canonical_bars((canon_with_native_text,))
    after = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1")
    assert len([b for b in after if b.open_time == T]) == 1
    stored = next(b for b in after if b.open_time == T)
    assert stored.open == Decimal("1.1")
    assert stored.provider_adapter_version == "NATIVE_CANONICAL_POLICY"
    malformed = Bar(
        instrument_id="EUR_USD",
        timeframe=Timeframe.H1,
        open_time=T + timedelta(hours=1),
        close_time=T + timedelta(hours=2),
        open=Decimal("2.2"),
        high=Decimal("2.3"),
        low=Decimal("2.1"),
        close=Decimal("2.25"),
        provider="MARKET_DATA_PLATFORM",
        is_complete=True,
        volume=Decimal(10),
        session_timezone="UTC",
        raw_provider_symbol="FX_EUR_USD",
        raw_open_time=(T + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        raw_close_time=(T + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        raw_open="2.2",
        raw_high="2.3",
        raw_low="2.1",
        raw_close="2.25",
        synthetic=False,
        quality_status="VALID",
        construction_profile_version="IC_MARKETS_NY_CLOSE_FOREX_V1",
        provider_adapter_version="UNKNOWN_RANDOM_VERSION",
        source_timeframe=Timeframe.H1,
        source_candle_ids=("MDP:2:hash",),
        forward_filled=False,
        expected_closure_before=False,
        ingestion_run_id="mdp-canonical-2",
        created_at=T + timedelta(hours=2, seconds=1),
    )
    repo.persist_canonical_bars((malformed,))
    assert any(b.open_time == malformed.open_time for b in repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "EUR_USD", "H1"))


def test_prepare_histories_nzdjpy_native_propagates(tmp_path: Path) -> None:
    from backend.app.engine.strategy import Spect8StrategyEvaluator
    from backend.app.market_data.platform_adapter import (
        PlatformNativeBootstrapBar,
        PlatformReadBatch,
    )
    from backend.app.market_data.platform_authority import PlatformAuthorityRuntime
    from backend.app.service import WalkingSkeletonService

    T = datetime(2026, 9, 2, 21, 0, tzinfo=UTC)
    repo = SQLiteProjectionRepository(tmp_path / "test.sqlite3")
    repo.initialize()
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repo)

    class _FakeGateway:
        def read(self, *args, **kwargs):
            raise AssertionError("gateway should not be called in _prepare_histories test")

    gateway = _FakeGateway()
    runtime = PlatformAuthorityRuntime(
        gateway=gateway,  # type: ignore[arg-type]
        repository=repo,
        service=service,
        instrument_ids=("NZD_JPY",),
        stale_after_seconds=3600,
        poll_seconds=30,
    )
    histories: dict = {}
    native_pnb = PlatformNativeBootstrapBar(
        bootstrap_bar_id=999,
        instrument_id="FX_NZD_JPY",
        timeframe="H1",
        price_type="BID",
        open_time=T,
        close_time=T + timedelta(hours=1),
        open=Decimal("1.1"),
        high=Decimal("1.2"),
        low=Decimal("1.0"),
        close=Decimal("1.15"),
        volume=Decimal(10),
        volume_type="UNKNOWN",
        source_provider_id="IG_DEMO",
        provenance="NATIVE_IG_HISTORICAL_BOOTSTRAP",
        observed_at=T + timedelta(hours=1),
        raw_snapshot_time="raw",
        raw_snapshot_time_utc="raw-utc",
    )
    batch = PlatformReadBatch(
        bars=(),
        availability=(),
        watermark_canonical_bar_id=0,
        available_as_of=T + timedelta(hours=1),
        instrument_master_checksum="a",
        session_calendar_checksum="b",
        timezone_data_version="tz",
        native_bootstrap_bars=(native_pnb,),
        partial_bar_snapshots=(),
    )
    candidates = runtime._prepare_histories(batch, histories, first_activation=False, startup_replay=False)
    assert any(b.open_time == T for b in repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "NZD_JPY", "H1"))
    assert len([b for b in repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "NZD_JPY", "H1") if b.open_time == T]) == 1
    stored = next(b for b in repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "NZD_JPY", "H1") if b.open_time == T)
    assert stored.open == Decimal("1.1")
    assert "NATIVE_IG_HISTORICAL_BOOTSTRAP" in stored.provider_adapter_version
    assert any(close == T + timedelta(hours=1) for _, _, _, close in candidates)
    # Canonical later upgrades native
    canon = PlatformCanonicalBar(
        canonical_bar_id=1,
        instrument_id="FX_NZD_JPY",
        timeframe="H1",
        price_type="BID",
        open_time=T,
        close_time=T + timedelta(hours=1),
        open=Decimal("2.2"),
        high=Decimal("2.3"),
        low=Decimal("2.1"),
        close=Decimal("2.25"),
        volume=Decimal(10),
        volume_type="TICK",
        quality_status="VALID",
        source_provider_id="TEST",
        policy_id="POLICY",
        policy_version="1",
        version_number=1,
        semantic_hash="hash-1",
        semantic_available_at=T + timedelta(hours=1, seconds=1),
    )
    repo.persist_canonical_bars((to_spect8_bar(canon),))
    after = repo.canonical_bar_objects("MARKET_DATA_PLATFORM", "NZD_JPY", "H1")
    assert len([b for b in after if b.open_time == T]) == 1
    upgraded = next(b for b in after if b.open_time == T)
    assert upgraded.open == Decimal("2.2")
    assert "canonical" in upgraded.provider_adapter_version.lower()
