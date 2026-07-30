# Calculation ledger: confirmed_buy_h1_05

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: H1 confirmed BUY candidate (variant 5).
- Evaluation boundary: `2026-02-03T11:00:01Z`
- Signal timeframe: `H1`
- Data status: `READY`
- Coverage: `confirmed_buy`, `h1`, `risk_usd_100`, `developing_bar_exclusion`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 1
- Signal bar close: `2026-02-03T11:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `425.1` / `425.05`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `417.0` / `433.0`
- Daily BUY / SELL level: `417.8` / `432.2`
- Recent 21-bar low / high: `417.5` / `428.0`

## Filter and signal decisions

- BUY / SELL Filter: `True` / `False`
- BUY / SELL SMA rejection: `True` / `False`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `417.5` / `417.5`
- SELL pivot / window extreme: `428.0` / `428.0`
- Technical BUY / SELL: `True` / `False`
- Confirmed BUY / SELL: `True` / `False`
- Dashboard state: `CONFIRMED_BUY`

## Candidate levels

- BUY entry: `426.0`
- BUY raw / displayed stop: `411.8` / `411.8`
- BUY risk distance / 3R target: `14.2` / `468.6`
- BUY target risk: `$100.00`
- BUY raw / displayed size: `0.0704225352` / `0.07`
- BUY contract status: `VALID`

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
