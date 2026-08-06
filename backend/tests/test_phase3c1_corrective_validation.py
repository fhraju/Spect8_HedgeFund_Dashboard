from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.market_data.rate_limiter import SlidingWindowRateLimiter
from backend.app.market_data.registry import (
    CanonicalInstrumentRegistry,
    CryptoExchangePolicy,
    twelve_data_instruments,
)
from backend.app.market_data.twelve_data_provider import HttpResponse
from backend.app.market_data.universe_validation import (
    CandidateDefinition,
    DiscoveryKind,
    InstrumentUniverseValidator,
    append_report,
    candidate_definitions,
    sanitized_report,
)


class FakeTime:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.epoch = datetime(2026, 8, 5, 18, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds

    def wall(self) -> datetime:
        return self.epoch + timedelta(seconds=self.elapsed)


DIRECT_COMMODITIES = {
    "XAG/USD": ("Silver Spot", "Precious Metal"),
    "WTI/USD": ("Crude Oil WTI Spot", "Energy Resource"),
    "XBR/USD": ("Brent Spot", "Energy Resource"),
    "HG1": ("Copper Spot", "Industrial Metal"),
}


class CorrectiveTransport:
    def __init__(self, *, h1_status: int = 200) -> None:
        self.h1_status = h1_status
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(
        self,
        path: str,
        params: dict[str, str],
        headers: dict[str, str],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> HttpResponse:
        del headers, connect_timeout, read_timeout
        self.calls.append((path, dict(params)))
        if path == "/commodities":
            symbol = params.get("symbol")
            if symbol in DIRECT_COMMODITIES:
                name, category = DIRECT_COMMODITIES[symbol]
                return self._json(
                    200,
                    {"status": "ok", "data": [{"symbol": symbol, "name": name, "category": category}]},
                )
            return self._json(200, {"status": "ok", "data": []})
        if path == "/cryptocurrencies":
            symbol = params["symbol"]
            base = "Bitcoin" if symbol == "BTC/USD" else "Ethereum"
            return self._json(
                200,
                {
                    "status": "ok",
                    "data": [
                        {
                            "symbol": symbol,
                            "currency_base": base,
                            "currency_quote": "US Dollar",
                            "available_exchanges": ["Binance", "Bitfinex"],
                        }
                    ],
                },
            )
        if path == "/time_series":
            if self.h1_status != 200:
                return self._json(
                    self.h1_status,
                    {"status": "error", "code": 403, "message": "upgrade your plan"},
                )
            first = datetime(2026, 7, 15, tzinfo=timezone.utc)
            values = [
                {
                    "datetime": (first + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S"),
                    "open": "100.100",
                    "high": "100.300",
                    "low": "100.000",
                    "close": "100.200",
                }
                for index in range(509)
            ]
            symbol = params["symbol"]
            return self._json(
                200,
                {
                    "status": "ok",
                    "meta": {
                        "symbol": symbol,
                        "interval": "1h",
                        "exchange": params.get("exchange", "Commodity"),
                        "exchange_timezone": "UTC",
                        "type": "Digital Currency" if params.get("exchange") else "Commodity",
                    },
                    "values": values,
                },
            )
        return self._json(200, {"status": "ok", "data": []})

    @staticmethod
    def _json(status: int, payload: dict[str, object]) -> HttpResponse:
        return HttpResponse(status, {}, json.dumps(payload).encode())


def validator_for(
    definitions: tuple[CandidateDefinition, ...],
    *,
    transport: CorrectiveTransport | None = None,
):
    fake = FakeTime()
    limiter = SlidingWindowRateLimiter(
        monotonic=fake.monotonic,
        sleep=fake.sleep,
        wall_clock=fake.wall,
    )
    selected_transport = transport or CorrectiveTransport()
    validator = InstrumentUniverseValidator(
        api_key="not-in-report",
        registry=CanonicalInstrumentRegistry(twelve_data_instruments()),
        limiter=limiter,
        transport=selected_transport,
        wall_clock=fake.wall,
        definitions=definitions,
    )
    return validator, limiter, selected_transport


@pytest.mark.parametrize(
    ("instrument_id", "symbol", "expected_name"),
    (
        ("XAG_USD", "XAG/USD", "Silver Spot"),
        ("WTI_CRUDE", "WTI/USD", "Crude Oil WTI Spot"),
        ("BRENT_CRUDE", "XBR/USD", "Brent Spot"),
        ("COPPER", "HG1", "Copper Spot"),
    ),
)
def test_direct_commodities_use_catalog_and_complete_h1_pipeline(
    instrument_id: str, symbol: str, expected_name: str
) -> None:
    definition = next(
        item for item in candidate_definitions() if item.canonical_instrument_id == instrument_id
    )
    validator, limiter, transport = validator_for((definition,))
    result = validator.validate((instrument_id,))[0]
    assert transport.calls[0] == (
        "/commodities",
        {"symbol": symbol, "outputsize": "120"},
    )
    assert transport.calls[1][0] == "/time_series"
    assert result.resolved_provider_symbol == symbol
    assert result.provider_instrument_name == expected_name
    assert result.validation_status == "H1_VALIDATED"
    assert result.validation_decision == "ENABLE"
    assert result.pipeline_checks["passed"] is True
    assert result.pipeline_checks["no_synthetic_bars"] is True
    assert result.pipeline_checks["no_forward_filled_bars"] is True
    starts = limiter.starts()
    assert all(right - left >= 8 for left, right in zip(starts, starts[1:]))


def test_exact_symbol_is_authoritative_without_exact_name_allowlist() -> None:
    definition = CandidateDefinition(
        "XAG_USD",
        "Silver",
        DiscoveryKind.EXACT_COMMODITY,
        provider_symbol="XAG/USD",
        expected_name="A deliberately different display label",
        symbol_aliases=("XAG/USD",),
        allowed_provider_types=("commodity",),
    )
    validator, _, _ = validator_for((definition,))
    assert validator.validate(("XAG_USD",))[0].validation_status == "H1_VALIDATED"


def test_crypto_ambiguity_requires_explicit_exchange_and_does_not_try_h1() -> None:
    definition = replace(
        next(item for item in candidate_definitions() if item.canonical_instrument_id == "BTC_USD"),
        selected_exchange=None,
    )
    validator, _, transport = validator_for((definition,))
    result = validator.validate(("BTC_USD",))[0]
    assert result.validation_status == "AMBIGUOUS_DISCOVERY"
    assert result.h1_request_status == "NOT_RUN"
    assert [path for path, _ in transport.calls] == ["/cryptocurrencies"]


@pytest.mark.parametrize(("instrument_id", "symbol"), (("BTC_USD", "BTC/USD"), ("ETH_USD", "ETH/USD")))
def test_binance_crypto_mapping_is_deterministic_and_attempts_h1(
    instrument_id: str, symbol: str
) -> None:
    definition = next(
        item for item in candidate_definitions() if item.canonical_instrument_id == instrument_id
    )
    validator, _, transport = validator_for((definition,))
    result = validator.validate((instrument_id,))[0]
    assert result.exchange == "Binance"
    assert result.resolved_provider_symbol == symbol
    assert transport.calls[-1] == (
        "/time_series",
        {
            "symbol": symbol,
            "interval": "1h",
            "outputsize": "509",
            "timezone": "UTC",
            "format": "JSON",
            "exchange": "Binance",
        },
    )
    assert result.validation_status == "H1_VALIDATED"


def test_plan_rejection_requires_real_h1_response_and_keeps_disabled() -> None:
    definition = next(item for item in candidate_definitions() if item.canonical_instrument_id == "XAG_USD")
    validator, _, _ = validator_for((definition,), transport=CorrectiveTransport(h1_status=403))
    result = validator.validate(("XAG_USD",))[0]
    assert result.validation_status == "PLAN_RESTRICTED"
    assert result.h1_http_status == 403
    assert result.h1_response_error == "403: upgrade your plan"
    assert result.current_plan_access == "DENIED"
    assert result.validation_decision == "KEEP_DISABLED"


def test_crypto_policy_is_typed_shared_and_can_be_unselected() -> None:
    configured = twelve_data_instruments(crypto_policy=CryptoExchangePolicy("Binance"))
    unselected = twelve_data_instruments(crypto_policy=CryptoExchangePolicy())
    assert {item.exchange for item in configured if item.asset_class == "CRYPTO"} == {"Binance"}
    assert {item.exchange for item in unselected if item.asset_class == "CRYPTO"} == {None}


def test_append_retains_previous_validation_run_and_attempts() -> None:
    definition = next(item for item in candidate_definitions() if item.canonical_instrument_id == "XAG_USD")
    first_validator, first_limiter, _ = validator_for((definition,))
    second_validator, second_limiter, _ = validator_for((definition,))
    first = sanitized_report(first_validator.validate(("XAG_USD",)), first_limiter)
    second = sanitized_report(second_validator.validate(("XAG_USD",)), second_limiter)
    merged = append_report(first, second)
    assert len(merged["validation_runs"]) == 2
    assert all(run["candidates"][0]["discovery_attempts"] for run in merged["validation_runs"])
    assert merged["request_count"] == first["request_count"] + second["request_count"]
