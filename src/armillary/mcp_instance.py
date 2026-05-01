"""FastMCP server instance, isolated in its own module so tool modules
can import it without cycling through the public ``mcp_server`` entry
point. The instance is created at import time; tools register against
it via ``@mcp.tool()`` in ``mcp_tools``.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

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
