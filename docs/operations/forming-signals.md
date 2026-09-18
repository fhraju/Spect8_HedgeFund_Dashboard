# Forming signals

The isolated platform runtime evaluates MICRO M30/H1 and MACRO H1/H4 independently after consuming authoritative history and partial snapshots. It reuses the frozen strategy calculation with one incomplete final bar and the filter anchored to the latest completed H1. H4 is broker-aligned and derived locally from contiguous H1 inputs.

Each new completed M5 component can update a provisional signal. Until the first component exists, the current bar is unavailable. Partial prefixes must be contiguous, source-qualified, and no older than ten minutes. No REST request or synthetic candle is used to create a partial. A provisional signal disappears when conditions cease to match, its bar closes, or its input expires; completed-bar confirmation and existing hold periods remain authoritative.

`GET /signals/current` reads an atomically published cache. It preserves `confirmed`, `forming`, `as_of` and `platform_healthy`, adding `forming_candidates` and an aggregate `forming_state`. Aggregate platform degradation does not suppress an eligible instrument. `forming` entries use `instrument_id`, with a temporary `instrument` alias, and include authority, source/evaluation timestamps and provenance.

Each candidate identifies authority, instrument, mode and timeframe; it reports `READY`, `WAITING_FOR_DATA`, `STALE`, or `BLOCKED`, a reason, source bar interval and timestamps. `READY` with no match means **NO SIGNAL**. The health endpoint exposes the same candidate diagnostics at `data.authority.platform.forming_evaluation`. Dependencies include bounded completed/native history, filter inputs and partial components, so repaired inputs invalidate the cache. GET requests expire stale cache entries without running strategy math.

On September 10, at the user's request, both frontend builds and their files were restored to the versions preceding both frontend updates. The original scanner presentation is active; it gives a current confirmed signal precedence over a forming signal in the same cell. The independent backend evaluations and backward-compatible API remain deployed. The restored frontend consumes the `instrument` alias. Five-second polling does not imply tick-level data: partials advance from completed five-minute stream candles.

## Validation

Run the backend forming-runtime, isolated-platform, signal-lifecycle and partial-snapshot suites, plus frontend tests and TypeScript checking. ASGI tests use temporary SQLite projections and fixture sources, never provider calls. Browser acceptance for the restored presentation verifies the original styling and compatibility with the current-signals API. The previous frontend rollout also tested concurrent cards using browser-only fixtures; that layout has since been rolled back. Live backend acceptance requires source and evaluation timestamps to advance, including zero-match evaluations.

## September 2026 rollout

Staging and production were validated independently. Production's stale DEMO EUR/USD and GBP/USD remain stale; shared LIVE USD/JPY can evaluate independently. No collector service, provider mapping, strategy threshold or market-data store was changed for this rollout.

Deployment backups, manifests, browser evidence and progress snapshots are under `/home/raju/The-System/rollouts/forming-signals-20260909/`. Staging retains its pre-existing scanner refresh coordinator and freshness presentation in its restored runtime frontend.

## Rollback

For each affected environment, stop only its Spect8 frontend and backend services. Restore files listed in `<environment>-manifest.json` from `<environment>-before/`; remove only newly added files whose manifest value is false. Restore `frontend/.next` from `<environment>-next-before/`, then start the two dashboard services and verify health and authenticated scanner responses. Keep current projection databases, acquired bars and confirmed signal history intact. Collector services remain running. Do not reset or clean runtime worktrees wholesale: they contain pre-existing deployment changes.

The subsequent frontend rollback is documented in `/home/raju/The-System/rollouts/frontend-rollback-20260910/REPORT.md`. Its saved build IDs are authoritative for the currently deployed presentation.

## Scanner totals

Confirmed Signals and Forming Signals use the current-signals API, scoped to all monitored instruments and the selected mode: MICRO M30/H1 or MACRO H1/H4. They count individual bar/timeframe/direction identities rather than pairs; duplicate versions are counted once. Current confirmations remain counted until their visibility deadline. Forming matches are counted independently, including when the restored row presentation gives a confirmed card precedence. The dropdown row filters do not change these dashboard-wide totals.

The confirmed total shows a dash for unavailable or delayed responses. At the user's request, the Forming Signals box displays 0 when no forming matches are available, including while inputs are loading; its status text still distinguishes unavailable data from a successful zero-match evaluation. Forming totals include only fresh READY candidates and label partial coverage when some instruments cannot evaluate. The summary adds a matching card without changing filter badges or row presentation.

## Share spread-betting source rollout (2026-09-10)

The approved share source is IG **SPREADBET**, labeled as spread betting rather
than CFD. Collection belongs to the Market Data Platform. Share H1/H4 forming
candidates use contiguous completed M30 prefixes; M30 forming is explicitly
unsupported. No additional IG request is made by the dashboard.

Share candidates carry their authority, source time, bar interval and 35-minute
maximum source age, and expire at their bar boundary. Unsupported candidates
are excluded from forming readiness coverage. Existing Forex thresholds and the
restored scanner/filter design remain unchanged.

A merged staging candidate was deployed on September 10 at 16:58 UTC, preserving
runtime scanner caching. Validation: 22 merged backend tests, 65 merged frontend
tests, TypeScript, a production build, and an authenticated browser check with
21 healthy Forex rows and no JavaScript errors. The isolated PostgreSQL suites
passed 17 tests with no skips; these are platform tests, not live market acceptance.

AAPL LIVE and MSFT DEMO are the only enabled share pilots. Shared collector
updates/restarts received additional explicit approval after automatic review
rejected the combined rollout. Production dashboard code has not changed.
New native share evidence fields use a separate share payload; the existing FX
payload remains compatible with the production reader.

Pilot acceptance is pending multiple advancing M30 closes and correct per-mode
filters, evaluation freshness and browser states. No remaining share batches or
production dashboard promotion are authorized by acceptance until those checks
pass. Source missing history must remain visible, not be presented as NO SIGNAL
or converted into an inferred closure.

September 11 follow-up: startup now schedules platform preparation in the
background, serving truthful waiting/stale health immediately. Share completed
evaluation diagnostics are per mode/timeframe; a missing H4/weekly dependency
cannot gate a separately valid Micro candidate. Candidate-specific warm-up counts
are preserved. The merged follow-up suite passed 9 tests.

Browser fixture acceptance passed against the deployed staging frontend using a
payload serialized by the real `/signals/current` route: BUY FORMING appeared,
source/bar timestamps rendered, M30 showed unsupported, request failure removed
the signal, and an expired response could not restore it. The isolated browser
intercepted responses only; it wrote no live signals or market data. Real browser
checks show 23 instruments and explicit pilot failure/waiting states.

Real share evaluation acceptance remains blocked: both pilots have current M30
prices, but missing September 7–8 daily and weekly dependencies. Micro currently
reports `MISSING_DAILY_CANDLE`; Macro reports
`SHARE_FILTER_REQUIRED_H1_MISSING_OR_INVALID`. Do not present a current filter
snapshot as an accepted completed signal calculation. See the Market Data
Platform `docs/operations/share-cfds.md` for exact gaps and rollout state.

## September 12: signal refresh flicker

The staging browser compared each new signal response's `as_of` with a React
clock sampled before the response arrived. This temporarily marked successful
responses unavailable until the next one-second tick. The signal summary had
another independent clock, causing counts to flash as well.

The scanner now publishes receipt time together with the response and passes
that same clock to the summary. Periodic ticks still expire signals without a
network response. Future/stale response checks, source freshness, real bar
boundaries, request-failure behavior, polling coordination and styling remain.

Source checks: 20 tests and TypeScript passed. The isolated deployment candidate
passed 22 tests and the production build. `frontend/scripts/verify-signal-refresh.cjs`
passed five real-browser fixture scenarios: refresh/removal/expiry, failure and
retry, future response, stale response, and stale forming source. Fixtures run
against an isolated loopback frontend; no live signal or market-data writes.

Deployed only staging's `market-scanner.tsx`, `signal-summary.tsx` and compiled
frontend build at September 12 02:00 UTC. Exact rollback backup and evidence:
`The-System/rollouts/signal-flicker-audit-20260911/`, including
`frontend-backup-20260912T020036Z`. Restore those two backed-up components and
`frontend.next` with a controlled staging frontend stop/start to roll back.
Production dashboard, backend and collector processes were not changed.

Post-deployment observation at 02:01 UTC: seven successful API refreshes, no false
unavailable transitions or browser errors; all seven viewport checks passed.
No live market signal existed during this weekend observation, so appearance,
removal and expiry acceptance uses the deterministic browser fixtures above.

### Correction at September 12 02:31 UTC: browser/server clock differences

The first fix was incomplete: it removed the stale React clock tick but still
compared server timestamps with device UTC. A separate browser with its clock
650 ms behind reproduced the user's exact `0 → — → 0` and unavailable/waiting
cycle after all seven successful responses. A synchronized browser stayed stable
through 19 responses. This reproduces a clock-skew failure; it does not measure
the user's actual device clock.

The staging signal proxy now stamps `X-Spect8-Response-Time` and `Cache-Control:
no-store`. `SignalClock` anchors to that server timestamp and advances using
`performance.now()` elapsed time. Cards and totals share this clock. Neither
device UTC nor its later corrections control signal freshness. Payload `as_of`
and source timestamps are still checked against server time: future/stale data,
missing clock headers and failed responses remain unavailable. Bar boundaries
and source freshness limits are unchanged.

Validation: 22 source tests, TypeScript, 24 deployment-candidate tests and build
passed. Eight isolated real-browser scenarios passed, including clocks one hour
ahead/behind across refreshes and expiry, missing headers, failed requests/retry,
and stale/future data. Browser fixtures never write to live market/signal data.

Deployed staging scanner component, new `lib/signal-clock.ts`, signal proxy route
and build only. Existing scanner coordination/timeouts and all styling remain.
Backup: `rollouts/signal-flicker-audit-20260911/clock-backup-20260912T023058Z`.
Its `files.json` identifies new versus replaced files for rollback; restore the
backed-up build and replaced files and remove only the newly added clock file.
The first fix's backup remains available separately.

Post-deployment: `clock-skew-after.json` and `clock-normal-after.json` each show
seven HTTP 200 refreshes, stable zero totals after initial loading, and no false
unavailable transitions or browser errors. The former repeats the exact 650 ms
behind-device-clock reproduction that failed before this correction. Live
markets are closed; real signal appearance/expiry is covered by the isolated
fixtures, not claimed from the weekend production-data observation.
