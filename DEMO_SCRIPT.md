# blastradius — demo script

Written for a viewer with no technical background. Every sentence is complete. There are no
em dashes, no semicolons, no irony, no understatement, and no conditional phrasing. The
delivery is continuous with no pauses.

Order is fixed by the packet: problem, then project, then live demo, then where HydraDB is
used and why it matters.

**Spoken words: 431. At 150 words per minute this runs 2:52. The ceiling is 3:00.**

Every number and every result named below was verified live against the deployed console
before this script was written. Nothing here is described from memory.

Lines beginning with `SCREEN` are actions, not narration.

---

## Before recording

Open the production URL and let it finish loading. The console opens on `lodash@3.10.1` on
purpose, so the first forty seconds require no typing. Have
`examples/acme-checkout.package-lock.json` visible in a folder, ready to drag onto the page.

---

## 1. The problem (0:00 to 0:33)

`SCREEN` Title card, or the console sitting still.

> Almost every app you use is built from thousands of small pieces of free code written by
> strangers. One piece can sit inside millions of projects.
>
> Sometimes an attacker takes over one of those pieces and publishes a poisoned version. That
> version often stays online for a few minutes before somebody removes it.
>
> During those minutes every software team needs to know whether their own product just
> installed the poison. Finding that answer today takes hours of manual searching, and the
> emergency ends long before anybody finishes.

## 2. The project (0:33 to 0:47)

`SCREEN` The console, already loaded on lodash 3.10.1.

> This is blastradius. You give it the name of a poisoned package. It shows you every project
> that would be affected, how far the damage travels, and the exact moment the danger began.

## 3. The live demo (0:47 to 2:17)

`SCREEN` Point at the verdict row.

> Here it is on a real case. This package is lodash, one of the most widely installed pieces of
> code in the world. This version carries five real security advisories.
>
> The big number is eight. Eight real projects depend on this exact version, and the console
> found them in under one millisecond.

`SCREEN` Scroll to the diagram. Hover an outer node so its chain lights up in red.

> This picture is the proof. The poisoned package sits in the middle. Every ring outward is one
> more step along the chain of who depends on whom. When I hover over a project, the console
> lights up the exact route back to the poison. A responder sees why they are affected instead
> of only a number.

`SCREEN` Scroll to the slider. Drag slowly left to right, resting inside the red band.

> Now I am dragging through time. Every position on this bar is a real date. At the start of
> 2015 this package was clean. The banner turns red as the poisoned version ships. It goes
> quiet again in the summer of 2020 when the fix lands. This package looks perfectly safe
> today, and it was wide open for more than five years.

`SCREEN` Drag `acme-checkout.package-lock.json` onto the drop zone. Click trace.

> Finally I will drop in a real installation file from acme checkout. It records exactly which
> versions that team installed. The console marks which of their services touched the poisoned
> code and how deep it runs.

## 4. Where HydraDB is used and why it matters (2:17 to 2:51)

`SCREEN` Scroll to the search box at the bottom. Run the query already in the field.

> HydraDB powers the half of this product that understands meaning. Here I describe an attack
> in plain English without naming a single package.
>
> The top result hijacked environment variables out of a package called proxy dot js. Further
> down is crossenv, one of the most famous supply chain attacks ever run against this ecosystem.
>
> Not one of those package names contains a word that I typed. Searching by name finds nothing,
> and HydraDB finds them all in half a second.
>
> The graph tells you who is affected. HydraDB tells you what happened. Real incident response
> needs both, and that is why this product has both.

---

## What was verified live before recording

| Beat | Claim | Verified result |
| --- | --- | --- |
| 3 | five advisories, blast radius eight | `blast radius=8, max depth=2, advisories=5`, page total 26.3 ms |
| 3 | found in under one millisecond | `0.364 ms` on the verdict row |
| 3 | clean 2015, red mid window, quiet after 2020 | scrubber: `2015-01-23` no window, `2016-06-23` and `2019-10-12` VULNERABLE, `2023-04-26` no window |
| 3 | lockfile marks their own services | `your services 0 → 2`, chain `jscs@3.0.7 -> lodash@3.10.1` |
| 4 | top result hijacked environment variables | `GHSA-qj73-v688-wqxf`, proxy.js, score 0.922 |
| 4 | crossenv appears | `GHSA-c2m4-w5hm-vqjw`, "crossenv is malware" |
| 4 | about half a second | server 455 ms, round trip 514 ms |

## Re-measuring after any edit

```bash
awk '/^> /{gsub(/^> /,""); print}' DEMO_SCRIPT.md | wc -w
```

Divide by 150 for minutes. Anything over 445 words breaks the three minute ceiling.
