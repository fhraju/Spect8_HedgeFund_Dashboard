"use client";

import type { CurrentSignals } from "@/lib/api-types";
import { SignalBadge } from "./market-scanner";
import { ZonedTimestamp } from "./zoned-timestamp";

interface SignalCardProps {
  instrument: string;
  mode: "MICRO" | "MACRO";
  timeframe: "M30" | "H1" | "H4";
  signal: CurrentSignals["forming"][0] | CurrentSignals["confirmed"][0];
  isForming: boolean;
}

function modeColor(mode: string): string {
  return mode === "MICRO" ? "micro" : "macro";
}

function timeframeLabel(tf: string): string {
  return tf;
}

function directionIcon(dir: string): string {
  if (dir === "BUY") return "▲";
  if (dir === "SELL") return "▼";
  return "◆";
}

function directionColor(dir: string): string {
  if (dir === "BUY") return "buy";
  if (dir === "SELL") return "sell";
  return "neutral";
}

export function SignalCard({
  instrument,
  mode,
  timeframe,
  signal,
  isForming,
}: SignalCardProps) {
  const direction = signal.direction;
  const isConfirmed = !isForming;

  const statusClass = isForming ? "forming" : "confirmed";
  const directionClass = direction === "BUY" ? "buy" : direction === "SELL" ? "sell" : "neutral";

  return (
    <div
      className={`signal-card ${isForming ? "forming" : "confirmed"} ${directionClass}`}
      title={isForming ? "Provisional — incomplete bar" : `Confirmed until ${signal.visible_until ?? signal.confirmed_at}`}
    >
      <div className="signal-card-header">
        <span className="signal-icon" aria-hidden="true">
          {direction === "BUY" ? "▲" : signal.direction === "SELL" ? "▼" : "◆"}
        </span>
        <span className={`signal-direction ${direction.toLowerCase()}`}>
          {signal.direction}
        </span>
        <span className={`signal-status ${isForming ? "forming" : "confirmed"}`}>
          {isForming ? "FORMING" : "CONFIRMED"}
        </span>
      </div>
      <div className="signal-card-meta">
        <span className="signal-timeframe">{timeframe}</span>
        <span className="signal-mode">{mode}</span>
        {!isForming && signal.visible_until && (
          <span className="signal-visible-until">
            <ZonedTimestamp value={signal.visible_until} />
          </span>
        )}
      </div>
    </div>
  );
}

export function SignalCardFallback({ status }: { status: string }) {
  return <span className={`signal-fallback ${status.toLowerCase()}`}><span aria-hidden="true">—</span> NO SIGNAL</span>;
}