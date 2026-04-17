"""SQLite cache for the project index.

A single `projects` table keyed by canonical path. The scanner is the
source of truth — `Cache.upsert()` records what the most recent scan
saw, and `Cache.prune_stale()` removes rows that have not been touched
for a configurable cutoff.

Schema is versioned with `PRAGMA user_version`. On any version mismatch
(including a brand-new DB) we drop and recreate the table — no
migrations, just rebuild from the next scan.

Schema history:

- v1 — basic scanner output (M3.1)
- v2 — adds metadata columns: status, branch, last_commit_ts,
  last_commit_author, dirty_count (M3.2). README excerpt and ADR list
  live in `metadata_json` since they are not used in WHERE clauses.

Note on `metadata_json`-only additions: when PR #10 added
`ahead`/`behind`/`size_bytes`/`file_count`/`note_paths`, those went
into `metadata_json` without changing the column layout. The schema
version stayed at 2 because every existing row is still perfectly
readable — `_row_to_metadata` uses `.get()` with `None` defaults so
old records simply have `None` for the new fields until the next
rescan refreshes them. Bumping the schema version is reserved for
actual table-shape changes.

The cache is intentionally **not** the search engine. Filtering and
sorting happen here in SQL so the dashboard (M4) can read directly,
but anything richer (semantic search, full text) lives elsewhere.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from .models import Project, ProjectMetadata, ProjectType, Status

SCHEMA_VERSION = 2

_SCHEMA_SQL = """
CREATE TABLE projects (
    path               TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    type               TEXT NOT NULL,
    umbrella           TEXT NOT NULL,
    last_modified_ts   REAL NOT NULL,
    last_scanned_at    REAL NOT NULL,
    -- M3.2 metadata fields, NULL until enriched.
    status             TEXT,
    branch             TEXT,
    last_commit_ts     REAL,
    last_commit_author TEXT,
    dirty_count        INTEGER,
    -- README excerpt + ADR paths + anything else not worth a column.
    metadata_json      TEXT
);
CREATE INDEX idx_projects_type     ON projects(type);
CREATE INDEX idx_projects_umbrella ON projects(umbrella);
CREATE INDEX idx_projects_status   ON projects(status);
"""

_DEFAULT_PRUNE_CUTOFF = timedelta(days=7)


def default_db_path() -> Path:
    """Where the cache lives if the caller does not override it.

    The `ARMILLARY_CACHE_DB` env var takes precedence — useful for tests
    and for users who want to keep their cache somewhere unusual. Otherwise
    we follow the XDG / macOS conventions.
    """
    override = os.environ.get("ARMILLARY_CACHE_DB")
    if override:
        return Path(override).expanduser()

    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "armillary"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            base = Path(xdg).expanduser() / "armillary"
        else:
            base = Path.home() / ".local" / "share" / "armillary"
    return base / "cache.db"


class Cache:
    """Thin wrapper around a SQLite connection holding the project index.

    Use as a context manager:

        with Cache() as cache:
            cache.upsert(projects)
            cache.prune_stale()
            for p in cache.list_projects(type=ProjectType.GIT):
                ...
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_db_path()
        self._conn: sqlite3.Connection | None = None

    # ----- lifecycle --------------------------------------------------------

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError(
                "Cache is not open. Use `with Cache() as cache:` or call open()."
            )
        return self._conn

    # ----- schema -----------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Create the schema if missing; rebuild on any version mismatch.

        We do not run migrations — the cache is derived data and a fresh
        scan reproduces it cheaply. Bumping `SCHEMA_VERSION` in this module
        is therefore a complete schema reset, not a migration.
        """
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            # Verify the table actually exists (defensive: someone may have
            # set user_version without creating the schema).
            existing = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='projects'"
            ).fetchone()
            if existing is not None:
                return
        # Wrong version, missing table, or fresh DB — wipe and recreate.
        self.conn.execute("DROP TABLE IF EXISTS projects")
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    # ----- mutations --------------------------------------------------------

    def upsert(
        self,
        projects: Iterable[Project],
        *,
        write_metadata: bool = True,
    ) -> int:
        """Insert or update a batch of projects.

        `last_scanned_at` is stamped with `time.time()` so a later
        `prune_stale()` call can remove rows that no scan has touched in
        a while.

        With `write_metadata=True` (the default) every metadata column
        is overwritten from each project's `ProjectMetadata`. A `None`
        metadata in this mode means "we tried extraction and got nothing"
        and resets the columns to `NULL`.

        With `write_metadata=False` (used by `armillary scan
        --no-metadata`) the basic scanner columns are refreshed but the
        metadata columns are left untouched on existing rows. New rows
        still insert with NULL metadata so the next full scan can
        populate them. This way a fast rescan does not wipe earlier
        extraction work.

        Returns the number of rows written.
        """
        if write_metadata:
            return self._upsert_with_metadata(projects)
        return self._upsert_basic_only(projects)

    def _upsert_with_metadata(self, projects: Iterable[Project]) -> int:
        now = time.time()
        rows = [_project_to_row(p, now=now) for p in projects]
        self.conn.executemany(
            """
            INSERT INTO projects (
                path, name, type, umbrella,
                last_modified_ts, last_scanned_at,
                status, branch, last_commit_ts, last_commit_author,
                dirty_count, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                name               = excluded.name,
                type               = excluded.type,
                umbrella           = excluded.umbrella,
                last_modified_ts   = excluded.last_modified_ts,
                last_scanned_at    = excluded.last_scanned_at,
                status             = excluded.status,
                branch             = excluded.branch,
                last_commit_ts     = excluded.last_commit_ts,
                last_commit_author = excluded.last_commit_author,
                dirty_count        = excluded.dirty_count,
                metadata_json      = excluded.metadata_json
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def _upsert_basic_only(self, projects: Iterable[Project]) -> int:
        """Refresh just the scanner columns; preserve existing metadata."""
        now = time.time()
        rows = [
            (
                str(p.path),
                p.name,
                p.type.value,
                str(p.umbrella),
                p.last_modified.timestamp(),
                now,
            )
            for p in projects
        ]
        self.conn.executemany(
            """
            INSERT INTO projects (
                path, name, type, umbrella, last_modified_ts, last_scanned_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                name             = excluded.name,
                type             = excluded.type,
                umbrella         = excluded.umbrella,
                last_modified_ts = excluded.last_modified_ts,
                last_scanned_at  = excluded.last_scanned_at
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def prune_stale(self, *, older_than: timedelta = _DEFAULT_PRUNE_CUTOFF) -> int:
        """Drop rows whose `last_scanned_at` is older than `older_than`.

        Multi-umbrella aware: scanning umbrella A then umbrella B does not
        nuke A's projects, because each `upsert` only refreshes its own
        rows. Stale rows accumulate naturally over time and this method
        sweeps them out.

        Returns the number of rows deleted.
        """
        cutoff = time.time() - older_than.total_seconds()
        cur = self.conn.execute(
            "DELETE FROM projects WHERE last_scanned_at < ?",
            (cutoff,),
        )
        self.conn.commit()
        return cur.rowcount

    def clear_projects(self) -> int:
        """Delete every row in the `projects` table.

        Used by `armillary config --init` to make sure a fresh setup
        leaves the cache containing exactly what the new config covers,
        not stale rows from a previous umbrella selection. The age-based
        `prune_stale()` is too lenient for this case — recent rows from
        a removed umbrella would persist for up to the prune cutoff.

        Returns the number of rows deleted.
        """
        cur = self.conn.execute("DELETE FROM projects")
        self.conn.commit()
        return cur.rowcount

    # ----- queries ----------------------------------------------------------

    def list_projects(
        self,
        *,
        type: ProjectType | None = None,
        umbrella_substring: str | None = None,
        status: Status | None = None,
    ) -> list[Project]:
        """Return projects from cache, sorted by `last_modified` desc.

        Filter parameters are AND-combined. `umbrella_substring` is a
        case-sensitive SQL `LIKE %x%` filter on the stored umbrella path
        — good enough for `armillary list`; the dashboard (M4) will use
        richer search.
        """
        sql = (
            "SELECT path, name, type, umbrella, last_modified_ts, "
            "status, branch, last_commit_ts, last_commit_author, "
            "dirty_count, metadata_json "
            "FROM projects"
        )
        where: list[str] = []
        params: list[object] = []
        if type is not None:
            where.append("type = ?")
            params.append(type.value)
        if status is not None:
            where.append("status = ?")
            params.append(status.value)
        if umbrella_substring:
            where.append("umbrella LIKE ?")
            params.append(f"%{umbrella_substring}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_modified_ts DESC"

        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_project(r) for r in rows]

    def get_project(self, path: str | Path) -> Project | None:
        """Single project by canonical path. Returns None if not cached."""
        row = self.conn.execute(
            "SELECT path, name, type, umbrella, last_modified_ts, "
            "status, branch, last_commit_ts, last_commit_author, "
            "dirty_count, metadata_json "
            "FROM projects WHERE path = ?",
            (str(path),),
        ).fetchone()
        if row is None:
            return None
        return _row_to_project(row)

    def get_project_by_name(self, name: str) -> Project | None:
        """Single project by name. Returns None if not cached."""
        row = self.conn.execute(
            "SELECT path, name, type, umbrella, last_modified_ts, "
            "status, branch, last_commit_ts, last_commit_author, "
            "dirty_count, metadata_json "
            "FROM projects WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_project(row)

    def last_scan_time(self) -> float | None:
        """Latest ``last_scanned_at`` across all rows. None if cache is empty."""
        row = self.conn.execute("SELECT MAX(last_scanned_at) FROM projects").fetchone()
        if row is None or row[0] is None:
            return None
        return row[0]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]


# --- row <-> Project mapping -----------------------------------------------


def _project_to_row(p: Project, *, now: float) -> tuple[Any, ...]:
    md = p.metadata
    status = md.status.value if md and md.status else None
    branch = md.branch if md else None
    last_commit_ts = md.last_commit_ts.timestamp() if md and md.last_commit_ts else None
    last_commit_author = md.last_commit_author if md else None
    dirty_count = md.dirty_count if md else None
    metadata_json = _serialize_metadata_extra(md) if md else None
    return (
        str(p.path),
        p.name,
        p.type.value,
        str(p.umbrella),
        p.last_modified.timestamp(),
        now,
        status,
        branch,
        last_commit_ts,
        last_commit_author,
        dirty_count,
        metadata_json,
    )


def _row_to_project(row: sqlite3.Row) -> Project:
    md = _row_to_metadata(row)
    return Project(
        path=Path(row["path"]),
        name=row["name"],
        type=ProjectType(row["type"]),
        umbrella=Path(row["umbrella"]),
        last_modified=datetime.fromtimestamp(row["last_modified_ts"]),
        metadata=md,
    )


def _row_to_metadata(row: sqlite3.Row) -> ProjectMetadata | None:
    """Reconstruct a `ProjectMetadata` from row columns + metadata_json.

    Returns `None` only when *every* metadata column is empty, so callers
    can distinguish "never extracted" from "extracted but empty".
    """
    has_any = any(
        row[col] is not None
        for col in (
            "status",
            "branch",
            "last_commit_ts",
            "last_commit_author",
            "dirty_count",
            "metadata_json",
        )
    )
    if not has_any:
        return None

    extra = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
    return ProjectMetadata(
        branch=row["branch"],
        last_commit_sha=extra.get("last_commit_sha"),
        last_commit_ts=(
            datetime.fromtimestamp(row["last_commit_ts"])
            if row["last_commit_ts"] is not None
            else None
        ),
        last_commit_author=row["last_commit_author"],
        dirty_count=row["dirty_count"],
        ahead=extra.get("ahead"),
        behind=extra.get("behind"),
        commit_count=extra.get("commit_count"),
        work_hours=extra.get("work_hours"),
        size_bytes=extra.get("size_bytes"),
        file_count=extra.get("file_count"),
        readme_excerpt=extra.get("readme_excerpt"),
        adr_paths=[Path(p) for p in extra.get("adr_paths", [])],
        note_paths=[Path(p) for p in extra.get("note_paths", [])],
        # Decision signals (ADR 0017)
        commit_velocity=extra.get("commit_velocity"),
        velocity_trend=extra.get("velocity_trend"),
        first_commit_ts=(
            datetime.fromtimestamp(extra["first_commit_ts"])
            if extra.get("first_commit_ts")
            else None
        ),
        branch_count=extra.get("branch_count"),
        has_remote=extra.get("has_remote"),
        status=Status(row["status"]) if row["status"] else None,
    )


def _serialize_metadata_extra(md: ProjectMetadata) -> str | None:
    """Serialize the `ProjectMetadata` fields that don't get their own column."""
    payload = {
        "last_commit_sha": md.last_commit_sha,
        "ahead": md.ahead,
        "behind": md.behind,
        "commit_count": md.commit_count,
        "work_hours": md.work_hours,
        "size_bytes": md.size_bytes,
        "file_count": md.file_count,
        "readme_excerpt": md.readme_excerpt,
        "adr_paths": [str(p) for p in md.adr_paths],
        "note_paths": [str(p) for p in md.note_paths],
        # Decision signals (ADR 0017)
        "commit_velocity": md.commit_velocity,
        "velocity_trend": md.velocity_trend,
        "first_commit_ts": (
            md.first_commit_ts.timestamp() if md.first_commit_ts else None
        ),
        "branch_count": md.branch_count,
        "has_remote": md.has_remote,
    }
    # Drop empty keys to keep the JSON small and the diff readable.
    cleaned = {k: v for k, v in payload.items() if v not in (None, [], "")}
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None
