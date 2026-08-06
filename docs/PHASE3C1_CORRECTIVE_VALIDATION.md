# Phase 3C-1 corrective validation

## Outcome

Phase 3C-1 replaces generic name-only discovery with typed, asset-aware
catalog and alias attempts. Catalog existence is only the first gate. An
instrument is enabled only after a real H1 response and the existing canonical
normalization, persistence, local H4, New York 17:00 D1, and strategy evaluator
pipeline all pass without synthetic or forward-filled bars.

The original ten instruments remain enabled in their original order. Binance
`BTC/USD` and `ETH/USD` passed every gate and are appended to the enabled
universe. The final enabled count is 12.

No strategy formula, event ordering, frozen session authority, reproducibility
fixture, ETF, ETP, CFD, futures contract, bond ETF, or other proxy was changed
or substituted.

## Validator corrections

- Exact commodities use `/commodities` and exact provider symbols.
- Crypto uses `/cryptocurrencies` and requires a typed explicit exchange.
- Both crypto pairs use one shared `CryptoExchangePolicy("Binance")`.
- Indices use `/indices?symbol=<alias>` before proxy-oriented generic search.
- Rates use `/bonds?symbol=<alias>` before generic search.
- Exact symbols are authoritative; provider display-name variants no longer
  fail an exact-name allowlist.
- Every attempt records endpoint, sanitized parameters, timestamps, HTTP and
  provider status, exact error, and every returned candidate.
- H1 plan status is assigned only from a real H1 response.
- MIC and exchange H1 identity attempts are both retained where applicable.
- `--append` retains complete prior validation runs rather than replacing
  earlier attempts.

## Direct commodity results

| ID | Direct symbol | Catalog name | Real H1 result | Decision |
|---|---|---|---|---|
| `XAG_USD` | `XAG/USD` | Silver Spot | HTTP 404, available from Grow/Venture | Disabled |
| `WTI_CRUDE` | `WTI/USD` | Crude Oil WTI Spot | HTTP 404, available from Grow/Venture | Disabled |
| `BRENT_CRUDE` | `XBR/USD` | Brent Spot | HTTP 404, available from Grow/Venture | Disabled |
| `COPPER` | `HG1` | Copper Spot | HTTP 404, available from Grow/Venture | Disabled |

The exact response was: `This symbol is available starting with the Grow or
Venture plan.` No commodity pipeline validation was claimed after the real H1
plan rejection.

## Binance crypto results

Both catalog pairs resolved on Binance and returned HTTP 200 H1 data.

| Evidence | BTC/USD | ETH/USD |
|---|---:|---:|
| Raw candles | 509 | 509 |
| Completed candles | 508 | 508 |
| Forming candles excluded | 1 | 1 |
| Latest completed H1 | 2026-08-05T16:00:00Z | 2026-08-05T16:00:00Z |
| Frozen-policy H1 persisted | 364 | 364 |
| Locally derived H4 | 90 | 90 |
| Constructed completed D1 | 14 | 14 |
| Latest H4 | 2026-08-05T13:00:00Z | 2026-08-05T13:00:00Z |
| H1 Filter / Signal | SELL / NONE | SELL / NONE |
| H4 Filter / Signal | SELL / NONE | SELL / NONE |
| Synthetic / forward-filled | 0 / 0 | 0 / 0 |
| Decision | Enabled | Enabled |

Crypto weekend input remains governed by the existing frozen market policy;
no crypto-specific strategy was introduced.

## Remaining disabled markets

- `SP_500`, `NASDAQ_100`, `DOW_30`: no exact direct index candidate resolved;
  only equity, ETF, fund, warrant, or other non-index results were retained.
- `DAX_40`: catalog resolved `GDAXI` on XETR (Grow), but both MIC and exchange
  H1 requests returned the provider's invalid symbol/FIGI error.
- `FTSE_100`: catalog resolved `FTSE` on LSE (Grow), but both H1 identity forms
  returned the same invalid symbol/FIGI error.
- `NIKKEI_225`: catalog resolved `N225` on JPX (Pro), but both H1 identity forms
  returned the same invalid symbol/FIGI error.
- `NATURAL_GAS`: no direct result from the complete 32-entry commodity catalog
  or exact aliases; generic search returned stocks and ETFs only.
- `VIX`: no direct index result; generic search returned VIX futures products.
- `US_10Y_YIELD`: no direct bond/rate result; generic search returned one ETF.

No H1 plan conclusion was made for unresolved symbols. Their current-plan
status remains unconfirmed.

## Enabled-universe runtime proof

The clean-database production-path scan processed all 12 enabled instruments:

- Result: PASS; 12/12 healthy and non-stale
- Provider requests: 12
- Minimum request-start spacing: 8.099566 seconds
- Maximum starts in any rolling 60 seconds: 8
- Canonical bars inserted: 840
- Zero overlap, duplicates, quarantines, instrument failures, synthetic bars,
  or forward-filled bars
- BTC/USD and ETH/USD each returned 673 raw, 672 completed, and one forming H1
  candle, with valid H4/D1 and H1/H4 strategy projections

## Evidence

- `docs/validation/PHASE3C1_DIRECT_MARKET_LIVE_VALIDATION_2026-08-05.json`
- `docs/validation/PHASE3C1_REMAINING_MARKETS_DISCOVERY_2026-08-05.json`
- `docs/validation/PHASE3C1_ENABLED_UNIVERSE_LIVE_SCAN_2026-08-05.json`
- `backend/tests/test_phase3c1_corrective_validation.py`
- `backend/tests/test_phase_3b_10_instrument_reproducibility.py`
