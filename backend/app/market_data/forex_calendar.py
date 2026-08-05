from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from ..domain import Bar
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from .session_boundaries import NEW_YORK


@dataclass(frozen=True, slots=True)
class ConfiguredClosure:
    start_utc: datetime
    end_utc: datetime
    label: str
    shortened_session: bool = False


class ForexMarketCalendar:
    """Central expected-closure authority for the canonical V1 profile."""

    def __init__(self, closures: Sequence[ConfiguredClosure] = ()) -> None:
        self.closures = tuple(closures)
        self.profile_id = PROFILE_ID

    def configured_closure(
        self, start: datetime, end: datetime
    ) -> ConfiguredClosure | None:
        return next(
            (
                closure
                for closure in self.closures
                if closure.start_utc <= start and end <= closure.end_utc
            ),
            None,
        )

    def is_expected_weekend_closure(self, previous: Bar, current: Bar) -> bool:
        previous_close = previous.close_time.astimezone(NEW_YORK)
        current_open = current.open_time.astimezone(NEW_YORK)
        return (
            previous_close.weekday() == 4
            and previous_close.hour == 17
            and current_open.weekday() == 6
            and current_open.hour == 17
        )

    def is_expected_closure(self, previous: Bar, current: Bar) -> bool:
        return (
            self.is_expected_weekend_closure(previous, current)
            or self.configured_closure(previous.close_time, current.open_time)
            is not None
        )
