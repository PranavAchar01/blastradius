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

from blastradius_ingest.build import (
    build_graph,
    freeze_ranking,
    rank_packages,
    resolve_latest_versions,
)
from blastradius_ingest.osv import HOLDOUT_CUTOFF, download_osv, load_advisories
from blastradius_ingest.registry import Paths, RegistryClient, iter_names, read_ndjson
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
    latest.add_argument("--concurrency", type=int, default=24)
    latest.set_defaults(func=cmd_latest)

    rk = sub.add_parser("rank", help="freeze the top-N coverage cap to ranked.tsv")
    rk.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    rk.set_defaults(func=cmd_rank)

    b = sub.add_parser("build", help="assemble the SQLite graph from staged files")
    b.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    b.set_defaults(func=cmd_build)

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
