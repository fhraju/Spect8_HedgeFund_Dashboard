# Calculation ledger: sell_sma20_failure_h1

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: SELL rejection fails only its SMA20 touch boundary.
- Evaluation boundary: `2026-02-03T11:00:01Z`
- Signal timeframe: `H1`
- Data status: `READY`
- Coverage: `sma20_failure`, `sell`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-03T11:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `796.3` / `799.4`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `792.0` / `808.0`
- Daily BUY / SELL level: `792.8` / `807.2`
- Recent 21-bar low / high: `787.0` / `820.0`

## Filter and signal decisions

- BUY / SELL Filter: `True` / `True`
- BUY / SELL SMA rejection: `False` / `False`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `787.0` / `787.0`
- SELL pivot / window extreme: `820.0` / `820.0`
- Technical BUY / SELL: `False` / `False`
- Confirmed BUY / SELL: `False` / `False`
- Dashboard state: `FILTERED_BOTH`

## Candidate levels

- No confirmed candidate; entry, stop, target, and size are not calculated.

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
