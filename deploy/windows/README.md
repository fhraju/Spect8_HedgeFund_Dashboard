# Windows Server demo deployment

This directory prepares the first deployment at
`https://spect8dashboard.digitallabb.com`. It does not perform a deployment.
The production layout used below is:

```text
C:\Spect8\app\       Git checkout
C:\Spect8\config\    protected environment configuration
C:\Spect8\data\      SQLite databases
C:\Spect8\exports\   workstation-side transfer backups, if desired
C:\Spect8\logs\      service and Caddy logs
```

FastAPI listens only on `127.0.0.1:8000`. Next.js listens only on
`127.0.0.1:3000`. Caddy is the only public listener on ports 80 and 443.
Caddy sends every public route, including `/api/*`, to Next.js. Next.js calls
FastAPI over loopback with `SPECT8_INTERNAL_API_KEY`; FastAPI is not exposed by
Caddy.

## Runtime requirements

- 64-bit Python 3.12; install `backend/requirements.txt` into `.venv`.
- Node.js satisfying Next.js 16's `>=20.9.0` requirement; use a supported LTS
  release and install the locked tree with `npm.cmd ci`.
- Caddy with permission to bind ports 80 and 443 and write `C:\Spect8\logs`.
- One Windows application account with modify access to `C:\Spect8\data`.

Build commands from `C:\Spect8\app`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Set-Location frontend
npm.cmd ci
npm.cmd run build
Set-Location ..
```

Production commands, configured as two services with restart-on-failure:

```powershell
# Backend service: exactly one worker and no --reload.
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app `
  --host 127.0.0.1 --port 8000 --workers 1

# Frontend service, from C:\Spect8\app\frontend.
npm.cmd run start -- --hostname 127.0.0.1 --port 3000
```

Load the variables from a protected copy of `env.production.example` into each
service process. Do not place the populated file in the Git checkout. The
backend and frontend must receive the same `SPECT8_INTERNAL_API_KEY`; only the
frontend needs the dashboard password hash and session secret. None of these
values belongs in a `NEXT_PUBLIC_*` variable.

## Database behavior and safety

The normal development default remains `var\spect8_phase1.sqlite3`. Production
requires an absolute `SPECT8_DATABASE_PATH` outside the repository. When
`SPECT8_APPLICATION_ENVIRONMENT=production`, FastAPI refuses to start if that
file is missing, so a typo cannot silently create an empty dashboard.

Normal FastAPI lifespan initialization runs idempotent `CREATE TABLE IF NOT
EXISTS`, index creation, and the repository's additive legacy migrations. It
also enables SQLite WAL mode. The import validator deliberately does none of
those things—it opens the copied database read-only and validates it first.

For an intentionally new empty installation only, initialization is explicit:

```powershell
.\.venv\Scripts\python.exe deploy\windows\initialize-database.py `
  --database C:\Spect8\data\spect8.db
```

That command refuses an existing file. It is not part of the copied-data demo
procedure.

## Stage 1: copied-data, offline demo

1. Stop the local poller or otherwise ensure no local writer is required. The
   exporter itself is safe against a live WAL database because it uses SQLite's
   backup API.
2. Export the configured local database without an ordinary file copy:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass `
     -File .\deploy\windows\export-database.ps1 `
     -Source "E:\Work\The-System\Spect8_HedgeFund_Dashboard\var\spect8_phase1.sqlite3" `
     -OutputDirectory "C:\Spect8\exports"
   ```

   Omitting `-OutputDirectory` writes under the Git-ignored
   `var\database-exports` directory. The command prints the exact timestamped
   output path, integrity results, important row counts, and latest bar and
   evaluation timestamps. It never imports provider code or calls Twelve Data.
3. Copy the generated `.sqlite3` file to the server using the approved manual
   transfer method. Place/rename it exactly as:

   ```text
   C:\Spect8\data\spect8.db
   ```

4. Grant the application account modify access to the database and its parent
   directory so SQLite can maintain `-wal` and `-shm` sidecars. Run validation
   as that intended account:

   ```powershell
   C:\Spect8\app\.venv\Scripts\python.exe `
     C:\Spect8\app\deploy\windows\validate-imported-database.py `
     --database C:\Spect8\data\spect8.db
   ```

   A successful validation reports `Integrity check: ok`, all required tables,
   non-zero copied-data tables, timestamps, `Schema migration: not run`, and
   `Provider/network calls: none`.
5. Start FastAPI in offline/copied-data mode with these exact controls:

   ```text
   SPECT8_APPLICATION_ENVIRONMENT=production
   SPECT8_DATABASE_PATH=C:\Spect8\data\spect8.db
   SPECT8_MARKET_DATA_PROVIDER=twelve_data
   SPECT8_AUTO_SEED_SYNTHETIC=false
   SPECT8_POLLING_ENABLED=false
   SPECT8_STARTUP_BACKFILL_ENABLED=false
   SPECT8_PROVIDER_DISCOVERY_ENABLED=false
   SPECT8_MARKET_DATA_RUNTIME_ENABLED=false
   SPECT8_MARKET_SCAN_ENABLED=false
   ```

   The copied dashboard data remains readable. `/health` reports
   `provider_health.state=POLLING_DISABLED` and
   `operations.polling_state=DISABLED_BY_CONFIGURATION`. No poll task,
   discovery call, startup catch-up, or provider request runs.
6. Start Next.js, then Caddy with `Caddyfile.template`. Verify locally before
   public DNS/TLS checks:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/health
   Invoke-WebRequest http://127.0.0.1:3000/login
   ```

   Log in through `https://spect8dashboard.digitallabb.com` and confirm the
   scanner and instrument pages display copied timestamps and evaluations.

## Stage 2: enable incremental live polling

After the Twelve Data quota resets:

1. Keep the validated database and static registry. Change only:

   ```text
   SPECT8_POLLING_ENABLED=true
   ```

   Keep `SPECT8_STARTUP_BACKFILL_ENABLED=false` and
   `SPECT8_PROVIDER_DISCOVERY_ENABLED=false`. The copied evaluation cursors and
   canonical timestamps remain the resume authority.
2. Restart the backend service only. Do not restart Caddy or Next.js.
3. Query authenticated `GET /runtime/status`. Verify `runtime.running=true`,
   `single_runtime_lock_acquired=true`, and `lock_conflict=false`.
4. Verify only one backend service process and one runtime session exist. Never
   use multiple Uvicorn workers while the coordinator runs inside FastAPI.
5. Review `runtime_poll_history`/runtime logs after the first scheduled cycle.
   Confirm persisted-current instruments were skipped and requests covered only
   unseen completed H1 bars. Do not invoke the universe validator, smoke tools,
   or historical replay creation during this quota-sensitive check.
6. If a full startup catch-up is deliberately required later, schedule it in a
   quota-approved window and temporarily set:

   ```text
   SPECT8_STARTUP_BACKFILL_ENABLED=true
   ```

   Restart only the backend, observe the bounded request budget, then restore
   the value to `false`.

## Request and route inventory

FastAPI has no prefix. `/health` is public on loopback. `/scanner`,
`/dashboard`, `/dashboard/{instrument_id}`, `/instruments`, `/statuses`,
`/filtered`, `/signals`, `/events`, `/runtime/status`, and historical replay
routes require `X-Spect8-Internal-Key`.

Browser requests use authenticated Next.js `/api/scanner`, `/api/dashboard`,
and `/api/historical-replays` routes. The signed `spect8_session` cookie guards
dashboard access. `BACKEND_URL=http://127.0.0.1:8000` is server-only.

Backend configuration inventory:

- Runtime/storage: `SPECT8_APPLICATION_ENVIRONMENT`, `SPECT8_DATABASE_PATH`,
  `SPECT8_HISTORICAL_REPLAY_DATABASE_PATH`, `SPECT8_RUNTIME_LOG_PATH`,
  `SPECT8_RUNTIME_LOG_MAX_BYTES`, and `SPECT8_RUNTIME_LOG_BACKUP_COUNT`.
- Provider/universe: `SPECT8_MARKET_DATA_PROVIDER`, `TWELVE_DATA_API_KEY`,
  `SPECT8_INSTRUMENT`, `SPECT8_TIMEFRAMES`, and
  `SPECT8_ENABLED_INSTRUMENT_IDS`.
- Startup/polling: `SPECT8_POLLING_ENABLED`,
  `SPECT8_STARTUP_BACKFILL_ENABLED`, `SPECT8_PROVIDER_DISCOVERY_ENABLED`,
  `SPECT8_MARKET_DATA_RUNTIME_ENABLED`, `SPECT8_MARKET_SCAN_ENABLED`,
  `SPECT8_AUTO_SEED_SYNTHETIC`, `SPECT8_MARKET_DATA_POLL_SECONDS`,
  `SPECT8_MARKET_DATA_SAFETY_DELAY_SECONDS`, and
  `SPECT8_MARKET_SCAN_AFTER_HOUR_SECONDS`.
- Request safety: `SPECT8_MARKET_DATA_REQUEST_MIN_INTERVAL_SECONDS`,
  `SPECT8_MARKET_DATA_MAX_REQUESTS_PER_MINUTE`,
  `SPECT8_MARKET_DATA_MAX_RETRIES_PER_INSTRUMENT`,
  `SPECT8_MARKET_DATA_STALE_AFTER_SECONDS`,
  `TWELVE_DATA_DAILY_CREDIT_LIMIT`, `MARKET_DATA_DAILY_OPERATIONAL_BUDGET`, and
  `MARKET_DATA_CREDIT_RESERVE`. Legacy unprefixed scan/rate aliases remain
  accepted for local compatibility, but production should use the prefixed
  names shown in `env.production.example`.
- Backend authentication: `SPECT8_INTERNAL_API_KEY`.

Frontend server-only configuration is `BACKEND_URL`, `DASHBOARD_ORIGIN`,
`SPECT8_INTERNAL_API_KEY`, `DASHBOARD_PASSWORD_HASH`, `SESSION_SECRET`, and the
optional display settings `DASHBOARD_DISPLAY_TIMEZONE` and
`DASHBOARD_DISPLAY_TIMEZONE_LABEL`.

Twelve Data can be contacted by:

- the in-process runtime when effective polling is enabled;
- retries in a polling cycle;
- an explicitly created historical replay with a live source;
- explicitly invoked smoke, provider-validation, or readiness scripts.

Normal runtime `discover_instruments()` is currently local registry metadata,
not HTTP. `SPECT8_PROVIDER_DISCOVERY_ENABLED=false` bypasses even that provider
method during startup as a forward-safe production guard. The separate
instrument-universe validation tool performs real discovery and must not be run
during Stage 1.

## Repeatable updates

For later releases: record `SPECT8_APPLICATION_VERSION`, stop the backend and
frontend services, update the checkout, rebuild the Python environment and
Next.js bundle from lockfiles, validate the existing external database, and
restart in Stage 1 mode first. Never replace or remove `C:\Spect8\data` as part
of a Git update. Enable polling only after offline verification succeeds.
