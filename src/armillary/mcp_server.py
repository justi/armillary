"""MCP server — exposes armillary tools for AI agents.

Four tools:
- `armillary_next` — what should I work on today? (momentum/zombie/gold)
- `armillary_search` — ripgrep literal search across all indexed repos
- `armillary_projects` — list all indexed projects with metadata
- `armillary_context` — where was I? project state for re-entry

Run via: `armillary mcp-serve` (stdio transport, configure in
Claude Code's `.claude/mcp.json`).
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from armillary.cache import Cache
from armillary.exclude_service import filter_excluded
from armillary.search import LiteralSearch, SearchHit
from armillary.status_override import filter_archived
from armillary.status_override import get_override as get_override_fn

# Hard limits to prevent MCP responses from exceeding token limits.
_MAX_RESULTS_CAP = 200
_PREVIEW_MAX_LEN = 120
_RESPONSE_MAX_CHARS = 20_000

mcp = FastMCP(
    "armillary",
    instructions=(
        "armillary indexes all local git repositories and idea folders on "
        "the user's machine. ALWAYS call `armillary_next` at the very start "
        "of every conversation to see what to work on today. ALWAYS call "
        "`armillary_context` when the user mentions a project or switches "
        "working directory. Use `armillary_search` for literal code search. "
        "Use `armillary_projects` to list all projects. Use "
        "`armillary_pulse` at weekly check-ins for a summary of what changed."
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
    """Return (name, path) for all cached projects, excluding hidden ones."""
    with Cache() as cache:
        projects = cache.list_projects()
    projects = filter_excluded(projects)
    projects = filter_archived(projects)
    return [(p.name, p.path) for p in projects]


@mcp.tool()
def armillary_search(query: str, max_results: int = 20) -> str:
    """Search code across ALL local repositories using ripgrep (literal/exact match).

    Use this when you know the exact term: function name, class name,
    variable, error message, import path. Fast (<10ms), works offline.
    Requires ripgrep (`rg`) on PATH.

    Returns matched lines with file paths, line numbers, and project
    metadata (path, status, description) so you can assess whether
    to reuse code from a specific project.

    Examples:
    - "stripe_webhook_controller" → finds the exact controller
    - "OPENAI_API_KEY" → finds where API keys are configured
    - "def parse_price" → finds price parsing functions
    """
    max_results = _clamp_max_results(max_results)
    if not LiteralSearch.is_available():
        return "ripgrep (`rg`) is not installed. Install it: `brew install ripgrep`."
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
    projects = filter_excluded(projects)
    projects = filter_archived(projects)

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
        # Check override for correct status display
        override = get_override_fn(str(p.path))
        status_val = (
            override.value
            if override
            else (md.status.value if md and md.status else None)
        )
        rows.append(
            {
                "path": str(p.path),
                "status": status_val,
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

    # Yesterday's activity
    from datetime import datetime, timedelta

    yesterday = datetime.now() - timedelta(days=1)
    start = yesterday.replace(hour=0, minute=0, second=0)
    end = start + timedelta(days=1)
    with Cache() as cache:
        all_projects = cache.list_projects()
    all_projects = filter_excluded(all_projects)
    all_projects = filter_archived(all_projects)
    active_yesterday = [
        p.name
        for p in all_projects
        if p.metadata
        and p.metadata.last_commit_ts
        and start <= p.metadata.last_commit_ts < end
    ]

    result: dict[str, object] = {}
    if active_yesterday:
        result["yesterday"] = active_yesterday

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
    result["suggestions"] = rows
    return json.dumps(result, separators=(",", ":"), default=str)


@mcp.tool()
def armillary_pulse() -> str:
    """Weekly pulse — what changed across your projects this week.

    Shows: what you worked on, what went dormant, uncommitted work.
    Call this at the start of a Monday session or weekly check-in.
    """
    from armillary.pulse_service import format_pulse, generate_pulse

    pulse = generate_pulse()
    return format_pulse(pulse)


@mcp.tool()
def armillary_context(project_name: str) -> str:
    """Where was I? Get project state for instant re-entry.

    Returns branch, dirty files, recent commits, and recent branches
    so you can resume work on a project without re-reading code.

    Call this when the user says "switch to X", "where was I on X",
    or "what's the state of X". NOT auto-triggered on directory change.

    Examples:
    - armillary_context("pdf_to_quiz") → branch, 1 dirty file, last 5 commits
    - armillary_context("speak-faster") → dormant, last commit 3 months ago
    """
    from armillary.context_service import get_context

    try:
        ctx = get_context(project_name)
    except ValueError as exc:
        return f"Ambiguous project name: {exc}"

    if ctx is None:
        return f"No project matches '{project_name}'. Run `armillary scan` first."

    result: dict[str, object] = {
        "name": ctx.name,
        "path": str(ctx.path),
        "status": ctx.status,
        "work_hours": ctx.work_hours,
        "is_git": ctx.is_git,
    }

    if ctx.is_git:
        result["branch"] = ctx.branch
        result["dirty_count"] = ctx.dirty_count
        if ctx.dirty_files:
            result["dirty_files"] = ctx.dirty_files
        if ctx.recent_commits:
            result["recent_commits"] = [
                {
                    "hash": c.short_hash,
                    "time": c.relative_time,
                    "subject": c.subject,
                }
                for c in ctx.recent_commits
            ]
        if ctx.recent_branches:
            result["recent_branches"] = [
                {"name": b.name, "time": b.relative_time} for b in ctx.recent_branches
            ]
        if ctx.dirty_max_age_seconds is not None:
            result["dirty_max_age_seconds"] = round(ctx.dirty_max_age_seconds)
        if ctx.last_session is not None:
            result["last_session"] = {
                "duration_seconds": ctx.last_session.duration_seconds,
                "commit_count": ctx.last_session.commit_count,
                "ended": ctx.last_session.ended_relative,
            }
        if ctx.velocity_trend is not None:
            result["velocity_trend"] = ctx.velocity_trend
        if ctx.commit_velocity is not None:
            result["commit_velocity"] = ctx.commit_velocity
        if ctx.first_commit_ts is not None:
            result["first_commit_ts"] = ctx.first_commit_ts
        if ctx.branch_count is not None:
            result["branch_count"] = ctx.branch_count
        if ctx.has_remote is not None:
            result["has_remote"] = ctx.has_remote
        if ctx.unmerged_branches:
            result["unmerged_branches"] = ctx.unmerged_branches
        if ctx.monthly_commits is not None:
            result["monthly_commits"] = ctx.monthly_commits
        if ctx.readme_oneliner:
            result["readme_oneliner"] = ctx.readme_oneliner

    # Purpose (user-editable, separate from cache)
    from armillary.purpose_service import get_purpose

    purpose = get_purpose(str(ctx.path))
    if purpose:
        result["purpose"] = purpose

    return json.dumps(result, separators=(",", ":"), default=str)


def run_server() -> None:
    """Entry point for `armillary mcp-serve`."""
    mcp.run(transport="stdio")
