from __future__ import annotations

import os
from datetime import datetime, timezone

from ..domain import Timeframe
from .models import MarketDataProviderError
from .normalizer import CandleNormalizer
from .twelve_data_provider import SUPPORTED_TIMEFRAMES, TwelveDataProvider

REQUIRED_HISTORY = {
    Timeframe.H1: 30,
    Timeframe.H4: 30,
    Timeframe.D1: 6,
}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    api_key = os.environ.get("TWELVE_DATA_API_KEY")
    if not api_key:
        print("NOT RUN — API key unavailable")
        return 0

    provider = TwelveDataProvider(api_key)
    instrument = provider.discover_instruments()[0]
    normalizer = CandleNormalizer()
    as_of = datetime.now(timezone.utc)
    try:
        for timeframe in SUPPORTED_TIMEFRAMES:
            raw = provider.fetch_smoke_bars(timeframe, as_of)
            canonical = []
            for candle in raw:
                result = normalizer.normalize(candle, instrument)
                if result.candle is None:
                    raise RuntimeError("canonical normalization failed")
                canonical.append(result.candle)
            if any(
                not candle.is_complete or candle.close_time >= as_of
                for candle in canonical
            ):
                raise RuntimeError("incomplete candle reached normalization")
            if any(
                current.open_time <= previous.open_time
                for previous, current in zip(canonical, canonical[1:])
            ):
                raise RuntimeError("candles are not chronologically ordered")
            required = REQUIRED_HISTORY[timeframe]
            if len(canonical) < required:
                raise RuntimeError("required completed history is unavailable")
            diagnostics = provider.diagnostics(timeframe)
            first = _iso(canonical[0].open_time) if canonical else "none"
            last = _iso(canonical[-1].close_time) if canonical else "none"
            print(
                " | ".join(
                    (
                        "provider=TWELVE_DATA",
                        "instrument=EUR/USD",
                        f"timeframe={timeframe.value}",
                        f"raw={diagnostics.received_count}",
                        f"completed={len(canonical)}",
                        f"forming_filtered={diagnostics.forming_filtered_count}",
                        "malformed=0",
                        f"first_open_utc={first}",
                        f"last_close_utc={last}",
                        "chronological=true",
                        f"duplicates={diagnostics.duplicate_count}",
                        f"gaps={diagnostics.gap_count}",
                        "normalizer=PASS",
                        f"required_history={len(canonical)}/{required}",
                        "all_close_lt_asof=true",
                        f"health={provider.health(as_of).state.value}",
                    )
                )
            )
    except (MarketDataProviderError, RuntimeError) as error:
        code = (
            error.code.value
            if isinstance(error, MarketDataProviderError)
            else "VALIDATION"
        )
        print(
            " | ".join(
                (
                    "provider=TWELVE_DATA",
                    "instrument=EUR/USD",
                    f"health={provider.health(as_of).state.value}",
                    f"result=FAIL",
                    f"reason={code}",
                )
            )
        )
        return 1
    print("result=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
