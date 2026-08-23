# Signal Lifecycle V1 — FORMING / CONFIRMED / Expired View

## Scope

Backend only. No dashboard UI or today's-history screen in this task.

## Timeframes

* **Micro**: `M30` + `H1`
* **Macro**: `H1` + `H4` (Spect8 broker 00/04/08/12/16/20, DST-aware)

`H1` is evaluated in both modes with different filter contexts (`MICRO` daily
filter `CURRENT_D1_FILTER_V2`, `MACRO` weekly filter `CURRENT_W1_FILTER_V1`).
`M30` uses the same `Spect8StrategyEvaluator` (now accepts `M30`) with 30×M30
history; `H4` uses broker-aligned H4 derived from H1.

## States

```
NO_SIGNAL → FORMING → CONFIRMED → EXPIRED_FROM_CURRENT_VIEW
          → NO_SIGNAL (if forming condition disappears before close)
```

`EXPIRED` is derived: `now >= visible_until`. The confirmed row remains
persisted for later history; only the current view filters by
`visible_until > now`.

## FORMING

* Source: `CurrentBarSnapshot(is_complete=False)` from `partial_snapshot`.
  * `M30` = 1..5×M5 (`partial_m30_from_m5`), `open=first.open` etc.
  * `H1` = `[completed M30] + [forming M30]`,
  * `H4` broker = `completed H1 (market_h1_bars)` + `forming H1` inside the
    current 4-hour broker bucket (`broker_partial_h4_from_h1_snapshots`).
* No lookahead: component `close <= as_of`, window `bar_start <= as_of < bar_end`.
* Evaluated by reusing `evaluate_spect8_signal` on the forming bar context.
  If `close > open` → `BUY`, `close < open` → `SELL` in the foundation stub
  (real filter/signal reuse is via `Spect8StrategyEvaluator` with the forming
  bar added to the 30-bar history). `None` → `NO_SIGNAL`.
* Provisional: not written to `confirmed_signals`, not counted as
  `processed_bars`, disappears if condition becomes false before close (no event,
  no idempotency).

Freshness: `FORMING` is only exposed when `PlatformAuthorityRuntime` is
`HEALTHY`. `STALE`/`UNAVAILABLE` blocks creation (`platform_healthy=False`
→ `None`).

## CONFIRMED

At authoritative close (`bar.is_complete=True`, `close_time <= as_of`):

1. Completed bar is evaluated with the frozen `Spect8StrategyEvaluator`.
2. If direction is present, a `ConfirmedSignal` is persisted:

```
signal_id = {instrument}:{mode}:{timeframe}:{bar_start}:{direction}:{strategy_version}
instrument, mode, timeframe, direction,
source_bar_start/end, confirmed_at (= bar.close_time), visible_until,
market_data_source, strategy_version, source_provider
```

`INSERT OR IGNORE` gives idempotency: same `signal_id` evaluated twice
→ one row, no duplicate.

## Hold periods (visible_until)

```
MICRO M30: confirmed_at + 30 minutes
MICRO H1 : confirmed_at + 60 minutes
MACRO H1 : confirmed_at + 60 minutes
MACRO H4 : confirmed_at + 60 minutes
```

`visible_until` is UTC, deterministic. `Macro H4` holds 1h, not 4h.

## Persistence / restart

Table `confirmed_signals` (PK `signal_id`) plus index on
`(instrument, mode, timeframe, visible_until)` in Spect8 SQLite. Also
`forming_recovery_state` persists the M5 open times that built the current
forming `M30` so a restart at e.g. `10:20` can re-aggregate `10:00,10:05,10:10,
10:15` without losing `open/high/low`.

* Confirmed: `current_confirmed(now)` → `visible_until > now`; restart before
  expiry still shows it; after expiry it is filtered from the current view but
  remains in `all_confirmed()` for history. No `sleep()` or frontend timer.
* Forming: `persist_forming_recovery` / `load_forming_recovery` round-trips
  deterministically; if recovery is incomplete, `FORMING` stays unavailable
  until the bounded provider catch-up refills the missing M5s.

## Freshness / authority

* `STALE`/`UNAVAILABLE` Platform data never creates a healthy `FORMING`.
  Existing `CONFIRMED` rows (with `visible_until` in the future) remain visible
  until their own expiry — market-data unavailability is distinct from signal
  expiry.
* No automatic Twelve Data fallback when `MARKET_DATA_SOURCE=MARKET_DATA_PLATFORM`.

## API

* `GET /signals/current` (protected) → `{ confirmed: current_confirmed(now), forming: [], as_of }`
* `GET /signals/confirmed` → `{ current, all, as_of }`
* `SignalLifecycleService.current_confirmed(now)` and `all_confirmed()` are the
  repository primitives for the next `Today's Signals` task.

## Provenance

Each `PartialBar`/`CurrentBarSnapshot` carries `component_source_ids`
(`M5:open`, `M30:open`) and `source_provider_id` (`IG_DEMO`/`IG_LIVE`);
`ConfirmedSignal.source_bar_start/end` preserves the authoritative bar identity.

## No strategy change

Only timeframe orchestration and lifecycle were added. `Spect8StrategyEvaluator`,
`evaluate_micro_daily_filter`, `evaluate_spect8_signal`, filter thresholds,
`SMA10/20`, `ATR`, `recent extremes` etc. are untouched.
