"""Approved multi-asset profile: platform H1 evidence -> London-anchored H4.

The Market Data Platform owns the approved family calendars (including the
``LONDON_00_04_08_12_16_20`` H4 anchor for these instruments). Spect8 therefore
builds H4 for multi-asset instruments from the platform's H1 evidence using the
same anchor, instead of the FX broker-time anchor. No bar is fabricated: only
buckets whose four contiguous H1 components are present become H4 bars.

D1/W1 remain the platform's provider-native session bars (explicit filter
inputs), exactly like the existing FX warm-up path.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from ..domain import Bar, FilterMode, Timeframe
from .platform_adapter import (
    PlatformInstrumentHistory,
    PlatformReadBatch,
    SPECT8_PRICE_TYPE,
    first_accepted_versions,
    to_spect8_bar,
    to_spect8_native_bootstrap_bar,
)

LONDON = ZoneInfo("Europe/London")
H1 = timedelta(hours=1)
H4 = timedelta(hours=4)

# Bars built from the platform's approved multi-asset family calendars are not
# IC Markets FX V1 bars: a London-anchored bucket may legitimately carry fewer
# than four H1 components when the approved calendar has a daily break.
MULTI_ASSET_PROFILE_ID = "IG_PLATFORM_MULTI_ASSET_V1"

# Bounded read limits for the approved multi-asset instruments. H1 is wider
# than the 128-bar readiness target so the London-anchored H4 rebuild can reach
# the frozen 30-bar H4 requirement from actual H1 evidence (no fabrication).
MULTI_ASSET_LIMITS: dict[str, int] = {
    "M30": 80,
    "H1": 260,
    "H4": 30,
    "D1": 10,
    "W1": 10,
}


def _london_bucket(open_time: datetime) -> tuple[datetime, datetime]:
    local = open_time.astimezone(LONDON)
    start = local.replace(hour=(local.hour // 4) * 4, minute=0, second=0, microsecond=0)
    close = (start.replace(tzinfo=None) + H4).replace(tzinfo=LONDON)
    return start.astimezone(timezone.utc), close.astimezone(timezone.utc)


def _expected_h1_opens(
    instrument_id: str, opened: datetime, closed: datetime
) -> tuple[datetime, ...]:
    """Approved expected H1 opens for one London-anchored bucket."""
    from hedgefund_market_data.domain import IntervalExpectation
    from hedgefund_market_data.domain import Timeframe as PlatformTimeframe

    authority = family_session_authority()
    expected: list[datetime] = []
    cursor = opened
    half = timedelta(minutes=30)
    while cursor + H1 <= closed:
        first = authority.expected_interval(
            instrument_id, PlatformTimeframe.M30, cursor, cursor + half
        )
        second = authority.expected_interval(
            instrument_id, PlatformTimeframe.M30, cursor + half, cursor + H1
        )
        if (
            first is IntervalExpectation.EXPECTED
            and second is IntervalExpectation.EXPECTED
        ):
            expected.append(cursor)
        cursor += H1
    return tuple(expected)


def multi_asset_h4_bars(
    h1_bars: tuple[Bar, ...], *, as_of: datetime, instrument_id: str
) -> tuple[Bar, ...]:
    """Build approved-anchor H4 bars from actual H1 evidence only.

    A bucket is complete when every H1 interval the approved family calendar
    expects inside it is present. Approved daily breaks therefore reduce a
    bucket's component count instead of destroying the whole bucket, and no bar
    is ever fabricated.
    """
    grouped: dict[tuple[datetime, datetime], dict[datetime, Bar]] = defaultdict(dict)
    for bar in h1_bars:
        if bar.timeframe is not Timeframe.H1 or not bar.is_complete:
            continue
        if bar.close_time - bar.open_time != H1:
            continue
        grouped[_london_bucket(bar.open_time)][bar.open_time] = bar
    result: list[Bar] = []
    for (opened, closed) in sorted(grouped):
        if closed > as_of.astimezone(timezone.utc):
            continue
        present = grouped[(opened, closed)]
        expected = _expected_h1_opens(instrument_id, opened, closed)
        if not expected or any(open_time not in present for open_time in expected):
            continue
        members = tuple(present[open_time] for open_time in sorted(present))
        first, last = members[0], members[-1]
        result.append(
            Bar(
                instrument_id=first.instrument_id,
                timeframe=Timeframe.H4,
                open_time=opened,
                close_time=closed,
                open=first.open,
                high=max(bar.high for bar in members),
                low=min(bar.low for bar in members),
                close=last.close,
                provider=first.provider,
                is_complete=True,
                volume=(
                    sum(
                        (bar.volume for bar in members if bar.volume is not None),
                        Decimal("0"),
                    )
                    if all(bar.volume is not None for bar in members)
                    else None
                ),
                session_timezone=first.session_timezone,
                raw_provider_symbol=first.raw_provider_symbol,
                raw_open_time=opened.isoformat().replace("+00:00", "Z"),
                raw_close_time=closed.isoformat().replace("+00:00", "Z"),
                raw_open=str(first.open),
                raw_high=str(max(bar.high for bar in members)),
                raw_low=str(min(bar.low for bar in members)),
                raw_close=str(last.close),
                synthetic=False,
                quality_status="VALID",
                construction_profile_version=MULTI_ASSET_PROFILE_ID,
                provider_adapter_version=first.provider_adapter_version,
                source_timeframe=Timeframe.H1,
                source_candle_ids=tuple(
                    source_id
                    for bar in members
                    for source_id in (bar.source_candle_ids or ())
                ),
                forward_filled=False,
                ingestion_run_id=first.ingestion_run_id,
                created_at=first.created_at,
            )
        )
    return tuple(result)


@lru_cache(maxsize=1)
def family_session_authority():
    """The platform's approved family session authority (read-only consumer).

    The platform remains the calendar authority; Spect8 only reads it to mark
    approved closures. If the approved artifact is present, its digest must
    match the code authority or the read fails closed.
    """
    from hedgefund_market_data.providers.ig.family_authority import (
        FamilyAuthorityArtifact,
    )
    from hedgefund_market_data.providers.ig.family_authority import (
        build_family_session_authority,
    )
    from hedgefund_market_data.providers.ig.multi_asset_v1 import (
        build_multi_asset_artifact,
        build_multi_asset_instrument_master,
        build_multi_asset_registry,
    )

    artifact_path = os.environ.get("THE_SYSTEM_FAMILY_CALENDAR_FILE")
    if artifact_path:
        payload = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        artifact = FamilyAuthorityArtifact.from_payload(payload)
        if artifact.digest() != build_multi_asset_artifact().digest():
            raise ValueError("FAMILY_CALENDAR_ARTIFACT_DIGEST_MISMATCH")
    catalog, registry = build_multi_asset_registry()
    master = build_multi_asset_instrument_master()
    return build_family_session_authority(master, catalog=catalog, registry=registry)


def _gap_is_approved_closure(
    instrument_id: str, start: datetime, end: datetime
) -> bool:
    """True only when every M30 slot in the gap is NOT_EXPECTED by authority."""
    from hedgefund_market_data.domain import IntervalExpectation, Timeframe as PlatformTimeframe

    if end <= start:
        return False
    authority = family_session_authority()
    cursor = start.astimezone(timezone.utc)
    limit = end.astimezone(timezone.utc)
    while cursor < limit:
        expectation = authority.expected_interval(
            instrument_id,
            PlatformTimeframe.M30,
            cursor,
            cursor + timedelta(minutes=30),
        )
        if expectation is not IntervalExpectation.NOT_EXPECTED:
            return False
        cursor += timedelta(minutes=30)
    return True


def mark_multi_asset_closures(
    bars: tuple[Bar, ...], instrument_id: str
) -> tuple[Bar, ...]:
    """Mark approved closures exactly like ``mark_closures`` does for shares.

    A bar keeps ``expected_closure_before=False`` unless the entire preceding
    gap is NOT_EXPECTED by the platform's approved family calendar, so genuine
    data holes still fail closed downstream.
    """
    result: list[Bar] = []
    for bar in sorted(bars, key=lambda item: item.open_time):
        if (
            result
            and result[-1].close_time < bar.open_time
            and not bar.expected_closure_before
            and _gap_is_approved_closure(
                instrument_id, result[-1].close_time, bar.open_time
            )
        ):
            bar = replace(bar, expected_closure_before=True)
        result.append(bar)
    return tuple(result)


def prepare_multi_asset_history(child, batch, history) -> tuple:
    """Persist marked multi-asset bars and return bounded evaluation candidates."""
    instrument_id = history.spect8_instrument_id
    m30 = mark_multi_asset_closures(
        tuple(to_spect8_bar(bar) for bar in history.m30), instrument_id
    )
    h1 = mark_multi_asset_closures(history.h1, instrument_id)
    h4 = mark_multi_asset_closures(history.h4, instrument_id)
    child._repository.persist_canonical_bars((*m30, *h1, *h4))
    candidates: list[tuple[str, FilterMode, Timeframe, datetime]] = []
    # H4 candidates must close on an actual H1 close, exactly like the generic
    # platform path: the weekly filter snapshot is built at the latest H1 close
    # and a mismatched as-of can never be projected.
    h1_closes = {bar.close_time for bar in h1}
    for timeframe, bars, modes in (
        (Timeframe.M30, m30, (FilterMode.MICRO,)),
        (Timeframe.H1, h1, (FilterMode.MICRO, FilterMode.MACRO)),
        (Timeframe.H4, h4, (FilterMode.MACRO,)),
    ):
        for bar in bars[29:]:
            if bar.close_time < batch.available_as_of - timedelta(days=1):
                continue
            if timeframe is Timeframe.H4 and bar.close_time not in h1_closes:
                continue
            candidates.extend(
                (instrument_id, mode, timeframe, bar.close_time)
                for mode in modes
            )
    return tuple(
        sorted(
            set(candidates),
            key=lambda item: (item[3], item[0], item[2].value, item[1].value),
        )
    )


def multi_asset_history(
    batch: PlatformReadBatch, spect8_id: str
) -> PlatformInstrumentHistory:
    """Build one multi-asset history from exact platform evidence."""
    platform_id = spect8_id
    selected = tuple(
        bar
        for bar in first_accepted_versions(batch.bars)
        if bar.instrument_id == platform_id
    )
    grouped = {
        timeframe: tuple(
            sorted(
                (bar for bar in selected if bar.timeframe == timeframe),
                key=lambda bar: bar.open_time,
            )
        )
        for timeframe in ("M30", "H1", "D1", "W1")
    }
    native = tuple(
        bar
        for bar in batch.native_bootstrap_bars
        if bar.instrument_id == platform_id and bar.price_type == SPECT8_PRICE_TYPE
    )
    native_grouped = {
        timeframe: tuple(
            sorted(
                (
                    to_spect8_native_bootstrap_bar(bar)
                    for bar in native
                    if bar.timeframe == timeframe
                ),
                key=lambda bar: bar.open_time,
            )
        )
        for timeframe in ("H1", "D1", "W1")
    }
    canonical_h1 = tuple(to_spect8_bar(bar) for bar in grouped["H1"])
    merged_h1 = {(bar.open_time, bar.close_time): bar for bar in native_grouped["H1"]}
    merged_h1.update({(bar.open_time, bar.close_time): bar for bar in canonical_h1})
    h1 = tuple(sorted(merged_h1.values(), key=lambda bar: bar.open_time))

    def session_series(timeframe: str) -> tuple[Bar, ...]:
        canonical = tuple(to_spect8_bar(bar) for bar in grouped[timeframe])
        temporary = native_grouped[timeframe]
        if temporary and (
            not canonical or temporary[-1].close_time > canonical[-1].close_time
        ):
            return temporary
        return canonical

    h4 = multi_asset_h4_bars(
        h1, as_of=batch.available_as_of, instrument_id=platform_id
    )
    return PlatformInstrumentHistory(
        platform_instrument_id=platform_id,
        spect8_instrument_id=spect8_id,
        m30=grouped["M30"],
        h1=h1,
        h4=h4,
        d1=session_series("D1"),
        w1=session_series("W1"),
        h4_issues=(),
    )


__all__ = ["multi_asset_h4_bars", "multi_asset_history"]
