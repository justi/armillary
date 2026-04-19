"""CLI lifecycle commands: exclude / include / archive / activate /
purpose / talked / revenue. Extracted from ``cli_tools.py`` to keep
each module under the 400-line architecture target.
"""

from __future__ import annotations

import typer

from armillary.cache import Cache
from armillary.cli import app
from armillary.cli_helpers import _resolve_project_or_report


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
        project = _resolve_project_or_report(
            all_projects,
            name,
            missing_message="No project matches '{name}'.",
            ambiguous_message="'{name}' is ambiguous: {matches}. Be more specific.",
            missing_color=typer.colors.YELLOW,
            missing_err=False,
        )
        if project is None:
            continue
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
        project = _resolve_project_or_report(
            all_projects,
            name,
            missing_message="No project matches '{name}'.",
            ambiguous_message="'{name}' is ambiguous: {matches}.",
            missing_color=typer.colors.YELLOW,
            missing_err=False,
        )
        if project is None:
            continue
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
        project = _resolve_project_or_report(
            projects,
            name,
            missing_message="No project matches '{name}'.",
            ambiguous_message="'{name}' is ambiguous: {matches}.",
        )
        if project is None:
            continue
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
        project = _resolve_project_or_report(
            projects,
            name,
            missing_message="No project matches '{name}'.",
            ambiguous_message="'{name}' is ambiguous: {matches}.",
        )
        if project is None:
            continue
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
    project = _resolve_project_or_report(
        projects,
        project_name,
        missing_message="No project matches '{name}'.",
        ambiguous_message="Ambiguous: {matches}.",
    )
    if project is None:
        raise typer.Exit(2)
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
    project = _resolve_project_or_report(
        projects,
        project_name,
        missing_message="No project matches '{name}'.",
        ambiguous_message="Ambiguous: {matches}.",
    )
    if project is None:
        raise typer.Exit(2)
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
    project = _resolve_project_or_report(
        projects,
        project_name,
        missing_message="No project matches '{name}'.",
        ambiguous_message="Ambiguous: {matches}.",
    )
    if project is None:
        raise typer.Exit(2)
    if amount is not None:
        set_revenue(str(project.path), amount)
        typer.secho(f"{project.name}: ${amount}/mo", fg=typer.colors.GREEN)
    else:
        current = get_revenue(str(project.path))
        if current is not None:
            typer.echo(f"{project.name}: ${current}/mo")
        else:
            typer.secho(f"No revenue set for {project.name}.", fg=typer.colors.YELLOW)
