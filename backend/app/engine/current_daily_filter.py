from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from ..domain import Bar, Timeframe
from ..market_data.profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from ..market_data.session_boundaries import NEW_YORK, new_york_session_bounds
from ..market_data.us_equity_calendar import US_ETF_PROFILE_ID
from .indicators import wilder_atr
from .models import (
    CURRENT_D1_FILTER_V2,
    CurrentPartialDailyCandle,
    DailyFilterSnapshot,
)

ATR_PERIOD = 5
BUFFER_PERCENTAGE = Decimal("0.05")


class DailyFilterUnavailableError(ValueError):
    """Raised when approved canonical inputs cannot produce a V2 snapshot."""


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(rows: object) -> str:
    encoded = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_id(bar: Bar) -> str:
    if bar.source_candle_ids:
        return (
            bar.source_candle_ids[0]
            if bar.timeframe is Timeframe.H1
            else (
                f"{bar.provider}:{bar.instrument_id}:{bar.timeframe.value}:"
                f"{_iso(bar.close_time)}"
            )
        )
    return (
        f"{bar.provider}:{bar.instrument_id}:{bar.timeframe.value}:"
        f"{_iso(bar.close_time)}"
    )


def _session_date_for_close(as_of_h1_close: datetime) -> date:
    local = as_of_h1_close.astimezone(NEW_YORK)
    if local.hour < 17:
        return local.date()
    if local.hour == 17 and local.minute == 0 and local.second == 0:
        return local.date()
    return local.date() + timedelta(days=1)


def _approved(bar: Bar, timeframe: Timeframe) -> bool:
    return (
        bar.timeframe is timeframe
        and bar.is_complete
        and bar.quality_status == "VALID"
        and bar.construction_profile_version in {PROFILE_ID, US_ETF_PROFILE_ID}
        and not bar.synthetic
        and not bar.forward_filled
    )


class CurrentDailyCandleBuilder:
    def build(
        self,
        *,
        provider: str,
        instrument: str,
        as_of_h1_close: datetime,
        h1_bars: Sequence[Bar],
        sparse_actual_h1: bool = False,
    ) -> CurrentPartialDailyCandle:
        if as_of_h1_close.tzinfo is None:
            raise ValueError("as_of_h1_close must be timezone-aware")
        session_date = _session_date_for_close(as_of_h1_close)
        session_open, session_close = new_york_session_bounds(session_date)
        candidates = tuple(
            sorted(
                (
                    bar
                    for bar in h1_bars
                    if bar.provider == provider
                    and bar.instrument_id == instrument
                    and _approved(bar, Timeframe.H1)
                    and session_open <= bar.open_time
                    and bar.close_time <= session_close
                    and bar.close_time <= as_of_h1_close
                ),
                key=lambda bar: bar.open_time,
            )
        )
        if not candidates:
            raise DailyFilterUnavailableError(
                "NO_COMPLETED_H1_IN_CURRENT_DAILY_SESSION"
            )
        if not sparse_actual_h1 and candidates[0].open_time != session_open:
            raise DailyFilterUnavailableError("CURRENT_D1_MISSING_INITIAL_H1")
        if any(
            current.open_time != previous.close_time
            for previous, current in zip(candidates, candidates[1:])
        ):
            raise DailyFilterUnavailableError("CURRENT_D1_UNEXPECTED_H1_GAP")
        if candidates[-1].close_time != as_of_h1_close:
            raise DailyFilterUnavailableError("CURRENT_D1_MISSING_LATEST_H1")
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
        return CurrentPartialDailyCandle(
            session_identifier=session_date.isoformat(),
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


def build_daily_filter_snapshot(
    *,
    provider: str,
    instrument: str,
    as_of_h1_close: datetime,
    h1_bars: Sequence[Bar],
    completed_d1_bars: Sequence[Bar],
    sparse_actual_h1: bool = False,
) -> DailyFilterSnapshot:
    partial = CurrentDailyCandleBuilder().build(
        provider=provider,
        instrument=instrument,
        as_of_h1_close=as_of_h1_close,
        h1_bars=h1_bars,
        sparse_actual_h1=sparse_actual_h1,
    )
    eligible = tuple(
        bar
        for bar in completed_d1_bars
        if bar.provider == provider
        and bar.instrument_id == instrument
        and _approved(bar, Timeframe.D1)
        and bar.close_time <= partial.session_open_utc
    )
    if len(eligible) < ATR_PERIOD + 1:
        raise DailyFilterUnavailableError("INSUFFICIENT_COMPLETED_D1_FOR_ATR5")
    previous = eligible[-1]
    # Across an expected weekend closure the immediately preceding completed
    # session closes Friday while the next tradable session opens Sunday.
    # Eligibility/order, rather than wall-clock adjacency, identifies it.
    atr = wilder_atr(eligible, ATR_PERIOD)
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
    atr_ids = tuple(_source_id(bar) for bar in eligible)
    atr_checksum = _digest(
        [
            [
                _iso(bar.open_time),
                _iso(bar.close_time),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                source_id,
            ]
            for bar, source_id in zip(eligible, atr_ids)
        ]
    )
    identity = {
        "profile": PROFILE_ID,
        "strategy": CURRENT_D1_FILTER_V2,
        "provider": provider,
        "instrument": instrument,
        "as_of": _iso(as_of_h1_close),
        "partial_source_checksum": partial.source_checksum,
        "atr_source_checksum": atr_checksum,
    }
    snapshot_id = f"dfs_{_digest(identity)}"
    return DailyFilterSnapshot(
        snapshot_id=snapshot_id,
        strategy_version=CURRENT_D1_FILTER_V2,
        canonical_profile_version=PROFILE_ID,
        provider=provider,
        instrument=instrument,
        evaluation_time_utc=as_of_h1_close,
        as_of_h1_close_time_utc=as_of_h1_close,
        current_partial_d1=partial,
        previous_d1_candle_id=_source_id(previous),
        previous_d1_session_id=(
            previous.session_identifier
            or previous.close_time.astimezone(NEW_YORK).date().isoformat()
        ),
        previous_d1_open_utc=previous.open_time,
        previous_d1_close_utc=previous.close_time,
        previous_d1_high=previous.high,
        previous_d1_low=previous.low,
        previous_d1_close=previous.close,
        atr_period=ATR_PERIOD,
        atr_value=atr,
        atr_source_d1_ids=atr_ids,
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
