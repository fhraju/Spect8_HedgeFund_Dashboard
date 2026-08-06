from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Sequence

from ..domain import Bar, Timeframe
from .session_boundaries import (
    NEW_YORK,
    NEW_YORK_SESSION_TIMEZONE,
    completed_forex_session_dates,
    new_york_session_bounds,
)
from .forex_profile import broker_wall_time
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID


class DailyAggregationIssueCode(StrEnum):
    MIXED_IDENTITY = "DAILY_SOURCE_MIXED_IDENTITY"
    WRONG_TIMEFRAME = "DAILY_SOURCE_WRONG_TIMEFRAME"
    INCOMPLETE_SOURCE = "DAILY_SOURCE_INCOMPLETE"
    OUT_OF_ORDER = "DAILY_SOURCE_OUT_OF_ORDER"
    DUPLICATE = "DAILY_SOURCE_DUPLICATE"
    OVERLAP = "DAILY_SOURCE_OVERLAP"
    FUTURE = "DAILY_SOURCE_FUTURE"
    INVALID_INTERVAL = "DAILY_SOURCE_INVALID_INTERVAL"
    BOUNDARY_CROSSING = "DAILY_SOURCE_BOUNDARY_CROSSING"
    MISSING_COVERAGE = "DAILY_SESSION_MISSING_COVERAGE"


@dataclass(frozen=True, slots=True)
class DailyAggregationIssue:
    code: DailyAggregationIssueCode
    detail: str
    session_start: datetime | None = None
    session_end: datetime | None = None


@dataclass(frozen=True, slots=True)
class AggregatedDailyBar:
    bar: Bar
    source_bar_count: int


@dataclass(frozen=True, slots=True)
class DailyAggregationResult:
    sessions: tuple[AggregatedDailyBar, ...]
    issues: tuple[DailyAggregationIssue, ...]

    @property
    def bars(self) -> tuple[Bar, ...]:
        return tuple(session.bar for session in self.sessions)


class NewYorkDailyAggregator:
    """Build canonical 17:00 America/New_York D1 bars from canonical H1."""

    def aggregate(
        self,
        source_bars: Sequence[Bar],
        *,
        as_of: datetime,
    ) -> DailyAggregationResult:
        if as_of.tzinfo is None:
            raise ValueError("aggregation as_of must be timezone-aware")
        if not source_bars:
            return DailyAggregationResult((), ())

        issues = self._source_issues(source_bars, as_of)
        if issues:
            return DailyAggregationResult((), tuple(issues))

        bars = tuple(source_bars)
        sessions: list[AggregatedDailyBar] = []
        session_issues: list[DailyAggregationIssue] = []
        for session_date in completed_forex_session_dates(
            bars[0].open_time,
            bars[-1].close_time,
            as_of,
        ):
            start, end = new_york_session_bounds(session_date)
            crossing = next(
                (
                    bar
                    for bar in bars
                    if (
                        bar.open_time < start < bar.close_time
                        or bar.open_time < end < bar.close_time
                    )
                ),
                None,
            )
            if crossing is not None:
                session_issues.append(
                    DailyAggregationIssue(
                        DailyAggregationIssueCode.BOUNDARY_CROSSING,
                        "H1 candle crosses a New York Daily boundary.",
                        start,
                        end,
                    )
                )
                continue
            members = tuple(
                bar for bar in bars if start <= bar.open_time and bar.close_time <= end
            )
            coverage_issue = self._coverage_issue(members, start, end)
            if coverage_issue is not None:
                session_issues.append(coverage_issue)
                continue
            sessions.append(
                AggregatedDailyBar(
                    bar=self._aggregate_session(members, start, end),
                    source_bar_count=len(members),
                )
            )
        return DailyAggregationResult(
            tuple(sessions),
            tuple(session_issues),
        )

    @staticmethod
    def _source_issues(
        bars: Sequence[Bar],
        as_of: datetime,
    ) -> list[DailyAggregationIssue]:
        issues: list[DailyAggregationIssue] = []
        identity = (bars[0].provider, bars[0].instrument_id)
        if any((bar.provider, bar.instrument_id) != identity for bar in bars):
            issues.append(
                DailyAggregationIssue(
                    DailyAggregationIssueCode.MIXED_IDENTITY,
                    "H1 aggregation input contains mixed provider/instrument identity.",
                )
            )
        if any(bar.timeframe is not Timeframe.H1 for bar in bars):
            issues.append(
                DailyAggregationIssue(
                    DailyAggregationIssueCode.WRONG_TIMEFRAME,
                    "New York D1 aggregation requires H1 source bars.",
                )
            )
        if any(not bar.is_complete for bar in bars):
            issues.append(
                DailyAggregationIssue(
                    DailyAggregationIssueCode.INCOMPLETE_SOURCE,
                    "H1 aggregation input contains a forming candle.",
                )
            )
        if any(
            current.open_time <= previous.open_time
            for previous, current in zip(bars, bars[1:])
        ):
            issues.append(
                DailyAggregationIssue(
                    DailyAggregationIssueCode.OUT_OF_ORDER,
                    "H1 aggregation input is not in strict chronological order.",
                )
            )
        opens = [bar.open_time for bar in bars]
        closes = [bar.close_time for bar in bars]
        if len(opens) != len(set(opens)) or len(closes) != len(set(closes)):
            issues.append(
                DailyAggregationIssue(
                    DailyAggregationIssueCode.DUPLICATE,
                    "H1 aggregation input contains duplicate boundaries.",
                )
            )
        if any(
            current.open_time < previous.close_time
            for previous, current in zip(bars, bars[1:])
        ):
            issues.append(
                DailyAggregationIssue(
                    DailyAggregationIssueCode.OVERLAP,
                    "H1 aggregation input contains overlapping candles.",
                )
            )
        if any(bar.close_time > as_of for bar in bars):
            issues.append(
                DailyAggregationIssue(
                    DailyAggregationIssueCode.FUTURE,
                    "H1 aggregation input contains a future candle.",
                )
            )
        if any(bar.close_time - bar.open_time != timedelta(hours=1) for bar in bars):
            issues.append(
                DailyAggregationIssue(
                    DailyAggregationIssueCode.INVALID_INTERVAL,
                    "H1 aggregation input contains a non-hourly candle.",
                )
            )
        return issues

    @staticmethod
    def _coverage_issue(
        members: tuple[Bar, ...],
        start: datetime,
        end: datetime,
    ) -> DailyAggregationIssue | None:
        expected = int((end - start).total_seconds() // 3600)
        complete = (
            len(members) == expected
            and bool(members)
            and members[0].open_time == start
            and members[-1].close_time == end
            and all(
                current.open_time == previous.close_time
                for previous, current in zip(members, members[1:])
            )
        )
        if complete:
            return None
        return DailyAggregationIssue(
            DailyAggregationIssueCode.MISSING_COVERAGE,
            (
                f"New York session expected {expected} contiguous H1 bars "
                f"but received {len(members)}."
            ),
            start,
            end,
        )

    @staticmethod
    def _aggregate_session(
        members: tuple[Bar, ...],
        start: datetime,
        end: datetime,
    ) -> Bar:
        volume: Decimal | None = None
        if all(bar.volume is not None for bar in members):
            volume = sum(
                (bar.volume for bar in members if bar.volume is not None),
                Decimal("0"),
            )
        first = members[0]
        last = members[-1]
        iso_start = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        iso_end = end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return Bar(
            instrument_id=first.instrument_id,
            timeframe=Timeframe.D1,
            open_time=start,
            close_time=end,
            open=first.open,
            high=max(bar.high for bar in members),
            low=min(bar.low for bar in members),
            close=last.close,
            provider=first.provider,
            is_complete=True,
            volume=volume,
            session_timezone=NEW_YORK_SESSION_TIMEZONE,
            raw_provider_symbol=first.raw_provider_symbol,
            raw_open_time=iso_start,
            raw_close_time=iso_end,
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
            expected_closure_before=first.expected_closure_before,
            ingestion_run_id=first.ingestion_run_id,
            created_at=max((bar.created_at or bar.close_time for bar in members)),
            session_identifier=end.astimezone(NEW_YORK).date().isoformat(),
            session_open_broker_time=broker_wall_time(start).isoformat(),
            session_close_broker_time=broker_wall_time(end).isoformat(),
        )


class ActualDataNewYorkDailyAggregator:
    """Build NY-17 D1 bars from actual sparse exchange H1 data only."""

    def aggregate(
        self,
        source_bars: Sequence[Bar],
        *,
        as_of: datetime,
    ) -> DailyAggregationResult:
        if as_of.tzinfo is None:
            raise ValueError("aggregation as_of must be timezone-aware")
        if not source_bars:
            return DailyAggregationResult((), ())
        bars = tuple(sorted(source_bars, key=lambda item: item.open_time))
        issues = NewYorkDailyAggregator._source_issues(bars, as_of)
        if issues:
            return DailyAggregationResult((), tuple(issues))
        grouped: dict[date, list[Bar]] = {}
        for bar in bars:
            local = bar.open_time.astimezone(NEW_YORK)
            session_date = local.date() if local.hour < 17 else local.date() + timedelta(days=1)
            grouped.setdefault(session_date, []).append(bar)
        sessions: list[AggregatedDailyBar] = []
        for session_date in sorted(grouped):
            start, end = new_york_session_bounds(session_date)
            if end > as_of.astimezone(timezone.utc):
                continue
            members = tuple(
                bar for bar in grouped[session_date]
                if start <= bar.open_time and bar.close_time <= end
            )
            if not members:
                continue
            sessions.append(
                AggregatedDailyBar(
                    bar=NewYorkDailyAggregator._aggregate_session(members, start, end),
                    source_bar_count=len(members),
                )
            )
        return DailyAggregationResult(tuple(sessions), ())
