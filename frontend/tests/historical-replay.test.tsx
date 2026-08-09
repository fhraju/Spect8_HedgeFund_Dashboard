import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { HistoricalReplayDashboard } from "@/components/historical-replay-dashboard";
import type {
  HistoricalReplayEvaluationDetail,
  HistoricalReplayEvaluationPage,
  HistoricalReplayRun,
  HistoricalReplaySummary,
} from "@/lib/api-types";

const root = resolve(import.meta.dirname, "..");

function run(status: HistoricalReplayRun["status"]): HistoricalReplayRun {
  return {
    run_id: `run-${status.toLowerCase()}`,
    dataset_fingerprint: status === "PENDING" ? null : "a".repeat(64),
    requested_dataset_fingerprint: null,
    provider: "TWELVE_DATA",
    instrument: "EUR/USD",
    display_start: "2026-07-01T00:00:00Z",
    display_end: "2026-08-01T00:00:00Z",
    timeframes: ["H1", "H4"],
    context_timeframe: "D1",
    strategy_version: "SPECT8_MICRO_DAILY_V1_0_2",
    status,
    progress: { total: 930, completed: status === "RUNNING" ? 320 : 930, percent: status === "RUNNING" ? 34.41 : 100 },
    duplicate_evaluations: 0,
    quarantined_windows: status === "PARTIAL" ? 1 : 0,
    determinism_digest: status === "COMPLETED" ? "b".repeat(64) : null,
    error: status === "FAILED" ? { code: "SOURCE_UNAVAILABLE", detail: "Sanitized provider failure." } : null,
    orders: 0,
    fills: 0,
    created_at: "2026-08-01T01:00:00Z",
    started_at: "2026-08-01T01:00:01Z",
    completed_at: status === "RUNNING" ? null : "2026-08-01T01:01:00Z",
  };
}

const marketValues = {
  signal_open: 1.1,
  signal_high: 1.101,
  signal_low: 1.099,
  signal_close: 1.1005,
  sma10: 1.1,
  sma20: 1.099,
  atr_d1_wilder_5: 0.008,
  daily_raw_low: 1.08,
  daily_raw_high: 1.12,
  daily_buy_level: 1.09,
  daily_sell_level: 1.11,
  recent_low_21: 1.085,
  recent_high_21: 1.115,
  daily_context_close_time: "2026-07-14T00:00:00Z",
};

const evaluation = {
  id: 12,
  ordinal: 12,
  signal_close_utc: "2026-07-15T05:00:00Z",
  replay_as_of_utc: "2026-07-15T05:00:00.000001Z",
  timeframe: "H4" as const,
  filter_outcome: "PASS" as const,
  signal_outcome: "SIGNAL" as const,
  dashboard_state: "CONFIRMED_BUY",
  d1_context_close_utc: "2026-07-14T00:00:00Z",
  reason_codes: ["DATA_READY", "CONFIRMED_BUY"],
  market_values: marketValues,
};

function summary(status: HistoricalReplayRun["status"] = "COMPLETED"): HistoricalReplaySummary {
  return {
    run: run(status),
    dataset: {
      fingerprint: "a".repeat(64),
      warmup_start: "2026-06-21T00:00:00Z",
      requested_ranges: {},
      returned_ranges: {},
      candle_counts: {
        H1: { received: 984, accepted: 984, duplicates: 0, malformed: 0, gaps: 0, warmup: 240, display: 744 },
        H4: { received: 246, accepted: 246, duplicates: 0, malformed: 0, gaps: 0, warmup: 60, display: 186 },
        D1: { received: 41, accepted: 41, duplicates: 0, malformed: 0, gaps: 0, warmup: 10, display: 31 },
      },
    },
    evaluation_counts: { total: 930, H1: 744, H4: 186, filter_pass: 40, filter_fail: 890, signal: 2, no_signal: 928 },
    reason_counts: { DATA_READY: 930, CONFIRMED_BUY: 2 },
    event_count: 5582,
    data_quality: status === "PARTIAL" ? [{ code: "QUARANTINED_WINDOW", timeframe: "H1", start_utc: null, end_utc: null, detail: "MISSING_SIGNAL_CANDLE" }] : [],
    execution: { enabled: false, orders: 0, fills: 0, detail: "Functional replay only; execution is disabled." },
  };
}

const page: HistoricalReplayEvaluationPage = {
  items: [evaluation],
  page: 1,
  page_size: 50,
  total: 1,
  pages: 1,
};

const detail: HistoricalReplayEvaluationDetail = {
  ...evaluation,
  status: {},
  evaluation: {},
  input: { replay_as_of_utc: evaluation.replay_as_of_utc, signal_bars: [{ close: 1.1005 }], daily_bars: [{ close: 1.09 }] },
  events: [{ sequence: 1, event_type: "BAR_CLOSED", occurred_at: evaluation.replay_as_of_utc, payload: {} }],
};

describe("historical replay dashboard", () => {
  it("renders the explicit empty and not-live states", () => {
    const html = renderToStaticMarkup(<HistoricalReplayDashboard runs={[]} summary={null} evaluations={null} detail={null} filters={{}} createError={false} />);
    expect(html).toContain('aria-label="Home — Spect8 Strategy Intelligence" href="/"');
    expect(html).toContain("REPLAY — NOT LIVE");
    expect(html).toContain("No historical replay exists");
    expect(html).toContain("Orders 0 · fills 0");
    expect(html).toContain('action="/api/auth/logout"');
  });

  it("renders backend totals, filters, history and the detailed inspector", () => {
    const complete = summary();
    const html = renderToStaticMarkup(<HistoricalReplayDashboard runs={[complete.run]} summary={complete} evaluations={page} detail={detail} filters={{ run: complete.run.run_id }} createError={false} />);
    expect(html).toContain("930");
    expect(html).toContain("H1 744 · H4 186");
    expect(html).toContain("All timeframes");
    expect(html).toContain("All outcomes");
    expect(html).toContain("All filters");
    expect(html).toContain("CONFIRMED_BUY");
    expect(html).toContain("Evaluation #12 · H4");
    expect(html).toContain("BAR_CLOSED");
    expect(html).toContain("0/0");
  });

  it("renders running, partial and failed states without inventing outcomes", () => {
    for (const status of ["RUNNING", "PARTIAL", "FAILED"] as const) {
      const state = summary(status);
      const html = renderToStaticMarkup(<HistoricalReplayDashboard runs={[state.run]} summary={state} evaluations={{ ...page, items: [], total: 0 }} detail={null} filters={{}} createError={false} />);
      expect(html).toContain(status);
      if (status === "RUNNING") expect(html).toContain("being evaluated chronologically");
      if (status === "PARTIAL") expect(html).toContain("QUARANTINED_WINDOW");
      if (status === "FAILED") expect(html).toContain("Sanitized provider failure.");
    }
  });

  it("keeps strategy calculation out of the frontend and includes loading protection", () => {
    const component = readFileSync(resolve(root, "components/historical-replay-dashboard.tsx"), "utf8");
    const pageSource = readFileSync(resolve(root, "app/historical-replay/page.tsx"), "utf8");
    const loading = readFileSync(resolve(root, "app/historical-replay/loading.tsx"), "utf8");
    for (const source of [component, pageSource]) {
      expect(source).not.toContain("golden/reference");
      expect(source).not.toContain("micro_daily_filter");
      expect(source).not.toContain("spect8_signal");
      expect(source).not.toContain("activation_buffer");
      expect(source).not.toContain("structural_pivot");
    }
    expect(pageSource).toContain("requireDashboardSession");
    expect(loading).toContain("Loading historical replay");
  });
});
