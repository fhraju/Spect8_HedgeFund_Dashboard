import { NextRequest } from "next/server";

export function dashboardRedirectUrl(
  request: NextRequest,
  path: string,
): URL {
  const configuredOrigin = process.env.DASHBOARD_ORIGIN;
  return new URL(path, configuredOrigin || request.url);
}
