"""``armillary next`` — today's suggestions + retention hooks.

Extracted from ``cli_tools.py`` to keep each module under the 400-line
architecture target. Pulls in ``_sparkline`` from ``cli_context`` so
momentum cards share the same renderer as the context view.
"""

from __future__ import annotations

import typer
from rich.console import Console

from armillary.cli import app
from armillary.cli_context import _sparkline


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


def _print_zombie_alert(console: Console) -> None:
    """Warn about ACTIVE projects with no commit in >14 days."""
    from datetime import datetime, timedelta

    from armillary.cache import Cache
    from armillary.exclude_service import filter_excluded
    from armillary.status_override import filter_archived

    cutoff = datetime.now() - timedelta(days=14)

    with Cache() as cache:
        projects = cache.list_projects()
    projects = filter_excluded(projects)
    projects = filter_archived(projects)

    zombies = [
        p
        for p in projects
        if p.metadata
        and p.metadata.status
        and p.metadata.status.value == "ACTIVE"
        and p.metadata.last_commit_ts
        and p.metadata.last_commit_ts < cutoff
        and (p.metadata.work_hours or 0) > 10
    ]
    if zombies:
        names = ", ".join(p.name for p in zombies[:3])
        more = f" +{len(zombies) - 3}" if len(zombies) > 3 else ""
        console.print(
            f"[bold yellow]⚠ {len(zombies)} zombie"
            f"{'s' if len(zombies) > 1 else ''}: "
            f"{names}{more} — no commit in 14+ days[/bold yellow]"
        )


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

    # Zombie alert — ACTIVE projects going stale (M3)
    _print_zombie_alert(console)

    # Status transitions (ADR 0025)
    import contextlib as _cl

    with _cl.suppress(Exception):
        from armillary.transition_service import (
            consume_pending_transitions,
            format_transitions,
        )

        transitions = consume_pending_transitions()
        if transitions:
            console.print(f"[dim]{format_transitions(transitions)}[/dim]")

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
            from armillary.utils import excerpt_one_liner

            console.print(
                f"  [dim italic]{excerpt_one_liner(md.readme_excerpt)}[/dim italic]"
            )
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
