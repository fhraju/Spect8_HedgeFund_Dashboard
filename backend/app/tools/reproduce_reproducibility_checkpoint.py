from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .reproducibility import CHECKPOINT_NAME, reproduce_checkpoint


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproduce a frozen Spect8 checkpoint without network access."
    )
    parser.add_argument("--name", default=CHECKPOINT_NAME)
    parser.add_argument("--database-path", type=Path)
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    fixture_root = (
        repository_root
        / "backend"
        / "tests"
        / "fixtures"
        / "reproducibility"
        / args.name
    )
    if args.database_path is not None:
        result = reproduce_checkpoint(
            fixture_root=fixture_root, database_path=args.database_path
        )
    else:
        with tempfile.TemporaryDirectory(prefix="spect8-repro-") as directory:
            result = reproduce_checkpoint(
                fixture_root=fixture_root,
                database_path=Path(directory) / "checkpoint.sqlite3",
            )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
