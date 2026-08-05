import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { Dashboard } from "@/components/dashboard";
import type { DashboardSnapshot, InstrumentStatus } from "@/lib/api-types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

const snapshotId = "dfs_fixture_shared";

function status(timeframe: "H1" | "H4"): InstrumentStatus {
  return {
    strategy_id: "MICRO_DAILY_FILTER_CURRENT_D1_V2",
    strategy_version: "MICRO_DAILY_FILTER_CURRENT_D1_V2",
    daily_filter_snapshot_id: snapshotId,
    provider: "TWELVE_DATA",
    instrument_id: "EUR/USD",
    timeframe,
    source_case_id: `v2-${timeframe}`,
    synthetic: false,
    data_status: "READY",
    dashboard_state: "FILTERED_BOTH",
    filter_result: { buy_matched: true, sell_matched: true, daily_buy_level: 1.09296, daily_sell_level: 1.10704 },
    signal_result: { technical_buy: false, technical_sell: false, confirmed_buy: false, confirmed_sell: false },
    levels_result: null,
    levels_results: [],
    reason_codes: ["DATA_READY"],
    market_values: {
      signal_open: 1.1, signal_high: 1.101, signal_low: 1.099, signal_close: 1.1,
      sma10: 1.1, sma20: 1.1, atr_d1_wilder_5: 0.0192,
      daily_raw_low: 1.092, daily_raw_high: 1.108,
      daily_buy_level: 1.09296, daily_sell_level: 1.10704,
      recent_low_21: 1.08, recent_high_21: 1.12,
      daily_context_close_time: "2026-08-04T21:00:00Z",
    },
    signal_bar_close_time: "2026-08-05T09:00:00Z",
    last_update: "2026-08-05T09:00:01Z",
    idempotency_key: `v2-${timeframe}`,
    filter_audit: null,
  };
}

const snapshot = {
  synthetic: false,
  source: "TWELVE_DATA_PROVIDER",
  notice: "Read only",
  data: {
    generated_at: "2026-08-05T10:00:01Z",
    data_state: "HEALTHY",
    stale: false,
    provider_health: null,
    provider_sync: null,
    instrument: { instrument_id: "EUR/USD", provider: "TWELVE_DATA", provider_symbol: "EUR/USD", display_name: "Euro / US Dollar", asset_class: "FOREX", session_timezone: "UTC", timeframes: ["H1", "H4"], price_precision: 5, synthetic: false },
    latest_candles: { H1: "2026-08-05T10:00:00Z", H4: "2026-08-05T09:00:00Z", D1: "2026-08-04T21:00:00Z" },
    daily_filter: {
      snapshot_id: snapshotId,
      strategy_version: "MICRO_DAILY_FILTER_CURRENT_D1_V2",
      canonical_profile_version: "IC_MARKETS_NY_CLOSE_FOREX_V1",
      provider: "TWELVE_DATA", instrument: "EUR/USD",
      evaluation_time_utc: "2026-08-05T10:00:00Z",
      as_of_h1_close_time_utc: "2026-08-05T10:00:00Z",
      current_partial_d1: { session_identifier: "2026-08-05", session_open_utc: "2026-08-04T21:00:00Z", session_close_utc: "2026-08-05T21:00:00Z", first_h1_open_time_utc: "2026-08-04T21:00:00Z", last_h1_close_time_utc: "2026-08-05T10:00:00Z", h1_count: 13, source_h1_ids: ["h1"], source_checksum: "partial", open: "1.1000", high: "1.107040", low: "1.092960", close: "1.1010", quality_status: "VALID" },
      previous_d1_candle_id: "d1", previous_d1_session_id: "2026-08-04", previous_d1_open_utc: "2026-08-03T21:00:00Z", previous_d1_close_utc: "2026-08-04T21:00:00Z", previous_d1_high: "1.1080", previous_d1_low: "1.0920", previous_d1_close: "1.1000",
      atr_period: 5, atr_value: "0.0192", atr_source_d1_ids: ["d1"], atr_source_checksum: "atr", buffer_percentage: "0.05", buffer_value: "0.000960", buy_threshold: "1.092960", sell_threshold: "1.107040",
      buy_left_value: "1.092960", buy_operator: "<=", buy_right_value: "1.092960", buy_matched: true,
      sell_left_value: "1.107040", sell_operator: ">=", sell_right_value: "1.107040", sell_matched: true,
      final_classification: "BUY_AND_SELL", data_quality_status: "VALID", ingestion_run_id: "fixture", created_at: "2026-08-05T10:00:00Z",
    },
    evaluations: [status("H1"), status("H4")],
    recent_events: [],
    execution: { enabled: false, orders: 0, fills: 0, detail: "Read only" },
  },
} as DashboardSnapshot;

describe("Current Daily Filter V2", () => {
  it("renders one shared exact backend snapshot referenced by H1 and H4", () => {
    const html = renderToStaticMarkup(<Dashboard snapshot={snapshot} />);
    expect((html.match(/<h2>Current Daily Filter<\/h2>/g) ?? [])).toHaveLength(1);
    expect(html).toContain("As of last completed H1 candle.");
    expect(html).toContain("Current partial D1 high");
    expect(html).toContain("Previous completed D1 low");
    expect(html).toContain("1.092960 &lt;= 1.092960");
    expect(html).toContain("1.107040 &gt;= 1.107040");
    expect(html).toContain("BUY + SELL");
    expect((html.match(new RegExp(snapshotId, "g")) ?? []).length).toBeGreaterThanOrEqual(3);
    expect(html).not.toContain("Recent 21-bar low");
    expect(html).toContain("Signal Audit");
  });
});
