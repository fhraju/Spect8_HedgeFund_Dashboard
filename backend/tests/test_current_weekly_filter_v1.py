from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.domain import Bar, EventType, FilterMode, Timeframe
from backend.app.engine.current_w1_filter import (
    CurrentWeeklyCandleBuilder,
    build_w1_filter_snapshot,
    derive_completed_weekly_candles,
)
from backend.app.engine.current_daily_filter import build_daily_filter_snapshot
from backend.app.engine.indicators import wilder_atr
from backend.app.engine.models import CURRENT_W1_FILTER_V1, StrategyRequest
from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.market_data.profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from backend.app.market_data.daily_aggregator import NewYorkDailyAggregator
from backend.app.market_data.session_boundaries import (
    NEW_YORK,
    active_new_york_weekly_session,
    new_york_weekly_session_bounds,
)
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService
from backend.tests.test_current_daily_filter_v2 import (
    daily_history,
    metadata,
    signal_history,
    snapshot as daily_snapshot,
)

PROVIDER = "TWELVE_DATA"
INSTRUMENT = "EUR/USD"


def h1_bar(
    open_time: datetime,
    *,
    high: Decimal = Decimal("101"),
    low: Decimal = Decimal("99"),
    open_value: Decimal = Decimal("100"),
    close_value: Decimal = Decimal("100"),
    complete: bool = True,
    synthetic: bool = False,
    forward_filled: bool = False,
    quality_status: str = "VALID",
) -> Bar:
    close_time = open_time + timedelta(hours=1)
    source_id = f"weekly:{close_time.isoformat()}:{high}:{low}"
    return Bar(
        instrument_id=INSTRUMENT,
        timeframe=Timeframe.H1,
        open_time=open_time,
        close_time=close_time,
        open=open_value,
        high=high,
        low=low,
        close=close_value,
        provider=PROVIDER,
        is_complete=complete,
        synthetic=synthetic,
        quality_status=quality_status,
        construction_profile_version=PROFILE_ID,
        provider_adapter_version="test",
        source_timeframe=Timeframe.H1,
        source_candle_ids=(source_id,),
        forward_filled=forward_filled,
        created_at=close_time,
    )


def trading_week(
    close_date: date,
    *,
    high: Decimal,
    low: Decimal,
    through: datetime | None = None,
) -> tuple[Bar, ...]:
    start, end = new_york_weekly_session_bounds(close_date)
    sunday_open = datetime.combine(
        start.astimezone(NEW_YORK).date() + timedelta(days=2),
        datetime.min.time().replace(hour=17),
        tzinfo=NEW_YORK,
    ).astimezone(timezone.utc)
    limit = min(end, through) if through is not None else end
    values: list[Bar] = []
    current = sunday_open
    while current + timedelta(hours=1) <= limit:
        values.append(h1_bar(current, high=high, low=low))
        current += timedelta(hours=1)
    return tuple(values)


def weekly_history(
    active_close_date: date,
    *,
    as_of: datetime,
) -> tuple[Bar, ...]:
    bars: list[Bar] = []
    for weeks_back in range(8, 0, -1):
        close_date = active_close_date - timedelta(days=7 * weeks_back)
        bars.extend(
            trading_week(
                close_date,
                high=Decimal(108 + weeks_back),
                low=Decimal(92 - weeks_back),
            )
        )
    bars.extend(
        trading_week(
            active_close_date,
            high=Decimal("104"),
            low=Decimal("96"),
            through=as_of,
        )
    )
    return tuple(bars)


@pytest.mark.parametrize(
    ("instant", "expected_start", "expected_end"),
    (
        (
            datetime(2026, 1, 14, 17, tzinfo=NEW_YORK),
            datetime(2026, 1, 9, 22, tzinfo=timezone.utc),
            datetime(2026, 1, 16, 22, tzinfo=timezone.utc),
        ),
        (
            datetime(2026, 7, 15, 17, tzinfo=NEW_YORK),
            datetime(2026, 7, 10, 21, tzinfo=timezone.utc),
            datetime(2026, 7, 17, 21, tzinfo=timezone.utc),
        ),
    ),
)
def test_friday_1700_weekly_authority_in_est_and_edt(
    instant: datetime,
    expected_start: datetime,
    expected_end: datetime,
) -> None:
    _, start, end = active_new_york_weekly_session(instant)
    assert start == expected_start
    assert end == expected_end


def test_dst_transition_uses_zoneinfo_and_keeps_local_friday_1700() -> None:
    start, end = new_york_weekly_session_bounds(date(2026, 3, 13))
    assert start == datetime(2026, 3, 6, 22, tzinfo=timezone.utc)
    assert end == datetime(2026, 3, 13, 21, tzinfo=timezone.utc)
    assert end - start == timedelta(hours=167)
    assert start.astimezone(NEW_YORK).hour == 17
    assert end.astimezone(NEW_YORK).hour == 17


def test_partial_w1_uses_only_eligible_completed_h1_through_cutoff() -> None:
    as_of = datetime(2026, 7, 15, 16, tzinfo=NEW_YORK).astimezone(timezone.utc)
    bars = list(
        trading_week(
            date(2026, 7, 17), high=Decimal("105"), low=Decimal("95"), through=as_of
        )
    )
    expected = tuple(bars)
    weekend = h1_bar(
        datetime(2026, 7, 11, 12, tzinfo=NEW_YORK).astimezone(timezone.utc),
        high=Decimal("999"),
    )
    future = h1_bar(as_of, high=Decimal("998"))
    invalid_open = bars[-1].open_time - timedelta(minutes=15)
    bars.extend(
        (
            replace(h1_bar(invalid_open, high=Decimal("997")), is_complete=False),
            replace(h1_bar(invalid_open, high=Decimal("996")), synthetic=True),
            replace(h1_bar(invalid_open, high=Decimal("995")), forward_filled=True),
            replace(
                h1_bar(invalid_open, high=Decimal("994")), quality_status="QUARANTINED"
            ),
            weekend,
            future,
        )
    )
    partial = CurrentWeeklyCandleBuilder().build(
        provider=PROVIDER,
        instrument=INSTRUMENT,
        as_of_h1_close=as_of,
        h1_bars=bars,
    )
    assert partial.open == expected[0].open
    assert partial.high == Decimal("105")
    assert partial.low == Decimal("95")
    assert partial.close == expected[-1].close
    assert partial.h1_count == len(expected)
    assert partial.last_h1_close_time_utc == as_of
    assert all("999" not in source_id for source_id in partial.source_h1_ids)
    assert all("998" not in source_id for source_id in partial.source_h1_ids)


def test_weekly_formula_wilder_atr_and_inclusive_boundaries() -> None:
    as_of = datetime(2026, 8, 12, 12, tzinfo=NEW_YORK).astimezone(timezone.utc)
    active_close = date(2026, 8, 14)
    bars = list(weekly_history(active_close, as_of=as_of))
    _, active_start, _ = active_new_york_weekly_session(as_of)
    completed = derive_completed_weekly_candles(
        provider=PROVIDER,
        instrument=INSTRUMENT,
        active_session_open=active_start,
        h1_bars=bars,
    )
    expected_atr = wilder_atr(completed, 5)  # type: ignore[arg-type]
    previous = completed[-1]
    buy_threshold = previous.low + expected_atr * Decimal("0.05")
    sell_threshold = previous.high - expected_atr * Decimal("0.05")
    current_start = next(
        index for index, bar in enumerate(bars) if bar.open_time >= active_start
    )
    bars[current_start] = replace(
        bars[current_start],
        high=sell_threshold,
        low=buy_threshold,
    )
    snapshot = build_w1_filter_snapshot(
        provider=PROVIDER,
        instrument=INSTRUMENT,
        as_of_h1_close=as_of,
        h1_bars=bars,
    )
    assert snapshot.atr_value == expected_atr
    assert snapshot.buy_threshold == buy_threshold
    assert snapshot.sell_threshold == sell_threshold
    assert snapshot.buy_left_value == buy_threshold
    assert snapshot.sell_left_value == sell_threshold
    assert snapshot.buy_matched is True
    assert snapshot.sell_matched is True
    assert snapshot.buy_operator == "<="
    assert snapshot.sell_operator == ">="
    assert all(
        source_id not in snapshot.current_partial_w1.source_h1_ids
        for source_id in snapshot.atr_source_w1_ids
    )


def test_no_lookahead_across_multiple_timestamps_in_one_week() -> None:
    active_close = date(2026, 8, 14)
    cutoffs = [
        datetime(2026, 8, day, 12, tzinfo=NEW_YORK).astimezone(timezone.utc)
        for day in (10, 11, 12)
    ]
    full = list(weekly_history(active_close, as_of=cutoffs[-1]))
    active_start = active_new_york_weekly_session(cutoffs[-1])[1]
    for index, bar in enumerate(full):
        if bar.open_time >= active_start:
            full[index] = replace(
                bar,
                high=Decimal("100") + Decimal(index) / Decimal("1000"),
                low=Decimal("99") - Decimal(index) / Decimal("1000"),
            )
    snapshots = [
        build_w1_filter_snapshot(
            provider=PROVIDER,
            instrument=INSTRUMENT,
            as_of_h1_close=cutoff,
            h1_bars=full,
        )
        for cutoff in cutoffs
    ]
    assert [
        item.current_partial_w1.last_h1_close_time_utc for item in snapshots
    ] == cutoffs
    assert [item.current_partial_w1.h1_count for item in snapshots] == sorted(
        item.current_partial_w1.h1_count for item in snapshots
    )
    assert len({item.current_partial_w1.source_checksum for item in snapshots}) == 3
    for snapshot, cutoff in zip(snapshots, cutoffs):
        assert all(
            source.close_time <= cutoff
            for source in full
            if source.source_candle_ids[0] in snapshot.current_partial_w1.source_h1_ids
        )


def test_macro_shared_snapshot_drives_h1_and_h4_without_changing_signal_formula() -> (
    None
):
    micro = daily_snapshot()
    as_of = micro.as_of_h1_close_time_utc
    active_close = active_new_york_weekly_session(as_of)[0]
    macro = build_w1_filter_snapshot(
        provider=PROVIDER,
        instrument=INSTRUMENT,
        as_of_h1_close=as_of,
        h1_bars=weekly_history(active_close, as_of=as_of),
    )
    macro = replace(macro, provider=metadata().provider)
    evaluator = Spect8StrategyEvaluator()
    for timeframe in (Timeframe.H1, Timeframe.H4):
        signals = signal_history(timeframe)
        assert macro.instrument == metadata().instrument_id
        assert macro.provider == metadata().provider
        assert macro.as_of_h1_close_time_utc == signals[-1].close_time
        assert macro.strategy_version == CURRENT_W1_FILTER_V1
        assert macro.filter_mode is FilterMode.MACRO
        macro_result = evaluator.evaluate(
            StrategyRequest(
                case_id=f"macro-{timeframe.value}",
                strategy_id=CURRENT_W1_FILTER_V1,
                timeframe=timeframe,
                evaluation_time=as_of + timedelta(seconds=1),
                signal_bars=signals,
                daily_bars=daily_history(),
                instrument=metadata(),
                strategy_version=CURRENT_W1_FILTER_V1,
                filter_mode=FilterMode.MACRO,
                w1_filter_snapshot=macro,
            )
        )
        micro_result = evaluator.evaluate(
            StrategyRequest(
                case_id=f"micro-{timeframe.value}",
                strategy_id="SPECT8_MICRO_DAILY_V1_0",
                timeframe=timeframe,
                evaluation_time=as_of + timedelta(seconds=1),
                signal_bars=signal_history(timeframe),
                daily_bars=daily_history(),
                instrument=metadata(),
                strategy_version=micro.strategy_version,
                daily_filter_snapshot=micro,
            )
        )
        assert macro_result.data_status == "READY", macro_result.issues
        assert macro_result.daily_filter_snapshot_id == macro.snapshot_id
        assert macro_result.signals == micro_result.signals
        assert macro_result.indicators.sma10 == micro_result.indicators.sma10
        assert macro_result.indicators.sma20 == micro_result.indicators.sma20
        assert (
            macro_result.indicators.atr_d1_wilder_5
            == micro_result.indicators.atr_d1_wilder_5
        )


def test_mode_authority_can_produce_different_filters_for_same_h1_state() -> None:
    as_of = datetime(2026, 8, 12, 12, tzinfo=NEW_YORK).astimezone(timezone.utc)
    bars = weekly_history(date(2026, 8, 14), as_of=as_of)
    aggregation = NewYorkDailyAggregator().aggregate(
        bars, as_of=as_of + timedelta(seconds=1)
    )
    assert aggregation.issues == ()
    micro = build_daily_filter_snapshot(
        provider=PROVIDER,
        instrument=INSTRUMENT,
        as_of_h1_close=as_of,
        h1_bars=bars,
        completed_d1_bars=aggregation.bars,
    )
    macro = build_w1_filter_snapshot(
        provider=PROVIDER,
        instrument=INSTRUMENT,
        as_of_h1_close=as_of,
        h1_bars=bars,
    )
    assert micro.buy_matched is True
    assert macro.buy_matched is False

    monday_1800 = datetime(2026, 8, 10, 18, tzinfo=NEW_YORK).astimezone(timezone.utc)
    macro_buy_bars = tuple(
        replace(bar, low=Decimal("90"))
        if bar.open_time == monday_1800
        else replace(bar, low=Decimal("99"))
        if bar.open_time >= active_new_york_weekly_session(as_of)[1]
        else bar
        for bar in bars
    )
    macro_buy_daily = NewYorkDailyAggregator().aggregate(
        macro_buy_bars, as_of=as_of + timedelta(seconds=1)
    )
    assert macro_buy_daily.issues == ()
    micro_not = build_daily_filter_snapshot(
        provider=PROVIDER,
        instrument=INSTRUMENT,
        as_of_h1_close=as_of,
        h1_bars=macro_buy_bars,
        completed_d1_bars=macro_buy_daily.bars,
    )
    macro_yes = build_w1_filter_snapshot(
        provider=PROVIDER,
        instrument=INSTRUMENT,
        as_of_h1_close=as_of,
        h1_bars=macro_buy_bars,
    )
    assert macro_yes.buy_matched is True
    assert micro_not.buy_matched is False


def test_macro_event_trace_contains_one_filter_event_with_mode_and_version(
    tmp_path: Path,
) -> None:
    micro = daily_snapshot()
    as_of = micro.as_of_h1_close_time_utc
    macro = build_w1_filter_snapshot(
        provider=PROVIDER,
        instrument=INSTRUMENT,
        as_of_h1_close=as_of,
        h1_bars=weekly_history(active_new_york_weekly_session(as_of)[0], as_of=as_of),
    )
    macro = replace(macro, provider=metadata().provider, buy_matched=True)
    request = StrategyRequest(
        case_id="macro-events",
        strategy_id=CURRENT_W1_FILTER_V1,
        timeframe=Timeframe.H1,
        evaluation_time=as_of + timedelta(seconds=1),
        signal_bars=signal_history(Timeframe.H1),
        daily_bars=daily_history(),
        instrument=metadata(),
        strategy_version=CURRENT_W1_FILTER_V1,
        filter_mode=FilterMode.MACRO,
        w1_filter_snapshot=macro,
    )
    repository = SQLiteProjectionRepository(tmp_path / "events.sqlite3")
    repository.initialize()
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository)
    events = service.events_for_projection(service.evaluate_request(request))
    filter_events = [
        event for event in events if event.event_type is EventType.FILTER_EVALUATED
    ]
    assert len(filter_events) == 1
    assert filter_events[0].payload["filter_mode"] == "MACRO"
    assert filter_events[0].payload["strategy_version"] == CURRENT_W1_FILTER_V1
    assert filter_events[0].payload["filter_snapshot_id"] == macro.snapshot_id
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
