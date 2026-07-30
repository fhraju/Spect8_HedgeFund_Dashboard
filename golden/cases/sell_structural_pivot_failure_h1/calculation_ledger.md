# Calculation ledger: sell_structural_pivot_failure_h1

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: SELL SMA rejection passes but the older structural extreme invalidates the pivot.
- Evaluation boundary: `2026-02-03T11:00:01Z`
- Signal timeframe: `H1`
- Data status: `READY`
- Coverage: `structural_pivot_failure`, `sell`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-03T11:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `874.9` / `874.95`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `867.0` / `883.0`
- Daily BUY / SELL level: `867.8` / `882.2`
- Recent 21-bar low / high: `872.0` / `883.0`

## Filter and signal decisions

- BUY / SELL Filter: `False` / `True`
- BUY / SELL SMA rejection: `False` / `True`
- BUY / SELL structural pivot: `True` / `False`
- BUY pivot / window extreme: `872.0` / `872.0`
- SELL pivot / window extreme: `882.5` / `883.0`
- Technical BUY / SELL: `False` / `False`
- Confirmed BUY / SELL: `False` / `False`
- Dashboard state: `FILTERED_SELL`

## Candidate levels

- No confirmed candidate; entry, stop, target, and size are not calculated.

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
