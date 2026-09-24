export type CollectionHealth = {
  state: string;
  pending_gaps?: number;
  terminal_gaps?: number;
  failure_reason?: string | null;
  retry_at?: number | null;
  account_type?: string;
  cadence_seconds?: number;
  latest_completed_close?: string | null;
  next_collection_at?: string | null;
  allowance?: { remaining?: number | null; reset_at?: number | null; observed_at?: number | null };
  market_observation?: { state: string; observed_at?: string; poll_failure_reason?: string | null };
  calendar_import?: { state: string; checked_at?: string | null; failure_reason?: string | null };
  calendar_activation?: { state: string; version?: string; reason?: string; issues?: {date: string; reason: string}[] } | null;
  stream_health_observed_at?: string | null;
};

export function CollectionStatus({ value, freshness }: { value?: CollectionHealth | null; freshness?: string | null }) {
  if (!value) return null;
  return <details className="collection-status">
    <summary>Collection: {(value.state ?? "UNKNOWN").replaceAll("_", " ")}</summary>
    <small>Evaluations: {freshness ?? "UNKNOWN"}</small>
    <small>Pending gaps: {value.pending_gaps ?? "Unknown"}</small>
    {value.allowance && <small>Historical points remaining: {value.allowance.remaining ?? "Unknown"}</small>}
    {value.account_type === "SPREADBET" && <small>Spread-betting share prices · Updates every 30 minutes</small>}
    {value.latest_completed_close && <small>Source completed: {value.latest_completed_close}</small>}
    {value.next_collection_at && <small>Next collection: {value.next_collection_at}</small>}
    {value.market_observation && <small>Market observation: {value.market_observation.state.replaceAll("_", " ")}{value.market_observation.observed_at ? ` · ${value.market_observation.observed_at}` : ""}</small>}
    {value.market_observation?.poll_failure_reason && <small role="status">Market check: {value.market_observation.poll_failure_reason}</small>}
    {value.calendar_import && <small>IG notice check: {value.calendar_import.state}{value.calendar_import.checked_at ? ` · ${value.calendar_import.checked_at}` : ""}</small>}
    {value.calendar_import?.failure_reason && <small role="status">{value.calendar_import.failure_reason}</small>}
    {value.calendar_activation && <small>Calendar rules: {value.calendar_activation.state.replaceAll("_", " ")}{value.calendar_activation.version ? ` · ${value.calendar_activation.version}` : ""}</small>}
    {value.calendar_activation?.reason && <small role="status">{value.calendar_activation.reason}</small>}
    {value.calendar_activation?.issues?.map(issue => <small role="status" key={`${issue.date}:${issue.reason}`}>{issue.date}: {issue.reason}</small>)}
    {value.stream_health_observed_at && <small>Stream health checked: {value.stream_health_observed_at}</small>}
    {value.allowance?.reset_at && <small>Allowance reset: {new Date(value.allowance.reset_at * 1000).toISOString()}</small>}
    {value.retry_at && <small>Retry: {new Date(value.retry_at * 1000).toISOString()}</small>}
    {value.failure_reason && <small role="status">{value.failure_reason}</small>}
  </details>;
}
