from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.market_data.isolated_platform import InstrumentProjection
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService
from backend.app.synthetic_inputs import SyntheticCaseInputLoader
from backend.tests.test_current_daily_filter_v2 import snapshot


def test_repaired_filter_replaces_current_and_preserves_prior_evidence(tmp_path):
    repo = SQLiteProjectionRepository(tmp_path / "projection.sqlite3")
    repo.initialize()
    projection = InstrumentProjection(repo, "TEST", "EUR/USD")
    original = snapshot()
    repaired = snapshot(low="1.0900")
    assert projection.persist_daily_filter_snapshot(original)
    assert projection.persist_daily_filter_snapshot(repaired)
    assert not projection.persist_daily_filter_snapshot(repaired)
    with closing(repo._connect()) as db:
        assert (
            db.execute("SELECT snapshot_id FROM daily_filter_snapshots").fetchone()[0]
            == repaired.snapshot_id
        )
        assert (
            db.execute("SELECT identity FROM platform_projection_revisions").fetchone()[
                0
            ]
            == original.snapshot_id
        )
    with pytest.raises(ValueError, match="without a new source identity"):
        projection.persist_daily_filter_snapshot(
            replace(repaired, created_at=repaired.created_at + timedelta(seconds=1))
        )


def test_recalculation_updates_latest_without_duplicate_events_or_time_regression(
    tmp_path,
):
    repo = SQLiteProjectionRepository(tmp_path / "projection.sqlite3")
    repo.initialize()
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repo)
    request = SyntheticCaseInputLoader(Path(__file__).resolve().parents[2]).load(
        "confirmed_buy_h1_01"
    )
    evaluated = service.evaluate_request(request)
    projection = InstrumentProjection(repo, "TEST", evaluated.status.instrument_id)
    events = service.events_for_projection(evaluated)
    assert projection.persist_projection(evaluated.status, events)
    repaired = replace(evaluated.status, reason_codes=("RECOVERED_INPUTS",))
    assert not projection.persist_projection(repaired, events)
    assert repo.event_count() == len(events)
    assert repo.statuses()[0]["reason_codes"] == ["RECOVERED_INPUTS"]
    older = replace(
        evaluated.status,
        idempotency_key="older-candle",
        last_update=evaluated.status.last_update - timedelta(hours=1),
    )
    assert projection.persist_projection(older, ())
    assert repo.statuses()[0]["reason_codes"] == ["RECOVERED_INPUTS"]
    assert repo.event_count() == len(events)
