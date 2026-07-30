import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { POST as login } from "@/app/api/auth/login/route";
import { POST as logout } from "@/app/api/auth/logout/route";
import {
  createPasswordHash,
  createSessionToken,
  SESSION_COOKIE,
  SESSION_TTL_SECONDS,
  verifyPassword,
  verifySessionToken,
} from "@/lib/crypto-auth";

const SECRET = "a-test-session-secret-that-is-longer-than-32-characters";

describe("single-client password and session protection", () => {
  const previousHash = process.env.DASHBOARD_PASSWORD_HASH;
  const previousSecret = process.env.SESSION_SECRET;
  const previousOrigin = process.env.DASHBOARD_ORIGIN;

  beforeEach(() => {
    process.env.DASHBOARD_PASSWORD_HASH = createPasswordHash(
      "correct horse battery staple",
      "fixed-test-salt",
    );
    process.env.SESSION_SECRET = SECRET;
    process.env.DASHBOARD_ORIGIN = "http://127.0.0.1:3000";
  });

  afterEach(() => {
    process.env.DASHBOARD_PASSWORD_HASH = previousHash;
    process.env.SESSION_SECRET = previousSecret;
    process.env.DASHBOARD_ORIGIN = previousOrigin;
  });

  it("accepts only the password matching the stored scrypt hash", () => {
    const hash = process.env.DASHBOARD_PASSWORD_HASH!;
    expect(hash).toMatch(/^scrypt:16384:8:1:/);
    expect(hash).not.toContain("$");
    expect(verifyPassword("correct horse battery staple", hash)).toBe(true);
    expect(verifyPassword("wrong password", hash)).toBe(false);
    expect(verifyPassword("correct horse battery staple", "invalid")).toBe(false);
  });

  it("signs, verifies, expires, and rejects tampered sessions", () => {
    const now = 1_800_000_000;
    const token = createSessionToken(SECRET, now);
    expect(verifySessionToken(token, SECRET, now)).toBe(true);
    expect(
      verifySessionToken(token, SECRET, now + SESSION_TTL_SECONDS + 1),
    ).toBe(false);
    expect(verifySessionToken(`${token}x`, SECRET, now)).toBe(false);
    expect(verifySessionToken(undefined, SECRET, now)).toBe(false);
  });

  it("logs in with an HTTP-only strict session cookie", async () => {
    const body = new URLSearchParams({
      password: "correct horse battery staple",
    });
    const response = await login(
      new NextRequest("http://localhost/api/auth/login", {
        method: "POST",
        body,
        headers: { "content-type": "application/x-www-form-urlencoded" },
      }),
    );
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      "http://127.0.0.1:3000/dashboard",
    );
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain(`${SESSION_COOKIE}=`);
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=strict");
    expect(cookie).not.toContain("correct horse battery staple");
  });

  it("rejects an incorrect login without creating a session", async () => {
    const body = new URLSearchParams({ password: "incorrect" });
    const response = await login(
      new NextRequest("http://localhost/api/auth/login", {
        method: "POST",
        body,
        headers: { "content-type": "application/x-www-form-urlencoded" },
      }),
    );
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      "http://127.0.0.1:3000/login?error=1",
    );
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("logs out by expiring the protected cookie", async () => {
    const response = await logout(
      new NextRequest("http://localhost/api/auth/logout", { method: "POST" }),
    );
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      "http://127.0.0.1:3000/login?logged_out=1",
    );
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain(`${SESSION_COOKIE}=`);
    expect(cookie).toContain("Max-Age=0");
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=strict");
  });
});
