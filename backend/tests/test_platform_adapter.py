from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.config import Settings
from backend.app.domain import Bar, FilterMode, Timeframe
from backend.app.engine.current_daily_filter import build_daily_filter_snapshot
from backend.app.engine.current_w1_filter import build_w1_filter_snapshot
from backend.app.engine.models import (
    CURRENT_D1_FILTER_V2,
    CURRENT_W1_FILTER_V1,
    StrategyRequest,
)
from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.main import create_app
from backend.app.market_data.forex_profile import broker_wall_to_utc
from backend.app.market_data.multi_provider import MultiInstrumentTwelveDataProvider
from backend.app.market_data.platform_adapter import (
    SPECT8_PLATFORM_BOOTSTRAP_LIMITS,
    InsufficientPlatformHistoryError,
    PlatformCanonicalBar,
    PlatformIncrementalProcessor,
    PlatformInstrumentHistory,
    PlatformReadBatch,
    PlatformSeriesAvailability,
    Spect8CanonicalReadServiceGateway,
    UnmappedPlatformInstrumentError,
    bid_bars,
    build_platform_history,
    platform_instrument_id,
    spect8_instrument_id,
    to_spect8_bar,
)
from backend.app.market_data.platform_parity import DeterministicParityHarness
from backend.app.market_data.session_boundaries import active_new_york_weekly_session
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService
from backend.app.synthetic_inputs import SyntheticCaseInputLoader
from backend.tests.test_current_daily_filter_v2 import (
    AS_OF,
    current_h1,
    daily_history,
    metadata,
    signal_history,
)
from backend.tests.test_current_weekly_filter_v1 import weekly_history

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc


def _canonical(
    canonical_bar_id: int,
    *,
    timeframe: str = "H1",
    price_type: str = "BID",
    open_time: datetime = datetime(2026, 1, 4, 22, tzinfo=UTC),
    open_value: str = "1.1000",
    high: str = "1.1010",
    low: str = "1.0990",
    close: str = "1.1005",
    volume: Decimal | None = Decimal("100"),
    version_number: int = 1,
    semantic_hash: str | None = None,
    instrument_id: str = "FX_EUR_USD",
) -> PlatformCanonicalBar:
    duration = {
        "M30": timedelta(minutes=30),
        "H1": timedelta(hours=1),
        "H4": timedelta(hours=4),
        "D1": timedelta(days=1),
        "W1": timedelta(days=7),
    }[timeframe]
    return PlatformCanonicalBar(
        canonical_bar_id=canonical_bar_id,
        instrument_id=instrument_id,
        timeframe=timeframe,
        price_type=price_type,
        open_time=open_time,
        close_time=open_time + duration,
        open=Decimal(open_value),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
        volume_type="TICK",
        quality_status="VALID",
        source_provider_id="TEST",
        policy_id="POLICY",
        policy_version="1",
        version_number=version_number,
        semantic_hash=semantic_hash or f"hash-{canonical_bar_id}",
        semantic_available_at=open_time + duration + timedelta(seconds=1),
    )


def _batch(
    bars: tuple[PlatformCanonicalBar, ...],
    *,
    watermark: int | None = None,
    as_of: datetime = datetime(2026, 1, 6, tzinfo=UTC),
) -> PlatformReadBatch:
    return PlatformReadBatch(
        bars=bars,
        availability=tuple(
            PlatformSeriesAvailability(
                instrument_id="FX_EUR_USD",
                timeframe=timeframe,
                price_type="BID",
                returned_rows=sum(
                    bar.timeframe == timeframe and bar.price_type == "BID"
                    for bar in bars
                ),
                latest_close_time=None,
                valid=True,
            )
            for timeframe in SPECT8_PLATFORM_BOOTSTRAP_LIMITS
        ),
        watermark_canonical_bar_id=(
            watermark
            if watermark is not None
            else max((bar.canonical_bar_id for bar in bars), default=0)
        ),
        available_as_of=as_of,
        instrument_master_checksum="instrument-master-v1",
        session_calendar_checksum="session-calendar-v1",
        timezone_data_version="tzdata-v1",
    )


class _Gateway:
    def __init__(self, batches: list[PlatformReadBatch]) -> None:
        self.batches = batches
        self.calls: list[dict[str, object]] = []

    def read(self, instrument_ids: tuple[str, ...], **kwargs: object) -> PlatformReadBatch:
        self.calls.append({"instrument_ids": instrument_ids, **kwargs})
        return self.batches.pop(0)


class _PlatformTimeframe(StrEnum):
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"


def _repository(tmp_path: Path) -> SQLiteProjectionRepository:
    repository = SQLiteProjectionRepository(tmp_path / "spect8.sqlite3")
    repository.initialize()
    return repository


def test_explicit_instrument_mapping_and_unmapped_failure() -> None:
    assert platform_instrument_id("EUR_USD") == "FX_EUR_USD"
    assert spect8_instrument_id("FX_GBP_USD") == "GBP_USD"
    assert spect8_instrument_id("FX_USD_JPY") == "USD_JPY"
    with pytest.raises(UnmappedPlatformInstrumentError, match="unmapped Spect8"):
        platform_instrument_id("XAU_USD")
    with pytest.raises(UnmappedPlatformInstrumentError, match="unmapped Platform"):
        spect8_instrument_id("FX_AUD_USD")


def test_gateway_passes_exact_bootstrap_policy_to_platform_service() -> None:
    class Service:
        kwargs: dict[str, object]

        def read(self, _instrument_ids: tuple[str, ...], **kwargs: object):
            self.kwargs = kwargs
            source = _batch((_canonical(1),), watermark=1)
            return SimpleNamespace(
                bars=source.bars,
                availability=source.availability,
                watermark_canonical_bar_id=source.watermark_canonical_bar_id,
                available_as_of=source.available_as_of,
                instrument_master_checksum=source.instrument_master_checksum,
                session_calendar_checksum=source.session_calendar_checksum,
                timezone_data_version=source.timezone_data_version,
            )

    service = Service()
    gateway = Spect8CanonicalReadServiceGateway(service, _PlatformTimeframe)
    result = gateway.read(
        ("FX_EUR_USD",),
        available_as_of=datetime(2026, 1, 6, tzinfo=UTC),
        after_canonical_bar_id=None,
    )
    assert service.kwargs["limits"] == {
        _PlatformTimeframe.M30: 1,
        _PlatformTimeframe.H1: 1_177,
        _PlatformTimeframe.H4: 30,
        _PlatformTimeframe.D1: 10,
        _PlatformTimeframe.W1: 6,
    }
    assert result.bars[0].immutable_identity == _canonical(1).immutable_identity


def test_bid_is_the_only_strategy_price_authority() -> None:
    bid = _canonical(1, close="1.1005")
    ask = _canonical(2, price_type="ASK", close="1.9000", high="1.9010")
    assert bid_bars((ask, bid)) == (bid,)
    translated = to_spect8_bar(bid)
    assert translated.close == Decimal("1.1005")
    assert translated.source_candle_ids == ("MDP:1:hash-1",)
    with pytest.raises(ValueError, match="requires BID"):
        to_spect8_bar(ask)


@pytest.mark.parametrize(
    ("bucket_open", "expected_open"),
    [
        (datetime(2026, 1, 4, 22, tzinfo=UTC), datetime(2026, 1, 4, 22, tzinfo=UTC)),
        (datetime(2026, 7, 5, 21, tzinfo=UTC), datetime(2026, 7, 5, 21, tzinfo=UTC)),
    ],
)
def test_h4_is_derived_from_h1_with_broker_dst_alignment(
    bucket_open: datetime, expected_open: datetime
) -> None:
    h1 = tuple(
        _canonical(
            index + 1,
            open_time=bucket_open + timedelta(hours=index),
            open_value=f"1.10{index}0",
            high=f"1.11{index}0",
            low=f"1.09{index}0",
            close=f"1.10{index}5",
        )
        for index in range(4)
    )
    history = build_platform_history(
        _batch(h1, as_of=bucket_open + timedelta(hours=5)), "EUR_USD"
    )
    assert len(history.h4) == 1
    assert history.h4[0].open_time == expected_open
    assert history.h4[0].open == Decimal("1.1000")
    assert history.h4[0].high == Decimal("1.1130")
    assert history.h4[0].low == Decimal("1.0900")
    assert history.h4[0].close == Decimal("1.1035")
    assert len(history.h4[0].source_candle_ids) == 4


def test_platform_utc_h4_is_ignored_in_favor_of_h1_derivation() -> None:
    h1 = tuple(
        _canonical(index + 1, open_time=datetime(2026, 1, 4, 22, tzinfo=UTC) + timedelta(hours=index))
        for index in range(4)
    )
    platform_h4 = _canonical(
        50,
        timeframe="H4",
        open_time=datetime(2026, 1, 4, 20, tzinfo=UTC),
        open_value="9",
        high="10",
        low="8",
        close="9",
    )
    history = build_platform_history(
        _batch((*h1, platform_h4), as_of=datetime(2026, 1, 5, 4, tzinfo=UTC)),
        "EUR_USD",
    )
    assert len(history.h4) == 1
    assert history.h4[0].open == Decimal("1.1000")
    assert "MDP:50:hash-50" not in history.h4[0].source_candle_ids


def test_bootstrap_depth_contract_includes_d1_ten() -> None:
    assert SPECT8_PLATFORM_BOOTSTRAP_LIMITS == {
        "M30": 1,
        "H1": 1_177,
        "H4": 30,
        "D1": 10,
        "W1": 6,
    }
    history = PlatformInstrumentHistory(
        platform_instrument_id="FX_EUR_USD",
        spect8_instrument_id="EUR_USD",
        m30=(),
        h1=(),
        h4=(),
        d1=(),
        w1=(),
        h4_issues=(),
    )
    with pytest.raises(InsufficientPlatformHistoryError, match="D1=0/10"):
        history.assert_bootstrap_ready()


def test_processing_failure_does_not_advance_watermark(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    gateway = _Gateway([_batch((_canonical(1),), watermark=1)])
    processor = PlatformIncrementalProcessor(gateway, repository)

    def fail(_bar: PlatformCanonicalBar, _instrument_id: str) -> None:
        raise RuntimeError("evaluation failed")

    with pytest.raises(RuntimeError, match="evaluation failed"):
        processor.process(
            ("EUR_USD",),
            available_as_of=datetime(2026, 1, 6, tzinfo=UTC),
            process_bar=fail,
        )
    assert repository.platform_integration_state() is None
    assert repository.platform_consumed_identity(_canonical(1).logical_identity) is None


def test_consumed_provenance_makes_replay_safe_before_watermark(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    bar = _canonical(1)
    gateway = _Gateway([_batch((bar,), watermark=1), _batch((bar,), watermark=1)])
    processor = PlatformIncrementalProcessor(gateway, repository)
    original_advance = repository.advance_platform_watermark
    calls: list[int] = []

    def fail_watermark(**_kwargs: object) -> None:
        raise RuntimeError("checkpoint unavailable")

    repository.advance_platform_watermark = fail_watermark  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        processor.process(
            ("EUR_USD",),
            available_as_of=datetime(2026, 1, 6, tzinfo=UTC),
            process_bar=lambda current, _mapped: calls.append(current.canonical_bar_id)
            or "evaluation-1",
        )
    repository.advance_platform_watermark = original_advance  # type: ignore[method-assign]
    result = processor.process(
        ("EUR_USD",),
        available_as_of=datetime(2026, 1, 6, tzinfo=UTC),
        process_bar=lambda current, _mapped: calls.append(current.canonical_bar_id)
        or "evaluation-duplicate",
    )
    assert calls == [1]
    assert result.replayed == 1
    assert repository.platform_integration_state()["watermark_canonical_bar_id"] == 1  # type: ignore[index]


def test_later_revision_is_detected_without_automatic_recompute(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = _canonical(1, semantic_hash="first")
    revised = _canonical(
        2,
        version_number=2,
        semantic_hash="revised",
        close="1.1006",
    )
    gateway = _Gateway([_batch((first,), watermark=1), _batch((revised,), watermark=2)])
    processor = PlatformIncrementalProcessor(gateway, repository)
    evaluations: list[int] = []
    for _ in range(2):
        processor.process(
            ("EUR_USD",),
            available_as_of=datetime(2026, 1, 6, tzinfo=UTC),
            process_bar=lambda bar, _mapped: evaluations.append(bar.canonical_bar_id)
            or f"evaluation-{bar.canonical_bar_id}",
        )
    consumed = repository.platform_consumed_identity(first.logical_identity)
    revisions = repository.platform_revisions()
    assert evaluations == [1]
    assert consumed is not None and consumed["canonical_bar_id"] == 1
    assert consumed["semantic_hash"] == "first"
    assert revisions == (
        {
            "revised_canonical_bar_id": 2,
            "logical_identity": first.logical_identity,
            "first_canonical_bar_id": 1,
            "revised_version_number": 2,
            "revised_semantic_hash": "revised",
            "observed_at": "2026-01-06T00:00:00Z",
            "action": "DETECTED_NO_AUTOMATIC_REPLAY",
        },
    )


def test_shadow_configuration_does_not_replace_current_provider(tmp_path: Path) -> None:
    settings = Settings(
        repository_root=ROOT,
        database_path=tmp_path / "shadow.sqlite3",
        internal_api_key="test",
        market_data_provider="twelve_data",
        twelve_data_api_key="fake",
        market_data_platform_shadow_enabled=True,
        market_data_platform_database_url="postgresql+psycopg://user:pass@host/db",
    )
    settings.validate()
    assert settings.market_data_provider == "twelve_data"
    assert settings.market_data_platform_shadow_enabled is True


def test_twelve_data_coordinator_path_remains_constructible(tmp_path: Path) -> None:
    settings = Settings(
        repository_root=ROOT,
        database_path=tmp_path / "twelve-data.sqlite3",
        internal_api_key="test",
        market_data_provider="twelve_data",
        twelve_data_api_key="fake",
        polling_enabled=False,
        provider_discovery_enabled=False,
    )
    application = create_app(settings)
    assert isinstance(application.state.provider, MultiInstrumentTwelveDataProvider)
    assert application.state.coordinator is not None


def _adapt_request(case_id: str):
    request = SyntheticCaseInputLoader(ROOT).load(case_id)
    instrument = replace(
        request.instrument,
        instrument_id="EUR_USD",
        provider="OLD_PROVIDER",
    )

    def old_bar(bar: Bar) -> Bar:
        return replace(bar, instrument_id="EUR_USD", provider="OLD_PROVIDER")

    canonical_id = 1

    def platform_bar(bar: Bar) -> Bar:
        nonlocal canonical_id
        canonical = _canonical(
            canonical_id,
            timeframe=bar.timeframe.value,
            open_time=bar.open_time,
            open_value=str(bar.open),
            high=str(bar.high),
            low=str(bar.low),
            close=str(bar.close),
            volume=bar.volume,
        )
        canonical_id += 1
        return to_spect8_bar(canonical)

    old = replace(
        request,
        instrument=instrument,
        signal_bars=tuple(old_bar(bar) for bar in request.signal_bars),
        daily_bars=tuple(old_bar(bar) for bar in request.daily_bars),
    )
    platform = replace(
        request,
        case_id=f"platform-{case_id}",
        instrument=replace(instrument, provider="MARKET_DATA_PLATFORM"),
        signal_bars=tuple(platform_bar(bar) for bar in request.signal_bars),
        daily_bars=tuple(platform_bar(bar) for bar in request.daily_bars),
    )
    return old, platform


def _adapt_broker_h4_request():
    request = SyntheticCaseInputLoader(ROOT).load("confirmed_sell_h4_01")
    wall = datetime(2026, 1, 19)
    aligned: list[Bar] = []
    for source in request.signal_bars:
        while wall.weekday() >= 5:
            wall += timedelta(hours=4)
        open_time = broker_wall_to_utc(wall)
        aligned.append(
            replace(
                source,
                instrument_id="EUR_USD",
                provider="OLD_PROVIDER",
                open_time=open_time,
                close_time=broker_wall_to_utc(wall + timedelta(hours=4)),
            )
        )
        wall += timedelta(hours=4)
    aligned = aligned[-SPECT8_PLATFORM_BOOTSTRAP_LIMITS["H4"] :]
    old_daily = tuple(
        replace(bar, instrument_id="EUR_USD", provider="OLD_PROVIDER")
        for bar in request.daily_bars
    )
    canonical: list[PlatformCanonicalBar] = []
    canonical_id = 1
    for source in aligned:
        for offset in range(4):
            member_open = source.open_time + timedelta(hours=offset)
            canonical.append(
                _canonical(
                    canonical_id,
                    open_time=member_open,
                    open_value=str(source.open),
                    high=str(source.high),
                    low=str(source.low),
                    close=str(source.close if offset == 3 else source.open),
                    volume=(
                        source.volume / Decimal("4")
                        if source.volume is not None
                        else None
                    ),
                )
            )
            canonical_id += 1
    platform_daily: list[Bar] = []
    for source in request.daily_bars:
        platform_daily.append(
            to_spect8_bar(
                _canonical(
                    canonical_id,
                    timeframe="D1",
                    open_time=source.open_time,
                    open_value=str(source.open),
                    high=str(source.high),
                    low=str(source.low),
                    close=str(source.close),
                    volume=source.volume,
                )
            )
        )
        canonical_id += 1
    evaluation_time = max(bar.close_time for bar in aligned) + timedelta(seconds=1)
    history = build_platform_history(
        _batch(tuple(canonical), as_of=evaluation_time), "EUR_USD"
    )
    old = replace(
        request,
        evaluation_time=evaluation_time,
        signal_bars=tuple(aligned),
        daily_bars=old_daily,
        instrument=replace(
            request.instrument,
            instrument_id="EUR_USD",
            provider="OLD_PROVIDER",
        ),
    )
    platform = replace(
        old,
        case_id="platform-confirmed_sell_h4_01",
        signal_bars=history.h4,
        daily_bars=tuple(platform_daily),
        instrument=replace(old.instrument, provider="MARKET_DATA_PLATFORM"),
    )
    return old, platform


def _old_bars(bars: tuple[Bar, ...]) -> tuple[Bar, ...]:
    return tuple(
        replace(bar, instrument_id="EUR_USD", provider="OLD_PROVIDER")
        for bar in bars
    )


def _platform_bars(
    bars: tuple[Bar, ...], *, first_id: int = 1
) -> tuple[Bar, ...]:
    return tuple(
        to_spect8_bar(
            _canonical(
                first_id + index,
                timeframe=bar.timeframe.value,
                open_time=bar.open_time,
                open_value=str(bar.open),
                high=str(bar.high),
                low=str(bar.low),
                close=str(bar.close),
                volume=bar.volume,
            )
        )
        for index, bar in enumerate(bars)
    )


def _metadata(provider: str):
    return replace(metadata(), instrument_id="EUR_USD", provider=provider)


def test_parity_harness_matches_exact_h1_strategy_and_event_outputs(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository)
    old, platform = _adapt_request("confirmed_buy_h1_01")
    report = DeterministicParityHarness(service).compare(old, platform)
    report.assert_matched()
    assert report.old_event_order == report.platform_event_order


def test_parity_harness_matches_broker_derived_h4_outputs(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository)
    old, platform = _adapt_broker_h4_request()
    report = DeterministicParityHarness(service).compare(old, platform)
    report.assert_matched()


def test_parity_harness_matches_current_micro_partial_d1(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository)
    old_h1 = _old_bars(current_h1())
    old_d1 = _old_bars(daily_history())
    platform_h1 = _platform_bars(current_h1())
    platform_d1 = _platform_bars(daily_history(), first_id=100)
    old_snapshot = build_daily_filter_snapshot(
        provider="OLD_PROVIDER",
        instrument="EUR_USD",
        as_of_h1_close=AS_OF,
        h1_bars=old_h1,
        completed_d1_bars=old_d1,
    )
    platform_snapshot = build_daily_filter_snapshot(
        provider="MARKET_DATA_PLATFORM",
        instrument="EUR_USD",
        as_of_h1_close=AS_OF,
        h1_bars=platform_h1,
        completed_d1_bars=platform_d1,
    )
    old = StrategyRequest(
        case_id="old-current-d1",
        strategy_id="SPECT8_MICRO_DAILY_V1_0",
        timeframe=Timeframe.H1,
        evaluation_time=AS_OF + timedelta(seconds=1),
        signal_bars=_old_bars(signal_history(Timeframe.H1)),
        daily_bars=old_d1,
        instrument=_metadata("OLD_PROVIDER"),
        strategy_version=CURRENT_D1_FILTER_V2,
        daily_filter_snapshot=old_snapshot,
    )
    platform = replace(
        old,
        case_id="platform-current-d1",
        signal_bars=_platform_bars(signal_history(Timeframe.H1), first_id=200),
        daily_bars=platform_d1,
        instrument=_metadata("MARKET_DATA_PLATFORM"),
        daily_filter_snapshot=platform_snapshot,
    )
    report = DeterministicParityHarness(service).compare(old, platform)
    report.assert_matched()


def test_parity_harness_matches_macro_partial_w1(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository)
    active_close = active_new_york_weekly_session(AS_OF)[0]
    weekly = weekly_history(active_close, as_of=AS_OF)
    old_weekly = _old_bars(weekly)
    platform_weekly = _platform_bars(weekly)
    old_snapshot = build_w1_filter_snapshot(
        provider="OLD_PROVIDER",
        instrument="EUR_USD",
        as_of_h1_close=AS_OF,
        h1_bars=old_weekly,
    )
    platform_snapshot = build_w1_filter_snapshot(
        provider="MARKET_DATA_PLATFORM",
        instrument="EUR_USD",
        as_of_h1_close=AS_OF,
        h1_bars=platform_weekly,
    )
    old_d1 = _old_bars(daily_history())
    old = StrategyRequest(
        case_id="old-current-w1",
        strategy_id=CURRENT_W1_FILTER_V1,
        timeframe=Timeframe.H1,
        evaluation_time=AS_OF + timedelta(seconds=1),
        signal_bars=_old_bars(signal_history(Timeframe.H1)),
        daily_bars=old_d1,
        instrument=_metadata("OLD_PROVIDER"),
        strategy_version=CURRENT_W1_FILTER_V1,
        filter_mode=FilterMode.MACRO,
        w1_filter_snapshot=old_snapshot,
    )
    platform = replace(
        old,
        case_id="platform-current-w1",
        signal_bars=_platform_bars(signal_history(Timeframe.H1), first_id=2_000),
        daily_bars=_platform_bars(daily_history(), first_id=3_000),
        instrument=_metadata("MARKET_DATA_PLATFORM"),
        w1_filter_snapshot=platform_snapshot,
    )
    report = DeterministicParityHarness(service).compare(old, platform)
    report.assert_matched()


def test_parity_harness_reports_exact_field_mismatch(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository)
    old, platform = _adapt_request("confirmed_buy_h1_01")
    evaluated_close_time = service.evaluate_request(old).evaluation.signal_bar.close_time  # type: ignore[union-attr]
    changed_bars = list(platform.signal_bars)
    changed_index = next(
        index
        for index, bar in enumerate(changed_bars)
        if bar.close_time == evaluated_close_time
    )
    selected_bar = changed_bars[changed_index]
    changed_bars[changed_index] = replace(
        selected_bar,
        high=selected_bar.high + Decimal("0.0001"),
    )
    changed = replace(
        platform,
        signal_bars=tuple(changed_bars),
    )
    report = DeterministicParityHarness(service).compare(old, changed)
    assert report.matched is False
    assert report.mismatches
