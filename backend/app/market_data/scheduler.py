from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Iterator

from .models import CanonicalInstrument


class RoundRobinScanScheduler:
    """Deterministic fair cycle ordering with non-overlapping scan ownership."""

    def __init__(self) -> None:
        self._next_start = 0
        self._cycle = 0
        self._lock = Lock()

    @contextmanager
    def cycle(
        self, instruments: tuple[CanonicalInstrument, ...]
    ) -> Iterator[tuple[CanonicalInstrument, ...] | None]:
        if not self._lock.acquire(blocking=False):
            yield None
            return
        try:
            enabled = tuple(item for item in instruments if item.enabled)
            if not enabled:
                yield ()
                return
            start = self._next_start % len(enabled)
            ordered = enabled[start:] + enabled[:start]
            self._next_start = (start + 1) % len(enabled)
            self._cycle += 1
            yield ordered
        finally:
            self._lock.release()

    @property
    def completed_cycles(self) -> int:
        return self._cycle
