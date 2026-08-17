"""P3: the six questions, against a hand-built SQLite graph with known answers."""

from __future__ import annotations

import pytest

from blastradius_ingest import queries as Q
from blastradius_ingest.store import Store

WINDOW = ("2026-03-10T09:00:00Z", "2026-03-10T09:06:00Z")
AFTER = ("2026-03-10T10:00:00Z", "2026-03-10T11:00:00Z")


@pytest.fixture
def store(tmp_path) -> Store:  # type: ignore[no-untyped-def]
    s = Store(tmp_path / "t.db")
    c = s.conn
    c.executemany(
        "INSERT INTO package(name, downloads, download_rank) VALUES(?,?,?)",
        [
            ("left-pad", 900_000, 400),
            ("mid-lib", 50_000, 8_800),
            ("top-app", 9_000, 15_000),
            ("lodash", 90_000_000, 3),
            ("1odash", 4, 999_999),
        ],
    )
    c.executemany(
        "INSERT INTO version(pkg, version, published_at, is_latest) VALUES(?,?,?,?)",
        [
            ("left-pad", "1.2.0", "2017-06-01T00:00:00Z", 0),
            ("left-pad", "1.3.0", "2018-04-05T00:00:00Z", 1),
            ("mid-lib", "2.0.0", "2025-11-02T00:00:00Z", 1),
            ("top-app", "3.1.0", "2026-01-15T00:00:00Z", 1),
        ],
    )
    # mid-lib -> left-pad@1.3.0 -> top-app -> mid-lib@2.0.0  (a 2-hop reverse chain)
    c.executemany(
        "INSERT INTO depends_on(src_pkg, src_version, dst_pkg, range, resolved, dep_type) "
        "VALUES(?,?,?,?,?,?)",
        [
            ("mid-lib", "2.0.0", "left-pad", "^1.3.0", "1.3.0", "prod"),
            ("top-app", "3.1.0", "mid-lib", "^2.0.0", "2.0.0", "prod"),
            # dev-depends on the SAFE version — must not appear in the 1.3.0 closure
            ("top-app", "3.1.0", "left-pad", "^1.2.0", "1.2.0", "dev"),
        ],
    )
    c.executemany(
        "INSERT INTO maintainer(handle) VALUES(?)", [("stevemao",), ("evil",)]
    )
    c.executemany(
        "INSERT INTO published_by(pkg, handle) VALUES(?,?)",
        [("left-pad", "stevemao"), ("mid-lib", "stevemao"), ("1odash", "evil")],
    )
    c.executemany(
        "INSERT INTO advisory(osv_id, published_at, severity, summary, withheld, withdrawn) "
        "VALUES(?,?,?,?,?,?)",
        [
            ("GHSA-live", "2025-06-01T00:00:00Z", "CRITICAL", "live", 0, None),
            ("GHSA-held", "2026-06-01T00:00:00Z", "HIGH", "held out", 1, None),
            ("GHSA-gone", "2025-06-01T00:00:00Z", "HIGH", "retracted", 0, "2025-07-01"),
            ("MAL-1", "2025-01-01T00:00:00Z", None, "malicious", 0, None),
        ],
    )
    c.executemany(
        "INSERT INTO affected_by(pkg, osv_id, introduced, fixed) VALUES(?,?,?,?)",
        [
            ("left-pad", "GHSA-live", "1.3.0", "1.3.1"),
            ("left-pad", "GHSA-held", "1.2.0", "1.2.9"),
            ("left-pad", "GHSA-gone", "1.0.0", "1.0.1"),
            ("1odash", "MAL-1", "0.0.0", None),
        ],
    )
    c.executemany(
        "INSERT INTO similar_name(pkg, other, distance, rank_gap) VALUES(?,?,?,?)",
        [("1odash", "lodash", 1, 999_996)],
    )
    s.commit()
    return s


# ------------------------------------------------------------------ Q1


def test_q1_reverse_closure_is_transitive(store: Store) -> None:
    a = Q.blast_radius(store, "left-pad", "1.3.0")
    assert {(r["pkg"], r["depth"]) for r in a.rows} == {("mid-lib", 1), ("top-app", 2)}


def test_q1_excludes_other_resolved_versions(store: Store) -> None:
    """top-app dev-depends on left-pad@1.2.0, so it is not a *direct* dependent of 1.3.0."""
    a = Q.blast_radius(store, "left-pad", "1.3.0")
    direct = {r["pkg"] for r in a.rows if r["depth"] == 1}
    assert direct == {"mid-lib"}

    safe = Q.blast_radius(store, "left-pad", "1.2.0")
    assert {r["pkg"] for r in safe.rows if r["depth"] == 1} == {"top-app"}


def test_q1_path_is_returned_as_evidence(store: Store) -> None:
    a = Q.blast_radius(store, "left-pad", "1.3.0")
    first = next(p for p in a.path if p["depth"] == 1)
    assert first["from"] == "mid-lib@2.0.0"
    assert first["to"] == "left-pad@1.3.0"
    assert first["range"] == "^1.3.0"


def test_q1_truncation_is_declared_not_silent(store: Store) -> None:
    a = Q.blast_radius(store, "left-pad", "1.3.0", limit=1)
    assert a.count == 1
    assert any("truncated" in n for n in a.notes)


# ------------------------------------------------------------------ Q2


def test_q2_returns_introduced_and_fixed_bounds(store: Store) -> None:
    a = Q.introducing_version(store, "GHSA-live")
    assert a.rows == [
        {
            "pkg": "left-pad",
            "introduced": "1.3.0",
            "fixed": "1.3.1",
            "introduced_published_at": "2018-04-05T00:00:00Z",
        }
    ]


def test_q2_excludes_held_out_advisories(store: Store) -> None:
    """The eval set must never reach an answer."""
    a = Q.introducing_version(store, "GHSA-held")
    assert a.rows == []
    assert a.notes


def test_q2_excludes_withdrawn_advisories(store: Store) -> None:
    assert Q.introducing_version(store, "GHSA-gone").rows == []


# ------------------------------------------------------------------ Q3


def _add_lockfile(store: Store) -> None:
    Q.ingest_lockfile(
        store,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "checkout"},
                "node_modules/left-pad": {"name": "left-pad", "version": "1.3.0"},
            },
        },
        service_id="svc:acme/checkout",
        service_name="checkout",
        lockfile_id="lock:1",
        observed_at="2026-03-10T08:00:00Z",
        valid_from="2026-03-10T09:01:00Z",
    )
    store.conn.execute("UPDATE resolved_to SET valid_to = '2026-03-10T09:05:00Z'")
    store.commit()


def test_q3_finds_exposure_inside_the_window(store: Store) -> None:
    _add_lockfile(store)
    a = Q.exposed_during(store, "left-pad", "1.3.0", *WINDOW)
    assert a.count == 1
    assert a.rows[0]["service_name"] == "checkout"


def test_q3_service_looks_clean_after_the_revert(store: Store) -> None:
    """The whole point of interval edges: present-tense views miss past exposure."""
    _add_lockfile(store)
    assert Q.exposed_during(store, "left-pad", "1.3.0", *AFTER).count == 0


def test_q3_lockfile_v1_format_is_supported(store: Store) -> None:
    Q.ingest_lockfile(
        store,
        {
            "lockfileVersion": 1,
            "dependencies": {
                "left-pad": {
                    "version": "1.3.0",
                    "dependencies": {"mid-lib": {"version": "2.0.0"}},
                }
            },
        },
        service_id="svc:legacy",
        service_name="legacy",
        lockfile_id="lock:v1",
    )
    got = {
        (r["pkg"], r["version"])
        for r in store.conn.execute(
            "SELECT pkg, version FROM resolved_to WHERE lockfile_id='lock:v1'"
        )
    }
    assert got == {("left-pad", "1.3.0"), ("mid-lib", "2.0.0")}


# ------------------------------------------------------------------ Q4/Q5/Q6


def test_q4_shared_maintainer_is_an_exact_join(store: Store) -> None:
    a = Q.shared_maintainer_packages(store, "left-pad")
    assert [r["pkg"] for r in a.rows] == ["mid-lib"]


def test_q5_surfaces_independent_malicious_flag(store: Store) -> None:
    a = Q.typosquat_candidates(store, "lodash")
    assert a.count == 1
    assert a.rows[0]["candidate"] == "1odash"
    assert a.rows[0]["distance"] == 1
    assert a.rows[0]["flagged_malicious"] is True


def test_q6_rollup_reports_service_depth_and_first_exposure(store: Store) -> None:
    _add_lockfile(store)
    a = Q.rollup(
        store, "left-pad", "1.3.0", window_start=WINDOW[0], window_end=WINDOW[1]
    )
    assert a.count == 1
    row = a.rows[0]
    assert row["service_id"] == "svc:acme/checkout"
    assert row["first_exposure"] == "2026-03-10T09:01:00Z"


def test_q6_is_empty_outside_the_exposure_window(store: Store) -> None:
    _add_lockfile(store)
    a = Q.rollup(store, "left-pad", "1.3.0", window_start=AFTER[0], window_end=AFTER[1])
    assert a.count == 0


def test_wide_frontier_does_not_blow_sqlite_expression_depth(store: Store) -> None:
    """A frontier wider than SQLite's 1000-deep expression limit must still resolve.

    One OR-clause per frontier node raised `Expression tree is too large` on exactly the
    biggest blast radii — lodash has 728 direct dependents. Caught by the web-db parity
    check, not by the eval, whose held-out advisories all have small closures.
    """
    c = store.conn
    c.execute(
        "INSERT INTO version(pkg, version, published_at, is_latest) "
        "VALUES('wide','1.0.0','2020-01-01T00:00:00Z',1)"
    )
    # 1,200 direct dependents — comfortably past the limit the old query hit.
    rows = [
        (f"dep{i}", "1.0.0", "wide", "^1.0.0", "1.0.0", "prod") for i in range(1200)
    ]
    c.executemany(
        "INSERT INTO version(pkg, version, published_at, is_latest) VALUES(?,?,?,1)",
        [(f"dep{i}", "1.0.0", "2021-01-01T00:00:00Z") for i in range(1200)],
    )
    c.executemany(
        "INSERT INTO depends_on(src_pkg, src_version, dst_pkg, range, resolved, dep_type) "
        "VALUES(?,?,?,?,?,?)",
        rows,
    )
    # A second level so the wide set becomes the *frontier*, not just the first result.
    c.execute(
        "INSERT INTO version(pkg, version, published_at, is_latest) "
        "VALUES('top','9.0.0','2022-01-01T00:00:00Z',1)"
    )
    c.executemany(
        "INSERT INTO depends_on(src_pkg, src_version, dst_pkg, range, resolved, dep_type) "
        "VALUES('top','9.0.0',?,'^1.0.0','1.0.0','prod')",
        [(f"dep{i}",) for i in range(1200)],
    )
    store.commit()

    answer = Q.blast_radius(store, "wide", "1.0.0")
    assert answer.count == 1201  # 1200 direct + 'top' at depth 2
    assert {r["depth"] for r in answer.rows} == {1, 2}


def test_every_answer_carries_a_latency(store: Store) -> None:
    for ans in (
        Q.blast_radius(store, "left-pad", "1.3.0"),
        Q.introducing_version(store, "GHSA-live"),
        Q.exposed_during(store, "left-pad", "1.3.0", *WINDOW),
        Q.shared_maintainer_packages(store, "left-pad"),
        Q.typosquat_candidates(store, "lodash"),
        Q.rollup(store, "left-pad", "1.3.0"),
    ):
        assert ans.latency_ms > 0
