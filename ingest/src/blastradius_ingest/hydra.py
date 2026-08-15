"""HydraDB — the semantic half of the system.

SCHEMA.md §0 splits the work deliberately: the SQLite graph answers the six questions *exactly*,
and HydraDB answers the ones that are actually about meaning. Concretely, it carries the
advisory corpus as prose so a responder can ask in English — "has this maintainer shipped
anything like this before?", "which advisories describe credential exfiltration during
postinstall?" — and get back passages with citations plus HydraDB's own extracted
entity/relation paths.

That division is not decorative. Exact reverse-dependency closure is a reachability problem and
HydraDB has no traversal API (STATE.md B1). Matching free-text advisory prose to the right
package, and recognising that two differently-worded advisories describe the same attack, is a
meaning problem that no amount of graph traversal solves. Each side does the half it is good at.

Two API facts, both measured in P0 rather than assumed:

* the ``app_knowledge`` ingest path accepts a 202 and then never indexes (``FILE_NOT_FOUND``
  forever), so **all writes go through the ``documents`` multipart path** with
  ``document_metadata`` binding a stable id to each file;
* ingest is asynchronous — a 202 means queued, not indexed — so callers must poll
  ``/context/status`` before querying.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from blastradius_ingest.store import Store

__all__ = [
    "DATABASE",
    "AdvisoryDoc",
    "HydraClient",
    "advisory_documents",
    "ingest_advisories",
]

DATABASE = "blastradius"
API_VERSION = "2"
BATCH = 25  # documents per multipart request
RETRIES = 6  # 429 backoff attempts before giving up on a batch
PACE_SECONDS = 1.5  # gap between batches; measured to keep 429s rare


@dataclass(frozen=True, slots=True)
class AdvisoryDoc:
    osv_id: str
    title: str
    text: str
    severity: str | None
    packages: list[str]
    published_at: str

    @property
    def doc_id(self) -> str:
        return f"adv:{self.osv_id}"


class HydraClient:
    """Thin client over the HydraDB v2 API.

    The key is read from the environment only — never from a config file, never committed.
    """

    def __init__(self, *, url: str | None = None, key: str | None = None) -> None:
        self.url = url or os.environ.get("HYDRADB_URL", "https://api.hydradb.com")
        key = key or os.environ.get("HYDRADB_API_KEY")
        if not key:
            raise RuntimeError(
                "HYDRADB_API_KEY is not set. Copy .env.example to .env and fill it in; "
                "the key is read from the environment only."
            )
        self._key = key

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "API-Version": API_VERSION}

    def _client(self, timeout: float = 120.0) -> httpx.Client:
        return httpx.Client(
            timeout=timeout,
            transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=3),
            follow_redirects=True,
        )

    # ------------------------------------------------------------------ reads

    def status(self, ids: Sequence[str]) -> dict[str, str]:
        """Indexing status per source id."""
        if not ids:
            return {}
        with self._client() as c:
            r = c.get(
                f"{self.url}/context/status",
                headers=self._auth,
                params={
                    "database": DATABASE,
                    "type": "knowledge",
                    "ids": ",".join(ids),
                },
            )
            r.raise_for_status()
            body = r.json()
        return {
            s["id"]: s.get("indexing_status", "unknown")
            for s in (body.get("data") or {}).get("statuses", [])
        }

    def indexed_ids(self, *, page_size: int = 100) -> set[str]:
        """Every source id already in the database, for resumability."""
        seen: set[str] = set()
        page = 1
        with self._client() as c:
            while True:
                r = c.post(
                    f"{self.url}/context/list",
                    headers={**self._auth, "Content-Type": "application/json"},
                    json={
                        "database": DATABASE,
                        "type": "knowledge",
                        "page": page,
                        "page_size": page_size,
                    },
                )
                r.raise_for_status()
                data = r.json().get("data") or {}
                batch = data.get("sources") or []
                if not batch:
                    break
                seen.update(s["id"] for s in batch)
                if len(batch) < page_size:
                    break
                page += 1
        return seen

    def query(
        self,
        text: str,
        *,
        mode: str = "fast",
        max_results: int = 10,
        graph_context: bool = True,
    ) -> dict[str, Any]:
        """Semantic + BM25 retrieval.

        ``mode="fast"`` is the default because ``thinking`` measured 3.2s server-side in P0,
        which blows the console's sub-second budget. ``thinking`` is worth it only for the
        offline eval, where latency is reported rather than experienced.
        """
        t0 = time.perf_counter()
        with self._client(timeout=60.0) as c:
            r = c.post(
                f"{self.url}/query",
                headers={**self._auth, "Content-Type": "application/json"},
                json={
                    "database": DATABASE,
                    "query": text,
                    "type": "knowledge",
                    "query_by": "hybrid",
                    "mode": mode,
                    "graph_context": graph_context,
                    "max_results": max_results,
                },
            )
            r.raise_for_status()
            body = r.json()
        data = body.get("data") or {}
        return {
            "chunks": data.get("chunks") or [],
            "graph_context": data.get("graph_context") or {},
            "latency_ms": (time.perf_counter() - t0) * 1000.0,
            "server_latency_ms": (body.get("meta") or {}).get("latency_ms"),
        }

    # ----------------------------------------------------------------- writes

    def ingest_documents(self, docs: Sequence[AdvisoryDoc]) -> dict[str, Any]:
        """Push a batch through the documents multipart path.

        The ``app_knowledge`` path is not used: it returns 202 with a success count and then
        never indexes. See the module docstring.
        """
        files = [
            (
                "documents",
                (f"{d.doc_id}.txt", d.text.encode("utf-8"), "text/plain"),
            )
            for d in docs
        ]
        meta = [{"id": d.doc_id} for d in docs]
        data = {
            "type": "knowledge",
            "database": DATABASE,
            "upsert": "true",
            "document_metadata": json.dumps(meta),
        }
        # HydraDB rate-limits ingest with 429. Honour Retry-After when it is sent and fall
        # back to exponential backoff when it is not; a 429 is a "slow down", not a failure,
        # so give up only after the corpus genuinely cannot be pushed.
        delay = 5.0
        last: httpx.Response | None = None
        for attempt in range(RETRIES):
            with self._client() as c:
                r = c.post(
                    f"{self.url}/context/ingest",
                    headers=self._auth,
                    data=data,
                    files=files,
                )
            if r.status_code != 429:
                r.raise_for_status()
                return dict(r.json().get("data") or {})
            last = r
            if attempt < RETRIES - 1:
                wait = float(r.headers.get("retry-after") or delay)
                print(f"[hydra] 429, backing off {wait:.0f}s", flush=True)
                time.sleep(wait)
                delay = min(delay * 2, 120.0)
        assert last is not None
        last.raise_for_status()
        return {}


def advisory_documents(
    store: Store, *, limit: int | None = None, min_severity: bool = True
) -> Iterator[AdvisoryDoc]:
    """Render usable advisories as prose documents.

    Only advisories that are **not held out and not withdrawn** are emitted — the eval set must
    not reach HydraDB any more than it reaches the graph, or the P4 numbers are meaningless.

    ``MAL-*`` records are excluded: 219,676 of them carry no prose beyond a scanner-generated
    stub, so they would swamp the index with near-identical text while adding nothing a name
    lookup does not already answer.
    """
    sql = [
        "SELECT a.osv_id, a.summary, a.details, a.severity, a.published_at,",
        "       GROUP_CONCAT(DISTINCT af.pkg) AS pkgs",
        "FROM advisory a JOIN affected_by af ON af.osv_id = a.osv_id",
        "WHERE a.withheld = 0 AND a.withdrawn IS NULL",
        "  AND a.osv_id NOT LIKE 'MAL-%'",
    ]
    if min_severity:
        sql.append("  AND a.severity IN ('CRITICAL','HIGH','MODERATE','LOW')")
    sql.append("GROUP BY a.osv_id ORDER BY a.published_at DESC")
    if limit is not None:
        sql.append(f"LIMIT {int(limit)}")

    for row in store.conn.execute(" ".join(sql)):
        packages = [p for p in (row["pkgs"] or "").split(",") if p]
        summary = row["summary"] or ""
        details = row["details"] or ""
        body = (
            f"Advisory {row['osv_id']}\n"
            f"Severity: {row['severity'] or 'unspecified'}\n"
            f"Published: {row['published_at']}\n"
            f"Affected npm packages: {', '.join(packages) or 'unspecified'}\n\n"
            f"{summary}\n\n{details}"
        ).strip()
        yield AdvisoryDoc(
            osv_id=row["osv_id"],
            title=f"{row['osv_id']}: {summary[:80]}",
            text=body,
            severity=row["severity"],
            packages=packages,
            published_at=row["published_at"],
        )


def ingest_advisories(
    store: Store,
    client: HydraClient,
    *,
    limit: int | None = None,
    batch: int = BATCH,
    state_file: Path | None = None,
    resume: bool = True,
) -> dict[str, int]:
    """Ingest advisory prose into HydraDB, resumably.

    Already-pushed ids are recorded to ``state_file`` so a killed run does not re-send the
    whole corpus; HydraDB upserts on id, so a duplicate is harmless but wasteful.
    """
    pushed: set[str] = set()
    if resume and state_file and state_file.exists():
        pushed = {
            line.strip() for line in state_file.read_text().splitlines() if line.strip()
        }

    docs = [d for d in advisory_documents(store, limit=limit) if d.doc_id not in pushed]
    stats = {"sent": 0, "batches": 0, "skipped_already_pushed": len(pushed)}
    if not docs:
        return stats

    handle = state_file.open("a") if state_file else None
    try:
        for i in range(0, len(docs), batch):
            chunk = docs[i : i + batch]
            result = client.ingest_documents(chunk)
            ok = int(result.get("success_count", len(chunk)))
            stats["sent"] += ok
            stats["batches"] += 1
            if handle:
                for d in chunk:
                    handle.write(d.doc_id + "\n")
                handle.flush()
            print(f"[hydra] {stats['sent']:,}/{len(docs):,} queued", flush=True)
            time.sleep(PACE_SECONDS)  # stay under the ingest rate limit
    finally:
        if handle:
            handle.close()
    return stats
