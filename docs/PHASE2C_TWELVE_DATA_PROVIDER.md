# Phase 2C — Twelve Data Provider Profile

## Verified baseline

Verified Phase 2B repository baseline: 404 passed; previous 416 count was not
reproducible.

Commit `71b8bd5` contains the provider protocol, canonical models, instrument
registry, candle normalizer, closed-bar detector, replay/restart idempotency,
health states, and D1/cross-timeframe look-ahead tests. Repository history has
no later or omitted committed 12-test set.

## Scope

Phase 2C adds one read-only REST adapter:

- provider: Twelve Data;
- canonical and provider symbol: `EUR/USD`;
- timeframes: H1, H4 and D1;
- endpoint: `/time_series`.

It does not add instruments, orders, fills, broker execution, a scheduler,
WebSockets, backtesting, optimization, dashboard work, or strategy changes.

## Official-provider feasibility

The check performed on 2026-07-31 used the current
[Twelve Data API documentation](https://twelvedata.com/docs) and
[historical-data guide](https://support.twelvedata.com/en/articles/5656039-how-to-get-historical-prices).

- Authentication supports an `apikey` query parameter or the recommended
  `Authorization: apikey ...` header. The adapter uses only the header.
- `/time_series` supports `1h`, `4h`, and `1day`.
- `outputsize` supports 1–5000 values; the default output order is descending,
  and `order=asc|desc` is available.
- Intraday requests accept `timezone=UTC`. The timezone parameter is ignored
  for daily intervals, but forex datetimes are documented as UTC.
- A returned `datetime` is the bar open time. The adapter derives close time by
  adding the canonical interval.
- Responses use `meta`, `values`, and `status`; error payloads use `code`,
  `message`, and `status`, including when the HTTP response itself succeeds.
- Documented errors include 400, 401, 403, 404, 429 and 500. API usage headers
  include `api-credits-used` and `api-credits-left`.
- `/time_series` does not expose a reliable completion flag. Provider material
  also notes that a closed REST candle can arrive after processing delay.
  Therefore the latest value is treated as potentially forming.
- Date-bounded history is available through `start_date`/`end_date`; Phase 2C
  needs only bounded `outputsize` calls because the frozen engine requires 30
  signal bars and 6 D1 bars.

## Configuration and secret handling

Select the provider with:

```text
SPECT8_MARKET_DATA_PROVIDER=twelve_data
SPECT8_INSTRUMENT=EUR/USD
SPECT8_TIMEFRAMES=H1,H4,D1
TWELVE_DATA_API_KEY=<rotated key>
```

The key is read only from `TWELVE_DATA_API_KEY`. It is sent in the
`Authorization` header, never in a URL. Provider errors discard raw response
messages so a provider echo cannot reach exceptions or health details.
`backend/.env` is ignored by Git; `backend/.env.example` contains only a blank
key placeholder. Run Uvicorn with an environment populated by the deployment
system or with its supported `--env-file backend/.env` option.

Configuration fails before provider construction when the selected provider
has no key, an instrument other than `EUR/USD`, or a timeframe set other than
H1/H4/D1.

## Timeframe, UTC and completed-candle rules

| Canonical | Twelve Data interval | Requested history |
| --- | --- | ---: |
| H1 | `1h` | 31 |
| H4 | `4h` | 31 |
| D1 | `1day` | 7 |

The extra value allows the current value to be filtered while retaining the
frozen minimum of 30 signal bars and 6 D1 bars. The provider requests UTC,
interprets timezone-less forex datetimes as UTC, derives close time from open
time plus interval, and returns ascending chronological candles.

A candle is eligible only when:

```text
derived_close_time < injected_as_of_utc
```

Consequently it is excluded immediately before and exactly at its close, and
becomes eligible immediately after close. This deliberately conservative rule
implements the frozen requirement that close time must have passed. D1 history
is also restricted to closes strictly before the signal trigger.

## Data quality, retry and health behavior

- Descending or otherwise reversed responses are sorted deterministically.
- Duplicate timestamps, unexpected interval gaps, malformed numerics, invalid
  OHLC, missing OHLC, invalid metadata, and invalid JSON are quarantined.
- Empty completed data maps to `DATA_UNAVAILABLE`.
- Authentication and validation failures are permanent for the current call
  and are not retried.
- Timeouts, connection failures, 429, and temporary 5xx failures are retried at
  most three total attempts by default.
- Backoff is bounded. `Retry-After` is honored up to the configured maximum.
- Connect and read timeouts are separate, explicit transport inputs.
- Raw transport/provider exceptions do not cross the provider boundary;
  callers receive `MarketDataProviderError` with canonical error and health
  codes.
- Success reports `HEALTHY`; age beyond two H1 periods reports `STALE`;
  transport/unavailable errors report `DATA_UNAVAILABLE`; response-integrity
  failures report `QUARANTINED`. The existing coordinator persists
  `RECOVERED` transitions.

The existing Phase 2B gap policy is intentionally unchanged. It requires exact
interval continuity and quarantines genuine missing intervals. Live validation
on 2026-07-31 found that Twelve Data supplied continuous EUR/USD rows across
the most recent completed weekend: 200 H1 rows, 80 H4 rows, and 30 D1 rows all
had exact interval deltas and zero duplicates, malformed candles, or gaps.
Twelve Data represented Saturday/Sunday periods with rows rather than omitting
the market-closure period, so the current policy did not quarantine any of the
live histories and no session-rule correction was needed.

## Contract metadata

Twelve Data time-series responses do not provide broker contract minimum,
maximum, step, tick value, conversion rate, or minimum stop distance. These
fields remain `None`; the existing sizing engine returns
`METADATA_UNAVAILABLE`. No sizing values are invented.

## Validation

Deterministic unit and integration tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Explicit live smoke test:

```powershell
.\.venv\Scripts\python.exe -m backend.app.market_data.twelve_data_smoke
```

The smoke command runs only when `TWELVE_DATA_API_KEY` exists in its process
environment. It prints only provider, instrument, timeframe, candle counts,
first/last completed timestamps, duplicate/gap counts, health, and result. It
normalizes every returned candle and verifies strict completion and ordering.

Validation labels:

- **Fixture-validated:** interface mapping, H1/H4/D1 parsing, UTC, sorting,
  forming filtering and boundaries, data quality, canonical errors, health,
  retries, secret redaction, non-synthetic persistence, restart/replay
  idempotency, and passage into the unmodified production strategy evaluator.
- **Live-validated:** only when the separate smoke command returns `PASS`.
- **Not yet validated:** production scheduling, sustained quota behavior,
  live weekend/session continuity, additional instruments, and any
  broker-specific contract sizing.

Evidence recorded on 2026-07-31:

- documented live smoke: `PASS`;
- H1: 31 raw, 30 completed, 1 forming filtered;
- H4: 31 raw, 30 completed, 1 forming filtered;
- D1: 7 raw, 6 completed, 1 forming filtered;
- all accepted candles normalized chronologically with
  `derived_close_time < as_of_utc`;
- live H1/H4 strategy-input evaluation passed with 30 signal and 6 D1 bars
  per instance;
- same-process and restart replay remained idempotent;
- a live-derived H1 candle was excluded immediately before and exactly at its
  close, then accepted immediately after close;
- extended weekend probes found zero gaps. Observed H4 opens were anchored at
  `01:00, 05:00, 09:00, 13:00, 17:00, 21:00 UTC`; D1 opens were anchored at
  `00:00 UTC`, consistent with the provider profile's UTC open-time convention.

## Expansion gates

1. **Three instruments:** first resolve live EUR/USD session/gap evidence, add
   provider-symbol and precision fixtures per instrument, then validate quota
   use and independent H1/H4 state.
2. **Ten instruments:** add batching/poll scheduling outside the provider
   contract, per-instrument freshness, quota budgeting, and restart tests.
3. **25–50 instruments:** add capacity/load evidence, rate-limit telemetry,
   staggered polling, operational alerting, and provider-failure isolation.

Each gate requires fixture and live evidence before registry expansion. Phase
2C itself remains one provider and one instrument.
