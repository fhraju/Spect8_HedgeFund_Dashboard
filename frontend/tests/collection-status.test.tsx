import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {expect, it} from "vitest";
import {CollectionStatus} from "../components/collection-status";

it("keeps market observations separate from failed historical recovery and current evaluation", () => {
  const value=JSON.parse(JSON.stringify({state:"BLOCKED",pending_gaps:1,failure_reason:"HISTORY_UNAVAILABLE",retry_at:null,
    market_observation:{state:"OPEN",observed_at:"2026-09-11T13:00:00Z"},
    calendar_import:{state:"CURRENT",checked_at:"2026-09-11T05:00:00Z"}}));
  const html=renderToStaticMarkup(<CollectionStatus value={value} freshness="CURRENT"/>);
  expect(html).toContain('class="collection-status"');
  expect(html).toContain("Collection: BLOCKED");
  expect(html).toContain("Evaluations: CURRENT");
  expect(html).toContain("Market observation: OPEN");
  expect(html).toContain("HISTORY_UNAVAILABLE");
  expect(html).not.toContain("Retry:");
});

it("shows unavailable market and calendar evidence without advertising an open market", () => {
  const html=renderToStaticMarkup(<CollectionStatus value={{state:"RECOVERING",
    market_observation:{state:"UNKNOWN",poll_failure_reason:"BLOCKED:IGInvalidApiKeyError"},
    calendar_import:{state:"STALE",failure_reason:"CALENDAR_REFRESH_REQUIRED"}}} freshness="STALE"/>);
  expect(html).toContain("Market observation: UNKNOWN");
  expect(html).toContain("CALENDAR_REFRESH_REQUIRED");
  expect(html).toContain("BLOCKED:IGInvalidApiKeyError");
  expect(html).not.toContain("Market observation: OPEN");
});

it("shows calendar-rule conflicts inside the existing collection field", () => {
  const html=renderToStaticMarkup(<CollectionStatus value={{state:"COLLECTING",
    calendar_activation:{state:"REVIEW_REQUIRED",issues:[{date:"2026-11-27",reason:"CALENDAR_RULE_CONFLICT"}]}}} freshness="CURRENT"/>);
  expect(html).toContain("Calendar rules: REVIEW REQUIRED");
  expect(html).toContain("2026-11-27: CALENDAR_RULE_CONFLICT");
  expect(html).toContain('class="collection-status"');
});

it("renders an explicit unknown state instead of crashing", () => {
  const html = renderToStaticMarkup(
    <CollectionStatus value={{ state: undefined as unknown as string }} />,
  );
  expect(html).toContain("Collection: UNKNOWN");
});

it("shows authoritative family pending gaps and omits non-applicable allowance telemetry", () => {
  const html = renderToStaticMarkup(
    <CollectionStatus
      value={{
        state: "STREAMING",
        pending_gaps: 0,
        terminal_gaps: 0,
      }}
      freshness="CURRENT"
    />,
  );
  expect(html).toContain("Collection: STREAMING");
  expect(html).toContain("Pending gaps: 0");
  expect(html).not.toContain("Historical points remaining");
  expect(html).not.toContain("Pending gaps: Unknown");
});

it("keeps authoritative FX allowance telemetry unchanged", () => {
  const html = renderToStaticMarkup(
    <CollectionStatus
      value={{
        state: "RECOVERING",
        pending_gaps: 2,
        allowance: { remaining: 2606, reset_at: 1790256688 },
      }}
      freshness="CURRENT"
    />,
  );
  expect(html).toContain("Pending gaps: 2");
  expect(html).toContain("Historical points remaining: 2606");
});

it("retains a truthful unknown only when allowance authority is present but unobserved", () => {
  const html = renderToStaticMarkup(
    <CollectionStatus
      value={{ state: "STREAMING", pending_gaps: 0, allowance: { remaining: null } }}
      freshness="CURRENT"
    />,
  );
  expect(html).toContain("Historical points remaining: Unknown");
});
