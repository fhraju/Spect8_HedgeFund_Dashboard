# Spect8 HedgeFund Dashboard

Protected read-only Market Scanner for the frozen
`SPECT8_MICRO_DAILY_V1_0` specification.

Phase 3A connects the Phase 2C Twelve Data adapter to a bounded polling
runtime, the existing coordinator and evaluator, durable projections, a typed
read-only API, and the protected `EUR/USD` dashboard. H1 and H4 remain
independent signal timeframes and D1 remains context only. Golden expected
results remain a test-only oracle. The scanner contains no trading,
backtesting, optimization, WebSockets, or multi-instrument expansion.

The frozen v1.0.1 clarification adds `CONFIRMED_BOTH` for simultaneous BUY and
SELL confirmations and preserves both directional calculations independently.

## Applications

- `backend/` — FastAPI, deterministic in-process event dispatch, and SQLite
  bar/event/status projections.
- `frontend/` — Next.js, TypeScript, Tailwind CSS, single-client login, and the
  protected dashboard.
- `golden/` — immutable synthetic test authority and independent reference
  oracle. Production code does not import it.
- `docs/mockup/` — approved mock-up captures and visual design specification.

## Local setup

Create the Python environment and install all backend/test dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Install frontend dependencies:

```powershell
Set-Location frontend
npm.cmd install
Set-Location ..
```

Generate a password hash:

```powershell
Set-Location frontend
npm.cmd run hash-password -- "choose-a-strong-client-password"
Set-Location ..
```

Copy `frontend/.env.local.example` to `frontend/.env.local`, replace every
placeholder, and use the same `SPECT8_INTERNAL_API_KEY` in both applications.

## Start locally

Terminal 1, from the repository root:

```powershell
$env:SPECT8_INTERNAL_API_KEY = "<same-long-random-key>"
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --port 8000
```

For the live EUR/USD slice, keep `TWELVE_DATA_API_KEY` only in the ignored
`backend/.env` or the process environment and configure:

```text
SPECT8_MARKET_DATA_PROVIDER=twelve_data
SPECT8_MARKET_DATA_RUNTIME_ENABLED=true
SPECT8_MARKET_DATA_POLL_SECONDS=300
SPECT8_AUTO_SEED_SYNTHETIC=false
```

The polling interval is validated between 60 and 900 seconds. Provider
timeouts, retry bounds, candle completion rules, and H4/D1 boundaries remain
owned by the Phase 2C adapter.

Terminal 2:

```powershell
Set-Location frontend
npm.cmd run dev
```

Open `http://localhost:3000`. The backend is available at
`http://127.0.0.1:8000`; its data endpoints require the internal server key.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest
Set-Location frontend
npm.cmd run type-check
npm.cmd run test:run
npm.cmd run build
```

See [Phase 1 architecture](docs/PHASE1_WALKING_SKELETON.md) for the event,
persistence, and authentication foundation, and
[Phase 2A engine](docs/PHASE2A_PRODUCTION_ENGINE.md) for the production
calculation and oracle boundaries. See
[Phase 2B market data](docs/PHASE2B_MARKET_DATA_FOUNDATION.md) for replay,
normalization, closed-bar, health, and persistence behavior. See
[Phase 2C Twelve Data](docs/PHASE2C_TWELVE_DATA_PROVIDER.md) for provider
configuration, secret handling, completed-candle policy, fixture validation,
and the explicitly invoked live smoke test. See
[Phase 3A vertical slice](docs/PHASE3A_EUR_USD_DASHBOARD.md) for runtime,
dashboard API, frontend states, and live end-to-end validation evidence.
