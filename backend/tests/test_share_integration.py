from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

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
from backend.app.market_data.share_catalog import SHARE_AUTHORITIES, platform_instruments
from backend.app.market_data.share_profile import forming_snapshot, share_history
from backend.app.market_data.signal_lifecycle import SignalLifecycleService
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService
from hedgefund_market_data.domain import Timeframe as PlatformTimeframe
from hedgefund_market_data.providers.ig.share_contract import ShareContract, ShareNormalizationManifest
from hedgefund_market_data.providers.ig.share_plan import required_windows

UTC = timezone.utc
NOW = datetime(2026, 9, 10, 12, 31, tzinfo=UTC)


def source(symbol="MSFT"):
    env = "DEMO" if symbol == "MSFT" else "LIVE"
    c = ShareContract(symbol, env, "UC.D.MSFT.DAILY.IP" if symbol == "MSFT" else "UA.D.AAPL.DAILY.IP",
                      symbol + " (24 Hours)", "0.01", 3, ("fixture",),
                      exceptions=(("2026-09-07", "CLOSED", None, None, "Fixture holiday"),))
    windows = required_windows(c, NOW)
    bars, native = [], []
    seq = 1
    for tf, openings in windows.items():
        if tf == PlatformTimeframe.H4:
            continue
        for opened in openings:
            end = c.bucket(opened, tf)[1]
            values = dict(instrument_id=c.instrument_id, timeframe=tf.value, price_type="BID",
                          open_time=opened, close_time=end, open=Decimal(100), high=Decimal(102),
                          low=Decimal(98), close=Decimal(101), volume=None, volume_type="UNKNOWN",
                          source_provider_id=c.provider_id)
            if tf == PlatformTimeframe.M30:
                bars.append(PlatformCanonicalBar(**values, canonical_bar_id=seq, quality_status="VALID",
                    policy_id=f"{c.profile_id}_M30_BID", policy_version="1", version_number=1,
                    semantic_hash=str(seq), semantic_available_at=end + timedelta(seconds=60)))
            else:
                native.append(PlatformNativeBootstrapBar(**values, bootstrap_bar_id=seq,
                    provenance="NATIVE_IG_HISTORICAL_BOOTSTRAP" if tf == PlatformTimeframe.H1 else "TEMPORARY_NATIVE_IG_FILTER_BOOTSTRAP",
                    observed_at=NOW, raw_snapshot_time=None, raw_snapshot_time_utc=opened.isoformat(),
                    normalization_manifest_checksum=ShareNormalizationManifest(c).checksum(),
                    provider_metadata={"account_type": "SPREADBET"}))
            seq += 1
    batch = PlatformReadBatch(tuple(bars), (), len(bars), NOW, "fixture", "fixture", "fixture", tuple(native))
    return c, batch


def test_share_history_filters_and_partial_prefix_are_calendar_bound():
    c, batch = source()
    history = share_history(batch, "SHARE_MSFT", c)
    history.assert_bootstrap_ready()
    for tf in (Timeframe.H1, Timeframe.H4):
        partial = forming_snapshot(c, "SHARE_MSFT", tf, history.m30, NOW)
        assert partial and partial.as_of == NOW.replace(minute=30)
        assert partial.to_spect8_bar().construction_profile_version == c.profile_id
    daily = build_daily_filter_snapshot(provider="MARKET_DATA_PLATFORM", instrument="SHARE_MSFT",
        as_of_h1_close=history.h1[-1].close_time, h1_bars=history.h1, completed_d1_bars=history.d1, share_contract=c)
    weekly = build_w1_filter_snapshot(provider="MARKET_DATA_PLATFORM", instrument="SHARE_MSFT",
        as_of_h1_close=history.h1[-1].close_time, h1_bars=history.h1, completed_w1_bars=history.w1, share_contract=c)
    assert daily.current_partial_d1.session_open_utc.hour == 4
    assert weekly.current_partial_w1.session_open_utc == datetime(2026, 9, 6, 23, tzinfo=UTC)
    assert daily.canonical_profile_version == weekly.canonical_profile_version == c.profile_id
    with pytest.raises(ValueError, match="MISSING_OR_INVALID"):
        build_w1_filter_snapshot(provider="MARKET_DATA_PLATFORM", instrument="SHARE_MSFT",
            as_of_h1_close=history.h1[-1].close_time, h1_bars=history.h1[:-4] + history.h1[-3:],
            completed_w1_bars=history.w1, share_contract=c)
    with pytest.raises(ValueError, match="prefix"):
        forming_snapshot(c, "SHARE_MSFT", Timeframe.H4, history.m30[:-2] + history.m30[-1:], NOW)


def test_micro_filter_is_not_valid_when_daily_dependency_has_an_unexpected_hole():
    from backend.app.engine.current_daily_filter import (
        DailyFilterUnavailableError,
        build_daily_filter_snapshot,
    )

    c, batch = source()
    history = share_history(batch, "SHARE_MSFT", c)
    healthy = build_daily_filter_snapshot(
        provider="MARKET_DATA_PLATFORM",
        instrument="SHARE_MSFT",
        as_of_h1_close=history.h1[-1].close_time,
        h1_bars=history.h1,
        completed_d1_bars=history.d1,
        share_contract=c,
    )
    assert healthy.data_quality_status == "VALID"

    index = len(history.d1) // 2
    holed = history.d1[:index] + history.d1[index + 1 :]
    with pytest.raises(DailyFilterUnavailableError, match="MISSING_DAILY_CANDLE"):
        build_daily_filter_snapshot(
            provider="MARKET_DATA_PLATFORM",
            instrument="SHARE_MSFT",
            as_of_h1_close=history.h1[-1].close_time,
            h1_bars=history.h1,
            completed_d1_bars=holed,
            share_contract=c,
        )


def test_macro_filter_is_not_valid_when_weekly_dependency_has_an_unexpected_hole():
    from backend.app.engine.current_w1_filter import (
        WeeklyFilterUnavailableError,
        build_w1_filter_snapshot,
    )

    c, batch = source()
    history = share_history(batch, "SHARE_MSFT", c)
    healthy = build_w1_filter_snapshot(
        provider="MARKET_DATA_PLATFORM",
        instrument="SHARE_MSFT",
        as_of_h1_close=history.h1[-1].close_time,
        h1_bars=history.h1,
        completed_w1_bars=history.w1,
        share_contract=c,
    )
    assert healthy.data_quality_status == "VALID"

    index = len(history.w1) // 2
    holed = history.w1[:index] + history.w1[index + 1 :]
    with pytest.raises(WeeklyFilterUnavailableError, match="MISSING_WEEKLY_CANDLE"):
        build_w1_filter_snapshot(
            provider="MARKET_DATA_PLATFORM",
            instrument="SHARE_MSFT",
            as_of_h1_close=history.h1[-1].close_time,
            h1_bars=history.h1,
            completed_w1_bars=holed,
            share_contract=c,
        )


def runtime(tmp_path, *, symbol="MSFT"):
    c, batch = source(symbol)
    inst = "SHARE_" + symbol
    gateway = SimpleNamespace(read=lambda *a, **k: batch)
    repo = SQLiteProjectionRepository(tmp_path / "share.sqlite3")
    repo.initialize()
    value = IsolatedPlatformAuthorityRuntime({c.provider_id: gateway}, repo,
        WalkingSkeletonService(Spect8StrategyEvaluator(), None, repo), (inst,),
        instrument_to_authority={inst: c.provider_id}, stale_after_seconds=7200, poll_seconds=30,
        signal_lifecycle=SignalLifecycleService(repo))
    value._collection_status = lambda _: {"state": "COLLECTING", "updated_at": NOW.isoformat(),
        "contract": c.payload(), "account_type": "SPREADBET", "automatic": True}
    return value, repo, c, batch


@pytest.mark.parametrize("symbol", ["MSFT", "AAPL"])
def test_runtime_starts_evaluating_all_share_candidates_without_api_request(tmp_path, symbol):
    value, repo, c, _ = runtime(tmp_path, symbol=symbol)
    result = value.run_once(available_as_of=NOW)
    inst = "SHARE_" + symbol
    assert result.evaluations_created > 0
    assert value.status()["collection_instruments"][inst]["evaluation_freshness"] == "CURRENT"
    view = value.forming_view(as_of=NOW)
    assert len(view["forming_candidates"]) == 4
    assert sum(c["state"] == "READY" for c in view["forming_candidates"]) == 3
    assert next(c for c in view["forming_candidates"] if c["timeframe"] == "M30")["state"] == "UNSUPPORTED"
    child = value._children[inst]
    child._forming_directions = Mock(return_value=(("BUY", "SELL"), None))
    value._forming_entries = {}
    value.refresh_forming(NOW)
    assert len(value.forming_view(as_of=NOW)["forming"]) == 6
    assert not value.forming_view(as_of=NOW + timedelta(minutes=35))["forming"]
    child._forming_directions = Mock(return_value=((), None))
    value._forming_entries = {}
    value.refresh_forming(NOW)
    assert not value.forming_view(as_of=NOW)["forming"]
    assert value.forming_view(as_of=NOW)["forming_state"] == "READY"
    assert SHARE_AUTHORITIES[inst] == c.provider_id


def test_catalog_exposes_explicit_spreadbet_labels_without_enabling_ig_polling():
    values = platform_instruments(tuple(SHARE_AUTHORITIES))
    assert len(values) == 33
    assert all("spread betting" in v.display_name and not v.polling_enabled for v in values)


def test_serialized_share_api_preserves_source_and_unsupported_candidates(tmp_path):
    from pathlib import Path
    from fastapi.testclient import TestClient
    from backend.app.config import Settings
    from backend.app.main import create_app

    value, _, c, _ = runtime(tmp_path)
    value.run_once(available_as_of=NOW)
    value._children['SHARE_MSFT']._forming_directions = Mock(return_value=(("BUY",), None))
    value._forming_entries = {}
    value.refresh_forming(NOW)
    app = create_app(Settings(repository_root=Path(__file__).resolve().parents[2],
        database_path=tmp_path / 'api.sqlite3', internal_api_key='test',
        polling_enabled=False, auto_seed_synthetic=False))
    app.state.platform_authority_runtime = value
    app.state.signal_lifecycle = SimpleNamespace(current_confirmed=lambda now: ())
    app.state.clock = SimpleNamespace(now=lambda: NOW)
    response = TestClient(app).get('/signals/current', headers={'X-Spect8-Internal-Key': 'test'})
    assert response.status_code == 200
    data = response.json()['data']
    assert len(data['forming']) == 3
    assert all(s['authority'] == c.provider_id and s['account_type'] == 'SPREADBET' for s in data['forming'])
    assert all(s['source_as_of'] and s['source_bar_end'] for s in data['forming'])
    unsupported = next(c for c in data['forming_candidates'] if c['timeframe'] == 'M30')
    assert unsupported['state'] == 'UNSUPPORTED' and unsupported['supported'] is False
    assert all(c['max_age_seconds'] == 2100 for c in data['forming_candidates'])


def test_missing_weekly_input_does_not_suppress_current_micro_evaluations(tmp_path):
    value, _, _, batch = runtime(tmp_path)
    hole = datetime(2026, 9, 8, 0, tzinfo=UTC)
    broken = replace(batch, native_bootstrap_bars=tuple(b for b in batch.native_bootstrap_bars
        if not (b.timeframe == 'H1' and b.open_time == hole)))
    # Keep the 128-H1 warm-up intact while removing a current-week dependency.
    c = value._collection_status('SHARE_MSFT')['contract']
    contract = ShareContract.from_payload(c)
    first = min((b for b in batch.native_bootstrap_bars if b.timeframe == 'H1'), key=lambda b:b.open_time)
    opened = first.open_time - timedelta(hours=1)
    while not contract.expected(opened, PlatformTimeframe.H1):
        opened -= timedelta(hours=1)
    extra = replace(first, open_time=opened, close_time=opened+timedelta(hours=1), bootstrap_bar_id=99999)
    broken = replace(broken, native_bootstrap_bars=(extra, *broken.native_bootstrap_bars))
    value.gateway_for('SHARE_MSFT').read = lambda *args, **kwargs: broken
    value.run_once(available_as_of=NOW)
    detail = value.status()['collection_instruments']['SHARE_MSFT']
    assert detail['evaluation_modes']['MICRO']['evaluation_freshness'] == 'CURRENT', detail['evaluation_candidates']
    assert detail['evaluation_modes']['MACRO']['evaluation_freshness'] == 'STALE'
    assert 'SHARE_FILTER_REQUIRED_H1_MISSING_OR_INVALID' in detail['evaluation_modes']['MACRO']['failure_reason']
    assert len(detail['evaluation_candidates']) == 4


@pytest.mark.parametrize('mode,state', [('MICRO','HEALTHY'),('MACRO','STALE')])
def test_scanner_uses_selected_mode_freshness_without_hiding_collection(mode, state):
    from pydantic import BaseModel, ConfigDict
    from backend.app.market_data.collection_view import attach_collection_status
    class View(BaseModel):
        model_config = ConfigDict(extra='allow')
    row = View(instrument_id='SHARE_MSFT', data_status='STALE', stale=True)
    snapshot = View(active_filter_mode=mode, instruments=[row])
    detail = {'evaluation_freshness':'STALE','collection':{'state':'RECOVERING'},
        'evaluation_modes':{'MICRO':{'evaluation_freshness':'CURRENT','failure_reason':None},
                            'MACRO':{'evaluation_freshness':'STALE','failure_reason':'missing weekly prefix'}}}
    result = attach_collection_status(snapshot, SimpleNamespace(status=lambda:{'collection_instruments':{'SHARE_MSFT':detail}}))
    assert result.instruments[0].data_status == state
    assert result.instruments[0].collection['state'] == 'RECOVERING'
