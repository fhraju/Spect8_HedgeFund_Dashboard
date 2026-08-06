from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from database_tools import (
    integrity_check,
    latest_timestamps,
    print_report,
    read_only_connection,
    row_counts,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def configured_source() -> Path:
    value = os.environ.get("SPECT8_DATABASE_PATH")
    if value:
        path = Path(value)
        return path if path.is_absolute() else REPOSITORY_ROOT / path
    return REPOSITORY_ROOT / "var" / "spect8_phase1.sqlite3"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a consistent, integrity-checked Spect8 SQLite backup."
    )
    parser.add_argument("--source", type=Path, default=configured_source())
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "var" / "database-exports",
    )
    return parser.parse_args()


def export_database(source: Path, output_dir: Path) -> Path:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source}")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = output_dir / f"spect8-{stamp}.sqlite3"

    try:
        with read_only_connection(source) as source_connection:
            source_integrity = integrity_check(source_connection)
            if source_integrity != ("ok",):
                raise RuntimeError(
                    f"Source integrity_check failed: {source_integrity}"
                )
            counts = row_counts(source_connection)
            timestamps = latest_timestamps(source_connection)
            with sqlite3.connect(destination) as backup_connection:
                source_connection.backup(backup_connection)

        with read_only_connection(destination) as backup_connection:
            backup_integrity = integrity_check(backup_connection)
            if backup_integrity != ("ok",):
                raise RuntimeError(
                    f"Backup integrity_check failed: {backup_integrity}"
                )
        print_report(database=source, counts=counts, timestamps=timestamps)
        print(f"Backup: {destination}")
        print("Backup integrity check: ok")
        return destination
    except Exception:
        if destination.exists():
            destination.unlink()
        raise


def main() -> int:
    args = parse_args()
    try:
        export_database(args.source, args.output_dir)
    except Exception as error:
        print(f"Database export failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
