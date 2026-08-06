from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo


US_EASTERN = ZoneInfo("America/New_York")
US_ETF_PROFILE_ID = "TWELVE_DATA_US_ETF_RTH_V1"


class EquityH1Disposition(StrEnum):
    VALID_COMPLETED = "VALID_COMPLETED"
    FORMING = "FORMING"
    STRUCTURAL_PARTIAL = "STRUCTURAL_PARTIAL"
    OUTSIDE_REGULAR_SESSION = "OUTSIDE_REGULAR_SESSION"
    MARKET_CLOSED = "MARKET_CLOSED"


@dataclass(frozen=True, slots=True)
class USRegularSession:
    session_date: date
    open_utc: datetime
    close_utc: datetime
    early_close: bool

    @property
    def valid_h1_opens(self) -> tuple[datetime, ...]:
        values: list[datetime] = []
        current = self.open_utc
        while current + timedelta(hours=1) <= self.close_utc:
            values.append(current)
            current += timedelta(hours=1)
        return tuple(values)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    value = date(year, month, 1)
    value += timedelta(days=(weekday - value.weekday()) % 7)
    return value + timedelta(weeks=occurrence - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    first_next = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    value = first_next - timedelta(days=1)
    return value - timedelta(days=(value.weekday() - weekday) % 7)


def _observed(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _easter_sunday(year: int) -> date:
    # Anonymous Gregorian algorithm; deterministic for the supported calendar.
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def us_equity_holidays(year: int) -> frozenset[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    # A following year's New Year holiday can be observed on Dec 31.
    next_new_year = _observed(date(year + 1, 1, 1))
    if next_new_year.year == year:
        holidays.add(next_new_year)
    return frozenset(holidays)


def _early_close_dates(year: int) -> frozenset[date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates = {thanksgiving + timedelta(days=1), date(year, 12, 24)}
    july_fourth = date(year, 7, 4)
    day_before = july_fourth - timedelta(days=1)
    if day_before.weekday() < 5:
        candidates.add(day_before)
    return frozenset(
        value
        for value in candidates
        if value.weekday() < 5 and value not in us_equity_holidays(year)
    )


def us_regular_session(session_date: date) -> USRegularSession | None:
    if session_date.weekday() >= 5 or session_date in us_equity_holidays(session_date.year):
        return None
    early = session_date in _early_close_dates(session_date.year)
    open_local = datetime.combine(session_date, time(9, 30), US_EASTERN)
    close_local = datetime.combine(session_date, time(13 if early else 16), US_EASTERN)
    return USRegularSession(
        session_date=session_date,
        open_utc=open_local.astimezone(timezone.utc),
        close_utc=close_local.astimezone(timezone.utc),
        early_close=early,
    )


def session_for_instant(value: datetime) -> USRegularSession | None:
    if value.tzinfo is None:
        raise ValueError("session instant must be timezone-aware")
    return us_regular_session(value.astimezone(US_EASTERN).date())


def classify_etf_h1_open(
    open_time: datetime,
    *,
    as_of: datetime,
) -> EquityH1Disposition:
    if open_time.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("ETF H1 timestamps must be timezone-aware")
    opened = open_time.astimezone(timezone.utc)
    session = session_for_instant(opened)
    if session is None:
        return EquityH1Disposition.MARKET_CLOSED
    if opened in session.valid_h1_opens:
        return (
            EquityH1Disposition.VALID_COMPLETED
            if opened + timedelta(hours=1) < as_of.astimezone(timezone.utc)
            else EquityH1Disposition.FORMING
        )
    aligned = (
        opened >= session.open_utc
        and (opened - session.open_utc).total_seconds() % 3600 == 0
    )
    if aligned and opened < session.close_utc and opened + timedelta(hours=1) > session.close_utc:
        return EquityH1Disposition.STRUCTURAL_PARTIAL
    return EquityH1Disposition.OUTSIDE_REGULAR_SESSION


def latest_expected_etf_h1_close(as_of: datetime) -> datetime | None:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    current = as_of.astimezone(US_EASTERN).date()
    for offset in range(15):
        session = us_regular_session(current - timedelta(days=offset))
        if session is None:
            continue
        completed = tuple(
            opened + timedelta(hours=1)
            for opened in session.valid_h1_opens
            if opened + timedelta(hours=1) < as_of.astimezone(timezone.utc)
        )
        if completed:
            return completed[-1]
    return None


def next_us_equity_session_date(after: date) -> date:
    value = after + timedelta(days=1)
    for _ in range(15):
        if us_regular_session(value) is not None:
            return value
        value += timedelta(days=1)
    raise ValueError("US equity calendar search exceeded fifteen days")


def is_expected_us_equity_gap(previous_open: datetime, current_open: datetime) -> bool:
    previous_date = previous_open.astimezone(US_EASTERN).date()
    current_date = current_open.astimezone(US_EASTERN).date()
    return (
        previous_date != current_date
        and next_us_equity_session_date(previous_date) == current_date
    )
