from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

from .domain import Bar, DomainEvent, InstrumentStatus, primitive

if TYPE_CHECKING:
    from .market_data.models import ProviderHealth


class SQLiteProjectionRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = RLock()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS processed_bars (
                    idempotency_key TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL,
                    source_case_id TEXT NOT NULL,
                    synthetic INTEGER NOT NULL CHECK (synthetic = 1)
                );

                CREATE TABLE IF NOT EXISTS instrument_status (
                    strategy_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    status_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    synthetic INTEGER NOT NULL CHECK (synthetic = 1),
                    PRIMARY KEY (strategy_id, provider, instrument_id, timeframe)
                );

                CREATE TABLE IF NOT EXISTS event_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    source_case_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    synthetic INTEGER NOT NULL CHECK (synthetic = 1),
                    UNIQUE (idempotency_key, sequence),
                    FOREIGN KEY (idempotency_key)
                        REFERENCES processed_bars(idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS canonical_bars (
                    provider TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    close_time_utc TEXT NOT NULL,
                    open_time_utc TEXT NOT NULL,
                    raw_open_time TEXT NOT NULL,
                    raw_close_time TEXT NOT NULL,
                    raw_provider_symbol TEXT NOT NULL,
                    session_timezone TEXT NOT NULL,
                    open TEXT NOT NULL,
                    high TEXT NOT NULL,
                    low TEXT NOT NULL,
                    close TEXT NOT NULL,
                    volume TEXT,
                    raw_evidence_json TEXT NOT NULL,
                    synthetic INTEGER NOT NULL CHECK (synthetic = 1),
                    PRIMARY KEY (
                        provider, instrument_id, timeframe, close_time_utc
                    )
                );

                CREATE TABLE IF NOT EXISTS provider_health (
                    provider TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    previous_state TEXT,
                    checked_at TEXT NOT NULL,
                    latest_completed_close TEXT,
                    freshness_seconds INTEGER,
                    detail TEXT NOT NULL,
                    synthetic INTEGER NOT NULL CHECK (synthetic = 1)
                );
                """
            )
            self._migrate_status_payloads(connection)
            connection.commit()

    @staticmethod
    def _migrate_status_payloads(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT strategy_id, provider, instrument_id, timeframe, status_json
            FROM instrument_status
            """
        ).fetchall()
        for row in rows:
            status = json.loads(row["status_json"])
            if "levels_results" in status:
                continue
            level = status.get("levels_result")
            status["levels_results"] = [level] if level is not None else []
            connection.execute(
                """
                UPDATE instrument_status
                SET status_json = ?
                WHERE strategy_id = ?
                  AND provider = ?
                  AND instrument_id = ?
                  AND timeframe = ?
                """,
                (
                    json.dumps(status, sort_keys=True),
                    row["strategy_id"],
                    row["provider"],
                    row["instrument_id"],
                    row["timeframe"],
                ),
            )

    def persist_projection(
        self,
        status: InstrumentStatus,
        events: tuple[DomainEvent, ...],
    ) -> bool:
        status_value = primitive(status)
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO processed_bars
                    (idempotency_key, processed_at, source_case_id, synthetic)
                VALUES (?, ?, ?, 1)
                """,
                (
                    status.idempotency_key,
                    status_value["last_update"],
                    status.source_case_id,
                ),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                return False

            for event in events:
                event_value = primitive(event)
                connection.execute(
                    """
                    INSERT INTO event_history (
                        idempotency_key, sequence, event_type, occurred_at,
                        instrument_id, timeframe, source_case_id, payload_json,
                        synthetic
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        event.idempotency_key,
                        event.sequence,
                        event.event_type.value,
                        event_value["occurred_at"],
                        event.instrument_id,
                        event.timeframe.value,
                        event.source_case_id,
                        json.dumps(event_value["payload"], sort_keys=True),
                    ),
                )

            connection.execute(
                """
                INSERT INTO instrument_status (
                    strategy_id, provider, instrument_id, timeframe,
                    status_json, updated_at, synthetic
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(strategy_id, provider, instrument_id, timeframe)
                DO UPDATE SET
                    status_json = excluded.status_json,
                    updated_at = excluded.updated_at,
                    synthetic = 1
                """,
                (
                    status.strategy_id,
                    status.provider,
                    status.instrument_id,
                    status.timeframe.value,
                    json.dumps(status_value, sort_keys=True),
                    status_value["last_update"],
                ),
            )
            connection.commit()
            return True

    def persist_canonical_bars(self, bars: tuple[Bar, ...]) -> int:
        inserted = 0
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for bar in bars:
                value = primitive(bar)
                raw_evidence = {
                    "provider_symbol": bar.raw_provider_symbol,
                    "open_time": bar.raw_open_time,
                    "close_time": bar.raw_close_time,
                    "open": bar.raw_open,
                    "high": bar.raw_high,
                    "low": bar.raw_low,
                    "close": bar.raw_close,
                }
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO canonical_bars (
                        provider, instrument_id, timeframe, close_time_utc,
                        open_time_utc, raw_open_time, raw_close_time,
                        raw_provider_symbol, session_timezone, open, high, low,
                        close, volume, raw_evidence_json, synthetic
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        bar.provider,
                        bar.instrument_id,
                        bar.timeframe.value,
                        value["close_time"],
                        value["open_time"],
                        bar.raw_open_time or value["open_time"],
                        bar.raw_close_time or value["close_time"],
                        bar.raw_provider_symbol or bar.instrument_id,
                        bar.session_timezone,
                        str(bar.open),
                        str(bar.high),
                        str(bar.low),
                        str(bar.close),
                        str(bar.volume) if bar.volume is not None else None,
                        json.dumps(raw_evidence, sort_keys=True),
                    ),
                )
                inserted += max(cursor.rowcount, 0)
            connection.commit()
        return inserted

    def update_provider_health(self, health: "ProviderHealth") -> None:
        value = primitive(health)
        with self._lock, closing(self._connect()) as connection:
            previous = connection.execute(
                "SELECT state FROM provider_health WHERE provider = ?",
                (health.provider_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO provider_health (
                    provider, state, previous_state, checked_at,
                    latest_completed_close, freshness_seconds, detail,
                    synthetic
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(provider) DO UPDATE SET
                    state = excluded.state,
                    previous_state = excluded.previous_state,
                    checked_at = excluded.checked_at,
                    latest_completed_close = excluded.latest_completed_close,
                    freshness_seconds = excluded.freshness_seconds,
                    detail = excluded.detail,
                    synthetic = 1
                """,
                (
                    health.provider_id,
                    health.state.value,
                    previous["state"] if previous else None,
                    value["checked_at"],
                    value["latest_completed_close"],
                    health.freshness_seconds,
                    health.detail,
                ),
            )
            connection.commit()

    def statuses(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT status_json
                FROM instrument_status
                ORDER BY instrument_id, timeframe
                """
            ).fetchall()
        return [json.loads(row["status_json"]) for row in rows]

    def events(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, idempotency_key, sequence, event_type, occurred_at,
                       instrument_id, timeframe, source_case_id, payload_json,
                       synthetic
                FROM event_history
                ORDER BY id ASC
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "idempotency_key": row["idempotency_key"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "occurred_at": row["occurred_at"],
                "instrument_id": row["instrument_id"],
                "timeframe": row["timeframe"],
                "source_case_id": row["source_case_id"],
                "payload": json.loads(row["payload_json"]),
                "synthetic": bool(row["synthetic"]),
            }
            for row in rows
        ]

    def processed_count(self) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM processed_bars"
                ).fetchone()[0]
            )

    def event_count(self) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM event_history"
                ).fetchone()[0]
            )

    def canonical_bar_count(self) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM canonical_bars"
                ).fetchone()[0]
            )

    def canonical_bars(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT provider, instrument_id, timeframe, close_time_utc,
                       open_time_utc, raw_open_time, raw_close_time,
                       raw_provider_symbol, session_timezone, open, high, low,
                       close, volume, raw_evidence_json, synthetic
                FROM canonical_bars
                ORDER BY close_time_utc, timeframe
                """
            ).fetchall()
        return [
            {
                "provider": row["provider"],
                "instrument_id": row["instrument_id"],
                "timeframe": row["timeframe"],
                "close_time_utc": row["close_time_utc"],
                "open_time_utc": row["open_time_utc"],
                "raw_open_time": row["raw_open_time"],
                "raw_close_time": row["raw_close_time"],
                "raw_provider_symbol": row["raw_provider_symbol"],
                "session_timezone": row["session_timezone"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "raw_evidence": json.loads(row["raw_evidence_json"]),
                "synthetic": bool(row["synthetic"]),
            }
            for row in rows
        ]

    def provider_health(self, provider_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT provider, state, previous_state, checked_at,
                       latest_completed_close, freshness_seconds, detail,
                       synthetic
                FROM provider_health
                WHERE provider = ?
                """,
                (provider_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "provider": row["provider"],
            "state": row["state"],
            "previous_state": row["previous_state"],
            "checked_at": row["checked_at"],
            "latest_completed_close": row["latest_completed_close"],
            "freshness_seconds": row["freshness_seconds"],
            "detail": row["detail"],
            "synthetic": bool(row["synthetic"]),
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
