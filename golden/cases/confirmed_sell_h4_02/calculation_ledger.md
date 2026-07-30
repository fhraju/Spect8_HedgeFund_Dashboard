# Calculation ledger: confirmed_sell_h4_02

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: H4 confirmed SELL candidate (variant 2).
- Evaluation boundary: `2026-02-07T20:00:01Z`
- Signal timeframe: `H4`
- Data status: `READY`
- Coverage: `confirmed_sell`, `h4`, `risk_usd_100`, `missing_metadata`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-07T20:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `349.9` / `349.95`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `342.0` / `358.0`
- Daily BUY / SELL level: `342.8` / `357.2`
- Recent 21-bar low / high: `347.0` / `357.5`

## Filter and signal decisions

- BUY / SELL Filter: `False` / `True`
- BUY / SELL SMA rejection: `False` / `True`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `347.0` / `347.0`
- SELL pivot / window extreme: `357.5` / `357.5`
- Technical BUY / SELL: `False` / `True`
- Confirmed BUY / SELL: `False` / `True`
- Dashboard state: `CONFIRMED_SELL`

## Candidate levels

- SELL entry: `349.0`
- SELL raw / displayed stop: `363.2` / `363.2`
- SELL risk distance / 3R target: `14.2` / `306.4`
- SELL target risk: `$100.00`
- SELL raw / displayed size: `None` / `None`
- SELL contract status: `METADATA_UNAVAILABLE`

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
