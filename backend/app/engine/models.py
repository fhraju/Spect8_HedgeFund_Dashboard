from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..domain import Bar, Direction, Timeframe


@dataclass(frozen=True, slots=True)
class InstrumentMetadata:
    strategy_id: str
    instrument_id: str
    display_name: str
    provider: str
    session_timezone: str
    candle_boundary_convention: str
    point_size: Decimal
    price_precision: int
    minimum_stop_distance_points: Decimal | None
    tick_size: Decimal | None
    tick_value_usd: Decimal | None
    conversion_rate_to_usd: Decimal | None
    contract_min: Decimal | None
    contract_max: Decimal | None
    contract_step: Decimal | None


@dataclass(frozen=True, slots=True)
class StrategyRequest:
    case_id: str
    strategy_id: str
    timeframe: Timeframe
    evaluation_time: datetime
    signal_bars: tuple[Bar, ...]
    daily_bars: tuple[Bar, ...]
    instrument: InstrumentMetadata


@dataclass(frozen=True, slots=True)
class BarsUsed:
    signal_completed_count: int
    daily_completed_count: int
    excluded_incomplete_count: int
    signal_bar_close_time: datetime | None
    daily_endpoint_close_time: datetime | None


@dataclass(frozen=True, slots=True)
class IndicatorResult:
    sma10: Decimal
    sma20: Decimal
    atr_d1_wilder_5: Decimal
    activation_buffer: Decimal
    stop_atr_distance: Decimal
    point_adjustment: Decimal
    daily_raw_low: Decimal
    daily_raw_high: Decimal
    daily_buy_level: Decimal
    daily_sell_level: Decimal
    recent_low_21: Decimal
    recent_high_21: Decimal


@dataclass(frozen=True, slots=True)
class FilterSideResult:
    direction: Direction
    matched: bool
    recent_extreme: Decimal
    daily_level: Decimal
    reason_code: str


@dataclass(frozen=True, slots=True)
class MicroDailyFilterResult:
    buy: FilterSideResult
    sell: FilterSideResult
    daily_raw_low: Decimal
    daily_raw_high: Decimal
    activation_buffer: Decimal


@dataclass(frozen=True, slots=True)
class PivotResult:
    direction: Direction
    shift: int
    open_time: datetime
    price: Decimal
    structural_window_extreme: Decimal
    structural_passed: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class TechnicalSideResult:
    direction: Direction
    sma_rejection: bool
    structural_pivot: bool
    technical_signal: bool
    pivot: PivotResult
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Spect8SignalResult:
    buy: TechnicalSideResult
    sell: TechnicalSideResult
    sma10: Decimal
    sma20: Decimal


@dataclass(frozen=True, slots=True)
class PositionSizeResult:
    target_risk_usd: Decimal
    monetary_loss_per_one_contract: Decimal | None
    raw_size: Decimal | None
    display_size: Decimal | None
    contract_status: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class CandidateResult:
    direction: Direction
    entry_reference: Decimal
    raw_strategy_stop: Decimal
    provider_adjusted_stop: Decimal
    risk_distance: Decimal
    target_3r: Decimal
    position_size: PositionSizeResult
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    buy_filter_matched: bool
    sell_filter_matched: bool
    buy_sma_rejection: bool
    sell_sma_rejection: bool
    buy_structural_pivot: bool
    sell_structural_pivot: bool
    technical_buy_signal: bool
    technical_sell_signal: bool
    confirmed_buy: bool
    confirmed_sell: bool
    dashboard_state: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    case_id: str
    strategy_id: str
    instrument_id: str
    timeframe: Timeframe
    evaluation_time: datetime
    data_status: str
    issues: tuple[str, ...]
    reason_codes: tuple[str, ...]
    bars: BarsUsed
    classification: ClassificationResult | None
    indicators: IndicatorResult | None
    filters: MicroDailyFilterResult | None
    signals: Spect8SignalResult | None
    buy_candidate: CandidateResult | None
    sell_candidate: CandidateResult | None
    signal_bar: Bar | None

    @property
    def candidates(self) -> tuple[CandidateResult, ...]:
        return tuple(
            candidate
            for candidate in (self.buy_candidate, self.sell_candidate)
            if candidate is not None
        )
