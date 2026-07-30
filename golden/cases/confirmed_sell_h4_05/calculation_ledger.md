# Calculation ledger: confirmed_sell_h4_05

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: H4 confirmed SELL candidate (variant 5).
- Evaluation boundary: `2026-02-07T20:00:01Z`
- Signal timeframe: `H4`
- Data status: `READY`
- Coverage: `confirmed_sell`, `h4`, `risk_usd_100`, `below_provider_minimum`, `provider_stop_adjustment`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-07T20:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `424.9` / `424.95`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `417.0` / `433.0`
- Daily BUY / SELL level: `417.8` / `432.2`
- Recent 21-bar low / high: `422.0` / `432.5`

## Filter and signal decisions

- BUY / SELL Filter: `False` / `True`
- BUY / SELL SMA rejection: `False` / `True`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `422.0` / `422.0`
- SELL pivot / window extreme: `432.5` / `432.5`
- Technical BUY / SELL: `False` / `True`
- Confirmed BUY / SELL: `False` / `True`
- Dashboard state: `CONFIRMED_SELL`

## Candidate levels

- SELL entry: `424.0`
- SELL raw / displayed stop: `438.2` / `444.0`
- SELL risk distance / 3R target: `20.0` / `364.0`
- SELL target risk: `$100.00`
- SELL raw / displayed size: `0.05` / `None`
- SELL contract status: `BELOW_PROVIDER_MINIMUM`

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
