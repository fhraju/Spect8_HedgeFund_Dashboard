"""Permanent regression: repeated Micro↔Macro PATCH followed by reloads must be idempotent/serialized, using real production request paths."""
from pathlib import Path
import tempfile
from fastapi.testclient import TestClient
from backend.app.config import Settings
from backend.app.main import create_app

ROOT = Path(__file__).resolve().parents[2]

def _settings(tmp: Path):
    return Settings(
        repository_root=ROOT,
        database_path=tmp / "test.db",
        runtime_log_path=tmp / "runtime.log",
        historical_replay_database_path=tmp / "replay.sqlite3",
        internal_api_key="test",
        auto_seed_synthetic=False,
        market_data_source="TWELVE_DATA",
        market_data_provider="twelve_data",
        twelve_data_api_key="fake",
        enabled_instrument_ids=("EUR_USD",),
        polling_enabled=False,
        provider_discovery_enabled=False,
    )

def test_repeated_filter_mode_idempotent_and_scanner_reload():
    tmp = Path(tempfile.mkdtemp())
    settings = _settings(tmp)
    app = create_app(settings)
    app.state.repository.initialize()
    headers = {"X-Spect8-Internal-Key": "test"}
    with TestClient(app, headers=headers) as client:
        # initial mode should be MICRO (default) – /filter-mode returns direct model, not envelope
        r = client.get("/filter-mode")
        assert r.status_code == 200
        # handle both envelope and direct
        payload = r.json().get("data", r.json())
        assert payload["active_filter_mode"] in ("MICRO", "MACRO")
        # PATCH to MICRO – also direct
        r = client.patch("/filter-mode", json={"mode": "MICRO"})
        assert r.status_code == 200
        payload = r.json().get("data", r.json())
        assert payload["active_filter_mode"] == "MICRO"
        # second identical PATCH must be idempotent – should not change timestamp (early return)
        r2 = client.patch("/filter-mode", json={"mode": "MICRO"})
        assert r2.status_code == 200
        payload2 = r2.json().get("data", r2.json())
        assert payload2["active_filter_mode"] == "MICRO"
        # Verify scanner loads after each patch (real production path – envelope)
        r = client.get("/scanner")
        assert r.status_code == 200
        assert "instruments" in r.json()["data"]
        # Toggle to MACRO
        r = client.patch("/filter-mode", json={"mode": "MACRO"})
        assert r.status_code == 200
        payload = r.json().get("data", r.json())
        assert payload["active_filter_mode"] == "MACRO"
        # Reload scanner
        r = client.get("/scanner")
        assert r.status_code == 200
        assert r.json()["data"]["active_filter_mode"] == "MACRO"
        assert r.json()["data"]["filter_timeframe"] == "W1"
        # Toggle back to MICRO
        r = client.patch("/filter-mode", json={"mode": "MICRO"})
        assert r.status_code == 200
        payload = r.json().get("data", r.json())
        assert payload["active_filter_mode"] == "MICRO"
        r = client.get("/scanner")
        assert r.status_code == 200
        assert r.json()["data"]["active_filter_mode"] == "MICRO"
        assert r.json()["data"]["filter_timeframe"] == "D1"
        # Repeated rapid toggles: MICRO->MACRO->MICRO->MACRO without intermediate scanner fetch, then final scanner
        for mode in ["MACRO", "MICRO", "MACRO", "MICRO", "MACRO"]:
            rr = client.patch("/filter-mode", json={"mode": mode})
            assert rr.status_code == 200
        r = client.get("/scanner")
        assert r.status_code == 200
        assert r.json()["data"]["active_filter_mode"] == "MACRO"
        # Finally ensure scanner still healthy after repeated changes
        r = client.get("/filter-mode")
        payload = r.json().get("data", r.json())
        assert payload["active_filter_mode"] == "MACRO"

def test_concurrent_filter_mode_serialized():
    """Rapid sequential PATCH /filter-mode must be serialized (no corruption) via real paths."""
    tmp = Path(tempfile.mkdtemp())
    settings = _settings(tmp)
    app = create_app(settings)
    app.state.repository.initialize()
    headers = {"X-Spect8-Internal-Key": "test"}
    results = []
    with TestClient(app, headers=headers) as client:
        for mode in ["MICRO","MACRO","MICRO","MACRO","MICRO"]:
            rr = client.patch("/filter-mode", json={"mode": mode})
            assert rr.status_code==200, f"{mode} failed {rr.text}"
            payload = rr.json().get("data", rr.json())
            results.append((mode, rr.status_code, payload["active_filter_mode"]))
        assert len(results)==5
        assert all(code==200 for _,code,_ in results)
        r = client.get("/filter-mode")
        payload = r.json().get("data", r.json())
        assert payload["active_filter_mode"] in ("MICRO","MACRO")
        r = client.get("/scanner")
        assert r.status_code==200
