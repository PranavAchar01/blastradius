# blastradius — demo script

**Measured 2:35 at 152 wpm. Hard ceiling 3:00.** Order is fixed by the packet:
problem → project → live demo → where HydraDB is used and why it matters.

Word counts below are **counted, not estimated** — the snippet at the bottom re-measures after
any edit. A first draft measured 465 words / 3:03, over the ceiling, and was cut to 394.

Screen actions are in **bold**; everything in `>` is spoken.

**Before recording:** open the production URL, let the demo case load, and have
`examples/acme-checkout.package-lock.json` ready to drag. Don't clear the fields — the console
loads on `lodash@3.10.1` deliberately, so nothing is typed in the first 30 seconds.

---

## 1 · The problem — 70 words · 0:00–0:28

> A maintainer's npm account gets taken over at nine in the morning. A malicious version ships.
> It's yanked six minutes later.
>
> In those six minutes you need to know: who transitively depends on the bad version, which of
> *your* services installed it while it was live, and what else that maintainer publishes.
>
> Today that's hours of grepping lockfiles. The incident is over before you know if you were in
> it.

## 2 · The project — 38 words · 0:28–0:43

**Screen: the console, already loaded on lodash@3.10.1.**

> This is blastradius. Every one of those is a *reachability* question over a graph that changes
> with time — not a similarity question. So it's answered by traversal, across twenty-five
> thousand npm packages and the complete OSV advisory corpus.

## 3 · Live demo — 158 words · 0:43–1:46

**Point at the verdict row.**

> lodash 3.10.1. Blast radius: eight package-versions, two hops deep. Two and a half
> milliseconds.
>
> Here's what matters. Eight hundred and twenty-six packages here depend on lodash — only six
> resolve to this compromised version. The rest are on 4.x and are genuinely fine. A search
> index can't tell those apart. I hand-verified all six against npm's live registry.

**Scroll to the graph. Hover an outer node.**

> Centre is the compromised version, each ring is one hop. Hovering lights the path back to the
> source with the semver range that made each edge true. The path is the proof — "you're
> exposed" isn't actionable; "you're exposed *because* this range resolves here" is.

**Drag the lockfile in. Click trace.**

> My actual service: exposed twice. It pins the bad version directly, and inherits it through
> jscs at depth one.

**Drag the scrubber.**

> And time is first-class. The affected set is recomputed by interval overlap as I drag. This
> was vulnerable for five years — install it then, revert later, you still show exposed. A
> present-tense view misses that.

## 4 · HydraDB, and why it matters — 99 words · 1:46–2:25

**Scroll to §9. Type "malicious postinstall that steals credentials". Run.**

> Traversal can't do everything. HydraDB holds the advisory corpus as prose — and that query
> names no package at all.
>
> Top hit: the real eslint-config-prettier compromise. 0.91, half a second. No SQL predicate
> finds that, because none of those words appear in the package name.
>
> The split is deliberate, and I measured it both ways. Same six questions: semantic search
> returns *zero* of eight correct dependents, and zero of forty-six typosquats. But it *beats*
> the graph on "which version introduced this" — because that answer is written down in prose.
>
> Reachability is a graph problem. Meaning is a HydraDB problem.

## 5 · Close — 29 words · 2:25–2:35

> Precision and recall are both 1.0 on twelve thousand nine hundred held-out advisories, scored
> against ground truth computed independently of the graph. Every number reproduces from a
> clone.

---

## Measured budget

| section | words | running |
| --- | ---: | ---: |
| 1 problem | 70 | 0:28 |
| 2 project | 38 | 0:43 |
| 3 live demo | 158 | 1:46 |
| 4 HydraDB | 99 | 2:25 |
| 5 close | 29 | 2:35 |
| **total** | **394** | **2:35** |

At 152 wpm: **2:35**. At a slow 140 wpm: **2:48**. At a brisk 165: **2:23**. Under the ceiling
across the whole plausible range, with room for the six screen actions.

## If it still runs long, cut in this order

1. The close (0:10) — the numbers are on screen and in the README.
2. "and what else that maintainer publishes" in §1 (0:03).
3. The Q2 concession in §4 (0:11). **Cut this last.** Volunteering the one case where the
   baseline beats us is what makes the rest of the comparison believable.

**Never cut:** the 826-vs-6 contrast, the lockfile drop, or the scrubber. Those three are the
whole argument.

## Re-measuring after an edit

```bash
python3 - <<'EOF'
import re, pathlib
body = pathlib.Path('DEMO_SCRIPT.md').read_text().split('## Measured budget')[0]
spoken = ' '.join(l[1:] for l in body.splitlines() if l.startswith('>'))
w = len(re.findall(r"[\w'’-]+", spoken))
print(f"{w} words -> " + " · ".join(f"{r}wpm {int(w/r*60)//60}:{int(w/r*60)%60:02d}" for r in (140,152,165)))
EOF
```
