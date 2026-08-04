from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.domain import Bar, Timeframe, primitive
from backend.app.engine.models import StrategyRequest
from backend.app.engine.strategy import STRATEGY_ID, Spect8StrategyEvaluator
from backend.app.market_data.twelve_data_provider import TwelveDataProvider
from backend.app.repository import SQLiteProjectionRepository
from golden.reference.calculator import evaluate_case as reference_evaluate_case

NEW_YORK = ZoneInfo("America/New_York")
CSV_FIELDS = (
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
)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _bar(value: dict[str, Any]) -> Bar:
    return Bar(
        instrument_id=str(value["instrument_id"]),
        timeframe=Timeframe(value["timeframe"]),
        open_time=_dt(value["open_time"]),
        close_time=_dt(value["close_time"]),
        open=Decimal(str(value["open"])),
        high=Decimal(str(value["high"])),
        low=Decimal(str(value["low"])),
        close=Decimal(str(value["close"])),
        provider=str(value["provider"]),
        is_complete=bool(value["is_complete"]),
        volume=(
            Decimal(str(value["volume"]))
            if value.get("volume") is not None
            else None
        ),
        session_timezone=str(value.get("session_timezone", "UTC")),
        raw_provider_symbol=value.get("raw_provider_symbol"),
        raw_open_time=value.get("raw_open_time"),
        raw_close_time=value.get("raw_close_time"),
        raw_open=value.get("raw_open"),
        raw_high=value.get("raw_high"),
        raw_low=value.get("raw_low"),
        raw_close=value.get("raw_close"),
        synthetic=bool(value.get("synthetic", False)),
    )


def _manual(
    signal_bars: tuple[Bar, ...],
    daily_bars: tuple[Bar, ...],
) -> dict[str, Any]:
    true_ranges = []
    for previous, current in zip(daily_bars, daily_bars[1:]):
        range_high_low = current.high - current.low
        range_high_close = abs(current.high - previous.close)
        range_low_close = abs(current.low - previous.close)
        true_range = max(range_high_low, range_high_close, range_low_close)
        true_ranges.append(
            {
                "previous_close": str(previous.close),
                "current_close_utc": _iso(current.close_time),
                "high_low": str(range_high_low),
                "high_previous_close": str(range_high_close),
                "low_previous_close": str(range_low_close),
                "true_range": str(true_range),
            }
        )
    values = [Decimal(item["true_range"]) for item in true_ranges]
    seed = sum(values[:5], Decimal("0")) / Decimal("5")
    atr = seed
    recurrences = []
    for true_range in values[5:]:
        next_atr = (atr * Decimal("4") + true_range) / Decimal("5")
        recurrences.append(
            {
                "previous_atr": str(atr),
                "true_range": str(true_range),
                "next_atr": str(next_atr),
            }
        )
        atr = next_atr
    latest_two = daily_bars[-2:]
    daily_low = min(bar.low for bar in latest_two)
    daily_high = max(bar.high for bar in latest_two)
    buffer = atr * Decimal("0.05")
    buy_level = daily_low + buffer
    sell_level = daily_high - buffer
    recent = signal_bars[-21:]
    recent_low = min(bar.low for bar in recent)
    recent_high = max(bar.high for bar in recent)
    return {
        "true_ranges": true_ranges,
        "wilder_seed": str(seed),
        "wilder_recurrence": recurrences,
        "atr_endpoint": str(atr),
        "latest_two_d1_closes": [_iso(bar.close_time) for bar in latest_two],
        "daily_low": str(daily_low),
        "daily_high": str(daily_high),
        "buffer_5_percent": str(buffer),
        "buy_level": str(buy_level),
        "sell_level": str(sell_level),
        "recent_low_21": str(recent_low),
        "recent_high_21": str(recent_high),
        "buy_matched": recent_low <= buy_level,
        "sell_matched": recent_high >= sell_level,
    }


def _same_number(left: Any, right: Any) -> bool:
    return abs(Decimal(str(left)) - Decimal(str(right))) <= Decimal("1e-12")


def _manual_matches_evaluation(
    manual: dict[str, Any],
    evaluation: dict[str, Any],
) -> bool:
    indicators = evaluation["indicators"]
    classification = evaluation["classification"]
    pairs = (
        (manual["atr_endpoint"], indicators["atr_d1_wilder_5"]),
        (manual["buffer_5_percent"], indicators["activation_buffer"]),
        (manual["daily_low"], indicators["daily_raw_low"]),
        (manual["daily_high"], indicators["daily_raw_high"]),
        (manual["buy_level"], indicators["daily_buy_level"]),
        (manual["sell_level"], indicators["daily_sell_level"]),
        (manual["recent_low_21"], indicators["recent_low_21"]),
        (manual["recent_high_21"], indicators["recent_high_21"]),
    )
    return (
        all(_same_number(left, right) for left, right in pairs)
        and manual["buy_matched"] == classification["buy_filter_matched"]
        and manual["sell_matched"] == classification["sell_filter_matched"]
    )


def _reference_result(
    root: Path,
    replay: dict[str, Any],
    input_value: dict[str, Any],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for name, rows in (
            ("signal_bars.csv", input_value["signal_bars"]),
            ("daily_bars.csv", input_value["daily_bars"]),
        ):
            with (directory / name).open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {
                            "canonical_instrument_id": row["instrument_id"],
                            "timeframe": row["timeframe"],
                            "open_time": row["open_time"],
                            "close_time": row["close_time"],
                            "open": row["open"],
                            "high": row["high"],
                            "low": row["low"],
                            "close": row["close"],
                            "volume": row.get("volume") or "",
                            "provider": row["provider"],
                            "is_complete": str(row["is_complete"]).lower(),
                        }
                    )
        metadata = TwelveDataProvider(
            "offline-readiness-reference"
        ).discover_instruments()[0].to_strategy_metadata()
        instrument = {
            "strategy_id": metadata.strategy_id,
            "instrument_id": metadata.instrument_id,
            "display_name": metadata.display_name,
            "provider": metadata.provider,
            "session_timezone": "America/New_York",
            "candle_boundary_convention": (
                "D1 closes at 17:00 America/New_York; H1/H4 in UTC"
            ),
            "price_precision": metadata.price_precision,
            "point_size": float(metadata.point_size),
            "tick_size": None,
            "tick_value_usd": None,
            "conversion_rate_to_usd": None,
            "contract_min": None,
            "contract_max": None,
            "contract_step": None,
            "minimum_stop_distance_points": 0,
        }
        (directory / "instrument.json").write_text(
            json.dumps(instrument), encoding="utf-8"
        )
        case = {
            "id": f"readiness_{replay['timeframe']}_{replay['signal_close_utc']}",
            "timeframe": replay["timeframe"],
            "evaluation_time": replay["replay_as_of_utc"],
        }
        return reference_evaluate_case(directory, case)


def _persisted_filter(
    connection: sqlite3.Connection,
    idempotency_key: str,
) -> tuple[dict[str, Any], str]:
    row = connection.execute(
        """
        SELECT event_history.payload_json, processed_bars.source_case_id
        FROM event_history
        JOIN processed_bars USING (idempotency_key)
        WHERE event_history.idempotency_key = ?
          AND event_history.event_type = 'FILTER_EVALUATED'
        """,
        (idempotency_key,),
    ).fetchone()
    if row is None:
        raise ValueError(f"missing persisted Filter projection: {idempotency_key}")
    return json.loads(row["payload_json"]), str(row["source_case_id"])


def _reference_matches(
    reference: dict[str, Any],
    replay: dict[str, Any],
) -> bool:
    expected = replay["evaluation"]
    # The frozen reference calculator deliberately serializes numeric outputs
    # to 10 decimal places.  Compare at that published precision while the
    # production/replay/manual comparisons above remain exact Decimals.
    reference_quantum = Decimal("0.0000000001")
    return all(
        Decimal(str(reference["indicators"][key])).quantize(
            reference_quantum
        )
        == Decimal(str(expected["indicators"][key])).quantize(
            reference_quantum
        )
        for key in (
            "atr_d1_wilder_5",
            "activation_buffer",
            "daily_raw_low",
            "daily_raw_high",
            "daily_buy_level",
            "daily_sell_level",
            "recent_low_21",
            "recent_high_21",
        )
    ) and all(
        reference["classification"][key] == expected["classification"][key]
        for key in ("buy_filter_matched", "sell_filter_matched")
    )


def generate(
    database: Path,
    replay_database: Path,
    run_ids: tuple[str, ...],
    sessions: int,
    root: Path,
) -> dict[str, Any]:
    repository = SQLiteProjectionRepository(database)
    h1 = repository.canonical_bar_objects("TWELVE_DATA", "EUR/USD", "H1")
    h4 = repository.canonical_bar_objects("TWELVE_DATA", "EUR/USD", "H4")
    daily = repository.canonical_bar_objects("TWELVE_DATA", "EUR/USD", "D1")
    selected_sessions = daily[-sessions:]
    if len(selected_sessions) != sessions:
        raise ValueError("active database has insufficient completed D1 sessions")
    first_close = selected_sessions[0].close_time
    streams = {Timeframe.H1: h1, Timeframe.H4: h4}
    instrument = TwelveDataProvider(
        "offline-readiness-direct"
    ).discover_instruments()[0].to_strategy_metadata()
    evaluator = Spect8StrategyEvaluator()

    replay_connection = sqlite3.connect(replay_database)
    replay_connection.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in run_ids)
    rows = replay_connection.execute(
        f"""
        SELECT run_id, idempotency_key, signal_close_utc,
               replay_as_of_utc, timeframe, evaluation_json, input_json
        FROM replay_evaluations
        WHERE run_id IN ({placeholders})
        ORDER BY signal_close_utc,
                 CASE timeframe WHEN 'H1' THEN 0 ELSE 1 END
        """,
        run_ids,
    ).fetchall()
    replay_connection.close()

    active = sqlite3.connect(database)
    active.row_factory = sqlite3.Row
    evaluations: list[dict[str, Any]] = []
    manual_candidates: dict[str, dict[str, Any]] = {}
    for row in rows:
        signal_close = _dt(row["signal_close_utc"])
        if signal_close < first_close:
            continue
        replay = {
            "run_id": row["run_id"],
            "idempotency_key": row["idempotency_key"],
            "signal_close_utc": row["signal_close_utc"],
            "replay_as_of_utc": row["replay_as_of_utc"],
            "timeframe": row["timeframe"],
            "evaluation": json.loads(row["evaluation_json"]),
        }
        input_value = json.loads(row["input_json"])
        signal_bars = tuple(_bar(value) for value in input_value["signal_bars"])
        daily_bars = tuple(_bar(value) for value in input_value["daily_bars"])
        direct = evaluator.evaluate(
            StrategyRequest(
                case_id=replay["evaluation"]["case_id"],
                strategy_id=STRATEGY_ID,
                timeframe=Timeframe(replay["timeframe"]),
                evaluation_time=_dt(replay["replay_as_of_utc"]),
                signal_bars=signal_bars,
                daily_bars=daily_bars,
                instrument=instrument,
            )
        )
        direct_value = primitive(direct)
        manual = _manual(signal_bars, daily_bars)
        persisted, source_case_id = _persisted_filter(
            active, replay["idempotency_key"]
        )
        persisted_match = (
            persisted["buy_matched"]
            == direct_value["classification"]["buy_filter_matched"]
            and persisted["sell_matched"]
            == direct_value["classification"]["sell_filter_matched"]
            and _same_number(
                persisted["daily_buy_level"],
                direct_value["indicators"]["daily_buy_level"],
            )
            and _same_number(
                persisted["daily_sell_level"],
                direct_value["indicators"]["daily_sell_level"],
            )
            and source_case_id.startswith(
                "new_york_daily_rebuild:v1.0.3-atr10:"
            )
        )
        all_eligible = [
            _iso(bar.close_time) for bar in daily if bar.close_time <= signal_close
        ]
        entry = {
            "signal_timeframe": replay["timeframe"],
            "signal_close_utc": replay["signal_close_utc"],
            "signal_close_new_york": signal_close.astimezone(NEW_YORK).isoformat(),
            "eligible_d1_close_timestamps": all_eligible,
            "selected_atr_d1_closes": [
                _iso(bar.close_time) for bar in daily_bars
            ],
            "selected_latest_two_d1_closes": manual[
                "latest_two_d1_closes"
            ],
            "atr_d1_wilder_5": manual["atr_endpoint"],
            "buffer_5_percent": manual["buffer_5_percent"],
            "daily_raw_low": manual["daily_low"],
            "daily_raw_high": manual["daily_high"],
            "buy_level": manual["buy_level"],
            "sell_level": manual["sell_level"],
            "recent_low_21": manual["recent_low_21"],
            "recent_high_21": manual["recent_high_21"],
            "buy_matched": manual["buy_matched"],
            "sell_matched": manual["sell_matched"],
            "equal_close_d1_included": (
                daily_bars[-1].close_time == signal_close
            ),
            "forming_signal_excluded": all(bar.is_complete for bar in signal_bars),
            "forming_d1_excluded": all(bar.is_complete for bar in daily_bars),
            "future_d1_excluded": all(
                bar.close_time <= signal_close for bar in daily_bars
            ),
            "direct_equals_replay": direct_value == replay["evaluation"],
            "manual_equals_direct": _manual_matches_evaluation(
                manual, direct_value
            ),
            "persisted_filter_equals_direct": persisted_match,
        }
        evaluations.append(entry)
        choices = (
            ("buy_pass", entry["buy_matched"]),
            ("buy_fail", not entry["buy_matched"]),
            ("sell_pass", entry["sell_matched"]),
            ("sell_fail", not entry["sell_matched"]),
            (
                "equal_close_h1",
                entry["equal_close_d1_included"]
                and entry["signal_timeframe"] == "H1",
            ),
            (
                "equal_close_h4",
                entry["equal_close_d1_included"]
                and entry["signal_timeframe"] == "H4",
            ),
        )
        for name, matched in choices:
            if matched and name not in manual_candidates:
                reference = _reference_result(root, replay, input_value)
                manual_candidates[name] = {
                    "signal_timeframe": entry["signal_timeframe"],
                    "signal_close_utc": entry["signal_close_utc"],
                    "calculation": manual,
                    "manual_equals_direct": entry["manual_equals_direct"],
                    "reference_equals_replay": _reference_matches(
                        reference, replay
                    ),
                    "persisted_filter_equals_direct": persisted_match,
                }
    active.close()

    if len(manual_candidates) != 6:
        raise ValueError("required manual calculation examples were unavailable")
    counters = Counter(item["signal_timeframe"] for item in evaluations)
    parity_fields = (
        "direct_equals_replay",
        "manual_equals_direct",
        "persisted_filter_equals_direct",
        "forming_signal_excluded",
        "forming_d1_excluded",
        "future_d1_excluded",
    )
    return {
        "authority": "SPECT8_MICRO_DAILY_V1_0_3",
        "active_database": str(database.resolve()),
        "replay_database": str(replay_database.resolve()),
        "replay_run_ids": list(run_ids),
        "completed_new_york_sessions": [
            _iso(bar.close_time) for bar in selected_sessions
        ],
        "session_count": len(selected_sessions),
        "evaluation_count": len(evaluations),
        "evaluation_counts": dict(sorted(counters.items())),
        "parity": {
            field: all(item[field] for item in evaluations)
            for field in parity_fields
        },
        "equal_close_counts": {
            timeframe: sum(
                item["equal_close_d1_included"]
                and item["signal_timeframe"] == timeframe
                for item in evaluations
            )
            for timeframe in ("H1", "H4")
        },
        "manual_examples": manual_candidates,
        "evaluations": evaluations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--replay-database", type=Path, required=True)
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument("--sessions", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(
        args.database,
        args.replay_database,
        tuple(args.run_id),
        args.sessions,
        ROOT,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sessions": result["session_count"],
                "evaluations": result["evaluation_count"],
                "counts": result["evaluation_counts"],
                "parity": result["parity"],
                "equal_close_counts": result["equal_close_counts"],
                "manual_examples": list(result["manual_examples"]),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
