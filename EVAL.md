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

## 3. Results — **pending**

Not yet run. Requires the completed ingest (P2 gate) and the query engine (P3).

Will contain, per question Q1–Q6: precision, recall, p50/p95 latency, cost per query, the
number of held-out advisories scored, and the number excluded for falling outside the coverage
cap.

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

## 4. Vector/embedding baseline — **pending**

Not yet run. Will report what a semantic-similarity approach returns for the same six
questions, against the same held-out set. The argument for why it cannot express them is in
[SCHEMA.md](SCHEMA.md) §4; this section is the measurement of that argument, not a restatement
of it.

## 5. Reproduction

One command, documented in the README, regenerates everything in §3–§4 from a cold clone.
Recorded here once §3 exists so the command and the numbers ship together.
