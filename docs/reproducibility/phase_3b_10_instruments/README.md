# Phase 3B ten-instrument reproducibility checkpoint

This checkpoint freezes the user-verified scanner state at the completed H1
close `2026-08-05T14:00:00Z` (IC Markets broker presentation
`2026-08-05 17:00`). The corresponding completed H4 evaluation is
`2026-08-05T13:00:00Z`.

The portable fixtures live under
`backend/tests/fixtures/reproducibility/phase_3b_10_instruments`. They contain
canonical H1 inputs, derived H4 inputs, completed New York 17:00 D1 sessions,
filter snapshots, exact H1/H4 projections, scanner output, event order, and
SHA-256 checksums. They contain no database, API key, authentication token,
environment file, user data, or absolute machine path.

Capture from the configured local database (never calls the provider):

```powershell
python -m backend.app.tools.capture_reproducibility_checkpoint `
  --name phase_3b_10_instruments `
  --evaluation-time 2026-08-05T14:00:00Z
```

Reproduce in a clean temporary database without network access:

```powershell
python -m backend.app.tools.reproduce_reproducibility_checkpoint `
  --name phase_3b_10_instruments
```

Run the regression checkpoint:

```powershell
python -m pytest -q `
  backend/tests/test_phase_3b_10_instrument_reproducibility.py
```

The fixture is a deterministic test/research artifact. It is not Historical
Replay, a backtest, or profitability evidence.
