/**
 * Q6 — the rollup, composed from the closure and the uploaded lockfile.
 *
 * Depth and first-exposure are properties of the *traversal*, not of any node: depth is
 * shortest-path length from the compromised version, and first exposure is the earliest point
 * at which a lockfile in the blast radius resolved something inside it. Neither exists
 * anywhere in the graph to be looked up — which is the whole argument in SCHEMA.md §4.
 */
import {
  type AffectedRange,
  type Hop,
  type PathEdge,
  advisoriesFor,
  blastRadius,
  inRange,
  sharedMaintainers,
  typosquats,
} from "./graph";
import type { ParsedLockfile } from "./lockfile";

export interface ExposedService {
  serviceName: string;
  viaPackage: string;
  viaVersion: string;
  depth: number;
  chain: string[];
}

export interface IncidentReport {
  subject: { pkg: string; version: string };
  closure: Hop[];
  path: PathEdge[];
  advisories: AffectedRange[];
  exposedServices: ExposedService[];
  siblings: { pkg: string; handle: string; rank: number | null }[];
  squats: { candidate: string; distance: number; flaggedMalicious: boolean }[];
  timings: Record<string, number>;
  notes: string[];
  coverage: { closureSize: number; maxDepth: number };
}

/**
 * Which packages in the blast radius does this lockfile actually resolve, and how deep?
 *
 * The compromised package itself counts at depth 0 — a service that pins it directly is the
 * most exposed case, not an edge case.
 */
export function exposureFromLockfile(
  lock: ParsedLockfile,
  subject: { pkg: string; version: string },
  closure: Hop[],
  path: PathEdge[],
): ExposedService[] {
  const inRadius = new Map<string, number>();
  inRadius.set(`${subject.pkg}@${subject.version}`, 0);
  for (const hop of closure) inRadius.set(`${hop.pkg}@${hop.version}`, hop.depth);

  const parent = new Map<string, string>();
  for (const e of path) parent.set(e.from, e.to);

  const hits: ExposedService[] = [];
  for (const entry of lock.entries) {
    const key = `${entry.name}@${entry.version}`;
    const depth = inRadius.get(key);
    if (depth === undefined) continue;

    // Walk the parent chain back to the compromised package — the proof for this service.
    const chain: string[] = [key];
    let cursor = key;
    for (let i = 0; i < 24; i += 1) {
      const nxt = parent.get(cursor);
      if (!nxt) break;
      chain.push(nxt);
      cursor = nxt;
    }
    hits.push({
      serviceName: lock.serviceName,
      viaPackage: entry.name,
      viaVersion: entry.version,
      depth,
      chain,
    });
  }
  return hits.sort((a, b) => a.depth - b.depth);
}

export function buildIncident(
  pkg: string,
  version: string,
  lock: ParsedLockfile | null,
  opts: { limit?: number } = {},
): IncidentReport {
  const radius = blastRadius(pkg, version, { limit: opts.limit });
  const adv = advisoriesFor(pkg, version);
  const sib = sharedMaintainers(pkg, 60);
  const squat = typosquats(pkg, 40);

  const exposed = lock
    ? exposureFromLockfile(lock, { pkg, version }, radius.rows, radius.path)
    : [];

  const notes = [...radius.notes];
  if (!lock) notes.push("no lockfile uploaded — service exposure is not evaluated");

  return {
    subject: { pkg, version },
    closure: radius.rows,
    path: radius.path,
    advisories: adv.rows,
    exposedServices: exposed,
    siblings: sib.rows.map((s) => ({ pkg: s.pkg, handle: s.handle, rank: s.rank })),
    squats: squat.rows.map((s) => ({
      candidate: s.candidate,
      distance: s.distance,
      flaggedMalicious: s.flaggedMalicious,
    })),
    timings: {
      blastRadius: radius.latencyMs,
      advisories: adv.latencyMs,
      maintainers: sib.latencyMs,
      typosquats: squat.latencyMs,
    },
    notes,
    coverage: { closureSize: radius.rows.length, maxDepth: radius.maxDepth },
  };
}

/**
 * The timeline scrubber's data: for each advisory window touching this version, the instants
 * at which the exposure set changes. Returned as real timestamps so the UI scrubs over
 * measured history rather than an invented axis.
 */
export function exposureTimeline(
  advisories: AffectedRange[],
  version: string,
): { from: string | null; to: string | null; osvId: string; severity: string | null }[] {
  return advisories
    .filter((a) => inRange(version, a.introduced, a.fixed))
    .map((a) => ({
      from: a.validFrom,
      to: a.validTo,
      osvId: a.osvId,
      severity: a.severity,
    }))
    .filter((w) => w.from !== null)
    .sort((a, b) => (a.from! < b.from! ? -1 : 1));
}
