"""MCP server — exposes armillary search as tools for AI agents.

Two tools:
- `armillary_search` — ripgrep literal search across all indexed repos
- `armillary_semantic` — Khoj conceptual search (optional, requires Docker)

Both return project metadata with every hit so the AI agent has full
context for reuse decisions.

Run via: `armillary mcp-serve` (stdio transport, configure in
Claude Code's `.claude/mcp.json`).
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from armillary.cache import Cache
from armillary.config import ConfigError, load_config
from armillary.search import KhojConfig, KhojSearch, LiteralSearch, SearchHit

# Hard limits to prevent MCP responses from exceeding token limits.
_MAX_RESULTS_CAP = 200
_PREVIEW_MAX_LEN = 120
_RESPONSE_MAX_CHARS = 20_000

mcp = FastMCP(
    "armillary",
    instructions=(
        "armillary indexes all local git repositories and idea folders on "
        "the user's machine. Use `armillary_search` for literal/exact code "
        "search (fast, always available). Use `armillary_semantic` for "
        "conceptual queries like 'authentication patterns' or 'payment "
        "handling approaches' (requires Khoj running). Use `armillary_projects` "
        "to list all indexed projects with metadata."
    ),
)


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


def _hit_to_dict(hit: SearchHit, project_meta: dict[str, object]) -> dict[str, object]:
    """Convert a SearchHit + project metadata to a flat dict."""
    preview = hit.preview
    if len(preview) > _PREVIEW_MAX_LEN:
        preview = preview[:_PREVIEW_MAX_LEN] + "…"
    return {
        **project_meta,
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


def _clamp_max_results(max_results: int) -> int:
    """Keep public MCP tool limits within the supported inclusive range."""
    return max(1, min(max_results, _MAX_RESULTS_CAP))


def _get_project_roots() -> list[tuple[str, Path]]:
    """Return (name, path) for all cached projects."""
    with Cache() as cache:
        projects = cache.list_projects()
    return [(p.name, p.path) for p in projects]


@mcp.tool()
def armillary_search(query: str, max_results: int = 20) -> str:
    """Search code across ALL local repositories using ripgrep (literal/exact match).

    Use this when you know the exact term: function name, class name,
    variable, error message, import path. Fast (<10ms), always available,
    works offline.

    Returns matched lines with file paths, line numbers, and project
    metadata (path, status, description) so you can assess whether
    to reuse code from a specific project.

    Examples:
    - "stripe_webhook_controller" → finds the exact controller
    - "OPENAI_API_KEY" → finds where API keys are configured
    - "def parse_price" → finds price parsing functions
    """
    max_results = _clamp_max_results(max_results)
    backend = LiteralSearch()
    results: list[dict[str, object]] = []
    project_roots = _get_project_roots()
    total_hits = 0

    for name, root in project_roots:
        if len(results) >= max_results:
            break
        remaining = max_results - len(results)
        try:
            hits = backend.search(query, root=root, max_results=remaining)
        except Exception:  # noqa: BLE001
            continue
        total_hits += len(hits)
        if hits:
            meta = _project_context(name)
            results.extend(_hit_to_dict(h, meta) for h in hits)

    if not results:
        return f"No matches for '{query}' across {len(project_roots)} projects."

    return _safe_json(results[:max_results], total_hits, len(results[:max_results]))


@mcp.tool()
def armillary_semantic(query: str, max_results: int = 10) -> str:
    """Search code across ALL local repositories using semantic/conceptual search.

    Use this when searching by CONCEPT, not exact text: "authentication
    patterns", "payment handling", "scraping approaches", "CSV import
    logic". Understands that devise, bcrypt, JWT are all "authentication".

    Requires Khoj running locally (armillary install-khoj + start-khoj).
    Falls back to ripgrep if Khoj is unavailable.

    Returns matched snippets with project metadata for reuse decisions.

    Examples:
    - "authentication patterns" → finds devise, bcrypt, JWT, has_secure_password
    - "web scraping approaches" → finds BeautifulSoup, Nokogiri, Selenium
    - "how to handle file uploads" → finds ActiveStorage, CarrierWave, Shrine
    """
    max_results = _clamp_max_results(max_results)
    try:
        cfg = load_config()
    except ConfigError:
        cfg = None

    if cfg and cfg.khoj.enabled:
        backend = KhojSearch(
            config=KhojConfig(
                api_url=cfg.khoj.api_url,
                api_key=cfg.khoj.api_key,
                timeout_seconds=cfg.khoj.timeout_seconds,
            ),
            fallback=LiteralSearch(),
        )
    else:
        backend = LiteralSearch()

    results: list[dict[str, object]] = []
    project_roots = _get_project_roots()
    total_hits = 0

    for name, root in project_roots:
        if len(results) >= max_results:
            break
        remaining = max_results - len(results)
        try:
            hits = backend.search(query, root=root, max_results=remaining)
        except Exception:  # noqa: BLE001
            continue
        total_hits += len(hits)
        if hits:
            meta = _project_context(name)
            results.extend(_hit_to_dict(h, meta) for h in hits)

    if not results:
        return (
            f"No semantic matches for '{query}' across {len(project_roots)} projects."
        )

    return _safe_json(results[:max_results], total_hits, len(results[:max_results]))


@mcp.tool()
def armillary_projects(status_filter: str | None = None) -> str:
    """List all indexed projects with path, status, and description.

    Use this to find projects by concept or status. The agent can
    then enter the project directory for full details (git log, etc.).

    Optional: filter by status (ACTIVE, PAUSED, DORMANT, IDEA, IN_PROGRESS).

    Examples:
    - armillary_projects() → all projects
    - armillary_projects(status_filter="ACTIVE") → only active projects
    - armillary_projects(status_filter="DORMANT") → forgotten projects
    """
    with Cache() as cache:
        projects = cache.list_projects()

    if status_filter:
        status_upper = status_filter.upper()
        projects = [
            p
            for p in projects
            if p.metadata
            and p.metadata.status
            and p.metadata.status.value == status_upper
        ]

    rows = []
    for p in projects:
        md = p.metadata
        rows.append(
            {
                "path": str(p.path),
                "status": md.status.value if md and md.status else None,
                "description": md.readme_excerpt if md else None,
            }
        )

    return _safe_json(rows, len(rows), len(rows))


@mcp.tool()
def armillary_next() -> str:
    """What should I work on today?

    Returns up to 3 project suggestions based on activity patterns:
    - **momentum** — active project with recent commits, keep going
    - **zombie** — marked active but no commit in >7 days, kill or ship
    - **forgotten_gold** — dormant/paused project with >50h invested,
      could be finished with AI tools

    Call this at the start of a coding session to get context about
    the user's project portfolio and recommend where to focus.
    """
    from armillary.next_service import get_suggestions

    suggestions = get_suggestions()
    if not suggestions:
        return "No suggestions — cache is empty or all projects are skipped."

    rows = []
    for s in suggestions:
        rows.append(
            {
                "project": s.project.name,
                "path": str(s.project.path),
                "category": s.category,
                "reason": s.reason,
            }
        )
    return _safe_json(rows, len(rows), len(rows))


def run_server() -> None:
    """Entry point for `armillary mcp-serve`."""
    mcp.run(transport="stdio")
