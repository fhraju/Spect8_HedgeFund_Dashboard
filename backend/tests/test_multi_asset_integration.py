"""Approved multi-asset platform integration tests (no network, no staging DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backend.app.config import INSTRUMENT_AUTHORITY_MAP, Settings
from backend.app.dashboard_api import ScannerData
from backend.app.domain import Bar, FilterMode, Timeframe
from backend.app.engine.strategy import _missing_issue
from backend.app.market_data.multi_asset_catalog import (
    FAVORITES,
    MULTI_ASSET_IDS,
    MULTI_ASSET_SPECS,
    is_multi_asset,
)
from backend.app.market_data.multi_asset_profile import (
    MULTI_ASSET_LIMITS,
    mark_multi_asset_closures,
    multi_asset_h4_bars,
)
from backend.app.market_data.models import ExposureCategory, InstrumentKind, SessionProfileKind
from backend.app.market_data.platform_adapter import (
    PLATFORM_PROVIDER_ID,
    InsufficientPlatformHistoryError,
    PlatformInstrumentHistory,
    platform_instrument_id,
    spect8_instrument_id,
)
from backend.app.market_data.share_catalog import platform_instruments

UTC = UTC
H1 = timedelta(hours=1)
EXPECTED_FAVORITES = (
    "EUR_USD",
    "CRYPTO_BTC_USD",
    "CRYPTO_ETH_USD",
    "METAL_XAU_USD",
    "METAL_XAG_USD",
    "INDEX_US500",
    "INDEX_US100",
    "INDEX_GERMANY_40",
    "COMMODITY_WTI_USD",
    "COMMODITY_HEATING_OIL_USD",
    "INDEX_US_DOLLAR_DXY",
)


def _bar(
    instrument_id: str,
    opened: datetime,
    close: float = 100.0,
    complete: bool = True,
    source_ids: tuple[str, ...] = (),
) -> Bar:
    from backend.app.market_data.profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID

    price = Decimal(str(close))
    return Bar(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        open_time=opened,
        close_time=opened + H1,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        provider=PLATFORM_PROVIDER_ID,
        is_complete=complete,
        synthetic=False,
        quality_status="VALID",
        construction_profile_version=PROFILE_ID,
        provider_adapter_version="test",
        source_candle_ids=source_ids,
    )


# 1. exact Platform -> Spect8 mappings -------------------------------------


def test_exact_platform_to_spect8_mappings():
    for instrument_id in MULTI_ASSET_IDS:
        assert spect8_instrument_id(instrument_id) == instrument_id
        assert platform_instrument_id(instrument_id) == instrument_id
    # Existing FX/share mappings are untouched.
    assert spect8_instrument_id("FX_EUR_USD") == "EUR_USD"
    assert platform_instrument_id("EUR_USD") == "FX_EUR_USD"
    assert spect8_instrument_id("SHARE_CFD_AAPL") == "SHARE_AAPL"
    assert platform_instrument_id("SHARE_AAPL") == "SHARE_CFD_AAPL"


# 2/3. semantic metadata ----------------------------------------------------


def test_multi_asset_metadata_is_declared_and_semantic():
    values = {i.instrument_id: i for i in platform_instruments(MULTI_ASSET_IDS)}
    assert set(values) == set(MULTI_ASSET_IDS)
    crypto = values["CRYPTO_BTC_USD"]
    assert crypto.instrument_kind is InstrumentKind.CRYPTO
    assert crypto.exposure_category is ExposureCategory.CRYPTO
    assert crypto.session_profile is SessionProfileKind.CRYPTO_24_7
    gold = values["METAL_XAU_USD"]
    assert gold.instrument_kind is InstrumentKind.SPOT_METAL
    assert gold.exposure_category is ExposureCategory.PRECIOUS_METAL
    assert gold.session_profile is SessionProfileKind.PLATFORM_FAMILY_WEEKDAY
    us500 = values["INDEX_US500"]
    assert us500.instrument_kind is InstrumentKind.INDEX
    assert us500.exposure_category is ExposureCategory.US_LARGE_CAP_EQUITY
    us100 = values["INDEX_US100"]
    assert us100.exposure_category is ExposureCategory.US_TECH_EQUITY
    dax = values["INDEX_GERMANY_40"]
    assert dax.exposure_category is ExposureCategory.EUROPE_EQUITY
    assert values["COMMODITY_WTI_USD"].instrument_kind is InstrumentKind.COMMODITY
    assert values["COMMODITY_HEATING_OIL_USD"].exposure_category is ExposureCategory.ENERGY


def test_dxy_remains_a_currency_index_despite_the_ig_commodity_epic():
    dxy = {i.instrument_id: i for i in platform_instruments(("INDEX_US_DOLLAR_DXY",))}[
        "INDEX_US_DOLLAR_DXY"
    ]
    assert dxy.exposure_category is ExposureCategory.CURRENCY
    assert dxy.instrument_kind is InstrumentKind.INDEX
    assert dxy.asset_class == "CURRENCY_INDEX"
    assert dxy.provider_symbol == "IG_DEMO:CC.D.DX.USS.IP"
    assert "Commodit" not in dxy.display_name


def test_all_multi_asset_authorities_are_ig_demo():
    for instrument_id in MULTI_ASSET_IDS:
        assert INSTRUMENT_AUTHORITY_MAP[instrument_id] == "IG_DEMO"


def test_platform_multi_asset_enabled_subset_is_approved():
    settings = Settings(
        repository_root=__import__("pathlib").Path("/tmp"),
        database_path=__import__("pathlib").Path("/tmp/spect8-test.sqlite3"),
        internal_api_key="k",
        market_data_source="MARKET_DATA_PLATFORM",
        enabled_instrument_ids=MULTI_ASSET_IDS,
        market_data_platform_demo_database_url=(
            "postgresql+psycopg://reader@localhost/market_data_staging"
        ),
        market_data_platform_live_database_url=(
            "postgresql+psycopg://reader@localhost/market_data_live"
        ),
    )
    settings.validate()


# 4/5. timeframes + fail closed --------------------------------------------


def _history(*, m30=40, h1=128, h4=30, d1=10, w1=10, instrument="CRYPTO_BTC_USD"):
    base = datetime(2026, 9, 1, tzinfo=UTC)
    return PlatformInstrumentHistory(
        platform_instrument_id=instrument,
        spect8_instrument_id=instrument,
        m30=tuple(object() for _ in range(m30)),  # type: ignore[arg-type]
        h1=tuple(_bar(instrument, base + i * H1) for i in range(h1)),
        h4=tuple(_bar(instrument, base + i * 4 * H1) for i in range(h4)),
        d1=tuple(_bar(instrument, base + timedelta(days=i)) for i in range(d1)),
        w1=tuple(_bar(instrument, base + timedelta(weeks=i)) for i in range(w1)),
        h4_issues=(),
    )


def test_all_required_timeframes_are_accepted():
    _history().assert_bootstrap_ready()
    assert set(MULTI_ASSET_LIMITS) >= {"M30", "H1", "H4", "D1", "W1"}
    assert MULTI_ASSET_LIMITS["H1"] >= 128


@pytest.mark.parametrize("timeframe", ["m30", "h1", "h4", "d1", "w1"])
def test_missing_required_timeframe_fails_closed(timeframe):
    kwargs = {"m30": 40, "h1": 128, "h4": 30, "d1": 10, "w1": 10}
    kwargs[timeframe] = 0
    with pytest.raises(InsufficientPlatformHistoryError, match=timeframe.upper()):
        _history(**kwargs).assert_bootstrap_ready()


# 6/7. approved closures + genuine holes ------------------------------------


def test_approved_break_is_marked_but_a_data_hole_is_not():
    day = datetime(2026, 9, 23, tzinfo=UTC)
    # 09:00-10:00 UTC exists, 10:00-11:00 UTC is a genuine hole, 11:00 resumes.
    bars = (
        _bar("METAL_XAU_USD", day + timedelta(hours=9)),
        _bar("METAL_XAU_USD", day + timedelta(hours=11)),
        _bar("METAL_XAU_USD", day + timedelta(hours=12)),
    )
    marked = mark_multi_asset_closures(bars, "METAL_XAU_USD")
    assert marked[1].expected_closure_before is False
    assert _missing_issue(marked, Timeframe.H1, "MISSING_SIGNAL_CANDLE") == [
        "MISSING_SIGNAL_CANDLE"
    ]

    # The approved 21:00->22:00 UTC (22:00-23:00 London) break is a closure.
    break_bars = (
        _bar("METAL_XAU_USD", day + timedelta(hours=20)),
        _bar("METAL_XAU_USD", day + timedelta(hours=22)),
    )
    marked_break = mark_multi_asset_closures(break_bars, "METAL_XAU_USD")
    assert marked_break[1].expected_closure_before is True
    assert _missing_issue(marked_break, Timeframe.H1, "MISSING_SIGNAL_CANDLE") == []


def test_genuine_hole_still_fails_closed_for_crypto():
    day = datetime(2026, 9, 23, tzinfo=UTC)
    bars = (
        _bar("CRYPTO_BTC_USD", day + timedelta(hours=9)),
        _bar("CRYPTO_BTC_USD", day + timedelta(hours=11)),
    )
    marked = mark_multi_asset_closures(bars, "CRYPTO_BTC_USD")
    assert marked[1].expected_closure_before is False
    assert _missing_issue(marked, Timeframe.H1, "MISSING_SIGNAL_CANDLE")


# H4 construction ------------------------------------------------------------


def test_h4_buckets_use_approved_components_and_keep_weekends_for_crypto():
    # Crypto is 24/7: a London bucket including weekend hours is complete.
    start = datetime(2026, 9, 19, 23, 0, tzinfo=UTC)  # London 00:00 (BST)
    bars = tuple(
        _bar("CRYPTO_BTC_USD", start + i * H1, source_ids=(f"MDP:{i}",))
        for i in range(4)
    )
    built = multi_asset_h4_bars(
        bars, as_of=start + 4 * H1, instrument_id="CRYPTO_BTC_USD"
    )
    assert len(built) == 1
    assert built[0].close_time == start + 4 * H1
    assert built[0].source_candle_ids

    # Gold has a 22:00-23:00 London break; the break-affected bucket is built
    # from its approved components instead of being dropped.
    day = datetime(2026, 9, 23, 19, 0, tzinfo=UTC)  # London 20:00
    gold_bars = tuple(
        _bar("METAL_XAU_USD", day + i * H1, source_ids=(f"MDP:{i}",))
        for i in (0, 1, 3)
    )  # 21:00 UTC (22:00 London) is the approved break
    built = multi_asset_h4_bars(
        gold_bars, as_of=day + 4 * H1, instrument_id="METAL_XAU_USD"
    )
    assert len(built) == 1
    assert built[0].source_candle_ids
    assert built[0].construction_profile_version == "IG_PLATFORM_MULTI_ASSET_V1"


# 8. readiness paths ---------------------------------------------------------


def test_micro_and_macro_readiness_use_existing_evaluator_requirements():
    from backend.app.market_data.evaluation_readiness import forex_candidate_ready

    history = _history()
    assert forex_candidate_ready(history, "MICRO", "M30")
    assert forex_candidate_ready(history, "MICRO", "H1")
    assert forex_candidate_ready(history, "MACRO", "H1")
    assert forex_candidate_ready(history, "MACRO", "H4")
    short = _history(h1=100)
    assert not forex_candidate_ready(short, "MICRO", "H1")


def _daily_bar(instrument_id: str, opened: datetime, close: float = 100.0) -> Bar:
    from backend.app.market_data.profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID

    price = Decimal(str(close))
    return Bar(
        instrument_id=instrument_id,
        timeframe=Timeframe.D1,
        open_time=opened,
        close_time=opened + timedelta(days=1),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        provider=PLATFORM_PROVIDER_ID,
        is_complete=True,
        synthetic=False,
        quality_status="VALID",
        construction_profile_version=PROFILE_ID,
        provider_adapter_version="test",
        source_candle_ids=("MDP-NATIVE:1",),
    )


def test_multi_asset_sparse_h1_filter_uses_approved_components():
    from backend.app.engine.current_daily_filter import build_daily_filter_snapshot

    day = datetime(2026, 9, 22, 0, 0, tzinfo=UTC)
    h1 = []
    cursor = day
    while cursor < day + timedelta(days=1):
        h1.append(_bar("METAL_XAU_USD", cursor))
        cursor += H1
    closed = tuple(h1)
    snapshot = build_daily_filter_snapshot(
        provider=PLATFORM_PROVIDER_ID,
        instrument="METAL_XAU_USD",
        as_of_h1_close=closed[-1].close_time,
        h1_bars=closed,
        completed_d1_bars=tuple(
            _daily_bar("METAL_XAU_USD", day - timedelta(days=offset))
            for offset in range(2, 12)
        ),
        sparse_actual_h1=True,
    )
    assert snapshot.current_partial_d1.h1_count > 0
    assert snapshot.strategy_version


# 9/10. favourites is presentation only -------------------------------------


def test_favorites_contains_exactly_the_required_instruments():
    assert FAVORITES == EXPECTED_FAVORITES
    assert len(FAVORITES) == 11
    assert len(set(FAVORITES)) == 11
    assert "EUR_USD" in FAVORITES
    assert set(FAVORITES) - {"EUR_USD"} == set(MULTI_ASSET_IDS)
    assert FAVORITES == tuple(dict.fromkeys(FAVORITES))


def test_favorites_does_not_create_parallel_evaluation_state():
    # Favorites is a bounded list of the same identities: no extra registry
    # entries, no duplicated metadata, no separate evaluation key namespace.
    instruments = platform_instruments(FAVORITES)
    ids = [item.instrument_id for item in instruments]
    assert len(ids) == len(set(ids)) == 11
    instances = [spec.instrument_id for spec in MULTI_ASSET_SPECS]
    assert len(instances) == len(set(instances))
    for instrument_id in FAVORITES:
        if instrument_id == "EUR_USD":
            assert platform_instrument_id(instrument_id) == "FX_EUR_USD"
        else:
            assert spect8_instrument_id(instrument_id) == instrument_id
    assert ScannerData.model_fields["favorites"].default == []


def test_favorites_payload_uses_the_single_authority_constant():
    from backend.app.dashboard_api import FAVORITES as api_favorites

    assert tuple(api_favorites) == EXPECTED_FAVORITES


# 11. existing FX mappings unchanged ----------------------------------------


def test_existing_fx_mappings_are_unchanged():
    from backend.app.market_data.share_catalog import SHARE_AUTHORITIES

    assert len(INSTRUMENT_AUTHORITY_MAP) == 21 + len(SHARE_AUTHORITIES) + len(MULTI_ASSET_IDS)
    assert INSTRUMENT_AUTHORITY_MAP["USD_JPY"] == "IG_LIVE"
    assert INSTRUMENT_AUTHORITY_MAP["EUR_USD"] == "IG_DEMO"
    assert is_multi_asset("EUR_USD") is False


def test_platform_family_collection_status_is_normalized():
    from backend.app.market_data.collection_view import _normalized_collection

    streaming = _normalized_collection(
        {"status": "READY_FOR_SPECT8", "subscription_state": "SUBSCRIBED"}
    )
    assert streaming["state"] == "STREAMING"
    blocked = _normalized_collection({"status": "STREAMING_FAILURE"})
    assert blocked["state"] == "BLOCKED"
    assert blocked["failure_reason"] == "STREAMING_FAILURE"
    assert _normalized_collection({"status": "PROVIDER_HISTORY_HOLE"})["state"] == (
        "RECOVERING"
    )
    # Authoritative FX states are never rewritten.
    assert _normalized_collection({"state": "RECOVERING"}) == {"state": "RECOVERING"}
    assert _normalized_collection(None) is None


def test_family_pending_gaps_come_from_authoritative_coverage():
    from backend.app.market_data.collection_view import _normalized_collection

    ready = _normalized_collection(
        {
            "status": "READY_FOR_SPECT8",
            "subscription_state": "SUBSCRIBED",
            "reasons": [],
            "terminal_holes": [],
            "coverage": {"M30": 46, "H1": 140, "H4": 30, "D1": 10, "W1": 10},
        }
    )
    assert ready["pending_gaps"] == 0
    assert ready["terminal_gaps"] == 0

    deficit = _normalized_collection(
        {
            "status": "HISTORY_INSUFFICIENT",
            "reasons": ["M30_INSUFFICIENT:33/40", "H4_INSUFFICIENT:27/30"],
            "terminal_holes": ["2026-09-24T07:30:00+00:00"],
            "coverage": {"M30": 33, "H1": 140, "H4": 27, "D1": 10, "W1": 10},
        }
    )
    assert deficit["state"] == "COLLECTING"
    assert deficit["pending_gaps"] == 7 + 3
    assert deficit["terminal_gaps"] == 1

    # Missing or partial telemetry is never converted into a fabricated zero.
    absent = _normalized_collection({"status": "READY_FOR_SPECT8"})
    assert "pending_gaps" not in absent
    partial = _normalized_collection(
        {"status": "READY_FOR_SPECT8", "coverage": {"M30": 46}}
    )
    assert "pending_gaps" not in partial
    malformed = _normalized_collection(
        {
            "status": "READY_FOR_SPECT8",
            "coverage": {"M30": None, "H1": 140, "H4": 30, "D1": 10, "W1": 10},
        }
    )
    assert "pending_gaps" not in malformed


def test_session_profile_is_legacy_metadata_only():
    from backend.app.market_data.models import InstrumentMetadata
    from backend.app.market_data.polling_policy import expected_latest_h1_close

    instruments = {i.instrument_id: i for i in platform_instruments(MULTI_ASSET_IDS)}
    assert all(
        item.session_profile
        in {SessionProfileKind.CRYPTO_24_7, SessionProfileKind.PLATFORM_FAMILY_WEEKDAY}
        for item in instruments.values()
    )
    # The strategy instrument metadata contract never carries session_profile.
    assert "session_profile" not in InstrumentMetadata.__dataclass_fields__

    # Legacy non-platform profiles are untouched.
    legacy = {i.instrument_id: i for i in platform_instruments(("EUR_USD",))}
    assert legacy["EUR_USD"].session_profile is SessionProfileKind.FOREX_WEEKDAY

    # The only session-profile branch in polling is US_EQUITY_REGULAR; the new
    # values keep the same whole-hour expectation as FOREX_WEEKDAY.
    now = datetime(2026, 9, 24, 10, 17, tzinfo=UTC)
    whole_hour = now.replace(minute=0, second=0, microsecond=0)
    for instrument_id in ("CRYPTO_BTC_USD", "METAL_XAU_USD", "INDEX_US500"):
        assert (
            expected_latest_h1_close(instruments[instrument_id], now) == whole_hour
        )
