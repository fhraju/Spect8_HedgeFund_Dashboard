from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ..domain import Bar, Timeframe
from ..repository import SQLiteProjectionRepository
from .daily_rebuild_cli import backup_sqlite
from .normalizer import CandleNormalizer
from .twelve_data_provider import TwelveDataProvider


def _aware(value: str, option: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{option} must include a UTC offset")
    return parsed


def _credential(env_file: Path) -> str:
    if not env_file.is_file():
        raise ValueError(f"environment file does not exist: {env_file}")
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "TWELVE_DATA_API_KEY" and value.strip():
            return value.strip()
    raise ValueError("TWELVE_DATA_API_KEY is unavailable")


def _normalized_history(
    provider: TwelveDataProvider,
    start: datetime,
    end: datetime,
) -> tuple[tuple[Bar, ...], dict[str, int]]:
    instrument = provider.discover_instruments()[0]
    normalizer = CandleNormalizer()
    accepted: list[Bar] = []
    counts: dict[str, int] = {}
    for timeframe in (Timeframe.H1, Timeframe.H4):
        raw = provider.fetch_historical_bars(timeframe, start, end)
        normalized: list[Bar] = []
        issues: list[str] = []
        for candle in raw:
            result = normalizer.normalize(candle, instrument)
            if result.candle is not None:
                normalized.append(result.candle)
            issues.extend(result.issues)
        if issues:
            raise ValueError(
                f"{timeframe.value} normalization rejected source history: "
                + ",".join(sorted(set(issues)))
            )
        counts[timeframe.value] = len(normalized)
        accepted.extend(normalized)
    return tuple(accepted), counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill canonical Twelve Data H1/H4 history safely."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--env-file", type=Path, default=Path("backend/.env"))
    parser.add_argument("--confirm-active-database", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    database = args.database.resolve()
    if not database.is_file():
        parser.error(f"database does not exist: {database}")
    safe_tokens = ("dev", "test", "validation", "replay")
    safe_filename = any(token in database.name.lower() for token in safe_tokens)
    confirmed_active = (
        args.confirm_active_database is not None
        and args.confirm_active_database.resolve() == database
    )
    if args.apply and not safe_filename and not confirmed_active:
        parser.error(
            "refusing active database apply without an exact "
            "--confirm-active-database path"
        )
    try:
        start = _aware(args.start, "--start")
        end = _aware(args.end, "--end")
        if start >= end:
            raise ValueError("--start must precede --end")
        api_key = _credential(args.env_file.resolve())
        bars, counts = _normalized_history(
            TwelveDataProvider(api_key=api_key), start, end
        )
    except ValueError as error:
        parser.error(str(error))

    backup = None
    inserted = 0
    if args.apply:
        backup = backup_sqlite(database)
        repository = SQLiteProjectionRepository(database)
        repository.initialize()
        inserted = repository.persist_canonical_bars(bars)
    print(
        json.dumps(
            {
                "database": str(database),
                "dry_run": args.dry_run,
                "requested_start": start.isoformat(),
                "requested_end": end.isoformat(),
                "accepted": counts,
                "inserted": inserted,
                "backup_path": str(backup) if backup is not None else None,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
