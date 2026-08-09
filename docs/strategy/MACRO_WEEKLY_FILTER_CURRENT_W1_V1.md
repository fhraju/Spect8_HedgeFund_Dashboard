# Macro Weekly Filter Current W1 V1

`MACRO_WEEKLY_FILTER_CURRENT_W1_V1` is an additive upstream filter authority.
It does not replace or modify `MICRO_DAILY_FILTER_CURRENT_D1_V2`.

## Authority

- Mode: `MACRO`
- Weekly boundary: Friday 17:00 `America/New_York`, converted to UTC for storage
- Partial W1 source: eligible completed canonical H1 bars through the evaluation H1 close
- ATR: Wilder ATR(5) over completed authoritative W1 sessions only
- Buffer: `Wilder W1 ATR(5) * 0.05`
- BUY: `current_partial_W1.low <= previous_completed_W1.low + buffer`
- SELL: `current_partial_W1.high >= previous_completed_W1.high - buffer`

The current partial W1 is not an ATR input. Provider-native weekly calendars do
not participate in construction.

## Persistence and compatibility

The additive `weekly_filter_snapshots` table stores Macro snapshots without
changing `daily_filter_snapshots`. `runtime_configuration.active_filter_mode`
is the one authoritative runtime choice and defaults to `MICRO` for existing
databases. Existing status/snapshot/event payloads without a `filter_mode` field
remain deterministically Micro records because Macro did not exist when those
records were produced; they are read compatibly and are not rewritten.

Switching modes changes future evaluation authority. Historical evaluations
retain their original strategy version, snapshot identifier, events, and data.
Daily ATR remains the downstream levels/stops authority in both modes; weekly
ATR is used only by the Macro gate.
