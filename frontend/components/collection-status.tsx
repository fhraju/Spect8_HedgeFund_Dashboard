export type CollectionHealth = {
  state: string;
  pending_gaps?: number;
  failure_reason?: string | null;
  retry_at?: number | null;
  allowance?: { remaining?: number | null; reset_at?: number | null };
};

export function CollectionStatus({ value, freshness }: { value?: CollectionHealth | null; freshness?: string | null }) {
  if (!value) return null;
  return <details className="collection-status">
    <summary>Collection: {value.state.replaceAll("_", " ")}</summary>
    <small>Evaluations: {freshness ?? "UNKNOWN"}</small>
    <small>Pending gaps: {value.pending_gaps ?? "Unknown"}</small>
    <small>Historical points remaining: {value.allowance?.remaining ?? "Unknown"}</small>
    {value.retry_at && <small>Retry: {new Date(value.retry_at * 1000).toISOString()}</small>}
    {value.failure_reason && <small role="status">{value.failure_reason}</small>}
  </details>;
}
