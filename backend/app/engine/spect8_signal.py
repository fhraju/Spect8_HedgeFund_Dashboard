from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from ..domain import Bar, Direction
from .models import PivotResult, Spect8SignalResult, TechnicalSideResult

PIVOT_LOOKBACK = 10
STRUCTURAL_LOOKBACK = 20


def _pivot(
    completed_bars: Sequence[Bar],
    direction: Direction,
) -> PivotResult:
    if len(completed_bars) < PIVOT_LOOKBACK + STRUCTURAL_LOOKBACK:
        raise ValueError("pivot evaluation requires 30 completed signal bars")

    newest_first = tuple(reversed(completed_bars))
    recent = newest_first[:PIVOT_LOOKBACK]
    extreme = (
        min(bar.low for bar in recent)
        if direction is Direction.BUY
        else max(bar.high for bar in recent)
    )
    attribute = "low" if direction is Direction.BUY else "high"
    pivot_index = next(
        index
        for index, bar in enumerate(recent)
        if getattr(bar, attribute) == extreme
    )
    pivot = recent[pivot_index]
    structural_window = newest_first[
        pivot_index : pivot_index + STRUCTURAL_LOOKBACK
    ]
    structural_extreme = (
        min(bar.low for bar in structural_window)
        if direction is Direction.BUY
        else max(bar.high for bar in structural_window)
    )
    passed = (
        structural_extreme >= extreme
        if direction is Direction.BUY
        else structural_extreme <= extreme
    )
    prefix = direction.value
    return PivotResult(
        direction=direction,
        shift=pivot_index + 1,
        open_time=pivot.open_time,
        price=extreme,
        structural_window_extreme=structural_extreme,
        structural_passed=passed,
        reason_code=(
            f"{prefix}_STRUCTURAL_PIVOT_MATCHED"
            if passed
            else f"{prefix}_STRUCTURAL_PIVOT_NOT_MATCHED"
        ),
    )


def evaluate_spect8_signal(
    completed_bars: Sequence[Bar],
    sma10: Decimal,
    sma20: Decimal,
) -> Spect8SignalResult:
    if not completed_bars:
        raise ValueError("signal evaluation requires completed signal bars")
    if any(not bar.is_complete for bar in completed_bars):
        raise ValueError("signal input contains an incomplete bar")

    latest = completed_bars[-1]
    buy_sma = (
        latest.close >= sma10
        and latest.low <= sma10
        and latest.close >= sma20
        and latest.low <= sma20
    )
    sell_sma = (
        latest.close <= sma10
        and latest.high >= sma10
        and latest.close <= sma20
        and latest.high >= sma20
    )
    buy_pivot = _pivot(completed_bars, Direction.BUY)
    sell_pivot = _pivot(completed_bars, Direction.SELL)

    def side(
        direction: Direction,
        sma_rejection: bool,
        pivot: PivotResult,
    ) -> TechnicalSideResult:
        technical_signal = sma_rejection and pivot.structural_passed
        prefix = direction.value
        return TechnicalSideResult(
            direction=direction,
            sma_rejection=sma_rejection,
            structural_pivot=pivot.structural_passed,
            technical_signal=technical_signal,
            pivot=pivot,
            reason_codes=(
                (
                    f"{prefix}_SMA_REJECTION_MATCHED"
                    if sma_rejection
                    else f"{prefix}_SMA_REJECTION_NOT_MATCHED"
                ),
                pivot.reason_code,
                (
                    f"TECHNICAL_{prefix}_SIGNAL_MATCHED"
                    if technical_signal
                    else f"TECHNICAL_{prefix}_SIGNAL_NOT_MATCHED"
                ),
            ),
        )

    return Spect8SignalResult(
        buy=side(Direction.BUY, buy_sma, buy_pivot),
        sell=side(Direction.SELL, sell_sma, sell_pivot),
        sma10=sma10,
        sma20=sma20,
    )
