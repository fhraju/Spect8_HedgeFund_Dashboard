from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..domain import Timeframe
from .models import (
    CanonicalInstrument,
    ExposureCategory,
    InstrumentKind,
    SessionProfileKind,
)


BASELINE_ENABLED_INSTRUMENT_IDS = (
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

PHASE3C1_ENABLED_INSTRUMENT_IDS = BASELINE_ENABLED_INSTRUMENT_IDS + (
    "BTC_USD",
    "ETH_USD",
)

ETF_INSTRUMENT_IDS = (
    "SPY_US_ETF",
    "QQQ_US_ETF",
    "IWM_US_ETF",
    "FEZ_US_ETF",
    "EWJ_US_ETF",
    "EEM_US_ETF",
    "TLT_US_ETF",
    "HYG_US_ETF",
    "SLV_US_ETF",
    "USO_US_ETF",
    "UNG_US_ETF",
    "DBA_US_ETF",
    "VIXM_US_ETF",
)

TARGET_INSTRUMENT_IDS = PHASE3C1_ENABLED_INSTRUMENT_IDS + ETF_INSTRUMENT_IDS

PHASE3C2_ENABLED_ETF_IDS = tuple(
    instrument_id for instrument_id in ETF_INSTRUMENT_IDS if instrument_id != "TLT_US_ETF"
)
PHASE3C2_ENABLED_INSTRUMENT_IDS = (
    PHASE3C1_ENABLED_INSTRUMENT_IDS + PHASE3C2_ENABLED_ETF_IDS
)

# Phase 3C-1 direct-market definitions remain available and disabled. They are
# deliberately separate from the approved ETF proxy identities.
CANDIDATE_INSTRUMENT_IDS = (
    "XAG_USD",
    "SP_500",
    "NASDAQ_100",
    "DOW_30",
    "DAX_40",
    "FTSE_100",
    "NIKKEI_225",
    "WTI_CRUDE",
    "BRENT_CRUDE",
    "NATURAL_GAS",
    "COPPER",
    "BTC_USD",
    "ETH_USD",
    "VIX",
    "US_10Y_YIELD",
)
DISABLED_DIRECT_MARKET_IDS = tuple(
    item for item in CANDIDATE_INSTRUMENT_IDS if item not in {"BTC_USD", "ETH_USD"}
)
ALL_INSTRUMENT_IDS = TARGET_INSTRUMENT_IDS + DISABLED_DIRECT_MARKET_IDS

# The 2026-08-06 controlled live report validated 12 ETF listings. TLT remains
# disabled because one malformed OHLC row was quarantined.
DEFAULT_ENABLED_INSTRUMENT_IDS = PHASE3C2_ENABLED_INSTRUMENT_IDS

_ETF_PRICE_PRECISION = {
    "SPY_US_ETF": 7,
    "QQQ_US_ETF": 7,
    "IWM_US_ETF": 7,
    "FEZ_US_ETF": 7,
    "EWJ_US_ETF": 7,
    "EEM_US_ETF": 7,
    "HYG_US_ETF": 7,
    "SLV_US_ETF": 9,
    "USO_US_ETF": 8,
    "UNG_US_ETF": 7,
    "DBA_US_ETF": 7,
    "VIXM_US_ETF": 7,
}


@dataclass(frozen=True, slots=True)
class _InstrumentSpec:
    instrument_id: str
    display_symbol: str
    display_name: str
    asset_class: str
    provider_symbol: str = ""
    point_size: str | None = None
    price_precision: int | None = None
    quote_currency: str = "USD"
    exchange: str | None = None
    mic_code: str | None = None
    provider_instrument_type: str | None = None
    provider_timezone: str | None = None
    validation_status: str = "PENDING_LIVE_VALIDATION"
    instrument_kind: InstrumentKind = InstrumentKind.DIRECT_MARKET
    exposure_category: ExposureCategory = ExposureCategory.CURRENCY
    underlying_description: str | None = None
    is_proxy: bool = False
    proxy_for: str | None = None
    session_profile: SessionProfileKind = SessionProfileKind.DIRECT_MARKET


@dataclass(frozen=True, slots=True)
class CryptoExchangePolicy:
    """One explicit venue selection shared by every configured crypto pair."""

    exchange: str | None = None

    def exchange_for(self, instrument_id: str) -> str | None:
        if instrument_id not in {"BTC_USD", "ETH_USD"}:
            return None
        return self.exchange


BINANCE_CRYPTO_POLICY = CryptoExchangePolicy(exchange="Binance")


def _forex(
    instrument_id: str,
    symbol: str,
    name: str,
    point_size: str,
    precision: int,
    quote: str,
) -> _InstrumentSpec:
    return _InstrumentSpec(
        instrument_id,
        symbol,
        name,
        "FOREX",
        symbol,
        point_size,
        precision,
        quote,
        validation_status="LIVE_VALIDATED",
        instrument_kind=InstrumentKind.FOREX,
        exposure_category=ExposureCategory.CURRENCY,
        session_profile=SessionProfileKind.FOREX_WEEKDAY,
    )


def _etf(
    instrument_id: str,
    symbol: str,
    name: str,
    exposure: ExposureCategory,
    underlying: str,
    proxy_for: str | None,
    exchange: str,
    mic_code: str,
) -> _InstrumentSpec:
    precision = _ETF_PRICE_PRECISION.get(instrument_id)
    return _InstrumentSpec(
        instrument_id,
        symbol,
        name,
        "ETF",
        symbol,
        point_size=(f"1e-{precision}" if precision is not None else None),
        price_precision=precision,
        quote_currency="USD",
        exchange=exchange,
        mic_code=mic_code,
        provider_instrument_type="ETF",
        provider_timezone="America/New_York",
        validation_status=(
            "LIVE_VALIDATED"
            if instrument_id in PHASE3C2_ENABLED_ETF_IDS
            else "H1_DATA_QUALITY_FAILED"
        ),
        instrument_kind=InstrumentKind.ETF,
        exposure_category=exposure,
        underlying_description=underlying,
        is_proxy=True,
        proxy_for=proxy_for,
        session_profile=SessionProfileKind.US_EQUITY_REGULAR,
    )


_INSTRUMENT_SPECS = (
    _forex("EUR_USD", "EUR/USD", "Euro / US Dollar", "0.00001", 5, "USD"),
    _forex("GBP_USD", "GBP/USD", "British Pound / US Dollar", "0.00001", 5, "USD"),
    _forex("USD_JPY", "USD/JPY", "US Dollar / Japanese Yen", "0.001", 3, "JPY"),
    _forex("AUD_USD", "AUD/USD", "Australian Dollar / US Dollar", "0.00001", 5, "USD"),
    _forex("USD_CAD", "USD/CAD", "US Dollar / Canadian Dollar", "0.00001", 5, "CAD"),
    _forex("NZD_USD", "NZD/USD", "New Zealand Dollar / US Dollar", "0.00001", 5, "USD"),
    _forex("EUR_GBP", "EUR/GBP", "Euro / British Pound", "0.00001", 5, "GBP"),
    _forex("EUR_JPY", "EUR/JPY", "Euro / Japanese Yen", "0.001", 3, "JPY"),
    _forex("GBP_JPY", "GBP/JPY", "British Pound / Japanese Yen", "0.001", 3, "JPY"),
    _InstrumentSpec(
        "XAU_USD", "XAU/USD", "Gold / US Dollar", "METAL", "XAU/USD",
        "0.01", 2, "USD", validation_status="LIVE_VALIDATED",
        instrument_kind=InstrumentKind.SPOT_METAL,
        exposure_category=ExposureCategory.PRECIOUS_METAL,
        session_profile=SessionProfileKind.FOREX_WEEKDAY,
    ),
    _InstrumentSpec(
        "BTC_USD", "BTC/USD", "Bitcoin / US Dollar", "CRYPTO", "BTC/USD",
        "0.01", 2, "USD", provider_instrument_type="Digital Currency",
        provider_timezone="UTC", validation_status="LIVE_VALIDATED",
        instrument_kind=InstrumentKind.CRYPTO,
        exposure_category=ExposureCategory.CURRENCY,
        session_profile=SessionProfileKind.CRYPTO_FROZEN_WEEKEND,
    ),
    _InstrumentSpec(
        "ETH_USD", "ETH/USD", "Ethereum / US Dollar", "CRYPTO", "ETH/USD",
        "0.01", 2, "USD", provider_instrument_type="Digital Currency",
        provider_timezone="UTC", validation_status="LIVE_VALIDATED",
        instrument_kind=InstrumentKind.CRYPTO,
        exposure_category=ExposureCategory.CURRENCY,
        session_profile=SessionProfileKind.CRYPTO_FROZEN_WEEKEND,
    ),
    _etf("SPY_US_ETF", "SPY", "S&P 500 ETF Proxy", ExposureCategory.US_LARGE_CAP_EQUITY, "US large-cap equities through the SPDR S&P 500 ETF Trust price series.", "SP_500", "NYSE Arca", "ARCX"),
    _etf("QQQ_US_ETF", "QQQ", "Nasdaq 100 ETF Proxy", ExposureCategory.US_TECH_EQUITY, "US technology and large-cap equities through the Invesco QQQ ETF price series.", "NASDAQ_100", "NASDAQ", "XNMS"),
    _etf("IWM_US_ETF", "IWM", "Russell 2000 ETF Proxy", ExposureCategory.US_SMALL_CAP_EQUITY, "US small-cap equities through the iShares Russell 2000 ETF price series.", None, "NYSE Arca", "ARCX"),
    _etf("FEZ_US_ETF", "FEZ", "Eurozone 50 ETF Proxy", ExposureCategory.EUROPE_EQUITY, "Eurozone large-cap equities through the SPDR EURO STOXX 50 ETF price series.", None, "NYSE Arca", "ARCX"),
    _etf("EWJ_US_ETF", "EWJ", "Japan Equity ETF Proxy", ExposureCategory.JAPAN_EQUITY, "Japanese equities through the iShares MSCI Japan ETF price series.", "NIKKEI_225", "NYSE Arca", "ARCX"),
    _etf("EEM_US_ETF", "EEM", "Emerging Markets ETF Proxy", ExposureCategory.EMERGING_MARKET_EQUITY, "Emerging-market equities through the iShares MSCI Emerging Markets ETF price series.", None, "NYSE Arca", "ARCX"),
    _etf("TLT_US_ETF", "TLT", "US Long Treasury ETF", ExposureCategory.GOVERNMENT_BONDS, "Long-duration US Treasury bonds through the iShares 20+ Year Treasury Bond ETF price series.", "US_10Y_YIELD", "NASDAQ", "XNMS"),
    _etf("HYG_US_ETF", "HYG", "High-Yield Credit ETF", ExposureCategory.CREDIT, "US high-yield corporate credit through the iShares iBoxx High Yield Corporate Bond ETF price series.", None, "NYSE Arca", "ARCX"),
    _etf("SLV_US_ETF", "SLV", "Silver ETF Proxy", ExposureCategory.PRECIOUS_METAL, "Silver bullion exposure through the iShares Silver Trust ETF price series.", "XAG_USD", "NYSE Arca", "ARCX"),
    _etf("USO_US_ETF", "USO", "WTI Oil ETF Proxy", ExposureCategory.ENERGY, "WTI crude-oil futures exposure through the United States Oil Fund ETF price series.", "WTI_CRUDE", "NYSE Arca", "ARCX"),
    _etf("UNG_US_ETF", "UNG", "Natural Gas ETF Proxy", ExposureCategory.ENERGY, "Natural-gas futures exposure through the United States Natural Gas Fund ETF price series.", "NATURAL_GAS", "NYSE Arca", "ARCX"),
    _etf("DBA_US_ETF", "DBA", "Agriculture ETF Basket", ExposureCategory.AGRICULTURE, "Agricultural commodity futures through the Invesco DB Agriculture Fund ETF price series.", None, "NYSE Arca", "ARCX"),
    _etf("VIXM_US_ETF", "VIXM", "Volatility ETF Proxy", ExposureCategory.VOLATILITY, "Mid-term VIX futures exposure through the ProShares VIX Mid-Term Futures ETF price series.", "VIX", "CBOE BZX", "BATS"),
    _InstrumentSpec("XAG_USD", "XAG/USD", "Silver Spot / US Dollar", "PRECIOUS_METAL", "XAG/USD", quote_currency="USD", provider_instrument_type="Commodity", provider_timezone="UTC", validation_status="PLAN_RESTRICTED", instrument_kind=InstrumentKind.SPOT_METAL, exposure_category=ExposureCategory.PRECIOUS_METAL),
    _InstrumentSpec("SP_500", "S&P 500", "S&P 500 Index", "EQUITY_INDEX", validation_status="DISCOVERY_UNAVAILABLE", exposure_category=ExposureCategory.US_LARGE_CAP_EQUITY),
    _InstrumentSpec("NASDAQ_100", "Nasdaq 100", "Nasdaq 100 Index", "EQUITY_INDEX", validation_status="DISCOVERY_UNAVAILABLE", exposure_category=ExposureCategory.US_TECH_EQUITY),
    _InstrumentSpec("DOW_30", "Dow 30", "Dow Jones Industrial Average", "EQUITY_INDEX", validation_status="DISCOVERY_UNAVAILABLE", exposure_category=ExposureCategory.US_LARGE_CAP_EQUITY),
    _InstrumentSpec("DAX_40", "DAX 40", "DAX 40 Index", "EQUITY_INDEX", quote_currency="EUR", validation_status="PROVIDER_ERROR", exposure_category=ExposureCategory.EUROPE_EQUITY),
    _InstrumentSpec("FTSE_100", "FTSE 100", "FTSE 100 Index", "EQUITY_INDEX", quote_currency="GBP", validation_status="PROVIDER_ERROR", exposure_category=ExposureCategory.EUROPE_EQUITY),
    _InstrumentSpec("NIKKEI_225", "Nikkei 225", "Nikkei 225 Index", "EQUITY_INDEX", quote_currency="JPY", validation_status="PROVIDER_ERROR", exposure_category=ExposureCategory.JAPAN_EQUITY),
    _InstrumentSpec("WTI_CRUDE", "WTI/USD", "Crude Oil WTI Spot", "ENERGY", "WTI/USD", quote_currency="USD", provider_instrument_type="Commodity", provider_timezone="UTC", validation_status="PLAN_RESTRICTED", exposure_category=ExposureCategory.ENERGY),
    _InstrumentSpec("BRENT_CRUDE", "XBR/USD", "Brent Spot", "ENERGY", "XBR/USD", quote_currency="USD", provider_instrument_type="Commodity", provider_timezone="UTC", validation_status="PLAN_RESTRICTED", exposure_category=ExposureCategory.ENERGY),
    _InstrumentSpec("NATURAL_GAS", "Natural Gas", "Natural Gas", "ENERGY", validation_status="DISCOVERY_UNAVAILABLE", exposure_category=ExposureCategory.ENERGY),
    _InstrumentSpec("COPPER", "HG1", "Copper Spot", "INDUSTRIAL_METAL", "HG1", quote_currency="USD", provider_instrument_type="Commodity", provider_timezone="UTC", validation_status="PLAN_RESTRICTED"),
    _InstrumentSpec("VIX", "VIX", "CBOE Volatility Index", "VOLATILITY", validation_status="DISCOVERY_UNAVAILABLE", exposure_category=ExposureCategory.VOLATILITY),
    _InstrumentSpec("US_10Y_YIELD", "US 10Y Yield", "US 10-Year Treasury Yield", "INTEREST_RATE", validation_status="DISCOVERY_UNAVAILABLE", exposure_category=ExposureCategory.GOVERNMENT_BONDS),
)


def twelve_data_instruments(
    enabled_ids: tuple[str, ...] | None = None,
    *,
    crypto_policy: CryptoExchangePolicy = BINANCE_CRYPTO_POLICY,
) -> tuple[CanonicalInstrument, ...]:
    """Authoritative provider-independent registry in stable display order."""

    enabled = set(enabled_ids or DEFAULT_ENABLED_INSTRUMENT_IDS)
    unknown = enabled.difference(ALL_INSTRUMENT_IDS)
    if unknown:
        raise ValueError(f"unknown enabled instruments: {', '.join(sorted(unknown))}")
    not_validated = {
        item.instrument_id
        for item in _INSTRUMENT_SPECS
        if item.instrument_id in enabled and item.validation_status != "LIVE_VALIDATED"
    }
    if not_validated:
        raise ValueError(
            "cannot enable instruments without live validation: "
            + ", ".join(sorted(not_validated))
        )
    return tuple(
        CanonicalInstrument(
            instrument_id=spec.instrument_id,
            provider_id="TWELVE_DATA",
            provider_symbol=spec.provider_symbol,
            display_name=spec.display_name,
            asset_class=spec.asset_class,
            point_size=(Decimal(spec.point_size) if spec.point_size else None),
            tick_size=None,
            price_precision=spec.price_precision,
            tick_value_usd=None,
            conversion_rate_to_usd=None,
            contract_min=None,
            contract_max=None,
            contract_step=None,
            minimum_stop_distance_points=None,
            quote_currency=spec.quote_currency,
            profit_currency=spec.quote_currency,
            session_timezone="UTC",
            candle_boundary_convention=(
                "Twelve Data US regular-session completed H1; shortened final fragment excluded; D1 bucketed at 17:00 America/New_York"
                if spec.instrument_kind is InstrumentKind.ETF
                else "Twelve Data actual completed H1; D1 bucketed at 17:00 America/New_York"
            ),
            available_timeframes=(Timeframe.H1, Timeframe.H4, Timeframe.D1),
            strategy_id="SPECT8_MICRO_DAILY_V1_0",
            display_symbol=spec.display_symbol,
            enabled=spec.instrument_id in enabled,
            exchange=(
                crypto_policy.exchange_for(spec.instrument_id)
                if spec.instrument_kind is InstrumentKind.CRYPTO
                else spec.exchange
            ),
            mic_code=spec.mic_code,
            provider_instrument_type=spec.provider_instrument_type,
            provider_timezone=spec.provider_timezone,
            validation_status=spec.validation_status,
            registry_order=index,
            synthetic=False,
            instrument_kind=spec.instrument_kind,
            exposure_category=spec.exposure_category,
            underlying_description=spec.underlying_description,
            is_proxy=spec.is_proxy,
            proxy_for=spec.proxy_for,
            session_profile=spec.session_profile,
        )
        for index, spec in enumerate(_INSTRUMENT_SPECS, start=1)
    )


@dataclass(frozen=True, slots=True)
class CanonicalInstrumentRegistry:
    _instruments: tuple[CanonicalInstrument, ...]

    def __post_init__(self) -> None:
        keys = [(item.provider_id, item.instrument_id) for item in self._instruments]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate canonical instrument registry key")
        symbols = [item.provider_symbol for item in self._instruments if item.provider_symbol]
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate non-empty provider symbol mapping")
        orders = [item.registry_order for item in self._instruments]
        if len(orders) != len(set(orders)):
            raise ValueError("duplicate registry order")
        if len(self.enabled()) > 25:
            raise ValueError("at most 25 instruments may be enabled")

    def all(self) -> tuple[CanonicalInstrument, ...]:
        return self._instruments

    def enabled(self) -> tuple[CanonicalInstrument, ...]:
        return tuple(item for item in self._instruments if item.enabled)

    def by_id(self, instrument_id: str) -> CanonicalInstrument:
        for instrument in self._instruments:
            if instrument.instrument_id == instrument_id:
                return instrument
        raise KeyError(f"unknown instrument {instrument_id}")

    def get(self, provider_id: str, instrument_id: str) -> CanonicalInstrument:
        instrument = self.by_id(instrument_id)
        if instrument.provider_id != provider_id:
            raise KeyError(f"unknown instrument {provider_id}:{instrument_id}")
        return instrument

    def supports(self, provider_id: str, instrument_id: str, timeframe: Timeframe) -> bool:
        return timeframe in self.get(provider_id, instrument_id).available_timeframes
