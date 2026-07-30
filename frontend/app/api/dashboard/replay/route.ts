import { NextRequest, NextResponse } from "next/server";

import { replaySyntheticCases } from "@/lib/backend";
import { requestHasDashboardSession } from "@/lib/server-auth";

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!requestHasDashboardSession(request)) {
    return NextResponse.json(
      { error: "Authentication required" },
      { status: 401 },
    );
  }
  return NextResponse.json(await replaySyntheticCases());
}
