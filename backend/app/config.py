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
    market_data_runtime_enabled: bool = False
    market_data_poll_seconds: int = 300
    market_data_safety_delay_seconds: int = 30
    runtime_log_path: Path | None = None
    runtime_log_max_bytes: int = 5_000_000
    runtime_log_backup_count: int = 5

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
        if not 60 <= self.market_data_poll_seconds <= 900:
            raise ValueError(
                "SPECT8_MARKET_DATA_POLL_SECONDS must be between 60 and 900."
            )
        if not 5 <= self.market_data_safety_delay_seconds <= 300:
            raise ValueError(
                "SPECT8_MARKET_DATA_SAFETY_DELAY_SECONDS must be "
                "between 5 and 300."
            )
        if not 65_536 <= self.runtime_log_max_bytes <= 100_000_000:
            raise ValueError("SPECT8_RUNTIME_LOG_MAX_BYTES is outside bounds.")
        if not 1 <= self.runtime_log_backup_count <= 10:
            raise ValueError(
                "SPECT8_RUNTIME_LOG_BACKUP_COUNT must be between 1 and 10."
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
            market_data_runtime_enabled=os.environ.get(
                "SPECT8_MARKET_DATA_RUNTIME_ENABLED", "true"
            ).lower()
            == "true",
            market_data_poll_seconds=int(
                os.environ.get("SPECT8_MARKET_DATA_POLL_SECONDS", "300")
            ),
            market_data_safety_delay_seconds=int(
                os.environ.get(
                    "SPECT8_MARKET_DATA_SAFETY_DELAY_SECONDS", "30"
                )
            ),
            runtime_log_path=Path(
                os.environ.get(
                    "SPECT8_RUNTIME_LOG_PATH",
                    repository_root / "var" / "spect8_runtime.log",
                )
            ),
            runtime_log_max_bytes=int(
                os.environ.get("SPECT8_RUNTIME_LOG_MAX_BYTES", "5000000")
            ),
            runtime_log_backup_count=int(
                os.environ.get("SPECT8_RUNTIME_LOG_BACKUP_COUNT", "5")
            ),
        )
        configured.validate()
        return configured
