from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from ..domain import Bar, Direction
from .indicators import completed_extremes
from .models import FilterSideResult, MicroDailyFilterResult

ACTIVATION_MULTIPLIER = Decimal("0.05")
RECENT_EXTREME_PERIOD = 21


def evaluate_micro_daily_filter(
    signal_bars: Sequence[Bar],
    daily_bars: Sequence[Bar],
    atr_d1_wilder_5: Decimal,
) -> MicroDailyFilterResult:
    if len(daily_bars) < 2:
        raise ValueError("Daily Filter requires two completed D1 reference bars")
    if any(not bar.is_complete for bar in daily_bars[-2:]):
        raise ValueError("Daily Filter input contains an incomplete D1 bar")

    activation_buffer = atr_d1_wilder_5 * ACTIVATION_MULTIPLIER
    daily_reference = daily_bars[-2:]
    daily_raw_low = min(bar.low for bar in daily_reference)
    daily_raw_high = max(bar.high for bar in daily_reference)
    daily_buy_level = daily_raw_low + activation_buffer
    daily_sell_level = daily_raw_high - activation_buffer
    recent_low, recent_high = completed_extremes(
        signal_bars, RECENT_EXTREME_PERIOD
    )
    buy_matched = recent_low <= daily_buy_level
    sell_matched = recent_high >= daily_sell_level

    return MicroDailyFilterResult(
        buy=FilterSideResult(
            direction=Direction.BUY,
            matched=buy_matched,
            recent_extreme=recent_low,
            daily_level=daily_buy_level,
            reason_code=(
                "BUY_FILTER_MATCHED"
                if buy_matched
                else "BUY_FILTER_NOT_MATCHED"
            ),
        ),
        sell=FilterSideResult(
            direction=Direction.SELL,
            matched=sell_matched,
            recent_extreme=recent_high,
            daily_level=daily_sell_level,
            reason_code=(
                "SELL_FILTER_MATCHED"
                if sell_matched
                else "SELL_FILTER_NOT_MATCHED"
            ),
        ),
        daily_raw_low=daily_raw_low,
        daily_raw_high=daily_raw_high,
        activation_buffer=activation_buffer,
    )
