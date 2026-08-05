from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from ..domain import Bar, Timeframe
from ..engine.models import InstrumentMetadata
from ..engine.strategy import STRATEGY_ID
from ..repository import SQLiteProjectionRepository
from .daily_aggregator import NewYorkDailyAggregator
from .daily_rebuild import DailyRebuildService
from .forex_profile import (
    BrokerAlignedH4Aggregator,
    GapType,
    classify_market_gap,
    market_h1_bars,
)
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID


@dataclass(frozen=True, slots=True)
class MarketProfileRebuildReport:
    profile_version: str
    dry_run: bool
    changed: bool
    source_h1_bars: int
    valid_h1_bars: int
    removed_invalid_h1_bars: int
    previous_h4_bars: int
    replacement_h4_bars: int
    previous_daily_bars: int
    replacement_daily_bars: int
    evaluations_rebuilt: int
    events_rebuilt: int
    incomplete_h4_buckets: tuple[str, ...]
    rejected_daily_sessions: tuple[str, ...]
    h4_source_links: int
    daily_source_links: int


class MarketProfileRebuildService:
    """Deterministically apply IC_MARKETS_NY_CLOSE_FOREX_V1 to one instrument."""

    def __init__(self, repository: SQLiteProjectionRepository) -> None:
        self._repository = repository
        self._h4 = BrokerAlignedH4Aggregator()
        self._daily = NewYorkDailyAggregator()
        self._projection_builder = DailyRebuildService(repository)

    def rebuild(
        self, *, instrument: InstrumentMetadata, as_of: datetime, dry_run: bool
    ) -> MarketProfileRebuildReport:
        if as_of.tzinfo is None:
            raise ValueError("market-profile rebuild as_of must be timezone-aware")
        provider, instrument_id = instrument.provider, instrument.instrument_id
        source = self._repository.canonical_bar_objects(
            provider, instrument_id, Timeframe.H1.value
        )
        previous_h4 = self._repository.canonical_bar_objects(
            provider, instrument_id, Timeframe.H4.value
        )
        previous_daily = self._repository.canonical_bar_objects(
            provider, instrument_id, Timeframe.D1.value
        )
        eligible_h1 = tuple(
            bar for bar in market_h1_bars(source) if bar.close_time <= as_of
        )
        enriched: list[Bar] = []
        for bar in eligible_h1:
            gap = classify_market_gap(enriched[-1], bar) if enriched else None
            source_id = bar.source_candle_ids or (
                f"{bar.provider}:{bar.instrument_id}:H1:"
                f"{bar.close_time.isoformat()}",
            )
            created_at = bar.created_at
            if created_at is None or created_at.year == 1970:
                created_at = bar.close_time
            enriched.append(
                replace(
                    bar,
                    synthetic=False,
                    quality_status="VALID",
                    construction_profile_version=PROFILE_ID,
                    provider_adapter_version=(
                        bar.provider_adapter_version
                        if bar.provider_adapter_version != "legacy"
                        else "legacy-migrated-v1"
                    ),
                    source_timeframe=Timeframe.H1,
                    source_candle_ids=source_id,
                    forward_filled=False,
                    expected_closure_before=(
                        gap is not None
                        and gap.gap_type is GapType.EXPECTED_MARKET_CLOSURE
                    ),
                    ingestion_run_id=(
                        bar.ingestion_run_id or "legacy-canonical-v1-migration"
                    ),
                    created_at=created_at,
                )
            )
        valid_h1 = tuple(enriched)
        if len(valid_h1) < 30:
            raise ValueError(
                "market-profile rebuild requires at least 30 valid completed H1 bars"
            )
        h4_result = self._h4.aggregate(valid_h1, as_of=as_of)
        daily_result = self._daily.aggregate(valid_h1, as_of=as_of)
        h4 = h4_result.bars
        daily = daily_result.bars
        projections = self._projection_builder._prepare_projections(
            instrument=instrument, h1=valid_h1, h4=h4, daily=daily, as_of=as_of
        )
        changed = (
            self._signature(source) != self._signature(valid_h1)
            or self._signature(previous_h4) != self._signature(h4)
            or self._signature(previous_daily) != self._signature(daily)
            or self._repository.projection_sources(STRATEGY_ID, provider, instrument_id)
            != {
                status.idempotency_key: status.source_case_id
                for status, _ in projections
            }
        )
        if changed and not dry_run:
            self._repository.replace_market_profile_and_projections(
                strategy_id=STRATEGY_ID,
                provider=provider,
                instrument_id=instrument_id,
                h1_bars=valid_h1,
                h4_bars=h4,
                daily_bars=daily,
                projections=projections,
            )
        return MarketProfileRebuildReport(
            profile_version=PROFILE_ID,
            dry_run=dry_run,
            changed=changed,
            source_h1_bars=len(source),
            valid_h1_bars=len(valid_h1),
            removed_invalid_h1_bars=len(source) - len(valid_h1),
            previous_h4_bars=len(previous_h4),
            replacement_h4_bars=len(h4),
            previous_daily_bars=len(previous_daily),
            replacement_daily_bars=len(daily),
            evaluations_rebuilt=len(projections) if changed else 0,
            events_rebuilt=sum(len(events) for _, events in projections)
            if changed
            else 0,
            incomplete_h4_buckets=tuple(
                issue.bucket_open.isoformat() for issue in h4_result.issues
            ),
            rejected_daily_sessions=tuple(
                issue.detail for issue in daily_result.issues
            ),
            h4_source_links=sum(len(bar.source_candle_ids) for bar in h4),
            daily_source_links=sum(len(bar.source_candle_ids) for bar in daily),
        )

    @staticmethod
    def _signature(bars: tuple[Bar, ...]) -> tuple:
        return tuple(
            (
                bar.open_time,
                bar.close_time,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.quality_status,
                bar.construction_profile_version,
                bar.provider_adapter_version,
                bar.source_timeframe,
                bar.source_candle_ids,
                bar.forward_filled,
                bar.expected_closure_before,
                bar.ingestion_run_id,
                bar.session_identifier,
                bar.session_open_broker_time,
                bar.session_close_broker_time,
            )
            for bar in bars
        )
