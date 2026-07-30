from __future__ import annotations

from decimal import Decimal

from ..domain import Bar, Direction
from .models import CandidateResult, InstrumentMetadata
from .position_sizing import calculate_position_size

STOP_ATR_MULTIPLIER = Decimal("0.35")
POINT_ADJUSTMENT_COUNT = Decimal("10")
TARGET_R_MULTIPLE = Decimal("3")
ZERO = Decimal("0")


def calculate_level_distances(
    atr_d1_wilder_5: Decimal,
    instrument: InstrumentMetadata,
) -> tuple[Decimal, Decimal]:
    return (
        atr_d1_wilder_5 * STOP_ATR_MULTIPLIER,
        instrument.point_size * POINT_ADJUSTMENT_COUNT,
    )


def calculate_candidate_levels(
    direction: Direction,
    signal_bar: Bar,
    recent_low_21: Decimal,
    recent_high_21: Decimal,
    stop_atr_distance: Decimal,
    point_adjustment: Decimal,
    instrument: InstrumentMetadata,
) -> CandidateResult | None:
    if not signal_bar.is_complete:
        raise ValueError("level calculation requires a completed signal bar")
    minimum_stop_distance = (
        (instrument.minimum_stop_distance_points or ZERO)
        * instrument.point_size
    )
    entry = signal_bar.close

    if direction is Direction.BUY:
        raw_stop = recent_low_21 - stop_atr_distance - point_adjustment
        displayed_stop = min(raw_stop, entry - minimum_stop_distance)
        risk_distance = entry - displayed_stop
        target = entry + (TARGET_R_MULTIPLE * risk_distance)
    else:
        raw_stop = recent_high_21 + stop_atr_distance + point_adjustment
        displayed_stop = max(raw_stop, entry + minimum_stop_distance)
        risk_distance = displayed_stop - entry
        target = entry - (TARGET_R_MULTIPLE * risk_distance)

    if risk_distance <= ZERO:
        return None

    position_size = calculate_position_size(instrument, risk_distance)
    stop_reason = (
        "PROVIDER_STOP_ADJUSTED"
        if displayed_stop != raw_stop
        else "RAW_STRATEGY_STOP_VALID"
    )
    return CandidateResult(
        direction=direction,
        entry_reference=entry,
        raw_strategy_stop=raw_stop,
        provider_adjusted_stop=displayed_stop,
        risk_distance=risk_distance,
        target_3r=target,
        position_size=position_size,
        reason_codes=(
            "LEVELS_VALID",
            stop_reason,
            position_size.reason_code,
        ),
    )
