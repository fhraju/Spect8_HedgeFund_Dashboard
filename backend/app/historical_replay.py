from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Protocol

from .domain import Bar, DomainEvent, Timeframe, primitive
from .engine.models import StrategyRequest
from .engine.strategy import (
    SPECIFICATION_ID,
    STRATEGY_ID,
    Spect8StrategyEvaluator,
)
from .market_data.clock import FixedClock
from .market_data.closed_bar import (
    DAILY_ATR_INPUT_HISTORY,
    MIN_DAILY_HISTORY,
    MIN_SIGNAL_HISTORY,
    TIMEFRAME_STEP,
    ClosedBarDetector,
)
from .market_data.daily_aggregator import NewYorkDailyAggregator
from .market_data.models import CanonicalInstrument, RawProviderCandle
from .market_data.normalizer import CandleNormalizer
from .market_data.session_boundaries import is_expected_forex_weekend_gap
from .market_data.twelve_data_provider import TwelveDataProvider
from .repository import SQLiteProjectionRepository
from .service import EvaluatedProjection, WalkingSkeletonService

PROVIDER_ID = "TWELVE_DATA"
INSTRUMENT_ID = "EUR/USD"
REPLAY_TIMEFRAMES = (Timeframe.H1, Timeframe.H4)
CONTEXT_TIMEFRAME = Timeframe.D1
# Ten completed New York sessions are retained for the production Wilder ATR
# input.  Twenty-one calendar days safely spans those ten sessions plus normal
# FX weekends/holidays, so a replay window begins with the same ATR state as a
# continuously running production evaluator.
WARMUP_DAYS = 21
TERMINAL_STATES = {"COMPLETED", "PARTIAL", "FAILED", "QUARANTINED"}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("replay timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    display_start: datetime
    display_end: datetime
    instrument: str = INSTRUMENT_ID
    provider: str = PROVIDER_ID
    timeframes: tuple[Timeframe, ...] = REPLAY_TIMEFRAMES
    context_timeframe: Timeframe = CONTEXT_TIMEFRAME
    requested_dataset_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "display_start", _utc(self.display_start))
        object.__setattr__(self, "display_end", _utc(self.display_end))
        if self.display_start >= self.display_end:
            raise ValueError("display_start must be before display_end")
        if self.display_end - self.display_start > timedelta(days=31):
            raise ValueError("historical replay is bounded to 31 days")
        if self.instrument != INSTRUMENT_ID or self.provider != PROVIDER_ID:
            raise ValueError("historical replay supports Twelve Data EUR/USD")
        if self.timeframes != REPLAY_TIMEFRAMES:
            raise ValueError("historical replay requires ordered H1 and H4")
        if self.context_timeframe is not Timeframe.D1:
            raise ValueError("historical replay requires D1 context")
        if self.requested_dataset_fingerprint is not None:
            fingerprint = self.requested_dataset_fingerprint
            if len(fingerprint) != 64 or any(
                character not in "0123456789abcdef" for character in fingerprint
            ):
                raise ValueError("dataset fingerprint must be lowercase SHA-256")

    @property
    def warmup_start(self) -> datetime:
        return self.display_start - timedelta(days=WARMUP_DAYS)

    @property
    def config_key(self) -> str:
        payload = {
            "instrument": self.instrument,
            "provider": self.provider,
            "display_start": _iso(self.display_start),
            "display_end": _iso(self.display_end),
            "timeframes": [item.value for item in self.timeframes],
            "context_timeframe": self.context_timeframe.value,
            "requested_dataset_fingerprint": (
                self.requested_dataset_fingerprint
            ),
            "strategy_version": SPECIFICATION_ID,
        }
        return hashlib.sha256(_json(payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class QualityFinding:
    code: str
    timeframe: Timeframe
    start_utc: datetime | None
    end_utc: datetime | None
    detail: str


@dataclass(frozen=True, slots=True)
class HistoricalDataset:
    fingerprint: str
    instrument: CanonicalInstrument
    config: ReplayConfig
    bars: Mapping[Timeframe, tuple[Bar, ...]]
    requested_ranges: Mapping[str, Mapping[str, str]]
    returned_ranges: Mapping[str, Mapping[str, str | None]]
    candle_counts: Mapping[str, Mapping[str, int]]
    findings: tuple[QualityFinding, ...]


class HistoricalDataSource(Protocol):
    def load(self, config: ReplayConfig) -> HistoricalDataset: ...


class ReplayConflictError(RuntimeError):
    pass


class ReplayNotFoundError(KeyError):
    pass


class ReplayUnavailableError(RuntimeError):
    pass


def _instrument_payload(instrument: CanonicalInstrument) -> dict[str, Any]:
    return {
        "instrument_id": instrument.instrument_id,
        "provider_id": instrument.provider_id,
        "provider_symbol": instrument.provider_symbol,
        "display_name": instrument.display_name,
        "asset_class": instrument.asset_class,
        "point_size": str(instrument.point_size),
        "tick_size": (
            str(instrument.tick_size) if instrument.tick_size is not None else None
        ),
        "price_precision": instrument.price_precision,
        "tick_value_usd": (
            str(instrument.tick_value_usd)
            if instrument.tick_value_usd is not None
            else None
        ),
        "conversion_rate_to_usd": (
            str(instrument.conversion_rate_to_usd)
            if instrument.conversion_rate_to_usd is not None
            else None
        ),
        "contract_min": (
            str(instrument.contract_min)
            if instrument.contract_min is not None
            else None
        ),
        "contract_max": (
            str(instrument.contract_max)
            if instrument.contract_max is not None
            else None
        ),
        "contract_step": (
            str(instrument.contract_step)
            if instrument.contract_step is not None
            else None
        ),
        "minimum_stop_distance_points": (
            str(instrument.minimum_stop_distance_points)
            if instrument.minimum_stop_distance_points is not None
            else None
        ),
        "quote_currency": instrument.quote_currency,
        "profit_currency": instrument.profit_currency,
        "session_timezone": instrument.session_timezone,
        "candle_boundary_convention": instrument.candle_boundary_convention,
        "available_timeframes": [
            timeframe.value for timeframe in instrument.available_timeframes
        ],
        "strategy_id": instrument.strategy_id,
        "synthetic": instrument.synthetic,
    }


def _instrument_from_payload(value: Mapping[str, Any]) -> CanonicalInstrument:
    def decimal(name: str) -> Decimal | None:
        child = value.get(name)
        return Decimal(str(child)) if child is not None else None

    return CanonicalInstrument(
        instrument_id=str(value["instrument_id"]),
        provider_id=str(value["provider_id"]),
        provider_symbol=str(value["provider_symbol"]),
        display_name=str(value["display_name"]),
        asset_class=str(value["asset_class"]),
        point_size=Decimal(str(value["point_size"])),
        tick_size=decimal("tick_size"),
        price_precision=int(value["price_precision"]),
        tick_value_usd=decimal("tick_value_usd"),
        conversion_rate_to_usd=decimal("conversion_rate_to_usd"),
        contract_min=decimal("contract_min"),
        contract_max=decimal("contract_max"),
        contract_step=decimal("contract_step"),
        minimum_stop_distance_points=decimal(
            "minimum_stop_distance_points"
        ),
        quote_currency=str(value["quote_currency"]),
        profit_currency=str(value["profit_currency"]),
        session_timezone=str(value["session_timezone"]),
        candle_boundary_convention=str(value["candle_boundary_convention"]),
        available_timeframes=tuple(
            Timeframe(item) for item in value["available_timeframes"]
        ),
        strategy_id=str(value["strategy_id"]),
        synthetic=bool(value["synthetic"]),
    )


def _bar_from_payload(value: Mapping[str, Any]) -> Bar:
    return Bar(
        instrument_id=str(value["instrument_id"]),
        timeframe=Timeframe(value["timeframe"]),
        open_time=_parse(str(value["open_time"])),
        close_time=_parse(str(value["close_time"])),
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


def build_historical_dataset(
    config: ReplayConfig,
    instrument: CanonicalInstrument,
    raw_by_timeframe: Mapping[Timeframe, tuple[RawProviderCandle, ...]],
) -> HistoricalDataset:
    normalizer = CandleNormalizer()
    findings: list[QualityFinding] = []
    accepted: dict[Timeframe, tuple[Bar, ...]] = {}
    normalized_sources: dict[Timeframe, tuple[Bar, ...]] = {}
    counts: dict[str, dict[str, int]] = {}
    requested: dict[str, dict[str, str]] = {}
    returned: dict[str, dict[str, str | None]] = {}

    for timeframe in REPLAY_TIMEFRAMES:
        raw_values = raw_by_timeframe.get(timeframe, ())
        normalized: list[Bar] = []
        malformed = 0
        for raw in raw_values:
            result = normalizer.normalize(raw, instrument)
            if result.candle is None:
                malformed += 1
                findings.append(
                    QualityFinding(
                        code="MALFORMED_CANDLE",
                        timeframe=timeframe,
                        start_utc=None,
                        end_utc=None,
                        detail=",".join(result.issues),
                    )
                )
            else:
                if result.candle.close_time < config.display_end:
                    normalized.append(result.candle)
        normalized.sort(key=lambda bar: (bar.close_time, bar.open_time))
        unique: list[Bar] = []
        seen: set[datetime] = set()
        duplicates = 0
        for bar in normalized:
            if bar.close_time in seen:
                duplicates += 1
                findings.append(
                    QualityFinding(
                        code="DUPLICATE_CANDLE",
                        timeframe=timeframe,
                        start_utc=bar.close_time,
                        end_utc=bar.close_time,
                        detail="Duplicate close time was excluded.",
                    )
                )
                continue
            seen.add(bar.close_time)
            unique.append(bar)
        gaps = 0
        step = TIMEFRAME_STEP[timeframe]
        for previous, current in zip(unique, unique[1:]):
            if (
                current.open_time - previous.open_time != step
                and not is_expected_forex_weekend_gap(
                    previous.close_time,
                    current.open_time,
                )
            ):
                gaps += 1
                findings.append(
                    QualityFinding(
                        code="MISSING_CANDLE",
                        timeframe=timeframe,
                        start_utc=previous.close_time,
                        end_utc=current.open_time,
                        detail="Unexpected provider interval gap.",
                    )
                )
        normalized_sources[timeframe] = tuple(unique)
        accepted[timeframe] = tuple(
            bar for bar in unique if bar.close_time >= config.warmup_start
        )
        requested[timeframe.value] = {
            "start_utc": _iso(config.warmup_start),
            "end_utc_exclusive": _iso(config.display_end),
        }
        returned[timeframe.value] = {
            "first_close_utc": (
                _iso(accepted[timeframe][0].close_time)
                if accepted[timeframe]
                else None
            ),
            "last_close_utc": (
                _iso(accepted[timeframe][-1].close_time)
                if accepted[timeframe]
                else None
            ),
        }
        counts[timeframe.value] = {
            "received": len(raw_values),
            "accepted": len(accepted[timeframe]),
            "duplicates": duplicates,
            "malformed": malformed,
            "gaps": gaps,
            "warmup": sum(
                bar.close_time < config.display_start
                for bar in accepted[timeframe]
            ),
            "display": sum(
                config.display_start <= bar.close_time < config.display_end
                for bar in accepted[timeframe]
            ),
        }

    aggregation = NewYorkDailyAggregator().aggregate(
        normalized_sources.get(Timeframe.H1, ()),
        as_of=config.display_end,
    )
    for issue in aggregation.issues:
        findings.append(
            QualityFinding(
                code=issue.code.value,
                timeframe=Timeframe.D1,
                start_utc=issue.session_start,
                end_utc=issue.session_end,
                detail=issue.detail,
            )
        )
    accepted[Timeframe.D1] = tuple(
        bar
        for bar in aggregation.bars
        if config.warmup_start <= bar.close_time < config.display_end
    )
    requested[Timeframe.D1.value] = {
        "start_utc": _iso(config.warmup_start),
        "end_utc_exclusive": _iso(config.display_end),
    }
    returned[Timeframe.D1.value] = {
        "first_close_utc": (
            _iso(accepted[Timeframe.D1][0].close_time)
            if accepted[Timeframe.D1]
            else None
        ),
        "last_close_utc": (
            _iso(accepted[Timeframe.D1][-1].close_time)
            if accepted[Timeframe.D1]
            else None
        ),
    }
    counts[Timeframe.D1.value] = {
        "received": len(normalized_sources.get(Timeframe.H1, ())),
        "accepted": len(accepted[Timeframe.D1]),
        "duplicates": 0,
        "malformed": 0,
        "gaps": len(aggregation.issues),
        "warmup": sum(
            bar.close_time < config.display_start
            for bar in accepted[Timeframe.D1]
        ),
        "display": sum(
            config.display_start <= bar.close_time < config.display_end
            for bar in accepted[Timeframe.D1]
        ),
    }

    fingerprint_payload = {
        "instrument": _instrument_payload(instrument),
        "provider": config.provider,
        "display_start": _iso(config.display_start),
        "display_end": _iso(config.display_end),
        "warmup_start": _iso(config.warmup_start),
        "timeframes": [item.value for item in config.timeframes],
        "context_timeframe": config.context_timeframe.value,
        "strategy_version": SPECIFICATION_ID,
        "warmup": {
            "signal_bars": MIN_SIGNAL_HISTORY,
            "daily_bars": MIN_DAILY_HISTORY,
        },
        "candles": {
            timeframe.value: [primitive(bar) for bar in accepted[timeframe]]
            for timeframe in (*REPLAY_TIMEFRAMES, CONTEXT_TIMEFRAME)
        },
    }
    fingerprint = hashlib.sha256(
        _json(fingerprint_payload).encode()
    ).hexdigest()
    return HistoricalDataset(
        fingerprint=fingerprint,
        instrument=instrument,
        config=config,
        bars=accepted,
        requested_ranges=requested,
        returned_ranges=returned,
        candle_counts=counts,
        findings=tuple(findings),
    )


class TwelveDataHistoricalSource:
    def __init__(self, provider: TwelveDataProvider) -> None:
        self._provider = provider
        self._instrument = provider.discover_instruments()[0]

    def load(self, config: ReplayConfig) -> HistoricalDataset:
        raw = {
            timeframe: self._provider.fetch_historical_bars(
                timeframe,
                (
                    config.warmup_start - timedelta(days=2)
                    if timeframe is Timeframe.H1
                    else config.warmup_start
                ),
                config.display_end,
            )
            for timeframe in REPLAY_TIMEFRAMES
        }
        return build_historical_dataset(config, self._instrument, raw)


class HistoricalReplayRepository:
    """Replay-only storage. It must never point at the live projection DB."""

    def __init__(self, database_path: Path, live_database_path: Path) -> None:
        if database_path.resolve() == live_database_path.resolve():
            raise ValueError("replay and live databases must be different")
        self.database_path = database_path
        self.live_database_path = live_database_path
        self._lock = RLock()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS replay_datasets (
                    fingerprint TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    display_start TEXT NOT NULL,
                    display_end TEXT NOT NULL,
                    warmup_start TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    instrument_json TEXT NOT NULL,
                    requested_ranges_json TEXT NOT NULL,
                    returned_ranges_json TEXT NOT NULL,
                    candle_counts_json TEXT NOT NULL,
                    quality_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS replay_candles (
                    dataset_fingerprint TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    close_time_utc TEXT NOT NULL,
                    bar_json TEXT NOT NULL,
                    PRIMARY KEY (
                        dataset_fingerprint, timeframe, close_time_utc
                    ),
                    FOREIGN KEY (dataset_fingerprint)
                        REFERENCES replay_datasets(fingerprint)
                        ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS replay_runs (
                    run_id TEXT PRIMARY KEY,
                    config_key TEXT NOT NULL,
                    requested_dataset_fingerprint TEXT,
                    dataset_fingerprint TEXT,
                    provider TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    display_start TEXT NOT NULL,
                    display_end TEXT NOT NULL,
                    timeframes_json TEXT NOT NULL,
                    context_timeframe TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    progress_completed INTEGER NOT NULL DEFAULT 0,
                    duplicate_evaluations INTEGER NOT NULL DEFAULT 0,
                    quarantined_windows INTEGER NOT NULL DEFAULT 0,
                    determinism_digest TEXT,
                    error_code TEXT,
                    error_detail TEXT,
                    orders INTEGER NOT NULL DEFAULT 0 CHECK (orders = 0),
                    fills INTEGER NOT NULL DEFAULT 0 CHECK (fills = 0),
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY (dataset_fingerprint)
                        REFERENCES replay_datasets(fingerprint)
                        ON DELETE RESTRICT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS replay_active_config
                ON replay_runs(config_key)
                WHERE status IN ('PENDING', 'RUNNING');

                CREATE TABLE IF NOT EXISTS replay_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    signal_close_utc TEXT NOT NULL,
                    replay_as_of_utc TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    filter_outcome TEXT NOT NULL,
                    signal_outcome TEXT NOT NULL,
                    dashboard_state TEXT NOT NULL,
                    d1_context_close_utc TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    market_values_json TEXT NOT NULL,
                    status_json TEXT NOT NULL,
                    evaluation_json TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    UNIQUE (run_id, ordinal),
                    UNIQUE (run_id, idempotency_key),
                    FOREIGN KEY (run_id) REFERENCES replay_runs(run_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS replay_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    evaluation_id INTEGER NOT NULL,
                    event_ordinal INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE (run_id, event_ordinal),
                    UNIQUE (evaluation_id, sequence),
                    FOREIGN KEY (run_id) REFERENCES replay_runs(run_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (evaluation_id)
                        REFERENCES replay_evaluations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS replay_quality_findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    start_utc TEXT,
                    end_utc TEXT,
                    detail TEXT NOT NULL,
                    UNIQUE (
                        run_id, code, timeframe, start_utc, end_utc, detail
                    ),
                    FOREIGN KEY (run_id) REFERENCES replay_runs(run_id)
                        ON DELETE CASCADE
                );
                """
            )
            connection.commit()

    def create_run(self, config: ReplayConfig) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        created = _iso(datetime.now(timezone.utc))
        try:
            with self._lock, closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO replay_runs (
                        run_id, config_key, requested_dataset_fingerprint,
                        provider, instrument, display_start, display_end,
                        timeframes_json, context_timeframe, strategy_version,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                    """,
                    (
                        run_id,
                        config.config_key,
                        config.requested_dataset_fingerprint,
                        config.provider,
                        config.instrument,
                        _iso(config.display_start),
                        _iso(config.display_end),
                        _json([item.value for item in config.timeframes]),
                        config.context_timeframe.value,
                        SPECIFICATION_ID,
                        created,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise ReplayConflictError(
                "An identical historical replay is already active."
            ) from error
        return self.get_run(run_id)

    def start_run(self, run_id: str) -> bool:
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE replay_runs
                SET status = 'RUNNING', started_at = ?
                WHERE run_id = ? AND status = 'PENDING'
                """,
                (_iso(datetime.now(timezone.utc)), run_id),
            )
            connection.commit()
            return cursor.rowcount == 1

    def store_dataset(self, dataset: HistoricalDataset) -> None:
        quality = [
            {
                "code": finding.code,
                "timeframe": finding.timeframe.value,
                "start_utc": (
                    _iso(finding.start_utc) if finding.start_utc else None
                ),
                "end_utc": _iso(finding.end_utc) if finding.end_utc else None,
                "detail": finding.detail,
            }
            for finding in dataset.findings
        ]
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO replay_datasets (
                    fingerprint, provider, instrument, display_start,
                    display_end, warmup_start, strategy_version,
                    instrument_json, requested_ranges_json,
                    returned_ranges_json, candle_counts_json, quality_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset.fingerprint,
                    dataset.config.provider,
                    dataset.config.instrument,
                    _iso(dataset.config.display_start),
                    _iso(dataset.config.display_end),
                    _iso(dataset.config.warmup_start),
                    SPECIFICATION_ID,
                    _json(_instrument_payload(dataset.instrument)),
                    _json(dataset.requested_ranges),
                    _json(dataset.returned_ranges),
                    _json(dataset.candle_counts),
                    _json(quality),
                    _iso(datetime.now(timezone.utc)),
                ),
            )
            existing_count = connection.execute(
                """
                SELECT COUNT(*) AS count FROM replay_candles
                WHERE dataset_fingerprint = ?
                """,
                (dataset.fingerprint,),
            ).fetchone()["count"]
            expected_count = sum(len(values) for values in dataset.bars.values())
            if existing_count not in (0, expected_count):
                connection.rollback()
                raise ReplayConflictError("Cached dataset is not immutable.")
            if existing_count == 0:
                for timeframe in (*REPLAY_TIMEFRAMES, CONTEXT_TIMEFRAME):
                    for bar in dataset.bars[timeframe]:
                        connection.execute(
                            """
                            INSERT INTO replay_candles (
                                dataset_fingerprint, timeframe,
                                close_time_utc, bar_json
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (
                                dataset.fingerprint,
                                timeframe.value,
                                _iso(bar.close_time),
                                _json(primitive(bar)),
                            ),
                        )
            connection.commit()

    def load_dataset(
        self, fingerprint: str, config: ReplayConfig
    ) -> HistoricalDataset:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM replay_datasets WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if row is None:
                raise ReplayNotFoundError("Dataset was not found.")
            if (
                row["display_start"] != _iso(config.display_start)
                or row["display_end"] != _iso(config.display_end)
                or row["provider"] != config.provider
                or row["instrument"] != config.instrument
            ):
                raise ReplayConflictError(
                    "Cached dataset does not match replay configuration."
                )
            candle_rows = connection.execute(
                """
                SELECT timeframe, bar_json FROM replay_candles
                WHERE dataset_fingerprint = ?
                ORDER BY close_time_utc, timeframe
                """,
                (fingerprint,),
            ).fetchall()
        bars: dict[Timeframe, list[Bar]] = {
            Timeframe.H1: [],
            Timeframe.H4: [],
            Timeframe.D1: [],
        }
        for candle_row in candle_rows:
            timeframe = Timeframe(candle_row["timeframe"])
            bars[timeframe].append(
                _bar_from_payload(json.loads(candle_row["bar_json"]))
            )
        findings = tuple(
            QualityFinding(
                code=item["code"],
                timeframe=Timeframe(item["timeframe"]),
                start_utc=(
                    _parse(item["start_utc"]) if item["start_utc"] else None
                ),
                end_utc=_parse(item["end_utc"]) if item["end_utc"] else None,
                detail=item["detail"],
            )
            for item in json.loads(row["quality_json"])
        )
        return HistoricalDataset(
            fingerprint=fingerprint,
            instrument=_instrument_from_payload(
                json.loads(row["instrument_json"])
            ),
            config=config,
            bars={key: tuple(value) for key, value in bars.items()},
            requested_ranges=json.loads(row["requested_ranges_json"]),
            returned_ranges=json.loads(row["returned_ranges_json"]),
            candle_counts=json.loads(row["candle_counts_json"]),
            findings=findings,
        )

    def attach_dataset(self, run_id: str, dataset: HistoricalDataset) -> None:
        total = sum(
            1
            for timeframe in REPLAY_TIMEFRAMES
            for bar in dataset.bars[timeframe]
            if dataset.config.display_start
            <= bar.close_time
            < dataset.config.display_end
        )
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE replay_runs
                SET dataset_fingerprint = ?, progress_total = ?
                WHERE run_id = ? AND status = 'RUNNING'
                """,
                (dataset.fingerprint, total, run_id),
            )
            for finding in dataset.findings:
                self._insert_finding(connection, run_id, finding)
            connection.commit()

    def persist_finding(
        self, run_id: str, finding: QualityFinding
    ) -> None:
        with self._lock, closing(self._connect()) as connection:
            self._insert_finding(connection, run_id, finding)
            connection.execute(
                """
                UPDATE replay_runs
                SET quarantined_windows = quarantined_windows + 1
                WHERE run_id = ?
                """,
                (run_id,),
            )
            connection.commit()

    @staticmethod
    def _insert_finding(
        connection: sqlite3.Connection,
        run_id: str,
        finding: QualityFinding,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO replay_quality_findings (
                run_id, code, timeframe, start_utc, end_utc, detail
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                finding.code,
                finding.timeframe.value,
                _iso(finding.start_utc) if finding.start_utc else None,
                _iso(finding.end_utc) if finding.end_utc else None,
                finding.detail,
            ),
        )

    def persist_evaluation(
        self,
        run_id: str,
        ordinal: int,
        projection: EvaluatedProjection,
        events: tuple[DomainEvent, ...],
        input_payload: Mapping[str, Any],
    ) -> bool:
        status = primitive(projection.status)
        evaluation = primitive(projection.evaluation)
        classification = projection.evaluation.classification
        assert classification is not None
        filter_outcome = (
            "PASS"
            if classification.buy_filter_matched
            or classification.sell_filter_matched
            else "FAIL"
        )
        signal_outcome = (
            "SIGNAL"
            if classification.confirmed_buy or classification.confirmed_sell
            else "NO_SIGNAL"
        )
        d1_close = projection.evaluation.bars.daily_endpoint_close_time
        assert d1_close is not None
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO replay_evaluations (
                        run_id, ordinal, idempotency_key, signal_close_utc,
                        replay_as_of_utc, timeframe, filter_outcome,
                        signal_outcome, dashboard_state,
                        d1_context_close_utc, reason_codes_json,
                        market_values_json, status_json, evaluation_json,
                        input_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        ordinal,
                        projection.status.idempotency_key,
                        _iso(projection.status.signal_bar_close_time),
                        _iso(projection.status.last_update),
                        projection.status.timeframe.value,
                        filter_outcome,
                        signal_outcome,
                        projection.status.dashboard_state,
                        _iso(d1_close),
                        _json(list(projection.status.reason_codes)),
                        _json(status["market_values"]),
                        _json(status),
                        _json(evaluation),
                        _json(input_payload),
                    ),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                connection.execute(
                    """
                    UPDATE replay_runs
                    SET duplicate_evaluations = duplicate_evaluations + 1
                    WHERE run_id = ?
                    """,
                    (run_id,),
                )
                connection.commit()
                return False
            evaluation_id = int(cursor.lastrowid)
            for event in events:
                value = primitive(event)
                connection.execute(
                    """
                    INSERT INTO replay_events (
                        run_id, evaluation_id, event_ordinal, sequence,
                        event_type, occurred_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        evaluation_id,
                        ordinal * 10 + event.sequence,
                        event.sequence,
                        event.event_type.value,
                        value["occurred_at"],
                        _json(value["payload"]),
                    ),
                )
            connection.commit()
            return True

    def update_progress(self, run_id: str, completed: int) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE replay_runs SET progress_completed = ?
                WHERE run_id = ? AND status = 'RUNNING'
                """,
                (completed, run_id),
            )
            connection.commit()

    def finish_run(self, run_id: str) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            evaluation_count = connection.execute(
                "SELECT COUNT(*) AS count FROM replay_evaluations WHERE run_id = ?",
                (run_id,),
            ).fetchone()["count"]
            row = connection.execute(
                "SELECT quarantined_windows FROM replay_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ReplayNotFoundError(run_id)
            quarantined = int(row["quarantined_windows"])
            status = (
                "COMPLETED"
                if quarantined == 0
                else "PARTIAL"
                if evaluation_count > 0
                else "QUARANTINED"
            )
            digest = self._determinism_digest(connection, run_id)
            connection.execute(
                """
                UPDATE replay_runs
                SET status = ?, determinism_digest = ?, completed_at = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    digest,
                    _iso(datetime.now(timezone.utc)),
                    run_id,
                ),
            )
            connection.commit()
        return self.get_run(run_id)

    def fail_run(self, run_id: str, code: str, detail: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE replay_runs
                SET status = 'FAILED', error_code = ?, error_detail = ?,
                    completed_at = ?
                WHERE run_id = ?
                """,
                (code, detail, _iso(datetime.now(timezone.utc)), run_id),
            )
            connection.commit()

    @staticmethod
    def _determinism_digest(
        connection: sqlite3.Connection, run_id: str
    ) -> str:
        evaluations = [
            dict(row)
            for row in connection.execute(
                """
                SELECT ordinal, signal_close_utc, replay_as_of_utc, timeframe,
                       filter_outcome, signal_outcome, dashboard_state,
                       d1_context_close_utc, reason_codes_json,
                       market_values_json
                FROM replay_evaluations WHERE run_id = ? ORDER BY ordinal
                """,
                (run_id,),
            ).fetchall()
        ]
        events = [
            dict(row)
            for row in connection.execute(
                """
                SELECT event_ordinal, sequence, event_type, occurred_at,
                       payload_json
                FROM replay_events WHERE run_id = ? ORDER BY event_ordinal
                """,
                (run_id,),
            ).fetchall()
        ]
        return hashlib.sha256(
            _json({"evaluations": evaluations, "events": events}).encode()
        ).hexdigest()

    def get_run(self, run_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM replay_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ReplayNotFoundError(run_id)
        return self._run_view(row)

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM replay_runs ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._run_view(row) for row in rows]

    @staticmethod
    def _run_view(row: sqlite3.Row) -> dict[str, Any]:
        total = int(row["progress_total"])
        completed = int(row["progress_completed"])
        return {
            "run_id": row["run_id"],
            "dataset_fingerprint": row["dataset_fingerprint"],
            "requested_dataset_fingerprint": row[
                "requested_dataset_fingerprint"
            ],
            "provider": row["provider"],
            "instrument": row["instrument"],
            "display_start": row["display_start"],
            "display_end": row["display_end"],
            "timeframes": json.loads(row["timeframes_json"]),
            "context_timeframe": row["context_timeframe"],
            "strategy_version": row["strategy_version"],
            "status": row["status"],
            "progress": {
                "total": total,
                "completed": completed,
                "percent": round(completed * 100 / total, 2) if total else 0.0,
            },
            "duplicate_evaluations": int(row["duplicate_evaluations"]),
            "quarantined_windows": int(row["quarantined_windows"]),
            "determinism_digest": row["determinism_digest"],
            "error": (
                {"code": row["error_code"], "detail": row["error_detail"]}
                if row["error_code"]
                else None
            ),
            "orders": int(row["orders"]),
            "fills": int(row["fills"]),
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }

    def summary(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        with closing(self._connect()) as connection:
            dataset = (
                connection.execute(
                    "SELECT * FROM replay_datasets WHERE fingerprint = ?",
                    (run["dataset_fingerprint"],),
                ).fetchone()
                if run["dataset_fingerprint"]
                else None
            )
            rows = connection.execute(
                """
                SELECT timeframe, filter_outcome, signal_outcome,
                       reason_codes_json
                FROM replay_evaluations WHERE run_id = ?
                """,
                (run_id,),
            ).fetchall()
            findings = connection.execute(
                """
                SELECT code, timeframe, start_utc, end_utc, detail
                FROM replay_quality_findings WHERE run_id = ?
                ORDER BY timeframe, start_utc, code
                """,
                (run_id,),
            ).fetchall()
            event_count = connection.execute(
                "SELECT COUNT(*) AS count FROM replay_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()["count"]
        reason_counts: dict[str, int] = {}
        for row in rows:
            for reason in json.loads(row["reason_codes_json"]):
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        evaluation_counts = {
            "total": len(rows),
            "H1": sum(row["timeframe"] == "H1" for row in rows),
            "H4": sum(row["timeframe"] == "H4" for row in rows),
            "filter_pass": sum(row["filter_outcome"] == "PASS" for row in rows),
            "filter_fail": sum(row["filter_outcome"] == "FAIL" for row in rows),
            "signal": sum(row["signal_outcome"] == "SIGNAL" for row in rows),
            "no_signal": sum(
                row["signal_outcome"] == "NO_SIGNAL" for row in rows
            ),
        }
        return {
            "run": run,
            "dataset": (
                {
                    "fingerprint": dataset["fingerprint"],
                    "warmup_start": dataset["warmup_start"],
                    "requested_ranges": json.loads(
                        dataset["requested_ranges_json"]
                    ),
                    "returned_ranges": json.loads(
                        dataset["returned_ranges_json"]
                    ),
                    "candle_counts": json.loads(
                        dataset["candle_counts_json"]
                    ),
                }
                if dataset is not None
                else None
            ),
            "evaluation_counts": evaluation_counts,
            "reason_counts": dict(sorted(reason_counts.items())),
            "event_count": int(event_count),
            "data_quality": [dict(row) for row in findings],
            "execution": {
                "enabled": False,
                "orders": run["orders"],
                "fills": run["fills"],
                "detail": "Functional replay only; execution is disabled.",
            },
        }

    def evaluations(
        self,
        run_id: str,
        *,
        page: int,
        page_size: int,
        timeframe: str | None = None,
        outcome: str | None = None,
        filter_outcome: str | None = None,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        self.get_run(run_id)
        clauses = ["run_id = ?"]
        values: list[Any] = [run_id]
        if timeframe:
            clauses.append("timeframe = ?")
            values.append(timeframe)
        if outcome:
            clauses.append("signal_outcome = ?")
            values.append(outcome)
        if filter_outcome:
            clauses.append("filter_outcome = ?")
            values.append(filter_outcome)
        if reason_code:
            clauses.append("reason_codes_json LIKE ?")
            values.append(f'%"{reason_code}"%')
        where = " AND ".join(clauses)
        with closing(self._connect()) as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS count FROM replay_evaluations WHERE {where}",
                values,
            ).fetchone()["count"]
            rows = connection.execute(
                f"""
                SELECT id, ordinal, signal_close_utc, replay_as_of_utc,
                       timeframe, filter_outcome, signal_outcome,
                       dashboard_state, d1_context_close_utc,
                       reason_codes_json, market_values_json
                FROM replay_evaluations WHERE {where}
                ORDER BY ordinal LIMIT ? OFFSET ?
                """,
                (*values, page_size, (page - 1) * page_size),
            ).fetchall()
        return {
            "items": [
                {
                    "id": row["id"],
                    "ordinal": row["ordinal"],
                    "signal_close_utc": row["signal_close_utc"],
                    "replay_as_of_utc": row["replay_as_of_utc"],
                    "timeframe": row["timeframe"],
                    "filter_outcome": row["filter_outcome"],
                    "signal_outcome": row["signal_outcome"],
                    "dashboard_state": row["dashboard_state"],
                    "d1_context_close_utc": row[
                        "d1_context_close_utc"
                    ],
                    "reason_codes": json.loads(row["reason_codes_json"]),
                    "market_values": json.loads(row["market_values_json"]),
                }
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": int(total),
            "pages": max(1, (int(total) + page_size - 1) // page_size),
        }

    def evaluation(self, run_id: str, evaluation_id: int) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM replay_evaluations
                WHERE run_id = ? AND id = ?
                """,
                (run_id, evaluation_id),
            ).fetchone()
            if row is None:
                raise ReplayNotFoundError(str(evaluation_id))
            events = connection.execute(
                """
                SELECT sequence, event_type, occurred_at, payload_json
                FROM replay_events WHERE evaluation_id = ? ORDER BY sequence
                """,
                (evaluation_id,),
            ).fetchall()
        return {
            "id": row["id"],
            "ordinal": row["ordinal"],
            "signal_close_utc": row["signal_close_utc"],
            "replay_as_of_utc": row["replay_as_of_utc"],
            "timeframe": row["timeframe"],
            "filter_outcome": row["filter_outcome"],
            "signal_outcome": row["signal_outcome"],
            "dashboard_state": row["dashboard_state"],
            "d1_context_close_utc": row["d1_context_close_utc"],
            "reason_codes": json.loads(row["reason_codes_json"]),
            "market_values": json.loads(row["market_values_json"]),
            "status": json.loads(row["status_json"]),
            "evaluation": json.loads(row["evaluation_json"]),
            "input": json.loads(row["input_json"]),
            "events": [
                {
                    "sequence": event["sequence"],
                    "event_type": event["event_type"],
                    "occurred_at": event["occurred_at"],
                    "payload": json.loads(event["payload_json"]),
                }
                for event in events
            ],
        }

    def delete_run(self, run_id: str) -> bool:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT status FROM replay_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return False
            if row["status"] in {"PENDING", "RUNNING"}:
                raise ReplayConflictError("An active replay cannot be deleted.")
            connection.execute("DELETE FROM replay_runs WHERE run_id = ?", (run_id,))
            connection.commit()
            return True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class HistoricalReplayService:
    def __init__(
        self,
        repository: HistoricalReplayRepository,
        source: HistoricalDataSource | None,
    ) -> None:
        self.repository = repository
        self.source = source
        self._detector = ClosedBarDetector()
        self._clock = FixedClock(datetime(2000, 1, 1, tzinfo=timezone.utc))
        projection_repository = SQLiteProjectionRepository(
            repository.database_path.with_suffix(".projection-unused.sqlite3")
        )
        self._projection_service = WalkingSkeletonService(
            Spect8StrategyEvaluator(), None, projection_repository
        )

    def create_run(self, config: ReplayConfig) -> dict[str, Any]:
        return self.repository.create_run(config)

    def execute(self, run_id: str) -> None:
        if not self.repository.start_run(run_id):
            return
        run = self.repository.get_run(run_id)
        config = ReplayConfig(
            display_start=_parse(run["display_start"]),
            display_end=_parse(run["display_end"]),
            requested_dataset_fingerprint=run[
                "requested_dataset_fingerprint"
            ],
        )
        try:
            if config.requested_dataset_fingerprint:
                dataset = self.repository.load_dataset(
                    config.requested_dataset_fingerprint, config
                )
            else:
                if self.source is None:
                    raise ReplayUnavailableError(
                        "Historical Twelve Data source is unavailable."
                    )
                dataset = self.source.load(config)
                self.repository.store_dataset(dataset)
            self.repository.attach_dataset(run_id, dataset)
            triggers = sorted(
                (
                    bar
                    for timeframe in REPLAY_TIMEFRAMES
                    for bar in dataset.bars[timeframe]
                    if config.display_start
                    <= bar.close_time
                    < config.display_end
                ),
                key=lambda bar: (
                    bar.close_time,
                    REPLAY_TIMEFRAMES.index(bar.timeframe),
                ),
            )
            completed = 0
            for ordinal, trigger in enumerate(triggers, start=1):
                replay_as_of = trigger.close_time + timedelta(microseconds=1)
                self._clock.set(replay_as_of)
                signal_bars = tuple(
                    bar
                    for bar in dataset.bars[trigger.timeframe]
                    if bar.close_time < self._clock.now()
                )[-MIN_SIGNAL_HISTORY:]
                daily_bars = tuple(
                    bar
                    for bar in dataset.bars[Timeframe.D1]
                    if bar.close_time <= trigger.close_time
                )[-DAILY_ATR_INPUT_HISTORY:]
                validation = self._detector.validate_history(
                    signal_bars,
                    daily_bars,
                    trigger.timeframe,
                    trigger.close_time,
                )
                if validation.issues:
                    self.repository.persist_finding(
                        run_id,
                        QualityFinding(
                            code="QUARANTINED_WINDOW",
                            timeframe=trigger.timeframe,
                            start_utc=(
                                signal_bars[0].close_time
                                if signal_bars
                                else trigger.close_time
                            ),
                            end_utc=trigger.close_time,
                            detail=",".join(validation.issues),
                        ),
                    )
                else:
                    request = StrategyRequest(
                        case_id=(
                            f"historical:{dataset.fingerprint}:"
                            f"{trigger.timeframe.value}:{_iso(trigger.close_time)}"
                        ),
                        strategy_id=STRATEGY_ID,
                        timeframe=trigger.timeframe,
                        evaluation_time=self._clock.now(),
                        signal_bars=validation.signal_bars,
                        daily_bars=validation.daily_bars,
                        instrument=dataset.instrument.to_strategy_metadata(),
                    )
                    projection = self._projection_service.evaluate_request(request)
                    events = self._projection_service.events_for_projection(
                        projection
                    )
                    self.repository.persist_evaluation(
                        run_id,
                        ordinal,
                        projection,
                        events,
                        {
                            "replay_as_of_utc": _iso(self._clock.now()),
                            "signal_bars": primitive(validation.signal_bars),
                            "daily_bars": primitive(validation.daily_bars),
                        },
                    )
                completed += 1
                if completed % 25 == 0 or completed == len(triggers):
                    self.repository.update_progress(run_id, completed)
            self.repository.finish_run(run_id)
        except (ReplayConflictError, ReplayNotFoundError) as error:
            self.repository.fail_run(run_id, "REPLAY_DATASET_ERROR", str(error))
        except ReplayUnavailableError as error:
            self.repository.fail_run(run_id, "SOURCE_UNAVAILABLE", str(error))
        except Exception:
            self.repository.fail_run(
                run_id,
                "REPLAY_EXECUTION_FAILED",
                "Historical replay execution failed.",
            )
