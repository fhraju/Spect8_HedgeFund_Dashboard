"""Share filter diagnostic must read the evaluator's dependency history.

The scanner diagnostic previously evaluated dependency validity from the
canonical projection only, while the evaluator builds share filters from the
bounded native+canonical share history. An actually-CURRENT AAPL/MSFT filter
could therefore be presented as STALE, and an actually-incomplete dependency
could be missed. The diagnostic now uses the same authoritative history and
the same continuity rule (``current_filter_components``); native bars are not
inserted into canonical storage and dependency validation is not weakened.
"""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta, timezone

import pytest

from backend.app.dashboard_api import scanner_snapshot
from backend.app.market_data.share_catalog import platform_instruments
from backend.tests.test_share_integration import NOW, runtime

UTC = timezone.utc


def _instrument(instrument_id):
    return next(
        item
        for item in platform_instruments((instrument_id,))
        if item.instrument_id == instrument_id
    )


def _delete_canonical_h1(repo, instrument_id):
    """Model a canonical projection that lacks the native share H1 series."""
    with closing(repo._connect()) as db:
        db.execute(
            "DELETE FROM canonical_bars WHERE provider='MARKET_DATA_PLATFORM' "
            "AND instrument_id=? AND timeframe='H1'",
            (instrument_id,),
        )
        db.commit()


@pytest.mark.parametrize("symbol", ["MSFT", "AAPL"])
def test_share_filter_diagnostic_matches_evaluator_dependency_history(tmp_path, symbol):
    instrument_id = "SHARE_" + symbol
    value, repo, _contract, _batch = runtime(tmp_path, symbol=symbol)
    value.run_once(available_as_of=NOW)
    assert (
        value.status()["collection_instruments"][instrument_id][
            "evaluation_modes"
        ]["MICRO"]["evaluation_freshness"]
        == "CURRENT"
    )
    instrument = _instrument(instrument_id)
    _delete_canonical_h1(repo, instrument_id)

    # Canonical-only diagnostic cannot establish currency without native H1.
    without = scanner_snapshot(repo, (instrument,), NOW + timedelta(minutes=1))
    assert without.instruments[0].current_filter.filter_freshness != "CURRENT"

    # The evaluator's bounded native+canonical history reports CURRENT.
    with_authority = scanner_snapshot(
        repo,
        (instrument,),
        NOW + timedelta(minutes=1),
        dependency_view=value.dependency_filter_view,
    )
    row = with_authority.instruments[0]
    assert row.current_filter.filter_freshness == "CURRENT"
    assert row.current_filter.stale_reason is None
    assert row.filter_readiness == "CURRENT"


@pytest.mark.parametrize("symbol", ["MSFT", "AAPL"])
def test_share_filter_diagnostic_reports_incomplete_dependency(tmp_path, symbol):
    instrument_id = "SHARE_" + symbol
    value, repo, _contract, _batch = runtime(tmp_path, symbol=symbol)
    value.run_once(available_as_of=NOW)
    history = value._current_histories[instrument_id]
    # Remove a required H1 inside the filter's dependency window.
    value._current_histories[instrument_id] = replace(
        history, h1=history.h1[:-2] + history.h1[-1:]
    )
    instrument = _instrument(instrument_id)
    snapshot = scanner_snapshot(
        repo,
        (instrument,),
        NOW + timedelta(minutes=1),
        dependency_view=value.dependency_filter_view,
    )
    row = snapshot.instruments[0]
    assert row.current_filter.filter_freshness == "STALE"
    assert (
        row.current_filter.stale_reason
        == "SHARE_FILTER_REQUIRED_H1_MISSING_OR_INVALID"
    )
    assert row.filter_readiness == "STALE"
