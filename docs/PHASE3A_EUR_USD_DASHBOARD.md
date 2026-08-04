# Phase 3A — EUR/USD Read-Only Dashboard Vertical Slice

> Historical phase record: provider-native midnight-UTC D1 statements below
> describe the Phase 3A baseline. They are not current runtime authority. The
> active v1.0.3 runtime reconstructs D1 OHLC from H1 at the DST-aware 17:00
> America/New_York close.

## Scope and immutable boundaries

Phase 3A connects only:

```text
Twelve Data EUR/USD H1/H4/D1
→ bounded runtime
→ Phase 2B coordinator
→ SQLite canonical/projection persistence
→ Phase 2A evaluator
→ protected read-only API
→ protected Next.js dashboard
```

It adds no strategy rule, candle aggregation, instrument, provider, execution,
order, fill, backtest, optimizer, alert, or deployment path. H1 and H4 are
evaluated independently. D1 is context only; under the active v1.0.2 boundary,
a completed D1 close at the signal trigger is included and future closes are
excluded.

## Verified baseline

Before Phase 3A changes:

- Backend: `427 passed` with one existing Starlette/httpx deprecation warning.
- Frontend: `12 passed`.
- TypeScript: PASS.
- Next.js production build: PASS.
- Git HEAD: `52afbd965b4fbddda0d0be0cfa235c09362628ce`.
- `.vscode/` was unrelated and untracked; it remains preserved.

## Provider semantics retained

The Phase 2C live evidence and committed provider profile were rechecked:

- Weekend EUR/USD rows contain changing OHLC values; they are not removed.
- Twelve Data H4 opens remain `01:00, 05:00, 09:00, 13:00, 17:00, 21:00 UTC`.
- D1 opens remain `00:00 UTC`.
- No explicit frozen strategy requirement conflicts with those provider
  boundaries.

No alternate aggregation or weekend filtering was introduced.

## Runtime

`MarketDataRuntime` is a small scheduler around the existing
`MarketDataCoordinator`.

- A live application starts one background task and performs an immediate
  bootstrap poll.
- The default interval is 300 seconds and configuration is rejected outside
  60–900 seconds.
- Polling runs outside the event loop thread and is guarded against concurrent
  execution.
- Shutdown cancels the scheduler cleanly.
- Existing Phase 2C timeouts, retries, and backoff remain the only provider
  request policy.
- The coordinator remains the only normalization, completion, history,
  evaluation, and persistence path.
- The provider cache is bounded to 12 request series.

The repository now persists provider synchronization separately from provider
health:

- latest attempt time;
- latest successful synchronization time;
- current synchronization state;
- sanitized detail.

Successful restart/replay processing continues to rely on the existing
canonical keys and event/evaluation idempotency keys.

## Read-only API

Protected `GET /dashboard` returns a typed `DashboardEnvelope` containing:

- generated UTC timestamp and overall data state;
- stale flag;
- persisted provider health and provider synchronization;
- canonical EUR/USD identity;
- latest persisted H1, H4, and D1 close timestamps;
- latest persisted H1 and H4 Phase 2A evaluations;
- filter, signal, candidate levels, deterministic reason codes, and persisted
  market values;
- recent persisted strategy events;
- an explicit execution contract fixed at `enabled=false`, `orders=0`,
  `fills=0`.

The endpoint contains no strategy calculation. It handles `EMPTY`, `PARTIAL`,
`HEALTHY`, `STALE`, `DATA_UNAVAILABLE`, `INSUFFICIENT_HISTORY`, and
`QUARANTINED`. Persisted evaluations remain available while current provider
health is unhealthy and are marked stale.

The existing endpoints remain for compatibility. Live mode rejects
`POST /synthetic/replay` with HTTP 409.

## Dashboard

The production dashboard fetches only `GET /dashboard`; it contains no mock
data and no TypeScript strategy calculation. It shows:

- EUR/USD identity and Twelve Data health;
- last successful sync and H1/H4/D1 freshness;
- separate H1 and H4 strategy cards;
- filter and signal outcomes;
- deterministic reason codes;
- persisted candle, moving-average, daily volatility, and threshold values;
- candidate display levels when confirmed;
- recent persisted events;
- distinct healthy/no-signal, empty, partial, stale, unavailable,
  insufficient-history, and quarantine messaging;
- explicit read-only state with zero orders and fills.

Loading and transport-error boundaries remain explicit. The layout keeps the
existing design system and adds responsive single-column behavior. The
server-rendered projection refreshes every 60 seconds and also provides a
manual refresh control.

## Deterministic validation

Final local validation:

- Backend: `436 passed`, one existing Starlette/httpx deprecation warning.
- Frontend: `16 passed` across four files.
- TypeScript: PASS.
- Next.js production build: PASS.
- `git diff --check`: PASS.

Coverage includes existing forming-candle exclusion, D1 look-ahead prevention,
H1/H4 independence, duplicate prevention, health recovery, and frozen golden
cases, plus Phase 3A schema/persistence equality, empty/partial/failure/stale
states, restart persistence, zero execution artifacts, and frontend state
rendering.

## Bounded live validation

On 2026-07-31, using the ignored `backend/.env` without printing the key:

- provider health: `HEALTHY`;
- coordinator outcomes: `2` (`H1`, `H4`);
- canonical bars: `66`;
- processed evaluations: `2`;
- persisted events: `12`;
- `GET /dashboard` evaluations exactly matched repository statuses;
- latest closes: H1 `2026-07-31T06:00:00Z`, H4
  `2026-07-31T05:00:00Z`, D1 `2026-07-31T00:00:00Z`;
- last successful synchronization was persisted;
- orders: `0`; fills: `0`.

A second live poll after application reconstruction returned two replayed
outcomes, created zero events, and left counts unchanged at
`66 bars / 2 evaluations / 12 events`.

The optimized Next.js production server was then connected to that persisted
live database through the protected browser-facing API. Authenticated checks
returned HTTP 200, `HEALTHY`, and two evaluations. The rendered dashboard
contained EUR/USD, H1, H4, independent-evaluation content, Twelve Data
identity, the zero-orders display, and the read-only safety panel.

No real H1 boundary was observed over an hour in this bounded validation
window. New-boundary behavior remains covered deterministically by the
provider/coordinator tests and should receive sustained observation in Phase
3B.

## Classification and handoff

Phase 3A classification: **PASS**.

The project is ready for Phase 3B client acceptance and sustained observation.
Remaining observation items are a real completed H1 boundary over a long-lived
runtime, provider quota behavior over time, and removal of the existing
Starlette/httpx test-client deprecation warning during a later dependency
maintenance phase.
