"""MCP server entrypoint for `armillary mcp-serve`.

Seven tools — see ``mcp_tools`` for implementations:

- ``armillary_next`` — what should I work on today? (momentum/zombie/gold)
- ``armillary_search`` — ripgrep literal search across all indexed repos
- ``armillary_projects`` — list all indexed projects with metadata
- ``armillary_context`` — where was I? project state for re-entry
- ``armillary_steal`` — find reusable 40-line blocks from prior repos
- ``armillary_pulse`` — weekly pulse over the portfolio
- ``armillary_revive`` — project brief plus up to 3 quoted blocks
  from other repos

This module is the public seam: it owns ``run_server`` and re-exports
the helpers + tool callables that ``tests/test_mcp_server.py`` imports
directly. The FastMCP instance lives in ``mcp_instance`` so the tool
module can register against it without circular imports.

Run via: `armillary mcp-serve` (stdio transport, configure in
Claude Code's `.claude/mcp.json`).
"""

from __future__ import annotations

# Importing mcp_tools registers every @mcp.tool() against the FastMCP
# instance. Without this side-effect import the server would start with
# zero tools on the wire.
from armillary import mcp_tools as _mcp_tools  # noqa: F401
from armillary.mcp_helpers import (
    _PREVIEW_MAX_LEN,
    _RESPONSE_MAX_CHARS,
    _clamp_max_results,
    _get_project_roots,
    _hit_to_dict,
    _project_context,
    _safe_json,
    _safe_search_json,
    _serialize,
    _serialize_search,
)
from armillary.mcp_instance import mcp
from armillary.mcp_tools import (
    armillary_context,
    armillary_next,
    armillary_projects,
    armillary_pulse,
    armillary_revive,
    armillary_search,
    armillary_steal,
)

__all__ = [
    "mcp",
    "run_server",
    # Tools (callable for tests + direct invocation).
    "armillary_context",
    "armillary_next",
    "armillary_projects",
    "armillary_pulse",
    "armillary_revive",
    "armillary_search",
    "armillary_steal",
    # Helpers re-exported for tests.
    "_PREVIEW_MAX_LEN",
    "_RESPONSE_MAX_CHARS",
    "_clamp_max_results",
    "_get_project_roots",
    "_hit_to_dict",
    "_project_context",
    "_safe_json",
    "_safe_search_json",
    "_serialize",
    "_serialize_search",
]


def run_server() -> None:
    """Entry point for `armillary mcp-serve`."""
    mcp.run(transport="stdio")
