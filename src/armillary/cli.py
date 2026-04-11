"""Command-line interface for armillary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import typer

from armillary.models import UmbrellaFolder
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
    ui_path = Path(__file__).parent / "ui" / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ui_path),
        "--server.port",
        str(port),
    ]
    if no_browser:
        cmd += ["--server.headless", "true"]
    subprocess.run(cmd, check=False)


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
) -> None:
    """Scan umbrella folders and print the project list as JSON.

    Config-file driven umbrella folders come in M5. Until then, pass
    them explicitly, e.g.:

        armillary scan -u ~/Projects -u ~/ideas
    """
    umbrellas = [
        UmbrellaFolder(path=p, max_depth=max_depth) for p in umbrella
    ]
    projects = scan_umbrellas(umbrellas)
    payload = [p.model_dump(mode="json") for p in projects]
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@app.command("list")
def list_projects() -> None:
    """Print the project table in the terminal. (M3)"""
    typer.secho("list: not implemented yet (milestone M3)", fg=typer.colors.YELLOW)


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
