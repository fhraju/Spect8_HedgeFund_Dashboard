import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  FilterBadge,
  MarketScanner,
  SignalBadge,
  filterScannerRows,
} from "../components/market-scanner";
import type { ScannerInstrument, ScannerSnapshot } from "../lib/api-types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

const symbols = [
  "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD",
  "NZD_USD", "EUR_GBP", "EUR_JPY", "GBP_JPY", "XAU_USD",
  "BTC_USD", "ETH_USD",
];

function row(instrumentId: string, index: number): ScannerInstrument {
  const display = instrumentId.replace("_", "/");
  return {
    instrument_id: instrumentId,
    display_symbol: display,
    display_name: display,
    asset_class:
      instrumentId === "XAU_USD"
        ? "METAL"
        : ["BTC_USD", "ETH_USD"].includes(instrumentId)
          ? "CRYPTO"
          : "FOREX",
    enabled: true,
    provider_symbol: display,
    provider_health: index === 1 ? "DATA_UNAVAILABLE" : index === 2 ? "STALE" : "HEALTHY",
    stale: index === 2,
    data_status: index === 1 ? "DATA_UNAVAILABLE" : index === 2 ? "STALE" : "HEALTHY",
    latest_completed_h1_timestamp: "2026-08-05T10:00:00Z",
    latest_completed_h4_timestamp: "2026-08-05T09:00:00Z",
    current_filter: {
      status: index === 0 ? "SELL" : "NONE",
      as_of_h1_close_time: "2026-08-05T10:00:00Z",
      snapshot_id: `current-filter-${instrumentId}`,
      source: "COMPLETED_H1",
    },
    H1: {
      filter_status: index === 0 ? "BUY" : "NONE",
      signal_status: index === 0 ? "BUY" : "NONE",
      evaluation_timestamp: "2026-08-05T10:00:00Z",
      latest_filter_snapshot_id: `snapshot-${instrumentId}`,
    },
    H4: {
      filter_status: index === 0 ? "SELL" : "NONE",
      signal_status: "NONE",
      evaluation_timestamp: "2026-08-05T09:00:00Z",
      latest_filter_snapshot_id: `snapshot-${instrumentId}`,
    },
    latest_error_summary: index === 1 ? "Provider rejected GBP/USD." : null,
    last_successful_provider_update: index === 1 ? null : "2026-08-05T10:01:00Z",
  };
}

const rows = symbols.map(row);
const snapshot: ScannerSnapshot = {
  synthetic: false,
  source: "TWELVE_DATA_PROVIDER",
  notice: "Read only",
  data: {
    generated_at: "2026-08-05T10:02:00Z",
    active_filter_mode: "MICRO",
    filter_timeframe: "D1",
    instruments: rows,
  },
};

describe("multi-instrument market scanner", () => {
  it("renders all instruments with independent H1/H4 and error states", () => {
    const html = renderToStaticMarkup(<MarketScanner snapshot={snapshot} />);
    for (const value of symbols) {
      expect(html).toContain(value.replace("_", "/"));
      expect(html).toContain(`/instruments/${value}`);
    }
    expect(html).toContain("Current D1 Filter");
    expect(html).toContain("Micro");
    expect(html).toContain("Daily Filter");
    expect(html).toContain('aria-label="Filter mode"');
    expect(html).toContain('action="/api/auth/logout"');
    expect(html).toContain("Logout</button>");
    expect(html).not.toContain("H1 Filter");
    expect(html).not.toContain("H4 Filter");
    expect(html).toContain("H4 Signal");
    expect(html).toContain("BUY");
    expect(html).toContain("SELL");
    expect(html).toContain("NO FILTER");
    expect(html).toContain("NO SIGNAL");
    expect(html).toContain("STALE");
    expect(html).toContain("Provider rejected GBP/USD.");
    expect(html).toContain('<b>12</b><small>Markets Monitored</small>');
    expect(html).toContain('<b>1</b><small>Filtered Candidates</small>');
    expect(html).toContain('<b>1</b><small>Confirmed Signals</small>');
    expect(html).toContain('<b>10/12</b><small>Healthy Feeds</small>');
    expect(html).toContain('aria-label="Home — Spect8 Strategy Intelligence" href="/"');
    expect(html).not.toContain(">Home</a>");
    expect(html).not.toContain("Historical Replay");
  });

  it("renders the authoritative Macro selector and W1 scanner labels", () => {
    const html = renderToStaticMarkup(
      <MarketScanner
        snapshot={{
          ...snapshot,
          data: {
            ...snapshot.data,
            active_filter_mode: "MACRO",
            filter_timeframe: "W1",
          },
        }}
      />,
    );
    expect(html).toContain("Current W1 Filter");
    expect(html).toContain("Macro");
    expect(html).toContain("Weekly Filter");
    expect(html).toContain("W1 authority active");
    expect(html).toContain('aria-pressed="true"');
  });

  it("shows provider-unavailable direct indices without an instrument link", () => {
    const unavailable: ScannerInstrument = {
      ...rows[0],
      instrument_id: "SP_500",
      display_symbol: "S&P 500",
      display_name: "S&P 500 Index",
      asset_class: "EQUITY_INDEX",
      provider_symbol: "",
      polling_enabled: false,
      validation_status: "DISCOVERY_UNAVAILABLE",
      instrument_kind: "DIRECT_MARKET",
      exposure_category: "US_LARGE_CAP_EQUITY",
      is_proxy: false,
      proxy_for: null,
      data_status: "DATA_UNAVAILABLE",
      provider_health: "DATA_UNAVAILABLE",
      latest_error_summary:
        "Unavailable from the configured provider; no market-data request is made.",
    };
    const html = renderToStaticMarkup(
      <MarketScanner
        snapshot={{
          ...snapshot,
          data: { ...snapshot.data, instruments: [unavailable] },
        }}
      />,
    );

    expect(html).toContain("S&amp;P 500 Index");
    expect(html).toContain("DATA UNAVAILABLE");
    expect(html).toContain("DISCOVERY UNAVAILABLE");
    expect(html).not.toContain("/instruments/SP_500");
  });

  it("filters by asset, timeframe direction, confirmed signal, and health", () => {
    const common = {
      asset: "ALL",
      timeframe: "ALL" as const,
      match: "ALL",
      confirmed: "ALL",
      health: "ALL",
    };
    expect(filterScannerRows(rows, { ...common, asset: "METAL" })).toHaveLength(1);
    expect(filterScannerRows(rows, { ...common, timeframe: "H1", match: "SELL" })).toEqual([rows[0]]);
    expect(filterScannerRows(rows, { ...common, timeframe: "H4", match: "SELL" })).toEqual([rows[0]]);
    expect(filterScannerRows(rows, { ...common, timeframe: "H1", match: "BUY" })).toEqual([]);
    expect(filterScannerRows(rows, { ...common, confirmed: "CONFIRMED" })).toEqual([rows[0]]);
    expect(filterScannerRows(rows, { ...common, health: "STALE" })).toEqual([rows[2]]);
    expect(filterScannerRows(rows, { ...common, health: "ERROR" })).toEqual([rows[1]]);
  });

  it("uses separate accessible filter and signal semantics", () => {
    const filter = renderToStaticMarkup(<FilterBadge status="BUY" />);
    const signal = renderToStaticMarkup(<SignalBadge status="BUY" />);
    expect(filter).toContain("scanner-filter-badge");
    expect(filter).toContain("Daily market eligibility filter: BUY");
    expect(filter).toContain("Filter = Daily market eligibility");
    expect(signal).toContain("scanner-signal-badge");
    expect(signal).toContain("Confirmed strategy signal: BUY SIGNAL");
    expect(signal).toContain("Signal = Confirmed H1/H4 setup");
    expect(signal).toContain("▲");
    expect(filter).not.toContain("scanner-signal-badge");
    expect(signal).not.toContain("scanner-filter-badge");
  });

  it("renders BUY + SELL filter, neutral states, and all asset classes", () => {
    expect(renderToStaticMarkup(<FilterBadge status="BUY_AND_SELL" />)).toContain("BUY + SELL");
    expect(renderToStaticMarkup(<FilterBadge status="NONE" />)).toContain("NO FILTER");
    expect(renderToStaticMarkup(<SignalBadge status="NONE" />)).toContain("NO SIGNAL");
    const expanded = {
      ...snapshot,
      data: {
        ...snapshot.data,
        instruments: [
          ...rows,
          { ...row("SP_500", 20), asset_class: "EQUITY_INDEX" },
          { ...row("BTC_USD", 21), asset_class: "CRYPTO" },
        ],
      },
    };
    const html = renderToStaticMarkup(<MarketScanner snapshot={expanded} />);
    expect(html).toContain("EQUITY INDEX");
    expect(html).toContain("CRYPTO");
  });

  it("renders twenty-five stable canonical row keys", () => {
    const expandedRows = Array.from({ length: 25 }, (_, index) =>
      row(`MARKET_${String(index + 1).padStart(2, "0")}`, index),
    );
    const html = renderToStaticMarkup(
      <MarketScanner
        snapshot={{
          ...snapshot,
          data: { ...snapshot.data, instruments: expandedRows },
        }}
      />,
    );
    expect((html.match(/<tr>/g) ?? [])).toHaveLength(26);
    for (const item of expandedRows) {
      expect(html).toContain(`/instruments/${item.instrument_id}`);
    }
  });

  it("labels ETF price-series proxies and filters by kind, exposure, and proxy", () => {
    const spy: ScannerInstrument = {
      ...row("SPY_US_ETF", 20),
      display_symbol: "SPY",
      display_name: "S&P 500 ETF Proxy",
      asset_class: "ETF",
      instrument_kind: "ETF",
      exposure_category: "US_LARGE_CAP_EQUITY",
      underlying_description: "US large-cap equities through the SPDR S&P 500 ETF price series.",
      is_proxy: true,
      proxy_for: "SP_500",
      provider_symbol: "SPY",
      provider_exchange: "NYSE Arca",
    };
    const html = renderToStaticMarkup(
      <MarketScanner snapshot={{ ...snapshot, data: { ...snapshot.data, instruments: [rows[0], spy] } }} />,
    );
    expect(html).toContain("ETF PROXY");
    expect(html).toContain("S&amp;P 500 ETF Proxy");
    expect(html).toContain("Signals use this ETF price series, not the direct underlying market.");
    const common = {
      asset: "ALL",
      timeframe: "ALL" as const,
      match: "ALL",
      confirmed: "ALL",
      health: "ALL",
    };
    expect(filterScannerRows([rows[0], spy], { ...common, kind: "ETF" })).toEqual([spy]);
    expect(filterScannerRows([rows[0], spy], { ...common, exposure: "US_LARGE_CAP_EQUITY" })).toEqual([spy]);
    expect(filterScannerRows([rows[0], spy], { ...common, proxy: "PROXY" })).toEqual([spy]);
    expect(filterScannerRows([rows[0], spy], { ...common, proxy: "DIRECT" })).toEqual([rows[0]]);
  });
});
