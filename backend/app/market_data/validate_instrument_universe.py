from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from ..config import Settings
from .rate_limiter import SlidingWindowRateLimiter
from .credit_budget import DailyCreditBudgetGuard
from .registry import ETF_INSTRUMENT_IDS, CanonicalInstrumentRegistry, twelve_data_instruments
from ..repository import SQLiteProjectionRepository
from .universe_validation import (
    InstrumentUniverseValidator,
    append_report,
    sanitized_report,
    write_sanitized_report,
    etf_candidate_definitions,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover and validate the controlled Phase 3C market candidates."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/validation/PHASE3C_INSTRUMENT_UNIVERSE_VALIDATION_2026-08-05.json"),
    )
    parser.add_argument("--instrument-id", action="append", dest="instrument_ids")
    parser.add_argument("--append", action="store_true")
    parser.add_argument(
        "--profile",
        choices=("phase3c1", "phase3c2-etf"),
        default="phase3c1",
    )
    args = parser.parse_args()
    settings = Settings.from_environment()
    if not settings.twelve_data_api_key:
        raise ValueError("TWELVE_DATA_API_KEY is required")
    limiter = SlidingWindowRateLimiter(
        max_requests=settings.market_data_max_requests_per_minute,
        min_interval_seconds=settings.market_data_request_min_interval_seconds + 0.1,
    )
    repository = SQLiteProjectionRepository(settings.database_path)
    repository.initialize()
    budget = DailyCreditBudgetGuard(
        repository,
        daily_limit=settings.twelve_data_daily_credit_limit,
        operational_budget=settings.market_data_daily_operational_budget,
        reserve=settings.market_data_credit_reserve,
    )
    definitions = (
        etf_candidate_definitions()
        if args.profile == "phase3c2-etf"
        else None
    )
    validator = InstrumentUniverseValidator(
        api_key=settings.twelve_data_api_key,
        registry=CanonicalInstrumentRegistry(twelve_data_instruments()),
        limiter=limiter,
        credit_budget=budget,
        **({"definitions": definitions} if definitions is not None else {}),
    )
    report = sanitized_report(
        validator.validate(tuple(args.instrument_ids) if args.instrument_ids else None),
        limiter,
    )
    report["credit_budget"] = asdict(budget.status())
    if args.append and args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        report = append_report(previous, report)
    write_sanitized_report(args.output, report)
    summary = {
        "output": args.output.as_posix(),
        "request_count": report["request_count"],
        "h1_validated_count": report["h1_validated_count"],
        "disabled_count": report["disabled_count"],
        "credit_budget": asdict(budget.status()),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    os.environ.pop("TWELVE_DATA_API_KEY", None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
