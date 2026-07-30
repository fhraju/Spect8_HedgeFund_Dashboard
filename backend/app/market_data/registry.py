from __future__ import annotations

from dataclasses import dataclass

from ..domain import Timeframe
from .models import CanonicalInstrument


@dataclass(frozen=True, slots=True)
class CanonicalInstrumentRegistry:
    _instruments: tuple[CanonicalInstrument, ...]

    def __post_init__(self) -> None:
        keys = [
            (instrument.provider_id, instrument.instrument_id)
            for instrument in self._instruments
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate canonical instrument registry key")

    def all(self) -> tuple[CanonicalInstrument, ...]:
        return self._instruments

    def get(self, provider_id: str, instrument_id: str) -> CanonicalInstrument:
        for instrument in self._instruments:
            if (
                instrument.provider_id == provider_id
                and instrument.instrument_id == instrument_id
            ):
                return instrument
        raise KeyError(f"unknown instrument {provider_id}:{instrument_id}")

    def supports(
        self,
        provider_id: str,
        instrument_id: str,
        timeframe: Timeframe,
    ) -> bool:
        return timeframe in self.get(
            provider_id, instrument_id
        ).available_timeframes
