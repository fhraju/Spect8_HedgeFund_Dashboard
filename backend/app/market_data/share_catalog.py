"""Approved dashboard share identities; collection stays in the data platform."""

LIVE_SHARES = ("AAPL", "AMD", "AMZN", "ARM", "ASML", "AVGO", "BAC", "BRK.B", "CSCO", "CVX",
               "DIS", "FDX", "GE", "GM", "GME", "GOOG", "INTC", "JNJ", "JPM", "META")
DEMO_SHARES = ("MSFT", "MSTR", "NFLX", "NKE", "PFE", "PLTR", "QCOM", "RTX", "SNOW", "SPCX", "TSLA", "WMT", "ZM")

# Persisted platform IDs that predate the generic account-type naming. This is
# explicit configuration data, never a per-symbol runtime branch: onboarding a
# new share uses the generic derivation below and needs no code change.
LEGACY_PLATFORM_IDS = {"AAPL": "SHARE_CFD_AAPL"}


def share_platform_id(symbol):
    """Generic persisted platform instrument ID for an approved share symbol."""
    legacy = LEGACY_PLATFORM_IDS.get(symbol)
    return legacy if legacy is not None else "SHARE_SPREADBET_" + symbol.replace(".", "_")


SHARE_AUTHORITIES = {"SHARE_" + symbol.replace(".", "_"): "IG_" + env
                     for env, symbols in (("LIVE", LIVE_SHARES), ("DEMO", DEMO_SHARES)) for symbol in symbols}
SHARE_PLATFORM_IDS = {share_platform_id(symbol): "SHARE_" + symbol.replace(".", "_")
                      for symbol in (*LIVE_SHARES, *DEMO_SHARES)}
SHARE_SYMBOLS = {"SHARE_" + s.replace(".", "_"): s for s in (*LIVE_SHARES, *DEMO_SHARES)}


def register_share(symbol, environment):
    """Register an approved compatible share as configuration, not a code branch.

    The frontend renders rows dynamically, so a newly approved share only needs
    its identity/environment supplied here (mirroring the platform authority).
    """
    if environment not in {"LIVE", "DEMO"}:
        raise ValueError("unsupported share environment")
    instrument = "SHARE_" + symbol.replace(".", "_")
    authority = "IG_" + environment
    existing = SHARE_AUTHORITIES.get(instrument)
    if existing is not None and existing != authority:
        raise ValueError("share symbol already registered with different authority")
    SHARE_AUTHORITIES[instrument] = authority
    SHARE_PLATFORM_IDS[share_platform_id(symbol)] = instrument
    SHARE_SYMBOLS[instrument] = symbol
    return instrument


def is_share(instrument):
    return instrument in SHARE_AUTHORITIES


def platform_instruments(instrument_ids):
    from dataclasses import replace
    from decimal import Decimal

    from ..domain import Timeframe
    from .models import ExposureCategory, InstrumentKind, SessionProfileKind
    from .multi_asset_catalog import (
        MULTI_ASSET_AUTHORITY,
        MULTI_ASSET_BY_ID,
        MULTI_ASSET_SPECS,
    )
    from .registry import twelve_data_instruments
    requested = tuple(instrument_ids)
    regular = tuple(
        i for i in requested if not is_share(i) and i not in MULTI_ASSET_BY_ID
    )
    existing = twelve_data_instruments(regular or ("EUR_USD",))
    result = [i for i in existing if i.instrument_id in regular]
    template = next(i for i in existing if i.instrument_id == "EUR_USD")
    for index, inst in enumerate(SHARE_AUTHORITIES, start=100):
        if inst not in requested:
            continue
        symbol = SHARE_SYMBOLS[inst]
        result.append(replace(template, instrument_id=inst, display_symbol=symbol,
            display_name=f"{symbol} · {SHARE_AUTHORITIES[inst].replace('_', ' ')} spread betting",
            provider_id="MARKET_DATA_PLATFORM", provider_symbol=SHARE_AUTHORITIES[inst] + ":" + symbol,
            asset_class="SHARE_SPREADBET", point_size=Decimal("0.01"), price_precision=None,
            quote_currency="USD", profit_currency="USD", enabled=True, polling_enabled=False,
            registry_order=index, validation_status="AWAITING_PLATFORM_VALIDATION",
            instrument_kind=InstrumentKind.SHARE_SPREADBET, exposure_category=ExposureCategory.US_LARGE_CAP_EQUITY,
            session_profile=SessionProfileKind.IG_SHARE_24H,
            session_timezone="Europe/London",
            candle_boundary_convention="IG share calendar; completed M30 REST",
            available_timeframes=(Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1, Timeframe.W1)))
    for index, spec in enumerate(MULTI_ASSET_SPECS, start=200):
        if spec.instrument_id not in requested:
            continue
        result.append(replace(template, instrument_id=spec.instrument_id,
            display_symbol=spec.display_symbol, display_name=spec.display_name,
            provider_id="MARKET_DATA_PLATFORM",
            provider_symbol=MULTI_ASSET_AUTHORITY + ":" + spec.epic,
            asset_class=spec.asset_class, point_size=Decimal(spec.point_size),
            price_precision=spec.price_precision, quote_currency=spec.quote_currency,
            profit_currency=spec.quote_currency, enabled=True, polling_enabled=True,
            registry_order=index, validation_status="PLATFORM_CONTRACT_VALIDATED",
            instrument_kind=spec.instrument_kind, exposure_category=spec.exposure_category,
            session_profile=spec.session_profile, session_timezone="Europe/London",
            candle_boundary_convention="IG platform calendar; canonical M30/H1 + native D1/W1",
            available_timeframes=(Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1, Timeframe.W1)))
    return tuple(result)
