from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..domain import Timeframe
from .models import (
    CanonicalInstrument,
    HealthState,
    ProviderClosedBar,
    ProviderHealth,
    ProviderHistory,
    ProviderIdentity,
    ProviderProfile,
    RawProviderCandle,
    TimestampSemantics,
)


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


@dataclass(frozen=True, slots=True)
class _ReplayCase:
    source_id: str
    instrument_id: str
    evaluation_time: datetime
    timeframe: Timeframe
    terminal: RawProviderCandle
    signal_source: tuple[RawProviderCandle, ...]
    daily_source: tuple[RawProviderCandle, ...]


class ReplayMarketDataProvider:
    """Deterministic provider adapter over committed CSV/JSON fixtures."""

    def __init__(
        self,
        fixture_root: Path,
        case_ids: tuple[str, ...],
    ) -> None:
        self._fixture_root = fixture_root
        manifest = json.loads(
            (fixture_root / "manifest.json").read_text(encoding="utf-8")
        )
        definitions = {case["id"]: case for case in manifest["cases"]}
        self._cases: list[_ReplayCase] = []
        instruments: dict[tuple[str, str], CanonicalInstrument] = {}

        for case_id in case_ids:
            try:
                definition = definitions[case_id]
            except KeyError as error:
                raise ValueError(f"unknown replay case: {case_id}") from error
            directory = fixture_root / definition["path"]
            raw_metadata = json.loads(
                (directory / "instrument.json").read_text(encoding="utf-8")
            )
            signal_source = self._load_candles(
                directory / "signal_bars.csv",
                raw_metadata["session_timezone"],
            )
            daily_source = self._load_candles(
                directory / "daily_bars.csv",
                raw_metadata["session_timezone"],
            )
            timeframe = Timeframe(definition["timeframe"])
            evaluation_time = _datetime(definition["evaluation_time"])
            eligible = [
                candle
                for candle in signal_source
                if candle.timeframe is timeframe
                and candle.provider_id == raw_metadata["provider"]
                and candle.provider_symbol == raw_metadata["instrument_id"]
                and candle.is_complete
                and _datetime(candle.raw_close_time) < evaluation_time
            ]
            if not eligible:
                raise ValueError(f"{case_id}: no completed terminal candle")
            terminal = eligible[-1]
            replay_case = _ReplayCase(
                source_id=case_id,
                instrument_id=raw_metadata["instrument_id"],
                evaluation_time=evaluation_time,
                timeframe=timeframe,
                terminal=terminal,
                signal_source=signal_source,
                daily_source=daily_source,
            )
            self._cases.append(replay_case)

            key = (
                raw_metadata["provider"],
                raw_metadata["instrument_id"],
            )
            available = {
                Timeframe.D1,
                *(
                    existing.timeframe
                    for existing in self._cases
                    if (
                        existing.terminal.provider_id,
                        existing.instrument_id,
                    )
                    == key
                ),
            }
            instruments[key] = CanonicalInstrument(
                instrument_id=raw_metadata["instrument_id"],
                provider_id=raw_metadata["provider"],
                provider_symbol=raw_metadata["instrument_id"],
                display_name=raw_metadata["display_name"],
                asset_class="METAL",
                point_size=Decimal(str(raw_metadata["point_size"])),
                tick_size=_optional_decimal(raw_metadata["tick_size"]),
                price_precision=int(raw_metadata["price_precision"]),
                tick_value_usd=_optional_decimal(raw_metadata["tick_value_usd"]),
                conversion_rate_to_usd=_optional_decimal(
                    raw_metadata["conversion_rate_to_usd"]
                ),
                contract_min=_optional_decimal(raw_metadata["contract_min"]),
                contract_max=_optional_decimal(raw_metadata["contract_max"]),
                contract_step=_optional_decimal(raw_metadata["contract_step"]),
                minimum_stop_distance_points=_optional_decimal(
                    raw_metadata["minimum_stop_distance_points"]
                ),
                quote_currency="USD",
                profit_currency="USD",
                session_timezone=raw_metadata["session_timezone"],
                candle_boundary_convention=raw_metadata["candle_boundary_convention"],
                available_timeframes=tuple(
                    sorted(available, key=lambda value: value.value)
                ),
                strategy_id=raw_metadata["strategy_id"],
            )
        self._instruments = tuple(instruments.values())
        self._identity = ProviderIdentity(
            provider_id=(
                self._instruments[0].provider_id if self._instruments else "REPLAY"
            ),
            display_name="Deterministic Replay Provider",
            adapter_version="1.0",
            synthetic=True,
        )

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def discover_instruments(self) -> tuple[CanonicalInstrument, ...]:
        return self._instruments

    def provider_profile(self) -> ProviderProfile:
        return ProviderProfile(
            provider_name=self.identity.provider_id,
            adapter_version=self.identity.adapter_version,
            timestamp_semantics=TimestampSemantics.INTERVAL_START,
            native_timeframes=tuple(
                sorted(
                    {case.timeframe for case in self._cases},
                    key=lambda value: value.value,
                )
            ),
        )

    def map_symbol(self, canonical_instrument: str) -> str:
        if canonical_instrument not in {
            instrument.instrument_id for instrument in self._instruments
        }:
            raise ValueError("replay adapter has no mapped instrument")
        return canonical_instrument

    def fetch_raw_candles(
        self,
        instrument: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[RawProviderCandle, ...]:
        self.map_symbol(instrument)
        values = {
            candle.source_id
            or (
                f"{candle.provider_id}:{instrument}:{timeframe.value}:"
                f"{candle.raw_close_time}"
            ): candle
            for case in self._cases
            for candle in (*case.signal_source, *case.daily_source)
            if candle.timeframe is timeframe
            and start <= _datetime(candle.raw_close_time) <= end
        }
        return tuple(sorted(values.values(), key=lambda candle: candle.raw_close_time))

    @staticmethod
    def normalize_timestamp(
        provider_timestamp: str,
        semantics: TimestampSemantics,
        timeframe: Timeframe,
    ) -> tuple[datetime, datetime]:
        value = _datetime(provider_timestamp)
        step = {
            Timeframe.H1: timedelta(hours=1),
            Timeframe.H4: timedelta(hours=4),
            Timeframe.D1: timedelta(days=1),
        }[timeframe]
        if semantics in (
            TimestampSemantics.OPEN_TIME,
            TimestampSemantics.INTERVAL_START,
        ):
            return value, value + step
        if semantics in (
            TimestampSemantics.CLOSE_TIME,
            TimestampSemantics.INTERVAL_END,
        ):
            return value - step, value
        raise ValueError("replay timestamp semantics must be explicit")

    def report_health(self, as_of: datetime) -> ProviderHealth:
        return self.health(as_of)

    def initial_clock_time(self) -> datetime:
        if not self._cases:
            raise ValueError("replay provider has no configured cases")
        return max(case.evaluation_time for case in self._cases)

    def fetch_completed_bars(
        self,
        as_of: datetime,
    ) -> tuple[ProviderClosedBar, ...]:
        self._require_aware(as_of)
        eligible = [
            ProviderClosedBar(
                source_id=case.source_id,
                instrument_id=case.instrument_id,
                evaluation_time=case.evaluation_time,
                candle=case.terminal,
            )
            for case in self._cases
            if case.evaluation_time <= as_of
            and case.terminal.is_complete
            and _datetime(case.terminal.raw_close_time) <= as_of
        ]
        return tuple(
            sorted(
                eligible,
                key=lambda item: (
                    _datetime(item.candle.raw_close_time),
                    item.candle.timeframe.value,
                    item.source_id,
                ),
            )
        )

    def fetch_required_history(
        self,
        closed_bar: ProviderClosedBar,
        as_of: datetime,
    ) -> ProviderHistory:
        self._require_aware(as_of)
        replay_case = next(
            (case for case in self._cases if case.source_id == closed_bar.source_id),
            None,
        )
        if replay_case is None:
            raise KeyError(f"unknown replay source: {closed_bar.source_id}")
        trigger_close = _datetime(closed_bar.candle.raw_close_time)
        signal = tuple(
            candle
            for candle in replay_case.signal_source
            if candle.timeframe is replay_case.timeframe
            and candle.provider_id == closed_bar.candle.provider_id
            and candle.provider_symbol == closed_bar.candle.provider_symbol
            and candle.is_complete
            and _datetime(candle.raw_close_time) <= trigger_close
            and _datetime(candle.raw_close_time) <= as_of
        )
        daily = tuple(
            candle
            for candle in replay_case.daily_source
            if candle.timeframe is Timeframe.D1
            and candle.provider_id == closed_bar.candle.provider_id
            and candle.provider_symbol == closed_bar.candle.provider_symbol
            and candle.is_complete
            and _datetime(candle.raw_close_time) <= trigger_close
            and _datetime(candle.raw_close_time) <= as_of
        )
        return ProviderHistory(
            source_id=replay_case.source_id,
            instrument_id=replay_case.instrument_id,
            timeframe=replay_case.timeframe,
            evaluation_time=replay_case.evaluation_time,
            signal_bars=signal,
            daily_bars=daily,
        )

    def health(self, as_of: datetime) -> ProviderHealth:
        self._require_aware(as_of)
        eligible = [
            case
            for case in self._cases
            if case.evaluation_time <= as_of and case.terminal.is_complete
        ]
        if not eligible:
            return ProviderHealth(
                provider_id=self.identity.provider_id,
                state=HealthState.DATA_UNAVAILABLE,
                checked_at=as_of,
                latest_completed_close=None,
                freshness_seconds=None,
                detail="No replay candle is available at the fixed clock.",
            )
        latest_case = max(
            eligible,
            key=lambda case: _datetime(case.terminal.raw_close_time),
        )
        latest_close = _datetime(latest_case.terminal.raw_close_time)
        freshness = max(0, int((as_of - latest_close).total_seconds()))
        timeframe_duration = {
            Timeframe.H1: timedelta(hours=1),
            Timeframe.H4: timedelta(hours=4),
        }[latest_case.timeframe]
        state = (
            HealthState.HEALTHY
            if as_of - latest_close <= timeframe_duration * 2
            else HealthState.STALE
        )
        return ProviderHealth(
            provider_id=self.identity.provider_id,
            state=state,
            checked_at=as_of,
            latest_completed_close=latest_close,
            freshness_seconds=freshness,
            detail=(
                "Replay data is within the configured freshness window."
                if state is HealthState.HEALTHY
                else "Latest replay candle is outside the freshness window."
            ),
        )

    @staticmethod
    def _load_candles(
        path: Path,
        session_timezone: str,
    ) -> tuple[RawProviderCandle, ...]:
        rows: list[RawProviderCandle] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append(
                    RawProviderCandle(
                        provider_id=row["provider"],
                        provider_symbol=row["canonical_instrument_id"],
                        timeframe=Timeframe(row["timeframe"]),
                        raw_open_time=row["open_time"],
                        raw_close_time=row["close_time"],
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=row.get("volume") or None,
                        is_complete=row["is_complete"].lower() == "true",
                        session_timezone=session_timezone,
                        provider_name=row["provider"],
                        canonical_instrument=row["canonical_instrument_id"],
                        source_timeframe=Timeframe(row["timeframe"]),
                        provider_timestamp=row["open_time"],
                        timestamp_semantics=TimestampSemantics.INTERVAL_START,
                        open_time_utc=_datetime(row["open_time"]),
                        close_time_utc=_datetime(row["close_time"]),
                        source_id=(
                            f"replay:{row['canonical_instrument_id']}:"
                            f"{row['timeframe']}:{row['close_time']}"
                        ),
                        received_at=_datetime(row["close_time"]),
                        provider_metadata={"fixture": path.name},
                        adapter_version="1.0",
                    )
                )
        return tuple(rows)

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("provider clock must be timezone-aware")
