"""Shared helpers for the MCP server: serialization with size budgets,
project-context lookup, and search-hit shaping. Split out of
``mcp_server.py`` for the 400-line architecture target (ADR 0001
rule 3).
"""

from __future__ import annotations

import json
from pathlib import Path

from armillary.cache import Cache
from armillary.exclude_service import filter_excluded
from armillary.search import SearchHit
from armillary.status_override import filter_archived

# Hard limits to prevent MCP responses from exceeding token limits.
_MAX_RESULTS_CAP = 200
_PREVIEW_MAX_LEN = 120
_RESPONSE_MAX_CHARS = 20_000


def _project_context(project_name: str) -> dict[str, object]:
    """Fetch project metadata from cache for enriching search results."""
    with Cache() as cache:
        project = cache.get_project_by_name(project_name)
    if project is None:
        return {"path": None, "status": None, "description": None}
    md = project.metadata
    return {
        "path": str(project.path),
        "status": md.status.value if md and md.status else None,
        "description": md.readme_excerpt if md else None,
    }


def _hit_to_dict(hit: SearchHit, project_name: str) -> dict[str, object]:
    """Convert a SearchHit to a compact dict tagged by project name.

    Project-level metadata (path, status, description) is emitted once
    per project under the top-level ``projects`` map by the search
    tool — this keeps each hit small when the same repo contributes
    many matches.
    """
    preview = hit.preview
    if len(preview) > _PREVIEW_MAX_LEN:
        preview = preview[:_PREVIEW_MAX_LEN] + "…"
    return {
        "project": project_name,
        "file": str(hit.path),
        "line": hit.line,
        "preview": preview,
    }


def _serialize(items: list[dict[str, object]], dropped: int) -> str:
    """Serialize items + optional truncation marker to compact JSON."""
    payload: list[dict[str, object] | dict[str, int]] = list(items)
    if dropped > 0:
        payload.append({"_truncated": dropped})
    return json.dumps(payload, separators=(",", ":"), default=str)


def _safe_json(results: list[dict[str, object]], total: int, shown: int) -> str:
    """Serialize results to compact JSON, truncating if over char limit."""
    dropped = total - shown if shown < total else 0
    output = _serialize(results, dropped)
    if len(output) <= _RESPONSE_MAX_CHARS:
        return output
    while results:
        results.pop()
        dropped = total - len(results)
        output = _serialize(results, dropped)
        if len(output) <= _RESPONSE_MAX_CHARS:
            return output
    return _serialize(results, total)


def _serialize_search(
    projects: dict[str, dict[str, object]],
    hits: list[dict[str, object]],
    dropped: int,
) -> str:
    """Compact ``{projects, hits}`` payload — description emitted once."""
    # Drop projects whose hits were trimmed, so the map stays tight.
    referenced = {str(h["project"]) for h in hits}
    pruned = {k: v for k, v in projects.items() if k in referenced}
    payload: dict[str, object] = {"projects": pruned, "hits": hits}
    if dropped > 0:
        payload["truncated"] = dropped
    return json.dumps(payload, separators=(",", ":"), default=str)


def _safe_search_json(
    projects: dict[str, dict[str, object]],
    hits: list[dict[str, object]],
    total: int,
    shown: int,
) -> str:
    """Serialize ``armillary_search`` payload, trimming hits to fit budget."""
    dropped = total - shown if shown < total else 0
    output = _serialize_search(projects, hits, dropped)
    if len(output) <= _RESPONSE_MAX_CHARS:
        return output
    while hits:
        hits.pop()
        dropped = total - len(hits)
        output = _serialize_search(projects, hits, dropped)
        if len(output) <= _RESPONSE_MAX_CHARS:
            return output
    return _serialize_search(projects, hits, total)


def _clamp_max_results(max_results: int) -> int:
    """Keep public MCP tool limits within the supported inclusive range."""
    return max(1, min(max_results, _MAX_RESULTS_CAP))


def _get_project_roots() -> list[tuple[str, Path]]:
    """Return (name, path) for all cached projects, excluding hidden ones."""
    with Cache() as cache:
        projects = cache.list_projects()
    projects = filter_excluded(projects)
    projects = filter_archived(projects)
    return [(p.name, p.path) for p in projects]
