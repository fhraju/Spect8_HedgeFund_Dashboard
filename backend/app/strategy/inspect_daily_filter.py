from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ..engine.models import CURRENT_D1_FILTER_V2
from ..repository import SQLiteProjectionRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a persisted Current Daily Filter V2 snapshot"
    )
    parser.add_argument("--instrument", default="EUR/USD")
    parser.add_argument("--provider", default="TWELVE_DATA")
    parser.add_argument("--as-of")
    parser.add_argument(
        "--database", type=Path, default=Path("var/spect8_phase1.sqlite3")
    )
    args = parser.parse_args(argv)
    repository = SQLiteProjectionRepository(args.database.resolve())
    as_of = (
        datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
        .isoformat()
        .replace("+00:00", "Z")
        if args.as_of
        else None
    )
    value = (
        repository.daily_filter_snapshot_at(
            args.provider, args.instrument, CURRENT_D1_FILTER_V2, as_of
        )
        if as_of
        else repository.latest_daily_filter_snapshot(
            args.provider, args.instrument, CURRENT_D1_FILTER_V2
        )
    )
    if value is None:
        parser.error("no Current Daily Filter V2 snapshot is persisted")
    partial = value["current_partial_d1"]
    h1 = repository.canonical_bar_objects(args.provider, args.instrument, "H1")
    source_bars = [
        {
            "open_time_utc": bar.open_time.isoformat().replace("+00:00", "Z"),
            "close_time_utc": bar.close_time.isoformat().replace("+00:00", "Z"),
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
        }
        for bar in h1
        if partial["session_open_utc"]
        <= bar.open_time.isoformat().replace("+00:00", "Z")
        and bar.close_time.isoformat().replace("+00:00", "Z")
        <= value["as_of_h1_close_time_utc"]
    ]
    result = {
        "snapshot": value,
        "current_session_h1_bars": source_bars,
        "evaluation_references": repository.daily_filter_evaluation_references(
            value["snapshot_id"]
        ),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
