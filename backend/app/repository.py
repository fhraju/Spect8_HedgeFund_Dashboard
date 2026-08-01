from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

from .domain import Bar, DomainEvent, InstrumentStatus, primitive
from .observation import expansion_projections

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
                    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1))
                );

                CREATE TABLE IF NOT EXISTS instrument_status (
                    strategy_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    status_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
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
                    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
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
                    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
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
                    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1))
                );

                CREATE TABLE IF NOT EXISTS provider_sync (
                    provider TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    last_attempt_at TEXT NOT NULL,
                    last_success_at TEXT,
                    detail TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_sessions (
                    session_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    exit_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS runtime_poll_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    health_state TEXT NOT NULL,
                    previous_health_state TEXT,
                    telemetry_json TEXT NOT NULL,
                    canonical_bars_inserted INTEGER NOT NULL,
                    evaluations_created INTEGER NOT NULL,
                    duplicate_evaluations_prevented INTEGER NOT NULL,
                    events_created INTEGER NOT NULL,
                    issues_json TEXT NOT NULL
                );
                """
            )
            self._migrate_synthetic_constraints(connection)
            self._migrate_status_payloads(connection)
            connection.commit()

    @staticmethod
    def _migrate_synthetic_constraints(connection: sqlite3.Connection) -> None:
        legacy = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name IN (
                  'processed_bars',
                  'instrument_status',
                  'event_history',
                  'canonical_bars',
                  'provider_health'
              )
              AND replace(sql, ' ', '') LIKE '%CHECK(synthetic=1)%'
            LIMIT 1
            """
        ).fetchone()
        if legacy is None:
            return
        connection.executescript(
            """
            PRAGMA foreign_keys = OFF;
            BEGIN IMMEDIATE;

            DROP TABLE IF EXISTS processed_bars_phase2c;
            DROP TABLE IF EXISTS instrument_status_phase2c;
            DROP TABLE IF EXISTS event_history_phase2c;
            DROP TABLE IF EXISTS canonical_bars_phase2c;
            DROP TABLE IF EXISTS provider_health_phase2c;

            CREATE TABLE processed_bars_phase2c (
                idempotency_key TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL,
                source_case_id TEXT NOT NULL,
                synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1))
            );
            INSERT INTO processed_bars_phase2c
            SELECT * FROM processed_bars;

            CREATE TABLE instrument_status_phase2c (
                strategy_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                instrument_id TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                status_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
                PRIMARY KEY (strategy_id, provider, instrument_id, timeframe)
            );
            INSERT INTO instrument_status_phase2c
            SELECT * FROM instrument_status;

            CREATE TABLE event_history_phase2c (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                instrument_id TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                source_case_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
                UNIQUE (idempotency_key, sequence),
                FOREIGN KEY (idempotency_key)
                    REFERENCES processed_bars_phase2c(idempotency_key)
            );
            INSERT INTO event_history_phase2c
            SELECT * FROM event_history;

            CREATE TABLE canonical_bars_phase2c (
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
                synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
                PRIMARY KEY (
                    provider, instrument_id, timeframe, close_time_utc
                )
            );
            INSERT INTO canonical_bars_phase2c
            SELECT * FROM canonical_bars;

            CREATE TABLE provider_health_phase2c (
                provider TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                previous_state TEXT,
                checked_at TEXT NOT NULL,
                latest_completed_close TEXT,
                freshness_seconds INTEGER,
                detail TEXT NOT NULL,
                synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1))
            );
            INSERT INTO provider_health_phase2c
            SELECT * FROM provider_health;

            DROP TABLE event_history;
            DROP TABLE instrument_status;
            DROP TABLE canonical_bars;
            DROP TABLE provider_health;
            DROP TABLE processed_bars;

            ALTER TABLE processed_bars_phase2c RENAME TO processed_bars;
            ALTER TABLE instrument_status_phase2c RENAME TO instrument_status;
            ALTER TABLE event_history_phase2c RENAME TO event_history;
            ALTER TABLE canonical_bars_phase2c RENAME TO canonical_bars;
            ALTER TABLE provider_health_phase2c RENAME TO provider_health;

            COMMIT;
            PRAGMA foreign_keys = ON;
            """
        )

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
                VALUES (?, ?, ?, ?)
                """,
                (
                    status.idempotency_key,
                    status_value["last_update"],
                    status.source_case_id,
                    int(status.synthetic),
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
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        int(event.synthetic),
                    ),
                )

            connection.execute(
                """
                INSERT INTO instrument_status (
                    strategy_id, provider, instrument_id, timeframe,
                    status_json, updated_at, synthetic
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id, provider, instrument_id, timeframe)
                DO UPDATE SET
                    status_json = excluded.status_json,
                    updated_at = excluded.updated_at,
                    synthetic = excluded.synthetic
                """,
                (
                    status.strategy_id,
                    status.provider,
                    status.instrument_id,
                    status.timeframe.value,
                    json.dumps(status_value, sort_keys=True),
                    status_value["last_update"],
                    int(status.synthetic),
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
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        int(bar.synthetic),
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    state = excluded.state,
                    previous_state = excluded.previous_state,
                    checked_at = excluded.checked_at,
                    latest_completed_close = excluded.latest_completed_close,
                    freshness_seconds = excluded.freshness_seconds,
                    detail = excluded.detail,
                    synthetic = excluded.synthetic
                """,
                (
                    health.provider_id,
                    health.state.value,
                    previous["state"] if previous else None,
                    value["checked_at"],
                    value["latest_completed_close"],
                    health.freshness_seconds,
                    health.detail,
                    int(health.synthetic),
                ),
            )
            connection.commit()

    def record_provider_sync(
        self,
        provider_id: str,
        *,
        state: str,
        attempted_at: str,
        succeeded: bool,
        detail: str,
    ) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO provider_sync (
                    provider, state, last_attempt_at, last_success_at, detail
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    state = excluded.state,
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = CASE
                        WHEN excluded.last_success_at IS NOT NULL
                        THEN excluded.last_success_at
                        ELSE provider_sync.last_success_at
                    END,
                    detail = excluded.detail
                """,
                (
                    provider_id,
                    state,
                    attempted_at,
                    attempted_at if succeeded else None,
                    detail,
                ),
            )
            connection.commit()

    def start_runtime_session(
        self, session_id: str, provider_id: str, started_at: str
    ) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO runtime_sessions (
                    session_id, provider, started_at
                ) VALUES (?, ?, ?)
                """,
                (session_id, provider_id, started_at),
            )
            connection.commit()

    def end_runtime_session(
        self, session_id: str, ended_at: str, exit_reason: str
    ) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE runtime_sessions
                SET ended_at = ?, exit_reason = ?
                WHERE session_id = ?
                """,
                (ended_at, exit_reason, session_id),
            )
            connection.commit()

    def record_runtime_poll(
        self,
        *,
        session_id: str,
        provider_id: str,
        attempted_at: str,
        completed_at: str,
        duration_ms: int,
        health_state: str,
        previous_health_state: str | None,
        telemetry: dict[str, Any],
        canonical_bars_inserted: int,
        evaluations_created: int,
        duplicate_evaluations_prevented: int,
        events_created: int,
        issues: tuple[str, ...],
    ) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO runtime_poll_history (
                    session_id, provider, attempted_at, completed_at,
                    duration_ms, health_state, previous_health_state,
                    telemetry_json, canonical_bars_inserted,
                    evaluations_created, duplicate_evaluations_prevented,
                    events_created, issues_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    provider_id,
                    attempted_at,
                    completed_at,
                    duration_ms,
                    health_state,
                    previous_health_state,
                    json.dumps(telemetry, sort_keys=True),
                    canonical_bars_inserted,
                    evaluations_created,
                    duplicate_evaluations_prevented,
                    events_created,
                    json.dumps(list(issues), sort_keys=True),
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

    def recent_events(self, limit: int = 12) -> list[dict[str, Any]]:
        if limit < 1 or limit > 100:
            raise ValueError("event limit must be between 1 and 100")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, idempotency_key, sequence, event_type, occurred_at,
                       instrument_id, timeframe, source_case_id, payload_json,
                       synthetic
                FROM event_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
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

    def provider_sync(self, provider_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT provider, state, last_attempt_at, last_success_at, detail
                FROM provider_sync
                WHERE provider = ?
                """,
                (provider_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def latest_candle_timestamps(
        self, provider_id: str, instrument_id: str
    ) -> dict[str, str]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT timeframe, MAX(close_time_utc) AS close_time_utc
                FROM canonical_bars
                WHERE provider = ? AND instrument_id = ?
                GROUP BY timeframe
                """,
                (provider_id, instrument_id),
            ).fetchall()
        return {
            row["timeframe"]: row["close_time_utc"]
            for row in rows
            if row["close_time_utc"] is not None
        }

    def latest_evaluation_close(
        self,
        provider_id: str,
        instrument_id: str,
        timeframe: str,
    ) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT status_json
                FROM instrument_status
                WHERE provider = ?
                  AND instrument_id = ?
                  AND timeframe = ?
                LIMIT 1
                """,
                (provider_id, instrument_id, timeframe),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["status_json"]).get("signal_bar_close_time")

    def observation_report(
        self,
        provider_id: str,
        instrument_id: str,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        generated_at = (as_of or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )
        with closing(self._connect()) as connection:
            sessions = connection.execute(
                """
                SELECT session_id, started_at, ended_at, exit_reason
                FROM runtime_sessions
                WHERE provider = ?
                ORDER BY started_at
                """,
                (provider_id,),
            ).fetchall()
            polls = connection.execute(
                """
                SELECT attempted_at, completed_at, duration_ms, health_state,
                       previous_health_state, telemetry_json,
                       canonical_bars_inserted, evaluations_created,
                       duplicate_evaluations_prevented, events_created,
                       issues_json
                FROM runtime_poll_history
                WHERE provider = ?
                ORDER BY id
                """,
                (provider_id,),
            ).fetchall()

        timeframe_attempts = {"H1": 0, "H4": 0, "D1": 0}
        discoveries = {"H1": 0, "H4": 0, "D1": 0}
        totals = {
            "network_attempts": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "rate_limit_responses": 0,
            "network_timeouts": 0,
            "cache_hits": 0,
        }
        transitions: list[dict[str, str]] = []
        unhealthy_periods: list[dict[str, Any]] = []
        unhealthy_start: str | None = None
        unhealthy_states = {
            "STALE",
            "DATA_UNAVAILABLE",
            "INSUFFICIENT_HISTORY",
            "QUARANTINED",
        }
        last_state: str | None = None
        for row in polls:
            telemetry = json.loads(row["telemetry_json"])
            for key in totals:
                totals[key] += int(telemetry.get(key, 0))
            for timeframe in timeframe_attempts:
                timeframe_attempts[timeframe] += int(
                    telemetry.get("series_attempts", {}).get(timeframe, 0)
                )
                discoveries[timeframe] += int(
                    telemetry.get("completed_discoveries", {}).get(
                        timeframe, 0
                    )
                )
            state = str(row["health_state"])
            if state != last_state:
                transitions.append(
                    {
                        "at": row["completed_at"],
                        "from": last_state or "UNOBSERVED",
                        "to": state,
                    }
                )
            if state in unhealthy_states and unhealthy_start is None:
                unhealthy_start = row["completed_at"]
            elif state not in unhealthy_states and unhealthy_start is not None:
                start = datetime.fromisoformat(
                    unhealthy_start.replace("Z", "+00:00")
                )
                end = datetime.fromisoformat(
                    row["completed_at"].replace("Z", "+00:00")
                )
                unhealthy_periods.append(
                    {
                        "started_at": unhealthy_start,
                        "recovered_at": row["completed_at"],
                        "recovery_seconds": max(
                            0, int((end - start).total_seconds())
                        ),
                    }
                )
                unhealthy_start = None
            last_state = state
        if unhealthy_start is not None:
            unhealthy_periods.append(
                {
                    "started_at": unhealthy_start,
                    "recovered_at": None,
                    "recovery_seconds": None,
                }
            )

        uptime_seconds = 0
        for session in sessions:
            start = datetime.fromisoformat(
                session["started_at"].replace("Z", "+00:00")
            )
            end = (
                datetime.fromisoformat(
                    session["ended_at"].replace("Z", "+00:00")
                )
                if session["ended_at"]
                else generated_at
            )
            uptime_seconds += max(0, int((end - start).total_seconds()))

        measured_hours = uptime_seconds / 3600
        measured_requests_per_hour = (
            totals["network_attempts"] / measured_hours
            if measured_hours > 0
            else None
        )

        return {
            "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
            "provider_id": provider_id,
            "instrument_id": instrument_id,
            "observation_start_utc": (
                sessions[0]["started_at"]
                if sessions
                else (polls[0]["attempted_at"] if polls else None)
            ),
            "observation_end_utc": (
                polls[-1]["completed_at"] if polls else None
            ),
            "runtime_uptime_seconds": uptime_seconds,
            "runtime_sessions": len(sessions),
            "restarts": max(0, len(sessions) - 1),
            "polls": len(polls),
            "request_metrics": {
                **totals,
                "attempts_by_timeframe": timeframe_attempts,
                "measured_average_requests_per_hour": (
                    measured_requests_per_hour
                ),
                "measured_projected_requests_per_day": (
                    measured_requests_per_hour * 24
                    if measured_requests_per_hour is not None
                    else None
                ),
            },
            "steady_state_request_projections": expansion_projections(),
            "completed_candle_discoveries": discoveries,
            "evaluations_created": sum(
                row["evaluations_created"] for row in polls
            ),
            "duplicate_evaluations_prevented": sum(
                row["duplicate_evaluations_prevented"] for row in polls
            ),
            "events_created": sum(row["events_created"] for row in polls),
            "canonical_bars_inserted": sum(
                row["canonical_bars_inserted"] for row in polls
            ),
            "health_transitions": transitions,
            "unhealthy_periods": unhealthy_periods,
            "latest_completed_candles": self.latest_candle_timestamps(
                provider_id, instrument_id
            ),
            "persisted_evaluations": self.processed_count(),
            "persisted_events": self.event_count(),
            "orders": 0,
            "fills": 0,
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
