import "server-only";

import type { DashboardSnapshot, SyntheticEnvelope } from "./api-types";

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
