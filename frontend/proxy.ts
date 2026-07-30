import { NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE, verifySessionToken } from "@/lib/crypto-auth";

export function proxy(request: NextRequest): NextResponse {
  const secret = process.env.SESSION_SECRET ?? "";
  const authenticated = verifySessionToken(
    request.cookies.get(SESSION_COOKIE)?.value,
    secret,
  );
  if (authenticated) {
    return NextResponse.next();
  }
  if (request.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json(
      { error: "Authentication required" },
      { status: 401 },
    );
  }
  return NextResponse.redirect(new URL("/login", request.url));
}

export const config = {
  matcher: ["/dashboard/:path*", "/api/dashboard/:path*"],
};
