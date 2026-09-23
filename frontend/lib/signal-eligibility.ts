import type {
  CurrentSignals,
  EvaluationCandidate,
  FilterMode,
  FormingSignal,
  ScannerInstrument,
} from "./api-types";

/**
 * One shared predicate for directional Filter candidacy.
 *
 * A Filter counts as a candidate only when it is directionally matched
 * (not NONE/WAITING) and its readiness is CURRENT. A missing readiness field
 * means the row predates the field and the backend default applies.
 */
export function currentFilterReadiness(row: ScannerInstrument): string {
  return (
    row.filter_readiness ??
    row.current_filter?.filter_freshness ??
    "CURRENT"
  );
}

export function isCurrentDirectionalFilter(row: ScannerInstrument): boolean {
  const status = row.current_filter?.status ?? "WAITING";
  if (status === "NONE" || status === "WAITING") return false;
  return currentFilterReadiness(row) === "CURRENT";
}

/**
 * The row's current valid evaluation for one mode/timeframe.
 *
 * Uses per-candidate evaluation state when the backend publishes it; a
 * STALE/BLOCKED/UNSUPPORTED candidate is never valid. Falls back to the
 * mode-level evaluation freshness, then to the legacy permissive default for
 * rows without evaluation metadata.
 */
export function currentEvaluation(
  row: ScannerInstrument | undefined,
  mode: FilterMode,
  timeframe: string,
): EvaluationCandidate | null {
  if (!row) return null;
  const candidates = row.evaluation_candidates ?? [];
  return (
    candidates.find(
      (candidate) => candidate.mode === mode && candidate.timeframe === timeframe,
    ) ?? null
  );
}

export function hasCurrentEvaluation(
  row: ScannerInstrument | undefined,
  mode: FilterMode,
  timeframe: string,
): boolean {
  if (!row) return true;
  const candidate = currentEvaluation(row, mode, timeframe);
  if (candidate) return candidate.state === "CURRENT";
  if (row.evaluation_freshness != null) {
    return row.evaluation_freshness === "CURRENT";
  }
  return true;
}

export function hasAttention(row: ScannerInstrument): boolean {
  const reasons = row.attention_reasons;
  if (reasons && reasons.length > 0) return true;
  if (reasons && reasons.length === 0) return false;
  return !["HEALTHY", "RECOVERED"].includes(row.data_status);
}

export function feedHealthy(row: ScannerInstrument): boolean {
  return ["HEALTHY", "RECOVERED"].includes(row.feed_health ?? row.data_status);
}

export type SignalCellState =
  | { kind: "loading" }
  | { kind: "unavailable" }
  | { kind: "expired" }
  | { kind: "unsupported"; reason: string | null }
  | { kind: "stale"; reason: string | null }
  | { kind: "waiting"; reason: string | null }
  | { kind: "none" }
  | {
      kind: "live";
      state: "CONFIRMED" | "FORMING";
      signal: FormingSignal | CurrentSignals["confirmed"][0];
    };

export function resolveSignalCell({
  data,
  failed,
  now,
  instrumentId,
  mode,
  timeframe,
  evaluationCurrent = true,
}: {
  data: CurrentSignals | null;
  failed: boolean;
  now: number;
  instrumentId: string;
  mode: FilterMode;
  timeframe: string;
  evaluationCurrent?: boolean;
}): SignalCellState {
  if (!data) {
    return failed ? { kind: "unavailable" } : { kind: "loading" };
  }
  const responseTime = Date.parse(data.as_of ?? "");
  if (
    failed ||
    !Number.isFinite(responseTime) ||
    responseTime > now ||
    now - responseTime > 30000
  ) {
    return { kind: "unavailable" };
  }
  const confirmedSignals = data.confirmed.filter(
    (signal) =>
      signal.instrument_id === instrumentId &&
      signal.mode === mode &&
      signal.timeframe === timeframe,
  );
  const confirmed = confirmedSignals.find(
    (signal) => Date.parse(signal.visible_until) > now,
  );
  if (confirmed) {
    if (!evaluationCurrent) {
      return { kind: "stale", reason: "Evaluation is no longer current" };
    }
    return { kind: "live", state: "CONFIRMED", signal: confirmed };
  }
  if (confirmedSignals.length > 0) {
    return { kind: "expired" };
  }
  const candidate = data.forming_candidates?.find(
    (item) =>
      item.instrument_id === instrumentId &&
      item.mode === mode &&
      item.timeframe === timeframe,
  );
  if (candidate && (candidate.state === "UNSUPPORTED" || candidate.supported === false)) {
    return { kind: "unsupported", reason: candidate.reason ?? null };
  }
  const forming = data.forming.find(
    (signal) =>
      (signal.instrument_id ?? signal.instrument) === instrumentId &&
      signal.mode === mode &&
      signal.timeframe === timeframe &&
      Date.parse(signal.source_bar_start) <= now &&
      Date.parse(signal.source_as_of ?? signal.formed_at) <= now &&
      Date.parse(signal.source_bar_end) > now &&
      now - Date.parse(signal.source_as_of ?? signal.formed_at) <=
        (candidate?.max_age_seconds ?? 600) * 1000,
  );
  if (forming) {
    if (!evaluationCurrent) {
      return { kind: "stale", reason: "Evaluation is no longer current" };
    }
    return { kind: "live", state: "FORMING", signal: forming };
  }
  if (candidate) {
    if (candidate.state === "STALE" || candidate.state === "BLOCKED") {
      return { kind: "stale", reason: candidate.reason ?? null };
    }
    if (candidate.state === "WAITING_FOR_DATA") {
      return { kind: "waiting", reason: candidate.reason ?? null };
    }
  }
  return { kind: "none" };
}
