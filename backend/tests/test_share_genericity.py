"""A fictional compatible share reaches the full share path without new branches.

The fixture registers an unfamiliar symbol, persisted ID and EPIC through the
generic configuration entry points only, then proves profile registration,
history reading, readiness, Micro/Macro filters, signal evaluation and the
catalog/scanner projection all work. No runtime Python is changed for the new
symbol.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.app.domain import FilterMode, Timeframe
from backend.app.engine.current_daily_filter import build_daily_filter_snapshot
from backend.app.engine.current_w1_filter import build_w1_filter_snapshot
from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.market_data.isolated_platform import IsolatedPlatformAuthorityRuntime
from backend.app.market_data.platform_adapter import (
    PlatformCanonicalBar,
    PlatformNativeBootstrapBar,
    PlatformReadBatch,
)
from backend.app.market_data.share_catalog import (
    SHARE_AUTHORITIES,
    SHARE_PLATFORM_IDS,
    SHARE_SYMBOLS,
    platform_instruments,
    register_share,
    share_platform_id,
)
from backend.app.market_data.share_profile import share_history
from backend.app.market_data.signal_lifecycle import SignalLifecycleService
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService
from hedgefund_market_data.domain import Timeframe as PlatformTimeframe
from hedgefund_market_data.providers.ig.share_contract import (
    ShareContract,
    ShareNormalizationManifest,
)
from hedgefund_market_data.providers.ig.share_plan import required_windows
from hedgefund_market_data.providers.ig.share_universe import (
    SHARES_BY_SYMBOL,
    ShareSpec,
    register_share_spec,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 10, 12, 31, tzinfo=UTC)
SYMBOL = "ZQX"
SPECT8_ID = "SHARE_ZQX"


@pytest.fixture()
def fictional_contract():
    spec = ShareSpec(SYMBOL, "DEMO", "Zephyr Quantum", ("zephyr", "quantum"))
    register_share_spec(spec)
    register_share(SYMBOL, "DEMO")
    try:
        yield ShareContract(
            SYMBOL,
            "DEMO",
            "UC.D.ZQX.DAILY.IP",
            "Zephyr Quantum (24 Hours)",
            "0.01",
            3,
            ("generic fixture",),
            exceptions=(("2026-09-07", "CLOSED", None, None, "Fixture holiday"),),
        )
    finally:
        SHARES_BY_SYMBOL.pop(SYMBOL, None)
        SHARE_AUTHORITIES.pop(SPECT8_ID, None)
        SHARE_PLATFORM_IDS.pop(share_platform_id(SYMBOL), None)
        SHARE_SYMBOLS.pop(SPECT8_ID, None)


def _batch(contract):
    windows = required_windows(contract, NOW)
    bars, native = [], []
    seq = 1
    for timeframe, openings in windows.items():
        if timeframe == PlatformTimeframe.H4:
            continue
        for opened in openings:
            end = contract.bucket(opened, timeframe)[1]
            values = dict(
                instrument_id=contract.instrument_id,
                timeframe=timeframe.value,
                price_type="BID",
                open_time=opened,
                close_time=end,
                open=Decimal(100),
                high=Decimal(102),
                low=Decimal(98),
                close=Decimal(101),
                volume=None,
                volume_type="UNKNOWN",
                source_provider_id=contract.provider_id,
            )
            if timeframe == PlatformTimeframe.M30:
                bars.append(
                    PlatformCanonicalBar(
                        **values,
                        canonical_bar_id=seq,
                        quality_status="VALID",
                        policy_id=f"{contract.profile_id}_M30_BID",
                        policy_version="1",
                        version_number=1,
                        semantic_hash=str(seq),
                        semantic_available_at=end + timedelta(seconds=60),
                    )
                )
            else:
                native.append(
                    PlatformNativeBootstrapBar(
                        **values,
                        bootstrap_bar_id=seq,
                        provenance=(
                            "NATIVE_IG_HISTORICAL_BOOTSTRAP"
                            if timeframe == PlatformTimeframe.H1
                            else "TEMPORARY_NATIVE_IG_FILTER_BOOTSTRAP"
                        ),
                        observed_at=NOW,
                        raw_snapshot_time=None,
                        raw_snapshot_time_utc=opened.isoformat(),
                        normalization_manifest_checksum=ShareNormalizationManifest(
                            contract
                        ).checksum(),
                        provider_metadata={"account_type": "SPREADBET"},
                    )
                )
            seq += 1
    return PlatformReadBatch(
        tuple(bars), (), len(bars), NOW, "fixture", "fixture", "fixture", tuple(native)
    )


def test_fictional_share_reaches_history_filters_and_readiness(fictional_contract):
    contract = fictional_contract
    batch = _batch(contract)
    history = share_history(batch, SPECT8_ID, contract)
    history.assert_bootstrap_ready()

    daily = build_daily_filter_snapshot(
        provider="MARKET_DATA_PLATFORM",
        instrument=SPECT8_ID,
        as_of_h1_close=history.h1[-1].close_time,
        h1_bars=history.h1,
        completed_d1_bars=history.d1,
        share_contract=contract,
    )
    weekly = build_w1_filter_snapshot(
        provider="MARKET_DATA_PLATFORM",
        instrument=SPECT8_ID,
        as_of_h1_close=history.h1[-1].close_time,
        h1_bars=history.h1,
        completed_w1_bars=history.w1,
        share_contract=contract,
    )
    assert daily.data_quality_status == weekly.data_quality_status == "VALID"
    assert daily.canonical_profile_version == contract.profile_id
    assert daily.current_partial_d1.session_open_utc.hour == 4


def test_fictional_share_catalog_row_needs_no_frontend_change(fictional_contract):
    values = platform_instruments((SPECT8_ID,))
    assert [item.instrument_id for item in values] == [SPECT8_ID]
    assert values[0].display_symbol == SYMBOL
    assert "spread betting" in values[0].display_name


def test_fictional_share_runtime_evaluates_without_symbol_specific_code(
    tmp_path, fictional_contract
):
    contract = fictional_contract
    batch = _batch(contract)
    gateway = SimpleNamespace(read=lambda *args, **kwargs: batch)
    repo = SQLiteProjectionRepository(tmp_path / "zqx.sqlite3")
    repo.initialize()
    runtime = IsolatedPlatformAuthorityRuntime(
        {contract.provider_id: gateway},
        repo,
        WalkingSkeletonService(Spect8StrategyEvaluator(), None, repo),
        (SPECT8_ID,),
        instrument_to_authority={SPECT8_ID: contract.provider_id},
        stale_after_seconds=7200,
        poll_seconds=30,
        signal_lifecycle=SignalLifecycleService(repo),
    )
    runtime._collection_status = lambda _: {
        "state": "COLLECTING",
        "updated_at": NOW.isoformat(),
        "contract": contract.payload(),
        "account_type": "SPREADBET",
        "automatic": True,
    }
    result = runtime.run_once(available_as_of=NOW)
    assert result.evaluations_created > 0
    detail = runtime.status()["collection_instruments"][SPECT8_ID]
    assert detail["evaluation_freshness"] == "CURRENT", detail
    assert FilterMode.MICRO.value in detail["evaluation_modes"]
