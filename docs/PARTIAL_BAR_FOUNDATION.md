# Partial Bar Foundation — FORMING Signal Data

## Status

Foundation only. No Micro/Macro signal conditions, no UI, no timers. Completed
canonical history is untouched.

## Incomplete vs completed

* **Completed** `Bar(is_complete=True)` — persisted canonical `M30/H1/H4/D1/W1`
  from Market Data Platform PostgreSQL (`market_data` IG_DEMO, `market_data_live`
  IG_LIVE) via `PlatformAuthorityRuntime`. Append-only, watermarked, checksummed.
* **Partial** `CurrentBarSnapshot(is_complete=False)` — ephemeral forming view
  derived from already-available lower-timeframe components. Explicit type with
  `is_complete=False` invariant; `to_spect8_bar()` produces a `Bar` with
  `is_complete=False` that is never written to canonical tables. Callers must
  check `is_complete`/`is_partial` before treating as confirmed.

## Aggregation source

* **M30 forming**: 1..5 completed M5 (`IGChartCandle5M` with `CONS_END=1`) within
  the 30-min window, aggregated `open=first.open`, `high=max(high)`,
  `low=min(low)`, `close=last.close`. No future M5 (close > as_of) is used.
  Implemented in `hedgefund_market_data.domain.partial.partial_m30_from_m5` and
  exposed via `IGM5ToM30Assembler.forming_partial` (in-memory buffer).
* **H1 forming**: at most one completed M30 + one forming M30 within the hour.
  `open=first.open`, `high=max`, `low=min`, `close=last.close`. See
  `partial_h1_from_m30` (Platform) and `aggregate_partial_h1` (Spect8).
* At H1/M30 close the forming snapshot disappears and the next canonical
  completed bar (via `IncrementalPollingCycle` → `canonical_bars`) becomes
  authoritative.

## Broker H4 forming

Platform canonical UTC H4 (`AggregationPolicy` on M30) is not changed.
Spect8 broker H4 (00/04/08/12/16/20 broker wall time, DST via
`broker_wall_time`) is rebuilt from H1:

```
completed H1 (BID, market_h1_bars)
+ forming H1 (CurrentBarSnapshot, if in current H4 bucket)
  -> broker_partial_h4_from_h1_snapshots -> partial H4 (is_complete=False)
```

`BrokerAlignedH4Aggregator` gives completed H4 (4×H1, bucket_close <= as_of);
the partial helper adds the current bucket's 1-3 H1s as a forming H4. Provenance
is `source_candle_ids` of contributing H1s. Platform H4 rows are ignored.

## Restart

Partial is not persisted. After restart the M5 buffer is empty until new
Lightstreamer updates arrive; recomputation from the same available components
yields identical OHLC (deterministic, tested). No duplicate canonical is
created.

## Freshness

Partials share Platform authority freshness: `HEALTHY` only when
`PlatformAuthorityRuntime` is `HEALTHY`; `STALE`/`UNAVAILABLE` must suppress
the forming candle (no live display). Tests verify `STALE` blocks partial
exposure. No automatic Twelve Data fallback when Platform is authoritative.

## Provenance

`component_ids`/`component_source_ids` retain each M5 open time and `M5:open`
identity; `source_provider_id` preserves `IG_DEMO` vs `IG_LIVE` separation.
Sufficient for deterministic replay and audit without confusing incomplete with
completed.
