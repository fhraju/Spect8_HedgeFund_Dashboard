"use client";

import type { CurrentSignals, FilterMode } from "@/lib/api-types";
import { signalTotals } from "@/lib/signal-totals";

export function SignalSummary({ data, mode, instrumentIds, failed = false, now }: {
  data: CurrentSignals | null;
  mode: FilterMode;
  instrumentIds: string[];
  failed?: boolean;
  now: number;
}) {
  // Share the scanner's receipt/tick clock so cards and counts expire together.
  const totals = signalTotals(data, mode, instrumentIds, now, failed);
  const label = mode === "MICRO" ? "Micro · M30 + H1" : "Macro · H1 + H4";
  return <>
    <article className="kpi" data-signal-summary="confirmed" title="Individual unexpired signals across all monitored pairs in the selected mode">
      <span className="kpi-icon green">◉</span>
      <span><b>{totals.confirmed ?? "—"}</b><small>Confirmed Signals</small></span>
      <em>{totals.confirmed === null ? "Waiting for current signals" : label}</em>
    </article>
    <article className="kpi" data-signal-summary="forming" title={`${label} · ${totals.formingNote}`}>
      <span className="kpi-icon amber">◌</span>
      <span><b>{totals.forming ?? 0}</b><small>Forming Signals</small></span>
      <em>{totals.formingNote === "Current provisional signals" ? label : totals.formingNote}</em>
    </article>
  </>;
}
