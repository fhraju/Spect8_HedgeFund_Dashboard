from __future__ import annotations

from datetime import datetime, timezone

from .models import CanonicalInstrument, SessionProfileKind
from .us_equity_calendar import latest_expected_etf_h1_close


def expected_latest_h1_close(
    instrument: CanonicalInstrument,
    as_of: datetime,
) -> datetime | None:
    if as_of.tzinfo is None:
        raise ValueError("polling as_of must be timezone-aware")
    now = as_of.astimezone(timezone.utc)
    if instrument.session_profile is SessionProfileKind.US_EQUITY_REGULAR:
        return latest_expected_etf_h1_close(now)
    # Existing Forex/XAU and crypto feeds expose the last completed candle at
    # the current whole-hour boundary. Provider-level frozen weekend handling
    # remains authoritative after this conservative freshness preflight.
    return now.replace(minute=0, second=0, microsecond=0)


def instrument_needs_poll(
    instrument: CanonicalInstrument,
    *,
    latest_completed_close: datetime | None,
    as_of: datetime,
) -> bool:
    expected = expected_latest_h1_close(instrument, as_of)
    if expected is None:
        return False
    return latest_completed_close is None or latest_completed_close < expected
