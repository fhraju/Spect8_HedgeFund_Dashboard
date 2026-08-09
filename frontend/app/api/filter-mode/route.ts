import { NextRequest, NextResponse } from "next/server";

import type { FilterMode } from "@/lib/api-types";
import { setFilterMode } from "@/lib/backend";
import { requestHasDashboardSession } from "@/lib/server-auth";

export async function PATCH(request: NextRequest): Promise<NextResponse> {
  if (!requestHasDashboardSession(request)) {
    return NextResponse.json(
      { error: "Authentication required" },
      { status: 401 },
    );
  }
  const body = (await request.json()) as { mode?: string };
  if (body.mode !== "MICRO" && body.mode !== "MACRO") {
    return NextResponse.json({ error: "Invalid filter mode" }, { status: 400 });
  }
  return NextResponse.json(await setFilterMode(body.mode as FilterMode));
}
