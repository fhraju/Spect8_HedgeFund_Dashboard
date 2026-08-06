# Phase 3C reproducibility and controlled market expansion

> Corrective Phase 3C-1 supersedes the candidate-availability conclusions in
> this document. The original evidence remains retained for audit history; see
> `docs/PHASE3C1_CORRECTIVE_VALIDATION.md` for the corrected asset-aware result.

## Outcome

Phase 3C preserves the user-verified ten-instrument scanner and adds one
provider-independent 25-entry registry. The original ten remain enabled. The
15 requested additions are present in deterministic order but disabled because
none passed exact direct-market discovery plus H1 access validation on the
current Twelve Data catalog/account. No ETF, CFD, futures contract, or other
proxy was substituted.

The approved strategy remains `MICRO_DAILY_FILTER_CURRENT_D1_V2`. Strategy
formula modules, New York 17:00 D1 authority, canonical UTC timestamps, and
H1/H4 independence were not changed.

## Frozen checkpoint

- Name: `phase_3b_10_instruments`
- H1 close: `2026-08-05T14:00:00Z`
- IC Markets broker presentation: `2026-08-05 17:00`
- Corresponding H4 close: `2026-08-05T13:00:00Z`
- Inputs: 30 canonical H1, 30 derived H4, and 10 completed D1 records per
  instrument
- Expected results: 20 evaluations and 123 events
- Network calls during reproduction: zero
- Checksums: `checksums.sha256` and manifest SHA-256 entries validate

Capture:

```powershell
python -m backend.app.tools.capture_reproducibility_checkpoint `
  --name phase_3b_10_instruments `
  --evaluation-time 2026-08-05T14:00:00Z
```

Reproduce:

```powershell
python -m backend.app.tools.reproduce_reproducibility_checkpoint `
  --name phase_3b_10_instruments
```

## Candidate validation

The validation command used Twelve Data `/symbol_search` with plan metadata
and would run `/time_series?interval=1h` only after one unambiguous exact direct
market was resolved. Discovery consumed 22 rate-limited requests including
targeted retries. No candidate reached the H1 step because direct discovery was
either unavailable or ambiguous.

- `BTC_USD` and `ETH_USD`: ambiguous across multiple crypto exchanges
- Other 13 candidates: no exact direct-market result; returned matches were
  absent or represented ETFs, stocks, warrants, or other proxies
- Minimum observed request-start spacing: `8.097075` seconds
- Maximum starts in a rolling 60-second window: `6`

See `docs/validation/PHASE3C_INSTRUMENT_UNIVERSE_VALIDATION_2026-08-05.json`
for the sanitized per-candidate evidence. It contains no credentials or
authentication headers.

## Enabled-universe live scan

The clean-database scan processed all ten enabled instruments in registry order:

- Result: PASS; 10/10 healthy and non-stale
- Duration: `94.002` seconds
- Requests/credits: 11, including one rate-limited XAU/USD retry
- Minimum request-start spacing: `8.102424` seconds
- Maximum starts in a rolling 60-second window: `8`
- No overlap, duplicates, quarantines, or instrument failures
- Latest H1 close: `2026-08-05T15:00:00Z`
- Latest locally aggregated H4 close: `2026-08-05T13:00:00Z`

See `docs/validation/PHASE3C_ENABLED_UNIVERSE_LIVE_SCAN_2026-08-05.json` for
request start/finish timestamps, candle counts, D1 state, H1/H4 Filter and
Signal results, validation state, and provider health per instrument.

## Presentation

`FilterBadge` is an outlined, low-emphasis eligibility badge. `SignalBadge` is
a separate solid, high-emphasis component with text plus a direction symbol.
Both expose distinct tooltips and accessible labels. Asset filters are derived
from registry-backed API rows, and the table keeps stable canonical keys,
horizontal responsiveness, and sticky headers for up to 25 enabled rows.

## Feed limitation

Results describe Twelve Data instruments. They are not claimed to be identical
to IC Markets CFD prices. Crypto would retain the frozen strategy weekend
policy; no separate crypto strategy was introduced.
