from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, BinaryIO


class RuntimeAlreadyActiveError(RuntimeError):
    pass


class SingleRuntimeLock:
    """Cross-process advisory lock scoped to one SQLite database."""

    def __init__(self, database_path: Path) -> None:
        resolved = database_path.resolve()
        self.path = resolved.with_suffix(resolved.suffix + ".runtime.lock")
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(
                    handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                )
        except OSError as error:
            handle.close()
            raise RuntimeAlreadyActiveError(
                "A market-data runtime already owns this database."
            ) from error
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None


class BoundaryAwareSchedule:
    """Wake after hourly UTC completion plus a bounded safety delay."""

    def __init__(
        self,
        *,
        safety_delay_seconds: int,
        health_check_seconds: int,
    ) -> None:
        if not 5 <= safety_delay_seconds <= 300:
            raise ValueError(
                "safety_delay_seconds must be between 5 and 300"
            )
        if not 60 <= health_check_seconds <= 900:
            raise ValueError(
                "health_check_seconds must be between 60 and 900"
            )
        self.safety_delay_seconds = safety_delay_seconds
        self.health_check_seconds = health_check_seconds

    def seconds_until_next_poll(self, now: datetime) -> float:
        if now.tzinfo is None:
            raise ValueError("schedule requires a timezone-aware datetime")
        current = now.astimezone(timezone.utc)
        hour = current.replace(minute=0, second=0, microsecond=0)
        due = hour + timedelta(seconds=self.safety_delay_seconds)
        if current >= due:
            due += timedelta(hours=1)
        boundary_wait = max(0.0, (due - current).total_seconds())
        return min(boundary_wait, float(self.health_check_seconds))


_SENSITIVE = re.compile(
    r"(?i)(authorization|api[_-]?key|apikey|token)"
    r"(\s*[:=]\s*|\s+)(bearer\s+)?[^,\s\"}]+"
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|apikey|token)=)[^&\s]+"
)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, str):
        redacted = _SENSITIVE.sub(r"\1\2[REDACTED]", value)
        return _QUERY_SECRET.sub(r"\1[REDACTED]", redacted)
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if re.search(
                    r"(?i)authorization|api[_-]?key|apikey|token", key
                )
                else redact_sensitive(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(child) for child in value]
    return value


class SanitizedJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "event": record.getMessage(),
            "data": getattr(record, "event_data", {}),
        }
        return json.dumps(redact_sensitive(payload), sort_keys=True)


def configure_runtime_logging(
    path: Path,
    *,
    max_bytes: int,
    backup_count: int,
) -> logging.Logger:
    if not 65_536 <= max_bytes <= 100_000_000:
        raise ValueError("runtime log max_bytes is outside safe bounds")
    if not 1 <= backup_count <= 10:
        raise ValueError("runtime log backup_count must be between 1 and 10")
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("spect8.market_data.runtime")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for existing in tuple(logger.handlers):
        logger.removeHandler(existing)
        existing.close()
    handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(SanitizedJsonFormatter())
    logger.addHandler(handler)
    return logger
