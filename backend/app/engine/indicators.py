from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from ..domain import Bar

ZERO = Decimal("0")


class InsufficientHistoryError(ValueError):
    """Raised when a frozen indicator does not have enough completed bars."""


def _require_completed(bars: Sequence[Bar]) -> None:
    if any(not bar.is_complete for bar in bars):
        raise ValueError("indicator input contains an incomplete bar")


def simple_moving_average(bars: Sequence[Bar], period: int) -> Decimal:
    if period <= 0:
        raise ValueError("SMA period must be positive")
    _require_completed(bars)
    if len(bars) < period:
        raise InsufficientHistoryError(
            f"SMA({period}) requires {period} completed bars"
        )
    selected = bars[-period:]
    return sum((bar.close for bar in selected), ZERO) / Decimal(period)


def wilder_atr(bars: Sequence[Bar], period: int) -> Decimal:
    """Wilder ATR seeded from the first ``period`` true ranges.

    One bar before the seed is required to supply the previous close.
    """

    if period <= 0:
        raise ValueError("ATR period must be positive")
    _require_completed(bars)
    if len(bars) < period + 1:
        raise InsufficientHistoryError(
            f"ATR({period}) requires {period + 1} completed bars"
        )

    true_ranges = [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(bars, bars[1:])
    ]
    atr = sum(true_ranges[:period], ZERO) / Decimal(period)
    for true_range in true_ranges[period:]:
        atr = ((atr * Decimal(period - 1)) + true_range) / Decimal(period)
    return atr


def completed_extremes(
    bars: Sequence[Bar],
    period: int,
) -> tuple[Decimal, Decimal]:
    if period <= 0:
        raise ValueError("extreme period must be positive")
    _require_completed(bars)
    if len(bars) < period:
        raise InsufficientHistoryError(
            f"extreme window requires {period} completed bars"
        )
    selected = bars[-period:]
    return (
        min(bar.low for bar in selected),
        max(bar.high for bar in selected),
    )
