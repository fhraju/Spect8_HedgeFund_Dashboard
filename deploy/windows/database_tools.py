from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path


REQUIRED_TABLES = (
    "canonical_bars",
    "canonical_quality_issues",
    "daily_filter_snapshots",
    "event_history",
    "instrument_health",
    "instrument_status",
    "processed_bars",
    "provider_credit_ledger",
    "provider_health",
    "provider_sync",
    "runtime_poll_history",
    "runtime_sessions",
)

EXPECTED_NON_EMPTY_TABLES = (
    "canonical_bars",
    "daily_filter_snapshots",
    "event_history",
    "instrument_status",
    "processed_bars",
)


def read_only_connection(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def integrity_check(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    present = table_names(connection)
    return {
        table: int(
            connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        )
        for table in REQUIRED_TABLES
        if table in present
    }


def latest_timestamps(connection: sqlite3.Connection) -> dict[str, str | None]:
    return {
        "latest_canonical_bar_close": connection.execute(
            "SELECT MAX(close_time_utc) FROM canonical_bars"
        ).fetchone()[0],
        "latest_evaluation_close": connection.execute(
            "SELECT MAX(json_extract(status_json, '$.signal_bar_close_time')) "
            "FROM instrument_status"
        ).fetchone()[0],
        "latest_evaluation_update": connection.execute(
            "SELECT MAX(updated_at) FROM instrument_status"
        ).fetchone()[0],
    }


def validate_schema_and_data(
    connection: sqlite3.Connection,
    *,
    require_non_empty: bool,
) -> tuple[dict[str, int], dict[str, str | None]]:
    integrity = integrity_check(connection)
    if integrity != ("ok",):
        raise RuntimeError(f"SQLite integrity_check failed: {integrity}")
    present = table_names(connection)
    missing = sorted(set(REQUIRED_TABLES).difference(present))
    if missing:
        raise RuntimeError(f"Required tables are missing: {', '.join(missing)}")
    counts = row_counts(connection)
    if require_non_empty:
        empty = [table for table in EXPECTED_NON_EMPTY_TABLES if counts[table] == 0]
        if empty:
            raise RuntimeError(
                f"Expected copied-data tables are empty: {', '.join(empty)}"
            )
    return counts, latest_timestamps(connection)


def assert_account_file_access(path: Path) -> None:
    if not os.access(path, os.R_OK):
        raise PermissionError(f"Database is not readable by this account: {path}")
    if not os.access(path, os.W_OK) or not path.stat().st_mode & stat.S_IWRITE:
        raise PermissionError(f"Database is not writable by this account: {path}")
    if not os.access(path.parent, os.W_OK):
        raise PermissionError(
            "Database directory is not writable; SQLite cannot maintain WAL files: "
            f"{path.parent}"
        )


def print_report(
    *,
    database: Path,
    counts: dict[str, int],
    timestamps: dict[str, str | None],
) -> None:
    print(f"Database: {database.resolve()}")
    print("Integrity check: ok")
    print("Row counts:")
    for table in REQUIRED_TABLES:
        print(f"  {table}: {counts.get(table, 0)}")
    print("Latest timestamps:")
    for name, value in timestamps.items():
        print(f"  {name}: {value or 'none'}")
