from __future__ import annotations

from typing import Any


def projected_request_usage(instruments: int) -> dict[str, Any]:
    if instruments < 1:
        raise ValueError("instrument count must be positive")
    per_instrument = {"H1": 24, "H4": 6, "D1": 1}
    by_timeframe = {
        timeframe: count * instruments
        for timeframe, count in per_instrument.items()
    }
    per_day = sum(by_timeframe.values())
    return {
        "instruments": instruments,
        "requests_per_day": per_day,
        "average_requests_per_hour": per_day / 24,
        "requests_by_timeframe_per_day": by_timeframe,
        "assumptions": (
            "One successful boundary fetch per completed H1/H4/D1 series; "
            "retries and startup catch-up are additional."
        ),
    }


def expansion_projections() -> dict[str, dict[str, Any]]:
    return {
        str(count): projected_request_usage(count)
        for count in (1, 3, 10, 25, 50)
    }
