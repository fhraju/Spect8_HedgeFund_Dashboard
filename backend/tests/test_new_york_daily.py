from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.app.domain import Bar, Timeframe
from backend.app.engine.models import InstrumentMetadata
from backend.app.market_data.daily_aggregator import (
    DailyAggregationIssueCode,
    NewYorkDailyAggregator,
)
from backend.app.market_data.daily_rebuild import DailyRebuildService
from backend.app.market_data.daily_rebuild_cli import backup_sqlite, main
from backend.app.market_data.history_backfill_cli import (
    main as history_backfill_main,
)
from backend.app.market_data.session_boundaries import (
    NEW_YORK_SESSION_TIMEZONE,
    new_york_session_bounds,
    new_york_session_close,
)
from backend.app.repository import SQLiteProjectionRepository

UTC = timezone.utc


def _h1_session(session_date: date, *, base: int = 100) -> tuple[Bar, ...]:
    start, end = new_york_session_bounds(session_date)
    count = int((end - start).total_seconds() // 3600)
    return tuple(
        Bar(
            instrument_id="EUR/USD",
            timeframe=Timeframe.H1,
            open_time=start + timedelta(hours=index),
            close_time=start + timedelta(hours=index + 1),
            open=Decimal(base + index),
            high=Decimal(base + index + 2),
            low=Decimal(base + index - 2),
            close=Decimal(base + index + 1),
            provider="TWELVE_DATA",
            is_complete=True,
            volume=Decimal(index + 1),
            session_timezone="UTC",
            raw_provider_symbol="EUR/USD",
            synthetic=False,
        )
        for index in range(count)
    )


def test_new_york_close_is_dst_aware_in_summer_winter_and_transitions() -> None:
    assert new_york_session_close(date(2026, 7, 15)) == datetime(
        2026, 7, 15, 21, tzinfo=UTC
    )
    assert new_york_session_close(date(2026, 1, 15)) == datetime(
        2026, 1, 15, 22, tzinfo=UTC
    )
    assert new_york_session_close(date(2026, 3, 6)).hour == 22
    assert new_york_session_close(date(2026, 3, 9)).hour == 21
    assert new_york_session_close(date(2026, 10, 30)).hour == 21
    assert new_york_session_close(date(2026, 11, 2)).hour == 22
    march_start, march_end = new_york_session_bounds(date(2026, 3, 8))
    november_start, november_end = new_york_session_bounds(date(2026, 11, 1))
    assert march_end - march_start == timedelta(hours=23)
    assert november_end - november_start == timedelta(hours=25)


def test_daily_aggregation_uses_exact_membership_and_ohlcv() -> None:
    source = _h1_session(date(2026, 7, 6))
    result = NewYorkDailyAggregator().aggregate(
        source,
        as_of=source[-1].close_time + timedelta(microseconds=1),
    )

    assert result.issues == ()
    assert len(result.sessions) == 1
    session = result.sessions[0]
    assert session.source_bar_count == 24
    assert session.bar.open_time == datetime(2026, 7, 5, 21, tzinfo=UTC)
    assert session.bar.close_time == datetime(2026, 7, 6, 21, tzinfo=UTC)
    assert session.bar.open == source[0].open
    assert session.bar.high == max(bar.high for bar in source)
    assert session.bar.low == min(bar.low for bar in source)
    assert session.bar.close == source[-1].close
    assert session.bar.volume == sum(
        (bar.volume for bar in source if bar.volume is not None), Decimal("0")
    )
    assert session.bar.session_timezone == NEW_YORK_SESSION_TIMEZONE


def test_session_close_bar_is_included_and_next_open_starts_next_session() -> None:
    monday = _h1_session(date(2026, 7, 6), base=100)
    tuesday = _h1_session(date(2026, 7, 7), base=200)
    result = NewYorkDailyAggregator().aggregate(
        (*monday, *tuesday),
        as_of=tuesday[-1].close_time + timedelta(microseconds=1),
    )

    assert result.issues == ()
    assert len(result.sessions) == 2
    first, second = result.sessions
    assert first.bar.close == monday[-1].close
    assert second.bar.open == tuesday[0].open
    assert monday[-1].close_time == tuesday[0].open_time
    assert first.bar.close_time == second.bar.open_time


@pytest.mark.parametrize(
    ("mutation", "code"),
    (
        ("forming", DailyAggregationIssueCode.INCOMPLETE_SOURCE),
        ("future", DailyAggregationIssueCode.FUTURE),
        ("duplicate", DailyAggregationIssueCode.OUT_OF_ORDER),
        ("missing", DailyAggregationIssueCode.MISSING_COVERAGE),
    ),
)
def test_invalid_source_never_produces_a_complete_daily_bar(
    mutation: str,
    code: DailyAggregationIssueCode,
) -> None:
    source = list(_h1_session(date(2026, 7, 6)))
    as_of = source[-1].close_time + timedelta(microseconds=1)
    if mutation == "forming":
        source[-1] = replace(source[-1], is_complete=False)
    elif mutation == "future":
        as_of = source[-1].close_time - timedelta(hours=1)
    elif mutation == "duplicate":
        source.insert(5, source[5])
    else:
        source.pop(5)

    result = NewYorkDailyAggregator().aggregate(source, as_of=as_of)

    assert not result.bars
    assert code in {issue.code for issue in result.issues}


def test_source_candle_crossing_session_close_is_rejected() -> None:
    source = list(_h1_session(date(2026, 7, 6)))
    source[-1] = replace(
        source[-1],
        open_time=source[-1].open_time + timedelta(minutes=30),
        close_time=source[-1].close_time + timedelta(minutes=30),
    )
    result = NewYorkDailyAggregator().aggregate(
        source,
        as_of=source[-1].close_time + timedelta(microseconds=1),
    )

    assert not result.bars
    assert DailyAggregationIssueCode.BOUNDARY_CROSSING in {
        issue.code for issue in result.issues
    }


def test_weekend_outside_forex_sessions_is_not_a_missing_daily_session() -> None:
    friday = _h1_session(date(2026, 7, 10), base=100)
    monday = _h1_session(date(2026, 7, 13), base=200)
    result = NewYorkDailyAggregator().aggregate(
        (*friday, *monday),
        as_of=monday[-1].close_time + timedelta(microseconds=1),
    )

    assert result.issues == ()
    assert [item.bar.close_time for item in result.sessions] == [
        new_york_session_close(date(2026, 7, 10)),
        new_york_session_close(date(2026, 7, 13)),
    ]


def _rebuild_fixture() -> tuple[tuple[Bar, ...], tuple[Bar, ...]]:
    dates = (
        date(2026, 7, 6),
        date(2026, 7, 7),
        date(2026, 7, 8),
        date(2026, 7, 9),
        date(2026, 7, 10),
        date(2026, 7, 13),
        date(2026, 7, 14),
        date(2026, 7, 15),
        date(2026, 7, 16),
        date(2026, 7, 17),
    )
    h1 = tuple(
        bar
        for offset, session_date in enumerate(dates)
        for bar in _h1_session(session_date, base=100 + offset * 30)
    )
    h4 = tuple(
        Bar(
            instrument_id=members[0].instrument_id,
            timeframe=Timeframe.H4,
            open_time=members[0].open_time,
            close_time=members[-1].close_time,
            open=members[0].open,
            high=max(bar.high for bar in members),
            low=min(bar.low for bar in members),
            close=members[-1].close,
            provider=members[0].provider,
            is_complete=True,
            session_timezone="UTC",
            raw_provider_symbol="EUR/USD",
            synthetic=False,
        )
        for index in range(0, len(h1), 4)
        for members in (h1[index : index + 4],)
    )
    return h1, h4


def _instrument() -> InstrumentMetadata:
    return InstrumentMetadata(
        strategy_id="SPECT8_MICRO_DAILY_V1_0",
        instrument_id="EUR/USD",
        display_name="EUR/USD",
        provider="TWELVE_DATA",
        session_timezone=NEW_YORK_SESSION_TIMEZONE,
        candle_boundary_convention="D1 closes 17:00 America/New_York",
        point_size=Decimal("0.00001"),
        price_precision=5,
        minimum_stop_distance_points=None,
        tick_size=Decimal("0.00001"),
        tick_value_usd=Decimal("1"),
        conversion_rate_to_usd=Decimal("1"),
        contract_min=Decimal("0.01"),
        contract_max=Decimal("100"),
        contract_step=Decimal("0.01"),
    )


def test_controlled_rebuild_is_dry_run_safe_atomic_and_idempotent(
    tmp_path,
    monkeypatch,
) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "rebuild.sqlite3")
    repository.initialize()
    h1, h4 = _rebuild_fixture()
    repository.persist_canonical_bars((*h1, *h4))
    native = replace(
        h1[0],
        timeframe=Timeframe.D1,
        open_time=datetime(2026, 7, 5, tzinfo=UTC),
        close_time=datetime(2026, 7, 6, tzinfo=UTC),
        session_timezone="UTC",
    )
    repository.persist_canonical_bars((native,))
    service = DailyRebuildService(repository)
    as_of = h1[-1].close_time + timedelta(microseconds=1)

    dry_run = service.rebuild(
        instrument=_instrument(), as_of=as_of, dry_run=True
    )
    assert dry_run.changed is True
    dry_run_daily = repository.canonical_bar_objects(
        "TWELVE_DATA", "EUR/USD", "D1"
    )
    assert len(dry_run_daily) == 1
    assert dry_run_daily[0].close_time == native.close_time
    assert dry_run_daily[0].session_timezone == "UTC"

    applied = service.rebuild(
        instrument=_instrument(), as_of=as_of, dry_run=False
    )
    daily = repository.canonical_bar_objects(
        "TWELVE_DATA", "EUR/USD", "D1"
    )
    assert applied.replacement_daily_bars == 10
    assert applied.evaluations_rebuilt > 0
    assert applied.events_rebuilt == repository.event_count()
    assert applied.statuses_rebuilt == 2
    assert all(
        bar.session_timezone == NEW_YORK_SESSION_TIMEZONE for bar in daily
    )
    assert all(bar.close_time.hour == 21 for bar in daily)
    true_ranges = [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(daily, daily[1:])
    ]
    expected_atr = sum(true_ranges[:5], Decimal("0")) / Decimal("5")
    for true_range in true_ranges[5:]:
        expected_atr = (
            expected_atr * Decimal("4") + true_range
        ) / Decimal("5")
    latest_h1 = next(
        status
        for status in repository.statuses()
        if status["provider"] == "TWELVE_DATA" and status["timeframe"] == "H1"
    )
    assert latest_h1["market_values"]["atr_d1_wilder_5"] == pytest.approx(
        float(expected_atr), abs=1e-10
    )

    second = service.rebuild(
        instrument=_instrument(), as_of=as_of, dry_run=False
    )
    assert second.changed is False
    assert second.evaluations_rebuilt == 0
    assert second.events_rebuilt == 0
    assert second.statuses_rebuilt == 0

    old_daily = daily

    def fail_insert(*_args) -> None:
        raise RuntimeError("forced rebuild failure")

    monkeypatch.setattr(repository, "_insert_canonical_bar", fail_insert)
    with pytest.raises(RuntimeError, match="forced rebuild failure"):
        repository.replace_daily_and_projections(
            strategy_id="SPECT8_MICRO_DAILY_V1_0",
            provider="TWELVE_DATA",
            instrument_id="EUR/USD",
            daily_bars=(replace(daily[0], close=Decimal("999")),),
            projections=(),
        )
    assert repository.canonical_bar_objects(
        "TWELVE_DATA", "EUR/USD", "D1"
    ) == old_daily


def test_rebuild_backup_is_a_readable_point_in_time_copy(tmp_path) -> None:
    database = tmp_path / "validation.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
    connection.execute("INSERT INTO evidence VALUES ('preserved')")
    connection.commit()
    connection.close()

    backup = backup_sqlite(database)

    assert backup != database
    copied = sqlite3.connect(backup)
    assert copied.execute("SELECT value FROM evidence").fetchone()[0] == "preserved"
    copied.close()


def test_active_database_apply_requires_exact_path_confirmation(tmp_path) -> None:
    database = tmp_path / "spect8_phase1.sqlite3"
    database.touch()

    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "--database",
                str(database),
                "--as-of",
                "2026-08-04T14:00:00Z",
                "--apply",
            ]
        )

    with pytest.raises(SystemExit, match="2"):
        history_backfill_main(
            [
                "--database",
                str(database),
                "--start",
                "2026-06-15T00:00:00Z",
                "--end",
                "2026-08-04T14:00:00Z",
                "--apply",
            ]
        )


def test_persisted_daily_boundaries_retain_summer_and_winter_provenance(
    tmp_path,
) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "boundary-test.sqlite3")
    repository.initialize()
    aggregator = NewYorkDailyAggregator()
    winter_source = _h1_session(date(2026, 1, 15))
    summer_source = _h1_session(date(2026, 7, 15), base=200)
    winter = aggregator.aggregate(
        winter_source,
        as_of=winter_source[-1].close_time + timedelta(microseconds=1),
    ).bars
    summer = aggregator.aggregate(
        summer_source,
        as_of=summer_source[-1].close_time + timedelta(microseconds=1),
    ).bars

    assert repository.persist_canonical_bars((*winter, *summer)) == 2
    persisted = repository.canonical_bar_objects(
        "TWELVE_DATA", "EUR/USD", "D1"
    )
    assert [bar.close_time.hour for bar in persisted] == [22, 21]
    assert all(
        bar.session_timezone == NEW_YORK_SESSION_TIMEZONE for bar in persisted
    )
