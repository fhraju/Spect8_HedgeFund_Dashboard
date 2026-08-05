# Micro Daily Filter Current D1 V2

Strategy version: `MICRO_DAILY_FILTER_CURRENT_D1_V2`

This specification supersedes the legacy Daily Filter for new evaluations. It
does not reinterpret or overwrite historical `SPECT8_MICRO_DAILY_V1_0_3`
records, fixtures, or golden authorities.

## Frozen formula

At each completed canonical H1 close, construct the current New-York-close D1
session from completed, valid, non-synthetic, non-forward-filled canonical H1
bars whose boundaries are inside that session and whose close is no later than
the H1 close.

The previous D1 is the immediately preceding fully completed canonical D1
session. Wilder ATR(5) uses completed canonical D1 candles only and is frozen
at that previous D1 close. The current partial D1 is never an ATR input.

With exact backend `Decimal` arithmetic:

```text
buffer = Wilder_ATR_5 * 0.05
buy_threshold = previous_completed_D1.low + buffer
sell_threshold = previous_completed_D1.high - buffer
buy_matched = current_partial_D1.low <= buy_threshold
sell_matched = current_partial_D1.high >= sell_threshold
```

Comparisons are inclusive. Classification is `NONE`, `BUY`, `SELL`, or
`BUY_AND_SELL`; both sides may match.

## Timing and sharing

One immutable instrument-level snapshot is created per canonical profile,
strategy version, instrument, and completed H1 close. H1 evaluation references
that snapshot. When the same H1 close finalizes an H4 candle, the H4 evaluation
references the identical snapshot ID. Historical evaluations at different
close timestamps retain their own snapshot IDs.

Canonical timestamps are UTC. Daily boundaries are consecutive 17:00
`America/New_York` instants. IC Markets Broker Time is presentation and bucket
alignment context only.

## Input boundary

Provider adapters supply provider-independent raw H1 candles. The strategy
accepts only approved canonical H1 plus H1-derived H4/D1. Provider-native H4,
D1, forming bars, ticks, M1 data, interpolated prices, weekend candles,
synthetic bars, and forward-filled bars are excluded. Unexpected open-market
H1 gaps make the partial D1 unavailable; prices are never invented.

The timeframe-specific 21-bar extrema remain available only where the frozen
candidate-level calculation needs them. They are not Daily Filter evidence.

## Live provider schedule

The Twelve Data strategy runtime discovers and fetches completed H1 only.
Canonical H4 triggers and D1 context are constructed locally. The H1 history
request supplies both Signal warm-up and Daily reconstruction, so an H4 close
reuses the same in-memory history and Daily Filter snapshot rather than making
an H4 request. Runtime telemetry exposes current-minute requests, estimated
daily credits, last H1 request, next expected H1 close, duplicates, excluded
forming bars, and provider errors/retries. Provider quotas remain adapter/runtime
configuration, not strategy formula inputs.
