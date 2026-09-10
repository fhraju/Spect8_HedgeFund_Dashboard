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
