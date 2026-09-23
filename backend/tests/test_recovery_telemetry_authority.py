"""Expired recovery telemetry is not authoritative evidence of a current failure.

`collection_recovery_status` only describes CURRENT recovery health while its
heartbeat is fresh. FX source streams can be current/HEALTHY while the IG_DEMO
recovery rows are frozen (the recovery publisher is not advancing). Converting
those expired rows into BLOCKED/RECOVERY_HEARTBEAT_STALE would add ~20 healthy
FX instruments to Require Attention. The read model must downgrade only the
expired telemetry, reusing the existing configured staleness threshold, and
must never touch feed/filter/signal readiness.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ConfigDict

from backend.app.market_data.collection_view import (
    _collection_telemetry_expired,
    attach_collection_status,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 23, 10, 50, tzinfo=UTC)
STALE_AFTER_SECONDS = 14400  # the existing configured data-staleness threshold


class Row(BaseModel):
    model_config = ConfigDict(extra="allow")
    instrument_id: str
    data_status: str = "HEALTHY"
    stale: bool = False
    feed_health: str = "HEALTHY"
    filter_readiness: str = "CURRENT"
    signal_readiness: str = "CURRENT"
    attention_reasons: list[str] = []


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="allow")
    active_filter_mode: str = "MICRO"
    instruments: list[Row]


def _collection(state, *, updated_at, failure_reason=None):
    return {
        "state": state,
        "updated_at": updated_at,
        "failure_reason": failure_reason,
        "stream_health": "HEALTHY",
    }


def _attach(state, *, updated_at, failure_reason=None, mode="MICRO", now=NOW):
    detail = {
        "collection": _collection(
            state, updated_at=updated_at, failure_reason=failure_reason
        ),
        "evaluation_freshness": "CURRENT",
    }
    runtime_holder = {
        "collection_instruments": {"EUR_USD": detail}
    }
    snapshot = Snapshot(
        active_filter_mode=mode, instruments=[Row(instrument_id="EUR_USD")]
    )
    return attach_collection_status(
        snapshot,
        _Runtime(runtime_holder),
        now=now,
        stale_after_seconds=STALE_AFTER_SECONDS,
    ).instruments[0]


class _Runtime:
    def __init__(self, holder):
        self._holder = holder

    def status(self):
        return self._holder


def test_healthy_feed_with_fresh_healthy_collection_has_no_attention():
    row = _attach("STREAMING", updated_at=NOW.isoformat())
    assert row.collection_health == "STREAMING"
    assert row.feed_health == "HEALTHY"
    assert row.attention_reasons == []


def test_healthy_feed_with_fresh_blocked_collection_stays_visible():
    row = _attach(
        "BLOCKED", updated_at=NOW.isoformat(), failure_reason="RECOVERY_HEARTBEAT_STALE"
    )
    # A genuinely fresh failure keeps surfacing as operational attention.
    assert row.collection_health == "BLOCKED"
    assert row.feed_health == "HEALTHY"
    assert any(reason.startswith("RECOVERY_") for reason in row.attention_reasons)


def test_healthy_feed_with_expired_blocked_telemetry_is_not_a_current_failure():
    expired = (NOW - timedelta(seconds=STALE_AFTER_SECONDS + 3600)).isoformat()
    row = _attach(
        "BLOCKED", updated_at=expired, failure_reason="RECOVERY_HEARTBEAT_STALE"
    )
    assert row.feed_health == "HEALTHY"
    assert row.collection_health == "UNKNOWN"
    assert not any(reason.startswith("RECOVERY_") for reason in row.attention_reasons)
    assert row.stale is False
    assert row.data_status == "HEALTHY"
    # The returned read model must not advertise the expired row as current
    # recovery truth: the frontend renders collection.state/failure_reason.
    assert row.collection["state"] == "UNKNOWN"
    assert row.collection["failure_reason"] is None
    assert row.collection["state"] != "BLOCKED"
    # Harmless provenance is retained for diagnostics.
    assert row.collection["updated_at"] == expired
    assert row.collection["stream_health"] == "HEALTHY"


@pytest.mark.parametrize("state", ["STREAMING", "COLLECTING", "RECOVERING"])
def test_expired_positive_state_with_old_failure_reason_is_non_authoritative(state):
    expired = (NOW - timedelta(days=1)).isoformat()
    row = _attach(
        state, updated_at=expired, failure_reason="RECOVERY_LONG_AGO_FAILURE"
    )
    # An old failure_reason on an expired row must not become current attention.
    assert row.collection_health == "UNKNOWN"
    assert not any(reason.startswith("RECOVERY_") for reason in row.attention_reasons)
    assert row.collection["state"] == "UNKNOWN"
    assert row.collection["failure_reason"] is None


def test_expired_payload_cannot_render_collection_blocked():
    """Frontend reads collection.state/failure_reason from the returned row."""
    expired = (NOW - timedelta(days=1)).isoformat()
    row = _attach(
        "BLOCKED", updated_at=expired, failure_reason="RECOVERY_HEARTBEAT_STALE"
    )
    payload = {"collection": row.collection, "collection_health": row.collection_health}
    assert payload["collection"]["state"] != "BLOCKED"
    assert payload["collection"].get("failure_reason") is None
    assert payload["collection_health"] == "UNKNOWN"
    assert not any(
        reason.startswith("RECOVERY_") for reason in row.attention_reasons
    )


def test_expired_telemetry_does_not_affect_filter_or_signal_readiness():
    expired = (NOW - timedelta(days=1)).isoformat()
    detail = {
        "collection": _collection(
            "BLOCKED", updated_at=expired, failure_reason="RECOVERY_HEARTBEAT_STALE"
        ),
        "evaluation_freshness": "CURRENT",
    }
    snapshot = Snapshot(
        active_filter_mode="MICRO", instruments=[Row(instrument_id="EUR_USD")]
    )
    row = attach_collection_status(
        snapshot,
        _Runtime({"collection_instruments": {"EUR_USD": detail}}),
        now=NOW,
        stale_after_seconds=STALE_AFTER_SECONDS,
    ).instruments[0]
    assert row.filter_readiness == "CURRENT"
    assert row.signal_readiness == "CURRENT"
    assert row.feed_health == "HEALTHY"
    assert row.evaluation_freshness == "CURRENT"


def test_boundary_is_the_existing_configured_threshold():
    fresh = NOW - timedelta(seconds=STALE_AFTER_SECONDS - 1)
    expired = NOW - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    assert _collection_telemetry_expired(
        {"updated_at": fresh.isoformat()}, NOW, STALE_AFTER_SECONDS
    ) is False
    assert _collection_telemetry_expired(
        {"updated_at": expired.isoformat()}, NOW, STALE_AFTER_SECONDS
    ) is True
    # No configured threshold -> never expired (backward-compatible default).
    assert _collection_telemetry_expired(
        {"updated_at": expired.isoformat()}, NOW, None
    ) is False
    # No/unparseable heartbeat -> never treated as expired evidence.
    assert _collection_telemetry_expired({}, NOW, STALE_AFTER_SECONDS) is False
    assert _collection_telemetry_expired(
        {"updated_at": "not-a-timestamp"}, NOW, STALE_AFTER_SECONDS
    ) is False


def test_expired_recovery_does_not_obscure_nzdjpy_provider_hole():
    """The real Macro H4 provider hole must stay visible on its own evidence."""
    expired = (NOW - timedelta(days=1)).isoformat()
    detail = {
        "evaluation_freshness": "STALE",
        "failure_reason": "MISSING_SIGNAL_CANDLE",
        "collection": _collection(
            "BLOCKED", updated_at=expired, failure_reason="RECOVERY_HEARTBEAT_STALE"
        ),
        "evaluation_modes": {
            "MICRO": {"evaluation_freshness": "CURRENT", "failure_reason": None},
            "MACRO": {
                "evaluation_freshness": "STALE",
                "failure_reason": "MISSING_SIGNAL_CANDLE",
            },
        },
        "evaluation_candidates": [],
    }
    snapshot = Snapshot(
        active_filter_mode="MACRO", instruments=[Row(instrument_id="NZD_JPY")]
    )
    row = attach_collection_status(
        snapshot,
        _Runtime({"collection_instruments": {"NZD_JPY": detail}}),
        now=NOW,
        stale_after_seconds=STALE_AFTER_SECONDS,
    ).instruments[0]
    # Expired recovery is suppressed...
    assert row.collection_health == "UNKNOWN"
    assert not any(
        reason.startswith("RECOVERY_") for reason in row.attention_reasons
    )
    # ...but the genuine signal-evaluation hole remains a current failure.
    assert row.signal_readiness == "STALE"
    assert any(
        reason == "SIGNAL_MISSING_SIGNAL_CANDLE"
        for reason in row.attention_reasons
    )
    assert row.data_status == "STALE"


@pytest.mark.parametrize("instrument", ["SHARE_AAPL", "SHARE_MSFT"])
def test_expired_recovery_does_not_change_share_readiness(instrument):
    """AAPL and MSFT share dimensions are untouched by recovery-telemetry expiry."""
    expired = (NOW - timedelta(days=1)).isoformat()
    detail = {
        "collection": _collection("COLLECTING", updated_at=expired),
        "evaluation_freshness": "CURRENT",
        "evaluation_modes": {
            "MICRO": {"evaluation_freshness": "CURRENT", "failure_reason": None},
            "MACRO": {"evaluation_freshness": "CURRENT", "failure_reason": None},
        },
        "evaluation_candidates": [],
    }
    snapshot = Snapshot(
        active_filter_mode="MACRO", instruments=[Row(instrument_id=instrument)]
    )
    row = attach_collection_status(
        snapshot,
        _Runtime({"collection_instruments": {instrument: detail}}),
        now=NOW,
        stale_after_seconds=STALE_AFTER_SECONDS,
    ).instruments[0]
    assert row.collection_health == "UNKNOWN"
    assert row.feed_health == "HEALTHY"
    assert row.filter_readiness == "CURRENT"
    assert row.signal_readiness == "CURRENT"
    assert row.attention_reasons == []


def test_counter_require_attention_excludes_expired_recovery_rows():
    expired = (NOW - timedelta(days=1)).isoformat()
    details = {
        instrument: {
            "collection": _collection(
                "BLOCKED",
                updated_at=expired,
                failure_reason="RECOVERY_HEARTBEAT_STALE",
            ),
            "evaluation_freshness": "CURRENT",
        }
        for instrument in ("EUR_USD", "GBP_USD", "AUD_USD", "NZD_JPY")
    }
    snapshot = Snapshot(
        active_filter_mode="MICRO",
        instruments=[Row(instrument_id=i) for i in details],
    )
    out = attach_collection_status(
        snapshot,
        _Runtime({"collection_instruments": details}),
        now=NOW,
        stale_after_seconds=STALE_AFTER_SECONDS,
    )
    # None of the healthy FX instruments becomes Require Attention purely
    # because of their expired recovery-status row.
    for row in out.instruments:
        assert row.attention_reasons == [], row.instrument_id
        assert row.collection_health == "UNKNOWN"
        assert row.feed_health == "HEALTHY"
