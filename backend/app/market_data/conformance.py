from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from ..domain import Bar, Timeframe
from .daily_aggregator import NewYorkDailyAggregator
from .forex_profile import (
    BrokerAlignedH4Aggregator,
    GapType,
    classify_market_gap,
    is_valid_market_h1,
    market_h1_bars,
)
from .models import (
    CanonicalInstrument,
    ProviderProfile,
    RawProviderCandle,
    TimestampSemantics,
)
from .normalizer import CandleNormalizer
from .profiles.ic_markets_ny_close_forex_v1 import PROFILE_ID


@dataclass(frozen=True, slots=True)
class CertificationReport:
    provider: str
    instrument: str
    profile: str
    start_utc: str
    end_utc: str
    raw_h1_count: int
    canonical_h1_count: int
    canonical_h4_count: int
    canonical_d1_count: int
    excluded_raw_session_count: int
    expected_closure_count: int
    unexpected_gap_count: int
    duplicate_count: int
    weekend_bar_count: int
    incomplete_h4_count: int
    incomplete_d1_count: int
    provenance_complete: bool
    structure_digest: str
    certified: bool
    issues: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


class FixtureProviderAdapter:
    def __init__(self, fixture_path: Path) -> None:
        self.fixture_path = fixture_path
        self.payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    def provider_profile(self) -> ProviderProfile:
        return ProviderProfile(
            provider_name="IC_MARKETS_REFERENCE",
            adapter_version="fixture-v1",
            timestamp_semantics=TimestampSemantics.INTERVAL_START,
            native_timeframes=(Timeframe.H1,),
        )

    @staticmethod
    def map_symbol(canonical_instrument: str) -> str:
        if canonical_instrument != "EUR/USD":
            raise ValueError("fixture supports only EUR/USD")
        return "EURUSD"

    def fetch_raw_candles(
        self,
        instrument: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[RawProviderCandle, ...]:
        self.map_symbol(instrument)
        if timeframe is not Timeframe.H1:
            raise ValueError("fixture exposes canonical-base H1 only")
        values = []
        for row in self.payload["bars"]:
            open_time = _utc(row["utc_open_time"])
            close_time = _utc(row["utc_close_time"])
            if close_time <= start or close_time > end:
                continue
            source_id = f"ic-markets-reference:EUR/USD:H1:{row['utc_close_time']}"
            values.append(
                RawProviderCandle(
                    provider_id="IC_MARKETS_REFERENCE",
                    provider_symbol="EURUSD",
                    timeframe=Timeframe.H1,
                    raw_open_time=row["utc_open_time"],
                    raw_close_time=row["utc_close_time"],
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["tick_volume"],
                    is_complete=True,
                    session_timezone="UTC",
                    provider_name="IC_MARKETS_REFERENCE",
                    canonical_instrument="EUR/USD",
                    source_timeframe=Timeframe.H1,
                    provider_timestamp=row["utc_open_time"],
                    timestamp_semantics=TimestampSemantics.INTERVAL_START,
                    open_time_utc=open_time,
                    close_time_utc=close_time,
                    source_id=source_id,
                    received_at=close_time,
                    provider_metadata={
                        "fixture_sha256": fixture_sha256(self.fixture_path),
                        "ingestion_run_id": "ic-markets-fixture-v1",
                    },
                    adapter_version="fixture-v1",
                )
            )
        return tuple(values)

    @staticmethod
    def normalize_timestamp(
        provider_timestamp: str,
        semantics: TimestampSemantics,
        timeframe: Timeframe,
    ) -> tuple[datetime, datetime]:
        if timeframe is not Timeframe.H1:
            raise ValueError("fixture exposes H1 only")
        value = _utc(provider_timestamp)
        if semantics in (
            TimestampSemantics.OPEN_TIME,
            TimestampSemantics.INTERVAL_START,
        ):
            from datetime import timedelta

            return value, value + timedelta(hours=1)
        if semantics in (
            TimestampSemantics.CLOSE_TIME,
            TimestampSemantics.INTERVAL_END,
        ):
            from datetime import timedelta

            return value - timedelta(hours=1), value
        raise ValueError("fixture timestamp semantics must be explicit")


class ProviderCertificationEngine:
    def certify(
        self,
        *,
        adapter: object,
        instrument: CanonicalInstrument,
        start: datetime,
        end: datetime,
    ) -> CertificationReport:
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("certification range must be ordered and timezone-aware")
        profile = adapter.provider_profile()
        raw = adapter.fetch_raw_candles(
            instrument.instrument_id, Timeframe.H1, start, end
        )
        normalizer = CandleNormalizer()
        bars: list[Bar] = []
        issues: list[str] = []
        for candle in raw:
            result = normalizer.normalize(candle, instrument)
            issues.extend(result.issues)
            if result.candle is not None:
                bars.append(result.candle)
        canonical = market_h1_bars(tuple(bars))
        identities = [(bar.open_time, bar.close_time) for bar in canonical]
        duplicate_count = len(identities) - len(set(identities))
        canonical_identities = {
            (bar.provider, bar.instrument_id, bar.open_time, bar.close_time)
            for bar in canonical
        }
        excluded_raw_session_count = sum(
            1
            for bar in bars
            if (
                bar.provider,
                bar.instrument_id,
                bar.open_time,
                bar.close_time,
            )
            not in canonical_identities
        )
        weekend_count = sum(1 for bar in canonical if not is_valid_market_h1(bar))
        expected = 0
        unexpected = 0
        for previous, current in zip(canonical, canonical[1:]):
            gap = classify_market_gap(previous, current)
            if gap is None or gap.gap_type is GapType.PROVIDER_PRICE_GAP:
                continue
            if gap.gap_type is GapType.EXPECTED_MARKET_CLOSURE:
                expected += 1
            else:
                unexpected += 1
        h4 = BrokerAlignedH4Aggregator().aggregate(canonical, as_of=end)
        daily = NewYorkDailyAggregator().aggregate(canonical, as_of=end)
        provenance_complete = all(
            bar.construction_profile_version == PROFILE_ID
            and bar.source_timeframe is Timeframe.H1
            and bool(bar.source_candle_ids)
            and not bar.synthetic
            and not bar.forward_filled
            for bar in (*canonical, *h4.bars, *daily.bars)
        )
        observable_h4_issues = (
            tuple(
                issue
                for issue in h4.issues
                if issue.bucket_open >= canonical[0].open_time
                and issue.bucket_close <= canonical[-1].close_time
            )
            if canonical
            else h4.issues
        )
        issues.extend(issue.code.value for issue in observable_h4_issues)
        issues.extend(issue.code.value for issue in daily.issues)
        if profile.timestamp_semantics is TimestampSemantics.UNKNOWN:
            issues.append("UNKNOWN_TIMESTAMP_SEMANTICS")
        if unexpected:
            issues.append("UNEXPECTED_DATA_GAP")
        if duplicate_count:
            issues.append("DUPLICATE_CANDLE")
        if weekend_count:
            issues.append("INVALID_SESSION_CANDLE")
        if not provenance_complete:
            issues.append("INCOMPLETE_PROVENANCE")
        unique_issues = tuple(sorted(set(issues)))
        digest = structure_digest(canonical, h4.bars, daily.bars)
        return CertificationReport(
            provider=profile.provider_name,
            instrument=instrument.instrument_id,
            profile=PROFILE_ID,
            start_utc=_iso(start),
            end_utc=_iso(end),
            raw_h1_count=len(raw),
            canonical_h1_count=len(canonical),
            canonical_h4_count=len(h4.bars),
            canonical_d1_count=len(daily.bars),
            excluded_raw_session_count=excluded_raw_session_count,
            expected_closure_count=expected,
            unexpected_gap_count=unexpected,
            duplicate_count=duplicate_count,
            weekend_bar_count=weekend_count,
            incomplete_h4_count=len(observable_h4_issues),
            incomplete_d1_count=len(daily.issues),
            provenance_complete=provenance_complete,
            structure_digest=digest,
            certified=not unique_issues,
            issues=unique_issues,
        )


def fixture_instrument() -> CanonicalInstrument:
    return CanonicalInstrument(
        instrument_id="EUR/USD",
        provider_id="IC_MARKETS_REFERENCE",
        provider_symbol="EURUSD",
        display_name="Euro / US Dollar",
        asset_class="FOREX",
        point_size=Decimal("0.00001"),
        tick_size=None,
        price_precision=5,
        tick_value_usd=None,
        conversion_rate_to_usd=None,
        contract_min=None,
        contract_max=None,
        contract_step=None,
        minimum_stop_distance_points=None,
        quote_currency="USD",
        profit_currency="USD",
        session_timezone="UTC",
        candle_boundary_convention=PROFILE_ID,
        available_timeframes=(Timeframe.H1,),
        strategy_id="SPECT8_MICRO_DAILY_V1_0",
        synthetic=False,
    )


def structure_digest(h1: Sequence[Bar], h4: Sequence[Bar], daily: Sequence[Bar]) -> str:
    payload = [
        (
            bar.timeframe.value,
            _iso(bar.open_time),
            _iso(bar.close_time),
            len(bar.source_candle_ids),
            bar.session_identifier,
            bar.expected_closure_before,
        )
        for bar in (*h1, *h4, *daily)
    ]
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def fixture_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
