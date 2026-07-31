from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from threading import Lock

from ..domain import primitive
from ..repository import SQLiteProjectionRepository
from .coordinator import MarketDataCoordinator, PollResult
from .models import HealthState


class MarketDataRuntime:
    """Bounded polling loop around the existing deterministic coordinator."""

    _SUCCESS_STATES = {
        HealthState.HEALTHY,
        HealthState.RECOVERED,
        HealthState.STALE,
    }

    def __init__(
        self,
        coordinator: MarketDataCoordinator,
        repository: SQLiteProjectionRepository,
        *,
        poll_seconds: int,
    ) -> None:
        if not 60 <= poll_seconds <= 900:
            raise ValueError("poll_seconds must be between 60 and 900")
        self._coordinator = coordinator
        self._repository = repository
        self._poll_seconds = poll_seconds
        self._lock = Lock()
        self._stop = asyncio.Event()

    def run_once(self) -> PollResult:
        with self._lock:
            result = self._coordinator.poll_once()
            succeeded = result.provider_health.state in self._SUCCESS_STATES
            self._repository.record_provider_sync(
                result.provider_health.provider_id,
                state=result.provider_health.state.value,
                attempted_at=primitive(result.checked_at),
                succeeded=succeeded,
                detail=result.provider_health.detail,
            )
            return result

    async def run(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.run_once)
            except Exception:
                provider_id = self._coordinator.provider.identity.provider_id
                attempted_at = primitive(datetime.now(timezone.utc))
                self._repository.record_provider_sync(
                    provider_id,
                    state=HealthState.DATA_UNAVAILABLE.value,
                    attempted_at=attempted_at,
                    succeeded=False,
                    detail="Unexpected market-data runtime failure.",
                )
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._poll_seconds
                )
            except TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()
