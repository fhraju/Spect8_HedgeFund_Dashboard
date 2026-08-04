# Phase 3B One-Month Historical Replay Validation

## Classification

**PASS — historical functional replay validation.**

This classification applies only to the isolated July 2026 historical replay
track. It does not claim profitability and does not replace the separate
multi-day live-observation/client-acceptance gate in the Phase 3B UAT
documents.

## Baseline and implementation boundary

- Starting repository baseline: backend 447 tests and frontend 17 tests.
- Final implementation baseline: backend 458 tests and frontend 21 tests.
- TypeScript check and Next.js production build passed.
- Production evaluator: `Spect8StrategyEvaluator` through the existing
  `WalkingSkeletonService` projection path.
- Normalization and completion policy: existing `CandleNormalizer`,
  `ClosedBarDetector`, and Twelve Data adapter conventions.
- Frozen strategy formulas and golden authorities were not modified.
- This is scanner/dashboard functional validation, not a profitability
  backtest. There are no trades, P&L, optimization, orders, or fills.

> Version note: the recorded July run below predates the frozen v1.0.2
> completed-D1 boundary clarification and is retained as historical evidence.
> The active replay now includes a completed D1 candle whose close equals the
> signal close. Dedicated deterministic H1/H4 v1.0.2 replay tests supersede the
> older strict-precedence statement at that equality boundary.

## Replay architecture and isolation

Historical replay is stored in `var/spect8_historical_replay.sqlite3`, separate
from the live projection database `var/spect8_phase3b.sqlite3`. Replay tables
are keyed by immutable dataset fingerprints and replay run IDs. Live candle,
evaluation, event, health, synchronization, and scheduler tables are never
opened by the replay repository.

The live scheduler was disabled during the validation. Live table counts were
captured immediately before and after both replays and were identical:

| Live table | Before | After |
| --- | ---: | ---: |
| `canonical_bars` | 95 | 95 |
| `processed_bars` | 18 | 18 |
| `event_history` | 108 | 108 |
| `instrument_status` | 2 | 2 |
| `provider_health` | 1 | 1 |
| `provider_sync` | 1 | 1 |
| `runtime_sessions` | 2 | 2 |
| `runtime_poll_history` | 118 | 118 |

The stopped live database SHA-256 before replay was
`9DE121F34AFE428B9995C353A9E69C4BDE5D5753D9B21181A5A50BA099FC4B92`.
Run creation, execution, cached rerun, and replay persistence did not change
the live table state. Automated tests also verify run deletion and rerun
isolation.

## Actual Twelve Data dataset

Display window:

```text
EUR/USD
2026-07-01T00:00:00Z inclusive
2026-08-01T00:00:00Z exclusive
Signal timeframes: H1, H4
Context timeframe: D1
```

Dataset fingerprint:

```text
c0667b2f147f347b8053a982f295a82160a055cd51ee5b5b0997f43f9cef6691
```

All accepted rows were continuous under the previously validated Twelve Data
weekend convention. H4 closes retained the provider's `01/05/09/13/17/21 UTC`
anchors.

| TF | Requested close range | Returned close range | Accepted | Warm-up | Display | Duplicates | Malformed | Gaps |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| H1 | 2026-06-21 00:00 to 2026-08-01 00:00 exclusive | 2026-06-21 00:00 to 2026-07-31 23:00 | 984 | 240 | 744 | 0 | 0 | 0 |
| H4 | 2026-06-21 00:00 to 2026-08-01 00:00 exclusive | 2026-06-21 01:00 to 2026-07-31 21:00 | 246 | 60 | 186 | 0 | 0 | 0 |
| D1 | 2026-06-21 00:00 to 2026-08-01 00:00 exclusive | 2026-06-22 00:00 to 2026-07-31 00:00 | 40 | 9 | 31 | 0 | 0 | 0 |

The H1/H4 warm-up totals exceed the required 30 signal candles and D1 exceeds
the required six context candles at the first display trigger.

## Evaluation totals

| Result | Count |
| --- | ---: |
| H1 evaluations | 744 |
| H4 evaluations | 186 |
| Total evaluations | 930 |
| Filter pass | 727 |
| Filter fail | 203 |
| Signal | 44 |
| No signal | 886 |
| Events | 5,624 |
| Duplicate evaluations | 0 |
| Quarantined windows | 0 |
| Orders | 0 |
| Fills | 0 |

Signal distribution was 13 H1 BUY, 11 H1 SELL, 14 H4 BUY, and six H4 SELL.
Filter results remained independent: H1 had 541 pass and 203 fail; H4 had 186
pass.

The expected display-period counts are exact: `31 × 24 = 744` H1 closes and
`31 × 6 = 186` provider-anchored H4 closes. Start-inclusive/end-exclusive
selection excluded the candle closing exactly at 2026-08-01T00:00:00Z.

## Look-ahead and manual inspection evidence

In this recorded pre-v1.0.2 run, every evaluation used an injected fixed replay
clock set one microsecond after the trigger close. Each input contained exactly
30 signal bars whose closes were strictly earlier than replay-as-of and six D1
bars whose closes were strictly earlier than the signal close.

Five H1 evaluations were inspected at ordinals 1, 226, 451, 701, and 930. Five
H4 evaluations were inspected at ordinals 3, 228, 453, 703, and 928. All ten
had 30 signal bars, six D1 bars, a complete event sequence, and zero look-ahead
violations.

All 44 signal evaluations in that run were inspected. Each had a seven-event
sequence, valid 30/6 input history, and the then-active strict D1 precedence.
There were zero signal inspection violations.

Specific boundary evidence:

- D1 transition: the 2026-07-01T00:00:00Z H1 trigger used
  2026-06-30T00:00:00Z D1 context; the next 01:00 UTC trigger used the newly
  eligible 2026-07-01T00:00:00Z D1 close.
- H1/H4 overlap: both timeframes were evaluated independently at
  2026-07-01T01:00:00Z, ordered H1 then H4.
- Weekend: the continuous 2026-07-04T00:00:00Z H1 row was evaluated normally;
  the complete dataset had zero weekend interval gaps.

## Determinism

First provider-backed run:

```text
11fbe15fd9134cd3941267a65486dfe7
```

Second cached-dataset run:

```text
45f0aaeb587e4a01a4d7cbf246c55c53
```

Both runs used the same dataset fingerprint and produced this identical
determinism digest:

```text
1c556a3682e45f934301791c13a1faac138894d172fee306cb8e29609d44f5d2
```

Direct SQLite `EXCEPT` comparisons found zero evaluation differences and zero
event differences across timestamps, timeframe order, filter/signal outcomes,
reason codes, market values, event types, sequences, timestamps, and payloads.
Both runs contained 930 unique evaluations and 5,624 events with zero duplicate
idempotency keys. The second run made no provider request; it referenced the
immutable cached dataset.

## API, persistence, and dashboard reconciliation

The authenticated summary API returned 744 H1, 186 H4, 930 total, 727 filter
pass, 203 filter fail, 44 signal, 886 no-signal, and 5,624 events. Direct replay
persistence returned the same values.

The production Next.js page `/historical-replay` was built and authenticated
successfully. Its rendered HTML contained the same totals, the dataset
fingerprint, `COMPLETED`, `REPLAY — NOT LIVE`, and zero orders/fills. Evaluation
detail responses matched persisted input, output, reason-code, and event JSON.

## Remaining limitations

- Results validate scanner behavior only; no profitability inference is valid.
- The provider-backed run is specific to Twelve Data's July 2026 EUR/USD
  candles and its established UTC/weekend/H4 boundary conventions.
- Replay execution is single-process background work. SQLite prevents an
  identical active configuration, but distributed job scheduling is outside
  this local/private phase.
- Replay datasets remain local private artifacts and are ignored by Git.
- The separate sustained live-observation/client-approval gate remains open.
