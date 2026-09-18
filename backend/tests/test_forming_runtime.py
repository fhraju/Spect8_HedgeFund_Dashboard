from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.app.market_data.forming_runtime import validate_partial
from backend.tests.test_isolated_platform import Source, runtime
from backend.tests.test_phase22a4_cutover import NOW, partial

AT = NOW + timedelta(minutes=5)


def prepare(tmp_path, *, stale=False):
    value, repo = runtime(tmp_path, Source("FX_USD_JPY", "IG_LIVE", stale=stale))
    for inst, child in value._children.items():
        child._forming_directions = Mock(return_value=(("BUY",), None))
    # Gateways intentionally expose read(); wrap it to deliver authoritative partials.
    for inst in value._children:
        gateway = value.gateway_for(inst)
        original = gateway.read

        def read(*args, _original=original, _inst=inst, **kwargs):
            batch = _original(*args, **kwargs)
            return replace(
                batch,
                partial_bar_snapshots=tuple(
                    replace(
                        partial(tf, as_of=AT),
                        instrument_id="FX_" + _inst,
                        source_provider_id=value.authority_for(_inst),
                    )
                    for tf in ("M30", "H1")
                ),
            )

        gateway.read = read
    return value, repo


def test_background_startup_publishes_all_modes_and_cache(tmp_path):
    value, _ = prepare(tmp_path)
    value.run_once(available_as_of=AT)
    view = value.forming_view(as_of=AT)
    assert len(view["forming_candidates"]) == 8
    assert {c["state"] for c in view["forming_candidates"]} == {"READY"}
    assert len(view["forming"]) == 8
    assert all(s["instrument_id"] == s["instrument"] for s in view["forming"])
    calls = value._children["EUR_USD"]._forming_directions.call_count
    value.refresh_forming(AT + timedelta(seconds=10))
    assert value._children["EUR_USD"]._forming_directions.call_count == calls
    assert value.forming_view(as_of=AT)["forming"] == view["forming"]


def test_stale_live_does_not_suppress_demo(tmp_path):
    value, _ = prepare(tmp_path, stale=True)
    value.run_once(available_as_of=AT)
    view = value.forming_view(as_of=AT)
    assert {s["authority"] for s in view["forming"]} == {"IG_DEMO"}
    assert all(
        c["state"] == "STALE"
        for c in view["forming_candidates"]
        if c["authority"] == "IG_LIVE"
    )


def test_missing_m30_does_not_suppress_h1_or_h4(tmp_path):
    value, _ = prepare(tmp_path)
    value.run_once(available_as_of=AT)
    child = value._children["EUR_USD"]
    child._forming_batch = replace(
        child._forming_batch,
        partial_bar_snapshots=tuple(
            p
            for p in child._forming_batch.partial_bar_snapshots
            if p.timeframe != "M30"
        ),
    )
    value.refresh_forming(AT)
    rows = [
        c
        for c in value.forming_view(as_of=AT)["forming_candidates"]
        if c["instrument_id"] == "EUR_USD"
    ]
    assert [c["state"] for c in rows] == ["WAITING_FOR_DATA", "READY", "READY", "READY"]


def test_disappears_repair_invalidation_and_empty_ready(tmp_path):
    value, _ = prepare(tmp_path)
    value.run_once(available_as_of=AT)
    child = value._children["EUR_USD"]
    child._forming_directions.return_value = ((), None)
    batch = child._forming_batch
    # A revision to existing completed data must invalidate cached matches.
    child._forming_batch = replace(
        batch, bars=(replace(batch.bars[0], version_number=99), *batch.bars[1:])
    )
    value.refresh_forming(AT)
    view = value.forming_view(as_of=AT)
    assert not any(s["instrument_id"] == "EUR_USD" for s in view["forming"])
    assert all(
        c["state"] == "READY" and c["evaluated_at"]
        for c in view["forming_candidates"]
        if c["instrument_id"] == "EUR_USD"
    )


def test_expiration_applies_without_another_background_cycle(tmp_path):
    value, _ = prepare(tmp_path)
    value.run_once(available_as_of=AT)
    assert not value.forming_view(as_of=AT + timedelta(minutes=10, seconds=1))[
        "forming"
    ]
    assert not value.forming_view(as_of=NOW + timedelta(hours=1))["forming"]


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"source_provider_id": "IG_LIVE"}, "BLOCKED"),
        ({"component_open_times": ()}, "BLOCKED"),
        ({"component_open_times": (AT,)}, "BLOCKED"),
        ({"as_of": AT + timedelta(minutes=5)}, "BLOCKED"),
        ({"low": 99}, "BLOCKED"),
    ],
)
def test_partial_validation(change, expected):
    assert (
        validate_partial(
            replace(partial("M30", as_of=AT), **change), "IG_DEMO", "EUR_USD", AT
        )[0]
        == expected
    )


def test_candidate_exception_and_blocked_history_are_visible(tmp_path):
    value, _ = prepare(tmp_path)
    value._children["EUR_USD"]._forming_directions.side_effect = RuntimeError(
        "private details"
    )
    value.run_once(available_as_of=AT)
    view = value.forming_view(as_of=AT)
    assert len(view["forming"]) == 4
    assert {
        c["reason"]
        for c in view["forming_candidates"]
        if c["instrument_id"] == "EUR_USD"
    } == {"RuntimeError"}
    assert "private details" not in str(view)


def test_http_route_reads_cache_despite_degraded_aggregate(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    from backend.app.config import Settings
    from backend.app.main import create_app

    value, _repo = prepare(tmp_path, stale=True)
    value.run_once(available_as_of=AT)
    app = create_app(
        Settings(
            repository_root=Path(__file__).resolve().parents[2],
            database_path=tmp_path / "api.sqlite3",
            internal_api_key="test",
            polling_enabled=False,
            auto_seed_synthetic=False,
        )
    )
    app.state.platform_authority_runtime = value
    app.state.signal_lifecycle = SimpleNamespace(current_confirmed=lambda now: ())
    app.state.clock = SimpleNamespace(now=lambda: AT)
    # No lifespan: inputs already prepared, and the GET must never start collection.
    response = TestClient(app).get(
        "/signals/current", headers={"X-Spect8-Internal-Key": "test"}
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["platform_healthy"] is False
    assert len(payload["forming"]) == 4
    assert all(s["instrument_id"] == "EUR_USD" for s in payload["forming"])
    assert len(payload["forming_candidates"]) == 8
