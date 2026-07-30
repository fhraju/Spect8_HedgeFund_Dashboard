# Calculation ledger: filtered_sell_h4_01

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: H4 SELL Filter matched while the SELL technical signal is false (variant 1).
- Evaluation boundary: `2026-02-07T20:00:01Z`
- Signal timeframe: `H4`
- Data status: `READY`
- Coverage: `filtered_sell_only`, `h4`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-07T20:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `124.6` / `124.8`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `117.0` / `133.0`
- Daily BUY / SELL level: `117.8` / `132.2`
- Recent 21-bar low / high: `120.0` / `132.5`

## Filter and signal decisions

- BUY / SELL Filter: `False` / `True`
- BUY / SELL SMA rejection: `False` / `False`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `120.0` / `120.0`
- SELL pivot / window extreme: `132.5` / `132.5`
- Technical BUY / SELL: `False` / `False`
- Confirmed BUY / SELL: `False` / `False`
- Dashboard state: `FILTERED_SELL`

## Candidate levels

- No confirmed candidate; entry, stop, target, and size are not calculated.

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
