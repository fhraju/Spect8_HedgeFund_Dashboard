# Phase 3B Client Acceptance Candidate

Client approval has **not** yet been received. This checklist is the review
candidate for the EUR/USD-only read-only dashboard.

## Demonstration checklist

- [ ] Header clearly identifies EUR/USD / Euro–US Dollar and Twelve Data.
- [ ] Separate H1 and H4 cards are visible and understandable.
- [ ] Filter outcome is clearly distinct from signal confirmation.
- [ ] `NO SIGNAL` is understood as a valid strategy state, not an error.
- [ ] Deterministic reason codes are useful and not overly technical.
- [ ] Candle, moving-average, volatility, and threshold values are sufficient.
- [ ] D1 context close is visible and clearly earlier than signal evaluation.
- [ ] Provider health and last successful synchronization are understandable.
- [ ] Healthy, stale, empty, unavailable, insufficient-history, and
      quarantined states are distinguishable.
- [ ] Recent persisted events are useful.
- [ ] Manual refresh works.
- [ ] Automatic 60-second dashboard refresh is acceptable.
- [ ] Desktop layout is usable on the client’s normal display.
- [ ] Mobile/small-screen layout is usable on the client’s normal device.
- [ ] “Read only”, execution disabled, zero orders, and zero fills are clear.
- [ ] UTC/provider conventions are accepted:
  - Twelve Data weekend EUR/USD candles are retained;
  - H4 opens are 01:00, 05:00, 09:00, 13:00, 17:00, and 21:00 UTC;
  - D1 opens at 00:00 UTC.

## Suggested demonstration

1. Open the authenticated dashboard on desktop.
2. Identify provider health, last sync, and latest H1/H4/D1 closes.
3. Walk through H1 independently from H4.
4. Explain filter, signal, reason codes, and market values.
5. Show a no-signal state and explain that it is healthy.
6. Show the recent event projection.
7. Demonstrate manual refresh and explain automatic refresh.
8. Show a deterministic stale/unavailable-state capture.
9. Resize to the client’s mobile viewport.
10. Confirm the execution panel remains disabled with zero orders/fills.

## Client feedback template

1. Is the strategy status understandable?
2. Are the filter, signal, and reason explanations sufficient?
3. Which information is unnecessary or missing?
4. Is the layout useful on your normal device?
5. Do you approve this design as the basis for multi-market expansion?

Decision:

- [ ] Approved
- [ ] Approved with changes
- [ ] Not approved

Requested changes:

```text

```

Client name:

Review date:

Explicit approval statement:

```text

```
