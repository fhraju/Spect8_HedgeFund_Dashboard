import { describe, expect, it } from "vitest";

import {
  DEFAULT_DASHBOARD_DISPLAY_TIME_ZONE,
  DEFAULT_DASHBOARD_DISPLAY_TIME_ZONE_LABEL,
  formatDashboardTimestamp,
  formatNewYorkSessionDate,
  NEW_YORK_TIME_ZONE,
} from "@/lib/time";

describe("broker and strategy timestamp formatting", () => {
  it("derives IC Markets broker time from New York EDT in summer", () => {
    expect(NEW_YORK_TIME_ZONE).toBe("America/New_York");
    expect(DEFAULT_DASHBOARD_DISPLAY_TIME_ZONE).toBe("IC_MARKETS_NY_CLOSE_FOREX_V1");
    expect(DEFAULT_DASHBOARD_DISPLAY_TIME_ZONE_LABEL).toBe("IC Markets Broker Time");
    expect(formatDashboardTimestamp("2026-08-05T07:00:00Z")).toEqual({
      primary: "05 Aug 2026, 10:00 IC Markets Broker Time",
      newYork: "03:00 EDT",
      newYorkFull: "05 Aug 2026, 03:00 EDT",
      utc: "07:00 UTC",
    });
    expect(formatDashboardTimestamp("2026-08-05T08:00:00Z").primary).toBe(
      "05 Aug 2026, 11:00 IC Markets Broker Time",
    );
  });

  it("uses the IANA rules for broker winter time and New York EST", () => {
    expect(formatDashboardTimestamp("2026-01-15T07:00:00Z")).toEqual({
      primary: "15 Jan 2026, 09:00 IC Markets Broker Time",
      newYork: "02:00 EST",
      newYorkFull: "15 Jan 2026, 02:00 EST",
      utc: "07:00 UTC",
    });
  });

  it("changes broker offset when New York enters daylight saving time", () => {
    expect(formatDashboardTimestamp("2026-03-08T06:59:00Z").primary).toBe("08 Mar 2026, 08:59 IC Markets Broker Time");
    expect(formatDashboardTimestamp("2026-03-08T07:01:00Z").primary).toBe("08 Mar 2026, 10:01 IC Markets Broker Time");
  });

  it("formats the aggregator session date as the New York closing date", () => {
    expect(formatNewYorkSessionDate("2026-08-04")).toBe("04 Aug 2026");
  });
});
