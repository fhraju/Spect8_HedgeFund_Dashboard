import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { Dashboard } from "@/components/dashboard";
import type {
  DashboardSnapshot,
  FilterAudit,
  InstrumentStatus,
} from "@/lib/api-types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

const root = resolve(import.meta.dirname, "..");

const watching: InstrumentStatus = {
  strategy_id: "SPECT8_MICRO_DAILY_V1_0",
  provider: "TWELVE_DATA",
  instrument_id: "EUR/USD",
  timeframe: "H1",
  source_case_id: "TWELVE_DATA:EUR/USD:H1",
  synthetic: false,
  data_status: "READY",
  dashboard_state: "WATCHING",
  filter_result: {
    buy_matched: false,
    sell_matched: false,
    daily_buy_level: 1.09,
    daily_sell_level: 1.1,
  },
  signal_result: {
    technical_buy: false,
    technical_sell: false,
    confirmed_buy: false,
    confirmed_sell: false,
  },
  levels_result: null,
  levels_results: [],
  reason_codes: ["DATA_READY", "BUY_NOT_CONFIRMED"],
  market_values: null,
  signal_bar_close_time: "2026-07-31T10:00:00Z",
  last_update: "2026-07-31T10:00:01Z",
  idempotency_key: "watching-h1",
};

const dailyReferenceSessions = [
  {
    session_identifier: "2026-08-03",
    session_open_time: "2026-08-02T21:00:00Z",
    session_close_time: "2026-08-03T21:00:00Z",
    daily_high: "1.15593",
    daily_low: "1.15018",
  },
  {
    session_identifier: "2026-08-04",
    session_open_time: "2026-08-03T21:00:00Z",
    session_close_time: "2026-08-04T21:00:00Z",
    daily_high: "1.15344",
    daily_low: "1.15030",
  },
];

function filterAudit(timeframe: "H1" | "H4"): FilterAudit {
  const h1 = timeframe === "H1";
  const signalClose = h1
    ? "2026-08-05T06:00:00Z"
    : "2026-08-05T05:00:00Z";
  const recentLow = h1 ? "1.15040" : "1.15018";
  const recentHigh = h1 ? "1.15409" : "1.15593";
  return {
    instrument_id: "EUR/USD",
    strategy_version: "SPECT8_MICRO_DAILY_V1_0_3",
    timeframe,
    evaluation_time: "2026-08-05T06:09:58.139605Z",
    evaluation_bar_open_time: h1
      ? "2026-08-05T05:00:00Z"
      : "2026-08-05T01:00:00Z",
    evaluation_bar_close_time: signalClose,
    evaluation_bar_open: h1 ? "1.15354" : "1.15317",
    evaluation_bar_high: h1 ? "1.15385" : "1.15409",
    evaluation_bar_low: h1 ? "1.15323" : "1.15262",
    evaluation_bar_close: h1 ? "1.15368" : "1.15354",
    evaluation_bar_confirmed_closed: true,
    completed_bar_count: 21,
    available_completed_bar_count: 30,
    lookback_period: 21,
    lookback_start_time: h1
      ? "2026-08-04T10:00:00Z"
      : "2026-08-01T21:00:00Z",
    lookback_end_time: signalClose,
    recent_low: recentLow,
    recent_low_bar_open_time: h1
      ? "2026-08-04T09:00:00Z"
      : "2026-08-03T17:00:00Z",
    recent_low_bar_close_time: h1
      ? "2026-08-04T10:00:00Z"
      : "2026-08-03T21:00:00Z",
    recent_high: recentHigh,
    recent_high_bar_open_time: h1
      ? "2026-08-05T02:00:00Z"
      : "2026-08-02T21:00:00Z",
    recent_high_bar_close_time: h1
      ? "2026-08-05T03:00:00Z"
      : "2026-08-03T01:00:00Z",
    daily_session: dailyReferenceSessions[1],
    daily_reference_sessions: dailyReferenceSessions,
    atr_sessions: dailyReferenceSessions,
    d1_context_eligibility_time: signalClose,
    atr_period: 5,
    atr_value: "0.0062243008",
    buffer_percentage: "0.05",
    buffer_value: "0.000311215040",
    daily_low: "1.15018",
    daily_high: "1.15593",
    buy_threshold: "1.150491215040",
    sell_threshold: "1.155618784960",
    buy_comparison: {
      recent_low: recentLow,
      operator: "<=",
      buy_threshold: "1.150491215040",
      matched: true,
    },
    sell_comparison: {
      recent_high: recentHigh,
      operator: ">=",
      sell_threshold: "1.155618784960",
      matched: !h1,
    },
    final_classification: h1 ? "BUY" : "BUY + SELL",
    source_provider: "TWELVE_DATA",
    construction_profile: "IC_MARKETS_NY_CLOSE_FOREX_V1",
    canonical_timezone: "UTC",
    display_timezone: "Broker Time",
    daily_session_authority: "17:00 America/New_York",
    selected_bars: Array.from({ length: 21 }, (_, index) => ({
      sequence: index + 1,
      open_time: "2026-08-04T09:00:00Z",
      close_time: "2026-08-04T10:00:00Z",
      open: "1.15100", high: "1.15200", low: "1.15040", close: "1.15150",
      source_id: `TWELVE_DATA:EUR/USD:${timeframe}:${index + 1}`,
      recent_low: index === 0,
      recent_high: index === 20,
      expected_market_closure_before: !h1 && index === 4,
    })),
  };
}

const persistedH1: InstrumentStatus = {
  ...watching,
  source_case_id: "twelve_data:EUR/USD:H1:2026-08-05T06:00:00Z",
  dashboard_state: "FILTERED_BUY",
  filter_result: {
    buy_matched: true,
    sell_matched: false,
    daily_buy_level: 1.15049121504,
    daily_sell_level: 1.15561878496,
  },
  market_values: {
    signal_open: 1.15354,
    signal_high: 1.15385,
    signal_low: 1.15323,
    signal_close: 1.15368,
    sma10: 1.15332,
    sma20: 1.1528125,
    atr_d1_wilder_5: 0.0062243008,
    daily_raw_low: 1.15018,
    daily_raw_high: 1.15593,
    daily_buy_level: 1.15049121504,
    daily_sell_level: 1.15561878496,
    recent_low_21: 1.1504,
    recent_high_21: 1.15409,
    daily_context_close_time: "2026-08-04T21:00:00Z",
  },
  signal_bar_close_time: "2026-08-05T06:00:00Z",
  last_update: "2026-08-05T06:09:58.139605Z",
  idempotency_key:
    "SPECT8_MICRO_DAILY_V1_0:TWELVE_DATA:EUR/USD:H1:2026-08-05T06:00:00Z",
  filter_audit: filterAudit("H1"),
};

const persistedH4: InstrumentStatus = {
  ...persistedH1,
  timeframe: "H4",
  source_case_id: "twelve_data:EUR/USD:H4:2026-08-05T05:00:00Z",
  dashboard_state: "FILTERED_BOTH",
  filter_result: {
    buy_matched: true,
    sell_matched: true,
    daily_buy_level: 1.15049121504,
    daily_sell_level: 1.15561878496,
  },
  market_values: {
    ...persistedH1.market_values!,
    signal_open: 1.15317,
    signal_high: 1.15409,
    signal_low: 1.15262,
    signal_close: 1.15354,
    sma10: 1.15175,
    sma20: 1.1524875,
    recent_low_21: 1.15018,
    recent_high_21: 1.15593,
  },
  signal_bar_close_time: "2026-08-05T05:00:00Z",
  idempotency_key:
    "SPECT8_MICRO_DAILY_V1_0:TWELVE_DATA:EUR/USD:H4:2026-08-05T05:00:00Z",
  filter_audit: filterAudit("H4"),
};

function snapshot(
  state: string,
  evaluations: InstrumentStatus[] = [watching],
): DashboardSnapshot {
  return {
    synthetic: false,
    source: "TWELVE_DATA_PROVIDER",
    notice: "Read-only Twelve Data EUR/USD market data.",
    data: {
      generated_at: "2026-07-31T10:05:00Z",
      data_state: state,
      stale: state !== "HEALTHY" && evaluations.length > 0,
      provider_health: {
        provider: "TWELVE_DATA",
        state,
        previous_state: null,
        checked_at: "2026-07-31T10:05:00Z",
        latest_completed_close: "2026-07-31T10:00:00Z",
        freshness_seconds: 300,
        detail: `${state} provider state.`,
        synthetic: false,
      },
      provider_sync: null,
      instrument: {
        instrument_id: "EUR/USD",
        provider: "TWELVE_DATA",
        provider_symbol: "EUR/USD",
        display_symbol: "EUR/USD",
        display_name: "Euro / US Dollar",
        asset_class: "FOREX",
        enabled: true,
        session_timezone: "UTC",
        timeframes: ["H1", "H4", "D1"],
        price_precision: 5,
        synthetic: false,
      },
      latest_candles: {
        H1: "2026-07-31T10:00:00Z",
        H4: "2026-07-31T09:00:00Z",
        D1: "2026-07-31T00:00:00Z",
      },
      evaluations,
      recent_events: [],
      execution: {
        enabled: false,
        orders: 0,
        fills: 0,
        detail: "Read-only scanner; execution is not implemented.",
      },
    },
  };
}

describe("dashboard data states", () => {
  it("renders extrema source open and close from distinct API fields", () => {
    const status: InstrumentStatus = {
      ...persistedH1,
      filter_audit: {
        ...persistedH1.filter_audit!,
        recent_high_bar_open_time: "2026-08-05T07:00:00Z",
        recent_high_bar_close_time: "2026-08-05T08:00:00Z",
      },
    };
    const html = renderToStaticMarkup(
      <Dashboard snapshot={snapshot("HEALTHY", [status])} />,
    );
    expect(html).toContain('aria-label="Home — Spect8 Strategy Intelligence" href="/"');
    expect(html).toContain("Recent-high source candle");
    expect(html).toContain("05 Aug 2026, 10:00 IC Markets Broker Time");
    expect(html).toContain("05 Aug 2026, 11:00 IC Markets Broker Time");
    expect(html).toContain("UTC: Open 07:00 UTC · Close 08:00 UTC");
  });

  it("renders the persisted EUR/USD H1 and H4 daily-reference filters without recalculating rounded values", () => {
    const html = renderToStaticMarkup(
      <Dashboard
        snapshot={snapshot("HEALTHY", [persistedH1, persistedH4])}
      />,
    );

    expect(persistedH1.market_values!.recent_low_21).toBeLessThan(
      persistedH1.market_values!.daily_buy_level,
    );
    expect(persistedH1.market_values!.recent_high_21).toBeLessThan(
      persistedH1.market_values!.daily_sell_level,
    );
    expect(persistedH4.market_values!.recent_low_21).toBeLessThan(
      persistedH4.market_values!.daily_buy_level,
    );
    expect(persistedH4.market_values!.recent_high_21).toBeGreaterThan(
      persistedH4.market_values!.daily_sell_level,
    );
    expect((html.match(/Daily filter/g) ?? []).length).toBeGreaterThanOrEqual(2);
    expect(html).toContain("FILTERED BUY");
    expect(html).toContain("FILTERED BOTH");
    expect(html).toContain("BUY + SELL");
    expect((html.match(/0\.00622/g) ?? []).length).toBeGreaterThanOrEqual(2);
    expect((html.match(/1\.15049/g) ?? []).length).toBeGreaterThanOrEqual(2);
    expect((html.match(/1\.15562/g) ?? []).length).toBeGreaterThanOrEqual(2);
    expect(html).toContain("05 Aug 2026, 09:00 IC Markets Broker Time");
    expect(html).toContain("02:00 EDT");
    expect(html).toContain("06:00 UTC");
    expect(html).toContain("1.15040 &lt;= 1.150491215040");
    expect(html).toContain("1.15593 &gt;= 1.155618784960");
    expect(html).toContain("NOT MATCHED");
    expect(html).toContain("MATCHED");
    expect(html).toContain("Exact 21 completed H1 bars used");
    expect(html).toContain("Exact 21 completed H4 bars used");
    expect(html).toContain("IC MARKETS NY CLOSE FOREX");
    expect(html).toContain("Expected market closure — no candles");
    expect(html).toContain("TWELVE_DATA:EUR/USD:H4:21");
    expect((html.match(/Signal candle close/g) ?? []).length).toBe(2);
    expect((html.match(/Recent-high source candle/g) ?? []).length).toBe(4);
    expect(html).toContain("UTC: Open 02:00 UTC · Close 03:00 UTC");
    expect(html).not.toContain("Audit evidence is unavailable");
  });

  it("handles a persisted evaluation without filter audit evidence safely", () => {
    const html = renderToStaticMarkup(
      <Dashboard snapshot={snapshot("HEALTHY", [watching])} />,
    );
    expect(html).toContain("Audit evidence is unavailable");
    expect(html).not.toContain("Exact filter calculations");
  });

  it("shows healthy no-signal as a valid watching state", () => {
    const html = renderToStaticMarkup(
      <Dashboard snapshot={snapshot("HEALTHY")} />,
    );
    expect(html).toContain("NO SIGNAL");
    expect(html).toContain("Both strategy timeframes are current.");
    expect(html).not.toContain("No persisted strategy evaluation yet");
  });

  it("distinguishes stale data from provider unavailability", () => {
    const stale = renderToStaticMarkup(
      <Dashboard snapshot={snapshot("STALE")} />,
    );
    const unavailable = renderToStaticMarkup(
      <Dashboard snapshot={snapshot("DATA_UNAVAILABLE")} />,
    );
    expect(stale).toContain("outside the freshness window");
    expect(unavailable).toContain("currently unavailable");
  });

  it("renders a clean startup empty state", () => {
    const html = renderToStaticMarkup(
      <Dashboard snapshot={snapshot("EMPTY", [])} />,
    );
    expect(html).toContain("No persisted strategy evaluation yet");
    expect(html).toContain("Waiting for the first completed H1 and H4");
  });

  it("keeps explicit loading and transport-error boundaries", () => {
    const loading = readFileSync(
      resolve(root, "app/dashboard/loading.tsx"),
      "utf8",
    );
    const error = readFileSync(
      resolve(root, "app/dashboard/error.tsx"),
      "utf8",
    );
    expect(loading).toContain("Loading EUR/USD scanner state");
    expect(error).toContain("Dashboard projection could not be loaded");
    expect(error).toContain("No signal was created");
  });
});
