from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.app.market_data.forex_profile import (
    BROKER_DISPLAY_LABEL,
    broker_wall_time,
    broker_wall_to_utc,
)


TIMEFRAMES = {
    "H1": ("TIMEFRAME_H1", timedelta(hours=1)),
    "H4": ("TIMEFRAME_H4", timedelta(hours=4)),
    "D1": ("TIMEFRAME_D1", timedelta(days=1)),
}
FIELDS = (
    "broker",
    "server",
    "symbol",
    "canonical_symbol",
    "timeframe",
    "bar_index",
    "broker_open_time",
    "broker_close_time",
    "utc_open_time",
    "utc_close_time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "real_volume",
    "spread",
    "completed",
    "weekend",
    "source",
)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _broker_label(value: datetime) -> str:
    return f"{value:%Y-%m-%d %H:%M:%S} {BROKER_DISPLAY_LABEL}"


def _wall_from_epoch(value: int) -> datetime:
    return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)


def _close_wall(open_wall: datetime, timeframe: str) -> datetime:
    if timeframe == "D1":
        return (open_wall + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    return open_wall + TIMEFRAMES[timeframe][1]


def _detect_symbol(mt5: Any) -> str:
    candidates = []
    for info in mt5.symbols_get() or ():
        normalized = "".join(
            character for character in info.name.upper() if character.isalpha()
        )
        if normalized.startswith("EURUSD"):
            candidates.append(info)
    if not candidates:
        raise RuntimeError("IC Markets terminal has no EUR/USD symbol")
    candidates.sort(
        key=lambda info: (info.name != "EURUSD", not info.visible, info.name)
    )
    return candidates[0].name


def export(
    *,
    terminal: Path,
    output: Path,
    start_utc: datetime,
    end_utc: datetime,
) -> dict[str, object]:
    import MetaTrader5 as mt5

    if not mt5.initialize(path=str(terminal), timeout=60_000):
        raise RuntimeError(f"MetaTrader5 initialize failed: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        terminal_info = mt5.terminal_info()
        if account is None or terminal_info is None or not terminal_info.connected:
            raise RuntimeError("IC Markets terminal has no connected existing session")
        symbol = _detect_symbol(mt5)
        broker_now = broker_wall_time(end_utc)
        request_start = broker_wall_time(start_utc).replace(tzinfo=timezone.utc)
        request_end = broker_now.replace(tzinfo=timezone.utc)
        output.mkdir(parents=True, exist_ok=True)
        counts: dict[str, int] = {}
        for timeframe, (constant, _duration) in TIMEFRAMES.items():
            rates = mt5.copy_rates_range(
                symbol,
                getattr(mt5, constant),
                request_start,
                request_end,
            )
            if rates is None:
                raise RuntimeError(f"{timeframe} export failed: {mt5.last_error()}")
            rows = []
            for rate in rates:
                open_wall = _wall_from_epoch(int(rate["time"]))
                close_wall = _close_wall(open_wall, timeframe)
                utc_open = broker_wall_to_utc(open_wall)
                utc_close = broker_wall_to_utc(close_wall)
                completed = utc_close <= end_utc
                if not completed or utc_close <= start_utc:
                    continue
                rows.append(
                    {
                        "broker": account.company,
                        "server": account.server,
                        "symbol": symbol,
                        "canonical_symbol": "EUR/USD",
                        "timeframe": timeframe,
                        "bar_index": len(rows) + 1,
                        "broker_open_time": _broker_label(open_wall),
                        "broker_close_time": _broker_label(close_wall),
                        "utc_open_time": _iso(utc_open),
                        "utc_close_time": _iso(utc_close),
                        "open": str(rate["open"]),
                        "high": str(rate["high"]),
                        "low": str(rate["low"]),
                        "close": str(rate["close"]),
                        "tick_volume": int(rate["tick_volume"]),
                        "real_volume": int(rate["real_volume"]),
                        "spread": int(rate["spread"]),
                        "completed": "true",
                        "weekend": str(open_wall.weekday() >= 5).lower(),
                        "source": "IC_MARKETS_MT5_PYTHON",
                    }
                )
            target = output / f"EURUSD_ICMARKETS_{timeframe}_20260705_20260805.csv"
            with target.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            counts[timeframe] = len(rows)
        return {
            "terminal_build": terminal_info.build,
            "server_environment": ("DEMO" if account.trade_mode == 0 else "LIVE"),
            "symbol": symbol,
            "broker_utc_offset_hours": int(
                (broker_now - end_utc.replace(tzinfo=None)).total_seconds() // 3600
            ),
            "counts": counts,
        }
    finally:
        mt5.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2026-07-05T00:00:00Z")
    parser.add_argument("--end")
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    end = (
        datetime.fromisoformat(args.end.replace("Z", "+00:00"))
        if args.end
        else datetime.now(timezone.utc)
    )
    report = export(
        terminal=args.terminal.resolve(),
        output=args.output.resolve(),
        start_utc=start,
        end_utc=end,
    )
    for key, value in report.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
