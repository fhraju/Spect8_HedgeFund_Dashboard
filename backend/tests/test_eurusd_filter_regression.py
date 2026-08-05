from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from backend.app.dashboard_api import DashboardEnvelope, dashboard_snapshot
from backend.app.domain import Bar, Timeframe, primitive
from backend.app.engine.models import StrategyRequest
from backend.app.engine.strategy import (
    SPECIFICATION_ID,
    STRATEGY_ID,
    Spect8StrategyEvaluator,
)
from backend.app.filter_audit_rebuild import FilterAuditRebuildService
from backend.app.market_data.twelve_data_provider import TwelveDataProvider
from backend.app.market_data.forex_profile import is_valid_market_h4
from backend.app.market_data.session_boundaries import NEW_YORK
from backend.app.repository import SQLiteProjectionRepository
from backend.app.service import WalkingSkeletonService


FIXTURE = Path(__file__).parent / "fixtures" / "eurusd_filter_20260805.json"


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _bar(row: list[str], timeframe: Timeframe) -> Bar:
    open_time, close_time, open_, high, low, close = row
    return Bar(
        instrument_id="EUR/USD",
        timeframe=timeframe,
        open_time=_utc(open_time),
        close_time=_utc(close_time),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        provider="TWELVE_DATA",
        is_complete=True,
        session_timezone=("America/New_York" if timeframe is Timeframe.D1 else "UTC"),
        raw_provider_symbol="EUR/USD",
        raw_open_time=open_time,
        raw_close_time=close_time,
        raw_open=open_,
        raw_high=high,
        raw_low=low,
        raw_close=close,
        synthetic=False,
    )


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_persisted_eurusd_filter_difference_is_a_weekend_data_defect_and_survives_projection(
    tmp_path: Path,
) -> None:
    source = _fixture()
    assert source["authority"] == SPECIFICATION_ID
    assert source["provider"] == "TWELVE_DATA"
    assert source["instrument"] == "EUR/USD"

    processing_time = _utc(source["processing_time"])
    daily = tuple(_bar(row, Timeframe.D1) for row in source["daily_bars"])
    signal = {
        Timeframe(timeframe): tuple(
            _bar(row, Timeframe(timeframe)) for row in evaluation["bars"]
        )
        for timeframe, evaluation in source["evaluations"].items()
    }
    expected = {
        Timeframe.H1: {
            "signal_close": _utc("2026-08-05T06:00:00Z"),
            "window_start": _utc("2026-08-04T10:00:00Z"),
            "recent_low": Decimal("1.15040"),
            "recent_low_close": _utc("2026-08-04T10:00:00Z"),
            "recent_high": Decimal("1.15409"),
            "recent_high_close": _utc("2026-08-05T03:00:00Z"),
            "buy": True,
            "sell": False,
            "filter_classification": "BUY",
            "dashboard_state": "FILTERED_BUY",
        },
        Timeframe.H4: {
            "signal_close": _utc("2026-08-05T05:00:00Z"),
            "window_start": _utc("2026-08-01T21:00:00Z"),
            "recent_low": Decimal("1.15018"),
            "recent_low_close": _utc("2026-08-03T21:00:00Z"),
            "recent_high": Decimal("1.15593"),
            "recent_high_close": _utc("2026-08-03T01:00:00Z"),
            "buy": True,
            "sell": True,
            "filter_classification": "BUY + SELL",
            "dashboard_state": "FILTERED_BOTH",
        },
    }

    repository = SQLiteProjectionRepository(tmp_path / "eurusd-filter.sqlite3")
    repository.initialize()
    repository.persist_canonical_bars(
        (*signal[Timeframe.H1], *signal[Timeframe.H4], *daily)
    )
    instrument = TwelveDataProvider(
        "offline-regression-fixture"
    ).discover_instruments()[0]
    service = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository)

    production = {}
    for timeframe in (Timeframe.H1, Timeframe.H4):
        wanted = expected[timeframe]
        signal_close = wanted["signal_close"]
        request = StrategyRequest(
            case_id=(
                f"twelve_data:EUR/USD:{timeframe.value}:"
                f"{signal_close.isoformat().replace('+00:00', 'Z')}"
            ),
            strategy_id=STRATEGY_ID,
            timeframe=timeframe,
            evaluation_time=processing_time,
            signal_bars=signal[timeframe],
            daily_bars=daily,
            instrument=instrument.to_strategy_metadata(),
        )
        evaluated = service.evaluate_request(request)
        result = evaluated.evaluation
        assert result.classification is not None
        assert result.indicators is not None
        assert result.filter_audit is not None

        selected = signal[timeframe][-21:]
        recent_low_bar = min(selected, key=lambda bar: bar.low)
        recent_high_bar = max(selected, key=lambda bar: bar.high)
        assert len(selected) == 21
        assert selected[0].close_time == wanted["window_start"]
        assert selected[-1].close_time == signal_close
        assert recent_low_bar.low == wanted["recent_low"]
        assert recent_low_bar.close_time == wanted["recent_low_close"]
        assert recent_high_bar.high == wanted["recent_high"]
        assert recent_high_bar.close_time == wanted["recent_high_close"]
        assert all(bar.timeframe is timeframe for bar in selected)
        assert all(
            bar.is_complete and bar.close_time < processing_time for bar in selected
        )
        assert all(bar.close_time <= signal_close for bar in daily)

        if timeframe is Timeframe.H4:
            assert all(
                bar.close_time - bar.open_time == timedelta(hours=4) for bar in selected
            )
            assert selected[-1].open_time == _utc("2026-08-05T01:00:00Z")
            assert selected[-1].close_time == _utc("2026-08-05T05:00:00Z")

        assert [bar.close_time for bar in daily[-2:]] == [
            _utc("2026-08-03T21:00:00Z"),
            _utc("2026-08-04T21:00:00Z"),
        ]
        assert result.bars.daily_endpoint_close_time == _utc("2026-08-04T21:00:00Z")
        assert result.indicators.daily_raw_low == Decimal("1.15018")
        assert result.indicators.daily_raw_high == Decimal("1.15593")
        assert result.indicators.atr_d1_wilder_5 == Decimal("0.0062243008")
        assert result.indicators.activation_buffer == Decimal("0.000311215040")
        assert result.indicators.daily_buy_level == Decimal("1.150491215040")
        assert result.indicators.daily_sell_level == Decimal("1.155618784960")
        assert result.indicators.recent_low_21 == wanted["recent_low"]
        assert result.indicators.recent_high_21 == wanted["recent_high"]
        assert result.classification.buy_filter_matched is wanted["buy"]
        assert result.classification.sell_filter_matched is wanted["sell"]
        assert result.classification.dashboard_state == wanted["dashboard_state"]
        audit = result.filter_audit
        assert audit.strategy_version == "SPECT8_MICRO_DAILY_V1_0_3"
        assert audit.timeframe is timeframe
        assert audit.evaluation_bar_confirmed_closed is True
        assert audit.completed_bar_count == 21
        assert audit.available_completed_bar_count == 30
        assert audit.lookback_start_time == wanted["window_start"]
        assert audit.lookback_end_time == signal_close
        assert audit.recent_low == wanted["recent_low"]
        assert audit.recent_low_bar_open_time == recent_low_bar.open_time
        assert audit.recent_low_bar_close_time == wanted["recent_low_close"]
        assert audit.recent_high == wanted["recent_high"]
        assert audit.recent_high_bar_open_time == recent_high_bar.open_time
        assert audit.recent_high_bar_close_time == wanted["recent_high_close"]
        assert audit.daily_session.session_identifier == "2026-08-04"
        assert audit.daily_session.session_close_time.astimezone(NEW_YORK).hour == 17
        assert (
            audit.daily_session.session_close_time.astimezone(NEW_YORK).tzname()
            == "EDT"
        )
        assert len(audit.daily_reference_sessions) == 2
        assert len(audit.atr_sessions) == 10
        assert all(
            session.session_close_time <= audit.d1_context_eligibility_time
            for session in audit.atr_sessions
        )
        assert audit.buy_comparison.recent_low == wanted["recent_low"]
        assert audit.buy_comparison.operator == "<="
        assert audit.buy_comparison.buy_threshold == Decimal("1.150491215040")
        assert audit.buy_comparison.matched is wanted["buy"]
        assert audit.sell_comparison.recent_high == wanted["recent_high"]
        assert audit.sell_comparison.operator == ">="
        assert audit.sell_comparison.sell_threshold == Decimal("1.155618784960")
        assert audit.sell_comparison.matched is wanted["sell"]
        assert audit.final_classification == wanted["filter_classification"]

        assert repository.persist_projection(
            evaluated.status,
            service.events_for_projection(evaluated),
        )
        production[timeframe.value] = evaluated.status

    sqlite_statuses = {value["timeframe"]: value for value in repository.statuses()}
    snapshot = dashboard_snapshot(repository, instrument, processing_time)
    response = DashboardEnvelope(
        synthetic=False,
        source="TWELVE_DATA_PROVIDER",
        notice="READ-ONLY Twelve Data EUR/USD market data.",
        data=snapshot,
    )
    typed_round_trip = DashboardEnvelope.model_validate_json(response.model_dump_json())
    api_statuses = {
        value.timeframe: value for value in typed_round_trip.data.evaluations
    }

    for timeframe in (Timeframe.H1, Timeframe.H4):
        name = timeframe.value
        wanted = expected[timeframe]
        projected = production[name]
        persisted = sqlite_statuses[name]
        typed = api_statuses[name]
        assert projected.filter_result.buy_matched is wanted["buy"]
        assert persisted["filter_result"]["buy_matched"] is wanted["buy"]
        assert typed.filter_result.buy_matched is wanted["buy"]
        assert projected.filter_result.sell_matched is wanted["sell"]
        assert persisted["filter_result"]["sell_matched"] is wanted["sell"]
        assert typed.filter_result.sell_matched is wanted["sell"]
        assert persisted["market_values"]["atr_d1_wilder_5"] == 0.0062243008
        assert typed.market_values is not None
        assert typed.market_values.atr_d1_wilder_5 == 0.0062243008
        assert typed.market_values.daily_buy_level == 1.15049121504
        assert typed.market_values.daily_sell_level == 1.15561878496
        assert projected.filter_audit is not None
        assert typed.filter_audit is not None
        assert persisted["filter_audit"] == typed.filter_audit.model_dump(mode="json")
        assert typed.filter_audit.recent_low == str(wanted["recent_low"])
        assert typed.filter_audit.recent_high == str(wanted["recent_high"])
        assert typed.filter_audit.atr_value == "0.0062243008"
        assert typed.filter_audit.buffer_value == "0.000311215040"
        assert typed.filter_audit.buy_threshold == "1.150491215040"
        assert typed.filter_audit.sell_threshold == "1.155618784960"
        assert (
            typed.filter_audit.final_classification == wanted["filter_classification"]
        )

    assert api_statuses["H1"].filter_result.sell_matched is False
    assert api_statuses["H4"].filter_result.sell_matched is True
    assert (
        api_statuses["H1"].market_values.recent_high_21
        < api_statuses["H1"].market_values.daily_sell_level
        < api_statuses["H4"].market_values.recent_high_21
    )
    contaminated_h4 = signal[Timeframe.H4][-21:]
    assert contaminated_h4[0].open_time == _utc("2026-08-01T17:00:00Z")
    assert any(not is_valid_market_h4(bar) for bar in contaminated_h4)
    assert is_valid_market_h4(contaminated_h4[-1])


def test_latest_filter_audit_rebuild_is_atomic_deterministic_and_idempotent(
    tmp_path: Path,
) -> None:
    source = _fixture()
    processing_time = _utc(source["processing_time"])
    daily = tuple(_bar(row, Timeframe.D1) for row in source["daily_bars"])
    signal = {
        Timeframe(timeframe): tuple(
            _bar(row, Timeframe(timeframe)) for row in evaluation["bars"]
        )
        for timeframe, evaluation in source["evaluations"].items()
    }
    repository = SQLiteProjectionRepository(tmp_path / "audit-rebuild.sqlite3")
    repository.initialize()
    repository.persist_canonical_bars(
        (*signal[Timeframe.H1], *signal[Timeframe.H4], *daily)
    )
    instrument = (
        TwelveDataProvider("offline-audit-rebuild")
        .discover_instruments()[0]
        .to_strategy_metadata()
    )
    walking = WalkingSkeletonService(Spect8StrategyEvaluator(), None, repository)
    legacy: dict[str, dict[str, Any]] = {}
    legacy_projections = []

    for timeframe in (Timeframe.H1, Timeframe.H4):
        signal_close = signal[timeframe][-1].close_time
        request = StrategyRequest(
            case_id=(
                f"twelve_data:EUR/USD:{timeframe.value}:"
                f"{signal_close.isoformat().replace('+00:00', 'Z')}"
            ),
            strategy_id=STRATEGY_ID,
            timeframe=timeframe,
            evaluation_time=processing_time,
            signal_bars=signal[timeframe],
            daily_bars=daily,
            instrument=instrument,
        )
        evaluated = walking.evaluate_request(request)
        legacy_status = replace(evaluated.status, filter_audit=None)
        legacy[timeframe.value] = primitive(legacy_status)
        events = walking.events_for_projection(evaluated)
        legacy_projections.append((legacy_status, events))
        assert repository.persist_projection(legacy_status, events)

    sources_before = repository.projection_sources(
        STRATEGY_ID, "TWELVE_DATA", "EUR/USD"
    )
    events_before = repository.events()
    rebuild = FilterAuditRebuildService(repository)
    dry_run = rebuild.rebuild_latest(instrument=instrument, dry_run=True)
    assert dry_run.changed is True
    assert dry_run.statuses_rebuilt == 0
    assert [item.completed_bar_count for item in dry_run.items] == [21, 21]

    applied = rebuild.rebuild_latest(instrument=instrument, dry_run=False)
    assert applied.changed is True
    assert applied.statuses_rebuilt == 2
    persisted = {value["timeframe"]: value for value in repository.statuses()}
    for timeframe in ("H1", "H4"):
        audit = persisted[timeframe]["filter_audit"]
        assert audit is not None
        assert audit["completed_bar_count"] == 21
        assert (
            audit["lookback_end_time"] == persisted[timeframe]["signal_bar_close_time"]
        )
        without_audit = dict(persisted[timeframe])
        without_audit.pop("filter_audit")
        legacy_without_audit = dict(legacy[timeframe])
        legacy_without_audit.pop("filter_audit")
        assert without_audit == legacy_without_audit

    assert (
        repository.projection_sources(STRATEGY_ID, "TWELVE_DATA", "EUR/USD")
        == sources_before
    )
    assert repository.events() == events_before
    second = rebuild.rebuild_latest(instrument=instrument, dry_run=False)
    assert second.changed is False
    assert second.statuses_rebuilt == 0

    missing = SQLiteProjectionRepository(tmp_path / "missing-history.sqlite3")
    missing.initialize()
    missing.persist_canonical_bars(
        (
            *signal[Timeframe.H1][-10:],
            *signal[Timeframe.H4][-10:],
            *daily,
        )
    )
    for status, events in legacy_projections:
        assert missing.persist_projection(status, events)
    with pytest.raises(
        ValueError,
        match=r"H1: requires 30 completed signal bars .* found 10",
    ):
        FilterAuditRebuildService(missing).rebuild_latest(
            instrument=instrument,
            dry_run=True,
        )
