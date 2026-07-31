"""Temporary Phase 1 adapter for frozen golden expected results.

This module reads committed fixture artifacts as data.  It does not import the
golden reference calculator and contains no strategy formulas.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .domain import (
    Bar,
    BarClosedEvent,
    Direction,
    FilterResult,
    InstrumentStatus,
    LevelsResult,
    SignalResult,
    StrategyMarketValues,
    Timeframe,
)


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True, slots=True)
class AdaptedGoldenCase:
    bar_event: BarClosedEvent
    filter_result: FilterResult
    signal_result: SignalResult
    levels_result: LevelsResult | None
    levels_results: tuple[LevelsResult, ...]
    status: InstrumentStatus


class FrozenExpectedResultAdapter:
    def __init__(self, repository_root: Path) -> None:
        self._golden_root = repository_root / "golden"

    def load(self, case_id: str) -> AdaptedGoldenCase:
        case_directory = self._golden_root / "cases" / case_id
        expected = json.loads(
            (case_directory / "expected.json").read_text(encoding="utf-8")
        )
        instrument = json.loads(
            (case_directory / "instrument.json").read_text(encoding="utf-8")
        )
        signal_bar = self._load_selected_bar(
            case_directory / "signal_bars.csv",
            expected["timeframe"],
            expected["bars"]["signal_bar_close_time"],
        )
        timeframe = Timeframe(expected["timeframe"])
        bar = Bar(
            instrument_id=signal_bar["canonical_instrument_id"],
            timeframe=timeframe,
            open_time=_datetime(signal_bar["open_time"]),
            close_time=_datetime(signal_bar["close_time"]),
            open=Decimal(signal_bar["open"]),
            high=Decimal(signal_bar["high"]),
            low=Decimal(signal_bar["low"]),
            close=Decimal(signal_bar["close"]),
            provider=signal_bar["provider"],
            is_complete=signal_bar["is_complete"].lower() == "true",
        )
        occurred_at = _datetime(expected["evaluation_time"])
        bar_event = BarClosedEvent(
            strategy_id=expected["strategy_id"],
            bar=bar,
            occurred_at=occurred_at,
            source_case_id=case_id,
        )
        classification = expected["classification"]
        indicators = expected["indicators"]
        filter_result = FilterResult(
            buy_matched=classification["buy_filter_matched"],
            sell_matched=classification["sell_filter_matched"],
            daily_buy_level=Decimal(str(indicators["daily_buy_level"])),
            daily_sell_level=Decimal(str(indicators["daily_sell_level"])),
        )
        signal_result = SignalResult(
            technical_buy=classification["technical_buy_signal"],
            technical_sell=classification["technical_sell_signal"],
            confirmed_buy=classification["confirmed_buy"],
            confirmed_sell=classification["confirmed_sell"],
        )
        levels_results = self._levels(expected["candidates"])
        levels_result = levels_results[0] if len(levels_results) == 1 else None
        status = InstrumentStatus(
            strategy_id=expected["strategy_id"],
            provider=instrument["provider"],
            instrument_id=expected["instrument_id"],
            timeframe=timeframe,
            source_case_id=case_id,
            synthetic=True,
            data_status=expected["data_status"],
            dashboard_state=classification["dashboard_state"],
            filter_result=filter_result,
            signal_result=signal_result,
            levels_result=levels_result,
            levels_results=levels_results,
            reason_codes=tuple(expected.get("reason_codes", ())),
            market_values=StrategyMarketValues(
                signal_open=bar.open,
                signal_high=bar.high,
                signal_low=bar.low,
                signal_close=bar.close,
                sma10=Decimal(str(indicators["sma10"])),
                sma20=Decimal(str(indicators["sma20"])),
                atr_d1_wilder_5=Decimal(
                    str(indicators["atr_d1_wilder_5"])
                ),
                daily_raw_low=Decimal(str(indicators["daily_raw_low"])),
                daily_raw_high=Decimal(str(indicators["daily_raw_high"])),
                daily_buy_level=Decimal(
                    str(indicators["daily_buy_level"])
                ),
                daily_sell_level=Decimal(
                    str(indicators["daily_sell_level"])
                ),
                recent_low_21=Decimal(str(indicators["recent_low_21"])),
                recent_high_21=Decimal(str(indicators["recent_high_21"])),
                daily_context_close_time=_datetime(
                    expected["bars"]["daily_endpoint_close_time"]
                ),
            ),
            signal_bar_close_time=_datetime(
                expected["bars"]["signal_bar_close_time"]
            ),
            last_update=occurred_at,
            idempotency_key=bar_event.idempotency_key,
        )
        return AdaptedGoldenCase(
            bar_event=bar_event,
            filter_result=filter_result,
            signal_result=signal_result,
            levels_result=levels_result,
            levels_results=levels_results,
            status=status,
        )

    @staticmethod
    def _levels(
        candidates: dict[str, Any],
    ) -> tuple[LevelsResult, ...]:
        results: list[LevelsResult] = []
        for direction in (Direction.BUY, Direction.SELL):
            candidate = candidates[direction.value.lower()]
            if candidate is None:
                continue
            results.append(
                LevelsResult(
                    direction=direction,
                    entry_reference=Decimal(str(candidate["entry_reference"])),
                    raw_stop=Decimal(str(candidate["raw_strategy_stop"])),
                    display_stop=Decimal(
                        str(candidate["provider_adjusted_stop"])
                    ),
                    target=Decimal(str(candidate["target_3r"])),
                    target_risk_usd=Decimal(
                        str(candidate["target_risk_usd"])
                    ),
                    contract_size=(
                        Decimal(str(candidate["display_size"]))
                        if candidate["display_size"] is not None
                        else None
                    ),
                    contract_status=candidate["contract_status"],
                )
            )
        return tuple(results)

    @staticmethod
    def _load_selected_bar(
        path: Path, timeframe: str, close_time: str
    ) -> dict[str, str]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            matches = [
                row
                for row in csv.DictReader(handle)
                if row["timeframe"] == timeframe
                and row["close_time"] == close_time
                and row["is_complete"].lower() == "true"
            ]
        if len(matches) != 1:
            raise ValueError(
                f"{path}: expected one completed {timeframe} bar closing {close_time}"
            )
        return matches[0]
