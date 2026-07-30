"""Provider-neutral market-data foundation for Phase 2B."""

from .clock import Clock, FixedClock, SystemClock
from .coordinator import MarketDataCoordinator, PollResult
from .interfaces import MarketDataProvider
from .models import (
    CanonicalInstrument,
    HealthState,
    ProviderHealth,
    RawProviderCandle,
)
from .replay_provider import ReplayMarketDataProvider

__all__ = [
    "CanonicalInstrument",
    "Clock",
    "FixedClock",
    "HealthState",
    "MarketDataCoordinator",
    "MarketDataProvider",
    "PollResult",
    "ProviderHealth",
    "RawProviderCandle",
    "ReplayMarketDataProvider",
    "SystemClock",
]
