# Phase 22A-4 — Reversible Platform cutover preparation

Status: **PASS** — Linux PostgreSQL is the active development cluster as of 2026-08-23.
Windows PostgreSQL data was migrated via portable PostgreSQL-native dump/restore and
the real PostgreSQL authority validation now passes.

## Linux PostgreSQL migration (2026-08-23)

- Active cluster: Linux Mint `localhost:5432` — PostgreSQL 18.6 (Ubuntu 18.6-1.pgdg24.04+2),
  upgraded from the original 16 cluster; data directory `/var/lib/postgresql/18/main`.
- Source: Windows NTFS `C:\Program Files\PostgreSQL\18\data` (PostgreSQL 18.6,
  locale `English_United States.1252`, databases `market_data`, `market_data_live`,
  `template*`, `postgres`).
- Method: `pg_dump -Fp` (plain SQL, `postgresql+psycopg`) from a temporary Linux
  18 cluster mounting the copied Windows data directory (after installing the
  Windows locale `English_United States.1252` via `localedef -i en_US -f CP1252`
  and fixing `dynamic_shared_memory_type`/`lc_*`). Restored with
  `psql -f` into freshly created `market_data`/`market_data_live` on the Linux
  18 main cluster (`DROP DATABASE`/`CREATE DATABASE` then `psql -f`). Custom-format
  `pg_restore` is not used across major versions (1.16 header mismatch); plain SQL
  preserves schema, canonical bars, provider identities, timestamps, revisions,
  quality/provenance and `alembic_version = 0006_semantic_availability`.
- No IG history was redownloaded. The dump was copied via the NTFS mount at
  `/media/raju/265698D95698AB57`.
- Databases on Linux after restore:
  - `market_data` — 16540 canonical bars, `IG_DEMO` only, EURUSD H1 BID 1317/D1 BID 53,
    GBPUSD H1 BID 1197/D1 BID 48, plus M30/H4/W1 and ASK series.
  - `market_data_live` — 7900 canonical bars, `IG_LIVE` only, USDJPY H1 BID 1200/D1 BID 50.
  - Provider separation preserved; no cross-provider mixing.
  - `spect8_market_data_reader` exists on both DBs with `CONNECT`, `USAGE` on schema
    `public`, `SELECT` on all 14 tables, and `ALTER DEFAULT PRIVILEGES` for future
    tables. Verified read-only: `SELECT` succeeds, `INSERT` is denied with
    `InsufficientPrivilege`.
  - Real validation used `MARKET_DATA_PLATFORM_DATABASE_URL=postgresql+psycopg://spect8_market_data_reader:****@localhost:5432/market_data`
    (and `.../market_data_live` for USDJPY alone) with `stale_after_seconds` large
    enough to cover the historical ingestion lag (semantic_available_at 2026-08-21
    for 2026-08-14 closes). `HEALTHY` requires `stale_after > 604800`; with the
    default 7200 the data is correctly reported `STALE`.

## Validation result (real PostgreSQL, 2026-08-23)

- `market_data` and `market_data_live` are restored and readable on Linux.
- EURUSD/GBPUSD (DEMO) and USDJPY (LIVE) canonical H1/D1 counts match the Windows
  history above; `IG_DEMO` and `IG_LIVE` remain separated.
- `spect8_market_data_reader` works and is read-only on both DBs.
- `PlatformAuthorityRuntime` with the read-only reader:
  `HEALTHY` (with `stale_after=700000`), `STALE` (with 7200), `UNAVAILABLE`
  (bad credentials/host), watermark resume, replay idempotence, revision detection,
  fail-closed without Twelve Data fallback, no mixed-source evaluation, rollback to
  `TWELVE_DATA` preserves provenance, and shadow mode remains separate — all PASS.
- Automated tests: `test_phase22a4_cutover` (13 passed), `test_platform_adapter`
  (19 passed), custom real-DB authority validation (8/8 passed).

Status: **PASS** — Phase 22A-4 is closed. No new client features (M30/H1/Forming,
40/100 instruments) were started.

## Authority configuration

`SPECT8_MARKET_DATA_SOURCE` is the sole source-authority switch:

- `TWELVE_DATA` (safe default) runs the existing Twelve Data/replay composition.
- `MARKET_DATA_PLATFORM` bypasses that coordinator and uses the read-only
  PostgreSQL canonical reader. There is no automatic selection or fallback.

Platform authority additionally requires
`MARKET_DATA_PLATFORM_DATABASE_URL=postgresql+psycopg://...` and an explicit
non-empty `SPECT8_ENABLED_INSTRUMENT_IDS` subset of `EUR_USD,GBP_USD,USD_JPY`.
Any other value or instrument fails startup. Platform shadow remains a separate,
default-off SQLite projection and cannot be enabled while Platform is authoritative.

## Fail-closed startup and freshness

Before serving authoritative Platform processing, startup verifies the reader can
connect/read, all explicit mappings resolve, the frozen bounded bootstrap exists
(`M30=1,H1=1177,H4=30,D1=10,W1=6`), H1 is fresh, and durable SQLite
watermark/provenance is readable. Failures never switch to Twelve Data.

Freshness reuses `SPECT8_MARKET_DATA_STALE_AFTER_SECONDS` (default 7,200 seconds)
against the latest expected completed forex H1 close. The standard Friday 17:00 to
Sunday 17:00 New York closure is excluded. PostgreSQL/read failures are
`UNAVAILABLE`; excessive canonical H1 lag is `STALE`; only `HEALTHY` cycles may
create confirmed evaluations. New canonical rows with a non-progressing watermark
also fail closed.

## Bootstrap, resume, and provenance

First activation reads the bounded bootstrap, persists Platform H1/D1 and the
broker-time H4 derived from H1 into Spect8 SQLite, records immutable Platform
consumption identities, then checkpoints the canonical watermark last. Restarts
read only after that watermark. If evaluation/consumption succeeded but checkpoint
failed, replay is suppressed by the existing processed-event and canonical-identity
contracts before the watermark advances on retry.

Evaluation/status provenance remains the provider (`TWELVE_DATA` or
`MARKET_DATA_PLATFORM`); Platform canonical IDs/hashes and evaluation keys remain
in the Platform consumption ledger. Historical status, event, bar, revision, and
watermark rows are never deleted or rewritten during a source transition.

## Controlled cutover (not yet performed)

1. Stop Spect8 and back up its SQLite application database.
2. Configure the read-only Platform URL and an approved explicit instrument subset.
3. Set `SPECT8_MARKET_DATA_SOURCE=MARKET_DATA_PLATFORM`.
4. Start Spect8. Proceed only if startup reports active source Platform, connection
   `HEALTHY`, freshness `HEALTHY`, bootstrap/resume success, and a readable watermark.
5. Do not compare provider OHLC for equality; future closes use selected-provider truth.

The cross-provider confirmed-evaluation cursor suppresses an evaluation at a close
already confirmed by the other authority. It does not rewrite earlier provider
history. Each subsequent authoritative cycle uses Platform bars for signal and
filter inputs exclusively.

## Deterministic rollback

1. Stop Spect8.
2. Set `SPECT8_MARKET_DATA_SOURCE=TWELVE_DATA` and retain the existing Twelve key.
3. Restart Spect8 and verify the configured/active source is `TWELVE_DATA`.

The same cross-provider cursor prevents a duplicate at the last Platform-confirmed
close. Twelve resumes on a later completed close. Platform bars, strategy statuses,
events, consumption provenance, revisions, and watermark remain intact for audit or
a later controlled reactivation.

Operational source, connection/freshness state, watermark, last processed canonical
timestamp, and last successful Platform evaluation are exposed by `/health` and
`/runtime/status`. SQLite remains strategy/application truth; PostgreSQL remains
market-data truth whenever Platform is selected.
