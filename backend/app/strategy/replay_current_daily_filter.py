from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from ..engine.current_daily_filter import (
    DailyFilterUnavailableError,
    build_daily_filter_snapshot,
)
from ..engine.models import CURRENT_D1_FILTER_V2, StrategyRequest
from ..engine.strategy import Spect8StrategyEvaluator
from ..market_data.daily_aggregator import NewYorkDailyAggregator
from ..market_data.daily_rebuild_cli import backup_sqlite
from ..market_data.forex_profile import (
    BrokerAlignedH4Aggregator,
    GapType,
    classify_market_gap,
)
from ..market_data.profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from ..market_data.twelve_data_provider import TwelveDataProvider
from ..repository import SQLiteProjectionRepository
from ..service import WalkingSkeletonService
from ..domain import Timeframe, primitive


@dataclass(frozen=True, slots=True)
class ReplayManifest:
    run_id: str
    provider: str
    adapter_version: str
    canonical_profile_version: str
    strategy_version: str
    instrument: str
    requested_start_utc: str
    requested_end_utc: str
    actual_h1_start_utc: str | None
    actual_h1_end_utc: str | None
    canonical_h1_count: int
    canonical_h4_count: int
    completed_d1_count: int
    daily_filter_snapshot_count: int
    h1_evaluation_count: int
    h4_evaluation_count: int
    expected_closure_count: int
    unexpected_gap_count: int
    quarantined_input_count: int
    source_checksum: str
    output_checksum: str
    applied_snapshot_count: int
    applied_evaluation_count: int
    duplicate_evaluation_count: int
    started_at: str
    finished_at: str
    result_status: str
    backup_path: str | None = None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _checksum(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def replay(
    *,
    repository: SQLiteProjectionRepository,
    provider: str,
    instrument_id: str,
    start: datetime,
    end: datetime,
    apply: bool,
) -> ReplayManifest:
    started = datetime.now(timezone.utc)
    provider_id = "TWELVE_DATA" if provider.lower() == "twelve_data" else provider
    source = repository.canonical_bar_objects(provider_id, instrument_id, "H1")
    source = tuple(bar for bar in source if bar.close_time <= end)
    display = tuple(bar for bar in source if start <= bar.close_time <= end)
    observable_as_of = (
        source[-1].close_time + timedelta(microseconds=1) if source else end
    )
    h4_result = BrokerAlignedH4Aggregator().aggregate(source, as_of=observable_as_of)
    d1_result = NewYorkDailyAggregator().aggregate(source, as_of=observable_as_of)
    expected = unexpected = 0
    for previous, current in zip(source, source[1:]):
        gap = classify_market_gap(previous, current)
        if gap is None or gap.gap_type is GapType.PROVIDER_PRICE_GAP:
            continue
        if gap.gap_type is GapType.EXPECTED_MARKET_CLOSURE:
            expected += 1
        else:
            unexpected += 1
    instrument = (
        TwelveDataProvider(api_key="offline-versioned-replay")
        .discover_instruments()[0]
        .to_strategy_metadata()
    )
    evaluator = Spect8StrategyEvaluator()
    service = WalkingSkeletonService(evaluator, None, repository)
    snapshots: dict[str, Any] = {}
    evaluations: list[Any] = []
    quarantined = 0
    applied_snapshots = applied_evaluations = duplicates = 0
    h4_by_close = {bar.close_time: bar for bar in h4_result.bars}
    for trigger in display:
        available_h1 = tuple(
            bar for bar in source if bar.close_time <= trigger.close_time
        )
        completed_d1 = tuple(
            bar for bar in d1_result.bars if bar.close_time <= trigger.close_time
        )[-10:]
        try:
            snapshot = build_daily_filter_snapshot(
                provider=provider_id,
                instrument=instrument_id,
                as_of_h1_close=trigger.close_time,
                h1_bars=available_h1,
                completed_d1_bars=completed_d1,
            )
        except DailyFilterUnavailableError:
            quarantined += 1
            continue
        snapshots[snapshot.snapshot_id] = snapshot
        if apply and repository.persist_daily_filter_snapshot(snapshot):
            applied_snapshots += 1
        candidates = [(Timeframe.H1, available_h1[-30:])]
        h4_trigger = h4_by_close.get(trigger.close_time)
        if h4_trigger is not None:
            available_h4 = tuple(
                bar for bar in h4_result.bars if bar.close_time <= trigger.close_time
            )[-30:]
            candidates.append((Timeframe.H4, available_h4))
        for timeframe, signal_bars in candidates:
            if len(signal_bars) < 30 or len(completed_d1) < 6:
                continue
            request = StrategyRequest(
                case_id=(
                    f"v2-replay:{provider_id}:{instrument_id}:"
                    f"{timeframe.value}:{_iso(trigger.close_time)}"
                ),
                strategy_id=CURRENT_D1_FILTER_V2,
                timeframe=timeframe,
                evaluation_time=trigger.close_time + timedelta(microseconds=1),
                signal_bars=signal_bars,
                daily_bars=completed_d1,
                instrument=instrument,
                strategy_version=CURRENT_D1_FILTER_V2,
                daily_filter_snapshot=snapshot,
            )
            projection = service.evaluate_request(request)
            evaluations.append(projection)
            if apply:
                outcome = service.process_request(request)
                if outcome.replayed:
                    duplicates += 1
                else:
                    applied_evaluations += 1
    source_payload = [
        [
            _iso(bar.open_time),
            _iso(bar.close_time),
            str(bar.open),
            str(bar.high),
            str(bar.low),
            str(bar.close),
        ]
        for bar in source
    ]
    output_payload = {
        "snapshots": [primitive(snapshots[key]) for key in sorted(snapshots)],
        "evaluations": [
            {
                "timeframe": item.status.timeframe.value,
                "close": _iso(item.status.signal_bar_close_time),
                "snapshot": item.status.daily_filter_snapshot_id,
                "filter": primitive(item.status.filter_result),
                "signal": primitive(item.status.signal_result),
            }
            for item in evaluations
        ],
    }
    finished = datetime.now(timezone.utc)
    source_checksum = _checksum(source_payload)
    output_checksum = _checksum(output_payload)
    run_id = f"current-d1-v2-{output_checksum[:16]}"
    relevant_h4_issues = sum(
        1
        for issue in h4_result.issues
        if issue.bucket_close is not None and start <= issue.bucket_close <= end
    )
    relevant_d1_issues = sum(
        1
        for issue in d1_result.issues
        if issue.session_end is not None and start <= issue.session_end <= end
    )
    return ReplayManifest(
        run_id=run_id,
        provider=provider_id,
        adapter_version="canonical-sqlite-v1",
        canonical_profile_version=PROFILE_ID,
        strategy_version=CURRENT_D1_FILTER_V2,
        instrument=instrument_id,
        requested_start_utc=_iso(start),
        requested_end_utc=_iso(end),
        actual_h1_start_utc=_iso(display[0].close_time) if display else None,
        actual_h1_end_utc=_iso(display[-1].close_time) if display else None,
        canonical_h1_count=len(display),
        canonical_h4_count=sum(
            1 for bar in h4_result.bars if start <= bar.close_time <= end
        ),
        completed_d1_count=sum(
            1 for bar in d1_result.bars if start <= bar.close_time <= end
        ),
        daily_filter_snapshot_count=len(snapshots),
        h1_evaluation_count=sum(
            1 for item in evaluations if item.status.timeframe is Timeframe.H1
        ),
        h4_evaluation_count=sum(
            1 for item in evaluations if item.status.timeframe is Timeframe.H4
        ),
        expected_closure_count=expected,
        unexpected_gap_count=unexpected,
        quarantined_input_count=quarantined + relevant_h4_issues + relevant_d1_issues,
        source_checksum=source_checksum,
        output_checksum=output_checksum,
        applied_snapshot_count=applied_snapshots,
        applied_evaluation_count=applied_evaluations,
        duplicate_evaluation_count=duplicates,
        started_at=_iso(started),
        finished_at=_iso(finished),
        result_status="PASS" if not unexpected else "QUARANTINED",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay Current Daily Filter V2 from persisted canonical H1"
    )
    parser.add_argument("--provider", default="twelve_data")
    parser.add_argument("--instrument", default="EUR/USD")
    parser.add_argument("--profile", default=PROFILE_ID)
    parser.add_argument("--strategy-version", default=CURRENT_D1_FILTER_V2)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--database", type=Path, default=Path("var/spect8_phase1.sqlite3")
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-active-database", type=Path)
    args = parser.parse_args(argv)
    if args.profile != PROFILE_ID or args.strategy_version != CURRENT_D1_FILTER_V2:
        parser.error("unsupported profile or strategy version")
    start = datetime.combine(
        datetime.fromisoformat(args.start).date(), time.min, tzinfo=timezone.utc
    )
    end = datetime.combine(
        datetime.fromisoformat(args.end).date(), time.max, tzinfo=timezone.utc
    )
    repository = SQLiteProjectionRepository(args.database.resolve())
    backup = None
    if args.apply:
        confirmed = (
            args.confirm_active_database is not None
            and args.confirm_active_database.resolve() == args.database.resolve()
        )
        if not confirmed:
            parser.error("--apply requires exact --confirm-active-database")
        backup = backup_sqlite(args.database.resolve())
        repository.initialize()
    manifest = replay(
        repository=repository,
        provider=args.provider,
        instrument_id=args.instrument,
        start=start,
        end=end,
        apply=args.apply,
    )
    if backup is not None:
        manifest = replace(manifest, backup_path=str(backup))
    print(json.dumps(asdict(manifest), indent=2, sort_keys=True))
    return 0 if manifest.result_status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
