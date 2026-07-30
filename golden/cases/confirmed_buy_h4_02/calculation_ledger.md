# Calculation ledger: confirmed_buy_h4_02

- Strategy: `SPECT8_MICRO_DAILY_V1_0`
- Description: H4 confirmed BUY candidate (variant 2).
- Evaluation boundary: `2026-02-07T20:00:01Z`
- Signal timeframe: `H4`
- Data status: `READY`
- Coverage: `confirmed_buy`, `h4`, `risk_usd_100`, `missing_metadata`

## Completed-bar gate

- Completed signal bars used: 35
- Completed D1 bars used: 10
- Excluded incomplete/developing signal bars: 0
- Signal bar close: `2026-02-07T20:00:00Z`
- D1 endpoint close: `2026-01-30T00:00:00Z`

## Indicators

- SMA10 / SMA20: `350.1` / `350.05`
- Wilder D1 ATR(5): `16.0`
- Activation buffer (ATR × 0.05): `0.8`
- Daily raw low / high: `342.0` / `358.0`
- Daily BUY / SELL level: `342.8` / `357.2`
- Recent 21-bar low / high: `342.5` / `353.0`

## Filter and signal decisions

- BUY / SELL Filter: `True` / `False`
- BUY / SELL SMA rejection: `True` / `False`
- BUY / SELL structural pivot: `True` / `True`
- BUY pivot / window extreme: `342.5` / `342.5`
- SELL pivot / window extreme: `353.0` / `353.0`
- Technical BUY / SELL: `True` / `False`
- Confirmed BUY / SELL: `True` / `False`
- Dashboard state: `CONFIRMED_BUY`

## Candidate levels

- BUY entry: `351.0`
- BUY raw / displayed stop: `336.8` / `336.8`
- BUY risk distance / 3R target: `14.2` / `393.6`
- BUY target risk: `$100.00`
- BUY raw / displayed size: `None` / `None`
- BUY contract status: `METADATA_UNAVAILABLE`

The Filter is not consumed. No reverse-filter or risk-multiplier calculation is present.
