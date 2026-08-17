# blastradius — evaluation

This file records the held-out eval set (P2) and, once P4 runs, the scored results. Sections
marked **pending** have not been measured yet and contain no numbers — nothing here is
projected, estimated, or filled in ahead of a real run.

---

## 1. Held-out advisory set

**Cutoff: `2026-01-01`.** Every npm advisory with `published >= 2026-01-01` is stored with
`withheld = 1` and is excluded from every query path. The graph is built only from advisories
published strictly before the cutoff.

The cutoff is enforced in one place — `osv.py:HOLDOUT_CUTOFF`, applied at load time in
`load_advisories` — and the resulting flag is what every query filters on, so the eval set
cannot leak into an answer by being forgotten at some call site.

| | count |
| --- | ---: |
| npm advisories ingested | 226,832 |
| **held out (eval set)** | **12,933** |
| usable by the graph | 213,899 |
| withdrawn by OSV (excluded from both) | 650 |

Held-out set composition:

| kind | count |
| --- | ---: |
| `MAL-*` (malicious-package reports) | 10,164 |
| `GHSA-*` and other | 2,769 |

Held-out GHSA severity distribution:

| severity | count |
| --- | ---: |
| MODERATE | 1,156 |
| HIGH | 1,096 |
| CRITICAL | 299 |
| LOW | 217 |
| unlabelled | 1 |

Date range of the held-out set: **2026-01-01 … 2026-08-14**.

### Why this cutoff

It is recent enough to leave 8 months of advisories unseen, and old enough that the remaining
213,899 still describe the ecosystem as it actually is. Picking a cutoff far in the past would
have produced a larger eval set but a graph too stale to answer questions about current
packages; picking a very recent one would have produced an eval set too small to say anything.

### Two honest caveats about this set

1. **`MAL-*` dominates it (10,164 of 12,933).** Those name packages with essentially zero
   downloads by construction, so they carry no reverse-dependency blast radius. They are the
   right ground truth for the typosquat question (Q5) and the wrong ground truth for the
   closure questions (Q1/Q3/Q6). Results are reported separately per question rather than as
   one blended score, because a single number over this mix would be meaningless.
2. **Only a fraction of held-out advisories name a package inside the coverage cap.** At the
   current partial cap this is 23; the final number against the full top-25,000 cap is reported
   in §3 once the ingest completes. Questions are scored only over the advisories whose package
   is actually in the graph, and **the excluded count is reported alongside every score** — an
   advisory for a package we never ingested is a coverage limitation, not a retrieval failure,
   and collapsing the two would flatter the numbers.

---

## 2. Coverage cap

See [README](README.md) for the user-facing statement. In short: the dependency graph covers the
top **25,000** npm packages by weekly downloads. The advisory corpus is **complete** — all
226,832 npm records, uncapped.

The cap is frozen once to `data/ranked.tsv` and recorded in the database's `ingest_meta` table,
so the number quoted here and the number actually in the graph cannot drift apart.

**Scoped-package limitation.** npm's bulk downloads endpoint rejects scoped packages
(`scoped packages are not currently supported in bulk lookups`) and single lookups are rate
limited to roughly 3 req/s, making an exhaustive sweep of all 1,623,133 scoped names about 22
hours. Unscoped packages (2,667,953) are therefore ranked exhaustively, while scoped packages
are ranked from a bounded candidate set. The measured miss rate against known-popular scoped
packages is reported in §3.

---

## 3. Results

Reproduce with one command (see §5): `uv run blastradius eval`.

### 3a. Closure accuracy — Q1 / Q6

**1,404 held-out advisories scored.** Ground truth is an independent recomputation from the raw
npm manifests, using separate semver logic that never touches the graph-build code.

| metric | value |
| --- | ---: |
| micro precision | **1.0000** |
| micro recall | **1.0000** |
| F1 | **1.0000** |
| true positives | 1,241 |
| false positives | **0** |
| false negatives | **0** |

**Read this number correctly.** It says the graph agrees exactly with an independent
implementation *over the same universe* — the 25,754 packages inside the coverage cap. It does
**not** say the graph finds every dependent on npm. Any dependent outside the cap is invisible
to the closure by construction, and that is a coverage limitation (§2), not a retrieval score.
npm has ~4.29M packages; this graph covers 25,754 of them.

**A correction worth recording.** The first full run reported recall 0.9710 with 37 false
negatives. Inspecting them showed every one was a package *outside* the cap: `latest.ndjson` is
append-only and had accumulated 505 packages from earlier runs at smaller caps, so the oracle
was crediting dependents the graph had correctly never ingested. The bug was in the eval, not
the system — the graph was never modified. The oracle now restricts to in-cap sources, which is
the only way the comparison means anything. Both numbers are reported here rather than only the
better one, and every remaining disagreement is dumped to `data/eval_results.json` under
`disagreements` so the claim stays falsifiable.

Excluded and reported rather than silently dropped:

| counter | value | why |
| --- | ---: | --- |
| `held_out_ghsa_in_cap` | 2,224 | held-out GHSA advisories naming an in-cap package |
| `skipped_no_known_affected_version` | 820 | affected window names no version we hold — a coverage limit, not a miss |
| scored | 1,404 | the remainder |

### 3b. Latency — all six questions, on the full graph

Measured over the same run; n is the number of timed invocations.

| question | n | p50 | p95 |
| --- | ---: | ---: | ---: |
| Q1 blast radius | 1,404 | **0.006 ms** | 0.214 ms |
| Q2 introducing version | 4,212 | 0.007 ms | 0.168 ms |
| Q3 exposed during (temporal) | 4,212 | **0.003 ms** | 0.004 ms |
| Q4 shared maintainer | 4,212 | 0.035 ms | 0.463 ms |
| Q5 typosquats | 4,212 | 9.842 ms | 11.508 ms |
| Q6 rollup | 4,212 | 0.013 ms | 0.033 ms |

Q5 is three orders of magnitude slower than the rest because it scans 212,932 SIMILAR_NAME
edges rather than following an index; it is still comfortably inside the sub-second budget.

### 3c. Typosquat recall — Q5, against held-out `MAL-*` records

| metric | value |
| --- | ---: |
| held-out malicious packages | 10,163 |
| flagged by SIMILAR_NAME | 207 |
| recall | **2.04%** |
| p50 / p95 lookup | 0.002 ms / 0.004 ms |

**This number is low and it is reported as-is.** SIMILAR_NAME only relates a candidate to the
25,000 ranked packages, so a malicious package that squats something *outside* the cap — or
that is not a name-similarity attack at all (a plausible-sounding new name, a compromised
legitimate package, dependency confusion) — cannot be flagged by construction. The 2.04% is
therefore a measure of how much of the malicious corpus is "typosquat of a popular package",
not a measure of detector quality on that subset. Raising it would mean widening the cap, not
tuning the matcher. See STATE.md B5.

### 3d. Cost per query

**$0.** Every query in §3a–§3c runs against the local SQLite graph with no network call and no
API spend. The HydraDB semantic layer (§3b above) is the only networked path, at ~735 ms mean
server-side and no per-query charge on the hackathon instance. Ingest cost is in STATE.md; it
is also $0, bounded by rate limits rather than billing.

## 3b. HydraDB semantic layer — measured

The corpus is **4,250 usable GHSA advisories** (held-out, withdrawn, and `MAL-*` records all
excluded — the eval set must not leak into the semantic index any more than into the graph).

Ingest, measured rather than assumed: 25 documents per multipart request in 2.0s, reaching
`completed` — including HydraDB's own graph extraction — in 45s. A 40-document random sample
re-checked afterwards was 40/40 `completed`. Ingest is rate-limited with HTTP 429, so batches
honour `Retry-After`; the full push took 53 minutes wall-clock, almost all of it backoff.

Retrieval quality, `mode="fast"`, on queries that name **no package at all**:

| query | top hit | score |
| --- | --- | ---: |
| malicious postinstall script that steals environment variables and credentials | `GHSA-f29h-pxvx-f335` — eslint-config-prettier, the real July 2025 compromise | 0.913 |
| prototype pollution in a deep merge utility | `GHSA-9g9w-hmvj-5h57` — merge-deep | 0.945 |
| regular expression denial of service from a catastrophic backtracking pattern | `GHSA-rmvr-2pp2-xj38` — @octokit | 0.952 |
| arbitrary file write via path traversal when extracting an archive | `GHSA-884w-698f-927f` — unzipper | 0.954 |
| compromised maintainer account published a backdoored release | `GHSA-8j7x-pr59-m5h8` — the Sept 2020 malicious-package cluster | 0.835 |

Server latency across those queries: **min 527ms, mean 735ms, max 853ms**.

This is the half of the problem the graph cannot do. None of those query strings share a token
with the package names they correctly retrieve, so no `LIKE`, no index, and no traversal over
the same corpus returns them. It is also why `mode="fast"` is the console default: `thinking`
measured 3.2s server-side in P0, which alone would blow the sub-second budget.

### 3c. The shipped artifact reproduces these numbers

The console ships a pruned 74.6MB database (see [README](README.md#coverage-and-limits)). Running
the identical eval against it rather than the 152.5MB working copy:

```
uv run blastradius --db web/data/blastradius-web.db eval

1404 closure cases scored
closure micro precision=1.0000 recall=1.0000 f1=1.0000 (tp=1241 fp=0 fn=0)
Q1 p50 0.006ms  Q3 p50 0.002ms  Q5 p50 6.528ms  Q6 p50 0.014ms
```

Identical precision, recall, and case count. The prune removes nothing that changes an answer,
and this is the measurement of that claim rather than a promise.

**One metric does not survive the prune, and it is reported rather than quietly omitted:** the
typosquat recall in §3 requires the individual `MAL-*` advisory records, which the artifact
collapses into a `malicious_package` name set. Run against the artifact, Q5 reports
`held_out_malicious_packages: 0`. The console does not need those records — it only asks "is
this name flagged?" — but the *eval* does, so the typosquat number must be reproduced against
the full database.

## 4. Vector/embedding baseline

Reproduce with: `uv run blastradius baseline`.

**This is not a strawman.** The baseline is HydraDB — a production semantic retrieval system
that §3b shows is genuinely excellent at what it is for. Both systems get the same question
about the same compromised package (`lodash@3.10.1`, GHSA-35jh-r3h4-6jhm) over the same corpus,
and both are scored against the graph's verified ground truth, which was hand-checked against
npm's live registry in P2. Package-name extraction from the retrieval results is deliberately
generous: any known package name appearing anywhere in a returned chunk counts as retrieved.

| question | truth | retrieved | overlap | precision | recall | graph | vector |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Q1 blast radius | 8 | 20 | **0** | 0.00 | 0.00 | 2.9 ms | 636 ms |
| Q2 introducing version | 4 | 4 | **4** | **1.00** | **1.00** | 2.4 ms | 531 ms |
| Q3 temporal exposure | 0 | 28 | 0 | — | — | 1.3 ms | 572 ms |
| Q4 shared maintainer | 303 | 29 | 10 | 0.34 | 0.03 | 38.8 ms | 484 ms |
| Q5 typosquats | 46 | 30 | **0** | 0.00 | 0.00 | 67.1 ms | 407 ms |
| Q6 rollup | 0 | 39 | 0 | — | — | 0.3 ms | 1495 ms |

### What this actually shows

**Q1 — zero overlap out of eight.** Semantic search returned 20 packages, none of which is a
dependent of `lodash@3.10.1`. It retrieves packages that are *about* lodash-ish things; being
depended upon is not a kind of resemblance. And closure is transitive: even a perfect depth-1
result could not compose to depth 2, because ranked retrieval has no composition operator.

**Q2 — the vector approach wins outright, 1.00/1.00.** Worth stating plainly rather than
burying: when the answer is written down in prose ("versions prior to 4.17.21 are affected"),
retrieval finds it, and the graph's advantage evaporates. This is the honest boundary of the
argument. It is also why the system keeps both halves instead of picking one.

**Q4 — 10 of 303, precision 0.34.** Retrieval surfaces a few packages that happen to be
discussed near each other, and misses 97% of the maintainer's actual catalogue. A maintainer's
packages are usually unrelated in purpose, so similarity is the wrong axis entirely — this is
the query where embeddings return a confidently wrong answer rather than an empty one.

**Q5 — zero of 46.** The clearest structural result. `1odash` and `lodash` are one keystroke
apart and mean the *same thing* to an embedding model, which is precisely why the attack works.
Any embedding good enough for search is, by construction, blind to typosquatting.

**Q3 and Q6 — not demonstrated, and marked as such.** The graph's truth set is empty here
because no lockfile is loaded in the eval database, so "0 overlap" is vacuous and is **not**
counted as a win. The claim that these are inexpressible rests on structure, argued in
[SCHEMA.md](SCHEMA.md) §4: Q3 needs interval algebra over two independent time ranges, and Q6
needs shortest-path depth plus a min over interval starts — properties of a traversal, with
nothing to embed. The console demonstrates both against a real uploaded lockfile.

**Latency.** The graph answers in 0.3–67 ms; retrieval takes 407–1495 ms. Two to four orders of
magnitude, before correctness is considered.

### The fair summary

Similarity search is the right tool for *"what is this advisory about?"* and the wrong tool for
*"who is exposed, how deep, and since when?"* — and this table is the measurement rather than
the assertion. The system uses HydraDB for the former (§3b: 0.91–0.95 on queries naming no
package) and the graph for the latter. Neither half is decoration.

## 5. Reproduction

Everything in §3 and §4 regenerates from a cold clone with the pipeline below. `eval` and
`baseline` are the two that produce the numbers in this file; the stages above them build the
graph they run against and are individually resumable.

```bash
cp .env.example .env          # then fill in HYDRADB_API_KEY
cd ingest
uv run blastradius names              # 4.29M package names
uv run blastradius downloads          # weekly downloads, to rank
uv run blastradius rank --top-n 25000 # freeze the coverage cap
uv run blastradius latest             # deps + maintainers for the top-N
uv run blastradius advisories         # OSV npm corpus + holdout
uv run blastradius build              # assemble the SQLite graph
uv run blastradius packuments         # version histories for advisory-affected packages
uv run blastradius resolve            # resolve ranges; fill validity intervals
uv run blastradius similar            # SIMILAR_NAME edges
uv run blastradius hydra-ingest       # advisory prose into HydraDB

uv run blastradius eval               # -> §3, writes data/eval_results.json
uv run blastradius baseline           # -> §4, writes data/baseline_results.json
```

Raw outputs are written as JSON so every number in this file can be checked rather than taken
on faith — including `disagreements` in `eval_results.json`, which lists every case where the
graph and the independent oracle differ.
