# Phase 2B Market-Data Foundation

Phase 2B adds a provider-neutral, deterministic market-data path around the
existing production `StrategyEvaluator`. It does not add a real provider,
scheduler, WebSocket, strategy formula, trading action, or frontend redesign.

## Runtime boundary

The normal application path is:

```text
FixedClock
  -> ReplayMarketDataProvider
  -> CanonicalInstrumentRegistry
  -> CandleNormalizer
  -> ClosedBarDetector
  -> SQLite canonical_bars
  -> MarketDataCoordinator
  -> production StrategyEvaluator
  -> event/status SQLite projections
  -> protected FastAPI
  -> protected Next.js dashboard
```

`ReplayMarketDataProvider` reads only the committed manifest, candle CSV files,
and instrument metadata. It does not read `expected.json`,
`calculation_ledger.md`, or import the golden reference calculator. The
production evaluator receives only canonical `Bar`, `InstrumentMetadata`, and
`StrategyRequest` objects.

## Provider contract and canonical instruments

`MarketDataProvider` supplies:

- immutable provider identity;
- instrument discovery/configuration;
- completed-bar discovery at an explicit clock time;
- required signal and D1 history for a closed bar;
- provider health and freshness.

The registry maps `(provider_id, instrument_id)` to an immutable
`CanonicalInstrument`. The selected replay instrument advertises H1, H4 and D1
availability and carries provider symbol, asset class, point/tick/precision,
contract bounds, quote/profit currencies, provider session timezone, and candle
boundary convention.

## Canonical candle example

The normalizer preserves the source strings alongside normalized UTC and
precision values. A representative persisted record is:

```json
{
  "provider": "SYNTHETIC_UTC_V1",
  "instrument_id": "SYNTH_XAUUSD",
  "timeframe": "H1",
  "raw_open_time": "2026-02-03T10:00:00Z",
  "raw_close_time": "2026-02-03T11:00:00Z",
  "open_time_utc": "2026-02-03T10:00:00Z",
  "close_time_utc": "2026-02-03T11:00:00Z",
  "session_timezone": "UTC",
  "raw_provider_symbol": "SYNTH_XAUUSD",
  "synthetic": true
}
```

OHLC values must be finite and positive, obey `low <= open/close <= high`, and
have a valid increasing timestamp range. Values are rounded to the configured
display precision only after validation; the raw strings remain persisted as
evidence.

## Completed bars and alignment

The selected cases are:

- `confirmed_buy_h1_01`, evaluated at `2026-02-03T11:00:01Z`;
- `confirmed_sell_h4_01`, evaluated at `2026-02-07T20:00:01Z`.

At the H1 evaluation time, the later H4 trigger is not visible. Signal history
is restricted to close times at or before its trigger, while completed D1
history is restricted to close times at or before the signal trigger. At the H4 evaluation time, its H4
stream is evaluated independently. Incomplete signal or D1 rows are excluded
before normalization and cannot become a `BAR_CLOSED` event.

The detector reports duplicate, out-of-order, missing, incomplete, look-ahead,
and insufficient-history conditions. Invalid streams are quarantined before
the strategy evaluator is called.

## Event trace

Each selected confirmed case produces:

```text
BAR_CLOSED
FILTER_EVALUATED
FILTER_MATCHED
SIGNAL_EVALUATED
SIGNAL_CONFIRMED
LEVELS_CALCULATED
STATUS_PROJECTED
```

The two independent projections therefore produce 14 ordered events. The
SQLite processed-bar key prevents the same closed candle from producing events
again in the same process or after restart.

## Persistence and health

Canonical bar uniqueness is:

```text
provider + instrument_id + timeframe + close_time_utc
```

The database retains normalized and raw timestamps, raw provider symbol,
provider session timezone, normalized OHLC, volume, and raw evidence. Replaying
the two selected cases persists 80 unique bars: 35 H1, 35 H4, and 10 shared D1.

Provider health is persisted as one of:

- `HEALTHY`
- `STALE`
- `DATA_UNAVAILABLE`
- `INSUFFICIENT_HISTORY`
- `QUARANTINED`
- `RECOVERED`

`FixedClock` drives deterministic replay and health tests. `SystemClock` is
available for a later live adapter; strategy code never reads system time
directly.
