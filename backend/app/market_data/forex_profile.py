from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Sequence

from ..domain import Bar, Timeframe
from .session_boundaries import NEW_YORK, NEW_YORK_SESSION_TIMEZONE
from .forex_calendar import ForexMarketCalendar
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE, PROFILE_ID

BROKER_DISPLAY_LABEL = PROFILE.display_timezone
CANONICAL_TIMEZONE = "UTC"
BROKER_TO_NEW_YORK_WALL_HOURS = PROFILE.broker_to_new_york_wall_hours
MARKET_CALENDAR = ForexMarketCalendar()


class GapType(StrEnum):
    EXPECTED_MARKET_CLOSURE = "EXPECTED_MARKET_CLOSURE"
    UNEXPECTED_DATA_GAP = "UNEXPECTED_DATA_GAP"
    PROVIDER_PRICE_GAP = "PROVIDER_PRICE_GAP"


class H4AggregationIssueCode(StrEnum):
    INVALID_SOURCE = "H4_SOURCE_INVALID"
    INCOMPLETE_BUCKET = "H4_BUCKET_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class MarketGap:
    gap_type: GapType
    previous_close: datetime
    next_open: datetime
    detail: str


@dataclass(frozen=True, slots=True)
class H4AggregationIssue:
    code: H4AggregationIssueCode
    bucket_open: datetime
    bucket_close: datetime
    source_bar_count: int
    detail: str


@dataclass(frozen=True, slots=True)
class AggregatedH4Bar:
    bar: Bar
    source_bars: tuple[Bar, ...]


@dataclass(frozen=True, slots=True)
class H4AggregationResult:
    buckets: tuple[AggregatedH4Bar, ...]
    issues: tuple[H4AggregationIssue, ...]

    @property
    def bars(self) -> tuple[Bar, ...]:
        return tuple(bucket.bar for bucket in self.buckets)


def broker_wall_time(instant: datetime) -> datetime:
    """Return IC Markets-style broker wall time using New York DST rules."""

    if instant.tzinfo is None:
        raise ValueError("canonical timestamp must be timezone-aware")
    new_york_wall = instant.astimezone(NEW_YORK).replace(tzinfo=None)
    return new_york_wall + timedelta(hours=BROKER_TO_NEW_YORK_WALL_HOURS)


def broker_wall_to_utc(value: datetime) -> datetime:
    """Convert an IC Markets broker wall label into canonical UTC."""

    wall = value.replace(tzinfo=None)
    new_york_wall = wall - timedelta(hours=BROKER_TO_NEW_YORK_WALL_HOURS)
    return new_york_wall.replace(tzinfo=NEW_YORK).astimezone(timezone.utc)


def broker_utc_offset(instant: datetime) -> timedelta:
    return broker_wall_time(instant) - instant.astimezone(timezone.utc).replace(
        tzinfo=None
    )


def is_valid_market_h1(bar: Bar) -> bool:
    if bar.timeframe is not Timeframe.H1 or not bar.is_complete:
        return False
    if bar.close_time - bar.open_time != timedelta(hours=1):
        return False
    wall = broker_wall_time(bar.open_time)
    return wall.weekday() < 5 and wall.minute == 0 and wall.second == 0


def market_h1_bars(bars: Sequence[Bar]) -> tuple[Bar, ...]:
    selected: dict[tuple[str, str, datetime], Bar] = {}
    for bar in sorted(bars, key=lambda item: item.close_time):
        if is_valid_market_h1(bar):
            selected.setdefault((bar.provider, bar.instrument_id, bar.close_time), bar)
    canonical: list[Bar] = []
    for bar in selected.values():
        gap = classify_market_gap(canonical[-1], bar) if canonical else None
        canonical.append(
            replace(
                bar,
                expected_closure_before=(
                    bar.expected_closure_before
                    or (
                        gap is not None
                        and gap.gap_type is GapType.EXPECTED_MARKET_CLOSURE
                    )
                ),
            )
        )
    return tuple(canonical)


def is_valid_market_h4(bar: Bar) -> bool:
    if bar.timeframe is not Timeframe.H4 or not bar.is_complete:
        return False
    if bar.close_time - bar.open_time != timedelta(hours=4):
        return False
    wall = broker_wall_time(bar.open_time)
    return (
        wall.weekday() < 5
        and wall.hour in (0, 4, 8, 12, 16, 20)
        and wall.minute == 0
        and wall.second == 0
    )


def is_broker_h4_close(instant: datetime) -> bool:
    if instant.tzinfo is None:
        raise ValueError("H4 close timestamp must be timezone-aware")
    wall = broker_wall_time(instant)
    return (
        wall.hour in (0, 4, 8, 12, 16, 20)
        and wall.minute == 0
        and wall.second == 0
        and wall.microsecond == 0
    )


def classify_market_gap(previous: Bar, current: Bar) -> MarketGap | None:
    if current.open_time == previous.close_time:
        if current.open != previous.close:
            return MarketGap(
                GapType.PROVIDER_PRICE_GAP,
                previous.close_time,
                current.open_time,
                "Consecutive source bars retain a provider price discontinuity.",
            )
        return None
    if MARKET_CALENDAR.is_expected_closure(previous, current):
        return MarketGap(
            GapType.EXPECTED_MARKET_CLOSURE,
            previous.close_time,
            current.open_time,
            "Standard Friday 17:00 to Sunday 17:00 America/New_York closure.",
        )
    return MarketGap(
        GapType.UNEXPECTED_DATA_GAP,
        previous.close_time,
        current.open_time,
        "Missing source interval while the broker market profile is open.",
    )


class BrokerAlignedH4Aggregator:
    """Build complete IC Markets broker-time H4 candles from actual H1 bars."""

    def aggregate(
        self,
        source_bars: Sequence[Bar],
        *,
        as_of: datetime,
    ) -> H4AggregationResult:
        if as_of.tzinfo is None:
            raise ValueError("H4 aggregation as_of must be timezone-aware")
        eligible = market_h1_bars(source_bars)
        grouped: dict[datetime, list[Bar]] = {}
        for bar in eligible:
            wall = broker_wall_time(bar.open_time)
            bucket_wall = wall.replace(
                hour=(wall.hour // 4) * 4,
                minute=0,
                second=0,
                microsecond=0,
            )
            grouped.setdefault(bucket_wall, []).append(bar)

        buckets: list[AggregatedH4Bar] = []
        issues: list[H4AggregationIssue] = []
        for bucket_wall in sorted(grouped):
            bucket_open = broker_wall_to_utc(bucket_wall)
            bucket_close = broker_wall_to_utc(bucket_wall + timedelta(hours=4))
            if bucket_close > as_of.astimezone(timezone.utc):
                continue
            members = tuple(sorted(grouped[bucket_wall], key=lambda bar: bar.open_time))
            expected_opens = tuple(
                broker_wall_to_utc(bucket_wall + timedelta(hours=offset))
                for offset in range(4)
            )
            complete = (
                len(members) == 4
                and tuple(bar.open_time for bar in members) == expected_opens
                and members[-1].close_time == bucket_close
            )
            if not complete:
                issues.append(
                    H4AggregationIssue(
                        H4AggregationIssueCode.INCOMPLETE_BUCKET,
                        bucket_open,
                        bucket_close,
                        len(members),
                        "Broker-time H4 bucket requires four contiguous H1 bars.",
                    )
                )
                continue
            buckets.append(
                AggregatedH4Bar(
                    bar=self._bar(
                        members,
                        bucket_open,
                        bucket_close,
                        expected_closure_before=(
                            (gap := self._previous_gap(eligible, members[0]))
                            is not None
                            and gap.gap_type is GapType.EXPECTED_MARKET_CLOSURE
                        ),
                    ),
                    source_bars=members,
                )
            )
        return H4AggregationResult(tuple(buckets), tuple(issues))

    @staticmethod
    def _bar(
        members: tuple[Bar, ...],
        open_time: datetime,
        close_time: datetime,
        expected_closure_before: bool,
    ) -> Bar:
        first = members[0]
        last = members[-1]
        volume: Decimal | None = None
        if all(bar.volume is not None for bar in members):
            volume = sum(
                (bar.volume for bar in members if bar.volume is not None),
                Decimal("0"),
            )
        return Bar(
            instrument_id=first.instrument_id,
            timeframe=Timeframe.H4,
            open_time=open_time,
            close_time=close_time,
            open=first.open,
            high=max(bar.high for bar in members),
            low=min(bar.low for bar in members),
            close=last.close,
            provider=first.provider,
            is_complete=True,
            volume=volume,
            session_timezone=NEW_YORK_SESSION_TIMEZONE,
            raw_provider_symbol=first.raw_provider_symbol,
            raw_open_time=open_time.isoformat().replace("+00:00", "Z"),
            raw_close_time=close_time.isoformat().replace("+00:00", "Z"),
            raw_open=str(first.open),
            raw_high=str(max(bar.high for bar in members)),
            raw_low=str(min(bar.low for bar in members)),
            raw_close=str(last.close),
            synthetic=all(bar.synthetic for bar in members),
            quality_status="VALID",
            construction_profile_version=(
                PROFILE_ID
                if all(
                    bar.construction_profile_version == PROFILE_ID and not bar.synthetic
                    for bar in members
                )
                else first.construction_profile_version
            ),
            provider_adapter_version=first.provider_adapter_version,
            source_timeframe=Timeframe.H1,
            source_candle_ids=tuple(
                source_id
                for bar in members
                for source_id in (
                    bar.source_candle_ids
                    or (
                        f"{bar.provider}:{bar.instrument_id}:H1:"
                        f"{bar.close_time.isoformat()}",
                    )
                )
            ),
            forward_filled=False,
            expected_closure_before=expected_closure_before,
            ingestion_run_id=first.ingestion_run_id,
            created_at=max((bar.created_at or bar.close_time for bar in members)),
        )

    @staticmethod
    def _previous_gap(
        eligible: tuple[Bar, ...],
        first_member: Bar,
    ) -> MarketGap | None:
        previous = next(
            (
                bar
                for bar in reversed(eligible)
                if bar.close_time <= first_member.open_time and bar is not first_member
            ),
            None,
        )
        return classify_market_gap(previous, first_member) if previous else None
