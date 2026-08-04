"""Generate SHA-256 checksums for frozen authority, inputs, and expectations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "golden"


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def main() -> int:
    manifest_path = GOLDEN / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = [
        ROOT / "Spect8_Micro_Daily_v1_0_FROZEN.md",
        ROOT / "Spect8_Micro_Daily_v1_0_1_FROZEN.md",
        ROOT / "Spect8_Micro_Daily_v1_0_2_FROZEN.md",
        ROOT / "Spect8_Micro_Daily_v1_0_3_FROZEN.md",
        manifest_path,
    ]
    for case in manifest["cases"]:
        directory = GOLDEN / case["path"]
        paths.extend(
            directory / name
            for name in (
                "signal_bars.csv",
                "daily_bars.csv",
                "instrument.json",
                "expected.json",
            )
        )
    relative_paths = sorted(path.relative_to(ROOT) for path in paths)
    output = "\n".join(
        f"{digest(ROOT / path)}  {path.as_posix()}" for path in relative_paths
    )
    (GOLDEN / "CHECKSUMS.sha256").write_text(output + "\n", encoding="utf-8")
    print(f"Wrote {len(relative_paths)} SHA-256 checksums.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
