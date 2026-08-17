import { NextResponse } from "next/server";

import { buildIncident, exposureTimeline } from "@/lib/incident";
import { packageInfo } from "@/lib/graph";
import { parseLockfile, type ParsedLockfile } from "@/lib/lockfile";

// Node runtime: node:sqlite and the bundled database file are both server-side only.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface IncidentRequest {
  pkg?: string;
  version?: string;
  lockfile?: unknown;
  limit?: number;
}

export async function POST(request: Request) {
  const started = performance.now();
  let body: IncidentRequest;
  try {
    body = (await request.json()) as IncidentRequest;
  } catch {
    return NextResponse.json({ error: "body must be JSON" }, { status: 400 });
  }

  const pkg = (body.pkg ?? "").trim();
  if (!pkg) return NextResponse.json({ error: "pkg is required" }, { status: 400 });

  const info = packageInfo(pkg);
  if (!info) {
    return NextResponse.json(
      {
        error: `'${pkg}' is not inside the coverage cap`,
        hint: "The graph covers the top 25,000 npm packages by weekly downloads. See README.",
      },
      { status: 404 },
    );
  }

  // Default to the newest version we hold, so a bare package name still answers.
  const version = (body.version ?? "").trim() || info.latest || info.versions[0];
  if (!version) {
    return NextResponse.json({ error: `no known versions for '${pkg}'` }, { status: 404 });
  }

  let lock: ParsedLockfile | null = null;
  if (body.lockfile) {
    try {
      lock = parseLockfile(body.lockfile);
    } catch (err) {
      return NextResponse.json(
        { error: `could not parse lockfile: ${(err as Error).message}` },
        { status: 400 },
      );
    }
  }

  const report = buildIncident(pkg, version, lock, { limit: body.limit });
  const timeline = exposureTimeline(report.advisories, version);

  return NextResponse.json({
    ...report,
    package: info,
    timeline,
    lockfile: lock
      ? { serviceName: lock.serviceName, version: lock.lockfileVersion, entries: lock.entries.length }
      : null,
    totalMs: performance.now() - started,
    // Sent so the client never reads its own clock during render — an unfixed advisory
    // window has to run to some instant, and the server's is the one the data was built at.
    generatedAt: new Date().toISOString(),
  });
}
