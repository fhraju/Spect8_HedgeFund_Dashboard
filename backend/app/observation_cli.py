from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .repository import SQLiteProjectionRepository

PROVIDER_ID = "TWELVE_DATA"
INSTRUMENT_ID = "EUR/USD"


def build_report(database_path: Path) -> dict[str, Any]:
    repository = SQLiteProjectionRepository(database_path)
    repository.initialize()
    return {
        "runtime": repository.observation_report(
            PROVIDER_ID, INSTRUMENT_ID
        ),
        "provider_health": repository.provider_health(PROVIDER_ID),
        "provider_sync": repository.provider_sync(PROVIDER_ID),
        "read_only": {"orders": 0, "fills": 0},
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect sanitized Spect8 Phase 3B observation data."
    )
    parser.add_argument("command", choices=("status", "report"))
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(
            os.environ.get(
                "SPECT8_DATABASE_PATH",
                Path(__file__).resolve().parents[2]
                / "var"
                / "spect8_phase1.sqlite3",
            )
        ),
    )
    arguments = parser.parse_args()
    report = build_report(arguments.database)
    output = (
        {
            "runtime": {
                key: report["runtime"][key]
                for key in (
                    "generated_at",
                    "observation_start_utc",
                    "observation_end_utc",
                    "runtime_uptime_seconds",
                    "runtime_sessions",
                    "restarts",
                    "polls",
                    "request_metrics",
                    "latest_completed_candles",
                    "orders",
                    "fills",
                )
            },
            "provider_health": report["provider_health"],
            "provider_sync": report["provider_sync"],
        }
        if arguments.command == "status"
        else report
    )
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
