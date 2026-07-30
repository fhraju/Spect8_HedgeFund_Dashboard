import type {
  DashboardSnapshot,
  EventRecord,
  InstrumentStatus,
} from "@/lib/api-types";

import { ReplayButton } from "./replay-button";

function direction(
  status: InstrumentStatus,
): "BUY" | "SELL" | "BOTH" | null {
  if (
    status.signal_result.confirmed_buy &&
    status.signal_result.confirmed_sell
  ) {
    return "BOTH";
  }
  if (status.signal_result.confirmed_buy) return "BUY";
  if (status.signal_result.confirmed_sell) return "SELL";
  return null;
}

function filterLabel(status: InstrumentStatus): string {
  const { buy_matched, sell_matched } = status.filter_result;
  if (buy_matched && sell_matched) return "Both";
  if (buy_matched) return "BUY Filter";
  if (sell_matched) return "SELL Filter";
  return "Watching";
}

function number(value: number | undefined): string {
  return value === undefined
    ? "—"
    : new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 5,
      }).format(value);
}

function time(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "UTC",
    hour12: false,
  }).format(new Date(value));
}

function eventMark(event: EventRecord): string {
  if (event.event_type === "SIGNAL_CONFIRMED") {
    if (event.payload.direction === "BOTH") return "↕";
    return event.payload.direction === "SELL" ? "↓" : "↑";
  }
  if (event.event_type.includes("FILTER")) return "○";
  if (event.event_type === "LEVELS_CALCULATED") return "◇";
  return "·";
}

function eventTone(event: EventRecord): string {
  if (event.event_type === "SIGNAL_CONFIRMED") {
    if (event.payload.direction === "BOTH") return "both";
    return event.payload.direction === "SELL" ? "down" : "up";
  }
  if (event.event_type.includes("FILTER")) return "wait";
  return "none";
}

export function Dashboard({ snapshot }: { snapshot: DashboardSnapshot }) {
  const statuses = snapshot.statuses.data;
  const filtered = statuses.filter(
    (status) =>
      status.filter_result.buy_matched || status.filter_result.sell_matched,
  );
  const confirmed = statuses.filter((status) => direction(status) !== null);
  const recentEvents = [...snapshot.events.data].reverse().slice(0, 8);

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-symbol">S8</span>
          <div>
            <strong>Spect8</strong>
            <span>Strategy Intelligence</span>
          </div>
        </div>
        <nav className="primary-nav" aria-label="Primary navigation">
          <span><i>⌂</i>Overview</span>
          <span className="active"><i>⌕</i>Market Scanner</span>
          <span><i>↗</i>Signals <em>{confirmed.length}</em></span>
          <span><i>≋</i>Event Tape</span>
        </nav>
        <div className="sidebar-bottom">
          <form action="/api/auth/logout" method="post">
            <button className="logout-button" type="submit">
              <i>↪</i> Logout
            </button>
          </form>
          <div className="connection-card">
            <div><i /> Synthetic Feed Ready</div>
            <small>Golden fixtures only</small>
            <small>Read-only · no trading</small>
          </div>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Manager workspace · Read only</p>
            <h1>Market Scanner</h1>
          </div>
          <div className="top-actions">
            <div className="api-status">
              <i />
              <span>Synthetic Feed Connected</span>
              <b>PHASE 2A</b>
            </div>
            <ReplayButton />
          </div>
        </header>

        <div className="synthetic-banner" role="status">
          SYNTHETIC GOLDEN FIXTURE DATA · NO LIVE MARKET-DATA PROVIDER CONNECTED
        </div>

        <div className="content">
          <section className="kpi-grid" aria-label="Scanner summary">
            <article className="kpi active">
              <span className="kpi-icon blue">◎</span>
              <span><b>{statuses.length}</b><small>Instances Monitored</small></span>
              <em>One H1 · one H4</em>
            </article>
            <article className="kpi">
              <span className="kpi-icon amber">▽</span>
              <span><b>{filtered.length}</b><small>Filtered Candidates</small></span>
              <em>Non-consuming Filter</em>
            </article>
            <article className="kpi">
              <span className="kpi-icon green">⌾</span>
              <span><b>{confirmed.length}</b><small>Confirmed Signals</small></span>
              <em>Independent BUY / SELL</em>
            </article>
            <article className="kpi">
              <span className="kpi-icon cyan">◇</span>
              <span><b>OK</b><small>System Health</small></span>
              <em>FastAPI · SQLite</em>
            </article>
          </section>

          <section className="dashboard-grid">
            <article className="pipeline panel">
              <div className="panel-heading">
                <div>
                  <span className="section-kicker">Event projection</span>
                  <h2>Signal Pipeline</h2>
                </div>
                <i className="live-dot" title="Synthetic fixture feed" />
              </div>
              <div className="pipeline-flow">
                <div className="pipeline-stage active universe">
                  <span>MARKET</span><b>{statuses.length}</b><small>Bars closed</small>
                </div>
                <div className="connector"><i /></div>
                <div className="pipeline-stage filter">
                  <span>FILTER</span><b>{filtered.length}</b><small>Candidates matched</small>
                </div>
                <div className="connector"><i /></div>
                <div className="pipeline-stage signal">
                  <span>SIGNAL</span><b>{confirmed.length}</b><small>Setups confirmed</small>
                </div>
              </div>
              <div className="pipeline-note">
                <span>BarClosed</span><b>→</b><span>Filter</span><b>→</b><span>Signal</span>
              </div>
              <p>
                Each stage is persisted by the deterministic event dispatcher
                and projected into this protected manager view.
              </p>
            </article>

            <article className="matrix-panel panel">
              <div className="panel-heading matrix-heading">
                <div>
                  <span className="section-kicker">Synthetic opportunity set</span>
                  <h2>Opportunity Matrix</h2>
                </div>
                <div className="segmented" aria-label="Available timeframes">
                  <span className="active">All</span><span>H1</span><span>H4</span>
                </div>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Instrument</th>
                      <th>TF</th>
                      <th>Filter state</th>
                      <th>Signal</th>
                      <th>Entry</th>
                      <th>Stop</th>
                      <th>Target</th>
                      <th>Risk / Contract</th>
                    </tr>
                  </thead>
                  <tbody>
                    {statuses.flatMap((status) => {
                      const displayedLevels =
                        status.levels_results.length > 0
                          ? status.levels_results
                          : [null];
                      return displayedLevels.map((levels) => {
                        const signalDirection =
                          levels?.direction ?? direction(status);
                        const isBoth =
                          status.dashboard_state === "CONFIRMED_BOTH";
                        return (
                        <tr
                          className={isBoth ? "confirmed-both-row" : undefined}
                          key={`${status.idempotency_key}-${
                            levels?.direction ?? "NONE"
                          }`}
                        >
                          <td>
                            <strong>{status.instrument_id}</strong>
                            <span>
                              Synthetic · {time(status.last_update)} UTC
                              {isBoth ? " · Confirmed Both" : ""}
                            </span>
                          </td>
                          <td><span className="tf-tag">{status.timeframe}</span></td>
                          <td>
                            <span className="status-pill qualified">
                              {filterLabel(status)}
                            </span>
                          </td>
                          <td>
                            <span
                              className={`signal-state ${
                                signalDirection?.toLowerCase() ?? ""
                              }`}
                            >
                              <i>
                                {signalDirection === "BUY"
                                  ? "↑"
                                  : signalDirection === "SELL"
                                    ? "↓"
                                    : signalDirection === "BOTH"
                                      ? "↕"
                                    : "—"}
                              </i>
                              {signalDirection
                                ? `${
                                    levels?.direction ?? signalDirection
                                  } Confirmed${isBoth ? " · Both" : ""}`
                                : "No Signal"}
                            </span>
                          </td>
                          <td className="number">{number(levels?.entry_reference)}</td>
                          <td className="number muted">{number(levels?.display_stop)}</td>
                          <td className="number">{number(levels?.target)}</td>
                          <td>
                            <strong className="risk-value">
                              ${number(levels?.target_risk_usd)}
                            </strong>
                            <span className="contract-state">
                              {levels?.contract_status ?? "N/A"} ·{" "}
                              {levels?.contract_size ?? "N/A"}
                            </span>
                          </td>
                        </tr>
                        );
                      });
                    })}
                  </tbody>
                </table>
              </div>
              <footer className="matrix-footer">
                <div>
                  <i className="legend-dot passed" /> Confirmed
                  <i className="legend-dot filtered" /> Filtered
                  <i className="legend-dot watching" /> Watching
                </div>
                <p>Read-only observer · No live trading advice</p>
              </footer>
            </article>

            <aside className="right-rail">
              <article className="health-panel panel">
                <div className="panel-heading">
                  <div>
                    <span className="section-kicker">Connection guardrail</span>
                    <h2>System Health</h2>
                  </div>
                  <strong className="health-number">READY</strong>
                </div>
                <dl className="health-list">
                  <div><dt>Backend</dt><dd>FastAPI online</dd></div>
                  <div><dt>Persistence</dt><dd>SQLite durable</dd></div>
                  <div><dt>Data source</dt><dd>Synthetic golden</dd></div>
                  <div><dt>Execution</dt><dd>Disabled</dd></div>
                </dl>
                <p className="health-note">
                  No live market API, order execution, charting, or WebSocket
                  connection is present.
                </p>
              </article>

              <article className="activity-panel panel">
                <div className="panel-heading">
                  <div>
                    <span className="section-kicker">Latest changes</span>
                    <h2>Recent Activity</h2>
                  </div>
                  <span className="event-arrow">→</span>
                </div>
                <div className="activity-list">
                  {recentEvents.map((event) => (
                    <div key={event.id}>
                      <i className={eventTone(event)}>{eventMark(event)}</i>
                      <strong>{event.timeframe}</strong>
                      <span>{event.event_type.replaceAll("_", " ")}</span>
                      <time>{time(event.occurred_at)}</time>
                    </div>
                  ))}
                </div>
              </article>
            </aside>
          </section>
        </div>
      </section>
    </main>
  );
}
