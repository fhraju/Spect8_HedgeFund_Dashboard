"""Independent authority/instrument consumption and durable evaluation retries."""

from __future__ import annotations

import json
import logging
import threading
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from ..domain import FilterMode, Timeframe
from ..service import WalkingSkeletonService
from .platform_adapter import (
    PLATFORM_PROVIDER_ID,
    SPECT8_PLATFORM_REPLAY_LIMITS,
    PlatformIncrementalProcessor,
    build_platform_history,
    platform_instrument_id,
    to_current_bar_snapshot,
)
from .platform_authority import (
    PlatformAuthorityRunResult,
    PlatformAuthorityRuntime,
    UnifiedPlatformAuthorityRuntime,
    _expected_latest_forex_h1_close,
    _live_streaming_readiness,
)
from .recovery_projection import RecoveryProjectionMixin

LOGGER = logging.getLogger(__name__)


class InstrumentProjection(RecoveryProjectionMixin):
    """Scope checkpoints and source identities without changing strategy keys."""

    def __init__(self, repository, authority, instrument):
        self.repository, self.authority, self.instrument = (
            repository,
            authority,
            instrument,
        )
        self.scope = authority + "|" + instrument
        self.initialize_revisions()
        with closing(repository._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS platform_instrument_progress (
                    scope TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS platform_pending_evaluations (
                    scope TEXT NOT NULL, mode TEXT NOT NULL, timeframe TEXT NOT NULL,
                    close_time TEXT NOT NULL, reason TEXT, state TEXT NOT NULL DEFAULT 'PENDING',
                    PRIMARY KEY(scope,mode,timeframe,close_time));
                CREATE TABLE IF NOT EXISTS platform_scoped_revisions (
                    scope TEXT NOT NULL, canonical_id INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(scope,canonical_id));
                CREATE TABLE IF NOT EXISTS platform_collection_incidents (
                    scope TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def latest_provider_evaluation_time(self, provider_id):
        with closing(self.repository._connect()) as db:
            row = db.execute(
                "SELECT MAX(updated_at) FROM instrument_status WHERE provider=? AND instrument_id=?",
                (provider_id, self.instrument),
            ).fetchone()
        return (
            datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            if row and row[0]
            else None
        )

    def platform_integration_state(self):
        with closing(self.repository._connect()) as db:
            row = db.execute(
                "SELECT payload FROM platform_instrument_progress WHERE scope=?",
                (self.scope,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def advance_platform_watermark(self, **kwargs):
        previous = self.platform_integration_state()
        if (
            previous
            and kwargs["watermark_canonical_bar_id"]
            < previous["watermark_canonical_bar_id"]
        ):
            raise ValueError("instrument checkpoint moved backwards")
        payload = {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in kwargs.items()
        }
        with self.repository._lock, closing(self.repository._connect()) as db:
            db.execute(
                "INSERT INTO platform_instrument_progress VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET payload=excluded.payload",
                (self.scope, json.dumps(payload)),
            )
            db.commit()

    def platform_consumed_identity(self, identity):
        return self.repository.platform_consumed_identity(
            self.authority + "|" + identity
        )

    def record_platform_consumption(self, **kwargs):
        kwargs["logical_identity"] = self.authority + "|" + kwargs["logical_identity"]
        kwargs["immutable_identity"] = (
            self.authority + "|" + kwargs["immutable_identity"]
        )
        return self.repository.record_platform_consumption(**kwargs)

    def record_platform_revision(self, **kwargs):
        with self.repository._lock, closing(self.repository._connect()) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO platform_scoped_revisions VALUES (?,?,?)",
                (
                    self.scope,
                    kwargs["revised_canonical_bar_id"],
                    json.dumps(kwargs, default=str),
                ),
            )
            db.commit()
            return cursor.rowcount > 0

    def enqueue(self, candidates):
        with self.repository._lock, closing(self.repository._connect()) as db:
            db.executemany(
                "INSERT OR IGNORE INTO platform_pending_evaluations(scope,mode,timeframe,close_time) VALUES (?,?,?,?)",
                [
                    (self.scope, m.value, t.value, c.isoformat())
                    for _, m, t, c in candidates
                ],
            )
            db.commit()

    def pending(self):
        with closing(self.repository._connect()) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM platform_pending_evaluations WHERE scope=? AND state='PENDING' ORDER BY close_time DESC,mode,timeframe",
                    (self.scope,),
                )
            ]

    def evaluated(self, row, reason=None, state=None):
        with self.repository._lock, closing(self.repository._connect()) as db:
            db.execute(
                "UPDATE platform_pending_evaluations SET reason=?,state=? WHERE scope=? AND mode=? AND timeframe=? AND close_time=?",
                (
                    reason,
                    state or ("PENDING" if reason else "DONE"),
                    self.scope,
                    row["mode"],
                    row["timeframe"],
                    row["close_time"],
                ),
            )
            db.commit()

    def incident(self, payload):
        with self.repository._lock, closing(self.repository._connect()) as db:
            db.execute(
                "INSERT INTO platform_collection_incidents VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET payload=excluded.payload",
                (self.scope, json.dumps(payload, default=str)),
            )
            db.commit()


class IsolatedPlatformAuthorityRuntime(UnifiedPlatformAuthorityRuntime):
    """Reuse financial calculations; isolate read, persistence and retry state."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._isolated_lock = threading.Lock()
        self._children = {}
        self._instrument_status = {}
        for inst in self._instrument_ids:
            projection = InstrumentProjection(
                self._repository, self.authority_for(inst), inst
            )
            child = PlatformAuthorityRuntime(
                self.gateway_for(inst),
                projection,
                WalkingSkeletonService(
                    self._service._evaluator, self._service._case_loader, projection
                ),
                (inst,),
                stale_after_seconds=self._stale_after_seconds,
                poll_seconds=self._poll_seconds,
                signal_lifecycle=self._signal_lifecycle,
            )
            self._children[inst] = child

    def _collection_status(self, inst):
        backend = self._backends.get(self.authority_for(inst))
        engine = getattr(backend, "_engine", None)
        if engine is None:
            return {}
        from sqlalchemy import text

        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT to_regclass('collection_recovery_status')")
            ).scalar()
            if not exists:
                return {"state": "UNKNOWN", "automatic": False}
            row = conn.execute(
                text(
                    "SELECT status FROM collection_recovery_status WHERE provider_id=:p AND instrument_id=:i"
                ),
                {"p": self.authority_for(inst), "i": platform_instrument_id(inst)},
            ).fetchone()
            return dict(row[0]) if row else {"state": "UNKNOWN", "automatic": False}

    def run_once(self, *, available_as_of=None):
        if not self._isolated_lock.acquire(blocking=False):
            raise RuntimeError("instrument cycle already running")
        try:
            now = (available_as_of or datetime.now(timezone.utc)).astimezone(
                timezone.utc
            )
            results = []
            self._current_partials = {}
            live = {}
            for inst, child in self._children.items():
                projection = child._repository
                try:
                    gateway = self.gateway_for(inst)
                    state = projection.platform_integration_state()
                    history_batch = gateway.read(
                        (platform_instrument_id(inst),),
                        available_as_of=now,
                        after_canonical_bar_id=None,
                        limits=SPECT8_PLATFORM_REPLAY_LIMITS,
                    )
                    batch = history_batch
                    if state:
                        delta = gateway.read(
                            (platform_instrument_id(inst),),
                            available_as_of=now,
                            after_canonical_bar_id=state["watermark_canonical_bar_id"],
                            limits=SPECT8_PLATFORM_REPLAY_LIMITS,
                        )
                        bars = {
                            b.canonical_bar_id: b
                            for b in (*history_batch.bars, *delta.bars)
                        }
                        batch = replace(
                            history_batch,
                            bars=tuple(bars.values()),
                            watermark_canonical_bar_id=max(
                                history_batch.watermark_canonical_bar_id,
                                delta.watermark_canonical_bar_id,
                            ),
                        )
                    if any(
                        b.instrument_id != platform_instrument_id(inst)
                        or b.source_provider_id != self.authority_for(inst)
                        for b in batch.bars
                    ):
                        raise ValueError("source authority mismatch")
                    history = build_platform_history(history_batch, inst)
                    child._current_histories = {inst: history}
                    child._current_partials = {
                        (inst, s.timeframe): s
                        for s in (
                            to_current_bar_snapshot(p)
                            for p in batch.partial_bar_snapshots
                            if p.price_type == "BID"
                        )
                    }
                    child._live_readiness = _live_streaming_readiness(
                        batch,
                        (inst,),
                        as_of=now,
                        stale_after_seconds=self._stale_after_seconds,
                    )
                    live.update(child._live_readiness["live_instruments"])
                    self._current_partials.update(child._current_partials)
                    self._current_histories[inst] = history
                    # Persist completed inputs before evaluating readiness or partials.
                    candidates = child._prepare_histories(
                        batch,
                        {inst: history},
                        first_activation=False,
                        startup_replay=not child._startup_replay_complete,
                    )
                    if state:
                        previous_time = datetime.fromisoformat(state["updated_at"])
                        candidates = tuple(
                            c for c in candidates if c[3] > previous_time
                        )
                    projection.observe_recovery_inputs(batch.native_bootstrap_bars)
                    projection.enqueue(candidates)
                    processed = PlatformIncrementalProcessor(
                        gateway, projection
                    ).process_batch(batch, process_bar=lambda *_: ())
                    # Enqueue is durable before the cursor advances. Missing inputs
                    # therefore cannot be forgotten by a later incremental read.
                    created = duplicates = 0
                    evaluated_at = projection.latest_provider_evaluation_time(
                        PLATFORM_PROVIDER_ID
                    )
                    for pending in projection.pending()[:32]:
                        close = datetime.fromisoformat(pending["close_time"])
                        if history.h1 and close < history.h1[0].close_time:
                            projection.evaluated(
                                pending, "outside required history window", "DEFERRED"
                            )
                            continue
                        _keys, new, duplicate, at, _confirmed, limitation = (
                            child._evaluate(
                                inst,
                                FilterMode(pending["mode"]),
                                Timeframe(pending["timeframe"]),
                                close,
                                filter_history=history,
                            )
                        )
                        projection.evaluated(pending, limitation)
                        created += new
                        duplicates += duplicate
                        if at is not None:
                            evaluated_at = max(evaluated_at or at, at)
                    try:
                        history.assert_bootstrap_ready()
                        child._historical_state = "READY"
                    except ValueError:
                        child._historical_state = "NOT_READY"
                    latest = history.h1[-1].close_time if history.h1 else None
                    fresh = latest is not None and _expected_latest_forex_h1_close(
                        now
                    ) - latest <= timedelta(seconds=self._stale_after_seconds)
                    child._connection_state = "HEALTHY"
                    child._freshness_state = "HEALTHY" if fresh else "STALE"
                    child._startup_replay_complete = True
                    child._startup_replay_report["complete"] = True
                    child._last_error = None
                    child._last_result = PlatformAuthorityRunResult(
                        connection_state="HEALTHY",
                        freshness_state=child._freshness_state,
                        previous_watermark=processed.previous_watermark,
                        watermark_canonical_bar_id=processed.new_watermark,
                        bootstrapped=state is None,
                        consumed=processed.consumed,
                        replayed_inputs=processed.replayed,
                        revisions_detected=processed.revisions_detected,
                        evaluations_created=created,
                        duplicate_evaluations_prevented=duplicates,
                        last_processed_canonical_timestamp=latest,
                        last_successful_evaluation_timestamp=evaluated_at,
                    )
                    results.append(child._last_result)
                    try:
                        collection = self._collection_status(inst)
                    except Exception as error:  # noqa: BLE001 - health is independent of consumption
                        collection = {
                            "state": "UNKNOWN",
                            "failure_reason": type(error).__name__,
                        }
                    observed = collection.get("updated_at")
                    if observed and now - datetime.fromisoformat(observed) > timedelta(
                        minutes=5
                    ):
                        collection = {
                            **collection,
                            "state": "BLOCKED",
                            "failure_reason": "RECOVERY_HEARTBEAT_STALE",
                        }
                    pending = projection.pending()
                    current_pending = [
                        p
                        for p in pending
                        if latest is not None
                        and datetime.fromisoformat(p["close_time"]) >= latest
                    ]
                    detail = {
                        **live[inst],
                        "authority": self.authority_for(inst),
                        "collection": collection,
                        "evaluation_freshness": "CURRENT"
                        if fresh
                        and evaluated_at is not None
                        and latest is not None
                        and evaluated_at >= latest
                        and not current_pending
                        and child._historical_state == "READY"
                        else "STALE",
                        "pending_evaluations": len(pending),
                        "last_successful_evaluation_timestamp": evaluated_at.isoformat()
                        if evaluated_at
                        else None,
                        "failure_reason": current_pending[0]["reason"]
                        if current_pending
                        else None,
                        "watermark_canonical_bar_id": processed.new_watermark,
                        "updated_at": now.isoformat(),
                    }
                    self._instrument_status[inst] = detail
                    projection.incident(detail)
                except Exception as error:  # noqa: BLE001 - isolate a failed instrument
                    # Exception type + stack location is useful without exposing DB URLs.
                    import traceback

                    location = traceback.extract_tb(error.__traceback__)[-1]
                    reason = (
                        f"{type(error).__name__} at {location.name}:{location.lineno}"
                    )
                    LOGGER.error(
                        "platform_instrument_failed instrument=%s authority=%s reason=%s",
                        inst,
                        self.authority_for(inst),
                        reason,
                    )
                    child._connection_state = "UNAVAILABLE"
                    child._freshness_state = "UNAVAILABLE"
                    child._last_error = reason
                    detail = {
                        "state": "NOT_READY",
                        "partial_state": "NOT_READY",
                        "evaluation_freshness": "STALE",
                        "failure_reason": reason,
                        "authority": self.authority_for(inst),
                        "updated_at": now.isoformat(),
                    }
                    self._instrument_status[inst] = detail
                    live[inst] = detail
                    projection.incident(detail)
            self._connection_state = "HEALTHY" if results else "UNAVAILABLE"
            self._freshness_state = (
                "HEALTHY"
                if results
                and all(
                    c._freshness_state == "HEALTHY" for c in self._children.values()
                )
                else "STALE"
            )
            self._historical_state = (
                "READY"
                if all(c._historical_state == "READY" for c in self._children.values())
                else "NOT_READY"
            )
            self._startup_replay_complete = bool(results)
            self._startup_replay_report["complete"] = all(
                c._startup_replay_complete for c in self._children.values()
            )
            self._live_readiness = {
                "live_instruments": dict(self._instrument_status),
                "streaming_state": "READY"
                if all(d.get("state") == "READY" for d in live.values())
                else "NOT_READY",
                "partial_data_state": "READY"
                if all(d.get("partial_state") == "READY" for d in live.values())
                else "NOT_READY",
                "overall_live_readiness": "LIVE_READY"
                if results
                and all(
                    d.get("evaluation_freshness") == "CURRENT"
                    and d.get("partial_state") == "READY"
                    for d in self._instrument_status.values()
                )
                else "DEGRADED",
            }
            if results:
                latest = max(
                    (
                        r.last_processed_canonical_timestamp
                        for r in results
                        if r.last_processed_canonical_timestamp
                    ),
                    default=None,
                )
                at = max(
                    (
                        r.last_successful_evaluation_timestamp
                        for r in results
                        if r.last_successful_evaluation_timestamp
                    ),
                    default=None,
                )
                self._last_result = replace(
                    results[0],
                    watermark_canonical_bar_id=max(
                        r.watermark_canonical_bar_id for r in results
                    ),
                    last_processed_canonical_timestamp=latest,
                    last_successful_evaluation_timestamp=at,
                    consumed=sum(r.consumed for r in results),
                    evaluations_created=sum(r.evaluations_created for r in results),
                )
            self._last_error = (
                None
                if len(results) == len(self._children)
                else "one or more instruments are unavailable"
            )
            return self._last_result
        finally:
            self._isolated_lock.release()

    def forming_signals(self, *, as_of):
        values = []
        for inst, child in self._children.items():
            if (
                self._instrument_status.get(inst, {}).get("evaluation_freshness")
                != "CURRENT"
            ):
                continue
            values.extend(child.forming_signals(as_of=as_of))
        reports = [c._forming_evaluation_report for c in self._children.values()]
        self._forming_evaluation_report = {
            "state": "READY"
            if all(r["state"] == "READY" for r in reports)
            else "NOT_READY",
            "as_of": as_of.isoformat(),
            "candidates": sum(r.get("candidates", 0) for r in reports),
            "evaluations_completed": sum(
                r.get("evaluations_completed", 0) for r in reports
            ),
            "signals_matched": len(values),
            "limitations": tuple(x for r in reports for x in r.get("limitations", ())),
        }
        return tuple(values)

    def status(self):
        base = super().status()
        base["collection_instruments"] = dict(self._instrument_status)
        base["instrument_watermarks"] = {
            i: d.get("watermark_canonical_bar_id")
            for i, d in self._instrument_status.items()
        }
        return base
