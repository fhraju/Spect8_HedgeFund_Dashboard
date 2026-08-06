from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .session_boundaries import is_expected_forex_weekend_gap


class GapClassification(StrEnum):
    CONTIGUOUS = "CONTIGUOUS"
    EXPECTED_MARKET_CLOSURE = "EXPECTED_MARKET_CLOSURE"
    UNEXPECTED_MISSING_DATA = "UNEXPECTED_MISSING_DATA"


@dataclass(frozen=True, slots=True)
class MarketSessionProfile:
    instrument_id: str
    continuous_forex_weekday: bool
    expected_closures_utc: tuple[tuple[datetime, datetime], ...] = ()


def classify_h1_gap(
    previous_open: datetime,
    current_open: datetime,
    profile: MarketSessionProfile,
) -> GapClassification:
    """Classify a gap without creating or forward-filling any candle."""

    step = timedelta(hours=1)
    if current_open - previous_open == step:
        return GapClassification.CONTIGUOUS
    if current_open <= previous_open:
        return GapClassification.UNEXPECTED_MISSING_DATA
    if profile.continuous_forex_weekday:
        return (
            GapClassification.EXPECTED_MARKET_CLOSURE
            if is_expected_forex_weekend_gap(previous_open + step, current_open)
            else GapClassification.UNEXPECTED_MISSING_DATA
        )
    missing = []
    cursor = previous_open + step
    while cursor < current_open:
        missing.append(cursor)
        cursor += step
    covered = all(
        any(start <= value < end for start, end in profile.expected_closures_utc)
        for value in missing
    )
    return (
        GapClassification.EXPECTED_MARKET_CLOSURE
        if missing and covered
        else GapClassification.UNEXPECTED_MISSING_DATA
    )
