# Calculation ledger: equal_close_filter_boundary_h1

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: H1 completed signal includes the D1 candle closing at the identical UTC timestamp.
- Evaluation boundary: `2026-01-11T00:00:01Z`
- Signal timeframe: `H1`
- Data status: `READY`
- Coverage: `equal_close_d1_boundary`, `completed_as_of_close`, `h1`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-01-11T00:00:00Z`
- D1 endpoint close: `2026-01-11T00:00:00Z`

## Indicators

- SMA10 / SMA20: `100.0` / `100.0`
- Wilder D1 ATR(5): `11.6`
- Activation buffer (ATR × 0.05): `0.58`
- Daily raw low / high: `50.0` / `101.0`
- Daily BUY / SELL level: `50.58` / `100.42`
- Recent 21-bar low / high: `51.0` / `100.0`

## Filter and signal decisions

- BUY / SELL Filter: `False` / `False`
- BUY / SELL SMA rejection: `True` / `True`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `51.0` / `51.0`
- SELL pivot / window extreme: `100.0` / `100.0`
- Technical BUY / SELL: `True` / `True`
- Confirmed BUY / SELL: `False` / `False`
- Dashboard state: `WATCHING`

## Candidate levels


## Completed-as-of-close boundary evidence

- Eligible D1 closes: `2026-01-02T00:00:00Z`, `2026-01-03T00:00:00Z`, `2026-01-04T00:00:00Z`, `2026-01-05T00:00:00Z`, `2026-01-06T00:00:00Z`, `2026-01-07T00:00:00Z`, `2026-01-08T00:00:00Z`, `2026-01-09T00:00:00Z`, `2026-01-10T00:00:00Z`, `2026-01-11T00:00:00Z`
- Selected two D1 closes: `2026-01-10T00:00:00Z`, `2026-01-11T00:00:00Z`
- The latest selected D1 close equals the signal close and is complete.
- No confirmed candidate; entry, stop, target, and size are not calculated.

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
