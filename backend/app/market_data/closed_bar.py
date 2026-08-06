from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from ..domain import Bar, Timeframe
from .models import HistoryValidation
from .session_boundaries import is_expected_forex_weekend_gap
from .models import SessionProfileKind
from .us_equity_calendar import US_EASTERN, next_us_equity_session_date

TIMEFRAME_STEP = {
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}
MIN_SIGNAL_HISTORY = 30
MIN_DAILY_HISTORY = 6
DAILY_ATR_INPUT_HISTORY = 10


class ClosedBarDetector:
    @staticmethod
    def batch_issues(bars: Sequence[Bar]) -> tuple[str, ...]:
        issues: list[str] = []
        identities = [
            (
                bar.provider,
                bar.instrument_id,
                bar.timeframe,
                bar.close_time,
            )
            for bar in bars
        ]
        if len(identities) != len(set(identities)):
            issues.append("DUPLICATE_CANDLE")
        closes = [bar.close_time for bar in bars]
        if any(
            current < previous
            for previous, current in zip(closes, closes[1:])
        ):
            issues.append("OUT_OF_ORDER_CANDLE")
        return tuple(sorted(set(issues)))

    def validate_history(
        self,
        signal_bars: Sequence[Bar],
        daily_bars: Sequence[Bar],
        timeframe: Timeframe,
        trigger_close_time: datetime,
        session_profile: SessionProfileKind | None = None,
    ) -> HistoryValidation:
        if timeframe not in (Timeframe.H1, Timeframe.H4):
            raise ValueError("signal timeframe must be H1 or H4")
        issues: list[str] = []
        issues.extend(
            self._stream_issues(signal_bars, timeframe, "SIGNAL", session_profile)
        )
        issues.extend(
            self._stream_issues(daily_bars, Timeframe.D1, "DAILY", session_profile)
        )

        if len(signal_bars) < MIN_SIGNAL_HISTORY:
            issues.append("INSUFFICIENT_SIGNAL_HISTORY")
        if len(daily_bars) < MIN_DAILY_HISTORY:
            issues.append("INSUFFICIENT_DAILY_HISTORY")
        if signal_bars and signal_bars[-1].close_time != trigger_close_time:
            issues.append("MISSING_TRIGGER_CANDLE")
        if any(bar.close_time > trigger_close_time for bar in signal_bars):
            issues.append("LOOKAHEAD_CANDLE")
        if any(bar.close_time > trigger_close_time for bar in daily_bars):
            issues.append("LOOKAHEAD_CANDLE")
        if any(not bar.is_complete for bar in (*signal_bars, *daily_bars)):
            issues.append("INCOMPLETE_CANDLE")

        return HistoryValidation(
            signal_bars=tuple(signal_bars),
            daily_bars=tuple(daily_bars),
            issues=tuple(sorted(set(issues))),
        )

    @staticmethod
    def _stream_issues(
        bars: Sequence[Bar],
        timeframe: Timeframe,
        label: str,
        session_profile: SessionProfileKind | None = None,
    ) -> list[str]:
        issues: list[str] = []
        relevant = [bar for bar in bars if bar.timeframe is timeframe]
        identities = [
            (
                bar.provider,
                bar.instrument_id,
                bar.timeframe,
                bar.open_time,
                bar.close_time,
            )
            for bar in relevant
        ]
        if len(identities) != len(set(identities)):
            issues.append("DUPLICATE_CANDLE")
        opens = [bar.open_time for bar in relevant]
        if any(current < previous for previous, current in zip(opens, opens[1:])):
            issues.append("OUT_OF_ORDER_CANDLE")
        step = TIMEFRAME_STEP[timeframe]
        if any(
            current.open_time - previous.open_time != step
            and not is_expected_forex_weekend_gap(
                previous.open_time + step, current.open_time
            )
            and not (
                session_profile is SessionProfileKind.US_EQUITY_REGULAR
                and ClosedBarDetector._expected_us_exchange_gap(previous, current)
            )
            for previous, current in zip(relevant, relevant[1:])
        ):
            issues.append(f"MISSING_{label}_CANDLE")
        return issues

    @staticmethod
    def _expected_us_exchange_gap(previous: Bar, current: Bar) -> bool:
        if previous.session_identifier and current.session_identifier:
            previous_date = datetime.fromisoformat(
                previous.session_identifier
            ).date()
            current_date = datetime.fromisoformat(current.session_identifier).date()
        else:
            previous_date = previous.open_time.astimezone(US_EASTERN).date()
            current_date = current.open_time.astimezone(US_EASTERN).date()
        if previous_date == current_date:
            return False
        return next_us_equity_session_date(previous_date) == current_date
