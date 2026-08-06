from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Protocol, Sequence

from ..domain import Bar, Direction, Timeframe
from ..market_data.forex_profile import (
    CANONICAL_TIMEZONE,
    PROFILE_ID,
    GapType,
    classify_market_gap,
)
from ..market_data.session_boundaries import (
    NEW_YORK,
    is_expected_forex_weekend_gap,
)
from .indicators import completed_extremes, simple_moving_average, wilder_atr
from .levels import calculate_candidate_levels, calculate_level_distances
from .micro_daily_filter import evaluate_micro_daily_filter
from .models import (
    BarsUsed,
    FilterAuditBar,
    ClassificationResult,
    FilterAuditBuyComparison,
    FilterAuditDailySession,
    FilterAuditResult,
    FilterAuditSellComparison,
    FilterSideResult,
    IndicatorResult,
    MicroDailyFilterResult,
    CURRENT_D1_FILTER_V2,
    StrategyEvaluation,
    StrategyRequest,
)
from .spect8_signal import evaluate_spect8_signal

STRATEGY_ID = "SPECT8_MICRO_DAILY_V1_0"
SPECIFICATION_ID = "SPECT8_MICRO_DAILY_V1_0_3"
TIMEFRAME_STEP = {
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}


class StrategyEvaluator(Protocol):
    def evaluate(self, request: StrategyRequest) -> StrategyEvaluation:
        """Calculate one independent instrument/timeframe strategy result."""


def _stream_issues(bars: Sequence[Bar], timeframe: Timeframe) -> list[str]:
    relevant = [bar for bar in bars if bar.timeframe is timeframe]
    identities = [
        (
            bar.instrument_id,
            bar.timeframe,
            bar.open_time,
            bar.close_time,
            bar.provider,
        )
        for bar in relevant
    ]
    issues: list[str] = []
    if len(identities) != len(set(identities)):
        issues.append("DUPLICATE_CANDLE")
    open_times = [bar.open_time for bar in relevant]
    if any(current < previous for previous, current in zip(open_times, open_times[1:])):
        issues.append("OUT_OF_ORDER_CANDLE")
    return issues


def _missing_issue(
    bars: Sequence[Bar],
    timeframe: Timeframe,
    issue: str,
) -> list[str]:
    step = TIMEFRAME_STEP[timeframe]
    if any(
        current.open_time - previous.open_time != step
        and not current.expected_closure_before
        and not is_expected_forex_weekend_gap(
            previous.open_time + step,
            current.open_time,
        )
        for previous, current in zip(bars, bars[1:])
    ):
        return [issue]
    return []


def _dashboard_state(
    confirmed_buy: bool,
    confirmed_sell: bool,
    buy_filter: bool,
    sell_filter: bool,
) -> str:
    if confirmed_buy and confirmed_sell:
        return "CONFIRMED_BOTH"
    if confirmed_buy:
        return "CONFIRMED_BUY"
    if confirmed_sell:
        return "CONFIRMED_SELL"
    if buy_filter and sell_filter:
        return "FILTERED_BOTH"
    if buy_filter:
        return "FILTERED_BUY"
    if sell_filter:
        return "FILTERED_SELL"
    return "WATCHING"


def _filter_classification(buy_matched: bool, sell_matched: bool) -> str:
    if buy_matched and sell_matched:
        return "BUY + SELL"
    if buy_matched:
        return "BUY"
    if sell_matched:
        return "SELL"
    return "WATCHING"


class Spect8StrategyEvaluator:
    """Pure production evaluator for the frozen Spect8 Micro Daily rules."""

    def evaluate(self, request: StrategyRequest) -> StrategyEvaluation:
        if request.strategy_id not in (STRATEGY_ID, CURRENT_D1_FILTER_V2):
            raise ValueError(f"unsupported strategy: {request.strategy_id}")
        if request.timeframe not in (Timeframe.H1, Timeframe.H4):
            raise ValueError("strategy timeframe must be H1 or H4")

        instrument = request.instrument
        selected_signal = [
            bar
            for bar in request.signal_bars
            if bar.timeframe is request.timeframe
            and bar.instrument_id == instrument.instrument_id
            and bar.provider == instrument.provider
        ]
        excluded = sum(
            1
            for bar in selected_signal
            if not bar.is_complete or bar.close_time >= request.evaluation_time
        )
        completed_signal = [
            bar
            for bar in selected_signal
            if bar.is_complete and bar.close_time < request.evaluation_time
        ]

        issues = _stream_issues(request.signal_bars, request.timeframe)
        issues.extend(_stream_issues(request.daily_bars, Timeframe.D1))
        issues.extend(
            _missing_issue(
                completed_signal,
                request.timeframe,
                "MISSING_SIGNAL_CANDLE",
            )
        )

        completed_daily: list[Bar] = []
        if completed_signal:
            signal_close = completed_signal[-1].close_time
            selected_daily = [
                bar
                for bar in request.daily_bars
                if bar.timeframe is Timeframe.D1
                and bar.instrument_id == instrument.instrument_id
                and bar.provider == instrument.provider
            ]
            completed_daily = [
                bar
                for bar in selected_daily
                if bar.is_complete and bar.close_time <= signal_close
            ]
        issues.extend(
            _missing_issue(
                completed_daily,
                Timeframe.D1,
                "MISSING_DAILY_CANDLE",
            )
        )
        if len(completed_signal) < 30:
            issues.append("INSUFFICIENT_SIGNAL_HISTORY")
        if len(completed_daily) < 6:
            issues.append("INSUFFICIENT_DAILY_HISTORY")
        snapshot = request.daily_filter_snapshot
        if request.strategy_version == CURRENT_D1_FILTER_V2:
            if snapshot is None:
                issues.append("DAILY_FILTER_SNAPSHOT_UNAVAILABLE")
            elif completed_signal and (
                snapshot.instrument != instrument.instrument_id
                or snapshot.provider != instrument.provider
                or snapshot.as_of_h1_close_time_utc != completed_signal[-1].close_time
                or snapshot.strategy_version != CURRENT_D1_FILTER_V2
            ):
                issues.append("DAILY_FILTER_SNAPSHOT_MISMATCH")
        if issues:
            unique_issues = tuple(sorted(set(issues)))
            return StrategyEvaluation(
                case_id=request.case_id,
                strategy_id=request.strategy_id,
                instrument_id=instrument.instrument_id,
                timeframe=request.timeframe,
                evaluation_time=request.evaluation_time,
                data_status="UNAVAILABLE",
                issues=unique_issues,
                reason_codes=("DATA_UNAVAILABLE", *unique_issues),
                bars=BarsUsed(
                    signal_completed_count=0,
                    daily_completed_count=0,
                    excluded_incomplete_count=excluded,
                    signal_bar_close_time=None,
                    daily_endpoint_close_time=None,
                ),
                classification=None,
                indicators=None,
                filters=None,
                signals=None,
                buy_candidate=None,
                sell_candidate=None,
                signal_bar=None,
                strategy_version=request.strategy_version,
                daily_filter_snapshot_id=(
                    snapshot.snapshot_id if snapshot is not None else None
                ),
            )

        atr = (
            snapshot.atr_value
            if snapshot is not None and request.strategy_version == CURRENT_D1_FILTER_V2
            else wilder_atr(completed_daily, 5)
        )
        sma10 = simple_moving_average(completed_signal, 10)
        sma20 = simple_moving_average(completed_signal, 20)
        recent_low, recent_high = completed_extremes(completed_signal, 21)
        if snapshot is not None and request.strategy_version == CURRENT_D1_FILTER_V2:
            filter_result = MicroDailyFilterResult(
                buy=FilterSideResult(
                    direction=Direction.BUY,
                    matched=snapshot.buy_matched,
                    recent_extreme=recent_low,
                    daily_level=snapshot.buy_threshold,
                    reason_code=(
                        "BUY_FILTER_MATCHED"
                        if snapshot.buy_matched
                        else "BUY_FILTER_NOT_MATCHED"
                    ),
                ),
                sell=FilterSideResult(
                    direction=Direction.SELL,
                    matched=snapshot.sell_matched,
                    recent_extreme=recent_high,
                    daily_level=snapshot.sell_threshold,
                    reason_code=(
                        "SELL_FILTER_MATCHED"
                        if snapshot.sell_matched
                        else "SELL_FILTER_NOT_MATCHED"
                    ),
                ),
                daily_raw_low=snapshot.previous_d1_low,
                daily_raw_high=snapshot.previous_d1_high,
                activation_buffer=snapshot.buffer_value,
            )
        else:
            filter_result = evaluate_micro_daily_filter(
                completed_signal, completed_daily, atr
            )
        signal_result = evaluate_spect8_signal(completed_signal, sma10, sma20)
        confirmed_buy = filter_result.buy.matched and signal_result.buy.technical_signal
        confirmed_sell = (
            filter_result.sell.matched and signal_result.sell.technical_signal
        )
        classification_reason_codes = (
            ("CONFIRMED_BUY" if confirmed_buy else "BUY_NOT_CONFIRMED"),
            ("CONFIRMED_SELL" if confirmed_sell else "SELL_NOT_CONFIRMED"),
        )
        dashboard_state = _dashboard_state(
            confirmed_buy,
            confirmed_sell,
            filter_result.buy.matched,
            filter_result.sell.matched,
        )
        classification = ClassificationResult(
            buy_filter_matched=filter_result.buy.matched,
            sell_filter_matched=filter_result.sell.matched,
            buy_sma_rejection=signal_result.buy.sma_rejection,
            sell_sma_rejection=signal_result.sell.sma_rejection,
            buy_structural_pivot=signal_result.buy.structural_pivot,
            sell_structural_pivot=signal_result.sell.structural_pivot,
            technical_buy_signal=signal_result.buy.technical_signal,
            technical_sell_signal=signal_result.sell.technical_signal,
            confirmed_buy=confirmed_buy,
            confirmed_sell=confirmed_sell,
            dashboard_state=dashboard_state,
            reason_codes=classification_reason_codes,
        )
        latest = completed_signal[-1]
        filter_window = completed_signal[-21:]
        recent_low_bar = min(filter_window, key=lambda bar: bar.low)
        recent_high_bar = max(filter_window, key=lambda bar: bar.high)
        selected_audit_bars = []
        for sequence, bar in enumerate(filter_window, start=1):
            previous = filter_window[sequence - 2] if sequence > 1 else None
            gap = classify_market_gap(previous, bar) if previous else None
            selected_audit_bars.append(
                FilterAuditBar(
                    sequence=sequence,
                    open_time=bar.open_time,
                    close_time=bar.close_time,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    source_id=(
                        f"{bar.provider}:{bar.instrument_id}:"
                        f"{bar.timeframe.value}:{bar.close_time.isoformat()}"
                    ),
                    recent_low=bar is recent_low_bar,
                    recent_high=bar is recent_high_bar,
                    expected_market_closure_before=(
                        gap is not None
                        and gap.gap_type is GapType.EXPECTED_MARKET_CLOSURE
                    ),
                )
            )

        def daily_session(bar: Bar) -> FilterAuditDailySession:
            return FilterAuditDailySession(
                session_identifier=(
                    bar.close_time.astimezone(NEW_YORK).date().isoformat()
                ),
                session_open_time=bar.open_time,
                session_close_time=bar.close_time,
                high=bar.high,
                low=bar.low,
            )

        audit = FilterAuditResult(
            instrument_id=instrument.instrument_id,
            strategy_version=SPECIFICATION_ID,
            timeframe=request.timeframe,
            evaluation_time=request.evaluation_time,
            evaluation_bar_open_time=latest.open_time,
            evaluation_bar_close_time=latest.close_time,
            evaluation_bar_open=latest.open,
            evaluation_bar_high=latest.high,
            evaluation_bar_low=latest.low,
            evaluation_bar_close=latest.close,
            evaluation_bar_confirmed_closed=(
                latest.is_complete and latest.close_time < request.evaluation_time
            ),
            completed_bar_count=len(filter_window),
            available_completed_bar_count=len(completed_signal),
            lookback_period=21,
            lookback_start_time=filter_window[0].close_time,
            lookback_end_time=filter_window[-1].close_time,
            recent_low=filter_result.buy.recent_extreme,
            recent_low_bar_open_time=recent_low_bar.open_time,
            recent_low_bar_close_time=recent_low_bar.close_time,
            recent_high=filter_result.sell.recent_extreme,
            recent_high_bar_open_time=recent_high_bar.open_time,
            recent_high_bar_close_time=recent_high_bar.close_time,
            daily_session=daily_session(completed_daily[-1]),
            daily_reference_sessions=tuple(
                daily_session(bar) for bar in completed_daily[-2:]
            ),
            atr_sessions=tuple(daily_session(bar) for bar in completed_daily),
            d1_context_eligibility_time=latest.close_time,
            atr_period=5,
            atr_value=atr,
            buffer_percentage=Decimal("0.05"),
            buffer_value=filter_result.activation_buffer,
            daily_low=filter_result.daily_raw_low,
            daily_high=filter_result.daily_raw_high,
            buy_threshold=filter_result.buy.daily_level,
            sell_threshold=filter_result.sell.daily_level,
            buy_comparison=FilterAuditBuyComparison(
                recent_low=filter_result.buy.recent_extreme,
                operator="<=",
                buy_threshold=filter_result.buy.daily_level,
                matched=filter_result.buy.matched,
            ),
            sell_comparison=FilterAuditSellComparison(
                recent_high=filter_result.sell.recent_extreme,
                operator=">=",
                sell_threshold=filter_result.sell.daily_level,
                matched=filter_result.sell.matched,
            ),
            final_classification=_filter_classification(
                filter_result.buy.matched,
                filter_result.sell.matched,
            ),
            source_provider=instrument.provider,
            construction_profile=PROFILE_ID,
            canonical_timezone=CANONICAL_TIMEZONE,
            display_timezone="Broker Time",
            daily_session_authority="17:00 America/New_York",
            selected_bars=tuple(selected_audit_bars),
        )
        if request.strategy_version == CURRENT_D1_FILTER_V2:
            audit = None
        stop_atr_distance, point_adjustment = calculate_level_distances(atr, instrument)
        buy_candidate = (
            calculate_candidate_levels(
                Direction.BUY,
                latest,
                filter_result.buy.recent_extreme,
                filter_result.sell.recent_extreme,
                stop_atr_distance,
                point_adjustment,
                instrument,
            )
            if confirmed_buy
            else None
        )
        sell_candidate = (
            calculate_candidate_levels(
                Direction.SELL,
                latest,
                filter_result.buy.recent_extreme,
                filter_result.sell.recent_extreme,
                stop_atr_distance,
                point_adjustment,
                instrument,
            )
            if confirmed_sell
            else None
        )
        if confirmed_buy and buy_candidate is None:
            classification_reason_codes += ("BUY_INVALID_R",)
        if confirmed_sell and sell_candidate is None:
            classification_reason_codes += ("SELL_INVALID_R",)

        activation_buffer = filter_result.activation_buffer
        indicators = IndicatorResult(
            sma10=sma10,
            sma20=sma20,
            atr_d1_wilder_5=atr,
            activation_buffer=activation_buffer,
            stop_atr_distance=stop_atr_distance,
            point_adjustment=point_adjustment,
            daily_raw_low=filter_result.daily_raw_low,
            daily_raw_high=filter_result.daily_raw_high,
            daily_buy_level=filter_result.buy.daily_level,
            daily_sell_level=filter_result.sell.daily_level,
            recent_low_21=filter_result.buy.recent_extreme,
            recent_high_21=filter_result.sell.recent_extreme,
        )
        all_reason_codes = (
            "DATA_READY",
            filter_result.buy.reason_code,
            filter_result.sell.reason_code,
            *signal_result.buy.reason_codes,
            *signal_result.sell.reason_codes,
            *classification_reason_codes,
            *(buy_candidate.reason_codes if buy_candidate is not None else ()),
            *(sell_candidate.reason_codes if sell_candidate is not None else ()),
        )
        return StrategyEvaluation(
            case_id=request.case_id,
            strategy_id=request.strategy_id,
            instrument_id=instrument.instrument_id,
            timeframe=request.timeframe,
            evaluation_time=request.evaluation_time,
            data_status="READY",
            issues=(),
            reason_codes=all_reason_codes,
            bars=BarsUsed(
                signal_completed_count=len(completed_signal),
                daily_completed_count=len(completed_daily),
                excluded_incomplete_count=excluded,
                signal_bar_close_time=latest.close_time,
                daily_endpoint_close_time=completed_daily[-1].close_time,
            ),
            classification=classification,
            indicators=indicators,
            filters=filter_result,
            signals=signal_result,
            buy_candidate=buy_candidate,
            sell_candidate=sell_candidate,
            signal_bar=latest,
            filter_audit=audit,
            strategy_version=request.strategy_version,
            daily_filter_snapshot_id=(
                snapshot.snapshot_id if snapshot is not None else None
            ),
        )
