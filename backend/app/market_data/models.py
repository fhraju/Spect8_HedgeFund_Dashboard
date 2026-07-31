from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from ..domain import Bar, Timeframe
from ..engine.models import InstrumentMetadata


class HealthState(StrEnum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    QUARANTINED = "QUARANTINED"
    RECOVERED = "RECOVERED"


class ProviderErrorCode(StrEnum):
    AUTHENTICATION = "AUTHENTICATION"
    VALIDATION = "VALIDATION"
    RATE_LIMIT = "RATE_LIMIT"
    TEMPORARY_UNAVAILABLE = "TEMPORARY_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    DUPLICATE_CANDLE = "DUPLICATE_CANDLE"
    MISSING_CANDLE = "MISSING_CANDLE"


class MarketDataProviderError(RuntimeError):
    """Sanitized provider-boundary error using canonical health vocabulary."""

    def __init__(
        self,
        code: ProviderErrorCode,
        health_state: HealthState,
        detail: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.health_state = health_state
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    provider_id: str
    display_name: str
    adapter_version: str
    synthetic: bool


@dataclass(frozen=True, slots=True)
class CanonicalInstrument:
    instrument_id: str
    provider_id: str
    provider_symbol: str
    display_name: str
    asset_class: str
    point_size: Decimal
    tick_size: Decimal | None
    price_precision: int
    tick_value_usd: Decimal | None
    conversion_rate_to_usd: Decimal | None
    contract_min: Decimal | None
    contract_max: Decimal | None
    contract_step: Decimal | None
    minimum_stop_distance_points: Decimal | None
    quote_currency: str
    profit_currency: str
    session_timezone: str
    candle_boundary_convention: str
    available_timeframes: tuple[Timeframe, ...]
    strategy_id: str
    synthetic: bool = True

    def to_strategy_metadata(self) -> InstrumentMetadata:
        return InstrumentMetadata(
            strategy_id=self.strategy_id,
            instrument_id=self.instrument_id,
            display_name=self.display_name,
            provider=self.provider_id,
            session_timezone=self.session_timezone,
            candle_boundary_convention=self.candle_boundary_convention,
            point_size=self.point_size,
            price_precision=self.price_precision,
            minimum_stop_distance_points=self.minimum_stop_distance_points,
            tick_size=self.tick_size,
            tick_value_usd=self.tick_value_usd,
            conversion_rate_to_usd=self.conversion_rate_to_usd,
            contract_min=self.contract_min,
            contract_max=self.contract_max,
            contract_step=self.contract_step,
        )


@dataclass(frozen=True, slots=True)
class RawProviderCandle:
    provider_id: str
    provider_symbol: str
    timeframe: Timeframe
    raw_open_time: str
    raw_close_time: str
    open: str
    high: str
    low: str
    close: str
    volume: str | None
    is_complete: bool
    session_timezone: str


@dataclass(frozen=True, slots=True)
class ProviderClosedBar:
    source_id: str
    instrument_id: str
    evaluation_time: datetime
    candle: RawProviderCandle


@dataclass(frozen=True, slots=True)
class ProviderHistory:
    source_id: str
    instrument_id: str
    timeframe: Timeframe
    evaluation_time: datetime
    signal_bars: tuple[RawProviderCandle, ...]
    daily_bars: tuple[RawProviderCandle, ...]


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider_id: str
    state: HealthState
    checked_at: datetime
    latest_completed_close: datetime | None
    freshness_seconds: int | None
    detail: str
    synthetic: bool = True


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    candle: Bar | None
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistoryValidation:
    signal_bars: tuple[Bar, ...]
    daily_bars: tuple[Bar, ...]
    issues: tuple[str, ...]
