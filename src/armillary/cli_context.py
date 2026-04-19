"""``armillary context <name>`` — show project state for instant re-entry.

Extracted from ``cli_tools.py`` to keep each module under the 400-line
architecture target. The ``_format_age`` and ``_sparkline`` helpers
are kept next to their single call site here.
"""

from __future__ import annotations

import typer
from rich.console import Console

from armillary.cli import app


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
