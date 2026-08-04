from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NEW_YORK_SESSION_TIMEZONE = "America/New_York"
NEW_YORK = ZoneInfo(NEW_YORK_SESSION_TIMEZONE)
NEW_YORK_CLOSE_TIME = time(17, 0)


def new_york_session_close(session_date: date) -> datetime:
    """Return the UTC instant for 17:00 New York on ``session_date``."""

    local = datetime.combine(
        session_date,
        NEW_YORK_CLOSE_TIME,
        tzinfo=NEW_YORK,
    )
    return local.astimezone(timezone.utc)


def new_york_session_bounds(session_date: date) -> tuple[datetime, datetime]:
    """Return consecutive local 17:00 boundaries converted to UTC."""

    end = new_york_session_close(session_date)
    start = new_york_session_close(session_date - timedelta(days=1))
    return start, end


def completed_forex_session_dates(
    first_source_open: datetime,
    last_source_close: datetime,
    as_of: datetime,
) -> tuple[date, ...]:
    """Enumerate fully observable Monday-Friday New York close sessions."""

    values = (first_source_open, last_source_close, as_of)
    if any(value.tzinfo is None for value in values):
        raise ValueError("session boundary inputs must be timezone-aware")
    first = first_source_open.astimezone(NEW_YORK).date()
    last = last_source_close.astimezone(NEW_YORK).date() + timedelta(days=1)
    dates: list[date] = []
    current = first
    while current <= last:
        if current.weekday() < 5:
            start, end = new_york_session_bounds(current)
            if (
                start >= first_source_open.astimezone(timezone.utc)
                and end <= last_source_close.astimezone(timezone.utc)
                and end < as_of.astimezone(timezone.utc)
            ):
                dates.append(current)
        current += timedelta(days=1)
    return tuple(dates)


def is_expected_forex_weekend_gap(
    expected_next_open: datetime,
    actual_next_open: datetime,
) -> bool:
    """Accept only the standard Friday 17:00 to Sunday 17:00 closure."""

    if expected_next_open.tzinfo is None or actual_next_open.tzinfo is None:
        return False
    expected_local = expected_next_open.astimezone(NEW_YORK)
    actual_local = actual_next_open.astimezone(NEW_YORK)
    return (
        expected_local.weekday() == 4
        and expected_local.time().replace(tzinfo=None) == NEW_YORK_CLOSE_TIME
        and actual_local.weekday() == 6
        and actual_local.time().replace(tzinfo=None) == NEW_YORK_CLOSE_TIME
    )
