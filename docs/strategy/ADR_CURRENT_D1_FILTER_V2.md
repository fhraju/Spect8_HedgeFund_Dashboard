# ADR: Current D1 Shared Daily Filter V2

Status: accepted

The client clarified that the current Daily Filter must compare the extrema of
the current partial New-York-close D1 candle with the immediately previous
completed D1 candle, buffered by 5% of Wilder ATR(5). The earlier 21-bar
signal-timeframe extrema remain historical behavior and Signal Audit evidence;
they are not inputs to the corrected filter.

Because this clarification changes strategy semantics, new evaluations use the
version `MICRO_DAILY_FILTER_CURRENT_D1_V2`. Historical evaluations, frozen
specifications, golden fixtures, and their recorded meanings remain unchanged.

At every completed canonical H1 close, the system persists the H1 candle,
updates H1-derived H4 state, constructs the current partial D1, and persists one
deterministic instrument-level filter snapshot. H1 and any H4 candle completing
at that same instant reference that snapshot. Evaluations at different closes
retain their timestamp-correct historical snapshot references.
