from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .domain import Bar, DomainEvent, InstrumentStatus, Timeframe, primitive
from .observation import expansion_projections

if TYPE_CHECKING:
    from .engine.models import DailyFilterSnapshot
    from .market_data.models import ProviderHealth


def _exact_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _exact_value(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_exact_value(child) for child in value]
    return value


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
                    quality_status TEXT NOT NULL DEFAULT 'VALID',
                    construction_profile_version TEXT NOT NULL DEFAULT 'LEGACY',
                    provider_adapter_version TEXT NOT NULL DEFAULT 'legacy',
                    source_timeframe TEXT,
                    source_candle_ids_json TEXT NOT NULL DEFAULT '[]',
                    forward_filled INTEGER NOT NULL DEFAULT 0 CHECK (forward_filled IN (0, 1)),
                    expected_closure_before INTEGER NOT NULL DEFAULT 0 CHECK (expected_closure_before IN (0, 1)),
                    ingestion_run_id TEXT,
                    created_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z',
                    session_identifier TEXT,
                    session_open_broker_time TEXT,
                    session_close_broker_time TEXT,
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

                CREATE TABLE IF NOT EXISTS instrument_health (
                    provider TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    latest_completed_close TEXT,
                    freshness_seconds INTEGER,
                    detail TEXT NOT NULL,
                    latest_error_code TEXT,
                    latest_error_summary TEXT,
                    last_success_at TEXT,
                    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
                    PRIMARY KEY (provider, instrument_id)
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

                CREATE TABLE IF NOT EXISTS canonical_quality_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    bucket_open_utc TEXT,
                    bucket_close_utc TEXT,
                    issue_code TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    construction_profile_version TEXT NOT NULL,
                    ingestion_run_id TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    UNIQUE (
                        provider, instrument_id, timeframe,
                        bucket_open_utc, bucket_close_utc, issue_code
                    )
                );

                CREATE TABLE IF NOT EXISTS daily_filter_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    strategy_version TEXT NOT NULL,
                    canonical_profile_version TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    as_of_h1_close_time_utc TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_checksum TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (
                        strategy_version, canonical_profile_version, provider,
                        instrument_id, as_of_h1_close_time_utc
                    )
                );

                CREATE TABLE IF NOT EXISTS provider_credit_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    request_started_at TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    request_category TEXT NOT NULL,
                    estimated_credits INTEGER NOT NULL CHECK (estimated_credits > 0),
                    request_status TEXT NOT NULL,
                    http_status INTEGER,
                    provider_quota_limit INTEGER,
                    provider_quota_used INTEGER,
                    provider_quota_remaining INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_canonical_bars_instrument_time
                ON canonical_bars (
                    instrument_id, timeframe, close_time_utc DESC
                );
                CREATE INDEX IF NOT EXISTS idx_status_instrument_strategy_time
                ON instrument_status (
                    instrument_id, strategy_id, timeframe, updated_at DESC
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_instrument_time
                ON daily_filter_snapshots (
                    instrument_id, strategy_version,
                    as_of_h1_close_time_utc DESC
                );
                CREATE INDEX IF NOT EXISTS idx_events_instrument_time
                ON event_history (instrument_id, timeframe, occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_credit_ledger_provider_time
                ON provider_credit_ledger (provider, request_started_at DESC);
                """
            )
            self._migrate_synthetic_constraints(connection)
            self._migrate_canonical_provenance(connection)
            self._migrate_status_payloads(connection)
            connection.commit()

    @staticmethod
    def _migrate_canonical_provenance(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(canonical_bars)")
        }
        additions = {
            "quality_status": "TEXT NOT NULL DEFAULT 'VALID'",
            "construction_profile_version": "TEXT NOT NULL DEFAULT 'LEGACY'",
            "provider_adapter_version": "TEXT NOT NULL DEFAULT 'legacy'",
            "source_timeframe": "TEXT",
            "source_candle_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "forward_filled": "INTEGER NOT NULL DEFAULT 0 CHECK (forward_filled IN (0, 1))",
            "expected_closure_before": "INTEGER NOT NULL DEFAULT 0 CHECK (expected_closure_before IN (0, 1))",
            "ingestion_run_id": "TEXT",
            "created_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z'",
            "session_identifier": "TEXT",
            "session_open_broker_time": "TEXT",
            "session_close_broker_time": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE canonical_bars ADD COLUMN {name} {definition}"
                )

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

    def replace_latest_statuses_with_audit(
        self,
        statuses: tuple[InstrumentStatus, ...],
    ) -> int:
        """Atomically add evaluator-produced audit evidence to latest statuses."""

        if not statuses:
            return 0
        if any(status.filter_audit is None for status in statuses):
            raise ValueError("replacement statuses must contain filter audit evidence")

        replacements = tuple((status, primitive(status)) for status in statuses)
        changed = 0
        with self._lock, closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for status, replacement in replacements:
                    row = connection.execute(
                        """
                        SELECT status_json
                        FROM instrument_status
                        WHERE strategy_id = ? AND provider = ?
                          AND instrument_id = ? AND timeframe = ?
                        """,
                        (
                            status.strategy_id,
                            status.provider,
                            status.instrument_id,
                            status.timeframe.value,
                        ),
                    ).fetchone()
                    if row is None:
                        raise ValueError(
                            f"{status.timeframe.value}: latest persisted status is missing"
                        )
                    current = json.loads(row["status_json"])
                    if current.get("idempotency_key") != status.idempotency_key:
                        raise ValueError(
                            f"{status.timeframe.value}: latest persisted status changed during rebuild"
                        )
                    current_audit = current.get("filter_audit")
                    if current_audit == replacement["filter_audit"]:
                        continue
                    if current_audit is not None:
                        raise ValueError(
                            f"{status.timeframe.value}: persisted audit differs from evaluator rebuild"
                        )
                    comparable = dict(replacement)
                    comparable.pop("filter_audit", None)
                    current_without_audit = dict(current)
                    current_without_audit.pop("filter_audit", None)
                    if current_without_audit != comparable:
                        differing = sorted(
                            key
                            for key in set(current_without_audit) | set(comparable)
                            if current_without_audit.get(key) != comparable.get(key)
                        )
                        raise ValueError(
                            f"{status.timeframe.value}: rebuild would change non-audit fields: "
                            + ", ".join(differing)
                        )
                    connection.execute(
                        """
                        UPDATE instrument_status
                        SET status_json = ?
                        WHERE strategy_id = ? AND provider = ?
                          AND instrument_id = ? AND timeframe = ?
                        """,
                        (
                            json.dumps(replacement, sort_keys=True),
                            status.strategy_id,
                            status.provider,
                            status.instrument_id,
                            status.timeframe.value,
                        ),
                    )
                    changed += 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return changed

    def persist_canonical_bars(self, bars: tuple[Bar, ...]) -> int:
        inserted = 0
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for bar in bars:
                self._validate_forex_v1_bar(bar)
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
                        close, volume, raw_evidence_json, synthetic,
                        quality_status, construction_profile_version,
                        provider_adapter_version, source_timeframe,
                        source_candle_ids_json, forward_filled,
                        expected_closure_before, ingestion_run_id, created_at,
                        session_identifier, session_open_broker_time,
                        session_close_broker_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        bar.quality_status,
                        bar.construction_profile_version,
                        bar.provider_adapter_version,
                        bar.source_timeframe.value if bar.source_timeframe else None,
                        json.dumps(bar.source_candle_ids),
                        int(bar.forward_filled),
                        int(bar.expected_closure_before),
                        bar.ingestion_run_id,
                        value["created_at"] or value["close_time"],
                        bar.session_identifier,
                        bar.session_open_broker_time,
                        bar.session_close_broker_time,
                    ),
                )
                inserted += max(cursor.rowcount, 0)
            connection.commit()
        return inserted

    def persist_quality_issue(
        self,
        *,
        provider: str,
        instrument_id: str,
        timeframe: Timeframe,
        issue_code: str,
        detail: str,
        created_at: datetime,
        bucket_open: datetime | None = None,
        bucket_close: datetime | None = None,
        construction_profile_version: str,
        ingestion_run_id: str | None = None,
    ) -> bool:
        def iso(value: datetime | None) -> str | None:
            return (
                value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if value is not None
                else None
            )

        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO canonical_quality_issues (
                    provider, instrument_id, timeframe, bucket_open_utc,
                    bucket_close_utc, issue_code, detail,
                    construction_profile_version, ingestion_run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    instrument_id,
                    timeframe.value,
                    iso(bucket_open) or "",
                    iso(bucket_close) or "",
                    issue_code,
                    detail,
                    construction_profile_version,
                    ingestion_run_id,
                    iso(created_at),
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def quality_issues(self) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM canonical_quality_issues ORDER BY id"
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def persist_daily_filter_snapshot(self, snapshot: "DailyFilterSnapshot") -> bool:
        payload = _exact_value(asdict(snapshot))
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO daily_filter_snapshots (
                    snapshot_id, strategy_version, canonical_profile_version,
                    provider, instrument_id, as_of_h1_close_time_utc,
                    payload_json, source_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.strategy_version,
                    snapshot.canonical_profile_version,
                    snapshot.provider,
                    snapshot.instrument,
                    payload["as_of_h1_close_time_utc"],
                    encoded,
                    snapshot.current_partial_d1.source_checksum,
                    payload["created_at"],
                ),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT payload_json FROM daily_filter_snapshots WHERE snapshot_id = ?",
                    (snapshot.snapshot_id,),
                ).fetchone()
                if row is None or row["payload_json"] != encoded:
                    raise ValueError("Daily Filter snapshot identity conflict")
            connection.commit()
            return cursor.rowcount > 0

    def latest_daily_filter_snapshot(
        self,
        provider: str,
        instrument_id: str,
        strategy_version: str | None = None,
    ) -> dict[str, Any] | None:
        query = """
            SELECT payload_json FROM daily_filter_snapshots
            WHERE provider = ? AND instrument_id = ?
        """
        values: list[Any] = [provider, instrument_id]
        if strategy_version is not None:
            query += " AND strategy_version = ?"
            values.append(strategy_version)
        query += " ORDER BY as_of_h1_close_time_utc DESC LIMIT 1"
        with closing(self._connect()) as connection:
            row = connection.execute(query, values).fetchone()
        return json.loads(row["payload_json"]) if row is not None else None

    def daily_filter_snapshot_at(
        self,
        provider: str,
        instrument_id: str,
        strategy_version: str,
        as_of_h1_close_time_utc: str,
    ) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT payload_json FROM daily_filter_snapshots
                   WHERE provider = ? AND instrument_id = ?
                     AND strategy_version = ?
                     AND as_of_h1_close_time_utc = ?""",
                (provider, instrument_id, strategy_version, as_of_h1_close_time_utc),
            ).fetchone()
        return json.loads(row["payload_json"]) if row is not None else None

    def daily_filter_evaluation_references(
        self, snapshot_id: str
    ) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT idempotency_key, timeframe, occurred_at
                   FROM event_history
                   WHERE event_type = 'FILTER_EVALUATED'
                     AND json_extract(payload_json, '$.daily_filter_snapshot_id') = ?
                   ORDER BY timeframe""",
                (snapshot_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def daily_filter_snapshot_count(
        self, provider: str, instrument_id: str, strategy_version: str
    ) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS count FROM daily_filter_snapshots
                   WHERE provider = ? AND instrument_id = ?
                     AND strategy_version = ?""",
                (provider, instrument_id, strategy_version),
            ).fetchone()
        return int(row["count"])

    def canonical_bar_objects(
        self,
        provider: str,
        instrument_id: str,
        timeframe: str,
    ) -> tuple[Bar, ...]:
        """Load exact persisted bars for controlled maintenance workflows."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT provider, instrument_id, timeframe, close_time_utc,
                       open_time_utc, raw_open_time, raw_close_time,
                       raw_provider_symbol, session_timezone, open, high, low,
                       close, volume, synthetic, quality_status,
                       construction_profile_version, provider_adapter_version,
                       source_timeframe, source_candle_ids_json, forward_filled,
                       expected_closure_before, ingestion_run_id, created_at,
                       session_identifier, session_open_broker_time,
                       session_close_broker_time
                FROM canonical_bars
                WHERE provider = ? AND instrument_id = ? AND timeframe = ?
                ORDER BY close_time_utc
                """,
                (provider, instrument_id, timeframe),
            ).fetchall()
        return tuple(
            Bar(
                instrument_id=row["instrument_id"],
                timeframe=Timeframe(row["timeframe"]),
                open_time=datetime.fromisoformat(
                    row["open_time_utc"].replace("Z", "+00:00")
                ),
                close_time=datetime.fromisoformat(
                    row["close_time_utc"].replace("Z", "+00:00")
                ),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                provider=row["provider"],
                is_complete=True,
                volume=(Decimal(row["volume"]) if row["volume"] is not None else None),
                session_timezone=row["session_timezone"],
                raw_provider_symbol=row["raw_provider_symbol"],
                raw_open_time=row["raw_open_time"],
                raw_close_time=row["raw_close_time"],
                raw_open=row["open"],
                raw_high=row["high"],
                raw_low=row["low"],
                raw_close=row["close"],
                synthetic=bool(row["synthetic"]),
                quality_status=row["quality_status"],
                construction_profile_version=row["construction_profile_version"],
                provider_adapter_version=row["provider_adapter_version"],
                source_timeframe=(
                    Timeframe(row["source_timeframe"])
                    if row["source_timeframe"]
                    else None
                ),
                source_candle_ids=tuple(json.loads(row["source_candle_ids_json"])),
                forward_filled=bool(row["forward_filled"]),
                expected_closure_before=bool(row["expected_closure_before"]),
                ingestion_run_id=row["ingestion_run_id"],
                created_at=datetime.fromisoformat(
                    row["created_at"].replace("Z", "+00:00")
                ),
                session_identifier=row["session_identifier"],
                session_open_broker_time=row["session_open_broker_time"],
                session_close_broker_time=row["session_close_broker_time"],
            )
            for row in rows
        )

    def projection_sources(
        self,
        strategy_id: str,
        provider: str,
        instrument_id: str,
    ) -> dict[str, str]:
        prefix = f"{strategy_id}:{provider}:{instrument_id}:"
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT idempotency_key, source_case_id
                FROM processed_bars
                WHERE substr(idempotency_key, 1, ?) = ?
                ORDER BY idempotency_key
                """,
                (len(prefix), prefix),
            ).fetchall()
        return {row["idempotency_key"]: row["source_case_id"] for row in rows}

    def replace_daily_and_projections(
        self,
        *,
        strategy_id: str,
        provider: str,
        instrument_id: str,
        daily_bars: tuple[Bar, ...],
        projections: tuple[tuple[InstrumentStatus, tuple[DomainEvent, ...]], ...],
    ) -> None:
        """Atomically replace D1 bars and all dependent strategy projections."""

        prefix = f"{strategy_id}:{provider}:{instrument_id}:"
        with self._lock, closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                keys = tuple(
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT idempotency_key FROM processed_bars
                        WHERE substr(idempotency_key, 1, ?) = ?
                        """,
                        (len(prefix), prefix),
                    ).fetchall()
                )
                if keys:
                    placeholders = ",".join("?" for _ in keys)
                    connection.execute(
                        f"DELETE FROM event_history WHERE idempotency_key IN ({placeholders})",
                        keys,
                    )
                    connection.execute(
                        f"DELETE FROM processed_bars WHERE idempotency_key IN ({placeholders})",
                        keys,
                    )
                connection.execute(
                    """
                    DELETE FROM instrument_status
                    WHERE strategy_id = ? AND provider = ? AND instrument_id = ?
                    """,
                    (strategy_id, provider, instrument_id),
                )
                connection.execute(
                    """
                    DELETE FROM canonical_bars
                    WHERE provider = ? AND instrument_id = ? AND timeframe = 'D1'
                    """,
                    (provider, instrument_id),
                )
                for bar in daily_bars:
                    self._insert_canonical_bar(connection, bar)
                for status, events in projections:
                    self._insert_projection(connection, status, events)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def replace_market_profile_and_projections(
        self,
        *,
        strategy_id: str,
        provider: str,
        instrument_id: str,
        h1_bars: tuple[Bar, ...],
        h4_bars: tuple[Bar, ...],
        daily_bars: tuple[Bar, ...],
        projections: tuple[tuple[InstrumentStatus, tuple[DomainEvent, ...]], ...],
    ) -> None:
        """Atomically replace one instrument's validated Forex streams/projections."""

        prefix = f"{strategy_id}:{provider}:{instrument_id}:"
        with self._lock, closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                keys = tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT idempotency_key FROM processed_bars WHERE substr(idempotency_key, 1, ?) = ?",
                        (len(prefix), prefix),
                    ).fetchall()
                )
                if keys:
                    placeholders = ",".join("?" for _ in keys)
                    connection.execute(
                        f"DELETE FROM event_history WHERE idempotency_key IN ({placeholders})",
                        keys,
                    )
                    connection.execute(
                        f"DELETE FROM processed_bars WHERE idempotency_key IN ({placeholders})",
                        keys,
                    )
                connection.execute(
                    "DELETE FROM instrument_status WHERE strategy_id = ? AND provider = ? AND instrument_id = ?",
                    (strategy_id, provider, instrument_id),
                )
                connection.execute(
                    "DELETE FROM canonical_bars WHERE provider = ? AND instrument_id = ? AND timeframe IN ('H1','H4','D1')",
                    (provider, instrument_id),
                )
                for bar in (*h1_bars, *h4_bars, *daily_bars):
                    self._insert_canonical_bar(connection, bar)
                for status, events in projections:
                    self._insert_projection(connection, status, events)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _insert_canonical_bar(
        connection: sqlite3.Connection,
        bar: Bar,
    ) -> None:
        SQLiteProjectionRepository._validate_forex_v1_bar(bar)
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
        connection.execute(
            """
            INSERT INTO canonical_bars (
                provider, instrument_id, timeframe, close_time_utc,
                open_time_utc, raw_open_time, raw_close_time,
                raw_provider_symbol, session_timezone, open, high, low,
                close, volume, raw_evidence_json, synthetic,
                quality_status, construction_profile_version,
                provider_adapter_version, source_timeframe,
                source_candle_ids_json, forward_filled,
                expected_closure_before, ingestion_run_id, created_at,
                session_identifier, session_open_broker_time,
                session_close_broker_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                bar.quality_status,
                bar.construction_profile_version,
                bar.provider_adapter_version,
                bar.source_timeframe.value if bar.source_timeframe else None,
                json.dumps(bar.source_candle_ids),
                int(bar.forward_filled),
                int(bar.expected_closure_before),
                bar.ingestion_run_id,
                value["created_at"] or value["close_time"],
                bar.session_identifier,
                bar.session_open_broker_time,
                bar.session_close_broker_time,
            ),
        )

    @staticmethod
    def _validate_forex_v1_bar(bar: Bar) -> None:
        from .market_data.profiles.ic_markets_ny_close_forex_v1 import (
            PROFILE_ID,
        )

        if bar.construction_profile_version != PROFILE_ID:
            return
        if bar.synthetic:
            raise ValueError("Forex V1 provider-derived candles cannot be synthetic")
        if bar.forward_filled:
            raise ValueError("Forex V1 candles cannot be forward-filled")
        if bar.open_time.utcoffset() != timezone.utc.utcoffset(
            bar.open_time
        ) or bar.close_time.utcoffset() != timezone.utc.utcoffset(bar.close_time):
            raise ValueError("Forex V1 canonical timestamps must be UTC")
        if not bar.source_candle_ids:
            raise ValueError("Forex V1 candles require source provenance")
        if bar.timeframe is Timeframe.H4 and len(bar.source_candle_ids) != 4:
            raise ValueError("Forex V1 H4 requires exactly four H1 source IDs")
        if bar.timeframe is Timeframe.D1 and len(bar.source_candle_ids) not in (
            23,
            24,
            25,
        ):
            raise ValueError("Forex V1 D1 requires complete DST-aware H1 provenance")

    @staticmethod
    def _insert_projection(
        connection: sqlite3.Connection,
        status: InstrumentStatus,
        events: tuple[DomainEvent, ...],
    ) -> None:
        status_value = primitive(status)
        connection.execute(
            """
            INSERT INTO processed_bars
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
            DO UPDATE SET status_json = excluded.status_json,
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

    def update_instrument_health(
        self,
        instrument_id: str,
        health: "ProviderHealth",
        *,
        error_code: str | None = None,
    ) -> None:
        checked_at = primitive(health.checked_at)
        healthy = health.state.value in {"HEALTHY", "RECOVERED"}
        with self._lock, closing(self._connect()) as connection:
            previous = connection.execute(
                """
                SELECT last_success_at
                FROM instrument_health
                WHERE provider = ? AND instrument_id = ?
                """,
                (health.provider_id, instrument_id),
            ).fetchone()
            last_success = (
                checked_at
                if healthy
                else (previous["last_success_at"] if previous is not None else None)
            )
            connection.execute(
                """
                INSERT INTO instrument_health (
                    provider, instrument_id, state, checked_at,
                    latest_completed_close, freshness_seconds, detail,
                    latest_error_code, latest_error_summary, last_success_at,
                    synthetic
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, instrument_id) DO UPDATE SET
                    state = excluded.state,
                    checked_at = excluded.checked_at,
                    latest_completed_close = excluded.latest_completed_close,
                    freshness_seconds = excluded.freshness_seconds,
                    detail = excluded.detail,
                    latest_error_code = excluded.latest_error_code,
                    latest_error_summary = excluded.latest_error_summary,
                    last_success_at = excluded.last_success_at,
                    synthetic = excluded.synthetic
                """,
                (
                    health.provider_id,
                    instrument_id,
                    health.state.value,
                    checked_at,
                    primitive(health.latest_completed_close),
                    health.freshness_seconds,
                    health.detail,
                    None if healthy else error_code,
                    None if healthy else health.detail,
                    last_success,
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

    def reconcile_and_start_runtime_session(
        self, session_id: str, provider_id: str, started_at: str
    ) -> tuple[str, ...]:
        """Close orphaned sessions and atomically record their successor.

        The caller must hold the database-scoped ``SingleRuntimeLock``. Once
        that lock has been acquired, any unfinished session for the same
        provider belongs to a process that no longer owns the runtime.
        """
        with self._lock, closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                interrupted = tuple(
                    str(row["session_id"])
                    for row in connection.execute(
                        """
                        SELECT session_id
                        FROM runtime_sessions
                        WHERE provider = ? AND ended_at IS NULL
                        ORDER BY started_at, session_id
                        """,
                        (provider_id,),
                    ).fetchall()
                )
                connection.execute(
                    """
                    UPDATE runtime_sessions
                    SET ended_at = ?, exit_reason = 'INTERRUPTED_RESTART'
                    WHERE provider = ? AND ended_at IS NULL
                    """,
                    (started_at, provider_id),
                )
                connection.execute(
                    """
                    INSERT INTO runtime_sessions (
                        session_id, provider, started_at
                    ) VALUES (?, ?, ?)
                    """,
                    (session_id, provider_id, started_at),
                )
                connection.commit()
                return interrupted
            except Exception:
                connection.rollback()
                raise

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

    def recent_events(
        self,
        limit: int = 12,
        *,
        instrument_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 100:
            raise ValueError("event limit must be between 1 and 100")
        with closing(self._connect()) as connection:
            query = """
                SELECT id, idempotency_key, sequence, event_type, occurred_at,
                       instrument_id, timeframe, source_case_id, payload_json,
                       synthetic
                FROM event_history
            """
            values: list[Any] = []
            if instrument_id is not None:
                query += " WHERE instrument_id = ?"
                values.append(instrument_id)
            query += " ORDER BY id DESC LIMIT ?"
            values.append(limit)
            rows = connection.execute(query, values).fetchall()
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
                connection.execute("SELECT COUNT(*) FROM processed_bars").fetchone()[0]
            )

    def event_count(self) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM event_history").fetchone()[0]
            )

    def canonical_bar_count(self) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM canonical_bars").fetchone()[0]
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

    def instrument_health(
        self, provider_id: str, instrument_id: str
    ) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT provider, instrument_id, state, checked_at,
                       latest_completed_close, freshness_seconds, detail,
                       latest_error_code, latest_error_summary,
                       last_success_at, synthetic
                FROM instrument_health
                WHERE provider = ? AND instrument_id = ?
                """,
                (provider_id, instrument_id),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["synthetic"] = bool(result["synthetic"])
        return result

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

    def latest_canonical_close(
        self,
        provider_id: str,
        instrument_id: str,
        timeframe: str = "H1",
    ) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT MAX(close_time_utc) AS close_time_utc
                FROM canonical_bars
                WHERE provider = ? AND instrument_id = ? AND timeframe = ?
                """,
                (provider_id, instrument_id, timeframe),
            ).fetchone()
        return str(row["close_time_utc"]) if row and row["close_time_utc"] else None

    def reserve_provider_credits(
        self,
        *,
        provider: str,
        request_started_at: datetime,
        endpoint: str,
        request_category: str,
        estimated_credits: int,
        window_start: datetime,
        operational_budget: int,
    ) -> int | None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            used = connection.execute(
                """
                SELECT COALESCE(SUM(estimated_credits), 0) AS used
                FROM provider_credit_ledger
                WHERE provider = ? AND request_started_at > ?
                """,
                (provider, primitive(window_start)),
            ).fetchone()["used"]
            if int(used) + estimated_credits > operational_budget:
                connection.rollback()
                return None
            cursor = connection.execute(
                """
                INSERT INTO provider_credit_ledger (
                    provider, request_started_at, endpoint, request_category,
                    estimated_credits, request_status
                ) VALUES (?, ?, ?, ?, ?, 'RESERVED')
                """,
                (
                    provider,
                    primitive(request_started_at),
                    endpoint,
                    request_category,
                    estimated_credits,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def finalize_provider_credit(
        self,
        reservation_id: int,
        *,
        request_status: str,
        http_status: int | None,
        quota_limit: int | None = None,
        quota_used: int | None = None,
        quota_remaining: int | None = None,
    ) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE provider_credit_ledger
                SET request_status = ?, http_status = ?,
                    provider_quota_limit = ?, provider_quota_used = ?,
                    provider_quota_remaining = ?
                WHERE id = ?
                """,
                (
                    request_status,
                    http_status,
                    quota_limit,
                    quota_used,
                    quota_remaining,
                    reservation_id,
                ),
            )
            connection.commit()

    def provider_credit_usage(
        self,
        provider: str,
        *,
        window_start: datetime,
    ) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(estimated_credits), 0) AS used,
                       COUNT(*) AS request_count,
                       MAX(provider_quota_limit) AS provider_quota_limit,
                       MAX(provider_quota_used) AS provider_quota_used,
                       MIN(provider_quota_remaining) AS provider_quota_remaining
                FROM provider_credit_ledger
                WHERE provider = ? AND request_started_at > ?
                """,
                (provider, primitive(window_start)),
            ).fetchone()
        return {
            "estimated_credits_used": int(row["used"]),
            "request_count": int(row["request_count"]),
            "provider_quota_limit": row["provider_quota_limit"],
            "provider_quota_used": row["provider_quota_used"],
            "provider_quota_remaining": row["provider_quota_remaining"],
        }

    def observation_report(
        self,
        provider_id: str,
        instrument_id: str,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        generated_at = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
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
                    telemetry.get("completed_discoveries", {}).get(timeframe, 0)
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
                start = datetime.fromisoformat(unhealthy_start.replace("Z", "+00:00"))
                end = datetime.fromisoformat(row["completed_at"].replace("Z", "+00:00"))
                unhealthy_periods.append(
                    {
                        "started_at": unhealthy_start,
                        "recovered_at": row["completed_at"],
                        "recovery_seconds": max(0, int((end - start).total_seconds())),
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
            start = datetime.fromisoformat(session["started_at"].replace("Z", "+00:00"))
            end = (
                datetime.fromisoformat(session["ended_at"].replace("Z", "+00:00"))
                if session["ended_at"]
                else generated_at
            )
            uptime_seconds += max(0, int((end - start).total_seconds()))

        measured_hours = uptime_seconds / 3600
        measured_requests_per_hour = (
            totals["network_attempts"] / measured_hours if measured_hours > 0 else None
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
            "observation_end_utc": (polls[-1]["completed_at"] if polls else None),
            "runtime_uptime_seconds": uptime_seconds,
            "runtime_sessions": len(sessions),
            "restarts": max(0, len(sessions) - 1),
            "polls": len(polls),
            "request_metrics": {
                **totals,
                "attempts_by_timeframe": timeframe_attempts,
                "measured_average_requests_per_hour": (measured_requests_per_hour),
                "measured_projected_requests_per_day": (
                    measured_requests_per_hour * 24
                    if measured_requests_per_hour is not None
                    else None
                ),
            },
            "steady_state_request_projections": expansion_projections(),
            "completed_candle_discoveries": discoveries,
            "evaluations_created": sum(row["evaluations_created"] for row in polls),
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
