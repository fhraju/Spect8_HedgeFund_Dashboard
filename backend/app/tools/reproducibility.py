from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import tempfile
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from ..dashboard_api import scanner_snapshot
from ..domain import Bar, Timeframe, primitive
from ..engine.current_daily_filter import build_daily_filter_snapshot
from ..engine.models import CURRENT_D1_FILTER_V2, StrategyRequest
from ..engine.strategy import Spect8StrategyEvaluator
from ..market_data.models import (
    CanonicalInstrument,
    ExposureCategory,
    HealthState,
    InstrumentKind,
    ProviderHealth,
    SessionProfileKind,
)
from ..market_data.normalizer import CandleNormalizer
from ..market_data.models import RawProviderCandle, TimestampSemantics
from ..market_data.profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from ..market_data.registry import CanonicalInstrumentRegistry
from ..repository import SQLiteProjectionRepository
from ..service import WalkingSkeletonService


CHECKPOINT_NAME = "phase_3b_10_instruments"
CHECKPOINT_INSTRUMENT_IDS = (
    "EUR_USD",
    "GBP_USD",
    "USD_JPY",
    "AUD_USD",
    "USD_CAD",
    "NZD_USD",
    "EUR_GBP",
    "EUR_JPY",
    "GBP_JPY",
    "XAU_USD",
)
PHASE_3C1_CHECKPOINT_NAME = "phase_3c1_12_instruments"
PHASE_3C1_INSTRUMENT_IDS = CHECKPOINT_INSTRUMENT_IDS + (
    "BTC_USD",
    "ETH_USD",
)
PHASE_3C2_CHECKPOINT_NAME = "phase_3c2_24_instruments"
PHASE_3C2_INSTRUMENT_IDS = PHASE_3C1_INSTRUMENT_IDS + (
    "SPY_US_ETF",
    "QQQ_US_ETF",
    "IWM_US_ETF",
    "FEZ_US_ETF",
    "EWJ_US_ETF",
    "EEM_US_ETF",
    "HYG_US_ETF",
    "SLV_US_ETF",
    "USO_US_ETF",
    "UNG_US_ETF",
    "DBA_US_ETF",
    "VIXM_US_ETF",
)
CHECKPOINT_INSTRUMENTS = {
    CHECKPOINT_NAME: CHECKPOINT_INSTRUMENT_IDS,
    PHASE_3C1_CHECKPOINT_NAME: PHASE_3C1_INSTRUMENT_IDS,
    PHASE_3C2_CHECKPOINT_NAME: PHASE_3C2_INSTRUMENT_IDS,
}
BASE_FIXTURE_FILES = (
    "instrument_registry.json",
    "canonical_h1.jsonl",
    "canonical_h4.jsonl",
    "completed_d1.jsonl",
    "filter_snapshots.jsonl",
    "expected_evaluations.jsonl",
    "expected_event_order.jsonl",
    "expected_scanner_response.json",
)


def _fixture_files(name: str) -> tuple[str, ...]:
    if name == CHECKPOINT_NAME:
        return BASE_FIXTURE_FILES
    return BASE_FIXTURE_FILES + ("current_partial_d1.jsonl",)


def _checkpoint_notice(name: str) -> str:
    return {
        CHECKPOINT_NAME: "Frozen Phase 3B ten-instrument reproducibility checkpoint.",
        PHASE_3C1_CHECKPOINT_NAME: "Frozen Phase 3C-1 twelve-instrument reproducibility checkpoint.",
        PHASE_3C2_CHECKPOINT_NAME: "Frozen Phase 3C-2 twenty-four-instrument reproducibility checkpoint.",
    }[name]


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware: {value}")
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _exact_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _exact_value(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_exact_value(child) for child in value]
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text(
        "".join(_canonical_json(row) + "\n" for row in rows), encoding="utf-8"
    )


def _bar_payload(bar: Bar) -> dict[str, Any]:
    return _exact_value(asdict(bar))


def _bar_from_payload(value: dict[str, Any]) -> Bar:
    return Bar(
        instrument_id=value["instrument_id"],
        timeframe=Timeframe(value["timeframe"]),
        open_time=_parse_time(value["open_time"]),
        close_time=_parse_time(value["close_time"]),
        open=Decimal(value["open"]),
        high=Decimal(value["high"]),
        low=Decimal(value["low"]),
        close=Decimal(value["close"]),
        provider=value["provider"],
        is_complete=bool(value["is_complete"]),
        volume=Decimal(value["volume"]) if value.get("volume") is not None else None,
        session_timezone=value.get("session_timezone", "UTC"),
        raw_provider_symbol=value.get("raw_provider_symbol"),
        raw_open_time=value.get("raw_open_time"),
        raw_close_time=value.get("raw_close_time"),
        raw_open=value.get("raw_open"),
        raw_high=value.get("raw_high"),
        raw_low=value.get("raw_low"),
        raw_close=value.get("raw_close"),
        synthetic=bool(value.get("synthetic", False)),
        quality_status=value.get("quality_status", "VALID"),
        construction_profile_version=value.get("construction_profile_version", "LEGACY"),
        provider_adapter_version=value.get("provider_adapter_version", "legacy"),
        source_timeframe=(
            Timeframe(value["source_timeframe"])
            if value.get("source_timeframe")
            else None
        ),
        source_candle_ids=tuple(value.get("source_candle_ids", ())),
        forward_filled=bool(value.get("forward_filled", False)),
        expected_closure_before=bool(value.get("expected_closure_before", False)),
        ingestion_run_id=value.get("ingestion_run_id"),
        created_at=(
            _parse_time(value["created_at"]) if value.get("created_at") else None
        ),
        session_identifier=value.get("session_identifier"),
        session_open_broker_time=value.get("session_open_broker_time"),
        session_close_broker_time=value.get("session_close_broker_time"),
    )


def _instrument_payload(instrument: CanonicalInstrument, order: int) -> dict[str, Any]:
    value = _exact_value(asdict(instrument))
    value["registry_order"] = order
    return value


def _instrument_from_payload(value: dict[str, Any]) -> CanonicalInstrument:
    def decimal(name: str) -> Decimal | None:
        raw = value.get(name)
        return Decimal(raw) if raw is not None else None

    return CanonicalInstrument(
        instrument_id=value["instrument_id"],
        provider_id=value["provider_id"],
        provider_symbol=value["provider_symbol"],
        display_name=value["display_name"],
        asset_class=value["asset_class"],
        point_size=Decimal(value["point_size"]),
        tick_size=decimal("tick_size"),
        price_precision=int(value["price_precision"]),
        tick_value_usd=decimal("tick_value_usd"),
        conversion_rate_to_usd=decimal("conversion_rate_to_usd"),
        contract_min=decimal("contract_min"),
        contract_max=decimal("contract_max"),
        contract_step=decimal("contract_step"),
        minimum_stop_distance_points=decimal("minimum_stop_distance_points"),
        quote_currency=value["quote_currency"],
        profit_currency=value["profit_currency"],
        session_timezone=value["session_timezone"],
        candle_boundary_convention=value["candle_boundary_convention"],
        available_timeframes=tuple(Timeframe(item) for item in value["available_timeframes"]),
        strategy_id=value["strategy_id"],
        display_symbol=value.get("display_symbol", ""),
        enabled=bool(value.get("enabled", True)),
        exchange=value.get("exchange"),
        mic_code=value.get("mic_code"),
        provider_instrument_type=value.get("provider_instrument_type"),
        provider_timezone=value.get("provider_timezone"),
        validation_status=value.get("validation_status", "NOT_VALIDATED"),
        registry_order=int(value.get("registry_order", 0)),
        synthetic=bool(value.get("synthetic", False)),
        instrument_kind=InstrumentKind(
            value.get("instrument_kind", InstrumentKind.DIRECT_MARKET.value)
        ),
        exposure_category=ExposureCategory(
            value.get("exposure_category", ExposureCategory.CURRENCY.value)
        ),
        underlying_description=value.get("underlying_description"),
        is_proxy=bool(value.get("is_proxy", False)),
        proxy_for=value.get("proxy_for"),
        session_profile=SessionProfileKind(
            value.get("session_profile", SessionProfileKind.DIRECT_MARKET.value)
        ),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _database_schema_digest(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return hashlib.sha256(_canonical_json([list(row) for row in rows]).encode()).hexdigest()


def _git_commit(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def capture_checkpoint(
    *,
    repository_root: Path,
    database_path: Path,
    registry: CanonicalInstrumentRegistry,
    name: str,
    evaluation_time: datetime,
    fixture_root: Path | None = None,
) -> dict[str, Any]:
    if name not in CHECKPOINT_INSTRUMENTS:
        raise ValueError(f"unsupported checkpoint name: {name}")
    instrument_ids = CHECKPOINT_INSTRUMENTS[name]
    evaluation_time = evaluation_time.astimezone(timezone.utc)
    target = _iso(evaluation_time)
    selected = tuple(registry.by_id(item) for item in instrument_ids)
    if tuple(item.instrument_id for item in selected) != instrument_ids:
        raise ValueError("checkpoint registry order differs from the verified order")
    if any(not item.enabled for item in selected):
        raise ValueError("all checkpoint instruments must be enabled")

    output = fixture_root or (
        repository_root
        / "backend"
        / "tests"
        / "fixtures"
        / "reproducibility"
        / name
    )
    output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        bars: dict[str, list[dict[str, Any]]] = {"H1": [], "H4": [], "D1": []}
        repository = SQLiteProjectionRepository(database_path)
        h1_targets: dict[str, str] = {}
        h4_targets: dict[str, str] = {}
        for instrument_id in instrument_ids:
            all_h1 = repository.canonical_bar_objects("TWELVE_DATA", instrument_id, "H1")
            all_h4 = repository.canonical_bar_objects("TWELVE_DATA", instrument_id, "H4")
            all_d1 = repository.canonical_bar_objects("TWELVE_DATA", instrument_id, "D1")
            selected_bars = {
                "H1": tuple(item for item in all_h1 if _iso(item.close_time) <= target)[-30:],
                "H4": tuple(item for item in all_h4 if _iso(item.close_time) <= target)[-30:],
                "D1": tuple(item for item in all_d1 if _iso(item.close_time) <= target)[-10:],
            }
            selected_bars = {
                timeframe: tuple(
                    replace(item, ingestion_run_id=None, created_at=item.close_time)
                    for item in values
                )
                for timeframe, values in selected_bars.items()
            }
            if selected_bars["H1"]:
                h1_targets[instrument_id] = _iso(selected_bars["H1"][-1].close_time)
            if selected_bars["H4"]:
                h4_targets[instrument_id] = _iso(selected_bars["H4"][-1].close_time)
            for timeframe, values in selected_bars.items():
                if len(values) < (30 if timeframe in {"H1", "H4"} else 6):
                    raise ValueError(
                        f"{instrument_id} has incomplete {timeframe} fixture history"
                    )
                expected_close = (
                    target
                    if timeframe == "H1" and name != PHASE_3C2_CHECKPOINT_NAME
                    else None
                )
                if expected_close and _iso(values[-1].close_time) != expected_close:
                    raise ValueError(
                        f"{target} is unavailable for {instrument_id}; "
                        f"latest available H1 close is {_iso(values[-1].close_time)}. "
                        "No checkpoint was created."
                    )
                bars[timeframe].extend(_bar_payload(item) for item in values)
        if name != PHASE_3C2_CHECKPOINT_NAME and len(set(h4_targets.values())) != 1:
            raise ValueError("checkpoint instruments do not share one latest H4 close")

        expected_statuses: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        snapshot_objects: list[Any] = []
        evaluated_by_key: dict[str, Any] = {}
        evaluator_service = WalkingSkeletonService(
            Spect8StrategyEvaluator(), None, repository
        )
        bar_objects = {
            timeframe: [_bar_from_payload(item) for item in values]
            for timeframe, values in bars.items()
        }
        for instrument_id in instrument_ids:
            instrument = registry.by_id(instrument_id)
            for timeframe, signal_close_text in (
                ("H1", h1_targets[instrument_id]),
                ("H4", h4_targets[instrument_id]),
            ):
                idempotency_key = (
                    f"{CURRENT_D1_FILTER_V2}:TWELVE_DATA:{instrument_id}:"
                    f"{timeframe}:{signal_close_text}"
                )
                evidence = connection.execute(
                    "SELECT occurred_at, source_case_id, payload_json FROM event_history "
                    "WHERE idempotency_key=? AND event_type='FILTER_EVALUATED'",
                    (idempotency_key,),
                ).fetchone()
                if evidence is None:
                    raise ValueError(
                        f"persisted evaluation is unavailable for {instrument_id} "
                        f"{timeframe} {signal_close_text}"
                    )
                filter_evidence = json.loads(evidence["payload_json"])
                if filter_evidence.get("strategy_version") != CURRENT_D1_FILTER_V2:
                    raise ValueError(f"wrong strategy version for {idempotency_key}")
                signal_close = _parse_time(signal_close_text)
                h1 = tuple(
                    item
                    for item in bar_objects["H1"]
                    if item.instrument_id == instrument_id
                    and item.close_time <= signal_close
                )
                daily = tuple(
                    item
                    for item in bar_objects["D1"]
                    if item.instrument_id == instrument_id
                    and item.close_time <= signal_close
                )
                signal = tuple(
                    item
                    for item in bar_objects[timeframe]
                    if item.instrument_id == instrument_id
                    and item.close_time <= signal_close
                )[-30:]
                snapshot = build_daily_filter_snapshot(
                    provider="TWELVE_DATA",
                    instrument=instrument_id,
                    as_of_h1_close=signal_close,
                    h1_bars=h1,
                    completed_d1_bars=daily,
                    sparse_actual_h1=(instrument.instrument_kind is InstrumentKind.ETF),
                )
                persisted_snapshot = connection.execute(
                    "SELECT payload_json FROM daily_filter_snapshots WHERE snapshot_id=?",
                    (snapshot.snapshot_id,),
                ).fetchone()
                if persisted_snapshot is None:
                    raise ValueError(f"missing filter snapshot {snapshot.snapshot_id}")
                snapshot = replace(snapshot, ingestion_run_id=None)
                snapshot_payload = _exact_value(asdict(snapshot))
                persisted_payload = json.loads(persisted_snapshot["payload_json"])
                persisted_payload["ingestion_run_id"] = None
                if persisted_payload != snapshot_payload:
                    raise ValueError(f"snapshot payload mismatch for {snapshot.snapshot_id}")
                request = StrategyRequest(
                    case_id=evidence["source_case_id"],
                    strategy_id=CURRENT_D1_FILTER_V2,
                    timeframe=Timeframe(timeframe),
                    evaluation_time=signal_close + timedelta(seconds=1),
                    signal_bars=signal,
                    daily_bars=daily,
                    instrument=instrument.to_strategy_metadata(),
                    strategy_version=CURRENT_D1_FILTER_V2,
                    daily_filter_snapshot=snapshot,
                )
                evaluated = evaluator_service.evaluate_request(request)
                status_payload = primitive(evaluated.status)
                if status_payload["strategy_version"] != CURRENT_D1_FILTER_V2:
                    raise ValueError(f"recalculated strategy mismatch for {idempotency_key}")
                expected_statuses.append(
                    {
                        "instrument_id": instrument_id,
                        "timeframe": timeframe,
                        "status": status_payload,
                    }
                )
                snapshots.append(snapshot_payload)
                snapshot_objects.append(snapshot)
                evaluated_by_key[idempotency_key] = evaluated

        idempotency_keys = [item["status"]["idempotency_key"] for item in expected_statuses]
        placeholders = ",".join("?" for _ in idempotency_keys)
        event_rows = connection.execute(
            f"SELECT idempotency_key, sequence, event_type, occurred_at, instrument_id, "
            f"timeframe, source_case_id, payload_json, synthetic FROM event_history "
            f"WHERE idempotency_key IN ({placeholders}) ORDER BY instrument_id, timeframe, sequence",
            idempotency_keys,
        ).fetchall()
        persisted_events = [
            {
                "idempotency_key": row["idempotency_key"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "occurred_at": row["occurred_at"],
                "instrument_id": row["instrument_id"],
                "timeframe": row["timeframe"],
                "source_case_id": row["source_case_id"],
                "payload": json.loads(row["payload_json"]),
                "synthetic": bool(row["synthetic"]),
            }
            for row in event_rows
        ]
        if any(
            len(
                [event for event in persisted_events if event["idempotency_key"] == key]
            )
            < 6
            for key in idempotency_keys
        ):
            raise ValueError("one or more persisted event traces are incomplete")
        events: list[dict[str, Any]] = []
        for key, evaluated in evaluated_by_key.items():
            recalculated = [
                {
                    "idempotency_key": event.idempotency_key,
                    "sequence": event.sequence,
                    "event_type": event.event_type.value,
                    "occurred_at": primitive(event.occurred_at),
                    "instrument_id": event.instrument_id,
                    "timeframe": event.timeframe.value,
                    "source_case_id": event.source_case_id,
                    "payload": primitive(event.payload),
                    "synthetic": event.synthetic,
                }
                for event in evaluator_service.events_for_projection(evaluated)
            ]
            persisted = [
                event for event in persisted_events if event["idempotency_key"] == key
            ]
            if [item["event_type"] for item in recalculated] != [
                item["event_type"] for item in persisted
            ]:
                raise ValueError(f"persisted event trace mismatch for {key}")
            if any(
                item["event_type"] == "FILTER_EVALUATED"
                and item["payload"].get("daily_filter_snapshot_id")
                != evaluated.status.daily_filter_snapshot_id
                for item in persisted
            ):
                raise ValueError(f"persisted snapshot reference mismatch for {key}")
            events.extend(recalculated)
        grouped_events = [
            {
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "events": [
                    event
                    for event in events
                    if event["instrument_id"] == instrument_id
                    and event["timeframe"] == timeframe
                ],
            }
            for instrument_id in instrument_ids
            for timeframe in ("H1", "H4")
        ]

        _write_json(
            output / "instrument_registry.json",
            [_instrument_payload(item, index + 1) for index, item in enumerate(selected)],
        )
        _write_jsonl(output / "canonical_h1.jsonl", bars["H1"])
        _write_jsonl(output / "canonical_h4.jsonl", bars["H4"])
        _write_jsonl(output / "completed_d1.jsonl", bars["D1"])
        _write_jsonl(output / "filter_snapshots.jsonl", snapshots)
        if name != CHECKPOINT_NAME:
            _write_jsonl(
                output / "current_partial_d1.jsonl",
                (
                    {
                        "instrument_id": item["instrument"],
                        "as_of_h1_close_time_utc": item[
                            "as_of_h1_close_time_utc"
                        ],
                        "current_partial_d1": item["current_partial_d1"],
                    }
                    for item in snapshots
                ),
            )
        _write_jsonl(output / "expected_evaluations.jsonl", expected_statuses)
        _write_jsonl(output / "expected_event_order.jsonl", grouped_events)

        # Project into a clean temporary database so no later live status can
        # change the frozen API response.
        with tempfile.TemporaryDirectory(prefix="spect8-capture-") as directory:
            frozen_repository = SQLiteProjectionRepository(
                Path(directory) / "checkpoint.sqlite3"
            )
            frozen_repository.initialize()
            frozen_repository.persist_canonical_bars(
                tuple(
                    item
                    for timeframe in ("H1", "H4", "D1")
                    for item in bar_objects[timeframe]
                )
            )
            for snapshot in snapshot_objects:
                frozen_repository.persist_daily_filter_snapshot(snapshot)
            for evaluated in evaluated_by_key.values():
                frozen_repository.persist_projection(
                    evaluated.status,
                    evaluator_service.events_for_projection(evaluated),
                )
            for instrument in selected:
                frozen_repository.update_instrument_health(
                    instrument.instrument_id,
                    ProviderHealth(
                        provider_id="TWELVE_DATA",
                        state=HealthState.HEALTHY,
                        checked_at=evaluation_time,
                        latest_completed_close=evaluation_time,
                        freshness_seconds=0,
                        detail="Frozen checkpoint input validated.",
                        synthetic=False,
                    ),
                )
            rows = scanner_snapshot(
                frozen_repository, selected, evaluation_time
            ).model_dump(mode="json")
        notice = _checkpoint_notice(name)
        scanner = {
            "synthetic": False,
            "source": "TWELVE_DATA_PROVIDER",
            "notice": notice,
            "data": rows,
        }
        _write_json(output / "expected_scanner_response.json", scanner)

        schema_digest = _database_schema_digest(connection)
        fixture_files = _fixture_files(name)
        checksums = {item: _sha256(output / item) for item in fixture_files}
        manifest = {
            "checkpoint_name": name,
            "creation_timestamp": target,
            "source_git_commit_sha": _git_commit(repository_root),
            "source_worktree_note": (
                "Checkpoint captured from the verified Phase 3B ten-instrument worktree."
                if name == CHECKPOINT_NAME
                else (
                    "Checkpoint captured before Phase 3C-2 ETF expansion from the verified twelve-instrument worktree."
                    if name == PHASE_3C1_CHECKPOINT_NAME
                    else "Checkpoint captured after controlled Phase 3C-2 validation with TLT disabled for one quarantined OHLC row."
                )
            ),
            "database_schema_revision": {
                "sqlite_user_version": connection.execute("PRAGMA user_version").fetchone()[0],
                "schema_sha256": schema_digest,
            },
            "strategy_version": CURRENT_D1_FILTER_V2,
            "canonical_profile_version": PROFILE_ID,
            "canonical_instrument_ids": list(instrument_ids),
            "provider_symbol_mappings": {
                item.instrument_id: item.provider_symbol for item in selected
            },
            "provider_exchange_identities": {
                item.instrument_id: {
                    "provider": item.provider_id,
                    "symbol": item.provider_symbol,
                    "exchange": item.exchange,
                    "mic_code": item.mic_code,
                }
                for item in selected
            },
            "canonical_utc_evaluation_timestamp": target,
            "h1_evaluation_timestamps": h1_targets,
            "h4_evaluation_timestamps": h4_targets,
            "h4_evaluation_timestamp": (
                next(iter(h4_targets.values()))
                if len(set(h4_targets.values())) == 1
                else None
            ),
            "broker_presentation_timestamp": "2026-08-05 17:00",
            "typed_settings": {
                "provider": "TWELVE_DATA",
                "canonical_timeframes": ["H1", "H4", "D1"],
                "minimum_signal_history": 30,
                "completed_d1_history": 10,
            },
            "d1_session_authority": "17:00 America/New_York",
            "atr_specification": "Wilder ATR(5), completed D1 sessions only; current partial D1 excluded",
            "buffer_specification": "Wilder ATR(5) * 0.05",
            "fixture_checksums": checksums,
            "export_command": (
                "python -m backend.app.tools.capture_reproducibility_checkpoint "
                f"--name {name} --evaluation-time {target}"
            ),
            "reproduction_command": (
                "python -m backend.app.tools.reproduce_reproducibility_checkpoint "
                f"--name {name}"
            ),
            "validation_command": (
                "python -m pytest -q backend/tests/"
                "test_phase_3b_10_instrument_reproducibility.py"
            ),
        }
        _write_json(output / "manifest.json", manifest)
        checksum_names = ("manifest.json", *fixture_files)
        (output / "checksums.sha256").write_text(
            "".join(f"{_sha256(output / item)}  {item}\n" for item in checksum_names),
            encoding="ascii",
        )
        return manifest
    finally:
        connection.close()


def validate_checksums(fixture_root: Path) -> None:
    manifest = json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["fixture_checksums"].items():
        actual = _sha256(fixture_root / name)
        if actual != expected:
            raise ValueError(f"checksum mismatch for {name}: {actual} != {expected}")
    for line in (fixture_root / "checksums.sha256").read_text(encoding="ascii").splitlines():
        expected, name = line.split("  ", 1)
        actual = _sha256(fixture_root / name)
        if actual != expected:
            raise ValueError(f"checksum manifest mismatch for {name}")


def reproduce_checkpoint(*, fixture_root: Path, database_path: Path) -> dict[str, Any]:
    validate_checksums(fixture_root)
    manifest = json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))
    registry_values = json.loads(
        (fixture_root / "instrument_registry.json").read_text(encoding="utf-8")
    )
    instruments = tuple(_instrument_from_payload(item) for item in registry_values)
    registry = CanonicalInstrumentRegistry(instruments)
    fixture_ids = tuple(manifest["canonical_instrument_ids"])
    expected_ids = CHECKPOINT_INSTRUMENTS.get(manifest["checkpoint_name"])
    if expected_ids is None or fixture_ids != expected_ids:
        raise ValueError("fixture manifest checkpoint definition is not canonical")
    if tuple(item.instrument_id for item in instruments) != fixture_ids:
        raise ValueError("fixture registry order is not canonical")
    expected = _read_jsonl(fixture_root / "expected_evaluations.jsonl")
    expected_events = _read_jsonl(fixture_root / "expected_event_order.jsonl")
    expected_scanner = json.loads(
        (fixture_root / "expected_scanner_response.json").read_text(encoding="utf-8")
    )
    bars_by_timeframe = {
        "H1": [_bar_from_payload(item) for item in _read_jsonl(fixture_root / "canonical_h1.jsonl")],
        "H4": [_bar_from_payload(item) for item in _read_jsonl(fixture_root / "canonical_h4.jsonl")],
        "D1": [_bar_from_payload(item) for item in _read_jsonl(fixture_root / "completed_d1.jsonl")],
    }
    snapshot_values = {
        (item["instrument"], item["as_of_h1_close_time_utc"]): item
        for item in _read_jsonl(fixture_root / "filter_snapshots.jsonl")
    }

    repository = SQLiteProjectionRepository(database_path)
    repository.initialize()
    normalizer = CandleNormalizer()
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository)
    normalized_h1: list[Bar] = []
    for bar in bars_by_timeframe["H1"]:
        instrument = registry.by_id(bar.instrument_id)
        raw = RawProviderCandle(
            provider_id=bar.provider,
            provider_symbol=bar.raw_provider_symbol or instrument.provider_symbol,
            timeframe=Timeframe.H1,
            raw_open_time=bar.raw_open_time or _iso(bar.open_time),
            raw_close_time=bar.raw_close_time or _iso(bar.close_time),
            open=bar.raw_open or str(bar.open),
            high=bar.raw_high or str(bar.high),
            low=bar.raw_low or str(bar.low),
            close=bar.raw_close or str(bar.close),
            volume=str(bar.volume) if bar.volume is not None else None,
            is_complete=True,
            session_timezone=bar.session_timezone,
            canonical_instrument=bar.instrument_id,
            source_timeframe=Timeframe.H1,
            timestamp_semantics=TimestampSemantics.INTERVAL_START,
            open_time_utc=bar.open_time,
            close_time_utc=bar.close_time,
            source_id=(bar.source_candle_ids[0] if bar.source_candle_ids else None),
            received_at=bar.created_at,
            provider_metadata={"ingestion_run_id": bar.ingestion_run_id},
            adapter_version=bar.provider_adapter_version,
        )
        result = normalizer.normalize(raw, instrument)
        if result.candle is None or result.issues:
            raise ValueError(f"normalization failed for {bar.instrument_id}: {result.issues}")
        if _bar_payload(result.candle) != _bar_payload(bar):
            raise ValueError(f"normalized H1 differs from fixture for {bar.instrument_id} {bar.close_time}")
        normalized_h1.append(result.candle)
    repository.persist_canonical_bars(tuple(normalized_h1))
    repository.persist_canonical_bars(tuple(bars_by_timeframe["H4"]))
    repository.persist_canonical_bars(tuple(bars_by_timeframe["D1"]))

    for item in expected:
        instrument_id = item["instrument_id"]
        timeframe = Timeframe(item["timeframe"])
        expected_status = item["status"]
        signal_close = _parse_time(expected_status["signal_bar_close_time"])
        h1 = tuple(
            bar
            for bar in normalized_h1
            if bar.instrument_id == instrument_id and bar.close_time <= signal_close
        )
        signal = tuple(
            bar
            for bar in bars_by_timeframe[timeframe.value]
            if bar.instrument_id == instrument_id and bar.close_time <= signal_close
        )[-30:]
        daily = tuple(
            bar
            for bar in bars_by_timeframe["D1"]
            if bar.instrument_id == instrument_id and bar.close_time <= signal_close
        )[-10:]
        snapshot = build_daily_filter_snapshot(
            provider="TWELVE_DATA",
            instrument=instrument_id,
            as_of_h1_close=signal_close,
            h1_bars=h1,
            completed_d1_bars=daily,
            sparse_actual_h1=(
                registry.by_id(instrument_id).instrument_kind is InstrumentKind.ETF
            ),
        )
        expected_snapshot = snapshot_values[(instrument_id, _iso(signal_close))]
        if _exact_value(asdict(snapshot)) != expected_snapshot:
            raise ValueError(f"snapshot mismatch for {instrument_id} {timeframe.value}")
        repository.persist_daily_filter_snapshot(snapshot)
        request = StrategyRequest(
            case_id=expected_status["source_case_id"],
            strategy_id=CURRENT_D1_FILTER_V2,
            timeframe=timeframe,
            evaluation_time=_parse_time(expected_status["last_update"]),
            signal_bars=signal,
            daily_bars=daily,
            instrument=registry.by_id(instrument_id).to_strategy_metadata(),
            strategy_version=CURRENT_D1_FILTER_V2,
            daily_filter_snapshot=snapshot,
        )
        service.process_request(request)

    actual_statuses = {
        (item["instrument_id"], item["timeframe"]): item
        for item in repository.statuses()
        if item.get("strategy_version") == CURRENT_D1_FILTER_V2
    }
    for item in expected:
        key = (item["instrument_id"], item["timeframe"])
        if actual_statuses.get(key) != item["status"]:
            raise ValueError(f"evaluation mismatch for {key[0]} {key[1]}")
    events = repository.events()
    for group in expected_events:
        actual = [
            {key: value for key, value in event.items() if key != "id"}
            for event in events
            if event["instrument_id"] == group["instrument_id"]
            and event["timeframe"] == group["timeframe"]
        ]
        if actual != group["events"]:
            raise ValueError(
                f"event order mismatch for {group['instrument_id']} {group['timeframe']}"
            )

    generated_at = _parse_time(manifest["canonical_utc_evaluation_timestamp"])
    for instrument in instruments:
        repository.update_instrument_health(
            instrument.instrument_id,
            ProviderHealth(
                provider_id="TWELVE_DATA",
                state=HealthState.HEALTHY,
                checked_at=generated_at,
                latest_completed_close=generated_at,
                freshness_seconds=0,
                detail="Frozen checkpoint input validated.",
                synthetic=False,
            ),
        )
    scanner_data = scanner_snapshot(repository, instruments, generated_at).model_dump(mode="json")
    # The Phase 3B fixture freezes its original API contract. Later additive
    # metadata is tested separately and deliberately excluded only from this
    # exact legacy projection comparison.
    if manifest["checkpoint_name"] == CHECKPOINT_NAME:
        for row in scanner_data["instruments"]:
            for field in (
                "provider",
                "exchange",
                "mic_code",
                "provider_instrument_type",
                "provider_timezone",
                "validation_status",
            ):
                row.pop(field, None)
    if manifest["checkpoint_name"] in {
        CHECKPOINT_NAME,
        PHASE_3C1_CHECKPOINT_NAME,
    }:
        scanner_data.pop("credit_budget", None)
        for row in scanner_data["instruments"]:
            for field in (
                "instrument_kind",
                "exposure_category",
                "underlying_description",
                "is_proxy",
                "proxy_for",
                "provider_exchange",
                "credit_budget_status",
            ):
                row.pop(field, None)
    notice = _checkpoint_notice(manifest["checkpoint_name"])
    actual_scanner = {
        "synthetic": False,
        "source": "TWELVE_DATA_PROVIDER",
        "notice": notice,
        "data": scanner_data,
    }
    if actual_scanner != expected_scanner:
        raise ValueError("scanner API projection differs from the frozen checkpoint")

    return {
        "checkpoint_name": manifest["checkpoint_name"],
        "instrument_count": len(instruments),
        "evaluation_count": len(expected),
        "event_count": len(events),
        "checksums": "VALID",
        "network_calls": 0,
        "canonical_utc_evaluation_timestamp": manifest["canonical_utc_evaluation_timestamp"],
        "h4_evaluation_timestamp": manifest["h4_evaluation_timestamp"],
    }
