# Phase 2A Production Calculation Engine

## Boundary

Phase 2A replaces the walking-skeleton runtime adapter with an independent
production implementation of `SPECT8_MICRO_DAILY_V1_0`. The active frozen
boundary authority is `SPECT8_MICRO_DAILY_V1_0_3`.

The default backend runtime:

1. loads the selected synthetic OHLC CSV inputs and instrument metadata;
2. evaluates them with `Spect8StrategyEvaluator`;
3. maps the calculated result into the existing deterministic event pipeline;
4. persists the status and events through the Phase 1 SQLite repository.

`backend/app/golden_adapter.py` remains only as a Phase 1 historical artifact.
Neither `main.py`, `service.py`, `synthetic_inputs.py`, nor the production
engine imports or invokes it.

## Production modules

- `engine/indicators.py`: completed-bar SMA, Wilder ATR, and common extrema.
- `engine/micro_daily_filter.py`: independent non-consuming BUY/SELL filters.
- `engine/spect8_signal.py`: SMA rejection and structural pivots.
- `engine/levels.py`: entry, raw/display stop, invalid-R guard, and 3R target.
- `engine/position_sizing.py`: USD 100 sizing and provider constraints.
- `engine/strategy.py`: pure validation and orchestration.
- `engine/models.py`: immutable requests, metadata, intermediates, and results.
- `synthetic_inputs.py`: runtime input loader for the two walking examples.

Formula primitives have one production implementation. FastAPI routes,
frontend code, persistence, and input loading do not calculate strategy values.

## Validation contract

The production parity suite runs all 59 committed cases and compares:

- data quarantine and issues;
- completed/excluded bar counts and endpoints;
- Filter, Signal, and confirmed booleans;
- SMA10, SMA20, Wilder ATR(5), both ATR distances, and point adjustment;
- daily raw and activation levels;
- 21-bar extremes;
- pivot timestamp, shift, price, structural extreme, and result;
- entry, raw/display stop, risk distance, and 3R target;
- USD 100 target risk, monetary loss, raw/display size, and contract status;
- deterministic reason codes.

Committed numeric fixture values are quantized to ten decimal places. Tests
therefore use an absolute tolerance of `1e-10` for numeric comparisons.
Booleans, directions, reason codes, classifications, events, and issues are
compared exactly.

## v1.0.1 simultaneous-direction clarification

The frozen v1.0.1 clarification resolves simultaneous BUY and SELL
confirmation as `CONFIRMED_BOTH`. The engine preserves both independent
candidates, the projection exposes both through `levels_results`, and the
dashboard renders one directional row for each result. The compatibility
`levels_result` field is null when two results exist, so neither direction is
silently prioritized.

The dedicated `confirmed_both_h1_01` golden case validates both complete
entry/stop/target/risk/contract result sets and the single seven-event
processing trace.

## v1.0.2 completed-D1 boundary clarification

A completed D1 candle is eligible when `d1.close_time <= signal.close_time`.
Equality represents the just-completed D1 shift 1 at the signal boundary, not
look-ahead. A forming D1 candle and every D1 close after the signal remain
excluded. Dedicated H1 and H4 golden cases freeze the changed ATR, activation
buffer, raw daily extrema, daily levels, and Filter outcomes.

## v1.0.3 New York-close D1 authority

Canonical D1 context is aggregated from completed H1 bars over DST-aware
17:00-to-17:00 `America/New_York` sessions. Provider-native Daily OHLC is not
eligible. Filter arithmetic is unchanged; two new golden cases freeze summer
21:00Z equal-close behavior for H1 and H4.
