from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Timeframe(StrEnum):
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


class Direction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class EventType(StrEnum):
    BAR_CLOSED = "BAR_CLOSED"
    FILTER_EVALUATED = "FILTER_EVALUATED"
    FILTER_MATCHED = "FILTER_MATCHED"
    FILTER_NOT_MATCHED = "FILTER_NOT_MATCHED"
    SIGNAL_EVALUATED = "SIGNAL_EVALUATED"
    SIGNAL_CONFIRMED = "SIGNAL_CONFIRMED"
    SIGNAL_NOT_CONFIRMED = "SIGNAL_NOT_CONFIRMED"
    LEVELS_CALCULATED = "LEVELS_CALCULATED"
    STATUS_PROJECTED = "STATUS_PROJECTED"


@dataclass(frozen=True, slots=True)
class Bar:
    instrument_id: str
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    provider: str
    is_complete: bool
    volume: Decimal | None = None
    session_timezone: str = "UTC"
    raw_provider_symbol: str | None = None
    raw_open_time: str | None = None
    raw_close_time: str | None = None
    raw_open: str | None = None
    raw_high: str | None = None
    raw_low: str | None = None
    raw_close: str | None = None
    synthetic: bool = True
    quality_status: str = "VALID"
    construction_profile_version: str = "LEGACY"
    provider_adapter_version: str = "legacy"
    source_timeframe: Timeframe | None = None
    source_candle_ids: tuple[str, ...] = ()
    forward_filled: bool = False
    expected_closure_before: bool = False
    ingestion_run_id: str | None = None
    created_at: datetime | None = None
    session_identifier: str | None = None
    session_open_broker_time: str | None = None
    session_close_broker_time: str | None = None


@dataclass(frozen=True, slots=True)
class BarClosedEvent:
    strategy_id: str
    bar: Bar
    occurred_at: datetime
    source_case_id: str

    @property
    def idempotency_key(self) -> str:
        return ":".join(
            (
                self.strategy_id,
                self.bar.provider,
                self.bar.instrument_id,
                self.bar.timeframe.value,
                self.bar.close_time.isoformat().replace("+00:00", "Z"),
            )
        )


@dataclass(frozen=True, slots=True)
class FilterResult:
    buy_matched: bool
    sell_matched: bool
    daily_buy_level: Decimal
    daily_sell_level: Decimal


@dataclass(frozen=True, slots=True)
class SignalResult:
    technical_buy: bool
    technical_sell: bool
    confirmed_buy: bool
    confirmed_sell: bool

    @property
    def confirmed_directions(self) -> tuple[Direction, ...]:
        return tuple(
            direction
            for direction, confirmed in (
                (Direction.BUY, self.confirmed_buy),
                (Direction.SELL, self.confirmed_sell),
            )
            if confirmed
        )

    @property
    def confirmed_direction(self) -> Direction | None:
        directions = self.confirmed_directions
        return directions[0] if len(directions) == 1 else None


@dataclass(frozen=True, slots=True)
class LevelsResult:
    direction: Direction
    entry_reference: Decimal
    raw_stop: Decimal
    display_stop: Decimal
    target: Decimal
    target_risk_usd: Decimal
    contract_size: Decimal | None
    contract_status: str


@dataclass(frozen=True, slots=True)
class StrategyMarketValues:
    signal_open: Decimal
    signal_high: Decimal
    signal_low: Decimal
    signal_close: Decimal
    sma10: Decimal
    sma20: Decimal
    atr_d1_wilder_5: Decimal
    daily_raw_low: Decimal
    daily_raw_high: Decimal
    daily_buy_level: Decimal
    daily_sell_level: Decimal
    recent_low_21: Decimal
    recent_high_21: Decimal
    daily_context_close_time: datetime | None


@dataclass(frozen=True, slots=True)
class FilterAuditDailySession:
    session_identifier: str
    session_open_time: datetime
    session_close_time: datetime
    daily_high: str
    daily_low: str


@dataclass(frozen=True, slots=True)
class FilterAuditBuyComparison:
    recent_low: str
    operator: str
    buy_threshold: str
    matched: bool


@dataclass(frozen=True, slots=True)
class FilterAuditSellComparison:
    recent_high: str
    operator: str
    sell_threshold: str
    matched: bool


@dataclass(frozen=True, slots=True)
class FilterAuditBar:
    sequence: int
    open_time: datetime
    close_time: datetime
    open: str
    high: str
    low: str
    close: str
    source_id: str
    recent_low: bool
    recent_high: bool
    expected_market_closure_before: bool


@dataclass(frozen=True, slots=True)
class FilterAudit:
    instrument_id: str
    strategy_version: str
    timeframe: Timeframe
    evaluation_time: datetime
    evaluation_bar_open_time: datetime
    evaluation_bar_close_time: datetime
    evaluation_bar_open: str
    evaluation_bar_high: str
    evaluation_bar_low: str
    evaluation_bar_close: str
    evaluation_bar_confirmed_closed: bool
    completed_bar_count: int
    available_completed_bar_count: int
    lookback_period: int
    lookback_start_time: datetime
    lookback_end_time: datetime
    recent_low: str
    recent_low_bar_open_time: datetime
    recent_low_bar_close_time: datetime
    recent_high: str
    recent_high_bar_open_time: datetime
    recent_high_bar_close_time: datetime
    daily_session: FilterAuditDailySession
    daily_reference_sessions: tuple[FilterAuditDailySession, ...]
    atr_sessions: tuple[FilterAuditDailySession, ...]
    d1_context_eligibility_time: datetime
    atr_period: int
    atr_value: str
    buffer_percentage: str
    buffer_value: str
    daily_low: str
    daily_high: str
    buy_threshold: str
    sell_threshold: str
    buy_comparison: FilterAuditBuyComparison
    sell_comparison: FilterAuditSellComparison
    final_classification: str
    source_provider: str
    construction_profile: str
    canonical_timezone: str
    display_timezone: str
    daily_session_authority: str
    selected_bars: tuple[FilterAuditBar, ...]


@dataclass(frozen=True, slots=True)
class InstrumentStatus:
    strategy_id: str
    provider: str
    instrument_id: str
    timeframe: Timeframe
    source_case_id: str
    synthetic: bool
    data_status: str
    dashboard_state: str
    filter_result: FilterResult
    signal_result: SignalResult
    levels_result: LevelsResult | None
    levels_results: tuple[LevelsResult, ...]
    reason_codes: tuple[str, ...]
    market_values: StrategyMarketValues
    signal_bar_close_time: datetime
    last_update: datetime
    idempotency_key: str
    filter_audit: FilterAudit | None = None
    strategy_version: str = "SPECT8_MICRO_DAILY_V1_0_3"
    daily_filter_snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_type: EventType
    sequence: int
    idempotency_key: str
    occurred_at: datetime
    instrument_id: str
    timeframe: Timeframe
    source_case_id: str
    synthetic: bool
    payload: dict[str, Any]


def primitive(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return primitive(asdict(value))
    if isinstance(value, dict):
        return {key: primitive(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [primitive(child) for child in value]
    return value
