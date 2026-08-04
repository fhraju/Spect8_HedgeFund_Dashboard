from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..domain import Bar, Timeframe
from ..engine.models import InstrumentMetadata, StrategyRequest
from ..engine.strategy import STRATEGY_ID, Spect8StrategyEvaluator
from ..repository import SQLiteProjectionRepository
from ..service import WalkingSkeletonService
from .closed_bar import DAILY_ATR_INPUT_HISTORY, MIN_DAILY_HISTORY
from .daily_aggregator import NewYorkDailyAggregator

REBUILD_SOURCE_VERSION = "new_york_daily_rebuild:v1.0.3-atr10"


@dataclass(frozen=True, slots=True)
class DailyRebuildReport:
    dry_run: bool
    changed: bool
    source_h1_bars: int
    previous_daily_bars: int
    replacement_daily_bars: int
    evaluations_rebuilt: int
    events_rebuilt: int
    statuses_rebuilt: int
    rejected_sessions: tuple[str, ...]


class DailyRebuildService:
    """Controlled, atomic rebuild of canonical D1 and its projections."""

    def __init__(self, repository: SQLiteProjectionRepository) -> None:
        self._repository = repository
        self._aggregator = NewYorkDailyAggregator()
        self._walking = WalkingSkeletonService(
            Spect8StrategyEvaluator(),
            None,
            repository,
        )

    def rebuild(
        self,
        *,
        instrument: InstrumentMetadata,
        as_of: datetime,
        dry_run: bool,
    ) -> DailyRebuildReport:
        if as_of.tzinfo is None:
            raise ValueError("daily rebuild as_of must be timezone-aware")
        provider = instrument.provider
        instrument_id = instrument.instrument_id
        h1 = self._repository.canonical_bar_objects(
            provider, instrument_id, Timeframe.H1.value
        )
        h4 = self._repository.canonical_bar_objects(
            provider, instrument_id, Timeframe.H4.value
        )
        previous = self._repository.canonical_bar_objects(
            provider, instrument_id, Timeframe.D1.value
        )
        if not h1:
            raise ValueError("daily rebuild requires persisted canonical H1 bars")

        aggregation = self._aggregator.aggregate(h1, as_of=as_of)
        replacement = aggregation.bars
        if len(replacement) < 6:
            raise ValueError(
                "daily rebuild produced fewer than six complete New York sessions"
            )
        rejected = tuple(issue.detail for issue in aggregation.issues)
        projections = self._prepare_projections(
            instrument=instrument,
            h1=h1,
            h4=h4,
            daily=replacement,
            as_of=as_of,
        )
        expected_sources = {
            status.idempotency_key: status.source_case_id
            for status, _events in projections
        }
        current_sources = self._repository.projection_sources(
            STRATEGY_ID,
            provider,
            instrument_id,
        )
        changed = (
            self._bar_signature(previous) != self._bar_signature(replacement)
            or current_sources != expected_sources
        )
        if not changed:
            return DailyRebuildReport(
                dry_run=dry_run,
                changed=False,
                source_h1_bars=len(h1),
                previous_daily_bars=len(previous),
                replacement_daily_bars=len(replacement),
                evaluations_rebuilt=0,
                events_rebuilt=0,
                statuses_rebuilt=0,
                rejected_sessions=rejected,
            )
        events = sum(len(item[1]) for item in projections)
        if not dry_run:
            self._repository.replace_daily_and_projections(
                strategy_id=STRATEGY_ID,
                provider=provider,
                instrument_id=instrument_id,
                daily_bars=replacement,
                projections=projections,
            )
        return DailyRebuildReport(
            dry_run=dry_run,
            changed=True,
            source_h1_bars=len(h1),
            previous_daily_bars=len(previous),
            replacement_daily_bars=len(replacement),
            evaluations_rebuilt=len(projections),
            events_rebuilt=events,
            statuses_rebuilt=len(
                {status.timeframe for status, _events in projections}
            ),
            rejected_sessions=rejected,
        )

    def _prepare_projections(
        self,
        *,
        instrument: InstrumentMetadata,
        h1: tuple[Bar, ...],
        h4: tuple[Bar, ...],
        daily: tuple[Bar, ...],
        as_of: datetime,
    ) -> tuple:
        prepared = []
        for timeframe, stream in ((Timeframe.H1, h1), (Timeframe.H4, h4)):
            for index in range(29, len(stream)):
                trigger = stream[index]
                if trigger.close_time >= as_of:
                    continue
                eligible_daily = tuple(
                    bar for bar in daily if bar.close_time <= trigger.close_time
                )
                if len(eligible_daily) < MIN_DAILY_HISTORY:
                    continue
                request = StrategyRequest(
                    case_id=(
                        f"{REBUILD_SOURCE_VERSION}:"
                        f"{timeframe.value}:{trigger.close_time.isoformat()}"
                    ),
                    strategy_id=STRATEGY_ID,
                    timeframe=timeframe,
                    evaluation_time=trigger.close_time + timedelta(microseconds=1),
                    signal_bars=stream[index - 29 : index + 1],
                    daily_bars=eligible_daily[-DAILY_ATR_INPUT_HISTORY:],
                    instrument=instrument,
                )
                evaluated = self._walking.evaluate_request(request)
                prepared.append(
                    (
                        evaluated.status,
                        self._walking.events_for_projection(evaluated),
                    )
                )
        return tuple(prepared)

    @staticmethod
    def _bar_signature(bars: tuple[Bar, ...]) -> tuple:
        return tuple(
            (
                bar.open_time,
                bar.close_time,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.session_timezone,
            )
            for bar in bars
        )
