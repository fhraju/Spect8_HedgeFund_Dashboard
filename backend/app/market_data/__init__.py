"""Provider-neutral market-data adapters for Phase 2B and Phase 2C."""

from .clock import Clock, FixedClock, SystemClock
from .coordinator import MarketDataCoordinator, PollResult
from .interfaces import MarketDataProvider
from .models import (
    CanonicalInstrument,
    HealthState,
    MarketDataProviderError,
    ProviderErrorCode,
    ProviderHealth,
    RawProviderCandle,
)
from .replay_provider import ReplayMarketDataProvider
from .twelve_data_provider import TwelveDataProvider

__all__ = [
    "CanonicalInstrument",
    "Clock",
    "FixedClock",
    "HealthState",
    "MarketDataCoordinator",
    "MarketDataProvider",
    "MarketDataProviderError",
    "PollResult",
    "ProviderErrorCode",
    "ProviderHealth",
    "RawProviderCandle",
    "ReplayMarketDataProvider",
    "SystemClock",
    "TwelveDataProvider",
]
