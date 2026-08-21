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
    market_data_platform_shadow_enabled: bool = False
    market_data_platform_database_url: str | None = field(default=None, repr=False)
    market_data_runtime_enabled: bool = False
    market_scan_enabled: bool = False
    market_scan_after_hour_seconds: int = 60
    market_data_request_min_interval_seconds: float = 8.0
    market_data_max_requests_per_minute: int = 8
    twelve_data_daily_credit_limit: int = 800
    market_data_daily_operational_budget: int = 700
    market_data_credit_reserve: int = 100
    market_data_max_retries_per_instrument: int = 2
    market_data_stale_after_seconds: int = 7200
    enabled_instrument_ids: tuple[str, ...] | None = None
    market_data_poll_seconds: int = 300
    market_data_safety_delay_seconds: int = 30
    runtime_log_path: Path | None = None
    runtime_log_max_bytes: int = 5_000_000
    runtime_log_backup_count: int = 5
    historical_replay_database_path: Path | None = None
    application_environment: str = "development"
    polling_enabled: bool | None = None
    startup_backfill_enabled: bool = True
    provider_discovery_enabled: bool = True

    @property
    def is_production(self) -> bool:
        return self.application_environment == "production"

    @property
    def effective_polling_enabled(self) -> bool:
        if self.polling_enabled is not None:
            return self.polling_enabled
        return self.market_scan_enabled or self.market_data_runtime_enabled

    def validate(self) -> None:
        if self.application_environment not in {
            "development",
            "test",
            "production",
        }:
            raise ValueError(
                "SPECT8_APPLICATION_ENVIRONMENT must be development, test, or production."
            )
        if self.is_production:
            if not self.database_path.is_absolute():
                raise ValueError(
                    "SPECT8_DATABASE_PATH must be absolute in production."
                )
            try:
                self.database_path.resolve().relative_to(
                    self.repository_root.resolve()
                )
            except ValueError:
                pass
            else:
                raise ValueError(
                    "SPECT8_DATABASE_PATH must be outside the Git repository "
                    "in production."
                )
        provider = self.market_data_provider.lower()
        if provider not in {"replay", "twelve_data"}:
            raise ValueError(
                "SPECT8_MARKET_DATA_PROVIDER must be replay or twelve_data."
            )
        if provider == "twelve_data":
            if not self.twelve_data_api_key:
                raise ValueError(
                    "TWELVE_DATA_API_KEY is required for twelve_data provider."
                )
        if self.market_data_platform_shadow_enabled:
            if not self.market_data_platform_database_url:
                raise ValueError(
                    "MARKET_DATA_PLATFORM_DATABASE_URL is required when the "
                    "Platform shadow adapter is enabled."
                )
            if not self.market_data_platform_database_url.startswith(
                "postgresql+psycopg://"
            ):
                raise ValueError(
                    "MARKET_DATA_PLATFORM_DATABASE_URL must use postgresql+psycopg."
                )
        if not 60 <= self.market_data_poll_seconds <= 900:
            raise ValueError(
                "SPECT8_MARKET_DATA_POLL_SECONDS must be between 60 and 900."
            )
        if not 5 <= self.market_data_safety_delay_seconds <= 300:
            raise ValueError(
                "SPECT8_MARKET_DATA_SAFETY_DELAY_SECONDS must be " "between 5 and 300."
            )
        if not 5 <= self.market_scan_after_hour_seconds <= 300:
            raise ValueError(
                "SPECT8_MARKET_SCAN_AFTER_HOUR_SECONDS must be between 5 and 300."
            )
        if self.market_data_request_min_interval_seconds < 8:
            raise ValueError(
                "SPECT8_MARKET_DATA_REQUEST_MIN_INTERVAL_SECONDS must be at least 8."
            )
        if not 1 <= self.market_data_max_requests_per_minute <= 8:
            raise ValueError(
                "SPECT8_MARKET_DATA_MAX_REQUESTS_PER_MINUTE must be between 1 and 8."
            )
        if self.twelve_data_daily_credit_limit <= 0:
            raise ValueError("TWELVE_DATA_DAILY_CREDIT_LIMIT must be positive.")
        if self.market_data_daily_operational_budget <= 0:
            raise ValueError("MARKET_DATA_DAILY_OPERATIONAL_BUDGET must be positive.")
        if self.market_data_credit_reserve < 0:
            raise ValueError("MARKET_DATA_CREDIT_RESERVE cannot be negative.")
        if (
            self.market_data_daily_operational_budget
            + self.market_data_credit_reserve
            > self.twelve_data_daily_credit_limit
        ):
            raise ValueError(
                "Operational budget plus reserve exceeds Twelve Data daily limit."
            )
        if not 0 <= self.market_data_max_retries_per_instrument <= 2:
            raise ValueError(
                "SPECT8_MARKET_DATA_MAX_RETRIES_PER_INSTRUMENT must be between 0 and 2."
            )
        if self.market_data_stale_after_seconds < 3600:
            raise ValueError(
                "SPECT8_MARKET_DATA_STALE_AFTER_SECONDS must be at least 3600."
            )
        if not 65_536 <= self.runtime_log_max_bytes <= 100_000_000:
            raise ValueError("SPECT8_RUNTIME_LOG_MAX_BYTES is outside bounds.")
        if not 1 <= self.runtime_log_backup_count <= 10:
            raise ValueError(
                "SPECT8_RUNTIME_LOG_BACKUP_COUNT must be between 1 and 10."
            )
        if (
            self.historical_replay_database_path is not None
            and self.historical_replay_database_path.resolve()
            == self.database_path.resolve()
        ):
            raise ValueError(
                "Historical replay database must be separate from live state."
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
        raw_timeframes = os.environ.get("SPECT8_TIMEFRAMES", "H1,H4,D1")
        try:
            timeframes = tuple(
                Timeframe(value.strip())
                for value in raw_timeframes.split(",")
                if value.strip()
            )
        except ValueError as error:
            raise ValueError("SPECT8_TIMEFRAMES must contain only H1,H4,D1.") from error
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
            market_data_platform_shadow_enabled=os.environ.get(
                "SPECT8_MARKET_DATA_PLATFORM_SHADOW_ENABLED", "false"
            ).lower()
            == "true",
            market_data_platform_database_url=os.environ.get(
                "MARKET_DATA_PLATFORM_DATABASE_URL"
            ),
            market_data_runtime_enabled=os.environ.get(
                "SPECT8_MARKET_DATA_RUNTIME_ENABLED", "true"
            ).lower()
            == "true",
            market_scan_enabled=os.environ.get(
                "SPECT8_MARKET_SCAN_ENABLED",
                os.environ.get("MARKET_SCAN_ENABLED", "false"),
            ).lower()
            == "true",
            market_scan_after_hour_seconds=int(
                os.environ.get(
                    "SPECT8_MARKET_SCAN_AFTER_HOUR_SECONDS",
                    os.environ.get("MARKET_SCAN_AFTER_HOUR_SECONDS", "60"),
                )
            ),
            market_data_request_min_interval_seconds=float(
                os.environ.get(
                    "SPECT8_MARKET_DATA_REQUEST_MIN_INTERVAL_SECONDS",
                    os.environ.get("MARKET_DATA_REQUEST_MIN_INTERVAL_SECONDS", "8"),
                )
            ),
            market_data_max_requests_per_minute=int(
                os.environ.get(
                    "SPECT8_MARKET_DATA_MAX_REQUESTS_PER_MINUTE",
                    os.environ.get(
                        "TWELVE_DATA_MAX_CREDITS_PER_MINUTE",
                        os.environ.get("MARKET_DATA_MAX_REQUESTS_PER_MINUTE", "8"),
                    ),
                )
            ),
            twelve_data_daily_credit_limit=int(
                os.environ.get("TWELVE_DATA_DAILY_CREDIT_LIMIT", "800")
            ),
            market_data_daily_operational_budget=int(
                os.environ.get("MARKET_DATA_DAILY_OPERATIONAL_BUDGET", "700")
            ),
            market_data_credit_reserve=int(
                os.environ.get("MARKET_DATA_CREDIT_RESERVE", "100")
            ),
            market_data_max_retries_per_instrument=int(
                os.environ.get("SPECT8_MARKET_DATA_MAX_RETRIES_PER_INSTRUMENT", "2")
            ),
            market_data_stale_after_seconds=int(
                os.environ.get("SPECT8_MARKET_DATA_STALE_AFTER_SECONDS", "7200")
            ),
            enabled_instrument_ids=(
                tuple(
                    item.strip()
                    for item in os.environ["SPECT8_ENABLED_INSTRUMENT_IDS"].split(",")
                    if item.strip()
                )
                if os.environ.get("SPECT8_ENABLED_INSTRUMENT_IDS")
                else None
            ),
            market_data_poll_seconds=int(
                os.environ.get("SPECT8_MARKET_DATA_POLL_SECONDS", "300")
            ),
            market_data_safety_delay_seconds=int(
                os.environ.get("SPECT8_MARKET_DATA_SAFETY_DELAY_SECONDS", "30")
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
            historical_replay_database_path=Path(
                os.environ.get(
                    "SPECT8_HISTORICAL_REPLAY_DATABASE_PATH",
                    repository_root / "var" / "spect8_historical_replay.sqlite3",
                )
            ),
            application_environment=os.environ.get(
                "SPECT8_APPLICATION_ENVIRONMENT", "development"
            ).lower(),
            polling_enabled=(
                os.environ["SPECT8_POLLING_ENABLED"].lower() == "true"
                if "SPECT8_POLLING_ENABLED" in os.environ
                else None
            ),
            startup_backfill_enabled=os.environ.get(
                "SPECT8_STARTUP_BACKFILL_ENABLED", "true"
            ).lower()
            == "true",
            provider_discovery_enabled=os.environ.get(
                "SPECT8_PROVIDER_DISCOVERY_ENABLED", "true"
            ).lower()
            == "true",
        )
        configured.validate()
        return configured
