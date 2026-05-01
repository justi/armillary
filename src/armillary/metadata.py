"""Per-project metadata extraction (orchestrator).

For git projects: branch, head commit (sha + timestamp + author),
dirty file count via GitPython. For all projects: README excerpt
(first 2-3 sentences) and ADR file list.

The extractor is **fault-tolerant by design**. A broken repo, missing
README, unreadable ADR directory, or unexpected GitPython exception
must never crash a scan — it just leaves the affected fields `None`.
The dashboard and `armillary list` then fall back to filesystem-only
signals for that project.

Parallelism: `extract_all()` uses a thread pool because GitPython
operations are I/O-bound (they shell out to `git`). For ~100 repos
this brings a sequential scan down from "annoying" to "instant" on
modern disks.

No status heuristics here — that lives in `status.py`. This module
just collects facts.

The actual collection logic lives in three split modules:

- ``metadata_git`` — git fields, commit stats, velocity, monthly activity
- ``metadata_readme`` — README excerpt extraction
- ``metadata_files`` — ADR + notes file lists, size + file_count walk

This module orchestrates them and re-exports the private helpers that
``tests/test_metadata.py`` imports directly.
"""

from __future__ import annotations

import contextlib
from concurrent.futures import ThreadPoolExecutor

from .metadata_files import (
    _compute_size_and_count,
    _find_adr_files,
    _find_note_files,
)
from .metadata_git import _fill_git_fields
from .metadata_readme import _extract_readme_excerpt, _first_paragraph_plain
from .models import Project, ProjectMetadata, ProjectType

__all__ = [
    "DEFAULT_WORKERS",
    "extract",
    "extract_all",
    # Re-exported private helpers for tests.
    "_extract_readme_excerpt",
    "_find_adr_files",
    "_find_note_files",
    "_first_paragraph_plain",
]

DEFAULT_WORKERS = 4


def extract(project: Project) -> ProjectMetadata:
    """Synchronously collect metadata for one project.

    All exceptions are swallowed and converted to "field stays None",
    so callers can rely on getting a `ProjectMetadata` instance back
    no matter how broken the underlying repo is.
    """
    md = ProjectMetadata()

    if project.type is ProjectType.GIT:
        # noqa: BLE001 — fault tolerance is the entire point of this layer.
        with contextlib.suppress(Exception):
            _fill_git_fields(project.path, md)

    with contextlib.suppress(Exception):
        md.readme_excerpt = _extract_readme_excerpt(project.path)

    try:
        md.adr_paths = _find_adr_files(project.path)
    except Exception:  # noqa: BLE001
        md.adr_paths = []

    try:
        md.note_paths = _find_note_files(project.path)
    except Exception:  # noqa: BLE001
        md.note_paths = []

    with contextlib.suppress(Exception):
        size_bytes, file_count = _compute_size_and_count(project.path)
        md.size_bytes = size_bytes
        md.file_count = file_count

    return md


def extract_all(
    projects: list[Project],
    *,
    workers: int = DEFAULT_WORKERS,
) -> None:
    """Extract metadata for every project in parallel and attach in place.

    Order of `projects` is preserved. Each project's `metadata` field is
    overwritten with the freshly-extracted value (or an empty
    `ProjectMetadata` if extraction returned None for some reason).
    """
    if not projects:
        return
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(extract, projects))
    for project, md in zip(projects, results, strict=True):
        project.metadata = md or ProjectMetadata()
