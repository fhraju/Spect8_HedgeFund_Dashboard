import type {
  DashboardSnapshot,
  EventRecord,
  InstrumentStatus,
} from "@/lib/api-types";

import { RefreshButton } from "./refresh-button";

function signalDirection(status: InstrumentStatus): string {
  if (status.dashboard_state === "CONFIRMED_BOTH") return "BUY + SELL";
  if (status.signal_result.confirmed_buy) return "BUY";
  if (status.signal_result.confirmed_sell) return "SELL";
  return "NO SIGNAL";
}

function filterDirection(status: InstrumentStatus): string {
  if (
    status.filter_result.buy_matched &&
    status.filter_result.sell_matched
  ) {
    return "BUY + SELL";
  }
  if (status.filter_result.buy_matched) return "BUY";
  if (status.filter_result.sell_matched) return "SELL";
  return "WATCHING";
}

function price(value: number | null | undefined, precision = 5): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  }).format(value);
}

function utc(value: string | null | undefined): string {
  if (!value) return "Not available";
  return `${new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    hour12: false,
  }).format(new Date(value))} UTC`;
}

function eventMark(event: EventRecord): string {
  if (event.event_type === "SIGNAL_CONFIRMED") return "↑";
  if (event.event_type.includes("FILTER")) return "○";
  if (event.event_type === "LEVELS_CALCULATED") return "◇";
  return "·";
}

function stateCopy(state: string): string {
  const messages: Record<string, string> = {
    EMPTY: "Waiting for the first completed H1 and H4 evaluations.",
    PARTIAL: "One timeframe is ready; the other remains pending.",
    STALE: "The latest completed provider candle is outside the freshness window.",
    DATA_UNAVAILABLE: "Twelve Data is currently unavailable. Persisted results remain read-only.",
    INSUFFICIENT_HISTORY: "The provider has not supplied enough completed history for evaluation.",
    QUARANTINED: "Invalid market data was quarantined before strategy evaluation.",
  };
  return messages[state] ?? "Both strategy timeframes are current.";
}

function EvaluationCard({
  status,
  precision,
}: {
  status: InstrumentStatus;
  precision: number;
}) {
  const values = status.market_values;
  return (
    <article className="evaluation-card panel">
      <div className="panel-heading evaluation-heading">
        <div>
          <span className="section-kicker">Independent evaluation</span>
          <h2>{status.timeframe} strategy</h2>
        </div>
        <span className={`state-chip ${status.dashboard_state.toLowerCase()}`}>
          {status.dashboard_state.replaceAll("_", " ")}
        </span>
      </div>

      <div className="decision-grid">
        <div>
          <small>Daily filter</small>
          <strong>{filterDirection(status)}</strong>
        </div>
        <div>
          <small>Confirmed signal</small>
          <strong>{signalDirection(status)}</strong>
        </div>
        <div>
          <small>Signal candle</small>
          <strong>{utc(status.signal_bar_close_time)}</strong>
        </div>
      </div>

      {values ? (
        <>
          <dl className="market-values">
            <div><dt>Open</dt><dd>{price(values.signal_open, precision)}</dd></div>
            <div><dt>High</dt><dd>{price(values.signal_high, precision)}</dd></div>
            <div><dt>Low</dt><dd>{price(values.signal_low, precision)}</dd></div>
            <div><dt>Close</dt><dd>{price(values.signal_close, precision)}</dd></div>
            <div><dt>MA 10</dt><dd>{price(values.sma10, precision)}</dd></div>
            <div><dt>MA 20</dt><dd>{price(values.sma20, precision)}</dd></div>
            <div><dt>Daily volatility</dt><dd>{price(values.atr_d1_wilder_5, precision)}</dd></div>
            <div><dt>D1 context</dt><dd>{utc(values.daily_context_close_time)}</dd></div>
          </dl>
          <div className="level-strip">
            <span>BUY threshold <b>{price(values.daily_buy_level, precision)}</b></span>
            <span>SELL threshold <b>{price(values.daily_sell_level, precision)}</b></span>
          </div>
        </>
      ) : (
        <p className="empty-copy">Market values are not available for this persisted evaluation.</p>
      )}

      {status.levels_results.length > 0 && (
        <div className="candidate-list">
          {status.levels_results.map((level) => (
            <div key={level.direction}>
              <strong>{level.direction} candidate</strong>
              <span>Entry {price(level.entry_reference, precision)}</span>
              <span>Stop {price(level.display_stop, precision)}</span>
              <span>Target {price(level.target, precision)}</span>
              <small>{level.contract_status} · display only</small>
            </div>
          ))}
        </div>
      )}

      <div className="reason-list" aria-label={`${status.timeframe} reason codes`}>
        {status.reason_codes.map((reason) => (
          <span key={reason}>{reason.replaceAll("_", " ")}</span>
        ))}
      </div>
    </article>
  );
}

export function Dashboard({ snapshot }: { snapshot: DashboardSnapshot }) {
  const { data } = snapshot;
  const precision = data.instrument.price_precision;
  const confirmed = data.evaluations.filter(
    (status) =>
      status.signal_result.confirmed_buy ||
      status.signal_result.confirmed_sell,
  ).length;
  const healthState = data.provider_health?.state ?? data.data_state;
  const isHealthy = ["HEALTHY", "RECOVERED"].includes(data.data_state);

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-symbol">S8</span>
          <div><strong>Spect8</strong><span>Strategy Intelligence</span></div>
        </div>
        <nav className="primary-nav" aria-label="Primary navigation">
          <span className="active"><i>⌕</i>Market Scanner</span>
          <span><i>↗</i>Signals <em>{confirmed}</em></span>
          <span><i>≋</i>Event Tape</span>
        </nav>
        <div className="sidebar-bottom">
          <form action="/api/auth/logout" method="post">
            <button className="logout-button" type="submit"><i>↪</i> Logout</button>
          </form>
          <div className="connection-card">
            <div><i className={isHealthy ? "" : "warning-dot"} /> {healthState}</div>
            <small>{data.instrument.provider} · EUR/USD only</small>
            <small>Read-only · execution disabled</small>
          </div>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Manager workspace · Read only</p>
            <h1>{data.instrument.display_name}</h1>
          </div>
          <div className="top-actions">
            <div className="api-status">
              <i className={isHealthy ? "" : "warning-dot"} />
              <span>{data.instrument.provider_symbol}</span>
              <b>PHASE 3B</b>
            </div>
            <RefreshButton />
          </div>
        </header>

        <div
          className={isHealthy ? "live-banner" : "state-banner"}
          role="status"
        >
          <strong>{data.data_state}</strong>
          <span>{stateCopy(data.data_state)}</span>
        </div>

        <div className="content">
          <section className="kpi-grid" aria-label="Market-data summary">
            <article className="kpi active">
              <span className="kpi-icon blue">◎</span>
              <span><b>{data.evaluations.length}/2</b><small>Evaluations Ready</small></span>
              <em>Independent H1 / H4</em>
            </article>
            <article className="kpi">
              <span className="kpi-icon cyan">◷</span>
              <span><b>{healthState}</b><small>Provider Health</small></span>
              <em>{data.provider_health?.detail ?? "Awaiting provider health"}</em>
            </article>
            <article className="kpi">
              <span className="kpi-icon amber">D1</span>
              <span><b>{data.latest_candles.D1 ? "READY" : "PENDING"}</b><small>Daily Context</small></span>
              <em>{utc(data.latest_candles.D1)}</em>
            </article>
            <article className="kpi">
              <span className="kpi-icon green">0</span>
              <span><b>{data.execution.orders}</b><small>Orders / Fills</small></span>
              <em>Execution disabled</em>
            </article>
          </section>

          <section className="freshness-panel panel">
            <div>
              <span className="section-kicker">Persisted provider timeline</span>
              <h2>Data freshness</h2>
            </div>
            <dl>
              <div><dt>Last successful sync</dt><dd>{utc(data.provider_sync?.last_success_at)}</dd></div>
              <div><dt>Latest H1</dt><dd>{utc(data.latest_candles.H1)}</dd></div>
              <div><dt>Latest H4</dt><dd>{utc(data.latest_candles.H4)}</dd></div>
              <div><dt>Latest D1</dt><dd>{utc(data.latest_candles.D1)}</dd></div>
            </dl>
          </section>

          {data.evaluations.length > 0 ? (
            <section className="evaluation-grid" aria-label="Independent BUY / SELL evaluations">
              {data.evaluations.map((status) => (
                <EvaluationCard
                  key={status.idempotency_key}
                  status={status}
                  precision={precision}
                />
              ))}
            </section>
          ) : (
            <section className="empty-panel panel">
              <strong>No persisted strategy evaluation yet</strong>
              <p>{stateCopy(data.data_state)}</p>
            </section>
          )}

          <section className="lower-grid">
            <article className="activity-panel panel">
              <div className="panel-heading">
                <div><span className="section-kicker">Persisted event projection</span><h2>Recent Activity</h2></div>
              </div>
              <div className="activity-list">
                {data.recent_events.length > 0 ? (
                  data.recent_events.map((event) => (
                    <div key={event.id}>
                      <i>{eventMark(event)}</i>
                      <strong>{event.timeframe}</strong>
                      <span>{event.event_type.replaceAll("_", " ")}</span>
                      <time>{utc(event.occurred_at)}</time>
                    </div>
                  ))
                ) : (
                  <p className="empty-copy">No persisted events are available.</p>
                )}
              </div>
            </article>

            <article className="health-panel panel">
              <div className="panel-heading">
                <div><span className="section-kicker">Safety boundary</span><h2>Read-only system</h2></div>
                <strong className="health-number">LOCKED</strong>
              </div>
              <dl className="health-list">
                <div><dt>Execution</dt><dd>Disabled</dd></div>
                <div><dt>Orders</dt><dd>{data.execution.orders}</dd></div>
                <div><dt>Fills</dt><dd>{data.execution.fills}</dd></div>
                <div><dt>Source</dt><dd>{snapshot.source}</dd></div>
              </dl>
              <p className="health-note">{data.execution.detail}</p>
            </article>
          </section>
        </div>
      </section>
    </main>
  );
}
