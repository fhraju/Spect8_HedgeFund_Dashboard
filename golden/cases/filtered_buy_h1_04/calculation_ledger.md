# Calculation ledger: filtered_buy_h1_04

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: H1 BUY Filter matched while the BUY technical signal is false (variant 4).
- Evaluation boundary: `2026-02-03T11:00:01Z`
- Signal timeframe: `H1`
- Data status: `READY`
- Coverage: `filtered_buy_only`, `h1`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-03T11:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `200.4` / `200.2`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `192.0` / `208.0`
- Daily BUY / SELL level: `192.8` / `207.2`
- Recent 21-bar low / high: `192.5` / `205.0`

## Filter and signal decisions

- BUY / SELL Filter: `True` / `False`
- BUY / SELL SMA rejection: `False` / `False`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `192.5` / `192.5`
- SELL pivot / window extreme: `205.0` / `205.0`
- Technical BUY / SELL: `False` / `False`
- Confirmed BUY / SELL: `False` / `False`
- Dashboard state: `FILTERED_BUY`

## Candidate levels

- No confirmed candidate; entry, stop, target, and size are not calculated.

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
