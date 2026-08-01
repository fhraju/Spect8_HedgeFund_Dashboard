import { NextRequest, NextResponse } from "next/server";

import {
  createSessionToken,
  SESSION_COOKIE,
  SESSION_TTL_SECONDS,
  verifyPassword,
} from "@/lib/crypto-auth";
import { dashboardRedirectUrl } from "@/lib/redirect-url";

export async function POST(request: NextRequest): Promise<NextResponse> {
  const form = await request.formData();
  const password = form.get("password");
  const passwordHash = process.env.DASHBOARD_PASSWORD_HASH;
  const sessionSecret = process.env.SESSION_SECRET;
  if (
    typeof password !== "string" ||
    !passwordHash ||
    !sessionSecret ||
    sessionSecret.length < 32 ||
    !verifyPassword(password, passwordHash)
  ) {
    return NextResponse.redirect(
      dashboardRedirectUrl(request, "/login?error=1"),
      303,
    );
  }

  const response = NextResponse.redirect(
    dashboardRedirectUrl(request, "/dashboard"),
    303,
  );
  response.cookies.set({
    name: SESSION_COOKIE,
    value: createSessionToken(sessionSecret),
    httpOnly: true,
    secure:
      process.env.DASHBOARD_ORIGIN?.startsWith("https://") ??
      process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: SESSION_TTL_SECONDS,
  });
  return response;
}
