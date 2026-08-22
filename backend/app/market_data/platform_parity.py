"""Deterministic old-provider versus Platform-adapter parity comparison."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain import Bar, primitive
from ..engine.models import StrategyRequest
from ..service import WalkingSkeletonService

_PROVENANCE_FIELDS = frozenset(
    {
        "case_id",
        "source_case_id",
        "provider",
        "synthetic",
        "idempotency_key",
        "raw_provider_symbol",
        "raw_open_time",
        "raw_close_time",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "construction_profile_version",
        "provider_adapter_version",
        "source_candle_ids",
        "source_id",
        "source_timeframe",
        "session_timezone",
        "ingestion_run_id",
        "created_at",
        "source_provider",
        "daily_filter_snapshot_id",
        "filter_snapshot_id",
        "snapshot_id",
        "source_checksum",
        "source_h1_ids",
        "previous_d1_candle_id",
        "atr_source_d1_ids",
        "atr_source_checksum",
        "previous_w1_candle_id",
        "atr_source_w1_ids",
    }
)


@dataclass(frozen=True, slots=True)
class ParityMismatch:
    path: str
    old_value: object
    platform_value: object


@dataclass(frozen=True, slots=True)
class ParityReport:
    matched: bool
    mismatches: tuple[ParityMismatch, ...]
    old_event_order: tuple[str, ...]
    platform_event_order: tuple[str, ...]

    def assert_matched(self) -> None:
        if self.matched:
            return
        first = self.mismatches[0]
        raise AssertionError(
            f"strategy parity mismatch at {first.path}: "
            f"old={first.old_value!r}, platform={first.platform_value!r}"
        )


@dataclass(frozen=True, slots=True)
class ProviderDataDifference:
    timeframe: str
    open_time: str
    close_time: str
    old_ohlc: tuple[str, str, str, str] | None
    platform_bid_ohlc: tuple[str, str, str, str] | None
    classification: str = "PROVIDER_DATA_DIFFERENCE"


def classify_provider_data_differences(
    old_bars: tuple[Bar, ...],
    platform_bid_bars: tuple[Bar, ...],
) -> tuple[ProviderDataDifference, ...]:
    """Report source OHLC differences without calling them semantic parity failures."""

    def key(bar: Bar) -> tuple[str, str, str]:
        return (
            bar.timeframe.value,
            bar.open_time.isoformat(),
            bar.close_time.isoformat(),
        )

    def ohlc(bar: Bar | None) -> tuple[str, str, str, str] | None:
        if bar is None:
            return None
        return tuple(str(value) for value in (bar.open, bar.high, bar.low, bar.close))

    old = {key(bar): bar for bar in old_bars}
    platform = {key(bar): bar for bar in platform_bid_bars}
    differences: list[ProviderDataDifference] = []
    for identity in sorted(set(old) | set(platform)):
        old_bar = old.get(identity)
        platform_bar = platform.get(identity)
        if ohlc(old_bar) == ohlc(platform_bar):
            continue
        differences.append(
            ProviderDataDifference(
                timeframe=identity[0],
                open_time=identity[1],
                close_time=identity[2],
                old_ohlc=ohlc(old_bar),
                platform_bid_ohlc=ohlc(platform_bar),
            )
        )
    return tuple(differences)


class DeterministicParityHarness:
    """Compare strategy semantics while excluding transport-only provenance."""

    def __init__(self, service: WalkingSkeletonService) -> None:
        self._service = service

    def compare(
        self,
        old_request: StrategyRequest,
        platform_request: StrategyRequest,
    ) -> ParityReport:
        old = self._service.evaluate_request(old_request)
        platform = self._service.evaluate_request(platform_request)
        old_events = self._service.events_for_projection(old)
        platform_events = self._service.events_for_projection(platform)
        old_value = _without_provenance(
            {
                "evaluation": primitive(old.evaluation),
                "filter_result": primitive(old.filter_result),
                "signal_result": primitive(old.signal_result),
                "levels_results": primitive(old.levels_results),
                "status": primitive(old.status),
                "events": primitive(old_events),
                "daily_filter_snapshot": primitive(old_request.daily_filter_snapshot),
                "w1_filter_snapshot": primitive(old_request.w1_filter_snapshot),
            }
        )
        platform_value = _without_provenance(
            {
                "evaluation": primitive(platform.evaluation),
                "filter_result": primitive(platform.filter_result),
                "signal_result": primitive(platform.signal_result),
                "levels_results": primitive(platform.levels_results),
                "status": primitive(platform.status),
                "events": primitive(platform_events),
                "daily_filter_snapshot": primitive(
                    platform_request.daily_filter_snapshot
                ),
                "w1_filter_snapshot": primitive(platform_request.w1_filter_snapshot),
            }
        )
        mismatches: list[ParityMismatch] = []
        _compare_exact("$", old_value, platform_value, mismatches)
        old_order = tuple(event.event_type.value for event in old_events)
        platform_order = tuple(event.event_type.value for event in platform_events)
        return ParityReport(
            matched=not mismatches and old_order == platform_order,
            mismatches=tuple(mismatches),
            old_event_order=old_order,
            platform_event_order=platform_order,
        )


def _without_provenance(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_provenance(child)
            for key, child in value.items()
            if key not in _PROVENANCE_FIELDS
        }
    if isinstance(value, list):
        return [_without_provenance(child) for child in value]
    return value


def _compare_exact(
    path: str,
    old: Any,
    platform: Any,
    mismatches: list[ParityMismatch],
) -> None:
    if type(old) is not type(platform):
        mismatches.append(ParityMismatch(path, old, platform))
        return
    if isinstance(old, dict):
        keys = sorted(set(old) | set(platform))
        for key in keys:
            if key not in old or key not in platform:
                mismatches.append(
                    ParityMismatch(f"{path}.{key}", old.get(key), platform.get(key))
                )
            else:
                _compare_exact(f"{path}.{key}", old[key], platform[key], mismatches)
        return
    if isinstance(old, list):
        if len(old) != len(platform):
            mismatches.append(ParityMismatch(f"{path}.length", len(old), len(platform)))
            return
        for index, (old_item, platform_item) in enumerate(zip(old, platform)):
            _compare_exact(f"{path}[{index}]", old_item, platform_item, mismatches)
        return
    if old != platform:
        mismatches.append(ParityMismatch(path, old, platform))
