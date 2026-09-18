"""Filter-freshness diagnostic: calendar-aware, evaluator-consistent, truthful count.

The scanner-row missing-H1 diagnostic must report required trading intervals
missing from the same canonical projection the evaluator reads — never
wall-clock hours (which wrongly count weekend closures) and never a
truncation cap presented as the total.
"""
from datetime import datetime, timezone

from backend.app.dashboard_api import _filter_freshness
from backend.app.repository import SQLiteProjectionRepository

UTC = timezone.utc


class FakeRepository:
    def __init__(self, closes):
        self._closes = set(closes)

    def present_h1_close_times(self, provider_id, instrument_id):
        assert provider_id == "MARKET_DATA_PLATFORM"
        return set(self._closes)


def closes(opens):
    from datetime import timedelta
    return {
        (datetime.fromisoformat(o.replace("Z", "+00:00")) + timedelta(hours=1))
        .strftime("%Y-%m-%dT%H:%M:%SZ") for o in opens
    }


def trading_opens():
    """Expected NZDJPY-style week opens Sun 2026-09-13 21Z -> Fri 2026-09-18 05Z."""
    from datetime import timedelta
    start = datetime(2026, 9, 13, 21, tzinfo=UTC)
    return [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(105)]


def test_weekend_hours_are_never_required():
    repo = FakeRepository(closes(trading_opens()))
    freshness, reason, missing, total = _filter_freshness(
        repo, "MARKET_DATA_PLATFORM", "NZD_JPY",
        "2026-09-11T21:00:00Z", "2026-09-18T06:00:00Z")
    assert freshness == "STALE" and reason == "FILTER_BEHIND_LATEST_H1"
    # Full week present: nothing missing despite 47 wall-clock weekend hours
    # in the range. The old wall-clock enumeration reported 32 here.
    assert missing == [] and total == 0


def test_two_terminal_holes_reported_exactly():
    holes = {"2026-09-13T21:00:00Z", "2026-09-15T21:00:00Z"}
    repo = FakeRepository(closes(o for o in trading_opens() if o not in holes))
    freshness, reason, missing, total = _filter_freshness(
        repo, "MARKET_DATA_PLATFORM", "NZD_JPY",
        "2026-09-11T21:00:00Z", "2026-09-18T06:00:00Z")
    assert freshness == "STALE"
    assert total == 2
    assert missing == ["2026-09-13T21:00:00Z", "2026-09-15T21:00:00Z"]


def test_healthy_pair_reports_zero():
    repo = FakeRepository(closes(trading_opens()))
    for pair in ("EUR_USD", "AUD_JPY", "NZD_USD"):
        freshness, reason, missing, total = _filter_freshness(
            repo, "MARKET_DATA_PLATFORM", pair,
            "2026-09-11T21:00:00Z", "2026-09-18T06:00:00Z")
        assert (missing, total) == ([], 0), pair


def test_total_is_truthful_beyond_display_cap():
    repo = FakeRepository(set())
    freshness, reason, missing, total = _filter_freshness(
        repo, "MARKET_DATA_PLATFORM", "NZD_JPY",
        "2026-09-11T21:00:00Z", "2026-09-18T06:00:00Z", display_limit=32)
    assert total == 105
    assert len(missing) == 32
    assert missing[0] == "2026-09-13T21:00:00Z"
    # The displayed count must come from the total, never the cap.
    assert total != len(missing)


def test_current_and_incomplete_branches_unchanged():
    repo = FakeRepository(set())
    assert _filter_freshness(repo, "MARKET_DATA_PLATFORM", "X",
                             "2026-09-18T06:00:00Z", "2026-09-18T06:00:00Z") == (
        "CURRENT", None, [], 0)
    assert _filter_freshness(repo, "MARKET_DATA_PLATFORM", "X",
                             None, "2026-09-18T06:00:00Z")[0] == "INCOMPLETE"
    assert _filter_freshness(repo, "MARKET_DATA_PLATFORM", "X",
                             "2026-09-18T06:00:00Z", None)[0] == "INCOMPLETE"
    assert _filter_freshness(repo, "MARKET_DATA_PLATFORM", "X",
                             "2026-09-18T07:00:00Z", "2026-09-18T06:00:00Z")[:2] == (
        "STALE", "FILTER_AHEAD_OF_LATEST_H1")
    # Non-platform providers get no invented schedule.
    assert _filter_freshness(repo, "OTHER", "X",
                             "2026-09-11T21:00:00Z", "2026-09-18T06:00:00Z") == (
        "STALE", "FILTER_BEHIND_LATEST_H1", [], None)


def test_real_repository_method_reads_projection(tmp_path):
    repo = SQLiteProjectionRepository(tmp_path / "proj.sqlite3")
    repo.initialize()
    assert repo.present_h1_close_times("MARKET_DATA_PLATFORM", "NZD_JPY") == set()


def test_repository_keeps_deployed_platform_contract(tmp_path):
    """Regression for the Sep-18 staging outage: the deployed platform layer
    calls existing_open_times/existing_close_times/set_active_filter_mode_atomic
    through a delegating projection, so removing them turns every instrument
    NOT_READY with `AttributeError at __getattr__:64`. They must exist and
    behave on a fresh repository."""
    from backend.app.domain import FilterMode

    repo = SQLiteProjectionRepository(tmp_path / "contract.sqlite3")
    repo.initialize()
    assert repo.existing_open_times("MARKET_DATA_PLATFORM", "NZD_JPY", "H1") == set()
    assert repo.existing_close_times("MARKET_DATA_PLATFORM", "NZD_JPY", "H1") == set()
    mode, changed = repo.set_active_filter_mode_atomic(
        FilterMode.MACRO, updated_at=datetime(2026, 9, 18, tzinfo=UTC))
    assert (mode, changed) == (FilterMode.MACRO, True)
    mode, changed = repo.set_active_filter_mode_atomic(
        FilterMode.MACRO, updated_at=datetime(2026, 9, 18, tzinfo=UTC))
    assert (mode, changed) == (FilterMode.MACRO, False)


def test_diagnostic_explosion_cannot_break_scanner_row(monkeypatch):
    """The invariant: even if _filter_freshness raises unexpectedly, the
    scanner row is still built and collection-side data passes through
    untouched (never converted to BLOCKED by a diagnostic)."""
    import backend.app.dashboard_api as api
    from backend.app.engine.models import CURRENT_D1_FILTER_V2
    from backend.app.market_data.registry import twelve_data_instruments

    def boom(*args, **kwargs):
        raise RuntimeError("diagnostic store on fire")

    monkeypatch.setattr(api, "_filter_freshness", boom)
    instrument = twelve_data_instruments()[0]

    class Repository:
        def statuses(self):
            common = {
                "provider": instrument.provider_id,
                "instrument_id": instrument.instrument_id,
                "strategy_version": CURRENT_D1_FILTER_V2,
                "signal_result": {"confirmed_buy": False, "confirmed_sell": False},
                "daily_filter_snapshot_id": "older-evaluation-snapshot",
            }
            return [
                {**common, "timeframe": "H1",
                 "filter_result": {"buy_matched": True, "sell_matched": False},
                 "signal_bar_close_time": "2026-08-05T06:00:00Z"},
                {**common, "timeframe": "H4",
                 "filter_result": {"buy_matched": False, "sell_matched": False},
                 "signal_bar_close_time": "2026-08-05T05:00:00Z"},
            ]

        def instrument_health(self, provider, instrument_id):
            return None

        def latest_candle_timestamps(self, provider, instrument_id):
            return {"H1": "2026-08-05T07:00:00Z"}

        def latest_daily_filter_snapshot(self, provider, instrument_id, version):
            return {
                "snapshot_id": "s1",
                "as_of_h1_close_time_utc": "2026-08-05T06:00:00Z",
                "final_classification": "BUY",
                "buy_matched": True,
                "sell_matched": False,
            }

    data = api.scanner_snapshot(
        Repository(), (instrument,), datetime(2026, 8, 5, 7, 1, tzinfo=UTC))
    row = data.instruments[0]
    assert row.current_filter.filter_freshness == "INCOMPLETE"
    assert row.current_filter.stale_reason == "FILTER_DIAGNOSTIC_FAILED"
    assert row.current_filter.total_missing_h1_count is None
    assert row.current_filter.status == "BUY"
