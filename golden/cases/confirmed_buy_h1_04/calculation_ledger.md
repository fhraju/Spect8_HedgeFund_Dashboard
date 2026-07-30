# Calculation ledger: confirmed_buy_h1_04

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: H1 confirmed BUY candidate (variant 4).
- Evaluation boundary: `2026-02-03T11:00:01Z`
- Signal timeframe: `H1`
- Data status: `READY`
- Coverage: `confirmed_buy`, `h1`, `risk_usd_100`, `equality_boundaries`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-03T11:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `400.0` / `400.0`
- Wilder D1 ATR(5): `4.0`
- Activation buffer (ATR × 0.05): `0.2`
- Daily raw low / high: `399.8` / `403.8`
- Daily BUY / SELL level: `400.0` / `403.6`
- Recent 21-bar low / high: `400.0` / `401.0`

## Filter and signal decisions

- BUY / SELL Filter: `True` / `False`
- BUY / SELL SMA rejection: `True` / `True`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `400.0` / `400.0`
- SELL pivot / window extreme: `401.0` / `401.0`
- Technical BUY / SELL: `True` / `True`
- Confirmed BUY / SELL: `True` / `False`
- Dashboard state: `CONFIRMED_BUY`

## Candidate levels

- BUY entry: `400.0`
- BUY raw / displayed stop: `398.5` / `398.5`
- BUY risk distance / 3R target: `1.5` / `404.5`
- BUY target risk: `$100.00`
- BUY raw / displayed size: `0.6666666667` / `0.66`
- BUY contract status: `VALID`

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
