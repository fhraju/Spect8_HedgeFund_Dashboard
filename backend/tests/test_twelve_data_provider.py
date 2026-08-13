from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.domain import Bar, Timeframe
from backend.app.engine.position_sizing import calculate_position_size
from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.main import create_app
from backend.app.market_data.clock import FixedClock
from backend.app.market_data.closed_bar import ClosedBarDetector
from backend.app.market_data.coordinator import MarketDataCoordinator
from backend.app.market_data.daily_aggregator import NewYorkDailyAggregator
from backend.app.market_data.interfaces import MarketDataProvider
from backend.app.market_data.models import (
    HealthState,
    MarketDataProviderError,
    ProviderErrorCode,
)
from backend.app.market_data.normalizer import CandleNormalizer
from backend.app.market_data.registry import (
    CanonicalInstrumentRegistry,
    twelve_data_instruments,
)
from backend.app.market_data.twelve_data_provider import (
    HttpResponse,
    TransportTimeoutError,
    TwelveDataProvider,
)
from backend.app.market_data.twelve_data_smoke import main as smoke_main
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "twelve_data"
AS_OF = datetime(2026, 7, 30, 11, 0, 1, tzinfo=timezone.utc)
TEST_KEY = "fixture-only-api-key"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _response(
    payload: Mapping[str, Any],
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    return HttpResponse(
        status_code=status,
        headers=headers or {},
        body=json.dumps(payload).encode(),
    )


def _empty(interval: str) -> HttpResponse:
    return _response(
        {
            "meta": {"symbol": "EUR/USD", "interval": interval},
            "values": [],
            "status": "ok",
        }
    )


class FakeTransport:
    def __init__(self, routes: Mapping[str, list[object]]) -> None:
        self.routes = {key: list(values) for key, values in routes.items()}
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        path: str,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> HttpResponse:
        self.calls.append(
            {
                "path": path,
                "params": dict(params),
                "headers": dict(headers),
                "connect_timeout": connect_timeout,
                "read_timeout": read_timeout,
            }
        )
        route = self.routes[params["interval"]]
        result = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, HttpResponse)
        return result


def _provider(
    routes: Mapping[str, list[object]],
    **overrides: Any,
) -> tuple[TwelveDataProvider, FakeTransport]:
    transport = FakeTransport(routes)
    return (
        TwelveDataProvider(
            TEST_KEY,
            transport=transport,
            sleep=overrides.pop("sleep", lambda _: None),
            **overrides,
        ),
        transport,
    )


def _standard_routes(
    *,
    h1: HttpResponse | None = None,
    h4: HttpResponse | None = None,
    d1: HttpResponse | None = None,
) -> dict[str, list[object]]:
    return {
        "1h": [h1 or _response(_fixture("valid_h1.json"))],
        "4h": [h4 or _response(_fixture("valid_h4.json"))],
        "1day": [d1 or _response(_fixture("valid_d1.json"))],
    }


def test_provider_contract_identity_and_eur_usd_registry_mapping() -> None:
    provider, _ = _provider(_standard_routes())

    assert isinstance(provider, MarketDataProvider)
    assert provider.identity.provider_id == "TWELVE_DATA"
    assert provider.identity.synthetic is False
    (instrument,) = provider.discover_instruments()
    assert instrument.instrument_id == "EUR/USD"
    assert instrument.provider_symbol == "EUR/USD"
    assert instrument.asset_class == "FOREX"
    assert instrument.session_timezone == "UTC"
    assert instrument.synthetic is False
    assert instrument.available_timeframes == (
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    )
    assert instrument.tick_size is None
    assert instrument.tick_value_usd is None
    assert instrument.contract_min is None
    assert (
        calculate_position_size(
            instrument.to_strategy_metadata(),
            instrument.point_size * 100,
        ).contract_status
        == "METADATA_UNAVAILABLE"
    )


def test_symbol_interval_outputsize_timeouts_and_header_auth_mapping() -> None:
    provider, transport = _provider(
        _standard_routes(),
        connect_timeout=1.25,
        read_timeout=4.5,
    )

    triggers = provider.fetch_completed_bars(AS_OF)
    provider.fetch_required_history(triggers[-1], AS_OF)

    assert {call["params"]["interval"] for call in transport.calls} == {"1h"}
    for call in transport.calls:
        assert call["path"] == "/time_series"
        assert call["params"]["symbol"] == "EUR/USD"
        assert call["params"]["timezone"] == "UTC"
        assert call["params"]["order"] == "desc"
        assert call["connect_timeout"] == 1.25
        assert call["read_timeout"] == 4.5
        assert "apikey" not in call["params"]
        assert call["headers"]["Authorization"].startswith("apikey ")
    assert {call["params"]["outputsize"] for call in transport.calls} == {"673"}
    telemetry = provider.telemetry()
    assert telemetry.network_attempts == 1
    assert telemetry.successful_requests == 1
    assert telemetry.failed_requests == 0
    assert telemetry.series_attempts == {"H1": 1, "H4": 0, "D1": 0}
    assert telemetry.requests_used_this_minute == 1
    assert telemetry.estimated_credits_used_today == 1
    assert telemetry.last_h1_request == AS_OF
    assert telemetry.next_expected_h1_close == datetime(
        2026, 7, 30, 12, tzinfo=timezone.utc
    )


def test_crypto_provider_request_and_response_are_bound_to_selected_exchange() -> None:
    instrument = CanonicalInstrumentRegistry(twelve_data_instruments()).by_id("BTC_USD")
    response = _response(
        {
            "meta": {
                "symbol": "BTC/USD",
                "interval": "1h",
                "exchange": "Binance",
                "exchange_timezone": "UTC",
            },
            "values": [],
            "status": "ok",
        }
    )
    provider, transport = _provider(
        _standard_routes(h1=response),
        canonical_instrument=instrument,
    )
    assert provider.fetch_completed_bars(AS_OF) == ()
    assert transport.calls[0]["params"]["symbol"] == "BTC/USD"
    assert transport.calls[0]["params"]["exchange"] == "Binance"


def test_bootstrap_derives_latest_h4_trigger_from_one_h1_request() -> None:
    provider, transport = _provider(_standard_routes(), bootstrap_latest_h4=True)

    triggers = provider.fetch_completed_bars(AS_OF)
    assert [item.candle.timeframe for item in triggers] == [
        Timeframe.H4,
        Timeframe.H1,
    ]
    h4 = triggers[0]
    assert h4.candle.source_timeframe is Timeframe.H1
    assert h4.candle.raw_close_time == "2026-07-30T09:00:00Z"
    assert h4.candle.open_time_utc == datetime(2026, 7, 30, 5, tzinfo=timezone.utc)
    assert h4.candle.close_time_utc == datetime(2026, 7, 30, 9, tzinfo=timezone.utc)
    provider.fetch_required_history(h4, AS_OF)
    assert len(transport.calls) == 1
    assert transport.calls[0]["params"]["interval"] == "1h"


def test_boundary_cache_avoids_requests_until_a_series_can_advance() -> None:
    provider, transport = _provider(_standard_routes())

    triggers = provider.fetch_completed_bars(AS_OF)
    provider.fetch_required_history(triggers[-1], AS_OF)
    first_call_count = len(transport.calls)
    same_boundary = provider.fetch_completed_bars(AS_OF)

    assert first_call_count == 1
    assert same_boundary == ()
    assert len(transport.calls) == first_call_count
    assert provider.telemetry().cache_hits >= 1


def test_resume_cursor_returns_every_unseen_completed_h1_in_order() -> None:
    start = datetime(2026, 7, 30, tzinfo=timezone.utc)
    values = [
        {
            "datetime": (start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S"),
            "open": "1.10000",
            "high": "1.10100",
            "low": "1.09900",
            "close": "1.10050",
            "volume": "100",
        }
        for index in range(36)
    ]
    payload = {
        "meta": {"symbol": "EUR/USD", "interval": "1h"},
        "values": list(reversed(values)),
        "status": "ok",
    }
    provider, _ = _provider(
        _standard_routes(
            h1=_response(payload),
            h4=_empty("4h"),
        )
    )
    provider.set_resume_cursor(
        Timeframe.H1,
        datetime(2026, 7, 31, 8, tzinfo=timezone.utc),
    )

    triggers = provider.fetch_completed_bars(
        datetime(2026, 7, 31, 12, 0, 30, tzinfo=timezone.utc)
    )

    assert [trigger.candle.raw_close_time for trigger in triggers] == [
        "2026-07-31T09:00:00Z",
        "2026-07-31T10:00:00Z",
        "2026-07-31T11:00:00Z",
        "2026-07-31T12:00:00Z",
    ]
    assert provider.telemetry().completed_discoveries["H1"] == 4


def test_rewound_database_cursor_replays_current_cached_h1_without_network() -> None:
    start = datetime(2026, 7, 30, tzinfo=timezone.utc)
    values = [
        {
            "datetime": (start + timedelta(hours=index)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "open": "1.10000",
            "high": "1.10100",
            "low": "1.09900",
            "close": "1.10050",
            "volume": "100",
        }
        for index in range(36)
    ]
    provider, transport = _provider(
        _standard_routes(
            h1=_response(
                {
                    "meta": {"symbol": "EUR/USD", "interval": "1h"},
                    "values": list(reversed(values)),
                    "status": "ok",
                }
            ),
            h4=_empty("4h"),
        )
    )
    as_of = datetime(2026, 7, 31, 12, 0, 30, tzinfo=timezone.utc)
    cursor = datetime(2026, 7, 31, 10, tzinfo=timezone.utc)
    provider.set_resume_cursor(Timeframe.H1, cursor)

    first = provider.fetch_completed_bars(as_of)
    calls_after_first = len(transport.calls)
    provider.set_resume_cursor(Timeframe.H1, cursor)
    replayed = provider.fetch_completed_bars(as_of)

    assert [item.candle.raw_close_time for item in first] == [
        "2026-07-31T11:00:00Z",
        "2026-07-31T12:00:00Z",
    ]
    assert [item.candle.raw_close_time for item in replayed] == [
        "2026-07-31T11:00:00Z",
        "2026-07-31T12:00:00Z",
    ]
    assert len(transport.calls) == calls_after_first


def test_missing_database_cursor_replays_latest_cached_h1_without_network() -> None:
    provider, transport = _provider(
        _standard_routes(
            h1=_response(_fixture("reverse_ordered.json")),
            h4=_empty("4h"),
        )
    )

    provider.fetch_completed_bars(AS_OF)
    calls_after_first = len(transport.calls)
    provider.set_resume_cursor(Timeframe.H1, None)
    replayed = provider.fetch_completed_bars(AS_OF)

    assert [item.candle.raw_close_time for item in replayed] == [
        "2026-07-30T10:00:00Z"
    ]
    assert len(transport.calls) == calls_after_first


def test_reverse_provider_order_is_normalized_to_utc_chronological_order() -> None:
    provider, _ = _provider(
        _standard_routes(
            h1=_response(_fixture("reverse_ordered.json")),
            h4=_empty("4h"),
        )
    )

    (trigger,) = provider.fetch_completed_bars(AS_OF)
    history = provider.fetch_required_history(trigger, AS_OF)
    normalized = [
        CandleNormalizer().normalize(candle, provider.discover_instruments()[0]).candle
        for candle in history.signal_bars
    ]

    assert [bar.raw_open_time for bar in history.signal_bars] == [
        "2026-07-30 07:00:00",
        "2026-07-30 08:00:00",
        "2026-07-30 09:00:00",
    ]
    assert all(isinstance(bar, Bar) for bar in normalized)
    assert all(
        bar is not None and bar.open_time.tzinfo is timezone.utc for bar in normalized
    )
    assert provider.diagnostics(Timeframe.H1).out_of_order_count == 1


@pytest.mark.parametrize(
    ("as_of", "expected_terminal_open"),
    [
        (
            datetime(2026, 7, 30, 9, 59, 59, tzinfo=timezone.utc),
            "2026-07-30 08:00:00",
        ),
        (
            datetime(2026, 7, 30, 10, 0, 0, tzinfo=timezone.utc),
            "2026-07-30 08:00:00",
        ),
        (
            datetime(2026, 7, 30, 10, 0, 1, tzinfo=timezone.utc),
            "2026-07-30 09:00:00",
        ),
    ],
)
def test_completed_candle_exact_close_boundary(
    as_of: datetime,
    expected_terminal_open: str,
) -> None:
    provider, _ = _provider(
        _standard_routes(
            h1=_response(_fixture("forming_candle.json")),
            h4=_empty("4h"),
        )
    )

    (trigger,) = provider.fetch_completed_bars(as_of)

    assert trigger.candle.raw_open_time == expected_terminal_open
    assert trigger.candle.is_complete is True
    assert (
        datetime.fromisoformat(trigger.candle.raw_close_time.replace("Z", "+00:00"))
        < as_of
    )


@pytest.mark.parametrize(
    ("fixture_name", "code"),
    [
        ("duplicate_candle.json", ProviderErrorCode.DUPLICATE_CANDLE),
        ("missing_interval.json", ProviderErrorCode.MISSING_CANDLE),
        ("malformed_candle.json", ProviderErrorCode.MALFORMED_RESPONSE),
    ],
)
def test_data_quality_failures_are_quarantined(
    fixture_name: str,
    code: ProviderErrorCode,
) -> None:
    provider, _ = _provider(_standard_routes(h1=_response(_fixture(fixture_name))))

    with pytest.raises(MarketDataProviderError) as captured:
        provider.fetch_completed_bars(AS_OF)

    assert captured.value.code is code
    assert captured.value.health_state is HealthState.QUARANTINED
    assert provider.health(AS_OF).state is HealthState.QUARANTINED


def test_empty_response_maps_to_data_unavailable_without_fake_candles() -> None:
    provider, _ = _provider(_standard_routes(h1=_empty("1h"), h4=_empty("4h")))

    assert provider.fetch_completed_bars(AS_OF) == ()
    assert provider.health(AS_OF).state is HealthState.DATA_UNAVAILABLE
    assert provider.health(AS_OF).latest_completed_close is None


@pytest.mark.parametrize(
    ("fixture_name", "expected_code"),
    [
        ("provider_error.json", ProviderErrorCode.VALIDATION),
        ("authentication_failure.json", ProviderErrorCode.AUTHENTICATION),
    ],
)
def test_http_success_error_payloads_are_canonical_and_not_retried(
    fixture_name: str,
    expected_code: ProviderErrorCode,
) -> None:
    provider, transport = _provider(
        _standard_routes(h1=_response(_fixture(fixture_name)))
    )

    with pytest.raises(MarketDataProviderError) as captured:
        provider.fetch_completed_bars(AS_OF)

    assert captured.value.code is expected_code
    assert captured.value.retryable is False
    assert len(transport.calls) == 1


def test_http_authentication_failure_is_not_retried() -> None:
    provider, transport = _provider(
        _standard_routes(
            h1=_response(_fixture("authentication_failure.json"), status=401)
        )
    )

    with pytest.raises(MarketDataProviderError) as captured:
        provider.fetch_completed_bars(AS_OF)

    assert captured.value.code is ProviderErrorCode.AUTHENTICATION
    assert len(transport.calls) == 1


def test_rate_limit_respects_retry_after_and_recovers() -> None:
    sleeps: list[float] = []
    provider, transport = _provider(
        {
            **_standard_routes(),
            "1h": [
                _response(
                    _fixture("rate_limit.json"),
                    status=429,
                    headers={"Retry-After": "2"},
                ),
                _response(_fixture("valid_h1.json")),
            ],
        },
        sleep=sleeps.append,
    )

    triggers = provider.fetch_completed_bars(AS_OF)

    assert len(triggers) == 1
    assert sleeps == [2.0]
    assert (
        len([call for call in transport.calls if call["params"]["interval"] == "1h"])
        == 2
    )
    assert provider.health(AS_OF).state is HealthState.HEALTHY
    telemetry = provider.telemetry()
    assert telemetry.rate_limit_responses == 1
    assert telemetry.failed_requests == 1
    assert telemetry.successful_requests == 1


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (
            _response(_fixture("temporary_server_failure.json"), status=500),
            ProviderErrorCode.TEMPORARY_UNAVAILABLE,
        ),
        (TransportTimeoutError("fixture timeout"), ProviderErrorCode.TIMEOUT),
    ],
)
def test_transient_failures_have_bounded_retry_count(
    failure: object,
    expected_code: ProviderErrorCode,
) -> None:
    sleeps: list[float] = []
    provider, transport = _provider(
        {
            **_standard_routes(),
            "1h": [failure],
        },
        max_attempts=3,
        backoff_seconds=0.1,
        sleep=sleeps.append,
    )

    with pytest.raises(MarketDataProviderError) as captured:
        provider.fetch_completed_bars(AS_OF)

    assert captured.value.code is expected_code
    assert captured.value.retryable is True
    assert len(transport.calls) == 3
    assert sleeps == pytest.approx([0.1, 0.2])
    telemetry = provider.telemetry()
    assert telemetry.network_attempts == 3
    assert telemetry.failed_requests == 3
    assert telemetry.network_timeouts == (
        3 if expected_code is ProviderErrorCode.TIMEOUT else 0
    )


def test_health_transitions_from_failure_to_success() -> None:
    provider, _ = _provider(
        {
            **_standard_routes(),
            "1h": [
                _response(
                    _fixture("temporary_server_failure.json"),
                    status=500,
                ),
                _response(_fixture("valid_h1.json")),
            ],
        },
        max_attempts=1,
    )

    with pytest.raises(MarketDataProviderError):
        provider.fetch_completed_bars(AS_OF)
    assert provider.health(AS_OF).state is HealthState.DATA_UNAVAILABLE

    assert provider.fetch_completed_bars(AS_OF)
    assert provider.health(AS_OF).state is HealthState.HEALTHY


def test_api_key_is_redacted_from_repr_errors_and_health() -> None:
    sentinel = "fixture-secret-must-not-leak"
    transport = FakeTransport(
        {
            **_standard_routes(),
            "1h": [
                _response(
                    {
                        "code": 401,
                        "message": f"invalid credential {sentinel}",
                        "status": "error",
                    }
                )
            ],
        }
    )
    provider = TwelveDataProvider(sentinel, transport=transport)

    with pytest.raises(MarketDataProviderError) as captured:
        provider.fetch_completed_bars(AS_OF)

    exposed = " ".join(
        (
            repr(provider),
            repr(captured.value),
            str(captured.value),
            repr(provider.health(AS_OF)),
        )
    )
    assert sentinel not in exposed
    settings = Settings(
        repository_root=ROOT,
        database_path=ROOT / "var" / "unused.sqlite3",
        internal_api_key="internal",
        market_data_provider="twelve_data",
        twelve_data_api_key=sentinel,
    )
    assert sentinel not in repr(settings)


def test_twelve_data_configuration_fails_fast_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPECT8_MARKET_DATA_PROVIDER", "twelve_data")
    monkeypatch.setenv("SPECT8_INSTRUMENT", "EUR/USD")
    monkeypatch.setenv("SPECT8_TIMEFRAMES", "H1,H4,D1")
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)

    with pytest.raises(ValueError, match="TWELVE_DATA_API_KEY is required"):
        Settings.from_environment()


def test_application_can_select_twelve_data_without_startup_network(
    tmp_path: Path,
) -> None:
    settings = Settings(
        repository_root=ROOT,
        database_path=tmp_path / "configured.sqlite3",
        internal_api_key="internal",
        auto_seed_synthetic=False,
        market_data_provider="twelve_data",
        twelve_data_api_key=TEST_KEY,
    )
    application = create_app(settings)

    with TestClient(application) as client:
        health = client.get("/health").json()

    assert application.state.provider.identity.provider_id == "TWELVE_DATA"
    assert health["synthetic"] is False
    assert health["data"]["mode"] == "PHASE_3B_TWELVE_DATA_RUNTIME"
    assert health["data"]["provider_health"]["state"] == "POLLING_DISABLED"
    assert health["data"]["operations"]["polling_state"] == (
        "DISABLED_BY_CONFIGURATION"
    )


def _golden_payload(case_id: str, filename: str, interval: str) -> HttpResponse:
    path = ROOT / "golden" / "cases" / case_id / filename
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    values = [
        {
            "datetime": row["open_time"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
        for row in reversed(rows)
    ]
    return _response(
        {
            "meta": {"symbol": "EUR/USD", "interval": interval},
            "values": values,
            "status": "ok",
        }
    )


def _combined_h1_payload(
    *,
    terminal_close: datetime = datetime(2026, 2, 3, 11, tzinfo=timezone.utc),
    history_hours: int = 505,
) -> HttpResponse:
    path = ROOT / "golden" / "cases" / "confirmed_buy_h1_01" / "signal_bars.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        signal = {row["open_time"]: row for row in csv.DictReader(handle)}
    start = terminal_close - timedelta(hours=history_hours)
    values = []
    for index in range(history_hours):
        opened = start + timedelta(hours=index)
        key = opened.isoformat().replace("+00:00", "Z")
        row = signal.get(key)
        values.append(
            {
                "datetime": opened.strftime("%Y-%m-%d %H:%M:%S"),
                "open": row["open"] if row else "325.00000",
                "high": row["high"] if row else "333.00000",
                "low": row["low"] if row else "317.00000",
                "close": row["close"] if row else "325.00000",
                "volume": "100",
            }
        )
    return _response(
        {
            "meta": {
                "symbol": "EUR/USD",
                "interval": "1h",
                "exchange_timezone": "UTC",
            },
            "values": list(reversed(values)),
            "status": "ok",
        }
    )


def test_provider_supplies_h1_source_for_new_york_equal_close_context() -> None:
    timeframe = Timeframe.H1
    terminal = datetime(2026, 1, 15, 22, tzinfo=timezone.utc)
    h1 = _combined_h1_payload(terminal_close=terminal)
    h4 = _empty("4h")
    if timeframe is Timeframe.H4:
        start = terminal - timedelta(hours=35 * 4)
        values = [
            {
                "datetime": (start + timedelta(hours=index * 4)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "open": "325",
                "high": "333",
                "low": "317",
                "close": "325",
            }
            for index in range(35)
        ]
        h4 = _response(
            {
                "meta": {"symbol": "EUR/USD", "interval": "4h"},
                "values": list(reversed(values)),
                "status": "ok",
            }
        )
    routes = {
        "1h": [h1],
        "4h": [h4],
        "1day": [_empty("1day")],
    }
    provider, _ = _provider(routes)
    as_of = terminal + timedelta(seconds=1)

    triggers = provider.fetch_completed_bars(as_of)
    trigger = next(item for item in triggers if item.candle.timeframe is timeframe)
    history = provider.fetch_required_history(trigger, as_of)

    assert history.daily_bars == ()
    assert len(history.daily_source_bars) == 505
    assert all(bar.timeframe is Timeframe.H1 for bar in history.daily_source_bars)
    assert history.daily_source_bars[-1].raw_close_time == trigger.candle.raw_close_time
    instrument = provider.discover_instruments()[0]
    canonical = tuple(
        result.candle
        for raw in history.daily_source_bars
        if (result := CandleNormalizer().normalize(raw, instrument)).candle is not None
    )
    aggregation = NewYorkDailyAggregator().aggregate(canonical, as_of=as_of)
    assert aggregation.issues == ()
    assert aggregation.bars[-1].close_time == datetime(
        2026, 1, 15, 22, tzinfo=timezone.utc
    )
    assert aggregation.bars[-1].close_time == datetime.fromisoformat(
        trigger.candle.raw_close_time.replace("Z", "+00:00")
    )


class RecordingEvaluator:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self._delegate = Spect8StrategyEvaluator()

    def evaluate(self, request: Any) -> Any:
        self.requests.append(request)
        return self._delegate.evaluate(request)


def _integration_coordinator(
    database: Path,
) -> tuple[MarketDataCoordinator, SQLiteProjectionRepository, RecordingEvaluator,]:
    provider, _ = _provider(
        {
            "1h": [_combined_h1_payload(history_hours=1345)],
            "4h": [_empty("4h")],
            "1day": [_empty("1day")],
        }
    )
    repository = SQLiteProjectionRepository(database)
    repository.initialize()
    evaluator = RecordingEvaluator()
    service = WalkingSkeletonService(evaluator, None, repository)
    coordinator = MarketDataCoordinator(
        provider=provider,
        registry=CanonicalInstrumentRegistry(provider.discover_instruments()),
        normalizer=CandleNormalizer(),
        detector=ClosedBarDetector(),
        service=service,
        repository=repository,
        clock=FixedClock(datetime(2026, 2, 3, 11, 0, 1, tzinfo=timezone.utc)),
    )
    return coordinator, repository, evaluator


def test_coordinator_catches_up_each_unseen_completed_trigger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, _ = _provider(
        {
            "1h": [_combined_h1_payload(history_hours=1345)],
            "4h": [_empty("4h")],
            "1day": [_empty("1day")],
        }
    )
    provider.set_resume_cursor(
        Timeframe.H1,
        datetime(2026, 2, 3, 9, tzinfo=timezone.utc),
    )
    repository = SQLiteProjectionRepository(tmp_path / "catchup.sqlite3")
    repository.initialize()
    evaluator = RecordingEvaluator()
    coordinator = MarketDataCoordinator(
        provider=provider,
        registry=CanonicalInstrumentRegistry(provider.discover_instruments()),
        normalizer=CandleNormalizer(),
        detector=ClosedBarDetector(),
        service=WalkingSkeletonService(evaluator, None, repository),
        repository=repository,
        clock=FixedClock(datetime(2026, 2, 3, 11, 0, 1, tzinfo=timezone.utc)),
    )

    order: list[str] = []
    original_persist_bars = repository.persist_canonical_bars
    original_h4_aggregate = coordinator._h4_aggregator.aggregate
    original_persist_snapshot = repository.persist_daily_filter_snapshot
    original_evaluate = evaluator.evaluate

    def persist_bars(bars: tuple[Bar, ...]) -> int:
        for item in bars:
            if item.close_time == datetime(2026, 2, 3, 10, tzinfo=timezone.utc):
                order.append(f"persist_{item.timeframe.value}")
        return original_persist_bars(bars)

    def aggregate_h4(*args: Any, **kwargs: Any) -> Any:
        order.append("aggregate_H4")
        return original_h4_aggregate(*args, **kwargs)

    def persist_snapshot(value: Any) -> bool:
        order.append("persist_snapshot")
        return original_persist_snapshot(value)

    def evaluate(request: Any) -> Any:
        if request.signal_bars[-1].close_time == datetime(
            2026, 2, 3, 10, tzinfo=timezone.utc
        ):
            order.append(f"evaluate_{request.timeframe.value}")
        return original_evaluate(request)

    monkeypatch.setattr(repository, "persist_canonical_bars", persist_bars)
    monkeypatch.setattr(coordinator._h4_aggregator, "aggregate", aggregate_h4)
    monkeypatch.setattr(repository, "persist_daily_filter_snapshot", persist_snapshot)
    monkeypatch.setattr(evaluator, "evaluate", evaluate)
    monkeypatch.setattr(coordinator, "_seed_resume_cursors", lambda: None)

    result = coordinator.poll_once()

    assert len(result.outcomes) == 6
    assert [
        (
            request.strategy_version,
            request.timeframe,
            request.signal_bars[-1].close_time,
        )
        for request in evaluator.requests
    ] == [
        (
            "MICRO_DAILY_FILTER_CURRENT_D1_V2",
            Timeframe.H1,
            datetime(2026, 2, 3, 10, tzinfo=timezone.utc),
        ),
        (
            "MACRO_WEEKLY_FILTER_CURRENT_W1_V1",
            Timeframe.H1,
            datetime(2026, 2, 3, 10, tzinfo=timezone.utc),
        ),
        (
            "MICRO_DAILY_FILTER_CURRENT_D1_V2",
            Timeframe.H4,
            datetime(2026, 2, 3, 10, tzinfo=timezone.utc),
        ),
        (
            "MACRO_WEEKLY_FILTER_CURRENT_W1_V1",
            Timeframe.H4,
            datetime(2026, 2, 3, 10, tzinfo=timezone.utc),
        ),
        (
            "MICRO_DAILY_FILTER_CURRENT_D1_V2",
            Timeframe.H1,
            datetime(2026, 2, 3, 11, tzinfo=timezone.utc),
        ),
        (
            "MACRO_WEEKLY_FILTER_CURRENT_W1_V1",
            Timeframe.H1,
            datetime(2026, 2, 3, 11, tzinfo=timezone.utc),
        ),
    ]
    assert repository.processed_count() == 6
    assert order.index("persist_H1") < order.index("aggregate_H4")
    assert order.index("aggregate_H4") < order.index("persist_snapshot")
    assert order.index("persist_snapshot") < order.index("evaluate_H1")
    assert order.index("evaluate_H1") < order.index("evaluate_H4")
    assert order.count("persist_snapshot") == 2
    assert len({event["idempotency_key"] for event in repository.events()}) == 6


def test_coordinator_catches_up_only_the_filter_mode_that_is_behind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, _ = _provider(
        {
            "1h": [_combined_h1_payload(history_hours=1345)],
            "4h": [_empty("4h")],
            "1day": [_empty("1day")],
        }
    )
    provider.set_resume_cursor(
        Timeframe.H1,
        datetime(2026, 2, 3, 9, tzinfo=timezone.utc),
    )
    repository = SQLiteProjectionRepository(tmp_path / "one-mode-catchup.sqlite3")
    repository.initialize()
    evaluator = RecordingEvaluator()
    coordinator = MarketDataCoordinator(
        provider=provider,
        registry=CanonicalInstrumentRegistry(provider.discover_instruments()),
        normalizer=CandleNormalizer(),
        detector=ClosedBarDetector(),
        service=WalkingSkeletonService(evaluator, None, repository),
        repository=repository,
        clock=FixedClock(datetime(2026, 2, 3, 11, 0, 1, tzinfo=timezone.utc)),
    )

    monkeypatch.setattr(coordinator, "_seed_resume_cursors", lambda: None)
    monkeypatch.setattr(
        repository,
        "latest_evaluation_close",
        lambda _provider, _instrument, _timeframe, strategy_id=None: (
            "2026-02-03T11:00:00Z"
            if strategy_id == "MACRO_WEEKLY_FILTER_CURRENT_W1_V1"
            else None
        ),
    )

    result = coordinator.poll_once()

    assert not [outcome for outcome in result.outcomes if outcome.issues]
    assert [request.strategy_version for request in evaluator.requests] == [
        "MICRO_DAILY_FILTER_CURRENT_D1_V2",
        "MICRO_DAILY_FILTER_CURRENT_D1_V2",
        "MICRO_DAILY_FILTER_CURRENT_D1_V2",
    ]
    assert [request.timeframe for request in evaluator.requests] == [
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.H1,
    ]


def test_coordinator_reuses_orphan_snapshot_when_projection_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "orphan-snapshot.sqlite3"
    first, repository, _ = _integration_coordinator(database)
    first.poll_once()
    key = (
        "MICRO_DAILY_FILTER_CURRENT_D1_V2:TWELVE_DATA:EUR/USD:"
        "H1:2026-02-03T11:00:00Z"
    )
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM event_history WHERE idempotency_key = ?", (key,))
        connection.execute("DELETE FROM processed_bars WHERE idempotency_key = ?", (key,))
        connection.execute(
            """DELETE FROM instrument_status
                 WHERE strategy_id = 'MICRO_DAILY_FILTER_CURRENT_D1_V2'
                   AND provider = 'TWELVE_DATA'
                   AND instrument_id = 'EUR/USD'
                   AND timeframe = 'H1'"""
        )
        connection.commit()
    assert repository.daily_filter_snapshot_at(
        "TWELVE_DATA",
        "EUR/USD",
        "MICRO_DAILY_FILTER_CURRENT_D1_V2",
        "2026-02-03T11:00:00Z",
    ) is not None

    def forbidden_rebuild(**_: Any) -> Any:
        raise AssertionError("an immutable orphan snapshot must be reused")

    monkeypatch.setattr(
        "backend.app.market_data.coordinator.build_daily_filter_snapshot",
        forbidden_rebuild,
    )
    restarted, _, evaluator = _integration_coordinator(database)

    result = restarted.poll_once()

    assert not [outcome for outcome in result.outcomes if outcome.issues]
    assert [request.strategy_version for request in evaluator.requests] == [
        "MICRO_DAILY_FILTER_CURRENT_D1_V2"
    ]
    assert [request.timeframe for request in evaluator.requests] == [Timeframe.H1]
    assert evaluator.requests[0].daily_filter_snapshot is not None
    assert evaluator.requests[0].daily_filter_snapshot.snapshot_id.startswith("dfs_")


def test_projection_failure_isolated_by_mode_and_next_poll_self_heals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, repository, evaluator = _integration_coordinator(
        tmp_path / "mode-isolation.sqlite3"
    )
    original = coordinator._service.process_request

    def fail_micro(request: Any) -> Any:
        if request.strategy_version == "MICRO_DAILY_FILTER_CURRENT_D1_V2":
            raise RuntimeError("injected interrupted micro projection")
        return original(request)

    monkeypatch.setattr(coordinator._service, "process_request", fail_micro)
    first = coordinator.poll_once()

    assert any(
        outcome.issues == ("PROJECTION_FAILURE:RuntimeError",)
        for outcome in first.outcomes
    )
    assert [request.strategy_version for request in evaluator.requests] == [
        "MACRO_WEEKLY_FILTER_CURRENT_W1_V1"
    ]
    assert repository.latest_evaluation_close(
        "TWELVE_DATA",
        "EUR/USD",
        "H1",
        "MICRO_DAILY_FILTER_CURRENT_D1_V2",
    ) is None
    assert repository.latest_evaluation_close(
        "TWELVE_DATA",
        "EUR/USD",
        "H1",
        "MACRO_WEEKLY_FILTER_CURRENT_W1_V1",
    ) == "2026-02-03T11:00:00Z"

    monkeypatch.setattr(coordinator._service, "process_request", original)
    second = coordinator.poll_once()

    assert not [outcome for outcome in second.outcomes if outcome.issues]
    assert [request.strategy_version for request in evaluator.requests] == [
        "MACRO_WEEKLY_FILTER_CURRENT_W1_V1",
        "MICRO_DAILY_FILTER_CURRENT_D1_V2",
    ]
    assert repository.latest_evaluation_close(
        "TWELVE_DATA",
        "EUR/USD",
        "H1",
        "MICRO_DAILY_FILTER_CURRENT_D1_V2",
    ) == "2026-02-03T11:00:00Z"
    assert coordinator.provider.telemetry().cache_hits >= 1


def test_provider_normalizer_replay_and_strategy_pipeline_is_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "twelve-data.sqlite3"
    coordinator, repository, evaluator = _integration_coordinator(database)

    first = coordinator.poll_once()
    second = coordinator.poll_once()
    restarted, reopened, restarted_evaluator = _integration_coordinator(database)
    third = restarted.poll_once()

    assert first.provider_health.state is HealthState.HEALTHY
    assert len(first.outcomes) == 2
    assert all(outcome.replayed is False for outcome in first.outcomes)
    assert all(outcome.events_created == 7 for outcome in first.outcomes)
    assert second.outcomes == ()
    assert third.outcomes == ()
    assert repository.event_count() == 14
    assert reopened.event_count() == 14
    assert repository.processed_count() == 2
    assert reopened.processed_count() == 2
    assert all(bar["provider"] == "TWELVE_DATA" for bar in repository.canonical_bars())
    assert all(bar["synthetic"] is False for bar in repository.canonical_bars())
    assert repository.statuses()[0]["synthetic"] is False
    assert all(event["synthetic"] is False for event in repository.events())

    requests = [*evaluator.requests, *restarted_evaluator.requests]
    assert requests
    for request in requests:
        assert all(isinstance(bar, Bar) for bar in request.signal_bars)
        assert all(isinstance(bar, Bar) for bar in request.daily_bars)
        assert request.instrument.provider == "TWELVE_DATA"
        assert not hasattr(request, "meta")
        assert not hasattr(request.signal_bars[0], "values")


def test_example_environment_has_blank_key_and_local_env_is_ignored() -> None:
    lines = (ROOT / "backend" / ".env.example").read_text(encoding="utf-8").splitlines()

    assert "TWELVE_DATA_API_KEY=" in lines
    assert all(
        not line.startswith("TWELVE_DATA_API_KEY=") or line == "TWELVE_DATA_API_KEY="
        for line in lines
    )
    assert (
        "backend/.env" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    )


def test_native_daily_fetch_and_smoke_paths_are_explicitly_blocked() -> None:
    provider, transport = _provider(_standard_routes())

    with pytest.raises(ValueError, match="provider-native D1"):
        provider.fetch_smoke_bars(Timeframe.D1, AS_OF)
    with pytest.raises(ValueError, match="provider-native D1"):
        provider.fetch_historical_bars(
            Timeframe.D1,
            AS_OF - timedelta(days=10),
            AS_OF,
        )
    assert transport.calls == []


def test_common_adapter_fetch_delegates_to_historical_h1_path() -> None:
    provider, transport = _provider(_standard_routes(h1=_empty("1h")))

    result = provider.fetch_raw_candles(
        "EUR/USD",
        Timeframe.H1,
        AS_OF - timedelta(hours=2),
        AS_OF,
    )

    assert result == ()
    assert len(transport.calls) == 1
    assert transport.calls[0]["params"]["interval"] == "1h"


def test_live_smoke_is_explicitly_not_run_without_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)

    assert smoke_main() == 0
    assert capsys.readouterr().out.strip() == "NOT RUN — API key unavailable"
