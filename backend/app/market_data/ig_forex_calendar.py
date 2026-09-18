"""Shared IG calendar snapshot, scoped to platform Forex consumers."""
import os
from datetime import timedelta

# Last observed snapshot mtime per path, so long-lived API workers pick up a
# newly published snapshot without a process restart. Mirrors the collector's
# hot activation; a corrupt publication keeps the previous definition.
_seen_snapshot_mtime_ns = {}


def definition_for(provider):
    if provider != 'MARKET_DATA_PLATFORM' or not os.environ.get('THE_SYSTEM_FX_CALENDAR_FILE'):
        return None
    from hedgefund_market_data.providers.ig.forex_calendar import load_definition
    path = os.environ['THE_SYSTEM_FX_CALENDAR_FILE']
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError:
        mtime_ns = None
    if mtime_ns != _seen_snapshot_mtime_ns.get(path):
        load_definition.cache_clear()
        _seen_snapshot_mtime_ns[path] = mtime_ns
    definition = load_definition()
    return definition if definition.closures else None


def expected_opens(provider, start, end):
    definition = definition_for(provider)
    if definition is None:
        return None
    from hedgefund_market_data.providers.ig.forex_calendar import expected_h1_opens
    return expected_h1_opens(definition, start, end)


def valid_coverage(provider, bars, start, end):
    expected = expected_opens(provider, start, end)
    if expected is None:
        return None
    return bool(expected) and tuple(b.open_time for b in bars) == expected and all(
        b.close_time == b.open_time + timedelta(hours=1) for b in bars
    )


def expected_gap(provider, start, end):
    definition = definition_for(provider)
    if definition is None or end <= start:
        return False
    from hedgefund_market_data.sessions.authority import _is_definition_trading_at
    cursor = start
    while cursor < end:
        if _is_definition_trading_at(definition, cursor + timedelta(seconds=30)):
            return False
        cursor += timedelta(minutes=1)
    return True
