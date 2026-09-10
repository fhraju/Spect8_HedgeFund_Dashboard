import type { CurrentSignals, FilterMode } from "./api-types";

const isDirection = (value: string) => value === "BUY" || value === "SELL";

/** Count current signal identities, not instruments or historical scanner statuses. */
export function signalTotals(
  data: CurrentSignals | null,
  mode: FilterMode,
  instrumentIds: readonly string[],
  now: number,
  failed = false,
) {
  const unavailable = { confirmed: null, forming: null, formingNote: "Waiting for updates" };
  if (!data) return { ...unavailable, formingNote: failed ? "Updates unavailable" : unavailable.formingNote };
  const responseTime = Date.parse(data.as_of);
  if (failed || !Number.isFinite(responseTime) || now - responseTime > 30000) {
    return { ...unavailable, formingNote: "Updates unavailable" };
  }
  const instruments = new Set(instrumentIds);
  const timeframes = new Set(mode === "MICRO" ? ["M30", "H1"] : ["H1", "H4"]);
  const eligible = (instrument: string, signalMode: string, timeframe: string) =>
    instruments.has(instrument) && signalMode === mode && timeframes.has(timeframe);
  const confirmed = new Set(data.confirmed.filter(s =>
    eligible(s.instrument_id, s.mode, s.timeframe) && isDirection(s.direction) &&
    Date.parse(s.confirmed_at) <= now && Date.parse(s.visible_until) > now &&
    Number.isFinite(Date.parse(s.source_bar_start)),
  ).map(s => JSON.stringify([s.source_provider, s.instrument_id, s.mode, s.timeframe, s.source_bar_start, s.direction]))).size;

  const candidates = (data.forming_candidates ?? []).filter(c => eligible(c.instrument_id, c.mode, c.timeframe));
  const ready = candidates.filter(c => c.state === "READY" && c.source_as_of && c.source_bar_end &&
    Date.parse(c.source_as_of) <= now && now - Date.parse(c.source_as_of) <= 600000 && Date.parse(c.source_bar_end) > now);
  const forming = new Set(data.forming.filter(s => {
    const instrument = s.instrument_id ?? s.instrument;
    const sourceTime = Date.parse(s.source_as_of ?? s.formed_at);
    return eligible(instrument, s.mode, s.timeframe) && isDirection(s.direction) &&
      Date.parse(s.source_bar_start) <= now && Date.parse(s.source_bar_end) > now &&
      sourceTime <= now && now - sourceTime <= 600000 &&
      ready.some(c => c.instrument_id === instrument && c.timeframe === s.timeframe &&
        (!s.authority || c.authority === s.authority));
  }).map(s => JSON.stringify([s.authority, s.instrument_id ?? s.instrument, s.mode, s.timeframe, s.source_bar_start, s.direction]))).size;
  const expected = instruments.size * timeframes.size;
  return {
    confirmed,
    forming: ready.length || !expected ? forming : null,
    formingNote: ready.length === expected ? "Current provisional signals" : ready.length
      ? `Available data · ${ready.length}/${expected} evaluations ready`
      : "Waiting for forming data",
  };
}
