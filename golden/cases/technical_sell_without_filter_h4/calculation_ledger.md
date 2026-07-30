# Calculation ledger: technical_sell_without_filter_h4

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: SELL technical signal exists without the corresponding Daily Filter.
- Evaluation boundary: `2026-02-07T20:00:01Z`
- Signal timeframe: `H4`
- Data status: `READY`
- Coverage: `technical_signal_without_filter`, `sell`, `h4`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-07T20:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `674.9` / `674.95`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `667.0` / `683.0`
- Daily BUY / SELL level: `667.8` / `682.2`
- Recent 21-bar low / high: `672.0` / `678.0`

## Filter and signal decisions

- BUY / SELL Filter: `False` / `False`
- BUY / SELL SMA rejection: `False` / `True`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `672.0` / `672.0`
- SELL pivot / window extreme: `678.0` / `678.0`
- Technical BUY / SELL: `False` / `True`
- Confirmed BUY / SELL: `False` / `False`
- Dashboard state: `WATCHING`

## Candidate levels

- No confirmed candidate; entry, stop, target, and size are not calculated.

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
