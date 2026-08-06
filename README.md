# Spect8 HedgeFund Dashboard

Protected read-only Market Scanner for the frozen
`SPECT8_MICRO_DAILY_V1_0` calculation strategy under the active frozen
`SPECT8_MICRO_DAILY_V1_0_3` specification.

The Phase 3C registry contains 25 target markets. The original ten and the
Phase 3C-1 `BTC/USD` and `ETH/USD` Binance listings remain enabled. Phase 3C-2
adds 12 live-validated US-listed ETF price series as explicitly labelled
proxies. TLT remains disabled after one provider H1 row failed OHLC validation,
so the authoritative enabled count is 24.
One
canonical H1 request per enabled instrument feeds local H4 and New-York-close
D1 construction. A provider-wide rolling limiter serializes startup, polling,
validation, and retry starts at no more than eight per rolling minute and at
least eight seconds apart. H1 and H4 remain independent signal timeframes and
D1 remains context only. Golden expected results remain a test-only oracle.
The scanner contains no trading, profitability backtesting, optimization, or
WebSockets. Historical Replay remains only as an isolated regression feature;
no additional replay product development is planned.

The frozen v1.0.1 clarification adds `CONFIRMED_BOTH` for simultaneous BUY and
SELL confirmations and preserves both directional calculations independently.
The frozen v1.0.2 boundary clarification includes a completed D1 candle when
its close equals the evaluated H1/H4 signal close; incomplete and future D1
candles remain excluded.
The frozen v1.0.3 authority constructs canonical D1 context from completed H1
bars over DST-aware 17:00-to-17:00 `America/New_York` sessions; provider-native
Daily OHLC is not a strategy input.

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

For the live scanner, keep `TWELVE_DATA_API_KEY` only in the ignored
`backend/.env` or the process environment and configure:

```text
SPECT8_MARKET_DATA_PROVIDER=twelve_data
SPECT8_MARKET_SCAN_ENABLED=true
SPECT8_MARKET_SCAN_AFTER_HOUR_SECONDS=60
SPECT8_MARKET_DATA_REQUEST_MIN_INTERVAL_SECONDS=8
SPECT8_MARKET_DATA_MAX_REQUESTS_PER_MINUTE=8
TWELVE_DATA_DAILY_CREDIT_LIMIT=800
MARKET_DATA_DAILY_OPERATIONAL_BUDGET=700
MARKET_DATA_CREDIT_RESERVE=100
SPECT8_AUTO_SEED_SYNTHETIC=false
```

The runtime performs a rate-limited catch-up at startup, then scans shortly
after each H1 close. The cycle start rotates through registry order and a new
cycle cannot overlap the active one. Retries are requeued behind other
instruments and consume the same global limiter capacity.

Capture and reproduce the verified ten-instrument baseline without network
access or the developer database:

```powershell
.\.venv\Scripts\python.exe -m backend.app.tools.capture_reproducibility_checkpoint `
  --name phase_3b_10_instruments --evaluation-time 2026-08-05T14:00:00Z
.\.venv\Scripts\python.exe -m backend.app.tools.reproduce_reproducibility_checkpoint `
  --name phase_3b_10_instruments
```

Discover and validate Phase 3C candidates using the ignored provider key and
the same global request limiter:

```powershell
.\.venv\Scripts\python.exe -m backend.app.market_data.validate_instrument_universe
```

For a sustained private Phase 3B observation:

```powershell
.\scripts\start_phase3b_observation.ps1
```

Terminal 2:

```powershell
Set-Location frontend
npm.cmd run dev
```

Open `http://localhost:3000`. The backend is available at
`http://127.0.0.1:8000`; its data endpoints require the internal server key.
The protected historical workspace is at
`http://localhost:3000/historical-replay`.

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
dashboard API, frontend states, and live end-to-end validation evidence. See
[Phase 3B observation runbook](docs/PHASE3B_OBSERVATION_RUNBOOK.md) and
[Phase 3B client checklist](docs/PHASE3B_CLIENT_ACCEPTANCE.md) for sustained
operation and UAT. See
[Phase 3B historical replay validation](docs/PHASE3B_HISTORICAL_REPLAY_VALIDATION.md)
for the isolated July 2026 functional replay evidence. The historical replay
track passed; the separate live-observation gate remains conditional until the
required multi-day observation and explicit client approval are complete.
See [Phase 3C reproducibility checkpoint](docs/reproducibility/phase_3b_10_instruments/README.md)
and the sanitized provider report under `docs/validation` for the controlled
25-market provisioning result. See
[Phase 3C-1 corrective validation](docs/PHASE3C1_CORRECTIVE_VALIDATION.md) for
the asset-aware discovery correction, exact plan results, and the validated
12-instrument runtime evidence.
See [Phase 3C-2 ETF expansion](docs/PHASE3C2_TWELVE_DATA_BASIC_ETF_EXPANSION.md)
for exact ETF listings, exchange-session handling, credit evidence, the TLT
quality failure, and the offline 24-instrument checkpoint.
