import type { CurrentSignals, FilterMode, ScannerInstrument } from "./api-types";
import { currentConfirmation, hasCurrentEvaluation } from "./signal-eligibility";

const isDirection = (value: string) => value === "BUY" || value === "SELL";

type InstrumentReference = string | ScannerInstrument;

function scannerRow(value: InstrumentReference): ScannerInstrument | null {
  return typeof value === "string" ? null : value;
}

/** Count current signal identities, not instruments or historical scanner statuses. */
export function signalTotals(
  data: CurrentSignals | null,
  mode: FilterMode,
  instruments: readonly InstrumentReference[],
  now: number,
  failed = false,
) {
  const unavailable = { confirmed: null, forming: null, formingNote: "Waiting for updates" };
  if (!data) return { ...unavailable, formingNote: failed ? "Updates unavailable" : unavailable.formingNote };
  const responseTime = Date.parse(data.as_of);
  if (failed || !Number.isFinite(responseTime) || responseTime > now || now - responseTime > 30000) {
    return { ...unavailable, formingNote: "Updates unavailable" };
  }
  const monitored = new Set(
    instruments.map((item) => (typeof item === "string" ? item : item.instrument_id)),
  );
  const rows = new Map(
    instruments
      .map(scannerRow)
      .filter((row): row is ScannerInstrument => row !== null)
      .map((row) => [row.instrument_id, row]),
  );
  const timeframes = new Set(mode === "MICRO" ? ["M30", "H1"] : ["H1", "H4"]);
  const eligible = (instrument: string, signalMode: string, timeframe: string) =>
    monitored.has(instrument) && signalMode === mode && timeframes.has(timeframe);
  // One eligibility rule for signal cards and the headline total: the shared
  // confirmation-validity predicate plus the matching evaluation
  // (dependencies) still current.
  const evaluationCurrent = (instrument: string, timeframe: string) => {
    const row = rows.get(instrument);
    return row ? hasCurrentEvaluation(row, mode, timeframe) : true;
  };
  const confirmed = new Set(data.confirmed.filter(s =>
    eligible(s.instrument_id, s.mode, s.timeframe) &&
    currentConfirmation(s, now) &&
    evaluationCurrent(s.instrument_id, s.timeframe),
  ).map(s => JSON.stringify([s.source_provider, s.instrument_id, s.mode, s.timeframe, s.source_bar_start, s.direction]))).size;

  const candidates = (data.forming_candidates ?? []).filter(c => eligible(c.instrument_id, c.mode, c.timeframe));
  const ready = candidates.filter(c => c.state === "READY" && c.source_as_of && c.source_bar_end &&
    Date.parse(c.source_as_of) <= now && now - Date.parse(c.source_as_of) <= (c.max_age_seconds ?? 600) * 1000 && Date.parse(c.source_bar_end) > now);
  const forming = new Set(data.forming.filter(s => {
    const instrument = s.instrument_id ?? s.instrument;
    const sourceTime = Date.parse(s.source_as_of ?? s.formed_at);
    // The card and the headline share the same rule: a forming signal is only
    // current when its matching mode/timeframe evaluation is CURRENT. An
    // unrelated timeframe being blocked does not affect this exact match.
    return eligible(instrument, s.mode, s.timeframe) && isDirection(s.direction) &&
      Date.parse(s.source_bar_start) <= now && Date.parse(s.source_bar_end) > now &&
      sourceTime <= now &&
      evaluationCurrent(instrument, s.timeframe) &&
      ready.some(c => c.instrument_id === instrument && c.timeframe === s.timeframe &&
        now - sourceTime <= (c.max_age_seconds ?? 600) * 1000 &&
        (!s.authority || c.authority === s.authority));
  }).map(s => JSON.stringify([s.authority, s.instrument_id ?? s.instrument, s.mode, s.timeframe, s.source_bar_start, s.direction]))).size;
  const unsupported = new Set(candidates.filter(c => c.state === "UNSUPPORTED" && c.supported === false)
    .map(c => JSON.stringify([c.instrument_id, c.mode, c.timeframe]))).size;
  const expected = monitored.size * timeframes.size - unsupported;
  return {
    confirmed,
    forming: ready.length || !expected ? forming : null,
    formingNote: ready.length === expected ? "Current provisional signals" : ready.length
      ? `Available data · ${ready.length}/${expected} evaluations ready`
      : "Waiting for forming data",
  };
}
