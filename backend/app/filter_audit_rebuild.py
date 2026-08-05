from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .domain import InstrumentStatus, Timeframe, primitive
from .engine.models import InstrumentMetadata, StrategyRequest
from .engine.strategy import STRATEGY_ID, Spect8StrategyEvaluator
from .market_data.closed_bar import (
    DAILY_ATR_INPUT_HISTORY,
    MIN_DAILY_HISTORY,
    MIN_SIGNAL_HISTORY,
    ClosedBarDetector,
)
from .repository import SQLiteProjectionRepository
from .service import WalkingSkeletonService


@dataclass(frozen=True, slots=True)
class FilterAuditRebuildItem:
    timeframe: str
    signal_close_time: str
    completed_bar_count: int
    recent_low: str
    recent_high: str
    final_classification: str
    changed: bool


@dataclass(frozen=True, slots=True)
class FilterAuditRebuildReport:
    dry_run: bool
    changed: bool
    statuses_rebuilt: int
    items: tuple[FilterAuditRebuildItem, ...]


class FilterAuditRebuildService:
    """Rebuild latest H1/H4 audit evidence from canonical SQLite bars."""

    def __init__(self, repository: SQLiteProjectionRepository) -> None:
        self._repository = repository
        self._detector = ClosedBarDetector()
        self._walking = WalkingSkeletonService(
            Spect8StrategyEvaluator(),
            None,
            repository,
        )

    def rebuild_latest(
        self,
        *,
        instrument: InstrumentMetadata,
        dry_run: bool,
    ) -> FilterAuditRebuildReport:
        persisted = {
            value["timeframe"]: value
            for value in self._repository.statuses()
            if value.get("strategy_id") == STRATEGY_ID
            and value.get("provider") == instrument.provider
            and value.get("instrument_id") == instrument.instrument_id
        }
        daily_stream = self._repository.canonical_bar_objects(
            instrument.provider,
            instrument.instrument_id,
            Timeframe.D1.value,
        )
        replacements: list[InstrumentStatus] = []
        items: list[FilterAuditRebuildItem] = []

        for timeframe in (Timeframe.H1, Timeframe.H4):
            current = persisted.get(timeframe.value)
            if current is None:
                raise ValueError(
                    f"{timeframe.value}: latest persisted evaluation is missing"
                )
            signal_close = self._timestamp(
                current.get("signal_bar_close_time"),
                f"{timeframe.value}: signal close",
            )
            evaluation_time = self._timestamp(
                current.get("last_update"),
                f"{timeframe.value}: evaluation time",
            )
            if evaluation_time <= signal_close:
                raise ValueError(
                    f"{timeframe.value}: evaluation time must be after signal close"
                )
            signal_stream = tuple(
                bar
                for bar in self._repository.canonical_bar_objects(
                    instrument.provider,
                    instrument.instrument_id,
                    timeframe.value,
                )
                if bar.close_time <= signal_close
            )
            signal_bars = signal_stream[-MIN_SIGNAL_HISTORY:]
            daily_bars = tuple(
                bar for bar in daily_stream if bar.close_time <= signal_close
            )[-DAILY_ATR_INPUT_HISTORY:]
            self._require_history(
                timeframe,
                signal_close,
                signal_bars,
                daily_bars,
            )
            request = StrategyRequest(
                case_id=current["source_case_id"],
                strategy_id=STRATEGY_ID,
                timeframe=timeframe,
                evaluation_time=evaluation_time,
                signal_bars=signal_bars,
                daily_bars=daily_bars,
                instrument=instrument,
            )
            first = self._walking.evaluate_request(request)
            second = self._walking.evaluate_request(request)
            if primitive(first.status) != primitive(second.status):
                raise RuntimeError(
                    f"{timeframe.value}: evaluator rebuild was not deterministic"
                )
            audit = first.status.filter_audit
            if audit is None:
                raise RuntimeError(
                    f"{timeframe.value}: evaluator did not produce filter audit"
                )
            replacements.append(first.status)
            items.append(
                FilterAuditRebuildItem(
                    timeframe=timeframe.value,
                    signal_close_time=current["signal_bar_close_time"],
                    completed_bar_count=audit.completed_bar_count,
                    recent_low=audit.recent_low,
                    recent_high=audit.recent_high,
                    final_classification=audit.final_classification,
                    changed=current.get("filter_audit") != primitive(audit),
                )
            )

        changed = sum(item.changed for item in items)
        rebuilt = 0
        if not dry_run and changed:
            rebuilt = self._repository.replace_latest_statuses_with_audit(
                tuple(replacements)
            )
        return FilterAuditRebuildReport(
            dry_run=dry_run,
            changed=bool(changed),
            statuses_rebuilt=rebuilt,
            items=tuple(items),
        )

    def _require_history(
        self,
        timeframe: Timeframe,
        signal_close: datetime,
        signal_bars: tuple,
        daily_bars: tuple,
    ) -> None:
        if len(signal_bars) < MIN_SIGNAL_HISTORY:
            raise ValueError(
                f"{timeframe.value}: requires {MIN_SIGNAL_HISTORY} completed "
                f"signal bars through {signal_close.isoformat()}, found {len(signal_bars)}"
            )
        if len(daily_bars) < MIN_DAILY_HISTORY:
            raise ValueError(
                f"{timeframe.value}: requires {MIN_DAILY_HISTORY} eligible New York "
                f"daily sessions through {signal_close.isoformat()}, found {len(daily_bars)}"
            )
        validation = self._detector.validate_history(
            signal_bars,
            daily_bars,
            timeframe,
            signal_close,
        )
        if validation.issues:
            raise ValueError(
                f"{timeframe.value}: canonical history is not eligible: "
                + ", ".join(validation.issues)
            )
        if any(
            bar.open_time.utcoffset().total_seconds() != 0
            or bar.close_time.utcoffset().total_seconds() != 0
            for bar in (*signal_bars, *daily_bars)
        ):
            raise ValueError(
                f"{timeframe.value}: canonical history contains non-UTC timestamps"
            )

    @staticmethod
    def _timestamp(value: object, label: str) -> datetime:
        if not isinstance(value, str):
            raise ValueError(f"{label} is missing")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError(f"{label} must be timezone-aware")
        return parsed
