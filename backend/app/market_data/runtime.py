from __future__ import annotations

import asyncio
import logging
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import uuid4

from ..domain import primitive
from ..repository import SQLiteProjectionRepository
from .clock import Clock
from .coordinator import MarketDataCoordinator, PollResult
from .models import HealthState
from .runtime_support import (
    BoundaryAwareSchedule,
    RuntimeAlreadyActiveError,
    SingleRuntimeLock,
)


class MarketDataRuntime:
    """Single-owner boundary-aware loop around the deterministic coordinator."""

    _SUCCESS_STATES = {
        HealthState.HEALTHY,
        HealthState.RECOVERED,
        HealthState.STALE,
    }

    def __init__(
        self,
        coordinator: MarketDataCoordinator,
        repository: SQLiteProjectionRepository,
        clock: Clock,
        *,
        poll_seconds: int,
        safety_delay_seconds: int = 30,
        startup_backfill_enabled: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._repository = repository
        self._clock = clock
        self._schedule = BoundaryAwareSchedule(
            safety_delay_seconds=safety_delay_seconds,
            health_check_seconds=poll_seconds,
        )
        self._lock = Lock()
        self._runtime_lock = SingleRuntimeLock(repository.database_path)
        self._stop = asyncio.Event()
        self._startup_backfill_enabled = startup_backfill_enabled
        self._logger = logger or logging.getLogger(
            "spect8.market_data.runtime"
        )
        self._session_id = uuid4().hex
        self._running = False
        self._lock_conflict = False
        self._last_poll_at: str | None = None

    def run_once(self) -> PollResult:
        with self._lock:
            before = self._telemetry()
            previous = self._repository.provider_health(
                self._coordinator.provider.identity.provider_id
            )
            started = perf_counter()
            result = self._coordinator.poll_once()
            duration_ms = max(0, int((perf_counter() - started) * 1000))
            after = self._telemetry()
            telemetry = self._telemetry_delta(before, after)
            succeeded = result.provider_health.state in self._SUCCESS_STATES
            attempted_at = primitive(result.checked_at)
            completed_at = primitive(self._clock.now())
            self._last_poll_at = completed_at
            self._repository.record_provider_sync(
                result.provider_health.provider_id,
                state=result.provider_health.state.value,
                attempted_at=attempted_at,
                succeeded=succeeded,
                detail=result.provider_health.detail,
            )
            evaluations_created = sum(
                1
                for outcome in result.outcomes
                if outcome.idempotency_key is not None
                and not outcome.replayed
                and not outcome.issues
            )
            duplicates = sum(
                1 for outcome in result.outcomes if outcome.replayed
            ) + int(telemetry.get("duplicate_triggers_prevented", 0))
            events_created = sum(
                outcome.events_created for outcome in result.outcomes
            )
            issues = tuple(
                sorted(
                    {
                        issue
                        for outcome in result.outcomes
                        for issue in outcome.issues
                    }
                )
            )
            self._repository.record_runtime_poll(
                session_id=self._session_id,
                provider_id=result.provider_health.provider_id,
                attempted_at=attempted_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                health_state=result.provider_health.state.value,
                previous_health_state=(
                    str(previous["state"]) if previous is not None else None
                ),
                telemetry=telemetry,
                canonical_bars_inserted=result.canonical_bars_inserted,
                evaluations_created=evaluations_created,
                duplicate_evaluations_prevented=duplicates,
                events_created=events_created,
                issues=issues,
            )
            self._log(
                "poll_completed",
                {
                    "provider": result.provider_health.provider_id,
                    "health_state": result.provider_health.state.value,
                    "duration_ms": duration_ms,
                    "evaluations_created": evaluations_created,
                    "duplicates_prevented": duplicates,
                    "events_created": events_created,
                    "request_metrics": telemetry,
                },
            )
            return result

    async def run(self) -> None:
        provider_id = self._coordinator.provider.identity.provider_id
        self._stop.clear()
        try:
            self._runtime_lock.acquire()
        except RuntimeAlreadyActiveError:
            self._lock_conflict = True
            self._log(
                "runtime_lock_denied",
                {"provider": provider_id, "database": "configured"},
                level=logging.ERROR,
            )
            return
        started_at = primitive(self._clock.now())
        self._repository.start_runtime_session(
            self._session_id, provider_id, started_at
        )
        self._running = True
        exit_reason = "GRACEFUL_STOP"
        self._log(
            "runtime_started",
            {
                "provider": provider_id,
                "session_id": self._session_id,
                "safety_delay_seconds": (
                    self._schedule.safety_delay_seconds
                ),
                "health_check_seconds": (
                    self._schedule.health_check_seconds
                ),
            },
        )
        try:
            if not self._startup_backfill_enabled:
                wait_seconds = self._schedule.seconds_until_next_poll(
                    self._clock.now()
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=wait_seconds)
                except TimeoutError:
                    pass
            while not self._stop.is_set():
                try:
                    await asyncio.to_thread(self.run_once)
                except Exception:
                    exit_reason = "POLL_FAILURE_RECOVERED"
                    attempted_at = primitive(self._clock.now())
                    self._repository.record_provider_sync(
                        provider_id,
                        state=HealthState.DATA_UNAVAILABLE.value,
                        attempted_at=attempted_at,
                        succeeded=False,
                        detail="Unexpected market-data runtime failure.",
                    )
                    self._log(
                        "poll_failed",
                        {
                            "provider": provider_id,
                            "detail": "Unexpected runtime failure.",
                        },
                        level=logging.ERROR,
                    )
                wait_seconds = self._schedule.seconds_until_next_poll(
                    self._clock.now()
                )
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=wait_seconds
                    )
                except TimeoutError:
                    continue
        finally:
            self._running = False
            ended_at = primitive(self._clock.now())
            self._repository.end_runtime_session(
                self._session_id, ended_at, exit_reason
            )
            self._runtime_lock.release()
            self._log(
                "runtime_stopped",
                {
                    "provider": provider_id,
                    "session_id": self._session_id,
                    "exit_reason": exit_reason,
                },
            )

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "single_runtime_lock_acquired": self._runtime_lock.acquired,
            "lock_conflict": self._lock_conflict,
            "session_id": self._session_id,
            "last_poll_at": self._last_poll_at,
            "startup_backfill_enabled": self._startup_backfill_enabled,
            "next_poll_in_seconds": (
                self._schedule.seconds_until_next_poll(self._clock.now())
                if self._running
                else None
            ),
        }

    def _telemetry(self) -> dict[str, Any]:
        telemetry = getattr(self._coordinator.provider, "telemetry", None)
        return primitive(telemetry()) if callable(telemetry) else {}

    @staticmethod
    def _telemetry_delta(
        before: dict[str, Any], after: dict[str, Any]
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in after.items():
            previous = before.get(key)
            if isinstance(value, dict):
                result[key] = MarketDataRuntime._telemetry_delta(
                    previous if isinstance(previous, dict) else {}, value
                )
            elif isinstance(value, (int, float)):
                result[key] = value - (
                    previous if isinstance(previous, (int, float)) else 0
                )
            else:
                result[key] = value
        return result

    def _log(
        self,
        event: str,
        data: dict[str, Any],
        *,
        level: int = logging.INFO,
    ) -> None:
        self._logger.log(level, event, extra={"event_data": data})
