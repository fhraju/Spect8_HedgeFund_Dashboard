from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

from ..domain import Bar
from .forex_profile import (
    AggregatedH4Bar,
    BrokerAlignedH4Aggregator,
    H4AggregationIssue,
    H4AggregationIssueCode,
    H4AggregationResult,
)
from .us_equity_calendar import session_for_instant


class ExchangeSessionH4Aggregator:
    """Aggregate only four contiguous H1 bars from one US regular session."""

    def aggregate(self, source_bars: Sequence[Bar], *, as_of: datetime) -> H4AggregationResult:
        if as_of.tzinfo is None:
            raise ValueError("H4 aggregation as_of must be timezone-aware")
        identities = {(bar.provider, bar.instrument_id) for bar in source_bars}
        if len(identities) > 1:
            first = min((bar.open_time for bar in source_bars), default=as_of)
            return H4AggregationResult(
                (),
                (
                    H4AggregationIssue(
                        H4AggregationIssueCode.INVALID_SOURCE,
                        first,
                        first + timedelta(hours=4),
                        len(source_bars),
                        "Exchange-session H4 input contains mixed instrument identity.",
                    ),
                ),
            )
        grouped: dict[str, list[Bar]] = {}
        for bar in sorted(source_bars, key=lambda item: item.open_time):
            if not bar.is_complete or bar.close_time - bar.open_time != timedelta(hours=1):
                continue
            session = session_for_instant(bar.open_time)
            if session is None or bar.open_time not in session.valid_h1_opens:
                continue
            grouped.setdefault(session.session_date.isoformat(), []).append(bar)
        buckets: list[AggregatedH4Bar] = []
        issues: list[H4AggregationIssue] = []
        for members in grouped.values():
            for index in range(0, len(members), 4):
                chunk = tuple(members[index : index + 4])
                if len(chunk) != 4 or any(
                    current.open_time != previous.close_time
                    for previous, current in zip(chunk, chunk[1:])
                ):
                    first = chunk[0]
                    issues.append(
                        H4AggregationIssue(
                            H4AggregationIssueCode.INCOMPLETE_BUCKET,
                            first.open_time,
                            first.open_time + timedelta(hours=4),
                            len(chunk),
                            "Exchange-session H4 requires four contiguous completed H1 bars from one session.",
                        )
                    )
                    continue
                close_time = chunk[-1].close_time
                if close_time > as_of.astimezone(timezone.utc):
                    continue
                buckets.append(
                    AggregatedH4Bar(
                        bar=BrokerAlignedH4Aggregator._bar(
                            chunk,
                            chunk[0].open_time,
                            close_time,
                            expected_closure_before=True,
                        ),
                        source_bars=chunk,
                    )
                )
        return H4AggregationResult(tuple(buckets), tuple(issues))
