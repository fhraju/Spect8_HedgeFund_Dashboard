"use client";

import { useEffect, useState } from "react";
import type { HistorySignals } from "@/lib/api-types";
import { ZonedTimestamp } from "./zoned-timestamp";

function SignalBadge({ status }: { status: string }) {
  const labels: Record<string, { icon: string; label: string }> = {
    BUY: { icon: "▲", label: "BUY" },
    SELL: { icon: "▼", label: "SELL" },
  };
  const v = labels[status] ?? { icon: "•", label: status };
  return (
    <span className={`scanner-signal-badge scanner-signal-${status.toLowerCase()}`}>
      <span aria-hidden="true">{v.icon}</span>
      {v.label}
    </span>
  );
}

export function InstrumentSignalsHistory({ instrumentId }: { instrumentId: string }) {
  const [data, setData] = useState<HistorySignals | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    async function fetchHistory() {
      try {
        const res = await fetch("/api/signals/history", { cache: "no-store" });
        if (!res.ok) return;
        const json = await res.json();
        if (!cancelled) {
          setData(json.data as HistorySignals);
          setLoading(false);
        }
      } catch {
        if (!cancelled) setLoading(false);
      }
    }
    fetchHistory();
  }, []);
  if (loading) {
    return (
      <section className="panel" aria-label="Today's signals">
        <div className="panel-heading">
          <h3>Today&apos;s Signals — {instrumentId.replace("_", "/")}</h3>
          <span className="live-dot">● Live</span>
        </div>
        <p>Loading…</p>
      </section>
    );
  }
  const rows = (data?.confirmed ?? []).filter((s) => s.instrument_id === instrumentId);
  return (
    <section className="panel" aria-label="Today's signals">
      <div className="panel-heading">
        <div>
          <h3>Today&apos;s Signals — {instrumentId.replace("_", "/")}</h3>
          <p className="panel-subtitle">
            {data?.date} ({data?.timezone}) · {rows.length} confirmed for this symbol today
          </p>
        </div>
        <span className="live-dot live">● Today</span>
      </div>
      {rows.length === 0 ? (
        <p className="scanner-empty">No confirmed signals for {instrumentId.replace("_", "/")} today — check Live Signals for forming.</p>
      ) : (
        <div className="table-wrap">
          <table className="scanner-table">
            <thead>
              <tr>
                <th>Time (Confirmed)</th>
                <th>Mode</th>
                <th>Timeframe</th>
                <th>Direction</th>
                <th>Bar</th>
                <th>Visible until</th>
                <th>State</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => {
                const visibleUntil = new Date(s.visible_until);
                const now = new Date();
                const isCurrent = now < visibleUntil;
                return (
                  <tr key={s.signal_id}>
                    <td>
                      <ZonedTimestamp value={s.confirmed_at} />
                    </td>
                    <td>
                      <span className={`mode-badge mode-${s.mode.toLowerCase()}`}>{s.mode}</span>
                    </td>
                    <td>
                      <span className="timeframe-badge">{s.timeframe}</span>
                    </td>
                    <td>
                      <SignalBadge status={s.direction} />
                    </td>
                    <td>
                      <ZonedTimestamp value={s.source_bar_start} /> → <ZonedTimestamp value={s.source_bar_end} />
                    </td>
                    <td>
                      <ZonedTimestamp value={s.visible_until} />
                    </td>
                    <td>
                      <span className={`signal-state state-${isCurrent ? "confirmed" : "expired"}`}>{isCurrent ? "CURRENT" : "EXPIRED"}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="panel-footnote">
        <small>
          Timezone: <strong>{data?.timezone}</strong> · Expired signals remain in history until next calendar day. FORMING signals are not persisted.
        </small>
      </p>
    </section>
  );
}
