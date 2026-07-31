from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from .market_data.models import CanonicalInstrument, HealthState
from .repository import SQLiteProjectionRepository


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
    display_name: str
    asset_class: str
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
    recent_events: list[EventView]
    execution: ExecutionView


class DashboardEnvelope(BaseModel):
    synthetic: bool
    source: Literal[
        "REPLAY_MARKET_DATA_PROVIDER", "TWELVE_DATA_PROVIDER"
    ]
    notice: str
    data: DashboardData


def dashboard_snapshot(
    repository: SQLiteProjectionRepository,
    instrument: CanonicalInstrument,
    generated_at: datetime,
) -> DashboardData:
    health = repository.provider_health(instrument.provider_id)
    sync = repository.provider_sync(instrument.provider_id)
    statuses = [
        status
        for status in repository.statuses()
        if status["provider"] == instrument.provider_id
        and status["instrument_id"] == instrument.instrument_id
        and status["timeframe"] in {"H1", "H4"}
    ]
    statuses.sort(key=lambda item: item["timeframe"])
    for status in statuses:
        status.setdefault("reason_codes", [])
        status.setdefault("market_values", None)

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
            display_name=instrument.display_name,
            asset_class=instrument.asset_class,
            session_timezone=instrument.session_timezone,
            timeframes=[
                timeframe.value
                for timeframe in instrument.available_timeframes
            ],
            price_precision=instrument.price_precision,
            synthetic=instrument.synthetic,
        ),
        latest_candles=repository.latest_candle_timestamps(
            instrument.provider_id, instrument.instrument_id
        ),
        evaluations=statuses,
        recent_events=repository.recent_events(),
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
