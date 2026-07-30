import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { NextRequest } from "next/server";

import { SESSION_COOKIE, verifySessionToken } from "./crypto-auth";

function sessionSecret(): string {
  const value = process.env.SESSION_SECRET;
  if (!value || value.length < 32) {
    throw new Error("SESSION_SECRET must contain at least 32 characters");
  }
  return value;
}

export async function hasDashboardSession(): Promise<boolean> {
  const store = await cookies();
  return verifySessionToken(store.get(SESSION_COOKIE)?.value, sessionSecret());
}

export async function requireDashboardSession(): Promise<void> {
  if (!(await hasDashboardSession())) {
    redirect("/login");
  }
}

export function requestHasDashboardSession(request: NextRequest): boolean {
  return verifySessionToken(
    request.cookies.get(SESSION_COOKIE)?.value,
    sessionSecret(),
  );
}
