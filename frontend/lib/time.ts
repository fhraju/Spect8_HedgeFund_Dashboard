export const NEW_YORK_TIME_ZONE = "America/New_York";
export const DEFAULT_DASHBOARD_DISPLAY_TIME_ZONE = "IC_MARKETS_NY_CLOSE_FOREX_V1";
export const DEFAULT_DASHBOARD_DISPLAY_TIME_ZONE_LABEL = "IC Markets Broker Time";

export type FormattedTimestamp = {
  primary: string;
  newYork: string;
  newYorkFull: string;
  utc: string;
};

export type DashboardTimeProfile = {
  timeZone: string;
  label: string;
};

function parts(
  value: Date,
  timeZone: string,
  includeZoneName: boolean,
): Record<string, string> {
  const formatter = new Intl.DateTimeFormat("en-US", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
    ...(includeZoneName ? { timeZoneName: "short" as const } : {}),
  });
  return Object.fromEntries(
    formatter
      .formatToParts(value)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
}

function brokerParts(value: Date): Record<string, string> {
  const numeric = new Intl.DateTimeFormat("en-US", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hourCycle: "h23", timeZone: NEW_YORK_TIME_ZONE,
  });
  const wall = Object.fromEntries(
    numeric.formatToParts(value).filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  const brokerWall = new Date(Date.UTC(
    Number(wall.year), Number(wall.month) - 1, Number(wall.day),
    Number(wall.hour) + 7, Number(wall.minute), Number(wall.second),
  ));
  return parts(brokerWall, "UTC", false);
}

export function dashboardTimeProfile(): DashboardTimeProfile {
  return {
    timeZone:
      process.env.DASHBOARD_DISPLAY_TIMEZONE ??
      DEFAULT_DASHBOARD_DISPLAY_TIME_ZONE,
    label:
      process.env.DASHBOARD_DISPLAY_TIMEZONE_LABEL ??
      DEFAULT_DASHBOARD_DISPLAY_TIME_ZONE_LABEL,
  };
}

export function formatDashboardTimestamp(
  value: string,
  profile: DashboardTimeProfile = dashboardTimeProfile(),
): FormattedTimestamp {
  const instant = new Date(value);
  if (Number.isNaN(instant.valueOf())) {
    throw new RangeError(`Invalid timestamp: ${value}`);
  }
  const broker = profile.timeZone === DEFAULT_DASHBOARD_DISPLAY_TIME_ZONE
    ? brokerParts(instant)
    : parts(instant, profile.timeZone, false);
  const newYork = parts(instant, NEW_YORK_TIME_ZONE, true);
  const utc = parts(instant, "UTC", false);
  return {
    primary: `${broker.day} ${broker.month} ${broker.year}, ${broker.hour}:${broker.minute} ${profile.label}`,
    newYork: `${newYork.hour}:${newYork.minute} ${newYork.timeZoneName}`,
    newYorkFull: `${newYork.day} ${newYork.month} ${newYork.year}, ${newYork.hour}:${newYork.minute} ${newYork.timeZoneName}`,
    utc: `${utc.hour}:${utc.minute} UTC`,
  };
}

export function formatNewYorkSessionDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) throw new RangeError(`Invalid New York session date: ${value}`);
  const [, year, month, day] = match;
  const monthName = new Intl.DateTimeFormat("en-US", {
    month: "short",
    timeZone: "UTC",
  }).format(new Date(`${year}-${month}-01T12:00:00Z`));
  return `${day} ${monthName} ${year}`;
}
