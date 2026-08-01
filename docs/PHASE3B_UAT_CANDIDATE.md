# Phase 3B — EUR/USD UAT Candidate

## Classification

- Technical status: **TECHNICALLY READY FOR CLIENT UAT**
- Phase 3B classification: **CONDITIONAL PASS**
- Three-instrument expansion: **NOT APPROVED**

The conditions are the required 3–5 market-day observation, three genuine H1
boundaries, one genuine H4 boundary, D1 if the window permits, and explicit
client acceptance. None is fabricated or inferred from bootstrap data.

## Checkpoint and baseline

Phase 3A was reproduced before Phase 3B:

- backend: 436 passed;
- frontend: 16 passed;
- TypeScript: PASS;
- Next.js production build: PASS;
- credential/frozen-authority checks: PASS.

Phase 3A was committed locally as `53477c3` using an explicit allowlist.
`.vscode/` remained unrelated, untracked, and untouched. Ignored environments,
databases, logs, and build output were excluded.

## Runtime hardening

- Cross-process advisory lock permits one scheduler per SQLite database.
- Graceful shutdown waits for any bounded request, closes the runtime session,
  and releases the lock.
- Immediate startup catch-up uses persisted H1/H4 evaluation cursors.
- A bounded 168-bar catch-up window processes every unseen trigger in order.
- If downtime exceeds the catch-up window, the provider reports insufficient
  history instead of silently skipping candles.
- Scheduler wakes no later than the configured health interval and polls
  shortly after hourly UTC boundaries using a configurable safety delay.
- H1/H4/D1 network series are requested only when their expected completed
  boundary advanced.
- Structured JSON logs redact credential-shaped fields and authenticated query
  values.
- Log files rotate by configured size and backup count.
- SQLite persists runtime sessions, polls, request deltas, health transitions,
  evaluations, duplicate prevention, and event counts.
- Protected `GET /runtime/status` and the observation CLI expose sanitized
  runtime/report state.

No alternate strategy, event, persistence, or market-data pipeline was added.

## Request model

Steady-state series requests for one continuously running instrument:

| Instruments | Average/hour | Requests/day |
|---:|---:|---:|
| 1 | 1.2917 | 31 |
| 3 | 3.8750 | 93 |
| 10 | 12.9167 | 310 |
| 25 | 32.2917 | 775 |
| 50 | 64.5833 | 1,550 |

Per instrument, the deterministic minimum is 24 H1 + 6 H4 + 1 D1 requests per
day. Startup catch-up and retry attempts are additional. Dashboard refreshes
do not call Twelve Data.

These figures are request projections, not claims about the user’s Twelve Data
plan or quota.

## Bounded live measurement

Observation UTC:

```text
2026-07-31T07:47:08.132983Z
→ 2026-07-31T07:47:11.327439Z
```

This was a 3-second bounded validation, **not** a multi-day observation.

- runtime sessions: 2;
- restarts: 1;
- polls: 2;
- network attempts: 5;
- successful responses: 5;
- failed responses: 0;
- rate-limit responses: 0;
- timeouts: 0;
- cache hits: 3;
- series attempts: H1 2, H4 2, D1 1;
- bootstrap discoveries: H1 1, H4 1, D1 1;
- evaluations created: 2;
- duplicate triggers prevented on restart: 2;
- events created: 12;
- first-session counts: 66 canonical bars / 2 evaluations / 12 events;
- restarted counts: 66 / 2 / 12;
- restarted coordinator outcomes: 0;
- API evaluations exactly matched SQLite;
- state: `HEALTHY`;
- latest H1: `2026-07-31T07:00:00Z`;
- latest H4: `2026-07-31T05:00:00Z`;
- latest D1: `2026-07-31T00:00:00Z`;
- orders: 0;
- fills: 0.

The CLI report parsed as valid sanitized JSON and contained no authorization or
API-key field.

## Final deterministic validation

- backend: `447 passed`;
- frontend: `16 passed` across four files;
- TypeScript: PASS;
- optimized Next.js production build: PASS;
- credential scan: PASS;
- `git diff --check`: PASS;
- frozen strategy/golden diff: empty.

The only warning is the pre-existing Starlette/httpx test-client deprecation
warning.

## Boundary and recovery evidence

Deterministic evidence passes for:

- forming-candle exclusion;
- D1 strictly before each signal trigger;
- H1/H4 independence;
- missed-boundary catch-up in chronological order;
- same-process and reconstructed-process duplicate prevention;
- provider rate-limit accounting and bounded retry;
- timeout accounting;
- stale/unavailable state while preserving persisted evaluations;
- recovery transition and recovery-duration reporting;
- API/SQLite equality;
- zero orders and fills.

The bounded live session started after already completed candles. Therefore:

- genuine H1 boundaries observed during the session: **0 of 3 required**;
- genuine H4 boundaries observed during the session: **0 of 1 required**;
- genuine D1 update observed during the session: **0**.

Bootstrap discovery is not counted as observed boundary evidence.

## Client acceptance and remaining risks

The client checklist and feedback template are prepared, but the client has
not reviewed or approved the candidate.

Remaining risks/conditions:

- 3–5 market-day continuous runtime evidence is incomplete;
- required real-boundary evidence is incomplete;
- live rate-limit and timeout incidents did not occur in the bounded session;
- measured long-duration request rate and provider-plan headroom remain
  unverified;
- client usability and explicit acceptance remain pending;
- the existing Starlette/httpx test-client deprecation warning remains.

Do not add three or more instruments until sustained observation and client
approval are complete.
