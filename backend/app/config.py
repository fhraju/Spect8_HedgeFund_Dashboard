from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    repository_root: Path
    database_path: Path
    internal_api_key: str
    selected_cases: tuple[str, ...] = (
        "confirmed_buy_h1_01",
        "confirmed_sell_h4_01",
    )
    auto_seed_synthetic: bool = True

    @classmethod
    def from_environment(cls) -> "Settings":
        repository_root = Path(__file__).resolve().parents[2]
        database_path = Path(
            os.environ.get(
                "SPECT8_DATABASE_PATH",
                repository_root / "var" / "spect8_phase1.sqlite3",
            )
        )
        return cls(
            repository_root=repository_root,
            database_path=database_path,
            internal_api_key=os.environ.get(
                "SPECT8_INTERNAL_API_KEY", "local-development-only"
            ),
            auto_seed_synthetic=os.environ.get(
                "SPECT8_AUTO_SEED_SYNTHETIC", "true"
            ).lower()
            == "true",
        )
