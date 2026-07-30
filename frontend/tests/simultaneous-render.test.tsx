import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { Dashboard } from "@/components/dashboard";
import type {
  DashboardSnapshot,
  LevelsResult,
  SyntheticEnvelope,
} from "@/lib/api-types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

const source = "REPLAY_MARKET_DATA_PROVIDER" as const;
const notice =
  "SYNTHETIC REPLAY MARKET DATA — no live provider is connected.";

function envelope<T>(data: T): SyntheticEnvelope<T> {
  return { synthetic: true, source, notice, data };
}

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
    const snapshot: DashboardSnapshot = {
      health: envelope({
        status: "ok",
        mode: "PHASE_2A_PRODUCTION_ENGINE",
        market_data: "SYNTHETIC_ONLY",
        database: "sqlite",
      }),
      statuses: envelope([
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
          signal_bar_close_time: "2026-02-03T11:00:00Z",
          last_update: "2026-02-03T11:00:01Z",
          idempotency_key: "confirmed-both",
        },
      ]),
      events: envelope([]),
    };

    const html = renderToStaticMarkup(<Dashboard snapshot={snapshot} />);
    expect(html).toContain("BUY Confirmed");
    expect(html).toContain("SELL Confirmed");
    expect(html).toContain("Confirmed Both");
    expect(html).toContain("498.20");
    expect(html).toContain("505.40");
    expect(html).toContain("501.80");
    expect(html).toContain("494.60");
  });
});
