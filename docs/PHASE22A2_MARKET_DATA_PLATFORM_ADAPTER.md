# Phase 22A-2 Market Data Platform adapter foundation

## State and ownership

Phase 22A-2 is a shadow/parity foundation, not a production cutover. The current
Twelve Data provider and coordinator remain the active selectable path. The
Platform path is enabled separately with
`SPECT8_MARKET_DATA_PLATFORM_SHADOW_ENABLED=true` and requires a
`MARKET_DATA_PLATFORM_DATABASE_URL` using `postgresql+psycopg`. Enabling the
shadow flag does not change `SPECT8_MARKET_DATA_PROVIDER`.

PostgreSQL in HedgeFund Market Data Platform owns canonical market-data truth.
Spect8 SQLite owns strategy/application state, projections, consumed-version
provenance, revision observations, and its durable read watermark. No Platform
table is written by this integration.

The boundary is:

```text
Platform PostgreSQL
  -> Spect8CanonicalReadService
  -> Spect8CanonicalReadServiceGateway
  -> Platform canonical DTOs
  -> BID/instrument/timeframe compatibility adapter
  -> existing Spect8 Bar and frozen strategy engine
```

The adapter is intentionally separate from the existing `MarketDataProvider`
interface because a canonical version stream and durable watermark do not have
the same lifecycle as a provider polling coordinator.

## Frozen data contracts

- BID is the only price type admitted to current strategy inputs. ASK and MID
  are ignored; there is no price fallback. This covers filters, signals,
  indicators, ATR, levels, current partial D1/W1, statuses, and projections.
- Platform canonical UTC H4 is never translated into a Spect8 strategy bar.
  Spect8 H4 is rebuilt from completed canonical H1 BID with the existing IC
  Markets broker wall clock (UTC+2/+3 under New York DST), at broker hours
  00/04/08/12/16/20. The result stays inside Spect8.
- Completed D1 continues to use New York 17:00 authority. Current partial D1
  and Macro partial W1 continue to be constructed by the frozen Spect8 code;
  Macro's weekly boundary remains Friday 17:00 America/New_York.
- The bounded bootstrap request is M30=1, H1=1,177, H4=30, D1=10, W1=6.
  D1=6 is not an accepted Phase 22 bootstrap even though six values can be the
  narrow mathematical input for one ATR(5) calculation.
- Approved identity mappings are explicit: `FX_EUR_USD -> EUR_USD`,
  `FX_GBP_USD -> GBP_USD`, and `FX_USD_JPY -> USD_JPY`. Both directions are
  fixed lookup tables. An absent mapping is a deterministic error; no provider
  symbol guessing or alias inference occurs.

## Version provenance, revisions, and watermark

For every directly consumed H1/D1 BID bar, Spect8 stores the Platform canonical
ID, logical and immutable identities, policy ID/version, canonical version,
semantic hash, semantic availability, associated strategy evaluation keys, and
consumption time. The first version Spect8 accepts for a logical identity owns
the already-confirmed live strategy history.

A later canonical version is written to the revision-observation table with
`DETECTED_NO_AUTOMATIC_REPLAY`. It does not invoke the strategy callback and
does not mutate the first-consumed record. Reconciliation or historical replay
must be a separately authorized workflow.

The Platform watermark is monotonic and is updated only after every selected
bar in the returned batch has been handled successfully. If evaluation or
provenance persistence fails, no checkpoint is written. If application work
and consumption provenance succeed but the final checkpoint fails, the next
read safely replays the batch: the immutable consumption record prevents a
second evaluation and allows the watermark to advance.

## Deterministic parity

`DeterministicParityHarness` sends OLD and Platform-adapted `StrategyRequest`
objects through the same unmodified `Spect8StrategyEvaluator` and projection
service. It compares evaluation, filter, signal, level, status, event payload,
event order, and current D1/W1 candle semantics exactly. Only transport
identity/provenance fields (provider IDs, source IDs/checksums, snapshot IDs,
and ingestion metadata) are excluded; price and strategy fields are never
toleranced or hidden. Any real difference is returned with its exact field
path and values.

Phase 22A-2 does not implement live cutover, execution, spread semantics,
forming signals, lower-timeframe evaluation, or automatic revision-driven
strategy recomputation.
