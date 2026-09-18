from dataclasses import replace
from datetime import timedelta

from backend.app.domain import FilterMode, Timeframe
from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.market_data.isolated_platform import (
    InstrumentProjection,
    IsolatedPlatformAuthorityRuntime,
)
from backend.app.market_data.platform_adapter import Spect8CanonicalReadServiceGateway
from backend.app.market_data.signal_lifecycle import SignalLifecycleService
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService
from backend.tests.test_phase22a4_cutover import NOW, bootstrap_batch


class Source:
    def __init__(self, instrument, provider, *, stale=False, broken=False):
        self.broken = broken
        batch = bootstrap_batch(
            now=NOW, latest_h1_close=NOW - timedelta(hours=15) if stale else NOW
        )
        self.batch = replace(
            batch,
            bars=tuple(
                replace(b, instrument_id=instrument, source_provider_id=provider)
                for b in batch.bars
            ),
            availability=tuple(
                replace(a, instrument_id=instrument) for a in batch.availability
            ),
            native_bootstrap_bars=tuple(
                replace(n, instrument_id=instrument, source_provider_id=provider)
                for n in batch.native_bootstrap_bars
            ),
        )

    def read(self, *args, **kwargs):
        if self.broken:
            raise ConnectionError("unavailable")
        return self.batch


def runtime(tmp_path, live):
    repo = SQLiteProjectionRepository(tmp_path / "projection.sqlite3")
    repo.initialize()
    sources = {
        "IG_DEMO": Spect8CanonicalReadServiceGateway(Source("FX_EUR_USD", "IG_DEMO")),
        "IG_LIVE": Spect8CanonicalReadServiceGateway(live),
    }
    value = IsolatedPlatformAuthorityRuntime(
        sources,
        repo,
        WalkingSkeletonService(Spect8StrategyEvaluator(), None, repo),
        ("EUR_USD", "USD_JPY"),
        instrument_to_authority={"EUR_USD": "IG_DEMO", "USD_JPY": "IG_LIVE"},
        stale_after_seconds=7200,
        poll_seconds=30,
        signal_lifecycle=SignalLifecycleService(repo),
    )
    return value, repo


def test_stale_source_does_not_block_healthy_consumption(tmp_path):
    value, repo = runtime(tmp_path, Source("FX_USD_JPY", "IG_LIVE", stale=True))
    value.run_once(available_as_of=NOW)
    statuses = value.status()["collection_instruments"]
    assert statuses["EUR_USD"]["watermark_canonical_bar_id"] > 0
    assert statuses["USD_JPY"]["evaluation_freshness"] == "STALE"
    assert statuses["EUR_USD"]["state"] == "READY"
    # Equal IDs from distinct databases are independently consumed.
    assert InstrumentProjection(repo, "IG_DEMO", "EUR_USD").platform_integration_state()
    assert InstrumentProjection(repo, "IG_LIVE", "USD_JPY").platform_integration_state()


def test_reader_outage_does_not_block_other_authority(tmp_path):
    value, _repo = runtime(tmp_path, Source("FX_USD_JPY", "IG_LIVE", broken=True))
    result = value.run_once(available_as_of=NOW)
    assert result.consumed > 0
    assert value.status()["collection_instruments"]["USD_JPY"]["state"] == "NOT_READY"
    assert (
        value.status()["collection_instruments"]["EUR_USD"][
            "watermark_canonical_bar_id"
        ]
        > 0
    )


def test_history_repaired_behind_ingestion_time_is_queued(tmp_path):
    source = Source("FX_USD_JPY", "IG_LIVE", stale=True)
    value, repo = runtime(tmp_path, source)
    value.run_once(available_as_of=NOW)
    source.batch = Source("FX_USD_JPY", "IG_LIVE").batch
    value.run_once(available_as_of=NOW + timedelta(minutes=10))
    with repo._connect() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM platform_pending_evaluations WHERE scope=? AND close_time=?",
            ("IG_LIVE|USD_JPY", NOW.isoformat()),
        ).fetchone()
    assert row[0] > 0


def test_pending_evaluation_survives_checkpoint_and_restart(tmp_path):
    value, repo = runtime(tmp_path, Source("FX_USD_JPY", "IG_LIVE", broken=True))
    child = value._children["EUR_USD"]
    original = child._evaluate
    child._evaluate = lambda *a, **kw: ((), 0, 0, None, 0, "missing required input")
    value.run_once(available_as_of=NOW)
    projection = InstrumentProjection(repo, "IG_DEMO", "EUR_USD")
    assert projection.pending()
    assert projection.platform_integration_state()["watermark_canonical_bar_id"] > 0
    child._evaluate = original
    value.run_once(available_as_of=NOW)
    assert (
        any(row["reason"] != "missing required input" for row in projection.pending())
        or not projection.pending()
    )


def test_scoped_checkpoint_never_borrows_other_database_cursor(tmp_path):
    repo = SQLiteProjectionRepository(tmp_path / "state.sqlite3")
    repo.initialize()
    demo = InstrumentProjection(repo, "IG_DEMO", "EUR_USD")
    live = InstrumentProjection(repo, "IG_LIVE", "USD_JPY")
    demo.advance_platform_watermark(watermark_canonical_bar_id=100000, updated_at=NOW)
    assert live.platform_integration_state() is None
    live.advance_platform_watermark(watermark_canonical_bar_id=10, updated_at=NOW)
    candidate = ("USD_JPY", FilterMode.MICRO, Timeframe.H1, NOW)
    live.enqueue((candidate,))
    assert InstrumentProjection(repo, "IG_LIVE", "USD_JPY").pending()
