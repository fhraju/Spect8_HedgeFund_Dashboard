from __future__ import annotations

import argparse
import sys
from pathlib import Path

from database_tools import (
    assert_account_file_access,
    print_report,
    read_only_connection,
    validate_schema_and_data,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a copied Spect8 database without migrations or network access."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(r"C:\Spect8\data\spect8.db"),
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Permit empty strategy-data tables for an explicitly initialized database.",
    )
    return parser.parse_args()


def validate_import(database: Path, *, require_non_empty: bool = True) -> None:
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Imported database does not exist: {database}")
    assert_account_file_access(database)
    with read_only_connection(database) as connection:
        counts, timestamps = validate_schema_and_data(
            connection,
            require_non_empty=require_non_empty,
        )
    print_report(database=database, counts=counts, timestamps=timestamps)
    print("Schema migration: not run")
    print("Provider/network calls: none")


def main() -> int:
    args = parse_args()
    try:
        validate_import(args.database, require_non_empty=not args.allow_empty)
    except Exception as error:
        print(f"Imported database validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
