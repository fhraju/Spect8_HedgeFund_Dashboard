from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from ..domain import Bar, FilterMode, Timeframe
from ..market_data.profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from ..market_data.session_boundaries import (
    NEW_YORK,
    active_new_york_weekly_session,
    is_expected_forex_weekend_gap,
    new_york_session_close,
    new_york_weekly_session_bounds,
)
from ..market_data.us_equity_calendar import US_ETF_PROFILE_ID
from .indicators import wilder_atr
from .models import (
    CURRENT_W1_FILTER_V1,
    CurrentPartialWeeklyCandle,
    WeeklyFilterSnapshot,
)

ATR_PERIOD = 5
BUFFER_PERCENTAGE = Decimal("0.05")


class WeeklyFilterUnavailableError(ValueError):
    """Raised when canonical H1 inputs cannot produce a weekly snapshot."""


@dataclass(frozen=True, slots=True)
class CompletedWeeklyCandle:
    candle_id: str
    session_identifier: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    source_h1_ids: tuple[str, ...]
    is_complete: bool = True


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(rows: object) -> str:
    encoded = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_id(bar: Bar) -> str:
    if bar.source_candle_ids:
        return bar.source_candle_ids[0]
    return (
        f"{bar.provider}:{bar.instrument_id}:{bar.timeframe.value}:"
        f"{_iso(bar.close_time)}"
    )


def _approved_h1(bar: Bar, *, as_of: datetime) -> bool:
    return (
        bar.timeframe is Timeframe.H1
        and bar.is_complete
        and bar.close_time <= as_of
        and bar.quality_status == "VALID"
        and bar.construction_profile_version in {PROFILE_ID, US_ETF_PROFILE_ID}
        and not bar.synthetic
        and not bar.forward_filled
        and _eligible_trading_hour(bar)
    )


def _eligible_trading_hour(bar: Bar) -> bool:
    local_open = bar.open_time.astimezone(NEW_YORK)
    local_close = bar.close_time.astimezone(NEW_YORK)
    if local_open.weekday() == 5:
        return False
    if local_open.weekday() == 6 and local_open.hour < 17:
        return False
    if local_open.weekday() == 4 and local_close.time().replace(tzinfo=None).hour > 17:
        return False
    return True


def _ordered_candidates(
    *,
    provider: str,
    instrument: str,
    as_of: datetime,
    start: datetime,
    end: datetime,
    h1_bars: Sequence[Bar],
) -> tuple[Bar, ...]:
    return tuple(
        sorted(
            (
                bar
                for bar in h1_bars
                if bar.provider == provider
                and bar.instrument_id == instrument
                and _approved_h1(bar, as_of=as_of)
                and start <= bar.open_time
                and bar.close_time <= end
            ),
            key=lambda bar: bar.open_time,
        )
    )


def _has_valid_forex_coverage(
    members: tuple[Bar, ...], start: datetime, end: datetime
) -> bool:
    if not members:
        return False
    start_date = start.astimezone(NEW_YORK).date()
    expected_first = new_york_session_close(start_date + timedelta(days=2))
    return (
        members[0].open_time == expected_first
        and members[-1].close_time == end
        and all(bar.close_time - bar.open_time == timedelta(hours=1) for bar in members)
        and all(
            current.open_time == previous.close_time
            or is_expected_forex_weekend_gap(previous.close_time, current.open_time)
            for previous, current in zip(members, members[1:])
        )
    )


class CurrentWeeklyCandleBuilder:
    def build(
        self,
        *,
        provider: str,
        instrument: str,
        as_of_h1_close: datetime,
        h1_bars: Sequence[Bar],
        sparse_actual_h1: bool = False,
    ) -> CurrentPartialWeeklyCandle:
        if as_of_h1_close.tzinfo is None:
            raise ValueError("as_of_h1_close must be timezone-aware")
        close_date, session_open, session_close = active_new_york_weekly_session(
            as_of_h1_close
        )
        candidates = _ordered_candidates(
            provider=provider,
            instrument=instrument,
            as_of=as_of_h1_close,
            start=session_open,
            end=session_close,
            h1_bars=h1_bars,
        )
        if not candidates:
            raise WeeklyFilterUnavailableError(
                "NO_COMPLETED_H1_IN_CURRENT_WEEKLY_SESSION"
            )
        if candidates[-1].close_time != as_of_h1_close:
            raise WeeklyFilterUnavailableError("CURRENT_W1_MISSING_LATEST_H1")
        if not sparse_actual_h1:
            expected_first = new_york_session_close(
                session_open.astimezone(NEW_YORK).date() + timedelta(days=2)
            )
            if candidates[0].open_time != expected_first:
                raise WeeklyFilterUnavailableError("CURRENT_W1_MISSING_INITIAL_H1")
            if any(
                current.open_time != previous.close_time
                and not is_expected_forex_weekend_gap(
                    previous.close_time, current.open_time
                )
                for previous, current in zip(candidates, candidates[1:])
            ):
                raise WeeklyFilterUnavailableError("CURRENT_W1_UNEXPECTED_H1_GAP")
        source_ids = tuple(_source_id(bar) for bar in candidates)
        source_checksum = _digest(
            [
                [
                    _iso(bar.open_time),
                    _iso(bar.close_time),
                    str(bar.open),
                    str(bar.high),
                    str(bar.low),
                    str(bar.close),
                    source_id,
                ]
                for bar, source_id in zip(candidates, source_ids)
            ]
        )
        return CurrentPartialWeeklyCandle(
            session_identifier=close_date.isoformat(),
            session_open_utc=session_open,
            session_close_utc=session_close,
            first_h1_open_time_utc=candidates[0].open_time,
            last_h1_close_time_utc=candidates[-1].close_time,
            h1_count=len(candidates),
            source_h1_ids=source_ids,
            source_checksum=source_checksum,
            open=candidates[0].open,
            high=max(bar.high for bar in candidates),
            low=min(bar.low for bar in candidates),
            close=candidates[-1].close,
            quality_status="VALID",
        )


def derive_completed_weekly_candles(
    *,
    provider: str,
    instrument: str,
    active_session_open: datetime,
    h1_bars: Sequence[Bar],
    sparse_actual_h1: bool = False,
) -> tuple[CompletedWeeklyCandle, ...]:
    if active_session_open.tzinfo is None:
        raise ValueError("active_session_open must be timezone-aware")
    approved = tuple(
        bar
        for bar in h1_bars
        if bar.provider == provider
        and bar.instrument_id == instrument
        and _approved_h1(bar, as_of=active_session_open)
    )
    if not approved:
        return ()
    earliest = min(bar.open_time for bar in approved)
    close_date = active_session_open.astimezone(NEW_YORK).date()
    sessions: list[CompletedWeeklyCandle] = []
    while True:
        start, end = new_york_weekly_session_bounds(close_date)
        if end < earliest:
            break
        members = _ordered_candidates(
            provider=provider,
            instrument=instrument,
            as_of=end,
            start=start,
            end=end,
            h1_bars=approved,
        )
        valid = (
            bool(members)
            if sparse_actual_h1
            else _has_valid_forex_coverage(members, start, end)
        )
        if valid:
            source_ids = tuple(_source_id(bar) for bar in members)
            identifier = close_date.isoformat()
            sessions.append(
                CompletedWeeklyCandle(
                    candle_id=(
                        f"{provider}:{instrument}:W1:{_iso(end)}:"
                        f"{_digest(source_ids)}"
                    ),
                    session_identifier=identifier,
                    open_time=start,
                    close_time=end,
                    open=members[0].open,
                    high=max(bar.high for bar in members),
                    low=min(bar.low for bar in members),
                    close=members[-1].close,
                    source_h1_ids=source_ids,
                )
            )
        close_date -= timedelta(days=7)
    return tuple(sorted(sessions, key=lambda bar: bar.close_time))


def build_w1_filter_snapshot(
    *,
    provider: str,
    instrument: str,
    as_of_h1_close: datetime,
    h1_bars: Sequence[Bar],
    sparse_actual_h1: bool = False,
) -> WeeklyFilterSnapshot:
    partial = CurrentWeeklyCandleBuilder().build(
        provider=provider,
        instrument=instrument,
        as_of_h1_close=as_of_h1_close,
        h1_bars=h1_bars,
        sparse_actual_h1=sparse_actual_h1,
    )
    completed = derive_completed_weekly_candles(
        provider=provider,
        instrument=instrument,
        active_session_open=partial.session_open_utc,
        h1_bars=h1_bars,
        sparse_actual_h1=sparse_actual_h1,
    )
    if len(completed) < ATR_PERIOD + 1:
        raise WeeklyFilterUnavailableError("INSUFFICIENT_COMPLETED_W1_FOR_ATR5")
    previous = completed[-1]
    atr = wilder_atr(completed, ATR_PERIOD)  # type: ignore[arg-type]
    buffer_value = atr * BUFFER_PERCENTAGE
    buy_threshold = previous.low + buffer_value
    sell_threshold = previous.high - buffer_value
    buy_matched = partial.low <= buy_threshold
    sell_matched = partial.high >= sell_threshold
    classification = (
        "BUY_AND_SELL"
        if buy_matched and sell_matched
        else "BUY"
        if buy_matched
        else "SELL"
        if sell_matched
        else "NONE"
    )
    atr_ids = tuple(bar.candle_id for bar in completed)
    atr_checksum = _digest(
        [
            [
                _iso(bar.open_time),
                _iso(bar.close_time),
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                bar.candle_id,
            ]
            for bar in completed
        ]
    )
    identity = {
        "profile": PROFILE_ID,
        "strategy": CURRENT_W1_FILTER_V1,
        "provider": provider,
        "instrument": instrument,
        "as_of": _iso(as_of_h1_close),
        "partial_source_checksum": partial.source_checksum,
        "atr_source_checksum": atr_checksum,
    }
    return WeeklyFilterSnapshot(
        snapshot_id=f"wfs_{_digest(identity)}",
        filter_mode=FilterMode.MACRO,
        strategy_version=CURRENT_W1_FILTER_V1,
        canonical_profile_version=PROFILE_ID,
        provider=provider,
        instrument=instrument,
        evaluation_time_utc=as_of_h1_close,
        as_of_h1_close_time_utc=as_of_h1_close,
        current_partial_w1=partial,
        previous_w1_candle_id=previous.candle_id,
        previous_w1_session_id=previous.session_identifier,
        previous_w1_open_utc=previous.open_time,
        previous_w1_close_utc=previous.close_time,
        previous_w1_open=previous.open,
        previous_w1_high=previous.high,
        previous_w1_low=previous.low,
        previous_w1_close=previous.close,
        atr_period=ATR_PERIOD,
        atr_value=atr,
        atr_source_w1_ids=atr_ids,
        atr_source_checksum=atr_checksum,
        buffer_percentage=BUFFER_PERCENTAGE,
        buffer_value=buffer_value,
        buy_threshold=buy_threshold,
        sell_threshold=sell_threshold,
        buy_left_value=partial.low,
        buy_operator="<=",
        buy_right_value=buy_threshold,
        buy_matched=buy_matched,
        sell_left_value=partial.high,
        sell_operator=">=",
        sell_right_value=sell_threshold,
        sell_matched=sell_matched,
        final_classification=classification,
        data_quality_status="VALID",
        ingestion_run_id=(h1_bars[-1].ingestion_run_id if h1_bars else None),
        created_at=as_of_h1_close,
    )
