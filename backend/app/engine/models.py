from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..domain import Bar, Direction, Timeframe

CURRENT_D1_FILTER_V2 = "MICRO_DAILY_FILTER_CURRENT_D1_V2"
LEGACY_FILTER_VERSION = "SPECT8_MICRO_DAILY_V1_0_3"


@dataclass(frozen=True, slots=True)
class CurrentPartialDailyCandle:
    session_identifier: str
    session_open_utc: datetime
    session_close_utc: datetime
    first_h1_open_time_utc: datetime
    last_h1_close_time_utc: datetime
    h1_count: int
    source_h1_ids: tuple[str, ...]
    source_checksum: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    quality_status: str


@dataclass(frozen=True, slots=True)
class DailyFilterSnapshot:
    snapshot_id: str
    strategy_version: str
    canonical_profile_version: str
    provider: str
    instrument: str
    evaluation_time_utc: datetime
    as_of_h1_close_time_utc: datetime
    current_partial_d1: CurrentPartialDailyCandle
    previous_d1_candle_id: str
    previous_d1_session_id: str
    previous_d1_open_utc: datetime
    previous_d1_close_utc: datetime
    previous_d1_high: Decimal
    previous_d1_low: Decimal
    previous_d1_close: Decimal
    atr_period: int
    atr_value: Decimal
    atr_source_d1_ids: tuple[str, ...]
    atr_source_checksum: str
    buffer_percentage: Decimal
    buffer_value: Decimal
    buy_threshold: Decimal
    sell_threshold: Decimal
    buy_left_value: Decimal
    buy_operator: str
    buy_right_value: Decimal
    buy_matched: bool
    sell_left_value: Decimal
    sell_operator: str
    sell_right_value: Decimal
    sell_matched: bool
    final_classification: str
    data_quality_status: str
    ingestion_run_id: str | None
    created_at: datetime


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
    strategy_version: str = LEGACY_FILTER_VERSION
    daily_filter_snapshot: DailyFilterSnapshot | None = None


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
class FilterAuditDailySession:
    session_identifier: str
    session_open_time: datetime
    session_close_time: datetime
    high: Decimal
    low: Decimal


@dataclass(frozen=True, slots=True)
class FilterAuditBuyComparison:
    recent_low: Decimal
    operator: str
    buy_threshold: Decimal
    matched: bool


@dataclass(frozen=True, slots=True)
class FilterAuditSellComparison:
    recent_high: Decimal
    operator: str
    sell_threshold: Decimal
    matched: bool


@dataclass(frozen=True, slots=True)
class FilterAuditBar:
    sequence: int
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    source_id: str
    recent_low: bool
    recent_high: bool
    expected_market_closure_before: bool


@dataclass(frozen=True, slots=True)
class FilterAuditResult:
    instrument_id: str
    strategy_version: str
    timeframe: Timeframe
    evaluation_time: datetime
    evaluation_bar_open_time: datetime
    evaluation_bar_close_time: datetime
    evaluation_bar_open: Decimal
    evaluation_bar_high: Decimal
    evaluation_bar_low: Decimal
    evaluation_bar_close: Decimal
    evaluation_bar_confirmed_closed: bool
    completed_bar_count: int
    available_completed_bar_count: int
    lookback_period: int
    lookback_start_time: datetime
    lookback_end_time: datetime
    recent_low: Decimal
    recent_low_bar_open_time: datetime
    recent_low_bar_close_time: datetime
    recent_high: Decimal
    recent_high_bar_open_time: datetime
    recent_high_bar_close_time: datetime
    daily_session: FilterAuditDailySession
    daily_reference_sessions: tuple[FilterAuditDailySession, ...]
    atr_sessions: tuple[FilterAuditDailySession, ...]
    d1_context_eligibility_time: datetime
    atr_period: int
    atr_value: Decimal
    buffer_percentage: Decimal
    buffer_value: Decimal
    daily_low: Decimal
    daily_high: Decimal
    buy_threshold: Decimal
    sell_threshold: Decimal
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
    filter_audit: FilterAuditResult | None = None
    strategy_version: str = LEGACY_FILTER_VERSION
    daily_filter_snapshot_id: str | None = None

    @property
    def candidates(self) -> tuple[CandidateResult, ...]:
        return tuple(
            candidate
            for candidate in (self.buy_candidate, self.sell_candidate)
            if candidate is not None
        )
