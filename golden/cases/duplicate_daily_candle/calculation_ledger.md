# Calculation ledger: duplicate_daily_candle

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: A duplicate D1 candle is rejected before strategy calculation.
- Evaluation boundary: `2026-02-03T11:00:01Z`
- Signal timeframe: `H1`
- Data status: `UNAVAILABLE`
- Coverage: `duplicate_candle`, `duplicate_daily_candle`, `data_unavailable`

## Completed-bar gate

- Completed signal bars used: 0
- Completed D1 bars used: 0
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `None`
- D1 endpoint close: `None`

## Quarantine decision

- Issues: `DUPLICATE_CANDLE`, `MISSING_DAILY_CANDLE`
- Strategy formulas were not evaluated and no candidate was emitted.
