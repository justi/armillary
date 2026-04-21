"""Ranked cross-repo code retrieval (ADR 0027 — Steal).

Given a query, returns a ranked list of code blocks drawn from every
indexed repo. Ranking is heuristic — recency and project status only
— per the ADR's "ship v1 with two signals" directive. More signals
wait for real feedback-loop data.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

from .cache import Cache
from .code_index import CodeBlockRow, CodeIndex
from .exclude_service import filter_excluded
from .models import Project, Status
from .status_override import filter_archived, get_override

_OVERFETCH_MULTIPLIER = 10

# ADR 0027 ranking: 2 signals only. Weights are deliberately simple so
# the feedback loop (thumbs up/down) generates the data we need before
# adding more dimensions.
_RECENCY_WEIGHT = 0.7
_STATUS_WEIGHT = 0.3
_RECENCY_DECAY_DAYS = 180.0

_STATUS_SCORES: dict[str, float] = {
    Status.ACTIVE.value: 1.0,
    Status.STALLED.value: 0.75,
    Status.IN_PROGRESS.value: 0.5,
    Status.DORMANT.value: 0.5,
    Status.IDEA.value: 0.25,
    Status.ARCHIVED.value: 0.0,
}
_STATUS_SCORE_DEFAULT = 0.5


@dataclass(frozen=True)
class StealResult:
    """A single ranked code block plus the project context it came from."""

    block: CodeBlockRow
    project_name: str
    project_status: str | None
    score: float


def steal(
    query: str,
    *,
    limit: int = 5,
    language: str | None = None,
) -> list[StealResult]:
    """Return up to ``limit`` ranked blocks matching ``query``.

    Empty query returns []. The language filter matches
    ``language_ext`` exactly (e.g. ``"py"``, ``"rb"``, ``"ts"``).
    Projects flagged as excluded or archived are still searchable
    because the user may want to steal from them; only their
    status score is lowered.
    """
    if not query.strip():
        return []

    overfetch = max(limit * _OVERFETCH_MULTIPLIER, limit)
    with CodeIndex() as idx:
        rows = idx.search(query, limit=overfetch, language_ext=language)

    if not rows:
        return []

    project_lookup = _build_project_lookup()
    now = time.time()

    scored: list[StealResult] = []
    for row in rows:
        project = _find_owning_project(row.repo_path, project_lookup)
        status_value = _effective_status(project)
        score = _score_block(
            updated_at=row.updated_at,
            status_value=status_value,
            now=now,
        )
        scored.append(
            StealResult(
                block=row,
                project_name=project.name if project else Path(row.repo_path).name,
                project_status=status_value,
                score=score,
            )
        )

    # Tie-break order: score DESC, path ASC, start_line ASC (stable).
    scored.sort(key=lambda r: (r.block.path, r.block.start_line))
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:limit]


# ----- helpers --------------------------------------------------------------


def _build_project_lookup() -> dict[str, Project]:
    """Map normalised project path → Project, for repo-owner resolution."""
    with Cache() as cache:
        projects = cache.list_projects()
    # Filter excluded/archived from the lookup? No — Steal should still
    # surface code from archived projects (user explicitly wants to reuse
    # their own dead code). But we apply the filters so the user sees
    # the *public* status value; archived status lowers the score
    # naturally via _STATUS_SCORES[ARCHIVED] = 0.0.
    _ = filter_excluded  # kept for future wiring; not used here on purpose
    _ = filter_archived  # same
    return {_normalise_path(str(p.path)): p for p in projects}


def _find_owning_project(
    repo_path: str,
    lookup: dict[str, Project],
) -> Project | None:
    """Return the Project whose path is the longest prefix of repo_path.

    block.repo_path is the absolute path passed to build_blocks_for_repo;
    in practice it equals project.path, but path separators and trailing
    slashes can drift. Longest-prefix match is the robust choice.
    """
    normalised = _normalise_path(repo_path)
    direct = lookup.get(normalised)
    if direct is not None:
        return direct
    best: Project | None = None
    best_len = -1
    for key, project in lookup.items():
        if normalised.startswith(key) and len(key) > best_len:
            best = project
            best_len = len(key)
    return best


def _normalise_path(path: str) -> str:
    return path.rstrip("/").rstrip("\\")


def _effective_status(project: Project | None) -> str | None:
    """Status override wins over cached status; None when unknown."""
    if project is None:
        return None
    override = get_override(str(project.path))
    if override is not None:
        return override.value
    md = project.metadata
    if md is None or md.status is None:
        return None
    return md.status.value


def _score_block(
    *,
    updated_at: float,
    status_value: str | None,
    now: float,
) -> float:
    age_days = max(0.0, (now - updated_at) / 86400.0)
    recency = math.exp(-age_days / _RECENCY_DECAY_DAYS)
    status_score = (
        _STATUS_SCORES.get(status_value, _STATUS_SCORE_DEFAULT)
        if status_value is not None
        else _STATUS_SCORE_DEFAULT
    )
    return recency * _RECENCY_WEIGHT + status_score * _STATUS_WEIGHT
