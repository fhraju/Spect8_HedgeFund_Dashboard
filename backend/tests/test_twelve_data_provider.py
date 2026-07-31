from __future__ import annotations

import csv
import json
from collections import defaultdict
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
from backend.app.market_data.interfaces import MarketDataProvider
from backend.app.market_data.models import (
    HealthState,
    MarketDataProviderError,
    ProviderErrorCode,
)
from backend.app.market_data.normalizer import CandleNormalizer
from backend.app.market_data.registry import CanonicalInstrumentRegistry
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

    assert {call["params"]["interval"] for call in transport.calls} == {
        "1h",
        "4h",
        "1day",
    }
    for call in transport.calls:
        assert call["path"] == "/time_series"
        assert call["params"]["symbol"] == "EUR/USD"
        assert call["params"]["timezone"] == "UTC"
        assert call["params"]["order"] == "desc"
        assert call["connect_timeout"] == 1.25
        assert call["read_timeout"] == 4.5
        assert "apikey" not in call["params"]
        assert call["headers"]["Authorization"].startswith("apikey ")
    outputsize = {
        call["params"]["interval"]: call["params"]["outputsize"]
        for call in transport.calls
    }
    assert outputsize == {"1h": "31", "4h": "31", "1day": "7"}


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
        CandleNormalizer().normalize(
            candle, provider.discover_instruments()[0]
        ).candle
        for candle in history.signal_bars
    ]

    assert [bar.raw_open_time for bar in history.signal_bars] == [
        "2026-07-30 07:00:00",
        "2026-07-30 08:00:00",
        "2026-07-30 09:00:00",
    ]
    assert all(isinstance(bar, Bar) for bar in normalized)
    assert all(bar is not None and bar.open_time.tzinfo is timezone.utc for bar in normalized)
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
    assert datetime.fromisoformat(
        trigger.candle.raw_close_time.replace("Z", "+00:00")
    ) < as_of


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
    provider, _ = _provider(
        _standard_routes(h1=_response(_fixture(fixture_name)))
    )

    with pytest.raises(MarketDataProviderError) as captured:
        provider.fetch_completed_bars(AS_OF)

    assert captured.value.code is code
    assert captured.value.health_state is HealthState.QUARANTINED
    assert provider.health(AS_OF).state is HealthState.QUARANTINED


def test_empty_response_maps_to_data_unavailable_without_fake_candles() -> None:
    provider, _ = _provider(
        _standard_routes(h1=_empty("1h"), h4=_empty("4h"))
    )

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

    assert len(triggers) == 2
    assert sleeps == [2.0]
    assert len([call for call in transport.calls if call["params"]["interval"] == "1h"]) == 2
    assert provider.health(AS_OF).state is HealthState.HEALTHY


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
    assert health["data"]["mode"] == "PHASE_3A_TWELVE_DATA_RUNTIME"
    assert health["data"]["provider_health"]["state"] == "DATA_UNAVAILABLE"


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


class RecordingEvaluator:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self._delegate = Spect8StrategyEvaluator()

    def evaluate(self, request: Any) -> Any:
        self.requests.append(request)
        return self._delegate.evaluate(request)


def _integration_coordinator(
    database: Path,
) -> tuple[
    MarketDataCoordinator,
    SQLiteProjectionRepository,
    RecordingEvaluator,
]:
    provider, _ = _provider(
        {
            "1h": [
                _golden_payload(
                    "confirmed_buy_h1_01", "signal_bars.csv", "1h"
                )
            ],
            "4h": [_empty("4h")],
            "1day": [
                _golden_payload(
                    "confirmed_buy_h1_01", "daily_bars.csv", "1day"
                )
            ],
        }
    )
    repository = SQLiteProjectionRepository(database)
    repository.initialize()
    evaluator = RecordingEvaluator()
    service = WalkingSkeletonService(evaluator, None, repository)
    coordinator = MarketDataCoordinator(
        provider=provider,
        registry=CanonicalInstrumentRegistry(
            provider.discover_instruments()
        ),
        normalizer=CandleNormalizer(),
        detector=ClosedBarDetector(),
        service=service,
        repository=repository,
        clock=FixedClock(
            datetime(2026, 2, 3, 11, 0, 1, tzinfo=timezone.utc)
        ),
    )
    return coordinator, repository, evaluator


def test_provider_normalizer_replay_and_strategy_pipeline_is_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "twelve-data.sqlite3"
    coordinator, repository, evaluator = _integration_coordinator(database)

    first = coordinator.poll_once()
    second = coordinator.poll_once()
    restarted, reopened, restarted_evaluator = _integration_coordinator(
        database
    )
    third = restarted.poll_once()

    assert first.provider_health.state is HealthState.HEALTHY
    assert len(first.outcomes) == 1
    assert first.outcomes[0].replayed is False
    assert first.outcomes[0].events_created == 7
    assert second.outcomes[0].replayed is True
    assert third.outcomes[0].replayed is True
    assert repository.event_count() == 7
    assert reopened.event_count() == 7
    assert repository.processed_count() == 1
    assert reopened.processed_count() == 1
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
    lines = (ROOT / "backend" / ".env.example").read_text(
        encoding="utf-8"
    ).splitlines()

    assert "TWELVE_DATA_API_KEY=" in lines
    assert all(
        not line.startswith("TWELVE_DATA_API_KEY=")
        or line == "TWELVE_DATA_API_KEY="
        for line in lines
    )
    assert "backend/.env" in (
        ROOT / ".gitignore"
    ).read_text(encoding="utf-8").splitlines()


def test_live_smoke_is_explicitly_not_run_without_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)

    assert smoke_main() == 0
    assert capsys.readouterr().out.strip() == "NOT RUN — API key unavailable"
