"""Load committed synthetic OHLC inputs for the walking skeleton runtime.

Only the manifest, candle CSV files, and instrument metadata are runtime
inputs. Oracle results and calculation ledgers remain test-only artifacts.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .domain import Bar, Timeframe
from .engine.models import InstrumentMetadata, StrategyRequest


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class SyntheticCaseInputLoader:
    def __init__(self, repository_root: Path) -> None:
        self._fixture_root = repository_root / "golden"
        manifest = json.loads(
            (self._fixture_root / "manifest.json").read_text(encoding="utf-8")
        )
        self._cases = {case["id"]: case for case in manifest["cases"]}

    def load(self, case_id: str) -> StrategyRequest:
        try:
            definition = self._cases[case_id]
        except KeyError as error:
            raise ValueError(f"unknown synthetic case: {case_id}") from error

        case_directory = self._fixture_root / definition["path"]
        raw = json.loads(
            (case_directory / "instrument.json").read_text(encoding="utf-8")
        )
        instrument = InstrumentMetadata(
            strategy_id=raw["strategy_id"],
            instrument_id=raw["instrument_id"],
            display_name=raw["display_name"],
            provider=raw["provider"],
            session_timezone=raw["session_timezone"],
            candle_boundary_convention=raw["candle_boundary_convention"],
            point_size=Decimal(str(raw["point_size"])),
            price_precision=int(raw["price_precision"]),
            minimum_stop_distance_points=_optional_decimal(
                raw["minimum_stop_distance_points"]
            ),
            tick_size=_optional_decimal(raw["tick_size"]),
            tick_value_usd=_optional_decimal(raw["tick_value_usd"]),
            conversion_rate_to_usd=_optional_decimal(
                raw["conversion_rate_to_usd"]
            ),
            contract_min=_optional_decimal(raw["contract_min"]),
            contract_max=_optional_decimal(raw["contract_max"]),
            contract_step=_optional_decimal(raw["contract_step"]),
        )
        return StrategyRequest(
            case_id=case_id,
            strategy_id=raw["strategy_id"],
            timeframe=Timeframe(definition["timeframe"]),
            evaluation_time=_datetime(definition["evaluation_time"]),
            signal_bars=self._load_bars(case_directory / "signal_bars.csv"),
            daily_bars=self._load_bars(case_directory / "daily_bars.csv"),
            instrument=instrument,
        )

    @staticmethod
    def _load_bars(path: Path) -> tuple[Bar, ...]:
        bars: list[Bar] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                bars.append(
                    Bar(
                        instrument_id=row["canonical_instrument_id"],
                        timeframe=Timeframe(row["timeframe"]),
                        open_time=_datetime(row["open_time"]),
                        close_time=_datetime(row["close_time"]),
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        provider=row["provider"],
                        is_complete=row["is_complete"].lower() == "true",
                        volume=(
                            Decimal(row["volume"])
                            if row.get("volume", "")
                            else None
                        ),
                        synthetic=True,
                    )
                )
        return tuple(bars)
