"""SQLite store for the deterministic graph.

Mirrors SCHEMA.md: the node kinds and edge types are the same, realized as tables with the
indices the six queries actually need. SQLite because the closure must be exact and fast, the
whole artifact has to ship to a Vercel function, and a read-only file is the least surprising
thing to hand a judge cloning the repo.

The reverse index on ``depends_on(dst_pkg, resolved)`` is the one that matters: it turns the
headline blast-radius query from a scan into a lookup.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Self

__all__ = ["SCHEMA_SQL", "Store"]

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS package (
    name           TEXT PRIMARY KEY,
    ecosystem      TEXT NOT NULL DEFAULT 'npm',
    downloads      INTEGER,
    download_rank  INTEGER,
    description    TEXT,
    latest_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_package_rank ON package(download_rank);

CREATE TABLE IF NOT EXISTS version (
    pkg          TEXT NOT NULL,
    version      TEXT NOT NULL,
    published_at TEXT,
    is_latest    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (pkg, version)
);
CREATE INDEX IF NOT EXISTS idx_version_pkg ON version(pkg);

CREATE TABLE IF NOT EXISTS maintainer (
    handle        TEXT PRIMARY KEY,
    package_count INTEGER NOT NULL DEFAULT 0
);

-- Package -> Maintainer
CREATE TABLE IF NOT EXISTS published_by (
    pkg     TEXT NOT NULL,
    handle  TEXT NOT NULL,
    PRIMARY KEY (pkg, handle)
);
CREATE INDEX IF NOT EXISTS idx_published_by_handle ON published_by(handle);

-- Version -> Package, carrying the declared range and the resolved concrete version.
CREATE TABLE IF NOT EXISTS depends_on (
    src_pkg     TEXT NOT NULL,
    src_version TEXT NOT NULL,
    dst_pkg     TEXT NOT NULL,
    range       TEXT,
    resolved    TEXT,
    dep_type    TEXT NOT NULL DEFAULT 'prod',
    PRIMARY KEY (src_pkg, src_version, dst_pkg, dep_type)
);
-- The reverse index. Every blast-radius hop is a lookup on this.
CREATE INDEX IF NOT EXISTS idx_depends_reverse ON depends_on(dst_pkg, resolved);
CREATE INDEX IF NOT EXISTS idx_depends_forward ON depends_on(src_pkg, src_version);

CREATE TABLE IF NOT EXISTS advisory (
    osv_id       TEXT PRIMARY KEY,
    aliases      TEXT,
    severity     TEXT,          -- GHSA label: LOW/MODERATE/HIGH/CRITICAL, when published
    cvss         TEXT,          -- CVSS vector string verbatim (e.g. "CVSS:4.0/AV:N/...")
    published_at TEXT NOT NULL,
    modified_at  TEXT,
    summary      TEXT,
    details      TEXT,          -- GHSA only; MAL-* details are scanner boilerplate (130MB)
    withdrawn    TEXT,          -- OSV retraction timestamp; non-NULL = excluded from queries
    withheld     INTEGER NOT NULL DEFAULT 0  -- 1 = held out as eval set
);
CREATE INDEX IF NOT EXISTS idx_advisory_withdrawn ON advisory(withdrawn);
CREATE INDEX IF NOT EXISTS idx_advisory_published ON advisory(published_at);
CREATE INDEX IF NOT EXISTS idx_advisory_withheld ON advisory(withheld);

-- Package -> Advisory, with version bounds and a validity interval.
CREATE TABLE IF NOT EXISTS affected_by (
    pkg        TEXT NOT NULL,
    osv_id     TEXT NOT NULL,
    introduced TEXT,
    fixed      TEXT,
    valid_from TEXT,
    valid_to   TEXT,
    PRIMARY KEY (pkg, osv_id, introduced)
);
CREATE INDEX IF NOT EXISTS idx_affected_pkg ON affected_by(pkg);
CREATE INDEX IF NOT EXISTS idx_affected_osv ON affected_by(osv_id);

CREATE TABLE IF NOT EXISTS similar_name (
    pkg      TEXT NOT NULL,
    other    TEXT NOT NULL,
    distance INTEGER NOT NULL,
    rank_gap INTEGER,
    PRIMARY KEY (pkg, other)
);
CREATE INDEX IF NOT EXISTS idx_similar_pkg ON similar_name(pkg);

-- Service / Lockfile are instantiated from uploaded package-lock.json files.
CREATE TABLE IF NOT EXISTS service (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    owner       TEXT,
    environment TEXT
);

CREATE TABLE IF NOT EXISTS lockfile (
    id               TEXT PRIMARY KEY,
    service_id       TEXT,
    lockfile_version INTEGER,
    entry_count      INTEGER,
    parsed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_lockfile_service ON lockfile(service_id);

-- Lockfile -> Version, with the validity interval that makes the temporal query a
-- traversal condition instead of a post-filter. See SCHEMA.md section 3.
CREATE TABLE IF NOT EXISTS resolved_to (
    lockfile_id TEXT NOT NULL,
    pkg         TEXT NOT NULL,
    version     TEXT NOT NULL,
    resolved_at TEXT,
    valid_from  TEXT,
    valid_to    TEXT,
    PRIMARY KEY (lockfile_id, pkg, version, valid_from)
);
CREATE INDEX IF NOT EXISTS idx_resolved_pkg ON resolved_to(pkg, version);
CREATE INDEX IF NOT EXISTS idx_resolved_lockfile ON resolved_to(lockfile_id);

-- Free-form provenance for the ingest run itself; surfaced in STATE.md and EVAL.md.
CREATE TABLE IF NOT EXISTS ingest_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    """Thin typed wrapper over the SQLite graph."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.conn.commit()
        self.close()

    # ----------------------------------------------------------------- meta

    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO ingest_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        self.conn.commit()

    def get_meta(self, key: str) -> Any:
        row = self.conn.execute(
            "SELECT value FROM ingest_meta WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else json.loads(row["value"])

    # ---------------------------------------------------------------- counts

    def counts(self) -> dict[str, int]:
        tables = [
            "package",
            "version",
            "maintainer",
            "published_by",
            "depends_on",
            "advisory",
            "affected_by",
            "similar_name",
            "service",
            "lockfile",
            "resolved_to",
        ]
        out: dict[str, int] = {}
        for t in tables:
            row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()
            out[t] = int(row["n"])
        return out

    # ---------------------------------------------------------------- writes

    def upsert_packages(self, rows: list[tuple[str, int | None, int | None]]) -> None:
        """(name, downloads, download_rank)."""
        self.conn.executemany(
            "INSERT INTO package(name, downloads, download_rank) VALUES(?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "  downloads = excluded.downloads, download_rank = excluded.download_rank",
            rows,
        )

    def upsert_versions(self, rows: list[tuple[str, str, str | None, int]]) -> None:
        """(pkg, version, published_at, is_latest)."""
        self.conn.executemany(
            "INSERT INTO version(pkg, version, published_at, is_latest) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(pkg, version) DO UPDATE SET "
            "  published_at = COALESCE(excluded.published_at, version.published_at), "
            "  is_latest = MAX(excluded.is_latest, version.is_latest)",
            rows,
        )

    def upsert_maintainers(self, handles: list[str]) -> None:
        self.conn.executemany(
            "INSERT INTO maintainer(handle) VALUES(?) ON CONFLICT(handle) DO NOTHING",
            [(h,) for h in handles],
        )

    def upsert_published_by(self, rows: list[tuple[str, str]]) -> None:
        self.conn.executemany(
            "INSERT INTO published_by(pkg, handle) VALUES(?, ?) "
            "ON CONFLICT(pkg, handle) DO NOTHING",
            rows,
        )

    def upsert_depends_on(
        self, rows: list[tuple[str, str, str, str | None, str | None, str]]
    ) -> None:
        """(src_pkg, src_version, dst_pkg, range, resolved, dep_type)."""
        self.conn.executemany(
            "INSERT INTO depends_on(src_pkg, src_version, dst_pkg, range, resolved, dep_type) "
            "VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(src_pkg, src_version, dst_pkg, dep_type) DO UPDATE SET "
            "  range = excluded.range, resolved = excluded.resolved",
            rows,
        )

    def commit(self) -> None:
        self.conn.commit()
