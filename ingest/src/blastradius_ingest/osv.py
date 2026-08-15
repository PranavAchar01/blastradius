"""OSV / GitHub Advisory Database ingest for the npm ecosystem.

Source: ``osv-vulnerabilities.storage.googleapis.com/npm/all.zip`` — 209MB, 226,833 records
as of the Aug 2026 pull. Composition measured, not assumed:

===========  =======  ==========================================================
prefix       count    what it is
===========  =======  ==========================================================
``MAL-``     219,676  malicious-package reports (typosquats, credential
                      stealers, protestware). Almost all name packages with
                      negligible download counts, by construction.
``GHSA-``      7,155  conventional advisories against real, often popular
                      packages. These are what produce a meaningful blast radius.
``EEF-``/
``GSD-``           2  aggregated cross-ecosystem records.
===========  =======  ==========================================================

Two things this module has to get right:

1. **Ecosystem filtering per affected entry, not per file.** Records in the npm bucket can
   carry affected entries for other ecosystems — the ``EEF-CVE-2026-56812`` record in this
   very archive is filed under npm but its affected package is ``phoenix`` on Hex. Filtering
   at the file level would silently import Hex/PyPI packages as npm ones.
2. **The holdout must not leak.** Advisories published on or after ``HOLDOUT_CUTOFF`` are
   stored with ``withheld = 1`` and are excluded from every query path; they exist only so
   the eval harness has ground truth it never trained the graph on.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from blastradius_ingest.store import Store

__all__ = [
    "HOLDOUT_CUTOFF",
    "OSV_NPM_ALL",
    "Advisory",
    "AffectedRange",
    "download_osv",
    "iter_npm_advisories",
    "load_advisories",
]

OSV_NPM_ALL = "https://osv-vulnerabilities.storage.googleapis.com/npm/all.zip"

# Everything published on or after this date is the eval set and must not influence the
# graph. Chosen because it leaves 12,933 npm advisories held out (a real eval set) while
# keeping the ~214k earlier records available to build from. Recorded in EVAL.md.
HOLDOUT_CUTOFF = "2026-01-01"

# Advisory prose is stored for display only; capped so 226k records stay shippable.
DETAILS_MAX_CHARS = 2_000

# GHSA labels; CVSS vectors are kept verbatim so nothing is invented.
_SEVERITY_ORDER = {"LOW": 1, "MODERATE": 2, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


@dataclass(frozen=True, slots=True)
class AffectedRange:
    pkg: str
    introduced: str | None
    fixed: str | None


@dataclass(frozen=True, slots=True)
class Advisory:
    osv_id: str
    aliases: list[str]
    severity: str | None
    cvss: str | None
    published_at: str
    modified_at: str | None
    summary: str | None
    details: str | None
    withdrawn: str | None
    affected: list[AffectedRange]

    @property
    def is_malicious_report(self) -> bool:
        return self.osv_id.startswith("MAL-")

    def withheld(self, cutoff: str = HOLDOUT_CUTOFF) -> bool:
        return self.published_at >= cutoff


def download_osv(dest: Path, *, force: bool = False) -> Path:
    """Fetch the npm OSV archive, skipping the download if it is already complete.

    Resumability here is coarse by design: the archive is a single immutable object, so
    "already the right size" is a sufficient checkpoint and a partial file is simply
    re-fetched.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    transport = httpx.HTTPTransport(local_address="0.0.0.0", retries=3)
    with httpx.Client(
        timeout=300.0, transport=transport, follow_redirects=True
    ) as client:
        if dest.exists() and not force:
            head = client.head(OSV_NPM_ALL)
            remote = int(head.headers.get("content-length", 0))
            if remote and dest.stat().st_size == remote:
                print(f"[osv] cached {dest} ({remote / 1024 / 1024:.1f}MB)")
                return dest

        print(f"[osv] downloading {OSV_NPM_ALL}")
        with client.stream("GET", OSV_NPM_ALL) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            written = 0
            with dest.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
                    written += len(chunk)
            print(f"[osv] {written / 1024 / 1024:.1f}MB of {total / 1024 / 1024:.1f}MB")
    return dest


def _extract_severity(doc: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (label, cvss_vector). Neither is fabricated when absent."""
    label = (doc.get("database_specific") or {}).get("severity")
    if isinstance(label, str):
        label = label.upper()
        if label not in _SEVERITY_ORDER:
            label = None

    vector: str | None = None
    for sev in doc.get("severity") or []:
        score = sev.get("score")
        if isinstance(score, str) and score.startswith("CVSS:"):
            vector = score
            break
    return label, vector


def _ranges_for(affected: dict[str, Any], pkg: str) -> list[AffectedRange]:
    """Flatten OSV range events into (introduced, fixed) pairs.

    OSV encodes ranges as an event stream: an ``introduced`` opens a window and the next
    ``fixed`` (or ``last_affected``) closes it. A package can have several disjoint windows —
    the Phoenix record in this archive has four — so this yields one row per window rather
    than collapsing to a single min/max, which would wrongly mark the gaps as vulnerable.
    """
    out: list[AffectedRange] = []
    for rng in affected.get("ranges") or []:
        if rng.get("type") not in ("SEMVER", "ECOSYSTEM"):
            continue
        introduced: str | None = None
        for event in rng.get("events") or []:
            if "introduced" in event:
                introduced = event["introduced"]
            elif "fixed" in event:
                out.append(AffectedRange(pkg, introduced, event["fixed"]))
                introduced = None
            elif "last_affected" in event:
                out.append(AffectedRange(pkg, introduced, None))
                introduced = None
        if introduced is not None:
            out.append(AffectedRange(pkg, introduced, None))  # still unfixed
    if not out and affected.get("versions"):
        # No range block, only an explicit version list.
        versions = sorted(affected["versions"])
        out.append(AffectedRange(pkg, versions[0], None))
    return out


def iter_npm_advisories(zip_path: Path) -> Iterator[Advisory]:
    """Stream npm-ecosystem advisories out of the archive."""
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.endswith(".json"):
                continue
            try:
                doc = json.loads(z.read(name))
            except (json.JSONDecodeError, KeyError):
                continue

            affected: list[AffectedRange] = []
            for entry in doc.get("affected") or []:
                package = entry.get("package") or {}
                # Per-entry ecosystem check — see module docstring, point 1.
                if package.get("ecosystem") != "npm":
                    continue
                pkg_name = package.get("name")
                if not pkg_name:
                    continue
                affected.extend(_ranges_for(entry, pkg_name))

            if not affected:
                continue

            published = doc.get("published") or doc.get("modified") or ""
            if not published:
                continue

            label, vector = _extract_severity(doc)
            yield Advisory(
                osv_id=doc.get("id", name.removesuffix(".json")),
                aliases=list(doc.get("aliases") or []),
                severity=label,
                cvss=vector,
                published_at=published,
                modified_at=doc.get("modified"),
                summary=doc.get("summary"),
                # MAL-* details are per-scanner boilerplate (130MB across the corpus, e.g.
                # "Per source details... ## Source: checkmarx (<hash>)") and carry no
                # information the summary lacks. Dropped rather than shipped.
                details=None
                if doc.get("id", "").startswith("MAL-")
                else doc.get("details"),
                withdrawn=doc.get("withdrawn"),
                affected=affected,
            )


def load_advisories(
    store: Store, zip_path: Path, *, cutoff: str = HOLDOUT_CUTOFF, batch: int = 5_000
) -> dict[str, int]:
    """Load advisories and AFFECTED_BY edges into the store.

    Returns counts, including how many were held out — that number goes straight into
    EVAL.md, so a silent holdout is impossible.
    """
    adv_rows: list[tuple[Any, ...]] = []
    aff_rows: list[tuple[Any, ...]] = []
    stats = {
        "advisories": 0,
        "withheld": 0,
        "malicious": 0,
        "withdrawn": 0,
        "affected_edges": 0,
    }

    def flush() -> None:
        if adv_rows:
            store.conn.executemany(
                "INSERT INTO advisory(osv_id, aliases, severity, cvss, published_at, "
                "  modified_at, summary, details, withdrawn, withheld) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(osv_id) DO UPDATE SET "
                "  modified_at = excluded.modified_at, withdrawn = excluded.withdrawn, "
                "  withheld = excluded.withheld",
                adv_rows,
            )
            adv_rows.clear()
        if aff_rows:
            store.conn.executemany(
                "INSERT INTO affected_by(pkg, osv_id, introduced, fixed, valid_from, valid_to) "
                "VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(pkg, osv_id, introduced) DO UPDATE SET fixed = excluded.fixed",
                aff_rows,
            )
            aff_rows.clear()
        store.commit()

    for adv in iter_npm_advisories(zip_path):
        held = adv.withheld(cutoff)
        stats["advisories"] += 1
        stats["withheld"] += int(held)
        stats["malicious"] += int(adv.is_malicious_report)
        stats["withdrawn"] += int(adv.withdrawn is not None)

        adv_rows.append(
            (
                adv.osv_id,
                json.dumps(adv.aliases),
                adv.severity,
                adv.cvss,
                adv.published_at,
                adv.modified_at,
                adv.summary,
                # Details are descriptive prose and do not feed any of the six answers;
                # capped so 226k records stay a shippable artifact. Noted in README limits.
                (adv.details or "")[:DETAILS_MAX_CHARS] or None,
                adv.withdrawn,
                int(held),
            )
        )
        for rng in adv.affected:
            # valid_from/valid_to are filled in by the version-history pass, which knows
            # when the introduced/fixed versions were actually published. Left NULL here
            # rather than guessed from the disclosure date.
            aff_rows.append(
                (rng.pkg, adv.osv_id, rng.introduced or "0.0.0", rng.fixed, None, None)
            )
            stats["affected_edges"] += 1

        if len(adv_rows) >= batch:
            flush()
            if stats["advisories"] % 50_000 < batch:
                print(f"[osv] {stats['advisories']:,} advisories")

    flush()
    store.set_meta("osv_holdout_cutoff", cutoff)
    store.set_meta("osv_stats", stats)
    return stats
