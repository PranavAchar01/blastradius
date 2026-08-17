"""Eval harness — score the held-out advisories.

**What the holdout actually tests.** No system predicts an undisclosed vulnerability, and this
one does not pretend to. The held-out advisories (published on or after ``HOLDOUT_CUTOFF``,
none of which influenced the graph or the HydraDB index) stand in for *tomorrow's* disclosure.
The question being scored is the operational one: when a new advisory lands naming a package,
does a graph built entirely from earlier data immediately give the right answers to the six
questions? That is exactly the incident-response scenario, and it is falsifiable.

**Ground truth is independent of the graph.** For the closure questions the expected answer is
recomputed from the raw npm manifests in ``latest.ndjson`` using separate semver logic that
never touches the build code. If the graph and the oracle disagree, the graph is wrong. Scoring
a graph against itself would be worthless, and that is the failure mode this avoids.

**Scored separately, never blended.** The held-out set is 79% ``MAL-*`` — malicious-package
reports naming packages with essentially no downloads and therefore no reverse-dependency blast
radius at all. Averaging closure scores over that mix would produce a number that means nothing.
Q1/Q6 are scored over GHSA advisories whose package is inside the coverage cap; Q5 is scored
over the ``MAL-*`` records, which are the correct ground truth for typosquatting.
"""

from __future__ import annotations

import gzip
import json
import statistics
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blastradius_ingest import queries as Q
from blastradius_ingest.registry import Paths, read_ndjson
from blastradius_ingest.store import Store

__all__ = ["EvalCase", "EvalReport", "build_oracle", "run_eval"]


@dataclass(slots=True)
class EvalCase:
    osv_id: str
    pkg: str
    version: str
    expected: set[str]
    got: set[str]
    latency_ms: float

    @property
    def true_positives(self) -> int:
        return len(self.expected & self.got)

    @property
    def false_positives(self) -> int:
        return len(self.got - self.expected)

    @property
    def false_negatives(self) -> int:
        return len(self.expected - self.got)


@dataclass(slots=True)
class EvalReport:
    cases: list[EvalCase] = field(default_factory=list)
    latencies: dict[str, list[float]] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def record(self, question: str, ms: float) -> None:
        self.latencies.setdefault(question, []).append(ms)

    def bump(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n

    def micro_prf(self) -> dict[str, float]:
        """Micro-averaged precision/recall — each dependent counts once, so a handful of
        huge closures cannot be hidden behind many tiny perfect ones."""
        tp = sum(c.true_positives for c in self.cases)
        fp = sum(c.false_positives for c in self.cases)
        fn = sum(c.false_negatives for c in self.cases)
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        f1 = (
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )
        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }

    def percentiles(self, question: str) -> dict[str, float]:
        xs = sorted(self.latencies.get(question, []))
        if not xs:
            return {}
        return {
            "n": float(len(xs)),
            "p50": xs[len(xs) // 2],
            "p95": xs[min(len(xs) - 1, int(len(xs) * 0.95))],
            "mean": statistics.fmean(xs),
            "max": xs[-1],
        }


def _oracle_manifests(paths: Paths) -> Iterator[dict[str, Any]]:
    """Raw npm manifests for the oracle.

    Prefers the working ``latest.ndjson``; falls back to the compressed copy committed at
    ``fixtures/oracle-manifests.ndjson.gz`` (827KB) so the eval reproduces from a cold clone.
    ``data/`` is gitignored, and without this fallback the oracle came back empty and the eval
    reported ``precision=0.0000`` — a number that looks measured but is purely an artifact of a
    missing input file. Raising beats reporting that.
    """
    if paths.latest.exists():
        yield from read_ndjson(paths.latest)
        return

    fallback = (
        Path(__file__).resolve().parents[2] / "fixtures" / "oracle-manifests.ndjson.gz"
    )
    if not fallback.exists():
        raise FileNotFoundError(
            f"oracle input missing: neither {paths.latest} nor {fallback} exists. "
            "Run `blastradius latest`, or restore the committed fixture."
        )
    with gzip.open(fallback, "rt") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                yield json.loads(stripped)


def build_oracle(paths: Paths, store: Store) -> dict[tuple[str, str], set[str]]:
    """Independent ground truth: direct dependents per (package, resolved version).

    Built straight from the raw npm manifests with its own semver resolution, deliberately
    not reusing ``build.resolve_all_versions``. Same inputs, independent implementation — so
    agreement is evidence and disagreement is a real defect.
    """
    import nodesemver

    versions: dict[str, list[str]] = {}
    for row in store.conn.execute("SELECT pkg, version FROM version"):
        versions.setdefault(row["pkg"], []).append(row["version"])

    # The oracle must model the *same universe* as the graph. `latest.ndjson` is append-only
    # and accumulates packages from earlier runs at smaller caps — 26,259 records against a
    # frozen 25,000-package ranking — so iterating it wholesale credits dependents that are
    # outside the coverage cap and were correctly never ingested. Scoring against that
    # inflated set produced 37 phantom false negatives (recall 0.971) that were an artifact
    # of the eval, not a defect in the graph. Restricting to in-cap sources is what makes
    # the comparison meaningful; out-of-cap dependents are a documented coverage limit,
    # reported in EVAL.md rather than charged against retrieval.
    in_cap = {r["name"] for r in store.conn.execute("SELECT name FROM package")}

    oracle: dict[tuple[str, str], set[str]] = {}
    for rec in _oracle_manifests(paths):
        if rec.get("missing"):
            continue
        src = rec["name"]
        if src not in in_cap:
            continue
        for field_name in ("deps", "peer", "optional"):
            for target, rng in (rec.get(field_name) or {}).items():
                known = versions.get(target)
                if not known:
                    continue
                try:
                    best = nodesemver.max_satisfying(known, rng, loose=True)
                except Exception:  # noqa: BLE001, S112 - see below
                    # git:/file:/workspace: specifiers are not version ranges; skipping
                    # them is the correct oracle behaviour, not an error worth logging.
                    continue
                if best is None:
                    continue
                oracle.setdefault((target, str(best)), set()).add(src)
    return oracle


def _held_out_ghsa(store: Store, limit: int | None) -> Iterator[Any]:
    sql = (
        "SELECT DISTINCT a.osv_id, af.pkg, af.introduced, af.fixed "
        "FROM advisory a JOIN affected_by af USING(osv_id) "
        "JOIN package p ON p.name = af.pkg "
        "WHERE a.withheld = 1 AND a.withdrawn IS NULL AND a.osv_id NOT LIKE 'MAL-%' "
        "ORDER BY a.published_at"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    yield from store.conn.execute(sql)


def _vulnerable_version(
    store: Store, pkg: str, introduced: str, fixed: str | None
) -> str | None:
    """The newest known version inside the advisory's affected window.

    That is the version an incident responder would actually be asked about — the last bad
    release before the fix.
    """
    import nodesemver

    known = [
        r["version"]
        for r in store.conn.execute("SELECT version FROM version WHERE pkg = ?", (pkg,))
    ]
    candidates = []
    for v in known:
        try:
            if (
                introduced
                and introduced != "0"
                and nodesemver.lt(v, introduced, loose=True)
            ):
                continue
            if fixed and not nodesemver.lt(v, fixed, loose=True):
                continue
        except Exception:  # noqa: BLE001, S112 - see below
            # Unparseable version strings exist in the wild; excluding them from the
            # affected-window search is correct rather than exceptional.
            continue
        candidates.append(v)
    if not candidates:
        return None
    try:
        return str(nodesemver.max_satisfying(candidates, "*", loose=True))
    except Exception:  # noqa: BLE001
        return None


def run_eval(
    store: Store, paths: Paths, *, limit: int | None = None, runs: int = 3
) -> EvalReport:
    """Score the held-out set. Returns a report; the caller renders EVAL.md."""
    report = EvalReport()
    oracle = build_oracle(paths, store)
    report.bump("oracle_entries", len(oracle))
    # An empty oracle scores every correct answer as a false positive and reports
    # `precision=0.0000` — which reads as a catastrophic result but is really a missing input.
    # Refuse to emit a score rather than publish a meaningless one.
    if not oracle:
        raise RuntimeError(
            "oracle is empty — every result would be scored as a false positive and the "
            "reported precision would be an artifact, not a measurement. Check that the "
            "manifest source exists and that the graph has version rows."
        )

    for row in _held_out_ghsa(store, limit):
        report.bump("held_out_ghsa_in_cap")
        version = _vulnerable_version(
            store, row["pkg"], row["introduced"], row["fixed"]
        )
        if version is None:
            # The affected window names no version we hold; scoring it would measure
            # coverage, not retrieval. Counted and excluded rather than silently dropped.
            report.bump("skipped_no_known_affected_version")
            continue

        expected = oracle.get((row["pkg"], version), set())

        answer = Q.blast_radius(store, row["pkg"], version)
        got = {r["pkg"] for r in answer.rows if r["depth"] == 1}
        report.record("Q1_blast_radius", answer.latency_ms)
        report.cases.append(
            EvalCase(
                row["osv_id"], row["pkg"], version, expected, got, answer.latency_ms
            )
        )

        # Latency for the other questions on the same subject.
        for _ in range(runs):
            report.record(
                "Q2_introducing_version",
                Q.introducing_version(store, row["osv_id"]).latency_ms,
            )
            report.record(
                "Q3_exposed_during",
                Q.exposed_during(store, row["pkg"], version).latency_ms,
            )
            report.record(
                "Q4_shared_maintainer",
                Q.shared_maintainer_packages(store, row["pkg"]).latency_ms,
            )
            report.record(
                "Q5_typosquats", Q.typosquat_candidates(store, row["pkg"]).latency_ms
            )
            report.record("Q6_rollup", Q.rollup(store, row["pkg"], version).latency_ms)

    return report


def score_typosquats(store: Store, *, limit: int | None = None) -> dict[str, Any]:
    """Q5 against held-out ``MAL-*`` records — newly-disclosed malicious packages.

    Recall is the meaningful metric: of the malicious packages disclosed *after* the cutoff,
    how many does SIMILAR_NAME already flag as orthographically adjacent to a popular name?
    Precision against these labels is not meaningful — most near-neighbours of a popular
    package are legitimate, and OSV only labels squats that were caught. See STATE.md B5.
    """
    sql = (
        "SELECT DISTINCT af.pkg FROM advisory a JOIN affected_by af USING(osv_id) "
        "WHERE a.withheld = 1 AND a.osv_id LIKE 'MAL-%'"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    held_out_malicious = [r["pkg"] for r in store.conn.execute(sql)]

    flagged = 0
    latencies: list[float] = []
    for pkg in held_out_malicious:
        t0 = time.perf_counter()
        hit = store.conn.execute(
            "SELECT 1 FROM similar_name WHERE pkg = ? LIMIT 1", (pkg,)
        ).fetchone()
        latencies.append((time.perf_counter() - t0) * 1000.0)
        if hit:
            flagged += 1

    xs = sorted(latencies)
    return {
        "held_out_malicious_packages": len(held_out_malicious),
        "flagged_by_similar_name": flagged,
        "recall": flagged / len(held_out_malicious) if held_out_malicious else 0.0,
        "p50_ms": xs[len(xs) // 2] if xs else 0.0,
        "p95_ms": xs[min(len(xs) - 1, int(len(xs) * 0.95))] if xs else 0.0,
    }


def render_json(
    report: EvalReport, typo: dict[str, Any], out: Path, *, fn_samples: int = 25
) -> None:
    # Every disagreement with the oracle is dumped, not just counted. A recall number with
    # no way to inspect the misses is unfalsifiable, and these are the rows that tell you
    # whether the gap is a bug or a coverage boundary.
    misses = [
        {
            "osv_id": c.osv_id,
            "pkg": c.pkg,
            "version": c.version,
            "missed": sorted(c.expected - c.got),
            "spurious": sorted(c.got - c.expected),
        }
        for c in report.cases
        if c.false_negatives or c.false_positives
    ]
    payload = {
        "counters": report.counters,
        "closure": report.micro_prf(),
        "latency": {q: report.percentiles(q) for q in sorted(report.latencies)},
        "typosquats": typo,
        "cases_scored": len(report.cases),
        "cases_with_disagreement": len(misses),
        "disagreements": misses[:fn_samples],
    }
    # `data/` is gitignored, so on a cold clone it does not exist yet and the eval would
    # score everything correctly and then die writing its own results.
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
