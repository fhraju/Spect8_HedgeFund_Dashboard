from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from golden.reference.calculator import evaluate_case

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "golden"
SCHEMAS = GOLDEN / "schemas"
MANIFEST = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
CASES = MANIFEST["cases"]
FORMAT_CHECKER = FormatChecker()


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name), format_checker=FORMAT_CHECKER)


def expected(case: dict[str, Any]) -> dict[str, Any]:
    return json.loads(
        (GOLDEN / case["path"] / "expected.json").read_text(encoding="utf-8")
    )


def csv_candles(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                {
                    "canonical_instrument_id": raw["canonical_instrument_id"],
                    "timeframe": raw["timeframe"],
                    "open_time": raw["open_time"],
                    "close_time": raw["close_time"],
                    "open": float(raw["open"]),
                    "high": float(raw["high"]),
                    "low": float(raw["low"]),
                    "close": float(raw["close"]),
                    "volume": float(raw["volume"]) if raw["volume"] else None,
                    "provider": raw["provider"],
                    "is_complete": raw["is_complete"].lower() == "true",
                }
            )
    return rows


@pytest.mark.parametrize(
    "schema_name",
    [
        "candle.schema.json",
        "instrument-metadata.schema.json",
        "expected-result.schema.json",
        "manifest.schema.json",
    ],
)
def test_schema_is_valid_draft_2020_12(schema_name: str) -> None:
    Draft202012Validator.check_schema(load_schema(schema_name))


def test_manifest_schema_and_identity_contract() -> None:
    validator("manifest.schema.json").validate(MANIFEST)
    ids = [case["id"] for case in CASES]
    paths = [case["path"] for case in CASES]
    assert len(CASES) == 59
    assert len(ids) == len(set(ids))
    assert len(paths) == len(set(paths))
    assert all(case["path"] == f"cases/{case['id']}" for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
def test_every_case_has_required_files(case: dict[str, Any]) -> None:
    directory = GOLDEN / case["path"]
    assert directory.is_dir()
    for name in (
        "signal_bars.csv",
        "daily_bars.csv",
        "instrument.json",
        "expected.json",
        "calculation_ledger.md",
    ):
        path = directory / name
        assert path.is_file(), f"{case['id']} is missing {name}"
        assert path.stat().st_size > 0


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
def test_candles_validate_against_schema_and_ohlc_rules(case: dict[str, Any]) -> None:
    directory = GOLDEN / case["path"]
    candle_validator = validator("candle.schema.json")
    instrument = json.loads(
        (directory / "instrument.json").read_text(encoding="utf-8")
    )
    for filename in ("signal_bars.csv", "daily_bars.csv"):
        candles = csv_candles(directory / filename)
        assert candles
        for bar in candles:
            candle_validator.validate(bar)
            assert bar["canonical_instrument_id"] == instrument["instrument_id"]
            assert bar["provider"] == instrument["provider"]
            assert datetime.fromisoformat(
                bar["open_time"].replace("Z", "+00:00")
            ) < datetime.fromisoformat(bar["close_time"].replace("Z", "+00:00"))
            assert bar["low"] <= min(bar["open"], bar["close"])
            assert bar["high"] >= max(bar["open"], bar["close"])
            assert bar["low"] <= bar["high"]


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
def test_instrument_and_expected_result_schemas(case: dict[str, Any]) -> None:
    directory = GOLDEN / case["path"]
    metadata = json.loads((directory / "instrument.json").read_text(encoding="utf-8"))
    result = expected(case)
    validator("instrument-metadata.schema.json").validate(metadata)
    validator("expected-result.schema.json").validate(result)
    if metadata["contract_min"] is not None:
        assert metadata["contract_min"] <= metadata["contract_max"]
        assert metadata["contract_step"] <= metadata["contract_max"]


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
def test_frozen_expected_result_matches_reference_calculation(
    case: dict[str, Any],
) -> None:
    directory = GOLDEN / case["path"]
    assert evaluate_case(directory, case) == expected(case)


def test_frozen_positive_example_minimums() -> None:
    counts: Counter[tuple[str, str]] = Counter()
    for case in CASES:
        tags = set(case["coverage"])
        timeframe = case["timeframe"].lower()
        for classification in (
            "filtered_buy_only",
            "filtered_sell_only",
            "confirmed_buy",
            "confirmed_sell",
        ):
            if classification in tags:
                counts[(classification, timeframe)] += 1
    for classification in (
        "filtered_buy_only",
        "filtered_sell_only",
        "confirmed_buy",
        "confirmed_sell",
    ):
        for timeframe in ("h1", "h4"):
            assert counts[(classification, timeframe)] >= 5


def test_required_coverage_categories_are_manifested() -> None:
    coverage = Counter(tag for case in CASES for tag in case["coverage"])
    required = {
        "filtered_buy_only",
        "filtered_sell_only",
        "confirmed_buy",
        "confirmed_sell",
        "confirmed_both",
        "simultaneous_direction",
        "technical_signal_without_filter",
        "sma10_failure",
        "sma20_failure",
        "structural_pivot_failure",
        "equality_boundaries",
        "developing_bar_exclusion",
        "h1_h4_independence",
        "missing_candle",
        "duplicate_candle",
        "missing_metadata",
        "equal_close_d1_boundary",
        "completed_as_of_close",
    }
    assert required <= coverage.keys()


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
def test_manifest_classification_tags_match_results(case: dict[str, Any]) -> None:
    result = expected(case)
    classification = result["classification"]
    tags = set(case["coverage"])
    if "filtered_buy_only" in tags:
        assert classification["buy_filter_matched"]
        assert not classification["technical_buy_signal"]
        assert not classification["confirmed_buy"]
    if "filtered_sell_only" in tags:
        assert classification["sell_filter_matched"]
        assert not classification["technical_sell_signal"]
        assert not classification["confirmed_sell"]
    if "confirmed_buy" in tags:
        assert classification["confirmed_buy"]
    if "confirmed_sell" in tags:
        assert classification["confirmed_sell"]
    if "confirmed_both" in tags:
        assert classification["confirmed_buy"]
        assert classification["confirmed_sell"]
        assert classification["dashboard_state"] == "CONFIRMED_BOTH"
        assert result["candidates"]["buy"] is not None
        assert result["candidates"]["sell"] is not None
    if "technical_signal_without_filter" in tags:
        direction = "buy" if "buy" in tags else "sell"
        assert classification[f"technical_{direction}_signal"]
        assert not classification[f"{direction}_filter_matched"]
        assert not classification[f"confirmed_{direction}"]
    if "structural_pivot_failure" in tags:
        direction = "buy" if "buy" in tags else "sell"
        assert classification[f"{direction}_sma_rejection"]
        assert not classification[f"{direction}_structural_pivot"]


def test_sma_failure_cases_isolate_the_named_touch() -> None:
    for case in CASES:
        tags = set(case["coverage"])
        if not tags.intersection({"sma10_failure", "sma20_failure"}):
            continue
        result = expected(case)
        direction = "buy" if "buy" in tags else "sell"
        bars = [
            bar
            for bar in csv_candles(GOLDEN / case["path"] / "signal_bars.csv")
            if bar["timeframe"] == case["timeframe"] and bar["is_complete"]
        ]
        latest = bars[-1]
        sma10 = result["indicators"]["sma10"]
        sma20 = result["indicators"]["sma20"]
        assert not result["classification"][f"{direction}_sma_rejection"]
        assert result["classification"][f"{direction}_structural_pivot"]
        if direction == "buy":
            assert latest["close"] >= max(sma10, sma20)
            if "sma10_failure" in tags:
                assert latest["low"] > sma10 and latest["low"] <= sma20
            else:
                assert latest["low"] <= sma10 and latest["low"] > sma20
        else:
            assert latest["close"] <= min(sma10, sma20)
            if "sma10_failure" in tags:
                assert latest["high"] < sma10 and latest["high"] >= sma20
            else:
                assert latest["high"] >= sma10 and latest["high"] < sma20


def test_equality_boundaries_are_inclusive() -> None:
    equality_cases = [
        case for case in CASES if "equality_boundaries" in case["coverage"]
    ]
    assert len(equality_cases) == 4
    for case in equality_cases:
        result = expected(case)
        tags = set(case["coverage"])
        direction = "buy" if "confirmed_buy" in tags else "sell"
        indicators = result["indicators"]
        bars = [
            bar
            for bar in csv_candles(GOLDEN / case["path"] / "signal_bars.csv")
            if bar["timeframe"] == case["timeframe"]
        ]
        latest = bars[-1]
        assert latest["close"] == indicators["sma10"] == indicators["sma20"]
        if direction == "buy":
            assert indicators["recent_low_21"] == indicators["daily_buy_level"]
        else:
            assert indicators["recent_high_21"] == indicators["daily_sell_level"]
        assert result["classification"][f"confirmed_{direction}"]


def test_equal_close_d1_boundary_is_frozen_for_h1_and_h4() -> None:
    boundary_cases = [
        case for case in CASES if "equal_close_d1_boundary" in case["coverage"]
    ]
    assert {case["timeframe"] for case in boundary_cases} == {"H1", "H4"}
    assert len(boundary_cases) == 4
    for case in boundary_cases:
        result = expected(case)
        assert result["bars"]["daily_endpoint_close_time"] == result["bars"][
            "signal_bar_close_time"
        ]
        assert result["indicators"]["atr_d1_wilder_5"] == 11.6
        assert result["indicators"]["activation_buffer"] == 0.58
        assert result["indicators"]["daily_raw_low"] == 50.0
        assert result["indicators"]["daily_buy_level"] == 50.58
        assert result["indicators"]["daily_sell_level"] == 100.42
        assert result["classification"]["buy_filter_matched"] is False
        assert result["classification"]["sell_filter_matched"] is False

    new_york_cases = [
        case for case in boundary_cases if "new_york_close_d1" in case["coverage"]
    ]
    assert len(new_york_cases) == 2
    assert all(
        expected(case)["bars"]["daily_endpoint_close_time"]
        == "2026-07-17T21:00:00Z"
        for case in new_york_cases
    )


def test_simultaneous_direction_preserves_both_candidates() -> None:
    simultaneous = [
        case
        for case in CASES
        if "simultaneous_direction" in case["coverage"]
    ]
    assert len(simultaneous) == 1
    result = expected(simultaneous[0])
    classification = result["classification"]
    assert classification["buy_filter_matched"] is True
    assert classification["sell_filter_matched"] is True
    assert classification["technical_buy_signal"] is True
    assert classification["technical_sell_signal"] is True
    assert classification["confirmed_buy"] is True
    assert classification["confirmed_sell"] is True
    assert classification["dashboard_state"] == "CONFIRMED_BOTH"
    buy = result["candidates"]["buy"]
    sell = result["candidates"]["sell"]
    assert buy["direction"] == "BUY"
    assert sell["direction"] == "SELL"
    assert buy["entry_reference"] == sell["entry_reference"]
    assert buy["provider_adjusted_stop"] < buy["entry_reference"]
    assert sell["provider_adjusted_stop"] > sell["entry_reference"]
    assert buy["target_3r"] > buy["entry_reference"]
    assert sell["target_3r"] < sell["entry_reference"]
    assert buy["target_risk_usd"] == sell["target_risk_usd"] == 100.0


def test_developing_bars_are_excluded_from_every_calculation() -> None:
    cases = [case for case in CASES if "developing_bar_exclusion" in case["coverage"]]
    assert len(cases) == 2
    for case in cases:
        directory = GOLDEN / case["path"]
        result = expected(case)
        evaluation = datetime.fromisoformat(case["evaluation_time"].replace("Z", "+00:00"))
        selected = [
            bar
            for bar in csv_candles(directory / "signal_bars.csv")
            if bar["timeframe"] == case["timeframe"]
        ]
        excluded = [
            bar
            for bar in selected
            if not bar["is_complete"]
            or datetime.fromisoformat(bar["close_time"].replace("Z", "+00:00"))
            >= evaluation
        ]
        assert excluded
        assert result["bars"]["excluded_incomplete_count"] == len(excluded)
        used_close = datetime.fromisoformat(
            result["bars"]["signal_bar_close_time"].replace("Z", "+00:00")
        )
        assert used_close < evaluation
        assert all(
            used_close
            < datetime.fromisoformat(bar["close_time"].replace("Z", "+00:00"))
            for bar in excluded
        )


def test_h1_h4_independence_groups_have_opposite_independent_results() -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in CASES:
        if "independence_group" in case:
            groups[case["independence_group"]].append(case)
    assert set(groups) == {
        "independence_h1_buy_h4_sell",
        "independence_h1_sell_h4_buy",
    }
    for group, cases in groups.items():
        assert {case["timeframe"] for case in cases} == {"H1", "H4"}
        assert len(cases) == 2
        directions = set()
        for case in cases:
            result = expected(case)
            if result["classification"]["confirmed_buy"]:
                directions.add("BUY")
            if result["classification"]["confirmed_sell"]:
                directions.add("SELL")
        assert directions == {"BUY", "SELL"}, group


def test_missing_and_duplicate_candles_are_quarantined() -> None:
    cases = [
        case
        for case in CASES
        if set(case["coverage"]).intersection({"missing_candle", "duplicate_candle"})
    ]
    assert len(cases) == 4
    for case in cases:
        result = expected(case)
        assert result["data_status"] == "UNAVAILABLE"
        assert result["classification"]["dashboard_state"] == "DATA_UNAVAILABLE"
        assert result["indicators"] is None
        assert result["pivots"] is None
        assert result["candidates"] == {"buy": None, "sell": None}
        if "missing_candle" in case["coverage"]:
            assert any(issue.startswith("MISSING_") for issue in result["issues"])
        if "duplicate_candle" in case["coverage"]:
            assert "DUPLICATE_CANDLE" in result["issues"]


def test_every_confirmed_candidate_uses_exactly_usd_100_and_rounds_down() -> None:
    confirmed_count = 0
    rounded_count = 0
    for case in CASES:
        result = expected(case)
        metadata = json.loads(
            (GOLDEN / case["path"] / "instrument.json").read_text(encoding="utf-8")
        )
        for candidate in result["candidates"].values():
            if candidate is None:
                continue
            confirmed_count += 1
            assert candidate["target_risk_usd"] == 100.0
            if candidate["contract_status"] == "VALID":
                assert candidate["display_size"] <= candidate["raw_size"]
                assert candidate["display_size"] * candidate[
                    "monetary_loss_per_one_contract"
                ] <= 100.0 + 1e-8
                step_units = candidate["display_size"] / metadata["contract_step"]
                assert step_units == pytest.approx(round(step_units))
                if candidate["display_size"] < candidate["raw_size"]:
                    rounded_count += 1
    assert confirmed_count == 22
    assert rounded_count > 0


def test_excluded_state_and_risk_features_do_not_exist() -> None:
    prohibited_keys = {
        "filter_consumed",
        "filter_armed",
        "risk_multiplier",
        "first_trade",
        "reverse_filter",
    }

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(child) for child in value), set())
        return set()

    for case in CASES:
        assert not (keys(expected(case)) & prohibited_keys)


def test_ledgers_record_source_counts_intermediates_and_decisions() -> None:
    for case in CASES:
        text = (GOLDEN / case["path"] / "calculation_ledger.md").read_text(
            encoding="utf-8"
        )
        assert f"# Calculation ledger: {case['id']}" in text
        assert "Completed-bar gate" in text
        if "data_unavailable" in case["coverage"]:
            assert "Quarantine decision" in text
        else:
            assert "Indicators" in text
            assert "Filter and signal decisions" in text
            assert "Candidate levels" in text


def test_frozen_checksums_are_complete_and_correct() -> None:
    checksum_path = GOLDEN / "CHECKSUMS.sha256"
    recorded: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        assert len(digest) == 64
        recorded[relative] = digest

    required = {
        "Spect8_Micro_Daily_v1_0_FROZEN.md",
        "Spect8_Micro_Daily_v1_0_1_FROZEN.md",
        "Spect8_Micro_Daily_v1_0_2_FROZEN.md",
        "Spect8_Micro_Daily_v1_0_3_FROZEN.md",
        "golden/manifest.json",
    }
    for case in CASES:
        required.update(
            f"golden/{case['path']}/{name}"
            for name in (
                "signal_bars.csv",
                "daily_bars.csv",
                "instrument.json",
                "expected.json",
            )
        )
    assert set(recorded) == required
    for relative, frozen_digest in recorded.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert actual == frozen_digest, relative
