import "server-only";

import type {
  DashboardSnapshot,
  HistoricalReplayEnvelope,
  HistoricalReplayEvaluationDetail,
  HistoricalReplayEvaluationPage,
  HistoricalReplayRun,
  HistoricalReplaySummary,
  SyntheticEnvelope,
} from "./api-types";

function backendConfiguration(): { baseUrl: string; apiKey: string } {
  const baseUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
  const apiKey = process.env.SPECT8_INTERNAL_API_KEY;
  if (!apiKey) {
    throw new Error("SPECT8_INTERNAL_API_KEY is not configured");
  }
  return { baseUrl, apiKey };
}

async function backendFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const { baseUrl, apiKey } = backendConfiguration();
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "X-Spect8-Internal-Key": apiKey,
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(`Backend ${path} failed with status ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function getDashboardSnapshot(): Promise<DashboardSnapshot> {
  return backendFetch<DashboardSnapshot>("/dashboard");
}

export async function replaySyntheticCases(): Promise<
  SyntheticEnvelope<
    Array<{
      source_case_id: string;
      idempotency_key: string;
      replayed: boolean;
      events_created: number;
    }>
  >
> {
  return backendFetch("/synthetic/replay", { method: "POST" });
}

export async function createHistoricalReplay(input: {
  display_start: string;
  display_end: string;
  dataset_fingerprint?: string;
}): Promise<HistoricalReplayEnvelope<HistoricalReplayRun>> {
  return backendFetch("/historical-replays", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function getHistoricalReplayRuns(): Promise<
  HistoricalReplayEnvelope<{ items: HistoricalReplayRun[] }>
> {
  return backendFetch("/historical-replays");
}

export async function getHistoricalReplaySummary(
  runId: string,
): Promise<HistoricalReplayEnvelope<HistoricalReplaySummary>> {
  return backendFetch(`/historical-replays/${encodeURIComponent(runId)}/summary`);
}

export async function getHistoricalReplayEvaluations(
  runId: string,
  filters: {
    page?: number;
    timeframe?: string;
    outcome?: string;
    filter_outcome?: string;
    reason_code?: string;
  },
): Promise<HistoricalReplayEnvelope<HistoricalReplayEvaluationPage>> {
  const params = new URLSearchParams({
    page: String(filters.page ?? 1),
    page_size: "50",
  });
  for (const [key, value] of Object.entries(filters)) {
    if (key !== "page" && value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  }
  return backendFetch(
    `/historical-replays/${encodeURIComponent(runId)}/evaluations?${params}`,
  );
}

export async function getHistoricalReplayEvaluation(
  runId: string,
  evaluationId: number,
): Promise<HistoricalReplayEnvelope<HistoricalReplayEvaluationDetail>> {
  return backendFetch(
    `/historical-replays/${encodeURIComponent(runId)}/evaluations/${evaluationId}`,
  );
}
