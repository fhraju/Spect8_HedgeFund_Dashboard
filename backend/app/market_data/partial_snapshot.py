"""Deterministic forming-bar snapshots for Spect8.

Partial snapshots are explicitly incomplete and never compete with completed
canonical bars. They are derived from authoritative lower-timeframe components
already available before the bar close (no lookahead). At close, the canonical
completed M30/H1 remains authoritative and the partial for that window disappears.

Platform canonical UTC H4 is untouched; Spect8 broker-aligned H4 (00/04/08/12/16/20
broker time) is rebuilt from H1 (including partial) via the existing
BrokerAlignedH4Aggregator. This keeps reusable Platform semantics separate from
Spect8-specific boundaries.

Persistence: no DB table for partials. They are ephemeral, recomputed from the
same authoritative lower-TF buffer after restart. This keeps completed history
append-only and reproducible.

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from ..domain import Bar, Timeframe
from .forex_profile import BrokerAlignedH4Aggregator
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID

UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class CurrentBarSnapshot:
    """Spect8-side forming-bar view. `is_complete` is always False."""

    instrument_id: str
    timeframe: Timeframe
    bar_start: datetime
    bar_end: datetime  # expected close
    as_of: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    is_complete: bool = False
    source_provider_id: str = ""
    component_ids: tuple[str, ...] = ()
    provenance: str = ""

    def __post_init__(self) -> None:
        if self.is_complete is not False:
            raise ValueError("CurrentBarSnapshot must be incomplete")
        if self.timeframe not in (Timeframe.M30, Timeframe.H1, Timeframe.H4):
            # H4 here is Spect8 broker H4, which may be partial
            raise ValueError("CurrentBarSnapshot timeframe must be M30, H1 or H4")
        if self.bar_end <= self.bar_start:
            raise ValueError("bar_end must be after bar_start")
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")

    @property
    def is_partial(self) -> bool:
        return not self.is_complete

    def to_spect8_bar(self) -> Bar:
        """Convert to a Spect8 Bar with is_complete=False for downstream use without persisting as canonical."""
        return Bar(
            instrument_id=self.instrument_id,
            timeframe=self.timeframe,
            open_time=self.bar_start,
            close_time=self.bar_end,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            provider=self.source_provider_id or "MARKET_DATA_PLATFORM",
            is_complete=False,
            volume=None,
            session_timezone="UTC",
            raw_provider_symbol=self.instrument_id,
            raw_open_time=self.bar_start.isoformat(),
            raw_close_time=self.bar_end.isoformat(),
            raw_open=str(self.open),
            raw_high=str(self.high),
            raw_low=str(self.low),
            raw_close=str(self.close),
            synthetic=False,
            quality_status="VALID",
            construction_profile_version=PROFILE_ID,
            provider_adapter_version=self.provenance or "partial:v1",
            source_candle_ids=self.component_ids,
            forward_filled=False,
            ingestion_run_id=f"partial-{self.timeframe.value}-{self.bar_start.isoformat()}",
            created_at=self.as_of,
        )


def _floor_utc(value: datetime, duration: timedelta) -> datetime:
    secs = int(duration.total_seconds())
    return datetime.fromtimestamp(int(value.timestamp()) // secs * secs, tz=UTC)


def aggregate_partial_m30(
    instrument_id: str,
    m30_open: datetime,
    m5_components: Sequence[Bar],
    as_of: datetime,
    *,
    source_provider_id: str = "MARKET_DATA_PLATFORM",
) -> CurrentBarSnapshot | None:
    """Build forming M30 from 1..5 completed M5 Bars available before as_of.

    Each M5 must satisfy close_time <= as_of. No future component is used.
    """
    if not m5_components:
        return None
    m30_open_utc = m30_open.astimezone(UTC)
    m30_close = m30_open_utc + timedelta(minutes=30)
    as_of_utc = as_of.astimezone(UTC)
    if not (m30_open_utc <= as_of_utc < m30_close):
        if as_of_utc >= m30_close:
            return None
        raise ValueError("as_of must be inside M30 window for partial")
    sorted_comps = sorted(m5_components, key=lambda b: b.open_time)
    for comp in sorted_comps:
        if comp.close_time.astimezone(UTC) > as_of_utc:
            raise ValueError("component close_time > as_of (lookahead)")
        if not (m30_open_utc <= comp.open_time.astimezone(UTC) < m30_close):
            raise ValueError("component outside M30 window")
    if len(sorted_comps) >= 6:
        return None  # complete, use canonical
    return CurrentBarSnapshot(
        instrument_id=instrument_id,
        timeframe=Timeframe.M30,
        bar_start=m30_open_utc,
        bar_end=m30_close,
        as_of=as_of_utc,
        open=sorted_comps[0].open,
        high=max(c.high for c in sorted_comps),
        low=min(c.low for c in sorted_comps),
        close=sorted_comps[-1].close,
        is_complete=False,
        source_provider_id=source_provider_id,
        component_ids=tuple(c.source_candle_ids[0] if c.source_candle_ids else c.open_time.isoformat() for c in sorted_comps),
        provenance="PARTIAL_M30_FROM_M5_V1",
    )


def aggregate_partial_h1(
    instrument_id: str,
    h1_open: datetime,
    completed_m30: Bar | None,
    forming_m30: CurrentBarSnapshot | None,
    as_of: datetime,
    *,
    source_provider_id: str = "MARKET_DATA_PLATFORM",
) -> CurrentBarSnapshot | None:
    """Build forming H1 from at most one completed M30 and one forming M30."""
    h1_open_utc = h1_open.astimezone(UTC)
    h1_close = h1_open_utc + timedelta(hours=1)
    as_of_utc = as_of.astimezone(UTC)
    if not (h1_open_utc <= as_of_utc < h1_close):
        if as_of_utc >= h1_close:
            return None
        raise ValueError("as_of must be inside H1 window")
    m30_mid = h1_open_utc + timedelta(minutes=30)
    parts: list[CurrentBarSnapshot | Bar] = []
    if completed_m30 is not None:
        if completed_m30.close_time.astimezone(UTC) > as_of_utc:
            raise ValueError("completed_m30 not yet available")
        # Must be first half if forming exists
        if completed_m30.open_time.astimezone(UTC) != h1_open_utc:
            raise ValueError("completed_m30 must be first half of H1")
        parts.append(completed_m30)
    if forming_m30 is not None:
        if forming_m30.as_of.astimezone(UTC) > as_of_utc:
            raise ValueError("forming_m30 as_of > H1 as_of")
        if forming_m30.bar_start.astimezone(UTC) not in (h1_open_utc, m30_mid):
            raise ValueError("forming_m30 not aligned to H1 halves")
        parts.append(forming_m30)
    if not parts:
        return None
    # If we have only completed first half and no forming second half yet, H1 is just that half's OHLC until second half starts
    # But we can still return partial with only first half
    # Sort by start
    def _start(p):
        return p.bar_start if isinstance(p, CurrentBarSnapshot) else p.open_time  # type: ignore

    parts_sorted = sorted(parts, key=_start)
    open_v = parts_sorted[0].open  # type: ignore
    high_v = max(p.high for p in parts_sorted)  # type: ignore
    low_v = min(p.low for p in parts_sorted)  # type: ignore
    close_v = parts_sorted[-1].close  # type: ignore
    comp_ids: tuple[str, ...] = ()
    for p in parts_sorted:
        if isinstance(p, CurrentBarSnapshot):
            comp_ids += p.component_ids
        else:
            comp_ids += p.source_candle_ids  # type: ignore
    return CurrentBarSnapshot(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        bar_start=h1_open_utc,
        bar_end=h1_close,
        as_of=as_of_utc,
        open=open_v,
        high=high_v,
        low=low_v,
        close=close_v,
        is_complete=False,
        source_provider_id=source_provider_id,
        component_ids=comp_ids,
        provenance="PARTIAL_H1_FROM_M30_V1",
    )


def broker_partial_h4_from_h1_snapshots(
    h1_bars: Sequence[Bar],
    partial_h1: CurrentBarSnapshot | None,
    as_of: datetime,
) -> tuple[Bar, ...]:
    """Build Spect8 broker-aligned H4 bars: completed via aggregator + forming partial.

    Completed H4 uses the existing BrokerAlignedH4Aggregator (requires 4 contiguous
    H1, bucket_close <= as_of). For the current forming H4 bucket (1-3 H1s
    including partial_h1), we aggregate deterministically without waiting for
    4. This keeps Platform UTC H4 untouched and lets Spect8 build its broker
    00/04/08/12/16/20 partial H4 from authoritative H1.
    """
    as_of_utc = as_of.astimezone(UTC)
    # Completed H4 via existing aggregator
    all_h1 = list(h1_bars)
    # For completed, only use truly completed H1s (is_complete True). Partial is handled separately.
    completed_result = BrokerAlignedH4Aggregator().aggregate(tuple(sorted(all_h1, key=lambda b: b.close_time)), as_of=as_of)
    completed_bars = list(completed_result.bars)

    # Now handle forming H4 bucket containing partial_h1 (or last H1s that don't make a full bucket)
    # Determine the current broker H4 bucket for as_of
    from .forex_profile import broker_wall_time, broker_wall_to_utc

    bucket_wall = broker_wall_time(as_of_utc)
    bucket_wall = bucket_wall.replace(hour=(bucket_wall.hour // 4) * 4, minute=0, second=0, microsecond=0)
    bucket_open = broker_wall_to_utc(bucket_wall)
    bucket_close = broker_wall_to_utc(bucket_wall + timedelta(hours=4))
    if not (bucket_open <= as_of_utc < bucket_close):
        return tuple(completed_bars)
    # Collect H1s that belong to this forming bucket and are available before as_of
    # Include completed H1s plus partial_h1 if its bar_start is inside bucket
    forming_h1s: list[Bar] = []
    for bar in h1_bars:
        wall = broker_wall_time(bar.open_time)
        b_wall = wall.replace(hour=(wall.hour // 4) * 4, minute=0, second=0, microsecond=0)
        b_open = broker_wall_to_utc(b_wall)
        if b_open == bucket_open and bar.close_time.astimezone(UTC) <= as_of_utc:
            forming_h1s.append(bar)
    if partial_h1 is not None and partial_h1.bar_start.astimezone(UTC) >= bucket_open and partial_h1.bar_start.astimezone(UTC) < bucket_close:
        # Use partial H1's OHLC as Bar with is_complete False
        forming_h1s.append(partial_h1.to_spect8_bar())
    if not forming_h1s:
        return tuple(completed_bars)
    # Sort by open
    forming_h1s_sorted = sorted(forming_h1s, key=lambda b: b.open_time)
    # If we have 4, it would have been completed already and returned above; but if as_of < bucket_close, we treat as partial
    if len(forming_h1s_sorted) == 4 and forming_h1s_sorted[-1].close_time.astimezone(UTC) == bucket_close:
        # This would be complete, but bucket_close > as_of, so not yet complete
        pass
    if len(forming_h1s_sorted) > 4:
        forming_h1s_sorted = forming_h1s_sorted[-4:]  # keep last 4 if more (should not happen)
    # If we already have a completed bar for this bucket, don't duplicate
    if any(b.open_time == bucket_open for b in completed_bars):
        return tuple(completed_bars)
    # Aggregate partial H4: open first, high max, low min, close last
    first = forming_h1s_sorted[0]
    last = forming_h1s_sorted[-1]
    partial_h4 = Bar(
        instrument_id=first.instrument_id,
        timeframe=Timeframe.H4,
        open_time=bucket_open,
        close_time=bucket_close,
        open=first.open,
        high=max(b.high for b in forming_h1s_sorted),
        low=min(b.low for b in forming_h1s_sorted),
        close=last.close,
        provider=first.provider,
        is_complete=False,
        volume=None,
        session_timezone="UTC",
        raw_provider_symbol=first.raw_provider_symbol,
        raw_open_time=bucket_open.isoformat(),
        raw_close_time=bucket_close.isoformat(),
        raw_open=str(first.open),
        raw_high=str(max(b.high for b in forming_h1s_sorted)),
        raw_low=str(min(b.low for b in forming_h1s_sorted)),
        raw_close=str(last.close),
        synthetic=False,
        quality_status="VALID",
        construction_profile_version=PROFILE_ID,
        provider_adapter_version="partial-h4:v1",
        source_candle_ids=tuple(sid for b in forming_h1s_sorted for sid in b.source_candle_ids),
        forward_filled=False,
        ingestion_run_id=f"partial-H4-{bucket_open.isoformat()}",
        created_at=as_of_utc,
    )
    return tuple(completed_bars) + (partial_h4,)


__all__ = [
    "CurrentBarSnapshot",
    "aggregate_partial_m30",
    "aggregate_partial_h1",
    "broker_partial_h4_from_h1_snapshots",
]
