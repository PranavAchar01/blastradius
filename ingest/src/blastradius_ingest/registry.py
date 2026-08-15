"""npm registry ingest — resumable, checkpointed, restartable.

Three stages, each of which can die at any point and resume from disk:

``names``      paginate ``replicate.npmjs.com/_all_docs`` for every package name (~4.29M)
``downloads``  weekly download counts for every name, to pick the top-N cap
``latest``     ``registry.npmjs.org/<pkg>/latest`` for the top-N: deps + maintainers in one
               small (1.6-4.2KB) payload

Two registry constraints shape this, both measured rather than assumed:

* the bulk downloads endpoint caps at 128 names **and rejects scoped packages outright**
  (``scoped packages are not currently supported in bulk lookups``), so scoped names are
  fetched individually;
* per-version publish dates live only on the *full* packument (``time``), not the abbreviated
  one, so version history is fetched separately and only for packages that appear in an
  advisory (see ``osv.py``).

Checkpointing is by append-only NDJSON plus a resume marker, so a partial file is always
valid input to the next run.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

__all__ = ["Paths", "RegistryClient", "iter_names", "read_ndjson"]

REPLICATE = "https://replicate.npmjs.com"
REGISTRY = "https://registry.npmjs.org"
DOWNLOADS = "https://api.npmjs.org/downloads/point/last-week"

BULK_MAX = 128  # measured: 129 returns "exceeded max bulk size of 128"

_SENTINEL = object()  # queue poison pill for the worker pool


@dataclass(frozen=True, slots=True)
class Paths:
    """Where each stage's output and resume marker live."""

    root: Path

    @property
    def names(self) -> Path:
        return self.root / "names.txt"

    @property
    def downloads(self) -> Path:
        return self.root / "downloads.ndjson"

    @property
    def latest(self) -> Path:
        return self.root / "latest.ndjson"

    @property
    def packuments(self) -> Path:
        return self.root / "packuments.ndjson"

    @property
    def osv_zip(self) -> Path:
        return self.root / "osv-npm-all.zip"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)


def read_ndjson(path: Path) -> Iterator[dict[str, Any]]:
    """Yield records from an append-only NDJSON file, tolerating a torn final line.

    A run killed mid-write leaves a partial last line; skipping it is what makes the file
    safe to resume from without a repair step.
    """
    if not path.exists():
        return
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # torn final line from an interrupted run


def iter_names(path: Path) -> Iterator[str]:
    if not path.exists():
        return
    with path.open() as fh:
        for line in fh:
            name = line.strip()
            if name:
                yield name


def _chunks(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class RegistryClient:
    def __init__(
        self,
        paths: Paths,
        *,
        concurrency: int = 24,
        timeout: float = 60.0,
        progress_every: int = 25_000,
    ) -> None:
        self.paths = paths
        self.paths.ensure()
        self.concurrency = concurrency
        self.timeout = timeout
        self.progress_every = progress_every
        self._sem = asyncio.Semaphore(concurrency)

    def _client(self) -> httpx.AsyncClient:
        """An IPv4-only client.

        Measured: this host has no usable IPv6 route, so every npm connection first burned
        the happy-eyeballs timeout on an AAAA address (``EHOSTUNREACH`` on 2606:4700::/32,
        then ``ETIMEDOUT``) before falling back. Binding to an IPv4 local address skips the
        dead half of the dual stack and turns an intermittent ~30% failure rate into a clean
        run. ``example.com`` and Google storage were unaffected throughout, which is what
        ruled out rate limiting.
        """
        return httpx.AsyncClient(
            timeout=self.timeout,
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2),
            headers={"user-agent": "blastradius-ingest/0.1 (+hackhydra 02A)"},
            follow_redirects=True,
        )

    # ------------------------------------------------------------------ http

    async def _get(
        self, client: httpx.AsyncClient, url: str, *, retries: int = 4
    ) -> httpx.Response | None:
        """GET with bounded retry and exponential backoff. Returns None on give-up."""
        delay = 1.0
        for attempt in range(retries):
            async with self._sem:
                try:
                    r = await client.get(url)
                except (httpx.TransportError, httpx.HTTPError):
                    r = None
            if r is not None:
                if r.status_code == 200:
                    return r
                if r.status_code == 404:
                    return None  # genuinely absent; not worth retrying
                if r.status_code < 500 and r.status_code != 429:
                    return None
            if attempt < retries - 1:
                await asyncio.sleep(delay)
                delay *= 2
        return None

    # ------------------------------------------------------- stage: names

    async def fetch_names(self, *, page_size: int = 5_000) -> int:
        # Measured ceiling: limit=25000 returns 400 "Bad Request"; 5000 returns 480KB cleanly.
        """Paginate every package name into ``names.txt``.

        Resumes from the last name already written by using it as ``startkey`` and
        dropping the duplicate first row.
        """
        existing = list(iter_names(self.paths.names))
        start_after = existing[-1] if existing else None
        total = len(existing)
        if start_after:
            print(f"[names] resuming after {start_after!r} ({total} already written)")

        async with self._client() as client:
            with self.paths.names.open("a") as out:
                while True:
                    url = f"{REPLICATE}/_all_docs?limit={page_size}"
                    if start_after is not None:
                        url += f"&startkey={json.dumps(start_after)}"
                    r = await self._get(client, url)
                    if r is None:
                        print("[names] request failed after retries; stopping")
                        break

                    rows = r.json().get("rows", [])
                    if (
                        start_after is not None
                        and rows
                        and rows[0]["key"] == start_after
                    ):
                        rows = rows[1:]
                    if not rows:
                        break

                    for row in rows:
                        out.write(row["key"] + "\n")
                    out.flush()

                    total += len(rows)
                    start_after = rows[-1]["key"]
                    print(f"[names] {total:,}")
                    if len(rows) < page_size - 1:
                        break
        return total

    # --------------------------------------------------- stage: downloads

    async def fetch_downloads(self) -> int:
        """Weekly download counts for every name.

        Unscoped names go through the 128-wide bulk endpoint; scoped names must be fetched
        one at a time because the bulk endpoint rejects them.
        """
        done = {rec["name"] for rec in read_ndjson(self.paths.downloads)}
        names = [n for n in iter_names(self.paths.names) if n not in done]
        unscoped = [n for n in names if not n.startswith("@")]
        scoped = [n for n in names if n.startswith("@")]
        print(
            f"[downloads] {len(done):,} done; {len(unscoped):,} unscoped + "
            f"{len(scoped):,} scoped remaining"
        )

        written = 0
        lock = asyncio.Lock()

        async with self._client() as client:
            with self.paths.downloads.open("a") as out:

                async def do_bulk(batch: Sequence[str]) -> None:
                    nonlocal written
                    r = await self._get(client, f"{DOWNLOADS}/{','.join(batch)}")
                    recs: list[dict[str, Any]] = []
                    if r is not None:
                        payload = r.json()
                        # A single-name batch returns a bare object, not a map.
                        if "downloads" in payload and "package" in payload:
                            payload = {payload["package"]: payload}
                        for name in batch:
                            entry = payload.get(name)
                            recs.append(
                                {
                                    "name": name,
                                    "downloads": (entry or {}).get("downloads", 0) or 0,
                                }
                            )
                    else:
                        recs = [{"name": n, "downloads": 0} for n in batch]
                    async with lock:
                        for rec in recs:
                            out.write(json.dumps(rec) + "\n")
                        out.flush()
                        written += len(recs)
                        if written % self.progress_every < len(recs):
                            print(f"[downloads] {written:,}")

                async def do_single(name: str) -> None:
                    nonlocal written
                    quoted = name.replace("/", "%2f")
                    r = await self._get(client, f"{DOWNLOADS}/{quoted}")
                    dl = 0
                    if r is not None:
                        try:
                            dl = r.json().get("downloads", 0) or 0
                        except json.JSONDecodeError:
                            dl = 0
                    async with lock:
                        out.write(json.dumps({"name": name, "downloads": dl}) + "\n")
                        out.flush()
                        written += 1
                        if written % self.progress_every == 0:
                            print(f"[downloads] {written:,}")

                # Work is streamed through a worker pool rather than pre-built as a list of
                # coroutines: there are 1.62M scoped names, and materializing that many
                # coroutine objects up front exhausts memory before a single request goes out.
                await _run_pool(
                    list(_chunks(unscoped, BULK_MAX)), do_bulk, self.concurrency
                )
                await _run_pool(scoped, do_single, self.concurrency)
        return written

    # ------------------------------------------------------ stage: latest

    async def fetch_latest(self, names: Sequence[str]) -> int:
        """``/<pkg>/latest`` for each name: version, dependencies, and maintainers.

        Measured at 1.6-4.2KB per package, versus ~81KB for the abbreviated packument and
        ~242KB for the full one. This is what makes the top-N pass affordable.
        """
        done = {rec["name"] for rec in read_ndjson(self.paths.latest)}
        todo = [n for n in names if n not in done]
        print(f"[latest] {len(done):,} done; {len(todo):,} remaining")

        written = 0
        lock = asyncio.Lock()

        async with self._client() as client:
            with self.paths.latest.open("a") as out:

                async def one(name: str) -> None:
                    nonlocal written
                    quoted = name.replace("/", "%2f")
                    r = await self._get(client, f"{REGISTRY}/{quoted}/latest")
                    if r is None:
                        rec: dict[str, Any] = {"name": name, "missing": True}
                    else:
                        try:
                            d = r.json()
                        except json.JSONDecodeError:
                            d = {}
                        rec = {
                            "name": name,
                            "version": d.get("version"),
                            "deps": d.get("dependencies") or {},
                            "peer": d.get("peerDependencies") or {},
                            "optional": d.get("optionalDependencies") or {},
                            "maintainers": [
                                m.get("name") if isinstance(m, dict) else str(m)
                                for m in (d.get("maintainers") or [])
                            ],
                            "description": d.get("description"),
                        }
                    async with lock:
                        out.write(json.dumps(rec) + "\n")
                        out.flush()
                        written += 1
                        if written % 2_500 == 0:
                            print(f"[latest] {written:,}/{len(todo):,}")

                await _run_pool(todo, one, self.concurrency)
        return written

    # -------------------------------------------- stage: version history

    async def fetch_packuments(self, names: Sequence[str]) -> int:
        """Full packuments, for per-version publish dates (``time``).

        Only called for packages that appear in an advisory — the full packument averages
        ~242KB, so this is deliberately not run across the whole top-N.
        """
        done = {rec["name"] for rec in read_ndjson(self.paths.packuments)}
        todo = [n for n in names if n not in done]
        print(f"[packuments] {len(done):,} done; {len(todo):,} remaining")

        written = 0
        lock = asyncio.Lock()

        async with self._client() as client:
            with self.paths.packuments.open("a") as out:

                async def one(name: str) -> None:
                    nonlocal written
                    quoted = name.replace("/", "%2f")
                    r = await self._get(client, f"{REGISTRY}/{quoted}")
                    if r is None:
                        rec: dict[str, Any] = {"name": name, "missing": True}
                    else:
                        try:
                            d = r.json()
                        except json.JSONDecodeError:
                            d = {}
                        times = {
                            k: v
                            for k, v in (d.get("time") or {}).items()
                            if k not in ("created", "modified")
                        }
                        rec = {
                            "name": name,
                            "times": times,
                            "maintainers": [
                                m.get("name") if isinstance(m, dict) else str(m)
                                for m in (d.get("maintainers") or [])
                            ],
                        }
                    async with lock:
                        out.write(json.dumps(rec) + "\n")
                        out.flush()
                        written += 1
                        if written % 250 == 0:
                            print(f"[packuments] {written:,}/{len(todo):,}")

                await _run_pool(todo, one, min(self.concurrency, 12))
        return written


async def _run_pool(
    items: Sequence[Any],
    handler: Callable[[Any], Awaitable[None]],
    workers: int,
) -> None:
    """Feed ``items`` through a fixed pool of workers.

    Each worker pulls from a bounded queue, so memory stays flat regardless of how many
    items there are — the scoped-downloads stage has 1.62M of them. A handler that raises
    is logged and skipped rather than killing the pool, since a single bad package name
    should not end a multi-hour ingest.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=workers * 4)

    async def worker() -> None:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                queue.task_done()
                return
            try:
                await handler(item)
            except Exception as exc:  # noqa: BLE001 - one bad item must not stop the run
                print(f"[pool] item failed ({type(exc).__name__}: {exc}); continuing")
            finally:
                queue.task_done()

    tasks = [asyncio.create_task(worker()) for _ in range(workers)]
    for item in items:
        await queue.put(item)
    for _ in tasks:
        await queue.put(_SENTINEL)
    await asyncio.gather(*tasks)
