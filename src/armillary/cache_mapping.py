"""Translation between SQLite rows and ``Project`` / ``ProjectMetadata``.

Split out of ``cache.py`` for the 400-line architecture target
(ADR 0001 rule 3). The cache module owns the schema and queries;
this module owns the data shape.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Project, ProjectMetadata, ProjectType, Status


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


def _safe_status(raw: str | None) -> Status | None:
    """Parse status string, handling renamed values gracefully."""
    if not raw:
        return None
    # Handle PAUSED → STALLED rename
    if raw == "PAUSED":
        return Status.STALLED
    try:
        return Status(raw)
    except ValueError:
        return None


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
        monthly_commits=extra.get("monthly_commits"),
        branch_count=extra.get("branch_count"),
        has_remote=extra.get("has_remote"),
        # ADR 0031 — framework-aware indexing observability.
        index_profile=extra.get("index_profile"),
        index_files_indexed=extra.get("index_files_indexed"),
        index_files_skipped=extra.get("index_files_skipped"),
        status=_safe_status(row["status"]),
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
        "monthly_commits": md.monthly_commits,
        "branch_count": md.branch_count,
        "has_remote": md.has_remote,
        # ADR 0031.
        "index_profile": md.index_profile,
        "index_files_indexed": md.index_files_indexed,
        "index_files_skipped": md.index_files_skipped,
    }
    # Drop empty keys to keep the JSON small and the diff readable.
    cleaned = {k: v for k, v in payload.items() if v not in (None, [], "")}
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None
