"""Scan orchestration — walk, extract, status compute, cache upsert.

Centralises the scan pipeline that was previously duplicated in
``cli.scan()``, ``cli._pre_start_scan()``,
``cli._run_initial_scan_and_summary()``, and
``ui.helpers._run_dashboard_scan()``.

Services never import ``typer`` or ``streamlit``. They return data;
the CLI/UI layer handles user-facing output.
"""

from __future__ import annotations

from armillary import metadata as metadata_mod
from armillary import status as status_mod
from armillary.cache import Cache
from armillary.models import Project, ProjectType, UmbrellaFolder
from armillary.scanner import scan as scan_umbrellas


def enrich(projects: list[Project]) -> None:
    """Extract metadata and compute status **in place**, no cache writes.

    Useful when the caller wants the enriched project list without
    persisting to the cache (e.g. ``armillary scan --no-cache``).
    """
    metadata_mod.extract_all(projects)
    _lift_last_modified_and_compute_status(projects)


def full_scan(
    umbrellas: list[UmbrellaFolder],
    *,
    write_metadata: bool = True,
) -> list[Project]:
    """Walk + extract + status compute + cache upsert + prune.

    When *write_metadata* is ``False`` the scanner columns are refreshed
    but metadata extraction is skipped entirely (fast path for
    ``armillary scan --no-metadata``).

    Returns the list of discovered projects.
    """
    projects = scan_umbrellas(umbrellas)

    if write_metadata:
        enrich(projects)

    with Cache() as cache:
        cache.upsert(projects, write_metadata=write_metadata)
        cache.prune_stale()

    # Record weekly pulse snapshot (idempotent per week)
    import contextlib

    with contextlib.suppress(Exception):
        from .pulse_service import take_snapshot

        take_snapshot()

    # Detect status transitions (ADR 0025)
    with contextlib.suppress(Exception):
        from .transition_service import detect_transitions

        detect_transitions()

    return projects


def initial_scan(umbrellas: list[UmbrellaFolder]) -> list[Project]:
    """Walk + extract + status compute + cache clear + upsert (no prune).

    Used by ``armillary config --init`` to start from a clean slate so
    no rows from a removed umbrella linger in the dashboard.
    """
    projects = scan_umbrellas(umbrellas)
    enrich(projects)

    with Cache() as cache:
        cache.clear_projects()
        cache.upsert(projects, write_metadata=True)

    return projects


def incremental_scan(
    umbrellas: list[UmbrellaFolder],
) -> tuple[list[Project], int]:
    """Compare mtime vs cache — only extract metadata for changed projects.

    Returns ``(all_projects, changed_count)``.
    """
    projects = scan_umbrellas(umbrellas)

    # Load cached metadata so we can skip unchanged projects.
    with Cache() as cache:
        cached = {str(p.path): p for p in cache.list_projects()}

    needs_extract: list[Project] = []
    for project in projects:
        cp = cached.get(str(project.path))
        if (
            cp is not None
            and cp.metadata is not None
            and abs((cp.last_modified - project.last_modified).total_seconds())
            < 2  # filesystem mtime granularity
        ):
            project.metadata = cp.metadata
        else:
            needs_extract.append(project)

    if needs_extract:
        metadata_mod.extract_all(needs_extract)

    _lift_last_modified_and_compute_status(needs_extract)

    with Cache() as cache:
        cache.upsert(projects, write_metadata=True)
        cache.prune_stale()

    return projects, len(needs_extract)


# --- internal helpers -------------------------------------------------------


def _lift_last_modified_and_compute_status(
    projects: list[Project],
) -> None:
    """Apply the ``last_modified = max(fs, last_commit_ts)`` lift and
    compute status for each project that has metadata.

    See ``cli.scan()`` for the full rationale on this reconciliation.
    """
    for project in projects:
        if project.metadata is None:
            continue
        if (
            project.type is ProjectType.GIT
            and project.metadata.last_commit_ts is not None
            and project.metadata.last_commit_ts > project.last_modified
        ):
            project.last_modified = project.metadata.last_commit_ts
        project.metadata.status = status_mod.compute_status(project)
