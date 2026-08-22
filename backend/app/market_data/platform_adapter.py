"""Market Data Platform compatibility boundary for confirmed Spect8 inputs.

The Platform remains the canonical market-data authority.  This module owns
only the explicit translation into Spect8's existing strategy-domain objects:

* BID is the sole current strategy price authority;
* instrument identities are mapped explicitly;
* Platform UTC H4 rows are never used by the strategy;
* Spect8 broker-aligned H4 is rebuilt from canonical H1 BID rows; and
* canonical versions and the read watermark remain observable application
  provenance rather than silently rewriting historical strategy state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Protocol

from ..domain import Bar, Timeframe
from .forex_profile import BrokerAlignedH4Aggregator
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID
from .session_boundaries import NEW_YORK_SESSION_TIMEZONE

PLATFORM_PROVIDER_ID = "MARKET_DATA_PLATFORM"
SPECT8_PRICE_TYPE = "BID"
DIRECT_STRATEGY_TIMEFRAMES = frozenset({"H1", "D1"})

SPECT8_PLATFORM_BOOTSTRAP_LIMITS: Mapping[str, int] = MappingProxyType(
    {
        "M30": 1,
        "H1": 1_177,
        "H4": 30,
        "D1": 10,
        "W1": 6,
    }
)

PLATFORM_TO_SPECT8_INSTRUMENT: Mapping[str, str] = MappingProxyType(
    {
        "FX_EUR_USD": "EUR_USD",
        "FX_GBP_USD": "GBP_USD",
        "FX_USD_JPY": "USD_JPY",
    }
)
SPECT8_TO_PLATFORM_INSTRUMENT: Mapping[str, str] = MappingProxyType(
    {value: key for key, value in PLATFORM_TO_SPECT8_INSTRUMENT.items()}
)


class PlatformAdapterError(ValueError):
    """Base class for deterministic Platform compatibility failures."""


class UnmappedPlatformInstrumentError(PlatformAdapterError):
    """Raised when an instrument lacks an explicitly approved mapping."""


class InsufficientPlatformHistoryError(PlatformAdapterError):
    """Raised when a bounded bootstrap cannot satisfy frozen Spect8 depth."""


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class PlatformCanonicalBar:
    canonical_bar_id: int
    instrument_id: str
    timeframe: str
    price_type: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    volume_type: str
    quality_status: str
    source_provider_id: str
    policy_id: str
    policy_version: str
    version_number: int
    semantic_hash: str
    semantic_available_at: datetime

    def __post_init__(self) -> None:
        if type(self.canonical_bar_id) is not int or self.canonical_bar_id <= 0:
            raise ValueError("canonical_bar_id must be positive")
        if type(self.version_number) is not int or self.version_number <= 0:
            raise ValueError("version_number must be positive")
        for field_name in (
            "instrument_id",
            "timeframe",
            "price_type",
            "volume_type",
            "quality_status",
            "source_provider_id",
            "policy_id",
            "policy_version",
            "semantic_hash",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must be non-empty")
        object.__setattr__(self, "open_time", _utc(self.open_time, "open_time"))
        object.__setattr__(self, "close_time", _utc(self.close_time, "close_time"))
        object.__setattr__(
            self,
            "semantic_available_at",
            _utc(self.semantic_available_at, "semantic_available_at"),
        )
        if self.close_time <= self.open_time:
            raise ValueError("canonical bar close_time must be after open_time")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("canonical bar high is invalid")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("canonical bar low is invalid")

    @property
    def logical_identity(self) -> str:
        return (
            f"{self.instrument_id}|{self.timeframe}|{self.price_type}|"
            f"{self.open_time.isoformat()}|{self.close_time.isoformat()}|"
            f"{self.policy_id}|{self.policy_version}"
        )

    @property
    def immutable_identity(self) -> str:
        return (
            f"{self.logical_identity}|version={self.version_number}|"
            f"semantic={self.semantic_hash}|id={self.canonical_bar_id}"
        )


@dataclass(frozen=True, slots=True)
class PlatformSeriesAvailability:
    instrument_id: str
    timeframe: str
    price_type: str
    returned_rows: int
    latest_close_time: datetime | None
    valid: bool


@dataclass(frozen=True, slots=True)
class PlatformReadBatch:
    bars: tuple[PlatformCanonicalBar, ...]
    availability: tuple[PlatformSeriesAvailability, ...]
    watermark_canonical_bar_id: int
    available_as_of: datetime
    instrument_master_checksum: str
    session_calendar_checksum: str
    timezone_data_version: str

    def __post_init__(self) -> None:
        if (
            type(self.watermark_canonical_bar_id) is not int
            or self.watermark_canonical_bar_id < 0
        ):
            raise ValueError("watermark_canonical_bar_id must be non-negative")
        object.__setattr__(
            self, "available_as_of", _utc(self.available_as_of, "available_as_of")
        )


class PlatformCanonicalReadGateway(Protocol):
    def read(
        self,
        instrument_ids: tuple[str, ...],
        *,
        available_as_of: datetime,
        after_canonical_bar_id: int | None,
        limits: Mapping[str, int],
    ) -> PlatformReadBatch:
        ...

    def read_canonical_ids(
        self, canonical_bar_ids: tuple[int, ...]
    ) -> tuple[PlatformCanonicalBar, ...]:
        ...


class Spect8CanonicalReadServiceGateway:
    """Thin optional-dependency wrapper around the Platform read service."""

    def __init__(self, service: object, timeframe_type: type | None = None) -> None:
        self._service = service
        self._timeframe_type = timeframe_type

    def read(
        self,
        instrument_ids: tuple[str, ...],
        *,
        available_as_of: datetime,
        after_canonical_bar_id: int | None,
        limits: Mapping[str, int] = SPECT8_PLATFORM_BOOTSTRAP_LIMITS,
    ) -> PlatformReadBatch:
        timeframe_type = self._timeframe_type
        if timeframe_type is None:
            try:
                from hedgefund_market_data.domain import (  # type: ignore[import-not-found]
                    Timeframe as PlatformTimeframe,
                )
            except ImportError as error:
                raise RuntimeError(
                    "hedgefund-market-data must be installed to use the Platform gateway"
                ) from error
            timeframe_type = PlatformTimeframe
        platform_limits = {
            timeframe_type(timeframe): count for timeframe, count in limits.items()
        }
        reader = getattr(self._service, "read", None)
        if not callable(reader):
            raise TypeError("service must provide a callable read method")
        result = reader(
            instrument_ids,
            available_as_of=available_as_of,
            after_canonical_bar_id=after_canonical_bar_id,
            limits=platform_limits,
        )
        return PlatformReadBatch(
            bars=tuple(PlatformCanonicalBar(**_public_fields(bar)) for bar in result.bars),
            availability=tuple(
                PlatformSeriesAvailability(**_public_fields(item))
                for item in result.availability
            ),
            watermark_canonical_bar_id=result.watermark_canonical_bar_id,
            available_as_of=result.available_as_of,
            instrument_master_checksum=result.instrument_master_checksum,
            session_calendar_checksum=result.session_calendar_checksum,
            timezone_data_version=result.timezone_data_version,
        )

    def read_canonical_ids(
        self, canonical_bar_ids: tuple[int, ...]
    ) -> tuple[PlatformCanonicalBar, ...]:
        reader = getattr(self._service, "read_canonical_ids", None)
        if not callable(reader):
            raise TypeError("service must provide a callable read_canonical_ids method")
        return tuple(
            PlatformCanonicalBar(**_public_fields(bar))
            for bar in reader(canonical_bar_ids)
        )


def _public_fields(value: object) -> dict[str, object]:
    slots = getattr(type(value), "__slots__", ())
    if slots:
        return {name: getattr(value, name) for name in slots if not name.startswith("_")}
    attributes = vars(value)
    return {name: item for name, item in attributes.items() if not name.startswith("_")}


def platform_instrument_id(spect8_instrument_id: str) -> str:
    try:
        return SPECT8_TO_PLATFORM_INSTRUMENT[spect8_instrument_id]
    except KeyError as error:
        raise UnmappedPlatformInstrumentError(
            f"unmapped Spect8 instrument: {spect8_instrument_id}"
        ) from error


def spect8_instrument_id(platform_id: str) -> str:
    try:
        return PLATFORM_TO_SPECT8_INSTRUMENT[platform_id]
    except KeyError as error:
        raise UnmappedPlatformInstrumentError(
            f"unmapped Platform instrument: {platform_id}"
        ) from error


def bid_bars(bars: Sequence[PlatformCanonicalBar]) -> tuple[PlatformCanonicalBar, ...]:
    """Select valid BID only; ASK/MID can never become strategy input."""

    selected = tuple(
        bar
        for bar in bars
        if bar.price_type == SPECT8_PRICE_TYPE and bar.quality_status == "VALID"
    )
    for bar in selected:
        spect8_instrument_id(bar.instrument_id)
    return tuple(
        sorted(
            selected,
            key=lambda bar: (
                bar.semantic_available_at,
                bar.canonical_bar_id,
                bar.version_number,
            ),
        )
    )


def first_accepted_versions(
    bars: Sequence[PlatformCanonicalBar],
) -> tuple[PlatformCanonicalBar, ...]:
    first: dict[str, PlatformCanonicalBar] = {}
    for bar in bid_bars(bars):
        first.setdefault(bar.logical_identity, bar)
    return tuple(
        sorted(
            first.values(),
            key=lambda bar: (bar.instrument_id, bar.timeframe, bar.open_time),
        )
    )


def to_spect8_bar(bar: PlatformCanonicalBar) -> Bar:
    """Translate a directly consumable canonical BID H1/D1 into Spect8."""

    if bar.price_type != SPECT8_PRICE_TYPE:
        raise PlatformAdapterError("Spect8 strategy input requires BID")
    if bar.quality_status != "VALID":
        raise PlatformAdapterError("Spect8 strategy input requires VALID quality")
    if bar.timeframe not in DIRECT_STRATEGY_TIMEFRAMES:
        raise PlatformAdapterError(
            f"Platform {bar.timeframe} is not a direct Spect8 strategy input"
        )
    instrument_id = spect8_instrument_id(bar.instrument_id)
    timeframe = Timeframe(bar.timeframe)
    source_identity = f"MDP:{bar.canonical_bar_id}:{bar.semantic_hash}"
    return Bar(
        instrument_id=instrument_id,
        timeframe=timeframe,
        open_time=bar.open_time,
        close_time=bar.close_time,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        provider=PLATFORM_PROVIDER_ID,
        is_complete=True,
        volume=bar.volume,
        session_timezone=(
            NEW_YORK_SESSION_TIMEZONE if timeframe is Timeframe.D1 else "UTC"
        ),
        raw_provider_symbol=bar.instrument_id,
        raw_open_time=bar.open_time.isoformat().replace("+00:00", "Z"),
        raw_close_time=bar.close_time.isoformat().replace("+00:00", "Z"),
        raw_open=str(bar.open),
        raw_high=str(bar.high),
        raw_low=str(bar.low),
        raw_close=str(bar.close),
        synthetic=False,
        quality_status="VALID",
        construction_profile_version=PROFILE_ID,
        provider_adapter_version=(
            f"{bar.policy_id}:{bar.policy_version}:canonical-v{bar.version_number}"
        ),
        source_candle_ids=(source_identity,),
        forward_filled=False,
        ingestion_run_id=f"mdp-canonical-{bar.canonical_bar_id}",
        created_at=bar.semantic_available_at,
    )


@dataclass(frozen=True, slots=True)
class PlatformInstrumentHistory:
    platform_instrument_id: str
    spect8_instrument_id: str
    m30: tuple[PlatformCanonicalBar, ...]
    h1: tuple[Bar, ...]
    h4: tuple[Bar, ...]
    d1: tuple[Bar, ...]
    w1: tuple[PlatformCanonicalBar, ...]
    h4_issues: tuple[str, ...]

    def assert_bootstrap_ready(self) -> None:
        counts = {
            "M30": len(self.m30),
            "H1": len(self.h1),
            "H4": len(self.h4),
            "D1": len(self.d1),
            "W1": len(self.w1),
        }
        insufficient = {
            timeframe: (counts[timeframe], required)
            for timeframe, required in SPECT8_PLATFORM_BOOTSTRAP_LIMITS.items()
            if counts[timeframe] < required
        }
        if insufficient:
            detail = ", ".join(
                f"{timeframe}={actual}/{required}"
                for timeframe, (actual, required) in insufficient.items()
            )
            raise InsufficientPlatformHistoryError(
                f"{self.spect8_instrument_id} bootstrap history is insufficient: {detail}"
            )


def build_platform_history(
    batch: PlatformReadBatch,
    spect8_id: str,
) -> PlatformInstrumentHistory:
    platform_id = platform_instrument_id(spect8_id)
    selected = tuple(
        bar
        for bar in first_accepted_versions(batch.bars)
        if bar.instrument_id == platform_id
    )
    grouped = {
        timeframe: tuple(
            sorted(
                (bar for bar in selected if bar.timeframe == timeframe),
                key=lambda bar: bar.open_time,
            )
        )
        for timeframe in SPECT8_PLATFORM_BOOTSTRAP_LIMITS
    }
    h1 = tuple(to_spect8_bar(bar) for bar in grouped["H1"])[
        -SPECT8_PLATFORM_BOOTSTRAP_LIMITS["H1"] :
    ]
    d1 = tuple(to_spect8_bar(bar) for bar in grouped["D1"])[
        -SPECT8_PLATFORM_BOOTSTRAP_LIMITS["D1"] :
    ]
    h4_result = BrokerAlignedH4Aggregator().aggregate(
        h1,
        as_of=batch.available_as_of,
    )
    return PlatformInstrumentHistory(
        platform_instrument_id=platform_id,
        spect8_instrument_id=spect8_id,
        m30=grouped["M30"][-SPECT8_PLATFORM_BOOTSTRAP_LIMITS["M30"] :],
        h1=h1,
        h4=h4_result.bars[-SPECT8_PLATFORM_BOOTSTRAP_LIMITS["H4"] :],
        d1=d1,
        w1=grouped["W1"][-SPECT8_PLATFORM_BOOTSTRAP_LIMITS["W1"] :],
        h4_issues=tuple(issue.code.value for issue in h4_result.issues),
    )


@dataclass(frozen=True, slots=True)
class PlatformProcessingResult:
    previous_watermark: int
    new_watermark: int
    consumed: int
    replayed: int
    revisions_detected: int
    ignored_ask_or_mid: int
    ignored_non_strategy_timeframes: int


class PlatformIncrementalProcessor:
    """Consume canonical changes and checkpoint only after successful handling."""

    def __init__(
        self,
        gateway: PlatformCanonicalReadGateway,
        repository: object,
    ) -> None:
        self._gateway = gateway
        self._repository = repository

    def process(
        self,
        spect8_instrument_ids: tuple[str, ...],
        *,
        available_as_of: datetime,
        process_bar: Callable[
            [PlatformCanonicalBar, str], str | tuple[str, ...] | None
        ],
    ) -> PlatformProcessingResult:
        platform_ids = tuple(
            platform_instrument_id(instrument_id)
            for instrument_id in spect8_instrument_ids
        )
        state = self._repository.platform_integration_state()
        previous_watermark = int(state["watermark_canonical_bar_id"]) if state else 0
        batch = self._gateway.read(
            platform_ids,
            available_as_of=available_as_of,
            after_canonical_bar_id=(previous_watermark or None),
            limits=SPECT8_PLATFORM_BOOTSTRAP_LIMITS,
        )
        consumed = 0
        replayed = 0
        revisions = 0
        ignored_non_strategy = 0
        selected = bid_bars(batch.bars)
        ignored_prices = len(batch.bars) - len(selected)
        for bar in selected:
            mapped_id = spect8_instrument_id(bar.instrument_id)
            if bar.timeframe not in DIRECT_STRATEGY_TIMEFRAMES:
                ignored_non_strategy += 1
                continue
            existing = self._repository.platform_consumed_identity(
                bar.logical_identity
            )
            if existing is not None:
                if (
                    int(existing["canonical_bar_id"]) == bar.canonical_bar_id
                    and existing["semantic_hash"] == bar.semantic_hash
                    and int(existing["version_number"]) == bar.version_number
                ):
                    replayed += 1
                else:
                    self._repository.record_platform_revision(
                        logical_identity=bar.logical_identity,
                        first_canonical_bar_id=int(existing["canonical_bar_id"]),
                        revised_canonical_bar_id=bar.canonical_bar_id,
                        revised_version_number=bar.version_number,
                        revised_semantic_hash=bar.semantic_hash,
                        observed_at=batch.available_as_of,
                    )
                    revisions += 1
                continue
            evaluation_keys = _evaluation_keys(process_bar(bar, mapped_id))
            self._repository.record_platform_consumption(
                logical_identity=bar.logical_identity,
                immutable_identity=bar.immutable_identity,
                canonical_bar_id=bar.canonical_bar_id,
                platform_instrument_id=bar.instrument_id,
                spect8_instrument_id=mapped_id,
                timeframe=bar.timeframe,
                price_type=bar.price_type,
                open_time=bar.open_time,
                close_time=bar.close_time,
                policy_id=bar.policy_id,
                policy_version=bar.policy_version,
                version_number=bar.version_number,
                semantic_hash=bar.semantic_hash,
                semantic_available_at=bar.semantic_available_at,
                evaluation_keys=evaluation_keys,
                consumed_at=batch.available_as_of,
            )
            consumed += 1
        self._repository.advance_platform_watermark(
            watermark_canonical_bar_id=batch.watermark_canonical_bar_id,
            instrument_master_checksum=batch.instrument_master_checksum,
            session_calendar_checksum=batch.session_calendar_checksum,
            timezone_data_version=batch.timezone_data_version,
            updated_at=batch.available_as_of,
        )
        return PlatformProcessingResult(
            previous_watermark=previous_watermark,
            new_watermark=batch.watermark_canonical_bar_id,
            consumed=consumed,
            replayed=replayed,
            revisions_detected=revisions,
            ignored_ask_or_mid=ignored_prices,
            ignored_non_strategy_timeframes=ignored_non_strategy,
        )


def _evaluation_keys(value: str | tuple[str, ...] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, tuple) or any(not item for item in value):
        raise TypeError("process_bar must return None, a key, or a tuple of keys")
    return value
