from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from backend.app.domain import Timeframe
from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.market_data.clock import SystemClock
from backend.app.market_data.closed_bar import ClosedBarDetector
from backend.app.market_data.coordinator import MarketDataCoordinator
from backend.app.market_data.credit_budget import DailyCreditBudgetGuard
from backend.app.market_data.multi_provider import (
    MultiInstrumentTwelveDataProvider,
)
from backend.app.market_data.normalizer import CandleNormalizer
from backend.app.market_data.registry import (
    CanonicalInstrumentRegistry,
    twelve_data_instruments,
)
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService


def _status(value: dict[str, object] | None) -> str:
    if value is None:
        return "WAITING"
    return str(value["dashboard_state"])


def _direction(value: dict[str, object] | None, field: str) -> str:
    if value is None:
        return "WAITING"
    result = value[field]
    assert isinstance(result, dict)
    if field == "filter_result":
        buy = bool(result["buy_matched"])
        sell = bool(result["sell_matched"])
    else:
        buy = bool(result["confirmed_buy"])
        sell = bool(result["confirmed_sell"])
    if buy and sell:
        return "BUY_AND_SELL"
    if buy:
        return "BUY"
    if sell:
        return "SELL"
    return "NONE"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rate-limited live smoke for the enabled market scanner."
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--credit-ledger-database",
        type=Path,
        help="Optional shared runtime database used only for the rolling credit ledger.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    api_key = os.environ.get("TWELVE_DATA_API_KEY")
    if not api_key:
        print(json.dumps({"result": "NOT_RUN", "reason": "API_KEY_UNAVAILABLE"}))
        return 2

    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    database = args.database or root / "var" / f"scanner_smoke_{stamp}.sqlite3"
    instruments = twelve_data_instruments()
    registry = CanonicalInstrumentRegistry(instruments)
    repository = SQLiteProjectionRepository(database)
    repository.initialize()
    budget_repository = (
        SQLiteProjectionRepository(args.credit_ledger_database)
        if args.credit_ledger_database
        else repository
    )
    budget_repository.initialize()
    budget = DailyCreditBudgetGuard(budget_repository)
    provider = MultiInstrumentTwelveDataProvider(
        api_key,
        instruments,
        credit_budget=budget,
        repository=repository,
    )
    clock = SystemClock()
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository)
    coordinator = MarketDataCoordinator(
        provider,
        registry,
        CandleNormalizer(),
        ClosedBarDetector(),
        service,
        repository,
        clock,
    )
    started_at = datetime.now(timezone.utc)
    result = coordinator.poll_once()
    completed_at = datetime.now(timezone.utc)
    statuses = repository.statuses()

    with sqlite3.connect(database) as connection:
        quality_counts = {
            row[0]: int(row[1])
            for row in connection.execute(
                """
                SELECT instrument_id, COUNT(*)
                FROM canonical_quality_issues
                WHERE resolved_at IS NULL
                GROUP BY instrument_id
                """
            )
        }

    rows = []
    for instrument in registry.enabled():
        child = provider.provider_for(instrument.instrument_id)
        observations = child.request_observations()
        diagnostics = child.diagnostics(Timeframe.H1)
        health = repository.instrument_health(
            instrument.provider_id, instrument.instrument_id
        )
        latest = repository.latest_candle_timestamps(
            instrument.provider_id, instrument.instrument_id
        )
        snapshot = repository.latest_daily_filter_snapshot(
            instrument.provider_id,
            instrument.instrument_id,
            "MICRO_DAILY_FILTER_CURRENT_D1_V2",
        )
        by_timeframe = {
            item["timeframe"]: item
            for item in statuses
            if item["instrument_id"] == instrument.instrument_id
        }
        rows.append(
            {
                "instrument_id": instrument.instrument_id,
                "display_symbol": instrument.display_symbol,
                "provider_symbol": instrument.provider_symbol,
                "asset_class": instrument.asset_class,
                "instrument_kind": instrument.instrument_kind.value,
                "exposure_category": instrument.exposure_category.value,
                "is_proxy": instrument.is_proxy,
                "proxy_for": instrument.proxy_for,
                "provider_exchange": instrument.exchange,
                "provider_mic": instrument.mic_code,
                "request_start_timestamp": (
                    observations[0].started_at_utc.isoformat().replace("+00:00", "Z")
                    if observations
                    else None
                ),
                "request_finish_timestamp": (
                    observations[-1].finished_at_utc.isoformat().replace("+00:00", "Z")
                    if observations
                    else None
                ),
                "request_status": observations[-1].status if observations else "NOT_RUN",
                "request_completion_status": (
                    health["state"] if health else "BOOTSTRAPPING"
                ),
                "raw_candle_count": diagnostics.received_count,
                "completed_candle_count": diagnostics.completed_count,
                "forming_candle_count": diagnostics.forming_filtered_count,
                "structurally_partial_candle_count": diagnostics.structurally_partial_count,
                "outside_regular_session_count": diagnostics.outside_regular_session_count,
                "duplicate_count": diagnostics.duplicate_count,
                "gap_quarantine_count": diagnostics.gap_count
                + int(bool(health and health["state"] == "QUARANTINED")),
                "quarantined_count": int(
                    bool(health and health["state"] == "QUARANTINED")
                ),
                "open_quality_finding_count": quality_counts.get(
                    instrument.instrument_id, 0
                ),
                "latest_completed_h1_timestamp": latest.get("H1"),
                "latest_aggregated_h4_timestamp": latest.get("H4"),
                "latest_d1_session_state": (
                    {
                        "session_identifier": snapshot["current_partial_d1"][
                            "session_identifier"
                        ],
                        "quality_status": snapshot["current_partial_d1"][
                            "quality_status"
                        ],
                    }
                    if snapshot
                    else "WAITING"
                ),
                "latest_h1_evaluation_status": _status(by_timeframe.get("H1")),
                "latest_h4_evaluation_status": _status(by_timeframe.get("H4")),
                "h1_filter": _direction(by_timeframe.get("H1"), "filter_result"),
                "h1_signal": _direction(by_timeframe.get("H1"), "signal_result"),
                "h4_filter": _direction(by_timeframe.get("H4"), "filter_result"),
                "h4_signal": _direction(by_timeframe.get("H4"), "signal_result"),
                "provider_health": health["state"] if health else "BOOTSTRAPPING",
                "stale": bool(health and health["state"] == "STALE"),
                "validation_status": instrument.validation_status,
                "latest_error_summary": (
                    health["latest_error_summary"] if health else None
                ),
            }
        )

    records = provider.limiter.request_records()
    starts = [value for _, value in records]
    spacings = [
        (right - left).total_seconds() for left, right in zip(starts, starts[1:])
    ]
    rolling_max = max(
        (
            sum(0 <= (candidate - start).total_seconds() < 60 for candidate in starts)
            for start in starts
        ),
        default=0,
    )
    report = {
        "result": "PASS"
        if all(not row["latest_error_summary"] for row in rows)
        else "PARTIAL",
        "database": (
            str(database.resolve().relative_to(root.resolve()))
            if database.resolve().is_relative_to(root.resolve())
            else str(database.resolve())
        ),
        "scan_started_at": started_at.isoformat().replace("+00:00", "Z"),
        "scan_completed_at": completed_at.isoformat().replace("+00:00", "Z"),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
        "provider_requests_used": len(records),
        "request_starts": [
            {
                "instrument_id": label,
                "timestamp": value.isoformat().replace("+00:00", "Z"),
            }
            for label, value in records
        ],
        "minimum_request_spacing_seconds": min(spacings) if spacings else None,
        "maximum_requests_in_any_rolling_60_seconds": rolling_max,
        "rate_limit_proof": {
            "spacing_at_least_8_seconds": all(value >= 8 for value in spacings),
            "rolling_max_at_most_8": rolling_max <= 8,
        },
        "canonical_bars_inserted": result.canonical_bars_inserted,
        "enabled_instrument_count": len(registry.enabled()),
        "scan_overlap_occurred": False,
        "scan_sequence": provider.telemetry().last_scan_sequence,
        "credit_budget": asdict(budget.status()),
        "instruments": rows,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
