from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.domain import Bar, EventType
from backend.app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
API_KEY = "test-internal-key"
HEADERS = {"X-Spect8-Internal-Key": API_KEY}
SELECTED_CASES = ("confirmed_buy_h1_01", "confirmed_sell_h4_01")


def settings(database_path: Path) -> Settings:
    return Settings(
        repository_root=ROOT,
        database_path=database_path,
        internal_api_key=API_KEY,
        selected_cases=SELECTED_CASES,
        auto_seed_synthetic=True,
    )


def expected(case_id: str) -> dict:
    return json.loads(
        (ROOT / "golden" / "cases" / case_id / "expected.json").read_text(
            encoding="utf-8"
        )
    )


def test_domain_models_are_immutable(tmp_path: Path) -> None:
    application = create_app(settings(tmp_path / "immutable.sqlite3"))
    with TestClient(application):
        adapted = application.state.service._adapter.load(SELECTED_CASES[0])
        bar: Bar = adapted.bar_event.bar
        with pytest.raises(FrozenInstanceError):
            bar.close = bar.open  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            adapted.filter_result.buy_matched = False  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            adapted.signal_result.confirmed_buy = False  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            adapted.levels_result.target = adapted.levels_result.entry_reference  # type: ignore[misc, union-attr]
        with pytest.raises(FrozenInstanceError):
            adapted.status.dashboard_state = "WATCHING"  # type: ignore[misc]


def test_exact_event_order_for_both_confirmed_cases(tmp_path: Path) -> None:
    application = create_app(settings(tmp_path / "order.sqlite3"))
    expected_order = [
        EventType.BAR_CLOSED.value,
        EventType.FILTER_EVALUATED.value,
        EventType.FILTER_MATCHED.value,
        EventType.SIGNAL_EVALUATED.value,
        EventType.SIGNAL_CONFIRMED.value,
        EventType.LEVELS_CALCULATED.value,
        EventType.STATUS_PROJECTED.value,
    ]
    with TestClient(application):
        events = application.state.repository.events()
    assert len(events) == 14
    for case_id in SELECTED_CASES:
        trace = [
            event["event_type"]
            for event in events
            if event["source_case_id"] == case_id
        ]
        assert trace == expected_order


def test_h1_and_h4_are_independent_projections(tmp_path: Path) -> None:
    application = create_app(settings(tmp_path / "independent.sqlite3"))
    with TestClient(application):
        statuses = application.state.repository.statuses()
    assert {(status["timeframe"], status["source_case_id"]) for status in statuses} == {
        ("H1", "confirmed_buy_h1_01"),
        ("H4", "confirmed_sell_h4_01"),
    }
    by_timeframe = {status["timeframe"]: status for status in statuses}
    assert by_timeframe["H1"]["signal_result"]["confirmed_buy"] is True
    assert by_timeframe["H1"]["signal_result"]["confirmed_sell"] is False
    assert by_timeframe["H4"]["signal_result"]["confirmed_buy"] is False
    assert by_timeframe["H4"]["signal_result"]["confirmed_sell"] is True
    assert by_timeframe["H1"]["idempotency_key"] != by_timeframe["H4"][
        "idempotency_key"
    ]


def test_replaying_same_candles_creates_no_duplicate_events(tmp_path: Path) -> None:
    application = create_app(settings(tmp_path / "replay.sqlite3"))
    with TestClient(application) as client:
        initial_events = application.state.repository.event_count()
        response = client.post("/synthetic/replay", headers=HEADERS)
        assert response.status_code == 200
        assert all(item["replayed"] for item in response.json()["data"])
        assert all(item["events_created"] == 0 for item in response.json()["data"])
        assert application.state.repository.event_count() == initial_events == 14
        assert application.state.repository.processed_count() == 2


def test_status_and_event_history_survive_application_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "restart.sqlite3"
    first = create_app(settings(database_path))
    with TestClient(first):
        original_statuses = first.state.repository.statuses()
        assert first.state.repository.event_count() == 14

    restarted = create_app(settings(database_path))
    with TestClient(restarted):
        assert restarted.state.repository.statuses() == original_statuses
        assert restarted.state.repository.processed_count() == 2
        assert restarted.state.repository.event_count() == 14


@pytest.mark.parametrize(
    "path",
    ["/instruments", "/statuses", "/filtered", "/signals", "/events"],
)
def test_unauthenticated_data_api_access_is_rejected(
    tmp_path: Path, path: str
) -> None:
    application = create_app(settings(tmp_path / f"{path[1:]}.sqlite3"))
    with TestClient(application) as client:
        response = client.get(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_every_api_response_is_clearly_synthetic(tmp_path: Path) -> None:
    application = create_app(settings(tmp_path / "synthetic.sqlite3"))
    with TestClient(application) as client:
        for path, headers in (
            ("/health", {}),
            ("/instruments", HEADERS),
            ("/statuses", HEADERS),
            ("/filtered", HEADERS),
            ("/signals", HEADERS),
            ("/events", HEADERS),
        ):
            response = client.get(path, headers=headers)
            assert response.status_code == 200
            payload = response.json()
            assert payload["synthetic"] is True
            assert "SYNTHETIC" in payload["notice"]
        for status in client.get("/statuses", headers=HEADERS).json()["data"]:
            assert status["synthetic"] is True
        for event in client.get("/events", headers=HEADERS).json()["data"]:
            assert event["synthetic"] is True


def test_api_statuses_match_selected_frozen_expected_results(
    tmp_path: Path,
) -> None:
    application = create_app(settings(tmp_path / "expected.sqlite3"))
    with TestClient(application) as client:
        response = client.get("/statuses", headers=HEADERS)
        assert response.status_code == 200
        statuses = {
            status["source_case_id"]: status for status in response.json()["data"]
        }

    assert set(statuses) == set(SELECTED_CASES)
    for case_id in SELECTED_CASES:
        golden = expected(case_id)
        status = statuses[case_id]
        classification = golden["classification"]
        assert status["timeframe"] == golden["timeframe"]
        assert status["dashboard_state"] == classification["dashboard_state"]
        assert status["filter_result"] == {
            "buy_matched": classification["buy_filter_matched"],
            "sell_matched": classification["sell_filter_matched"],
            "daily_buy_level": golden["indicators"]["daily_buy_level"],
            "daily_sell_level": golden["indicators"]["daily_sell_level"],
        }
        assert status["signal_result"] == {
            "technical_buy": classification["technical_buy_signal"],
            "technical_sell": classification["technical_sell_signal"],
            "confirmed_buy": classification["confirmed_buy"],
            "confirmed_sell": classification["confirmed_sell"],
        }
        candidate = golden["candidates"][
            "buy" if classification["confirmed_buy"] else "sell"
        ]
        assert status["levels_result"] == {
            "direction": candidate["direction"],
            "entry_reference": candidate["entry_reference"],
            "raw_stop": candidate["raw_strategy_stop"],
            "display_stop": candidate["provider_adjusted_stop"],
            "target": candidate["target_3r"],
            "target_risk_usd": candidate["target_risk_usd"],
            "contract_size": candidate["display_size"],
            "contract_status": candidate["contract_status"],
        }


def test_production_backend_never_imports_reference_or_calculator() -> None:
    for path in (ROOT / "backend" / "app").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "golden.reference" not in source
        assert "reference.calculator" not in source
