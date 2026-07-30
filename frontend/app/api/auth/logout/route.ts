import { NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE } from "@/lib/crypto-auth";
import { dashboardRedirectUrl } from "@/lib/redirect-url";

export async function POST(request: NextRequest): Promise<NextResponse> {
  const response = NextResponse.redirect(
    dashboardRedirectUrl(request, "/login?logged_out=1"),
    303,
  );
  response.cookies.set({
    name: SESSION_COOKIE,
    value: "",
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: 0,
  });
  return response;
}
