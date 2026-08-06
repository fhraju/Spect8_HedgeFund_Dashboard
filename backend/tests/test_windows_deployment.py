from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.market_data.multi_provider import (
    MultiInstrumentTwelveDataProvider,
)
from backend.app.market_data.twelve_data_provider import TwelveDataProvider


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_WINDOWS = ROOT / "deploy" / "windows"
sys.path.insert(0, str(DEPLOY_WINDOWS))

from database_tools import (  # noqa: E402
    EXPECTED_NON_EMPTY_TABLES,
    REQUIRED_TABLES,
    read_only_connection,
    validate_schema_and_data,
)
from export_database import export_database  # noqa: E402

VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "spect8_validate_imported_database",
    DEPLOY_WINDOWS / "validate-imported-database.py",
)
assert VALIDATOR_SPEC is not None and VALIDATOR_SPEC.loader is not None
VALIDATOR_MODULE = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR_MODULE)
validate_import = VALIDATOR_MODULE.validate_import


HEADERS = {"X-Spect8-Internal-Key": "deployment-test-key"}


def live_settings(database_path: Path, **overrides: object) -> Settings:
    values = {
        "repository_root": ROOT,
        "database_path": database_path,
        "historical_replay_database_path": database_path.with_name("replay.sqlite3"),
        "internal_api_key": "deployment-test-key",
        "auto_seed_synthetic": False,
        "market_data_provider": "twelve_data",
        "twelve_data_api_key": "test-key-never-sent",
        "market_data_runtime_enabled": True,
        "market_scan_enabled": True,
        "polling_enabled": False,
        "startup_backfill_enabled": False,
        "provider_discovery_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_polling_disabled_startup_makes_zero_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_discovery(self):
        raise AssertionError("provider discovery must remain disabled")

    def forbidden_request(self, params, as_of):
        raise AssertionError("Twelve Data must not be called during startup")

    monkeypatch.setattr(
        MultiInstrumentTwelveDataProvider,
        "discover_instruments",
        forbidden_discovery,
    )
    monkeypatch.setattr(TwelveDataProvider, "_request_json", forbidden_request)

    application = create_app(live_settings(tmp_path / "offline.sqlite3"))
    with TestClient(application) as client:
        health = client.get("/health").json()["data"]
        scanner = client.get("/scanner", headers=HEADERS)

    assert scanner.status_code == 200
    assert health["provider_health"]["state"] == "POLLING_DISABLED"
    assert health["operations"] == {
        "polling_enabled": False,
        "polling_state": "DISABLED_BY_CONFIGURATION",
        "startup_backfill_enabled": False,
        "provider_discovery_enabled": False,
    }
    assert application.state.market_data_runtime.status()["running"] is False
    assert application.state.provider.telemetry().network_attempts == 0


def test_production_startup_refuses_missing_external_database(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / "spect8.db"
    settings = live_settings(
        missing,
        application_environment="production",
    )

    with pytest.raises(FileNotFoundError, match="production database does not exist"):
        create_app(settings)


def test_explicit_polling_setting_overrides_legacy_runtime_flags(
    tmp_path: Path,
) -> None:
    disabled = live_settings(tmp_path / "disabled.sqlite3")
    enabled = live_settings(
        tmp_path / "enabled.sqlite3",
        polling_enabled=True,
        market_data_runtime_enabled=False,
        market_scan_enabled=False,
    )

    assert disabled.effective_polling_enabled is False
    assert enabled.effective_polling_enabled is True


def test_production_environment_controls_are_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPECT8_APPLICATION_ENVIRONMENT", "production")
    monkeypatch.setenv("SPECT8_DATABASE_PATH", str(tmp_path / "spect8.db"))
    monkeypatch.setenv("SPECT8_POLLING_ENABLED", "false")
    monkeypatch.setenv("SPECT8_STARTUP_BACKFILL_ENABLED", "false")
    monkeypatch.setenv("SPECT8_PROVIDER_DISCOVERY_ENABLED", "false")

    configured = Settings.from_environment()

    assert configured.is_production is True
    assert configured.database_path == tmp_path / "spect8.db"
    assert configured.effective_polling_enabled is False
    assert configured.startup_backfill_enabled is False
    assert configured.provider_discovery_enabled is False


def test_export_and_import_validation_are_offline_and_source_immutable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE canonical_bars (close_time_utc TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE instrument_status "
            "(status_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        for table in REQUIRED_TABLES:
            if table not in {"canonical_bars", "instrument_status"}:
                connection.execute(f'CREATE TABLE "{table}" (id INTEGER)')
        connection.execute(
            "INSERT INTO canonical_bars VALUES ('2026-08-06T09:00:00Z')"
        )
        connection.execute(
            "INSERT INTO instrument_status VALUES (?, ?)",
            (
                '{"signal_bar_close_time":"2026-08-06T09:00:00Z"}',
                "2026-08-06T09:00:30Z",
            ),
        )
        for table in EXPECTED_NON_EMPTY_TABLES:
            if table not in {"canonical_bars", "instrument_status"}:
                connection.execute(f'INSERT INTO "{table}" VALUES (1)')
        connection.commit()

    original = source.read_bytes()
    backup = export_database(source, tmp_path / "exports")
    validate_import(backup)

    assert source.read_bytes() == original
    assert backup.is_file()
    with read_only_connection(backup) as connection:
        counts, timestamps = validate_schema_and_data(
            connection,
            require_non_empty=True,
        )
    assert counts["canonical_bars"] == 1
    assert timestamps["latest_evaluation_close"] == "2026-08-06T09:00:00Z"
