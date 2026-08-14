"""P1 gate: the 20-node fixture loads and reverse traversal is correct to 3 hops."""

from __future__ import annotations

from pathlib import Path

import pytest

from blastradius_ingest.graph import Graph, interval_overlaps, parse_ts

FIXTURE = Path(__file__).parent.parent / "fixtures" / "mini.json"


@pytest.fixture(scope="module")
def g() -> Graph:
    return Graph.load(FIXTURE)


def test_fixture_has_twenty_nodes(g: Graph) -> None:
    assert len(g.nodes) == 20


def test_every_node_kind_present(g: Graph) -> None:
    kinds = {n.kind for n in g.nodes.values()}
    assert kinds == {
        "Package",
        "Version",
        "Maintainer",
        "Advisory",
        "Lockfile",
        "Service",
    }


def test_every_edge_type_present(g: Graph) -> None:
    types = {e.type for e in g.edges}
    assert types == {
        "VERSION_OF",
        "DEPENDS_ON",
        "PUBLISHED_BY",
        "AFFECTED_BY",
        "SIMILAR_NAME",
        "DECLARES",
        "RESOLVED_TO",
    }


def test_three_hop_reverse_closure(g: Graph) -> None:
    """The gate. left-pad@1.3.0 <- mid-lib <- top-app <- demo-app, at depths 1/2/3."""
    reached = g.reverse_dependents("ver:left-pad@1.3.0")

    assert set(reached) == {
        "ver:mid-lib@2.0.0",
        "ver:top-app@3.1.0",
        "ver:demo-app@1.0.0",
    }
    assert reached["ver:mid-lib@2.0.0"].depth == 1
    assert reached["ver:top-app@3.1.0"].depth == 2
    assert reached["ver:demo-app@1.0.0"].depth == 3


def test_path_is_the_proof(g: Graph) -> None:
    """Each hop carries the edge chain that justifies it — this is what the console renders."""
    hop = g.reverse_dependents("ver:left-pad@1.3.0")["ver:demo-app@1.0.0"]
    assert [e.src for e in hop.path] == [
        "ver:mid-lib@2.0.0",
        "ver:top-app@3.1.0",
        "ver:demo-app@1.0.0",
    ]
    assert [e.dst for e in hop.path] == ["pkg:left-pad", "pkg:mid-lib", "pkg:top-app"]
    assert all(e.type == "DEPENDS_ON" for e in hop.path)


def test_resolved_version_discriminates(g: Graph) -> None:
    """top-app dev-depends on left-pad@1.2.0, so it is NOT a direct dependent of 1.3.0.

    This is the case a similarity index gets wrong: both edges point at the same package and
    read almost identically, but only one resolves to the compromised version.
    """
    direct = {
        k for k, v in g.reverse_dependents("ver:left-pad@1.3.0").items() if v.depth == 1
    }
    assert direct == {"ver:mid-lib@2.0.0"}

    other = g.reverse_dependents("ver:left-pad@1.2.0")
    assert other["ver:top-app@3.1.0"].depth == 1
    assert "ver:mid-lib@2.0.0" not in other


def test_max_depth_truncates(g: Graph) -> None:
    reached = g.reverse_dependents("ver:left-pad@1.3.0", max_depth=2)
    assert set(reached) == {"ver:mid-lib@2.0.0", "ver:top-app@3.1.0"}


def test_unknown_version_yields_empty_closure(g: Graph) -> None:
    assert g.reverse_dependents("ver:nonexistent@9.9.9") == {}


# ------------------------------------------------------------------ intervals


@pytest.mark.parametrize(
    "vf,vt,ws,we,expected",
    [
        # Exposed 09:01-09:05, window 09:00-09:06 -> overlaps.
        (
            "2026-03-10T09:01:00Z",
            "2026-03-10T09:05:00Z",
            "2026-03-10T09:00:00Z",
            "2026-03-10T09:06:00Z",
            True,
        ),
        # Closed before the window opened.
        (
            "2026-03-10T08:00:00Z",
            "2026-03-10T08:30:00Z",
            "2026-03-10T09:00:00Z",
            "2026-03-10T09:06:00Z",
            False,
        ),
        # Opens after the window closed.
        (
            "2026-03-10T09:30:00Z",
            None,
            "2026-03-10T09:00:00Z",
            "2026-03-10T09:06:00Z",
            False,
        ),
        # Still open, started long ago -> overlaps.
        (
            "2018-04-05T00:00:00Z",
            None,
            "2026-03-10T09:00:00Z",
            "2026-03-10T09:06:00Z",
            True,
        ),
        # Touching at the boundary is not overlap (half-open).
        (
            "2026-03-10T09:06:00Z",
            None,
            "2026-03-10T09:00:00Z",
            "2026-03-10T09:06:00Z",
            False,
        ),
    ],
)
def test_interval_overlap(
    vf: str, vt: str | None, ws: str, we: str, expected: bool
) -> None:
    assert (
        interval_overlaps(parse_ts(vf), parse_ts(vt), parse_ts(ws), parse_ts(we))
        is expected
    )


def test_exposure_window_is_a_traversal_condition(g: Graph) -> None:
    """The lockfile resolved the bad version 09:01-09:05 then pinned back.

    Present-tense the service looks clean; during the window it was exposed. This is the
    case SCHEMA.md section 3 argues a post-filter gets wrong.
    """
    bad = next(
        e
        for e in g.out_edges("lock:acme-checkout-sha", "RESOLVED_TO")
        if e.dst == "ver:left-pad@1.3.0"
    )

    during = bad.live_during(
        parse_ts("2026-03-10T09:00:00Z"), parse_ts("2026-03-10T09:06:00Z")
    )
    after = bad.live_during(
        parse_ts("2026-03-10T10:00:00Z"), parse_ts("2026-03-10T11:00:00Z")
    )

    assert during is True
    assert after is False
