"""Regression tests for version resolution — the B7 class of bug.

Resolving a declared range only against a package's *latest* version makes the blast radius of
any older version come back empty, which is the wrong answer for exactly the incidents that
matter most. These tests pin the correct behaviour.
"""

from __future__ import annotations

import pytest

from blastradius_ingest import queries as Q
from blastradius_ingest.build import (
    backfill_affected_intervals,
    resolve_all_versions,
    resolve_latest_versions,
)
from blastradius_ingest.store import Store


@pytest.fixture
def store(tmp_path) -> Store:  # type: ignore[no-untyped-def]
    s = Store(tmp_path / "r.db")
    c = s.conn
    c.executemany(
        "INSERT INTO package(name, downloads, download_rank) VALUES(?,?,?)",
        [("acorn", 1, 1), ("acorn-node", 2, 2), ("acorn-jsx", 3, 3)],
    )
    # acorn has a full version history; 8.18.0 is latest.
    c.executemany(
        "INSERT INTO version(pkg, version, published_at, is_latest) VALUES(?,?,?,?)",
        [
            ("acorn", "6.4.2", "2020-01-01T00:00:00Z", 0),
            ("acorn", "7.4.1", "2020-08-01T00:00:00Z", 0),
            ("acorn", "8.18.0", "2025-01-01T00:00:00Z", 1),
            ("acorn-node", "2.0.1", "2024-01-01T00:00:00Z", 1),
            ("acorn-jsx", "5.3.2", "2024-01-01T00:00:00Z", 1),
        ],
    )
    c.executemany(
        "INSERT INTO depends_on(src_pkg, src_version, dst_pkg, range, resolved, dep_type) "
        "VALUES(?,?,?,?,NULL,?)",
        [
            ("acorn-node", "2.0.1", "acorn", "^7.0.0", "prod"),  # older major
            ("acorn-jsx", "5.3.2", "acorn", "^8.0.0", "prod"),  # current major
        ],
    )
    s.commit()
    return s


def test_latest_only_resolution_cannot_see_the_older_major(store: Store) -> None:
    """Baseline: this is the behaviour that made B7 a silent wrong answer."""
    stats = resolve_latest_versions(store)
    assert stats["resolved"] == 1  # only acorn-jsx (^8.0.0 admits 8.18.0)
    assert stats["range_excludes_latest"] == 1  # acorn-node's ^7.0.0 is dropped
    assert Q.blast_radius(store, "acorn", "7.4.1").count == 0


def test_resolving_against_all_versions_picks_max_satisfying(store: Store) -> None:
    resolve_all_versions(store)
    got = {
        (r["src_pkg"], r["resolved"])
        for r in store.conn.execute("SELECT src_pkg, resolved FROM depends_on")
    }
    assert got == {("acorn-node", "7.4.1"), ("acorn-jsx", "8.18.0")}


def test_blast_radius_works_for_a_non_latest_version(store: Store) -> None:
    """The fix: an older major now has a findable dependent."""
    resolve_all_versions(store)
    older = Q.blast_radius(store, "acorn", "7.4.1")
    assert {r["pkg"] for r in older.rows} == {"acorn-node"}

    current = Q.blast_radius(store, "acorn", "8.18.0")
    assert {r["pkg"] for r in current.rows} == {"acorn-jsx"}


def test_resolution_never_invents_a_version(store: Store) -> None:
    """A range no published version satisfies must stay NULL, not snap to something close."""
    store.conn.execute(
        "INSERT INTO depends_on(src_pkg, src_version, dst_pkg, range, resolved, dep_type) "
        "VALUES('ghost','1.0.0','acorn','^99.0.0',NULL,'prod')"
    )
    store.commit()
    stats = resolve_all_versions(store)
    assert stats["no_satisfying_version"] >= 1
    row = store.conn.execute(
        "SELECT resolved FROM depends_on WHERE src_pkg='ghost'"
    ).fetchone()
    assert row["resolved"] is None


def test_non_semver_specifiers_are_left_unresolved(store: Store) -> None:
    """git:, file:, workspace: specifiers are not version ranges at all."""
    store.conn.executemany(
        "INSERT INTO depends_on(src_pkg, src_version, dst_pkg, range, resolved, dep_type) "
        "VALUES(?,'1.0.0','acorn',?,NULL,'prod')",
        [("g1", "git+https://github.com/x/y.git"), ("g2", "file:../local")],
    )
    store.commit()
    resolve_all_versions(store)
    unresolved = store.conn.execute(
        "SELECT COUNT(*) FROM depends_on WHERE src_pkg IN ('g1','g2') AND resolved IS NULL"
    ).fetchone()[0]
    assert unresolved == 2


def test_affected_intervals_come_from_publish_dates_not_disclosure(
    store: Store,
) -> None:
    """valid_from is when the vulnerable version shipped, not when the advisory landed."""
    store.conn.execute(
        "INSERT INTO advisory(osv_id, published_at, withheld) "
        "VALUES('GHSA-x','2026-01-15T00:00:00Z',0)"
    )
    store.conn.execute(
        "INSERT INTO affected_by(pkg, osv_id, introduced, fixed) "
        "VALUES('acorn','GHSA-x','6.4.2','8.18.0')"
    )
    store.commit()
    backfill_affected_intervals(store)
    row = store.conn.execute(
        "SELECT valid_from, valid_to FROM affected_by WHERE osv_id='GHSA-x'"
    ).fetchone()
    assert row["valid_from"] == "2020-01-01T00:00:00Z"  # 6.4.2 publish date
    assert row["valid_to"] == "2025-01-01T00:00:00Z"  # 8.18.0 publish date


def test_unknown_bounds_stay_null_rather_than_guessed(store: Store) -> None:
    store.conn.execute(
        "INSERT INTO advisory(osv_id, published_at, withheld) "
        "VALUES('GHSA-y','2026-01-15T00:00:00Z',0)"
    )
    store.conn.execute(
        "INSERT INTO affected_by(pkg, osv_id, introduced, fixed) "
        "VALUES('acorn','GHSA-y','0','9.9.9')"  # neither bound is a real published version
    )
    store.commit()
    backfill_affected_intervals(store)
    row = store.conn.execute(
        "SELECT valid_from, valid_to FROM affected_by WHERE osv_id='GHSA-y'"
    ).fetchone()
    assert row["valid_from"] is None
    assert row["valid_to"] is None
