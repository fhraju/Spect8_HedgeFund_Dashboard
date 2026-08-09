import Link from "next/link";

import type {
  HistoricalReplayEvaluationDetail,
  HistoricalReplayEvaluationPage,
  HistoricalReplayRun,
  HistoricalReplaySummary,
} from "@/lib/api-types";
import { LogoutButton } from "./logout-button";
import { ZonedTimestamp } from "./zoned-timestamp";

type ReplayFilters = {
  run?: string;
  timeframe?: string;
  outcome?: string;
  filter_outcome?: string;
  reason_code?: string;
  page?: string;
  evaluation?: string;
};

function price(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 5,
    maximumFractionDigits: 5,
  }).format(value);
}

function href(filters: ReplayFilters, changes: ReplayFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...filters, ...changes })) {
    if (value) params.set(key, value);
  }
  return `/historical-replay?${params}`;
}

export function HistoricalReplayDashboard({
  runs,
  summary,
  evaluations,
  detail,
  filters,
  createError,
}: {
  runs: HistoricalReplayRun[];
  summary: HistoricalReplaySummary | null;
  evaluations: HistoricalReplayEvaluationPage | null;
  detail: HistoricalReplayEvaluationDetail | null;
  filters: ReplayFilters;
  createError: boolean;
}) {
  const run = summary?.run ?? runs[0] ?? null;
  const counts = summary?.evaluation_counts;
  return (
    <main className="app-shell replay-shell">
      <aside className="sidebar">
        <Link className="brand-lockup" href="/" aria-label="Home — Spect8 Strategy Intelligence">
          <span className="brand-symbol">S8</span>
          <div><strong>Spect8</strong><span>Strategy Intelligence</span></div>
        </Link>
        <nav className="primary-nav" aria-label="Primary navigation">
          <Link className="nav-link" href="/dashboard"><i>⌕</i>Live Scanner</Link>
          <span className="active"><i>◷</i>Historical Replay</span>
        </nav>
        <div className="sidebar-bottom">
          <LogoutButton />
          <div className="connection-card replay-connection">
            <div><i /> REPLAY ONLY</div>
            <small>Twelve Data · EUR/USD</small>
            <small>Orders 0 · fills 0</small>
          </div>
        </div>
      </aside>
      <LogoutButton className="mobile-logout" />

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Functional validation workspace</p>
            <h1>Historical Replay</h1>
          </div>
          <form action="/api/historical-replays" method="post">
            <input type="hidden" name="display_start" value="2026-07-01T00:00:00Z" />
            <input type="hidden" name="display_end" value="2026-08-01T00:00:00Z" />
            <button className="replay-create-button" type="submit">Run July replay</button>
          </form>
        </header>

        <div className="replay-not-live" role="status">
          <strong>REPLAY — NOT LIVE</strong>
          <span>Historical scanner validation only. No trading, P&amp;L, orders or fills.</span>
        </div>

        <div className="content replay-content">
          {createError && (
            <div className="replay-alert error">Replay creation was rejected or unavailable.</div>
          )}

          <section className="replay-run-strip panel">
            <div>
              <span className="section-kicker">Selected replay run</span>
              <h2>{run ? `${run.instrument} · ${run.status}` : "No replay runs"}</h2>
            </div>
            {run && (
              <dl>
                <div><dt>Display range start</dt><dd><ZonedTimestamp value={run.display_start} /></dd></div>
                <div><dt>Display range end</dt><dd><ZonedTimestamp value={run.display_end} /></dd></div>
                <div><dt>Provider</dt><dd>{run.provider}</dd></div>
                <div><dt>Strategy</dt><dd>{run.strategy_version}</dd></div>
                <div><dt>Progress</dt><dd>{run.progress.completed}/{run.progress.total} · {run.progress.percent}%</dd></div>
              </dl>
            )}
          </section>

          {runs.length > 0 && (
            <section className="replay-run-list" aria-label="Replay runs">
              {runs.slice(0, 8).map((item) => (
                <Link
                  className={item.run_id === run?.run_id ? "selected" : ""}
                  href={href(filters, { run: item.run_id, page: "1", evaluation: "" })}
                  key={item.run_id}
                >
                  <b>{item.status}</b>
                  <ZonedTimestamp value={item.created_at} />
                  <small>{item.run_id.slice(0, 10)}</small>
                </Link>
              ))}
            </section>
          )}

          {!run ? (
            <section className="empty-panel panel replay-empty">
              <strong>No historical replay exists</strong>
              <p>Start the bounded July 2026 replay to populate this read-only workspace.</p>
            </section>
          ) : (
            <>
              {run.error && (
                <div className="replay-alert error"><b>{run.error.code}</b> {run.error.detail}</div>
              )}
              {run.status === "RUNNING" || run.status === "PENDING" ? (
                <section className="panel replay-progress-state">
                  <strong>{run.status}</strong>
                  <div><span style={{ width: `${run.progress.percent}%` }} /></div>
                  <p>The immutable dataset is loading or being evaluated chronologically.</p>
                </section>
              ) : null}

              <section className="kpi-grid replay-kpis" aria-label="Replay summary">
                <article className="kpi active"><span className="kpi-icon blue">Σ</span><span><b>{counts?.total ?? 0}</b><small>Evaluations</small></span><em>H1 {counts?.H1 ?? 0} · H4 {counts?.H4 ?? 0}</em></article>
                <article className="kpi"><span className="kpi-icon cyan">F</span><span><b>{counts?.filter_pass ?? 0}</b><small>Filter pass</small></span><em>Fail {counts?.filter_fail ?? 0}</em></article>
                <article className="kpi"><span className="kpi-icon amber">S</span><span><b>{counts?.signal ?? 0}</b><small>Signals</small></span><em>No signal {counts?.no_signal ?? 0}</em></article>
                <article className="kpi"><span className="kpi-icon green">0</span><span><b>{summary?.execution.orders ?? 0}/{summary?.execution.fills ?? 0}</b><small>Orders / fills</small></span><em>Execution disabled</em></article>
              </section>

              <section className="replay-evidence-grid">
                <article className="panel replay-dataset">
                  <div className="panel-heading"><div><span className="section-kicker">Immutable source evidence</span><h2>Dataset</h2></div></div>
                  {summary?.dataset ? (
                    <dl>
                      <div><dt>Fingerprint</dt><dd title={summary.dataset.fingerprint}>{summary.dataset.fingerprint}</dd></div>
                      <div><dt>Warm-up start</dt><dd><ZonedTimestamp value={summary.dataset.warmup_start} /></dd></div>
                      {Object.entries(summary.dataset.candle_counts).map(([timeframe, value]) => (
                        <div key={timeframe}><dt>{timeframe} candles</dt><dd>{value.accepted} accepted · {value.warmup} warm-up · {value.display} display</dd></div>
                      ))}
                      <div><dt>Determinism digest</dt><dd title={run.determinism_digest ?? ""}>{run.determinism_digest ?? "Pending"}</dd></div>
                    </dl>
                  ) : <p className="empty-copy">Dataset metadata is pending.</p>}
                </article>
                <article className="panel replay-quality">
                  <div className="panel-heading"><div><span className="section-kicker">Normalizer and window checks</span><h2>Data quality</h2></div><b>{summary?.data_quality.length ?? 0}</b></div>
                  {summary?.data_quality.length ? (
                    <ul>{summary.data_quality.slice(0, 12).map((finding, index) => <li key={`${finding.code}-${index}`}><b>{finding.code}</b><span>{finding.timeframe} · {finding.detail}</span></li>)}</ul>
                  ) : <p className="empty-copy">No duplicates, gaps, malformed rows or quarantined windows.</p>}
                </article>
              </section>

              <section className="panel replay-history">
                <div className="panel-heading"><div><span className="section-kicker">Backend-produced outcomes</span><h2>Chronological evaluations</h2></div><b>{evaluations?.total ?? 0}</b></div>
                <form className="replay-filters" method="get">
                  <input type="hidden" name="run" value={run.run_id} />
                  <select name="timeframe" defaultValue={filters.timeframe ?? ""}><option value="">All timeframes</option><option value="H1">H1</option><option value="H4">H4</option></select>
                  <select name="outcome" defaultValue={filters.outcome ?? ""}><option value="">All outcomes</option><option value="SIGNAL">Signal</option><option value="NO_SIGNAL">No signal</option></select>
                  <select name="filter_outcome" defaultValue={filters.filter_outcome ?? ""}><option value="">All filters</option><option value="PASS">Pass</option><option value="FAIL">Fail</option></select>
                  <select name="reason_code" defaultValue={filters.reason_code ?? ""}><option value="">All reason codes</option>{Object.keys(summary?.reason_counts ?? {}).map((reason) => <option value={reason} key={reason}>{reason}</option>)}</select>
                  <button type="submit">Apply filters</button>
                </form>
                {evaluations?.items.length ? (
                  <div className="replay-table-wrap"><table className="replay-table"><thead><tr><th>Timestamp (Broker Time)</th><th>TF</th><th>Filter</th><th>Signal</th><th>Close</th><th>New York D1 close</th><th>Reason codes</th><th /></tr></thead><tbody>{evaluations.items.map((item) => <tr key={item.id}><td><ZonedTimestamp value={item.signal_close_utc} /></td><td><span className="tf-tag">{item.timeframe}</span></td><td><span className={`replay-result ${item.filter_outcome.toLowerCase()}`}>{item.filter_outcome}</span></td><td><span className={`replay-result ${item.signal_outcome === "SIGNAL" ? "pass" : "neutral"}`}>{item.signal_outcome}</span></td><td>{price(item.market_values.signal_close)}</td><td><ZonedTimestamp value={item.d1_context_close_utc} newYorkPrefix="New York close" /></td><td><span className="replay-reasons">{item.reason_codes.slice(0, 3).join(" · ")}</span></td><td><Link href={href(filters, { evaluation: String(item.id) })}>Inspect</Link></td></tr>)}</tbody></table></div>
                ) : <div className="replay-empty-row">No evaluations match the selected filters.</div>}
                {evaluations && evaluations.pages > 1 && <div className="replay-pagination"><Link aria-disabled={evaluations.page <= 1} href={href(filters, { page: String(Math.max(1, evaluations.page - 1)), evaluation: "" })}>Previous</Link><span>Page {evaluations.page} of {evaluations.pages}</span><Link aria-disabled={evaluations.page >= evaluations.pages} href={href(filters, { page: String(Math.min(evaluations.pages, evaluations.page + 1)), evaluation: "" })}>Next</Link></div>}
              </section>

              {detail && (
                <section className="panel replay-inspector" aria-label="Evaluation inspector">
                  <div className="panel-heading"><div><span className="section-kicker">Persisted inputs and outputs</span><h2>Evaluation #{detail.ordinal} · {detail.timeframe}</h2></div><Link href={href(filters, { evaluation: "" })}>Close</Link></div>
                  <div className="inspector-grid">
                    <dl><div><dt>Signal close</dt><dd><ZonedTimestamp value={detail.signal_close_utc} /></dd></div><div><dt>Replay as-of</dt><dd><ZonedTimestamp value={detail.replay_as_of_utc} /></dd></div><div><dt>New York D1 close</dt><dd><ZonedTimestamp value={detail.d1_context_close_utc} newYorkPrefix="New York close" /></dd></div><div><dt>Filter / signal</dt><dd>{detail.filter_outcome} / {detail.signal_outcome}</dd></div></dl>
                    <dl><div><dt>Open</dt><dd>{price(detail.market_values.signal_open)}</dd></div><div><dt>High / low</dt><dd>{price(detail.market_values.signal_high)} / {price(detail.market_values.signal_low)}</dd></div><div><dt>MA 10 / 20</dt><dd>{price(detail.market_values.sma10)} / {price(detail.market_values.sma20)}</dd></div><div><dt>D1 ATR</dt><dd>{price(detail.market_values.atr_d1_wilder_5)}</dd></div></dl>
                  </div>
                  <div className="reason-list">{detail.reason_codes.map((reason) => <span key={reason}>{reason}</span>)}</div>
                  <div className="replay-event-sequence">{detail.events.map((event) => <span key={event.sequence}><b>{event.sequence}</b>{event.event_type}</span>)}</div>
                  <details><summary>Canonical input bars</summary><pre>{JSON.stringify(detail.input, null, 2)}</pre></details>
                </section>
              )}
            </>
          )}
        </div>
      </section>
    </main>
  );
}
