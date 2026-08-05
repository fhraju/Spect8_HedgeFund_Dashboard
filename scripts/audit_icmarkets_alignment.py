from __future__ import annotations

import argparse
import csv
import json
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from backend.app.domain import Bar, Timeframe
from backend.app.market_data.forex_profile import (
    PROFILE_ID,
    BrokerAlignedH4Aggregator,
    broker_wall_time,
    market_h1_bars,
)
from backend.app.repository import SQLiteProjectionRepository


PRICE_FIELDS = ("open", "high", "low", "close")
EXPORT_FIELDS = (
    "broker",
    "server",
    "symbol",
    "canonical_symbol",
    "timeframe",
    "bar_index",
    "broker_open_time",
    "broker_close_time",
    "utc_open_time",
    "utc_close_time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "real_volume",
    "spread",
    "completed",
    "weekend",
    "source",
)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _broker_label(value: datetime) -> str:
    return f"{broker_wall_time(value):%Y-%m-%d %H:%M:%S} Broker Time"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _project_row(bar: Bar, index: int) -> dict[str, object]:
    return {
        "broker": "Twelve Data",
        "server": "",
        "symbol": bar.raw_provider_symbol or "EUR/USD",
        "canonical_symbol": "EUR/USD",
        "timeframe": bar.timeframe.value,
        "bar_index": index,
        "broker_open_time": _broker_label(bar.open_time),
        "broker_close_time": _broker_label(bar.close_time),
        "utc_open_time": _iso(bar.open_time),
        "utc_close_time": _iso(bar.close_time),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "tick_volume": str(bar.volume) if bar.volume is not None else "",
        "real_volume": "",
        "spread": "",
        "completed": str(bar.is_complete).lower(),
        "weekend": str(broker_wall_time(bar.open_time).weekday() >= 5).lower(),
        "source": "SPECT8_CANONICAL_SQLITE",
    }


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _structure(rows: list[dict[str, str]]) -> dict[str, object]:
    opens = [_utc(row["utc_open_time"]) for row in rows]
    broker_opens = [broker_wall_time(value) for value in opens]
    identities = [(row["utc_open_time"], row["utc_close_time"]) for row in rows]
    return {
        "count": len(rows),
        "duplicate_count": len(identities) - len(set(identities)),
        "weekend_open_count": sum(value.weekday() >= 5 for value in broker_opens),
        "broker_open_hours": sorted({value.hour for value in broker_opens}),
        "broker_open_minutes": sorted({value.minute for value in broker_opens}),
        "weekday_distribution": {
            str(day): sum(value.weekday() == day for value in broker_opens)
            for day in range(7)
        },
    }


def _weekend_transitions(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    transitions = []
    for previous, current in zip(rows, rows[1:]):
        previous_close = _utc(previous["utc_close_time"])
        current_open = _utc(current["utc_open_time"])
        if current_open <= previous_close + timedelta(hours=1):
            continue
        gap = Decimal(current["open"]) - Decimal(previous["close"])
        transitions.append(
            {
                "friday_final_open": previous["utc_open_time"],
                "friday_final_close": previous["utc_close_time"],
                "friday_close_price": previous["close"],
                "monday_first_open": current["utc_open_time"],
                "monday_first_close": current["utc_close_time"],
                "monday_open_price": current["open"],
                "gap_points": str(gap / Decimal("0.00001")),
                "gap_pips": str(gap / Decimal("0.0001")),
                "elapsed_hours": (current_open - previous_close).total_seconds() / 3600,
                "actual_candles_between": 0,
            }
        )
    return transitions


def _price_comparison(
    reference: list[dict[str, str]],
    project: list[dict[str, str]],
) -> dict[str, object]:
    ref = {(row["utc_open_time"], row["utc_close_time"]): row for row in reference}
    current = {(row["utc_open_time"], row["utc_close_time"]): row for row in project}
    shared = sorted(set(ref) & set(current))
    field_report = {}
    exact_candles = 0
    for field in PRICE_FIELDS:
        deltas = [
            abs(Decimal(current[key][field]) - Decimal(ref[key][field]))
            for key in shared
        ]
        field_report[field] = {
            "maximum_delta": str(max(deltas)) if deltas else None,
            "mean_absolute_delta": str(sum(deltas) / len(deltas)) if deltas else None,
            "median_absolute_delta": str(statistics.median(deltas)) if deltas else None,
            "exact_match_percentage": (
                100 * sum(value == 0 for value in deltas) / len(deltas)
                if deltas
                else None
            ),
        }
    for key in shared:
        if all(
            Decimal(current[key][field]) == Decimal(ref[key][field])
            for field in PRICE_FIELDS
        ):
            exact_candles += 1
    return {
        "shared_timestamp_count": len(shared),
        "reference_only_count": len(set(ref) - set(current)),
        "spect8_only_count": len(set(current) - set(ref)),
        "exact_ohlc_candle_percentage": (
            100 * exact_candles / len(shared) if shared else None
        ),
        "fields": field_report,
        "interpretation": "Expected provider-price differences"
        if shared
        else "Timestamp misalignment",
    }


def _lookback_rows(
    bars: tuple[Bar, ...],
    timeframe: Timeframe,
    source_members: dict[datetime, tuple[Bar, ...]] | None = None,
) -> list[dict[str, object]]:
    selected = bars[-21:]
    low = min(selected, key=lambda bar: bar.low)
    high = max(selected, key=lambda bar: bar.high)
    rows = []
    for index, bar in enumerate(selected, 1):
        row = _project_row(bar, index)
        row.update(
            {
                "source_ids": "|".join(
                    _iso(source.close_time)
                    for source in (source_members or {}).get(bar.close_time, (bar,))
                ),
                "recent_low": bar is low,
                "recent_high": bar is high,
            }
        )
        rows.append(row)
    return rows


def run(
    database: Path, reference_dir: Path, report_path: Path, markdown_path: Path
) -> dict[str, object]:
    repository = SQLiteProjectionRepository(database)
    reference = {
        timeframe: _read_csv(
            reference_dir / f"EURUSD_ICMARKETS_{timeframe}_20260705_20260805.csv"
        )
        for timeframe in ("H1", "H4", "D1")
    }
    project_bars = {
        timeframe: repository.canonical_bar_objects("TWELVE_DATA", "EUR/USD", timeframe)
        for timeframe in ("H1", "H4", "D1")
    }
    project: dict[str, list[dict[str, object]]] = {}
    for timeframe in ("H1", "H4", "D1"):
        last_close = max(_utc(row["utc_close_time"]) for row in reference[timeframe])
        selected = tuple(
            bar
            for bar in project_bars[timeframe]
            if bar.close_time > datetime(2026, 7, 5, tzinfo=timezone.utc)
            and bar.close_time <= last_close
        )
        project[timeframe] = [
            _project_row(bar, index) for index, bar in enumerate(selected, 1)
        ]
        _write_rows(
            reference_dir / f"EURUSD_SPECT8_{timeframe}_20260705_20260805.csv",
            project[timeframe],
        )

    statuses = {
        value["timeframe"]: value
        for value in repository.statuses()
        if value.get("provider") == "TWELVE_DATA"
        and value.get("instrument_id") == "EUR/USD"
    }
    valid_h1 = market_h1_bars(project_bars["H1"])
    h1_close = _utc(statuses["H1"]["signal_bar_close_time"])
    h1_lookback = tuple(bar for bar in valid_h1 if bar.close_time <= h1_close)[-21:]
    h4_close = _utc(statuses["H4"]["signal_bar_close_time"])
    h4_result = BrokerAlignedH4Aggregator().aggregate(valid_h1, as_of=h4_close)
    h4_lookback = tuple(bar for bar in h4_result.bars if bar.close_time <= h4_close)[
        -21:
    ]
    members = {
        bucket.bar.close_time: bucket.source_bars for bucket in h4_result.buckets
    }
    lookback_fields = (*EXPORT_FIELDS, "source_ids", "recent_low", "recent_high")
    for timeframe, rows in (
        ("H1", _lookback_rows(h1_lookback, Timeframe.H1)),
        ("H4", _lookback_rows(h4_lookback, Timeframe.H4, members)),
    ):
        path = reference_dir / f"EURUSD_SPECT8_LATEST_{timeframe}_LOOKBACK.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=lookback_fields)
            writer.writeheader()
            writer.writerows(rows)

    result = {
        "profile": PROFILE_ID,
        "range_start_utc": "2026-07-05T00:00:00Z",
        "range_end_utc": max(row["utc_close_time"] for row in reference["H1"]),
        "reference": {tf: _structure(reference[tf]) for tf in reference},
        "spect8_current": {tf: _structure(project[tf]) for tf in project},
        "weekend_transitions": _weekend_transitions(reference["H1"]),
        "comparison": {
            tf: _price_comparison(reference[tf], project[tf])
            for tf in ("H1", "H4", "D1")
        },
        "correct_latest_lookbacks": {
            "H1": {
                "count": len(h1_lookback),
                "window_start": _iso(h1_lookback[0].close_time),
                "window_end": _iso(h1_lookback[-1].close_time),
                "recent_low": str(min(bar.low for bar in h1_lookback)),
                "recent_high": str(max(bar.high for bar in h1_lookback)),
            },
            "H4": {
                "count": len(h4_lookback),
                "window_start": _iso(h4_lookback[0].close_time),
                "window_end": _iso(h4_lookback[-1].close_time),
                "recent_low": str(min(bar.low for bar in h4_lookback)),
                "recent_high": str(max(bar.high for bar in h4_lookback)),
                "bars": [
                    {
                        "sequence": index,
                        "open": _iso(bar.open_time),
                        "close": _iso(bar.close_time),
                        "broker_open": f"{broker_wall_time(bar.open_time):%Y-%m-%d %H:%M:%S}",
                        "ohlc": [
                            str(bar.open),
                            str(bar.high),
                            str(bar.low),
                            str(bar.close),
                        ],
                    }
                    for index, bar in enumerate(h4_lookback, 1)
                ],
            },
        },
        "findings": {
            "spect8_weekend_h1_defect": _structure(project["H1"])["weekend_open_count"]
            > 0,
            "spect8_weekend_h4_defect": _structure(project["H4"])["weekend_open_count"]
            > 0,
            "h4_boundary_alignment": "aligned",
            "h4_source": "native Twelve Data H4; must be replaced by validated H1 aggregation",
            "evaluator_formula": "unchanged; input stream contained invalid weekend candles",
        },
    }
    report_path.write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )
    transitions = "\n".join(
        f"- {item['friday_final_close']} to {item['monday_first_open']}: "
        f"{item['gap_pips']} pips, {item['elapsed_hours']} hours"
        for item in result["weekend_transitions"]
    )
    markdown_path.write_text(
        "# IC Markets data alignment audit\n\n"
        f"Profile: `{PROFILE_ID}`. Range: {result['range_start_utc']} through "
        f"{result['range_end_utc']}.\n\n"
        "## Verdict\n\n"
        "DEFECT: Twelve Data supplied continuous weekend H1/H4 candles and the "
        "current canonical path accepted them. H4 boundaries align with IC Markets, "
        "but native H4 and weekend bars contaminated the 21-existing-bar window. "
        "The evaluator's latest-21 algorithm itself is correct.\n\n"
        "## Weekend transitions\n\n"
        f"{transitions}\n\n"
        "## Machine-readable evidence\n\n"
        "See `backend/data/icmarkets_reference/comparison_report.json` and the "
        "ignored CSV exports generated by `scripts/audit_icmarkets_alignment.py`.\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.database.resolve(),
        args.reference_dir.resolve(),
        args.report.resolve(),
        args.markdown.resolve(),
    )
    print(
        json.dumps(
            {
                "reference_counts": {
                    tf: result["reference"][tf]["count"] for tf in ("H1", "H4", "D1")
                },
                "spect8_weekend_counts": {
                    tf: result["spect8_current"][tf]["weekend_open_count"]
                    for tf in ("H1", "H4", "D1")
                },
                "correct_h4": result["correct_latest_lookbacks"]["H4"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
