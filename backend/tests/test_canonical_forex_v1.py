from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from backend.app.domain import Timeframe
from backend.app.market_data.conformance import (
    FixtureProviderAdapter,
    ProviderCertificationEngine,
    fixture_instrument,
    fixture_sha256,
)
from backend.app.market_data.models import (
    ProviderProfile,
    TimestampSemantics,
)
from backend.app.market_data.forex_profile import market_h1_bars
from backend.app.market_data.forex_profile import BrokerAlignedH4Aggregator
from backend.app.market_data.daily_aggregator import NewYorkDailyAggregator
from backend.app.market_data.normalizer import CandleNormalizer
from backend.app.market_data.profiles.ic_markets_ny_close_forex_v1 import (
    PROFILE,
    PROFILE_ID,
)
from backend.app.repository import SQLiteProjectionRepository


FIXTURE = Path(__file__).parent / "fixtures" / "ic_markets_forex_v1" / "reference.json"
CHECKSUM = "e5ff9efbb98aaaf64840d717bca40e7aa41c8870d6bc039e51cfee5905a67699"
START = datetime(2026, 7, 30, tzinfo=timezone.utc)
END = datetime(2026, 8, 5, tzinfo=timezone.utc)


def test_versioned_profile_freezes_provider_independent_contract() -> None:
    assert PROFILE.profile_id == "IC_MARKETS_NY_CLOSE_FOREX_V1"
    assert PROFILE.canonical_timezone == "UTC"
    assert PROFILE.display_timezone == "IC Markets Broker Time"
    assert PROFILE.daily_session_timezone == "America/New_York"
    assert PROFILE.broker_standard_utc_offset_hours == 2
    assert PROFILE.broker_daylight_utc_offset_hours == 3
    assert PROFILE.h4_open_hours == (0, 4, 8, 12, 16, 20)
    assert PROFILE.native_h4_policy == "COMPARISON_ONLY"
    assert PROFILE.native_d1_policy == "COMPARISON_ONLY"
    assert PROFILE.forward_fill_policy == "FORBIDDEN"


def test_frozen_sanitized_fixture_checksum_and_metadata() -> None:
    assert fixture_sha256(FIXTURE) == CHECKSUM
    expected = FIXTURE.with_suffix(".sha256").read_text(encoding="utf-8")
    assert expected.split()[0] == CHECKSUM
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["profile"] == PROFILE_ID
    assert payload["symbol"] == "EURUSD"
    assert len(payload["bars"]) == 48
    assert not any(key in payload for key in ("account", "login", "password", "path"))


def test_fixture_provider_certification_is_deterministic() -> None:
    adapter = FixtureProviderAdapter(FIXTURE)
    engine = ProviderCertificationEngine()
    first = engine.certify(
        adapter=adapter,
        instrument=fixture_instrument(),
        start=START,
        end=END,
    )
    second = engine.certify(
        adapter=adapter,
        instrument=fixture_instrument(),
        start=START,
        end=END,
    )
    assert first == second
    assert first.certified is True
    assert first.issues == ()
    assert first.canonical_h1_count == 48
    assert first.canonical_h4_count == 12
    assert first.canonical_d1_count == 2
    assert first.expected_closure_count == 1
    assert (
        first.structure_digest
        == "922d90ad04f8606f0b04341ec973cbedb84fb9f7013a2c2d247c2e71a28005c9"
    )


class CloseLabelFixtureAdapter(FixtureProviderAdapter):
    def provider_profile(self) -> ProviderProfile:
        return ProviderProfile(
            provider_name="CLOSE_LABEL_PROVIDER",
            adapter_version="test-v1",
            timestamp_semantics=TimestampSemantics.INTERVAL_END,
            native_timeframes=(Timeframe.H1,),
        )

    def fetch_raw_candles(self, instrument, timeframe, start, end):
        return tuple(
            replace(
                candle,
                provider_id="CLOSE_LABEL_PROVIDER",
                provider_name="CLOSE_LABEL_PROVIDER",
                provider_timestamp=candle.raw_close_time,
                timestamp_semantics=TimestampSemantics.INTERVAL_END,
                adapter_version="test-v1",
            )
            for candle in super().fetch_raw_candles(instrument, timeframe, start, end)
        )


def test_open_and_close_label_providers_produce_same_canonical_structure() -> None:
    engine = ProviderCertificationEngine()
    open_report = engine.certify(
        adapter=FixtureProviderAdapter(FIXTURE),
        instrument=fixture_instrument(),
        start=START,
        end=END,
    )
    close_report = engine.certify(
        adapter=CloseLabelFixtureAdapter(FIXTURE),
        instrument=replace(fixture_instrument(), provider_id="CLOSE_LABEL_PROVIDER"),
        start=START,
        end=END,
    )
    assert close_report.certified is True
    assert close_report.structure_digest == open_report.structure_digest


def test_explicit_timestamp_with_unknown_semantics_is_rejected() -> None:
    adapter = FixtureProviderAdapter(FIXTURE)
    raw = replace(
        adapter.fetch_raw_candles("EUR/USD", Timeframe.H1, START, END)[0],
        timestamp_semantics=TimestampSemantics.UNKNOWN,
    )
    result = CandleNormalizer().normalize(raw, fixture_instrument())
    assert result.candle is None
    assert result.issues == ("INVALID_TIMESTAMP",)


def test_canonical_provenance_round_trips_through_sqlite(tmp_path: Path) -> None:
    adapter = FixtureProviderAdapter(FIXTURE)
    instrument = fixture_instrument()
    normalizer = CandleNormalizer()
    bars = market_h1_bars(
        tuple(
            result.candle
            for raw in adapter.fetch_raw_candles("EUR/USD", Timeframe.H1, START, END)
            if (result := normalizer.normalize(raw, instrument)).candle is not None
        )
    )
    repository = SQLiteProjectionRepository(tmp_path / "provenance.sqlite3")
    repository.initialize()
    assert repository.persist_canonical_bars(bars) == 48
    restored = repository.canonical_bar_objects("IC_MARKETS_REFERENCE", "EUR/USD", "H1")
    assert [
        (bar.open_time, bar.close_time, bar.open, bar.high, bar.low, bar.close)
        for bar in restored
    ] == [
        (bar.open_time, bar.close_time, bar.open, bar.high, bar.low, bar.close)
        for bar in bars
    ]
    assert all(bar.construction_profile_version == PROFILE_ID for bar in restored)
    assert all(bar.source_timeframe is Timeframe.H1 for bar in restored)
    assert all(len(bar.source_candle_ids) == 1 for bar in restored)
    assert all(not bar.synthetic and not bar.forward_filled for bar in restored)


def test_quality_issue_persistence_is_idempotent(tmp_path: Path) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "quality.sqlite3")
    repository.initialize()
    values = dict(
        provider="TEST",
        instrument_id="EUR/USD",
        timeframe=Timeframe.H4,
        issue_code="H4_BUCKET_INCOMPLETE",
        detail="requires four H1 bars",
        bucket_open=datetime(2026, 8, 3, 1, tzinfo=timezone.utc),
        bucket_close=datetime(2026, 8, 3, 5, tzinfo=timezone.utc),
        construction_profile_version=PROFILE_ID,
        ingestion_run_id="test-run",
        created_at=datetime(2026, 8, 3, 5, tzinfo=timezone.utc),
    )
    assert repository.persist_quality_issue(**values) is True
    assert repository.persist_quality_issue(**values) is False
    assert len(repository.quality_issues()) == 1


def test_derived_h4_and_d1_retain_every_h1_source_id() -> None:
    adapter = FixtureProviderAdapter(FIXTURE)
    instrument = fixture_instrument()
    normalizer = CandleNormalizer()
    h1 = market_h1_bars(
        tuple(
            result.candle
            for raw in adapter.fetch_raw_candles("EUR/USD", Timeframe.H1, START, END)
            if (result := normalizer.normalize(raw, instrument)).candle is not None
        )
    )
    h4 = BrokerAlignedH4Aggregator().aggregate(h1, as_of=END)
    daily = NewYorkDailyAggregator().aggregate(h1, as_of=END)
    assert h4.issues == () and daily.issues == ()
    assert all(len(bar.source_candle_ids) == 4 for bar in h4.bars)
    assert all(len(bar.source_candle_ids) == 24 for bar in daily.bars)
    assert all(
        bar.construction_profile_version == PROFILE_ID
        for bar in (*h4.bars, *daily.bars)
    )
    assert all(bar.session_identifier for bar in daily.bars)
    assert all(bar.session_open_broker_time for bar in daily.bars)
    assert all(bar.session_close_broker_time for bar in daily.bars)


def test_legacy_canonical_schema_migrates_additively(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE canonical_bars (
            provider TEXT NOT NULL, instrument_id TEXT NOT NULL,
            timeframe TEXT NOT NULL, close_time_utc TEXT NOT NULL,
            open_time_utc TEXT NOT NULL, raw_open_time TEXT NOT NULL,
            raw_close_time TEXT NOT NULL, raw_provider_symbol TEXT NOT NULL,
            session_timezone TEXT NOT NULL, open TEXT NOT NULL,
            high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
            volume TEXT, raw_evidence_json TEXT NOT NULL,
            synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
            PRIMARY KEY (provider, instrument_id, timeframe, close_time_utc)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO canonical_bars VALUES (
            'TEST','EUR/USD','H1','2026-08-03T01:00:00Z',
            '2026-08-03T00:00:00Z','2026-08-03T00:00:00Z',
            '2026-08-03T01:00:00Z','EURUSD','UTC','1','1','1','1',
            NULL,'{}',0
        )
        """
    )
    connection.commit()
    connection.close()
    repository = SQLiteProjectionRepository(database)
    repository.initialize()
    connection = sqlite3.connect(database)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(canonical_bars)")
    }
    row = connection.execute(
        "SELECT construction_profile_version, source_candle_ids_json, forward_filled FROM canonical_bars"
    ).fetchone()
    connection.close()
    assert {
        "quality_status",
        "construction_profile_version",
        "source_candle_ids_json",
        "ingestion_run_id",
        "created_at",
    } <= columns
    assert row == ("LEGACY", "[]", 0)
