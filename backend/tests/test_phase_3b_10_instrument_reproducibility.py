from __future__ import annotations

import json
from pathlib import Path

from backend.app.tools.reproducibility import (
    CHECKPOINT_INSTRUMENT_IDS,
    reproduce_checkpoint,
    validate_checksums,
)


FIXTURE_ROOT = (
    Path(__file__).parent
    / "fixtures"
    / "reproducibility"
    / "phase_3b_10_instruments"
)


def test_phase_3b_ten_instrument_checkpoint_reproduces_offline(tmp_path: Path) -> None:
    validate_checksums(FIXTURE_ROOT)
    result = reproduce_checkpoint(
        fixture_root=FIXTURE_ROOT,
        database_path=tmp_path / "checkpoint.sqlite3",
    )
    assert result == {
        "checkpoint_name": "phase_3b_10_instruments",
        "instrument_count": 10,
        "evaluation_count": 20,
        "event_count": 123,
        "checksums": "VALID",
        "network_calls": 0,
        "canonical_utc_evaluation_timestamp": "2026-08-05T14:00:00Z",
        "h4_evaluation_timestamp": "2026-08-05T13:00:00Z",
    }


def test_checkpoint_registry_and_manifest_are_frozen() -> None:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    registry = json.loads(
        (FIXTURE_ROOT / "instrument_registry.json").read_text(encoding="utf-8")
    )
    assert tuple(manifest["canonical_instrument_ids"]) == CHECKPOINT_INSTRUMENT_IDS
    assert tuple(item["instrument_id"] for item in registry) == CHECKPOINT_INSTRUMENT_IDS
    assert all(item["enabled"] for item in registry)
    assert manifest["strategy_version"] == "MICRO_DAILY_FILTER_CURRENT_D1_V2"
    assert manifest["canonical_utc_evaluation_timestamp"] == "2026-08-05T14:00:00Z"
