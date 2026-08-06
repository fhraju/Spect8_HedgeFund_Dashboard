from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from .market_data.models import CanonicalInstrument, HealthState
from .repository import SQLiteProjectionRepository
from .engine.models import CURRENT_D1_FILTER_V2


class ProviderHealthView(BaseModel):
    provider: str
    state: str
    previous_state: str | None
    checked_at: datetime
    latest_completed_close: datetime | None
    freshness_seconds: int | None
    detail: str
    synthetic: bool


class ProviderSyncView(BaseModel):
    provider: str
    state: str
    last_attempt_at: datetime
    last_success_at: datetime | None
    detail: str


class InstrumentView(BaseModel):
    instrument_id: str
    provider: str
    provider_symbol: str
    display_symbol: str
    display_name: str
    asset_class: str
    enabled: bool
    session_timezone: str
    timeframes: list[str]
    price_precision: int
    synthetic: bool


class CandleTimesView(BaseModel):
    H1: datetime | None = None
    H4: datetime | None = None
    D1: datetime | None = None


class FilterResultView(BaseModel):
    buy_matched: bool
    sell_matched: bool
    daily_buy_level: float
    daily_sell_level: float


class SignalResultView(BaseModel):
    technical_buy: bool
    technical_sell: bool
    confirmed_buy: bool
    confirmed_sell: bool


class LevelsResultView(BaseModel):
    direction: Literal["BUY", "SELL"]
    entry_reference: float
    raw_stop: float
    display_stop: float
    target: float
    target_risk_usd: float
    contract_size: float | None
    contract_status: str


class MarketValuesView(BaseModel):
    signal_open: float
    signal_high: float
    signal_low: float
    signal_close: float
    sma10: float
    sma20: float
    atr_d1_wilder_5: float
    daily_raw_low: float
    daily_raw_high: float
    daily_buy_level: float
    daily_sell_level: float
    recent_low_21: float
    recent_high_21: float
    daily_context_close_time: datetime | None


class FilterAuditDailySessionView(BaseModel):
    session_identifier: str
    session_open_time: datetime
    session_close_time: datetime
    daily_high: str
    daily_low: str


class FilterAuditBuyComparisonView(BaseModel):
    recent_low: str
    operator: Literal["<="]
    buy_threshold: str
    matched: bool


class FilterAuditSellComparisonView(BaseModel):
    recent_high: str
    operator: Literal[">="]
    sell_threshold: str
    matched: bool


class FilterAuditBarView(BaseModel):
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


class FilterAuditView(BaseModel):
    instrument_id: str
    strategy_version: str
    timeframe: Literal["H1", "H4"]
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
    daily_session: FilterAuditDailySessionView
    daily_reference_sessions: list[FilterAuditDailySessionView]
    atr_sessions: list[FilterAuditDailySessionView]
    d1_context_eligibility_time: datetime
    atr_period: int
    atr_value: str
    buffer_percentage: str
    buffer_value: str
    daily_low: str
    daily_high: str
    buy_threshold: str
    sell_threshold: str
    buy_comparison: FilterAuditBuyComparisonView
    sell_comparison: FilterAuditSellComparisonView
    final_classification: str
    source_provider: str
    construction_profile: str
    canonical_timezone: str
    display_timezone: str
    daily_session_authority: str
    selected_bars: list[FilterAuditBarView]


class CurrentPartialD1View(BaseModel):
    session_identifier: str
    session_open_utc: datetime
    session_close_utc: datetime
    first_h1_open_time_utc: datetime
    last_h1_close_time_utc: datetime
    h1_count: int
    source_h1_ids: list[str]
    source_checksum: str
    open: str
    high: str
    low: str
    close: str
    quality_status: str


class DailyFilterSnapshotView(BaseModel):
    snapshot_id: str
    strategy_version: str
    canonical_profile_version: str
    provider: str
    instrument: str
    evaluation_time_utc: datetime
    as_of_h1_close_time_utc: datetime
    current_partial_d1: CurrentPartialD1View
    previous_d1_candle_id: str
    previous_d1_session_id: str
    previous_d1_open_utc: datetime
    previous_d1_close_utc: datetime
    previous_d1_high: str
    previous_d1_low: str
    previous_d1_close: str
    atr_period: int
    atr_value: str
    atr_source_d1_ids: list[str]
    atr_source_checksum: str
    buffer_percentage: str
    buffer_value: str
    buy_threshold: str
    sell_threshold: str
    buy_left_value: str
    buy_operator: Literal["<="]
    buy_right_value: str
    buy_matched: bool
    sell_left_value: str
    sell_operator: Literal[">="]
    sell_right_value: str
    sell_matched: bool
    final_classification: Literal["NONE", "BUY", "SELL", "BUY_AND_SELL"]
    data_quality_status: str
    ingestion_run_id: str | None
    created_at: datetime


class StrategyEvaluationView(BaseModel):
    strategy_id: str
    provider: str
    instrument_id: str
    timeframe: Literal["H1", "H4"]
    source_case_id: str
    synthetic: bool
    data_status: str
    dashboard_state: str
    filter_result: FilterResultView
    signal_result: SignalResultView
    levels_result: LevelsResultView | None
    levels_results: list[LevelsResultView]
    reason_codes: list[str]
    market_values: MarketValuesView | None
    signal_bar_close_time: datetime
    last_update: datetime
    idempotency_key: str
    filter_audit: FilterAuditView | None = None
    strategy_version: str = "SPECT8_MICRO_DAILY_V1_0_3"
    daily_filter_snapshot_id: str | None = None


class EventView(BaseModel):
    id: int
    idempotency_key: str
    sequence: int
    event_type: str
    occurred_at: datetime
    instrument_id: str
    timeframe: Literal["H1", "H4"]
    source_case_id: str
    payload: dict[str, Any]
    synthetic: bool


class ExecutionView(BaseModel):
    enabled: Literal[False] = False
    orders: Literal[0] = 0
    fills: Literal[0] = 0
    detail: str = "Read-only scanner; execution is not implemented."


class DashboardData(BaseModel):
    generated_at: datetime
    data_state: Literal[
        "HEALTHY",
        "EMPTY",
        "PARTIAL",
        "STALE",
        "DATA_UNAVAILABLE",
        "INSUFFICIENT_HISTORY",
        "QUARANTINED",
    ]
    stale: bool
    provider_health: ProviderHealthView | None
    provider_sync: ProviderSyncView | None
    instrument: InstrumentView
    latest_candles: CandleTimesView
    evaluations: list[StrategyEvaluationView]
    daily_filter: DailyFilterSnapshotView | None = None
    recent_events: list[EventView]
    execution: ExecutionView


class DashboardEnvelope(BaseModel):
    synthetic: bool
    source: Literal["REPLAY_MARKET_DATA_PROVIDER", "TWELVE_DATA_PROVIDER"]
    notice: str
    data: DashboardData


class ScannerTimeframeView(BaseModel):
    filter_status: str
    signal_status: str
    evaluation_timestamp: datetime | None
    latest_filter_snapshot_id: str | None


class ScannerCurrentFilterView(BaseModel):
    status: str
    as_of_h1_close_time: datetime | None
    snapshot_id: str | None
    source: Literal["COMPLETED_H1", "WAITING"]


class ScannerInstrumentView(BaseModel):
    instrument_id: str
    display_symbol: str
    display_name: str
    asset_class: str
    enabled: bool
    provider_symbol: str
    provider: str
    exchange: str | None
    mic_code: str | None
    provider_instrument_type: str | None
    provider_timezone: str | None
    validation_status: str
    instrument_kind: str
    exposure_category: str
    underlying_description: str | None
    is_proxy: bool
    proxy_for: str | None
    provider_exchange: str | None
    credit_budget_status: str
    provider_health: str
    stale: bool
    data_status: str
    latest_completed_h1_timestamp: datetime | None
    latest_completed_h4_timestamp: datetime | None
    current_filter: ScannerCurrentFilterView
    H1: ScannerTimeframeView
    H4: ScannerTimeframeView
    latest_error_summary: str | None
    last_successful_provider_update: datetime | None


class ScannerData(BaseModel):
    generated_at: datetime
    instruments: list[ScannerInstrumentView]
    credit_budget: dict[str, Any] | None = None


class ScannerEnvelope(BaseModel):
    synthetic: bool
    source: Literal["REPLAY_MARKET_DATA_PROVIDER", "TWELVE_DATA_PROVIDER"]
    notice: str
    data: ScannerData


def scanner_snapshot(
    repository: SQLiteProjectionRepository,
    instruments: tuple[CanonicalInstrument, ...],
    generated_at: datetime,
    *,
    credit_budget: dict[str, Any] | None = None,
) -> ScannerData:
    statuses = repository.statuses()
    rows: list[ScannerInstrumentView] = []
    for instrument in instruments:
        if not instrument.enabled:
            continue
        health = repository.instrument_health(
            instrument.provider_id, instrument.instrument_id
        )
        latest = repository.latest_candle_timestamps(
            instrument.provider_id, instrument.instrument_id
        )
        instrument_statuses = {
            item["timeframe"]: item
            for item in statuses
            if item["provider"] == instrument.provider_id
            and item["instrument_id"] == instrument.instrument_id
            and item["timeframe"] in {"H1", "H4"}
            and item.get("strategy_version") == CURRENT_D1_FILTER_V2
        }
        latest_filter = repository.latest_daily_filter_snapshot(
            instrument.provider_id,
            instrument.instrument_id,
            CURRENT_D1_FILTER_V2,
        )

        if latest_filter is None:
            current_filter = ScannerCurrentFilterView(
                status="WAITING",
                as_of_h1_close_time=None,
                snapshot_id=None,
                source="WAITING",
            )
        else:
            classification = str(latest_filter.get("final_classification") or "")
            if classification not in {"NONE", "BUY", "SELL", "BUY_AND_SELL"}:
                classification = _direction_status(
                    bool(latest_filter.get("buy_matched")),
                    bool(latest_filter.get("sell_matched")),
                )
            current_filter = ScannerCurrentFilterView(
                status=classification,
                as_of_h1_close_time=latest_filter.get("as_of_h1_close_time_utc"),
                snapshot_id=latest_filter.get("snapshot_id"),
                source="COMPLETED_H1",
            )

        def timeframe(value: str) -> ScannerTimeframeView:
            status = instrument_statuses.get(value)
            if status is None:
                return ScannerTimeframeView(
                    filter_status="WAITING",
                    signal_status="WAITING",
                    evaluation_timestamp=None,
                    latest_filter_snapshot_id=None,
                )
            filters = status["filter_result"]
            signals = status["signal_result"]
            return ScannerTimeframeView(
                filter_status=_direction_status(
                    filters["buy_matched"], filters["sell_matched"]
                ),
                signal_status=_direction_status(
                    signals["confirmed_buy"], signals["confirmed_sell"]
                ),
                evaluation_timestamp=status["signal_bar_close_time"],
                latest_filter_snapshot_id=status.get("daily_filter_snapshot_id"),
            )

        state = str(health["state"]) if health else "BOOTSTRAPPING"
        rows.append(
            ScannerInstrumentView(
                instrument_id=instrument.instrument_id,
                display_symbol=(
                    instrument.display_symbol or instrument.provider_symbol
                ),
                display_name=instrument.display_name,
                asset_class=instrument.asset_class,
                enabled=instrument.enabled,
                provider_symbol=instrument.provider_symbol,
                provider=instrument.provider_id,
                exchange=instrument.exchange,
                mic_code=instrument.mic_code,
                provider_instrument_type=instrument.provider_instrument_type,
                provider_timezone=instrument.provider_timezone,
                validation_status=instrument.validation_status,
                instrument_kind=instrument.instrument_kind.value,
                exposure_category=instrument.exposure_category.value,
                underlying_description=instrument.underlying_description,
                is_proxy=instrument.is_proxy,
                proxy_for=instrument.proxy_for,
                provider_exchange=instrument.exchange,
                credit_budget_status=str(
                    (credit_budget or {}).get("state", "NOT_CONFIGURED")
                ),
                provider_health=state,
                stale=state == HealthState.STALE.value,
                data_status=state,
                latest_completed_h1_timestamp=latest.get("H1"),
                latest_completed_h4_timestamp=latest.get("H4"),
                current_filter=current_filter,
                H1=timeframe("H1"),
                H4=timeframe("H4"),
                latest_error_summary=(
                    health["latest_error_summary"] if health else None
                ),
                last_successful_provider_update=(
                    health["last_success_at"] if health else None
                ),
            )
        )
    return ScannerData(
        generated_at=generated_at,
        instruments=rows,
        credit_budget=credit_budget,
    )


def _direction_status(buy: bool, sell: bool) -> str:
    if buy and sell:
        return "BUY_AND_SELL"
    if buy:
        return "BUY"
    if sell:
        return "SELL"
    return "NONE"


def dashboard_snapshot(
    repository: SQLiteProjectionRepository,
    instrument: CanonicalInstrument,
    generated_at: datetime,
) -> DashboardData:
    instrument_health = repository.instrument_health(
        instrument.provider_id, instrument.instrument_id
    )
    health = (
        {
            "provider": instrument_health["provider"],
            "state": instrument_health["state"],
            "previous_state": None,
            "checked_at": instrument_health["checked_at"],
            "latest_completed_close": instrument_health["latest_completed_close"],
            "freshness_seconds": instrument_health["freshness_seconds"],
            "detail": instrument_health["detail"],
            "synthetic": instrument_health["synthetic"],
        }
        if instrument_health is not None
        else repository.provider_health(instrument.provider_id)
    )
    sync = repository.provider_sync(instrument.provider_id)
    statuses = [
        status
        for status in repository.statuses()
        if status["provider"] == instrument.provider_id
        and status["instrument_id"] == instrument.instrument_id
        and status["timeframe"] in {"H1", "H4"}
    ]
    statuses.sort(key=lambda item: item["timeframe"])
    v2_statuses = [
        status
        for status in statuses
        if status.get("strategy_version") == CURRENT_D1_FILTER_V2
    ]
    if v2_statuses:
        statuses = v2_statuses
    for status in statuses:
        status.setdefault("reason_codes", [])
        status.setdefault("market_values", None)
        status.setdefault("filter_audit", None)

    state = _data_state(health, statuses)
    return DashboardData(
        generated_at=generated_at,
        data_state=state,
        stale=bool(statuses)
        and state
        not in {
            HealthState.HEALTHY.value,
            HealthState.RECOVERED.value,
        },
        provider_health=health,
        provider_sync=sync,
        instrument=InstrumentView(
            instrument_id=instrument.instrument_id,
            provider=instrument.provider_id,
            provider_symbol=instrument.provider_symbol,
            display_symbol=(instrument.display_symbol or instrument.provider_symbol),
            display_name=instrument.display_name,
            asset_class=instrument.asset_class,
            enabled=instrument.enabled,
            session_timezone=instrument.session_timezone,
            timeframes=[
                timeframe.value for timeframe in instrument.available_timeframes
            ],
            price_precision=instrument.price_precision,
            synthetic=instrument.synthetic,
        ),
        latest_candles=repository.latest_candle_timestamps(
            instrument.provider_id, instrument.instrument_id
        ),
        evaluations=statuses,
        daily_filter=repository.latest_daily_filter_snapshot(
            instrument.provider_id,
            instrument.instrument_id,
            CURRENT_D1_FILTER_V2,
        ),
        recent_events=repository.recent_events(instrument_id=instrument.instrument_id),
        execution=ExecutionView(),
    )


def _data_state(
    health: dict[str, Any] | None,
    statuses: list[dict[str, Any]],
) -> str:
    if health is not None and health["state"] in {
        HealthState.STALE.value,
        HealthState.DATA_UNAVAILABLE.value,
        HealthState.INSUFFICIENT_HISTORY.value,
        HealthState.QUARANTINED.value,
    }:
        return str(health["state"])
    if not statuses:
        return "EMPTY"
    if len(statuses) < 2:
        return "PARTIAL"
    if any(status["data_status"] != "READY" for status in statuses):
        return HealthState.INSUFFICIENT_HISTORY.value
    return HealthState.HEALTHY.value
