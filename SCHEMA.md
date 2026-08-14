# blastradius — graph schema

The six questions in the brief are *reachability* questions over a typed, time-varying graph.
This document defines that graph, then justifies each modeling choice against the only
alternative a hackathon reviewer will reasonably ask about: "why not just embed everything and
use a vector index?"

It also states honestly which half of the system HydraDB serves, because measuring that
correctly was the main finding of P0.

---

## 0. Where each half lives

P0 established empirically (see [STATE.md](STATE.md) B1) that HydraDB is a semantic context
substrate: retrieval plus a knowledge graph it extracts from document text. It has no typed-edge
store and no N-hop traversal call. So the schema below is realized in two places, deliberately:

| Layer | Holds | Answers | Why there |
| --- | --- | --- | --- |
| **Deterministic graph** (embedded, built from npm + OSV) | `Package`, `Version`, `Maintainer`, `Advisory`, `Lockfile`, `Service` nodes and all five typed edges with their properties and validity intervals | Q1–Q6 exactly | Closure must be *exact and complete*. A missed dependent is a service that stays compromised. This is not a similarity problem and must not be answered by one. |
| **HydraDB** | The same entities as documents, plus advisory prose, maintainer profiles, and package descriptions | Semantic layer: which advisory text actually describes this compromise, which packages are plausibly the same maintainer under different handles, which names are confusable, and the human-readable **provenance path** shown in the console | These are meaning problems, and HydraDB's extracted triplets are genuinely good at them — it derived `demo-app 1.0.0 --depends on--> left-pad 1.3.0` from prose in P0's smoke test |

The console renders both: the exact closure (the answer) and HydraDB's extracted path (the
proof, in language a responder can read). Neither layer is decorative and neither is oversold.

---

## 1. Nodes

| Node | Identity | Key properties |
| --- | --- | --- |
| `Package` | `pkg:<name>` | `name`, `ecosystem`, `download_rank`, `description` |
| `Version` | `ver:<name>@<semver>` | `version`, `published_at`, `dist_shasum`, `deprecated` |
| `Maintainer` | `mnt:<npm-handle>` | `handle`, `email_hash`, `package_count` |
| `Advisory` | `adv:<GHSA-id>` | `osv_id`, `aliases[]`, `severity`, `cvss`, `published_at`, `summary` |
| `Lockfile` | `lock:<sha256-of-content>` | `lockfile_version`, `parsed_at`, `entry_count` |
| `Service` | `svc:<org>/<name>` | `name`, `owner`, `environment` |

`Package` and `Version` are separate nodes rather than one node with a version list. Every
interesting question in the brief is version-scoped — "which version *introduced* it", "who
resolved *the bad version*" — so version has to be a first-class vertex you can traverse to and
from, not an attribute you filter on after the fact.

## 2. Edges

| Edge | From → To | Properties |
| --- | --- | --- |
| `DEPENDS_ON` | `Version` → `Package` | `range` (declared semver), `resolved` (concrete version), `dep_type` (prod/dev/peer/optional) |
| `VERSION_OF` | `Version` → `Package` | — |
| `PUBLISHED_BY` | `Package` → `Maintainer` | `since` |
| `AFFECTED_BY` | `Package` → `Advisory` | `introduced`, `fixed`, **`valid_from`, `valid_to`** |
| `RESOLVED_TO` | `Lockfile` → `Version` | `resolved_at`, **`valid_from`, `valid_to`** |
| `SIMILAR_NAME` | `Package` → `Package` | `distance` (Damerau-Levenshtein ≤2), `rank_gap` |
| `DECLARES` | `Service` → `Lockfile` | `observed_at` |

`DEPENDS_ON` points `Version → Package` and carries both the declared `range` and the `resolved`
concrete version. Pointing at the *package* is what makes the edge honest: a dependency
declaration genuinely targets a package and a range, and resolution is a separate fact that
changes over time. Storing only the resolved version would make the graph unable to answer "what
*would* resolve here today", and storing only the range would make blast radius unanswerable.

### Reverse traversal

Q1 and Q6 walk `DEPENDS_ON` **backwards**: given `left-pad@1.3.0`, find every `Version` whose
`DEPENDS_ON` edge resolves to it, then every version depending on *those*, transitively. The
deterministic layer therefore materializes an explicit reverse adjacency index; forward-only
storage would make the headline query a full scan.

---

## 3. Time is first-class

`RESOLVED_TO` and `AFFECTED_BY` each carry `valid_from` / `valid_to`, making them **interval
edges** rather than facts with a timestamp attached.

This is the difference between a query and a post-filter. "Who was exposed between 09:00 and
09:06 on the day of the compromise" becomes an interval-overlap predicate evaluated *during*
traversal:

```
edge.valid_from < window_end  AND  (edge.valid_to IS NULL OR edge.valid_to > window_start)
```

Edges that don't overlap the window are never expanded, so the traversal frontier itself is
time-scoped. A post-filter would compute today's blast radius and then discard rows — which
gives the wrong answer, not just a slower one: a service that resolved the bad version at 09:01
and pinned back to a safe version at 09:05 **was exposed**, but appears clean in every
present-tense view of the graph. Retroactive advisory publication has the same shape:
`AFFECTED_BY.valid_from` is when the vulnerability was *introduced*, not when it was disclosed,
so a lockfile from six months before the GHSA existed still tests positive.

`valid_to IS NULL` means "still in effect", so open intervals need no sentinel date.

---

## 4. Why a vector index cannot answer these

Not "would be slower" — cannot express. Taking the brief's six questions in order:

**Q1 — transitive reverse-dep closure.** Embeddings encode *similarity*, and depending on a
package is not being similar to it. A React app and a CLI tool that both depend on the
compromised package sit nowhere near each other in embedding space; two competing HTTP clients
that share no dependency edge sit right next to each other. Worse, closure is transitive and
unbounded in depth — the answer at depth 5 is reached by *composing* edges, and vector search has
no composition operator. Retrieval returns a ranked top-k; a blast radius is a set with a
boundary, and `k` is not that boundary. Ranking is also the wrong shape: the 900th dependent is
exactly as compromised as the 1st.

**Q2 — which version introduced the vulnerability.** This is an interval containment test over
ordered semver (`introduced <= v < fixed`). Version strings embed almost identically — `1.2.3`
and `1.2.4` are neighbors in embedding space and on opposite sides of a patch boundary. Nearest-
neighbor over version text is uncorrelated with the actual ordering relation.

**Q3 — who resolved the bad version while it was live.** Requires interval algebra over two
independent time ranges (exposure window, edge validity). Vectors have no temporal operator at
all; the standard workaround is a metadata pre-filter, which is the post-filter problem in
Section 3 — and it still can't traverse.

**Q4 — packages sharing a maintainer.** An exact join through `Maintainer`. Semantic similarity
of package descriptions is actively misleading: a maintainer's packages are often unrelated in
purpose (a logger, a date library, a CLI helper), while similar-sounding packages usually have
different maintainers. This is the query where similarity gives you a confidently wrong answer.

**Q5 — likely typosquats.** Needs *orthographic* distance (Damerau-Levenshtein over characters),
which is close to the opposite of semantic distance. `lodash` and `1odash` are one substitution
apart and mean the same thing to an embedding model — which is exactly why the attack works.
Meanwhile `underscore` is semantically adjacent to `lodash` and is not a typosquat. Any embedding
good enough to be useful for search is, by construction, blind to this attack.

**Q6 — rollup with depth and first-exposure time.** Depth is shortest-path length; first-exposure
is a min over interval starts along that path. Both are properties *of the traversal*, not of any
node or chunk, so there is nothing to embed. Even a perfect retrieval of every relevant document
would not contain the answer.

**Where HydraDB genuinely wins.** The inverse is also true, and the console leans on it: mapping
free-text advisory prose onto the right package and version bounds, recognizing one maintainer
across handle changes, and producing a readable provenance path are all meaning problems that
exact graph traversal cannot do. P0's smoke test showed HydraDB extracting a correct dependency
triplet from an English sentence with a chunk citation. That is the half of the problem it should
be carrying — and the half that makes the closure explainable to whoever is being paged at 3am.

---

## 5. Fixture

`ingest/fixtures/mini.json` — 20 nodes exercising every node type, every edge type, a 3-hop
reverse-dependency chain, an interval edge that opens and closes inside the exposure window, and
a typosquat pair. It is the P1 gate and the regression test for the traversal engine.
