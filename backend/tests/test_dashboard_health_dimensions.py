"""Health dimensions stay separate and counters stay truthful.

Feed/source health, collection/recovery health, filter readiness and signal
readiness are independent. A healthy stream with a stale recovery heartbeat
or a blocked H4 evaluation must never become an "unhealthy feed", and a
blocked signal dependency must never invalidate a healthy filter.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.app.dashboard_api import _feed_health_state, scanner_snapshot
from backend.app.domain import FilterMode
from backend.app.main import _apply_scanner_row_overlay
from backend.app.market_data.collection_view import attach_collection_status
from backend.app.repository import SQLiteProjectionRepository

UTC = timezone.utc


class Row(BaseModel):
    model_config = ConfigDict(extra="allow")
    instrument_id: str
    data_status: str
    stale: bool
    feed_health: str = "HEALTHY"
    attention_reasons: list[str] = []


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="allow")
    active_filter_mode: str
    instruments: list[Any]


def _nzdjpy_blocked_detail():
    return {
        "evaluation_freshness": "STALE",
        "failure_reason": "MISSING_SIGNAL_CANDLE",
        "collection": {
            "state": "BLOCKED",
            "failure_reason": "RECOVERY_HEARTBEAT_STALE",
        },
        "evaluation_modes": {
            "MICRO": {"evaluation_freshness": "CURRENT", "failure_reason": None},
            "MACRO": {
                "evaluation_freshness": "STALE",
                "failure_reason": "MISSING_SIGNAL_CANDLE",
            },
        },
        "evaluation_candidates": [
            {"mode": "MICRO", "timeframe": "M30", "state": "CURRENT"},
            {"mode": "MICRO", "timeframe": "H1", "state": "CURRENT"},
            {"mode": "MACRO", "timeframe": "H1", "state": "CURRENT"},
            {
                "mode": "MACRO",
                "timeframe": "H4",
                "state": "STALE",
                "failure_reason": "MISSING_SIGNAL_CANDLE",
            },
        ],
    }


def test_blocked_recovery_and_signal_do_not_become_unhealthy_feed():
    row = Row(instrument_id="NZD_JPY", data_status="HEALTHY", stale=False)
    snapshot = Snapshot(active_filter_mode="MACRO", instruments=[row])
    detail = _nzdjpy_blocked_detail()
    result = attach_collection_status(
        snapshot,
        SimpleNamespace(
            status=lambda: {"collection_instruments": {"NZD_JPY": detail}}
        ),
    )
    row = result.instruments[0]
    # Source/feed health is preserved independently of recovery + signal state.
    assert row.feed_health == "HEALTHY"
    assert row.collection_health == "BLOCKED"
    assert row.signal_readiness == "STALE"
    assert any("SIGNAL_MISSING_SIGNAL_CANDLE" == reason for reason in row.attention_reasons)
    assert any("RECOVERY_RECOVERY_HEARTBEAT_STALE" == reason for reason in row.attention_reasons)
    assert row.data_status == "STALE"
    assert len(row.evaluation_candidates) == 4


def test_filter_and_evaluation_overlay_preserve_feed_health():
    row = Row(
        instrument_id="EUR_USD",
        data_status="HEALTHY",
        stale=False,
        feed_health="HEALTHY",
    )
    row.current_filter = SimpleNamespace(
        filter_freshness="STALE",
        stale_reason="FILTER_BEHIND_LATEST_H1",
        as_of_h1_close_time=None,
    )
    runtime = SimpleNamespace(status=lambda: {"collection_instruments": {}})
    overlaid = _apply_scanner_row_overlay([row], runtime)[0]
    assert overlaid.feed_health == "HEALTHY"
    assert overlaid.data_status == "STALE"
    assert "FILTER_BEHIND_LATEST_H1" in overlaid.attention_reasons


def _fresh_h1_bar(instrument_id: str):
    from decimal import Decimal

    from backend.app.domain import Bar, Timeframe

    close = datetime.now(UTC) - timedelta(minutes=15)
    opened = close - timedelta(hours=1)
    return Bar(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        open_time=opened,
        close_time=close,
        open=Decimal("1.1"),
        high=Decimal("1.2"),
        low=Decimal("1.0"),
        close=Decimal("1.15"),
        provider="MARKET_DATA_PLATFORM",
        is_complete=True,
        volume=None,
        session_timezone="UTC",
        raw_provider_symbol=instrument_id,
        raw_open_time=opened.isoformat(),
        raw_close_time=close.isoformat(),
        raw_open="1.1",
        raw_high="1.2",
        raw_low="1.0",
        raw_close="1.15",
        synthetic=False,
        quality_status="VALID",
        construction_profile_version="test",
        provider_adapter_version="test",
        source_candle_ids=("t",),
        forward_filled=False,
        ingestion_run_id="t",
        created_at=close,
    )


def test_feed_health_is_source_freshness_only(tmp_path):
    from dataclasses import replace

    from backend.app.market_data.registry import twelve_data_instruments

    repo = SQLiteProjectionRepository(tmp_path / "feed.sqlite3")
    repo.initialize()
    instrument = replace(
        next(
            item
            for item in twelve_data_instruments()
            if item.instrument_id == "NZD_JPY"
        ),
        provider_id="MARKET_DATA_PLATFORM",
        synthetic=False,
    )
    now = datetime.now(UTC)
    # No source data yet: bootstrapping, not healthy.
    assert _feed_health_state(repo, instrument, None, now, 7200) == "BOOTSTRAPPING"
    repo.persist_canonical_bars((_fresh_h1_bar("NZD_JPY"),))
    assert _feed_health_state(repo, instrument, None, now, 7200) == "HEALTHY"


class _NzdjpyRepository:
    """Canonical projection that holds a current W1 filter snapshot."""

    def active_filter_mode(self):
        return FilterMode.MACRO

    def statuses(self):
        return []

    def instrument_health(self, provider_id, instrument_id):
        return None

    def latest_candle_timestamps(self, provider_id, instrument_id):
        return {"H1": "2026-09-22T09:00:00Z", "H4": "2026-09-22T09:00:00Z"}

    def latest_weekly_filter_snapshot(self, provider_id, instrument_id, version):
        return {
            "snapshot_id": "wfs_1",
            "as_of_h1_close_time_utc": "2026-09-22T09:00:00Z",
            "final_classification": "BUY",
            "buy_matched": True,
            "sell_matched": False,
        }

    def present_h1_close_times(self, provider_id, instrument_id):
        return {"2026-09-22T09:00:00Z"}


def test_nzdjpy_blocked_h4_keeps_feed_and_filter_current():
    from dataclasses import replace

    from backend.app.market_data.registry import twelve_data_instruments

    instrument = replace(
        next(
            item
            for item in twelve_data_instruments()
            if item.instrument_id == "NZD_JPY"
        ),
        provider_id="MARKET_DATA_PLATFORM",
        synthetic=False,
    )
    generated = datetime(2026, 9, 22, 9, 5, tzinfo=UTC)
    snapshot = scanner_snapshot(_NzdjpyRepository(), (instrument,), generated)
    row = snapshot.instruments[0]
    # The W1 filter snapshot is authoritative and current for NZDJPY; the
    # known provider-history hole blocks only the Macro H4 evaluation.
    assert row.current_filter.filter_freshness == "CURRENT"
    assert row.filter_readiness == "CURRENT"
    assert row.feed_health == "HEALTHY"
    blocked = Snapshot(active_filter_mode="MACRO", instruments=[row])
    attached = attach_collection_status(
        blocked,
        SimpleNamespace(
            status=lambda: {
                "collection_instruments": {"NZD_JPY": _nzdjpy_blocked_detail()}
            }
        ),
    ).instruments[0]
    assert attached.feed_health == "HEALTHY"
    assert attached.filter_readiness == "CURRENT"
    assert attached.current_filter.filter_freshness == "CURRENT"
    assert attached.signal_readiness == "STALE"
    assert attached.data_status == "STALE"
