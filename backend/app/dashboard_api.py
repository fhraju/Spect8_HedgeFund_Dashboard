from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel

from .market_data.models import CanonicalInstrument, HealthState
from .market_data.multi_asset_catalog import FAVORITES
from .repository import SQLiteProjectionRepository
from .domain import FilterMode
from .engine.models import CURRENT_D1_FILTER_V2, CURRENT_W1_FILTER_V1


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
    price_precision: int | None
    synthetic: bool
    provider_environment: str | None = None
    display_timezone: str | None = None
    display_timezone_label: str | None = None


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


class CurrentPartialW1View(BaseModel):
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


class WeeklyFilterSnapshotView(BaseModel):
    snapshot_id: str
    filter_mode: Literal["MACRO"]
    strategy_version: str
    canonical_profile_version: str
    provider: str
    instrument: str
    evaluation_time_utc: datetime
    as_of_h1_close_time_utc: datetime
    current_partial_w1: CurrentPartialW1View
    previous_w1_candle_id: str
    previous_w1_session_id: str
    previous_w1_open_utc: datetime
    previous_w1_close_utc: datetime
    previous_w1_open: str
    previous_w1_high: str
    previous_w1_low: str
    previous_w1_close: str
    atr_period: int
    atr_value: str
    atr_source_w1_ids: list[str]
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
    timeframe: Literal["M30", "H1", "H4"]
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
    active_filter_mode: Literal["MICRO", "MACRO"] = "MICRO"
    filter_timeframe: Literal["D1", "W1"] = "D1"
    daily_filter: DailyFilterSnapshotView | None = None
    weekly_filter: WeeklyFilterSnapshotView | None = None
    recent_events: list[EventView]
    execution: ExecutionView


class DashboardEnvelope(BaseModel):
    synthetic: bool
    source: Literal["REPLAY_MARKET_DATA_PROVIDER", "TWELVE_DATA_PROVIDER", "MARKET_DATA_PLATFORM"]
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
    # Explicit freshness vs the latest completed H1 close. A snapshot whose
    # as_of lags the latest H1 (e.g. evaluation halted by a missing initial
    # session bar) must never be presented as current/healthy.
    filter_freshness: Literal["CURRENT", "STALE", "INCOMPLETE"] = "CURRENT"
    stale_reason: str | None = None
    latest_completed_h1: datetime | None = None
    # Required H1 intervals absent from the same canonical projection the
    # evaluator reads. `missing_h1_intervals` is a bounded display list;
    # `total_missing_h1_count` is the truthful total (None when unknown), so
    # the displayed count is never a truncation cap.
    missing_h1_intervals: list[str] = []
    total_missing_h1_count: int | None = None


class ScannerInstrumentView(BaseModel):
    collection: dict[str, Any] | None = None
    evaluation_freshness: str | None = None
    instrument_id: str
    display_symbol: str
    display_name: str
    asset_class: str
    enabled: bool
    polling_enabled: bool
    provider_symbol: str
    provider: str
    provider_environment: str | None = None
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
    # Independent health dimensions, never collapsed into one status.
    # `feed_health` is source-data freshness only; collection, filter and
    # signal readiness stay separately observable.
    feed_health: str | None = None
    collection_health: str | None = None
    filter_readiness: Literal["CURRENT", "STALE", "INCOMPLETE"] | None = None
    signal_readiness: str | None = None
    evaluation_candidates: list[dict[str, Any]] = []
    attention_reasons: list[str] = []
    display_timezone: str | None = None
    display_timezone_label: str | None = None
    latest_completed_h1_timestamp: datetime | None
    latest_completed_h4_timestamp: datetime | None
    current_filter: ScannerCurrentFilterView
    H1: ScannerTimeframeView
    H4: ScannerTimeframeView
    latest_error_summary: str | None
    last_successful_provider_update: datetime | None


class ScannerData(BaseModel):
    generated_at: datetime
    active_filter_mode: Literal["MICRO", "MACRO"] = "MICRO"
    filter_timeframe: Literal["D1", "W1"] = "D1"
    instruments: list[ScannerInstrumentView]
    credit_budget: dict[str, Any] | None = None
    # Presentation grouping only. Favorites never create separate evaluations,
    # subscriptions, database rows or market state: the same instrument rows are
    # rendered through one additional client-side view.
    favorites: list[str] = []


class ScannerEnvelope(BaseModel):
    synthetic: bool
    source: Literal["REPLAY_MARKET_DATA_PROVIDER", "TWELVE_DATA_PROVIDER", "MARKET_DATA_PLATFORM"]
    notice: str
    data: ScannerData


def scanner_snapshot(
    repository: SQLiteProjectionRepository,
    instruments: tuple[CanonicalInstrument, ...],
    generated_at: datetime,
    *,
    credit_budget: dict[str, Any] | None = None,
    stale_after_seconds: int = 7200,
    dependency_view: Callable[[str, Any, datetime | None], dict[str, Any] | None]
    | None = None,
) -> ScannerData:
    mode_reader = getattr(repository, "active_filter_mode", None)
    active_mode = mode_reader() if callable(mode_reader) else FilterMode.MICRO
    selected_version = (
        CURRENT_W1_FILTER_V1
        if active_mode is FilterMode.MACRO
        else CURRENT_D1_FILTER_V2
    )
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
            and item.get("strategy_version") == selected_version
        }
        latest_filter = (
            repository.latest_weekly_filter_snapshot(
                instrument.provider_id,
                instrument.instrument_id,
                CURRENT_W1_FILTER_V1,
            )
            if active_mode is FilterMode.MACRO
            else repository.latest_daily_filter_snapshot(
                instrument.provider_id,
                instrument.instrument_id,
                CURRENT_D1_FILTER_V2,
            )
        )

        if latest_filter is None:
            current_filter = ScannerCurrentFilterView(
                status="WAITING",
                as_of_h1_close_time=None,
                snapshot_id=None,
                source="WAITING",
                filter_freshness="INCOMPLETE",
                stale_reason="NO_FILTER_SNAPSHOT",
                latest_completed_h1=_parse_utc(latest.get("H1") if latest else None),
                missing_h1_intervals=[],
                total_missing_h1_count=0,
            )
        else:
            classification = str(latest_filter.get("final_classification") or "")
            if classification not in {"NONE", "BUY", "SELL", "BUY_AND_SELL"}:
                classification = _direction_status(
                    bool(latest_filter.get("buy_matched")),
                    bool(latest_filter.get("sell_matched")),
                )
            latest_h1_raw = latest.get("H1") if latest else None
            dependency_valid = None
            dependency_reason = None
            dependency_latest = None
            dependency_missing: list[datetime] | None = None
            if instrument.asset_class == "SHARE_SPREADBET":
                # The evaluator builds share filters from the approved bounded
                # native+canonical share history and validates the required H1
                # prefix with ``current_filter_components``. Prefer that same
                # authoritative view when the runtime can supply it; otherwise
                # fall back to the certified canonical-projection continuity
                # check (never weakened).
                view = None
                if dependency_view is not None:
                    try:
                        view = dependency_view(
                            instrument.instrument_id,
                            active_mode,
                            _parse_utc(latest_filter.get("as_of_h1_close_time_utc")),
                        )
                    except Exception:  # noqa: BLE001 - diagnostic stays informational
                        view = None
                if view:
                    dependency_valid = view.get("valid")
                    dependency_reason = view.get("reason")
                    dependency_latest = _parse_utc(view.get("latest_h1_close"))
                    dependency_missing = list(view.get("missing_opens") or [])
                else:
                    dependency_valid, dependency_reason = _filter_dependency_valid(
                        repository,
                        instrument.provider_id,
                        instrument.instrument_id,
                        active_mode,
                        _parse_utc(latest_filter.get("as_of_h1_close_time_utc")),
                    )
            try:
                freshness, stale_reason, missing_intervals, total_missing = _filter_freshness(
                    repository,
                    instrument.provider_id,
                    instrument.instrument_id,
                    latest_filter.get("as_of_h1_close_time_utc"),
                    latest_h1_raw,
                    dependency_valid=dependency_valid,
                    dependency_reason=dependency_reason,
                    dependency_latest_h1=dependency_latest,
                    dependency_missing=dependency_missing,
                )
            except Exception:  # noqa: BLE001 - diagnostic must stay informational only
                freshness, stale_reason, missing_intervals, total_missing = (
                    "INCOMPLETE", "FILTER_DIAGNOSTIC_FAILED", [], None,
                )
            current_filter = ScannerCurrentFilterView(
                status=classification,
                as_of_h1_close_time=latest_filter.get("as_of_h1_close_time_utc"),
                snapshot_id=latest_filter.get("snapshot_id"),
                source="COMPLETED_H1",
                filter_freshness=freshness,
                stale_reason=stale_reason,
                latest_completed_h1=(
                    dependency_latest or _parse_utc(latest_h1_raw)
                ),
                missing_h1_intervals=missing_intervals,
                total_missing_h1_count=total_missing,
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

        state = (
            HealthState.DATA_UNAVAILABLE.value
            if not instrument.polling_enabled and instrument.asset_class != "SHARE_SPREADBET"
            else (
                str(health["state"])
                if health
                else _fallback_data_state(
                    repository,
                    instrument,
                    generated_at,
                    stale_after_seconds,
                )
            )
        )
        feed_health = _feed_health_state(
            repository, instrument, health, generated_at, stale_after_seconds
        )
        attention_reasons: list[str] = []
        if feed_health not in {
            HealthState.HEALTHY.value,
            HealthState.RECOVERED.value,
        }:
            attention_reasons.append(f"SOURCE_{feed_health}")
        if current_filter.filter_freshness != "CURRENT":
            attention_reasons.append(
                current_filter.stale_reason
                or f"FILTER_{current_filter.filter_freshness}"
            )
        display_timezone, display_timezone_label = _display_time_profile(instrument)
        try:
            from .config import INSTRUMENT_AUTHORITY_MAP
            _provider_env = INSTRUMENT_AUTHORITY_MAP.get(instrument.instrument_id)
        except Exception:
            _provider_env = None
        rows.append(
            ScannerInstrumentView(
                instrument_id=instrument.instrument_id,
                display_symbol=(
                    instrument.display_symbol or instrument.provider_symbol
                ),
                display_name=instrument.display_name,
                asset_class=instrument.asset_class,
                enabled=instrument.enabled,
                polling_enabled=instrument.polling_enabled,
                provider_symbol=instrument.provider_symbol,
                provider=instrument.provider_id,
                provider_environment=_provider_env,
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
                feed_health=feed_health,
                filter_readiness=current_filter.filter_freshness,
                attention_reasons=attention_reasons,
                display_timezone=display_timezone,
                display_timezone_label=display_timezone_label,
                latest_completed_h1_timestamp=latest.get("H1"),
                latest_completed_h4_timestamp=latest.get("H4"),
                current_filter=current_filter,
                H1=timeframe("H1"),
                H4=timeframe("H4"),
                latest_error_summary=(
                    (
                        "Unavailable from the configured provider; no market-data "
                        "request is made."
                        if not instrument.polling_enabled and instrument.asset_class != "SHARE_SPREADBET"
                        else (health["latest_error_summary"] if health else None)
                    )
                ),
                last_successful_provider_update=(
                    health["last_success_at"] if health else None
                ),
            )
        )
    return ScannerData(
        generated_at=generated_at,
        active_filter_mode=active_mode,
        filter_timeframe=("W1" if active_mode is FilterMode.MACRO else "D1"),
        instruments=rows,
        credit_budget=credit_budget,
        favorites=list(FAVORITES),
    )


def _direction_status(buy: bool, sell: bool) -> str:
    if buy and sell:
        return "BUY_AND_SELL"
    if buy:
        return "BUY"
    if sell:
        return "SELL"
    return "NONE"


def _fallback_data_state(
    repository: SQLiteProjectionRepository,
    instrument: CanonicalInstrument,
    generated_at: datetime,
    stale_after_seconds: int,
) -> str:
    """Truthful state when no per-instrument polling health row exists.

    Derived from the actual latest completed canonical H1 bar age rather than
    unconditionally reporting BOOTSTRAPPING.
    """
    latest = repository.latest_candle_timestamps(
        instrument.provider_id, instrument.instrument_id
    )
    latest_h1 = latest.get("H1") if latest else None
    if latest_h1 is None:
        return "BOOTSTRAPPING"
    try:
        from datetime import timezone as _tz

        closed_at = (
            latest_h1.astimezone(_tz.utc)
            if isinstance(latest_h1, datetime)
            else datetime.fromisoformat(str(latest_h1).replace("Z", "+00:00"))
        )
        age_seconds = (
            generated_at.astimezone(_tz.utc) - closed_at.astimezone(_tz.utc)
        ).total_seconds()
    except (ValueError, TypeError):
        return "BOOTSTRAPPING"
    if age_seconds > stale_after_seconds:
        return HealthState.STALE.value
    return "BOOTSTRAPPING"


def _feed_health_state(
    repository: SQLiteProjectionRepository,
    instrument: CanonicalInstrument,
    health: dict[str, Any] | None,
    generated_at: datetime,
    stale_after_seconds: int,
) -> str:
    """Source/feed health: completed source-data freshness only.

    Deliberately independent of filter readiness, collection recovery and
    signal/evaluation readiness. A fresh canonical H1 without a polling
    health row is HEALTHY source data, not BOOTSTRAPPING.
    """
    if not instrument.polling_enabled and instrument.asset_class != "SHARE_SPREADBET":
        return HealthState.DATA_UNAVAILABLE.value
    if health is not None:
        return str(health["state"])
    latest = repository.latest_candle_timestamps(
        instrument.provider_id, instrument.instrument_id
    )
    latest_h1 = latest.get("H1") if latest else None
    if latest_h1 is None:
        return "BOOTSTRAPPING"
    try:
        closed_at = (
            latest_h1.astimezone(timezone.utc)
            if isinstance(latest_h1, datetime)
            else datetime.fromisoformat(str(latest_h1).replace("Z", "+00:00"))
        )
        age_seconds = (
            generated_at.astimezone(timezone.utc) - closed_at.astimezone(timezone.utc)
        ).total_seconds()
    except (ValueError, TypeError):
        return "BOOTSTRAPPING"
    if age_seconds > stale_after_seconds:
        return HealthState.STALE.value
    return HealthState.HEALTHY.value


def _display_time_profile(
    instrument: CanonicalInstrument,
) -> tuple[str | None, str | None]:
    """Approved display-time metadata; never inferred from a ticker.

    FX keeps the existing IC Markets broker-time display. Shares use the
    approved IG session display timezone (Europe/London). Anything without
    approved metadata resolves to an explicit UTC fallback in the frontend.
    """
    session_timezone = getattr(instrument, "session_timezone", None)
    asset_class = getattr(instrument, "asset_class", None)
    if asset_class == "SHARE_SPREADBET" or session_timezone == "Europe/London":
        return "Europe/London", "IG session time"
    if asset_class in {"FOREX", "METAL"}:
        return "IC_MARKETS_NY_CLOSE_FOREX_V1", "IC Markets Broker Time"
    return None, None


def dashboard_snapshot(
    repository: SQLiteProjectionRepository,
    instrument: CanonicalInstrument,
    generated_at: datetime,
) -> DashboardData:
    mode_reader = getattr(repository, "active_filter_mode", None)
    active_mode = mode_reader() if callable(mode_reader) else FilterMode.MICRO
    selected_version = (
        CURRENT_W1_FILTER_V1
        if active_mode is FilterMode.MACRO
        else CURRENT_D1_FILTER_V2
    )
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
    selected_statuses = [
        status
        for status in statuses
        if status.get("strategy_version") == selected_version
    ]
    if selected_statuses or active_mode is FilterMode.MACRO:
        statuses = selected_statuses
    for status in statuses:
        status.setdefault("reason_codes", [])
        status.setdefault("market_values", None)
        status.setdefault("filter_audit", None)

    state = _data_state(health, statuses)
    # Provider environment badge: IG_DEMO for EUR/GBP, IG_LIVE for USD_JPY
    try:
        from .config import INSTRUMENT_AUTHORITY_MAP
        provider_env = INSTRUMENT_AUTHORITY_MAP.get(instrument.instrument_id)
    except Exception:
        provider_env = None
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
            provider_environment=provider_env,
            display_timezone=_display_time_profile(instrument)[0],
            display_timezone_label=_display_time_profile(instrument)[1],
        ),
        latest_candles=repository.latest_candle_timestamps(
            instrument.provider_id, instrument.instrument_id
        ),
        evaluations=statuses,
        active_filter_mode=active_mode,
        filter_timeframe=("W1" if active_mode is FilterMode.MACRO else "D1"),
        daily_filter=repository.latest_daily_filter_snapshot(
            instrument.provider_id,
            instrument.instrument_id,
            CURRENT_D1_FILTER_V2,
        ),
        weekly_filter=repository.latest_weekly_filter_snapshot(
            instrument.provider_id,
            instrument.instrument_id,
            CURRENT_W1_FILTER_V1,
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


def _parse_utc(value: object) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp; None when absent/unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _expected_trading_h1_opens(provider_id: str, start: datetime, end: datetime) -> list[datetime] | None:
    """Expected H1 opens from the shared FX session/calendar authority.

    This is the same authority the collector registers
    (`forex_calendar.load_definition`, defaulting to the frozen FX_NY_17
    normal schedule): one definition of expected trading intervals for both
    collection and diagnosis. Returns None when the provider is not covered
    by that authority instead of inventing a wall-clock schedule.
    """
    if provider_id != "MARKET_DATA_PLATFORM":
        return None
    try:
        from hedgefund_market_data.providers.ig.forex_calendar import (
            expected_h1_opens,
            load_definition,
        )
        return list(expected_h1_opens(load_definition(), start, end))
    except Exception:
        return None


def _filter_dependency_valid(
    repository: SQLiteProjectionRepository,
    provider_id: str,
    instrument_id: str,
    mode: FilterMode,
    as_of: datetime | None,
) -> tuple[bool | None, str | None]:
    """Whether the filter's required dependency window is currently contiguous.

    Filter validity is independent of the filter's own H1 timestamp: a filter
    can be timestamp-current while its required daily/weekly dependency context
    has an unexpected hole. This reads the same canonical projection the
    evaluator reads and applies the frozen continuity rule (consecutive
    intervals must abut; an ``expected_closure_before`` bar is the only allowed
    gap). Returns ``(None, None)`` when the projection cannot be inspected so a
    diagnostic failure degrades to "unknown" rather than a false CURRENT.
    """
    required = ("W1", "D1") if mode is FilterMode.MACRO else ("D1",)
    reader = getattr(repository, "canonical_bar_objects", None)
    if not callable(reader) or as_of is None:
        return (None, None)
    try:
        for timeframe in required:
            bars = tuple(
                sorted(
                    (
                        bar
                        for bar in reader(provider_id, instrument_id, timeframe)
                        if bar.quality_status == "VALID"
                        and not bar.synthetic
                        and not bar.forward_filled
                        and bar.close_time <= as_of
                    ),
                    key=lambda bar: bar.open_time,
                )
            )
            if len(bars) < 2 or any(
                current.open_time != previous.close_time
                and not current.expected_closure_before
                for previous, current in zip(bars, bars[1:])
            ):
                return (False, f"FILTER_DEPENDENCY_{timeframe}_INCOMPLETE")
        return (True, None)
    except Exception:  # noqa: BLE001 - freshness must stay best-effort
        return (None, None)


def _filter_freshness(
    repository: SQLiteProjectionRepository,
    provider_id: str,
    instrument_id: str,
    as_of_h1_close_time: object,
    latest_h1_close_time: object,
    *,
    display_limit: int = 32,
    dependency_valid: bool | None = None,
    dependency_reason: str | None = None,
    dependency_latest_h1: datetime | None = None,
    dependency_missing: list[datetime] | None = None,
) -> tuple[
    Literal["CURRENT", "STALE", "INCOMPLETE"], str | None, list[str], int | None
]:
    """Compare the filter snapshot as_of against the latest completed H1.

    Returns (freshness, stale_reason, missing_h1_intervals, total_missing).
    `missing_h1_intervals` lists expected H1 *opens* (bounded display list);
    the total is truthful and calendar-aware (weekend closures are never
    counted). Reads the same canonical projection the evaluator reads, so
    the diagnostic can neither contradict the evaluation nor consult the
    retired fossil store. A matching H1 timestamp is not sufficient for
    CURRENT: when the filter's required dependency window is known to be
    incomplete the filter is reported STALE instead of advertising health.
    Never raises: lookup failures degrade to an unknown total instead of
    letting a stale filter look current.

    For shares the evaluator's bounded native+canonical history may supply
    ``dependency_latest_h1`` (authoritative latest completed H1) and
    ``dependency_missing`` (expected H1 opens absent from that same history);
    those replace the canonical projection comparison without weakening the
    fail-closed dependency verdict.
    """
    as_of = _parse_utc(as_of_h1_close_time)
    latest_h1 = _parse_utc(latest_h1_close_time)
    if dependency_latest_h1 is not None:
        latest_h1 = dependency_latest_h1
    if as_of is None:
        return ("INCOMPLETE", "NO_FILTER_SNAPSHOT", [], 0)
    if latest_h1 is None:
        return ("INCOMPLETE", "NO_COMPLETED_H1", [], 0)
    if as_of == latest_h1:
        if dependency_valid is False:
            missing = [_iso_utc(o) for o in (dependency_missing or [])]
            return (
                "STALE",
                dependency_reason or "FILTER_DEPENDENCY_INCOMPLETE",
                missing[:display_limit],
                len(missing),
            )
        return ("CURRENT", None, [], 0)
    if as_of > latest_h1:
        return ("STALE", "FILTER_AHEAD_OF_LATEST_H1", [], 0)
    if dependency_missing is not None:
        missing = [_iso_utc(o) for o in dependency_missing]
        return (
            "STALE",
            "FILTER_BEHIND_LATEST_H1",
            missing[:display_limit],
            len(missing),
        )
    try:
        expected = _expected_trading_h1_opens(provider_id, as_of, latest_h1)
        if expected is None:
            return ("STALE", "FILTER_BEHIND_LATEST_H1", [], None)
        # An expected open is satisfied by the canonical VALID bar closing
        # one hour later; the diagnostic reports expected opens.
        wanted = [o for o in expected if o >= as_of]
        present = set(
            repository.present_h1_close_times(provider_id, instrument_id)
            if hasattr(repository, "present_h1_close_times")
            else ()
        )
        missing = [
            _iso_utc(o)
            for o in wanted
            if _iso_utc(o + timedelta(hours=1)) not in present
        ]
        return ("STALE", "FILTER_BEHIND_LATEST_H1", missing[:display_limit], len(missing))
    except Exception:  # noqa: BLE001 - freshness must stay best-effort
        return ("STALE", "FILTER_BEHIND_LATEST_H1", [], None)
