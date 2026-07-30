# Calculation ledger: buy_sma20_failure_h1

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: BUY rejection fails only its SMA20 touch boundary.
- Evaluation boundary: `2026-02-03T11:00:01Z`
- Signal timeframe: `H1`
- Data status: `READY`
- Coverage: `sma20_failure`, `buy`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-03T11:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `778.7` / `775.6`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `767.0` / `783.0`
- Daily BUY / SELL level: `767.8` / `782.2`
- Recent 21-bar low / high: `767.5` / `788.0`

## Filter and signal decisions

- BUY / SELL Filter: `True` / `True`
- BUY / SELL SMA rejection: `False` / `False`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `767.5` / `767.5`
- SELL pivot / window extreme: `788.0` / `788.0`
- Technical BUY / SELL: `False` / `False`
- Confirmed BUY / SELL: `False` / `False`
- Dashboard state: `FILTERED_BOTH`

## Candidate levels

- No confirmed candidate; entry, stop, target, and size are not calculated.

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
