"""Generate deterministic synthetic golden fixtures and their ledgers.

Run from the repository root:

    python golden/tools/generate_golden_cases.py

The generated expected results are frozen artifacts.  Tests never regenerate
them; they compare them with the independent reference calculator.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "golden"
CASES = GOLDEN / "cases"
sys.path.insert(0, str(ROOT))

from golden.reference.calculator import evaluate_case
PROVIDER = "SYNTHETIC_UTC_V1"
INSTRUMENT = "SYNTH_XAUUSD"
STRATEGY_ID = "SPECT8_MICRO_DAILY_V1_0"
SPECIFICATION_ID = "SPECT8_MICRO_DAILY_V1_0_3"
CSV_FIELDS = [
    "canonical_instrument_id",
    "timeframe",
    "open_time",
    "close_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "provider",
    "is_complete",
]


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def value(number: Decimal | float | int) -> str:
    return format(Decimal(str(number)).quantize(Decimal("0.0000000001")), "f")


def candle(
    timeframe: str,
    open_time: datetime,
    open_price: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    *,
    complete: bool = True,
    volume: int = 1000,
) -> dict[str, str]:
    step = {"H1": timedelta(hours=1), "H4": timedelta(hours=4), "D1": timedelta(days=1)}[
        timeframe
    ]
    return {
        "canonical_instrument_id": INSTRUMENT,
        "timeframe": timeframe,
        "open_time": iso(open_time),
        "close_time": iso(open_time + step),
        "open": value(open_price),
        "high": value(high),
        "low": value(low),
        "close": value(close),
        "volume": str(volume),
        "provider": PROVIDER,
        "is_complete": str(complete).lower(),
    }


def standard_daily(base: Decimal) -> list[dict[str, str]]:
    start = datetime(2026, 1, 20, tzinfo=timezone.utc)
    return [
        candle(
            "D1",
            start + timedelta(days=index),
            base,
            base + Decimal("8"),
            base - Decimal("8"),
            base,
            volume=10000 + index,
        )
        for index in range(10)
    ]


def equal_close_filter_boundary(
    timeframe: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return the frozen counterexample for completed D1 timestamp equality."""

    signal_close = datetime(2026, 1, 11, tzinfo=timezone.utc)
    signal_step = timedelta(hours=1 if timeframe == "H1" else 4)
    signal_start = signal_close - (signal_step * 35)
    signal = [
        candle(
            timeframe,
            signal_start + (signal_step * index),
            Decimal("100"),
            Decimal("100"),
            Decimal("51"),
            Decimal("100"),
            volume=12000 + index,
        )
        for index in range(35)
    ]

    daily_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    daily = [
        candle(
            "D1",
            daily_start + timedelta(days=index),
            Decimal("100"),
            Decimal("100") if index == 9 else Decimal("101"),
            Decimal("50") if index == 9 else Decimal("99"),
            Decimal("90") if index == 9 else Decimal("100"),
            volume=13000 + index,
        )
        for index in range(10)
    ]
    return signal, daily


def new_york_close_filter_boundary(
    timeframe: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return a summer 17:00 New York equal-close Filter authority case."""

    signal_close = datetime(2026, 7, 17, 21, tzinfo=timezone.utc)
    signal_step = timedelta(hours=1 if timeframe == "H1" else 4)
    signal_start = signal_close - (signal_step * 35)
    signal = [
        candle(
            timeframe,
            signal_start + (signal_step * index),
            Decimal("100"),
            Decimal("100"),
            Decimal("51"),
            Decimal("100"),
            volume=14000 + index,
        )
        for index in range(35)
    ]
    closes = (
        datetime(2026, 7, 6, 21, tzinfo=timezone.utc),
        datetime(2026, 7, 7, 21, tzinfo=timezone.utc),
        datetime(2026, 7, 8, 21, tzinfo=timezone.utc),
        datetime(2026, 7, 9, 21, tzinfo=timezone.utc),
        datetime(2026, 7, 10, 21, tzinfo=timezone.utc),
        datetime(2026, 7, 13, 21, tzinfo=timezone.utc),
        datetime(2026, 7, 14, 21, tzinfo=timezone.utc),
        datetime(2026, 7, 15, 21, tzinfo=timezone.utc),
        datetime(2026, 7, 16, 21, tzinfo=timezone.utc),
        datetime(2026, 7, 17, 21, tzinfo=timezone.utc),
    )
    daily = [
        candle(
            "D1",
            close_time - timedelta(days=1),
            Decimal("100"),
            Decimal("100") if index == 9 else Decimal("101"),
            Decimal("50") if index == 9 else Decimal("99"),
            Decimal("90") if index == 9 else Decimal("100"),
            volume=15000 + index,
        )
        for index, close_time in enumerate(closes)
    ]
    return signal, daily


def equality_daily(base: Decimal, direction: str) -> list[dict[str, str]]:
    start = datetime(2026, 1, 20, tzinfo=timezone.utc)
    if direction == "BUY":
        low = base - Decimal("0.2")
        open_close = base + Decimal("1")
        high = base + Decimal("3.8")
    else:
        low = base - Decimal("3.8")
        open_close = base - Decimal("1")
        high = base + Decimal("0.2")
    return [
        candle(
            "D1",
            start + timedelta(days=index),
            open_close,
            high,
            low,
            open_close,
            volume=11000 + index,
        )
        for index in range(10)
    ]


def flat_signal(
    timeframe: str,
    base: Decimal,
    *,
    bars: int = 35,
) -> list[dict[str, str]]:
    start = datetime(2026, 2, 2, tzinfo=timezone.utc)
    step = timedelta(hours=1 if timeframe == "H1" else 4)
    return [
        candle(
            timeframe,
            start + (step * index),
            base,
            base + Decimal("3"),
            base - Decimal("3"),
            base,
            volume=2000 + index,
        )
        for index in range(bars)
    ]


def set_ohlc(
    bar: dict[str, str],
    *,
    open_price: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
) -> None:
    bar.update(
        {
            "open": value(open_price),
            "high": value(high),
            "low": value(low),
            "close": value(close),
        }
    )


def filtered_only(timeframe: str, direction: str, base: Decimal) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    signal = flat_signal(timeframe, base)
    if direction == "BUY":
        prior = signal[-2]
        set_ohlc(
            prior,
            open_price=base,
            high=base + Decimal("2"),
            low=base - Decimal("7.5"),
            close=base,
        )
        set_ohlc(
            signal[-1],
            open_price=base + Decimal("4"),
            high=base + Decimal("5"),
            low=base + Decimal("3"),
            close=base + Decimal("4"),
        )
    else:
        prior = signal[-2]
        set_ohlc(
            prior,
            open_price=base,
            high=base + Decimal("7.5"),
            low=base - Decimal("2"),
            close=base,
        )
        set_ohlc(
            signal[-1],
            open_price=base - Decimal("4"),
            high=base - Decimal("3"),
            low=base - Decimal("5"),
            close=base - Decimal("4"),
        )
    return signal, standard_daily(base)


def confirmed(
    timeframe: str,
    direction: str,
    base: Decimal,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    signal = flat_signal(timeframe, base)
    if direction == "BUY":
        set_ohlc(
            signal[-1],
            open_price=base + Decimal("1"),
            high=base + Decimal("2"),
            low=base - Decimal("7.5"),
            close=base + Decimal("1"),
        )
    else:
        set_ohlc(
            signal[-1],
            open_price=base - Decimal("1"),
            high=base + Decimal("7.5"),
            low=base - Decimal("2"),
            close=base - Decimal("1"),
        )
    return signal, standard_daily(base)


def equality_case(
    timeframe: str,
    direction: str,
    base: Decimal,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    signal = flat_signal(timeframe, base)
    for bar in signal:
        if direction == "BUY":
            set_ohlc(
                bar,
                open_price=base,
                high=base + Decimal("1"),
                low=base,
                close=base,
            )
        else:
            set_ohlc(
                bar,
                open_price=base,
                high=base,
                low=base - Decimal("1"),
                close=base,
            )
    return signal, equality_daily(base, direction)


def simultaneous_confirmed(
    timeframe: str,
    base: Decimal,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    signal = flat_signal(timeframe, base)
    for bar in signal:
        set_ohlc(
            bar,
            open_price=base,
            high=base + Decimal("1"),
            low=base - Decimal("1"),
            close=base,
        )
    start = datetime(2026, 1, 20, tzinfo=timezone.utc)
    daily = [
        candle(
            "D1",
            start + timedelta(days=index),
            base,
            base + Decimal("1"),
            base - Decimal("1"),
            base,
            volume=12000 + index,
        )
        for index in range(10)
    ]
    return signal, daily


def technical_without_filter(
    timeframe: str,
    direction: str,
    base: Decimal,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    signal = flat_signal(timeframe, base)
    if direction == "BUY":
        set_ohlc(
            signal[-1],
            open_price=base + Decimal("1"),
            high=base + Decimal("2"),
            low=base - Decimal("1"),
            close=base + Decimal("1"),
        )
    else:
        set_ohlc(
            signal[-1],
            open_price=base - Decimal("1"),
            high=base + Decimal("1"),
            low=base - Decimal("2"),
            close=base - Decimal("1"),
        )
    return signal, standard_daily(base)


def sma_failure(
    timeframe: str,
    direction: str,
    failed_period: int,
    base: Decimal,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    signal = flat_signal(timeframe, base)
    rising = (direction == "BUY" and failed_period == 20) or (
        direction == "SELL" and failed_period == 10
    )
    for relative, bar in enumerate(signal[-20:-1]):
        fraction = Decimal(relative) / Decimal("18")
        close = (
            base - Decimal("5") + (fraction * Decimal("10"))
            if rising
            else base + Decimal("5") - (fraction * Decimal("10"))
        )
        set_ohlc(
            bar,
            open_price=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
        )

    if direction == "BUY":
        latest_close = base + Decimal("12")
        signal[-1]["close"] = value(latest_close)
        signal[-1]["open"] = value(latest_close)
        closes = [Decimal(bar["close"]) for bar in signal]
        sma10 = sum(closes[-10:]) / Decimal("10")
        sma20 = sum(closes[-20:]) / Decimal("20")
        midpoint = (sma10 + sma20) / Decimal("2")
        set_ohlc(
            signal[-1],
            open_price=latest_close,
            high=latest_close + Decimal("1"),
            low=midpoint,
            close=latest_close,
        )
        signal[-2]["low"] = value(base - Decimal("7.5"))
    else:
        latest_close = base - Decimal("12")
        signal[-1]["close"] = value(latest_close)
        signal[-1]["open"] = value(latest_close)
        closes = [Decimal(bar["close"]) for bar in signal]
        sma10 = sum(closes[-10:]) / Decimal("10")
        sma20 = sum(closes[-20:]) / Decimal("20")
        midpoint = (sma10 + sma20) / Decimal("2")
        set_ohlc(
            signal[-1],
            open_price=latest_close,
            high=midpoint,
            low=latest_close - Decimal("1"),
            close=latest_close,
        )
        signal[-2]["high"] = value(base + Decimal("20"))
    return signal, standard_daily(base)


def structural_failure(
    timeframe: str,
    direction: str,
    base: Decimal,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    signal, daily = technical_without_filter(timeframe, direction, base)
    if direction == "BUY":
        signal[-5]["low"] = value(base - Decimal("7.5"))
        signal[-16]["low"] = value(base - Decimal("8"))
    else:
        signal[-5]["high"] = value(base + Decimal("7.5"))
        signal[-16]["high"] = value(base + Decimal("8"))
    return signal, daily


def instrument(
    *,
    metadata_available: bool = True,
    minimum: Decimal = Decimal("0.01"),
    min_stop_points: Decimal = Decimal("0"),
    session_timezone: str = "UTC",
    candle_boundary_convention: str = (
        "D1 closes at 00:00 UTC; H1/H4 aligned to UTC midnight"
    ),
) -> dict[str, Any]:
    return {
        "strategy_id": STRATEGY_ID,
        "instrument_id": INSTRUMENT,
        "display_name": "Synthetic XAU/USD",
        "provider": PROVIDER,
        "session_timezone": session_timezone,
        "candle_boundary_convention": candle_boundary_convention,
        "price_precision": 2,
        "point_size": 0.01,
        "tick_size": 0.01 if metadata_available else None,
        "tick_value_usd": 1.0 if metadata_available else None,
        "conversion_rate_to_usd": 1.0 if metadata_available else None,
        "contract_min": float(minimum) if metadata_available else None,
        "contract_max": 100.0 if metadata_available else None,
        "contract_step": 0.01 if metadata_available else None,
        "minimum_stop_distance_points": float(min_stop_points),
    }


def latest_close(rows: list[dict[str, str]], timeframe: str) -> datetime:
    matching = [row for row in rows if row["timeframe"] == timeframe and row["is_complete"] == "true"]
    return datetime.fromisoformat(matching[-1]["close_time"].replace("Z", "+00:00"))


def append_developing(rows: list[dict[str, str]], timeframe: str, base: Decimal) -> None:
    last_close = datetime.fromisoformat(rows[-1]["close_time"].replace("Z", "+00:00"))
    rows.append(
        candle(
            timeframe,
            last_close,
            base,
            base + Decimal("50"),
            base - Decimal("50"),
            base - Decimal("40"),
            complete=False,
            volume=99999,
        )
    )


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def ledger(case: dict[str, Any], result: dict[str, Any]) -> str:
    classification = result["classification"]
    lines = [
        f"# Calculation ledger: {case['id']}",
        "",
        f"- Strategy: `{STRATEGY_ID}`",
        f"- Description: {case['description']}",
        f"- Evaluation boundary: `{case['evaluation_time']}`",
        f"- Signal timeframe: `{case['timeframe']}`",
        f"- Data status: `{result['data_status']}`",
        f"- Coverage: {', '.join(f'`{tag}`' for tag in case['coverage'])}",
        "",
        "## Completed-bar gate",
        "",
        f"- Completed signal bars used: {result['bars']['signal_completed_count']}",
        f"- Completed D1 bars used: {result['bars']['daily_completed_count']}",
        f"- Excluded incomplete/developing signal bars: {result['bars']['excluded_incomplete_count']}",
        f"- Signal bar close: `{result['bars']['signal_bar_close_time']}`",
        f"- D1 endpoint close: `{result['bars']['daily_endpoint_close_time']}`",
    ]
    if result["data_status"] == "UNAVAILABLE":
        lines.extend(
            [
                "",
                "## Quarantine decision",
                "",
                f"- Issues: {', '.join(f'`{issue}`' for issue in result['issues'])}",
                "- Strategy formulas were not evaluated and no candidate was emitted.",
                "",
            ]
        )
        return "\n".join(lines)

    indicators = result["indicators"]
    pivots = result["pivots"]
    lines.extend(
        [
            "",
            "## Indicators",
            "",
            f"- SMA10 / SMA20: `{indicators['sma10']}` / `{indicators['sma20']}`",
            f"- Wilder D1 ATR(5): `{indicators['atr_d1_wilder_5']}`",
            f"- Activation buffer (ATR × 0.05): `{indicators['activation_buffer']}`",
            f"- Daily raw low / high: `{indicators['daily_raw_low']}` / `{indicators['daily_raw_high']}`",
            f"- Daily BUY / SELL level: `{indicators['daily_buy_level']}` / `{indicators['daily_sell_level']}`",
            f"- Recent 21-bar low / high: `{indicators['recent_low_21']}` / `{indicators['recent_high_21']}`",
            "",
            "## Filter and signal decisions",
            "",
            f"- BUY / SELL Filter: `{classification['buy_filter_matched']}` / `{classification['sell_filter_matched']}`",
            f"- BUY / SELL SMA rejection: `{classification['buy_sma_rejection']}` / `{classification['sell_sma_rejection']}`",
            f"- BUY / SELL structural pivot: `{classification['buy_structural_pivot']}` / `{classification['sell_structural_pivot']}`",
            f"- BUY pivot / window extreme: `{pivots['buy']['pivot_value']}` / `{pivots['buy']['structural_window_extreme']}`",
            f"- SELL pivot / window extreme: `{pivots['sell']['pivot_value']}` / `{pivots['sell']['structural_window_extreme']}`",
            f"- Technical BUY / SELL: `{classification['technical_buy_signal']}` / `{classification['technical_sell_signal']}`",
            f"- Confirmed BUY / SELL: `{classification['confirmed_buy']}` / `{classification['confirmed_sell']}`",
            f"- Dashboard state: `{classification['dashboard_state']}`",
            "",
            "## Candidate levels",
            "",
        ]
    )
    if "equal_close_d1_boundary" in case["coverage"]:
        signal_close = datetime.fromisoformat(
            result["bars"]["signal_bar_close_time"].replace("Z", "+00:00")
        )
        eligible_daily = [
            row
            for row in case["_daily"]
            if row["is_complete"] == "true"
            and datetime.fromisoformat(
                row["close_time"].replace("Z", "+00:00")
            )
            <= signal_close
        ]
        lines.extend(
            [
                "",
                "## Completed-as-of-close boundary evidence",
                "",
                "- Eligible D1 closes: "
                + ", ".join(
                    f"`{row['close_time']}`" for row in eligible_daily
                ),
                "- Selected two D1 closes: "
                + ", ".join(
                    f"`{row['close_time']}`" for row in eligible_daily[-2:]
                ),
                "- The latest selected D1 close equals the signal close and is complete.",
            ]
        )
    candidates = result["candidates"]
    if candidates["buy"] is None and candidates["sell"] is None:
        lines.append("- No confirmed candidate; entry, stop, target, and size are not calculated.")
    for direction in ("buy", "sell"):
        candidate = candidates[direction]
        if candidate is not None:
            lines.extend(
                [
                    f"- {direction.upper()} entry: `{candidate['entry_reference']}`",
                    f"- {direction.upper()} raw / displayed stop: `{candidate['raw_strategy_stop']}` / `{candidate['provider_adjusted_stop']}`",
                    f"- {direction.upper()} risk distance / 3R target: `{candidate['risk_distance']}` / `{candidate['target_3r']}`",
                    f"- {direction.upper()} target risk: `${candidate['target_risk_usd']:.2f}`",
                    f"- {direction.upper()} raw / displayed size: `{candidate['raw_size']}` / `{candidate['display_size']}`",
                    f"- {direction.upper()} contract status: `{candidate['contract_status']}`",
                ]
            )
    lines.extend(
        [
            "",
            "The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.",
            "",
        ]
    )
    return "\n".join(lines)


def build_cases() -> list[dict[str, Any]]:
    specifications: list[dict[str, Any]] = []

    def add(
        case_id: str,
        timeframe: str,
        description: str,
        coverage: list[str],
        signal: list[dict[str, str]],
        daily: list[dict[str, str]],
        metadata: dict[str, Any] | None = None,
        independence_group: str | None = None,
    ) -> None:
        evaluation = latest_close(signal, timeframe) + timedelta(seconds=1)
        entry: dict[str, Any] = {
            "id": case_id,
            "path": f"cases/{case_id}",
            "timeframe": timeframe,
            "evaluation_time": iso(evaluation),
            "description": description,
            "coverage": coverage,
            "_signal": signal,
            "_daily": daily,
            "_instrument": metadata or instrument(),
        }
        if independence_group is not None:
            entry["independence_group"] = independence_group
        specifications.append(entry)

    for timeframe in ("H1", "H4"):
        for direction in ("BUY", "SELL"):
            for variant in range(1, 6):
                base = Decimal("100") + Decimal(variant * 25)
                signal, daily = filtered_only(timeframe, direction, base)
                add(
                    f"filtered_{direction.lower()}_{timeframe.lower()}_{variant:02d}",
                    timeframe,
                    f"{timeframe} {direction} Filter matched while the {direction} technical signal is false (variant {variant}).",
                    [f"filtered_{direction.lower()}_only", timeframe.lower()],
                    signal,
                    daily,
                )

    for timeframe in ("H1", "H4"):
        for direction in ("BUY", "SELL"):
            for variant in range(1, 6):
                base = Decimal("300") + Decimal(variant * 25)
                if variant == 4:
                    signal, daily = equality_case(timeframe, direction, base)
                    extra = ["equality_boundaries"]
                else:
                    signal, daily = confirmed(timeframe, direction, base)
                    extra = []

                metadata = instrument()
                if variant == 2:
                    metadata = instrument(metadata_available=False)
                    extra.append("missing_metadata")
                if variant == 3:
                    other_timeframe = "H4" if timeframe == "H1" else "H1"
                    other_direction = "SELL" if direction == "BUY" else "BUY"
                    other_signal, _ = confirmed(other_timeframe, other_direction, base)
                    signal = signal + other_signal
                    signal.sort(key=lambda row: (row["open_time"], row["timeframe"]))
                    extra.append("h1_h4_independence")
                if variant == 5 and direction == "BUY":
                    append_developing(signal, timeframe, base)
                    extra.append("developing_bar_exclusion")
                if variant == 5 and direction == "SELL":
                    metadata = instrument(minimum=Decimal("0.10"), min_stop_points=Decimal("2000"))
                    extra.extend(["below_provider_minimum", "provider_stop_adjustment"])

                if variant == 3:
                    group = (
                        "independence_h1_buy_h4_sell"
                        if (timeframe, direction) in {("H1", "BUY"), ("H4", "SELL")}
                        else "independence_h1_sell_h4_buy"
                    )
                else:
                    group = None
                add(
                    f"confirmed_{direction.lower()}_{timeframe.lower()}_{variant:02d}",
                    timeframe,
                    f"{timeframe} confirmed {direction} candidate (variant {variant}).",
                    [f"confirmed_{direction.lower()}", timeframe.lower(), "risk_usd_100", *extra],
                    signal,
                    daily,
                    metadata,
                    group,
                )

    signal, daily = simultaneous_confirmed("H1", Decimal("500"))
    add(
        "confirmed_both_h1_01",
        "H1",
        "H1 BUY and SELL are simultaneously confirmed and retained independently.",
        [
            "confirmed_buy",
            "confirmed_sell",
            "confirmed_both",
            "simultaneous_direction",
            "h1",
            "risk_usd_100",
        ],
        signal,
        daily,
    )

    for timeframe in ("H1", "H4"):
        signal, daily = equal_close_filter_boundary(timeframe)
        add(
            f"equal_close_filter_boundary_{timeframe.lower()}",
            timeframe,
            (
                f"{timeframe} completed signal includes the D1 candle closing "
                "at the identical UTC timestamp."
            ),
            [
                "equal_close_d1_boundary",
                "completed_as_of_close",
                timeframe.lower(),
            ],
            signal,
            daily,
        )

        signal, daily = new_york_close_filter_boundary(timeframe)
        add(
            f"new_york_close_filter_boundary_{timeframe.lower()}",
            timeframe,
            (
                f"{timeframe} includes the reconstructed 17:00 New York D1 "
                "closing at the identical summer UTC timestamp."
            ),
            [
                "new_york_close_d1",
                "equal_close_d1_boundary",
                "completed_as_of_close",
                timeframe.lower(),
            ],
            signal,
            daily,
            instrument(
                session_timezone="America/New_York",
                candle_boundary_convention=(
                    "D1 closes at 17:00 America/New_York; H1/H4 in UTC"
                ),
            ),
        )

    negative_cases = [
        ("technical_buy_without_filter_h1", "H1", "BUY"),
        ("technical_sell_without_filter_h1", "H1", "SELL"),
        ("technical_buy_without_filter_h4", "H4", "BUY"),
        ("technical_sell_without_filter_h4", "H4", "SELL"),
    ]
    for index, (case_id, timeframe, direction) in enumerate(negative_cases):
        signal, daily = technical_without_filter(
            timeframe, direction, Decimal("600") + Decimal(index * 25)
        )
        add(
            case_id,
            timeframe,
            f"{direction} technical signal exists without the corresponding Daily Filter.",
            ["technical_signal_without_filter", direction.lower(), timeframe.lower()],
            signal,
            daily,
        )

    for index, (direction, period) in enumerate(
        (("BUY", 10), ("SELL", 10), ("BUY", 20), ("SELL", 20))
    ):
        case_id = f"{direction.lower()}_sma{period}_failure_h1"
        signal, daily = sma_failure(
            "H1", direction, period, Decimal("725") + Decimal(index * 25)
        )
        add(
            case_id,
            "H1",
            f"{direction} rejection fails only its SMA{period} touch boundary.",
            [f"sma{period}_failure", direction.lower()],
            signal,
            daily,
        )

    for index, direction in enumerate(("BUY", "SELL")):
        signal, daily = structural_failure(
            "H1", direction, Decimal("850") + Decimal(index * 25)
        )
        add(
            f"{direction.lower()}_structural_pivot_failure_h1",
            "H1",
            f"{direction} SMA rejection passes but the older structural extreme invalidates the pivot.",
            ["structural_pivot_failure", direction.lower()],
            signal,
            daily,
        )

    data_base = Decimal("925")
    signal, daily = confirmed("H1", "BUY", data_base)
    del signal[10]
    add(
        "missing_signal_candle",
        "H1",
        "A missing H1 interval quarantines the signal stream.",
        ["missing_candle", "missing_signal_candle", "data_unavailable"],
        signal,
        daily,
    )

    signal, daily = confirmed("H1", "BUY", data_base + Decimal("25"))
    del daily[4]
    add(
        "missing_daily_candle",
        "H1",
        "A missing D1 interval quarantines the daily reference stream.",
        ["missing_candle", "missing_daily_candle", "data_unavailable"],
        signal,
        daily,
    )

    signal, daily = confirmed("H1", "BUY", data_base + Decimal("50"))
    signal.insert(10, deepcopy(signal[10]))
    add(
        "duplicate_signal_candle",
        "H1",
        "A duplicate H1 candle is rejected before strategy calculation.",
        ["duplicate_candle", "duplicate_signal_candle", "data_unavailable"],
        signal,
        daily,
    )

    signal, daily = confirmed("H1", "BUY", data_base + Decimal("75"))
    daily.insert(4, deepcopy(daily[4]))
    add(
        "duplicate_daily_candle",
        "H1",
        "A duplicate D1 candle is rejected before strategy calculation.",
        ["duplicate_candle", "duplicate_daily_candle", "data_unavailable"],
        signal,
        daily,
    )
    return specifications


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="Write only the named case directory; always refresh the manifest.",
    )
    args = parser.parse_args(argv)
    CASES.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    requested = set(args.case)
    known = {case["id"] for case in cases}
    unknown = requested - known
    if unknown:
        parser.error(f"unknown case(s): {', '.join(sorted(unknown))}")
    manifest_cases: list[dict[str, Any]] = []
    written = 0
    for generated in cases:
        case = {
            key: value
            for key, value in generated.items()
            if not key.startswith("_")
        }
        manifest_cases.append(case)
        if requested and case["id"] not in requested:
            continue
        directory = GOLDEN / case["path"]
        directory.mkdir(parents=True, exist_ok=True)
        write_csv(directory / "signal_bars.csv", generated["_signal"])
        write_csv(directory / "daily_bars.csv", generated["_daily"])
        (directory / "instrument.json").write_text(
            json.dumps(generated["_instrument"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        expected = evaluate_case(directory, case)
        (directory / "expected.json").write_text(
            json.dumps(expected, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (directory / "calculation_ledger.md").write_text(
            ledger(generated, expected),
            encoding="utf-8",
        )
        written += 1

    manifest = {
        "strategy_id": STRATEGY_ID,
        "authority": "../Spect8_Micro_Daily_v1_0_3_FROZEN.md",
        "dataset_version": "1.0.3",
        "description": (
            "Deterministic synthetic golden cases for the separate client Market "
            "Scanner. These are test fixtures, not production strategy code."
        ),
        "cases": manifest_cases,
    }
    (GOLDEN / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {written} golden case directories and indexed "
        f"{len(manifest_cases)} cases for {SPECIFICATION_ID}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
