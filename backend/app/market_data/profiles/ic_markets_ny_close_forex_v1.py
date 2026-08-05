from __future__ import annotations

from dataclasses import dataclass
from datetime import time

PROFILE_ID = "IC_MARKETS_NY_CLOSE_FOREX_V1"


@dataclass(frozen=True, slots=True)
class CanonicalForexProfile:
    profile_id: str = PROFILE_ID
    canonical_timezone: str = "UTC"
    display_timezone: str = "IC Markets Broker Time"
    daily_session_timezone: str = "America/New_York"
    daily_close: time = time(17, 0)
    broker_standard_utc_offset_hours: int = 2
    broker_daylight_utc_offset_hours: int = 3
    broker_to_new_york_wall_hours: int = 7
    base_timeframe: str = "H1"
    h1_open_minute: int = 0
    h4_open_hours: tuple[int, ...] = (0, 4, 8, 12, 16, 20)
    weekend_open_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    forming_bar_policy: str = "EXCLUDE"
    duplicate_bar_policy: str = "REJECT"
    unexpected_gap_policy: str = "QUARANTINE"
    incomplete_aggregation_policy: str = "QUARANTINE"
    price_gap_policy: str = "PRESERVE"
    volume_policy: str = "SUM_IF_ALL_SOURCE_VOLUMES_PRESENT_ELSE_NULL"
    native_h4_policy: str = "COMPARISON_ONLY"
    native_d1_policy: str = "COMPARISON_ONLY"
    synthetic_policy: str = "FORBIDDEN_FOR_PROVIDER_DERIVED_FOREX"
    forward_fill_policy: str = "FORBIDDEN"
    timestamp_semantics: str = "OPEN_INCLUSIVE_CLOSE_EXCLUSIVE"


PROFILE = CanonicalForexProfile()
