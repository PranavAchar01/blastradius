/**
 * The six questions, ported to TypeScript over the shipped SQLite artifact.
 *
 * Uses `node:sqlite` (built into Node 22.5+) rather than better-sqlite3: no native
 * compilation, so the Vercel build has nothing to go wrong. The database is read-only and
 * bundled with the deployment.
 *
 * Semantics are kept identical to `ingest/src/blastradius_ingest/queries.py` — in particular
 * the frontier is expanded in chunks, because SQLite caps expression-tree depth at 1000 and
 * lodash alone has 728 direct dependents.
 */
import { existsSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import path from "node:path";

export const MAX_DEPTH = 12;
const FRONTIER_CHUNK = 250;

let cached: DatabaseSync | null = null;

/**
 * Locate the graph across local dev and the serverless bundle.
 *
 * `process.cwd()` is the project root in dev but `/var/task` in a Vercel function, and the
 * traced artifact can land beside the bundled route rather than at the root. Trying the known
 * candidates — and reporting all of them when none exists — turns a bare
 * "unable to open database file" into something diagnosable.
 */
function locate(): string {
  const candidates = [
    path.join(process.cwd(), "data", "blastradius-web.db"),
    path.join(process.cwd(), "web", "data", "blastradius-web.db"),
    path.join("/var/task", "data", "blastradius-web.db"),
    path.join(__dirname, "data", "blastradius-web.db"),
    path.join(__dirname, "..", "data", "blastradius-web.db"),
  ];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  throw new Error(
    `graph database not found. cwd=${process.cwd()} — looked in:\n  ${candidates.join("\n  ")}`,
  );
}

export function db(): DatabaseSync {
  if (!cached) {
    cached = new DatabaseSync(locate(), { readOnly: true });
  }
  return cached;
}

export interface Hop {
  pkg: string;
  version: string;
  depth: number;
  via: string;
}

export interface PathEdge {
  from: string;
  to: string;
  range: string | null;
  depType: string;
  depth: number;
}

export interface Answer<T> {
  question: string;
  rows: T[];
  latencyMs: number;
  notes: string[];
}

function timed<T>(question: string, fn: () => { rows: T[]; notes?: string[] }): Answer<T> {
  const t0 = performance.now();
  const { rows, notes } = fn();
  return { question, rows, latencyMs: performance.now() - t0, notes: notes ?? [] };
}

/* ------------------------------------------------------------------ Q1 */

export interface BlastRadius {
  rows: Hop[];
  path: PathEdge[];
  latencyMs: number;
  notes: string[];
  maxDepth: number;
}

export function blastRadius(
  pkg: string,
  version: string,
  opts: { maxDepth?: number; limit?: number } = {},
): BlastRadius {
  const maxDepth = opts.maxDepth ?? MAX_DEPTH;
  const t0 = performance.now();
  const conn = db();

  const reached = new Map<string, Hop>();
  const path: PathEdge[] = [];
  let frontier: Array<[string, string]> = [[pkg, version]];
  let depth = 0;
  let truncated = false;

  while (frontier.length && depth < maxDepth) {
    depth += 1;
    const next: Array<[string, string]> = [];

    for (let i = 0; i < frontier.length; i += FRONTIER_CHUNK) {
      const chunk = frontier.slice(i, i + FRONTIER_CHUNK);
      const clauses = chunk.map(() => "(d.dst_pkg = ? AND d.resolved = ?)").join(" OR ");
      const params = chunk.flat();
      const stmt = conn.prepare(
        `SELECT d.src_pkg, d.src_version, d.dst_pkg, d.resolved, d.dep_type, d.range
         FROM depends_on d WHERE ${clauses}`,
      );
      for (const r of stmt.all(...params) as Array<Record<string, string>>) {
        const key = `${r.src_pkg}@${r.src_version}`;
        if (reached.has(key)) continue;
        reached.set(key, {
          pkg: r.src_pkg,
          version: r.src_version,
          depth,
          via: `${r.dst_pkg}@${r.resolved}`,
        });
        path.push({
          from: key,
          to: `${r.dst_pkg}@${r.resolved}`,
          range: r.range ?? null,
          depType: r.dep_type,
          depth,
        });
        next.push([r.src_pkg, r.src_version]);
        if (opts.limit && reached.size >= opts.limit) {
          truncated = true;
          break;
        }
      }
      if (truncated) break;
    }
    if (truncated) break;
    frontier = next;
  }

  const notes: string[] = [];
  if (truncated) notes.push(`truncated at ${opts.limit}; NOT the complete closure`);
  if (depth >= maxDepth && frontier.length)
    notes.push(`stopped at depth ${maxDepth}; deeper dependents not included`);

  return {
    rows: [...reached.values()].sort((a, b) => a.depth - b.depth),
    path,
    latencyMs: performance.now() - t0,
    notes,
    maxDepth: depth,
  };
}

/* ------------------------------------------------------------------ Q2 */

export interface AffectedRange {
  osvId: string;
  severity: string | null;
  summary: string | null;
  introduced: string;
  fixed: string | null;
  validFrom: string | null;
  validTo: string | null;
  publishedAt: string;
}

export function advisoriesFor(pkg: string, version?: string): Answer<AffectedRange> {
  return timed("introducing_version", () => {
    const rows = db()
      .prepare(
        `SELECT a.osv_id, a.severity, a.summary, a.published_at,
                af.introduced, af.fixed, af.valid_from, af.valid_to
         FROM affected_by af JOIN advisory a ON a.osv_id = af.osv_id
         WHERE af.pkg = ? AND a.withheld = 0 AND a.withdrawn IS NULL
         ORDER BY a.published_at DESC`,
      )
      .all(pkg) as Array<Record<string, string | null>>;

    const mapped = rows.map((r) => ({
      osvId: r.osv_id as string,
      severity: r.severity,
      summary: r.summary,
      introduced: (r.introduced as string) ?? "0",
      fixed: r.fixed,
      validFrom: r.valid_from,
      validTo: r.valid_to,
      publishedAt: r.published_at as string,
    }));
    // When a version is supplied, keep only windows that actually contain it.
    const filtered = version
      ? mapped.filter((m) => inRange(version, m.introduced, m.fixed))
      : mapped;
    return { rows: filtered };
  });
}

/** Numeric semver comparison; prerelease tags are compared lexically after the triple. */
export function cmpVersion(a: string, b: string): number {
  const parse = (v: string) => {
    const [core, pre] = v.split("-", 2);
    const nums = core.split(".").map((n) => parseInt(n, 10) || 0);
    return { nums, pre: pre ?? "" };
  };
  const pa = parse(a);
  const pb = parse(b);
  for (let i = 0; i < 3; i += 1) {
    const d = (pa.nums[i] ?? 0) - (pb.nums[i] ?? 0);
    if (d !== 0) return d < 0 ? -1 : 1;
  }
  if (pa.pre === pb.pre) return 0;
  if (!pa.pre) return 1; // a release outranks its own prerelease
  if (!pb.pre) return -1;
  return pa.pre < pb.pre ? -1 : 1;
}

export function inRange(version: string, introduced: string, fixed: string | null): boolean {
  if (introduced && introduced !== "0" && cmpVersion(version, introduced) < 0) return false;
  if (fixed && cmpVersion(version, fixed) >= 0) return false;
  return true;
}

/* ------------------------------------------------------------------ Q3 */

export interface Exposure {
  lockfileId: string;
  serviceId: string | null;
  serviceName: string | null;
  environment: string | null;
  pkg: string;
  version: string;
  validFrom: string | null;
  validTo: string | null;
}

export function exposedDuring(
  pkg: string,
  version: string,
  windowStart?: string,
  windowEnd?: string,
): Answer<Exposure> {
  return timed("exposed_during", () => {
    const clauses = ["r.pkg = ?", "r.version = ?"];
    const params: string[] = [pkg, version];
    // Interval overlap evaluated in the query, not applied to its output.
    if (windowEnd) {
      clauses.push("(r.valid_from IS NULL OR r.valid_from < ?)");
      params.push(windowEnd);
    }
    if (windowStart) {
      clauses.push("(r.valid_to IS NULL OR r.valid_to > ?)");
      params.push(windowStart);
    }
    const rows = db()
      .prepare(
        `SELECT r.lockfile_id, r.pkg, r.version, r.valid_from, r.valid_to,
                l.service_id, s.name AS service_name, s.environment
         FROM resolved_to r
         LEFT JOIN lockfile l ON l.id = r.lockfile_id
         LEFT JOIN service s ON s.id = l.service_id
         WHERE ${clauses.join(" AND ")}`,
      )
      .all(...params) as Array<Record<string, string | null>>;

    return {
      rows: rows.map((r) => ({
        lockfileId: r.lockfile_id as string,
        serviceId: r.service_id,
        serviceName: r.service_name,
        environment: r.environment,
        pkg: r.pkg as string,
        version: r.version as string,
        validFrom: r.valid_from,
        validTo: r.valid_to,
      })),
    };
  });
}

/* ------------------------------------------------------------------ Q4 */

export interface Sibling {
  pkg: string;
  handle: string;
  downloads: number | null;
  rank: number | null;
}

export function sharedMaintainers(pkg: string, limit = 200): Answer<Sibling> {
  return timed("shared_maintainer", () => {
    const rows = db()
      .prepare(
        `SELECT pb2.pkg AS other, pb1.handle, p.downloads, p.download_rank
         FROM published_by pb1
         JOIN published_by pb2 ON pb2.handle = pb1.handle AND pb2.pkg != pb1.pkg
         LEFT JOIN package p ON p.name = pb2.pkg
         WHERE pb1.pkg = ?
         ORDER BY p.download_rank IS NULL, p.download_rank
         LIMIT ?`,
      )
      .all(pkg, limit) as Array<Record<string, string | number | null>>;
    return {
      rows: rows.map((r) => ({
        pkg: r.other as string,
        handle: r.handle as string,
        downloads: (r.downloads as number) ?? null,
        rank: (r.download_rank as number) ?? null,
      })),
    };
  });
}

/* ------------------------------------------------------------------ Q5 */

export interface Typosquat {
  candidate: string;
  distance: number;
  candidateDownloads: number | null;
  flaggedMalicious: boolean;
  reports: number;
}

export function typosquats(pkg: string, limit = 60): Answer<Typosquat> {
  return timed("typosquats", () => {
    const rows = db()
      .prepare(
        `SELECT s.pkg, s.distance, p.downloads,
                m.name IS NOT NULL AS flagged, COALESCE(m.reports, 0) AS reports
         FROM similar_name s
         LEFT JOIN package p ON p.name = s.pkg
         LEFT JOIN malicious_package m ON m.name = s.pkg
         WHERE s.other = ?
         ORDER BY flagged DESC, s.distance, p.downloads IS NOT NULL, p.downloads
         LIMIT ?`,
      )
      .all(pkg, limit) as Array<Record<string, string | number | null>>;
    return {
      rows: rows.map((r) => ({
        candidate: r.pkg as string,
        distance: r.distance as number,
        candidateDownloads: (r.downloads as number) ?? null,
        flaggedMalicious: Boolean(r.flagged),
        reports: (r.reports as number) ?? 0,
      })),
      notes: ["candidate generator, not a classifier — see EVAL.md"],
    };
  });
}

/* ---------------------------------------------------------------- misc */

export interface PackageInfo {
  name: string;
  downloads: number | null;
  rank: number | null;
  versions: string[];
  latest: string | null;
  maintainers: string[];
}

export function packageInfo(pkg: string): PackageInfo | null {
  const conn = db();
  const row = conn
    .prepare("SELECT name, downloads, download_rank FROM package WHERE name = ?")
    .get(pkg) as Record<string, string | number> | undefined;
  if (!row) return null;

  const versions = (
    conn.prepare("SELECT version, is_latest FROM version WHERE pkg = ?").all(pkg) as Array<
      Record<string, string | number>
    >
  ).sort((a, b) => cmpVersion(b.version as string, a.version as string));

  const maintainers = (
    conn.prepare("SELECT handle FROM published_by WHERE pkg = ?").all(pkg) as Array<
      Record<string, string>
    >
  ).map((m) => m.handle);

  return {
    name: row.name as string,
    downloads: (row.downloads as number) ?? null,
    rank: (row.download_rank as number) ?? null,
    versions: versions.map((v) => v.version as string),
    latest: (versions.find((v) => v.is_latest === 1)?.version as string) ?? null,
    maintainers,
  };
}

export function searchPackages(prefix: string, limit = 12): string[] {
  if (!prefix) return [];
  return (
    db()
      .prepare(
        "SELECT name FROM package WHERE name LIKE ? ORDER BY download_rank LIMIT ?",
      )
      .all(`${prefix}%`, limit) as Array<Record<string, string>>
  ).map((r) => r.name);
}

export function coverageStats(): Record<string, number> {
  const conn = db();
  const one = (sql: string) => (conn.prepare(sql).get() as Record<string, number>).n;
  return {
    packages: one("SELECT COUNT(*) n FROM package"),
    versions: one("SELECT COUNT(*) n FROM version"),
    dependsOn: one("SELECT COUNT(*) n FROM depends_on"),
    advisories: one("SELECT COUNT(*) n FROM advisory"),
    maliciousPackages: one("SELECT COUNT(*) n FROM malicious_package"),
    similarNames: one("SELECT COUNT(*) n FROM similar_name"),
  };
}
