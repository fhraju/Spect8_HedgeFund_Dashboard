import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { Dashboard } from "@/components/dashboard";
import type {
  DashboardSnapshot,
  LevelsResult,
  MarketValues,
} from "@/lib/api-types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

describe("CONFIRMED_BOTH presentation", () => {
  it("renders independent BUY and SELL calculation rows", () => {
    const buy: LevelsResult = {
      direction: "BUY",
      entry_reference: 500,
      raw_stop: 498.2,
      display_stop: 498.2,
      target: 505.4,
      target_risk_usd: 100,
      contract_size: 0.55,
      contract_status: "VALID",
    };
    const sell: LevelsResult = {
      direction: "SELL",
      entry_reference: 500,
      raw_stop: 501.8,
      display_stop: 501.8,
      target: 494.6,
      target_risk_usd: 100,
      contract_size: 0.55,
      contract_status: "VALID",
    };
    const marketValues: MarketValues = {
      signal_open: 499,
      signal_high: 501,
      signal_low: 498,
      signal_close: 500,
      sma10: 499.5,
      sma20: 499.25,
      atr_d1_wilder_5: 1.2,
      daily_raw_low: 498,
      daily_raw_high: 501,
      daily_buy_level: 499.1,
      daily_sell_level: 500.9,
      recent_low_21: 498,
      recent_high_21: 501,
      daily_context_close_time: "2026-02-03T00:00:00Z",
    };
    const snapshot: DashboardSnapshot = {
      synthetic: true,
      source: "REPLAY_MARKET_DATA_PROVIDER",
      notice: "Synthetic replay data.",
      data: {
        generated_at: "2026-02-03T11:00:01Z",
        data_state: "HEALTHY",
        stale: false,
        provider_health: {
          provider: "SYNTHETIC_UTC_V1",
          state: "HEALTHY",
          previous_state: null,
          checked_at: "2026-02-03T11:00:01Z",
          latest_completed_close: "2026-02-03T11:00:00Z",
          freshness_seconds: 1,
          detail: "Replay data ready.",
          synthetic: true,
        },
        provider_sync: {
          provider: "SYNTHETIC_UTC_V1",
          state: "HEALTHY",
          last_attempt_at: "2026-02-03T11:00:01Z",
          last_success_at: "2026-02-03T11:00:01Z",
          detail: "Replay data ready.",
        },
        instrument: {
          instrument_id: "SYNTH_XAUUSD",
          provider: "SYNTHETIC_UTC_V1",
          provider_symbol: "SYNTH_XAUUSD",
          display_symbol: "XAU/USD",
          display_name: "Synthetic Gold",
          asset_class: "FOREX",
          enabled: true,
          session_timezone: "UTC",
          timeframes: ["H1", "H4", "D1"],
          price_precision: 2,
          synthetic: true,
        },
        latest_candles: {
          H1: "2026-02-03T11:00:00Z",
          H4: "2026-02-03T08:00:00Z",
          D1: "2026-02-03T00:00:00Z",
        },
        evaluations: [
          {
            strategy_id: "SPECT8_MICRO_DAILY_V1_0",
            provider: "SYNTHETIC_UTC_V1",
            instrument_id: "SYNTH_XAUUSD",
            timeframe: "H1",
            source_case_id: "confirmed_both_h1_01",
            synthetic: true,
            data_status: "READY",
            dashboard_state: "CONFIRMED_BOTH",
            filter_result: {
              buy_matched: true,
              sell_matched: true,
              daily_buy_level: 499.1,
              daily_sell_level: 500.9,
            },
            signal_result: {
              technical_buy: true,
              technical_sell: true,
              confirmed_buy: true,
              confirmed_sell: true,
            },
            levels_result: null,
            levels_results: [buy, sell],
            reason_codes: ["DATA_READY", "CONFIRMED_BUY", "CONFIRMED_SELL"],
            market_values: marketValues,
            signal_bar_close_time: "2026-02-03T11:00:00Z",
            last_update: "2026-02-03T11:00:01Z",
            idempotency_key: "confirmed-both",
          },
        ],
        recent_events: [],
        execution: {
          enabled: false,
          orders: 0,
          fills: 0,
          detail: "Read-only scanner; execution is not implemented.",
        },
      },
    };

    const html = renderToStaticMarkup(<Dashboard snapshot={snapshot} />);
    expect(html).toContain("BUY candidate");
    expect(html).toContain("SELL candidate");
    expect(html).toContain("CONFIRMED BOTH");
    expect(html).toContain("498.20");
    expect(html).toContain("505.40");
    expect(html).toContain("501.80");
    expect(html).toContain("494.60");
  });
});
