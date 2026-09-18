from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.domain import Timeframe
from backend.app.engine.current_daily_filter import CurrentDailyCandleBuilder, DailyFilterUnavailableError
from backend.app.engine.current_w1_filter import CurrentWeeklyCandleBuilder, WeeklyFilterUnavailableError
from backend.app.market_data.platform_authority import _expected_latest_forex_h1_close
from backend.app.market_data.forex_calendar import ForexMarketCalendar
from backend.tests.test_current_daily_filter_v2 import bar
from hedgefund_market_data.providers.ig import forex_calendar as calendar

UTC = timezone.utc
START = datetime(2026, 11, 26, 0, tzinfo=UTC)
END = START + timedelta(hours=12)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    path = tmp_path / 'calendar.json'
    calendar.publish_snapshot(path, {'source': calendar.SOURCE, 'digest': 'fixture', 'records': [
        {'scope': 'FX', 'status': 'VERIFIED', 'kind': 'CLOSED', 'date': '2026-11-26',
         'start': START.isoformat(), 'end': END.isoformat()}]}, now=datetime(2026, 9, 17, tzinfo=UTC))
    monkeypatch.setenv(calendar.ENVIRONMENT_KEY, str(path))
    calendar.load_definition.cache_clear()
    yield calendar.load_definition()
    calendar.load_definition.cache_clear()


def inputs(start, end, definition, provider='MARKET_DATA_PLATFORM'):
    return tuple(replace(bar(Timeframe.H1, opened, '1', '2', '0.5', '1.5'), provider=provider)
                 for opened in calendar.expected_h1_opens(definition, start, end))


@pytest.mark.parametrize('weekly', [False, True])
def test_holiday_coverage_keeps_real_gaps_blocked(configured, weekly):
    start = datetime(2026, 11, 22 if weekly else 25, 22, tzinfo=UTC)
    end = END + timedelta(hours=2)
    h1 = inputs(start, end, configured)
    builder = CurrentWeeklyCandleBuilder() if weekly else CurrentDailyCandleBuilder()
    error = WeeklyFilterUnavailableError if weekly else DailyFilterUnavailableError
    kwargs = dict(provider='MARKET_DATA_PLATFORM', instrument='EUR/USD', as_of_h1_close=end, h1_bars=h1)
    result = builder.build(**kwargs)
    assert result.h1_count == len(h1)
    with pytest.raises(error, match='UNEXPECTED_H1_GAP'):
        builder.build(**{**kwargs, 'h1_bars': h1[:-2] + h1[-1:]})
    with pytest.raises(error, match='UNEXPECTED_H1_GAP'):
        builder.build(**{**kwargs, 'h1_bars': h1[1:]})
    # No implicit calendar changes for unrelated provider profiles.
    with pytest.raises(error, match='UNEXPECTED_H1_GAP'):
        builder.build(**{**kwargs, 'provider': 'TEST', 'h1_bars': tuple(replace(b, provider='TEST') for b in h1)})


def test_freshness_and_gap_classification_share_the_collector_calendar(configured):
    assert _expected_latest_forex_h1_close(START + timedelta(hours=5)) == START
    assert _expected_latest_forex_h1_close(END + timedelta(minutes=59)) == START
    assert _expected_latest_forex_h1_close(END + timedelta(hours=1)) == END + timedelta(hours=1)
    previous = inputs(START - timedelta(hours=1), START, configured)[0]
    current = inputs(END, END + timedelta(hours=1), configured)[0]
    assert ForexMarketCalendar().is_expected_closure(previous, current)
    assert not ForexMarketCalendar().is_expected_closure(previous, replace(current, open_time=END + timedelta(hours=1)))


def test_confirmed_signal_gap_check_accepts_only_verified_closures(configured):
    from backend.app.engine.strategy import _missing_issue
    previous = inputs(START - timedelta(hours=1), START, configured)[0]
    current = inputs(END, END + timedelta(hours=1), configured)[0]
    assert not _missing_issue((previous, current), Timeframe.H1, 'missing')
    assert _missing_issue((previous, replace(current, open_time=END + timedelta(hours=1))), Timeframe.H1, 'missing') == ['missing']
    assert _missing_issue(tuple(replace(b, provider='TEST') for b in (previous, current)), Timeframe.H1, 'missing') == ['missing']


def test_terminal_hole_semantics_at_evaluation_layer(configured):
    """A terminal provider hole changes nothing at the evaluation layer.

    The dashboard reads canonical presence, not ledger state: a required bar
    that is missing (e.g. NZDJPY M30 2026-09-10T21:30Z while still required)
    keeps the evaluation visibly blocked, while current evaluations built from
    complete recent bars succeed once the hole has aged out. Nothing is ever
    synthesized: replaying a range that includes the hole stays incomplete.
    """
    from backend.app.market_data.ig_forex_calendar import valid_coverage
    start = datetime(2026, 11, 25, 22, tzinfo=UTC)
    end = END + timedelta(hours=2)
    complete = inputs(start, end, configured)
    builder = CurrentDailyCandleBuilder()
    kwargs = dict(provider='MARKET_DATA_PLATFORM', instrument='EUR/USD', as_of_h1_close=end)
    assert builder.build(**kwargs, h1_bars=complete).h1_count == len(complete)
    # Simulates the terminal hole inside the requested range: the missing bar
    # is not skipped, interpolated, or treated as valid.
    gapped = complete[:-3] + complete[-2:]
    assert valid_coverage('MARKET_DATA_PLATFORM', gapped, start, end) is False
    with pytest.raises(DailyFilterUnavailableError):
        builder.build(**kwargs, h1_bars=gapped)


def test_definition_for_picks_up_republished_snapshot_without_restart(tmp_path, monkeypatch):
    import os
    import time

    from backend.app.market_data import ig_forex_calendar as consumer

    path = tmp_path / 'calendar.json'
    calendar.publish_snapshot(path, {'source': calendar.SOURCE, 'digest': 'empty', 'records': []}, now=datetime(2026, 9, 17, tzinfo=UTC))
    monkeypatch.setenv(calendar.ENVIRONMENT_KEY, str(path))
    calendar.load_definition.cache_clear()
    consumer._seen_snapshot_mtime_ns.clear()
    try:
        assert consumer.definition_for('MARKET_DATA_PLATFORM') is None
        os.utime(path, ns=(time.time_ns(), time.time_ns() + 2_000_000))
        calendar.publish_snapshot(path, {'source': calendar.SOURCE, 'digest': 'fixture', 'records': [
            {'scope': 'FX', 'status': 'VERIFIED', 'kind': 'CLOSED', 'date': '2026-11-26',
             'start': START.isoformat(), 'end': END.isoformat()}]}, now=datetime(2026, 9, 17, tzinfo=UTC))
        reloaded = consumer.definition_for('MARKET_DATA_PLATFORM')
        assert reloaded is not None and reloaded.version != '1'
        assert calendar.expected_h1_opens(reloaded, START, END) == ()
    finally:
        calendar.load_definition.cache_clear()
        consumer._seen_snapshot_mtime_ns.clear()
