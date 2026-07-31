import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { Dashboard } from "@/components/dashboard";
import type { DashboardSnapshot, InstrumentStatus } from "@/lib/api-types";

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
        display_name: "Euro / US Dollar",
        asset_class: "FOREX",
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
