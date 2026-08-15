# blastradius — STATE

**Track:** Hack Hydra 02A · **Deadline:** Aug 20 2026 11:59 PM PT · **Started:** Aug 14 2026

Supply-chain blast-radius console. Graph-native answers to "who is exposed, when, and how deep"
when an npm package is compromised.

---

## P0 — Bootstrap
- [x] git init; MIT LICENSE; README skeleton; .gitignore; .env.example
- [x] uv project for `ingest/`, pnpm Next.js app in `web/`
- [x] HydraDB instance provisioned, key in env, round-trip smoke test (write a node, read it back)

**GATE: PASSED.** Database `blastradius` provisioned (org `wqnemcithf`), three nodes written via
`POST /context/ingest`, all reached `indexing_status: completed` in ≤15s, and were read back via
both `POST /query` and `GET /context/relations`.

```
POST /databases -> 200  {"status":"accepted","database":"blastradius"}
GET  /databases/status -> {"scheduler_status":true,"graph_status":true,
                           "vectorstore_status":{"knowledge":true,"memories":true},
                           "ready_for_ingestion":true}

POST /context/ingest -> 202
 [{"id":"g-pkg-leftpad","status":"queued"},
  {"id":"g-ver-leftpad-130","status":"queued","relations_created":1},
  {"id":"g-ver-app-100","status":"queued","relations_created":1}]

GET /context/status  t+15s -> g-pkg-leftpad=completed g-ver-leftpad-130=completed g-ver-app-100=completed

POST /query "what depends on left-pad 1.3.0" -> 200 (server 3243.8ms, mode=thinking)
 chunks: [smoke-file-1, g-ver-app-100, g-ver-leftpad-130, g-pkg-leftpad]
 graph_context.query_paths[0].triplets[0]:
   demo-app 1.0.0 [PRODUCT] --depends on--> left-pad 1.3.0 [PRODUCT]
   context: "demo-app 1.0.0 depends on version 1.3.0 of the left-pad npm package."

GET /context/relations?id=g-ver-leftpad-130 -> 200 (131.8ms)
   left-pad 1.3.0 [PRODUCT] --published on--> 2018-04-05 [CONCEPT]  confidence 0.8
```

## P1 — Schema
- [x] Read HydraDB docs: data model, write path, traversal API, cost/latency
- [x] SCHEMA.md: Package/Version/Maintainer/Advisory/Lockfile/Service nodes;
      DEPENDS_ON / PUBLISHED_BY / AFFECTED_BY / RESOLVED_TO / SIMILAR_NAME edges
- [x] Time first-class: validity intervals on RESOLVED_TO + AFFECTED_BY
- [x] Justify each choice against "why a vector index cannot answer this"

**GATE: PASSED.** [SCHEMA.md](SCHEMA.md) written (7 node kinds, 7 edge types, interval
semantics, per-question vector-index argument). 20-node fixture at
`ingest/fixtures/mini.json` loads; 3-hop reverse traversal correct.

```
$ uv run pytest -q
..............                                                           [100%]
14 passed in 0.01s

$ uv run ruff check . --output-format=concise
All checks passed!
$ uv run ruff format --check .
4 files already formatted
$ uv run mypy src tests
Success: no issues found in 3 source files
```

The gate case — `reverse_dependents("ver:left-pad@1.3.0")`:

```
ver:mid-lib@2.0.0   depth 1   via mid-lib@2.0.0  --DEPENDS_ON(^1.3.0 -> 1.3.0)--> pkg:left-pad
ver:top-app@3.1.0   depth 2   via top-app@3.1.0  --DEPENDS_ON(^2.0.0 -> 2.0.0)--> pkg:mid-lib
ver:demo-app@1.0.0  depth 3   via demo-app@1.0.0 --DEPENDS_ON(~3.1.0 -> 3.1.0)--> pkg:top-app
```

`top-app@3.1.0` also dev-depends on `pkg:left-pad` but resolves to `1.2.0`, and is correctly
excluded from the `1.3.0` closure at depth 1 — the discriminating case a similarity index gets
wrong. Querying `1.2.0` instead returns it at depth 1, as expected.

## P2 — Ingest at ecosystem scale
- [x] npm replication: **all 4,291,086 package names** pulled from `replicate.npmjs.com/_all_docs`
      (resumable, checkpointed). Versions + maintainers come from the `latest` stage, which is
      gated on ranking — see B4.
- [x] OSV + GitHub Advisory Database → affected package/version ranges
      (226,832 advisories, 230,023 AFFECTED_BY edges, 6s)
- [x] SIMILAR_NAME edges (damerau-levenshtein ≤2 vs top-N by downloads) — implemented and
      validated; final run pending the full ranking
- [x] Hold out advisories after cutoff date → eval set, recorded in [EVAL.md](EVAL.md)
      (cutoff `2026-01-01`; 12,933 held out — 10,164 MAL-* + 2,769 GHSA; enforced once at
      load time so it cannot leak at a call site)
- [ ] Log throughput, node/edge counts, cost here

**GATE:** graph loaded; counts reported; a real advisory's reverse-dep closure hand-checked vs npm.

## P3 — Query engine
- [ ] Q1 transitive reverse-dep closure (blast radius) for pkg@version
- [ ] Q2 which version introduced the vulnerability
- [ ] Q3 which lockfiles/services resolved the bad version while it was live (temporal)
- [ ] Q4 packages sharing a maintainer with the compromised one
- [ ] Q5 likely typosquats near the compromised name
- [ ] Q6 blast radius rollup: services exposed, depth, first-exposure time
- [ ] Upload package-lock.json → real Service/Lockfile nodes
- [ ] p50/p95 latency measured per query

**GATE:** all six correct on the P2 hand-checked advisory; latencies recorded.

## P4 — Eval harness
- [ ] Score held-out advisories: precision, recall, p50/p95, cost/query → EVAL.md table
- [ ] Honest baseline: what a vector/embedding approach returns for the same six questions
- [ ] Document every limitation and coverage cap (also in README)

**GATE:** EVAL.md has real numbers, reproducible via one documented command.

## P5 — Console
- [ ] Next.js: paste pkg@version or upload lockfile → interactive graph with traversal path visible
- [ ] Timeline scrubber for the temporal query
- [ ] Live latency on every query
- [ ] Deploy to Vercel; verify production URL end to end

**GATE:** production URL returns correct results for the demo advisory in under a second.

## P6 — Harden
- [ ] ruff + mypy clean on `ingest/`; tsc strict + lint clean on `web/`; tests pass; build succeeds
- [ ] Re-run full eval against production
- [ ] README: what/problem/cold-clone setup/HydraDB usage/deps/attribution/license

**GATE:** cold clone + README instructions reproduce the eval numbers.

## P7 — Submission
- [ ] DEMO_SCRIPT.md — ≤3:00, ordered: problem, project, live demo, HydraDB usage
- [ ] SUBMISSION.md — every Google Form field pre-written
- [ ] Pre-submission checklist run item by item with evidence

**GATE:** every checklist line checked with evidence.

---

## Log

- **Aug 14, iter 1** — Empty dir. Toolchain verified: uv 0.11.25, pnpm 11.20.0, node 24.15.0,
  Python 3.12.13, git 2.50.1. All four `hydradb.com` subdomains resolve (dashboard→Vercel,
  api→AWS ELB us-east-1, docs→Cloudflare), so HydraDB is a real service, not a stub.
- **Aug 14, iter 1** — P0 complete, gate passed. Read the HydraDB v2 agent guide end to end and
  probed every endpoint against the live API. HydraDB is a *context substrate* — semantic +
  BM25 retrieval with an auto-extracted knowledge graph — not a property-graph database. It
  genuinely extracted `demo-app 1.0.0 --depends on--> left-pad 1.3.0` from plain text, which is
  impressive and is the thing it's actually good at. But declared typed edges are write-only
  (B1) and there is no traversal call, so P1/P3 as specified cannot be built on it alone.
  Architecture fork raised with the user; defaulting to the hybrid design next iteration if no
  answer. P1 doc work that doesn't depend on the fork can proceed regardless.
- **Aug 14, iter 1** — Architecture fork answered: **hybrid**, coverage capped at **top ~25k npm
  packages by downloads + the full OSV/GHSA advisory set**. B1 closed as a decision. P1 complete
  and gate passed in the same iteration. Writing the traversal first paid off immediately: the
  `resolved`-version discriminator (top-app dev-depends on left-pad@1.2.0, not 1.3.0) is exactly
  the case that separates a real closure from a plausible one, and it now has a regression test.
  Half-open interval semantics chosen so a boundary touch is not an overlap.
- **Aug 14, iter 2** — P2 underway. All 4,291,086 npm names pulled. OSV npm corpus loaded:
  226,832 advisories / 230,023 AFFECTED_BY edges in 6s. Composition was the surprise —
  **219,676 (96.8%) are `MAL-*` malicious-package reports**, only 7,155 GHSA. MAL entries name
  222k mostly-zero-download packages, so they carry no blast radius, but they are labeled
  ground truth for the typosquat question (Q5) — a real eval set rather than a toy one.
- **Aug 14, iter 2** — Three real bugs caught by looking at the data instead of trusting it:
  (1) a record in the npm bucket had `ecosystem: "Hex"` (`EEF-CVE-2026-56812`, package
  `phoenix`), so ecosystem must be filtered *per affected entry*, not per file — the corrected
  count is 226,832 of 226,833 files, and that one rejection is the Hex record;
  (2) OSV `withdrawn` was not being captured, so retracted advisories (e.g. the express
  "Withdrawn Advisory" GHSA-pj86-cfqh-vqx6) would have counted as live exposure;
  (3) MAL `details` are per-scanner boilerplate — 130MB of the 136MB details column — dropped,
  taking the DB from 239.5MB to 84.1MB and back under GitHub's 100MB file limit.
  Multi-window ranges verified preserved (`@solana/web3.js` GHSA-8m45-2rjm-j347 has 92 disjoint
  windows; collapsing to min/max would mark the safe gaps as vulnerable).
- **Aug 14, iter 2** — Networking: every npm host was intermittently failing (~30%) until I
  traced it to a dead IPv6 route on this machine — `EHOSTUNREACH` on every AAAA plus
  `ETIMEDOUT` on IPv4, while `example.com` and Google storage stayed clean. Binding httpx to
  an IPv4 local address fixed it. Also: `_all_docs?limit=25000` returns 400; 5000 is the
  working page size. And the download stages now stream through a worker pool — the old
  approach built one coroutine per item, which 1.62M scoped names would have OOM'd.
- **Aug 14, iter 3** — Three correctness bugs, all found by checking output against reality
  rather than trusting green exit codes:
  1. **False negatives cached as permanent.** `/latest` was returning 429 (Cloudflare) for
     ~50% of requests, and the code recorded those as `{"missing": true}` — which the resume
     logic then skips forever. 990 of 2000 real packages (chalk, ajv, acorn) were written off
     as nonexistent. `_get` now returns a three-way result (response / `"gone"` / transient),
     honours `Retry-After`, and callers persist **nothing** on transient failure so the next
     run retries. Purged the 990 poisoned records.
  2. **Moving-target coverage cap.** `latest` and `build` each recomputed the top-N from
     `downloads.ndjson` while that file was still being appended to, so they saw different
     sets — only 505 of 2000 overlapped, which is why the first build produced 2000 packages
     but 505 versions. The cap is now frozen once to `ranked.tsv` by a new `rank` stage, and
     both stages read that.
  3. **Fabricated dependency resolution.** `resolve_latest_versions` set `resolved = latest`
     unconditionally. `chalk` declares `ansi-styles: ^4.1.0`; marking that resolved to 7.0.0
     invents an edge that does not exist *and* hides the real one — blast radius wrong in
     both directions. Now checks the declared range with npm semver semantics
     (`nodesemver.satisfies`) and leaves the edge NULL when the range excludes latest
     (198/6805 on the validation slice). True historical resolution comes from lockfiles via
     RESOLVED_TO, which is where resolution actually happens.
- **Aug 14, iter 3** — Validation slice (top 2,000) builds coherently: 2,000 packages /
  1,999 versions / 2,457 maintainers / 6,805 DEPENDS_ON. Reverse traversal verified against
  the SQL reverse index — `call-bound@1.0.4` returns 8 transitive dependents across 2 depths
  (`array.prototype.*`, all genuine). npm rate-limits hard: sustained ~199 names/s on bulk
  downloads and ~3/s on `/latest` at concurrency 4, with zero failures once concurrency is
  kept low. Full sweep is ~3.5h + ~2.3h, running in background.
- **Aug 15, iter 4** — SIMILAR_NAME built with true Damerau-Levenshtein (transposition costs
  1, so `lodash`→`lodahs` is 1 — plain Levenshtein says 2 and misses the commonest squat).
  rapidfuzz does ~21M pairs/s; bucketing on length (DL≤2 implies `abs(len(a)-len(b))≤2`) turns
  a 107B-pair sweep into 164s over all 4,291,086 names.
  It immediately caught real OSV-confirmed malicious packages: `axois`/`axioss`→axios,
  `bulebird`/`blueibrd`/`blubird`→bluebird, and eleven chalk squats (`cahlk`, `chakl`,
  `chalq`, `chlak`, `shalk`, …).
- **Aug 15, iter 4** — First pass produced 115,545 edges, of which the distance-2 tier was
  junk for short names: a 1-character package is within 2 edits of *every* 2-character name,
  giving pairs like `-` ~ `ai`/`0x`/`ci`. Threshold is now length-relative (distance 1 only
  for names ≤6 chars, minimum name length 3), which cut the set to 12,264 and left genuinely
  plausible pairs (`@apollo/server ~ apollo-server`, `aframe-testras ~ aframe-extras`).
- **Aug 15, iter 5** — EVAL.md written with the held-out set recorded: cutoff `2026-01-01`,
  12,933 advisories withheld (10,164 MAL-* + 2,769 GHSA), 650 OSV-withdrawn excluded from
  both sides, range 2026-01-01…2026-08-14. Two caveats recorded rather than smoothed over:
  the held-out set is 79% MAL-* (zero-download packages that carry no blast radius, so
  Q1/Q3/Q6 and Q5 must be scored separately, not blended), and only advisories whose package
  is inside the coverage cap can be scored — that excluded count gets reported next to every
  score, because an advisory for a package never ingested is a coverage limit, not a
  retrieval miss.
- **Aug 15, iter 5** — npm's throttling is not steady: the bulk sweep drifts between ~11 and
  ~200 names/s over hours, while a hand probe of the same endpoint returns 200 in <500ms
  throughout. So it is per-connection shaping, not a global block, and the job is best left
  to run rather than tuned. 61% priced.
- **Aug 15, iter 5** — P3 query engine written and tested (29 tests pass). All six questions
  run against the real graph; the temporal one is the load-bearing result: with a lockfile
  that pinned `call-bound@1.0.4` at 09:01 and reverted at 09:05, Q3 returns **1 exposed
  service for the 09:00–09:06 window and 0 for 10:00–11:00**. A present-tense view of the
  same graph shows the service clean, which is exactly the failure SCHEMA.md §3 predicted.
  Measured latencies on the partial graph: Q1 1.38ms, Q2 0.72ms, Q3 0.11ms, Q4 2.83ms,
  Q5 13.95ms, Q6 0.34ms. Reverse traversal is level-batched (one statement per BFS depth,
  whole frontier bound at once) rather than one statement per node.
  Q1 also returns the traversal path as evidence — `mid-lib@2.0.0 --DEPENDS_ON(^1.3.0)-->
  left-pad@1.3.0` — which is what the console renders as proof.
  Truncation is declared in `notes`, never silent.
- **Aug 15, iter 6** — Caught the worst bug of the build so far by auditing the graph instead
  of trusting the passing tests. `depends_on.resolved` was NULL on 88.4% of edges; of the ones
  whose target was *inside* the cap, 20.1% failed to resolve, all of them major-version pins
  (`acorn-node` wants `acorn@^7.0.0`, we only stored `latest=8.18.0`). Because
  `blast_radius` matches `dst_pkg = X AND resolved = V`, the closure could only ever answer
  for a package's **current** version — so any compromise of an older major would have
  returned "nobody is affected". The earlier `call-bound@1.0.4` test passed only because
  1.0.4 happened to be latest. Fixed by resolving against all known versions via
  max-satisfying; `blast_radius(acorn@7.4.1)` went 0 → 3 dependents.

## Blockers

### B6 + B7 — RESOLVED (Aug 15, iter 6)

Both fixed by the packument pass plus `resolve_all_versions`, which resolves declared ranges
against **every** known version (npm's own max-satisfying rule) instead of only latest.

Evidence — edges that previously could not resolve now do, to the correct older version:

```
acorn-node            -> acorn        wants ^7.0.0    resolved=7.4.1   (latest=8.18.0)
acorn-dynamic-import  -> acorn        wants ^6.0.0    resolved=6.4.2   (latest=8.18.0)
apollo-server-express -> body-parser  wants ^1.19.0   resolved=1.20.6  (latest=2.3.0)
artillery             -> async        wants ^2.6.4    resolved=2.6.4   (latest=3.2.6)
```

And the query that used to return nothing now returns the right answer:

```
blast_radius(acorn@7.4.1)  [non-latest] -> 3 dependents  (was 0)
blast_radius(acorn@8.18.0) [latest]     -> 16 dependents
```

B6 closed too: AFFECTED_BY intervals are now dated from real publish times, e.g. `bootstrap`
GHSA-3wqf-4x89-9g79 vulnerable from `2018-01-18T18:34:21Z` to `2018-07-12T16:38:44Z`.
`valid_from` is when the introduced version *shipped*, not when the advisory was disclosed —
which is what lets a lockfile predating the GHSA still test positive.

Seven regression tests pin this (`tests/test_resolution.py`), including the baseline showing
latest-only resolution returning 0, that a range no version satisfies stays NULL rather than
snapping to something close, and that `git:`/`file:` specifiers are left unresolved.

**Remaining scope note:** only 62 advisory-affected packages are inside the current top-2,000
cap, so just 56 of 230,017 AFFECTED_BY rows carry intervals so far. Both numbers grow with the
full 25k cap; the packument pass is re-run after the ranking completes.

#### Original finding — blast radius only answered for a package's *latest* version (CRITICAL)

Measured on the current graph: of `depends_on` edges whose target is inside the cap, **79.9%
resolve and 20.1% do not**, and the failures are all major-version pins —
`acorn-node` wants `acorn@^7.0.0` but the only version row we hold is `latest=8.18.0`;
`apollo-server-express` wants `body-parser@^1.19.0` against `latest=2.3.0`.

Root cause: the `latest` stage stores exactly one version per package, so a declared range can
only resolve when the package's *current* version satisfies it. `blast_radius` then matches on
`dst_pkg = X AND resolved = V`, so it can only find dependents when **V is the latest version**.

Consequence, stated plainly: if a compromise affects an older major, every dependent pins that
older major, none of them resolve, and the closure comes back **empty** — the single most
important query silently answers "nobody is affected" for exactly the incidents that matter
most. The earlier `call-bound@1.0.4` test passed only because 1.0.4 *was* latest.

**Fix (bounded, and it subsumes B6):** the `fetch_packuments` stage already retrieves full
packuments, which carry every version plus `time`. Run it for advisory-affected packages inside
the cap (~thousands, not 25k), store their full version lists, and resolve declared ranges
against all known versions rather than just latest. That makes the closure correct for any
version of any package that actually has an advisory — precisely the use case — and fills in
`published_at` for B6 at the same time.

Until that lands, any blast-radius number for a non-latest version is a floor, not a count, and
must not be reported as recall.


### B6 — RESOLVED, see the combined B6+B7 entry above

#### Original finding — version publish dates not loaded, so Q2 could not date the vulnerable version

`introducing_version` returns the correct `introduced`/`fixed` bounds, but
`introduced_published_at` comes back `NULL` on real data because only latest-version rows carry
a `published_at`. Per-version dates live solely on the *full* packument (`time`), which the
`fetch_packuments` stage retrieves — that stage exists and is wired, but has not been run.

Also leaves `AFFECTED_BY.valid_from`/`valid_to` NULL, so the advisory side of the temporal
story is currently untimed (the lockfile side, `RESOLVED_TO`, is fully timed and working).

**Fix:** run `fetch_packuments` for the ~5k advisory-affected packages inside the coverage cap
after the ranking completes, then backfill both columns. Bounded work — not all 25k.


### B5 — `rank_gap` does not discriminate among unranked candidates (quality limit, not a bug)

`rank_gap` is `candidate_rank - target_rank`, and every candidate outside the coverage cap gets
the same sentinel rank, so filtering at `>1M` and `>4M` returns an identical 4,057 edges. The
signal is effectively binary (ranked vs unranked) rather than graded.

Consequence for the eval: SIMILAR_NAME must be reported as a **candidate generator**, not a
classifier. Precision against OSV `MAL-*` labels is 2.3% at distance 1 — which is the expected
and correct number, because most distance-1 neighbours of a popular package are perfectly
legitimate (`angulartics2 ~ angulartics`), and the MAL label set only covers squats that were
caught. Recall is the meaningful metric and is exhaustive by construction; against just the
top-2,000 it already catches 207 distinct known-malicious packages.

**Refinement available:** weekly downloads are already collected for every unscoped candidate,
so "5 downloads/week and one edit from lodash" is a far sharper signal than rank alone. Fold
the candidate's own download count into the ranking the console shows, and report both numbers
in EVAL.md rather than claiming precision the edge type cannot deliver.


### B4 — npm downloads API cannot be enumerated for 1.62M scoped packages (shapes P2 coverage)

The bulk downloads endpoint takes 128 names per call but **rejects scoped packages outright**
(`scoped packages are not currently supported in bulk lookups`). Of 4,291,086 names,
**2,667,953 are unscoped** (20,844 bulk calls — fine) and **1,623,133 are scoped**, which would
need one call each. Measured:

```
sequential, 150ms spacing:  12/12 -> 200,  avg 513ms          (API is healthy at low rate)
pooled concurrency=32:    1200 reqs, 393.7s =  3.0/s, 903 err (75% failure)
pooled concurrency=96:    1200 reqs, 466.1s =  2.6/s, 838 err (70% failure — worse)
```

Failure rate *rises* with concurrency, so this is server-side rate limiting, not local. At a
sustainable ~20 req/s the scoped sweep alone is ~22 hours, which does not fit the remaining
budget alongside P3–P7. Not a code bug and not fixable by tuning.

**Plan (next iteration):** rank all 2.67M unscoped exhaustively via bulk, then build a bounded
scoped *candidate* set from three sources — scoped packages appearing as dependencies of ranked
packages, scoped packages named in the advisory corpus, and npm's own popularity search — and
price only those. The methodology and the measured miss rate against known-popular scoped
packages both go in README and EVAL.md. No silent truncation.



### B1 — RESOLVED by architecture decision (Aug 14, iter 1)

**Decision: hybrid.** The deterministic dependency/temporal graph lives in an embedded local
store built from real npm + OSV data and answers Q1–Q6 exactly. HydraDB holds the same corpus as
system of record and carries the semantic layer it is actually built for: advisory
understanding, maintainer and typosquat similarity, and the extracted provenance paths the
console renders as proof. README and DEMO_SCRIPT must state plainly which answers only HydraDB
produces, so its role is not overstated *or* undersold. Finding preserved below as the
justification.

**Coverage decision:** top ~25,000 npm packages by download count + the complete OSV/GHSA
advisory set for npm. Cap stated in README and EVAL.md; no silent truncation anywhere.

---

#### Original finding — HydraDB has no typed-edge store and no traversal API

P1 specifies typed edges with properties (`DEPENDS_ON` with semver range + resolved version,
`AFFECTED_BY` with version bounds, `RESOLVED_TO` with validity intervals) and P3 specifies
multi-hop traversal over them. HydraDB does not provide either primitive. Evidence:

1. **Declared relations are not retrievable.** Ingesting with
   `document_metadata: [{id, relations:{ids:[...], properties:{edge:"DEPENDS_ON", range:"^1.3.0",
   resolved:"1.3.0", valid_from:"..."}}}]` returns `relations_created: 1` — but the declared
   target id never appears in `GET /context/relations` for that source
   (`declared-id present? false`), and `additional_context` came back **empty `{}`** even with
   `mode:"thinking"` + `query_forceful_relations:true`, which the docs say is the only way
   forceful relations surface. So edge properties cannot be stored or read back.
2. **`/context/relations` returns LLM-extracted triplets, not my edges.** For
   `g-ver-leftpad-130` it returned `left-pad 1.3.0 [PRODUCT] --published on--> 2018-04-05
   [CONCEPT]`, confidence 0.8 — a semantic triplet mined from the document text, with
   natural-language predicates, entity ids, and a chunk citation. Useful, but probabilistic and
   not the typed edge I declared.
3. **No traversal primitive exists.** The full v2 surface is `/databases`, `/context/ingest`,
   `/context/status`, `/context/inspect`, `/context/list`, `/context/relations`, `/query`,
   `/feedback`. There is no "N-hop neighbors of node X" call. `/query` is text-driven retrieval;
   `graph_context.query_paths` returns paths the retriever happened to touch for that query
   string, not a closure.

**Consequence:** an exact transitive reverse-dependency closure — the core of Q1, Q3 and Q6 —
cannot be expressed as a HydraDB query. Awaiting an architecture decision (see Log, iter 1).

### B2 — `app_knowledge` ingest path fails silently

`POST /context/ingest` with `app_knowledge` returns `202` and `success_count: 3`, but the sources
never index: `GET /context/status` reports `FILE_NOT_FOUND` / "ID not found" indefinitely (polled
120s), and `POST /context/list` shows `total: 0`. Reproduced across four id formats (plain,
kebab, colon-separated, `@`-containing) and with/without `url`. The `documents` file-upload path
with `document_metadata` works correctly on the same database, so **all ingest uses the file
path**. Not blocking; recorded so the workaround isn't mistaken for a preference.

### B3 — "full npm" (P2) is not reachable at this ingest cost

npm is ~3.5M packages / ~40M versions. Ingest is LLM-extraction-backed (~15s to index a 3-doc
batch) and `mode:"thinking"` queries cost 3.2s server-side. Full-ecosystem ingest is neither
time- nor cost-feasible in 6 days. P2 will need a documented coverage cap rather than a silent
one. Exact cap to be set once B1 is resolved.
