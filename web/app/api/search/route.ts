import { NextResponse } from "next/server";

import { coverageStats, searchPackages } from "@/lib/graph";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const q = new URL(request.url).searchParams.get("q") ?? "";
  if (!q) return NextResponse.json({ names: [], coverage: coverageStats() });
  return NextResponse.json({ names: searchPackages(q, 10) });
}
