"""Ranked cross-repo code retrieval (ADR 0027 — Steal).

Given a query, returns a ranked list of code blocks drawn from every
indexed repo. Ranking combines three signals: BM25 relevance (from
the FTS index), recency, and project status — multiplied by a
content-type weight that demotes docs and data files. BM25 is the
dominant signal: without it, actively-edited but off-topic code
saturated the results.
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

_OVERFETCH_MULTIPLIER = 20
# Cap per repo in the overfetch pool so one noisy repo (many hits in
# comments/fixtures) cannot dominate the final ranking slice.
_MAX_PER_REPO_IN_OVERFETCH = 3

# ADR 0027 ranking — 3 signals, BM25 relevance dominant.
# Earlier design used only recency + status; observed in the wild:
# actively-edited repos dominated unrelated queries because recency
# saturated near 1.0 and the match-quality signal was missing entirely.
# BM25 rank position (from CodeIndex.search) answers "does this block
# actually match the query?" — without it, ranking is blind.
_RELEVANCE_WEIGHT = 0.6
_RECENCY_WEIGHT = 0.25
_STATUS_WEIGHT = 0.15
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

# Content-type multiplier (v1 correction — see ADR 0027).
# Observed in the wild: BM25 promotes translation bundles (.pot),
# docs (.md), and configs to the top when they happen to contain the
# query string. This is a multiplicative noise filter, not a third
# ranking dimension — the 2-signal mental model stays intact.
_CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        "py",
        "rb",
        "ts",
        "tsx",
        "js",
        "jsx",
        "mjs",
        "cjs",
        "go",
        "rs",
        "java",
        "kt",
        "swift",
        "m",
        "mm",
        "c",
        "cc",
        "cpp",
        "cxx",
        "h",
        "hh",
        "hpp",
        "cs",
        "fs",
        "vb",
        "php",
        "ex",
        "exs",
        "erl",
        "elm",
        "clj",
        "cljs",
        "scala",
        "sc",
        "sh",
        "bash",
        "zsh",
        "fish",
        "ps1",
        "sql",
        "pl",
        "pm",
        "lua",
        "r",
        "jl",
        "nim",
        "zig",
        "dart",
        "hs",
        "ml",
        "mli",
        "coffee",
        "tcl",
    }
)
_DOC_EXTENSIONS: frozenset[str] = frozenset(
    {"md", "rst", "txt", "adoc", "org", "tex", "pot", "po", "html", "htm"}
)
_DATA_EXTENSIONS: frozenset[str] = frozenset(
    {
        "json",
        "yaml",
        "yml",
        "toml",
        "ini",
        "cfg",
        "conf",
        "env",
        "xml",
        "csv",
        "tsv",
        "log",
    }
)
_CONTENT_MULTIPLIER_CODE = 1.0
_CONTENT_MULTIPLIER_UNKNOWN = 0.8
_CONTENT_MULTIPLIER_DOC = 0.55
_CONTENT_MULTIPLIER_DATA = 0.4


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

    # Diversify the re-ranking pool: keep at most N hits per repo so a
    # single repo with many incidental matches (docstrings, fixtures)
    # cannot crowd out other repos.
    per_repo: dict[str, int] = {}
    diversified = []
    for row in rows:
        count = per_repo.get(row.repo_path, 0)
        if count >= _MAX_PER_REPO_IN_OVERFETCH:
            continue
        per_repo[row.repo_path] = count + 1
        diversified.append(row)
    rows = diversified

    project_lookup = _build_project_lookup()
    now = time.time()

    pool_size = max(1, len(rows))
    scored: list[StealResult] = []
    for position, row in enumerate(rows):
        project = _find_owning_project(row.repo_path, project_lookup)
        status_value = _effective_status(project)
        score = _score_block(
            updated_at=row.updated_at,
            status_value=status_value,
            language_ext=row.language_ext,
            bm25_position=position,
            pool_size=pool_size,
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
    language_ext: str,
    bm25_position: int,
    pool_size: int,
    now: float,
) -> float:
    # BM25 position inside the diversified pool — 0 is the best match,
    # pool_size-1 is the worst. We turn it into a relevance score in
    # [0, 1] where 1.0 = top hit. This is the dominant signal.
    relevance = 1.0 - (bm25_position / pool_size)
    age_days = max(0.0, (now - updated_at) / 86400.0)
    recency = math.exp(-age_days / _RECENCY_DECAY_DAYS)
    status_score = (
        _STATUS_SCORES.get(status_value, _STATUS_SCORE_DEFAULT)
        if status_value is not None
        else _STATUS_SCORE_DEFAULT
    )
    base = (
        relevance * _RELEVANCE_WEIGHT
        + recency * _RECENCY_WEIGHT
        + status_score * _STATUS_WEIGHT
    )
    return base * _content_multiplier(language_ext)


def _content_multiplier(language_ext: str) -> float:
    ext = language_ext.lower()
    if ext in _CODE_EXTENSIONS:
        return _CONTENT_MULTIPLIER_CODE
    if ext in _DOC_EXTENSIONS:
        return _CONTENT_MULTIPLIER_DOC
    if ext in _DATA_EXTENSIONS:
        return _CONTENT_MULTIPLIER_DATA
    return _CONTENT_MULTIPLIER_UNKNOWN
