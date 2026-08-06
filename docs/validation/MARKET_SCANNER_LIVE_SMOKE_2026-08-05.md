# Ten-instrument scanner live smoke — 2026-08-05

## Result

PASS. The controlled run used the ignored local Twelve Data credential without
printing it. State was written only to the ignored database
`var/scanner_smoke_20260805T135557Z.sqlite3`.

- Scan: `2026-08-05T13:55:57.961516Z` to `2026-08-05T13:57:46.887634Z`
- Provider request starts: 12
- Minimum observed start spacing: 8.107747 seconds
- Maximum starts in any rolling 60 seconds: 8
- Canonical bars inserted: 700
- Every request was H1; H4 and D1 were derived locally
- NZD/USD and EUR/JPY each required one bounded, rate-limited retry
- Provider-symbol rejections: none
- Per instrument persistence: 30 H1, 30 H4, 10 completed D1 bars
- Per instrument projection: two evaluations (H1/H4) referencing one shared,
  instrument-specific snapshot
- Each evaluation persisted the ordered event sequence `BAR_CLOSED`,
  `FILTER_EVALUATED`, filter outcome, `SIGNAL_EVALUATED`, signal outcome,
  `STATUS_PROJECTED`

## Request-start evidence

| # | Instrument | UTC request start |
|---:|---|---|
| 1 | EUR_USD | 2026-08-05T13:55:58.022906Z |
| 2 | GBP_USD | 2026-08-05T13:56:06.131706Z |
| 3 | USD_JPY | 2026-08-05T13:56:14.244528Z |
| 4 | AUD_USD | 2026-08-05T13:56:22.352275Z |
| 5 | USD_CAD | 2026-08-05T13:56:30.461627Z |
| 6 | NZD_USD | 2026-08-05T13:56:38.582841Z |
| 7 | EUR_GBP | 2026-08-05T13:56:46.695165Z |
| 8 | EUR_JPY | 2026-08-05T13:56:54.805768Z |
| 9 | GBP_JPY | 2026-08-05T13:57:09.598818Z |
| 10 | XAU_USD | 2026-08-05T13:57:17.719685Z |
| 11 | NZD_USD retry | 2026-08-05T13:57:32.989880Z |
| 12 | EUR_JPY retry | 2026-08-05T13:57:41.107730Z |

## Instrument results

Every provider response contained 673 raw H1 candles: 672 completed, one
forming, zero duplicates, and zero provider gaps/quarantines. Every instrument
ended `HEALTHY`, with latest completed H1 and aggregated H4 at
`2026-08-05T13:00:00Z` and a valid current D1 session identified as
`2026-08-05`.

| Instrument | Provider symbol | H1 evaluation | H4 evaluation |
|---|---|---|---|
| EUR_USD | EUR/USD | FILTERED_SELL | FILTERED_SELL |
| GBP_USD | GBP/USD | FILTERED_SELL | FILTERED_SELL |
| USD_JPY | USD/JPY | WATCHING | WATCHING |
| AUD_USD | AUD/USD | FILTERED_SELL | FILTERED_SELL |
| USD_CAD | USD/CAD | FILTERED_SELL | FILTERED_SELL |
| NZD_USD | NZD/USD | FILTERED_BOTH | FILTERED_BOTH |
| EUR_GBP | EUR/GBP | FILTERED_SELL | FILTERED_SELL |
| EUR_JPY | EUR/JPY | FILTERED_SELL | FILTERED_SELL |
| GBP_JPY | GBP/JPY | FILTERED_SELL | FILTERED_SELL |
| XAU_USD | XAU/USD | FILTERED_SELL | FILTERED_SELL |

The bounded bootstrap history begins inside one old H4 bucket, so each symbol
records one non-blocking `H4_BUCKET_INCOMPLETE` provenance finding for that
leading historical bucket. It does not affect the latest H4, filter snapshot,
evaluation, or provider health.
