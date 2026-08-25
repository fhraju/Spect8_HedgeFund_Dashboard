"""Spect8 Signal Lifecycle V1 — FORMING → CONFIRMED → EXPIRED view.

Micro: M30 (30m hold), H1 (1h)
Macro: H1 (1h), H4 (1h, broker 00/04/08/12/16/20)

FORMING is provisional on partial bars (is_complete=False) when Platform is HEALTHY.
CONFIRMED is persisted with visible_until and survives restart. Duplicate
confirmation of same source bar is impossible via signal_id idempotency.

"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..domain import Bar
from ..repository import SQLiteProjectionRepository
from .partial_snapshot import CurrentBarSnapshot

UTC = timezone.utc

HOLD_PERIODS: dict[tuple[str, str], timedelta] = {
    ("MICRO", "M30"): timedelta(minutes=30),
    ("MICRO", "H1"): timedelta(hours=1),
    ("MACRO", "H1"): timedelta(hours=1),
    ("MACRO", "H4"): timedelta(hours=1),
}


class SignalState:
    FORMING = "FORMING"
    CONFIRMED = "CONFIRMED"
    NO_SIGNAL = "NO_SIGNAL"


@dataclass(frozen=True, slots=True)
class SignalSnapshot:
    instrument_id: str
    mode: str  # MICRO / MACRO
    timeframe: str
    direction: str  # BUY / SELL
    state: str
    source_bar_start: datetime
    source_bar_end: datetime
    formed_at: datetime | None
    confirmed_at: datetime | None
    visible_until: datetime | None


def _hold_for(mode: str, timeframe: str) -> timedelta:
    try:
        return HOLD_PERIODS[(mode, timeframe)]
    except KeyError:
        raise ValueError(f"no hold period for {mode} {timeframe}")


def _signal_id(
    instrument_id: str,
    mode: str,
    timeframe: str,
    bar_start: datetime,
    direction: str,
    version: str,
) -> str:
    return f"{instrument_id}:{mode}:{timeframe}:{bar_start.isoformat()}:{direction}:{version}"


class SignalLifecycleService:
    """Deterministic FORMING/CONFIRMED lifecycle, persistence, and recovery."""

    def __init__(
        self,
        repository: SQLiteProjectionRepository,
        *,
        strategy_version: str = "SPECT8_MICRO_DAILY_V1_0_3",
        market_data_source: str = "MARKET_DATA_PLATFORM",
        signal_evaluator: Callable[[Bar | CurrentBarSnapshot], str | None]
        | None = None,
    ) -> None:
        self._repo = repository
        self._strategy_version = strategy_version
        self._market_data_source = market_data_source
        # evaluator returns direction "BUY"/"SELL" or None; for tests inject simple logic
        self._evaluator = signal_evaluator or self._default_evaluator

    @staticmethod
    def _default_evaluator(bar: Bar | CurrentBarSnapshot) -> str | None:
        # Reuse existing Spect8 logic minimally: for demo, BUY if close > open else SELL if < else None
        # Real signal logic would call Spect8StrategyEvaluator via StrategyRequest; we keep it simple
        # but still reuse the same threshold concept (close vs open) to prove forming uses same rule.
        # This is intentionally simple and deterministic for foundation.
        try:
            if bar.close > bar.open:
                return "BUY"
            if bar.close < bar.open:
                return "SELL"
        except Exception:
            return None
        return None

    def evaluate_forming(
        self,
        *,
        instrument_id: str,
        mode: str,
        timeframe: str,
        snapshot: CurrentBarSnapshot,
        as_of: datetime,
        platform_healthy: bool,
        evaluated_direction: str | None = None,
        direction_was_evaluated: bool = False,
    ) -> SignalSnapshot | None:
        """Return FORMING if partial satisfies signal and Platform HEALTHY."""
        if not platform_healthy:
            return None
        if snapshot.is_complete:
            raise ValueError("forming requires is_complete=False")
        if snapshot.as_of.astimezone(UTC) > as_of.astimezone(UTC):
            raise ValueError("snapshot as_of > evaluation as_of (lookahead)")
        direction = (
            evaluated_direction
            if direction_was_evaluated
            else self._evaluator(snapshot)
        )
        if direction is None:
            return None
        return SignalSnapshot(
            instrument_id=instrument_id,
            mode=mode,
            timeframe=timeframe,
            direction=direction,
            state=SignalState.FORMING,
            source_bar_start=snapshot.bar_start,
            source_bar_end=snapshot.bar_end,
            formed_at=snapshot.as_of,
            confirmed_at=None,
            visible_until=None,
        )

    def confirm(
        self,
        *,
        instrument_id: str,
        mode: str,
        timeframe: str,
        completed_bar: Bar,
        as_of: datetime,
        direction: str | None = None,
        provider: str = "MARKET_DATA_PLATFORM",
        strategy_version: str | None = None,
    ) -> SignalSnapshot | None:
        """Confirm at authoritative close. Persists with visible_until."""
        if not completed_bar.is_complete:
            raise ValueError("confirm requires completed bar")
        if completed_bar.close_time.astimezone(UTC) > as_of.astimezone(UTC):
            raise ValueError("bar not yet closed")
        # If direction not supplied, evaluate completed bar (reuse same rule)
        if direction is None:
            direction = self._evaluator(completed_bar)
        if direction is None:
            return None
        hold = _hold_for(mode, timeframe)
        confirmed_at = completed_bar.close_time.astimezone(UTC)
        visible_until = confirmed_at + hold
        effective_version = strategy_version or self._strategy_version
        sig_id = _signal_id(
            instrument_id,
            mode,
            timeframe,
            completed_bar.open_time,
            direction,
            effective_version,
        )
        # Idempotent persist
        self._repo.persist_confirmed_signal(
            signal_id=sig_id,
            instrument_id=instrument_id,
            mode=mode,
            timeframe=timeframe,
            direction=direction,
            source_bar_start=completed_bar.open_time,
            source_bar_end=completed_bar.close_time,
            formed_at=None,
            confirmed_at=confirmed_at,
            visible_until=visible_until,
            market_data_source=self._market_data_source,
            strategy_version=effective_version,
            source_provider=provider,
        )
        return SignalSnapshot(
            instrument_id=instrument_id,
            mode=mode,
            timeframe=timeframe,
            direction=direction,
            state=SignalState.CONFIRMED,
            source_bar_start=completed_bar.open_time,
            source_bar_end=completed_bar.close_time,
            formed_at=None,
            confirmed_at=confirmed_at,
            visible_until=visible_until,
        )

    def current_confirmed(self, as_of: datetime) -> tuple[dict, ...]:
        return self._repo.current_confirmed_signals(as_of)

    def all_confirmed(self) -> tuple[dict, ...]:
        return self._repo.all_confirmed_signals()

    # Recovery helpers for M30 forming: persist component open times so restart can rebuild

    def persist_forming_recovery(
        self,
        instrument_id: str,
        timeframe: str,
        m30_open: datetime,
        provider: str,
        components: tuple[datetime, ...],
    ) -> None:
        self._repo.persist_forming_recovery(
            instrument_id=instrument_id,
            timeframe=timeframe,
            m30_open=m30_open,
            provider=provider,
            component_open_times=components,
        )

    def load_forming_recovery(
        self, instrument_id: str, timeframe: str, m30_open: datetime
    ) -> tuple[datetime, ...] | None:
        row = self._repo.forming_recovery_snapshot(instrument_id, timeframe, m30_open)
        if row is None:
            return None
        import json

        return tuple(
            datetime.fromisoformat(s.replace("Z", "+00:00"))
            for s in json.loads(row["component_open_times_json"])
        )

    def clear_forming_recovery(
        self, instrument_id: str, timeframe: str, m30_open: datetime
    ) -> None:
        self._repo.clear_forming_recovery(instrument_id, timeframe, m30_open)
