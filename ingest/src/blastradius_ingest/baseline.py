"""Vector/embedding baseline — what semantic retrieval returns for the same six questions.

This is deliberately **not** a strawman. The baseline is HydraDB itself: a production semantic
retrieval system that P3 already showed is genuinely excellent at what it is for — it maps
"malicious postinstall script that steals environment variables" onto the real
eslint-config-prettier compromise at 0.91 without the query naming a single package.

The point of this section is not that embeddings are bad. It is that the six questions in the
brief are *reachability and interval* problems, and similarity search has no operator for
either. The comparison below asks each system the same question against the same corpus and
scores both against the verified ground truth from the graph, which was itself hand-checked
against npm's live registry in P2.

Run it and read the numbers; the argument in SCHEMA.md §4 is the prediction, this is the test.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blastradius_ingest import queries as Q
from blastradius_ingest.hydra import HydraClient
from blastradius_ingest.store import Store

__all__ = ["BaselineCase", "run_baseline"]


@dataclass(slots=True)
class BaselineCase:
    question: str
    prompt: str
    truth: set[str]
    retrieved: set[str]
    graph_latency_ms: float
    vector_latency_ms: float
    note: str = ""
    expressible: bool = True

    @property
    def precision(self) -> float:
        return (
            len(self.truth & self.retrieved) / len(self.retrieved)
            if self.retrieved
            else 0.0
        )

    @property
    def recall(self) -> float:
        return len(self.truth & self.retrieved) / len(self.truth) if self.truth else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "prompt": self.prompt,
            "expressible_as_a_similarity_query": self.expressible,
            "truth_size": len(self.truth),
            "retrieved_size": len(self.retrieved),
            "overlap": len(self.truth & self.retrieved),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "graph_latency_ms": round(self.graph_latency_ms, 3),
            "vector_latency_ms": round(self.vector_latency_ms, 1),
            "note": self.note,
        }


def _packages_in(chunks: list[dict[str, Any]], store: Store) -> set[str]:
    """Extract package names a retrieval result actually names.

    Generous on purpose: any known package name appearing anywhere in a returned chunk counts
    as "retrieved". If the baseline still misses, it is not because the scoring was stingy.
    """
    known = {r["name"] for r in store.conn.execute("SELECT name FROM package")}
    found: set[str] = set()
    for ch in chunks:
        text = (ch.get("chunk_content") or "") + " " + str(ch.get("id") or "")
        for token in set(text.replace(",", " ").replace("\n", " ").split()):
            t = token.strip(".:;()[]'\"")
            if t in known:
                found.add(t)
    return found


def run_baseline(
    store: Store, client: HydraClient, pkg: str, version: str, osv_id: str
) -> list[BaselineCase]:
    """Ask both systems the same six questions about the same compromised package."""
    cases: list[BaselineCase] = []

    def vector(prompt: str) -> tuple[set[str], float]:
        res = client.query(prompt, max_results=10)
        return _packages_in(res["chunks"], store), float(
            res["server_latency_ms"] or 0.0
        )

    # Q1 — transitive reverse-dependency closure.
    a = Q.blast_radius(store, pkg, version)
    truth = {r["pkg"] for r in a.rows}
    prompt = f"which npm packages depend on {pkg} version {version}"
    got, ms = vector(prompt)
    cases.append(
        BaselineCase(
            "Q1 blast radius",
            prompt,
            truth,
            got,
            a.latency_ms,
            ms,
            note=(
                "Closure is transitive and unbounded in depth; retrieval returns a ranked "
                "top-k with no composition operator, so depth>1 is unreachable in principle."
            ),
        )
    )

    # Q2 — which version introduced it.
    b = Q.introducing_version(store, osv_id)
    truth2 = {f"{r['pkg']}@{r['introduced']}" for r in b.rows}
    prompt = f"which version of {pkg} introduced the vulnerability {osv_id}"
    res = client.query(prompt, max_results=10)
    text = " ".join((c.get("chunk_content") or "") for c in res["chunks"])
    got2 = {t for t in truth2 if t.split("@")[1] in text}
    cases.append(
        BaselineCase(
            "Q2 introducing version",
            prompt,
            truth2,
            got2,
            b.latency_ms,
            float(res["server_latency_ms"] or 0.0),
            note=(
                "Interval containment over ordered semver. Adjacent versions embed almost "
                "identically while sitting on opposite sides of the fix boundary."
            ),
        )
    )

    # Q3 — temporal exposure. Not expressible: there is no time operator at all.
    c3 = Q.exposed_during(
        store, pkg, version, "2026-03-10T09:00:00Z", "2026-03-10T09:06:00Z"
    )
    prompt = (
        f"which services resolved {pkg} {version} between 09:00 and 09:06 on 2026-03-10"
    )
    got3, ms3 = vector(prompt)
    cases.append(
        BaselineCase(
            "Q3 temporal exposure",
            prompt,
            {r["lockfile_id"] for r in c3.rows},
            got3,
            c3.latency_ms,
            ms3,
            expressible=False,
            note=(
                "Requires interval algebra over two independent time ranges. Similarity "
                "search has no temporal operator; a metadata pre-filter cannot traverse."
            ),
        )
    )

    # Q4 — shared maintainer: an exact join.
    d = Q.shared_maintainer_packages(store, pkg)
    truth4 = {r["pkg"] for r in d.rows}
    prompt = f"which npm packages are published by the same maintainer as {pkg}"
    got4, ms4 = vector(prompt)
    cases.append(
        BaselineCase(
            "Q4 shared maintainer",
            prompt,
            truth4,
            got4,
            d.latency_ms,
            ms4,
            note=(
                "An exact join through the maintainer. Semantic similarity is actively "
                "misleading here: one maintainer's packages are usually unrelated in purpose."
            ),
        )
    )

    # Q5 — typosquats: orthographic, near-opposite of semantic distance.
    e = Q.typosquat_candidates(store, pkg)
    truth5 = {r["candidate"] for r in e.rows}
    prompt = f"which npm package names are typosquats of {pkg}"
    got5, ms5 = vector(prompt)
    cases.append(
        BaselineCase(
            "Q5 typosquats",
            prompt,
            truth5,
            got5,
            e.latency_ms,
            ms5,
            note=(
                "Needs orthographic distance. An embedding good enough for search maps "
                "'lodash' and '1odash' to nearly the same point — which is why the attack works."
            ),
        )
    )

    # Q6 — rollup: properties of the traversal itself.
    f = Q.rollup(store, pkg, version)
    prompt = (
        f"which services are exposed to {pkg} {version}, at what depth, and since when"
    )
    got6, ms6 = vector(prompt)
    cases.append(
        BaselineCase(
            "Q6 rollup",
            prompt,
            {str(r["service_id"]) for r in f.rows},
            got6,
            f.latency_ms,
            ms6,
            expressible=False,
            note=(
                "Depth is shortest-path length and first-exposure is a min over interval "
                "starts along that path. Both are properties of the traversal, so there is "
                "nothing to embed — perfect retrieval of every document still lacks the answer."
            ),
        )
    )
    return cases


def render_baseline(cases: list[BaselineCase], out: Path) -> None:
    out.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "cases": [c.as_dict() for c in cases],
            },
            indent=2,
        )
    )
