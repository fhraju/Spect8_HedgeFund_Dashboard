from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.market_data.models import HealthState, ProviderHealth

ROOT = Path(__file__).resolve().parents[2]
API_KEY = "phase3a-test-key"
HEADERS = {"X-Spect8-Internal-Key": API_KEY}


def replay_settings(database_path: Path) -> Settings:
    return Settings(
        repository_root=ROOT,
        database_path=database_path,
        internal_api_key=API_KEY,
        auto_seed_synthetic=True,
    )


def live_settings(database_path: Path) -> Settings:
    return Settings(
        repository_root=ROOT,
        database_path=database_path,
        internal_api_key=API_KEY,
        auto_seed_synthetic=False,
        market_data_provider="twelve_data",
        twelve_data_api_key="test-key-not-a-secret",
        market_data_runtime_enabled=False,
    )


def test_dashboard_api_is_protected_and_backend_derived(
    tmp_path: Path,
) -> None:
    application = create_app(replay_settings(tmp_path / "dashboard.sqlite3"))
    with TestClient(application) as client:
        assert client.get("/dashboard").status_code == 401
        response = client.get("/dashboard", headers=HEADERS)
        persisted = application.state.repository.statuses()

    assert response.status_code == 200
    payload = response.json()
    data = payload["data"]
    assert payload["synthetic"] is True
    assert data["data_state"] == "HEALTHY"
    assert [item["timeframe"] for item in data["evaluations"]] == ["H1", "H4"]
    assert data["evaluations"] == persisted
    assert set(data["latest_candles"]) == {"H1", "H4", "D1"}
    assert all(
        evaluation["market_values"] is not None
        and evaluation["reason_codes"]
        for evaluation in data["evaluations"]
    )
    assert data["execution"] == {
        "enabled": False,
        "orders": 0,
        "fills": 0,
        "detail": "Read-only scanner; execution is not implemented.",
    }


def test_runtime_sync_and_projection_are_restart_idempotent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "restart.sqlite3"
    first = create_app(replay_settings(database_path))
    with TestClient(first) as client:
        original = client.get("/dashboard", headers=HEADERS).json()["data"]
        processed = first.state.repository.processed_count()
        events = first.state.repository.event_count()
        bars = first.state.repository.canonical_bar_count()
        assert original["provider_sync"]["last_success_at"] is not None

    restarted = create_app(replay_settings(database_path))
    with TestClient(restarted) as client:
        after_restart = client.get("/dashboard", headers=HEADERS).json()["data"]
        assert restarted.state.repository.processed_count() == processed
        assert restarted.state.repository.event_count() == events
        assert restarted.state.repository.canonical_bar_count() == bars

    assert after_restart["evaluations"] == original["evaluations"]
    assert after_restart["recent_events"] == original["recent_events"]
    assert after_restart["provider_sync"]["last_success_at"] == original[
        "provider_sync"
    ]["last_success_at"]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (HealthState.STALE, "STALE"),
        (HealthState.DATA_UNAVAILABLE, "DATA_UNAVAILABLE"),
        (HealthState.QUARANTINED, "QUARANTINED"),
    ],
)
def test_dashboard_keeps_persisted_evaluations_during_provider_failures(
    tmp_path: Path,
    state: HealthState,
    expected: str,
) -> None:
    application = create_app(
        replay_settings(tmp_path / f"{state.value}.sqlite3")
    )
    with TestClient(application) as client:
        repository = application.state.repository
        original_statuses = repository.statuses()
        provider_id = application.state.provider.identity.provider_id
        repository.update_provider_health(
            ProviderHealth(
                provider_id=provider_id,
                state=state,
                checked_at=datetime(2026, 2, 4, tzinfo=timezone.utc),
                latest_completed_close=datetime(
                    2026, 2, 3, tzinfo=timezone.utc
                ),
                freshness_seconds=86400,
                detail="Deterministic failure-state test.",
                synthetic=True,
            )
        )
        data = client.get("/dashboard", headers=HEADERS).json()["data"]

    assert data["data_state"] == expected
    assert data["stale"] is True
    assert data["evaluations"] == original_statuses
    assert data["execution"]["orders"] == 0
    assert data["execution"]["fills"] == 0


def test_live_empty_state_and_replay_guard_do_not_touch_network(
    tmp_path: Path,
) -> None:
    application = create_app(live_settings(tmp_path / "live-empty.sqlite3"))
    with TestClient(application) as client:
        dashboard = client.get("/dashboard", headers=HEADERS)
        replay = client.post("/synthetic/replay", headers=HEADERS)

    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["data_state"] == "EMPTY"
    assert dashboard.json()["data"]["evaluations"] == []
    assert replay.status_code == 409


def test_partial_database_state_is_explicit(tmp_path: Path) -> None:
    application = create_app(replay_settings(tmp_path / "partial.sqlite3"))
    with TestClient(application) as client:
        repository = application.state.repository
        with repository._connect() as connection:
            connection.execute(
                "DELETE FROM instrument_status WHERE timeframe = 'H4'"
            )
        data = client.get("/dashboard", headers=HEADERS).json()["data"]

    assert data["data_state"] == "PARTIAL"
    assert [item["timeframe"] for item in data["evaluations"]] == ["H1"]


@pytest.mark.parametrize("seconds", [59, 901])
def test_runtime_polling_interval_is_bounded(
    tmp_path: Path, seconds: int
) -> None:
    configured = live_settings(tmp_path / "bounded.sqlite3")
    invalid = Settings(
        repository_root=configured.repository_root,
        database_path=configured.database_path,
        internal_api_key=configured.internal_api_key,
        auto_seed_synthetic=False,
        market_data_provider="twelve_data",
        twelve_data_api_key="test-key-not-a-secret",
        market_data_poll_seconds=seconds,
    )
    with pytest.raises(ValueError, match="between 60 and 900"):
        invalid.validate()
