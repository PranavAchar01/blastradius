"""blastradius ingest CLI.

Every stage is independently runnable and resumable, so a run that dies mid-way is restarted
with the same command and picks up from disk:

    uv run blastradius names        # ~4.29M package names
    uv run blastradius downloads    # weekly download counts, to rank
    uv run blastradius latest       # deps + maintainers for the top-N
    uv run blastradius advisories   # OSV npm corpus
    uv run blastradius build        # assemble the SQLite graph
    uv run blastradius stats        # node/edge counts
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from blastradius_ingest.baseline import render_baseline, run_baseline
from blastradius_ingest.build import (
    backfill_affected_intervals,
    build_graph,
    freeze_ranking,
    load_packuments,
    rank_packages,
    resolve_all_versions,
    resolve_latest_versions,
)
from blastradius_ingest.eval import render_json, run_eval, score_typosquats
from blastradius_ingest.hydra import HydraClient, ingest_advisories
from blastradius_ingest.osv import HOLDOUT_CUTOFF, download_osv, load_advisories
from blastradius_ingest.registry import Paths, RegistryClient, iter_names, read_ndjson
from blastradius_ingest.similar import build_similar_names
from blastradius_ingest.store import Store

DEFAULT_DATA = Path(__file__).resolve().parents[3] / "data"
DEFAULT_DB = DEFAULT_DATA / "blastradius.db"
DEFAULT_TOP_N = 25_000


def _paths(args: argparse.Namespace) -> Paths:
    return Paths(Path(args.data))


def cmd_rank(args: argparse.Namespace) -> int:
    paths = _paths(args)
    n = freeze_ranking(paths, args.top_n)
    print(f"[rank] froze top {n:,} packages -> {paths.ranked}")
    return 0


def cmd_names(args: argparse.Namespace) -> int:
    paths = _paths(args)
    t0 = time.monotonic()
    total = asyncio.run(RegistryClient(paths).fetch_names())
    print(f"[names] {total:,} names in {time.monotonic() - t0:.0f}s -> {paths.names}")
    return 0


def cmd_downloads(args: argparse.Namespace) -> int:
    paths = _paths(args)
    t0 = time.monotonic()
    n = asyncio.run(
        RegistryClient(paths, concurrency=args.concurrency).fetch_downloads(
            scope=args.scope
        )
    )
    dt = time.monotonic() - t0
    rate = n / dt if dt else 0
    print(f"[downloads] +{n:,} in {dt:.0f}s ({rate:.0f}/s) -> {paths.downloads}")
    return 0


def cmd_latest(args: argparse.Namespace) -> int:
    paths = _paths(args)
    try:
        names = [name for name, _, _ in rank_packages(paths, args.top_n)]
    except FileNotFoundError as exc:
        print(f"[latest] {exc}", file=sys.stderr)
        return 1
    if not names:
        print("[latest] frozen ranking is empty", file=sys.stderr)
        return 1
    t0 = time.monotonic()
    n = asyncio.run(
        RegistryClient(paths, concurrency=args.concurrency).fetch_latest(names)
    )
    dt = time.monotonic() - t0
    print(
        f"[latest] +{n:,} in {dt:.0f}s ({n / dt if dt else 0:.0f}/s) -> {paths.latest}"
    )
    return 0


def cmd_advisories(args: argparse.Namespace) -> int:
    paths = _paths(args)
    zip_path = download_osv(paths.osv_zip)
    t0 = time.monotonic()
    with Store(args.db) as store:
        stats = load_advisories(store, zip_path, cutoff=args.cutoff)
    dt = time.monotonic() - t0
    print(
        f"[osv] {stats['advisories']:,} npm advisories "
        f"({stats['malicious']:,} MAL-*, {stats['withheld']:,} held out >= {args.cutoff}), "
        f"{stats['affected_edges']:,} AFFECTED_BY edges in {dt:.0f}s"
    )
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    paths = _paths(args)
    t0 = time.monotonic()
    with Store(args.db) as store:
        stats = build_graph(store, paths, top_n=args.top_n)
        res = resolve_latest_versions(store)
        counts = store.counts()
    print(f"[build] {time.monotonic() - t0:.0f}s")
    for k, v in {**stats, **res}.items():
        print(f"  {k:22} {v:,}")
    print("[build] table counts:")
    for k, v in counts.items():
        print(f"  {k:16} {v:,}")
    return 0


def cmd_similar(args: argparse.Namespace) -> int:
    paths = _paths(args)
    ranked = rank_packages(paths, args.top_n)
    if not ranked:
        print("[similar] no frozen ranking — run `rank` first", file=sys.stderr)
        return 1
    candidates = list(iter_names(paths.names))
    print(f"[similar] {len(candidates):,} candidates vs top {len(ranked):,}")
    with Store(args.db) as store:
        stats = build_similar_names(store, candidates, ranked)
    for k, v in stats.items():
        print(f"  {k:10} {v:,}")
    return 0


def cmd_packuments(args: argparse.Namespace) -> int:
    """Full packuments for advisory-affected packages inside the cap.

    Bounded on purpose: full packuments average ~242KB, so this runs only where the extra
    versions actually change an answer — packages that have an advisory. See STATE.md B7.
    """
    paths = _paths(args)
    with Store(args.db) as store:
        targets = [
            r["pkg"]
            for r in store.conn.execute(
                "SELECT DISTINCT af.pkg FROM affected_by af "
                "JOIN package p ON p.name = af.pkg "
                "JOIN advisory a ON a.osv_id = af.osv_id "
                "WHERE a.withheld = 0 AND a.withdrawn IS NULL"
            )
        ]
    print(f"[packuments] {len(targets):,} advisory-affected packages inside the cap")
    if not targets:
        print("[packuments] nothing to fetch — build the graph first", file=sys.stderr)
        return 1
    t0 = time.monotonic()
    n = asyncio.run(
        RegistryClient(paths, concurrency=args.concurrency).fetch_packuments(targets)
    )
    print(f"[packuments] +{n:,} in {time.monotonic() - t0:.0f}s -> {paths.packuments}")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    """Load version histories, then resolve ranges against all known versions."""
    paths = _paths(args)
    with Store(args.db) as store:
        loaded = load_packuments(store, paths)
        print(f"[resolve] versions loaded: {loaded}")
        before = store.conn.execute(
            "SELECT COUNT(*) FROM depends_on WHERE resolved IS NOT NULL"
        ).fetchone()[0]
        stats = resolve_all_versions(store)
        after = store.conn.execute(
            "SELECT COUNT(*) FROM depends_on WHERE resolved IS NOT NULL"
        ).fetchone()[0]
        intervals = backfill_affected_intervals(store)
    print(f"[resolve] {stats}")
    print(f"[resolve] resolved edges {before:,} -> {after:,}  (+{after - before:,})")
    print(f"[resolve] AFFECTED_BY intervals: {intervals}")
    return 0


def cmd_hydra_ingest(args: argparse.Namespace) -> int:
    """Push advisory prose into HydraDB — the semantic half (SCHEMA.md §0)."""
    paths = _paths(args)
    client = HydraClient()
    t0 = time.monotonic()
    with Store(args.db) as store:
        stats = ingest_advisories(
            store,
            client,
            limit=args.limit,
            state_file=paths.root / "hydra_pushed.txt",
        )
    dt = time.monotonic() - t0
    print(f"[hydra] {stats} in {dt:.0f}s")
    return 0


def cmd_hydra_query(args: argparse.Namespace) -> int:
    """Ask HydraDB a natural-language question over the advisory corpus."""
    client = HydraClient()
    res = client.query(args.text, mode=args.mode, max_results=args.max_results)
    print(
        f"[hydra] {len(res['chunks'])} chunks in {res['latency_ms']:.0f}ms "
        f"(server {res['server_latency_ms']}ms, mode={args.mode})"
    )
    for ch in res["chunks"][:5]:
        body = (ch.get("chunk_content") or "").replace("\n", " ")[:150]
        print(f"  - {ch.get('id')}  score={ch.get('relevancy_score')}")
        print(f"      {body}")
    paths = (res["graph_context"] or {}).get("query_paths") or []
    if paths:
        print(f"  graph paths: {len(paths)}")
        for trip in (paths[0].get("triplets") or [])[:3]:
            s, rel, t = trip.get("source"), trip.get("relation"), trip.get("target")
            print(
                f"      {s.get('name')} [{s.get('type')}] "
                f"--{rel.get('canonical_predicate')}--> {t.get('name')} [{t.get('type')}]"
            )
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Score the held-out advisories and write machine-readable results."""
    paths = _paths(args)
    t0 = time.monotonic()
    with Store(args.db) as store:
        report = run_eval(store, paths, limit=args.limit)
        typo = score_typosquats(store, limit=args.typo_limit)
        out = paths.root / "eval_results.json"
        render_json(report, typo, out)

    prf = report.micro_prf()
    print(
        f"[eval] {len(report.cases)} closure cases scored in {time.monotonic() - t0:.0f}s"
    )
    print(f"[eval] counters: {report.counters}")
    print(
        f"[eval] closure micro precision={prf['precision']:.4f} "
        f"recall={prf['recall']:.4f} f1={prf['f1']:.4f} "
        f"(tp={prf['tp']} fp={prf['fp']} fn={prf['fn']})"
    )
    for q in sorted(report.latencies):
        p = report.percentiles(q)
        print(
            f"[eval] {q:24} n={int(p['n']):5} p50={p['p50']:7.3f}ms p95={p['p95']:8.3f}ms"
        )
    print(f"[eval] typosquats: {typo}")
    print(f"[eval] wrote {out}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    """Ask HydraDB the same six questions and score it against verified graph truth."""
    paths = _paths(args)
    client = HydraClient()
    with Store(args.db) as store:
        cases = run_baseline(store, client, args.pkg, args.version, args.osv_id)
        out = paths.root / "baseline_results.json"
        render_baseline(cases, out)
    print(
        f"\n{'question':<24} {'truth':>6} {'found':>6} {'hit':>4} {'prec':>6} {'recall':>7}"
        f" {'graph':>9} {'vector':>9}"
    )
    print("-" * 80)
    for c in cases:
        mark = "" if c.expressible else "  (not expressible)"
        print(
            f"{c.question:<24} {len(c.truth):>6} {len(c.retrieved):>6} "
            f"{len(c.truth & c.retrieved):>4} {c.precision:>6.2f} {c.recall:>7.2f} "
            f"{c.graph_latency_ms:>7.3f}ms {c.vector_latency_ms:>7.0f}ms{mark}"
        )
    print(f"\nwrote {out}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    paths = _paths(args)
    n_names = sum(1 for _ in iter_names(paths.names))
    n_dl = sum(1 for _ in read_ndjson(paths.downloads))
    n_latest = sum(1 for _ in read_ndjson(paths.latest))
    print(f"names.txt        {n_names:,}")
    print(f"downloads.ndjson {n_dl:,}")
    print(f"latest.ndjson    {n_latest:,}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="blastradius", description=__doc__)
    p.add_argument("--data", default=str(DEFAULT_DATA), help="working data directory")
    p.add_argument("--db", default=str(DEFAULT_DB), help="SQLite graph path")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("names", help="fetch every npm package name").set_defaults(
        func=cmd_names
    )

    d = sub.add_parser("downloads", help="fetch weekly download counts")
    d.add_argument("--concurrency", type=int, default=10)
    d.add_argument(
        "--scope",
        choices=("unscoped", "scoped", "all"),
        default="unscoped",
        help=(
            "which half of the registry to price. Defaults to unscoped: the bulk endpoint "
            "rejects scoped names and rate-limits single lookups to ~3 req/s (see B4), so "
            "scoped packages are priced from a candidate set instead."
        ),
    )
    d.set_defaults(func=cmd_downloads)

    latest = sub.add_parser("latest", help="fetch deps + maintainers for the top-N")
    latest.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    # Measured: concurrency 10 collapses to ~5 packages/min because 429s send every worker
    # into exponential backoff; concurrency 4 sustains ~1,500/min. Lower is dramatically
    # faster here — do not raise this without re-measuring.
    latest.add_argument("--concurrency", type=int, default=4)
    latest.set_defaults(func=cmd_latest)

    rk = sub.add_parser("rank", help="freeze the top-N coverage cap to ranked.tsv")
    rk.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    rk.set_defaults(func=cmd_rank)

    b = sub.add_parser("build", help="assemble the SQLite graph from staged files")
    b.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    b.set_defaults(func=cmd_build)

    pk = sub.add_parser(
        "packuments", help="full version histories for advisory-affected packages"
    )
    pk.add_argument("--concurrency", type=int, default=8)
    pk.set_defaults(func=cmd_packuments)

    sub.add_parser(
        "resolve", help="resolve DEPENDS_ON against all known versions; fill intervals"
    ).set_defaults(func=cmd_resolve)

    hi = sub.add_parser("hydra-ingest", help="push advisory prose into HydraDB")
    hi.add_argument("--limit", type=int, default=None)
    hi.set_defaults(func=cmd_hydra_ingest)

    hq = sub.add_parser(
        "hydra-query", help="natural-language query over the advisory corpus"
    )
    hq.add_argument("text")
    hq.add_argument("--mode", choices=("fast", "thinking"), default="fast")
    hq.add_argument("--max-results", type=int, default=10)
    hq.set_defaults(func=cmd_hydra_query)

    bl = sub.add_parser(
        "baseline", help="vector/embedding baseline for the same six questions"
    )
    bl.add_argument("--pkg", default="lodash")
    bl.add_argument("--version", default="3.10.1")
    bl.add_argument("--osv-id", dest="osv_id", default="GHSA-35jh-r3h4-6jhm")
    bl.set_defaults(func=cmd_baseline)

    ev = sub.add_parser("eval", help="score the held-out advisories")
    ev.add_argument("--limit", type=int, default=None, help="cap closure cases scored")
    ev.add_argument("--typo-limit", type=int, default=None)
    ev.set_defaults(func=cmd_eval)

    sim = sub.add_parser("similar", help="build SIMILAR_NAME typosquat edges")
    sim.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    sim.set_defaults(func=cmd_similar)

    adv = sub.add_parser("advisories", help="ingest the OSV npm advisory corpus")
    adv.add_argument(
        "--cutoff",
        default=HOLDOUT_CUTOFF,
        help="advisories published on/after this date are held out as the eval set",
    )
    adv.set_defaults(func=cmd_advisories)

    sub.add_parser("stats", help="row counts for each stage file").set_defaults(
        func=cmd_stats
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
