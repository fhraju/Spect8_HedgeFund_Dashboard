"""Opt-in real PostgreSQL validation for the Phase 22A-3 shadow path."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.market_data.forex_profile import (
    AggregatedH4Bar,
    BrokerAlignedH4Aggregator,
    broker_utc_offset,
    broker_wall_time,
)
from backend.app.market_data.platform_adapter import (
    PlatformIncrementalProcessor,
    Spect8CanonicalReadServiceGateway,
    bid_bars,
    to_spect8_bar,
)
from backend.app.market_data.platform_parity import classify_provider_data_differences
from backend.app.market_data.platform_shadow import PlatformShadowRuntime
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService

DATABASE_URL = os.environ.get("MARKET_DATA_PLATFORM_DATABASE_URL")
SPECT8_INSTRUMENTS = tuple(
    item.strip()
    for item in os.environ.get(
        "SPECT8_PLATFORM_SHADOW_INSTRUMENTS", "EUR_USD,GBP_USD,USD_JPY"
    ).split(",")
    if item.strip()
)
PLATFORM_INSTRUMENTS = tuple(
    {"EUR_USD": "FX_EUR_USD", "GBP_USD": "FX_GBP_USD", "USD_JPY": "FX_USD_JPY"}[item]
    for item in SPECT8_INSTRUMENTS
)
TWELVE_COMPARISON_DATABASE = Path(
    os.environ.get(
        "SPECT8_TWELVE_COMPARISON_DATABASE",
        Path(__file__).resolve().parents[2] / "var" / "spect8_phase1.sqlite3",
    )
)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="MARKET_DATA_PLATFORM_DATABASE_URL is required for real shadow validation",
)


def test_real_postgresql_bootstrap_strategy_state_and_restart_replay(
    tmp_path: Path,
) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "platform-shadow.sqlite3")
    repository.initialize()
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository)
    runtime = PlatformShadowRuntime.from_database_url(
        DATABASE_URL or "", repository, service
    )
    as_of = datetime.now(timezone.utc)
    try:
        first = runtime.run_once(
            available_as_of=as_of, instrument_ids=SPECT8_INSTRUMENTS
        )
        second = runtime.run_once(
            available_as_of=as_of, instrument_ids=SPECT8_INSTRUMENTS
        )
    finally:
        runtime.close()

    assert first.bootstrap_counts == tuple(
        (instrument_id, 1, 1_177, 30, 10, 6) for instrument_id in SPECT8_INSTRUMENTS
    )
    assert len(first.evaluations) == 4 * len(SPECT8_INSTRUMENTS)
    assert all(not item.replayed for item in first.evaluations)
    assert all(item.replayed for item in second.evaluations)
    assert second.consumed == 0
    assert second.replayed_inputs == 0
    assert second.watermark_canonical_bar_id == first.watermark_canonical_bar_id
    assert repository.processed_count() == 4 * len(SPECT8_INSTRUMENTS)


def test_real_postgresql_watermark_failure_and_restart_are_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hedgefund_market_data.pipeline import PostgreSQLSpect8CanonicalReadService

    backend = PostgreSQLSpect8CanonicalReadService.from_database_url(DATABASE_URL or "")
    repository = SQLiteProjectionRepository(tmp_path / "watermark.sqlite3")
    repository.initialize()
    processor = PlatformIncrementalProcessor(
        Spect8CanonicalReadServiceGateway(backend), repository
    )
    as_of = datetime.now(timezone.utc)
    try:
        with pytest.raises(RuntimeError, match="strategy failure"):
            processor.process(
                (SPECT8_INSTRUMENTS[0],),
                available_as_of=as_of,
                process_bar=lambda _bar, _mapped: (_ for _ in ()).throw(
                    RuntimeError("strategy failure")
                ),
            )
        assert repository.platform_integration_state() is None

        callbacks: list[int] = []
        original = repository.advance_platform_watermark
        monkeypatch.setattr(
            repository,
            "advance_platform_watermark",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("checkpoint failure")),
        )
        with pytest.raises(RuntimeError, match="checkpoint failure"):
            processor.process(
                (SPECT8_INSTRUMENTS[0],),
                available_as_of=as_of,
                process_bar=lambda bar, _mapped: (
                    callbacks.append(bar.canonical_bar_id)
                    or f"shadow:{bar.canonical_bar_id}"
                ),
            )
        assert callbacks
        assert repository.platform_integration_state() is None

        monkeypatch.setattr(repository, "advance_platform_watermark", original)
        replay_callbacks: list[int] = []
        restarted = processor.process(
            (SPECT8_INSTRUMENTS[0],),
            available_as_of=as_of,
            process_bar=lambda bar, _mapped: (
                replay_callbacks.append(bar.canonical_bar_id) or "unexpected-duplicate"
            ),
        )
        assert replay_callbacks == []
        assert restarted.replayed == len(callbacks)
        assert restarted.new_watermark > 0
    finally:
        backend.close()


def test_real_postgresql_h1_builds_broker_aligned_h4() -> None:
    from hedgefund_market_data.pipeline import PostgreSQLSpect8CanonicalReadService

    backend = PostgreSQLSpect8CanonicalReadService.from_database_url(DATABASE_URL or "")
    gateway = Spect8CanonicalReadServiceGateway(backend)
    as_of = datetime.now(timezone.utc)
    try:
        batch = gateway.read(
            (PLATFORM_INSTRUMENTS[0],),
            available_as_of=as_of,
            after_canonical_bar_id=None,
            limits={"M30": 1, "H1": 5_000, "H4": 30, "D1": 10, "W1": 6},
        )
    finally:
        backend.close()

    platform_h4_ids = {
        bar.canonical_bar_id for bar in batch.bars if bar.timeframe == "H4"
    }
    h1 = tuple(
        to_spect8_bar(bar) for bar in bid_bars(batch.bars) if bar.timeframe == "H1"
    )
    result = BrokerAlignedH4Aggregator().aggregate(h1, as_of=as_of)
    buckets_by_offset: dict[int, list[AggregatedH4Bar]] = {2: [], 3: []}
    for bucket in result.buckets:
        offset = int(broker_utc_offset(bucket.bar.open_time).total_seconds() // 3_600)
        if offset in buckets_by_offset:
            buckets_by_offset[offset].append(bucket)
        members = bucket.source_bars
        assert len(members) == 4
        assert tuple(item.open_time for item in members) == tuple(
            bucket.bar.open_time + timedelta(hours=index) for index in range(4)
        )
        assert bucket.bar.open == members[0].open
        assert bucket.bar.high == max(item.high for item in members)
        assert bucket.bar.low == min(item.low for item in members)
        assert bucket.bar.close == members[-1].close
        assert bucket.bar.close_time == members[-1].close_time
        assert broker_wall_time(bucket.bar.open_time).hour in (0, 4, 8, 12, 16, 20)
        assert platform_h4_ids.isdisjoint(
            int(source_id.split(":", 2)[1])
            for source_id in bucket.bar.source_candle_ids
        )

    available_offsets = tuple(
        offset for offset, buckets in buckets_by_offset.items() if buckets
    )
    assert available_offsets
    assert set(available_offsets) <= {2, 3}
    if available_offsets == (2, 3):
        winter = buckets_by_offset[2][-1]
        summer = buckets_by_offset[3][0]
        assert winter.bar.open_time < summer.bar.open_time
        assert broker_utc_offset(winter.bar.open_time) == timedelta(hours=2)
        assert broker_utc_offset(summer.bar.open_time) == timedelta(hours=3)


@pytest.mark.skipif(
    not TWELVE_COMPARISON_DATABASE.exists(),
    reason="persisted Twelve Data comparison database is required",
)
def test_actual_twelve_and_platform_rows_are_compared_as_provider_differences() -> None:
    from hedgefund_market_data.pipeline import PostgreSQLSpect8CanonicalReadService

    backend = PostgreSQLSpect8CanonicalReadService.from_database_url(DATABASE_URL or "")
    gateway = Spect8CanonicalReadServiceGateway(backend)
    as_of = datetime.now(timezone.utc)
    try:
        batch = gateway.read(
            PLATFORM_INSTRUMENTS,
            available_as_of=as_of,
            after_canonical_bar_id=None,
            limits={"M30": 1, "H1": 5_000, "H4": 30, "D1": 100, "W1": 30},
        )
    finally:
        backend.close()

    old_repository = SQLiteProjectionRepository(TWELVE_COMPARISON_DATABASE)
    for instrument_id, platform_id in zip(
        SPECT8_INSTRUMENTS, PLATFORM_INSTRUMENTS, strict=True
    ):
        selected = tuple(
            bar for bar in bid_bars(batch.bars) if bar.instrument_id == platform_id
        )
        platform_h1 = tuple(
            to_spect8_bar(bar) for bar in selected if bar.timeframe == "H1"
        )
        platform_d1 = tuple(
            to_spect8_bar(bar) for bar in selected if bar.timeframe == "D1"
        )
        platform_h4 = (
            BrokerAlignedH4Aggregator().aggregate(platform_h1, as_of=as_of).bars
        )
        for timeframe, new_bars in (
            ("H1", platform_h1),
            ("H4", platform_h4),
            ("D1", platform_d1),
        ):
            old_bars = old_repository.canonical_bar_objects(
                "TWELVE_DATA", instrument_id, timeframe
            )
            common = {
                (bar.timeframe.value, bar.open_time, bar.close_time) for bar in old_bars
            } & {
                (bar.timeframe.value, bar.open_time, bar.close_time) for bar in new_bars
            }
            assert common, (
                f"no identical eligible {instrument_id} {timeframe} evaluation rows"
            )
            differences = classify_provider_data_differences(old_bars, new_bars)
            assert all(
                item.classification == "PROVIDER_DATA_DIFFERENCE"
                for item in differences
            )
