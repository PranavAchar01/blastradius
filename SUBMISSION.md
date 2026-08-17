# Submission — Hack Hydra, Track 02A

Every field the form asks for, pre-written. Copy verbatim.

---

## Project name

**blastradius**

## One-line description

A supply-chain blast-radius console: when an npm package is compromised, it answers who is
exposed, how deep, and since when — by graph traversal, in single-digit milliseconds.

## Deployed link

https://blastradius-c8mvz5mw9-phantom3452s-projects.vercel.app

Loads on the demo case (`lodash@3.10.1`) automatically. Drop
`examples/acme-checkout.package-lock.json` from the repo onto the console to see real service
exposure.

## Repository

*(fill in the public GitHub URL once pushed — see the checklist in STATE.md)*

Public, MIT licensed. Every commit dated Aug 14–15 2026.

## Video

*(fill in the link — script is in [DEMO_SCRIPT.md](DEMO_SCRIPT.md), measured at 2:35)*

---

## The problem

A maintainer's npm account is taken over at 09:00. A malicious version ships. It's yanked at
09:06. Six minutes.

In those six minutes, an incident responder needs four things: who transitively depends on the
bad version, which of *their* services actually resolved it while it was live, what else that
maintainer publishes, and what typosquats sit next to the name. Today that means hours of
grepping lockfiles — the incident is over before you know whether you were in it.

Every one of those is a **reachability question over a typed, time-varying graph**. None is a
similarity question, which is why reaching for a vector index produces confident, wrong answers.

## What I built

A console over a graph of **25,754 packages, 237,198 versions, 81,181 dependency edges, 226,832
advisories and 212,932 typosquat edges**, answering all six questions in the brief as real
traversals:

| | question | p50 |
| --- | --- | ---: |
| Q1 | transitive reverse-dependency closure for `pkg@version` | 0.006 ms |
| Q2 | which version introduced the vulnerability | 0.005 ms |
| Q3 | which lockfiles/services resolved it **while it was live** | 0.002 ms |
| Q4 | packages sharing a maintainer | 0.028 ms |
| Q5 | likely typosquats near the name | 6.53 ms |
| Q6 | rollup: services exposed, depth, first-exposure time | 0.014 ms |

The console renders the blast radius as concentric depth rings; hovering any node lights its
full traversal path back to the compromised package, with the semver range that made each edge
true. **The path is the proof** — "you're exposed" isn't actionable, "you're exposed *because*
this range resolves here" is. Upload a real `package-lock.json` and exposure becomes yours
rather than hypothetical. A timeline scrubber recomputes the affected set by interval overlap at
each instant, so a service that installed the bad version and later reverted still shows as
exposed — which every present-tense view misses.

**Results.** Precision **1.0000** and recall **1.0000** on the held-out closure eval (tp=1241,
fp=0, fn=0), scored against ground truth recomputed independently from raw npm manifests rather
than against the graph itself. 12,933 advisories published on/after 2026-01-01 were held out
entirely and never touched the graph or the semantic index. The demo advisory's closure was
hand-verified against npm's live registry: 6/6 direct dependents confirmed, 0 contradicted.

The number that captures the whole argument: **826 packages in the graph depend on lodash; only
6 resolve to the compromised `3.10.1`.** The other 820 are on 4.x and are genuinely fine. A
similarity index collapses that distinction; this doesn't.

## How it uses HydraDB

The system is two halves, split on measurement rather than taste.

**HydraDB carries the semantic layer** — 4,250 GHSA advisories ingested as prose, so a responder
can describe an attack in English and get passages back with citations, plus HydraDB's own
extracted entity/relation paths. Queries that name *no package at all*:

| query | top hit | score |
| --- | --- | ---: |
| malicious postinstall script that steals environment variables | `GHSA-f29h-pxvx-f335` — **eslint-config-prettier**, the real July 2025 compromise | 0.913 |
| prototype pollution in a deep merge utility | `GHSA-9g9w-hmvj-5h57` — merge-deep | 0.945 |
| arbitrary file write via path traversal when extracting an archive | `GHSA-884w-698f-927f` — unzipper | 0.954 |

**Without HydraDB the project cannot get from a description to an advisory.** No `LIKE`, no
index, no traversal over the same corpus returns those — none of the query words appear in the
package names retrieved. That is the capability that matters at 3am when all you know is what
the attack *did*.

**The graph carries reachability**, because HydraDB has no typed-edge store and no N-hop
traversal call — established by probing every v2 endpoint before designing anything, recorded in
STATE.md B1. Exact closure isn't a retrieval problem and isn't attempted as one.

I measured the inverse too, so the division is evidence rather than assertion. Asked the same
six questions, semantic retrieval returns **0 of 8** correct dependents for Q1 and **0 of 46**
typosquats for Q5 — but **beats the graph outright on Q2** (precision and recall both 1.00),
where the answer is written down in prose. That result is reported rather than buried; it's the
honest boundary of the argument and the reason both halves ship.

## Tech stack

**Ingest & eval** — Python 3.12, uv, httpx, rapidfuzz, nodesemver, numpy. SQLite for the
deterministic graph. Ruff + mypy strict, 37 tests.

**Console** — Next.js App Router, TypeScript strict, `node:sqlite` (built into Node 22.5+, so no
native module in the Vercel build). Hand-written CSS — no component library, no UI kit, no
charting library. Set in Knuth's Computer Modern (OFL, self-hosted).

**Data** — npm registry + downloads API; the complete OSV npm advisory corpus (which aggregates
the GitHub Advisory Database). All public, all free. **Total API spend: $0.**

**Deployed on** Vercel.

## Team

Pranav Achar — solo.

## Coverage and limits

Stated up front, because silent truncation would make every number above meaningless. The
dependency graph covers the **top 25,000 npm packages by weekly downloads** (rank 25,000 =
14,641 downloads/week); the **advisory corpus is complete and uncapped**; package names were
enumerated exhaustively (all 4,291,086). A dependent outside the cap cannot appear in a closure.
Scoped packages aren't exhaustively ranked — npm's bulk endpoint rejects them and single lookups
measured ~3 req/s, about 22 hours for all 1.62M. Seven limits in total are enumerated in the
README, and the eval reports how many held-out advisories were excluded for falling outside the
cap alongside every score.
