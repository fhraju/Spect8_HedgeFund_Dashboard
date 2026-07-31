from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .domain import Timeframe


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
    market_data_provider: str = "replay"
    instrument: str = "EUR/USD"
    timeframes: tuple[Timeframe, ...] = (
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    )
    twelve_data_api_key: str | None = field(default=None, repr=False)

    def validate(self) -> None:
        provider = self.market_data_provider.lower()
        if provider not in {"replay", "twelve_data"}:
            raise ValueError(
                "SPECT8_MARKET_DATA_PROVIDER must be replay or twelve_data."
            )
        if provider == "twelve_data":
            if self.instrument != "EUR/USD":
                raise ValueError("Phase 2C supports only EUR/USD.")
            if set(self.timeframes) != {
                Timeframe.H1,
                Timeframe.H4,
                Timeframe.D1,
            }:
                raise ValueError("Phase 2C requires exactly H1, H4 and D1.")
            if not self.twelve_data_api_key:
                raise ValueError(
                    "TWELVE_DATA_API_KEY is required for twelve_data provider."
                )

    @classmethod
    def from_environment(cls) -> "Settings":
        repository_root = Path(__file__).resolve().parents[2]
        database_path = Path(
            os.environ.get(
                "SPECT8_DATABASE_PATH",
                repository_root / "var" / "spect8_phase1.sqlite3",
            )
        )
        raw_timeframes = os.environ.get(
            "SPECT8_TIMEFRAMES", "H1,H4,D1"
        )
        try:
            timeframes = tuple(
                Timeframe(value.strip())
                for value in raw_timeframes.split(",")
                if value.strip()
            )
        except ValueError as error:
            raise ValueError(
                "SPECT8_TIMEFRAMES must contain only H1,H4,D1."
            ) from error
        configured = cls(
            repository_root=repository_root,
            database_path=database_path,
            internal_api_key=os.environ.get(
                "SPECT8_INTERNAL_API_KEY", "local-development-only"
            ),
            auto_seed_synthetic=os.environ.get(
                "SPECT8_AUTO_SEED_SYNTHETIC", "true"
            ).lower()
            == "true",
            market_data_provider=os.environ.get(
                "SPECT8_MARKET_DATA_PROVIDER", "replay"
            ).lower(),
            instrument=os.environ.get("SPECT8_INSTRUMENT", "EUR/USD"),
            timeframes=timeframes,
            twelve_data_api_key=os.environ.get("TWELVE_DATA_API_KEY"),
        )
        configured.validate()
        return configured
