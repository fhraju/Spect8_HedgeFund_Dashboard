from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.repository import SQLiteProjectionRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explicitly initialize a new Spect8 production database."
    )
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_absolute():
        print("Database path must be absolute.", file=sys.stderr)
        return 1
    database = args.database.resolve()
    if database.exists():
        print(f"Refusing to initialize an existing database: {database}", file=sys.stderr)
        return 1
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        SQLiteProjectionRepository(database).initialize()
    except Exception as error:
        print(f"Database initialization failed: {error}", file=sys.stderr)
        return 1
    print(f"Initialized empty Spect8 database: {database}")
    print("Provider/network calls: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
