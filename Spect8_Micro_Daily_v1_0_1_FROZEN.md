# Spect8 Micro Daily Market Scanner

## Frozen Specification Clarification v1.0.1

| Field | Value |
|---|---|
| Clarification ID | `SPECT8_MICRO_DAILY_V1_0_1` |
| Status | **FROZEN** |
| Freeze date | 2026-07-30 |
| Base authority | `Spect8_Micro_Daily_v1_0_FROZEN.md` |
| Calculation strategy ID | `SPECT8_MICRO_DAILY_V1_0` |
| Scope | Simultaneous BUY/SELL classification and presentation only |

This document supplements and supersedes the base authority only for the
simultaneous-direction behavior defined below. All Filter, Signal, level,
position-sizing, completed-bar, independence, idempotency, risk, and exclusion
rules in v1.0 remain unchanged.

The calculation strategy ID remains `SPECT8_MICRO_DAILY_V1_0` because no
calculation formula changed. The frozen product/dataset clarification version
is v1.0.1.

## 1. Simultaneous confirmation

For the same strategy, provider, instrument, timeframe, and completed candle,
if:

```text
confirmed_buy = true
AND
confirmed_sell = true
```

then:

- both confirmed results must be preserved;
- the dashboard state must be `CONFIRMED_BOTH`;
- the BUY and SELL entry, raw stop, displayed stop, risk distance, 3R target,
  target risk, contract size, and contract status must be calculated and
  displayed independently;
- neither direction may be selected as the preferred or primary result;
- the status must not be classified as `CONFIRMED_BUY` or `CONFIRMED_SELL`;
- each direction continues to use exactly USD 100 target risk.

## 2. Status projection contract

An instrument status exposes:

```text
levels_results = zero, one, or two directional level results
```

For `CONFIRMED_BOTH`, `levels_results` contains exactly one BUY result and one
SELL result. The compatibility field `levels_result` must be `null` when two
results exist; it may be populated only when exactly one direction is
confirmed.

The protected dashboard renders the BUY and SELL results as independent
opportunity rows or equivalent clearly separated directional presentations.

## 3. Event contract

A simultaneous result preserves the existing seven-stage event order:

```text
BAR_CLOSED
→ FILTER_EVALUATED
→ FILTER_MATCHED
→ SIGNAL_EVALUATED
→ SIGNAL_CONFIRMED
→ LEVELS_CALCULATED
→ STATUS_PROJECTED
```

For the simultaneous result:

- `SIGNAL_CONFIRMED` carries `direction = "BOTH"` and
  `directions = ["BUY", "SELL"]`;
- one `LEVELS_CALCULATED` event carries both independent directional results;
- `STATUS_PROJECTED` carries `dashboard_state = "CONFIRMED_BOTH"`.

This does not create two processed-bar keys or duplicate the event pipeline.

## 4. Required validation

The golden dataset must include at least one deterministic case where:

- both Filters match;
- both technical Signals match;
- both confirmations are true;
- the expected dashboard state is `CONFIRMED_BOTH`;
- both directional entry/stop/target/risk/contract results are present;
- schema, checksum, independent-oracle, production-engine, API, event, and
  frontend tests pass.

## 5. Freeze declaration

The simultaneous-direction clarification above is frozen as v1.0.1. No other
v1.0 rule is changed by this document.
