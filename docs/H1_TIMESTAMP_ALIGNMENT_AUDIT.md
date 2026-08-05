# H1 extrema-source timestamp alignment audit

Date: 2026-08-05. Instrument: EUR/USD. Verdict: canonical data is correct; the compact dashboard presentation was ambiguous and showed the close as the only source-candle timestamp.

## Disputed evaluation and candle

- Evaluation ID: `SPECT8_MICRO_DAILY_V1_0:TWELVE_DATA:EUR/USD:H1:2026-08-05T09:00:00Z`
- Source case: `twelve_data:EUR/USD:H1:2026-08-05T09:00:00Z`
- Recent high: `1.15462`
- Source ID: `TWELVE_DATA:EUR/USD:H1:2026-08-05T08:00:00+00:00`
- Canonical open/close: `2026-08-05T07:00:00Z` / `2026-08-05T08:00:00Z`
- Broker open/close: `2026-08-05 10:00` / `2026-08-05 11:00`
- Spect8 OHLC: `1.15449 / 1.15462 / 1.15343 / 1.15393`
- IC Markets MT5 OHLC for the same interval: `1.15448 / 1.15467 / 1.15339 / 1.15380`

The small OHLC differences are normal provider-price differences. The interval identity is exact.

## Layer trace

| Layer | Field | Stored value | Meaning | Expected | Match |
|---|---|---|---|---|---|
| MT5 Python rate | `time` | broker wall label 2026-08-05 10:00 | MT5 H1 open | 10:00 broker | Yes |
| Twelve Data payload | `datetime` | 2026-08-05 07:00:00 | provider H1 open in UTC | 07:00 UTC | Yes |
| Raw candle | `raw_open_time` | 2026-08-05 07:00:00 | provider open | 07:00 UTC | Yes |
| Raw candle | `raw_close_time` | 2026-08-05T08:00:00Z | calculated H1 close | 08:00 UTC | Yes |
| Normalizer | `open_time` | 2026-08-05T07:00:00+00:00 | canonical open | 07:00 UTC | Yes |
| Normalizer | `close_time` | 2026-08-05T08:00:00+00:00 | canonical close | 08:00 UTC | Yes |
| SQLite | `open_time_utc` | 2026-08-05T07:00:00Z | canonical open | 07:00 UTC | Yes |
| SQLite | `close_time_utc` | 2026-08-05T08:00:00Z | canonical close | 08:00 UTC | Yes |
| Evaluator | extrema source object | exact selected `Bar` | source for `recent_high` | same candle | Yes |
| Filter Audit | `recent_high_bar_open_time` | 2026-08-05T07:00:00Z | source open | 07:00 UTC | Yes |
| Filter Audit | `recent_high_bar_close_time` | 2026-08-05T08:00:00Z | source close | 08:00 UTC | Yes |
| Typed API | explicit open/close fields | 07:00Z / 08:00Z | canonical source interval | 07:00Z / 08:00Z | Yes |
| Formatter | separate input instants | 10:00 / 11:00 broker | display conversion once | 10:00 / 11:00 | Yes |
| Dashboard before fix | close field only | 11:00 broker | ambiguously labelled source candle | both fields | No |
| Dashboard after fix | backend open and close | 10:00 / 11:00 broker | explicit source interval | both fields | Yes |

`timestamp`, `bar_time`, and `recent_high_bar_time` are not used in this path. `evaluation_time` is processing time (`2026-08-05T09:20:56.104313Z`), not a candle timestamp.

## Exact comparison

| Field | IC Markets MT5 | Spect8 DB | Typed API | Dashboard after fix |
|---|---|---|---|---|
| Open Broker Time | 10:00 | derived 10:00 | derived 10:00 | 10:00 IC Markets Broker Time |
| Close Broker Time | 11:00 | derived 11:00 | derived 11:00 | 11:00 IC Markets Broker Time |
| Open UTC | 07:00 | 07:00 | 07:00 | Open 07:00 UTC |
| Close UTC | 08:00 | 08:00 | 08:00 | Close 08:00 UTC |
| Open | 1.15448 | 1.15449 | 1.15449 | 1.15449 in exact-bars evidence |
| High | 1.15467 | 1.15462 | 1.15462 | 1.15462, recent high |
| Low | 1.15339 | 1.15343 | 1.15343 | 1.15343 in exact-bars evidence |
| Close | 1.15380 | 1.15393 | 1.15393 | 1.15393 in exact-bars evidence |
| Recent-high source | same interval | true | true | marked Recent high |

## Surrounding completed bars

All available completed bars surrounding the disputed candle align by interval. Five later completed bars do not yet exist at audit time; only the 11:00-12:00 broker bar is completed after it, and the 12:00-13:00 bar was forming and excluded. The month-wide comparison below supplies the broader shift check.

| Relative | Broker interval | UTC interval | MT5 high | Spect8 high | Timestamp match |
|---:|---|---|---:|---:|---|
| -6 | 04:00-05:00 | 01:00-02:00 | 1.15318 | 1.15318 | Yes |
| -5 | 05:00-06:00 | 02:00-03:00 | 1.15406 | 1.15409 | Yes |
| -4 | 06:00-07:00 | 03:00-04:00 | 1.15405 | 1.15407 | Yes |
| -3 | 07:00-08:00 | 04:00-05:00 | 1.15402 | 1.15406 | Yes |
| -2 | 08:00-09:00 | 05:00-06:00 | 1.15382 | 1.15385 | Yes |
| -1 | 09:00-10:00 | 06:00-07:00 | 1.15459 | 1.15458 | Yes |
| disputed | 10:00-11:00 | 07:00-08:00 | 1.15467 | 1.15462 | Yes |
| +1 | 11:00-12:00 | 08:00-09:00 | 1.15444 | 1.15445 | Yes |

## One-month structural result

For 2026-07-05 through the disputed completed candle, IC Markets and Spect8 each contain 539 H1 timestamps: 539 shared, zero reference-only, zero Spect8-only, zero duplicates, and zero weekend opens. All broker opening hours 00 through 23 occur only at minute 00. Therefore there is no systematic one-hour shift and no stale canonical timestamp migration is required.

## Root cause and fix

`frontend/components/dashboard.tsx` used `recent_high_bar_close_time` and `recent_low_bar_close_time` as the sole values in compact cells labelled “source candle.” No API field was mis-mapped and no timezone was applied twice. The fix renders the backend-provided open and close fields separately and adds a combined UTC line. The frontend does not add or subtract a duration.
