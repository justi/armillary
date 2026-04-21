"""CLI commands: search, open, install-claude-bridge, mcp-serve.

Extracted from cli.py to keep modules under 400 lines.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from armillary import exporter, launcher
from armillary.cache import Cache
from armillary.cli import app
from armillary.cli_helpers import _resolve_project_or_report, _safe_load_config
from armillary.exclude_service import filter_excluded
from armillary.search import LiteralSearch
from armillary.status_override import filter_archived


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
    project_filter: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Restrict the search to projects matching this substring.",
    ),
    max_results: int = typer.Option(
        50,
        "--max",
        "-n",
        min=1,
        max=500,
        help="Maximum number of hits to print.",
    ),
) -> None:
    """Search across indexed project files using ripgrep.

    Runs `rg <query>` over every cached project (or a subset filtered
    by `--project`).
    """
    cfg = _safe_load_config()
    if cfg is None:
        raise typer.Exit(2)

    with Cache() as cache:
        all_projects = cache.list_projects()
    all_projects = filter_excluded(all_projects)
    all_projects = filter_archived(all_projects)

    if project_filter:
        needle = project_filter.lower()
        projects = [p for p in all_projects if needle in p.name.lower()]
    else:
        projects = all_projects

    if not projects:
        typer.secho(
            "No projects in cache. Run `armillary scan` first.",
            fg=typer.colors.YELLOW,
        )
        return

    if not LiteralSearch.is_available():
        typer.secho(
            "ripgrep (`rg`) is not on PATH. "
            "Install it (`brew install ripgrep`) to use search.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    backend = LiteralSearch()

    console = Console()
    total_hits = 0
    errors = 0
    for project in projects:
        remaining = max_results - total_hits
        if remaining <= 0:
            break
        try:
            hits = backend.search(query, root=project.path, max_results=remaining)
        except Exception as exc:  # noqa: BLE001 — permission errors, broken files, etc.
            typer.secho(
                f"Search failed on {project.name}: {exc}",
                fg=typer.colors.RED,
                err=True,
            )
            errors += 1
            continue
        if not hits:
            continue
        console.print(
            f"[bold cyan]{project.name}[/bold cyan]  [dim]{project.path}[/dim]"
        )
        for hit in hits[:max_results]:
            location = (
                f"{hit.path}:{hit.line}" if hit.line is not None else str(hit.path)
            )
            console.print(f"  [magenta]{location}[/magenta]")
            console.print(f"    {hit.preview}")
            total_hits += 1
            if total_hits >= max_results:
                break
        if total_hits >= max_results:
            console.print(f"[dim](truncated at --max {max_results})[/dim]")
            break

    if total_hits == 0:
        if errors == len(projects):
            typer.secho("Search failed on all projects.", fg=typer.colors.RED)
            raise typer.Exit(2)
        typer.secho(f"No matches for '{query}'.", fg=typer.colors.YELLOW)


@app.command("open")
def open_project(
    project_name: str = typer.Argument(
        ...,
        help="Project name (as shown by `armillary list`).",
    ),
    target: str = typer.Option(
        "cursor",
        "--target",
        "-t",
        help="Launcher id from your config (cursor, vscode, claude-code, ...).",
    ),
) -> None:
    """Open a project in the configured launcher."""
    cfg = _safe_load_config()
    if cfg is None:
        raise typer.Exit(2)

    with Cache() as cache:
        all_projects = cache.list_projects()

    project = _resolve_project_or_report(
        all_projects,
        project_name,
        missing_message=(
            "No project in cache matches '{name}'. "
            "Run `armillary list` to see what is indexed."
        ),
        ambiguous_message="'{name}' is ambiguous: {matches}. Be more specific.",
    )
    if project is None:
        raise typer.Exit(2)
    result = launcher.launch(project, target, launchers=cfg.launchers)

    if not result.ok:
        typer.secho(result.error or "Launch failed.", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)

    typer.secho(
        f"Opened {project.name} in {target}.",
        fg=typer.colors.GREEN,
    )


@app.command("install-claude-bridge")
def install_claude_bridge(
    with_claude_md: bool = typer.Option(
        False,
        "--with-claude-md",
        help=(
            "Also append an `@armillary/repos-index.md` import line to "
            "~/.claude/CLAUDE.md so every Claude Code session in your home "
            "automatically loads the project table. Idempotent — safe to "
            "re-run."
        ),
    ),
) -> None:
    """Write the repos-index for Claude Code at `~/.claude/armillary/repos-index.md`."""
    bridge_path, written, appended = exporter.install_claude_bridge(
        with_claude_md=with_claude_md,
    )

    if written == 0:
        typer.secho(
            f"Wrote {bridge_path} but the cache is empty. "
            "Run `armillary scan` first, then re-run this command.",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(
            f"Wrote {written} project(s) to {bridge_path}",
            fg=typer.colors.GREEN,
        )

    if with_claude_md:
        claude_md = bridge_path.parent.parent / "CLAUDE.md"
        if appended:
            typer.secho(
                f"  ✓ Appended @armillary/repos-index.md import to {claude_md}",
                fg=typer.colors.GREEN,
            )
        else:
            typer.secho(
                f"  · {claude_md} already imports armillary — left untouched.",
                fg=typer.colors.CYAN,
            )


@app.command("steal")
def steal_command(
    query: str = typer.Argument(..., help="What to steal (e.g. 'stripe webhook')."),
    limit: int = typer.Option(
        5, "--limit", "-n", min=1, max=20, help="How many blocks to return."
    ),
    language: str | None = typer.Option(
        None,
        "--lang",
        "-l",
        help="File-extension filter (py, rb, ts, go, ...).",
    ),
    copy: int | None = typer.Option(
        None,
        "--copy",
        "-c",
        help="Copy the Nth result (1-based) to clipboard via pbcopy.",
    ),
    vote: int | None = typer.Option(
        None,
        "--vote",
        help="After copy, record a thumbs vote for the query (1 = up, -1 = down).",
    ),
) -> None:
    """Cross-repo ranked code search — steal from your own projects (ADR 0027)."""
    import subprocess as _sp

    from armillary.feedback_service import record_vote
    from armillary.steal_service import steal
    from armillary.transition_service import record_steal

    if not query.strip():
        typer.secho("Query is empty.", fg=typer.colors.YELLOW)
        raise typer.Exit(2)

    try:
        results = steal(query, limit=limit, language=language)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    if not results:
        typer.secho(
            f"No reusable blocks for '{query}'. Run `armillary scan` first?",
            fg=typer.colors.YELLOW,
        )
        return

    console = Console()
    for i, r in enumerate(results, start=1):
        header = (
            f"[bold cyan]{i}. {r.project_name}[/bold cyan] "
            f"[dim]({r.project_status or 'unknown'}, score={r.score:.2f})[/dim]"
        )
        console.print(header)
        symbol_part = f"[magenta]{r.block.symbol}[/magenta]  " if r.block.symbol else ""
        console.print(
            f"   {symbol_part}[dim]{r.block.path}:"
            f"{r.block.start_line}-{r.block.end_line}[/dim]"
        )
        console.print(f"[dim]{'─' * 60}[/dim]")
        console.print(r.block.content)
        console.print()

    if copy is None:
        return

    if copy < 1 or copy > len(results):
        typer.secho(
            f"--copy {copy} is out of range (1..{len(results)}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    chosen = results[copy - 1]
    content_bytes = chosen.block.content.encode("utf-8")
    try:
        _sp.run(  # noqa: S603 — fixed argv, no shell
            ["pbcopy"],
            input=content_bytes,
            check=True,
            timeout=5,
        )
        typer.secho(
            f"Copied block from {chosen.project_name} to clipboard.",
            fg=typer.colors.GREEN,
        )
    except (FileNotFoundError, _sp.CalledProcessError, _sp.TimeoutExpired):
        typer.secho(
            "pbcopy unavailable — dumping the block content:",
            fg=typer.colors.YELLOW,
            err=True,
        )
        console.print(chosen.block.content)

    # Journal + feedback — only after a successful "take".
    import contextlib as _ctx

    with _ctx.suppress(Exception):
        record_steal(
            query=query,
            src_path=chosen.block.path,
            dst_path=str(Path.cwd()),
        )

    if vote is not None:
        record_vote(query, chosen.block.path, chosen.block.start_line, vote)


@app.command("mcp-serve")
def mcp_serve() -> None:
    """Start the MCP server (stdio transport) for AI coding agents.

    Exposes three tools that Claude Code / Cursor / Codex can call:

    - armillary_next — what should I work on today? (momentum/zombie/gold)
    - armillary_search — ripgrep literal search across all repos
    - armillary_projects — list all indexed projects with metadata

    Configure in Claude Code's `.claude/mcp.json`:

        { "armillary": { "command": "armillary", "args": ["mcp-serve"] } }
    """
    from armillary.mcp_server import run_server

    run_server()


# Sibling CLI modules — importing them here registers their @app.command
# decorators on the shared Typer app without requiring cli.py to know
# about each one individually.
import armillary.cli_context  # noqa: F401, E402
import armillary.cli_lifecycle  # noqa: F401, E402
import armillary.cli_next  # noqa: F401, E402
import armillary.cli_share  # noqa: F401, E402

# Re-export for `cli.py`, which does ``from armillary.cli_tools import
# next_command`` inside the bare-invocation path.
from armillary.cli_next import next_command  # noqa: F401, E402
