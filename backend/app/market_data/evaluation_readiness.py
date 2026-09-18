"""Per-candidate evaluation progress; one failed timeframe cannot hide its peers."""

from datetime import datetime


def candidate_evaluation_status(history, completed, pending, *, fresh, readiness):
    candidates = []
    for mode, tf in (("MICRO", "M30"), ("MICRO", "H1"), ("MACRO", "H1"), ("MACRO", "H4")):
        bars = getattr(history, tf.lower())
        latest = bars[-1].close_time if bars else None
        done = completed.get((mode, tf))
        done_at = datetime.fromisoformat(done) if done else None
        blocked = next((p for p in pending if p["mode"] == mode and p["timeframe"] == tf
                        and latest is not None and datetime.fromisoformat(p["close_time"]) >= latest), None)
        current = bool(fresh and readiness(history, mode, tf) and latest and done_at
                       and done_at >= latest and not blocked)
        candidates.append({"mode": mode, "timeframe": tf,
            "state": "CURRENT" if current else "STALE",
            "latest_completed_close": latest.isoformat() if latest else None,
            "last_successful_evaluation_timestamp": done,
            "failure_reason": blocked.get("reason") if blocked else None})
    modes = {}
    for mode in ("MICRO", "MACRO"):
        values = [c for c in candidates if c["mode"] == mode]
        modes[mode] = {"evaluation_freshness": "CURRENT" if all(c["state"] == "CURRENT" for c in values) else "STALE",
            "failure_reason": next((c["failure_reason"] for c in values if c["state"] != "CURRENT"), None)}
    return {"evaluation_candidates": candidates, "evaluation_modes": modes}


def forex_candidate_ready(history, mode, timeframe):
    required = {"h1": 128, "d1": 10}
    if timeframe == "M30":
        required["m30"] = 40
    if timeframe == "H4":
        required["h4"] = 30
    if mode == "MACRO":
        required["w1"] = 10
    return all(len(getattr(history, tf)) >= count for tf, count in required.items())
