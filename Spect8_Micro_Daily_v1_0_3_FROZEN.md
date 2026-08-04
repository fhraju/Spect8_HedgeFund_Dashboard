# Spect8 Micro Daily Market Scanner

## Frozen New York-close Daily Authority v1.0.3

| Field | Value |
|---|---|
| Authority ID | `SPECT8_MICRO_DAILY_V1_0_3` |
| Status | **FROZEN** |
| Freeze date | 2026-08-04 |
| Base authority | `Spect8_Micro_Daily_v1_0_FROZEN.md` |
| Prior clarification | `Spect8_Micro_Daily_v1_0_2_FROZEN.md` |
| Calculation strategy ID | `SPECT8_MICRO_DAILY_V1_0` |
| Scope | Canonical D1 session construction and provenance |

This authority supplements the prior frozen authorities only for canonical D1
construction. The v1.0.1 simultaneous-direction and v1.0.2 completed-as-of-
close rules remain in force. All Filter arithmetic, Signal, levels, sizing,
risk, order, fill, independence, and exclusion formulas remain unchanged.

The calculation strategy ID remains `SPECT8_MICRO_DAILY_V1_0`; the active
frozen specification version is `SPECT8_MICRO_DAILY_V1_0_3`.

## 1. Canonical Daily session

The canonical Forex D1 session is:

```text
session timezone = America/New_York
session start    = 17:00 America/New_York on the prior trading session date
session end      = 17:00 America/New_York on the current trading session date
```

The boundary is calculated with the IANA `America/New_York` timezone. Its UTC
representation changes with New York daylight saving time, normally closing at
21:00 UTC in daylight time and 22:00 UTC in standard time. Fixed UTC offsets
are not authoritative.

## 2. Intraday aggregation authority

Canonical Daily candles are aggregated from completed canonical intraday
candles, with H1 as the required production source. Eligible source membership
is:

```text
session_start <= intraday.open_time
AND intraday.close_time <= session_end
```

OHLCV is constructed as follows:

```text
open   = first eligible H1 open
high   = maximum eligible H1 high
low    = minimum eligible H1 low
close  = last eligible H1 close
volume = sum of eligible volume only when every source volume is available
```

Provider-native Daily OHLC must not be used unless independently proven to use
the identical New York-close session. Provider-native Daily timestamps must not
be relabeled because that would preserve OHLC from the wrong period.

## 3. Completeness and provenance

A canonical D1 is complete only after its session end has passed and every
required source candle is complete, ordered, unique, non-overlapping, and gives
the expected session coverage. Missing or invalid source coverage quarantines
that session; no OHLC is forward-filled, invented, or replaced by a native D1.
Weekend closure outside active Forex sessions is not itself missing coverage.

Persisted canonical D1 rows retain provider/instrument provenance, exact UTC
open and close timestamps, and `session_timezone = America/New_York`.

## 4. Strategy eligibility and unchanged Filter

For signal close `T`, a D1 is eligible exactly when:

```text
d1_bar.is_complete = true
AND d1_bar.close_time <= T
```

A completed D1 candle closing exactly with the evaluated H1 or H4 candle is
eligible. Live production and historical replay must use the same boundary and
aggregation implementation.

The unchanged Filter remains latest two eligible D1 low/high, Wilder D1 ATR(5),
five-percent ATR buffer, latest 21 signal-timeframe low/high, inclusive BUY
`<=`, and inclusive SELL `>=`.

## 5. Traceability and freeze declaration

No earlier frozen authority is rewritten. Version 1.0.3 supersedes only native
or midnight-UTC D1 construction for the active strategy input. Prior files and
their historical golden cases remain traceable artifacts.
