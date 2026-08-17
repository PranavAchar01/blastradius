# blastradius

**A supply-chain blast-radius console.** When an npm package is compromised, it answers the
questions that actually matter in the first ten minutes — by traversing a graph, not by ranking
similarity.

**Live:** https://blastradius-c8mvz5mw9-phantom3452s-projects.vercel.app
· Built for **Hack Hydra, Track 02A**.

---

## The problem

A maintainer's account gets taken over at 09:00. A malicious version ships. It is yanked at
09:06. Six minutes.

In those six minutes the questions are: *who transitively depends on the bad version, which of
**our** services actually resolved it while it was live, what else does that maintainer publish,
and what typosquats are sitting next to the name.* Every one of those is a **reachability**
question over a typed, time-varying graph. None of them is a similarity question — which is why
reaching for a vector index produces confident, wrong answers. blastradius answers all six
exactly, in single-digit milliseconds, and shows the traversal path that proves each one.

## The six questions

| | question | how |
| --- | --- | --- |
| Q1 | transitive reverse-dependency closure for `pkg@version` | BFS over `DEPENDS_ON`, matched on the *resolved* version |
| Q2 | which version introduced the vulnerability | `AFFECTED_BY` version bounds |
| Q3 | which lockfiles/services resolved it **while it was live** | interval overlap during traversal |
| Q4 | packages sharing a maintainer | exact join through `PUBLISHED_BY` |
| Q5 | likely typosquats near the name | Damerau-Levenshtein ≤2, length-relative |
| Q6 | rollup: services exposed, depth, first-exposure time | Q1 ∘ Q3, with depth and min-interval |

Measured p50 across 1,404 held-out advisories: **Q1 0.006ms · Q3 0.003ms · Q6 0.013ms**.
Full numbers in [EVAL.md](EVAL.md).

## What makes the answers trustworthy

- **Precision 1.0000, recall 1.0000** on the held-out closure eval (tp=1241, fp=0, fn=0),
  scored against ground truth recomputed independently from raw npm manifests — not against the
  graph itself.
- The demo advisory's closure was **hand-verified against npm's live registry**: all six direct
  dependents re-fetched and their declared ranges checked, 6/6, 0 contradicted.
- **826** packages in the graph declare a dependency on lodash; only **6** resolve to
  `3.10.1`. That gap is the entire argument for a graph over an index.

## How HydraDB is used, and what breaks without it

The system is deliberately two halves, and the split is documented with measurements in
[SCHEMA.md §0](SCHEMA.md):

**HydraDB carries the semantic layer** — 4,250 GHSA advisories as prose, so a responder can ask
in English and get passages with citations plus HydraDB's own extracted entity/relation paths.
It is genuinely good at this. Queries that name *no package at all*:

| query | top hit | score |
| --- | --- | ---: |
| malicious postinstall script that steals environment variables | `GHSA-f29h-pxvx-f335` — **eslint-config-prettier**, the real July 2025 compromise | 0.913 |
| prototype pollution in a deep merge utility | `GHSA-9g9w-hmvj-5h57` — merge-deep | 0.945 |
| arbitrary file write via path traversal when extracting an archive | `GHSA-884w-698f-927f` — unzipper | 0.954 |

**Without HydraDB the project loses the ability to go from a description to an advisory.** No
`LIKE`, no index, and no traversal over the same corpus returns those — none of the query words
appear in the package names retrieved. That capability is what turns "GHSA-f29h-pxvx-f335" into
something a responder can find at 3am when all they know is what the attack *did*.

**The graph carries reachability**, because HydraDB has no typed-edge store and no N-hop
traversal call — established by probing every v2 endpoint, recorded in
[STATE.md](STATE.md) B1. Exact closure is not a retrieval problem and is not attempted as one.

We measured the inverse too, so the division is evidence rather than assertion: asked the same
six questions, semantic retrieval returns **0 of 8** correct dependents for Q1 and **0 of 46**
typosquats for Q5, while *winning outright* on Q2 (1.00/1.00) where the answer is written down
in prose. That last result is reported rather than buried — it is the honest boundary, and the
reason both halves ship. [EVAL.md §4](EVAL.md).

---

## Setup from a cold clone

Requires **Python 3.12 + [uv](https://docs.astral.sh/uv/)**, **Node 22.5+** (for the built-in
`node:sqlite`), and **pnpm**.

```bash
git clone <this repo> && cd Hydra
cp .env.example .env          # then fill in HYDRADB_API_KEY
```

### Run the console

The 74.6MB graph artifact is committed, so this works immediately — no ingest required:

```bash
cd web
pnpm install
cp ../.env .env.local         # the semantic panel needs the HydraDB key
pnpm dev                      # http://localhost:3000
```

Paste `lodash` / `3.10.1`, then drag `examples/acme-checkout.package-lock.json` onto the drop
zone to see real service exposure.

### Reproduce the eval numbers

```bash
cd ingest
uv sync
uv run pytest -q                                          # 37 tests
uv run blastradius --db ../web/data/blastradius-web.db eval   # -> EVAL.md §3, §3c
```

That scores the held-out advisories against the shipped artifact and reproduces
`precision=1.0000 recall=1.0000 (tp=1241 fp=0 fn=0)` in about 85 seconds. The vector baseline
(`uv run blastradius baseline`) additionally needs the HydraDB key.

### Rebuild the whole graph from scratch

Only needed to regenerate the artifact. Every stage is resumable — it is designed to be killed
and restarted. Budget **~7 hours**, almost entirely waiting on npm's rate limits.

```bash
cd ingest
uv run blastradius names              # 4,291,086 package names
uv run blastradius downloads          # weekly downloads for 2,667,953 unscoped packages
uv run blastradius rank --top-n 25000 # freeze the coverage cap
uv run blastradius latest             # deps + maintainers  (keep concurrency low — see below)
uv run blastradius advisories         # 226,832 OSV records + holdout
uv run blastradius build
uv run blastradius packuments         # version histories for advisory-affected packages
uv run blastradius resolve            # resolve ranges; fill validity intervals
uv run blastradius similar            # SIMILAR_NAME edges
uv run blastradius hydra-ingest       # advisory prose into HydraDB
uv run blastradius webdb              # pruned console artifact + parity proof
```

> **Do not raise ingest concurrency.** Measured: the `latest` stage runs at ~5 packages/min at
> concurrency 10 and **~1,507/min at concurrency 4**. High concurrency trips npm's 429s and
> every worker then sleeps in exponential backoff. Lower is 300× faster.

---

## Coverage and limits

Stated plainly, because silent truncation would make every number above meaningless.

**The dependency graph covers the top 25,000 npm packages by weekly downloads** (rank 25,000 =
14,641 downloads/week). The **advisory corpus is complete** — all 226,832 npm OSV records,
uncapped. Package names were enumerated exhaustively: all 4,291,086.

| | |
| --- | ---: |
| packages | 25,754 |
| versions | 237,198 |
| DEPENDS_ON edges | 81,181 (48,685 resolved) |
| advisories | 226,832 |
| AFFECTED_BY edges | 230,017 |
| SIMILAR_NAME edges | 212,932 |

Known limits:

1. **A dependent outside the top 25,000 is invisible.** Its manifest was never ingested, so it
   cannot appear in a closure. This bounds recall in the real world; it does not affect the
   reported eval numbers, which are scored only over packages inside the cap, with the excluded
   count reported alongside.
2. **Scoped packages are not exhaustively ranked.** npm's bulk downloads endpoint rejects them
   and single lookups measured ~3 req/s — about 22 hours for all 1,623,133. Unscoped packages
   (2,667,953) *are* exhaustively ranked.
3. **Resolution reflects what npm would install today.** A range resolves to the highest known
   satisfying version. Historical resolutions come from uploaded lockfiles via `RESOLVED_TO`,
   which is where resolution actually happens in the real world.
4. **`AFFECTED_BY` validity intervals exist for 1,818 of 230,017 edges** — only where the
   publish dates of both the introduced and fixed versions are known. Unknown bounds are left
   NULL rather than guessed from the disclosure date.
5. **SIMILAR_NAME is a candidate generator, not a classifier.** Precision against OSV's
   malicious labels is 2.3% at distance 1, which is the correct number: most near-neighbours of
   a popular package are legitimate. Recall is the meaningful metric.
6. **The shipped console artifact is pruned** from 152.5MB to 74.6MB — 219,676 `MAL-*` advisory
   records collapse to a name set, and advisory prose is dropped (it lives in HydraDB). Q1–Q6
   answer identically; [EVAL.md §3c](EVAL.md) reproduces the full eval against it to prove it.
7. **Advisories published on/after 2026-01-01 are held out entirely** — 12,933 of them. They
   never entered the graph or the HydraDB index.

## Layout

| path | what |
| --- | --- |
| `ingest/` | Python 3.12 + uv. Registry and OSV ingest, graph build, query engine, eval, baseline. |
| `web/` | Next.js App Router, TypeScript strict. The console. |
| `web/data/` | The shipped 74.6MB graph artifact. |
| `examples/` | A sample `package-lock.json` for the demo. |
| [SCHEMA.md](SCHEMA.md) | The graph, and why a vector index cannot express these questions. |
| [EVAL.md](EVAL.md) | Held-out set, scores, latencies, vector baseline, reproduction. |
| [STATE.md](STATE.md) | Full build log — every phase gate, every bug, every measurement. |

## Dependencies and attribution

**Data.** [npm registry](https://registry.npmjs.org) and downloads API; the
[OSV](https://osv.dev) npm advisory corpus (which aggregates the GitHub Advisory Database),
fetched from Google Cloud Storage. All public, all free — **total API spend: $0**.

**Runtime.** [HydraDB](https://hydradb.com) for semantic retrieval.
Python: `httpx`, `rapidfuzz`, `nodesemver`, `numpy`.
Web: Next.js, React, `node:sqlite` (built in — no native module).

**Typeface.** Computer Modern by Donald Knuth, via
[computer-modern](https://github.com/stevenpetryk/computer-modern) (OFL), self-hosted.

## License

MIT — see [LICENSE](LICENSE).
