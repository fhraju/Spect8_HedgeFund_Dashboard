# Calculation ledger: buy_structural_pivot_failure_h1

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: BUY SMA rejection passes but the older structural extreme invalidates the pivot.
- Evaluation boundary: `2026-02-03T11:00:01Z`
- Signal timeframe: `H1`
- Data status: `READY`
- Coverage: `structural_pivot_failure`, `buy`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-03T11:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `850.1` / `850.05`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `842.0` / `858.0`
- Daily BUY / SELL level: `842.8` / `857.2`
- Recent 21-bar low / high: `842.0` / `853.0`

## Filter and signal decisions

- BUY / SELL Filter: `True` / `False`
- BUY / SELL SMA rejection: `True` / `False`
- BUY / SELL structural pivot: `False` / `True`
- BUY pivot / window extreme: `842.5` / `842.0`
- SELL pivot / window extreme: `853.0` / `853.0`
- Technical BUY / SELL: `False` / `False`
- Confirmed BUY / SELL: `False` / `False`
- Dashboard state: `FILTERED_BUY`

## Candidate levels

- No confirmed candidate; entry, stop, target, and size are not calculated.

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
