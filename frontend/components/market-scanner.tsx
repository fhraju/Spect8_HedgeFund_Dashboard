"use client";

import type {
  ScannerInstrument,
  ScannerSnapshot,
  ScannerTimeframe,
} from "@/lib/api-types";
import Link from "next/link";
import { useMemo, useState } from "react";

import { RefreshButton } from "./refresh-button";
import { ZonedTimestamp } from "./zoned-timestamp";

export type TimeframeFilter = "ALL" | "H1" | "H4";
export type ScannerFilters = {
  asset: string;
  kind?: string;
  exposure?: string;
  proxy?: string;
  timeframe: TimeframeFilter;
  match: string;
  confirmed: string;
  health: string;
};

function healthBadge(status: string) {
  return (
    <span className={`scanner-chip scanner-${status.toLowerCase()}`}>
      {status.replaceAll("_", " + ")}
    </span>
  );
}

const filterLabels: Record<string, string> = {
  BUY: "BUY",
  SELL: "SELL",
  BUY_AND_SELL: "BUY + SELL",
  NONE: "NO FILTER",
  WAITING: "WAITING",
};

export function FilterBadge({
  status,
  asOf,
}: {
  status: string;
  asOf?: string | null;
}) {
  const label = filterLabels[status] ?? status.replaceAll("_", " ");
  const title = asOf
    ? `Current D1 Filter · completed H1 snapshot at ${asOf}`
    : "Filter = Daily market eligibility";
  return (
    <span
      aria-label={`Daily market eligibility filter: ${label}`}
      className={`scanner-filter-badge scanner-filter-${status.toLowerCase()}`}
      title={title}
    >
      {label}
    </span>
  );
}

const signalLabels: Record<string, { icon: string; label: string }> = {
  BUY: { icon: "▲", label: "BUY SIGNAL" },
  SELL: { icon: "▼", label: "SELL SIGNAL" },
  BUY_AND_SELL: { icon: "◆", label: "BUY + SELL SIGNAL" },
  NONE: { icon: "—", label: "NO SIGNAL" },
  WAITING: { icon: "…", label: "WAITING" },
};

export function SignalBadge({ status }: { status: string }) {
  const value = signalLabels[status] ?? {
    icon: "•",
    label: status.replaceAll("_", " "),
  };
  return (
    <span
      aria-label={`Confirmed strategy signal: ${value.label}`}
      className={`scanner-signal-badge scanner-signal-${status.toLowerCase()}`}
      title="Signal = Confirmed H1/H4 setup"
    >
      <span aria-hidden="true">{value.icon}</span>
      {value.label}
    </span>
  );
}

function matchesDirection(
  row: ScannerInstrument,
  timeframe: TimeframeFilter,
  direction: string,
  field: keyof Pick<ScannerTimeframe, "filter_status" | "signal_status">,
) {
  const values = timeframe === "ALL" ? [row.H1, row.H4] : [row[timeframe]];
  return values.some((value) =>
    direction === "ALL"
      ? true
      : direction === "CONFIRMED"
        ? !["NONE", "WAITING"].includes(value[field])
      : value[field].split("_AND_").includes(direction),
  );
}

function matchesCurrentFilter(row: ScannerInstrument, direction: string) {
  return direction === "ALL"
    ? true
    : row.current_filter.status.split("_AND_").includes(direction);
}

export function filterScannerRows(
  rows: ScannerInstrument[],
  filters: ScannerFilters,
) {
  return rows.filter((row) => {
    const healthGroup = ["HEALTHY", "RECOVERED"].includes(row.data_status)
      ? "HEALTHY"
      : row.data_status === "STALE"
        ? "STALE"
        : row.data_status === "BOOTSTRAPPING"
          ? "BOOTSTRAPPING"
          : "ERROR";
    return (
      (filters.asset === "ALL" || row.asset_class === filters.asset) &&
      (!filters.kind || filters.kind === "ALL" || row.instrument_kind === filters.kind) &&
      (!filters.exposure || filters.exposure === "ALL" || row.exposure_category === filters.exposure) &&
      (!filters.proxy || filters.proxy === "ALL" || (filters.proxy === "PROXY") === Boolean(row.is_proxy)) &&
      matchesCurrentFilter(row, filters.match) &&
      matchesDirection(
        row,
        filters.timeframe,
        filters.confirmed,
        "signal_status",
      ) &&
      (filters.health === "ALL" || healthGroup === filters.health)
    );
  });
}

export function MarketScanner({ snapshot }: { snapshot: ScannerSnapshot }) {
  const [asset, setAsset] = useState("ALL");
  const [kind, setKind] = useState("ALL");
  const [exposure, setExposure] = useState("ALL");
  const [proxy, setProxy] = useState("ALL");
  const [timeframe, setTimeframe] = useState<TimeframeFilter>("ALL");
  const [match, setMatch] = useState("ALL");
  const [confirmed, setConfirmed] = useState("ALL");
  const [health, setHealth] = useState("ALL");

  const rows = useMemo(
    () => filterScannerRows(snapshot.data.instruments, {
      asset,
      kind,
      exposure,
      proxy,
      timeframe,
      match,
      confirmed,
      health,
    }),
    [asset, confirmed, exposure, health, kind, match, proxy, snapshot.data.instruments, timeframe],
  );

  const unhealthy = snapshot.data.instruments.filter(
    (row) => !["HEALTHY", "RECOVERED"].includes(row.data_status),
  ).length;
  const monitored = snapshot.data.instruments.length;
  const filteredCandidates = snapshot.data.instruments.filter(
    (row) => !["NONE", "WAITING"].includes(row.current_filter.status),
  ).length;
  const confirmedSignals = snapshot.data.instruments.filter((row) =>
    [row.H1.signal_status, row.H4.signal_status].some(
      (status) => !["NONE", "WAITING"].includes(status),
    ),
  ).length;
  const healthy = monitored - unhealthy;
  const assetClasses = Array.from(
    new Set(snapshot.data.instruments.map((row) => row.asset_class)),
  );
  const instrumentKinds = Array.from(
    new Set(snapshot.data.instruments.map((row) => row.instrument_kind).filter(Boolean)),
  );
  const exposureCategories = Array.from(
    new Set(snapshot.data.instruments.map((row) => row.exposure_category).filter(Boolean)),
  );

  return (
    <main className="app-shell scanner-shell">
      <aside className="sidebar">
        <Link className="brand-lockup" href="/" aria-label="Home — Spect8 Strategy Intelligence">
          <span className="brand-symbol">S8</span>
          <div><strong>Spect8</strong><span>Strategy Intelligence</span></div>
        </Link>
        <nav className="primary-nav" aria-label="Primary navigation">
          <span className="active"><i>⌕</i>Market Scanner</span>
        </nav>
      </aside>

      <section className="dashboard-content scanner-content">
        <header className="scanner-header">
          <div>
            <span className="section-kicker">Completed H1 bars · read only</span>
            <h1>Multi-instrument market scanner</h1>
            <p>{snapshot.data.instruments.length} enabled instruments · {unhealthy} require attention{snapshot.data.credit_budget ? ` · ${snapshot.data.credit_budget.estimated_operational_remaining} operational credits remaining` : ""}</p>
          </div>
          <RefreshButton />
        </header>

        <section className="kpi-grid scanner-kpis" aria-label="Market scanner summary">
          <article className="kpi active">
            <span className="kpi-icon blue">◎</span>
            <span><b>{monitored}</b><small>Markets Monitored</small></span>
            <em>Live enabled universe</em>
          </article>
          <article className="kpi">
            <span className="kpi-icon amber">▽</span>
            <span><b>{filteredCandidates}</b><small>Filtered Candidates</small></span>
            <em>Eligible on H1 or H4</em>
          </article>
          <article className="kpi">
            <span className="kpi-icon green">◉</span>
            <span><b>{confirmedSignals}</b><small>Confirmed Signals</small></span>
            <em>Confirmed on H1 or H4</em>
          </article>
          <article className="kpi">
            <span className="kpi-icon cyan">◇</span>
            <span><b>{healthy}/{monitored}</b><small>Healthy Feeds</small></span>
            <em>{unhealthy === 0 ? "All markets current" : `${unhealthy} require attention`}</em>
          </article>
        </section>

        <section className="scanner-filters" aria-label="Scanner filters">
          <label>Asset class<select aria-label="Asset class" value={asset} onChange={(event) => setAsset(event.target.value)}><option value="ALL">All</option>{assetClasses.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
          <label>Instrument type<select aria-label="Instrument type" value={kind} onChange={(event) => setKind(event.target.value)}><option value="ALL">All</option>{instrumentKinds.map((value) => <option key={value} value={value}>{value?.replaceAll("_", " ")}</option>)}</select></label>
          <label>Exposure<select aria-label="Exposure category" value={exposure} onChange={(event) => setExposure(event.target.value)}><option value="ALL">All</option>{exposureCategories.map((value) => <option key={value} value={value}>{value?.replaceAll("_", " ")}</option>)}</select></label>
          <label>Direct / proxy<select aria-label="Direct or proxy" value={proxy} onChange={(event) => setProxy(event.target.value)}><option value="ALL">All</option><option value="DIRECT">Direct</option><option value="PROXY">ETF Proxy</option></select></label>
          <label>Timeframe<select aria-label="Timeframe" value={timeframe} onChange={(event) => setTimeframe(event.target.value as TimeframeFilter)}><option value="ALL">H1 + H4</option><option value="H1">H1</option><option value="H4">H4</option></select></label>
          <label>Filter match<select aria-label="BUY or SELL matches" value={match} onChange={(event) => setMatch(event.target.value)}><option value="ALL">Any</option><option value="BUY">BUY</option><option value="SELL">SELL</option></select></label>
          <label>Confirmed signal<select aria-label="Confirmed signals" value={confirmed} onChange={(event) => setConfirmed(event.target.value)}><option value="ALL">Any</option><option value="CONFIRMED">Confirmed only</option><option value="BUY">BUY</option><option value="SELL">SELL</option></select></label>
          <label>Data health<select aria-label="Data health" value={health} onChange={(event) => setHealth(event.target.value)}><option value="ALL">All</option><option value="HEALTHY">Healthy</option><option value="STALE">Stale</option><option value="ERROR">Error</option><option value="BOOTSTRAPPING">Bootstrapping</option></select></label>
        </section>

        <div className="scanner-table-wrap panel">
          <table className="scanner-table">
            <thead><tr><th>Instrument</th><th>Exposure</th><th>Type</th><th>Current D1 Filter</th>{timeframe !== "H4" && <th>H1 Signal</th>}{timeframe !== "H1" && <th>H4 Signal</th>}<th>Latest completed bar</th><th>Provider / Data status</th></tr></thead>
            <tbody>
              {rows.map((row) => {
                const latest = timeframe === "H4"
                  ? row.latest_completed_h4_timestamp
                  : row.latest_completed_h1_timestamp;
                return (
                  <tr key={row.instrument_id}>
                    <td><Link href={`/instruments/${row.instrument_id}`}><strong>{row.display_symbol}</strong>{row.is_proxy && <span className="scanner-proxy-badge" title="Signals use this ETF price series, not the direct underlying market.">ETF PROXY</span>}<small>{row.display_name}</small></Link></td>
                    <td><span>{(row.exposure_category ?? row.asset_class).replaceAll("_", " ")}</span>{row.underlying_description && <small title={row.underlying_description}>{row.underlying_description}</small>}</td>
                    <td>{(row.instrument_kind ?? row.asset_class).replaceAll("_", " ")}</td>
                    <td className="scanner-current-filter"><FilterBadge status={row.current_filter.status} asOf={row.current_filter.as_of_h1_close_time} />{row.current_filter.as_of_h1_close_time && <div className="scanner-filter-time">Completed H1 · <ZonedTimestamp value={row.current_filter.as_of_h1_close_time} /></div>}</td>
                    {timeframe !== "H4" && <td><SignalBadge status={row.H1.signal_status} /></td>}
                    {timeframe !== "H1" && <td><SignalBadge status={row.H4.signal_status} /></td>}
                    <td>{latest ? <ZonedTimestamp value={latest} /> : <span>Waiting</span>}</td>
                    <td>{healthBadge(row.data_status)}<small>{row.provider ?? snapshot.source}{row.provider_exchange ? ` · ${row.provider_exchange}` : ""}{row.validation_status ? ` · ${row.validation_status.replaceAll("_", " ")}` : ""}</small>{row.latest_error_summary && <small className="scanner-error">{row.latest_error_summary}</small>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.length === 0 && <p className="scanner-empty">No instruments match these filters.</p>}
        </div>
      </section>
    </main>
  );
}
