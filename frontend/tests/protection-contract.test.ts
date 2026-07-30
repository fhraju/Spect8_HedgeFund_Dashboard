import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { createSessionToken, SESSION_COOKIE } from "@/lib/crypto-auth";
import { proxy } from "@/proxy";

const root = resolve(import.meta.dirname, "..");

function source(relative: string): string {
  return readFileSync(resolve(root, relative), "utf8");
}

describe("server-only protection and strategy boundary", () => {
  const previousSecret = process.env.SESSION_SECRET;

  afterEach(() => {
    process.env.SESSION_SECRET = previousSecret;
  });

  it("rejects unauthenticated page and API requests before rendering", () => {
    process.env.SESSION_SECRET =
      "proxy-test-session-secret-that-is-long-enough";
    const page = proxy(new NextRequest("http://localhost/dashboard"));
    const api = proxy(new NextRequest("http://localhost/api/dashboard"));
    expect(page.status).toBe(307);
    expect(page.headers.get("location")).toBe("http://localhost/login");
    expect(api.status).toBe(401);
  });

  it("allows a correctly signed session through the proxy", () => {
    const secret = "proxy-test-session-secret-that-is-long-enough";
    process.env.SESSION_SECRET = secret;
    const token = createSessionToken(secret);
    const request = new NextRequest("http://localhost/dashboard", {
      headers: { cookie: `${SESSION_COOKIE}=${token}` },
    });
    expect(proxy(request).headers.get("x-middleware-next")).toBe("1");
  });

  it("protects both the dashboard page and browser-facing API routes", () => {
    expect(source("app/dashboard/page.tsx")).toContain(
      "requireDashboardSession",
    );
    expect(source("app/api/dashboard/route.ts")).toContain(
      "requestHasDashboardSession",
    );
    expect(source("app/api/dashboard/replay/route.ts")).toContain(
      "requestHasDashboardSession",
    );
  });

  it("keeps backend credentials in server-only modules", () => {
    expect(source("lib/backend.ts")).toContain('import "server-only"');
    expect(source("lib/backend.ts")).not.toContain("NEXT_PUBLIC_");
    expect(source("lib/server-auth.ts")).toContain('import "server-only"');
  });

  it("does not import golden code or calculate strategy formulas", () => {
    const productionFiles = [
      "components/dashboard.tsx",
      "lib/backend.ts",
      "app/dashboard/page.tsx",
    ].map(source);
    for (const code of productionFiles) {
      expect(code).not.toContain("golden/reference");
      expect(code).not.toContain("reference/calculator");
      expect(code).not.toMatch(/\bSMA10\b|\bSMA20\b|\bATR\b/);
      expect(code).not.toContain("activation_buffer");
      expect(code).not.toContain("pivot_low");
    }
  });

  it("preserves and renders simultaneous BUY and SELL results", () => {
    expect(source("lib/api-types.ts")).toContain(
      "levels_results: LevelsResult[]",
    );
    expect(source("components/dashboard.tsx")).toContain("CONFIRMED_BOTH");
    expect(source("components/dashboard.tsx")).toContain(
      "status.levels_results",
    );
    expect(source("components/dashboard.tsx")).toContain(
      "Independent BUY / SELL",
    );
  });
});
