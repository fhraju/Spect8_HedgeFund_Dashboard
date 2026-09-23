import { describe, expect, it } from "vitest";

import type { ConfirmedSignal, CurrentSignals, FormingSignal } from "@/lib/api-types";
import {
  currentConfirmation,
  currentFilterReadiness,
  feedHealthy,
  hasAttention,
  hasCurrentEvaluation,
  isCurrentDirectionalFilter,
  resolveSignalCell,
} from "@/lib/signal-eligibility";

const now = Date.parse("2026-09-10T04:40:00Z");

const confirmed = (change: Partial<ConfirmedSignal> = {}): ConfirmedSignal => ({
  signal_id: "id", instrument_id: "EUR_USD", mode: "MICRO", timeframe: "M30",
  direction: "BUY", source_bar_start: "2026-09-10T04:00:00Z", source_bar_end: "2026-09-10T04:30:00Z",
  formed_at: null, confirmed_at: "2026-09-10T04:30:00Z", visible_until: "2026-09-10T05:00:00Z",
  market_data_source: "MARKET_DATA_PLATFORM", strategy_version: "v1", source_provider: "IG_DEMO",
  created_at: "2026-09-10T04:30:00Z", ...change,
});

const forming = (change: Partial<FormingSignal> = {}): FormingSignal => ({
  instrument: "EUR_USD", instrument_id: "EUR_USD", authority: "IG_DEMO", mode: "MICRO",
  timeframe: "M30", direction: "SELL", state: "FORMING", source_bar_start: "2026-09-10T04:30:00Z",
  source_bar_end: "2026-09-10T05:00:00Z", formed_at: "2026-09-10T04:40:00Z",
  source_as_of: "2026-09-10T04:40:00Z", confirmed_at: null, visible_until: null,
  market_data_source: "MARKET_DATA_PLATFORM", ...change,
});

function data(change: Partial<CurrentSignals> = {}): CurrentSignals {
  return {
    as_of: new Date(now).toISOString(),
    confirmed: [],
    forming: [],
    forming_candidates: [],
    ...change,
  };
}

function cell(change: Partial<Parameters<typeof resolveSignalCell>[0]> = {}) {
  return resolveSignalCell({
    data: data(),
    failed: false,
    now,
    instrumentId: "EUR_USD",
    mode: "MICRO",
    timeframe: "M30",
    ...change,
  });
}

describe("signal cell states", () => {
  it("distinguishes initial loading from update-unavailable", () => {
    expect(cell({ data: null })).toEqual({ kind: "loading" });
    expect(cell({ data: null, failed: true })).toEqual({ kind: "unavailable" });
    expect(
      cell({ data: data({ as_of: "2026-09-10T04:39:00Z" }) }),
    ).toEqual({ kind: "unavailable" });
  });

  it("resolves a successful poll to no-signal without inventing staleness", () => {
    expect(cell()).toEqual({ kind: "none" });
  });

  it("reports an unsupported candidate explicitly", () => {
    const result = cell({
      data: data({
        forming_candidates: [
          {
            authority: "IG_DEMO", instrument_id: "EUR_USD", mode: "MICRO",
            timeframe: "M30", state: "UNSUPPORTED", supported: false,
            source_as_of: null, source_bar_end: null, reason: "M30 unsupported",
          },
        ],
      }),
    });
    expect(result).toEqual({ kind: "unsupported", reason: "M30 unsupported" });
  });

  it("distinguishes an expired signal from a genuinely stale signal", () => {
    const expired = cell({
      data: data({ confirmed: [confirmed({ visible_until: "2026-09-10T04:35:00Z" })] }),
    });
    expect(expired).toEqual({ kind: "expired" });
    const stale = cell({
      data: data({ confirmed: [confirmed()] }),
      evaluationCurrent: false,
    });
    expect(stale.kind).toBe("stale");
  });

  it("keeps a live confirmed signal when its evaluation is current", () => {
    const result = cell({ data: data({ confirmed: [confirmed()] }) });
    expect(result.kind).toBe("live");
    if (result.kind === "live") {
      expect(result.state).toBe("CONFIRMED");
    }
  });

  it("exposes forming signals and stale candidate reasons", () => {
    const live = cell({
      data: data({ forming: [forming()] }),
    });
    expect(live.kind).toBe("live");
    if (live.kind === "live") {
      expect(live.state).toBe("FORMING");
    }
    const stale = cell({
      data: data({
        forming_candidates: [
          {
            authority: "IG_DEMO", instrument_id: "EUR_USD", mode: "MICRO",
            timeframe: "M30", state: "STALE", source_as_of: null,
            source_bar_end: null, reason: "Partial expired",
          },
        ],
      }),
    });
    expect(stale).toEqual({ kind: "stale", reason: "Partial expired" });
  });
});

describe("shared confirmation validity", () => {
  it("rejects invalid, future, expired and non-directional confirmations", () => {
    expect(currentConfirmation(confirmed(), now)).toBe(true);
    expect(currentConfirmation(confirmed({ confirmed_at: "invalid" }), now)).toBe(false);
    expect(
      currentConfirmation(confirmed({ confirmed_at: "2026-09-10T04:45:00Z" }), now),
    ).toBe(false);
    expect(
      currentConfirmation(
        confirmed({ visible_until: "2026-09-10T04:35:00Z" }),
        now,
      ),
    ).toBe(false);
    expect(currentConfirmation(confirmed({ source_bar_start: "invalid" }), now)).toBe(
      false,
    );
    expect(currentConfirmation(confirmed({ direction: "NONE" }), now)).toBe(false);
  });
});

describe("shared filter and health predicates", () => {
  const row = (change: Record<string, unknown> = {}) => ({
    instrument_id: "EUR_USD",
    data_status: "HEALTHY",
    stale: false,
    current_filter: { status: "BUY", filter_freshness: "CURRENT" },
    ...change,
  });

  it("counts directional filters only when readiness is CURRENT", () => {
    expect(isCurrentDirectionalFilter(row() as never)).toBe(true);
    expect(
      isCurrentDirectionalFilter(
        row({ current_filter: { status: "BUY", filter_freshness: "STALE" } }) as never,
      ),
    ).toBe(false);
    expect(
      isCurrentDirectionalFilter(
        row({ current_filter: { status: "NONE", filter_freshness: "CURRENT" } }) as never,
      ),
    ).toBe(false);
    expect(
      currentFilterReadiness(
        row({ filter_readiness: "CURRENT", current_filter: { status: "BUY" } }) as never,
      ),
    ).toBe("CURRENT");
  });

  it("derives feed health independently from attention reasons", () => {
    const blocked = row({
      data_status: "STALE",
      feed_health: "HEALTHY",
      attention_reasons: ["SIGNAL_MISSING_SIGNAL_CANDLE"],
    });
    expect(feedHealthy(blocked as never)).toBe(true);
    expect(hasAttention(blocked as never)).toBe(true);
    expect(
      hasAttention(row({ data_status: "STALE", feed_health: "STALE" }) as never),
    ).toBe(true);
    expect(
      hasAttention(row({ data_status: "HEALTHY", feed_health: "HEALTHY" }) as never),
    ).toBe(false);
  });

  it("requires a current per-timeframe evaluation for confirmed signals", () => {
    const withCandidates = row({
      evaluation_candidates: [
        { mode: "MICRO", timeframe: "M30", state: "STALE" },
        { mode: "MICRO", timeframe: "H1", state: "CURRENT" },
      ],
    });
    expect(hasCurrentEvaluation(withCandidates as never, "MICRO", "M30")).toBe(false);
    expect(hasCurrentEvaluation(withCandidates as never, "MICRO", "H1")).toBe(true);
    expect(hasCurrentEvaluation(row() as never, "MICRO", "M30")).toBe(true);
  });
});
