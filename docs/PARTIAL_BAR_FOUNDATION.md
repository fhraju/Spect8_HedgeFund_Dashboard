# Partial Bar Foundation — FORMING Signal Data

## Status

Operational cross-process partial consumption and startup replay. Completed
canonical history and frozen strategy calculations are untouched.

## Incomplete vs completed

* **Completed** `Bar(is_complete=True)` — persisted canonical `M30/H1/H4/D1/W1`
  from Market Data Platform PostgreSQL (`market_data` IG_DEMO, `market_data_live`
  IG_LIVE) via `PlatformAuthorityRuntime`. Append-only, watermarked, checksummed.
* **Partial** `CurrentBarSnapshot(is_complete=False)` — read-only forming view
persisted and owned by the Platform from available M5 components. Explicit type with
`is_complete=False` invariant; `to_spect8_bar()` produces a `Bar` with
`is_complete=False` that is never written to canonical tables. Callers must
check `is_complete`/`is_partial` before treating as confirmed.
  Partial OHLC is already normalized by the Platform's canonical IG FX rule;
  provider-native point levels remain audit-only provenance.

## Aggregation source

* **M30 forming**: 1..5 completed M5 (`IGChartCandle5M` with `CONS_END=1`) within
  the 30-min window, aggregated `open=first.open`, `high=max(high)`,
  `low=min(low)`, `close=last.close`. No future M5 (close > as_of) is used.
  Implemented in `hedgefund_market_data.domain.partial.partial_m30_from_m5` and
  exposed via `IGM5ToM30Assembler.forming_partial` and persisted by the collector.
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

## Restart and startup replay

Platform replace-upserts partial M30/H1 into PostgreSQL and clears each row at
the corresponding completed boundary. Spect8 reads it with
`spect8_market_data_reader`; Spect8 never writes partial market data. If a
collector restart loses the beginning of a window, state is truthfully absent
until a clean boundary rather than reconstructed from missing M5 members.

On each Spect8 process start, the authority runtime performs one bounded local
history read, loads 30 completed warm-up bars before eligible current-New-York-
day closes, and evaluates Micro M30/H1 plus Macro H1/H4 chronologically. Event
and confirmed-signal identities remain idempotent across repeated starts. This
path makes no historical provider REST calls.

## Freshness

Partials share Platform authority freshness. `LIVE_READY` requires both recent
canonical M30 advancement and current persisted BID M30/H1 snapshots;
`STALE`/`UNAVAILABLE`/missing-prefix state suppresses FORMING. The non-persisting
FORMING pass uses the production evaluator with 29 completed inputs plus the
real partial snapshot. No automatic Twelve Data fallback occurs.

## Provenance

`component_ids`/`component_source_ids` retain each M5 open time and `M5:open`
identity; `source_provider_id` preserves `IG_DEMO` vs `IG_LIVE` separation.
Sufficient for deterministic replay and audit without confusing incomplete with
completed.
