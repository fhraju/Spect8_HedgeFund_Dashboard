"""Approved platform multi-asset dashboard identities (single authority).

The Market Data Platform remains the collection/readiness authority. This module
owns only Spect8's explicit, human-approved identity metadata for the ten
platform multi-asset instruments, exactly mirroring ``share_catalog``:

* identities are exact Platform instrument IDs (no ticker inference);
* semantic kind/exposure/session metadata is declared here, never derived from
  the IG ``instrument_type`` (DXY stays a currency index even though IG groups
  its EPIC under commodities);
* Favorites is a presentation grouping over existing identities only.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ExposureCategory, InstrumentKind, SessionProfileKind

MULTI_ASSET_AUTHORITY = "IG_DEMO"


@dataclass(frozen=True, slots=True)
class MultiAssetSpec:
    instrument_id: str
    display_symbol: str
    display_name: str
    epic: str
    asset_class: str
    instrument_kind: InstrumentKind
    exposure_category: ExposureCategory
    session_profile: SessionProfileKind
    quote_currency: str
    price_precision: int
    point_size: str
    tick_value_usd: str | None = None


# Platform contract precision (normalized_precision) is authoritative for the
# display precision and point size; nothing is inferred from the EPIC shape.
MULTI_ASSET_SPECS: tuple[MultiAssetSpec, ...] = (
    MultiAssetSpec(
        "CRYPTO_BTC_USD",
        "BTC/USD",
        "Bitcoin / US Dollar",
        "CS.D.BITCOIN.TODAY.IP",
        "CRYPTO",
        InstrumentKind.CRYPTO,
        ExposureCategory.CRYPTO,
        SessionProfileKind.CRYPTO_FROZEN_WEEKEND,
        "USD",
        0,
        "1",
    ),
    MultiAssetSpec(
        "CRYPTO_ETH_USD",
        "ETH/USD",
        "Ether / US Dollar",
        "CS.D.ETHUSD.TODAY.IP",
        "CRYPTO",
        InstrumentKind.CRYPTO,
        ExposureCategory.CRYPTO,
        SessionProfileKind.CRYPTO_FROZEN_WEEKEND,
        "USD",
        1,
        "0.1",
    ),
    MultiAssetSpec(
        "METAL_XAU_USD",
        "XAU/USD",
        "Gold / US Dollar",
        "CS.D.USCGC.TODAY.IP",
        "METAL",
        InstrumentKind.SPOT_METAL,
        ExposureCategory.PRECIOUS_METAL,
        SessionProfileKind.FOREX_WEEKDAY,
        "USD",
        2,
        "0.01",
    ),
    MultiAssetSpec(
        "METAL_XAG_USD",
        "XAG/USD",
        "Silver / US Dollar",
        "CS.D.USCSI.TODAY.IP",
        "METAL",
        InstrumentKind.SPOT_METAL,
        ExposureCategory.PRECIOUS_METAL,
        SessionProfileKind.FOREX_WEEKDAY,
        "USD",
        1,
        "0.1",
    ),
    MultiAssetSpec(
        "INDEX_US500",
        "US500",
        "US 500",
        "IX.D.SPTRD.DAILY.IP",
        "EQUITY_INDEX",
        InstrumentKind.INDEX,
        ExposureCategory.US_LARGE_CAP_EQUITY,
        SessionProfileKind.FOREX_WEEKDAY,
        "USD",
        2,
        "0.01",
    ),
    MultiAssetSpec(
        "INDEX_US100",
        "US100",
        "US Tech 100",
        "IX.D.NASDAQ.CASH.IP",
        "EQUITY_INDEX",
        InstrumentKind.INDEX,
        ExposureCategory.US_TECH_EQUITY,
        SessionProfileKind.FOREX_WEEKDAY,
        "USD",
        1,
        "0.1",
    ),
    MultiAssetSpec(
        "INDEX_GERMANY_40",
        "DE40",
        "Germany 40",
        "IX.D.DAX.DAILY.IP",
        "EQUITY_INDEX",
        InstrumentKind.INDEX,
        ExposureCategory.EUROPE_EQUITY,
        SessionProfileKind.FOREX_WEEKDAY,
        "EUR",
        1,
        "0.1",
    ),
    MultiAssetSpec(
        "COMMODITY_WTI_USD",
        "WTI",
        "Oil - US Crude",
        "CC.D.CL.USS.IP",
        "ENERGY",
        InstrumentKind.COMMODITY,
        ExposureCategory.ENERGY,
        SessionProfileKind.FOREX_WEEKDAY,
        "USD",
        1,
        "0.1",
    ),
    MultiAssetSpec(
        "COMMODITY_HEATING_OIL_USD",
        "HO",
        "Heating Oil",
        "CC.D.HO.USS.IP",
        "ENERGY",
        InstrumentKind.COMMODITY,
        ExposureCategory.ENERGY,
        SessionProfileKind.FOREX_WEEKDAY,
        "USD",
        0,
        "1",
    ),
    MultiAssetSpec(
        "INDEX_US_DOLLAR_DXY",
        "DXY",
        "US Dollar Basket (DXY)",
        "CC.D.DX.USS.IP",
        "CURRENCY_INDEX",
        InstrumentKind.INDEX,
        ExposureCategory.CURRENCY,
        SessionProfileKind.FOREX_WEEKDAY,
        "USD",
        1,
        "0.1",
    ),
)

MULTI_ASSET_BY_ID = {spec.instrument_id: spec for spec in MULTI_ASSET_SPECS}
MULTI_ASSET_IDS = tuple(spec.instrument_id for spec in MULTI_ASSET_SPECS)

# Presentation grouping only: exact approved Favorites membership.
FAVORITES: tuple[str, ...] = ("EUR_USD", *MULTI_ASSET_IDS)


def is_multi_asset(instrument_id: str) -> bool:
    return instrument_id in MULTI_ASSET_BY_ID


def epic_for(instrument_id: str) -> str | None:
    spec = MULTI_ASSET_BY_ID.get(instrument_id)
    return spec.epic if spec else None


__all__ = [
    "FAVORITES",
    "MULTI_ASSET_AUTHORITY",
    "MULTI_ASSET_BY_ID",
    "MULTI_ASSET_IDS",
    "MULTI_ASSET_SPECS",
    "MultiAssetSpec",
    "epic_for",
    "is_multi_asset",
]
