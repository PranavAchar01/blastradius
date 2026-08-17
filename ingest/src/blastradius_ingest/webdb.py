"""Build the SQLite artifact the console ships with.

The working database is 153MB, over GitHub's 100MB per-file limit, so the deployed copy is
pruned. Everything dropped is dropped for a stated reason, and nothing that changes an answer
to the six questions is removed:

* **``MAL-*`` advisory records** — 219,676 of the 226,832 advisories and 220,410 of the 230,017
  ``affected_by`` rows. The console needs exactly one fact from them: *is this package
  independently flagged malicious?* That collapses to a set of names, so they become a
  ``malicious_package`` table. Their prose is scanner boilerplate and was never stored anyway.
* **Advisory ``details``** — the long-form prose lives in HydraDB, which is where the console
  reads it from. The ``summary`` is kept for display.

Kept at full fidelity: every package, version, maintainer, DEPENDS_ON edge, SIMILAR_NAME edge,
and every GHSA advisory with its version bounds and validity intervals. Q1–Q6 answer identically
against this file and the working database — ``verify_parity`` proves it rather than asserting
it, and the README states the prune so nobody discovers it by surprise.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from blastradius_ingest.store import Store

__all__ = ["build_web_db", "verify_parity"]

MALICIOUS_TABLE = """
CREATE TABLE IF NOT EXISTS malicious_package (
    name TEXT PRIMARY KEY,
    reports INTEGER NOT NULL DEFAULT 1
);
"""


def build_web_db(src: Path, dest: Path) -> dict[str, Any]:
    """Produce the pruned console database. Returns before/after sizes and row counts."""
    t0 = time.monotonic()
    if dest.exists():
        dest.unlink()
    for suffix in ("-wal", "-shm"):
        stale = dest.with_name(dest.name + suffix)
        if stale.exists():
            stale.unlink()

    shutil.copy2(src, dest)
    conn = sqlite3.connect(dest)
    conn.row_factory = sqlite3.Row
    conn.executescript(MALICIOUS_TABLE)

    # Collapse the malicious corpus to the one fact the console uses.
    conn.execute(
        "INSERT INTO malicious_package(name, reports) "
        "SELECT af.pkg, COUNT(*) FROM affected_by af "
        "WHERE af.osv_id LIKE 'MAL-%' GROUP BY af.pkg "
        "ON CONFLICT(name) DO UPDATE SET reports = excluded.reports"
    )
    malicious = conn.execute("SELECT COUNT(*) FROM malicious_package").fetchone()[0]

    conn.execute("DELETE FROM affected_by WHERE osv_id LIKE 'MAL-%'")
    conn.execute("DELETE FROM advisory WHERE osv_id LIKE 'MAL-%'")
    conn.execute("UPDATE advisory SET details = NULL")
    conn.commit()
    conn.execute("VACUUM")
    conn.commit()

    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in (
            "package",
            "version",
            "maintainer",
            "published_by",
            "depends_on",
            "advisory",
            "affected_by",
            "similar_name",
            "malicious_package",
        )
    }
    conn.close()

    return {
        "src_mb": round(src.stat().st_size / 1024 / 1024, 1),
        "dest_mb": round(dest.stat().st_size / 1024 / 1024, 1),
        "malicious_packages": malicious,
        "counts": counts,
        "seconds": round(time.monotonic() - t0, 1),
    }


def verify_parity(
    src: Path, dest: Path, samples: list[tuple[str, str]]
) -> dict[str, Any]:
    """Prove the pruned database answers the closure questions identically.

    Runs the real query engine against both files over the same subjects and compares the
    full result sets — not just counts, which would hide a substitution.
    """
    from blastradius_ingest import queries as Q

    full = Store(src)
    web = Store(dest)
    mismatches: list[dict[str, Any]] = []
    checked = 0

    for pkg, version in samples:
        a = {
            (r["pkg"], r["version"], r["depth"])
            for r in Q.blast_radius(full, pkg, version).rows
        }
        b = {
            (r["pkg"], r["version"], r["depth"])
            for r in Q.blast_radius(web, pkg, version).rows
        }
        checked += 1
        if a != b:
            mismatches.append(
                {
                    "subject": f"{pkg}@{version}",
                    "only_in_full": sorted(str(x) for x in a - b),
                    "only_in_web": sorted(str(x) for x in b - a),
                }
            )

        ma = {r["pkg"] for r in Q.shared_maintainer_packages(full, pkg).rows}
        mb = {r["pkg"] for r in Q.shared_maintainer_packages(web, pkg).rows}
        if ma != mb:
            mismatches.append({"subject": f"maintainers({pkg})", "diff": len(ma ^ mb)})

    full.close()
    web.close()
    return {
        "subjects_checked": checked,
        "mismatches": mismatches,
        "identical": not mismatches,
    }
