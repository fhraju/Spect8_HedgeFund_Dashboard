import type {
  DashboardSnapshot,
  DailyFilterSnapshot,
  WeeklyFilterSnapshot,
  EventRecord,
  FilterAuditDailySession,
  InstrumentStatus,
} from "@/lib/api-types";
import { formatDashboardTimestamp, formatNewYorkSessionDate } from "@/lib/time";
import Link from "next/link";
import { Fragment } from "react";

import { RefreshButton } from "./refresh-button";
import { FilterModeSelector } from "./filter-mode-selector";
import { LogoutButton } from "./logout-button";
import { ZonedTimestamp } from "./zoned-timestamp";

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

function matchLabel(matched: boolean): string {
  return matched ? "MATCHED" : "NOT MATCHED";
}

function AuditComparison({
  label,
  left,
  operator,
  right,
  matched,
}: {
  label: "BUY" | "SELL";
  left: string;
  operator: "<=" | ">=";
  right: string;
  matched: boolean;
}) {
  return (
    <div className={`audit-comparison ${matched ? "matched" : "not-matched"}`}>
      <span>{label} comparison</span>
      <code>{left} {operator} {right}</code>
      <strong>{matchLabel(matched)}</strong>
    </div>
  );
}

function SessionEvidence({ session }: { session: FilterAuditDailySession }) {
  return (
    <li>
      <b>{formatNewYorkSessionDate(session.session_identifier)}</b>
      <span>Open <ZonedTimestamp value={session.session_open_time} newYorkPrefix="New York open" /></span>
      <span>Close <ZonedTimestamp value={session.session_close_time} newYorkPrefix="New York close" /></span>
      <small>High {session.daily_high} / Low {session.daily_low}</small>
    </li>
  );
}

function SourceCandleEvidence({
  label,
  openTime,
  closeTime,
}: {
  label: string;
  openTime: string;
  closeTime: string;
}) {
  const open = formatDashboardTimestamp(openTime);
  const close = formatDashboardTimestamp(closeTime);
  return (
    <div className="source-candle-evidence" aria-label={label}>
      <span>{label}</span>
      <dl>
        <div><dt>Open:</dt><dd>{open.primary}</dd></div>
        <div><dt>Close:</dt><dd>{close.primary}</dd></div>
      </dl>
      <small>UTC: Open {open.utc} · Close {close.utc}</small>
    </div>
  );
}

function FilterAuditView({ status }: { status: InstrumentStatus }) {
  const audit = status.filter_audit;
  if (!audit) {
    if (
      status.strategy_version === "MICRO_DAILY_FILTER_CURRENT_D1_V2" ||
      status.strategy_version === "MACRO_WEEKLY_FILTER_CURRENT_W1_V1"
    ) {
      return (
        <details className="filter-audit-details">
          <summary>Signal Audit</summary>
          <div className="audit-section">
            <p>The Daily Filter is supplied by the shared instrument snapshot. Timeframe-specific values below are retained only for Signal and candidate-level evidence.</p>
            <dl>
              <div><dt>21-bar low</dt><dd>{status.market_values?.recent_low_21 ?? "â€”"}</dd></div>
              <div><dt>21-bar high</dt><dd>{status.market_values?.recent_high_21 ?? "â€”"}</dd></div>
              <div><dt>Shared snapshot</dt><dd><code>{status.daily_filter_snapshot_id ?? "Unavailable"}</code></dd></div>
            </dl>
          </div>
        </details>
      );
    }
    return (
      <div className="filter-audit-unavailable" role="status">
        <strong>Filter Audit</strong>
        <span>Audit evidence is unavailable for this persisted evaluation.</span>
      </div>
    );
  }

  return (
    <>
      <div className="filter-audit-summary" aria-label={`${status.timeframe} filter audit summary`}>
        <div><span>Lookback</span><b>{audit.lookback_period} completed bars</b></div>
        <div><span>Window start</span><b><ZonedTimestamp value={audit.lookback_start_time} /></b></div>
        <div><span>Window end</span><b><ZonedTimestamp value={audit.lookback_end_time} /></b></div>
        <div><span>Recent {audit.lookback_period}-bar low</span><b>{audit.recent_low}</b></div>
        <SourceCandleEvidence label="Recent-low source candle" openTime={audit.recent_low_bar_open_time} closeTime={audit.recent_low_bar_close_time} />
        <div><span>Recent {audit.lookback_period}-bar high</span><b>{audit.recent_high}</b></div>
        <SourceCandleEvidence label="Recent-high source candle" openTime={audit.recent_high_bar_open_time} closeTime={audit.recent_high_bar_close_time} />
        <div><span>BUY threshold</span><b>{audit.buy_threshold}</b></div>
        <div><span>SELL threshold</span><b>{audit.sell_threshold}</b></div>
        <div><span>BUY matched</span><b>{audit.buy_comparison.matched ? "YES" : "NO"}</b></div>
        <div><span>SELL matched</span><b>{audit.sell_comparison.matched ? "YES" : "NO"}</b></div>
        <AuditComparison label="BUY" left={audit.buy_comparison.recent_low} operator={audit.buy_comparison.operator} right={audit.buy_comparison.buy_threshold} matched={audit.buy_comparison.matched} />
        <AuditComparison label="SELL" left={audit.sell_comparison.recent_high} operator={audit.sell_comparison.operator} right={audit.sell_comparison.sell_threshold} matched={audit.sell_comparison.matched} />
        <div className="audit-classification"><span>Daily filter classification</span><b>{audit.final_classification}</b></div>
      </div>

      <details className="filter-audit-details">
        <summary>Filter Audit</summary>
        <div className="audit-section">
          <h3>Evaluation context</h3>
          <dl>
            <div><dt>Instrument</dt><dd>{audit.instrument_id}</dd></div>
            <div><dt>Strategy version</dt><dd>{audit.strategy_version}</dd></div>
            <div><dt>Timeframe</dt><dd>{audit.timeframe}</dd></div>
            <div><dt>Evaluated at</dt><dd><ZonedTimestamp value={audit.evaluation_time} /></dd></div>
            <div><dt>Bar open</dt><dd><ZonedTimestamp value={audit.evaluation_bar_open_time} /></dd></div>
            <div><dt>Bar close</dt><dd><ZonedTimestamp value={audit.evaluation_bar_close_time} /></dd></div>
            <div><dt>Bar OHLC</dt><dd>{audit.evaluation_bar_open} / {audit.evaluation_bar_high} / {audit.evaluation_bar_low} / {audit.evaluation_bar_close}</dd></div>
            <div><dt>Confirmed closed</dt><dd>{audit.evaluation_bar_confirmed_closed ? "Yes" : "No"}</dd></div>
            <div><dt>Filter bars used</dt><dd>{audit.completed_bar_count} completed bars</dd></div>
            <div><dt>Available history</dt><dd>{audit.available_completed_bar_count} completed bars</dd></div>
            <div><dt>Source provider</dt><dd>{audit.source_provider}</dd></div>
            <div><dt>Construction profile</dt><dd>{audit.construction_profile.replaceAll("_", " ")}</dd></div>
            <div><dt>Canonical timezone</dt><dd>{audit.canonical_timezone}</dd></div>
            <div><dt>Display timezone</dt><dd>{audit.display_timezone}</dd></div>
            <div><dt>Daily session</dt><dd>{audit.daily_session_authority}</dd></div>
          </dl>
        </div>

        <div className="audit-section">
          <h3>Exact 21 completed {audit.timeframe} bars used</h3>
          <div className="audit-table-wrap">
            <table className="audit-bars-table">
              <thead><tr><th>#</th><th>Open</th><th>Close</th><th>O</th><th>H</th><th>L</th><th>C</th><th>Source ID / extrema</th></tr></thead>
              <tbody>
                {audit.selected_bars.map((bar) => (
                  <Fragment key={bar.source_id}>
                    {bar.expected_market_closure_before && <tr className="expected-closure"><td colSpan={8}>Expected market closure — no candles</td></tr>}
                    <tr>
                      <td>{bar.sequence}</td><td><ZonedTimestamp value={bar.open_time} /></td><td><ZonedTimestamp value={bar.close_time} /></td>
                      <td>{bar.open}</td><td>{bar.high}</td><td>{bar.low}</td><td>{bar.close}</td>
                      <td><code>{bar.source_id}</code>{bar.recent_low && <b> Recent low</b>}{bar.recent_high && <b> Recent high</b>}</td>
                    </tr>
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="audit-section">
          <h3>Timeframe-specific extrema</h3>
          <dl>
            <div><dt>Lookback</dt><dd>{audit.lookback_period} completed {audit.timeframe} bars</dd></div>
            <div><dt>Window start</dt><dd><ZonedTimestamp value={audit.lookback_start_time} /></dd></div>
            <div><dt>Window end</dt><dd><ZonedTimestamp value={audit.lookback_end_time} /></dd></div>
            <div><dt>Recent low</dt><dd>{audit.recent_low}</dd></div>
            <div><dt>Low source open</dt><dd><ZonedTimestamp value={audit.recent_low_bar_open_time} /></dd></div>
            <div><dt>Low source close</dt><dd><ZonedTimestamp value={audit.recent_low_bar_close_time} /></dd></div>
            <div><dt>Recent high</dt><dd>{audit.recent_high}</dd></div>
            <div><dt>High source open</dt><dd><ZonedTimestamp value={audit.recent_high_bar_open_time} /></dd></div>
            <div><dt>High source close</dt><dd><ZonedTimestamp value={audit.recent_high_bar_close_time} /></dd></div>
          </dl>
        </div>

        <div className="audit-section">
          <h3>New York daily context</h3>
          <dl>
            <div><dt>Session date</dt><dd>{formatNewYorkSessionDate(audit.daily_session.session_identifier)}</dd></div>
            <div><dt>Session open</dt><dd><ZonedTimestamp value={audit.daily_session.session_open_time} newYorkPrefix="New York open" /></dd></div>
            <div><dt>Session close</dt><dd><ZonedTimestamp value={audit.daily_session.session_close_time} newYorkPrefix="New York close" /></dd></div>
            <div><dt>Daily low</dt><dd>{audit.daily_low}</dd></div>
            <div><dt>Daily high</dt><dd>{audit.daily_high}</dd></div>
            <div><dt>D1 eligible as of</dt><dd><ZonedTimestamp value={audit.d1_context_eligibility_time} /></dd></div>
            <div><dt>Daily volatility</dt><dd>Wilder period {audit.atr_period} = {audit.atr_value}</dd></div>
            <div><dt>Buffer</dt><dd>{audit.atr_value} x {audit.buffer_percentage} = {audit.buffer_value}</dd></div>
          </dl>
          <h4>Two daily reference sessions</h4>
          <ul className="audit-session-list">
            {audit.daily_reference_sessions.map((session) => <SessionEvidence key={`reference-${session.session_close_time}`} session={session} />)}
          </ul>
          <h4>Exact D1 input sessions used by Wilder period {audit.atr_period}</h4>
          <ul className="audit-session-list compact">
            {audit.atr_sessions.map((session) => <SessionEvidence key={`atr-${session.session_close_time}`} session={session} />)}
          </ul>
        </div>

        <div className="audit-section">
          <h3>Exact filter calculations</h3>
          <p>BUY threshold: {audit.daily_low} + {audit.atr_value} x {audit.buffer_percentage} = {audit.buy_threshold}</p>
          <AuditComparison label="BUY" left={audit.buy_comparison.recent_low} operator={audit.buy_comparison.operator} right={audit.buy_comparison.buy_threshold} matched={audit.buy_comparison.matched} />
          <p>SELL threshold: {audit.daily_high} - {audit.atr_value} x {audit.buffer_percentage} = {audit.sell_threshold}</p>
          <AuditComparison label="SELL" left={audit.sell_comparison.recent_high} operator={audit.sell_comparison.operator} right={audit.sell_comparison.sell_threshold} matched={audit.sell_comparison.matched} />
          <div className="audit-final"><span>Final classification</span><strong>{audit.final_classification}</strong></div>
        </div>
      </details>
    </>
  );
}

function SharedDailyFilter({ snapshot }: { snapshot: DailyFilterSnapshot }) {
  const partial = snapshot.current_partial_d1;
  const classification = snapshot.final_classification === "BUY_AND_SELL"
    ? "BUY + SELL"
    : snapshot.final_classification;
  return (
    <section className="panel shared-daily-filter" aria-label="Current Daily Filter">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">As of last completed H1 candle.</span>
          <h2>Current Daily Filter</h2>
        </div>
        <strong>{classification}</strong>
      </div>
      <dl className="market-values">
        <div><dt>Strategy version</dt><dd>{snapshot.strategy_version}</dd></div>
        <div><dt>Snapshot ID</dt><dd><code>{snapshot.snapshot_id}</code></dd></div>
        <div><dt>H1 as-of close</dt><dd><ZonedTimestamp value={snapshot.as_of_h1_close_time_utc} /></dd></div>
        <div><dt>New York session</dt><dd>{formatNewYorkSessionDate(partial.session_identifier)}</dd></div>
        <div><dt>Session open</dt><dd><ZonedTimestamp value={partial.session_open_utc} newYorkPrefix="New York open" /></dd></div>
        <div><dt>Scheduled session close</dt><dd><ZonedTimestamp value={partial.session_close_utc} newYorkPrefix="New York close" /></dd></div>
        <div><dt>Latest included H1 close</dt><dd><ZonedTimestamp value={partial.last_h1_close_time_utc} /></dd></div>
        <div><dt>Completed H1 bars used</dt><dd>{partial.h1_count}</dd></div>
        <div><dt>Current partial D1 high</dt><dd>{partial.high}</dd></div>
        <div><dt>Current partial D1 low</dt><dd>{partial.low}</dd></div>
        <div><dt>Previous completed D1 high</dt><dd>{snapshot.previous_d1_high}</dd></div>
        <div><dt>Previous completed D1 low</dt><dd>{snapshot.previous_d1_low}</dd></div>
        <div><dt>ATR({snapshot.atr_period})</dt><dd>{snapshot.atr_value}</dd></div>
        <div><dt>5% ATR buffer</dt><dd>{snapshot.buffer_value}</dd></div>
        <div><dt>BUY threshold</dt><dd>{snapshot.buy_threshold}</dd></div>
        <div><dt>SELL threshold</dt><dd>{snapshot.sell_threshold}</dd></div>
      </dl>
      <div className="filter-audit-summary">
        <AuditComparison label="BUY" left={snapshot.buy_left_value} operator={snapshot.buy_operator} right={snapshot.buy_right_value} matched={snapshot.buy_matched} />
        <AuditComparison label="SELL" left={snapshot.sell_left_value} operator={snapshot.sell_operator} right={snapshot.sell_right_value} matched={snapshot.sell_matched} />
      </div>
    </section>
  );
}

function SharedWeeklyFilter({ snapshot }: { snapshot: WeeklyFilterSnapshot }) {
  const partial = snapshot.current_partial_w1;
  const classification = snapshot.final_classification === "BUY_AND_SELL"
    ? "BUY + SELL"
    : snapshot.final_classification;
  return (
    <section className="panel shared-daily-filter" aria-label="Current Weekly Filter">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">As of last completed canonical H1 candle.</span>
          <h2>Current Weekly Filter</h2>
        </div>
        <strong>{classification}</strong>
      </div>
      <dl className="market-values">
        <div><dt>Filter mode</dt><dd>Macro — Weekly Filter</dd></div>
        <div><dt>Strategy version</dt><dd>{snapshot.strategy_version}</dd></div>
        <div><dt>Snapshot ID</dt><dd><code>{snapshot.snapshot_id}</code></dd></div>
        <div><dt>H1 as-of close</dt><dd><ZonedTimestamp value={snapshot.as_of_h1_close_time_utc} /></dd></div>
        <div><dt>Weekly session</dt><dd>{formatNewYorkSessionDate(partial.session_identifier)}</dd></div>
        <div><dt>Friday 17:00 session open</dt><dd><ZonedTimestamp value={partial.session_open_utc} newYorkPrefix="New York open" /></dd></div>
        <div><dt>Friday 17:00 session close</dt><dd><ZonedTimestamp value={partial.session_close_utc} newYorkPrefix="New York close" /></dd></div>
        <div><dt>Latest included H1 close</dt><dd><ZonedTimestamp value={partial.last_h1_close_time_utc} /></dd></div>
        <div><dt>Completed H1 bars used</dt><dd>{partial.h1_count}</dd></div>
        <div><dt>Current partial W1 high</dt><dd>{partial.high}</dd></div>
        <div><dt>Current partial W1 low</dt><dd>{partial.low}</dd></div>
        <div><dt>Previous completed W1 high</dt><dd>{snapshot.previous_w1_high}</dd></div>
        <div><dt>Previous completed W1 low</dt><dd>{snapshot.previous_w1_low}</dd></div>
        <div><dt>Wilder W1 ATR({snapshot.atr_period})</dt><dd>{snapshot.atr_value}</dd></div>
        <div><dt>5% W1 ATR buffer</dt><dd>{snapshot.buffer_value}</dd></div>
        <div><dt>BUY threshold</dt><dd>{snapshot.buy_threshold}</dd></div>
        <div><dt>SELL threshold</dt><dd>{snapshot.sell_threshold}</dd></div>
      </dl>
      <div className="filter-audit-summary">
        <AuditComparison label="BUY" left={snapshot.buy_left_value} operator={snapshot.buy_operator} right={snapshot.buy_right_value} matched={snapshot.buy_matched} />
        <AuditComparison label="SELL" left={snapshot.sell_left_value} operator={snapshot.sell_operator} right={snapshot.sell_right_value} matched={snapshot.sell_matched} />
      </div>
    </section>
  );
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
          <small>Filter at signal close (shared snapshot)</small>
          <strong>{filterDirection(status)}</strong>
        </div>
        <div><small>Referenced snapshot</small><strong><code>{status.daily_filter_snapshot_id ?? "Legacy evaluation"}</code></strong></div>
        <div>
          <small>Confirmed signal</small>
          <strong>{signalDirection(status)}</strong>
        </div>
        <div>
          <small>Signal candle close</small>
          <strong><ZonedTimestamp value={status.signal_bar_close_time} /></strong>
        </div>
      </div>

      <FilterAuditView status={status} />

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
            <div>
              <dt>New York Daily Session</dt>
              <dd>
                {status.filter_audit ? (
                  <>
                    <b>{formatNewYorkSessionDate(status.filter_audit.daily_session.session_identifier)}</b>
                    <ZonedTimestamp value={status.filter_audit.daily_session.session_close_time} newYorkPrefix="New York close" />
                  </>
                ) : (
                  <ZonedTimestamp value={values.daily_context_close_time} newYorkPrefix="New York close" />
                )}
              </dd>
            </div>
          </dl>
          {status.strategy_version !== "MICRO_DAILY_FILTER_CURRENT_D1_V2" && status.strategy_version !== "MACRO_WEEKLY_FILTER_CURRENT_W1_V1" && (
            <div className="level-strip"><span>Legacy BUY threshold <b>{price(values.daily_buy_level, precision)}</b></span><span>Legacy SELL threshold <b>{price(values.daily_sell_level, precision)}</b></span></div>
          )}
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
  const activeFilterMode = data.active_filter_mode ?? "MICRO";
  const filterTimeframe = data.filter_timeframe ?? "D1";
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
        <Link className="brand-lockup" href="/" aria-label="Home — Spect8 Strategy Intelligence">
          <span className="brand-symbol">S8</span>
          <div><strong>Spect8</strong><span>Strategy Intelligence</span></div>
        </Link>
        <nav className="primary-nav" aria-label="Primary navigation">
          <span className="active"><i>⌕</i>Market Scanner</span>
          <span><i>↗</i>Signals <em>{confirmed}</em></span>
          <span><i>≋</i>Event Tape</span>
          <Link className="nav-link" href="/historical-replay">
            <i>◷</i>Historical Replay
          </Link>
        </nav>
        <div className="sidebar-bottom">
          <LogoutButton />
          <div className="connection-card">
            <div><i className={isHealthy ? "" : "warning-dot"} /> {healthState}</div>
            <small>{data.instrument.provider} · EUR/USD only</small>
            <small>Read-only · execution disabled</small>
          </div>
        </div>
      </aside>
      <LogoutButton className="mobile-logout" />

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Manager workspace · Read only</p>
            <h1>{data.instrument.display_name}</h1>
          </div>
          <div className="top-actions">
            <FilterModeSelector activeMode={activeFilterMode} />
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
              <span className="kpi-icon amber">{filterTimeframe}</span>
              <span><b>{activeFilterMode}</b><small>{filterTimeframe} Filter Authority</small></span>
              <em>{activeFilterMode === "MICRO" ? "Micro — Daily Filter" : "Macro — Weekly Filter"}</em>
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
              <div><dt>Last successful sync</dt><dd><ZonedTimestamp value={data.provider_sync?.last_success_at} /></dd></div>
              <div><dt>Latest H1</dt><dd><ZonedTimestamp value={data.latest_candles.H1} /></dd></div>
              <div><dt>Latest H4</dt><dd><ZonedTimestamp value={data.latest_candles.H4} /></dd></div>
              <div><dt>Latest D1</dt><dd><ZonedTimestamp value={data.latest_candles.D1} newYorkPrefix="New York close" /></dd></div>
            </dl>
          </section>

          {activeFilterMode === "MACRO" ? (
            data.weekly_filter ? (
              <SharedWeeklyFilter snapshot={data.weekly_filter} />
            ) : (
              <section className="panel filter-audit-unavailable" role="status"><strong>Current Weekly Filter</strong><span>Unavailable until completed canonical H1 history produces a valid W1 snapshot.</span></section>
            )
          ) : data.daily_filter ? (
            <SharedDailyFilter snapshot={data.daily_filter} />
          ) : (
            <section className="panel filter-audit-unavailable" role="status"><strong>Current Daily Filter</strong><span>Unavailable until a valid completed H1 produces a V2 snapshot.</span></section>
          )}

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
                      <ZonedTimestamp value={event.occurred_at} />
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
