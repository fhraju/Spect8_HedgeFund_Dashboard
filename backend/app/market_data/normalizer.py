from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..domain import Bar
from .models import (
    CanonicalInstrument,
    NormalizationResult,
    RawProviderCandle,
    TimestampSemantics,
)
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID


def _utc(value: str, session_timezone: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(session_timezone))
        except ZoneInfoNotFoundError as error:
            raise ValueError(
                f"unknown provider session timezone: {session_timezone}"
            ) from error
    return parsed.astimezone(timezone.utc)


class CandleNormalizer:
    def normalize(
        self,
        raw: RawProviderCandle,
        instrument: CanonicalInstrument,
    ) -> NormalizationResult:
        issues: list[str] = []
        if raw.provider_id != instrument.provider_id:
            issues.append("PROVIDER_MISMATCH")
        if raw.provider_symbol != instrument.provider_symbol:
            issues.append("INSTRUMENT_MISMATCH")
        if raw.session_timezone != instrument.session_timezone:
            issues.append("SESSION_TIMEZONE_MISMATCH")
        if raw.timeframe not in instrument.available_timeframes:
            issues.append("TIMEFRAME_UNAVAILABLE")
        if not raw.is_complete:
            issues.append("INCOMPLETE_CANDLE")

        try:
            if raw.open_time_utc is not None or raw.close_time_utc is not None:
                if (
                    raw.open_time_utc is None
                    or raw.close_time_utc is None
                    or raw.timestamp_semantics is TimestampSemantics.UNKNOWN
                ):
                    raise ValueError("explicit timestamps require declared semantics")
                open_time = raw.open_time_utc.astimezone(timezone.utc)
                close_time = raw.close_time_utc.astimezone(timezone.utc)
            else:
                # Compatibility path for frozen replay/test fixtures created
                # before explicit provider timestamp semantics existed.
                open_time = _utc(raw.raw_open_time, raw.session_timezone)
                close_time = _utc(raw.raw_close_time, raw.session_timezone)
        except (ValueError, TypeError):
            issues.append("INVALID_TIMESTAMP")
            open_time = close_time = None
        if open_time is not None and close_time is not None:
            if close_time <= open_time:
                issues.append("INVALID_TIMESTAMP_RANGE")

        raw_prices = (raw.open, raw.high, raw.low, raw.close)
        try:
            prices = tuple(Decimal(value) for value in raw_prices)
        except (InvalidOperation, TypeError, ValueError):
            issues.append("INVALID_PRICE")
            prices = ()
        prices_are_valid = bool(prices) and not any(
            not price.is_finite() or price <= Decimal("0") for price in prices
        )
        if prices and not prices_are_valid:
            issues.append("INVALID_PRICE")
        if prices_are_valid:
            open_price, high, low, close = prices
            if (
                low > high
                or low > open_price
                or low > close
                or high < open_price
                or high < close
            ):
                issues.append("INVALID_OHLC")

        volume: Decimal | None = None
        if raw.volume not in (None, ""):
            try:
                volume = Decimal(raw.volume)
            except (InvalidOperation, TypeError, ValueError):
                issues.append("INVALID_VOLUME")
            else:
                if not volume.is_finite() or volume < Decimal("0"):
                    issues.append("INVALID_VOLUME")

        if issues:
            return NormalizationResult(
                candle=None,
                issues=tuple(sorted(set(issues))),
            )

        assert open_time is not None
        assert close_time is not None
        open_price, high, low, close = prices
        quantum = Decimal("1").scaleb(-instrument.price_precision)

        def normalized(value: Decimal) -> Decimal:
            return value.quantize(quantum, rounding=ROUND_HALF_EVEN)

        return NormalizationResult(
            candle=Bar(
                instrument_id=instrument.instrument_id,
                timeframe=raw.timeframe,
                open_time=open_time,
                close_time=close_time,
                open=normalized(open_price),
                high=normalized(high),
                low=normalized(low),
                close=normalized(close),
                provider=instrument.provider_id,
                is_complete=True,
                volume=volume,
                session_timezone=raw.session_timezone,
                raw_provider_symbol=raw.provider_symbol,
                raw_open_time=raw.raw_open_time,
                raw_close_time=raw.raw_close_time,
                raw_open=raw.open,
                raw_high=raw.high,
                raw_low=raw.low,
                raw_close=raw.close,
                synthetic=instrument.synthetic,
                quality_status="VALID",
                construction_profile_version=(
                    PROFILE_ID
                    if (
                        raw.timeframe.value == "H1"
                        and instrument.asset_class == "FOREX"
                        and not instrument.synthetic
                    )
                    else "PROVIDER_NATIVE_COMPARISON_ONLY"
                ),
                provider_adapter_version=raw.adapter_version,
                source_timeframe=raw.source_timeframe or raw.timeframe,
                source_candle_ids=(
                    raw.source_id
                    or (
                        f"{raw.provider_id}:{instrument.instrument_id}:"
                        f"{raw.timeframe.value}:{close_time.isoformat()}"
                    ),
                ),
                forward_filled=False,
                ingestion_run_id=(
                    str(raw.provider_metadata.get("ingestion_run_id"))
                    if raw.provider_metadata
                    and raw.provider_metadata.get("ingestion_run_id")
                    else None
                ),
                created_at=raw.received_at or close_time,
            ),
            issues=(),
        )
