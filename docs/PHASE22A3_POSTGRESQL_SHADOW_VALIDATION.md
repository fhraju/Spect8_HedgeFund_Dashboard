# Phase 22A-3 PostgreSQL shadow integration

## Runtime and ownership

The optional path is:

```text
Platform PostgreSQL (read only)
  -> PostgreSQLSpect8CanonicalReadService
  -> Spect8CanonicalReadServiceGateway
  -> BID compatibility history
  -> existing partial D1/W1 builders and broker H4 aggregator
  -> frozen Spect8 evaluator
  -> isolated MARKET_DATA_PLATFORM projections and provenance in a dedicated
     Spect8 shadow SQLite database
```

`SPECT8_MARKET_DATA_PLATFORM_SHADOW_ENABLED` remains false by default. When it
is true, startup requires `MARKET_DATA_PLATFORM_DATABASE_URL`, runs one shadow
cycle, and exposes its evidence on application state. It does not replace the
configured Twelve Data/current provider or coordinator, write to Platform,
execute orders, or make Platform results authoritative. Disabling the flag
removes the composition cleanly. Shadow statuses, events, snapshots,
canonical-consumption provenance, revisions, and watermark are stored in
`<authoritative-name>.platform-shadow.sqlite3`, so instrument-only event queries
cannot expose them through the current-provider dashboard.

The dashboard environment must contain the Platform package. A two-checkout
validation may install the sibling checkout with
`.venv\Scripts\python.exe -m pip install -e ..\HedgeFund_Market_Data_Platform`.
A deployed shadow must instead install an approved, pinned Platform release;
the repositories are not copied or merged.

The only mappings are `FX_EUR_USD -> EUR_USD`, `FX_GBP_USD -> GBP_USD`, and
`FX_USD_JPY -> USD_JPY`. Bootstrap is enforced as M30=1, H1=1,177, H4=30,
D1=10, W1=6. BID is the only strategy authority. Platform UTC-H4 rows are
ignored; Spect8 rebuilds H4 from completed H1 BID at IC Markets broker hours
00/04/08/12/16/20 with UTC+2/+3 DST alignment. Existing New-York-17:00 D1 and
Friday-New-York-17:00 partial W1 construction remains unchanged.

## Checkpoint and revision behavior

Each cycle evaluates the bounded, first-consumed history through the existing
strategy service, then persists canonical H1/D1 consumption provenance, then
advances the durable canonical-ID watermark. A pre-evaluation failure leaves
the watermark unchanged. If provenance commits but the final checkpoint fails,
restart rereads from the prior watermark; immutable canonical identities
suppress duplicate evaluation/event creation and allow the checkpoint to catch
up.

A later canonical version is recorded as `DETECTED_NO_AUTOMATIC_REPLAY`.
Shadow evaluation pins the original consumed canonical ID, so the first
confirmed strategy state and provenance remain unchanged. Reconciliation is
outside this phase.

## Comparison authority

`DeterministicParityHarness` is the semantic test: identical OHLC input through
the OLD-compatible and Platform-adapted requests must match exactly for Micro
and Macro, H1 and broker-derived H4, filters, signals, indicators, levels,
statuses, events and ordering, partial D1/W1, and strategy versions. Only
transport provenance is excluded.

Actual Twelve Data and Platform BID bars are compared separately by exact
timeframe/open/close identity. Each differing or missing OHLC row is labeled
`PROVIDER_DATA_DIFFERENCE`; it is not converted into a semantic-parity failure.
Any downstream strategy difference must retain that source classification.

Real shadow tests are opt-in with `MARKET_DATA_PLATFORM_DATABASE_URL`. They
require populated history and fail rather than fabricate missing bootstrap or
UTC+2/UTC+3 H4 evidence. Destructive Platform tests use only the independently
validated safe `TEST_DATABASE_URL` fixture.
