from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain import Timeframe

from .models import (
    CanonicalInstrument,
    ProviderClosedBar,
    ProviderHealth,
    ProviderHistory,
    ProviderIdentity,
    ProviderProfile,
    RawProviderCandle,
    TimestampSemantics,
)


@runtime_checkable
class MarketDataProvider(Protocol):
    @property
    def identity(self) -> ProviderIdentity:
        ...

    def discover_instruments(self) -> tuple[CanonicalInstrument, ...]:
        ...

    def fetch_completed_bars(self, as_of: datetime) -> tuple[ProviderClosedBar, ...]:
        ...

    def fetch_required_history(
        self,
        closed_bar: ProviderClosedBar,
        as_of: datetime,
    ) -> ProviderHistory:
        ...

    def health(self, as_of: datetime) -> ProviderHealth:
        ...


@runtime_checkable
class ProviderAdapter(Protocol):
    """Raw provider boundary; canonical construction stays downstream."""

    def provider_profile(self) -> ProviderProfile:
        ...

    def map_symbol(self, canonical_instrument: str) -> str:
        ...

    def fetch_raw_candles(
        self,
        instrument: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[RawProviderCandle, ...]:
        ...

    def normalize_timestamp(
        self,
        provider_timestamp: str,
        semantics: TimestampSemantics,
        timeframe: Timeframe,
    ) -> tuple[datetime, datetime]:
        ...

    def report_health(self, as_of: datetime) -> ProviderHealth:
        ...
