from __future__ import annotations

import argparse
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from .conformance import (
    FixtureProviderAdapter,
    ProviderCertificationEngine,
    fixture_instrument,
)
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from .twelve_data_provider import TwelveDataProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Certify a raw provider adapter against the canonical Forex V1 structure."
    )
    parser.add_argument(
        "--provider", choices=("ic_markets_fixture", "twelve_data"), required=True
    )
    parser.add_argument("--instrument", default="EUR/USD")
    parser.add_argument("--profile", default=PROFILE_ID)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("backend/tests/fixtures/ic_markets_forex_v1/reference.json"),
    )
    args = parser.parse_args(argv)
    if args.profile != PROFILE_ID:
        parser.error(f"unsupported profile: {args.profile}")
    start = datetime.combine(
        datetime.fromisoformat(args.start).date(), time.min, tzinfo=timezone.utc
    )
    end = datetime.combine(
        datetime.fromisoformat(args.end).date(), time.min, tzinfo=timezone.utc
    ) + timedelta(days=1)
    if args.provider == "ic_markets_fixture":
        adapter = FixtureProviderAdapter(args.fixture.resolve())
        instrument = fixture_instrument()
    else:
        api_key = os.environ.get("TWELVE_DATA_API_KEY")
        if not api_key:
            parser.error(
                "TWELVE_DATA_API_KEY is required and is never written to the report"
            )
        adapter = TwelveDataProvider(api_key=api_key)
        instrument = adapter.discover_instruments()[0]
    if args.instrument != instrument.instrument_id:
        parser.error("adapter symbol mapping does not match --instrument")
    report = ProviderCertificationEngine().certify(
        adapter=adapter,
        instrument=instrument,
        start=start,
        end=end,
    )
    print(report.to_json())
    return 0 if report.certified else 2


if __name__ == "__main__":
    raise SystemExit(main())
