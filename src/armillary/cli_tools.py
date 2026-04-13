"""CLI commands: search, open, install-claude-bridge, mcp-serve.

Extracted from cli.py to keep modules under 400 lines.
"""

from __future__ import annotations

import typer
from rich.console import Console

from armillary import exporter, launcher
from armillary.cache import Cache
from armillary.cli import app
from armillary.cli_helpers import _safe_load_config
from armillary.search import LiteralSearch


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

    matches = [p for p in all_projects if project_name.lower() in p.name.lower()]
    if not matches:
        typer.secho(
            f"No project in cache matches '{project_name}'. "
            "Run `armillary list` to see what is indexed.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    if len(matches) > 1:
        names = ", ".join(p.name for p in matches[:5])
        suffix = "" if len(matches) <= 5 else f" (+{len(matches) - 5} more)"
        typer.secho(
            f"'{project_name}' is ambiguous: {names}{suffix}. Be more specific.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    project = matches[0]
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


_CATEGORY_ICONS = {"momentum": "🔥", "zombie": "⚠️", "forgotten_gold": "💀"}


@app.command("next")
def next_command(
    skip: str | None = typer.Option(
        None,
        "--skip",
        help="Project name to dismiss from suggestions for 30 days.",
    ),
) -> None:
    """What should you work on today?

    Shows up to 3 suggestions based on your project activity:
    momentum (keep going), zombies (kill or ship), and forgotten
    gold (high-effort dormant projects worth revisiting with AI).

    Use --skip <name> to dismiss a project for 30 days.
    """
    from armillary.cache import Cache
    from armillary.next_service import get_suggestions, skip_project

    if skip:
        with Cache() as cache:
            matches = [
                p for p in cache.list_projects() if skip.lower() in p.name.lower()
            ]
        if not matches:
            typer.secho(f"No project matches '{skip}'.", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
        if len(matches) > 1:
            names = ", ".join(p.name for p in matches[:5])
            suffix = f" (+{len(matches) - 5} more)" if len(matches) > 5 else ""
            typer.secho(
                f"'{skip}' is ambiguous: {names}{suffix}. Be more specific.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)
        skip_project(str(matches[0].path))
        typer.secho(f"Skipped {matches[0].name} for 30 days.", fg=typer.colors.CYAN)
        return

    suggestions = get_suggestions()

    if not suggestions:
        typer.secho(
            "No suggestions — cache is empty or everything is skipped. "
            "Run `armillary scan` first.",
            fg=typer.colors.YELLOW,
        )
        return

    from armillary.cli_helpers import _shorten_home

    console = Console()
    for s in suggestions:
        icon = _CATEGORY_ICONS.get(s.category, "•")
        short_path = _shorten_home(s.project.path)
        console.print(
            f"\n{icon} [bold]{s.project.name}[/bold]  [dim]{short_path}[/dim]"
        )
        console.print(f"  {s.reason}")
        console.print(f"  [dim]→ cd {short_path}[/dim]")
        if s.category == "forgotten_gold":
            console.print(f"  [dim]→ armillary next --skip {s.project.name}[/dim]")


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
