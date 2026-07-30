from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.domain import EventType, Timeframe
from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.main import create_app
from backend.app.market_data.clock import FixedClock
from backend.app.market_data.closed_bar import ClosedBarDetector
from backend.app.market_data.coordinator import MarketDataCoordinator
from backend.app.market_data.interfaces import MarketDataProvider
from backend.app.market_data.models import (
    CanonicalInstrument,
    HealthState,
    RawProviderCandle,
)
from backend.app.market_data.normalizer import CandleNormalizer
from backend.app.market_data.registry import CanonicalInstrumentRegistry
from backend.app.market_data.replay_provider import ReplayMarketDataProvider
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "golden"
SELECTED_CASES = ("confirmed_buy_h1_01", "confirmed_sell_h4_01")
EXPECTED_TRACE = [
    EventType.BAR_CLOSED.value,
    EventType.FILTER_EVALUATED.value,
    EventType.FILTER_MATCHED.value,
    EventType.SIGNAL_EVALUATED.value,
    EventType.SIGNAL_CONFIRMED.value,
    EventType.LEVELS_CALCULATED.value,
    EventType.STATUS_PROJECTED.value,
]


def _coordinator(
    database_path: Path,
    case_ids: tuple[str, ...] = SELECTED_CASES,
) -> tuple[
    MarketDataCoordinator,
    ReplayMarketDataProvider,
    FixedClock,
    SQLiteProjectionRepository,
]:
    provider = ReplayMarketDataProvider(GOLDEN, case_ids)
    repository = SQLiteProjectionRepository(database_path)
    repository.initialize()
    clock = FixedClock(provider.initial_clock_time())
    registry = CanonicalInstrumentRegistry(provider.discover_instruments())
    service = WalkingSkeletonService(
        Spect8StrategyEvaluator(),
        None,
        repository,
    )
    coordinator = MarketDataCoordinator(
        provider=provider,
        registry=registry,
        normalizer=CandleNormalizer(),
        detector=ClosedBarDetector(),
        service=service,
        repository=repository,
        clock=clock,
    )
    return coordinator, provider, clock, repository


def _first_instrument(
    provider: ReplayMarketDataProvider,
) -> CanonicalInstrument:
    return provider.discover_instruments()[0]


def _raw(
    instrument: CanonicalInstrument,
    **overrides: object,
) -> RawProviderCandle:
    values: dict[str, object] = {
        "provider_id": instrument.provider_id,
        "provider_symbol": instrument.provider_symbol,
        "timeframe": Timeframe.H1,
        "raw_open_time": "2026-02-03T10:00:00Z",
        "raw_close_time": "2026-02-03T11:00:00Z",
        "open": "2875.125",
        "high": "2876.235",
        "low": "2874.115",
        "close": "2875.555",
        "volume": "100",
        "is_complete": True,
        "session_timezone": instrument.session_timezone,
    }
    values.update(overrides)
    return RawProviderCandle(**values)  # type: ignore[arg-type]


def test_provider_contract_and_registry_are_provider_neutral() -> None:
    provider: MarketDataProvider = ReplayMarketDataProvider(
        GOLDEN, SELECTED_CASES
    )
    identity = provider.identity
    assert identity.synthetic is True
    assert identity.display_name == "Deterministic Replay Provider"

    instruments = provider.discover_instruments()
    assert len(instruments) == 1
    instrument = instruments[0]
    assert instrument.instrument_id == "SYNTH_XAUUSD"
    assert instrument.provider_symbol == "SYNTH_XAUUSD"
    assert instrument.asset_class == "METAL"
    assert instrument.quote_currency == "USD"
    assert instrument.profit_currency == "USD"
    assert instrument.point_size == Decimal("0.01")
    assert instrument.price_precision == 2
    assert set(instrument.available_timeframes) == {
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    }

    registry = CanonicalInstrumentRegistry(instruments)
    assert registry.get(
        instrument.provider_id, instrument.instrument_id
    ) is instrument
    assert registry.supports(
        instrument.provider_id, instrument.instrument_id, Timeframe.D1
    )


def test_registry_rejects_duplicate_canonical_keys() -> None:
    provider = ReplayMarketDataProvider(GOLDEN, SELECTED_CASES)
    instrument = _first_instrument(provider)
    with pytest.raises(ValueError, match="duplicate canonical"):
        CanonicalInstrumentRegistry((instrument, instrument))


def test_normalizer_converts_utc_and_preserves_raw_evidence() -> None:
    provider = ReplayMarketDataProvider(GOLDEN, SELECTED_CASES)
    base = _first_instrument(provider)
    instrument = replace(base, session_timezone="Asia/Dhaka")
    raw = _raw(
        instrument,
        raw_open_time="2026-02-03T16:00:00",
        raw_close_time="2026-02-03T17:00:00",
        session_timezone="Asia/Dhaka",
    )

    result = CandleNormalizer().normalize(raw, instrument)

    assert result.issues == ()
    assert result.candle is not None
    candle = result.candle
    assert candle.open_time == datetime(
        2026, 2, 3, 10, 0, tzinfo=timezone.utc
    )
    assert candle.close_time == datetime(
        2026, 2, 3, 11, 0, tzinfo=timezone.utc
    )
    assert candle.open == Decimal("2875.12")
    assert candle.close == Decimal("2875.56")
    assert candle.raw_open == "2875.125"
    assert candle.raw_close == "2875.555"
    assert candle.raw_open_time == "2026-02-03T16:00:00"
    assert candle.session_timezone == "Asia/Dhaka"


@pytest.mark.parametrize(
    ("overrides", "issue"),
    [
        ({"is_complete": False}, "INCOMPLETE_CANDLE"),
        ({"open": "0"}, "INVALID_PRICE"),
        ({"close": "NaN"}, "INVALID_PRICE"),
        ({"volume": "NaN"}, "INVALID_VOLUME"),
        ({"high": "2874", "low": "2876"}, "INVALID_OHLC"),
        (
            {"session_timezone": "Asia/Dhaka"},
            "SESSION_TIMEZONE_MISMATCH",
        ),
        (
            {
                "raw_open_time": "2026-02-03T11:00:00Z",
                "raw_close_time": "2026-02-03T10:00:00Z",
            },
            "INVALID_TIMESTAMP_RANGE",
        ),
    ],
)
def test_normalizer_rejects_invalid_or_incomplete_candles(
    overrides: dict[str, object],
    issue: str,
) -> None:
    provider = ReplayMarketDataProvider(GOLDEN, SELECTED_CASES)
    instrument = _first_instrument(provider)
    result = CandleNormalizer().normalize(
        _raw(instrument, **overrides), instrument
    )
    assert result.candle is None
    assert issue in result.issues


def test_selected_replay_projects_two_statuses_and_14_ordered_events(
    tmp_path: Path,
) -> None:
    coordinator, _, _, repository = _coordinator(
        tmp_path / "selected.sqlite3"
    )

    poll = coordinator.poll_once()

    assert poll.provider_health.state is HealthState.HEALTHY
    assert len(poll.outcomes) == 2
    assert all(not outcome.replayed for outcome in poll.outcomes)
    assert sum(outcome.events_created for outcome in poll.outcomes) == 14
    assert repository.processed_count() == 2
    assert len(repository.statuses()) == 2
    assert repository.canonical_bar_count() == 80
    assert {
        (
            status["timeframe"],
            tuple(
                level["direction"]
                for level in status["levels_results"]
            ),
        )
        for status in repository.statuses()
    } == {("H1", ("BUY",)), ("H4", ("SELL",))}

    events = repository.events()
    for case_id in SELECTED_CASES:
        trace = [
            event["event_type"]
            for event in events
            if event["source_case_id"] == case_id
        ]
        assert trace == EXPECTED_TRACE


def test_replay_is_idempotent_in_process_and_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "restart.sqlite3"
    coordinator, _, _, repository = _coordinator(database)
    first = coordinator.poll_once()
    second = coordinator.poll_once()

    assert first.canonical_bars_inserted == 80
    assert second.canonical_bars_inserted == 0
    assert all(outcome.replayed for outcome in second.outcomes)
    assert sum(outcome.events_created for outcome in second.outcomes) == 0
    assert repository.event_count() == 14
    assert repository.canonical_bar_count() == 80

    restarted, _, _, reopened = _coordinator(database)
    third = restarted.poll_once()
    assert third.canonical_bars_inserted == 0
    assert all(outcome.replayed for outcome in third.outcomes)
    assert sum(outcome.events_created for outcome in third.outcomes) == 0
    assert reopened.processed_count() == 2
    assert reopened.event_count() == 14
    assert reopened.canonical_bar_count() == 80
    assert len(reopened.statuses()) == 2
    database.unlink()
    assert not database.exists()


def test_replay_prevents_d1_and_cross_timeframe_lookahead() -> None:
    provider = ReplayMarketDataProvider(GOLDEN, SELECTED_CASES)
    h1_time = datetime.fromisoformat("2026-02-03T11:00:01+00:00")
    triggers = provider.fetch_completed_bars(h1_time)

    assert [trigger.source_id for trigger in triggers] == [
        "confirmed_buy_h1_01"
    ]
    history = provider.fetch_required_history(triggers[0], h1_time)
    trigger_close = datetime.fromisoformat(
        triggers[0].candle.raw_close_time.replace("Z", "+00:00")
    )
    assert history.timeframe is Timeframe.H1
    assert all(
        datetime.fromisoformat(
            bar.raw_close_time.replace("Z", "+00:00")
        )
        <= trigger_close
        for bar in history.signal_bars
    )
    assert all(
        datetime.fromisoformat(
            bar.raw_close_time.replace("Z", "+00:00")
        )
        < trigger_close
        for bar in history.daily_bars
    )


@pytest.mark.parametrize(
    "case_id",
    ("confirmed_buy_h1_05", "confirmed_buy_h4_05"),
)
def test_developing_signal_bar_is_ignored(case_id: str) -> None:
    provider = ReplayMarketDataProvider(GOLDEN, (case_id,))
    as_of = provider.initial_clock_time()
    trigger = provider.fetch_completed_bars(as_of)[0]
    history = provider.fetch_required_history(trigger, as_of)

    assert trigger.candle.is_complete is True
    assert len(history.signal_bars) == 35
    assert all(bar.is_complete for bar in history.signal_bars)
    assert max(
        datetime.fromisoformat(
            bar.raw_close_time.replace("Z", "+00:00")
        )
        for bar in history.signal_bars
    ) < as_of


def test_developing_daily_bar_is_ignored() -> None:
    provider = ReplayMarketDataProvider(GOLDEN, ("confirmed_buy_h1_01",))
    replay_case = provider._cases[0]
    developing = replace(
        replay_case.daily_source[-1],
        raw_open_time="2026-02-03T00:00:00Z",
        raw_close_time="2026-02-04T00:00:00Z",
        is_complete=False,
    )
    provider._cases[0] = replace(
        replay_case,
        daily_source=(*replay_case.daily_source, developing),
    )
    as_of = provider.initial_clock_time()
    trigger = provider.fetch_completed_bars(as_of)[0]
    history = provider.fetch_required_history(trigger, as_of)

    assert developing not in history.daily_bars
    assert all(bar.is_complete for bar in history.daily_bars)


def test_duplicate_candle_is_quarantined(tmp_path: Path) -> None:
    coordinator, _, _, repository = _coordinator(
        tmp_path / "duplicate.sqlite3",
        ("duplicate_signal_candle",),
    )

    poll = coordinator.poll_once()

    assert poll.provider_health.state is HealthState.QUARANTINED
    assert poll.outcomes[0].health_state is HealthState.QUARANTINED
    assert "DUPLICATE_CANDLE" in poll.outcomes[0].issues
    assert repository.processed_count() == 0
    assert repository.event_count() == 0
    assert repository.canonical_bar_count() == 0


@pytest.mark.parametrize(
    ("case_id", "expected_issue"),
    [
        ("missing_signal_candle", "MISSING_SIGNAL_CANDLE"),
        ("missing_daily_candle", "MISSING_DAILY_CANDLE"),
    ],
)
def test_missing_required_history_is_reported_and_quarantined(
    tmp_path: Path,
    case_id: str,
    expected_issue: str,
) -> None:
    coordinator, _, _, repository = _coordinator(
        tmp_path / f"{case_id}.sqlite3",
        (case_id,),
    )

    poll = coordinator.poll_once()

    assert poll.provider_health.state is HealthState.QUARANTINED
    assert expected_issue in poll.outcomes[0].issues
    assert repository.processed_count() == 0
    assert repository.event_count() == 0


def test_out_of_order_candle_is_quarantined(tmp_path: Path) -> None:
    coordinator, provider, _, repository = _coordinator(
        tmp_path / "out-of-order.sqlite3",
        ("confirmed_buy_h1_01",),
    )
    replay_case = provider._cases[0]
    signal = list(replay_case.signal_source)
    signal[-3], signal[-2] = signal[-2], signal[-3]
    provider._cases[0] = replace(
        replay_case,
        signal_source=tuple(signal),
    )

    poll = coordinator.poll_once()

    assert poll.provider_health.state is HealthState.QUARANTINED
    assert "OUT_OF_ORDER_CANDLE" in poll.outcomes[0].issues
    assert repository.event_count() == 0
    assert repository.canonical_bar_count() == 0


def test_insufficient_history_is_reported_without_strategy_call(
    tmp_path: Path,
) -> None:
    coordinator, provider, _, repository = _coordinator(
        tmp_path / "insufficient.sqlite3",
        ("confirmed_buy_h1_01",),
    )
    replay_case = provider._cases[0]
    provider._cases[0] = replace(
        replay_case,
        signal_source=replay_case.signal_source[-20:],
    )

    poll = coordinator.poll_once()

    assert poll.provider_health.state is HealthState.INSUFFICIENT_HISTORY
    assert (
        "INSUFFICIENT_SIGNAL_HISTORY" in poll.outcomes[0].issues
    )
    assert repository.processed_count() == 0
    assert repository.event_count() == 0


def test_fixed_clock_drives_data_unavailable_stale_and_recovered(
    tmp_path: Path,
) -> None:
    coordinator, provider, clock, repository = _coordinator(
        tmp_path / "health.sqlite3"
    )
    h1_time = datetime.fromisoformat("2026-02-03T11:00:01+00:00")
    h4_time = datetime.fromisoformat("2026-02-07T20:00:01+00:00")

    clock.set(h1_time - timedelta(days=1))
    unavailable = coordinator.poll_once()
    assert unavailable.provider_health.state is HealthState.DATA_UNAVAILABLE
    assert unavailable.outcomes == ()

    clock.set(h1_time)
    recovered_from_unavailable = coordinator.poll_once()
    assert (
        recovered_from_unavailable.provider_health.state
        is HealthState.RECOVERED
    )
    assert [outcome.source_case_id for outcome in recovered_from_unavailable.outcomes] == [
        "confirmed_buy_h1_01"
    ]

    clock.set(h1_time + timedelta(hours=3))
    stale = coordinator.poll_once()
    assert stale.provider_health.state is HealthState.STALE

    clock.set(h4_time)
    recovered = coordinator.poll_once()
    assert recovered.provider_health.state is HealthState.RECOVERED
    assert repository.processed_count() == 2

    healthy = coordinator.poll_once()
    assert healthy.provider_health.state is HealthState.HEALTHY
    persisted = repository.provider_health(provider.identity.provider_id)
    assert persisted is not None
    assert persisted["state"] == "HEALTHY"
    assert persisted["previous_state"] == "RECOVERED"


def test_api_health_exposes_replay_provider_and_persisted_freshness(
    tmp_path: Path,
) -> None:
    settings = Settings(
        repository_root=ROOT,
        database_path=tmp_path / "api.sqlite3",
        internal_api_key="test-key",
        selected_cases=SELECTED_CASES,
        auto_seed_synthetic=True,
    )
    application = create_app(settings)

    with TestClient(application) as client:
        response = client.get("/health")
        instruments = client.get(
            "/instruments",
            headers={"X-Spect8-Internal-Key": "test-key"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "REPLAY_MARKET_DATA_PROVIDER"
    assert payload["data"]["mode"] == "PHASE_2B_REPLAY_MARKET_DATA"
    assert payload["data"]["provider"]["synthetic"] is True
    assert payload["data"]["provider_health"]["state"] == "HEALTHY"
    assert instruments.status_code == 200
    assert instruments.json()["data"][0]["timeframes"] == ["D1", "H1", "H4"]


def test_api_health_has_same_shape_before_first_poll(tmp_path: Path) -> None:
    settings = Settings(
        repository_root=ROOT,
        database_path=tmp_path / "unpolled.sqlite3",
        internal_api_key="test-key",
        selected_cases=SELECTED_CASES,
        auto_seed_synthetic=False,
    )
    application = create_app(settings)

    with TestClient(application) as client:
        health = client.get("/health").json()["data"]["provider_health"]

    assert set(health) == {
        "provider",
        "state",
        "previous_state",
        "checked_at",
        "latest_completed_close",
        "freshness_seconds",
        "detail",
        "synthetic",
    }
    assert health["provider"] == "SYNTHETIC_UTC_V1"
    assert health["state"] == "HEALTHY"
    assert health["previous_state"] is None


def test_market_data_runtime_has_no_oracle_or_strategy_formulas() -> None:
    paths = [
        *(
            ROOT / "backend" / "app" / "market_data"
        ).glob("*.py"),
        ROOT / "backend" / "app" / "main.py",
    ]
    forbidden = (
        "golden.reference",
        "reference.calculator",
        "expected.json",
        "calculation_ledger",
        "simple_moving_average",
        "wilder_atr",
        "activation_buffer",
        "STOP_ATR_MULTIPLIER",
        "TARGET_R_MULTIPLE",
        "calculate_position_size",
        "SyntheticCaseInputLoader",
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path} contains forbidden {token}"


def test_strategy_code_does_not_read_system_clock() -> None:
    for path in (ROOT / "backend" / "app" / "engine").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "datetime.now" not in source
        assert "SystemClock" not in source
