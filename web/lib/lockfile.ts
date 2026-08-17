/**
 * package-lock.json intake — this is what makes "your exposure" real rather than hypothetical.
 *
 * Supports lockfileVersion 1 (nested `dependencies` tree) and 2/3 (flat `packages` map).
 * Uploaded lockfiles are held in memory for the request only; nothing is written to the
 * shipped read-only database and nothing is persisted between requests.
 */

export interface LockEntry {
  name: string;
  version: string;
}

export interface ParsedLockfile {
  serviceName: string;
  lockfileVersion: number | null;
  entries: LockEntry[];
}

interface LockV1Node {
  version?: string;
  dependencies?: Record<string, LockV1Node>;
}

interface LockV2Node {
  name?: string;
  version?: string;
}

export function parseLockfile(raw: unknown, fallbackName = "uploaded-service"): ParsedLockfile {
  if (typeof raw !== "object" || raw === null) {
    throw new Error("not a JSON object");
  }
  const doc = raw as Record<string, unknown>;
  const entries: LockEntry[] = [];
  const seen = new Set<string>();

  const push = (name: string, version: string) => {
    const key = `${name}@${version}`;
    if (!seen.has(key)) {
      seen.add(key);
      entries.push({ name, version });
    }
  };

  const packages = doc.packages;
  if (packages && typeof packages === "object") {
    // v2/v3: keys are paths like "node_modules/foo" or "" for the root project.
    for (const [p, metaRaw] of Object.entries(packages as Record<string, unknown>)) {
      if (!p || typeof metaRaw !== "object" || metaRaw === null) continue;
      const meta = metaRaw as LockV2Node;
      const name = meta.name ?? p.split("node_modules/").pop();
      if (name && meta.version) push(name, meta.version);
    }
  } else if (doc.dependencies && typeof doc.dependencies === "object") {
    // v1: recursive tree.
    const walk = (tree: Record<string, LockV1Node>) => {
      for (const [name, meta] of Object.entries(tree)) {
        if (!meta || typeof meta !== "object") continue;
        if (meta.version) push(name, meta.version);
        if (meta.dependencies) walk(meta.dependencies);
      }
    };
    walk(doc.dependencies as Record<string, LockV1Node>);
  } else {
    throw new Error("no `packages` or `dependencies` — is this a package-lock.json?");
  }

  const rootName =
    (doc.name as string | undefined) ??
    ((packages as Record<string, LockV2Node> | undefined)?.[""]?.name ?? fallbackName);

  return {
    serviceName: rootName,
    lockfileVersion: (doc.lockfileVersion as number | undefined) ?? null,
    entries,
  };
}
