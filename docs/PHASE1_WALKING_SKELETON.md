# Phase 1 walking skeleton

## Scope boundary

The backend reads two frozen `expected.json` artifacts through
`FrozenExpectedResultAdapter`. This is a temporary result adapter, not a
strategy engine. No production file imports `golden.reference` or invokes the
reference calculator. The frontend renders API-provided values and contains no
SMA, ATR, Filter, pivot, stop, target, or contract-size formulas.

Selected cases:

- `confirmed_buy_h1_01` — `SYNTH_XAUUSD`, H1, confirmed BUY.
- `confirmed_sell_h4_01` — `SYNTH_XAUUSD`, H4, confirmed SELL.

## Runtime flow

```text
Committed expected result + selected completed candle
                    |
                    v
              BAR_CLOSED
                    |
                    v
            FILTER_EVALUATED
                    |
                    v
             FILTER_MATCHED
                    |
                    v
            SIGNAL_EVALUATED
                    |
                    v
            SIGNAL_CONFIRMED
                    |
                    v
           LEVELS_CALCULATED
                    |
                    v
            STATUS_PROJECTED
                    |
                    v
    SQLite status + event history projections
                    |
                    v
 Protected FastAPI -> Next.js server -> dashboard
```

Each selected confirmed case emits exactly seven events in that order.

## Idempotency and restart

The processed-bar key is:

```text
strategy_id + provider + instrument + timeframe + closed_bar_time
```

`processed_bars.idempotency_key` is a SQLite primary key. Claiming the key,
writing the ordered events, and upserting current status happen in one
transaction. Replaying either fixture, including during application startup
after a restart, creates zero new events. H1 and H4 use different keys and
different current-status rows.

## Protection boundary

- FastAPI `/health` is public and reveals only synthetic system health.
- All FastAPI data endpoints require `X-Spect8-Internal-Key`.
- The browser never receives that key. Next.js calls FastAPI only from a module
  marked `server-only`.
- The single client password is stored as a scrypt hash.
- Successful login sets a signed, expiring, HTTP-only, SameSite=Strict cookie.
- The dashboard and browser-facing dashboard API verify the signed session.
- Logout expires the cookie.

## Synthetic labeling

Every backend response envelope includes:

- `synthetic: true`;
- source `COMMITTED_GOLDEN_EXPECTED_RESULT_ADAPTER`;
- a notice that no live market-data provider is connected.

Every persisted status and event record also carries `synthetic: true`.
