from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from backend.app.domain import Bar, Direction, Timeframe
from backend.app.engine.indicators import (
    InsufficientHistoryError,
    simple_moving_average,
    wilder_atr,
)
from backend.app.engine.models import InstrumentMetadata
from backend.app.engine.position_sizing import calculate_position_size
from backend.app.engine.spect8_signal import evaluate_spect8_signal
from backend.app.engine.strategy import Spect8StrategyEvaluator
from backend.app.synthetic_inputs import SyntheticCaseInputLoader

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "golden"
MANIFEST = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
CASES = MANIFEST["cases"]
ABS_TOLERANCE = Decimal("0.0000000001")


def expected(case: dict[str, Any]) -> dict[str, Any]:
    return json.loads(
        (GOLDEN / case["path"] / "expected.json").read_text(encoding="utf-8")
    )


def assert_number(actual: Decimal | None, wanted: float | None) -> None:
    if wanted is None:
        assert actual is None
        return
    assert actual is not None
    assert abs(actual - Decimal(str(wanted))) <= ABS_TOLERANCE


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _completed_signal(request: Any) -> list[Bar]:
    return [
        bar
        for bar in request.signal_bars
        if bar.timeframe is request.timeframe
        and bar.instrument_id == request.instrument.instrument_id
        and bar.provider == request.instrument.provider
        and bar.is_complete
        and bar.close_time < request.evaluation_time
    ]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_production_engine_matches_every_golden_case(
    case: dict[str, Any],
) -> None:
    request = SyntheticCaseInputLoader(ROOT).load(case["id"])
    actual = Spect8StrategyEvaluator().evaluate(request)
    wanted = expected(case)

    assert actual.case_id == wanted["case_id"]
    assert actual.strategy_id == wanted["strategy_id"]
    assert actual.instrument_id == wanted["instrument_id"]
    assert actual.timeframe.value == wanted["timeframe"]
    assert _iso(actual.evaluation_time) == wanted["evaluation_time"]
    assert actual.data_status == wanted["data_status"]
    assert list(actual.issues) == wanted["issues"]
    assert actual.bars.signal_completed_count == wanted["bars"][
        "signal_completed_count"
    ]
    assert actual.bars.daily_completed_count == wanted["bars"][
        "daily_completed_count"
    ]
    assert actual.bars.excluded_incomplete_count == wanted["bars"][
        "excluded_incomplete_count"
    ]
    assert _iso(actual.bars.signal_bar_close_time) == wanted["bars"][
        "signal_bar_close_time"
    ]
    assert _iso(actual.bars.daily_endpoint_close_time) == wanted["bars"][
        "daily_endpoint_close_time"
    ]

    if wanted["data_status"] == "UNAVAILABLE":
        assert actual.reason_codes == ("DATA_UNAVAILABLE", *wanted["issues"])
        assert actual.classification is None
        assert actual.indicators is None
        assert actual.filters is None
        assert actual.signals is None
        assert actual.candidates == ()
        return

    classification = actual.classification
    indicators = actual.indicators
    filters = actual.filters
    signals = actual.signals
    assert classification is not None
    assert indicators is not None
    assert filters is not None
    assert signals is not None

    wanted_classification = wanted["classification"]
    for field in (
        "buy_filter_matched",
        "sell_filter_matched",
        "buy_sma_rejection",
        "sell_sma_rejection",
        "buy_structural_pivot",
        "sell_structural_pivot",
        "technical_buy_signal",
        "technical_sell_signal",
        "confirmed_buy",
        "confirmed_sell",
        "dashboard_state",
    ):
        assert getattr(classification, field) == wanted_classification[field]

    for field in (
        "sma10",
        "sma20",
        "atr_d1_wilder_5",
        "activation_buffer",
        "stop_atr_distance",
        "point_adjustment",
        "daily_raw_low",
        "daily_raw_high",
        "daily_buy_level",
        "daily_sell_level",
        "recent_low_21",
        "recent_high_21",
    ):
        assert_number(getattr(indicators, field), wanted["indicators"][field])

    completed = _completed_signal(request)
    newest_first = list(reversed(completed))
    for side, result in (("buy", signals.buy), ("sell", signals.sell)):
        wanted_pivot = wanted["pivots"][side]
        assert _iso(result.pivot.open_time) == wanted_pivot["open_time"]
        assert_number(result.pivot.price, wanted_pivot["pivot_value"])
        assert_number(
            result.pivot.structural_window_extreme,
            wanted_pivot["structural_window_extreme"],
        )
        assert result.pivot.structural_passed is wanted_pivot["passed"]
        expected_shift = next(
            index
            for index, bar in enumerate(newest_first, start=1)
            if _iso(bar.open_time) == wanted_pivot["open_time"]
        )
        assert result.pivot.shift == expected_shift

    for side, result in (
        ("buy", actual.buy_candidate),
        ("sell", actual.sell_candidate),
    ):
        wanted_candidate = wanted["candidates"][side]
        if wanted_candidate is None:
            assert result is None
            continue
        assert result is not None
        assert result.direction.value == wanted_candidate["direction"]
        for actual_field, wanted_field in (
            ("entry_reference", "entry_reference"),
            ("raw_strategy_stop", "raw_strategy_stop"),
            ("provider_adjusted_stop", "provider_adjusted_stop"),
            ("risk_distance", "risk_distance"),
            ("target_3r", "target_3r"),
        ):
            assert_number(
                getattr(result, actual_field),
                wanted_candidate[wanted_field],
            )
        sizing = result.position_size
        assert_number(
            sizing.target_risk_usd,
            wanted_candidate["target_risk_usd"],
        )
        assert_number(
            sizing.monetary_loss_per_one_contract,
            wanted_candidate["monetary_loss_per_one_contract"],
        )
        assert_number(sizing.raw_size, wanted_candidate["raw_size"])
        assert_number(sizing.display_size, wanted_candidate["display_size"])
        assert sizing.contract_status == wanted_candidate["contract_status"]
        if sizing.display_size is not None:
            assert sizing.monetary_loss_per_one_contract is not None
            assert (
                sizing.display_size * sizing.monetary_loss_per_one_contract
                <= Decimal("100")
            )

    expected_codes = [
        "DATA_READY",
        (
            "BUY_FILTER_MATCHED"
            if wanted_classification["buy_filter_matched"]
            else "BUY_FILTER_NOT_MATCHED"
        ),
        (
            "SELL_FILTER_MATCHED"
            if wanted_classification["sell_filter_matched"]
            else "SELL_FILTER_NOT_MATCHED"
        ),
        (
            "BUY_SMA_REJECTION_MATCHED"
            if wanted_classification["buy_sma_rejection"]
            else "BUY_SMA_REJECTION_NOT_MATCHED"
        ),
        (
            "BUY_STRUCTURAL_PIVOT_MATCHED"
            if wanted_classification["buy_structural_pivot"]
            else "BUY_STRUCTURAL_PIVOT_NOT_MATCHED"
        ),
        (
            "TECHNICAL_BUY_SIGNAL_MATCHED"
            if wanted_classification["technical_buy_signal"]
            else "TECHNICAL_BUY_SIGNAL_NOT_MATCHED"
        ),
        (
            "SELL_SMA_REJECTION_MATCHED"
            if wanted_classification["sell_sma_rejection"]
            else "SELL_SMA_REJECTION_NOT_MATCHED"
        ),
        (
            "SELL_STRUCTURAL_PIVOT_MATCHED"
            if wanted_classification["sell_structural_pivot"]
            else "SELL_STRUCTURAL_PIVOT_NOT_MATCHED"
        ),
        (
            "TECHNICAL_SELL_SIGNAL_MATCHED"
            if wanted_classification["technical_sell_signal"]
            else "TECHNICAL_SELL_SIGNAL_NOT_MATCHED"
        ),
        (
            "CONFIRMED_BUY"
            if wanted_classification["confirmed_buy"]
            else "BUY_NOT_CONFIRMED"
        ),
        (
            "CONFIRMED_SELL"
            if wanted_classification["confirmed_sell"]
            else "SELL_NOT_CONFIRMED"
        ),
    ]
    for side in ("buy", "sell"):
        candidate = wanted["candidates"][side]
        if candidate is not None:
            expected_codes.extend(
                [
                    "LEVELS_VALID",
                    (
                        "PROVIDER_STOP_ADJUSTED"
                        if candidate["provider_adjusted_stop"]
                        != candidate["raw_strategy_stop"]
                        else "RAW_STRATEGY_STOP_VALID"
                    ),
                    (
                        "CONTRACT_SIZE_VALID"
                        if candidate["contract_status"] == "VALID"
                        else candidate["contract_status"]
                    ),
                ]
            )
    assert actual.reason_codes == tuple(expected_codes)


def test_developing_signal_bar_is_excluded_even_if_values_are_extreme() -> None:
    loader = SyntheticCaseInputLoader(ROOT)
    request = loader.load("confirmed_buy_h1_05")
    baseline = Spect8StrategyEvaluator().evaluate(request)
    mutated = tuple(
        replace(
            bar,
            high=Decimal("999999"),
            low=Decimal("-999999"),
            close=Decimal("-500000"),
        )
        if not bar.is_complete
        else bar
        for bar in request.signal_bars
    )
    changed = Spect8StrategyEvaluator().evaluate(
        replace(request, signal_bars=mutated)
    )
    assert changed.classification == baseline.classification
    assert changed.indicators == baseline.indicators
    assert changed.buy_candidate == baseline.buy_candidate
    assert changed.bars.excluded_incomplete_count == 1


def test_non_selected_timeframe_cannot_change_h1_result() -> None:
    loader = SyntheticCaseInputLoader(ROOT)
    request = loader.load("confirmed_buy_h1_03")
    baseline = Spect8StrategyEvaluator().evaluate(request)
    changed_bars = tuple(
        replace(
            bar,
            high=bar.high + Decimal("100000"),
            low=bar.low - Decimal("100000"),
            close=bar.close + Decimal("50000"),
        )
        if bar.timeframe is Timeframe.H4
        else bar
        for bar in request.signal_bars
    )
    changed = Spect8StrategyEvaluator().evaluate(
        replace(request, signal_bars=changed_bars)
    )
    assert changed.classification == baseline.classification
    assert changed.indicators == baseline.indicators
    assert changed.candidates == baseline.candidates


def test_filter_is_non_consuming_across_repeated_evaluations() -> None:
    request = SyntheticCaseInputLoader(ROOT).load("confirmed_buy_h1_01")
    evaluator = Spect8StrategyEvaluator()
    first = evaluator.evaluate(request)
    second = evaluator.evaluate(request)
    assert first == second
    assert first.classification is not None
    assert first.classification.buy_filter_matched is True
    assert first.classification.confirmed_buy is True


def test_out_of_order_stream_is_quarantined() -> None:
    request = SyntheticCaseInputLoader(ROOT).load("confirmed_buy_h1_01")
    bars = list(request.signal_bars)
    bars[0], bars[1] = bars[1], bars[0]
    result = Spect8StrategyEvaluator().evaluate(
        replace(request, signal_bars=tuple(bars))
    )
    assert result.data_status == "UNAVAILABLE"
    assert "OUT_OF_ORDER_CANDLE" in result.issues
    assert result.classification is None


@pytest.mark.parametrize(
    ("case_id", "issues"),
    [
        ("missing_signal_candle", ("MISSING_SIGNAL_CANDLE",)),
        ("missing_daily_candle", ("MISSING_DAILY_CANDLE",)),
        (
            "duplicate_signal_candle",
            ("DUPLICATE_CANDLE", "MISSING_SIGNAL_CANDLE"),
        ),
        (
            "duplicate_daily_candle",
            ("DUPLICATE_CANDLE", "MISSING_DAILY_CANDLE"),
        ),
    ],
)
def test_unsafe_candle_stream_is_quarantined(
    case_id: str,
    issues: tuple[str, ...],
) -> None:
    request = SyntheticCaseInputLoader(ROOT).load(case_id)
    result = Spect8StrategyEvaluator().evaluate(request)
    assert result.data_status == "UNAVAILABLE"
    assert result.issues == issues
    assert result.classification is None
    assert result.candidates == ()


def _bar(index: int, *, complete: bool = True) -> Bar:
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
    value = Decimal(index + 1)
    return Bar(
        instrument_id="TEST",
        timeframe=Timeframe.D1,
        open_time=opened,
        close_time=opened + timedelta(days=1),
        open=value,
        high=value + Decimal("1"),
        low=value - Decimal("1"),
        close=value,
        provider="TEST",
        is_complete=complete,
        synthetic=True,
    )


def test_indicators_require_completed_sufficient_history() -> None:
    bars = tuple(_bar(index) for index in range(6))
    assert simple_moving_average(bars, 5) == Decimal("4")
    assert wilder_atr(bars, 5) == Decimal("2")
    with pytest.raises(InsufficientHistoryError):
        simple_moving_average(bars[:4], 5)
    with pytest.raises(InsufficientHistoryError):
        wilder_atr(bars[:5], 5)
    with pytest.raises(ValueError, match="incomplete"):
        simple_moving_average((*bars[:-1], _bar(5, complete=False)), 5)
    with pytest.raises(ValueError, match="incomplete"):
        wilder_atr((*bars[:-1], _bar(5, complete=False)), 5)


def test_pivot_ties_select_the_most_recent_completed_bar() -> None:
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = tuple(
        Bar(
            instrument_id="TEST",
            timeframe=Timeframe.H1,
            open_time=opened + timedelta(hours=index),
            close_time=opened + timedelta(hours=index + 1),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("100"),
            provider="TEST",
            is_complete=True,
        )
        for index in range(30)
    )
    result = evaluate_spect8_signal(
        bars,
        sma10=Decimal("100"),
        sma20=Decimal("100"),
    )
    assert result.buy.pivot.shift == 1
    assert result.sell.pivot.shift == 1
    assert result.buy.pivot.open_time == bars[-1].open_time
    assert result.sell.pivot.open_time == bars[-1].open_time
    assert result.buy.pivot.structural_passed is True
    assert result.sell.pivot.structural_passed is True


def _instrument(**overrides: Decimal | None) -> InstrumentMetadata:
    values: dict[str, Any] = {
        "strategy_id": "SPECT8_MICRO_DAILY_V1_0",
        "instrument_id": "TEST",
        "display_name": "Test",
        "provider": "TEST",
        "session_timezone": "UTC",
        "candle_boundary_convention": "UTC",
        "point_size": Decimal("0.01"),
        "price_precision": 2,
        "minimum_stop_distance_points": Decimal("0"),
        "tick_size": Decimal("0.01"),
        "tick_value_usd": Decimal("1"),
        "conversion_rate_to_usd": Decimal("1"),
        "contract_min": Decimal("0.01"),
        "contract_max": Decimal("100"),
        "contract_step": Decimal("0.01"),
    }
    values.update(overrides)
    return InstrumentMetadata(**values)


def test_position_size_rounds_down_and_never_exceeds_100_usd() -> None:
    result = calculate_position_size(_instrument(), Decimal("14.2"))
    assert result.raw_size is not None
    assert result.display_size == Decimal("0.07")
    assert result.monetary_loss_per_one_contract == Decimal("1420")
    assert result.display_size * result.monetary_loss_per_one_contract <= Decimal(
        "100"
    )


def test_position_size_reports_minimum_and_metadata_failures() -> None:
    below = calculate_position_size(
        _instrument(contract_min=Decimal("0.1")),
        Decimal("20"),
    )
    assert below.contract_status == "BELOW_PROVIDER_MINIMUM"
    assert below.display_size is None
    missing = calculate_position_size(
        _instrument(tick_size=None),
        Decimal("20"),
    )
    assert missing.contract_status == "METADATA_UNAVAILABLE"
    assert missing.display_size is None


def test_position_size_applies_currency_conversion() -> None:
    result = calculate_position_size(
        _instrument(conversion_rate_to_usd=Decimal("1.25")),
        Decimal("8"),
    )
    assert result.monetary_loss_per_one_contract == Decimal("1000")
    assert result.raw_size == Decimal("0.1")
    assert result.display_size == Decimal("0.1")
    assert result.contract_status == "VALID"


def test_production_runtime_has_no_oracle_dependency_or_access() -> None:
    runtime_paths = [
        ROOT / "backend" / "app" / "main.py",
        ROOT / "backend" / "app" / "service.py",
        ROOT / "backend" / "app" / "synthetic_inputs.py",
        *(ROOT / "backend" / "app" / "engine").glob("*.py"),
    ]
    forbidden = (
        "golden.reference",
        "reference.calculator",
        "golden_adapter",
        "expected.json",
        "calculation_ledger",
    )
    for path in runtime_paths:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path} contains forbidden {token}"
        for excluded_feature in (
            "reverse_filter",
            "risk_multiplier",
            "filter_consumed",
            "weekly_filter",
            "macro_filter",
        ):
            assert excluded_feature not in source
        if "backend/app/engine" in path.as_posix():
            assert "fastapi" not in source.lower()
            assert "sqlite" not in source.lower()


def test_fastapi_routes_and_frontend_contain_no_strategy_formulas() -> None:
    route_source = (ROOT / "backend" / "app" / "main.py").read_text(
        encoding="utf-8"
    )
    formula_tokens = (
        "simple_moving_average",
        "wilder_atr",
        "activation_buffer",
        "STOP_ATR_MULTIPLIER",
        "TARGET_R_MULTIPLE",
        "calculate_position_size",
    )
    assert all(token not in route_source for token in formula_tokens)

    frontend_sources = [
        *ROOT.glob("frontend/app/**/*.ts"),
        *ROOT.glob("frontend/app/**/*.tsx"),
        *ROOT.glob("frontend/components/**/*.ts"),
        *ROOT.glob("frontend/components/**/*.tsx"),
        *ROOT.glob("frontend/lib/**/*.ts"),
        *ROOT.glob("frontend/lib/**/*.tsx"),
    ]
    for path in frontend_sources:
        source = path.read_text(encoding="utf-8")
        assert "wilder_atr" not in source.lower()
        assert "activation_buffer" not in source.lower()
        assert "calculate_position_size" not in source.lower()
