"""Independent calculator for SPECT8_MICRO_DAILY_V1_0 golden fixtures.

This module intentionally has no production-service dependencies.  It reads a
fixture directory, rejects unsafe candle streams, applies the frozen formulas,
and returns a deterministic JSON-compatible result.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN, getcontext
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

getcontext().prec = 28

STRATEGY_ID = "SPECT8_MICRO_DAILY_V1_0"
ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
TIMEFRAME_STEP = {
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D1": timedelta(days=1),
}
NEW_YORK = ZoneInfo("America/New_York")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _number(value: Decimal | None) -> float | None:
    if value is None:
        return None
    if value == ZERO:
        return 0.0
    return float(value.quantize(Decimal("0.0000000001")).normalize())


def _load_candles(path: Path) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            candles.append(
                {
                    "canonical_instrument_id": row["canonical_instrument_id"],
                    "timeframe": row["timeframe"],
                    "open_time": _dt(row["open_time"]),
                    "close_time": _dt(row["close_time"]),
                    "open": Decimal(row["open"]),
                    "high": Decimal(row["high"]),
                    "low": Decimal(row["low"]),
                    "close": Decimal(row["close"]),
                    "volume": (
                        Decimal(row["volume"]) if row.get("volume", "") else None
                    ),
                    "provider": row["provider"],
                    "is_complete": row["is_complete"].lower() == "true",
                }
            )
    return candles


def _stream_issues(
    candles: list[dict[str, Any]],
    timeframe: str,
) -> list[str]:
    relevant = [bar for bar in candles if bar["timeframe"] == timeframe]
    issues: list[str] = []
    identities = [
        (
            bar["canonical_instrument_id"],
            bar["timeframe"],
            bar["open_time"],
            bar["close_time"],
            bar["provider"],
        )
        for bar in relevant
    ]
    if len(identities) != len(set(identities)):
        issues.append("DUPLICATE_CANDLE")

    open_times = [bar["open_time"] for bar in relevant]
    if any(current < previous for previous, current in zip(open_times, open_times[1:])):
        issues.append("OUT_OF_ORDER_CANDLE")
    return issues


def _missing_issue(
    candles: list[dict[str, Any]],
    timeframe: str,
    issue: str,
) -> list[str]:
    step = TIMEFRAME_STEP[timeframe]
    def expected(previous: dict[str, Any], current: dict[str, Any]) -> bool:
        if current["open_time"] - previous["open_time"] == step:
            return True
        if timeframe != "D1":
            return False
        previous_close = previous["close_time"].astimezone(NEW_YORK)
        current_open = current["open_time"].astimezone(NEW_YORK)
        return (
            previous_close.weekday() == 4
            and previous_close.hour == 17
            and current_open.weekday() == 6
            and current_open.hour == 17
        )

    if any(
        not expected(previous, current)
        for previous, current in zip(candles, candles[1:])
    ):
        return [issue]
    return []


def _sma(candles: list[dict[str, Any]], period: int) -> Decimal:
    return sum((bar["close"] for bar in candles[-period:]), ZERO) / Decimal(period)


def _wilder_atr(candles: list[dict[str, Any]], period: int = 5) -> Decimal:
    """MT4-compatible Wilder ATR seeded by the first period true ranges.

    The first candle supplies only the previous close.  The next ``period``
    candles supply the seed true ranges.  Any later bars apply Wilder's
    recurrence, ending on the most recently completed D1 candle.
    """

    true_ranges: list[Decimal] = []
    for previous, current in zip(candles, candles[1:]):
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    if len(true_ranges) < period:
        raise ValueError("insufficient D1 warm-up for ATR")
    atr = sum(true_ranges[:period], ZERO) / Decimal(period)
    for true_range in true_ranges[period:]:
        atr = ((atr * Decimal(period - 1)) + true_range) / Decimal(period)
    return atr


def _pivot(
    newest_first: list[dict[str, Any]],
    direction: str,
) -> tuple[dict[str, Any], Decimal, bool, Decimal]:
    recent = newest_first[:10]
    extreme = (
        min(bar["low"] for bar in recent)
        if direction == "BUY"
        else max(bar["high"] for bar in recent)
    )
    # newest_first makes the first equality the mandated most recent occurrence.
    pivot = next(
        bar
        for bar in recent
        if bar["low" if direction == "BUY" else "high"] == extreme
    )
    pivot_index = newest_first.index(pivot)
    structural_window = newest_first[pivot_index : pivot_index + 20]
    structural_extreme = (
        min(bar["low"] for bar in structural_window)
        if direction == "BUY"
        else max(bar["high"] for bar in structural_window)
    )
    passed = structural_extreme >= extreme if direction == "BUY" else structural_extreme <= extreme
    return pivot, extreme, passed, structural_extreme


def _contract(
    instrument: dict[str, Any],
    stop_distance: Decimal,
) -> dict[str, Any]:
    metadata_keys = (
        "tick_size",
        "tick_value_usd",
        "conversion_rate_to_usd",
        "contract_min",
        "contract_max",
        "contract_step",
    )
    if any(instrument.get(key) is None for key in metadata_keys):
        return {
            "target_risk_usd": 100.0,
            "monetary_loss_per_one_contract": None,
            "raw_size": None,
            "display_size": None,
            "contract_status": "METADATA_UNAVAILABLE",
        }

    tick_size = Decimal(str(instrument["tick_size"]))
    tick_value = Decimal(str(instrument["tick_value_usd"]))
    conversion = Decimal(str(instrument["conversion_rate_to_usd"]))
    minimum = Decimal(str(instrument["contract_min"]))
    maximum = Decimal(str(instrument["contract_max"]))
    step = Decimal(str(instrument["contract_step"]))
    loss = (stop_distance / tick_size) * tick_value * conversion
    if loss <= ZERO:
        return {
            "target_risk_usd": 100.0,
            "monetary_loss_per_one_contract": _number(loss),
            "raw_size": None,
            "display_size": None,
            "contract_status": "METADATA_UNAVAILABLE",
        }
    raw_size = ONE_HUNDRED / loss
    if raw_size < minimum:
        return {
            "target_risk_usd": 100.0,
            "monetary_loss_per_one_contract": _number(loss),
            "raw_size": _number(raw_size),
            "display_size": None,
            "contract_status": "BELOW_PROVIDER_MINIMUM",
        }
    capped = min(raw_size, maximum)
    display_size = (capped / step).to_integral_value(rounding=ROUND_DOWN) * step
    return {
        "target_risk_usd": 100.0,
        "monetary_loss_per_one_contract": _number(loss),
        "raw_size": _number(raw_size),
        "display_size": _number(display_size),
        "contract_status": "VALID",
    }


def _empty_result(
    case_id: str,
    instrument: dict[str, Any],
    timeframe: str,
    evaluation_time: datetime,
    issues: Iterable[str],
    excluded_incomplete_count: int,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "strategy_id": STRATEGY_ID,
        "instrument_id": instrument["instrument_id"],
        "timeframe": timeframe,
        "evaluation_time": _iso(evaluation_time),
        "data_status": "UNAVAILABLE",
        "issues": sorted(set(issues)),
        "bars": {
            "signal_completed_count": 0,
            "daily_completed_count": 0,
            "excluded_incomplete_count": excluded_incomplete_count,
            "signal_bar_close_time": None,
            "daily_endpoint_close_time": None,
        },
        "classification": {
            "buy_filter_matched": None,
            "sell_filter_matched": None,
            "buy_sma_rejection": None,
            "sell_sma_rejection": None,
            "buy_structural_pivot": None,
            "sell_structural_pivot": None,
            "technical_buy_signal": None,
            "technical_sell_signal": None,
            "confirmed_buy": None,
            "confirmed_sell": None,
            "dashboard_state": "DATA_UNAVAILABLE",
        },
        "indicators": None,
        "pivots": None,
        "candidates": {"buy": None, "sell": None},
        "evidence": ["Unsafe candle input was quarantined; no strategy result was calculated."],
    }


def evaluate_case(
    case_directory: str | Path,
    case_definition: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate one manifest case and return its expected-result structure."""

    case_path = Path(case_directory)
    instrument = json.loads(
        (case_path / "instrument.json").read_text(encoding="utf-8")
    )
    timeframe = case_definition["timeframe"]
    case_id = case_definition["id"]
    evaluation_time = _dt(case_definition["evaluation_time"])
    signal_source = _load_candles(case_path / "signal_bars.csv")
    daily_source = _load_candles(case_path / "daily_bars.csv")

    selected_source = [
        bar
        for bar in signal_source
        if bar["timeframe"] == timeframe
        and bar["canonical_instrument_id"] == instrument["instrument_id"]
        and bar["provider"] == instrument["provider"]
    ]
    excluded = sum(
        1
        for bar in selected_source
        if not bar["is_complete"] or bar["close_time"] >= evaluation_time
    )
    completed_signal = [
        bar
        for bar in selected_source
        if bar["is_complete"] and bar["close_time"] < evaluation_time
    ]

    issues = _stream_issues(signal_source, timeframe)
    issues.extend(_stream_issues(daily_source, "D1"))
    issues.extend(
        _missing_issue(completed_signal, timeframe, "MISSING_SIGNAL_CANDLE")
    )

    if completed_signal:
        signal_close = completed_signal[-1]["close_time"]
        selected_daily_source = [
            bar
            for bar in daily_source
            if bar["timeframe"] == "D1"
            and bar["canonical_instrument_id"] == instrument["instrument_id"]
            and bar["provider"] == instrument["provider"]
        ]
        completed_daily = [
            bar
            for bar in selected_daily_source
            if bar["is_complete"] and bar["close_time"] <= signal_close
        ]
    else:
        completed_daily = []
    issues.extend(_missing_issue(completed_daily, "D1", "MISSING_DAILY_CANDLE"))

    if len(completed_signal) < 30:
        issues.append("INSUFFICIENT_SIGNAL_HISTORY")
    if len(completed_daily) < 6:
        issues.append("INSUFFICIENT_DAILY_HISTORY")
    if issues:
        return _empty_result(
            case_id,
            instrument,
            timeframe,
            evaluation_time,
            issues,
            excluded,
        )

    atr = _wilder_atr(completed_daily, 5)
    activation_buffer = atr * Decimal("0.05")
    stop_atr_distance = atr * Decimal("0.35")
    last_daily = completed_daily[-2:]
    daily_raw_low = min(bar["low"] for bar in last_daily)
    daily_raw_high = max(bar["high"] for bar in last_daily)
    daily_buy_level = daily_raw_low + activation_buffer
    daily_sell_level = daily_raw_high - activation_buffer

    latest = completed_signal[-1]
    newest_first = list(reversed(completed_signal))
    recent_21 = newest_first[:21]
    recent_low = min(bar["low"] for bar in recent_21)
    recent_high = max(bar["high"] for bar in recent_21)
    sma10 = _sma(completed_signal, 10)
    sma20 = _sma(completed_signal, 20)

    buy_filter = recent_low <= daily_buy_level
    sell_filter = recent_high >= daily_sell_level
    buy_sma = (
        latest["close"] >= sma10
        and latest["low"] <= sma10
        and latest["close"] >= sma20
        and latest["low"] <= sma20
    )
    sell_sma = (
        latest["close"] <= sma10
        and latest["high"] >= sma10
        and latest["close"] <= sma20
        and latest["high"] >= sma20
    )
    buy_pivot, pivot_low, buy_structure, buy_window_extreme = _pivot(
        newest_first, "BUY"
    )
    sell_pivot, pivot_high, sell_structure, sell_window_extreme = _pivot(
        newest_first, "SELL"
    )
    technical_buy = buy_sma and buy_structure
    technical_sell = sell_sma and sell_structure
    confirmed_buy = buy_filter and technical_buy
    confirmed_sell = sell_filter and technical_sell

    point_adjustment = Decimal(str(instrument["point_size"])) * Decimal("10")
    minimum_stop_distance = (
        Decimal(str(instrument["minimum_stop_distance_points"]))
        * Decimal(str(instrument["point_size"]))
    )

    def candidate(direction: str) -> dict[str, Any] | None:
        confirmed = confirmed_buy if direction == "BUY" else confirmed_sell
        if not confirmed:
            return None
        entry = latest["close"]
        if direction == "BUY":
            raw_stop = recent_low - stop_atr_distance - point_adjustment
            displayed_stop = min(raw_stop, entry - minimum_stop_distance)
            risk_distance = entry - displayed_stop
            target = entry + (Decimal("3") * risk_distance)
        else:
            raw_stop = recent_high + stop_atr_distance + point_adjustment
            displayed_stop = max(raw_stop, entry + minimum_stop_distance)
            risk_distance = displayed_stop - entry
            target = entry - (Decimal("3") * risk_distance)
        if risk_distance <= ZERO:
            return None
        contract = _contract(instrument, risk_distance)
        return {
            "direction": direction,
            "entry_reference": _number(entry),
            "raw_strategy_stop": _number(raw_stop),
            "provider_adjusted_stop": _number(displayed_stop),
            "risk_distance": _number(risk_distance),
            "target_3r": _number(target),
            **contract,
        }

    buy_candidate = candidate("BUY")
    sell_candidate = candidate("SELL")
    if confirmed_buy and confirmed_sell:
        dashboard_state = "CONFIRMED_BOTH"
    elif confirmed_buy:
        dashboard_state = "CONFIRMED_BUY"
    elif confirmed_sell:
        dashboard_state = "CONFIRMED_SELL"
    elif buy_filter and sell_filter:
        dashboard_state = "FILTERED_BOTH"
    elif buy_filter:
        dashboard_state = "FILTERED_BUY"
    elif sell_filter:
        dashboard_state = "FILTERED_SELL"
    else:
        dashboard_state = "WATCHING"

    evidence = [
        "Only provider-complete candles closing before the evaluation boundary were used.",
        f"{timeframe} and D1 calculations were evaluated independently of any other signal timeframe.",
        "The Daily Filter is non-consuming; reverse-filter and risk-multiplier logic are absent.",
    ]
    return {
        "case_id": case_id,
        "strategy_id": STRATEGY_ID,
        "instrument_id": instrument["instrument_id"],
        "timeframe": timeframe,
        "evaluation_time": _iso(evaluation_time),
        "data_status": "READY",
        "issues": [],
        "bars": {
            "signal_completed_count": len(completed_signal),
            "daily_completed_count": len(completed_daily),
            "excluded_incomplete_count": excluded,
            "signal_bar_close_time": _iso(latest["close_time"]),
            "daily_endpoint_close_time": _iso(completed_daily[-1]["close_time"]),
        },
        "classification": {
            "buy_filter_matched": buy_filter,
            "sell_filter_matched": sell_filter,
            "buy_sma_rejection": buy_sma,
            "sell_sma_rejection": sell_sma,
            "buy_structural_pivot": buy_structure,
            "sell_structural_pivot": sell_structure,
            "technical_buy_signal": technical_buy,
            "technical_sell_signal": technical_sell,
            "confirmed_buy": confirmed_buy,
            "confirmed_sell": confirmed_sell,
            "dashboard_state": dashboard_state,
        },
        "indicators": {
            "sma10": _number(sma10),
            "sma20": _number(sma20),
            "atr_d1_wilder_5": _number(atr),
            "activation_buffer": _number(activation_buffer),
            "stop_atr_distance": _number(stop_atr_distance),
            "point_adjustment": _number(point_adjustment),
            "daily_raw_low": _number(daily_raw_low),
            "daily_raw_high": _number(daily_raw_high),
            "daily_buy_level": _number(daily_buy_level),
            "daily_sell_level": _number(daily_sell_level),
            "recent_low_21": _number(recent_low),
            "recent_high_21": _number(recent_high),
        },
        "pivots": {
            "buy": {
                "open_time": _iso(buy_pivot["open_time"]),
                "pivot_value": _number(pivot_low),
                "structural_window_extreme": _number(buy_window_extreme),
                "passed": buy_structure,
            },
            "sell": {
                "open_time": _iso(sell_pivot["open_time"]),
                "pivot_value": _number(pivot_high),
                "structural_window_extreme": _number(sell_window_extreme),
                "passed": sell_structure,
            },
        },
        "candidates": {"buy": buy_candidate, "sell": sell_candidate},
        "evidence": evidence,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_directory", type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--timeframe", choices=("H1", "H4"), required=True)
    parser.add_argument("--evaluation-time", required=True)
    args = parser.parse_args()
    result = evaluate_case(
        args.case_directory,
        {
            "id": args.case_id,
            "timeframe": args.timeframe,
            "evaluation_time": args.evaluation_time,
        },
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
