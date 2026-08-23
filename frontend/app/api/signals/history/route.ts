import { NextResponse } from "next/server";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const date = searchParams.get("date");
  const baseUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
  const apiKey = process.env.SPECT8_INTERNAL_API_KEY;
  if (!apiKey) {
    return NextResponse.json({ error: "Missing API key" }, { status: 500 });
  }
  const qs = date ? `?date=${encodeURIComponent(date)}` : "";
  const res = await fetch(`${baseUrl}/signals/history${qs}`, {
    headers: { "X-Spect8-Internal-Key": apiKey },
    cache: "no-store",
  });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
