from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from backend.app.domain import Bar, Timeframe
from backend.app.engine.current_daily_filter import (
    DailyFilterUnavailableError,
    build_daily_filter_snapshot,
)
from backend.app.engine.indicators import wilder_atr
from backend.app.engine.models import CURRENT_D1_FILTER_V2
from backend.app.engine.models import InstrumentMetadata, StrategyRequest
from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.market_data.profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from backend.app.repository import SQLiteProjectionRepository
from backend.app.dashboard_api import dashboard_snapshot
from backend.app.market_data.twelve_data_provider import TwelveDataProvider

UTC = timezone.utc
AS_OF = datetime(2026, 8, 5, 10, tzinfo=UTC)
SESSION_OPEN = datetime(2026, 8, 4, 21, tzinfo=UTC)
FIXTURE = (
    Path(__file__).parent / "fixtures" / "current_daily_filter_v2" / "reference.json"
)


def bar(
    timeframe: Timeframe,
    open_time: datetime,
    open_value: str,
    high: str,
    low: str,
    close: str,
    *,
    complete: bool = True,
) -> Bar:
    step = timedelta(
        hours=1 if timeframe is Timeframe.H1 else 4 if timeframe is Timeframe.H4 else 24
    )
    close_time = open_time + step
    source_id = f"TEST:EUR/USD:{timeframe.value}:{close_time.isoformat()}"
    return Bar(
        instrument_id="EUR/USD",
        timeframe=timeframe,
        open_time=open_time,
        close_time=close_time,
        open=Decimal(open_value),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        provider="TEST",
        is_complete=complete,
        synthetic=False,
        construction_profile_version=PROFILE_ID,
        provider_adapter_version="test-v1",
        source_timeframe=Timeframe.H1,
        source_candle_ids=(source_id,),
        created_at=close_time,
        session_identifier=(
            close_time.date().isoformat() if timeframe is Timeframe.D1 else None
        ),
    )


def daily_history() -> tuple[Bar, ...]:
    first = SESSION_OPEN - timedelta(days=6)
    return tuple(
        bar(
            Timeframe.D1,
            first + timedelta(days=index),
            "1.1000",
            "1.1100" if index < 5 else "1.1080",
            "1.0900" if index < 5 else "1.0920",
            "1.1000",
        )
        for index in range(6)
    )


def current_h1(*, low: str = "1.0910", high: str = "1.1070") -> tuple[Bar, ...]:
    values = []
    for index in range(13):
        values.append(
            bar(
                Timeframe.H1,
                SESSION_OPEN + timedelta(hours=index),
                "1.1000",
                high if index == 7 else "1.1050",
                low if index == 4 else "1.0950",
                "1.1010",
            )
        )
    return tuple(values)


def snapshot(*, low: str = "1.0910", high: str = "1.1070"):
    return build_daily_filter_snapshot(
        provider="TEST",
        instrument="EUR/USD",
        as_of_h1_close=AS_OF,
        h1_bars=current_h1(low=low, high=high),
        completed_d1_bars=daily_history(),
    )


def test_correct_formula_partial_ohlc_and_frozen_completed_d1_atr() -> None:
    result = snapshot()
    expected_atr = wilder_atr(daily_history(), 5)

    assert result.strategy_version == CURRENT_D1_FILTER_V2
    assert result.atr_value == expected_atr
    assert result.buffer_value == expected_atr * Decimal("0.05")
    assert result.buy_threshold == Decimal("1.0920") + result.buffer_value
    assert result.sell_threshold == Decimal("1.1080") - result.buffer_value
    assert result.buy_left_value == Decimal("1.0910")
    assert result.sell_left_value == Decimal("1.1070")
    assert result.current_partial_d1.open == Decimal("1.1000")
    assert result.current_partial_d1.close == Decimal("1.1010")
    assert result.current_partial_d1.h1_count == 13
    assert result.current_partial_d1.last_h1_close_time_utc == AS_OF


def test_inclusive_equality_and_both_match() -> None:
    baseline = snapshot()
    result = snapshot(
        low=str(baseline.buy_threshold),
        high=str(baseline.sell_threshold),
    )
    assert result.buy_matched is True
    assert result.sell_matched is True
    assert result.final_classification == "BUY_AND_SELL"


def test_forming_h1_is_excluded_and_does_not_change_checksum() -> None:
    complete = current_h1()
    forming = replace(
        complete[-1],
        open_time=AS_OF,
        close_time=AS_OF + timedelta(hours=1),
        high=Decimal("9"),
        low=Decimal("0"),
        is_complete=False,
    )
    first = build_daily_filter_snapshot(
        provider="TEST",
        instrument="EUR/USD",
        as_of_h1_close=AS_OF,
        h1_bars=complete,
        completed_d1_bars=daily_history(),
    )
    second = build_daily_filter_snapshot(
        provider="TEST",
        instrument="EUR/USD",
        as_of_h1_close=AS_OF,
        h1_bars=(*complete, forming),
        completed_d1_bars=daily_history(),
    )
    assert first.snapshot_id == second.snapshot_id
    assert first.current_partial_d1.high == second.current_partial_d1.high


def test_unexpected_h1_gap_is_unavailable_and_no_price_is_invented() -> None:
    missing = current_h1()[:5] + current_h1()[6:]
    with pytest.raises(DailyFilterUnavailableError, match="UNEXPECTED_H1_GAP"):
        build_daily_filter_snapshot(
            provider="TEST",
            instrument="EUR/USD",
            as_of_h1_close=AS_OF,
            h1_bars=missing,
            completed_d1_bars=daily_history(),
        )


def test_synthetic_or_forward_filled_h1_cannot_enter_filter() -> None:
    invalid = tuple(replace(item, synthetic=True) for item in current_h1())
    with pytest.raises(DailyFilterUnavailableError, match="NO_COMPLETED_H1"):
        build_daily_filter_snapshot(
            provider="TEST",
            instrument="EUR/USD",
            as_of_h1_close=AS_OF,
            h1_bars=invalid,
            completed_d1_bars=daily_history(),
        )


def metadata() -> InstrumentMetadata:
    return InstrumentMetadata(
        strategy_id="SPECT8_MICRO_DAILY_V1_0",
        instrument_id="EUR/USD",
        display_name="Euro / US Dollar",
        provider="TEST",
        session_timezone="UTC",
        candle_boundary_convention=PROFILE_ID,
        point_size=Decimal("0.00001"),
        price_precision=5,
        minimum_stop_distance_points=None,
        tick_size=None,
        tick_value_usd=None,
        conversion_rate_to_usd=None,
        contract_min=None,
        contract_max=None,
        contract_step=None,
    )


def signal_history(timeframe: Timeframe) -> tuple[Bar, ...]:
    step = timedelta(hours=1 if timeframe is Timeframe.H1 else 4)
    return tuple(
        bar(
            timeframe,
            AS_OF - step * (30 - index),
            "1.1000",
            "1.1060",
            "1.0940",
            "1.1010",
        )
        for index in range(30)
    )


def test_h1_and_completing_h4_reference_the_same_snapshot() -> None:
    shared = snapshot()
    evaluator = Spect8StrategyEvaluator()
    results = []
    for timeframe in (Timeframe.H1, Timeframe.H4):
        results.append(
            evaluator.evaluate(
                StrategyRequest(
                    case_id=f"v2-{timeframe.value}",
                    strategy_id="SPECT8_MICRO_DAILY_V1_0",
                    timeframe=timeframe,
                    evaluation_time=AS_OF + timedelta(seconds=1),
                    signal_bars=signal_history(timeframe),
                    daily_bars=daily_history(),
                    instrument=metadata(),
                    strategy_version=CURRENT_D1_FILTER_V2,
                    daily_filter_snapshot=shared,
                )
            )
        )
    assert all(result.data_status == "READY" for result in results)
    assert {result.daily_filter_snapshot_id for result in results} == {
        shared.snapshot_id
    }
    assert all(
        result.classification.buy_filter_matched == shared.buy_matched
        for result in results
        if result.classification
    )
    assert all(result.filter_audit is None for result in results)


def test_snapshot_persistence_is_exact_and_idempotent(tmp_path) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "snapshots.sqlite3")
    repository.initialize()
    value = snapshot()
    assert repository.persist_daily_filter_snapshot(value) is True
    assert repository.persist_daily_filter_snapshot(value) is False
    restored = repository.latest_daily_filter_snapshot(
        "TEST", "EUR/USD", CURRENT_D1_FILTER_V2
    )
    assert restored is not None
    assert restored["snapshot_id"] == value.snapshot_id
    assert restored["atr_value"] == str(value.atr_value)
    assert restored["buy_left_value"] == str(value.buy_left_value)
    assert (
        repository.daily_filter_snapshot_count("TEST", "EUR/USD", CURRENT_D1_FILTER_V2)
        == 1
    )


def test_frozen_v2_fixture_checksum_and_full_precision_cases() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == (
        "c68fedcd8adae0061c45c6b0dbbcf9c62caff7d5ab4d87230d688efc53804afd"
    )
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    threshold_buy = Decimal(payload["expected"]["buy_threshold"])
    threshold_sell = Decimal(payload["expected"]["sell_threshold"])
    for case in payload["classification_cases"]:
        buy = Decimal(case["partial_low"]) <= threshold_buy
        sell = Decimal(case["partial_high"]) >= threshold_sell
        classification = (
            "BUY_AND_SELL"
            if buy and sell
            else "BUY"
            if buy
            else "SELL"
            if sell
            else "NONE"
        )
        assert (buy, sell, classification) == (
            case["buy"],
            case["sell"],
            case["classification"],
        )


def test_sqlite_and_typed_dashboard_api_preserve_exact_snapshot_values(
    tmp_path,
) -> None:
    repository = SQLiteProjectionRepository(tmp_path / "api.sqlite3")
    repository.initialize()
    value = replace(snapshot(), provider="TWELVE_DATA")
    repository.persist_daily_filter_snapshot(value)
    instrument = TwelveDataProvider(api_key="offline-api-test").discover_instruments()[
        0
    ]

    api = dashboard_snapshot(repository, instrument, AS_OF)

    assert api.daily_filter is not None
    assert api.daily_filter.snapshot_id == value.snapshot_id
    assert api.daily_filter.atr_value == str(value.atr_value)
    assert api.daily_filter.buy_left_value == str(value.buy_left_value)
    assert api.daily_filter.buy_right_value == str(value.buy_right_value)
    assert api.daily_filter.buy_matched is value.buy_matched


def test_new_york_standard_time_session_uses_22_utc_boundary() -> None:
    standard_open = datetime(2026, 11, 1, 22, tzinfo=UTC)
    standard_close = datetime(2026, 11, 2, 22, tzinfo=UTC)
    h1 = tuple(
        bar(
            Timeframe.H1,
            standard_open + timedelta(hours=index),
            "1.1000",
            "1.1010",
            "1.0990",
            "1.1005",
        )
        for index in range(24)
    )
    d1_closes = (
        datetime(2026, 10, 23, 21, tzinfo=UTC),
        datetime(2026, 10, 26, 21, tzinfo=UTC),
        datetime(2026, 10, 27, 21, tzinfo=UTC),
        datetime(2026, 10, 28, 21, tzinfo=UTC),
        datetime(2026, 10, 29, 21, tzinfo=UTC),
        datetime(2026, 10, 30, 21, tzinfo=UTC),
    )
    d1 = tuple(
        replace(
            bar(
                Timeframe.D1,
                close - timedelta(days=1),
                "1.1000",
                "1.1100",
                "1.0900",
                "1.1000",
            ),
            close_time=close,
        )
        for close in d1_closes
    )
    value = build_daily_filter_snapshot(
        provider="TEST",
        instrument="EUR/USD",
        as_of_h1_close=standard_close,
        h1_bars=h1,
        completed_d1_bars=d1,
    )
    assert value.current_partial_d1.session_open_utc == standard_open
    assert value.current_partial_d1.session_close_utc == standard_close
    assert value.current_partial_d1.h1_count == 24
