"""Deterministic, interval-aware dependency graph.

This is the layer that answers the brief's six questions *exactly*. HydraDB carries the
semantic half (advisory prose, maintainer identity, typosquat plausibility, human-readable
provenance); see SCHEMA.md section 0 for the split and why it exists.

The traversal that matters is the reverse one: ``DEPENDS_ON`` is stored ``Version -> Package``,
so finding "everything that transitively depends on this exact version" walks edges backwards
through a materialized reverse index, filtered on the *resolved* version at each step.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = ["Edge", "Graph", "Hop", "Node", "interval_overlaps", "parse_ts"]

# Open intervals use None rather than a sentinel date; see SCHEMA.md section 3.
Timestamp = datetime | None


def parse_ts(value: str | None) -> Timestamp:
    """Parse an ISO-8601 timestamp. ``None`` means the interval is open at that end."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


def interval_overlaps(
    valid_from: Timestamp,
    valid_to: Timestamp,
    window_start: Timestamp,
    window_end: Timestamp,
) -> bool:
    """Half-open interval overlap: ``[valid_from, valid_to)`` vs ``[window_start, window_end)``.

    A ``None`` bound is unbounded on that side. This is the predicate that makes "who was
    exposed between 09:00 and 09:06" a traversal condition instead of a post-filter: an edge
    that closed before the window opened is never expanded, so the frontier itself is
    time-scoped.
    """
    starts_after_window = (
        window_end is not None and valid_from is not None and valid_from >= window_end
    )
    ends_before_window = (
        window_start is not None and valid_to is not None and valid_to <= window_start
    )
    return not (starts_after_window or ends_before_window)


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    kind: str
    props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Edge:
    type: str
    src: str
    dst: str
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def valid_from(self) -> Timestamp:
        return parse_ts(self.props.get("valid_from"))

    @property
    def valid_to(self) -> Timestamp:
        return parse_ts(self.props.get("valid_to"))

    def live_during(self, start: Timestamp, end: Timestamp) -> bool:
        """True if this edge's validity interval overlaps the given window.

        Edges with no interval (``DEPENDS_ON``, ``VERSION_OF``) are timeless and always live.
        """
        if "valid_from" not in self.props and "valid_to" not in self.props:
            return True
        return interval_overlaps(self.valid_from, self.valid_to, start, end)


@dataclass(frozen=True, slots=True)
class Hop:
    """One reached node in a traversal, with the edge path that proves it."""

    node_id: str
    depth: int
    path: tuple[Edge, ...]


class Graph:
    """In-memory typed graph with forward and reverse adjacency."""

    def __init__(self, nodes: Iterable[Node], edges: Iterable[Edge]) -> None:
        self.nodes: dict[str, Node] = {n.id: n for n in nodes}
        self.edges: list[Edge] = list(edges)

        # Forward: src -> type -> edges.  Reverse: dst -> type -> edges.
        self._out: dict[str, dict[str, list[Edge]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._in: dict[str, dict[str, list[Edge]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for e in self.edges:
            self._out[e.src][e.type].append(e)
            self._in[e.dst][e.type].append(e)

    # ---------------------------------------------------------------- loading

    @classmethod
    def load(cls, path: str | Path) -> Graph:
        raw = json.loads(Path(path).read_text())
        nodes = [Node(n["id"], n["kind"], n.get("props", {})) for n in raw["nodes"]]
        edges = [
            Edge(e["type"], e["src"], e["dst"], e.get("props", {}))
            for e in raw["edges"]
        ]
        return cls(nodes, edges)

    # ------------------------------------------------------------- accessors

    def out_edges(self, node_id: str, edge_type: str) -> list[Edge]:
        return self._out.get(node_id, {}).get(edge_type, [])

    def in_edges(self, node_id: str, edge_type: str) -> list[Edge]:
        return self._in.get(node_id, {}).get(edge_type, [])

    def by_kind(self, kind: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.kind == kind]

    def package_of(self, version_id: str) -> str | None:
        """The Package a Version belongs to, via VERSION_OF."""
        edges = self.out_edges(version_id, "VERSION_OF")
        return edges[0].dst if edges else None

    def version_string(self, version_id: str) -> str | None:
        node = self.nodes.get(version_id)
        return None if node is None else str(node.props.get("version"))

    # ------------------------------------------------------------- traversal

    def reverse_dependents(
        self,
        version_id: str,
        *,
        max_depth: int | None = None,
        window_start: Timestamp = None,
        window_end: Timestamp = None,
    ) -> dict[str, Hop]:
        """Transitive reverse-dependency closure of a concrete ``Version``.

        Walks ``DEPENDS_ON`` backwards. At each step a dependent only counts if the edge's
        ``resolved`` property matches the version actually reached — so a dependency on the
        same package at a *different* resolved version is correctly excluded.

        Returns every reached Version keyed by id, with its depth and the edge path that
        proves it. The starting version is not included in its own closure.
        """
        start_pkg = self.package_of(version_id)
        start_ver = self.version_string(version_id)
        if start_pkg is None or start_ver is None:
            return {}

        reached: dict[str, Hop] = {}
        # Frontier carries (package, resolved-version-string, path-so-far).
        queue: deque[tuple[str, str, tuple[Edge, ...]]] = deque(
            [(start_pkg, start_ver, ())]
        )

        while queue:
            pkg_id, resolved, path = queue.popleft()
            depth = len(path)
            if max_depth is not None and depth >= max_depth:
                continue

            for edge in self.in_edges(pkg_id, "DEPENDS_ON"):
                if str(edge.props.get("resolved")) != resolved:
                    continue
                if not edge.live_during(window_start, window_end):
                    continue

                dependent = edge.src
                new_path = path + (edge,)
                existing = reached.get(dependent)
                if existing is not None and existing.depth <= len(new_path):
                    continue

                reached[dependent] = Hop(dependent, len(new_path), new_path)

                next_pkg = self.package_of(dependent)
                next_ver = self.version_string(dependent)
                if next_pkg is not None and next_ver is not None:
                    queue.append((next_pkg, next_ver, new_path))

        return reached
