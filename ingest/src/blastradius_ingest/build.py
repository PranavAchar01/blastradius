"""Assemble the SQLite graph from the staged NDJSON files.

Input is whatever the registry stages have written so far; this step is pure local
computation and can be re-run at any time without touching the network.

The coverage cap is applied *here*, once, and recorded in ``ingest_meta`` so the number in
EVAL.md and the number in the database can never drift apart.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from pathlib import Path

from blastradius_ingest.registry import Paths, read_ndjson
from blastradius_ingest.store import Store

__all__ = [
    "backfill_affected_intervals",
    "build_graph",
    "load_packuments",
    "rank_packages",
    "resolve_all_versions",
]

_DEP_KINDS = (("deps", "prod"), ("peer", "peer"), ("optional", "optional"))


def freeze_ranking(paths: Paths, top_n: int) -> int:
    """Compute the coverage cap once and write it to ``ranked.tsv``.

    Ties break by name so the cap is deterministic across runs; without that the package set
    drifts between ingests and the eval stops being reproducible.
    """
    ranked = sorted(
        (
            (int(r.get("downloads") or 0), str(r["name"]))
            for r in read_ndjson(paths.downloads)
        ),
        key=lambda t: (-t[0], t[1]),
    )[:top_n]
    with paths.ranked.open("w") as fh:
        for dl, name in ranked:
            fh.write(f"{name}\t{dl}\n")
    return len(ranked)


def rank_packages(paths: Paths, top_n: int) -> list[tuple[str, int, int]]:
    """Read the frozen ranking as ``(name, downloads, rank)``.

    Reads ``ranked.tsv``, never ``downloads.ndjson`` — see ``Paths.ranked`` for why.
    """
    if not paths.ranked.exists():
        raise FileNotFoundError(
            f"{paths.ranked} not found — run `blastradius rank` to freeze the coverage cap"
        )
    out: list[tuple[str, int, int]] = []
    with paths.ranked.open() as fh:
        for i, line in enumerate(fh):
            if i >= top_n:
                break
            name, _, dl = line.rstrip("\n").partition("\t")
            if name:
                out.append((name, int(dl or 0), i + 1))
    return out


def _batched(
    rows: Iterable[tuple[object, ...]], size: int
) -> Iterator[list[tuple[object, ...]]]:
    batch: list[tuple[object, ...]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def build_graph(
    store: Store, paths: Paths, *, top_n: int, batch: int = 10_000
) -> dict[str, int]:
    """Load packages, versions, maintainers and DEPENDS_ON edges into the store."""
    t0 = time.monotonic()
    ranked = rank_packages(paths, top_n)
    in_scope = {name for name, _, _ in ranked}
    stats = {
        "packages": len(ranked),
        "versions": 0,
        "maintainers": 0,
        "published_by": 0,
        "depends_on": 0,
        "dangling_deps": 0,
    }

    store.upsert_packages([(n, dl, rank) for n, dl, rank in ranked])
    store.commit()

    maintainers: set[str] = set()
    version_rows: list[tuple[str, str, str | None, int]] = []
    dep_rows: list[tuple[str, str, str, str | None, str | None, str]] = []
    pub_rows: list[tuple[str, str]] = []

    def flush() -> None:
        if version_rows:
            store.upsert_versions(version_rows)
            version_rows.clear()
        if dep_rows:
            store.upsert_depends_on(dep_rows)
            dep_rows.clear()
        if pub_rows:
            store.upsert_published_by(pub_rows)
            pub_rows.clear()
        store.commit()

    for rec in read_ndjson(paths.latest):
        name = str(rec.get("name") or "")
        if not name or name not in in_scope or rec.get("missing"):
            continue
        version = rec.get("version")
        if not version:
            continue

        version_rows.append((name, str(version), None, 1))
        stats["versions"] += 1

        for handle in rec.get("maintainers") or []:
            if handle:
                maintainers.add(str(handle))
                pub_rows.append((name, str(handle)))
                stats["published_by"] += 1

        for field, dep_type in _DEP_KINDS:
            for dst, rng in (rec.get(field) or {}).items():
                # The resolved version is not known from the manifest alone; it is filled in
                # by the lockfile pass, which is where resolution actually happens. Storing
                # the declared range without a fabricated resolution keeps DEPENDS_ON honest.
                dep_rows.append(
                    (name, str(version), str(dst), str(rng), None, dep_type)
                )
                stats["depends_on"] += 1
                if dst not in in_scope:
                    stats["dangling_deps"] += 1

        if len(dep_rows) >= batch:
            flush()

    flush()
    store.upsert_maintainers(sorted(maintainers))
    store.commit()
    stats["maintainers"] = len(maintainers)

    store.set_meta("coverage_top_n", top_n)
    store.set_meta("coverage_packages", len(ranked))
    store.set_meta("build_seconds", round(time.monotonic() - t0, 1))
    if ranked:
        store.set_meta("coverage_min_downloads", ranked[-1][1])
        store.set_meta("coverage_max_downloads", ranked[0][1])
    return stats


def resolve_latest_versions(store: Store, *, batch: int = 20_000) -> dict[str, int]:
    """Resolve each DEPENDS_ON edge to the target's latest version **only if the declared
    range actually admits it**.

    An earlier version assigned ``resolved = latest`` unconditionally, which is simply false:
    ``chalk`` declares ``ansi-styles: ^4.1.0``, and calling that "resolved to 7.0.0" both
    invents a dependency that does not exist and hides the real one. Blast radius computed on
    top of that is wrong in both directions.

    Edges whose range excludes the latest version are left ``NULL`` — the honest answer given
    we only carry latest-version manifests. Their true resolution comes from an uploaded
    lockfile via RESOLVED_TO, which is where resolution actually happens in the real world.
    """
    import nodesemver

    latest = {
        row["pkg"]: row["version"]
        for row in store.conn.execute(
            "SELECT pkg, version FROM version WHERE is_latest = 1"
        )
    }
    stats = {"resolved": 0, "range_excludes_latest": 0, "target_out_of_scope": 0}
    updates: list[tuple[str, str, str, str, str]] = []

    for row in store.conn.execute(
        "SELECT src_pkg, src_version, dst_pkg, dep_type, range FROM depends_on "
        "WHERE resolved IS NULL"
    ).fetchall():
        target = latest.get(row["dst_pkg"])
        if target is None:
            stats["target_out_of_scope"] += 1
            continue
        rng = row["range"] or "*"
        try:
            ok = nodesemver.satisfies(target, rng, loose=True)
        except Exception:  # noqa: BLE001 - nodesemver raises bare ValueError/TypeError/AttributeError
            # Non-semver specifiers (git URLs, "file:", "workspace:", dist-tags) are not
            # version ranges at all; leaving them unresolved is correct.
            ok = False
        if not ok:
            stats["range_excludes_latest"] += 1
            continue
        updates.append(
            (
                target,
                row["src_pkg"],
                row["src_version"],
                row["dst_pkg"],
                row["dep_type"],
            )
        )
        stats["resolved"] += 1
        if len(updates) >= batch:
            store.conn.executemany(
                "UPDATE depends_on SET resolved = ? WHERE src_pkg = ? AND src_version = ? "
                "AND dst_pkg = ? AND dep_type = ?",
                updates,
            )
            updates.clear()

    if updates:
        store.conn.executemany(
            "UPDATE depends_on SET resolved = ? WHERE src_pkg = ? AND src_version = ? "
            "AND dst_pkg = ? AND dep_type = ?",
            updates,
        )
    store.commit()
    return stats


def load_packuments(
    store: Store, paths: Paths, *, batch: int = 20_000
) -> dict[str, int]:
    """Load full version histories from ``packuments.ndjson``.

    The full packument is the only source for per-version publish dates (``time``), and it is
    also what makes non-latest resolution possible — see ``resolve_all_versions``. It averages
    ~242KB, so it is fetched only for packages that actually appear in an advisory.
    """
    rows: list[tuple[str, str, str | None, int]] = []
    stats = {"packages": 0, "versions": 0}

    for rec in read_ndjson(paths.packuments):
        if rec.get("missing"):
            continue
        name = rec["name"]
        stats["packages"] += 1
        for version, published in (rec.get("times") or {}).items():
            rows.append((name, version, published, 0))
            stats["versions"] += 1
            if len(rows) >= batch:
                store.upsert_versions(rows)
                rows.clear()
    if rows:
        store.upsert_versions(rows)
    store.commit()
    return stats


def resolve_all_versions(store: Store, *, batch: int = 20_000) -> dict[str, int]:
    """Resolve DEPENDS_ON edges against **every** known version of the target, not just latest.

    This is what npm itself does: pick the highest version satisfying the declared range.
    Resolving only against latest makes ``acorn-node -> acorn@^7.0.0`` unresolvable whenever
    ``acorn`` has moved to 8.x, which in turn makes the blast radius of *any* non-latest
    version come back empty — the wrong answer for precisely the incidents that matter.

    Only runs where multi-version history exists (i.e. packages whose packument was fetched),
    so it strictly adds resolutions and never overwrites one made from a lockfile.
    """
    import nodesemver

    versions: dict[str, list[str]] = {}
    for row in store.conn.execute("SELECT pkg, version FROM version"):
        versions.setdefault(row["pkg"], []).append(row["version"])

    stats = {"resolved": 0, "no_satisfying_version": 0, "target_out_of_scope": 0}
    updates: list[tuple[str, str, str, str, str]] = []

    for row in store.conn.execute(
        "SELECT src_pkg, src_version, dst_pkg, dep_type, range FROM depends_on "
        "WHERE resolved IS NULL"
    ).fetchall():
        candidates = versions.get(row["dst_pkg"])
        if not candidates:
            stats["target_out_of_scope"] += 1
            continue
        try:
            best = nodesemver.max_satisfying(
                candidates, row["range"] or "*", loose=True
            )
        except Exception:  # noqa: BLE001 - non-semver specifiers (git:, file:, workspace:)
            best = None
        if best is None:
            stats["no_satisfying_version"] += 1
            continue
        updates.append(
            (
                str(best),
                row["src_pkg"],
                row["src_version"],
                row["dst_pkg"],
                row["dep_type"],
            )
        )
        stats["resolved"] += 1
        if len(updates) >= batch:
            store.conn.executemany(
                "UPDATE depends_on SET resolved = ? WHERE src_pkg = ? AND src_version = ? "
                "AND dst_pkg = ? AND dep_type = ?",
                updates,
            )
            updates.clear()

    if updates:
        store.conn.executemany(
            "UPDATE depends_on SET resolved = ? WHERE src_pkg = ? AND src_version = ? "
            "AND dst_pkg = ? AND dep_type = ?",
            updates,
        )
    store.commit()
    return stats


def backfill_affected_intervals(store: Store) -> dict[str, int]:
    """Fill AFFECTED_BY validity intervals from real version publish dates.

    ``valid_from`` is when the *introduced* version was published — when the package actually
    became vulnerable — not when the advisory was disclosed. ``valid_to`` is when the *fixed*
    version shipped. That distinction is what lets a lockfile from before the GHSA existed
    still test positive. Rows whose bounds have no known publish date are left NULL rather
    than guessed from the disclosure date.
    """
    updated = store.conn.execute(
        "UPDATE affected_by SET "
        "  valid_from = (SELECT v.published_at FROM version v "
        "                WHERE v.pkg = affected_by.pkg AND v.version = affected_by.introduced), "
        "  valid_to = (SELECT v.published_at FROM version v "
        "              WHERE v.pkg = affected_by.pkg AND v.version = affected_by.fixed) "
        "WHERE valid_from IS NULL OR valid_to IS NULL"
    ).rowcount
    store.commit()
    filled = store.conn.execute(
        "SELECT COUNT(*) FROM affected_by WHERE valid_from IS NOT NULL"
    ).fetchone()[0]
    return {"rows_touched": updated, "with_valid_from": filled}


def load_fixture_paths(root: Path) -> Paths:
    return Paths(root)
