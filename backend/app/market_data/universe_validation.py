from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from ..domain import Timeframe
from ..engine.current_daily_filter import (
    DailyFilterUnavailableError,
    build_daily_filter_snapshot,
)
from ..engine.models import CURRENT_D1_FILTER_V2, StrategyRequest
from ..engine.strategy import Spect8StrategyEvaluator
from ..repository import SQLiteProjectionRepository
from .daily_aggregator import (
    ActualDataNewYorkDailyAggregator,
    NewYorkDailyAggregator,
)
from .credit_budget import CreditBudgetExhausted, DailyCreditBudgetGuard
from .forex_profile import BrokerAlignedH4Aggregator, market_h1_bars
from .exchange_aggregator import ExchangeSessionH4Aggregator
from .models import (
    CanonicalInstrument,
    InstrumentKind,
    RawProviderCandle,
    TimestampSemantics,
)
from .normalizer import CandleNormalizer
from .rate_limiter import SlidingWindowRateLimiter
from .registry import (
    CANDIDATE_INSTRUMENT_IDS,
    ETF_INSTRUMENT_IDS,
    CanonicalInstrumentRegistry,
)
from .twelve_data_provider import (
    HttpResponse,
    StdlibTwelveDataHttpTransport,
    TransportConnectionError,
    TransportTimeoutError,
    TwelveDataHttpTransport,
)
from .us_equity_calendar import EquityH1Disposition, classify_etf_h1_open


class DiscoveryKind(StrEnum):
    EXACT_COMMODITY = "EXACT_COMMODITY"
    COMMODITY_CATALOG = "COMMODITY_CATALOG"
    CRYPTO_CATALOG = "CRYPTO_CATALOG"
    INDEX_CATALOG_AND_ALIASES = "INDEX_CATALOG_AND_ALIASES"
    BOND_CATALOG_AND_ALIASES = "BOND_CATALOG_AND_ALIASES"
    US_ETF_EXACT_LISTING = "US_ETF_EXACT_LISTING"


@dataclass(frozen=True, slots=True)
class CandidateDefinition:
    canonical_instrument_id: str
    requested_instrument_name: str
    discovery_kind: DiscoveryKind
    provider_symbol: str | None = None
    expected_name: str | None = None
    symbol_aliases: tuple[str, ...] = ()
    allowed_provider_types: tuple[str, ...] = ()
    selected_exchange: str | None = None
    selected_mic: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryAttempt:
    query_type: str
    provider_endpoint: str
    query_parameters: dict[str, str]
    request_start_utc: str
    request_finish_utc: str
    http_status: int | None
    provider_status: str | None
    response_error: str | None
    candidates: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ValidationResult:
    canonical_instrument_id: str
    requested_instrument_name: str
    asset_class: str
    discovery_kind: str
    resolved_provider_symbol: str | None
    provider_instrument_name: str | None
    exchange: str | None
    mic_code: str | None
    provider_instrument_type: str | None
    provider_timezone: str | None
    minimum_plan_access: str | None
    current_plan_access: str
    h1_request_status: str
    h1_http_status: int | None
    h1_provider_status: str | None
    h1_response_error: str | None
    returned_candle_count: int
    completed_candle_count: int
    forming_candle_count: int
    structurally_partial_candle_count: int
    duplicate_candle_count: int
    quarantined_candle_count: int
    latest_timestamp: str | None
    price_precision: int | None
    point_size: str | None
    session_policy: str
    validation_status: str
    validation_decision: str
    error_reason: str | None
    discovery_request_start_utc: str
    discovery_request_finish_utc: str
    h1_request_start_utc: str | None
    h1_request_finish_utc: str | None
    discovery_candidates: tuple[dict[str, Any], ...]
    discovery_attempts: tuple[DiscoveryAttempt, ...]
    h1_attempts: tuple[DiscoveryAttempt, ...]
    pipeline_checks: dict[str, Any]
    candle_semantics: dict[str, Any]


_CANDIDATES = (
    CandidateDefinition(
        "XAG_USD",
        "Silver / US Dollar",
        DiscoveryKind.EXACT_COMMODITY,
        provider_symbol="XAG/USD",
        expected_name="Silver Spot",
        symbol_aliases=("XAG/USD",),
        allowed_provider_types=("commodity", "physical currency"),
    ),
    CandidateDefinition(
        "SP_500",
        "S&P 500 Index",
        DiscoveryKind.INDEX_CATALOG_AND_ALIASES,
        symbol_aliases=("SPX", "GSPC", "S&P 500"),
        allowed_provider_types=("index",),
    ),
    CandidateDefinition(
        "NASDAQ_100",
        "Nasdaq 100 Index",
        DiscoveryKind.INDEX_CATALOG_AND_ALIASES,
        symbol_aliases=("NDX", "NASDAQ 100"),
        allowed_provider_types=("index",),
    ),
    CandidateDefinition(
        "DOW_30",
        "Dow Jones Industrial Average",
        DiscoveryKind.INDEX_CATALOG_AND_ALIASES,
        symbol_aliases=("DJI", "DJIA", "Dow Jones Industrial Average"),
        allowed_provider_types=("index",),
    ),
    CandidateDefinition(
        "DAX_40",
        "DAX 40 Index",
        DiscoveryKind.INDEX_CATALOG_AND_ALIASES,
        symbol_aliases=("DAX", "GDAXI", "DAX 40"),
        allowed_provider_types=("index",),
    ),
    CandidateDefinition(
        "FTSE_100",
        "FTSE 100 Index",
        DiscoveryKind.INDEX_CATALOG_AND_ALIASES,
        symbol_aliases=("FTSE", "UKX", "FTSE 100"),
        allowed_provider_types=("index",),
    ),
    CandidateDefinition(
        "NIKKEI_225",
        "Nikkei 225 Index",
        DiscoveryKind.INDEX_CATALOG_AND_ALIASES,
        symbol_aliases=("N225", "NIKKEI 225"),
        allowed_provider_types=("index",),
    ),
    CandidateDefinition(
        "WTI_CRUDE",
        "WTI Crude Oil Spot",
        DiscoveryKind.EXACT_COMMODITY,
        provider_symbol="WTI/USD",
        expected_name="Crude Oil WTI Spot",
        symbol_aliases=("WTI/USD",),
        allowed_provider_types=("commodity",),
    ),
    CandidateDefinition(
        "BRENT_CRUDE",
        "Brent Crude Oil Spot",
        DiscoveryKind.EXACT_COMMODITY,
        provider_symbol="XBR/USD",
        expected_name="Brent Spot",
        symbol_aliases=("XBR/USD",),
        allowed_provider_types=("commodity",),
    ),
    CandidateDefinition(
        "NATURAL_GAS",
        "Natural Gas",
        DiscoveryKind.COMMODITY_CATALOG,
        symbol_aliases=("NG1", "NG/USD", "NATURAL GAS"),
        allowed_provider_types=("commodity",),
    ),
    CandidateDefinition(
        "COPPER",
        "Copper Spot",
        DiscoveryKind.EXACT_COMMODITY,
        provider_symbol="HG1",
        expected_name="Copper Spot",
        symbol_aliases=("HG1",),
        allowed_provider_types=("commodity",),
    ),
    CandidateDefinition(
        "BTC_USD",
        "Bitcoin / US Dollar",
        DiscoveryKind.CRYPTO_CATALOG,
        provider_symbol="BTC/USD",
        expected_name="Bitcoin / US Dollar",
        symbol_aliases=("BTC/USD",),
        allowed_provider_types=("digital currency", "cryptocurrency"),
        selected_exchange="Binance",
    ),
    CandidateDefinition(
        "ETH_USD",
        "Ethereum / US Dollar",
        DiscoveryKind.CRYPTO_CATALOG,
        provider_symbol="ETH/USD",
        expected_name="Ethereum / US Dollar",
        symbol_aliases=("ETH/USD",),
        allowed_provider_types=("digital currency", "cryptocurrency"),
        selected_exchange="Binance",
    ),
    CandidateDefinition(
        "VIX",
        "CBOE Volatility Index",
        DiscoveryKind.INDEX_CATALOG_AND_ALIASES,
        symbol_aliases=("VIX", "CBOE Volatility Index"),
        allowed_provider_types=("index",),
    ),
    CandidateDefinition(
        "US_10Y_YIELD",
        "US 10-Year Treasury Yield",
        DiscoveryKind.BOND_CATALOG_AND_ALIASES,
        symbol_aliases=("US10Y", "US 10Y", "US 10-Year Treasury Yield"),
        allowed_provider_types=("bond", "index"),
    ),
    CandidateDefinition(
        "DXY",
        "US Dollar Index Market",
        DiscoveryKind.INDEX_CATALOG_AND_ALIASES,
        symbol_aliases=("DXY", "US Dollar Index"),
        allowed_provider_types=("index",),
    ),
    CandidateDefinition(
        "AUS_200",
        "Australia 200 Index",
        DiscoveryKind.INDEX_CATALOG_AND_ALIASES,
        symbol_aliases=("AUS200", "ASX 200", "S&P/ASX 200"),
        allowed_provider_types=("index",),
    ),
)


_ETF_CANDIDATES = (
    CandidateDefinition("SPY_US_ETF", "S&P 500 ETF Proxy", DiscoveryKind.US_ETF_EXACT_LISTING, "SPY", symbol_aliases=("SPY",), allowed_provider_types=("etf",), selected_exchange="NYSE Arca", selected_mic="ARCX"),
    CandidateDefinition("QQQ_US_ETF", "Nasdaq 100 ETF Proxy", DiscoveryKind.US_ETF_EXACT_LISTING, "QQQ", symbol_aliases=("QQQ",), allowed_provider_types=("etf",), selected_exchange="NASDAQ", selected_mic="XNMS"),
    CandidateDefinition("IWM_US_ETF", "Russell 2000 ETF Proxy", DiscoveryKind.US_ETF_EXACT_LISTING, "IWM", symbol_aliases=("IWM",), allowed_provider_types=("etf",), selected_exchange="NYSE Arca", selected_mic="ARCX"),
    CandidateDefinition("FEZ_US_ETF", "Eurozone 50 ETF Proxy", DiscoveryKind.US_ETF_EXACT_LISTING, "FEZ", symbol_aliases=("FEZ",), allowed_provider_types=("etf",), selected_exchange="NYSE Arca", selected_mic="ARCX"),
    CandidateDefinition("EWJ_US_ETF", "Japan Equity ETF Proxy", DiscoveryKind.US_ETF_EXACT_LISTING, "EWJ", symbol_aliases=("EWJ",), allowed_provider_types=("etf",), selected_exchange="NYSE Arca", selected_mic="ARCX"),
    CandidateDefinition("EEM_US_ETF", "Emerging Markets ETF Proxy", DiscoveryKind.US_ETF_EXACT_LISTING, "EEM", symbol_aliases=("EEM",), allowed_provider_types=("etf",), selected_exchange="NYSE Arca", selected_mic="ARCX"),
    CandidateDefinition("TLT_US_ETF", "US Long Treasury ETF", DiscoveryKind.US_ETF_EXACT_LISTING, "TLT", symbol_aliases=("TLT",), allowed_provider_types=("etf",), selected_exchange="NASDAQ", selected_mic="XNMS"),
    CandidateDefinition("HYG_US_ETF", "High-Yield Credit ETF", DiscoveryKind.US_ETF_EXACT_LISTING, "HYG", symbol_aliases=("HYG",), allowed_provider_types=("etf",), selected_exchange="NYSE Arca", selected_mic="ARCX"),
    CandidateDefinition("SLV_US_ETF", "Silver ETF Proxy", DiscoveryKind.US_ETF_EXACT_LISTING, "SLV", symbol_aliases=("SLV",), allowed_provider_types=("etf",), selected_exchange="NYSE Arca", selected_mic="ARCX"),
    CandidateDefinition("USO_US_ETF", "WTI Oil ETF Proxy", DiscoveryKind.US_ETF_EXACT_LISTING, "USO", symbol_aliases=("USO",), allowed_provider_types=("etf",), selected_exchange="NYSE Arca", selected_mic="ARCX"),
    CandidateDefinition("UNG_US_ETF", "Natural Gas ETF Proxy", DiscoveryKind.US_ETF_EXACT_LISTING, "UNG", symbol_aliases=("UNG",), allowed_provider_types=("etf",), selected_exchange="NYSE Arca", selected_mic="ARCX"),
    CandidateDefinition("DBA_US_ETF", "Agriculture ETF Basket", DiscoveryKind.US_ETF_EXACT_LISTING, "DBA", symbol_aliases=("DBA",), allowed_provider_types=("etf",), selected_exchange="NYSE Arca", selected_mic="ARCX"),
    CandidateDefinition("VIXM_US_ETF", "Volatility ETF Proxy", DiscoveryKind.US_ETF_EXACT_LISTING, "VIXM", symbol_aliases=("VIXM",), allowed_provider_types=("etf",), selected_exchange="CBOE BZX", selected_mic="BATS"),
)


def candidate_definitions() -> tuple[CandidateDefinition, ...]:
    return _CANDIDATES


def etf_candidate_definitions() -> tuple[CandidateDefinition, ...]:
    if tuple(item.canonical_instrument_id for item in _ETF_CANDIDATES) != ETF_INSTRUMENT_IDS:
        raise ValueError("ETF validation definitions are not in canonical order")
    return _ETF_CANDIDATES


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize(value: object) -> str:
    return " ".join(str(value or "").strip().lower().replace("â€“", "-").split())


def _access_plan(value: Mapping[str, Any]) -> str | None:
    access = value.get("access")
    if not isinstance(access, Mapping):
        return None
    plan = access.get("plan") or access.get("global")
    return str(plan) if plan else None


def _candidate(
    value: Mapping[str, Any],
    *,
    instrument_type: str | None = None,
    exchange: str | None = None,
) -> dict[str, Any]:
    return {
        "symbol": value.get("symbol"),
        "instrument_name": value.get("instrument_name") or value.get("name"),
        "exchange": exchange if exchange is not None else value.get("exchange"),
        "mic_code": value.get("mic_code"),
        "exchange_timezone": value.get("exchange_timezone"),
        "instrument_type": (
            instrument_type
            or value.get("instrument_type")
            or value.get("type")
            or value.get("category")
        ),
        "country": value.get("country"),
        "currency": value.get("currency"),
        "minimum_plan": _access_plan(value),
        "category": value.get("category"),
        "description": value.get("description"),
    }


def _deduplicate(values: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    selected: dict[tuple[object, ...], dict[str, Any]] = {}
    for item in values:
        key = (
            item.get("symbol"),
            item.get("exchange"),
            item.get("mic_code"),
            item.get("instrument_type"),
            item.get("instrument_name"),
        )
        selected.setdefault(key, item)
    return tuple(selected.values())


def _json_response(response: HttpResponse) -> Mapping[str, Any]:
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("provider returned non-JSON data") from error
    if not isinstance(value, Mapping):
        raise ValueError("provider returned an invalid JSON object")
    return value


def _provider_error(payload: Mapping[str, Any], status_code: int) -> str:
    message = str(payload.get("message") or payload.get("status") or "request failed")
    lowered = message.lower()
    if status_code in {401, 403} or "plan" in lowered or "subscription" in lowered:
        return "PLAN_RESTRICTED"
    if status_code == 429 or "rate limit" in lowered:
        return "RATE_LIMITED"
    return "PROVIDER_ERROR"


def _response_error(payload: Mapping[str, Any], status_code: int) -> str | None:
    if status_code == 200 and payload.get("status") != "error":
        return None
    message = payload.get("message") or payload.get("status") or "request failed"
    code = payload.get("code")
    return f"{code}: {message}" if code not in (None, "") else str(message)


def _session_policy(asset_class: str) -> str:
    if asset_class in {"FOREX", "METAL", "PRECIOUS_METAL"}:
        return "FOREX_WEEKDAY_ACTUAL_CANDLES"
    if asset_class == "CRYPTO":
        return "FROZEN_STRATEGY_WEEKEND_POLICY"
    return "EXCHANGE_SESSION_ACTUAL_CANDLES"


def _price_metadata(values: list[Mapping[str, Any]]) -> tuple[int | None, str | None]:
    price_strings = [
        str(row[field])
        for row in values
        for field in ("open", "high", "low", "close")
        if row.get(field) not in (None, "")
    ]
    if not price_strings:
        return None, None
    precision = max(
        len(value.partition(".")[2].rstrip("0")) if "." in value else 0
        for value in price_strings
    )
    return precision, str(Decimal("1").scaleb(-precision))


def _parse_provider_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class InstrumentUniverseValidator:
    """Asset-aware discovery and real H1 access validation with full evidence."""

    def __init__(
        self,
        *,
        api_key: str,
        registry: CanonicalInstrumentRegistry,
        limiter: SlidingWindowRateLimiter,
        transport: TwelveDataHttpTransport | None = None,
        wall_clock: Any = _utc_now,
        bootstrap_outputsize: int = 509,
        definitions: tuple[CandidateDefinition, ...] = _CANDIDATES,
        credit_budget: DailyCreditBudgetGuard | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("TWELVE_DATA_API_KEY is required")
        self._api_key = api_key
        self._registry = registry
        self._limiter = limiter
        self._transport = transport or StdlibTwelveDataHttpTransport()
        self._wall_clock = wall_clock
        self._bootstrap_outputsize = bootstrap_outputsize
        self._definitions = definitions
        self._credit_budget = credit_budget

    def _attempt(
        self,
        *,
        instrument_id: str,
        query_type: str,
        endpoint: str,
        parameters: Mapping[str, str],
    ) -> tuple[DiscoveryAttempt, Mapping[str, Any] | None]:
        label = f"validation:{instrument_id}:{query_type.lower()}"
        self._limiter.acquire(label)
        started = self._wall_clock()
        payload: Mapping[str, Any] | None = None
        status_code: int | None = None
        error: str | None = None
        reservation_id: int | None = None
        try:
            if self._credit_budget is not None:
                reservation_id = self._credit_budget.reserve_request(
                    endpoint,
                    "validation",
                    started_at=started,
                )
            response = self._transport.get(
                endpoint,
                parameters,
                {"Authorization": f"apikey {self._api_key}"},
                connect_timeout=5.0,
                read_timeout=20.0,
            )
            status_code = response.status_code
            payload = _json_response(response)
            error = _response_error(payload, status_code)
            if self._credit_budget is not None and reservation_id is not None:
                self._credit_budget.finalize_request(
                    reservation_id,
                    status=f"HTTP_{status_code}",
                    http_status=status_code,
                    headers=response.headers,
                )
        except CreditBudgetExhausted:
            error = "CREDIT_BUDGET_EXHAUSTED"
        except (TransportTimeoutError, TransportConnectionError, ValueError) as caught:
            error = type(caught).__name__
            if self._credit_budget is not None and reservation_id is not None:
                self._credit_budget.finalize_request(
                    reservation_id,
                    status=error,
                    http_status=None,
                )
        finished = self._wall_clock()
        candidates = self._candidates_for_endpoint(endpoint, payload)
        return (
            DiscoveryAttempt(
                query_type=query_type,
                provider_endpoint=endpoint,
                query_parameters=dict(parameters),
                request_start_utc=_iso(started),
                request_finish_utc=_iso(finished),
                http_status=status_code,
                provider_status=(str(payload.get("status")) if payload else None),
                response_error=error,
                candidates=candidates,
            ),
            payload,
        )

    @staticmethod
    def _candidates_for_endpoint(
        endpoint: str, payload: Mapping[str, Any] | None
    ) -> tuple[dict[str, Any], ...]:
        if payload is None:
            return ()
        if endpoint == "/etfs/list":
            result = payload.get("result")
            if not isinstance(result, Mapping) or not isinstance(result.get("list"), list):
                return ()
            exchanges = {
                "ARCX": "NYSE Arca",
                "XNAS": "NASDAQ",
                "BATS": "CBOE BZX",
            }
            expanded: list[dict[str, Any]] = []
            for item in result["list"]:
                if not isinstance(item, Mapping):
                    continue
                enriched = dict(item)
                enriched["exchange"] = exchanges.get(str(item.get("mic_code") or ""))
                enriched["exchange_timezone"] = "America/New_York"
                enriched["instrument_type"] = "ETF"
                expanded.append(_candidate(enriched))
            return _deduplicate(expanded)
        if not isinstance(payload.get("data"), list):
            return ()
        values = [item for item in payload["data"] if isinstance(item, Mapping)]
        if endpoint == "/cryptocurrencies":
            expanded: list[dict[str, Any]] = []
            for item in values:
                exchanges = item.get("available_exchanges")
                if not isinstance(exchanges, list) or not exchanges:
                    exchanges = [None]
                name = (
                    f"{item.get('currency_base') or ''} / "
                    f"{item.get('currency_quote') or ''}"
                ).strip()
                enriched = dict(item)
                enriched["name"] = name
                for exchange in exchanges:
                    expanded.append(
                        _candidate(
                            enriched,
                            instrument_type="Digital Currency",
                            exchange=str(exchange) if exchange else None,
                        )
                    )
            return _deduplicate(expanded)
        if endpoint == "/commodities":
            return _deduplicate(
                [_candidate(item, instrument_type="Commodity") for item in values]
            )
        if endpoint == "/indices":
            return _deduplicate(
                [_candidate(item, instrument_type="Index") for item in values]
            )
        if endpoint == "/bonds":
            return _deduplicate(
                [_candidate(item, instrument_type="Bond") for item in values]
            )
        return _deduplicate([_candidate(item) for item in values])

    def validate(
        self, instrument_ids: tuple[str, ...] | None = None
    ) -> tuple[ValidationResult, ...]:
        definitions = {
            item.canonical_instrument_id: item for item in self._definitions
        }
        if self._definitions is _CANDIDATES and tuple(definitions) != CANDIDATE_INSTRUMENT_IDS:
            raise ValueError("candidate definitions are not in canonical registry order")
        selected = instrument_ids or tuple(definitions)
        unknown = set(selected).difference(definitions)
        if unknown:
            raise ValueError("unknown candidate instruments: " + ", ".join(sorted(unknown)))
        return tuple(
            self._validate_one(self._registry.by_id(item), definitions[item])
            for item in selected
        )

    def _discover(
        self, definition: CandidateDefinition
    ) -> tuple[tuple[DiscoveryAttempt, ...], tuple[dict[str, Any], ...]]:
        attempts: list[DiscoveryAttempt] = []
        values: list[dict[str, Any]] = []

        def run(query_type: str, endpoint: str, parameters: dict[str, str]) -> None:
            attempt, _ = self._attempt(
                instrument_id=definition.canonical_instrument_id,
                query_type=query_type,
                endpoint=endpoint,
                parameters=parameters,
            )
            attempts.append(attempt)
            values.extend(attempt.candidates)

        if definition.discovery_kind is DiscoveryKind.EXACT_COMMODITY:
            assert definition.provider_symbol is not None
            run(
                "COMMODITY_EXACT_SYMBOL",
                "/commodities",
                {"symbol": definition.provider_symbol, "outputsize": "120"},
            )
        elif definition.discovery_kind is DiscoveryKind.COMMODITY_CATALOG:
            run("COMMODITY_CATALOG", "/commodities", {"outputsize": "120"})
            if not self._matches(definition, _deduplicate(values)):
                for alias in definition.symbol_aliases:
                    run(
                        "COMMODITY_ALIAS_SEARCH",
                        "/symbol_search",
                        {"symbol": alias, "outputsize": "120", "show_plan": "true"},
                    )
        elif definition.discovery_kind is DiscoveryKind.CRYPTO_CATALOG:
            assert definition.provider_symbol is not None
            run(
                "CRYPTO_EXACT_PAIR",
                "/cryptocurrencies",
                {"symbol": definition.provider_symbol, "outputsize": "120"},
            )
        elif definition.discovery_kind is DiscoveryKind.INDEX_CATALOG_AND_ALIASES:
            for alias in definition.symbol_aliases:
                if self._matches(definition, _deduplicate(values)):
                    break
                run(
                    "INDEX_ALIAS_CATALOG",
                    "/indices",
                    {"symbol": alias, "outputsize": "120", "show_plan": "true"},
                )
            if not self._matches(definition, _deduplicate(values)):
                for alias in definition.symbol_aliases:
                    run(
                        "INDEX_PROXY_SEARCH",
                        "/symbol_search",
                        {"symbol": alias, "outputsize": "120", "show_plan": "true"},
                    )
        elif definition.discovery_kind is DiscoveryKind.BOND_CATALOG_AND_ALIASES:
            for alias in definition.symbol_aliases:
                if self._matches(definition, _deduplicate(values)):
                    break
                run(
                    "BOND_RATE_ALIAS_CATALOG",
                    "/bonds",
                    {"symbol": alias, "country": "United States", "outputsize": "120", "show_plan": "true"},
                )
            if not self._matches(definition, _deduplicate(values)):
                for alias in definition.symbol_aliases:
                    run(
                        "BOND_RATE_PROXY_SEARCH",
                        "/symbol_search",
                    {"symbol": alias, "outputsize": "120", "show_plan": "true"},
                    )
        elif definition.discovery_kind is DiscoveryKind.US_ETF_EXACT_LISTING:
            assert definition.provider_symbol is not None
            run(
                "US_ETF_EXACT_SYMBOL",
                "/etfs/list",
                {
                    "symbol": definition.provider_symbol,
                    "country": "United States",
                    "outputsize": "120",
                },
            )
        else:
            raise AssertionError(f"unsupported discovery kind {definition.discovery_kind}")
        return tuple(attempts), _deduplicate(values)

    @staticmethod
    def _matches(
        definition: CandidateDefinition,
        candidates: tuple[dict[str, Any], ...],
    ) -> list[dict[str, Any]]:
        aliases = {_normalize(item) for item in definition.symbol_aliases}
        allowed_types = {_normalize(item) for item in definition.allowed_provider_types}
        matches: list[dict[str, Any]] = []
        for item in candidates:
            symbol = _normalize(item.get("symbol"))
            item_type = _normalize(item.get("instrument_type"))
            exact_symbol = (
                definition.provider_symbol is not None
                and symbol == _normalize(definition.provider_symbol)
            )
            alias_symbol = bool(symbol and symbol in aliases)
            type_allowed = not allowed_types or item_type in allowed_types
            exchange_allowed = (
                (
                    definition.selected_mic is not None
                    and _normalize(item.get("mic_code"))
                    == _normalize(definition.selected_mic)
                )
                or (
                    definition.selected_mic is None
                    and (
                        definition.selected_exchange is None
                        or _normalize(item.get("exchange"))
                        == _normalize(definition.selected_exchange)
                    )
                )
            )
            if (exact_symbol or (alias_symbol and type_allowed)) and exchange_allowed:
                matches.append(item)
        return matches

    def _validate_one(
        self, instrument: CanonicalInstrument, definition: CandidateDefinition
    ) -> ValidationResult:
        attempts, candidates = self._discover(definition)
        matches = self._matches(definition, candidates)
        unique = {
            (
                item.get("symbol"),
                item.get("exchange"),
                item.get("mic_code"),
                item.get("instrument_type"),
            ): item
            for item in matches
        }
        start = attempts[0].request_start_utc if attempts else _iso(self._wall_clock())
        finish = attempts[-1].request_finish_utc if attempts else start
        if len(unique) != 1:
            unavailable = not unique
            provider_errors = [item.response_error for item in attempts if item.response_error]
            reason = (
                "No exact direct-market candidate was resolved after asset-aware discovery."
                if unavailable
                else "Multiple exact direct-market candidates require explicit exchange selection."
            )
            if unavailable and provider_errors:
                reason += " Provider attempts: " + "; ".join(provider_errors)
            return self._failure(
                instrument,
                definition,
                start,
                finish,
                "DISCOVERY_UNAVAILABLE" if unavailable else "AMBIGUOUS_DISCOVERY",
                reason,
                candidates=candidates,
                attempts=attempts,
            )
        match = next(iter(unique.values()))
        return self._validate_h1(
            instrument,
            definition,
            match,
            candidates,
            attempts,
            start,
            finish,
        )

    def _validate_h1(
        self,
        instrument: CanonicalInstrument,
        definition: CandidateDefinition,
        match: Mapping[str, Any],
        candidates: tuple[dict[str, Any], ...],
        attempts: tuple[DiscoveryAttempt, ...],
        discovery_start: str,
        discovery_finish: str,
    ) -> ValidationResult:
        params = {
            "symbol": str(match["symbol"]),
            "interval": "1h",
            "outputsize": str(self._bootstrap_outputsize),
            "timezone": "UTC",
            "format": "JSON",
        }
        if match.get("mic_code"):
            params["mic_code"] = str(match["mic_code"])
        elif match.get("exchange"):
            params["exchange"] = str(match["exchange"])
        attempt, series = self._attempt(
            instrument_id=instrument.instrument_id,
            query_type="H1_TIME_SERIES",
            endpoint="/time_series",
            parameters=params,
        )
        h1_attempts = [attempt]
        if (
            (series is None or attempt.http_status != 200 or series.get("status") == "error")
            and match.get("mic_code")
            and match.get("exchange")
            and _provider_error(series or {}, attempt.http_status or 0) != "PLAN_RESTRICTED"
        ):
            exchange_params = dict(params)
            exchange_params.pop("mic_code", None)
            exchange_params["exchange"] = str(match["exchange"])
            attempt, series = self._attempt(
                instrument_id=instrument.instrument_id,
                query_type="H1_TIME_SERIES_EXCHANGE_RETRY",
                endpoint="/time_series",
                parameters=exchange_params,
            )
            h1_attempts.append(attempt)
        h1_start = h1_attempts[0].request_start_utc
        h1_finish = h1_attempts[-1].request_finish_utc
        if series is None or attempt.http_status != 200 or series.get("status") == "error":
            status = _provider_error(series or {}, attempt.http_status or 0)
            return self._failure(
                instrument,
                definition,
                discovery_start,
                discovery_finish,
                status,
                attempt.response_error or "Provider H1 request failed.",
                match=match,
                candidates=candidates,
                attempts=attempts,
                h1_attempt=attempt,
                h1_attempts=tuple(h1_attempts),
            )
        meta = series.get("meta")
        values = series.get("values")
        if not isinstance(meta, Mapping) or not isinstance(values, list):
            return self._failure(
                instrument,
                definition,
                discovery_start,
                discovery_finish,
                "INVALID_H1_PAYLOAD",
                "H1 response did not contain metadata and candles.",
                match=match,
                candidates=candidates,
                attempts=attempts,
                h1_attempt=attempt,
                h1_attempts=tuple(h1_attempts),
            )
        typed_values = [item for item in values if isinstance(item, Mapping)]
        now = self._wall_clock().astimezone(timezone.utc)
        completed: list[Mapping[str, Any]] = []
        forming = 0
        invalid = 0
        structurally_partial = 0
        duplicate = 0
        outside_regular_session = 0
        seen_opens: set[datetime] = set()
        for row in typed_values:
            try:
                open_time = _parse_provider_time(row.get("datetime"))
                prices = tuple(
                    Decimal(str(row[field])) for field in ("open", "high", "low", "close")
                )
            except (KeyError, InvalidOperation, TypeError, ValueError):
                invalid += 1
                continue
            if open_time in seen_opens:
                duplicate += 1
                continue
            seen_opens.add(open_time)
            open_price, high, low, close = prices
            if min(prices) <= 0 or low > min(open_price, close) or high < max(open_price, close):
                invalid += 1
                continue
            if instrument.instrument_kind is InstrumentKind.ETF:
                disposition = classify_etf_h1_open(open_time, as_of=now)
                if disposition is EquityH1Disposition.VALID_COMPLETED:
                    completed.append(row)
                elif disposition is EquityH1Disposition.FORMING:
                    forming += 1
                elif disposition is EquityH1Disposition.STRUCTURAL_PARTIAL:
                    structurally_partial += 1
                else:
                    outside_regular_session += 1
            elif open_time + timedelta(hours=1) >= now:
                forming += 1
            else:
                completed.append(row)
        identity_valid = str(meta.get("symbol")) == str(match["symbol"])
        # ETF discovery and the H1 request are scoped by the exact provider MIC.
        # Twelve Data's time-series metadata may return a venue family label
        # (for example "NYSE") while /etfs/list returns the exact listing venue
        # ("NYSE Arca").  Comparing those display labels would reject an
        # otherwise deterministic MIC-scoped response.
        if instrument.instrument_kind is not InstrumentKind.ETF and match.get("exchange"):
            identity_valid = identity_valid and _normalize(meta.get("exchange")) == _normalize(
                match.get("exchange")
            )
        precision, point_size = _price_metadata(list(completed))
        required_completed = (
            min(180, self._bootstrap_outputsize)
            if instrument.instrument_kind is InstrumentKind.ETF
            else min(500, self._bootstrap_outputsize)
        )
        sufficient = len(completed) >= required_completed
        validation_status = (
            "H1_VALIDATED"
            if identity_valid and sufficient and invalid == 0 and precision is not None
            else "INVALID_H1_DATA"
        )
        reason = None
        if not identity_valid:
            reason = "Returned metadata did not identify the selected direct market."
        elif invalid:
            reason = f"Provider returned {invalid} malformed or invalid OHLC rows."
        elif not sufficient:
            reason = (
                f"H1 validation requires at least {required_completed} "
                f"completed candles; received {len(completed)}."
            )
        latest = max(
            (_iso(_parse_provider_time(item["datetime"]) + timedelta(hours=1)) for item in completed),
            default=None,
        )
        pipeline_checks = self._pipeline_checks(
            instrument,
            match,
            completed,
            precision,
            point_size,
            now,
        )
        if validation_status == "H1_VALIDATED" and not pipeline_checks["passed"]:
            validation_status = "PIPELINE_VALIDATION_FAILED"
            reason = str(pipeline_checks["exact_error"])
        return ValidationResult(
            canonical_instrument_id=instrument.instrument_id,
            requested_instrument_name=definition.requested_instrument_name,
            asset_class=instrument.asset_class,
            discovery_kind=definition.discovery_kind.value,
            resolved_provider_symbol=str(match["symbol"]),
            provider_instrument_name=str(match.get("instrument_name") or "") or None,
            exchange=str(match.get("exchange") or "") or None,
            mic_code=str(match.get("mic_code") or "") or None,
            provider_instrument_type=str(match.get("instrument_type") or "") or None,
            provider_timezone=str(meta.get("exchange_timezone") or match.get("exchange_timezone") or "") or None,
            minimum_plan_access=str(match.get("minimum_plan") or "") or None,
            current_plan_access="GRANTED",
            h1_request_status="OK",
            h1_http_status=attempt.http_status,
            h1_provider_status=str(series.get("status") or "ok"),
            h1_response_error=None,
            returned_candle_count=len(typed_values),
            completed_candle_count=len(completed),
            forming_candle_count=forming,
            structurally_partial_candle_count=structurally_partial,
            duplicate_candle_count=duplicate,
            quarantined_candle_count=invalid,
            latest_timestamp=latest,
            price_precision=precision,
            point_size=point_size,
            session_policy=_session_policy(instrument.asset_class),
            validation_status=validation_status,
            validation_decision="ENABLE" if validation_status == "H1_VALIDATED" else "KEEP_DISABLED",
            error_reason=reason,
            discovery_request_start_utc=discovery_start,
            discovery_request_finish_utc=discovery_finish,
            h1_request_start_utc=h1_start,
            h1_request_finish_utc=h1_finish,
            discovery_candidates=candidates,
            discovery_attempts=attempts,
            h1_attempts=tuple(h1_attempts),
            pipeline_checks=pipeline_checks,
            candle_semantics={
                "timestamp_semantics": "INTERVAL_START",
                "provider_timezone": str(
                    meta.get("exchange_timezone")
                    or match.get("exchange_timezone")
                    or "UTC"
                ),
                "regular_session_only_policy": (
                    instrument.instrument_kind is InstrumentKind.ETF
                ),
                "first_regular_bar_starts_at_09_30_eastern": (
                    instrument.instrument_kind is InstrumentKind.ETF
                ),
                "full_h1_duration_minutes": 60,
                "structurally_partial_final_bars_excluded": structurally_partial,
                "outside_regular_session_bars_excluded": outside_regular_session,
                "extended_hours_included_in_canonical_data": False,
            },
        )

    @staticmethod
    def _direction(buy: bool, sell: bool) -> str:
        if buy and sell:
            return "BUY_AND_SELL"
        if buy:
            return "BUY"
        if sell:
            return "SELL"
        return "NONE"

    def _pipeline_checks(
        self,
        instrument: CanonicalInstrument,
        match: Mapping[str, Any],
        completed: list[Mapping[str, Any]],
        precision: int | None,
        point_size: str | None,
        as_of: datetime,
    ) -> dict[str, Any]:
        checks: dict[str, Any] = {
            "passed": False,
            "timestamp_normalization": "NOT_RUN",
            "forming_candles_excluded": True,
            "canonical_h1_persistence": "NOT_RUN",
            "derived_h4_status": "NOT_RUN",
            "d1_session_status": "NOT_RUN",
            "strategy_evaluator_status": "NOT_RUN",
            "h1_filter": "WAITING",
            "h1_signal": "WAITING",
            "h4_filter": "WAITING",
            "h4_signal": "WAITING",
            "no_forward_filled_bars": False,
            "no_synthetic_bars": False,
            "persisted_h1_count": 0,
            "derived_h4_count": 0,
            "constructed_d1_count": 0,
            "latest_completed_h1_timestamp": None,
            "latest_derived_h4_timestamp": None,
            "exact_error": None,
        }
        if precision is None or point_size is None:
            checks["exact_error"] = "PRICE_PRECISION_UNAVAILABLE"
            return checks
        selected = replace(
            instrument,
            provider_symbol=str(match["symbol"]),
            exchange=str(match.get("exchange") or "") or None,
            mic_code=str(match.get("mic_code") or "") or None,
            provider_instrument_type=str(match.get("instrument_type") or "") or None,
            provider_timezone=str(match.get("exchange_timezone") or "UTC"),
            session_timezone="UTC",
            price_precision=precision,
            point_size=Decimal(point_size),
            synthetic=False,
        )
        normalizer = CandleNormalizer()
        bars = []
        try:
            for row in sorted(completed, key=lambda item: _parse_provider_time(item["datetime"])):
                open_time = _parse_provider_time(row["datetime"])
                close_time = open_time + timedelta(hours=1)
                raw = RawProviderCandle(
                    provider_id=selected.provider_id,
                    provider_symbol=selected.provider_symbol,
                    timeframe=Timeframe.H1,
                    raw_open_time=_iso(open_time),
                    raw_close_time=_iso(close_time),
                    open=str(row["open"]),
                    high=str(row["high"]),
                    low=str(row["low"]),
                    close=str(row["close"]),
                    volume=str(row["volume"]) if row.get("volume") not in (None, "") else None,
                    is_complete=True,
                    session_timezone="UTC",
                    provider_name=selected.provider_id,
                    canonical_instrument=selected.instrument_id,
                    source_timeframe=Timeframe.H1,
                    provider_timestamp=str(row["datetime"]),
                    timestamp_semantics=TimestampSemantics.INTERVAL_START,
                    open_time_utc=open_time,
                    close_time_utc=close_time,
                    source_id=(
                        f"twelve_data:{selected.instrument_id}:H1:{_iso(close_time)}"
                    ),
                    received_at=as_of,
                    provider_metadata={"interval": "1h", "ingestion_run_id": "phase3c1-live-validation"},
                    adapter_version="phase3c1-validation",
                )
                normalized = normalizer.normalize(raw, selected)
                if normalized.candle is None:
                    raise ValueError("NORMALIZATION_FAILED:" + ",".join(normalized.issues))
                bars.append(normalized.candle)
            if not bars:
                raise ValueError("NO_COMPLETED_H1_BARS")
            checks["timestamp_normalization"] = "PASS"
            bars = (
                sorted(bars, key=lambda item: item.open_time)
                if selected.instrument_kind is InstrumentKind.ETF
                else list(market_h1_bars(tuple(bars)))
            )
            if len(bars) < 30:
                raise ValueError("INSUFFICIENT_FROZEN_POLICY_H1_BARS")
            checks["latest_completed_h1_timestamp"] = _iso(bars[-1].close_time)
            if selected.instrument_kind is InstrumentKind.ETF:
                h4 = ExchangeSessionH4Aggregator().aggregate(bars, as_of=as_of)
                d1 = ActualDataNewYorkDailyAggregator().aggregate(bars, as_of=as_of)
            else:
                h4 = BrokerAlignedH4Aggregator().aggregate(bars, as_of=as_of)
                d1 = NewYorkDailyAggregator().aggregate(bars, as_of=as_of)
            checks["derived_h4_count"] = len(h4.bars)
            checks["constructed_d1_count"] = len(d1.bars)
            checks["derived_h4_status"] = "PASS" if len(h4.bars) >= 30 else "INSUFFICIENT_H4"
            checks["d1_session_status"] = "PASS" if len(d1.bars) >= 6 else "INSUFFICIENT_D1"
            checks["latest_derived_h4_timestamp"] = (
                _iso(h4.bars[-1].close_time) if h4.bars else None
            )
            all_bars = tuple((*bars, *h4.bars, *d1.bars))
            checks["no_forward_filled_bars"] = not any(item.forward_filled for item in all_bars)
            checks["no_synthetic_bars"] = not any(item.synthetic for item in all_bars)
            with tempfile.TemporaryDirectory(prefix="spect8-phase3c1-") as temporary:
                repository = SQLiteProjectionRepository(Path(temporary) / "validation.sqlite3")
                repository.initialize()
                inserted = repository.persist_canonical_bars(all_bars)
                checks["persisted_h1_count"] = len(bars)
                checks["canonical_h1_persistence"] = (
                    "PASS" if inserted == len(all_bars) else "PERSISTENCE_COUNT_MISMATCH"
                )
            h1_evaluation = self._evaluate(
                selected, tuple(bars), tuple(d1.bars), Timeframe.H1
            )
            h4_evaluation = self._evaluate(
                selected, tuple(h4.bars), tuple(d1.bars), Timeframe.H4,
                h1_source=tuple(bars),
            )
            for prefix, evaluation in (("h1", h1_evaluation), ("h4", h4_evaluation)):
                classification = evaluation.classification
                checks[f"{prefix}_filter"] = (
                    self._direction(
                        classification.buy_filter_matched,
                        classification.sell_filter_matched,
                    )
                    if classification
                    else "UNAVAILABLE"
                )
                checks[f"{prefix}_signal"] = (
                    self._direction(
                        classification.confirmed_buy,
                        classification.confirmed_sell,
                    )
                    if classification
                    else "UNAVAILABLE"
                )
            evaluator_ok = (
                h1_evaluation.data_status != "UNAVAILABLE"
                and h4_evaluation.data_status != "UNAVAILABLE"
            )
            checks["strategy_evaluator_status"] = "PASS" if evaluator_ok else (
                "H1:" + ",".join(h1_evaluation.issues)
                + ";H4:" + ",".join(h4_evaluation.issues)
            )
            checks["passed"] = all(
                (
                    checks["timestamp_normalization"] == "PASS",
                    checks["canonical_h1_persistence"] == "PASS",
                    checks["derived_h4_status"] == "PASS",
                    checks["d1_session_status"] == "PASS",
                    checks["strategy_evaluator_status"] == "PASS",
                    checks["no_forward_filled_bars"],
                    checks["no_synthetic_bars"],
                )
            )
            if not checks["passed"]:
                checks["exact_error"] = next(
                    str(checks[key])
                    for key in (
                        "canonical_h1_persistence",
                        "derived_h4_status",
                        "d1_session_status",
                        "strategy_evaluator_status",
                    )
                    if checks[key] != "PASS"
                )
        except (DailyFilterUnavailableError, ValueError) as error:
            checks["exact_error"] = str(error)
        return checks

    @staticmethod
    def _evaluate(
        instrument: CanonicalInstrument,
        signal_bars: tuple[Any, ...],
        daily_bars: tuple[Any, ...],
        timeframe: Timeframe,
        *,
        h1_source: tuple[Any, ...] | None = None,
    ) -> Any:
        if len(signal_bars) < 30 or len(daily_bars) < 6:
            raise ValueError(f"INSUFFICIENT_{timeframe.value}_OR_D1_FOR_EVALUATOR")
        endpoint = signal_bars[-1].close_time
        source = h1_source or signal_bars
        snapshot = build_daily_filter_snapshot(
            provider=instrument.provider_id,
            instrument=instrument.instrument_id,
            as_of_h1_close=endpoint,
            h1_bars=source,
            completed_d1_bars=daily_bars,
            sparse_actual_h1=(instrument.instrument_kind is InstrumentKind.ETF),
        )
        return Spect8StrategyEvaluator().evaluate(
            StrategyRequest(
                case_id=f"phase3c1:{instrument.instrument_id}:{timeframe.value}",
                strategy_id=CURRENT_D1_FILTER_V2,
                timeframe=timeframe,
                evaluation_time=endpoint + timedelta(microseconds=1),
                signal_bars=signal_bars[-30:],
                daily_bars=daily_bars,
                instrument=instrument.to_strategy_metadata(),
                strategy_version=CURRENT_D1_FILTER_V2,
                daily_filter_snapshot=snapshot,
            )
        )

    def _failure(
        self,
        instrument: CanonicalInstrument,
        definition: CandidateDefinition,
        discovery_start: str,
        discovery_finish: str,
        status: str,
        reason: str,
        *,
        match: Mapping[str, Any] | None = None,
        candidates: tuple[dict[str, Any], ...] = (),
        attempts: tuple[DiscoveryAttempt, ...] = (),
        h1_attempt: DiscoveryAttempt | None = None,
        h1_attempts: tuple[DiscoveryAttempt, ...] = (),
    ) -> ValidationResult:
        plan_restricted = status == "PLAN_RESTRICTED"
        return ValidationResult(
            canonical_instrument_id=instrument.instrument_id,
            requested_instrument_name=definition.requested_instrument_name,
            asset_class=instrument.asset_class,
            discovery_kind=definition.discovery_kind.value,
            resolved_provider_symbol=str(match.get("symbol")) if match else None,
            provider_instrument_name=str(match.get("instrument_name")) if match else None,
            exchange=str(match.get("exchange")) if match and match.get("exchange") else None,
            mic_code=str(match.get("mic_code")) if match and match.get("mic_code") else None,
            provider_instrument_type=str(match.get("instrument_type")) if match else None,
            provider_timezone=str(match.get("exchange_timezone")) if match and match.get("exchange_timezone") else None,
            minimum_plan_access=str(match.get("minimum_plan")) if match and match.get("minimum_plan") else None,
            current_plan_access="DENIED" if plan_restricted else "UNCONFIRMED",
            h1_request_status=(status if h1_attempt else "NOT_RUN"),
            h1_http_status=h1_attempt.http_status if h1_attempt else None,
            h1_provider_status=h1_attempt.provider_status if h1_attempt else None,
            h1_response_error=h1_attempt.response_error if h1_attempt else None,
            returned_candle_count=0,
            completed_candle_count=0,
            forming_candle_count=0,
            structurally_partial_candle_count=0,
            duplicate_candle_count=0,
            quarantined_candle_count=0,
            latest_timestamp=None,
            price_precision=None,
            point_size=None,
            session_policy=_session_policy(instrument.asset_class),
            validation_status=status,
            validation_decision="KEEP_DISABLED",
            error_reason=reason,
            discovery_request_start_utc=discovery_start,
            discovery_request_finish_utc=discovery_finish,
            h1_request_start_utc=h1_attempt.request_start_utc if h1_attempt else None,
            h1_request_finish_utc=h1_attempt.request_finish_utc if h1_attempt else None,
            discovery_candidates=candidates,
            discovery_attempts=attempts,
            h1_attempts=h1_attempts or ((h1_attempt,) if h1_attempt else ()),
            pipeline_checks={
                "passed": False,
                "exact_error": reason,
                "strategy_evaluator_status": "NOT_RUN",
            },
            candle_semantics={},
        )


def sanitized_report(
    results: tuple[ValidationResult, ...], limiter: SlidingWindowRateLimiter
) -> dict[str, Any]:
    starts = [
        {"label": label, "request_start_utc": _iso(started)}
        for label, started in limiter.request_records()
    ]
    created_at = _iso(_utc_now())
    candidates = [asdict(item) for item in results]
    return {
        "schema_version": 2,
        "provider": "TWELVE_DATA",
        "created_at_utc": created_at,
        "rate_limit": {
            "maximum_request_starts_per_rolling_60_seconds": limiter.max_requests,
            "minimum_request_start_interval_seconds": limiter.min_interval_seconds,
        },
        "requests": starts,
        "request_count": len(starts),
        "candidates": candidates,
        "h1_validated_count": sum(item.validation_status == "H1_VALIDATED" for item in results),
        "enabled_count": sum(item.validation_decision == "ENABLE" for item in results),
        "disabled_count": sum(item.validation_decision != "ENABLE" for item in results),
        "credentials_included": False,
        "validation_runs": [
            {
                "created_at_utc": created_at,
                "instrument_ids": [item.canonical_instrument_id for item in results],
                "requests": starts,
                "candidates": candidates,
            }
        ],
    }


def append_report(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """Merge latest state while retaining every earlier run and attempt."""

    prior_runs = previous.get("validation_runs")
    if not isinstance(prior_runs, list):
        prior_runs = [
            {
                "created_at_utc": previous.get("created_at_utc"),
                "instrument_ids": [
                    item.get("canonical_instrument_id")
                    for item in previous.get("candidates", [])
                    if isinstance(item, Mapping)
                ],
                "requests": previous.get("requests", []),
                "candidates": previous.get("candidates", []),
            }
        ]
    current_runs = current.get("validation_runs")
    if not isinstance(current_runs, list):
        current_runs = []
    latest = {
        item["canonical_instrument_id"]: item
        for item in previous.get("candidates", [])
        if isinstance(item, Mapping) and item.get("canonical_instrument_id")
    }
    latest.update(
        {
            item["canonical_instrument_id"]: item
            for item in current.get("candidates", [])
            if isinstance(item, Mapping) and item.get("canonical_instrument_id")
        }
    )
    merged = dict(current)
    merged["candidates"] = list(latest.values())
    merged["requests"] = [*previous.get("requests", []), *current.get("requests", [])]
    merged["request_count"] = len(merged["requests"])
    merged["validation_runs"] = [*prior_runs, *current_runs]
    merged["h1_validated_count"] = sum(
        item.get("validation_status") == "H1_VALIDATED" for item in merged["candidates"]
    )
    merged["enabled_count"] = sum(
        item.get("validation_decision") == "ENABLE" for item in merged["candidates"]
    )
    merged["disabled_count"] = len(merged["candidates"]) - merged["enabled_count"]
    return merged


def write_sanitized_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
