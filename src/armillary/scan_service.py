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
        from .transition_service import detect_and_store_transitions

        detect_and_store_transitions()

    # Index code blocks for Steal (ADR 0027). Best-effort — failures
    # must never break the scan. Skip idea projects (no git tree).
    with contextlib.suppress(Exception):
        _index_code_blocks(projects)

    return projects


def _index_code_blocks(projects: list[Project]) -> None:
    """Populate the Steal code-block index from tracked files.

    Isolated in its own helper so `contextlib.suppress` in `full_scan`
    catches the whole sub-pipeline (FTS5 unsupported, disk full, etc.)
    without swallowing more than we intend.

    Per ADR 0031: each repo is indexed under a framework profile
    detected from its manifests. The profile name plus
    files_indexed / files_skipped counts are persisted to the project
    metadata blob so a misclassified layout is observable instead of
    silent.
    """
    import contextlib as _ctx

    from .cache import Cache
    from .code_block_service import build_blocks_with_stats
    from .code_index import CodeIndex
    from .framework_profiles import detect_profile

    stats_by_path: dict[str, tuple[str, int, int]] = {}

    with CodeIndex() as idx:
        for project in projects:
            if project.type is not ProjectType.GIT:
                continue
            with _ctx.suppress(Exception):
                profile = detect_profile(project.path)
                blocks, result = build_blocks_with_stats(project.path, profile=profile)
                stats_by_path[str(project.path)] = (
                    result.profile_name,
                    result.files_indexed,
                    result.files_skipped,
                )
                # Group by file path — upsert per-file so a partial
                # failure leaves earlier files indexed.
                by_file: dict[str, list] = {}
                for blk in blocks:
                    by_file.setdefault(blk.path, []).append(blk)
                idx.delete_repo(str(project.path))
                for path_str, file_blocks in by_file.items():
                    idx.upsert_blocks(str(project.path), path_str, file_blocks)

    # Persist profile observability back to the project cache. We
    # deliberately do NOT mutate `Project.metadata` on the in-memory
    # list — `armillary scan` prints those objects as JSON and the
    # contract (test_scan_json_output_unchanged_when_caching) is that
    # stdout is invariant to whether the cache was written. So we
    # build copies, upsert those, and let `--report-profiles` read
    # from the cache after the scan completes.
    if not stats_by_path:
        return
    touched: list[Project] = []
    for project in projects:
        stats = stats_by_path.get(str(project.path))
        if stats is None or project.metadata is None:
            continue
        name, indexed, skipped = stats
        md_copy = project.metadata.model_copy(
            update={
                "index_profile": name,
                "index_files_indexed": indexed,
                "index_files_skipped": skipped,
            }
        )
        touched.append(project.model_copy(update={"metadata": md_copy}))
    if touched:
        with _ctx.suppress(Exception), Cache() as cache:
            cache.upsert(touched, write_metadata=True)


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
