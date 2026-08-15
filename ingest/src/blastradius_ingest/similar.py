"""SIMILAR_NAME edges — orthographic neighbours of popular package names.

This is the typosquat question (Q5), and it is the one place where the *opposite* of semantic
similarity is what matters: `lodash` and `1odash` are one substitution apart and mean the same
thing to an embedding model, which is precisely why the attack works. See SCHEMA.md section 4.

Distance is true Damerau-Levenshtein, so a transposition counts as one edit
(`lodash` -> `lodahs` is 1, not 2). Plain Levenshtein would miss the most common fat-finger
squat entirely.

Scale: 4.29M candidate names against the top-N targets is ~107B pairs, which is 1.4h even at
rapidfuzz's ~21M pairs/s. Bucketing on length collapses it: Damerau-Levenshtein <= 2 implies
``abs(len(a) - len(b)) <= 2``, so each candidate is only compared against targets in five
length buckets.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable, Iterator

import numpy as np
from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein

from blastradius_ingest.store import Store

__all__ = ["MAX_DISTANCE", "build_similar_names", "similar_pairs"]

MAX_DISTANCE = 2

# Below this length, an edit distance of 2 is meaningless: a 1-character package name is
# within 2 edits of *every* 2-character name, which produced pairs like "-" ~ "ai" / "0x" /
# "ci". Short names get distance 1 only.
SHORT_NAME_LEN = 6
MIN_NAME_LEN = 3

# An unranked candidate sits outside the coverage cap; for rank_gap it is treated as the
# bottom of the registry, which is the honest reading — we know it is not popular.
UNRANKED = 4_291_086


def allowed_distance(a: str, b: str) -> int:
    """Length-relative threshold.

    Typosquatting is a *proportional* phenomenon: two edits on a 4-character name is a
    different word, while two edits on ``normalize-package-data`` is still obviously the same
    target. Gating on the shorter of the two names keeps the long-name recall without the
    short-name noise.
    """
    return 1 if min(len(a), len(b)) <= SHORT_NAME_LEN else 2


def _bucket_by_length(names: Iterable[str]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = defaultdict(list)
    for n in names:
        out[len(n)].append(n)
    return out


def similar_pairs(
    candidates: list[str],
    targets: list[str],
    *,
    max_distance: int = MAX_DISTANCE,
    chunk: int = 4_000,
) -> Iterator[tuple[str, str, int]]:
    """Yield ``(candidate, target, distance)`` for every pair within ``max_distance``.

    Self-pairs are skipped. Both directions are *not* emitted — the caller decides
    orientation; here ``candidate`` is the possible squatter and ``target`` the popular name.
    """
    by_len = _bucket_by_length(targets)
    cand_by_len = _bucket_by_length(candidates)

    for clen, cands in cand_by_len.items():
        # DL <= 2 implies the lengths differ by at most 2.
        pool: list[str] = []
        for tlen in range(clen - max_distance, clen + max_distance + 1):
            pool.extend(by_len.get(tlen, ()))
        if not pool:
            continue

        for i in range(0, len(cands), chunk):
            block = cands[i : i + chunk]
            matrix = process.cdist(
                block,
                pool,
                scorer=DamerauLevenshtein.distance,
                score_cutoff=max_distance,
                workers=-1,
                dtype=np.uint8,
            )
            # score_cutoff makes non-matches come back as max_distance + 1.
            hits = np.argwhere(matrix <= max_distance)
            for row, col in hits:
                cand = block[int(row)]
                tgt = pool[int(col)]
                if cand == tgt:
                    continue
                if len(cand) < MIN_NAME_LEN or len(tgt) < MIN_NAME_LEN:
                    continue
                dist = int(matrix[row, col])
                if dist > allowed_distance(cand, tgt):
                    continue
                yield cand, tgt, dist


def build_similar_names(
    store: Store,
    candidates: list[str],
    ranked: list[tuple[str, int, int]],
    *,
    batch: int = 20_000,
) -> dict[str, int]:
    """Compute SIMILAR_NAME edges from every candidate name to the ranked (popular) set.

    ``rank_gap`` is ``candidate_rank - target_rank``, with unranked candidates treated as the
    bottom of the registry. A large gap at distance 1 is the typosquat signal: an obscure
    package one edit away from a very popular one.
    """
    t0 = time.monotonic()
    rank_of = {name: rank for name, _, rank in ranked}
    targets = [name for name, _, _ in ranked]

    rows: list[tuple[str, str, int, int]] = []
    stats = {"pairs": 0, "d1": 0, "d2": 0}

    def flush() -> None:
        if not rows:
            return
        store.conn.executemany(
            "INSERT INTO similar_name(pkg, other, distance, rank_gap) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(pkg, other) DO UPDATE SET distance = MIN(distance, excluded.distance)",
            rows,
        )
        store.commit()
        rows.clear()

    for cand, tgt, dist in similar_pairs(candidates, targets):
        gap = rank_of.get(cand, UNRANKED) - rank_of[tgt]
        rows.append((cand, tgt, dist, gap))
        stats["pairs"] += 1
        stats[f"d{dist}"] = stats.get(f"d{dist}", 0) + 1
        if len(rows) >= batch:
            flush()
    flush()

    stats["seconds"] = int(time.monotonic() - t0)
    store.set_meta("similar_name_stats", stats)
    return stats
