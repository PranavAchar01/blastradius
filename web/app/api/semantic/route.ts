import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The HydraDB half. Free-text advisory search — the question the graph cannot answer.
 *
 * `mode: "fast"` is deliberate: `thinking` measured 3.2s server-side, which alone would blow
 * the console's sub-second budget. Measured latency is returned so the UI can show it.
 */
export async function POST(request: Request) {
  const { query } = (await request.json()) as { query?: string };
  if (!query?.trim()) return NextResponse.json({ error: "query is required" }, { status: 400 });

  const key = process.env.HYDRADB_API_KEY;
  if (!key) {
    return NextResponse.json(
      { error: "HYDRADB_API_KEY is not configured on the server" },
      { status: 503 },
    );
  }
  const base = process.env.HYDRADB_URL ?? "https://api.hydradb.com";

  const t0 = performance.now();
  const res = await fetch(`${base}/query`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${key}`,
      "API-Version": "2",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      database: "blastradius",
      query,
      type: "knowledge",
      query_by: "hybrid",
      mode: "fast",
      graph_context: true,
      max_results: 8,
    }),
  });
  const wallMs = performance.now() - t0;

  if (!res.ok) {
    return NextResponse.json(
      { error: `HydraDB returned ${res.status}`, wallMs },
      { status: 502 },
    );
  }
  const body = (await res.json()) as {
    data?: { chunks?: Array<Record<string, unknown>>; graph_context?: Record<string, unknown> };
    meta?: { latency_ms?: number };
  };

  const chunks = (body.data?.chunks ?? []).map((c) => ({
    id: String(c.id ?? ""),
    score: Number(c.relevancy_score ?? 0),
    text: String(c.chunk_content ?? "").slice(0, 420),
  }));

  return NextResponse.json({
    chunks,
    graphPaths: body.data?.graph_context ?? {},
    serverMs: body.meta?.latency_ms ?? null,
    wallMs,
  });
}
