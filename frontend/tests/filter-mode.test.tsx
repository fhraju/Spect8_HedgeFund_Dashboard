import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { Dashboard } from "@/components/dashboard";
import type { DashboardSnapshot } from "@/lib/api-types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

const macroSnapshot: DashboardSnapshot = {
  synthetic: false,
  source: "TWELVE_DATA_PROVIDER",
  notice: "Read only",
  data: {
    generated_at: "2026-08-12T16:00:00Z",
    data_state: "EMPTY",
    stale: false,
    provider_health: null,
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
      H1: "2026-08-12T16:00:00Z",
      H4: "2026-08-12T13:00:00Z",
      D1: "2026-08-11T21:00:00Z",
    },
    active_filter_mode: "MACRO",
    filter_timeframe: "W1",
    evaluations: [],
    weekly_filter: {
      snapshot_id: "wfs_fixture",
      filter_mode: "MACRO",
      strategy_version: "MACRO_WEEKLY_FILTER_CURRENT_W1_V1",
      canonical_profile_version: "IC_MARKETS_NY_CLOSE_FOREX_V1",
      provider: "TWELVE_DATA",
      instrument: "EUR/USD",
      evaluation_time_utc: "2026-08-12T16:00:00Z",
      as_of_h1_close_time_utc: "2026-08-12T16:00:00Z",
      current_partial_w1: {
        session_identifier: "2026-08-14",
        session_open_utc: "2026-08-07T21:00:00Z",
        session_close_utc: "2026-08-14T21:00:00Z",
        first_h1_open_time_utc: "2026-08-09T21:00:00Z",
        last_h1_close_time_utc: "2026-08-12T16:00:00Z",
        h1_count: 67,
        source_h1_ids: ["h1"],
        source_checksum: "partial",
        open: "100",
        high: "104",
        low: "96",
        close: "101",
        quality_status: "VALID",
      },
      previous_w1_candle_id: "w1",
      previous_w1_session_id: "2026-08-07",
      previous_w1_open_utc: "2026-07-31T21:00:00Z",
      previous_w1_close_utc: "2026-08-07T21:00:00Z",
      previous_w1_open: "100",
      previous_w1_high: "108",
      previous_w1_low: "92",
      previous_w1_close: "100",
      atr_period: 5,
      atr_value: "20",
      atr_source_w1_ids: ["w1"],
      atr_source_checksum: "atr",
      buffer_percentage: "0.05",
      buffer_value: "1",
      buy_threshold: "93",
      sell_threshold: "107",
      buy_left_value: "96",
      buy_operator: "<=",
      buy_right_value: "93",
      buy_matched: false,
      sell_left_value: "104",
      sell_operator: ">=",
      sell_right_value: "107",
      sell_matched: false,
      final_classification: "NONE",
      data_quality_status: "VALID",
      ingestion_run_id: "fixture",
      created_at: "2026-08-12T16:00:00Z",
    },
    recent_events: [],
    execution: { enabled: false, orders: 0, fills: 0, detail: "Read only" },
  },
};

describe("Macro filter mode", () => {
  it("makes the active backend authority and weekly evidence obvious", () => {
    const html = renderToStaticMarkup(<Dashboard snapshot={macroSnapshot} />);
    expect(html).toContain("Macro");
    expect(html).toContain("Weekly Filter");
    expect(html).toContain("Current Weekly Filter");
    expect(html).toContain("Wilder W1 ATR(5)");
    expect(html).toContain("96 &lt;= 93");
    expect(html).toContain("104 &gt;= 107");
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain('action="/api/auth/logout"');
    expect(html).not.toContain("<h2>Current Daily Filter</h2>");
  });
});
