"""The six questions, as real traversals.

Each returns its answer *and* the traversal path that produced it, because in an incident the
path is the thing a responder actually needs: "you are exposed" is not actionable, "you are
exposed because service X's lockfile pinned Y@1.2.3 which depends on Z@4.5.6" is.

Every query is timed. Latency is returned with the result rather than logged, so the console
can display it per query and the eval harness can aggregate p50/p95 without a second code path.

Reverse traversal is level-batched: one SQL statement per BFS depth with all frontier packages
bound at once, rather than one statement per node. On a wide frontier that is the difference
between a handful of queries and tens of thousands.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from blastradius_ingest.store import Store

__all__ = [
    "Answer",
    "blast_radius",
    "exposed_during",
    "ingest_lockfile",
    "introducing_version",
    "measure",
    "rollup",
    "shared_maintainer_packages",
    "typosquat_candidates",
]

MAX_DEPTH = (
    12  # npm dependency chains are deep but not unbounded; guards against cycles
)


@dataclass(slots=True)
class Answer:
    """A query result plus the evidence and cost of producing it."""

    question: str
    rows: list[dict[str, Any]]
    latency_ms: float
    path: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.rows)

    def to_json(self) -> str:
        return json.dumps(
            {
                "question": self.question,
                "count": self.count,
                "latency_ms": round(self.latency_ms, 2),
                "rows": self.rows,
                "path": self.path,
                "notes": self.notes,
            }
        )


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


# --------------------------------------------------------------------------- Q1


def blast_radius(
    store: Store,
    pkg: str,
    version: str,
    *,
    max_depth: int = MAX_DEPTH,
    limit: int | None = None,
) -> Answer:
    """Q1 — transitive reverse-dependency closure of ``pkg@version``.

    Walks DEPENDS_ON backwards. A dependent only counts when the edge's ``resolved`` matches
    the version actually reached, so a dependency on the same package at a different resolved
    version is correctly excluded.
    """
    t0 = _now_ms()
    # (pkg, version) -> (depth, via_pkg, via_version)
    reached: dict[tuple[str, str], tuple[int, str, str]] = {}
    frontier: list[tuple[str, str]] = [(pkg, version)]
    path: list[dict[str, Any]] = []
    depth = 0
    truncated = False

    while frontier and depth < max_depth:
        depth += 1
        rows = _frontier_query(store, frontier)

        next_frontier: list[tuple[str, str]] = []
        for r in rows:
            key = (r["src_pkg"], r["src_version"])
            if key in reached:
                continue
            reached[key] = (depth, r["dst_pkg"], r["resolved"])
            path.append(
                {
                    "from": f"{r['src_pkg']}@{r['src_version']}",
                    "to": f"{r['dst_pkg']}@{r['resolved']}",
                    "edge": "DEPENDS_ON",
                    "range": r["range"],
                    "dep_type": r["dep_type"],
                    "depth": depth,
                }
            )
            next_frontier.append(key)
            if limit is not None and len(reached) >= limit:
                truncated = True
                break
        if truncated:
            break
        frontier = next_frontier

    notes = []
    if truncated:
        notes.append(f"truncated at limit={limit}; result is NOT the complete closure")
    if depth >= max_depth and frontier:
        notes.append(
            f"stopped at max_depth={max_depth}; deeper dependents not included"
        )

    rows_out = [
        {"pkg": p, "version": v, "depth": d, "via": f"{vp}@{vv}"}
        for (p, v), (d, vp, vv) in sorted(reached.items(), key=lambda kv: kv[1][0])
    ]
    return Answer(
        question=f"blast_radius({pkg}@{version})",
        rows=rows_out,
        latency_ms=_now_ms() - t0,
        path=path,
        notes=notes,
    )


def _frontier_query(store: Store, frontier: Sequence[tuple[str, str]]) -> list[Any]:
    """One statement per BFS level, with the whole frontier bound at once."""
    if not frontier:
        return []
    clauses = " OR ".join("(d.dst_pkg = ? AND d.resolved = ?)" for _ in frontier)
    params: list[str] = []
    for p, v in frontier:
        params.extend((p, v))
    sql = (
        "SELECT d.src_pkg, d.src_version, d.dst_pkg, d.resolved, d.dep_type, d.range "
        f"FROM depends_on d WHERE {clauses}"
    )
    return list(store.conn.execute(sql, params))


# --------------------------------------------------------------------------- Q2


def introducing_version(store: Store, osv_id: str) -> Answer:
    """Q2 — which version introduced the vulnerability.

    Reads the ``introduced`` bound off AFFECTED_BY. OSV encodes disjoint windows, so a package
    can legitimately have several introducing versions (``@solana/web3.js`` has 92 windows for
    one advisory); all are returned rather than collapsing to a single min.
    """
    t0 = _now_ms()
    rows = [
        {
            "pkg": r["pkg"],
            "introduced": r["introduced"],
            "fixed": r["fixed"],
            "introduced_published_at": r["published_at"],
        }
        for r in store.conn.execute(
            "SELECT af.pkg, af.introduced, af.fixed, v.published_at "
            "FROM affected_by af "
            "LEFT JOIN version v ON v.pkg = af.pkg AND v.version = af.introduced "
            "JOIN advisory a ON a.osv_id = af.osv_id "
            "WHERE af.osv_id = ? AND a.withheld = 0 AND a.withdrawn IS NULL "
            "ORDER BY af.pkg, af.introduced",
            (osv_id,),
        )
    ]
    notes = []
    if not rows:
        notes.append(
            "no usable AFFECTED_BY rows — advisory is held out, withdrawn, or not ingested"
        )
    return Answer(
        question=f"introducing_version({osv_id})",
        rows=rows,
        latency_ms=_now_ms() - t0,
        path=[{"edge": "AFFECTED_BY", "osv_id": osv_id}],
        notes=notes,
    )


# --------------------------------------------------------------------------- Q3


def exposed_during(
    store: Store,
    pkg: str,
    version: str,
    window_start: str | None = None,
    window_end: str | None = None,
) -> Answer:
    """Q3 — which lockfiles/services resolved this version *while it was live*.

    The interval predicate is evaluated in the query, not applied to its output. A service that
    pinned the bad version at 09:01 and reverted at 09:05 was exposed, and is invisible to any
    present-tense view of the graph. See SCHEMA.md §3.
    """
    t0 = _now_ms()
    sql = [
        "SELECT r.lockfile_id, r.pkg, r.version, r.resolved_at, r.valid_from, r.valid_to,",
        "       l.service_id, s.name AS service_name, s.environment",
        "FROM resolved_to r",
        "LEFT JOIN lockfile l ON l.id = r.lockfile_id",
        "LEFT JOIN service s ON s.id = l.service_id",
        "WHERE r.pkg = ? AND r.version = ?",
    ]
    params: list[Any] = [pkg, version]
    if window_end is not None:
        sql.append("AND (r.valid_from IS NULL OR r.valid_from < ?)")
        params.append(window_end)
    if window_start is not None:
        sql.append("AND (r.valid_to IS NULL OR r.valid_to > ?)")
        params.append(window_start)

    rows = [dict(r) for r in store.conn.execute(" ".join(sql), params)]
    return Answer(
        question=f"exposed_during({pkg}@{version}, {window_start}..{window_end})",
        rows=rows,
        latency_ms=_now_ms() - t0,
        path=[
            {
                "edge": "RESOLVED_TO",
                "window": [window_start, window_end],
                "interval": True,
            }
        ],
    )


# --------------------------------------------------------------------------- Q4


def shared_maintainer_packages(store: Store, pkg: str) -> Answer:
    """Q4 — packages sharing a maintainer with this one.

    An exact join through PUBLISHED_BY. Note this is the query where semantic similarity is
    actively misleading: a maintainer's packages are usually unrelated in purpose.
    """
    t0 = _now_ms()
    rows = [
        {
            "pkg": r["other"],
            "handle": r["handle"],
            "downloads": r["downloads"],
            "download_rank": r["download_rank"],
        }
        for r in store.conn.execute(
            "SELECT pb2.pkg AS other, pb1.handle, p.downloads, p.download_rank "
            "FROM published_by pb1 "
            "JOIN published_by pb2 ON pb2.handle = pb1.handle AND pb2.pkg != pb1.pkg "
            "LEFT JOIN package p ON p.name = pb2.pkg "
            "WHERE pb1.pkg = ? "
            "ORDER BY p.download_rank IS NULL, p.download_rank",
            (pkg,),
        )
    ]
    return Answer(
        question=f"shared_maintainer_packages({pkg})",
        rows=rows,
        latency_ms=_now_ms() - t0,
        path=[{"edge": "PUBLISHED_BY", "pkg": pkg}],
    )


# --------------------------------------------------------------------------- Q5


def typosquat_candidates(store: Store, pkg: str, *, max_distance: int = 2) -> Answer:
    """Q5 — likely typosquats near this name.

    Returns SIMILAR_NAME neighbours ordered by edit distance then by how obscure the neighbour
    is. This is a candidate generator, not a classifier — see EVAL.md and STATE.md B5. Where a
    neighbour is independently flagged malicious by OSV, that is surfaced explicitly.
    """
    t0 = _now_ms()
    rows = [
        {
            "candidate": r["pkg"],
            "near": r["other"],
            "distance": r["distance"],
            "candidate_downloads": r["downloads"],
            "flagged_malicious": bool(r["flagged"]),
        }
        for r in store.conn.execute(
            "SELECT s.pkg, s.other, s.distance, p.downloads, "
            "  EXISTS(SELECT 1 FROM affected_by af JOIN advisory a USING(osv_id) "
            "         WHERE af.pkg = s.pkg AND a.osv_id LIKE 'MAL-%') AS flagged "
            "FROM similar_name s "
            "LEFT JOIN package p ON p.name = s.pkg "
            "WHERE s.other = ? AND s.distance <= ? "
            "ORDER BY s.distance, p.downloads IS NOT NULL, p.downloads",
            (pkg, max_distance),
        )
    ]
    return Answer(
        question=f"typosquat_candidates({pkg})",
        rows=rows,
        latency_ms=_now_ms() - t0,
        path=[{"edge": "SIMILAR_NAME", "target": pkg}],
        notes=["candidate generator, not a classifier; see EVAL.md §1"],
    )


# --------------------------------------------------------------------------- Q6


def rollup(
    store: Store,
    pkg: str,
    version: str,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    max_depth: int = MAX_DEPTH,
) -> Answer:
    """Q6 — the full picture: services exposed, depth, and first-exposure time.

    Composes Q1 and Q3: the closure says which packages carry the compromise, the interval
    edges say which lockfiles actually resolved it and when. First exposure is the earliest
    ``valid_from`` across the resolving edges, which is a property of the traversal and exists
    nowhere in any single node.
    """
    t0 = _now_ms()
    radius = blast_radius(store, pkg, version, max_depth=max_depth)
    affected = {(r["pkg"], r["version"]) for r in radius.rows}
    affected.add((pkg, version))

    services: dict[str, dict[str, Any]] = {}
    for p, v in affected:
        exposure = exposed_during(store, p, v, window_start, window_end)
        for row in exposure.rows:
            sid = row.get("service_id") or row.get("lockfile_id") or "(unattributed)"
            depth = next(
                (
                    r["depth"]
                    for r in radius.rows
                    if r["pkg"] == p and r["version"] == v
                ),
                0,
            )
            entry = services.setdefault(
                sid,
                {
                    "service_id": sid,
                    "service_name": row.get("service_name"),
                    "environment": row.get("environment"),
                    "min_depth": depth,
                    "first_exposure": row.get("valid_from"),
                    "via": [],
                },
            )
            entry["min_depth"] = min(entry["min_depth"], depth)
            first = entry["first_exposure"]
            candidate = row.get("valid_from")
            if candidate and (first is None or candidate < first):
                entry["first_exposure"] = candidate
            entry["via"].append(f"{p}@{v}")

    return Answer(
        question=f"rollup({pkg}@{version})",
        rows=sorted(
            services.values(), key=lambda s: (s["min_depth"], str(s["service_id"]))
        ),
        latency_ms=_now_ms() - t0,
        path=radius.path,
        notes=radius.notes + [f"closure size: {len(affected)} package-versions"],
    )


# ---------------------------------------------------------------- lockfile intake


def ingest_lockfile(
    store: Store,
    lockfile_json: dict[str, Any],
    *,
    service_id: str,
    service_name: str,
    lockfile_id: str,
    observed_at: str | None = None,
    valid_from: str | None = None,
    environment: str = "production",
) -> dict[str, int]:
    """Instantiate Service / Lockfile / RESOLVED_TO from a real ``package-lock.json``.

    Supports lockfileVersion 2 and 3 (the ``packages`` map) and falls back to the v1
    ``dependencies`` tree. Every resolution becomes an interval edge opening at ``valid_from``,
    which is what makes "your exposure" answerable over a time window instead of only now.
    """
    stamp = observed_at or datetime.now().astimezone().isoformat()
    opened = valid_from or stamp

    entries: list[tuple[str, str]] = []
    packages = lockfile_json.get("packages")
    if isinstance(packages, dict):
        for path, meta in packages.items():
            if not path or not isinstance(meta, dict):
                continue  # "" is the root project itself
            name = meta.get("name") or path.split("node_modules/")[-1]
            ver = meta.get("version")
            if name and ver:
                entries.append((str(name), str(ver)))
    else:

        def walk(tree: dict[str, Any]) -> None:
            for name, meta in tree.items():
                if not isinstance(meta, dict):
                    continue
                ver = meta.get("version")
                if ver:
                    entries.append((str(name), str(ver)))
                nested = meta.get("dependencies")
                if isinstance(nested, dict):
                    walk(nested)

        deps = lockfile_json.get("dependencies")
        if isinstance(deps, dict):
            walk(deps)

    store.conn.execute(
        "INSERT INTO service(id, name, owner, environment) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name = excluded.name",
        (service_id, service_name, None, environment),
    )
    store.conn.execute(
        "INSERT INTO lockfile(id, service_id, lockfile_version, entry_count, parsed_at) "
        "VALUES(?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET entry_count = excluded.entry_count",
        (
            lockfile_id,
            service_id,
            lockfile_json.get("lockfileVersion"),
            len(entries),
            stamp,
        ),
    )
    store.conn.executemany(
        "INSERT INTO resolved_to(lockfile_id, pkg, version, resolved_at, valid_from, valid_to) "
        "VALUES(?, ?, ?, ?, ?, NULL) "
        "ON CONFLICT(lockfile_id, pkg, version, valid_from) DO NOTHING",
        [(lockfile_id, n, v, stamp, opened) for n, v in entries],
    )
    store.commit()
    return {"entries": len(entries), "distinct": len({e[0] for e in entries})}


def measure(fn: Any, *args: Any, runs: int = 20, **kwargs: Any) -> dict[str, float]:
    """Run a query repeatedly and return p50/p95/mean latency in ms."""
    samples: list[float] = []
    for _ in range(runs):
        samples.append(fn(*args, **kwargs).latency_ms)
    samples.sort()
    return {
        "runs": float(runs),
        "p50": samples[len(samples) // 2],
        "p95": samples[min(len(samples) - 1, int(len(samples) * 0.95))],
        "mean": sum(samples) / len(samples),
        "max": samples[-1],
    }
