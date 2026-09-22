"""Retained multi-policy history must be filtered to the exact active policy.

The Platform bounded reader now preserves every policy/revision row for the
latest N logical intervals so downstream authority selection stays possible.
Dashboard preparation must therefore select the exact approved/current policy
*before* converting rows with ``share_bar``; obsolete policy rows for the same
logical interval must never reach it. These are pure fixtures — no real
database or provider is touched.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.app.domain import Timeframe
from backend.app.market_data.platform_adapter import (
    PlatformCanonicalBar,
    PlatformNativeBootstrapBar,
    PlatformReadBatch,
)
from backend.app.market_data.share_profile import (
    authoritative_canonical_rows,
    prepare_share_history,
    share_history,
)
from backend.app.repository import SQLiteProjectionRepository
from hedgefund_market_data.domain import Timeframe as PlatformTimeframe
from hedgefund_market_data.providers.ig.share_contract import (
    ShareContract,
    ShareNormalizationManifest,
)
from hedgefund_market_data.providers.ig.share_plan import required_windows

UTC = timezone.utc
NOW = datetime(2026, 9, 10, 12, 31, tzinfo=UTC)
OBSOLETE_PROFILE = "IG_DEMO_MSFT_SHARE_SB_V0"


def _contract() -> ShareContract:
    return ShareContract(
        "MSFT",
        "DEMO",
        "UC.D.MSFT.DAILY.IP",
        "Microsoft Corp (24 Hours)",
        "0.01",
        3,
        ("fixture",),
        exceptions=(("2026-09-07", "CLOSED", None, None, "Fixture holiday"),),
    )


def _canonical(contract, timeframe, opened, *, policy_id, seq, price_type="BID"):
    end = contract.bucket(opened, timeframe)[1]
    return PlatformCanonicalBar(
        instrument_id=contract.instrument_id,
        timeframe=timeframe.value,
        price_type=price_type,
        open_time=opened,
        close_time=end,
        open=Decimal(100),
        high=Decimal(102),
        low=Decimal(98),
        close=Decimal(101),
        volume=None,
        volume_type="UNKNOWN",
        source_provider_id=contract.provider_id,
        canonical_bar_id=seq,
        quality_status="VALID",
        policy_id=policy_id,
        policy_version="1",
        version_number=1,
        semantic_hash=f"{policy_id}-{opened.isoformat()}-{price_type}",
        semantic_available_at=end + timedelta(seconds=60),
    )


def _native(contract, timeframe, opened, *, seq):
    end = contract.bucket(opened, timeframe)[1]
    return PlatformNativeBootstrapBar(
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
        bootstrap_bar_id=seq,
        provenance=(
            "NATIVE_IG_HISTORICAL_BOOTSTRAP"
            if timeframe == PlatformTimeframe.H1
            else "TEMPORARY_NATIVE_IG_FILTER_BOOTSTRAP"
        ),
        observed_at=NOW,
        raw_snapshot_time=None,
        raw_snapshot_time_utc=opened.isoformat(),
        normalization_manifest_checksum=ShareNormalizationManifest(contract).checksum(),
        provider_metadata={"account_type": "SPREADBET"},
    )


def _batch(contract, *, obsolete_timeframes=("D1", "W1"), native=True, obsolete_only=False):
    windows = required_windows(contract, NOW)
    bars, natives = [], []
    seq = 1
    for timeframe, openings in windows.items():
        if timeframe == PlatformTimeframe.H4:
            continue
        for opened in openings:
            if timeframe == PlatformTimeframe.M30:
                bars.append(
                    _canonical(
                        contract, timeframe, opened,
                        policy_id=f"{contract.profile_id}_M30_BID", seq=seq,
                    )
                )
                seq += 1
                continue
            if obsolete_only:
                bars.append(
                    _canonical(
                        contract, timeframe, opened,
                        policy_id=f"{OBSOLETE_PROFILE}_{timeframe.value}_BID", seq=seq,
                    )
                )
                seq += 1
                continue
            bars.append(
                _canonical(
                    contract, timeframe, opened,
                    policy_id=f"{contract.profile_id}_{timeframe.value}_BID", seq=seq,
                )
            )
            seq += 1
            if timeframe.value in obsolete_timeframes:
                bars.append(
                    _canonical(
                        contract, timeframe, opened,
                        policy_id=f"{OBSOLETE_PROFILE}_{timeframe.value}_BID", seq=seq,
                    )
                )
                seq += 1
            if native:
                natives.append(_native(contract, timeframe, opened, seq=seq))
                seq += 1
    return PlatformReadBatch(
        tuple(bars), (), len(bars), NOW, "fixture", "fixture", "fixture", tuple(natives)
    )


class Child:
    def __init__(self, contract, repository):
        self._share_contract = contract
        self._repository = repository


def _child(tmp_path, contract):
    repo = SQLiteProjectionRepository(tmp_path / "retained.sqlite3")
    repo.initialize()
    return Child(contract, repo)


def test_retained_obsolete_policy_rows_are_excluded_before_share_bar(tmp_path):
    contract = _contract()
    batch = _batch(contract)
    history = share_history(batch, "SHARE_MSFT", contract)
    child = _child(tmp_path, contract)
    # Baseline raised ValueError("share canonical policy mismatch") here because
    # obsolete D1/W1 rows reached share_bar().
    candidates = prepare_share_history(child, batch, history)
    assert candidates
    assert all(
        row.policy_id == f"{contract.profile_id}_{row.timeframe}_BID"
        for row in authoritative_canonical_rows(batch, contract)
    )


def test_all_current_policy_intervals_survive(tmp_path):
    contract = _contract()
    batch = _batch(contract)
    history = share_history(batch, "SHARE_MSFT", contract)
    child = _child(tmp_path, contract)
    prepare_share_history(child, batch, history)
    persisted = child._repository.canonical_bar_objects(
        "MARKET_DATA_PLATFORM", "SHARE_MSFT", "D1"
    )
    assert persisted
    assert all(
        bar.provider_adapter_version.startswith("IG_DEMO_MSFT_SHARE_SB_V1_D1_BID")
        for bar in persisted
    )


def test_obsolete_only_interval_is_not_substituted(tmp_path):
    contract = _contract()
    batch = _batch(contract, obsolete_only=True, native=False)
    history = share_history(batch, "SHARE_MSFT", contract)
    assert history.d1 == ()
    assert history.w1 == ()
    with pytest.raises(Exception, match="insufficient"):
        history.assert_bootstrap_ready()


def test_ask_rows_never_enter_the_bid_authority(tmp_path):
    contract = _contract()
    batch = _batch(contract)
    ask = _canonical(
        contract,
        PlatformTimeframe.D1,
        required_windows(contract, NOW)[PlatformTimeframe.D1][0],
        policy_id=f"{contract.profile_id}_D1_BID",
        seq=99999,
        price_type="ASK",
    )
    batch = replace(batch, bars=(*batch.bars, ask))
    rows = authoritative_canonical_rows(batch, contract)
    assert all(row.price_type == "BID" for row in rows)
    assert ask not in rows


def test_current_policy_history_is_complete_and_contiguous(tmp_path):
    contract = _contract()
    batch = _batch(contract)
    history = share_history(batch, "SHARE_MSFT", contract)
    history.assert_bootstrap_ready()
    for series in (history.d1, history.w1):
        for previous, current in zip(series, series[1:]):
            assert current.open_time == previous.close_time or current.expected_closure_before
