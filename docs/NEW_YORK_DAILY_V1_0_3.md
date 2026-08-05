# New York-close Daily implementation v1.0.3

The active canonical D1 boundary is 17:00 `America/New_York`. The UTC close is
derived with `zoneinfo` and therefore normally changes between 21:00Z and
22:00Z as New York daylight saving time changes.

## Data path

```text
Twelve Data completed H1
  -> CandleNormalizer (canonical UTC H1)
  -> NewYorkDailyAggregator (17:00-to-17:00 membership and validation)
  -> canonical D1 persistence (session_timezone=America/New_York)
  -> Spect8StrategyEvaluator (shared H1/H4 D1 context)
```

Provider-native `1day` retrieval is disabled for strategy and historical
replay inputs. Replay calls the same `NewYorkDailyAggregator`; it has no
replay-only Daily approximation. A complete D1 is eligible when its close is
less than or equal to the evaluated signal close.

The aggregator rejects mixed identity, wrong timeframe, incomplete, future,
out-of-order, duplicate, overlapping, non-hourly, or incomplete-coverage H1
input with typed reasons. A rejected session produces no D1. Expected weekend
closure outside active Forex sessions is not classified as a missing candle.

## Dashboard session naming and timestamps

The dashboard names a canonical D1 session by the calendar date of its closing
17:00 `America/New_York` boundary. For example, the session opening at 17:00
EDT on 2026-08-03 and closing at 17:00 EDT on 2026-08-04 is the **04 Aug 2026
New York Daily Session**. This is the same `session_date` convention used by
`NewYorkDailyAggregator`; it is not inferred from the UTC date in the browser.

Canonical timestamps remain stored and transported in UTC. Strategy-session
membership continues to use `America/New_York`. Dashboard presentation is a
separate concern: its primary timezone is configured with
`DASHBOARD_DISPLAY_TIMEZONE` and its label with
`DASHBOARD_DISPLAY_TIMEZONE_LABEL`. The local default is `Europe/Athens` /
`Broker Time`, which is UTC+3 in summer and UTC+2 in winter under IANA rules.
New York `EST`/`EDT` and UTC remain visible as secondary evidence. No fixed
offset arithmetic is used.

## Latest Filter Audit reconstruction

Legacy status rows created before `filter_audit` remain valid decisions but do
not contain the evidence payload. The guarded reconstruction command selects
the latest persisted H1 and H4 signal closes, loads the latest 30 eligible
canonical signal bars and ten eligible reconstructed D1 sessions, and calls
`Spect8StrategyEvaluator` twice. It refuses missing history, non-determinism,
or any change outside `filter_audit`. Applying updates both status payloads in
one transaction without replacing processed-bar identities or event history.

```powershell
.\.venv\Scripts\python.exe -m backend.app.filter_audit_rebuild_cli `
  --database var/spect8_phase1.sqlite3 --dry-run
```

For the active database, stop its runtime writer, replace `--dry-run` with
`--apply`, and add `--confirm-active-database` followed by the exact resolved
database path. Apply creates a point-in-time SQLite backup first. A repeated
apply is deterministic and reports `changed=false`.

## Guarded history backfill and rebuild

Neither workflow requests provider-native D1. Backfill authenticates Twelve
Data H1/H4, normalizes it, and inserts only missing canonical source bars. An
active database apply requires `--confirm-active-database` to repeat the exact
resolved target. Each apply creates a SQLite point-in-time backup first.

```powershell
.\.venv\Scripts\python.exe -m backend.app.market_data.history_backfill_cli `
  --database var/spect8_phase1.sqlite3 `
  --start 2026-05-25T00:00:00Z --end 2026-08-04T15:00:00Z `
  --env-file backend/.env --dry-run
```

After review, replace `--dry-run` with both `--apply` and:

```text
--confirm-active-database <absolute-path-to-var\spect8_phase1.sqlite3>
```

Dry-run the D1/projection replacement:

```powershell
.\.venv\Scripts\python.exe -m backend.app.market_data.daily_rebuild_cli `
  --database var/spect8_phase1.sqlite3 `
  --as-of 2026-08-04T15:00:00Z `
  --dry-run
```

For the active target, replace `--dry-run` with `--apply` and the same exact
confirmation argument shown above. Valid H1/H4 rows are never deleted. The
transaction atomically replaces D1 plus dependent processed-bar, event, and
status projections; injected failures roll it back. A second identical apply
must report `changed=false`, zero rebuilt evaluations/events/statuses, and no
backup because there is no change.

## Recognition, runtime verification, and rollback

Old provider-native data is recognizable by D1 closes at `00:00:00Z`, a
non-New-York `session_timezone`, or OHLC that cannot be reproduced from all
completed H1 members between the stored session bounds. A valid summer row
closes at `21:00:00Z`; a valid winter row closes at `22:00:00Z`.

`backend/.env` is the ignored deployment configuration loaded by
`scripts/start_phase3b_observation.ps1`. Confirm it names
`SPECT8_DATABASE_PATH=var/spect8_phase1.sqlite3`; `Settings.from_environment`
resolves that path and rejects a replay database equal to the live database.
The launcher imports the dashboard internal key from the ignored frontend
environment only when the backend file does not override it; it never prints
the value.

Before rollback, stop the backend and verify no process owns the database.
Preserve the failed database for diagnosis, then copy the selected timestamped
`spect8_phase1.backup.<UTC timestamp>.sqlite3` over
`var/spect8_phase1.sqlite3`. Run `PRAGMA integrity_check`, restart with
`scripts/start_phase3b_observation.ps1`, and reconcile `/health`, `/dashboard`,
and SQLite before accepting the rollback.

Validation commands are recorded in the dated production-readiness report and
its machine-readable companion under `docs/validation/`.
