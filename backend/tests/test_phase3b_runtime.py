from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.market_data.runtime_support import (
    BoundaryAwareSchedule,
    RuntimeAlreadyActiveError,
    SanitizedJsonFormatter,
    SingleRuntimeLock,
    configure_runtime_logging,
)
from backend.app.observation import projected_request_usage
from backend.app.observation_cli import build_report
from backend.app.repository import SQLiteProjectionRepository

ROOT = Path(__file__).resolve().parents[2]
HEADERS = {"X-Spect8-Internal-Key": "phase3b-test"}


def settings(database_path: Path) -> Settings:
    return Settings(
        repository_root=ROOT,
        database_path=database_path,
        internal_api_key="phase3b-test",
        auto_seed_synthetic=False,
    )


class LongWaitSchedule:
    safety_delay_seconds = 30
    health_check_seconds = 300

    def seconds_until_next_poll(self, now):
        return 3600


async def start_without_poll(runtime):
    runtime._schedule = LongWaitSchedule()

    def forbidden_poll():
        raise AssertionError("offline lifecycle test must not poll a provider")

    runtime.run_once = forbidden_poll
    task = asyncio.create_task(runtime.run())
    for _ in range(1_000):
        if runtime.status()["running"] or task.done():
            break
        await asyncio.sleep(0)
    assert task.done() is False
    assert runtime.status()["running"] is True
    return task


def runtime_sessions(repository: SQLiteProjectionRepository):
    with closing(repository._connect()) as connection:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT session_id, provider, started_at, ended_at, exit_reason
                FROM runtime_sessions
                ORDER BY started_at, session_id
                """
            ).fetchall()
        ]


def test_single_runtime_lock_rejects_second_owner_and_releases(
    tmp_path: Path,
) -> None:
    database = tmp_path / "single.sqlite3"
    first = SingleRuntimeLock(database)
    second = SingleRuntimeLock(database)
    first.acquire()
    try:
        with pytest.raises(RuntimeAlreadyActiveError):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    assert second.acquired is True
    second.release()


def test_boundary_schedule_uses_safety_delay_and_health_bound() -> None:
    schedule = BoundaryAwareSchedule(
        safety_delay_seconds=30, health_check_seconds=300
    )
    before_delay = datetime(
        2026, 7, 31, 10, 0, 10, tzinfo=timezone.utc
    )
    after_delay = datetime(
        2026, 7, 31, 10, 0, 31, tzinfo=timezone.utc
    )
    assert schedule.seconds_until_next_poll(before_delay) == 20
    assert schedule.seconds_until_next_poll(after_delay) == 300


def test_runtime_starts_immediately_and_stops_gracefully(
    tmp_path: Path,
) -> None:
    application = create_app(settings(tmp_path / "graceful.sqlite3"))
    with TestClient(application):
        runtime = application.state.market_data_runtime
        original = runtime.run_once

        def one_poll():
            result = original()
            runtime.stop()
            return result

        runtime.run_once = one_poll
        asyncio.run(runtime.run())
        report = application.state.repository.observation_report(
            "SYNTHETIC_UTC_V1", "SYNTH_XAUUSD"
        )

    assert report["runtime_sessions"] == 1
    assert report["polls"] == 1
    assert report["runtime_uptime_seconds"] == 0
    assert runtime.status()["running"] is False
    assert runtime.status()["single_runtime_lock_acquired"] is False


def test_runtime_records_a_new_session_across_graceful_restart(
    tmp_path: Path,
) -> None:
    application = create_app(
        replace(
            settings(tmp_path / "graceful-restart.sqlite3"),
            startup_backfill_enabled=False,
        )
    )
    repository = application.state.repository
    repository.initialize()
    runtime = application.state.market_data_runtime

    async def exercise() -> tuple[str, str]:
        first_task = await start_without_poll(runtime)
        first_session_id = runtime.status()["session_id"]
        runtime.stop()
        await first_task

        second_task = await start_without_poll(runtime)
        second_session_id = runtime.status()["session_id"]
        runtime.stop()
        await second_task
        return first_session_id, second_session_id

    first_session_id, second_session_id = asyncio.run(exercise())
    sessions = runtime_sessions(repository)

    assert application.state.provider.identity.synthetic is True
    assert first_session_id != second_session_id
    assert len(sessions) == 2
    assert {row["session_id"] for row in sessions} == {
        first_session_id,
        second_session_id,
    }
    assert all(row["ended_at"] is not None for row in sessions)
    assert all(row["exit_reason"] == "GRACEFUL_STOP" for row in sessions)
    assert runtime.status()["running"] is False
    assert runtime.status()["single_runtime_lock_acquired"] is False


def test_runtime_recovers_session_left_by_abruptly_terminated_process(
    tmp_path: Path,
) -> None:
    database = tmp_path / "abrupt-restart.sqlite3"
    child = "\n".join(
        (
            "import os",
            "import sys",
            "from pathlib import Path",
            "from backend.app.market_data.runtime_support import SingleRuntimeLock",
            "from backend.app.repository import SQLiteProjectionRepository",
            "database = Path(sys.argv[1])",
            "repository = SQLiteProjectionRepository(database)",
            "repository.initialize()",
            "runtime_lock = SingleRuntimeLock(database)",
            "runtime_lock.acquire()",
            "repository.start_runtime_session(",
            "    'abrupt-session', 'SYNTHETIC_UTC_V1',",
            "    '2026-02-07T19:59:00Z'",
            ")",
            "os._exit(0)",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", child, str(database)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr

    application = create_app(
        replace(
            settings(database),
            startup_backfill_enabled=False,
        )
    )
    repository = application.state.repository
    repository.initialize()
    runtime = application.state.market_data_runtime

    async def exercise() -> tuple[str, list[dict]]:
        task = await start_without_poll(runtime)
        new_session_id = runtime.status()["session_id"]
        active_sessions = runtime_sessions(repository)
        runtime.stop()
        await task
        return new_session_id, active_sessions

    new_session_id, active_sessions = asyncio.run(exercise())
    final_sessions = runtime_sessions(repository)

    assert application.state.provider.identity.synthetic is True
    assert [row["session_id"] for row in active_sessions] == [
        "abrupt-session",
        new_session_id,
    ]
    assert active_sessions[0]["ended_at"] == "2026-02-07T20:00:01Z"
    assert active_sessions[0]["exit_reason"] == "INTERRUPTED_RESTART"
    assert active_sessions[1]["ended_at"] is None
    assert active_sessions[1]["exit_reason"] is None
    assert final_sessions[1]["exit_reason"] == "GRACEFUL_STOP"
    assert runtime.status()["running"] is False
    assert runtime.status()["single_runtime_lock_acquired"] is False


def test_duplicate_runtime_is_blocked_without_reconciling_owner_session(
    tmp_path: Path,
) -> None:
    database = tmp_path / "duplicate-runtime.sqlite3"
    first_application = create_app(
        replace(settings(database), startup_backfill_enabled=False)
    )
    second_application = create_app(
        replace(settings(database), startup_backfill_enabled=False)
    )
    repository = first_application.state.repository
    repository.initialize()
    first = first_application.state.market_data_runtime
    second = second_application.state.market_data_runtime

    async def exercise() -> tuple[str, dict]:
        first_task = await start_without_poll(first)
        first_session_id = first.status()["session_id"]
        await second.run()
        second_status = second.status()
        assert first.status()["running"] is True
        first.stop()
        await first_task
        return first_session_id, second_status

    first_session_id, second_status = asyncio.run(exercise())
    sessions = runtime_sessions(repository)

    assert first_application.state.provider.identity.synthetic is True
    assert second_application.state.provider.identity.synthetic is True
    assert second_status["running"] is False
    assert second_status["lock_conflict"] is True
    assert second_status["single_runtime_lock_acquired"] is False
    assert [row["session_id"] for row in sessions] == [first_session_id]
    assert sessions[0]["exit_reason"] == "GRACEFUL_STOP"


def test_runtime_start_failure_logs_releases_lock_and_records_no_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = tmp_path / "startup-failure.sqlite3"
    application = create_app(
        replace(settings(database), startup_backfill_enabled=False)
    )
    repository = application.state.repository
    repository.initialize()
    runtime = application.state.market_data_runtime
    runtime._logger = logging.getLogger("spect8.test.runtime.startup")

    def fail_start(*args, **kwargs):
        raise RuntimeError("simulated offline session-write failure")

    monkeypatch.setattr(
        repository, "reconcile_and_start_runtime_session", fail_start
    )
    with caplog.at_level(logging.ERROR, logger=runtime._logger.name):
        asyncio.run(runtime.run())

    assert application.state.provider.identity.synthetic is True
    assert runtime.status()["running"] is False
    assert runtime.status()["single_runtime_lock_acquired"] is False
    assert runtime_sessions(repository) == []
    assert "runtime_start_failed" in caplog.messages

    successor = SingleRuntimeLock(database)
    successor.acquire()
    assert successor.acquired is True
    successor.release()


def test_runtime_skips_immediate_poll_when_startup_backfill_is_disabled(
    tmp_path: Path,
) -> None:
    configured = replace(
        settings(tmp_path / "no-startup-backfill.sqlite3"),
        startup_backfill_enabled=False,
    )
    application = create_app(configured)
    with TestClient(application):
        runtime = application.state.market_data_runtime
        calls = 0

        class Schedule:
            safety_delay_seconds = 30
            health_check_seconds = 300

            def seconds_until_next_poll(self, now):
                return 3600

        runtime._schedule = Schedule()

        def forbidden_poll():
            nonlocal calls
            calls += 1
            raise AssertionError("startup poll must remain disabled")

        runtime.run_once = forbidden_poll

        async def exercise() -> None:
            task = asyncio.create_task(runtime.run())
            while not runtime.status()["running"]:
                await asyncio.sleep(0)
            runtime.stop()
            await task

        asyncio.run(exercise())

    assert calls == 0
    assert runtime.status()["startup_backfill_enabled"] is False


def test_observation_report_aggregates_restarts_requests_and_recovery(
    tmp_path: Path,
) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "report.sqlite3")
    repository.initialize()
    repository.start_runtime_session(
        "one", "TWELVE_DATA", "2026-07-31T00:00:00Z"
    )
    repository.record_runtime_poll(
        session_id="one",
        provider_id="TWELVE_DATA",
        attempted_at="2026-07-31T00:01:00Z",
        completed_at="2026-07-31T00:01:00Z",
        duration_ms=100,
        health_state="DATA_UNAVAILABLE",
        previous_health_state=None,
        telemetry={
            "network_attempts": 2,
            "successful_requests": 1,
            "failed_requests": 1,
            "rate_limit_responses": 1,
            "network_timeouts": 0,
            "cache_hits": 0,
            "series_attempts": {"H1": 1, "H4": 1, "D1": 0},
            "completed_discoveries": {"H1": 0, "H4": 0, "D1": 0},
        },
        canonical_bars_inserted=0,
        evaluations_created=0,
        duplicate_evaluations_prevented=0,
        events_created=0,
        issues=("RATE_LIMIT",),
    )
    repository.end_runtime_session(
        "one", "2026-07-31T00:02:00Z", "GRACEFUL_STOP"
    )
    repository.start_runtime_session(
        "two", "TWELVE_DATA", "2026-07-31T00:03:00Z"
    )
    repository.record_runtime_poll(
        session_id="two",
        provider_id="TWELVE_DATA",
        attempted_at="2026-07-31T00:04:00Z",
        completed_at="2026-07-31T00:04:00Z",
        duration_ms=90,
        health_state="RECOVERED",
        previous_health_state="DATA_UNAVAILABLE",
        telemetry={
            "network_attempts": 3,
            "successful_requests": 3,
            "failed_requests": 0,
            "rate_limit_responses": 0,
            "network_timeouts": 0,
            "cache_hits": 2,
            "series_attempts": {"H1": 1, "H4": 1, "D1": 1},
            "completed_discoveries": {"H1": 1, "H4": 1, "D1": 1},
        },
        canonical_bars_inserted=66,
        evaluations_created=2,
        duplicate_evaluations_prevented=0,
        events_created=12,
        issues=(),
    )
    repository.end_runtime_session(
        "two", "2026-07-31T00:05:00Z", "GRACEFUL_STOP"
    )

    report = repository.observation_report(
        "TWELVE_DATA",
        "EUR/USD",
        as_of=datetime(2026, 7, 31, 0, 5, tzinfo=timezone.utc),
    )

    assert report["runtime_sessions"] == 2
    assert report["restarts"] == 1
    assert report["runtime_uptime_seconds"] == 240
    assert report["request_metrics"]["network_attempts"] == 5
    assert report["request_metrics"]["rate_limit_responses"] == 1
    assert report["request_metrics"]["attempts_by_timeframe"] == {
        "H1": 2,
        "H4": 2,
        "D1": 1,
    }
    assert report["completed_candle_discoveries"] == {
        "H1": 1,
        "H4": 1,
        "D1": 1,
    }
    assert report["evaluations_created"] == 2
    assert report["events_created"] == 12
    assert report["unhealthy_periods"] == [
        {
            "started_at": "2026-07-31T00:01:00Z",
            "recovered_at": "2026-07-31T00:04:00Z",
            "recovery_seconds": 180,
        }
    ]
    assert report["orders"] == report["fills"] == 0


def test_request_projections_are_formulaic_not_quota_claims() -> None:
    assert projected_request_usage(1)["requests_per_day"] == 31
    assert projected_request_usage(3)["requests_per_day"] == 93
    assert projected_request_usage(10)["requests_per_day"] == 310
    assert projected_request_usage(25)["requests_per_day"] == 775
    assert projected_request_usage(50)["requests_per_day"] == 1550


def test_structured_logging_redacts_secrets_and_rotates(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "runtime.log"
    logger = configure_runtime_logging(
        log_path, max_bytes=65_536, backup_count=2
    )
    secret = "private-provider-key-value"
    for index in range(100):
        logger.info(
            "provider_check",
            extra={
                "event_data": {
                    "Authorization": f"apikey {secret}",
                    "url": (
                        "https://example.invalid/data?apikey="
                        f"{secret}&n={index}"
                    ),
                    "padding": "x" * 1200,
                }
            },
        )
    for handler in logger.handlers:
        handler.flush()
    combined = "".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.glob("runtime.log*")
    )
    assert secret not in combined
    assert "[REDACTED]" in combined
    assert (tmp_path / "runtime.log.1").exists()


def test_json_formatter_never_serializes_exception_details() -> None:
    formatter = SanitizedJsonFormatter()
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "Authorization: Bearer secret-token",
        (),
        None,
    )
    payload = json.loads(formatter.format(record))
    assert "secret-token" not in json.dumps(payload)


def test_runtime_status_api_and_cli_report_are_sanitized(
    tmp_path: Path,
) -> None:
    database = tmp_path / "status.sqlite3"
    application = create_app(settings(database))
    with TestClient(application) as client:
        assert client.get("/runtime/status").status_code == 401
        response = client.get("/runtime/status", headers=HEADERS)
    cli = build_report(database)

    assert response.status_code == 200
    assert response.json()["data"]["observation"]["orders"] == 0
    assert response.json()["data"]["observation"]["fills"] == 0
    assert cli["read_only"] == {"orders": 0, "fills": 0}
