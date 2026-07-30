from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Timeframe(StrEnum):
    H1 = "H1"
    H4 = "H4"


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
    synthetic: bool = True


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
    def confirmed_direction(self) -> Direction | None:
        if self.confirmed_buy:
            return Direction.BUY
        if self.confirmed_sell:
            return Direction.SELL
        return None


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
    signal_bar_close_time: datetime
    last_update: datetime
    idempotency_key: str


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
