from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.app.domain import Bar, Timeframe
from backend.app.market_data.forex_profile import (
    BrokerAlignedH4Aggregator,
    GapType,
    broker_utc_offset,
    broker_wall_time,
    classify_market_gap,
    is_broker_h4_close,
    market_h1_bars,
)


def h1(
    open_time: str, *, open_price: str = "1.1000", close_price: str = "1.1001"
) -> Bar:
    opened = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
    return Bar(
        instrument_id="EUR/USD",
        timeframe=Timeframe.H1,
        open_time=opened,
        close_time=opened + timedelta(hours=1),
        open=Decimal(open_price),
        high=max(Decimal(open_price), Decimal(close_price)) + Decimal("0.0002"),
        low=min(Decimal(open_price), Decimal(close_price)) - Decimal("0.0002"),
        close=Decimal(close_price),
        provider="TWELVE_DATA",
        is_complete=True,
        volume=Decimal("1"),
        synthetic=False,
    )


def test_broker_clock_follows_new_york_dst_without_fixed_dates() -> None:
    winter = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
    summer = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    assert broker_utc_offset(winter) == timedelta(hours=2)
    assert broker_utc_offset(summer) == timedelta(hours=3)
    assert broker_wall_time(winter).hour == 14
    assert broker_wall_time(summer).hour == 15
    assert is_broker_h4_close(datetime(2026, 1, 15, 2, tzinfo=timezone.utc))
    assert not is_broker_h4_close(datetime(2026, 1, 15, 1, tzinfo=timezone.utc))
    assert is_broker_h4_close(datetime(2026, 7, 15, 1, tzinfo=timezone.utc))


def test_weekend_bars_are_excluded_and_friday_monday_gap_is_preserved() -> None:
    friday = h1("2026-07-10T20:00:00Z", close_price="1.1500")
    saturday_filler = h1(
        "2026-07-11T10:00:00Z", open_price="1.1500", close_price="1.1500"
    )
    monday = h1("2026-07-12T21:00:00Z", open_price="1.1520", close_price="1.1518")
    selected = market_h1_bars((friday, saturday_filler, monday))
    assert tuple(bar.open_time for bar in selected) == (
        friday.open_time,
        monday.open_time,
    )
    assert selected[-1].expected_closure_before is True
    gap = classify_market_gap(friday, monday)
    assert gap is not None and gap.gap_type is GapType.EXPECTED_MARKET_CLOSURE
    assert friday.close == Decimal("1.1500")
    assert monday.open == Decimal("1.1520")


def test_provider_price_gap_is_not_interpolated_or_missing_data() -> None:
    previous = h1("2026-07-06T00:00:00Z", close_price="1.1500")
    current = h1("2026-07-06T01:00:00Z", open_price="1.1510")
    gap = classify_market_gap(previous, current)
    assert gap is not None and gap.gap_type is GapType.PROVIDER_PRICE_GAP


def test_weekday_missing_h1_is_an_unexpected_gap() -> None:
    previous = h1("2026-07-06T00:00:00Z")
    current = h1("2026-07-06T02:00:00Z")
    gap = classify_market_gap(previous, current)
    assert gap is not None and gap.gap_type is GapType.UNEXPECTED_DATA_GAP


def test_five_consecutive_h1_bars_keep_exact_open_and_close_spacing() -> None:
    bars = tuple(h1(f"2026-08-05T{hour:02d}:00:00Z") for hour in range(3, 8))
    assert [bar.open_time.hour for bar in bars] == [3, 4, 5, 6, 7]
    assert all(bar.close_time - bar.open_time == timedelta(hours=1) for bar in bars)
    assert all(
        current.open_time == previous.close_time
        for previous, current in zip(bars, bars[1:])
    )


def test_h4_uses_four_h1_bars_and_broker_0000_bucket() -> None:
    bars = tuple(
        h1(
            f"2026-07-05T{hour:02d}:00:00Z",
            open_price=f"1.10{hour}",
            close_price=f"1.10{hour + 1}",
        )
        for hour in (21, 22, 23)
    ) + (h1("2026-07-06T00:00:00Z", open_price="1.1024", close_price="1.1025"),)
    result = BrokerAlignedH4Aggregator().aggregate(
        bars, as_of=datetime(2026, 7, 6, 2, tzinfo=timezone.utc)
    )
    assert len(result.buckets) == 1 and result.issues == ()
    bucket = result.buckets[0]
    assert len(bucket.source_bars) == 4
    assert bucket.bar.open_time == datetime(2026, 7, 5, 21, tzinfo=timezone.utc)
    assert bucket.bar.close_time == datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    assert broker_wall_time(bucket.bar.open_time).hour == 0
    assert bucket.bar.open == bars[0].open and bucket.bar.close == bars[-1].close
    assert bucket.bar.high == max(bar.high for bar in bars)
    assert bucket.bar.low == min(bar.low for bar in bars)


def test_incomplete_weekday_h4_bucket_is_quarantined_not_filled() -> None:
    bars = tuple(h1(f"2026-07-06T{hour:02d}:00:00Z") for hour in (1, 2, 3))
    result = BrokerAlignedH4Aggregator().aggregate(
        bars, as_of=datetime(2026, 7, 6, 6, tzinfo=timezone.utc)
    )
    assert result.bars == ()
    assert len(result.issues) == 1
    assert result.issues[0].source_bar_count == 3
