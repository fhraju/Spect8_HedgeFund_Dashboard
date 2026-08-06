from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..market_data.registry import CanonicalInstrumentRegistry, twelve_data_instruments
from .reproducibility import capture_checkpoint


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture an offline deterministic Spect8 reproducibility checkpoint."
    )
    parser.add_argument("--name", required=True)
    parser.add_argument("--evaluation-time", required=True)
    args = parser.parse_args()
    settings = Settings.from_environment()
    evaluation_time = datetime.fromisoformat(args.evaluation_time.replace("Z", "+00:00"))
    manifest = capture_checkpoint(
        repository_root=settings.repository_root,
        database_path=settings.database_path,
        registry=CanonicalInstrumentRegistry(twelve_data_instruments()),
        name=args.name,
        evaluation_time=evaluation_time,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    os.environ.pop("TWELVE_DATA_API_KEY", None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
