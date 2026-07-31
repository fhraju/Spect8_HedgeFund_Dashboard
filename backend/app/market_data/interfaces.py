from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import (
    CanonicalInstrument,
    ProviderClosedBar,
    ProviderHealth,
    ProviderHistory,
    ProviderIdentity,
)


@runtime_checkable
class MarketDataProvider(Protocol):
    @property
    def identity(self) -> ProviderIdentity: ...

    def discover_instruments(self) -> tuple[CanonicalInstrument, ...]: ...

    def fetch_completed_bars(self, as_of: datetime) -> tuple[ProviderClosedBar, ...]:
        ...

    def fetch_required_history(
        self,
        closed_bar: ProviderClosedBar,
        as_of: datetime,
    ) -> ProviderHistory:
        ...

    def health(self, as_of: datetime) -> ProviderHealth: ...
