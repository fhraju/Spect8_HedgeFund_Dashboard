from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from .filter_audit_rebuild import FilterAuditRebuildService
from .market_data.daily_rebuild_cli import backup_sqlite
from .market_data.twelve_data_provider import TwelveDataProvider
from .repository import SQLiteProjectionRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild latest H1/H4 filter audit from canonical SQLite bars."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--confirm-active-database",
        type=Path,
        help="For --apply, repeat the exact active database path.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    database_path = args.database.resolve()
    if not database_path.is_file():
        parser.error(f"database does not exist: {database_path}")
    if args.apply and (
        args.confirm_active_database is None
        or args.confirm_active_database.resolve() != database_path
    ):
        parser.error("refusing apply without the exact --confirm-active-database path")

    repository = SQLiteProjectionRepository(database_path)
    repository.initialize()
    instrument = (
        TwelveDataProvider(api_key="offline-filter-audit-rebuild")
        .discover_instruments()[0]
        .to_strategy_metadata()
    )
    service = FilterAuditRebuildService(repository)
    report = service.rebuild_latest(instrument=instrument, dry_run=True)
    backup = None
    if args.apply and report.changed:
        backup = backup_sqlite(database_path)
        report = service.rebuild_latest(instrument=instrument, dry_run=False)
    elif args.apply:
        report = replace(report, dry_run=False)
    payload = asdict(report)
    payload["backup_path"] = str(backup) if backup is not None else None
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
