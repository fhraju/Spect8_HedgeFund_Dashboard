# Calculation ledger: confirmed_buy_h4_01

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: H4 confirmed BUY candidate (variant 1).
- Evaluation boundary: `2026-02-07T20:00:01Z`
- Signal timeframe: `H4`
- Data status: `READY`
- Coverage: `confirmed_buy`, `h4`, `risk_usd_100`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-07T20:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `325.1` / `325.05`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `317.0` / `333.0`
- Daily BUY / SELL level: `317.8` / `332.2`
- Recent 21-bar low / high: `317.5` / `328.0`

## Filter and signal decisions

- BUY / SELL Filter: `True` / `False`
- BUY / SELL SMA rejection: `True` / `False`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `317.5` / `317.5`
- SELL pivot / window extreme: `328.0` / `328.0`
- Technical BUY / SELL: `True` / `False`
- Confirmed BUY / SELL: `True` / `False`
- Dashboard state: `CONFIRMED_BUY`

## Candidate levels

- BUY entry: `326.0`
- BUY raw / displayed stop: `311.8` / `311.8`
- BUY risk distance / 3R target: `14.2` / `368.6`
- BUY target risk: `$100.00`
- BUY raw / displayed size: `0.0704225352` / `0.07`
- BUY contract status: `VALID`

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
