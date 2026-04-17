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


@app.command("exclude")
def exclude_command(
    names: list[str] = typer.Argument(..., help="Project names to exclude."),
) -> None:
    """Exclude projects from all armillary output.

    Excluded projects won't appear in next, search, context, overview,
    or MCP tools. Use `armillary include` to restore.
    """
    from armillary.exclude_service import exclude_project

    with Cache() as cache:
        all_projects = cache.list_projects()

    for name in names:
        matches = [p for p in all_projects if name.lower() in p.name.lower()]
        if not matches:
            typer.secho(f"No project matches '{name}'.", fg=typer.colors.YELLOW)
            continue
        if len(matches) > 1:
            exact = [p for p in matches if p.name.lower() == name.lower()]
            if len(exact) == 1:
                matches = exact
            else:
                match_names = ", ".join(p.name for p in matches[:5])
                typer.secho(
                    f"'{name}' is ambiguous: {match_names}. Be more specific.",
                    fg=typer.colors.RED,
                    err=True,
                )
                continue
        project = matches[0]
        exclude_project(str(project.path))
        typer.secho(f"Excluded {project.name}", fg=typer.colors.CYAN)

    typer.echo("Use `armillary include <name>` to restore.")


@app.command("include")
def include_command(
    names: list[str] = typer.Argument(..., help="Project names to restore."),
) -> None:
    """Restore excluded projects back to armillary output."""
    from armillary.exclude_service import include_project

    with Cache() as cache:
        all_projects = cache.list_projects()

    for name in names:
        matches = [p for p in all_projects if name.lower() in p.name.lower()]
        if not matches:
            typer.secho(f"No project matches '{name}'.", fg=typer.colors.YELLOW)
            continue
        if len(matches) > 1:
            exact = [p for p in matches if p.name.lower() == name.lower()]
            if len(exact) == 1:
                matches = exact
            else:
                match_names = ", ".join(p.name for p in matches[:5])
                typer.secho(
                    f"'{name}' is ambiguous: {match_names}.",
                    fg=typer.colors.RED,
                    err=True,
                )
                continue
        project = matches[0]
        include_project(str(project.path))
        typer.secho(f"Restored {project.name}", fg=typer.colors.GREEN)


@app.command("archive")
def archive_command(
    names: list[str] = typer.Argument(..., help="Project name(s) to archive."),
    reason: str | None = typer.Option(
        None,
        "--reason",
        "-r",
        help="Why you're archiving (e.g. 'no traction', 'finished').",
    ),
) -> None:
    """Archive a project — mark it as consciously done.

    Archived projects are hidden from next, search, and overview
    but their code stays on disk. Use `armillary activate` to restore.
    """
    from armillary.models import Status
    from armillary.purpose_service import set_archive_reason
    from armillary.status_override import get_override, set_override

    with Cache() as cache:
        projects = cache.list_projects()

    for name in names:
        matches = [p for p in projects if name.lower() in p.name.lower()]
        if not matches:
            typer.secho(f"No project matches '{name}'.", fg=typer.colors.RED, err=True)
            continue
        if len(matches) > 1:
            exact = [p for p in matches if p.name.lower() == name.lower()]
            if len(exact) == 1:
                matches = exact
            else:
                match_names = ", ".join(p.name for p in matches[:5])
                typer.secho(
                    f"'{name}' is ambiguous: {match_names}.",
                    fg=typer.colors.RED,
                    err=True,
                )
                continue
        project = matches[0]
        existing = get_override(str(project.path))
        if existing == Status.ARCHIVED:
            typer.secho(f"{project.name} is already archived.", fg=typer.colors.YELLOW)
            continue
        set_override(str(project.path), Status.ARCHIVED)
        if reason:
            set_archive_reason(str(project.path), reason)
        msg = (
            f"Archived {project.name}. "
            f"Use `armillary activate {project.name}` to restore."
        )
        typer.secho(msg, fg=typer.colors.CYAN)


@app.command("activate")
def activate_command(
    names: list[str] = typer.Argument(..., help="Project name(s) to activate."),
) -> None:
    """Restore a project from archived — return to automatic status.

    Clears any manual status override so the project's status is
    determined by git activity again.
    """
    from armillary.status_override import clear_override, get_override

    with Cache() as cache:
        projects = cache.list_projects()

    for name in names:
        matches = [p for p in projects if name.lower() in p.name.lower()]
        if not matches:
            typer.secho(f"No project matches '{name}'.", fg=typer.colors.RED, err=True)
            continue
        if len(matches) > 1:
            exact = [p for p in matches if p.name.lower() == name.lower()]
            if len(exact) == 1:
                matches = exact
            else:
                match_names = ", ".join(p.name for p in matches[:5])
                typer.secho(
                    f"'{name}' is ambiguous: {match_names}.",
                    fg=typer.colors.RED,
                    err=True,
                )
                continue
        project = matches[0]
        if get_override(str(project.path)) is None:
            typer.secho(
                f"{project.name} has no manual override — already automatic.",
                fg=typer.colors.YELLOW,
            )
            continue
        clear_override(str(project.path))
        typer.secho(
            f"Activated {project.name} — status is now automatic.",
            fg=typer.colors.GREEN,
        )


@app.command("purpose")
def purpose_command(
    project_name: str = typer.Argument(..., help="Project name (substring match)."),
    text: str | None = typer.Argument(None, help="Purpose text. Omit to show current."),
    clear: bool = typer.Option(False, "--clear", help="Remove the purpose."),
) -> None:
    """Set or show a project's purpose — why it exists, in one sentence."""
    from armillary.cache import Cache
    from armillary.purpose_service import clear_purpose, get_purpose, set_purpose

    with Cache() as cache:
        projects = cache.list_projects()
    matches = [p for p in projects if project_name.lower() in p.name.lower()]
    if not matches:
        typer.secho(
            f"No project matches '{project_name}'.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(2)
    if len(matches) > 1:
        exact = [p for p in matches if p.name.lower() == project_name.lower()]
        if len(exact) == 1:
            matches = exact
        else:
            names = ", ".join(p.name for p in matches[:5])
            typer.secho(f"Ambiguous: {names}.", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)

    project = matches[0]
    path_str = str(project.path)

    if clear:
        clear_purpose(path_str)
        typer.secho(f"Cleared purpose for {project.name}.", fg=typer.colors.CYAN)
        return

    if text:
        set_purpose(path_str, text)
        typer.secho(f"Purpose for {project.name}: {text}", fg=typer.colors.GREEN)
        return

    current = get_purpose(path_str)
    if current:
        typer.echo(f"{project.name}: {current}")
    else:
        typer.secho(
            f"No purpose set for {project.name}. "
            f'Use: armillary purpose {project.name} "your purpose here"',
            fg=typer.colors.YELLOW,
        )


@app.command("talked")
def talked_command(
    project_name: str = typer.Argument(..., help="Project name."),
    date: str | None = typer.Argument(None, help="Date (YYYY-MM-DD). Omit for today."),
) -> None:
    """Record when you last talked to a user about this project."""
    from datetime import date as date_type

    from armillary.purpose_service import (
        set_last_conversation,
    )

    with Cache() as cache:
        projects = cache.list_projects()
    matches = [p for p in projects if project_name.lower() in p.name.lower()]
    if not matches:
        typer.secho(
            f"No project matches '{project_name}'.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    if len(matches) > 1:
        exact = [p for p in matches if p.name.lower() == project_name.lower()]
        if len(exact) == 1:
            matches = exact
        else:
            names = ", ".join(p.name for p in matches[:5])
            typer.secho(f"Ambiguous: {names}.", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)

    project = matches[0]
    date_str = date or date_type.today().isoformat()
    set_last_conversation(str(project.path), date_str)
    typer.secho(
        f"Recorded: last talked to user about {project.name} on {date_str}",
        fg=typer.colors.GREEN,
    )


@app.command("revenue")
def revenue_command(
    project_name: str = typer.Argument(..., help="Project name."),
    amount: int | None = typer.Argument(None, help="Monthly revenue in USD."),
) -> None:
    """Set or show monthly revenue (MRR) for a project."""
    from armillary.purpose_service import get_revenue, set_revenue

    with Cache() as cache:
        projects = cache.list_projects()
    matches = [p for p in projects if project_name.lower() in p.name.lower()]
    if not matches:
        typer.secho(
            f"No project matches '{project_name}'.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    if len(matches) > 1:
        exact = [p for p in matches if p.name.lower() == project_name.lower()]
        if len(exact) == 1:
            matches = exact
        else:
            names = ", ".join(p.name for p in matches[:5])
            typer.secho(f"Ambiguous: {names}.", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)

    project = matches[0]
    if amount is not None:
        set_revenue(str(project.path), amount)
        typer.secho(f"{project.name}: ${amount}/mo", fg=typer.colors.GREEN)
    else:
        current = get_revenue(str(project.path))
        if current is not None:
            typer.echo(f"{project.name}: ${current}/mo")
        else:
            typer.secho(f"No revenue set for {project.name}.", fg=typer.colors.YELLOW)


def _format_age(seconds: float) -> str:
    """Human-readable age from seconds (e.g. '3 days', '2h')."""
    if seconds < 3600:
        return f"{seconds / 60:.0f}min"
    if seconds < 86400:
        return f"{seconds / 3600:.0f}h"
    days = seconds / 86400
    if days < 30:
        return f"{days:.0f}d"
    return f"{days / 30:.0f}mo"


_SPARK_CHARS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def _sparkline(values: list[int]) -> str:
    """Render a list of ints as a unicode sparkline."""
    if not values:
        return ""
    peak = max(values) or 1
    return "".join(_SPARK_CHARS[min(int(v / peak * 7), 7)] for v in values)


@app.command("context")
def context_command(
    project_name: str = typer.Argument(..., help="Project name (substring match)."),
) -> None:
    """Where was I? Show project state for instant re-entry.

    Displays branch, dirty files, recent commits, and recent branches
    so you can resume work without re-reading code. Sub-second response.
    """
    from armillary.cli_helpers import _shorten_home
    from armillary.context_service import get_context

    try:
        ctx = get_context(project_name)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    if ctx is None:
        typer.secho(
            f"No project matches '{project_name}'. Run `armillary scan` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    console = Console()
    status_str = ctx.status or "?"
    hours_str = f" — {ctx.work_hours:.1f} h" if ctx.work_hours is not None else ""
    short_path = _shorten_home(ctx.path)

    branch_str = f" on [cyan]{ctx.branch}[/cyan]" if ctx.branch else ""
    # S1: velocity trend inline with header
    trend_labels = {
        "rising": "trending up",
        "falling": "trending down",
        "flat": "steady",
        "dead": "no recent activity",
    }
    trend_str = ""
    if ctx.velocity_trend and ctx.velocity_trend != "dead":
        label = trend_labels.get(ctx.velocity_trend, ctx.velocity_trend)
        trend_str = f" · [dim]{label}[/dim]"
    elif ctx.velocity_trend == "dead":
        trend_str = " · [dim red]no recent activity[/dim red]"

    header = (
        f"\n  [bold]{ctx.name}[/bold]{branch_str} — {status_str}{hours_str}{trend_str}"
    )
    console.print(header)
    console.print(f"  [dim]{short_path}[/dim]")

    # Purpose or README one-liner — "what is this project?"
    from armillary.purpose_service import get_purpose

    purpose = get_purpose(str(ctx.path))
    if purpose:
        console.print(f"  [italic]{purpose}[/italic]")
    elif ctx.readme_oneliner:
        console.print(f"  [dim italic]{ctx.readme_oneliner}[/dim italic]")

    # Revenue
    from armillary.purpose_service import get_revenue

    rev = get_revenue(str(ctx.path))
    if rev is not None:
        console.print(f"  [green]${rev}/mo[/green]")

    # S5: project age + intensity (active span = first→last commit)
    if ctx.first_commit_ts and ctx.work_hours:
        from datetime import datetime

        try:
            first = datetime.fromisoformat(ctx.first_commit_ts)
            age_days = (datetime.now() - first).days
            if age_days > 0:
                if age_days >= 365:
                    age_str = f"{age_days / 365:.1f}y"
                elif age_days >= 30:
                    age_str = f"{age_days / 30.44:.0f}mo"
                else:
                    age_str = f"{age_days}d"
                # Intensity: h/mo over active span (first→last commit)
                intensity_str = ""
                if ctx.last_commit_ts_iso:
                    last = datetime.fromisoformat(ctx.last_commit_ts_iso)
                    span_days = max((last - first).days, 1)
                    if span_days >= 30:
                        span_months = span_days / 30.44
                        intensity = ctx.work_hours / span_months
                        intensity_str = f" · {intensity:.0f} h/mo"
                console.print(f"  [dim]Age {age_str}{intensity_str}[/dim]")
        except (ValueError, TypeError):
            pass

    # Days since last commit — bold kill trigger
    if ctx.last_commit_ts_iso:
        from datetime import datetime as _dt

        try:
            last = _dt.fromisoformat(ctx.last_commit_ts_iso)
            days_ago = (_dt.now() - last).days
            if days_ago > 90:
                console.print(f"  [bold red]{days_ago}d since last commit[/bold red]")
            elif days_ago > 30:
                console.print(
                    f"  [bold yellow]{days_ago}d since last commit[/bold yellow]"
                )
        except (ValueError, TypeError):
            pass

    # Monthly sparkline
    if ctx.monthly_commits and any(c > 0 for c in ctx.monthly_commits):
        console.print(f"  [dim]Activity  {_sparkline(ctx.monthly_commits)} (6mo)[/dim]")

    if not ctx.is_git:
        console.print("\n  [dim]Not a git repo — no commit history.[/dim]")
        return

    if ctx.dirty_count > 0:
        s = "s" if ctx.dirty_count > 1 else ""
        age_hint = ""
        if ctx.dirty_max_age_seconds is not None:
            age_hint = f" — {_format_age(ctx.dirty_max_age_seconds)} stale"
        msg = f"{ctx.dirty_count} uncommitted file{s}{age_hint}"
        console.print(f"\n  [bold yellow]{msg}[/bold yellow]")
        for f in ctx.dirty_files:
            console.print(f"    [yellow]{f}[/yellow]")
        if ctx.dirty_count > len(ctx.dirty_files):
            more = ctx.dirty_count - len(ctx.dirty_files)
            console.print(f"    [dim]and {more} more[/dim]")

    if ctx.last_session is not None:
        dur = ctx.last_session.duration_seconds
        if dur >= 3600:
            dur_str = f"{dur / 3600:.1f}h"
        elif dur >= 60:
            dur_str = f"{dur / 60:.0f}min"
        else:
            dur_str = "<1min"
        n = ctx.last_session.commit_count
        c_word = "commit" if n == 1 else "commits"
        console.print(
            f"\n  [bold]Last session[/bold]  "
            f"{dur_str}, {n} {c_word}, "
            f"{ctx.last_session.ended_relative}"
        )

    if ctx.recent_commits:
        console.print("\n  [bold]Last commits[/bold]")
        for c in ctx.recent_commits:
            console.print(
                f"  [dim]{c.short_hash}[/dim]  "
                f"[cyan]{c.relative_time:>13}[/cyan]   "
                f"{c.subject}"
            )
    else:
        console.print("\n  [dim]No commits yet.[/dim]")

    if ctx.recent_branches:
        console.print("\n  [bold]Recent branches[/bold]")
        for b in ctx.recent_branches:
            console.print(f"  {b.name:<30} [dim]{b.relative_time}[/dim]")

    # S6: branch count + remote safety + unmerged
    hints: list[str] = []
    if ctx.branch_count is not None and ctx.branch_count > 1:
        hints.append(f"{ctx.branch_count} local branches")
    if ctx.unmerged_branches:
        n = len(ctx.unmerged_branches)
        hints.append(f"[yellow]{n} unmerged[/yellow]")
    if ctx.has_remote is False:
        hints.append("[bold red]no remote — push before archiving[/bold red]")
    if hints:
        console.print(f"\n  [dim]{' · '.join(hints)}[/dim]")
    if ctx.unmerged_branches:
        for b in ctx.unmerged_branches[:5]:
            console.print(f"    [dim yellow]{b}[/dim yellow]")

    # Last user conversation
    from armillary.purpose_service import get_last_conversation

    last_convo = get_last_conversation(str(ctx.path))
    if last_convo:
        console.print(f"\n  [dim]Last user conversation: {last_convo}[/dim]")

    # Actionable hint
    if ctx.dirty_count > 0:
        console.print(
            f"\n  [dim]→ {ctx.dirty_count} uncommitted change{s}"
            f" — commit or stash before switching[/dim]"
        )

    console.print("")


def _print_yesterday(console: Console, suggestions: list) -> None:
    """Show yesterday's activity as a one-liner retention hook."""
    from datetime import datetime, timedelta

    from armillary.cache import Cache
    from armillary.exclude_service import filter_excluded
    from armillary.status_override import filter_archived

    yesterday = datetime.now() - timedelta(days=1)
    start_of_yesterday = yesterday.replace(hour=0, minute=0, second=0)
    end_of_yesterday = start_of_yesterday + timedelta(days=1)

    with Cache() as cache:
        projects = cache.list_projects()
    projects = filter_excluded(projects)
    projects = filter_archived(projects)

    active_yesterday = []
    for p in projects:
        md = p.metadata
        if (
            md
            and md.last_commit_ts
            and start_of_yesterday <= md.last_commit_ts < end_of_yesterday
        ):
            active_yesterday.append(p.name)

    if active_yesterday:
        names = ", ".join(active_yesterday[:3])
        more = f" +{len(active_yesterday) - 3}" if len(active_yesterday) > 3 else ""
        console.print(f"[dim]Yesterday: {names}{more}[/dim]")


_CATEGORY_ICONS = {
    "momentum": "🔥",
    "zombie": "⚠️",
    "forgotten_gold": "💀",
    "archive_candidate": "📦",
}


@app.command("next")
def next_command(
    skip: str | None = typer.Option(
        None,
        "--skip",
        help="Project name to dismiss from suggestions for 30 days.",
    ),
    reason: str | None = typer.Option(
        None,
        "--reason",
        help="Why you're skipping (e.g. 'blocked by API', 'not now').",
    ),
) -> None:
    """What should you work on today?

    Shows up to 3 suggestions based on your project activity:
    momentum (keep going), zombies (kill or ship), and forgotten
    gold (high-effort dormant projects worth revisiting with AI).

    Use --skip <name> to dismiss a project for 30 days.
    Use --reason with --skip to record why.
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
        skip_project(str(matches[0].path), reason=reason)
        msg = f"Skipped {matches[0].name} for 30 days."
        if reason:
            msg += f" Reason: {reason}"
        typer.secho(msg, fg=typer.colors.CYAN)
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
    from armillary.purpose_service import get_purpose, get_revenue

    console = Console()

    # Yesterday's activity — retention hook (panel 2/3)
    _print_yesterday(console, suggestions)

    for s in suggestions:
        icon = _CATEGORY_ICONS.get(s.category, "•")
        short_path = _shorten_home(s.project.path)
        console.print(
            f"\n{icon} [bold]{s.project.name}[/bold]  [dim]{short_path}[/dim]"
        )
        # Purpose or README one-liner
        purpose = get_purpose(str(s.project.path))
        md = s.project.metadata
        if purpose:
            console.print(f"  [italic]{purpose}[/italic]")
        elif md and md.readme_excerpt:
            excerpt = md.readme_excerpt
            dot = excerpt.find(". ")
            oneliner = excerpt[: dot + 1] if 0 < dot < 80 else excerpt[:80]
            console.print(f"  [dim italic]{oneliner}[/dim italic]")
        # Revenue inline
        rev = get_revenue(str(s.project.path))
        rev_str = f" · [green]${rev}/mo[/green]" if rev else ""
        console.print(f"  {s.reason}{rev_str}")
        # Monthly sparkline
        if md and md.monthly_commits and any(c > 0 for c in md.monthly_commits):
            console.print(
                f"  [dim]Activity  {_sparkline(md.monthly_commits)} (6mo)[/dim]"
            )
        console.print(f"  [dim]→ cd {short_path}[/dim]")
        if s.category == "forgotten_gold":
            console.print(f"  [dim]→ armillary next --skip {s.project.name}[/dim]")


@app.command("card")
def card_command(
    output: str = typer.Option(
        "armillary-card.html",
        "--output",
        "-o",
        help="Output file path.",
    ),
) -> None:
    """Export your activity heatmap as a shareable HTML card."""
    from armillary.heatmap_service import (
        daily_activity,
        export_heatmap_html,
        heatmap_summary,
    )

    activity = daily_activity()
    summary = heatmap_summary(activity)
    html = export_heatmap_html(activity, summary)
    from pathlib import Path

    Path(output).write_text(html, encoding="utf-8")
    typer.secho(f"Card exported to {output}", fg=typer.colors.GREEN)


@app.command("pulse")
def pulse_command() -> None:
    """Weekly pulse — what changed across your projects this week."""
    from armillary.pulse_service import (
        format_pulse,
        generate_pulse,
        load_history,
        take_snapshot,
    )

    pulse = generate_pulse()
    take_snapshot()  # record this week
    console = Console()
    console.print(f"\n{format_pulse(pulse)}")

    history = load_history()
    if len(history) >= 2:
        console.print(
            f"\n[dim]History: {len(history)} weeks tracked. "
            f"Active: {' → '.join(str(h['active']) for h in history[-4:])}[/dim]"
        )
    console.print()


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
