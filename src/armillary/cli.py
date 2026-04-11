"""Command-line interface for armillary."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

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
def scan() -> None:
    """Re-scan umbrella folders and update the cache. (M2)"""
    typer.secho("scan: not implemented yet (milestone M2)", fg=typer.colors.YELLOW)


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
