"""Opt-in real PostgreSQL validation for Phase 22A-4 authority startup/resume."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.market_data.platform_authority import PlatformAuthorityRuntime
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService

DATABASE_URL = os.environ.get("MARKET_DATA_PLATFORM_DATABASE_URL")
INSTRUMENTS = tuple(
    item.strip()
    for item in os.environ.get(
        "SPECT8_PLATFORM_AUTHORITY_INSTRUMENTS", "EUR_USD"
    ).split(",")
    if item.strip()
)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="MARKET_DATA_PLATFORM_DATABASE_URL is required for real authority validation",
)


def test_real_platform_authority_startup_and_watermark_resume(tmp_path: Path) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "platform-authority.sqlite3")
    repository.initialize()
    runtime = PlatformAuthorityRuntime.from_database_url(
        DATABASE_URL or "",
        repository,
        WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository),
        INSTRUMENTS,
        stale_after_seconds=7200,
        poll_seconds=300,
    )
    as_of = datetime.now(timezone.utc)
    try:
        first = runtime.run_once(available_as_of=as_of)
        second = runtime.run_once(available_as_of=as_of)
    finally:
        runtime.close()

    assert first.connection_state == "HEALTHY"
    assert first.freshness_state == "HEALTHY"
    assert first.bootstrapped is True
    assert first.evaluations_created == 4 * len(INSTRUMENTS)
    assert second.bootstrapped is False
    assert second.previous_watermark == first.watermark_canonical_bar_id
    assert second.watermark_canonical_bar_id == first.watermark_canonical_bar_id
    assert second.evaluations_created == 0
    assert repository.platform_integration_state() is not None
