"""Independent provisional share evaluations on completed-M30 prefixes."""

from hashlib import sha256

from ..domain import FilterMode, Timeframe, primitive
from .share_profile import forming_snapshot, share_candidate_ready


def refresh_share_forming(runtime, inst, child, now, previous):
    authority = runtime.authority_for(inst)
    entries = {}
    c = getattr(child, "_share_contract", None)
    history = child._current_histories.get(inst)
    for mode, tf in (("MICRO", "M30"), ("MICRO", "H1"), ("MACRO", "H1"), ("MACRO", "H4")):
        key = (authority, inst, mode, tf)
        entry = {"authority": authority, "instrument_id": inst, "mode": mode, "timeframe": tf,
                 "state": "WAITING_FOR_DATA", "reason": None, "evaluated_at": None,
                 "source_as_of": None, "source_bar_start": None, "source_bar_end": None,
                 "max_age_seconds": 2100, "cadence_seconds": 1800, "account_type": "SPREADBET", "signals": []}
        try:
            if tf == "M30":
                entry.update(state="UNSUPPORTED", supported=False,
                    reason="M30 forming is unavailable with completed-M30 collection; H1 and H4 update every 30 minutes")
            elif c is None or history is None or not share_candidate_ready(history, mode, tf):
                entry.update(reason="Required share history is not ready")
            elif child._connection_state != "HEALTHY":
                entry.update(state="BLOCKED", reason="Share data source is unavailable")
            elif child._freshness_state != "HEALTHY":
                entry.update(state="STALE", reason="Completed share data is overdue")
            else:
                snapshot = forming_snapshot(c, inst, Timeframe(tf), history.m30, now)
                if snapshot is None:
                    entry.update(reason="Waiting for the first completed M30 portion of this bar")
                else:
                    entry.update(source_as_of=primitive(snapshot.as_of), source_bar_start=primitive(snapshot.bar_start),
                                 source_bar_end=primitive(snapshot.bar_end))
                    fingerprint = sha256(repr((history, snapshot, mode)).encode()).hexdigest()
                    old = previous.get(key)
                    if old and old.get("fingerprint") == fingerprint and old["state"] == "READY":
                        entries[key] = old
                        continue
                    directions, limitation = child._forming_directions(inst, FilterMode(mode), Timeframe(tf), snapshot)
                    entry.update(state="BLOCKED" if limitation else "READY", reason=limitation,
                                 evaluated_at=primitive(now), fingerprint=fingerprint)
                    if not limitation and child._signal_lifecycle:
                        for direction in directions:
                            signal = child._signal_lifecycle.evaluate_forming(instrument_id=inst, mode=mode,
                                timeframe=tf, snapshot=snapshot, as_of=now, platform_healthy=True,
                                evaluated_direction=direction, direction_was_evaluated=True)
                            if signal:
                                entry["signals"].append({**primitive(signal), "instrument": inst,
                                    "authority": authority, "account_type": "SPREADBET",
                                    "source_as_of": primitive(snapshot.as_of), "evaluated_at": primitive(now),
                                    "market_data_source": "MARKET_DATA_PLATFORM", "provenance": snapshot.provenance,
                                    "component_ids": list(snapshot.component_ids)})
        except Exception as error:  # noqa: BLE001 - isolate each candidate
            entry.update(state="BLOCKED", reason=str(error) if isinstance(error, ValueError) else type(error).__name__,
                         evaluated_at=primitive(now), signals=[])
        entries[key] = entry
    return entries
