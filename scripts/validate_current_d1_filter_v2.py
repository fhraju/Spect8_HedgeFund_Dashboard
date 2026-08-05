from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.engine.current_daily_filter import build_daily_filter_snapshot
from backend.app.market_data.daily_aggregator import NewYorkDailyAggregator
from backend.app.market_data.forex_profile import BrokerAlignedH4Aggregator
from backend.app.repository import SQLiteProjectionRepository


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def reference_times(path: Path) -> set[tuple[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            (row["utc_open_time"], row["utc_close_time"])
            for row in csv.DictReader(handle)
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repository = SQLiteProjectionRepository(args.database.resolve())
    bars = {
        timeframe: repository.canonical_bar_objects("TWELVE_DATA", "EUR/USD", timeframe)
        for timeframe in ("H1", "H4", "D1")
    }
    reference = {
        timeframe: reference_times(
            args.reference_dir / f"EURUSD_ICMARKETS_{timeframe}_20260705_20260805.csv"
        )
        for timeframe in ("H1", "H4", "D1")
    }
    end = max(utc(close) for _, close in reference["H1"])
    start = datetime(2026, 7, 5, tzinfo=timezone.utc)
    structural = {}
    for timeframe in ("H1", "H4", "D1"):
        current = {
            (iso(bar.open_time), iso(bar.close_time))
            for bar in bars[timeframe]
            if start < bar.close_time <= end
        }
        structural[timeframe] = {
            "reference_count": len(reference[timeframe]),
            "spect8_count": len(current),
            "shared_count": len(reference[timeframe] & current),
            "reference_only": len(reference[timeframe] - current),
            "spect8_only": len(current - reference[timeframe]),
        }
    h1 = tuple(bar for bar in bars["H1"] if bar.close_time <= end)
    daily = (
        NewYorkDailyAggregator().aggregate(h1, as_of=end + timedelta(seconds=1)).bars
    )
    h4 = (
        BrokerAlignedH4Aggregator().aggregate(h1, as_of=end + timedelta(seconds=1)).bars
    )
    sample_closes = (
        datetime(2026, 7, 6, 9, tzinfo=timezone.utc),
        datetime(2026, 7, 15, 9, tzinfo=timezone.utc),
        end,
    )
    samples = []
    for close in sample_closes:
        available = tuple(bar for bar in h1 if bar.close_time <= close)
        snapshot = build_daily_filter_snapshot(
            provider="TWELVE_DATA",
            instrument="EUR/USD",
            as_of_h1_close=close,
            h1_bars=available,
            completed_d1_bars=tuple(bar for bar in daily if bar.close_time <= close)[
                -10:
            ],
        )
        partial = snapshot.current_partial_d1
        exact = tuple(
            bar
            for bar in available
            if partial.session_open_utc <= bar.open_time and bar.close_time <= close
        )
        samples.append(
            {
                "as_of_h1_close_utc": iso(close),
                "snapshot_id": snapshot.snapshot_id,
                "h1_count": partial.h1_count,
                "partial_ohlc": [
                    str(partial.open),
                    str(partial.high),
                    str(partial.low),
                    str(partial.close),
                ],
                "manual_ohlc": [
                    str(exact[0].open),
                    str(max(bar.high for bar in exact)),
                    str(min(bar.low for bar in exact)),
                    str(exact[-1].close),
                ],
                "previous_d1_close_utc": iso(snapshot.previous_d1_close_utc),
                "atr_5": str(snapshot.atr_value),
                "buffer": str(snapshot.buffer_value),
                "buy_comparison": (
                    f"{snapshot.buy_left_value} <= {snapshot.buy_right_value} "
                    f"= {snapshot.buy_matched}"
                ),
                "sell_comparison": (
                    f"{snapshot.sell_left_value} >= {snapshot.sell_right_value} "
                    f"= {snapshot.sell_matched}"
                ),
                "classification": snapshot.final_classification,
                "h4_closes_same_instant": any(bar.close_time == close for bar in h4),
            }
        )
    result = {
        "profile": "IC_MARKETS_NY_CLOSE_FOREX_V1",
        "strategy_version": "MICRO_DAILY_FILTER_CURRENT_D1_V2",
        "range": [iso(start), iso(end)],
        "structural_alignment": structural,
        "weekend_h1_count": sum(
            1
            for bar in h1
            if start < bar.close_time <= end
            and bar.open_time.astimezone(timezone.utc).weekday() == 5
        ),
        "samples": samples,
        "pass": all(
            item["reference_only"] == 0 and item["spect8_only"] == 0
            for item in structural.values()
        )
        and all(item["partial_ohlc"] == item["manual_ohlc"] for item in samples),
    }
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
