from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock


class SlidingWindowRateLimiter:
    """One synchronous request-start gate shared by a provider instance."""

    def __init__(
        self,
        *,
        max_requests: int = 8,
        window_seconds: float = 60.0,
        min_interval_seconds: float = 8.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0 or min_interval_seconds < 0:
            raise ValueError("rate-limit durations are invalid")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.min_interval_seconds = min_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._wall_clock = wall_clock
        self._starts: deque[float] = deque()
        self._starts_utc: list[datetime] = []
        self._labels: list[str | None] = []
        self._lock = Lock()

    def acquire(self, label: str | None = None) -> float:
        """Wait until capacity exists, record, and return the start instant."""

        with self._lock:
            while True:
                now = self._monotonic()
                while self._starts and now - self._starts[0] >= self.window_seconds:
                    self._starts.popleft()
                waits = [0.0]
                if self._starts:
                    waits.append(self.min_interval_seconds - (now - self._starts[-1]))
                if len(self._starts) >= self.max_requests:
                    waits.append(self.window_seconds - (now - self._starts[0]))
                wait = max(waits)
                if wait <= 0:
                    self._starts.append(now)
                    self._starts_utc.append(self._wall_clock())
                    self._labels.append(label)
                    return now
                self._sleep(wait)

    def starts(self) -> tuple[float, ...]:
        with self._lock:
            return tuple(self._starts)

    def request_starts_utc(self) -> tuple[datetime, ...]:
        with self._lock:
            return tuple(self._starts_utc)

    def request_records(self) -> tuple[tuple[str | None, datetime], ...]:
        with self._lock:
            return tuple(zip(self._labels, self._starts_utc))
