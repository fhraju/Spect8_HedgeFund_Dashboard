"""Filter freshness must respect required dependency continuity, not just H1.

A Filter can be timestamp-current (its ``as_of_h1_close_time`` equals the latest
completed H1) while its required daily/weekly dependency context has an
unexpected hole. Reporting that filter as CURRENT is misleading: the evaluation
is blocked on exactly that missing dependency. These tests pin the fail-closed
behaviour without changing any filter arithmetic.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.app.dashboard_api import (
    _filter_dependency_valid,
    _filter_freshness,
    scanner_snapshot,
)
from backend.app.domain import Bar, FilterMode, Timeframe
from backend.app.market_data.collection_view import attach_collection_status
from backend.app.market_data.share_catalog import platform_instruments

UTC = timezone.utc
INSTRUMENT = "SHARE_MSFT"
PROVIDER = "MARKET_DATA_PLATFORM"
LATEST_H1 = "2026-09-18T06:00:00Z"
FILTER_AS_OF = "2026-09-18T06:00:00Z"


def _bar(timeframe, open_time, close_time, *, expected_closure_before=False):
    return Bar(
        instrument_id=INSTRUMENT,
        timeframe=timeframe,
        open_time=open_time,
        close_time=close_time,
        open=Decimal(100),
        high=Decimal(102),
        low=Decimal(98),
        close=Decimal(101),
        provider=PROVIDER,
        is_complete=True,
        synthetic=False,
        quality_status="VALID",
        construction_profile_version="IG_LIVE_AAPL_SHARE_SB_V1",
        expected_closure_before=expected_closure_before,
    )


def _d1_series(*, hole=False):
    start = datetime(2026, 9, 10, 4, tzinfo=UTC)
    day = timedelta(days=1)
    bars = [_bar(Timeframe.D1, start + day * i, start + day * (i + 1)) for i in range(6)]
    if hole:
        # Drop an interior expected trading day: the next bar now has a gap it
        # does not declare as an expected closure.
        del bars[3]
        bars[3] = _bar(
            Timeframe.D1, start + day * 4, start + day * 5, expected_closure_before=False
        )
    return tuple(bars)


def _w1_series(*, hole=False):
    start = datetime(2026, 8, 3, tzinfo=UTC)
    week = timedelta(days=7)
    bars = [_bar(Timeframe.W1, start + week * i, start + week * (i + 1)) for i in range(5)]
    if hole:
        del bars[2]
        bars[2] = _bar(
            Timeframe.W1, start + week * 3, start + week * 4, expected_closure_before=False
        )
    return tuple(bars)


class DepRepository:
    def __init__(self, bars_by_timeframe):
        self._bars = bars_by_timeframe

    def canonical_bar_objects(self, provider, instrument_id, timeframe):
        assert provider == PROVIDER
        return tuple(self._bars.get(timeframe, ()))


def test_micro_dependency_hole_is_not_current():
    repo = DepRepository({Timeframe.D1: _d1_series(hole=True)})
    valid, reason = _filter_dependency_valid(
        repo, PROVIDER, INSTRUMENT, FilterMode.MICRO, datetime(2026, 9, 18, 6, tzinfo=UTC)
    )
    assert (valid, reason) == (False, "FILTER_DEPENDENCY_D1_INCOMPLETE")
    freshness, stale_reason, _, _ = _filter_freshness(
        repo, PROVIDER, INSTRUMENT, FILTER_AS_OF, LATEST_H1,
        dependency_valid=valid, dependency_reason=reason,
    )
    assert freshness == "STALE"
    assert stale_reason == "FILTER_DEPENDENCY_D1_INCOMPLETE"


def test_macro_dependency_hole_is_not_current():
    repo = DepRepository(
        {Timeframe.W1: _w1_series(hole=True), Timeframe.D1: _d1_series()}
    )
    valid, reason = _filter_dependency_valid(
        repo, PROVIDER, INSTRUMENT, FilterMode.MACRO, datetime(2026, 9, 18, 6, tzinfo=UTC)
    )
    assert (valid, reason) == (False, "FILTER_DEPENDENCY_W1_INCOMPLETE")
    freshness, stale_reason, _, _ = _filter_freshness(
        repo, PROVIDER, INSTRUMENT, FILTER_AS_OF, LATEST_H1,
        dependency_valid=valid, dependency_reason=reason,
    )
    assert freshness == "STALE"
    assert stale_reason == "FILTER_DEPENDENCY_W1_INCOMPLETE"


def test_healthy_dependencies_stay_current():
    repo = DepRepository(
        {Timeframe.W1: _w1_series(), Timeframe.D1: _d1_series()}
    )
    micro = _filter_dependency_valid(
        repo, PROVIDER, INSTRUMENT, FilterMode.MICRO, datetime(2026, 9, 18, 6, tzinfo=UTC)
    )
    macro = _filter_dependency_valid(
        repo, PROVIDER, INSTRUMENT, FilterMode.MACRO, datetime(2026, 9, 18, 6, tzinfo=UTC)
    )
    assert micro == (True, None)
    assert macro == (True, None)
    assert _filter_freshness(
        repo, PROVIDER, INSTRUMENT, FILTER_AS_OF, LATEST_H1,
        dependency_valid=True, dependency_reason=None,
    )[:2] == ("CURRENT", None)


def test_expected_closure_gap_is_allowed():
    start = datetime(2026, 9, 10, 4, tzinfo=UTC)
    day = timedelta(days=1)
    bars = [
        _bar(Timeframe.D1, start, start + day),
        _bar(Timeframe.D1, start + day, start + 2 * day),
        # Monday after a weekend: declared expected closure, not a hole.
        _bar(Timeframe.D1, start + 4 * day, start + 5 * day, expected_closure_before=True),
    ]
    repo = DepRepository({Timeframe.D1: tuple(bars)})
    assert _filter_dependency_valid(
        repo, PROVIDER, INSTRUMENT, FilterMode.MICRO, datetime(2026, 9, 18, 6, tzinfo=UTC)
    ) == (True, None)


def test_unknown_projection_does_not_fabricate_current():
    class Bare:
        pass

    assert _filter_dependency_valid(
        Bare(), PROVIDER, INSTRUMENT, FilterMode.MICRO, datetime(2026, 9, 18, 6, tzinfo=UTC)
    ) == (None, None)
    # A matching H1 with unknown dependency validity preserves prior behaviour.
    assert _filter_freshness(
        Bare(), PROVIDER, INSTRUMENT, FILTER_AS_OF, LATEST_H1
    )[:2] == ("CURRENT", None)


class ScannerRepository:
    def __init__(self, *, bars_by_timeframe, filter_snapshot):
        self._bars = bars_by_timeframe
        self._filter = filter_snapshot

    def active_filter_mode(self):
        return FilterMode.MICRO

    def statuses(self):
        return []

    def instrument_health(self, provider, instrument_id):
        return None

    def latest_candle_timestamps(self, provider, instrument_id):
        return {"H1": LATEST_H1}

    def latest_daily_filter_snapshot(self, provider, instrument_id, version):
        return self._filter

    def latest_weekly_filter_snapshot(self, provider, instrument_id, version):
        return None

    def canonical_bar_objects(self, provider, instrument_id, timeframe):
        return tuple(self._bars.get(timeframe, ()))

    def present_h1_close_times(self, provider, instrument_id):
        return set()


def _filter_snapshot():
    return {
        "snapshot_id": "dfs_fixture",
        "as_of_h1_close_time_utc": FILTER_AS_OF,
        "final_classification": "NONE",
        "buy_matched": False,
        "sell_matched": False,
    }


def _share_instrument(spect8_id=INSTRUMENT):
    return next(
        item
        for item in platform_instruments((spect8_id,))
        if item.instrument_id == spect8_id
    )


def test_scanner_row_reports_not_current_on_dependency_hole():
    repo = ScannerRepository(
        bars_by_timeframe={Timeframe.D1: _d1_series(hole=True)},
        filter_snapshot=_filter_snapshot(),
    )
    data = scanner_snapshot(
        repo, (_share_instrument(),), datetime(2026, 9, 18, 6, 5, tzinfo=UTC)
    )
    row = data.instruments[0]
    assert row.current_filter.filter_freshness == "STALE"
    assert row.current_filter.stale_reason == "FILTER_DEPENDENCY_D1_INCOMPLETE"


def test_scanner_row_reports_current_when_dependencies_healthy():
    repo = ScannerRepository(
        bars_by_timeframe={Timeframe.D1: _d1_series()},
        filter_snapshot=_filter_snapshot(),
    )
    data = scanner_snapshot(
        repo, (_share_instrument(),), datetime(2026, 9, 18, 6, 5, tzinfo=UTC)
    )
    row = data.instruments[0]
    assert row.current_filter.filter_freshness == "CURRENT"


def test_scanner_row_reports_current_for_healthy_aapl_control():
    repo = ScannerRepository(
        bars_by_timeframe={Timeframe.D1: _d1_series()},
        filter_snapshot=_filter_snapshot(),
    )
    data = scanner_snapshot(
        repo, (_share_instrument("SHARE_AAPL"),), datetime(2026, 9, 18, 6, 5, tzinfo=UTC)
    )
    assert data.instruments[0].current_filter.filter_freshness == "CURRENT"


def _snapshot_row():
    repo = ScannerRepository(
        bars_by_timeframe={Timeframe.D1: _d1_series()},
        filter_snapshot=_filter_snapshot(),
    )
    return scanner_snapshot(
        repo, (_share_instrument(),), datetime(2026, 9, 18, 6, 5, tzinfo=UTC)
    )


class Runtime:
    def __init__(self, reason):
        self._reason = reason

    def status(self):
        return {
            "collection_instruments": {
                INSTRUMENT: {
                    "evaluation_modes": {
                        "MICRO": {
                            "evaluation_freshness": "STALE",
                            "failure_reason": self._reason,
                        }
                    },
                    "collection": {"state": "RECOVERING"},
                }
            }
        }


def test_blocked_filter_dependency_downgrades_filter_diagnostic():
    snapshot = _snapshot_row()
    result = attach_collection_status(snapshot, Runtime("MISSING_DAILY_CANDLE"))
    row = result.instruments[0]
    assert row.evaluation_freshness == "STALE"
    assert row.current_filter.filter_freshness == "STALE"
    assert row.current_filter.stale_reason == "MISSING_DAILY_CANDLE"


def test_signal_only_failure_does_not_invalidate_filter():
    snapshot = _snapshot_row()
    result = attach_collection_status(snapshot, Runtime("SIGNAL_LEVEL_REJECTED"))
    row = result.instruments[0]
    assert row.evaluation_freshness == "STALE"
    # The filter dependency is healthy; a signal-only block must not lie about it.
    assert row.current_filter.filter_freshness == "CURRENT"
