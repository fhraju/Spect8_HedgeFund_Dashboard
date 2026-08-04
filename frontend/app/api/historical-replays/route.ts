import { NextRequest, NextResponse } from "next/server";

import { createHistoricalReplay } from "@/lib/backend";
import { dashboardRedirectUrl } from "@/lib/redirect-url";
import { requestHasDashboardSession } from "@/lib/server-auth";

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!requestHasDashboardSession(request)) {
    return NextResponse.json(
      { error: "Authentication required" },
      { status: 401 },
    );
  }
  const form = await request.formData();
  const displayStart = String(form.get("display_start") ?? "");
  const displayEnd = String(form.get("display_end") ?? "");
  const fingerprint = String(form.get("dataset_fingerprint") ?? "").trim();
  try {
    const created = await createHistoricalReplay({
      display_start: displayStart,
      display_end: displayEnd,
      ...(fingerprint ? { dataset_fingerprint: fingerprint } : {}),
    });
    return NextResponse.redirect(
      dashboardRedirectUrl(
        request,
        `/historical-replay?run=${encodeURIComponent(created.data.run_id)}`,
      ),
      303,
    );
  } catch {
    return NextResponse.redirect(
      dashboardRedirectUrl(request, "/historical-replay?create_error=1"),
      303,
    );
  }
}
