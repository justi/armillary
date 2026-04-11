"""Command-line interface for armillary."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from armillary.cache import Cache
from armillary.models import ProjectType, UmbrellaFolder
from armillary.scanner import scan as scan_umbrellas

app = typer.Typer(
    name="armillary",
    help="Project observatory with AI integration.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def start(
    port: int = typer.Option(8501, "--port", "-p", help="Port for the dashboard."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Do not open the browser automatically."
    ),
) -> None:
    """Launch the dashboard in the browser."""
    if importlib.util.find_spec("streamlit") is None:
        typer.secho(
            "streamlit is not installed — reinstall armillary "
            "(`pip install -e .`) or run `pip install streamlit`",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    ui_path = Path(__file__).parent / "ui" / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ui_path),
        "--server.port",
        str(port),
        # Privacy: PLAN.md §14 promises no telemetry. Streamlit defaults
        # browser.gatherUsageStats to true, so we explicitly disable it
        # on every launch (no need for a user-managed config file).
        "--browser.gatherUsageStats",
        "false",
    ]
    if no_browser:
        cmd += ["--server.headless", "true"]

    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


@app.command()
def scan(
    umbrella: list[Path] = typer.Option(
        ...,
        "--umbrella",
        "-u",
        help="Umbrella folder to scan. Repeat for multiple.",
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        "-d",
        min=1,
        max=10,
        help="Max recursion depth per umbrella.",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Skip writing the result to the SQLite cache (just print JSON).",
    ),
) -> None:
    """Scan umbrella folders and print the project list as JSON.

    By default the result is also persisted to the SQLite cache so
    `armillary list` can read it back. Use `--no-cache` for ad-hoc
    introspection that should not touch on-disk state.

    Config-file driven umbrella folders come in M5. Until then, pass
    them explicitly, e.g.:

        armillary scan -u ~/Projects -u ~/ideas
    """
    umbrellas = [UmbrellaFolder(path=p, max_depth=max_depth) for p in umbrella]
    projects = scan_umbrellas(umbrellas)
    payload = [p.model_dump(mode="json") for p in projects]
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))

    if not no_cache:
        with Cache() as cache:
            cache.upsert(projects)
            cache.prune_stale()


@app.command("list")
def list_projects(
    type_filter: ProjectType | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by project type (git or idea).",
    ),
    umbrella_filter: str | None = typer.Option(
        None,
        "--umbrella",
        "-u",
        help="Substring filter on the umbrella path.",
    ),
) -> None:
    """Print the project table from cache, sorted by last modified."""
    with Cache() as cache:
        projects = cache.list_projects(
            type=type_filter,
            umbrella_substring=umbrella_filter,
        )

    if not projects:
        typer.secho(
            "No projects in cache. Run `armillary scan -u <path>` first.",
            fg=typer.colors.YELLOW,
        )
        return

    table = Table(title=f"{len(projects)} project(s)", show_lines=False)
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Umbrella", style="dim")
    table.add_column("Last modified", justify="right")

    for p in projects:
        table.add_row(
            p.type.value,
            p.name,
            _shorten_home(p.umbrella),
            _humanize_relative_time(p.last_modified),
        )

    Console().print(table)


def _shorten_home(path: Path) -> str:
    """Replace the user's home prefix with `~` for display."""
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home) :] if s.startswith(home) else s


def _humanize_relative_time(when: datetime) -> str:
    """Render a `datetime` as a short relative-to-now string (`3d ago`)."""
    delta = datetime.now() - when
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        return f"{days // 7}w ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
) -> None:
    """Search across all indexed projects. (M4)"""
    typer.secho(
        f"search '{query}': not implemented yet (milestone M4)",
        fg=typer.colors.YELLOW,
    )


@app.command("open")
def open_project(
    project: str = typer.Argument(..., help="Project name to open."),
) -> None:
    """Open a project in the default launcher. (M5)"""
    typer.secho(
        f"open '{project}': not implemented yet (milestone M5)",
        fg=typer.colors.YELLOW,
    )


@app.command()
def config() -> None:
    """Open the config file in $EDITOR. (M5)"""
    typer.secho("config: not implemented yet (milestone M5)", fg=typer.colors.YELLOW)


if __name__ == "__main__":
    app()
