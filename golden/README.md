# Spect8 Micro Daily golden dataset

This directory is the synthetic golden-test foundation for
`SPECT8_MICRO_DAILY_V1_0`. It is deliberately separate from any production
strategy engine, API, frontend, database, or market-data integration.

## Layout

- `schemas/` contains Draft 2020-12 schemas for `Candle`,
  `InstrumentMetadata`, `ExpectedResult`, and the fixture manifest.
- `manifest.json` is the inventory and coverage index for every case.
- `cases/<case-id>/` contains the two candle streams, instrument metadata,
  frozen expected result, and a human-readable calculation ledger.
- `reference/calculator.py` is an independent, standard-library-only oracle.
- `CHECKSUMS.sha256` freezes the authority document, manifest, candle inputs,
  instrument inputs, and expected results.

Each CSV candle row follows `candle.schema.json` after CSV scalar coercion. The
explicit `is_complete` field records provider completion. The reference
calculator additionally requires the candle close to be strictly earlier than
the evaluation boundary. D1 bars must close strictly before the selected signal
bar closes.

## ATR convention

Wilder D1 ATR(5) is calculated chronologically. The oldest candle supplies the
previous close, the following five true ranges seed ATR with their arithmetic
mean, and every later true range applies:

```text
ATR(next) = ((ATR(previous) × 4) + TR(next)) / 5
```

The result ends on the last eligible completed D1 candle. Every fixture includes
ten D1 candles, providing deterministic warm-up beyond the minimum seed.

## Regeneration

Golden artifacts are checked in and tests do not rewrite them. If and only if an
authorized specification revision requires fixture regeneration:

```powershell
python golden/tools/generate_golden_cases.py
python golden/tools/generate_checksums.py
```

Review every changed ledger and expected result before accepting new checksums.

## Tests

From the repository root:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

Tests validate all JSON and CSV fixtures against the schemas, re-run every case
through the independent calculator, assert the coverage contract, verify
completed-bar exclusion and data quarantine, enforce USD 100 risk, and validate
all recorded SHA-256 checksums.
