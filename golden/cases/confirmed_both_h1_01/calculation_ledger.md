# Calculation ledger: confirmed_both_h1_01

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: H1 BUY and SELL are simultaneously confirmed and retained independently.
- Evaluation boundary: `2026-02-03T11:00:01Z`
- Signal timeframe: `H1`
- Data status: `READY`
- Coverage: `confirmed_buy`, `confirmed_sell`, `confirmed_both`, `simultaneous_direction`, `h1`, `risk_usd_100`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-03T11:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `500.0` / `500.0`
- Wilder D1 ATR(5): `2.0`
- Activation buffer (ATR × 0.05): `0.1`
- Daily raw low / high: `499.0` / `501.0`
- Daily BUY / SELL level: `499.1` / `500.9`
- Recent 21-bar low / high: `499.0` / `501.0`

## Filter and signal decisions

- BUY / SELL Filter: `True` / `True`
- BUY / SELL SMA rejection: `True` / `True`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `499.0` / `499.0`
- SELL pivot / window extreme: `501.0` / `501.0`
- Technical BUY / SELL: `True` / `True`
- Confirmed BUY / SELL: `True` / `True`
- Dashboard state: `CONFIRMED_BOTH`

## Candidate levels

- BUY entry: `500.0`
- BUY raw / displayed stop: `498.2` / `498.2`
- BUY risk distance / 3R target: `1.8` / `505.4`
- BUY target risk: `$100.00`
- BUY raw / displayed size: `0.5555555556` / `0.55`
- BUY contract status: `VALID`
- SELL entry: `500.0`
- SELL raw / displayed stop: `501.8` / `501.8`
- SELL risk distance / 3R target: `1.8` / `494.6`
- SELL target risk: `$100.00`
- SELL raw / displayed size: `0.5555555556` / `0.55`
- SELL contract status: `VALID`

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
