"""Share calculation inputs bound to an explicit platform calendar and source."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

from ..domain import Bar, Timeframe
from .partial_snapshot import CurrentBarSnapshot
from .share_catalog import SHARE_AUTHORITIES, SHARE_PLATFORM_IDS

SHARE_LIMITS = {"M30": 260, "H1": 180, "H4": 30, "D1": 12, "W1": 12}


def contract_from_status(instrument, status):
    from hedgefund_market_data.providers.ig.share_contract import ShareContract
    c = ShareContract.from_payload(status["contract"])
    if (c.provider_id != SHARE_AUTHORITIES[instrument] or SHARE_PLATFORM_IDS.get(c.instrument_id) != instrument
            or status.get("account_type") != "SPREADBET"):
        raise ValueError("share source contract mismatch")
    return c


def share_bar(raw, contract, *, native=False):
    from hedgefund_market_data.domain import Timeframe as PlatformTimeframe
    from hedgefund_market_data.providers.ig.share_contract import (
        ShareNormalizationManifest,
    )
    c = contract
    if c is None:
        raise ValueError("validated share contract is required")
    tf = Timeframe(raw.timeframe)
    if (raw.instrument_id != c.instrument_id or raw.source_provider_id != c.provider_id or raw.price_type != "BID"
            or c.bucket(raw.open_time, PlatformTimeframe(tf.value)) != (raw.open_time, raw.close_time)):
        raise ValueError("share bar authority or boundary mismatch")
    if native:
        if (raw.normalization_manifest_checksum != ShareNormalizationManifest(c).checksum()
                or (raw.provider_metadata or {}).get("account_type") != "SPREADBET"):
            raise ValueError("share native normalization authority mismatch")
        source = f"MDP-NATIVE:{raw.bootstrap_bar_id}:{c.provider_id}:{raw.provenance}"
        version, created = raw.provenance, raw.observed_at
    else:
        if raw.policy_id != f"{c.profile_id}_{tf.value}_BID" or raw.quality_status != "VALID":
            raise ValueError("share canonical policy mismatch")
        source = f"MDP:{raw.canonical_bar_id}:{raw.semantic_hash}"
        version, created = raw.policy_id, raw.semantic_available_at
    return Bar(instrument_id=SHARE_PLATFORM_IDS[c.instrument_id], timeframe=tf,
        open_time=raw.open_time, close_time=raw.close_time, open=raw.open, high=raw.high,
        low=raw.low, close=raw.close, provider="MARKET_DATA_PLATFORM", is_complete=True,
        volume=raw.volume, session_timezone="America/New_York" if tf == Timeframe.D1 else "Europe/London",
        synthetic=False, quality_status="VALID", construction_profile_version=c.profile_id,
        provider_adapter_version=version, source_candle_ids=(source,), created_at=created)


def mark_closures(bars, contract):
    from hedgefund_market_data.domain import Timeframe as PlatformTimeframe
    result = []
    for bar in sorted(bars, key=lambda b: b.open_time):
        if result and result[-1].close_time < bar.open_time:
            tf = PlatformTimeframe(bar.timeframe.value)
            cursor = result[-1].close_time
            has_expected = False
            while cursor < bar.open_time:
                start, end = contract.bucket(cursor, tf)
                has_expected |= (contract.expected(start, tf) if tf.fixed_duration
                                 else bool(contract.expected_opens(start, end)))
                cursor = end
            bar = replace(bar, expected_closure_before=not has_expected)
        result.append(bar)
    return tuple(result)


class ShareH4Aggregator:
    def __init__(self, contract):
        self.contract = contract

    def aggregate(self, bars, *, as_of):
        from hedgefund_market_data.domain import Timeframe as PlatformTimeframe
        c = self.contract
        groups = {}
        for bar in bars:
            if (bar.timeframe == Timeframe.H1 and bar.is_complete and not bar.synthetic
                    and not bar.forward_filled and bar.quality_status == "VALID"
                    and bar.construction_profile_version == c.profile_id and bar.close_time <= as_of):
                groups.setdefault(c.bucket(bar.open_time, PlatformTimeframe.H4), []).append(bar)
        result = []
        for (opened, closed), members in sorted(groups.items()):
            members.sort(key=lambda b: b.open_time)
            if (closed > as_of or len(members) != 4 or not c.expected(opened, PlatformTimeframe.H4)
                    or any(b.open_time != opened + timedelta(hours=i)
                           or b.close_time != b.open_time + timedelta(hours=1) for i, b in enumerate(members))):
                continue
            result.append(replace(members[0], timeframe=Timeframe.H4, open_time=opened, close_time=closed,
                high=max(b.high for b in members), low=min(b.low for b in members), close=members[-1].close,
                volume=None, source_candle_ids=tuple(s for b in members for s in b.source_candle_ids),
                source_timeframe=Timeframe.H1, provider_adapter_version="IG_SHARE_H4_FROM_H1_V1"))
        return SimpleNamespace(bars=mark_closures(result, c), issues=())


def share_history(batch, instrument, contract):
    from hedgefund_market_data.providers.ig.share_contract import (
        ShareNormalizationManifest,
    )

    from .platform_adapter import PlatformInstrumentHistory
    c = contract
    checksum = ShareNormalizationManifest(c).checksum()
    # Legacy AAPL rows use an incompatible profile. Retain them in the source
    # audit, but they cannot become inputs to this approved share profile.
    canonical = [r for r in batch.bars if r.price_type == "BID" and r.instrument_id == c.instrument_id
                 and r.policy_id == f"{c.profile_id}_{r.timeframe}_BID" and r.close_time <= batch.available_as_of]
    native = [r for r in batch.native_bootstrap_bars if r.price_type == "BID"
              and r.instrument_id == c.instrument_id and r.normalization_manifest_checksum == checksum
              and r.close_time <= batch.available_as_of]
    series = {}
    for tf in ("H1", "D1", "W1"):
        merged = {(r.open_time, r.close_time): share_bar(r, c, native=True) for r in native if r.timeframe == tf}
        for raw in sorted(canonical, key=lambda r: r.version_number):
            if raw.timeframe == tf:
                merged[raw.open_time, raw.close_time] = share_bar(raw, c)
        series[tf] = mark_closures(sorted(merged.values(), key=lambda b: b.open_time)[-SHARE_LIMITS[tf]:], c)
    m30 = {}
    for raw in sorted(canonical, key=lambda r: r.version_number):
        if raw.timeframe == "M30":
            share_bar(raw, c)  # validates the actual normalized source
            m30[raw.open_time] = raw
    h4 = ShareH4Aggregator(c).aggregate(series["H1"], as_of=batch.available_as_of).bars
    return PlatformInstrumentHistory(c.instrument_id, instrument, tuple(m30[o] for o in sorted(m30)),
        series["H1"], h4[-30:], series["D1"], series["W1"], ())


def prepare_share_history(child, batch, history):
    from ..domain import FilterMode
    c = child._share_contract
    m30 = mark_closures(tuple(share_bar(b, c) for b in history.m30), c)
    child._repository.persist_canonical_bars((*m30, *history.h1, *history.h4))
    # Native D1/W1 remain explicit filter bootstrap inputs; they do not pretend
    # to be platform canonical data in the dashboard projection. Expected
    # closures are marked exactly as the filter builder marks them, so the
    # persisted dependency series is a truthful continuity reference.
    canonical_filters = mark_closures(tuple(share_bar(b, c) for b in batch.bars
                                            if b.price_type == "BID"
                                            and b.timeframe in {"D1", "W1"}), c)
    child._repository.persist_canonical_bars(canonical_filters)
    candidates = []
    for tf, bars, modes in ((Timeframe.M30, m30, (FilterMode.MICRO,)),
                           (Timeframe.H1, history.h1, (FilterMode.MICRO, FilterMode.MACRO)),
                           (Timeframe.H4, history.h4, (FilterMode.MACRO,))):
        # Bounded restart catch-up; durable pending identities prevent duplicate
        # events while keeping blocked evaluations eligible for later repair.
        for bar in bars[29:]:
            if bar.close_time >= batch.available_as_of - timedelta(days=1):
                candidates.extend((history.spect8_instrument_id, mode, tf, bar.close_time) for mode in modes)
    return tuple(candidates)


def share_readiness(history, contract, now):
    from hedgefund_market_data.domain import Timeframe as PlatformTimeframe
    from hedgefund_market_data.providers.ig.share_plan import previous_completed
    expected = previous_completed(contract, PlatformTimeframe.M30, now - timedelta(minutes=5), 1)[0] + timedelta(minutes=30)
    latest = max((b.close_time for b in history.m30), default=None)
    fresh = latest is not None and latest >= expected
    try:
        partial = bool(forming_snapshot(contract, history.spect8_instrument_id, Timeframe.H1, history.m30, now)) if fresh else False
    except ValueError:
        partial = False  # Missing partial components cannot block completed consumption.
    detail = {"state": "READY" if fresh else "NOT_READY", "partial_state": "READY" if partial else "NOT_READY",
              "latest_completed_m30_close": latest.isoformat() if latest else None,
              "expected_completed_m30_close": expected.isoformat(), "account_type": "SPREADBET",
              "collection_method": "REST_COMPLETED_M30", "cadence_seconds": 1800,
              "supported_forming_timeframes": ["H1", "H4"]}
    return fresh, {"live_instruments": {history.spect8_instrument_id: detail},
                   "partial_data_state": "READY" if partial else "NOT_READY"}


def current_filter_components(contract, timeframe, provider, instrument, close, h1):
    from hedgefund_market_data.domain import Timeframe as PlatformTimeframe
    opened, end = contract.bucket(close - timedelta(microseconds=1), PlatformTimeframe(timeframe.value))
    expected = contract.expected_opens(opened, close, PlatformTimeframe.H1)
    values = tuple(sorted((b for b in h1 if opened <= b.open_time and b.close_time <= close
                           and b.provider == provider and b.instrument_id == instrument), key=lambda b: b.open_time))
    if (not values or tuple(b.open_time for b in values) != expected or values[-1].close_time != close
            or any(not b.is_complete or b.synthetic or b.forward_filled or b.quality_status != "VALID"
                   or b.construction_profile_version != contract.profile_id
                   or b.close_time != b.open_time + timedelta(hours=1) for b in values)):
        raise ValueError("SHARE_FILTER_REQUIRED_H1_MISSING_OR_INVALID")
    return opened, end, values


def share_partial_filter(contract, timeframe, provider, instrument, close, h1):
    from ..engine.current_daily_filter import _digest, _iso, _source_id
    from ..engine.models import CurrentPartialDailyCandle, CurrentPartialWeeklyCandle
    opened, end, values = current_filter_components(contract, timeframe, provider, instrument, close, h1)
    source_ids = tuple(_source_id(b) for b in values)
    checksum = _digest([[_iso(b.open_time), _iso(b.close_time), str(b.open), str(b.high),
                         str(b.low), str(b.close), source] for b, source in zip(values, source_ids)])
    model = CurrentPartialDailyCandle if timeframe == Timeframe.D1 else CurrentPartialWeeklyCandle
    return model(session_identifier=opened.isoformat(), session_open_utc=opened, session_close_utc=end,
        first_h1_open_time_utc=values[0].open_time, last_h1_close_time_utc=values[-1].close_time,
        h1_count=len(values), source_h1_ids=source_ids, source_checksum=checksum, open=values[0].open,
        high=max(b.high for b in values), low=min(b.low for b in values), close=values[-1].close,
        quality_status="VALID")


def forming_snapshot(contract, instrument, timeframe, m30, now):
    from hedgefund_market_data.domain import Timeframe as PlatformTimeframe
    opened, end = contract.bucket(now, PlatformTimeframe(timeframe.value))
    cutoff = contract.bucket(now, PlatformTimeframe.M30)[0]
    expected = tuple(opened + timedelta(minutes=30 * i) for i in range(int((cutoff - opened).total_seconds() // 1800)))
    if not expected:
        return None
    members = tuple(sorted((b for b in m30 if opened <= b.open_time < cutoff), key=lambda b: b.open_time))
    if (tuple(b.open_time for b in members) != expected or now - members[-1].close_time > timedelta(minutes=35)
            or any(b.close_time > now or b.semantic_available_at > now or not contract.expected(b.open_time)
                   or b.close_time != b.open_time + timedelta(minutes=30) for b in members)):
        raise ValueError("Share partial requires a complete contiguous M30 prefix")
    for bar in members:
        share_bar(bar, contract)
    return CurrentBarSnapshot(instrument, timeframe, opened, end, members[-1].close_time,
        members[0].open, max(b.high for b in members), min(b.low for b in members), members[-1].close,
        source_provider_id="MARKET_DATA_PLATFORM", component_ids=tuple(f"MDP:{b.canonical_bar_id}:{b.semantic_hash}" for b in members),
        provenance=f"{contract.provider_id}:SPREADBET:COMPLETED_M30_PREFIX", construction_profile_version=contract.profile_id)


def share_candidate_ready(history, mode, timeframe):
    required = {"h1": 128, "d1": 10}
    if timeframe == "M30":
        required["m30"] = 40
    if timeframe == "H4":
        required["h4"] = 30
    if mode == "MACRO":
        required["w1"] = 10
    return all(len(getattr(history, tf)) >= count for tf, count in required.items())


def share_evaluation_status(history, completed, pending, *, fresh, ready):
    """Keep an unavailable weekly filter from hiding current Micro evaluations."""
    candidates = []
    for mode, tf in (("MICRO", "M30"), ("MICRO", "H1"), ("MACRO", "H1"), ("MACRO", "H4")):
        bars = getattr(history, tf.lower())
        latest = bars[-1].close_time.isoformat() if bars else None
        done = completed.get((mode, tf))
        blocked = next((p for p in pending if p["mode"] == mode and p["timeframe"] == tf
                        and latest is not None and p["close_time"] >= latest), None)
        current = fresh and share_candidate_ready(history, mode, tf) and latest and done and done >= latest and not blocked
        candidates.append({"mode": mode, "timeframe": tf, "state": "CURRENT" if current else "STALE",
            "latest_completed_close": latest, "last_successful_evaluation_timestamp": done,
            "failure_reason": blocked["reason"] if blocked else None})
    modes = {}
    for mode in ("MICRO", "MACRO"):
        values = [c for c in candidates if c["mode"] == mode]
        modes[mode] = {"evaluation_freshness": "CURRENT" if all(c["state"] == "CURRENT" for c in values) else "STALE",
            "failure_reason": next((c["failure_reason"] for c in values if c["state"] != "CURRENT"), None)}
    return {"evaluation_candidates": candidates, "evaluation_modes": modes}
