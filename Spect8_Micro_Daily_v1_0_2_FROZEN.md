# Spect8 Micro Daily Market Scanner

## Frozen Completed-D1 Boundary Clarification v1.0.2

| Field | Value |
|---|---|
| Clarification ID | `SPECT8_MICRO_DAILY_V1_0_2` |
| Status | **FROZEN** |
| Freeze date | 2026-08-04 |
| Base authority | `Spect8_Micro_Daily_v1_0_FROZEN.md` |
| Prior clarification | `Spect8_Micro_Daily_v1_0_1_FROZEN.md` |
| Calculation strategy ID | `SPECT8_MICRO_DAILY_V1_0` |
| Scope | Completed D1 eligibility at an equal signal-close timestamp only |

This document supplements and supersedes the base authority only for the D1
timestamp-boundary behavior defined below. The v1.0.1 simultaneous-direction
clarification remains in force. All Filter arithmetic, Signal, level,
position-sizing, independence, idempotency, risk, and exclusion rules remain
unchanged.

The calculation strategy ID remains `SPECT8_MICRO_DAILY_V1_0`; the active
frozen specification version is `SPECT8_MICRO_DAILY_V1_0_2`.

## 1. Authoritative completed-as-of-close rule

Let `T` be the close timestamp of the completed H1 or H4 signal candle being
evaluated. A D1 candle is eligible when:

```text
d1_bar.is_complete = true
AND
d1_bar.close_time <= T
```

A D1 candle is ineligible when:

```text
d1_bar.is_complete = false
OR
d1_bar.close_time > T
```

A completed D1 candle closing at exactly the same timestamp as the evaluated
H1 or H4 candle is D1 shift `1` and must be included. This is not look-ahead:
both candles are complete at `T`, and evaluation occurs after their close.

## 2. Filter endpoint

The two-bar Daily reference and Wilder D1 ATR(5) must end on the latest
eligible completed D1 candle:

```text
daily_reference = latest 2 eligible completed D1 candles
atr_endpoint = latest eligible completed D1 candle
```

The frozen Filter arithmetic remains:

```text
activation_buffer = Wilder_D1_ATR(5) * 0.05

daily_buy_level = minimum D1 low over daily_reference + activation_buffer
daily_sell_level = maximum D1 high over daily_reference - activation_buffer

buy_filter_matched = recent_21_signal_bar_low <= daily_buy_level
sell_filter_matched = recent_21_signal_bar_high >= daily_sell_level
```

The ATR true-range formula, seed, recurrence, period, and timeframe are
unchanged.

## 3. Safety and independence

- Forming D1 candles remain excluded, including at timestamp equality.
- D1 candles closing after `T` remain look-ahead and are rejected.
- H1 and H4 remain independent strategy instances.
- BUY and SELL Filter results remain independent and non-consuming.
- The Filter remains separate from SMA, pivot, reverse-filter, risk-multiplier,
  position-sizing, stop, target, order, fill, and execution logic.

## 4. Required validation

Deterministic validation must cover:

- equal-close completed D1 inclusion for H1 and H4;
- D1 ATR(5) and two-bar reference ending on that equal-close D1 candle;
- future D1 exclusion;
- forming equal-close D1 exclusion;
- production/reference parity;
- replay/runtime parity;
- unchanged Filter arithmetic and unrelated golden results.

## 5. Traceability and freeze declaration

The former strict-prior interpretation (`D1 close_time < T`) is superseded.
The authoritative boundary is completed-as-of-close (`D1 close_time <= T`).
No prior frozen file is rewritten; v1.0 and v1.0.1 remain historical
authorities for their original scopes.
