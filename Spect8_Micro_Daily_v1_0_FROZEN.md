# Spect8 Micro Daily Market Scanner

## Frozen Strategy and Product Specification

| Field | Value |
|---|---|
| Specification ID | `SPECT8_MICRO_DAILY_V1_0` |
| Status | **FROZEN** |
| Freeze date | 2026-07-30 |
| Project | Separate paid client Market Scanner project |
| Source baseline | `Spect8(V3.8).mq4` |
| Source SHA-256 | `40a355c992d5fc71d7ae8c6801ba01ce361eaf94bea99dd89e5cee6727904b56` |
| Operating mode | Read-only observer/scanner |
| Signal instances | Independent H1 and H4 |
| Risk per candidate | USD 100 |

This document is the implementation authority for the first Spect8 client Market Scanner version. The MQL source is provenance and supporting evidence, but where it differs from this document, this frozen specification takes precedence.

Any strategy-rule change requires:

1. A written change request.
2. A new specification version.
3. Updated golden test cases.
4. Revalidation before deployment.

No implementation may silently reinterpret, optimize, repair, or extend the rules.

---

## 1. Product objective

Build a password-protected, hedge-fund-style web dashboard that scans configured instruments and reports:

- which instruments meet the Spect8 Micro Daily Filter;
- which filtered instruments also meet the Spect8 technical Signal;
- the direction, timeframe, evidence, entry reference, stop, target, and estimated contract size;
- the current data-feed and processing health;
- a chronological event history.

The product is an observer. It must not place, modify, or close orders.

---

## 2. Frozen Version 1 scope

### Included

- Maximum 25 configured instruments.
- Independent H1 and H4 strategy instances for every instrument.
- BUY and SELL evaluation.
- Closed bars only.
- Spect8 Micro Daily Filter.
- Spect8 SMA rejection and structural-pivot Signal.
- ATR activation buffer.
- ATR-from-extreme stop calculation.
- 3R target calculation.
- USD 100 target risk for every confirmed candidate.
- Current-status projection.
- Event history.
- Python FastAPI backend.
- Next.js, React, TypeScript, and Tailwind frontend.
- Single-client password-protected login.
- SQLite persistence for Version 1.
- One market-data provider for Version 1.

### Excluded

- Weekly/Macro filter.
- Reverse filter.
- Filter consumption or first-trade state.
- First-filtered-trade risk multiplier.
- Automatic or manual order execution.
- Multi-trade, stop-and-reverse, and open-position management.
- Backtesting and optimization.
- Charting.
- News, macroeconomic, sentiment, or discretionary filters.
- Multi-user accounts, subscriptions, and permissions.
- Multiple simultaneous data providers.

---

## 3. Deliberate differences from the MQL source

The following are intentional frozen decisions:

| MQL source behaviour | Frozen scanner behaviour |
|---|---|
| Some calculations use the forming bar at shift `0` | Every strategy calculation uses completed bars only |
| SMA values are read from shift `0` while the signal candle is shift `1` | SMA and signal candle both use the same last completed bar |
| Daily reference includes the developing D1 candle | Daily reference uses completed D1 candles only |
| Filter is consumed after a successful first order per side | Filter is never consumed |
| First filtered order can use a 2× risk multiplier | Every candidate uses USD 100 target risk |
| Optional reverse filter exists | Reverse filter is excluded |
| Signal timeframe is the attached chart timeframe | H1 and H4 run as explicit independent instances |
| Entry and some level calculations use live Bid/Ask and forming-bar extremes | Candidate entry and structural calculations use completed-bar data |

These differences must be covered by automated tests.

---

## 4. Canonical data rules

### 4.1 Candle model

Every candle must contain:

- canonical instrument ID;
- timeframe;
- open time in UTC;
- close time in UTC;
- open;
- high;
- low;
- close;
- optional volume;
- provider identity.

### 4.2 Closed-bar rule

A candle may be processed only after its configured close time has passed and the provider identifies it as complete.

- Signal-bar index `1` means the most recently completed bar.
- Index `2` means the completed bar immediately before index `1`.
- Index `0` is never used by the strategy.

Incomplete, duplicate, missing, or out-of-order candles must not silently enter the strategy engine.

### 4.3 Higher-timeframe alignment

At an H1 or H4 evaluation time, the engine may use only D1 candles that were fully completed before that signal bar closed.

No future D1 information or partially completed D1 candle may be used.

### 4.4 Provider session

D1 boundaries depend on the selected provider. The provider name, session timezone, and candle-boundary convention must be stored with every evaluation.

Golden tests and production validation must use data from the same provider and session convention.

---

## 5. Frozen configuration

| Parameter | Frozen value |
|---|---:|
| Signal timeframes | H1 and H4 |
| Directions | BUY and SELL |
| Fast SMA period | 10 |
| Slow SMA period | 20 |
| Pivot lookback (`CLB`) | 10 completed signal bars |
| Structural lookback (`BLB`) | 20 completed bars beginning at the pivot |
| Activation recent-extreme window | 21 completed signal bars |
| Daily reference timeframe | D1 |
| Daily reference bars | 2 completed D1 bars |
| Activation-buffer ATR timeframe | D1 |
| Activation-buffer ATR period | 5 |
| Activation-buffer multiplier | 0.05 |
| Stop structural window | 21 completed signal bars |
| Stop ATR timeframe | D1 |
| Stop ATR period | 5 |
| Stop ATR multiplier | 0.35 |
| Stop adjustment | 10 instrument points |
| Profit target | 3R |
| Target risk | USD 100 for every confirmed candidate |
| Reverse filter | Disabled and excluded |
| Filter consumption | Never |

SMA and ATR values must be calculated locally from the selected provider's OHLC candles. ATR must use a documented Wilder-style calculation compatible with MT4 `iATR`, with sufficient warm-up history.

---

## 6. Micro Daily Filter

The Daily Filter is evaluated independently for BUY and SELL on every completed H1 and H4 bar.

### 6.1 Activation buffer

Using the last completed D1 bar as the indicator endpoint:

```text
activation_buffer = ATR_D1_Wilder(period=5) × 0.05
```

### 6.2 Daily reference levels

Using the two most recently completed D1 candles:

```text
daily_raw_low  = minimum(D1 low[1], D1 low[2])
daily_raw_high = maximum(D1 high[1], D1 high[2])

daily_buy_level  = daily_raw_low  + activation_buffer
daily_sell_level = daily_raw_high - activation_buffer
```

### 6.3 Recent signal-timeframe extremes

For each independent H1 or H4 instance:

```text
recent_low  = minimum low over the 21 most recently completed signal bars
recent_high = maximum high over the 21 most recently completed signal bars
```

### 6.4 BUY Filter

```text
buy_filter_matched = recent_low <= daily_buy_level
```

### 6.5 SELL Filter

```text
sell_filter_matched = recent_high >= daily_sell_level
```

BUY and SELL filters are independent. Both may be true at the same time.

### 6.6 Non-consuming state

The filter is a current, objective market condition.

- A match does not consume, disarm, or lock the filter.
- A confirmed signal does not consume the filter.
- A dashboard refresh or application restart does not change the filter.
- The filter remains matched for as long as the frozen formula remains true.
- The next evaluation may emit `FILTER_EXITED` when the formula becomes false.

There is no concept of “first trade of the day” in Version 1.

---

## 7. Spect8 technical Signal

The Signal is calculated independently for H1 and H4 using the most recently completed candle and locally calculated SMAs ending on that same candle.

Define:

```text
C1 = close of the most recently completed signal bar
L1 = low of the most recently completed signal bar
H1 = high of the most recently completed signal bar
SMA10 = 10-period SMA ending on that completed bar
SMA20 = 20-period SMA ending on that completed bar
```

### 7.1 BUY SMA rejection condition

```text
C1 >= SMA10
L1 <= SMA10
C1 >= SMA20
L1 <= SMA20
```

The completed candle must touch or cross both SMAs and close at or above both.

### 7.2 SELL SMA rejection condition

```text
C1 <= SMA10
H1 >= SMA10
C1 <= SMA20
H1 >= SMA20
```

The completed candle must touch or cross both SMAs and close at or below both.

### 7.3 BUY structural-pivot condition

1. Find the minimum low within the 10 most recently completed signal bars.
2. If the minimum occurs more than once, select its most recent occurrence.
3. Call that bar the BUY pivot and its low `pivot_low`.
4. Starting with the pivot bar, inspect 20 completed bars including the pivot and extending backward in time.
5. The minimum low in that 20-bar window must equal `pivot_low`.

Equivalent condition:

```text
minimum_low_in_20_bar_window_from_pivot >= pivot_low
```

Because the pivot is included in the window, equality is the valid outcome.

### 7.4 SELL structural-pivot condition

1. Find the maximum high within the 10 most recently completed signal bars.
2. If the maximum occurs more than once, select its most recent occurrence.
3. Call that bar the SELL pivot and its high `pivot_high`.
4. Starting with the pivot bar, inspect 20 completed bars including the pivot and extending backward in time.
5. The maximum high in that 20-bar window must equal `pivot_high`.

Equivalent condition:

```text
maximum_high_in_20_bar_window_from_pivot <= pivot_high
```

Because the pivot is included in the window, equality is the valid outcome.

### 7.5 Technical signal results

```text
technical_buy_signal =
    buy_sma_rejection
    AND buy_structural_pivot

technical_sell_signal =
    sell_sma_rejection
    AND sell_structural_pivot
```

---

## 8. Confirmed candidate classification

### 8.1 Confirmed BUY

```text
confirmed_buy =
    buy_filter_matched
    AND technical_buy_signal
```

### 8.2 Confirmed SELL

```text
confirmed_sell =
    sell_filter_matched
    AND technical_sell_signal
```

A technical signal without the corresponding Daily Filter is not a confirmed candidate. It may be retained in diagnostic evidence but must not appear in the confirmed-signal list.

The H1 and H4 results are independent:

- H1 BUY does not create or suppress H4 BUY.
- H4 SELL does not create or suppress H1 SELL.
- No cross-timeframe confirmation is required.

---

## 9. Entry, stop, target, and risk

These values are observer estimates, not executable orders.

### 9.1 Entry reference

```text
entry_reference = close of the confirmed signal bar
```

### 9.2 Structural extremes

```text
stop_window_low  = minimum low over the 21 most recently completed signal bars
stop_window_high = maximum high over the 21 most recently completed signal bars
```

### 9.3 Stop ATR

Using the last completed D1 bar as the endpoint:

```text
stop_atr_distance = ATR_D1_Wilder(period=5) × 0.35
point_adjustment  = instrument_point_size × 10
```

### 9.4 BUY stop and target

```text
buy_stop = stop_window_low - stop_atr_distance - point_adjustment
buy_R    = entry_reference - buy_stop
buy_target = entry_reference + (3 × buy_R)
```

The candidate is invalid if `buy_R <= 0`.

### 9.5 SELL stop and target

```text
sell_stop = stop_window_high + stop_atr_distance + point_adjustment
sell_R    = sell_stop - entry_reference
sell_target = entry_reference - (3 × sell_R)
```

The candidate is invalid if `sell_R <= 0`.

### 9.6 Minimum stop distance

If the selected provider supplies a minimum stop distance, the displayed stop may be adjusted outward to satisfy it. The engine must retain both:

- `raw_strategy_stop`;
- `provider_adjusted_stop`.

The target and contract estimate must use the displayed provider-adjusted stop when an adjustment occurs.

### 9.7 Risk and contract estimate

Every confirmed candidate uses:

```text
target_risk_usd = 100.00
```

There is no first-filtered multiplier.

The contract estimate must use the selected provider's point/tick value, contract specification, conversion rate, minimum size, maximum size, and size step.

```text
raw_size = target_risk_usd / monetary_loss_per_one_contract_at_stop
display_size = raw_size rounded down to the valid provider size step
```

The engine must never silently increase size above the USD 100 target. If the provider minimum size would exceed the risk target, display:

```text
contract_status = BELOW_PROVIDER_MINIMUM
contract = N/A
```

If required metadata or conversion data is unavailable, display:

```text
contract_status = METADATA_UNAVAILABLE
contract = N/A
```

---

## 10. Event-driven processing contract

The required processing order for each instrument and timeframe is:

```text
BAR_CLOSED
→ FILTER_EVALUATED
→ FILTER_MATCHED or FILTER_NOT_MATCHED
→ SIGNAL_EVALUATED
→ SIGNAL_CONFIRMED or SIGNAL_NOT_CONFIRMED
→ LEVELS_CALCULATED when confirmed
→ STATUS_PROJECTED
```

State-transition events include:

- `FILTER_ENTERED`;
- `FILTER_EXITED`;
- `SIGNAL_CONFIRMED`;
- `SIGNAL_INVALIDATED`;
- `DATA_UNAVAILABLE`;
- `DATA_RECOVERED`.

Each processing result must use an idempotency key:

```text
strategy_id + provider + instrument + timeframe + closed_bar_time
```

The same completed candle must never create duplicate signal events after polling, retry, or application restart.

---

## 11. Current-state and expiry rules

- Filter state is recalculated after every completed H1 or H4 candle.
- A confirmed signal is current for the completed candle on which it was generated.
- At the next completed candle, the complete Filter and Signal rules are recalculated.
- If the corresponding confirmed condition is no longer true, emit `SIGNAL_INVALIDATED`.
- Historical confirmed events remain in the event history.
- A data outage must not convert the last known state into a new signal.
- Stale status must be visibly labelled with its last successful evaluation time.

---

## 12. Dashboard contract

### 12.1 Authentication

- One client account/password.
- Password stored only as a secure hash or deployment secret.
- Successful login creates a secure, HTTP-only session cookie.
- Unauthenticated requests cannot access protected pages or strategy data.
- Logout invalidates the session.
- Market-data and backend secrets must never be sent to the browser.

### 12.2 Required dashboard states

- `WATCHING`;
- `FILTERED_BUY`;
- `FILTERED_SELL`;
- `FILTERED_BOTH`;
- `CONFIRMED_BUY`;
- `CONFIRMED_SELL`;
- `SIGNAL_INVALIDATED`;
- `DATA_STALE`;
- `DATA_UNAVAILABLE`.

### 12.3 Required columns

- Instrument.
- Timeframe.
- Strategy ID.
- Filter direction/status.
- Signal direction/status.
- Entry reference.
- Raw stop.
- Display stop.
- Target.
- Target risk.
- Estimated contract.
- Contract status.
- Daily BUY level.
- Daily SELL level.
- Signal candle close time.
- Last successful update.
- Evidence/reason.

### 12.4 Required API endpoints

- `GET /health`
- `GET /instruments`
- `GET /statuses`
- `GET /filtered`
- `GET /signals`
- `GET /events`

The frontend must not calculate SMAs, ATRs, filters, signals, stops, targets, or contract sizes.

---

## 13. Validation and acceptance criteria

Implementation is not accepted until all tests pass.

### 13.1 Deterministic unit tests

- SMA10 and SMA20 closed-bar calculations.
- Wilder D1 ATR(5) calculations.
- Daily BUY and SELL levels.
- BUY Filter pass and fail.
- SELL Filter pass and fail.
- BUY SMA rejection pass and fail.
- SELL SMA rejection pass and fail.
- BUY structural-pivot pass and fail.
- SELL structural-pivot pass and fail.
- Confirmed BUY and SELL classification.
- H1 and H4 independence.
- Stop and 3R target calculations.
- USD 100 contract calculation and round-down behaviour.
- Missing metadata behaviour.

### 13.2 State tests

- Filter remains available after one or multiple confirmed signals.
- No filter-consumed state exists.
- No risk multiplier is applied.
- Reverse filter cannot affect results.
- Duplicate and out-of-order candles are rejected or quarantined.
- Restarting the service does not duplicate events.
- Stale data is labelled, not treated as a new signal.

### 13.3 Golden examples

Before live deployment, prepare at minimum:

- 5 Filtered BUY examples per timeframe;
- 5 Filtered SELL examples per timeframe;
- 5 Confirmed BUY examples per timeframe;
- 5 Confirmed SELL examples per timeframe;
- negative examples isolating each failed condition.

Golden examples must record provider identity, candle timestamps, source OHLC, intermediate values, and expected result.

### 13.4 System acceptance

- Backend automated tests pass.
- Frontend type-check and production build pass.
- Login, logout, and protected-route tests pass.
- One complete synthetic event flows from `BAR_CLOSED` to the protected dashboard.
- Real-provider smoke test passes for the configured instruments.
- A continuous 24–48 hour observer test produces no duplicate signals or unexplained gaps.

---

## 14. Configuration not frozen as strategy logic

The following deployment values may be selected later without changing the strategy version:

- final list of up to 25 instruments;
- market-data provider;
- provider credentials;
- deployment host;
- dashboard branding;
- polling schedule, provided completed H1/H4 bars are processed promptly;
- password and other secrets.

Changing candle boundaries, provider session conventions, formula parameters, risk, event order, or classification rules requires a new strategy specification version.

---

## 15. Freeze declaration

`SPECT8_MICRO_DAILY_V1_0` is frozen with:

- Daily/Micro Filter;
- completed-bar calculations only;
- two completed D1 reference bars;
- independent H1 and H4 instances;
- persistent non-consuming Filter;
- USD 100 target risk for every confirmed candidate;
- no first-filtered risk multiplier;
- no reverse filter;
- read-only observation;
- no execution.

This is the authoritative starting point for the project walking skeleton, golden cases, backend engine, API, and protected dashboard.
