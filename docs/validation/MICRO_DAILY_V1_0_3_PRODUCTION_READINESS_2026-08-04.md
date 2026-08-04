# Spect8 Micro/Daily v1.0.3 production readiness — 2026-08-04

## 1. Final Verdict

**PASS WITH EXTERNAL BROKER VERIFICATION PENDING**

Code, the active runtime database, historical replay, golden/reference
authority, documentation, builds, and a real-process runtime smoke conform to
the DST-aware 17:00 America/New_York D1 rule. The repository contains no direct
evidence for the selected broker's winter UTC+2 server switch, so that one
external fact remains unverified.

## 2. Repository State Before Work

- Repository: `E:\Work\The-System\Spect8_HedgeFund_Dashboard`
- Branch: `main`
- Commit: `bd6a3e615779a2803e0d1c00982cbe42d9b150de`
- The worktree was already dirty with the in-progress v1.0.2/v1.0.3,
  historical-replay, dashboard, golden, and documentation work shown by the
  preflight `git status --short`. `.vscode/settings.json` was unrelated,
  untracked, and preserved. No existing user change was reverted.
- No Python, Node, Uvicorn, or Next process owned ports 8000/3000, and no
  concurrent SQLite writer was active before backup/rebuild.
- Active authority is `SPECT8_MICRO_DAILY_V1_0_3`: the production constant is
  in `backend/app/engine/strategy.py`, the manifest points to
  `../Spect8_Micro_Daily_v1_0_3_FROZEN.md`, and dataset version is `1.0.3`.

## 3. Active Database Identification

The deployment database is unequivocally:

```text
E:\Work\The-System\Spect8_HedgeFund_Dashboard\var\spect8_phase1.sqlite3
```

Evidence: `scripts/start_phase3b_observation.ps1` loads ignored
`backend/.env`; that file now explicitly sets
`SPECT8_DATABASE_PATH=var/spect8_phase1.sqlite3`. With no process-level
override, `Settings.from_environment()` resolves the same default. The replay
setting resolves separately to `var/spect8_historical_replay.sqlite3`, and
configuration rejects identical live/replay paths. `spect8_phase3b.sqlite3`
and the validation databases are not selected by the launcher or settings.
No credential value was printed or copied into documentation.

## 4. Final Filter Formula

For independent completed H1 or H4 signal close `T`:

```text
eligible D1 = complete D1 where close_time <= T
daily_low   = min(low of latest 2 eligible D1)
daily_high  = max(high of latest 2 eligible D1)
atr         = Wilder ATR(5), ending at latest eligible D1
buffer      = atr * 0.05
buy_level   = daily_low + buffer
sell_level  = daily_high - buffer
recent_low  = min(low of latest 21 completed signal-timeframe bars)
recent_high = max(high of latest 21 completed signal-timeframe bars)
buy matched = recent_low <= buy_level
sell matched = recent_high >= sell_level
```

The ATR implementation uses the first candle as previous-close context, seeds
from the first five true ranges, and applies Wilder recurrence through the
latest eligible D1. Production/rebuild/replay retain the latest ten eligible
D1 inputs. Buy/Sell and H1/H4 remain independent; the filter has no consuming
or first-trade state. Signal MA/CrossLookBack, stops, targets, sizing, orders,
and fills were not changed.

## 5. Final Daily Session Rule

Canonical D1 is reconstructed only from completed normalized H1:

```text
timezone: America/New_York (ZoneInfo)
open: previous 17:00 New York
close: current 17:00 New York
OHLC: first H1 open, maximum H1 high, minimum H1 low, last H1 close
```

Summer closes are 21:00 UTC and winter closes are 22:00 UTC. The active 50
stored sessions are summer rows at 21:00 UTC, all tagged
`America/New_York`; deterministic winter and March/November transition tests
prove 22:00 and 23/25-hour sessions. SQL independently recomputed all 50 OHLC
rows from their H1 members: zero mismatches and zero incomplete sessions.

Twelve Data native `1day` is not accepted as canonical input. The provider
documentation says the timezone parameter is ignored for daily intervals and
daily data is returned in exchange-local time; its timezone guide lists Forex
default as Australia/Sydney. This is not a guaranteed New York-close boundary.
The prior active sample confirmed the mismatch: all six provider-derived D1
rows closed at 00:00 UTC. See [Twelve Data advanced API documentation](https://twelvedata.com/docs/advanced)
and [Twelve Data timezone guide](https://support.twelvedata.com/en/articles/5745849-timezones).

Runtime paths now share `NewYorkDailyAggregator`: coordinator production,
historical replay, rebuild/backfill, and tests. Provider historical and smoke
methods explicitly raise for D1; the smoke command requests H1/H4 only.
`micro_daily_filter.py` and `indicators.py` were inspected and already matched
the formula, so they were not modified.

## 6. Broker-Time Mapping

```text
Summer: New York 17:00 = 21:00 UTC = broker 00:00 at UTC+3
Winter: New York 17:00 = 22:00 UTC = broker 00:00 at UTC+2
```

No fixed broker UTC+3 offset is used. Repository-wide broker/DST searches
found no selected-broker statement or log proving the winter UTC+2 switch.
Broker DST behaviour is therefore **EXTERNALLY UNVERIFIED**; the database
authority remains America/New_York 17:00 regardless of broker evidence.

## 7. Code Changes

The principal implementation points are:

- `session_boundaries.py`: IANA New York close/session bounds and expected FX
  weekend logic.
- `daily_aggregator.py`: validated H1 membership, DST-aware coverage, and D1
  OHLC construction.
- `coordinator.py`, `historical_replay.py`, and `daily_rebuild.py`: one shared
  aggregation/evaluation policy with ten D1 ATR inputs.
- `twelve_data_provider.py` and `twelve_data_smoke.py`: H1 depth increased and
  native D1 retrieval explicitly blocked.
- `repository.py` and rebuild CLIs: atomic replacement, backup, rollback,
  stale-projection replacement, active-path confirmation, and idempotency.
- `scripts/start_phase3b_observation.ps1`: protected frontend/backend key
  handoff without copying or printing the secret.

The complete per-file inventory is in Appendix A.

## 8. Configuration Changes

Ignored `backend/.env` now explicitly identifies the live database, isolated
replay database, EUR/USD, and H1/H4/D1 runtime context. The provider credential
was preserved and never displayed. `backend/.env.example` and `Settings`
document/validate the replay path. The launcher sets Twelve Data mode,
runtime enabled, and synthetic seeding disabled, and imports only the internal
dashboard key from ignored `frontend/.env.local` when backend configuration
does not override it.

## 9. Database Backup and Rebuild

Initial stopped-database checks: `integrity_check=ok`, `quick_check=ok`.
Initial active byte checksum was
`faca2b72f253f20a052e62795c8d15c60d0d725bc01d4bd3454e1049865bba5a`.

Backups used during the controlled workflow:

- Initial point-in-time:
  `var/spect8_phase1.backup.20260804T144323539205Z.sqlite3`
  (`08e216c3cd2a909d529b221c9dcba2ad1d2dbdc061230a12dfd44cf40619bf5c`).
- Deep-history backfill preimage:
  `var/spect8_phase1.backup.20260804T150811651029Z.sqlite3`
  (`00d29a288e3f2ad145875e4f6f93b52c48ea5a44017413eded144bdc6e9e5e09`).
- Final authoritative rebuild preimage:
  `var/spect8_phase1.backup.20260804T150837040156Z.sqlite3`
  (`4db8724babae5e2beaf0b76b29adff9613b4b33268c6877a24cea26417a4e04f`).

Authenticated backfill accepted 1,719 H1 and 430 H4 bars from 2026-05-25
through 2026-08-04 and inserted 630 missing source rows. It never requested
D1. The final atomic rebuild replaced 35 interim D1 rows with 50 canonical
rows and rebuilt 1,883 evaluations, 11,369 events, and two Twelve Data current
statuses. The immediately repeated apply reported `changed=false`, zero
evaluations/events/statuses, and no backup.

## 10. Database Before/After Evidence

| Evidence | Before task | Final after runtime smoke |
| --- | ---: | ---: |
| H1 rows | 32 | 1,720 |
| H4 rows | 30 | 430 |
| D1 rows | 6 | 50 |
| D1 range | 2026-07-29 00:00Z–2026-08-03 00:00Z | 2026-05-26 21:00Z–2026-08-03 21:00Z |
| D1 close distribution | 00:00Z: 6 | 21:00Z: 50 |
| D1 timezone | UTC: 6 | America/New_York: 50 |
| Old midnight D1 | 6 | 0 |
| Duplicate D1 closes | 0 | 0 |
| Incomplete/rejected/quarantined sessions | not canonical | 0 / 0 / 0 |
| Processed rows | 6 | 1,886 (1,883 rebuild + live/pre-existing) |
| Events | 38 | 11,389 (11,369 rebuild + live/pre-existing) |
| Statuses | 4 | 4 (two live + two preserved synthetic) |
| SQLite integrity | ok | ok |

Old deployment D1 rows removed/superseded: 6. Final canonical D1 rows: 50.
Final stopped database SHA-256 after smoke:
`35ea10853cbfaae2dc2808ab59d15de2322edcf9d3cb9647abd233561cd3ade6`.
There are no order/fill tables or execution path; API reports zero/zero.

## 11. Thirty-Day Validation

Machine-readable evidence:
`docs/validation/MICRO_DAILY_V1_0_3_PRODUCTION_READINESS_2026-08-04.json`.

- 30 completed New York sessions: 2026-06-23 21:00Z through 2026-08-03
  21:00Z.
- 1,253 evaluations: H1 1,002; H4 251.
- Every row records UTC/New York signal time, all eligible/selected D1 closes,
  ATR(5), buffer, raw extremes, levels, 21-bar extremes, and both matches.
- Equal-close inclusion: 30 H1 and 30 H4 cases.
- Forming signal, forming D1, and future D1 rejection: all true.
- Both replay runs: completed, zero duplicate evaluations, zero quarantines,
  zero orders/fills.

Provider runs and cached deterministic reruns:

| Window | Live run | Dataset fingerprint | Digest | Cached rerun |
| --- | --- | --- | --- | --- |
| first | `4e165699a2ea46f089f58269a41da3f2` | `22f658baccad169905caf21699c7f05b943fe901a3675214f8683e0999adb448` | `ac7abe15c755ca0180cf5ca7ca170526fd9761ac391ac7a8aaea99582905ac3c` | `9a667498d872409aad09712175f76462` |
| second | `54c31d28dbe04312b022fed46d1bdb60` | `47f8cb7542e700ada8f23d458452c331445a4db3f355053799f24bfb609c0d19` | `bc90d21d689a66c96d0ab207dfd6870b8123dcadc4b9f8ed027bc40f8b86a4a5` | `bd63536b2dcb4ed491dcb9cd496a972e` |

Cached rerun fingerprints/digests exactly match their live-source runs. Replay
warm-up is 21 calendar days (H1 requests two additional days), sufficient to
reconstruct the same latest-ten D1 Wilder state as continuous production.

## 12. Manual Calculation Evidence

The JSON contains every component, including high-low/high-previous-close/
low-previous-close values for each true range. Key independent examples:

| Required case | Signal | ATR evidence | Level comparison | Result |
| --- | --- | --- | --- | --- |
| Buy PASS | H1 2026-06-23 21:00Z | TRs `0.00852,0.00324,0.00635,0.00448,0.01391,0.00784,0.00632,0.00575,0.00626`; seed `0.00730`; recurrence `0.007408→0.0071904→0.00690232→0.006773856` | D1 low `1.1376`; buffer `0.00033869280`; level `1.13793869280`; recent low `1.1376 <= level` | PASS |
| Buy FAIL | H1 2026-06-25 11:00Z | seed `0.007164`; recurrence `0.0069952→0.00674616→0.006648928→0.0065371424` | D1 low `1.13239`; buffer `0.000326857120`; level `1.132716857120`; recent low `1.13368 > level` | FAIL |
| Sell FAIL | H1 2026-06-23 21:00Z | ATR `0.006773856` | D1 high `1.14767`; level `1.14733130720`; recent high `1.14386 < level` | FAIL |
| Sell PASS | H4 2026-06-23 21:00Z | ATR `0.006773856` | D1 high `1.14767`; level `1.14733130720`; recent high `1.1483 >= level` | PASS |
| Equal H1/D1 | H1 2026-06-23 21:00Z | latest D1 includes 2026-06-23 21:00Z | inclusive endpoint | INCLUDED |
| Equal H4/D1 | H4 2026-06-23 21:00Z | latest D1 includes 2026-06-23 21:00Z | inclusive endpoint | INCLUDED |

All six independently recomputed results agree with production, replay,
persistence, and the frozen independent reference. The reference serializes
numbers to 10 decimal places; comparison is made at that published precision,
while production/replay/manual comparisons use exact Decimal values.

## 13. Production/Replay/Persistence Parity

Across all 1,253 evaluations:

```text
direct evaluator == replay: true
independent manual == direct: true
persisted FILTER_EVALUATED == direct: true
forming/future exclusions: true
```

H1/H4 and Buy/Sell outcomes are stored independently. Repeated evaluation and
cached replay digests are deterministic. Unit evidence also calls the same
mathematical filter repeatedly and proves there is no hidden consumption
state.

## 14. Documentation and Authority

Active frozen file was retained, not rewritten during final readiness closure.
SHA-256:
`04247a1bebc25d5b57f468752f148bdfe5923e7a751e3749220627bdcfb2ad8e`.
Operational documentation now covers formula/indexing/equality, New York/DST
and broker mapping, H1 provenance, native-D1 prohibition, guarded dry-run/
apply, backup/rollback, validation, midnight-data recognition, and live DB
verification. Historical Phase 2C/3A/3B documents retain their old observations
but are visibly marked as superseded where midnight D1 would otherwise look
current.

## 15. Golden Dataset Evidence

- Manifest authority: `../Spect8_Micro_Daily_v1_0_3_FROZEN.md`.
- Dataset: `1.0.3`.
- Dedicated New York equal-close H1 and H4 cases exist with
  `session_timezone=America/New_York`.
- Older equality cases remain for traceability; unrelated expected files were
  not regenerated.
- Independent calculator does not import production code.
- 241 SHA-256 entries checked, zero mismatches.
- Golden suite: 313 passed.

## 16. Focused Test Results

Focused command covering New York/DST aggregation, production evaluator,
historical replay, provider, persistence/API, rebuild/rollback/idempotency, and
golden/reference: **494 passed**, one existing Starlette/httpx deprecation
warning. Dedicated tests include summer/winter, March/November DST transitions,
23/25-hour aggregation, missing/duplicate H1, forming/future/equal close,
Wilder seed/recurrence, ten-D1 parity, active guard, backup, rollback, stale
projection replacement, and native D1 blocking.

## 17. Complete Suite and Build Results

- Complete Python suite: **511 passed**, one existing deprecation warning.
- Frontend Vitest: **21 passed** in five files.
- `tsc --noEmit`: PASS.
- Next.js 16.2.12 production build: PASS; nine routes/pages generated.
- Golden checksum verification: 241/241, zero mismatches.
- `git diff --check`: PASS (only Windows LF/CRLF notices, no errors).
- Final SQLite `integrity_check`: `ok`.

## 18. Runtime Smoke Result

Actual Uvicorn and production Next.js processes ran on 127.0.0.1:8000 and
port 3000 against the rebuilt active DB. Results:

- `/health`: `ok`, `PHASE_3B_TWELVE_DATA_RUNTIME`, Twelve Data, non-synthetic,
  provider `HEALTHY`, latest completed provider close 2026-08-04 15:00Z.
- Protected dashboard: HTTP 200 with EUR/USD, healthy state, H1/H4 content.
- Latest persisted/API candles: H1 15:00Z, H4 13:00Z, reconstructed D1
  2026-08-03 21:00Z.
- API H1/H4 idempotency key, close, filter, signal, market values, and dashboard
  state exactly matched SQLite.
- H1 filter: Buy false/Sell false. H4: Buy false/Sell true.
- Execution disabled; orders 0; fills 0.
- First startup added the newly completed 15:00 H1 and one evaluation. Second
  real startup poll inserted 0 bars, created 0 evaluations/events, prevented
  two duplicates, and reported no issues.
- D1 midnight count 0; duplicate D1 count 0.
- Smoke processes were stopped; ports 3000/8000 are clear.

## 19. Remaining Risks

Only the selected broker's external winter server-time behaviour is not
proven. Obtain a winter broker log/chart/server-time statement showing midnight
at UTC+2 before removing the verdict qualifier. This does not affect the
canonical database rule. The existing Starlette/httpx warning is non-functional
technical debt. Historical replay validates deterministic scanner behaviour,
not profitability.

## 20. Exact Commands for Future Rebuild and Validation

Run from repository root. Never print `backend/.env`.

```powershell
# Backfill dry-run; review, then use --apply plus exact confirmation.
.\.venv\Scripts\python.exe -m backend.app.market_data.history_backfill_cli `
  --database var/spect8_phase1.sqlite3 `
  --start 2026-05-25T00:00:00Z --end 2026-08-04T15:00:00Z `
  --env-file backend/.env --dry-run

# Rebuild dry-run.
.\.venv\Scripts\python.exe -m backend.app.market_data.daily_rebuild_cli `
  --database var/spect8_phase1.sqlite3 `
  --as-of 2026-08-04T15:00:00Z --dry-run

# Guarded active apply (repeat once to prove changed=false).
.\.venv\Scripts\python.exe -m backend.app.market_data.daily_rebuild_cli `
  --database var/spect8_phase1.sqlite3 `
  --as-of 2026-08-04T15:00:00Z `
  --confirm-active-database E:\Work\The-System\Spect8_HedgeFund_Dashboard\var\spect8_phase1.sqlite3 `
  --apply

# Evidence from the recorded immutable replay datasets.
.\.venv\Scripts\python.exe scripts/generate_micro_daily_readiness_evidence.py `
  --database var/spect8_phase1.sqlite3 `
  --replay-database var/spect8_historical_replay.sqlite3 `
  --run-id 4e165699a2ea46f089f58269a41da3f2 `
  --run-id 54c31d28dbe04312b022fed46d1bdb60 `
  --sessions 30 `
  --output docs/validation/MICRO_DAILY_V1_0_3_PRODUCTION_READINESS_2026-08-04.json

# Tests/builds.
.\.venv\Scripts\python.exe -m pytest -q
Push-Location frontend
npm.cmd run test:run
npm.cmd run type-check
npm.cmd run build
Pop-Location
.\.venv\Scripts\python.exe -m pytest -q tests/test_golden_dataset.py
git diff --check

# Actual runtime.
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/start_phase3b_observation.ps1 -Port 8000
```

For rollback, stop writers, preserve the rejected DB, restore the selected
timestamped backup to `var/spect8_phase1.sqlite3`, run SQLite
`PRAGMA integrity_check`, restart, and reconcile API/SQLite again.

## Appendix A — changed-file inventory

Each path below is exact. Files sharing a row share the named section/reason.
`.vscode/settings.json` is intentionally absent because it was unrelated and
untouched.

| Exact path(s) | Function/class/section | Reason |
| --- | --- | --- |
| `README.md`; `Spect8_Micro_Daily_v1_0_2_FROZEN.md`; `Spect8_Micro_Daily_v1_0_3_FROZEN.md` | active authority/readme | Version chain, final rule, runtime/replay pointers. |
| `backend/.env.example`; ignored `backend/.env`; `backend/app/config.py` | `Settings` / environment | Explicit isolated replay/live paths and deployment selection. |
| `backend/app/engine/__init__.py`; `backend/app/engine/strategy.py` | exports; `Spect8StrategyEvaluator` | Activate v1.0.3 and inclusive complete D1 eligibility. |
| `backend/app/market_data/session_boundaries.py` | New York boundary functions | ZoneInfo-based 17:00 sessions and FX weekend dates. |
| `backend/app/market_data/daily_aggregator.py` | `NewYorkDailyAggregator` | Complete-H1 validation and canonical D1 OHLC. |
| `backend/app/market_data/closed_bar.py` | `ClosedBarDetector`; history constants | Ten-D1 ATR input with six-bar minimum and equality validation. |
| `backend/app/market_data/coordinator.py` | `MarketDataCoordinator.run_once` | Shared D1 aggregation and ten-bar evaluator context. |
| `backend/app/market_data/models.py`; `backend/app/market_data/replay_provider.py` | provider history/models | Carry aggregation findings and inclusive replay context. |
| `backend/app/market_data/twelve_data_provider.py`; `backend/app/market_data/twelve_data_smoke.py` | fetch/history/smoke | Deeper H1 history; block native D1; smoke H1/H4 only. |
| `backend/app/market_data/daily_rebuild.py`; `backend/app/market_data/daily_rebuild_cli.py` | `DailyRebuilder`; CLI guard | Atomic D1/projection replacement, backup, active confirmation, status counts. |
| `backend/app/market_data/history_backfill_cli.py` | guarded CLI | Authenticated normalized H1/H4-only history backfill. |
| `backend/app/repository.py`; `backend/app/service.py` | `SQLiteProjectionRepository`; event projection | Atomic canonical/projection replacement and reusable event generation. |
| `backend/app/historical_replay.py`; `backend/app/historical_replay_api.py` | replay dataset/repository/service/API models | Isolated H1-derived D1 replay, 21-day warm-up, deterministic persistence. |
| `backend/app/main.py` | `create_app`; historical routes | Wire isolated replay service/API without changing execution. |
| `backend/tests/test_market_data_foundation.py`; `backend/tests/test_production_engine.py`; `backend/tests/test_twelve_data_provider.py`; `backend/tests/test_walking_skeleton.py` | unit/integration cases | Equality, non-consuming filter, provider aggregation, API/persistence parity. |
| `backend/tests/test_new_york_daily.py`; `backend/tests/test_historical_replay.py` | DST/rebuild/replay suites | Boundary, OHLC, quarantine, rollback, idempotency, ten-D1 parity. |
| `golden/reference/calculator.py`; `golden/tools/generate_golden_cases.py`; `golden/tools/generate_checksums.py` | independent calculator/generators | NY weekend gaps, inclusive equality, v1.0.3 cases/checksums. |
| `golden/manifest.json`; `golden/schemas/manifest.schema.json`; `golden/CHECKSUMS.sha256`; `golden/README.md`; `tests/test_golden_dataset.py` | frozen dataset contract | Activate 1.0.3, enumerate cases, validate 241 immutable hashes. |
| `golden/cases/equal_close_filter_boundary_h1/signal_bars.csv`; `golden/cases/equal_close_filter_boundary_h1/daily_bars.csv`; `golden/cases/equal_close_filter_boundary_h1/instrument.json`; `golden/cases/equal_close_filter_boundary_h1/expected.json`; `golden/cases/equal_close_filter_boundary_h1/calculation_ledger.md` | legacy H1 equality case | Preserve deterministic inclusive boundary traceability. |
| `golden/cases/equal_close_filter_boundary_h4/signal_bars.csv`; `golden/cases/equal_close_filter_boundary_h4/daily_bars.csv`; `golden/cases/equal_close_filter_boundary_h4/instrument.json`; `golden/cases/equal_close_filter_boundary_h4/expected.json`; `golden/cases/equal_close_filter_boundary_h4/calculation_ledger.md` | legacy H4 equality case | Preserve deterministic inclusive boundary traceability. |
| `golden/cases/new_york_close_filter_boundary_h1/signal_bars.csv`; `golden/cases/new_york_close_filter_boundary_h1/daily_bars.csv`; `golden/cases/new_york_close_filter_boundary_h1/instrument.json`; `golden/cases/new_york_close_filter_boundary_h1/expected.json`; `golden/cases/new_york_close_filter_boundary_h1/calculation_ledger.md` | New York H1 equality case | Prove 17:00 New York equal-close inclusion. |
| `golden/cases/new_york_close_filter_boundary_h4/signal_bars.csv`; `golden/cases/new_york_close_filter_boundary_h4/daily_bars.csv`; `golden/cases/new_york_close_filter_boundary_h4/instrument.json`; `golden/cases/new_york_close_filter_boundary_h4/expected.json`; `golden/cases/new_york_close_filter_boundary_h4/calculation_ledger.md` | New York H4 equality case | Prove 17:00 New York equal-close inclusion. |
| `frontend/lib/api-types.ts`; `frontend/lib/backend.ts`; `frontend/proxy.ts` | replay API types/client/protection | Typed protected replay/dashboard access. |
| `frontend/app/api/historical-replays/route.ts`; `frontend/app/historical-replay/page.tsx`; `frontend/app/historical-replay/loading.tsx` | Next routes/page | Protected historical replay UI and loading state. |
| `frontend/components/historical-replay-dashboard.tsx`; `frontend/components/dashboard.tsx`; `frontend/app/globals.css` | replay/live components/styles | Expose replay evidence and link from live read-only dashboard. |
| `frontend/tests/historical-replay.test.tsx`; `frontend/tests/protection-contract.test.ts` | frontend contracts | Render/API/auth and server-only execution boundaries. |
| `docs/PHASE2A_PRODUCTION_ENGINE.md`; `docs/PHASE2B_MARKET_DATA_FOUNDATION.md`; `docs/PHASE2C_TWELVE_DATA_PROVIDER.md`; `docs/PHASE3A_EUR_USD_DASHBOARD.md`; `docs/PHASE3B_CLIENT_ACCEPTANCE.md`; `docs/PHASE3B_OBSERVATION_RUNBOOK.md`; `docs/PHASE3B_UAT_CANDIDATE.md` | historical/current phase notes | Correct active equality/NY rule while retaining superseded history. |
| `docs/NEW_YORK_DAILY_V1_0_3.md`; `docs/PHASE3B_HISTORICAL_REPLAY_VALIDATION.md` | operations/replay records | Rebuild, rollback, recognition, runtime, and replay isolation guidance. |
| `scripts/start_phase3b_observation.ps1`; `scripts/generate_micro_daily_readiness_evidence.py` | launcher/evidence generator | Safe shared auth configuration and exact 30-session independent proof. |
| `docs/validation/MICRO_DAILY_V1_0_3_PRODUCTION_READINESS_2026-08-04.json`; `docs/validation/MICRO_DAILY_V1_0_3_PRODUCTION_READINESS_2026-08-04.md` | machine/human readiness evidence | Persist complete audit, calculations, commands, and verdict. |
