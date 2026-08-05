import { formatDashboardTimestamp } from "@/lib/time";

export function ZonedTimestamp({
  value,
  showUtc = true,
  newYorkPrefix,
}: {
  value: string | null | undefined;
  showUtc?: boolean;
  newYorkPrefix?: string;
}) {
  if (!value) return <span className="zoned-time missing">Not available</span>;
  const formatted = formatDashboardTimestamp(value);
  return (
    <time className="zoned-time" dateTime={value}>
      <span>{formatted.primary}</span>
      {showUtc && (
        <small>
          {newYorkPrefix
            ? `${newYorkPrefix}: ${formatted.newYorkFull}`
            : formatted.newYork}{" "}
          · {formatted.utc}
        </small>
      )}
    </time>
  );
}
