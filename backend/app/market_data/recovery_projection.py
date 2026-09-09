"""Audited projection revisions for repaired source history; events stay unique."""

import hashlib
import json
from contextlib import closing
from dataclasses import asdict

from ..domain import primitive
from ..repository import _exact_value, _status_value


class RecoveryProjectionMixin:
    def initialize_revisions(self):
        with closing(self.repository._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS platform_projection_revisions (
                    scope TEXT NOT NULL, kind TEXT NOT NULL, identity TEXT NOT NULL,
                    digest TEXT NOT NULL, payload TEXT NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(scope,kind,identity,digest));
                CREATE TABLE IF NOT EXISTS platform_recovery_inputs (
                    scope TEXT PRIMARY KEY, digest TEXT NOT NULL);
            """)

    def _archive(self, db, kind, identity, payload):
        encoded = json.dumps(payload, sort_keys=True, default=str)
        db.execute(
            "INSERT OR IGNORE INTO platform_projection_revisions(scope,kind,identity,digest,payload) VALUES (?,?,?,?,?)",
            (
                self.scope,
                kind,
                identity,
                hashlib.sha256(encoded.encode()).hexdigest(),
                encoded,
            ),
        )

    def observe_recovery_inputs(self, bars):
        encoded = json.dumps([asdict(bar) for bar in bars], sort_keys=True, default=str)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.repository._lock, closing(self.repository._connect()) as db:
            previous = db.execute(
                "SELECT digest FROM platform_recovery_inputs WHERE scope=?",
                (self.scope,),
            ).fetchone()
            db.execute(
                "INSERT INTO platform_recovery_inputs VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET digest=excluded.digest",
                (self.scope, digest),
            )
            if previous and previous[0] != digest:
                db.execute(
                    "UPDATE platform_pending_evaluations SET state='PENDING',reason='RECOVERED_INPUTS_CHANGED' WHERE scope=? AND state='DONE'",
                    (self.scope,),
                )
            db.commit()

    def persist_daily_filter_snapshot(self, snapshot):
        return self._persist_filter_revision(snapshot, "daily")

    def persist_w1_filter_snapshot(self, snapshot):
        return self._persist_filter_revision(snapshot, "weekly")

    def _persist_filter_revision(self, snapshot, kind):
        if snapshot.instrument != self.instrument:
            raise ValueError("filter revision instrument mismatch")
        table = {
            "daily": "daily_filter_snapshots",
            "weekly": "weekly_filter_snapshots",
        }[kind]
        payload = _exact_value(asdict(snapshot))
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        partial = (
            snapshot.current_partial_d1
            if kind == "daily"
            else snapshot.current_partial_w1
        )
        values = {
            "snapshot_id": snapshot.snapshot_id,
            "strategy_version": snapshot.strategy_version,
            "canonical_profile_version": snapshot.canonical_profile_version,
            "provider": snapshot.provider,
            "instrument_id": snapshot.instrument,
            "as_of_h1_close_time_utc": payload["as_of_h1_close_time_utc"],
            "payload_json": encoded,
            "source_checksum": partial.source_checksum,
            "created_at": payload["created_at"],
        }
        if kind == "weekly":
            values["filter_mode"] = snapshot.filter_mode.value
        with self.repository._lock, closing(self.repository._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                f"SELECT * FROM {table} WHERE strategy_version=? AND canonical_profile_version=? AND provider=? AND instrument_id=? AND as_of_h1_close_time_utc=?",
                (
                    snapshot.strategy_version,
                    snapshot.canonical_profile_version,
                    snapshot.provider,
                    snapshot.instrument,
                    payload["as_of_h1_close_time_utc"],
                ),
            ).fetchone()
            if old:
                if old["payload_json"] == encoded:
                    return False
                if old["snapshot_id"] == snapshot.snapshot_id:
                    raise ValueError(
                        "filter content changed without a new source identity"
                    )
                self._archive(db, kind, old["snapshot_id"], dict(old))
                db.execute(
                    f"DELETE FROM {table} WHERE snapshot_id=?", (old["snapshot_id"],)
                )
            db.execute(
                f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
            db.commit()
            return True

    def persist_projection(self, status, events):
        if status.instrument_id != self.instrument:
            raise ValueError("evaluation revision instrument mismatch")
        payload = _status_value(status)
        encoded = json.dumps(payload, sort_keys=True)
        with self.repository._lock, closing(self.repository._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "INSERT OR IGNORE INTO processed_bars(idempotency_key,processed_at,source_case_id,synthetic) VALUES (?,?,?,?)",
                (
                    status.idempotency_key,
                    payload["last_update"],
                    status.source_case_id,
                    int(status.synthetic),
                ),
            )
            created = cursor.rowcount > 0
            if created:
                for event in events:
                    value = primitive(event)
                    db.execute(
                        "INSERT INTO event_history(idempotency_key,sequence,event_type,occurred_at,instrument_id,timeframe,source_case_id,payload_json,synthetic) VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            event.idempotency_key,
                            event.sequence,
                            event.event_type.value,
                            value["occurred_at"],
                            event.instrument_id,
                            event.timeframe.value,
                            event.source_case_id,
                            json.dumps(value["payload"], sort_keys=True),
                            int(event.synthetic),
                        ),
                    )
            identity = (
                status.strategy_id,
                status.provider,
                status.instrument_id,
                status.timeframe.value,
            )
            old = db.execute(
                "SELECT * FROM instrument_status WHERE strategy_id=? AND provider=? AND instrument_id=? AND timeframe=?",
                identity,
            ).fetchone()
            # Replaying an older candle must never move the latest scanner backwards.
            if old is None or payload["last_update"] >= old["updated_at"]:
                if old and old["status_json"] != encoded:
                    self._archive(db, "evaluation", status.idempotency_key, dict(old))
                db.execute(
                    "INSERT INTO instrument_status(strategy_id,provider,instrument_id,timeframe,status_json,updated_at,synthetic) VALUES (?,?,?,?,?,?,?) ON CONFLICT(strategy_id,provider,instrument_id,timeframe) DO UPDATE SET status_json=excluded.status_json,updated_at=excluded.updated_at,synthetic=excluded.synthetic",
                    (*identity, encoded, payload["last_update"], int(status.synthetic)),
                )
            db.commit()
            return created
