"""Independent, cached evaluations of validated authoritative partial bars."""

import logging
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from ..domain import FilterMode, Timeframe, primitive
from .partial_snapshot import CurrentBarSnapshot, broker_partial_h4_from_h1_snapshots
from .platform_adapter import PLATFORM_PROVIDER_ID, platform_instrument_id

LOGGER = logging.getLogger(__name__)
PARTIAL_MAX_AGE = timedelta(minutes=10)
COMBINATIONS = (("MICRO", "M30"), ("MICRO", "H1"), ("MACRO", "H1"), ("MACRO", "H4"))


def validate_partial(raw, authority, instrument, now):
    if raw is None:
        return (
            "WAITING_FOR_DATA",
            "Waiting for the first completed five-minute component",
        )
    if (
        raw.source_provider_id != authority
        or raw.instrument_id != platform_instrument_id(instrument)
    ):
        return "BLOCKED", "Partial source identity mismatch"
    if raw.as_of > now:
        return "BLOCKED", "Partial timestamp is in the future"
    if not raw.bar_start <= now < raw.bar_end or now - raw.as_of > PARTIAL_MAX_AGE:
        return "STALE", "Partial is expired or older than ten minutes"
    opens = tuple(raw.component_open_times)
    count = int((raw.as_of - raw.bar_start).total_seconds() // 300)
    expected = tuple(raw.bar_start + timedelta(minutes=5 * i) for i in range(count))
    if (
        raw.status != "PARTIAL"
        or count <= 0
        or opens != expected
        or raw.completed_member_count != count
        or raw.as_of != raw.bar_start + timedelta(minutes=5 * count)
        or raw.expected_member_count
        != int((raw.bar_end - raw.bar_start).total_seconds() // 300)
    ):
        return "BLOCKED", "Partial components do not form a complete contiguous prefix"
    if not raw.low <= min(raw.open, raw.close) <= max(raw.open, raw.close) <= raw.high:
        return "BLOCKED", "Invalid partial OHLC"
    return "READY", None


class FormingRuntimeMixin:
    def refresh_forming(self, now):
        previous = getattr(self, "_forming_entries", {})
        entries = {}
        for inst, child in self._children.items():
            authority = self.authority_for(inst)
            batch = getattr(child, "_forming_batch", None)
            raw_by_tf = (
                {
                    p.timeframe: p
                    for p in batch.partial_bar_snapshots
                    if p.price_type == "BID"
                }
                if batch
                else {}
            )
            # Include full bounded context so a repaired historical/filter input invalidates the cache.
            context = sha256(
                repr(
                    (
                        child._current_histories,
                        batch.bars if batch else (),
                        batch.native_bootstrap_bars if batch else (),
                    )
                ).encode()
            ).hexdigest()
            for mode, tf in COMBINATIONS:
                key = (authority, inst, mode, tf)
                raw = raw_by_tf.get("H1" if tf == "H4" else tf)
                entry = {
                    "authority": authority,
                    "instrument_id": inst,
                    "mode": mode,
                    "timeframe": tf,
                    "state": "WAITING_FOR_DATA",
                    "reason": None,
                    "evaluated_at": None,
                    "source_as_of": primitive(raw.as_of) if raw else None,
                    "source_bar_start": primitive(raw.bar_start) if raw else None,
                    "source_bar_end": primitive(raw.bar_end) if raw else None,
                    "signals": [],
                }
                try:
                    state, reason = validate_partial(raw, authority, inst, now)
                    if child._connection_state != "HEALTHY":
                        state, reason = "BLOCKED", "Instrument source is unavailable"
                    elif child._freshness_state != "HEALTHY":
                        state, reason = "STALE", "Completed history is stale"
                    elif (
                        child._historical_state != "READY"
                        or not child._startup_replay_complete
                    ):
                        state, reason = (
                            "WAITING_FOR_DATA",
                            "Required historical context is not ready",
                        )
                    entry.update(state=state, reason=reason)
                    if state != "READY":
                        entries[key] = entry
                        continue
                    snapshot = child._current_partials[
                        (inst, Timeframe("H1" if tf == "H4" else tf))
                    ]
                    if tf == "H4":
                        h1 = child._repository.canonical_bar_objects(
                            PLATFORM_PROVIDER_ID, inst, "H1"
                        )
                        values = broker_partial_h4_from_h1_snapshots(h1, snapshot, now)
                        bar = next(
                            (b for b in reversed(values) if not b.is_complete), None
                        )
                        if bar is None:
                            raise ValueError("H4 partial is unavailable")
                        members = sorted(
                            (
                                b
                                for b in h1
                                if bar.open_time <= b.open_time < snapshot.bar_start
                            ),
                            key=lambda b: b.open_time,
                        )
                        expected = int(
                            (snapshot.bar_start - bar.open_time).total_seconds() // 3600
                        )
                        if len(members) != expected or any(
                            b.open_time != bar.open_time + timedelta(hours=i)
                            or b.close_time != b.open_time + timedelta(hours=1)
                            or not b.is_complete
                            or b.synthetic
                            or b.forward_filled
                            for i, b in enumerate(members)
                        ):
                            raise ValueError(
                                "H4 components do not form a complete contiguous prefix"
                            )
                        snapshot = CurrentBarSnapshot(
                            inst,
                            Timeframe.H4,
                            bar.open_time,
                            bar.close_time,
                            snapshot.as_of,
                            bar.open,
                            bar.high,
                            bar.low,
                            bar.close,
                            source_provider_id=PLATFORM_PROVIDER_ID,
                            component_ids=bar.source_candle_ids,
                            provenance="SPECT8_BROKER_PARTIAL_H4_FROM_PLATFORM_H1_V1",
                        )
                    entry.update(
                        source_bar_start=primitive(snapshot.bar_start),
                        source_bar_end=primitive(snapshot.bar_end),
                    )
                    fingerprint = sha256(
                        repr((context, raw, snapshot, mode, tf)).encode()
                    ).hexdigest()
                    old = previous.get(key)
                    if old and old.get("fingerprint") == fingerprint:
                        entries[key] = old
                        continue
                    directions, limitation = child._forming_directions(
                        inst, FilterMode(mode), Timeframe(tf), snapshot
                    )
                    entry.update(evaluated_at=primitive(now), fingerprint=fingerprint)
                    if limitation:
                        entry.update(state="BLOCKED", reason=limitation)
                    else:
                        for direction in directions:
                            signal = child._signal_lifecycle.evaluate_forming(
                                instrument_id=inst,
                                mode=mode,
                                timeframe=tf,
                                snapshot=snapshot,
                                as_of=now,
                                platform_healthy=True,
                                evaluated_direction=direction,
                                direction_was_evaluated=True,
                            )
                            if signal:
                                entry["signals"].append(
                                    {
                                        **primitive(signal),
                                        "instrument": inst,
                                        "authority": authority,
                                        "source_as_of": primitive(snapshot.as_of),
                                        "evaluated_at": primitive(now),
                                        "market_data_source": PLATFORM_PROVIDER_ID,
                                        "provenance": snapshot.provenance,
                                        "component_ids": list(snapshot.component_ids),
                                    }
                                )
                except Exception as error:  # noqa: BLE001 - isolate candidate failures
                    # Never expose connection strings from dependency exceptions.
                    reason = (
                        str(error)
                        if isinstance(error, ValueError)
                        and str(error).startswith("H4 ")
                        else type(error).__name__
                    )
                    entry.update(
                        state="BLOCKED",
                        reason=reason,
                        evaluated_at=primitive(now),
                        signals=[],
                    )
                    LOGGER.warning(
                        "forming_candidate_failed instrument=%s authority=%s mode=%s timeframe=%s reason=%s",
                        inst,
                        authority,
                        mode,
                        tf,
                        reason,
                    )
                entries[key] = entry
        # Readers see either the previous complete publication or the new one.
        self._forming_entries = entries
        self._forming_evaluation_report = self.forming_diagnostics(as_of=now)

    def forming_view(self, *, as_of):
        candidates, signals = [], []
        for saved in getattr(self, "_forming_entries", {}).values():
            entry = {
                k: v for k, v in saved.items() if k not in ("signals", "fingerprint")
            }
            if entry["state"] == "READY":
                source = datetime.fromisoformat(
                    entry["source_as_of"].replace("Z", "+00:00")
                )
                end = datetime.fromisoformat(
                    entry["source_bar_end"].replace("Z", "+00:00")
                )
                if source > as_of or as_of >= end or as_of - source > PARTIAL_MAX_AGE:
                    entry.update(
                        state="STALE",
                        reason="Partial expired; waiting for fresh source data",
                    )
                else:
                    signals.extend(saved["signals"])
            candidates.append(entry)
        ready = sum(c["state"] == "READY" for c in candidates)
        return {
            "forming": signals,
            "forming_candidates": candidates,
            "forming_state": "READY"
            if candidates and ready == len(candidates)
            else "DEGRADED"
            if ready
            else "NOT_READY",
        }

    def cached_forming_signals(self):
        return tuple(self.forming_view(as_of=datetime.now(timezone.utc))["forming"])

    def forming_signals(self, *, as_of):
        return tuple(self.forming_view(as_of=as_of)["forming"])

    def forming_diagnostics(self, *, as_of=None):
        view = self.forming_view(as_of=as_of or datetime.now(timezone.utc))
        candidates = view["forming_candidates"]
        return {
            "state": view["forming_state"],
            "candidates": len(candidates),
            "evaluations_completed": sum(c["state"] == "READY" for c in candidates),
            "signals_matched": len(view["forming"]),
            "as_of": max(
                (c["evaluated_at"] for c in candidates if c["evaluated_at"]),
                default=None,
            ),
            "limitations": tuple(c["reason"] for c in candidates if c["reason"]),
            "instruments": candidates,
        }
