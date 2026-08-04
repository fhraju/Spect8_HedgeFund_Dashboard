from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from ..repository import SQLiteProjectionRepository
from .daily_rebuild import DailyRebuildService
from .twelve_data_provider import TwelveDataProvider


def backup_sqlite(database_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = database_path.with_name(
        f"{database_path.stem}.backup.{stamp}{database_path.suffix}"
    )
    with closing(sqlite3.connect(database_path)) as source:
        with closing(sqlite3.connect(backup_path)) as target:
            source.backup(target)
    return backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild New York-close D1 bars in a development database."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument(
        "--confirm-active-database",
        type=Path,
        help=(
            "For --apply to a non-development filename, repeat the exact "
            "database path as an explicit active-database confirmation."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    database_path = args.database.resolve()
    safe_tokens = ("dev", "test", "validation", "replay")
    safe_filename = any(
        token in database_path.name.lower() for token in safe_tokens
    )
    confirmed_active = (
        args.confirm_active_database is not None
        and args.confirm_active_database.resolve() == database_path
    )
    if args.apply and not safe_filename and not confirmed_active:
        parser.error(
            "refusing active database apply without an exact "
            "--confirm-active-database path"
        )
    if not database_path.is_file():
        parser.error(f"database does not exist: {database_path}")
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    if as_of.tzinfo is None:
        parser.error("--as-of must include a UTC offset")

    repository = SQLiteProjectionRepository(database_path)
    repository.initialize()
    instrument = TwelveDataProvider(
        api_key="offline-rebuild-no-network"
    ).discover_instruments()[0].to_strategy_metadata()
    report = DailyRebuildService(repository).rebuild(
        instrument=instrument,
        as_of=as_of,
        dry_run=True,
    )
    backup = None
    if args.apply and report.changed:
        # Re-run only after the point-in-time backup. The dry calculation above
        # validates every aggregate and dependent projection before mutation.
        backup = backup_sqlite(database_path)
        report = DailyRebuildService(repository).rebuild(
            instrument=instrument,
            as_of=as_of,
            dry_run=False,
        )
    elif args.apply:
        report = replace(report, dry_run=False)
    payload = asdict(report)
    payload["backup_path"] = str(backup) if backup is not None else None
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
