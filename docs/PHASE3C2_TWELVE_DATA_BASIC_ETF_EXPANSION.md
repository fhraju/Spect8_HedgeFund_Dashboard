# Phase 3C-2 Twelve Data Basic ETF expansion

## Outcome

The authoritative target registry remains capped at 25 entries. Twelve of the
13 requested US-listed ETF price series passed exact listing discovery, a real
Basic-plan H1 request, OHLC validation, canonical persistence, local H4 and D1
construction, and the frozen evaluator. `TLT_US_ETF` remains disabled because
one of 509 returned rows was malformed or had invalid OHLC relationships and
was quarantined. The final enabled count is therefore 24.

These instruments are ETF price-series proxies, not renamed direct markets.
For example, SPY is not the S&P 500 cash index, SLV is not XAG/USD spot silver,
USO is not WTI spot, UNG is not natural-gas spot, TLT is not the US 10-year
yield, and VIXM is not spot VIX. Signals use each ETF's own prices. The earlier
direct-market IDs remain separate and disabled.

Twelve Data documents the Basic-plan ETF catalog at `/etfs/list`; the validator
uses that asset-aware endpoint and then proves access with `/time_series`.

## Sanitized live results

Every response below was HTTP 200 with provider status `ok`, instrument type
`ETF`, timezone `America/New_York`, 509 raw candles, 73 structurally partial
15:30 Eastern session fragments, zero duplicates, and a latest completed H1 at
`2026-08-05T19:30:00Z`. Successful instruments had 436 canonical full H1 bars,
73 valid derived H4 bars, 73 actual-data D1 sessions, and zero quarantines.

| Canonical ID | Exact provider listing | Venue / MIC | H1 / H4 Filter | Decision |
|---|---|---|---|---|
| SPY_US_ETF | SPY — SPDR S&P 500 ETF Trust | NYSE Arca / ARCX | SELL / SELL | ENABLE |
| QQQ_US_ETF | QQQ — Invesco QQQ Trust, Series 1 | XNMS | SELL / SELL | ENABLE |
| IWM_US_ETF | IWM — iShares Russell 2000 ETF | NYSE Arca / ARCX | SELL / SELL | ENABLE |
| FEZ_US_ETF | FEZ — SPDR Euro Stoxx 50 ETF | NYSE Arca / ARCX | SELL / SELL | ENABLE |
| EWJ_US_ETF | EWJ — iShares MSCI Japan ETF | NYSE Arca / ARCX | SELL / SELL | ENABLE |
| EEM_US_ETF | EEM — iShares MSCI Emerging Markets ETF | NYSE Arca / ARCX | SELL / SELL | ENABLE |
| TLT_US_ETF | TLT — iShares 20+ Year Treasury Bond ETF | XNMS | SELL / SELL | KEEP DISABLED: 435 completed, 1 quarantined invalid OHLC row |
| HYG_US_ETF | HYG — iShares iBoxx $ High Yield Corporate Bond ETF | NYSE Arca / ARCX | NONE / NONE | ENABLE |
| SLV_US_ETF | SLV — iShares Silver Trust | NYSE Arca / ARCX | SELL / SELL | ENABLE |
| USO_US_ETF | USO — United States Oil Fund, LP | NYSE Arca / ARCX | BUY / BUY | ENABLE |
| UNG_US_ETF | UNG — United States Natural Gas Fund, L.P. | NYSE Arca / ARCX | BUY + SELL / BUY + SELL | ENABLE |
| DBA_US_ETF | DBA — Invesco DB Agriculture Fund | NYSE Arca / ARCX | SELL / SELL | ENABLE |
| VIXM_US_ETF | VIXM — ProShares VIX Mid-Term Futures ETF | CBOE BZX / BATS | BUY / BUY | ENABLE |

All H1 and H4 Signals were `NONE` in this validation snapshot. The full
sanitized evidence, including request start/finish timestamps and non-secret
parameters, is in
`docs/validation/PHASE3C2_ETF_LIVE_VALIDATION_2026-08-06.json`.

## Session and construction policy

- US ETF polling follows the deterministic US Eastern regular-session
  calendar, including weekends, holidays, early closes, and DST.
- Verified provider timestamps are interval starts: 09:30, 10:30, 11:30,
  12:30, 13:30, 14:30, and 15:30 Eastern. Only the first six are full H1 bars.
  The final 15:30–16:00 fragment is structural M30 and is excluded from H1
  evaluation; extended-hours bars are excluded.
- An ETF H4 requires exactly four contiguous full H1 bars from one exchange
  session. Remainders never cross overnight, a weekend, holiday, or early
  close.
- New York 17:00 remains the D1 authority. ETF D1 and current-partial D1 use
  only actual completed H1 bars. No empty, synthetic, or forward-filled daily
  bars are created. ATR(5) uses the latest five completed data-bearing D1
  sessions and excludes the current partial session.

## Limits and credit evidence

The persistent rolling-24-hour guard is configured for an 800-credit account,
a 700-credit operational budget, and a protected 100-credit reserve. The
controlled work used 135 recorded credits: validation and preserved corrective
runs plus the diagnostic and final 24-instrument scans. The successful pipeline
data was also restarted without a single refetch. Remaining operational
capacity was 565 and the reserve was preserved.

The final application restart then performed one necessary 24-request catch-up
because the production database was one H1 behind and had no ETF history. The
post-restart ledger therefore ended at 159 used, 541 operational credits
remaining, with the reserve still preserved; all 24 scanner rows were healthy.

The final successful full scan ran from `2026-08-06T05:34:56.512663Z` to
`2026-08-06T05:38:30.324058Z` (213.811 seconds). It used 24 requests in exact
registry order, with minimum start spacing 8.101604 seconds, maximum eight
starts in every rolling 60-second window, and no overlapping cycle. A restart
completed in 0.524 seconds with zero provider requests and zero new bars.

## Reproducibility

The final checkpoint is named `phase_3c2_24_instruments`, reflecting the real
enabled count rather than claiming 25. It freezes 24 registry/provider
identities, 720 H1 bars, 720 valid H4 bars, 240 completed D1 sessions, current
partial D1 state, 48 evaluations, 288 ordered events, scanner projection, and
SHA-256 checksums.

```powershell
.\.venv\Scripts\python.exe -m backend.app.tools.reproduce_reproducibility_checkpoint --name phase_3b_10_instruments
.\.venv\Scripts\python.exe -m backend.app.tools.reproduce_reproducibility_checkpoint --name phase_3c1_12_instruments
.\.venv\Scripts\python.exe -m backend.app.tools.reproduce_reproducibility_checkpoint --name phase_3c2_24_instruments
```

Reproduction needs no network, API key, developer database, wall-clock access,
or sleeping. The original ten and BTC/ETH expected results remain unchanged.

## Feed limitation and next phase

ETF signals describe Twelve Data ETF prints and can differ from IC Markets
broker feeds and from their cash-index, spot, yield, or volatility underlyings.
A later phase should revalidate TLT with a fresh bounded sample and retain it
disabled unless every row passes; it should not silently substitute another
bond instrument.
