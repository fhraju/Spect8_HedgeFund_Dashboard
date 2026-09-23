"use client";

import type {
  ScannerInstrument,
  ScannerSnapshot,
  CurrentSignals,
  ScannerTimeframe,
} from "@/lib/api-types";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { SignalClock } from "@/lib/signal-clock";
import {
  hasAttention,
  feedHealthy,
  hasCurrentEvaluation,
  isCurrentDirectionalFilter,
  resolveSignalCell,
} from "@/lib/signal-eligibility";
import { resolveDashboardTimeProfile, type DashboardTimeProfile } from "@/lib/time";

import { CollectionStatus } from "./collection-status";
import { FilterModeSelector } from "./filter-mode-selector";
import { LogoutButton } from "./logout-button";
import { RefreshButton } from "./refresh-button";
import { ZonedTimestamp } from "./zoned-timestamp";
import { SignalCard, SignalCardFallback } from "./signal-card";
import { SignalSummary } from "./signal-summary";

export type ScannerFilters = {
  asset: string;
  kind?: string;
  exposure?: string;
  proxy?: string;
  match: string;
  confirmed: string;
  health: string;
};

function healthBadge(status: string) {
  return (
    <span className={`scanner-chip scanner-${status.toLowerCase()}`}>
      {status.replaceAll("_", " ")}
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
  timeframe = "D1",
  freshness,
  staleReason,
  latestH1,
  missingIntervals,
  totalMissing,
  profile,
}: {
  status: string;
  asOf?: string | null;
  timeframe?: "D1" | "W1";
  freshness?: "CURRENT" | "STALE" | "INCOMPLETE";
  staleReason?: string | null;
  latestH1?: string | null;
  missingIntervals?: string[];
  totalMissing?: number | null;
  profile?: DashboardTimeProfile;
}) {
  const label = filterLabels[status] ?? status.replaceAll("_", " ");
  const isStale = freshness === "STALE" || freshness === "INCOMPLETE";
  const title = asOf
    ? `Current ${timeframe} Filter · completed H1 snapshot at ${asOf}${isStale ? ` · STALE (${staleReason ?? freshness}) · latest completed H1 ${latestH1 ?? "unknown"}` : ""}`
    : `Filter = ${timeframe === "D1" ? "Daily" : "Weekly"} market eligibility${isStale ? ` · ${staleReason ?? freshness}` : ""}`;
  const shownTotal = totalMissing ?? missingIntervals?.length ?? 0;
  return (
    <span>
      <span
        aria-label={`${timeframe === "D1" ? "Daily" : "Weekly"} market eligibility filter: ${label}`}
        className={`scanner-filter-badge scanner-filter-${status.toLowerCase()}`}
        title={title}
      >
        {label}
      </span>
      {isStale && (
        <span
          role="status"
          aria-label={`${timeframe} filter stale: ${staleReason ?? freshness}`}
          className="scanner-filter-badge scanner-filter-stale"
          title={title}
        >
          STALE FILTER
        </span>
      )}
      {isStale && asOf && latestH1 && shownTotal > 0 && missingIntervals && missingIntervals.length > 0 && (
        <div className="scanner-filter-time">
          Filter as of <ZonedTimestamp value={asOf} profile={profile} /> · latest H1 <ZonedTimestamp value={latestH1} profile={profile} />
          <span> · missing {shownTotal} H1 interval{shownTotal === 1 ? "" : "s"} (oldest {missingIntervals[0]})</span>
        </div>
      )}
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

function matchesCurrentFilter(row: ScannerInstrument, direction: string) {
  return direction === "ALL"
    ? true
    : isCurrentDirectionalFilter(row) &&
        row.current_filter.status.split("_AND_").includes(direction);
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

    // Check confirmed signal filter
    const confirmedMatch =
      filters.confirmed === "ALL" ||
      (filters.confirmed === "CONFIRMED" &&
        (["H1", "H4"] as const).some((tf) => {
          const tfData = row[tf] as ScannerTimeframe | undefined;
          return tfData?.signal_status && !["NONE", "WAITING"].includes(tfData.signal_status ?? "NONE");
        }));

    return (
      (filters.asset === "ALL" || row.asset_class === filters.asset) &&
      (!filters.kind || filters.kind === "ALL" || row.instrument_kind === filters.kind) &&
      (!filters.exposure || filters.exposure === "ALL" || row.exposure_category === filters.exposure) &&
      (!filters.proxy || filters.proxy === "ALL" || (filters.proxy === "PROXY") === Boolean(row.is_proxy)) &&
      matchesCurrentFilter(row, filters.match) &&
      confirmedMatch &&
      (filters.health === "ALL" || healthGroup === filters.health)
    );
  });
}

export function MarketScanner({ snapshot }: { snapshot: ScannerSnapshot }) {
  const activeFilterMode = snapshot.data.active_filter_mode ?? "MICRO";
  const filterTimeframe = snapshot.data.filter_timeframe ?? "D1";
  const [asset, setAsset] = useState("ALL");
  const [kind, setKind] = useState("ALL");
  const [exposure, setExposure] = useState("ALL");
  const [proxy, setProxy] = useState("ALL");
  const [match, setMatch] = useState("ALL");
  const [confirmed, setConfirmed] = useState("ALL");
  const [health, setHealth] = useState("ALL");
  const [liveSignals, setLiveSignals] = useState<CurrentSignals | null>(null);
  const [signalsFailed, setSignalsFailed] = useState(false);
  const [signalsLoading, setSignalsLoading] = useState(true);
  const signalClock = useRef(new SignalClock());
  const [signalNow, setSignalNow] = useState(Number.NaN);
  useEffect(() => {
    const timer = setInterval(() => setSignalNow(signalClock.current.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function fetchLive() {
      try {
        const res = await fetch("/api/signals/current", { cache: "no-store" });
        if (!res.ok) throw new Error("Signals unavailable");
        const json = await res.json();
        if (!Array.isArray(json.data?.confirmed) || !Array.isArray(json.data?.forming)) throw new Error("Invalid signals response");
        if (!cancelled) {
          // Use the proxy's clock for both cards and totals, never device UTC.
          setSignalNow(signalClock.current.receive(res.headers.get("X-Spect8-Response-Time")));
          setLiveSignals(json.data as CurrentSignals);
          setSignalsFailed(false);
          setSignalsLoading(false);
        }
      } catch {
        if (!cancelled) {
          setSignalsFailed(true);
          setSignalsLoading(false);
        }
      }
    }
    fetchLive();
    const id = setInterval(fetchLive, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const timeframes = activeFilterMode === "MICRO" ? (["M30", "H1"] as const) : (["H1", "H4"] as const);

  const rows = useMemo(
    () => filterScannerRows(snapshot.data.instruments, {
      asset,
      kind,
      exposure,
      proxy,
      match,
      confirmed,
      health,
    }),
    [asset, confirmed, exposure, health, kind, match, proxy, snapshot.data.instruments],
  );

  function getSignalCell(row: ScannerInstrument, timeframe: string) {
    return resolveSignalCell({
      data: liveSignals,
      failed: signalsFailed,
      now: signalNow,
      instrumentId: row.instrument_id,
      mode: activeFilterMode,
      timeframe,
      evaluationCurrent: hasCurrentEvaluation(row, activeFilterMode, timeframe),
    });
  }

  // Deduplicated instruments with any actual source/recovery/filter/signal
  // issue. Feed health stays independent: a stale recovery heartbeat or a
  // blocked evaluation never becomes an unhealthy feed.
  const attention = snapshot.data.instruments.filter(hasAttention).length;
  const monitored = snapshot.data.instruments.length;
  const filteredCandidates = snapshot.data.instruments.filter(
    isCurrentDirectionalFilter,
  ).length;
  const healthy = snapshot.data.instruments.filter(feedHealthy).length;
  const assetClasses = Array.from(
    new Set(snapshot.data.instruments.map((row) => row.asset_class)),
  );
  const instrumentKinds = Array.from(
    new Set(snapshot.data.instruments.map((row) => row.instrument_kind).filter(Boolean)),
  );
  const exposureCategories = Array.from(
    new Set(snapshot.data.instruments.map((row) => row.exposure_category).filter(Boolean)),
  );
  const creditBudget = snapshot.data.credit_budget;
  const creditSummary = snapshot.data.instruments.some((row) => row.collection)
    ? " · Collection and evaluation freshness shown per instrument"
    : creditBudget
    ? ` · ${creditBudget.estimated_operational_remaining} app operational credits · ${creditBudget.estimated_total_remaining} estimated provider-day credits remaining`
    : "";

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
        <div className="sidebar-bottom">
          <LogoutButton />
        </div>
      </aside>
      <LogoutButton className="mobile-logout" />

      <section className="dashboard-content scanner-content">
        <header className="scanner-header">
          <div>
            <span className="section-kicker">Completed H1 bars · read only</span>
            <h1>Multi-instrument market scanner</h1>
            <p>{snapshot.data.instruments.length} enabled instruments · {attention} require attention{creditSummary}</p>
          </div>
          <div className="scanner-header-actions">
            <FilterModeSelector activeMode={activeFilterMode} />
            <RefreshButton />
          </div>
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
            <em>Current directional filters</em>
          </article>
          <SignalSummary data={liveSignals} mode={activeFilterMode} instrumentIds={snapshot.data.instruments.map(row => row.instrument_id)} rows={snapshot.data.instruments} failed={signalsFailed} now={signalNow} />
          <article className="kpi">
            <span className="kpi-icon cyan">◇</span>
            <span><b>{healthy}/{monitored}</b><small>Healthy Feeds</small></span>
            <em>{attention === 0 ? "All markets current" : `${attention} require attention`}</em>
          </article>
        </section>

        <section className="scanner-filters" aria-label="Scanner filters">
          <label>Asset class<select aria-label="Asset class" value={asset} onChange={(event) => setAsset(event.target.value)}><option value="ALL">All</option>{assetClasses.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
          <label>Instrument type<select aria-label="Instrument type" value={kind} onChange={(event) => setKind(event.target.value)}><option value="ALL">All</option>{instrumentKinds.map((value) => <option key={value} value={value}>{value?.replaceAll("_", " ")}</option>)}</select></label>
          <label>Exposure<select aria-label="Exposure category" value={exposure} onChange={(event) => setExposure(event.target.value)}><option value="ALL">All</option>{exposureCategories.map((value) => <option key={value} value={value}>{value?.replaceAll("_", " ")}</option>)}</select></label>
          <label>Direct / proxy<select aria-label="Direct or proxy" value={proxy} onChange={(event) => setProxy(event.target.value)}><option value="ALL">All</option><option value="DIRECT">Direct</option><option value="PROXY">ETF Proxy</option></select></label>
          <label>Filter match<select aria-label="BUY or SELL matches" value={match} onChange={(event) => setMatch(event.target.value)}><option value="ALL">Any</option><option value="BUY">BUY</option><option value="SELL">SELL</option></select></label>
          <label>Confirmed signal<select aria-label="Confirmed signals" value={confirmed} onChange={(event) => setConfirmed(event.target.value)}><option value="ALL">Any</option><option value="CONFIRMED">Confirmed only</option><option value="BUY">BUY</option><option value="SELL">SELL</option></select></label>
          <label>Data health<select aria-label="Data health" value={health} onChange={(event) => setHealth(event.target.value)}><option value="ALL">All</option><option value="HEALTHY">Healthy</option><option value="STALE">Stale</option><option value="ERROR">Error</option><option value="BOOTSTRAPPING">Bootstrapping</option></select></label>
        </section>

        <div className="scanner-table-wrap panel">
          <table className="scanner-table">
            <thead><tr><th>Instrument</th><th>Exposure</th><th>Type</th><th>Current {filterTimeframe} Filter</th>{timeframes.map((tf) => <th key={tf}>{tf} Signal</th>)}<th>Latest completed bar</th><th>Provider / Data status</th></tr></thead>
            <tbody>
              {rows.map((row) => {
                const latest = row.latest_completed_h1_timestamp;
                const profile = resolveDashboardTimeProfile(row);
                return (
                  <tr key={row.instrument_id}>
                    <td data-label="Instrument">
                      {row.polling_enabled === false && row.asset_class !== "SHARE_SPREADBET" ? (
                        <span className="scanner-unavailable-instrument" title="Provider data unavailable; this instrument is not polled.">
                          <strong>{row.display_symbol}</strong>
                          <small>{row.display_name}</small>
                        </span>
                      ) : (
                        <Link href={`/instruments/${row.instrument_id}`}><strong>{row.display_symbol}</strong>{row.is_proxy && <span className="scanner-proxy-badge" title="Signals use this ETF price series, not the direct underlying market.">ETF PROXY</span>}<small>{row.display_name}</small></Link>
                      )}
                    </td>
                    <td data-label="Exposure"><span>{(row.exposure_category ?? row.asset_class).replaceAll("_", " ")}</span>{row.underlying_description && <small title={row.underlying_description}>{row.underlying_description}</small>}</td>
                    <td data-label="Type">{(row.instrument_kind ?? row.asset_class).replaceAll("_", " ")}</td>
                    <td className="scanner-current-filter" data-label={`Current ${filterTimeframe} Filter`}><FilterBadge status={row.current_filter.status} asOf={row.current_filter.as_of_h1_close_time} timeframe={filterTimeframe} freshness={row.current_filter.filter_freshness} staleReason={row.current_filter.stale_reason} latestH1={row.current_filter.latest_completed_h1} missingIntervals={row.current_filter.missing_h1_intervals} totalMissing={row.current_filter.total_missing_h1_count} profile={profile} />{row.current_filter.as_of_h1_close_time && <div className="scanner-filter-time">Completed H1 · <ZonedTimestamp value={row.current_filter.as_of_h1_close_time} profile={profile} /></div>}</td>
                    {timeframes.map((tf) => {
                      const cell = getSignalCell(row, tf);
                      if (cell.kind === "live") {
                        return (
                          <td key={tf} data-label={`${tf} Signal`}>
                            <SignalCard
                              instrument={row.instrument_id}
                              mode={activeFilterMode}
                              timeframe={tf}
                              signal={cell.signal}
                              isForming={cell.state === "FORMING"}
                              showSource={row.asset_class === "SHARE_SPREADBET"}
                              profile={profile}
                            />
                          </td>
                        );
                      }
                      // Initial loading, update-unavailable, unsupported and
                      // genuinely stale signal states stay distinguishable.
                      if (row.asset_class === "SHARE_SPREADBET") {
                        const label =
                          cell.kind === "loading"
                            ? "LOADING"
                            : cell.kind === "unavailable"
                              ? "UPDATES UNAVAILABLE"
                              : cell.kind === "unsupported"
                                ? "UNSUPPORTED"
                                : cell.kind === "expired"
                                  ? "EXPIRED"
                                  : cell.kind === "stale"
                                    ? "STALE"
                                    : cell.kind === "waiting"
                                      ? "WAITING FOR DATA"
                                      : "NO SIGNAL";
                        const reason = "reason" in cell ? cell.reason : null;
                        return (
                          <td key={tf} data-label={`${tf} Signal`}>
                            {cell.kind === "none" && <SignalCardFallback status="NONE" />}
                            <small title={reason ?? undefined}>{label}</small>
                            {reason && <small>{reason}</small>}
                          </td>
                        );
                      }
                      // Fallback to legacy H1/H4 status for non-live timeframes (should not happen for M30)
                      const legacy = tf === "M30" ? "NONE" : row[tf as "H1" | "H4"]?.signal_status ?? "NONE";
                      return <td key={tf} data-label={`${tf} Signal`}><SignalCardFallback status={legacy} /></td>;
                    })}
                    <td data-label="Latest completed bar">{latest ? <ZonedTimestamp value={latest} profile={profile} /> : <span>Waiting</span>}</td>
                    <td data-label="Provider / Data status">{healthBadge(row.data_status)}<CollectionStatus value={row.collection} freshness={row.evaluation_freshness} /><small>{row.provider ?? snapshot.source}{row.provider_environment ? ` · ${row.provider_environment.replaceAll("_", " ")}` : ""}{row.provider_exchange ? ` · ${row.provider_exchange}` : ""}{row.validation_status ? ` · ${row.validation_status.replaceAll("_", " ")}` : ""}</small>{row.provider_environment && <span className="scanner-source-badge" title={`Market data source: ${row.provider_environment}`}>[{row.provider_environment.replaceAll("_", " ")}]</span>}{row.latest_error_summary && <small className="scanner-error">{row.latest_error_summary}</small>}</td>
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
