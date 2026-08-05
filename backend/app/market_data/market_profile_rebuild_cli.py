from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from ..repository import SQLiteProjectionRepository
from .daily_rebuild_cli import backup_sqlite
from .market_profile_rebuild import MarketProfileRebuildService
from .twelve_data_provider import TwelveDataProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply IC Markets NY-close Forex profile to a development SQLite database."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--confirm-active-database", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    database = args.database.resolve()
    if not database.is_file():
        parser.error(f"database does not exist: {database}")
    safe = any(
        token in database.name.lower()
        for token in ("dev", "test", "validation", "replay")
    )
    confirmed = (
        args.confirm_active_database
        and args.confirm_active_database.resolve() == database
    )
    if args.apply and not safe and not confirmed:
        parser.error("refusing apply without exact --confirm-active-database path")
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    if as_of.tzinfo is None:
        parser.error("--as-of must include a UTC offset")
    repository = SQLiteProjectionRepository(database)
    repository.initialize()
    instrument = (
        TwelveDataProvider(api_key="offline-rebuild-no-network")
        .discover_instruments()[0]
        .to_strategy_metadata()
    )
    service = MarketProfileRebuildService(repository)
    report = service.rebuild(instrument=instrument, as_of=as_of, dry_run=True)
    backup = None
    if args.apply and report.changed:
        backup = backup_sqlite(database)
        report = service.rebuild(instrument=instrument, as_of=as_of, dry_run=False)
    elif args.apply:
        report = replace(report, dry_run=False)
    payload = asdict(report)
    payload["backup_path"] = str(backup) if backup else None
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
