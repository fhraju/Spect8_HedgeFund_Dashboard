# Platform-authority live startup

This runbook preserves the validated split deployment:

| Instance | Instruments | Provider/database | Backend | Frontend |
|---|---|---|---:|---:|
| DEMO | EURUSD, GBPUSD | IG_DEMO / `market_data` | 8000 | 3000 |
| LIVE | USDJPY | IG_LIVE / `market_data_live` | 8001 | 3001 |

Run commands from the repository named in each section. Never copy credentials
onto a command line or into a log.

## 1. Preflight and deficit audit

Confirm PostgreSQL is healthy, then run the non-executing audit from
`HedgeFund_Market_Data_Platform`:

```bash
.venv/bin/python -m hedgefund_market_data.providers.ig.bootstrap_spect8
```

The audit authenticates to neither IG environment and consumes zero historical
requests. It reports M30/H1/D1/W1 counts separately for every instrument.

If every minimum is present, do not run any historical recovery. If native
H1/D1/W1 has a deficit, execute the same bounded, duplicate-safe command once:

```bash
.venv/bin/python -m hedgefund_market_data.providers.ig.bootstrap_spect8 --execute
```

The command requests no H4 data. D1/W1 remain isolated with
`TEMPORARY_NATIVE_IG_FILTER_BOOTSTRAP` provenance. An M30 deficit fails closed
and must be handled through the existing bounded canonical M30 recovery path;
do not substitute native H4 or broaden the native bootstrap.

## 2. Start or verify collectors

Inspect the process list first. Leave an already connected collector alone.
For an absent collector, use the write-capable `DATABASE_URL`, never the Spect8
reader URL:

```bash
.venv/bin/python -m hedgefund_market_data.pipeline.ig_live_daemon \
  --env .env.demo --database-url-var DATABASE_URL \
  --instruments FX_EUR_USD,FX_GBP_USD --stream-only

.venv/bin/python -m hedgefund_market_data.pipeline.ig_live_daemon \
  --env .env.live --database-url-var DATABASE_URL \
  --instruments FX_USD_JPY --stream-only
```

`--stream-only` deliberately consumes zero historical-price requests. A usable
collector must show a connected Lightstreamer status, accepted subscriptions,
incoming M5 timestamps, and clean persistence cycles. The daemon rejects a
read-only database role before authenticating to IG.

## 3. Start Spect8 and evaluate immediately

Start the existing two backend instances with these explicit scopes:

```text
DEMO: EUR_USD,GBP_USD + spect8 reader URL for market_data + port 8000
LIVE: USD_JPY         + spect8 reader URL for market_data_live + port 8001
```

Use a separate SQLite projection path for each instance. Backend lifespan calls
the Platform authority once before serving requests, derives broker-aligned H4
from H1, evaluates both Micro and Macro from existing completed history, and
then starts polling. It does not wait for the next candle close.

Start one existing Next.js production frontend against each backend on ports
3000 and 3001. Do not share a backend across the two PostgreSQL databases.

## 4. Readiness interpretation

`/runtime/status` separates strategy-history freshness from live readiness:

- `freshness_state` covers the completed history used for immediate strategy
  evaluation, including explicitly isolated native bootstrap H1.
- `streaming_state` uses only canonical M30 advancement and never treats native
  bootstrap as proof that a collector is alive.
- `partial_data_state` is fail-closed. In the current two-process deployment,
  the collector's M5 partial buffer is ephemeral and is not exposed to Spect8.
- `overall_live_readiness` remains `DEGRADED` until a real partial-data source
  is wired without changing canonical database semantics.

The dashboard and scanner show `STALE` when canonical streaming evidence is not
recent enough, even if historical strategy evaluation is ready. `NO_SIGNAL` is
a valid evaluator outcome; never insert signal rows for display purposes.

## 5. Validation

For each instance verify `/health`, `/runtime/status`, `/statuses`,
`/signals/current`, `/scanner`, and every instrument dashboard. Compare one
canonical timestamp from PostgreSQL through the API to the rendered dashboard.
The PostgreSQL session user must be `spect8_market_data_reader`, with SELECT and
without INSERT/UPDATE/DELETE privileges.
