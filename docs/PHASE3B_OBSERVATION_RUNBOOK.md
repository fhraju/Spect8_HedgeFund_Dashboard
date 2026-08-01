# Phase 3B Observation and Deployment Runbook

## Boundary

This runbook is local/private preparation only. It does not authorize public
deployment, paid hosting, infrastructure purchase, public DNS, or exposure of
the service.

## Environment

Keep secrets only in ignored files or the process environment:

```text
backend/.env
frontend/.env.local
```

Required backend settings:

```text
TWELVE_DATA_API_KEY=<rotated private value>
SPECT8_INTERNAL_API_KEY=<shared private backend/frontend value>
SPECT8_DATABASE_PATH=var/spect8_phase3b.sqlite3
SPECT8_MARKET_DATA_PROVIDER=twelve_data
SPECT8_MARKET_DATA_RUNTIME_ENABLED=true
SPECT8_AUTO_SEED_SYNTHETIC=false
SPECT8_MARKET_DATA_POLL_SECONDS=300
SPECT8_MARKET_DATA_SAFETY_DELAY_SECONDS=30
SPECT8_RUNTIME_LOG_PATH=var/spect8_runtime.log
SPECT8_RUNTIME_LOG_MAX_BYTES=5000000
SPECT8_RUNTIME_LOG_BACKUP_COUNT=5
```

`backend/.env` is loaded by the observation script without printing values.
The runtime log is structured JSON, sanitized, and rotated at the configured
size.

## Start a 3–5 market-day observation

Run in the foreground so Ctrl+C reaches FastAPI’s graceful shutdown:

```powershell
.\scripts\start_phase3b_observation.ps1
```

Start the frontend separately:

```powershell
Set-Location frontend
npm.cmd run start
```

The backend immediately performs bounded catch-up, then wakes at most every
configured health interval and shortly after each hourly UTC boundary.
Only H1/H4/D1 series whose expected completed boundary advanced are requested.

## Check current status

Sanitized database-backed status:

```powershell
.\.venv\Scripts\python.exe -m backend.app.observation_cli status `
  --database var\spect8_phase3b.sqlite3
```

Protected API status:

```text
GET /runtime/status
X-Spect8-Internal-Key: <private internal key>
```

Never place either key in a URL or command history intended for sharing.

## Stop gracefully

Press Ctrl+C in the backend observation terminal. The lifespan handler signals
the scheduler, waits for any bounded in-flight request, records the session
end, releases the single-runtime lock, and exits.

Do not kill the process while a normal graceful stop remains available.

## Export a sanitized report

```powershell
.\.venv\Scripts\python.exe -m backend.app.observation_cli report `
  --database var\spect8_phase3b.sqlite3 `
  > var\phase3b-observation-report.json
```

The report contains UTC bounds, uptime, sessions/restarts, poll and request
counts, per-timeframe attempts/discoveries, evaluations/events, duplicate
prevention, health transitions, unhealthy/recovery periods, latest completed
candles, request projections, and zero orders/fills. It contains no key,
authenticated URL, authorization header, or raw provider payload.

## Evidence procedure at genuine boundaries

For at least three H1 closes, one H4 close, and D1 if observed:

1. Record expected close and runtime poll timestamps.
2. Query `GET /runtime/status` and `GET /dashboard`.
3. Compare the dashboard evaluation to `instrument_status.status_json`.
4. Confirm the evaluated signal close equals the newly completed close.
5. Confirm no candle with close after the trigger entered the evaluation.
6. Confirm D1 context close is strictly earlier than the trigger.
7. Confirm exactly one new applicable evaluation and expected event trace.
8. Confirm H1/H4 independence and zero orders/fills.

For controlled failure, use a deterministic fixture/test transport—not the
live key—and record transition to unavailable/stale, preservation of persisted
evaluations, recovery state, and recovery duration.

Do not claim unobserved boundaries.

## Private deployment recommendation

- Run backend and frontend as separate supervised processes under a
  non-administrator account.
- Permit only the reverse proxy to reach application ports.
- Terminate HTTPS at a maintained reverse proxy; never expose plain HTTP
  publicly.
- Keep `SPECT8_INTERNAL_API_KEY`, session secret, password hash, and provider
  key in the supervisor’s secret environment.
- Persist `var/*.sqlite3`, SQLite WAL/SHM files, and rotated runtime logs on a
  durable volume.
- Back up SQLite using the SQLite online backup mechanism or a quiesced copy;
  never copy only the main file while WAL writes are active.
- Health-check `/health`; use authenticated `/runtime/status` for operational
  monitoring.
- Retain the configured rolling log set and apply host-level retention to
  exported observation reports.

## Restart

1. Stop backend gracefully.
2. Confirm no process owns the configured port.
3. Back up the SQLite database.
4. Start the same committed version and database path.
5. Confirm one runtime owns the database and `lock_conflict=false`.
6. Confirm catch-up processes unseen completed candles once.
7. Confirm counts do not duplicate when no boundary was missed.

## Rollback

1. Stop backend/frontend gracefully.
2. Preserve the current database and logs as rollback evidence.
3. Restore the prior application commit.
4. Use a database backup known to be compatible with that commit.
5. Start privately and run health/API smoke checks.

Never use `git reset --hard` against a working repository with unpreserved
changes. No external rollout should occur without explicit authorization.
