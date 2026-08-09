from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.domain import FilterMode
from backend.app.main import create_app
from backend.app.repository import SQLiteProjectionRepository
from backend.tests.test_phase3a_dashboard import API_KEY, HEADERS, replay_settings
from backend.tests.test_twelve_data_provider import AS_OF, _provider, _standard_routes


def test_active_mode_defaults_legacy_data_to_micro_and_persists(tmp_path: Path) -> None:
    database = tmp_path / "mode.sqlite3"
    repository = SQLiteProjectionRepository(database)
    repository.initialize()
    assert repository.active_filter_mode() is FilterMode.MICRO

    changed = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    assert (
        repository.set_active_filter_mode(FilterMode.MACRO, updated_at=changed)
        is FilterMode.MACRO
    )
    reopened = SQLiteProjectionRepository(database)
    reopened.initialize()
    assert reopened.active_filter_mode() is FilterMode.MACRO


def test_filter_mode_api_validates_and_does_not_relabel_micro_history(
    tmp_path: Path,
) -> None:
    application = create_app(replay_settings(tmp_path / "api-mode.sqlite3"))
    with TestClient(application) as client:
        assert client.get("/filter-mode").status_code == 401
        before = client.get("/dashboard", headers=HEADERS).json()["data"]
        assert before["active_filter_mode"] == "MICRO"
        assert len(before["evaluations"]) == 2

        invalid = client.patch(
            "/filter-mode", headers=HEADERS, json={"mode": "INVALID"}
        )
        assert invalid.status_code == 422
        assert (
            client.get("/filter-mode", headers=HEADERS).json()["active_filter_mode"]
            == "MICRO"
        )

        selected = client.patch("/filter-mode", headers=HEADERS, json={"mode": "MACRO"})
        assert selected.status_code == 200
        assert selected.json() == {
            "active_filter_mode": "MACRO",
            "filter_timeframe": "W1",
        }
        after = client.get("/dashboard", headers=HEADERS).json()["data"]

    assert after["active_filter_mode"] == "MACRO"
    assert after["filter_timeframe"] == "W1"
    assert after["evaluations"] == []
    persisted = application.state.repository.statuses()
    assert len(persisted) == 2
    assert all(
        item.get("strategy_version") == "SPECT8_MICRO_DAILY_V1_0_3"
        for item in persisted
    )
    assert API_KEY not in str(after)


def test_macro_requests_sufficient_h1_history_without_changing_micro_default() -> None:
    provider, transport = _provider(_standard_routes())
    trigger = provider.fetch_completed_bars(AS_OF)[-1]
    provider.fetch_required_history(trigger, AS_OF)
    assert transport.calls[-1]["params"]["outputsize"] == "673"

    provider.fetch_required_history_for_filter_mode(trigger, AS_OF, FilterMode.MACRO)
    assert transport.calls[-1]["params"]["outputsize"] == "1345"
