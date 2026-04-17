"""Command-line interface for armillary."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from armillary import exporter, scan_service
from armillary.cache import Cache
from armillary.cli_helpers import (
    _humanize_relative_time,
    _resolve_umbrellas,
    _shorten_home,
)
from armillary.config import (
    ConfigError,
    load_config,
)
from armillary.models import ProjectType, Status, UmbrellaFolder
from armillary.scanner import scan as scan_umbrellas

app = typer.Typer(
    name="armillary",
    help="What should you work on today?",
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
)


@app.callback()
def _default(ctx: typer.Context) -> None:
    """Run `next` when no subcommand is given. Auto-bootstrap on first run."""
    if ctx.invoked_subcommand is None:
        from armillary.config import default_config_path

        if not default_config_path().exists():
            typer.secho(
                "No config found. Let's set you up (< 60s).\n",
                fg=typer.colors.CYAN,
            )
            from armillary.cli_config import config as config_cmd

            ctx.invoke(config_cmd, init=True)
            _print_delight_card()
            return

        from armillary.cli_tools import next_command

        ctx.invoke(next_command, skip=None, reason=None)


def _print_delight_card() -> None:
    """Portfolio snapshot after first scan — the wow moment."""
    from rich.console import Console
    from rich.panel import Panel

    from armillary.cache import Cache
    from armillary.exclude_service import filter_excluded
    from armillary.status_override import filter_archived

    with Cache() as cache:
        projects = cache.list_projects()
    projects = filter_excluded(projects)
    projects = filter_archived(projects)

    if not projects:
        return

    from collections import Counter
    from datetime import datetime

    total = len(projects)
    total_hours = sum(p.metadata.work_hours or 0 for p in projects if p.metadata)
    statuses = Counter(
        p.metadata.status.value for p in projects if p.metadata and p.metadata.status
    )

    # Find oldest project — clamp to 2005 (git did not exist before)
    _GIT_EPOCH = datetime(2005, 4, 1)
    first_dates = [
        p.metadata.first_commit_ts
        for p in projects
        if p.metadata
        and p.metadata.first_commit_ts
        and p.metadata.first_commit_ts >= _GIT_EPOCH
    ]
    span = ""
    if first_dates:
        oldest = min(first_dates)
        years = (datetime.now() - oldest).days / 365
        span = f" · since {oldest.strftime('%b %Y')}" if years >= 1 else ""

    # Top 2 by hours
    by_hours = sorted(
        projects,
        key=lambda p: p.metadata.work_hours or 0 if p.metadata else 0,
        reverse=True,
    )
    top = ", ".join(
        f"{p.name} ({p.metadata.work_hours:.0f}h)"
        for p in by_hours[:2]
        if p.metadata and p.metadata.work_hours
    )

    status_line = "  ".join(f"{count} {name}" for name, count in statuses.most_common())

    content = (
        f"[bold]{total} projects[/bold] · "
        f"{total_hours:,.0f}h invested{span}\n"
        f"{status_line}\n"
    )
    if top:
        content += f"Top: {top}\n"
    content += (
        "\n[dim]→ armillary       what to work on today[/dim]\n"
        "[dim]→ armillary start  open dashboard[/dim]"
    )

    console = Console()
    console.print()
    console.print(Panel(content, title="YOUR PORTFOLIO", border_style="green"))
    console.print()


@app.command()
def start(
    port: int = typer.Option(8501, "--port", "-p", help="Port for the dashboard."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Do not open the browser automatically."
    ),
    no_scan: bool = typer.Option(
        False,
        "--no-scan",
        help="Skip the automatic pre-start scan (just open the dashboard).",
    ),
) -> None:
    """Launch the dashboard in the browser.

    By default runs a quick scan before opening Streamlit so the
    dashboard shows fresh data from the first render. The scan reads
    the umbrellas from config, enriches with git metadata, and
    persists to the SQLite cache — exactly what `armillary scan`
    does. Pass `--no-scan` if you want to skip this and open the
    dashboard instantly (it will show whatever the cache already has).
    """
    if importlib.util.find_spec("streamlit") is None:
        typer.secho(
            "streamlit is not installed — reinstall armillary "
            "(`pip install -e .`) or run `pip install streamlit`",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    if not no_scan:
        _pre_start_scan()

    ui_path = Path(__file__).parent / "ui" / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ui_path),
        "--server.port",
        str(port),
        # Privacy: armillary promises no telemetry. Streamlit defaults
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


def _pre_start_scan() -> None:
    """Incremental scan before opening the dashboard.

    Walks the filesystem to discover projects (fast — just iterdir),
    then compares each project's `last_modified` against the cached
    value. Only projects whose mtime changed get the expensive
    metadata extraction (GitPython, README, size walk, commit stats).
    Unchanged projects reuse their cached metadata as-is.

    On a typical run where 2-3 repos changed out of 50+, this takes
    1-2 s instead of 20+.
    """
    try:
        cfg = load_config()
    except ConfigError:
        typer.secho(
            "⚠ No config found. Run `armillary config --init` "
            "or use ⚙️ Settings in the dashboard.",
            fg=typer.colors.YELLOW,
        )
        return

    if not cfg.umbrellas:
        typer.secho(
            "⚠ No umbrellas configured. Run `armillary config --init` "
            "or use ⚙️ Settings in the dashboard.",
            fg=typer.colors.YELLOW,
        )
        return

    typer.secho("Scanning before dashboard launch…", fg=typer.colors.CYAN)
    try:
        umbrellas = [
            UmbrellaFolder(path=u.path, max_depth=u.max_depth) for u in cfg.umbrellas
        ]
        projects, changed_count = scan_service.incremental_scan(umbrellas)

        typer.secho(
            f"  ✓ {len(projects)} project(s), {changed_count} changed.",
            fg=typer.colors.GREEN,
        )
    except Exception as exc:  # noqa: BLE001
        typer.secho(
            f"  ⚠ Pre-start scan failed: {exc}",
            fg=typer.colors.YELLOW,
        )
        typer.echo("  Dashboard will show cached data.")


@app.command()
def scan(
    umbrella: list[Path] = typer.Option(
        None,
        "--umbrella",
        "-u",
        help=(
            "Umbrella folder to scan. Repeat for multiple. "
            "If omitted, falls back to umbrellas in ~/.config/armillary/config.yaml."
        ),
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        "-d",
        min=1,
        max=10,
        help="Max recursion depth per umbrella (overridden per-entry by config).",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Skip writing the result to the SQLite cache (just print JSON).",
    ),
    no_metadata: bool = typer.Option(
        False,
        "--no-metadata",
        help=(
            "Skip GitPython / README / ADR extraction. Faster on huge "
            "umbrellas; status will be missing for git projects."
        ),
    ),
    refresh_bridge: bool = typer.Option(
        False,
        "--refresh-bridge",
        help=(
            "After the scan, re-write the Claude Code bridge repos-index "
            "at ~/.claude/armillary/repos-index.md so AI sessions see the "
            "fresh project table. No-op if ~/.claude/ does not exist."
        ),
    ),
) -> None:
    """Scan umbrella folders and print the project list as JSON.

    By default the result is enriched with git metadata, README excerpt,
    and computed status, then persisted to the SQLite cache so
    `armillary list` can read it back. Use `--no-cache` for ad-hoc
    introspection that should not touch on-disk state, and `--no-metadata`
    for the fast path that just walks the filesystem.

    Umbrella folders come from `--umbrella` flags first, then fall back
    to the `umbrellas:` block in `~/.config/armillary/config.yaml`. Run
    `armillary config` to edit the file.
    """
    # `--no-cache --refresh-bridge` is semantically contradictory: the
    # bridge writer reads from the SQLite cache, so skipping the cache
    # write would publish stale (or empty) data while the command
    # claims it refreshed. Reject the combo up front instead of
    # silently lying to the user.
    if no_cache and refresh_bridge:
        typer.secho(
            "--refresh-bridge cannot be combined with --no-cache — the "
            "bridge writer reads from the cache. Drop one flag.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    umbrellas = _resolve_umbrellas(umbrella, max_depth)
    if not umbrellas:
        typer.secho(
            "No umbrellas to scan. Pass `-u <path>` or add an `umbrellas:` "
            "block to your config (`armillary config`).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    if no_cache:
        # No cache write: scan + enrich in-memory only.
        projects = scan_umbrellas(umbrellas)
        if not no_metadata:
            scan_service.enrich(projects)
    else:
        projects = scan_service.full_scan(umbrellas, write_metadata=not no_metadata)

    payload = [p.model_dump(mode="json") for p in projects]
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))

    if refresh_bridge:
        claude_dir = Path.home() / ".claude"
        if not claude_dir.is_dir():
            typer.secho(
                "--refresh-bridge: ~/.claude/ not found — nothing to refresh.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        else:
            try:
                bridge_path, written, _ = exporter.install_claude_bridge(
                    with_claude_md=False,
                )
            except OSError as exc:
                typer.secho(
                    f"--refresh-bridge: could not write bridge: {exc}",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
            else:
                typer.secho(
                    f"--refresh-bridge: wrote {written} project(s) to {bridge_path}",
                    fg=typer.colors.CYAN,
                    err=True,
                )


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
    status_filter: Status | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by computed status (ACTIVE, STALLED, DORMANT, IDEA, IN_PROGRESS).",
    ),
) -> None:
    """Print the project table from cache, sorted by last modified."""
    with Cache() as cache:
        projects = cache.list_projects(
            type=type_filter,
            umbrella_substring=umbrella_filter,
            status=status_filter,
        )

    if not projects:
        typer.secho(
            "No projects in cache. Run `armillary scan -u <path>` first.",
            fg=typer.colors.YELLOW,
        )
        return

    table = Table(title=f"{len(projects)} project(s)", show_lines=False)
    table.add_column("Status", style="green", no_wrap=True)
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Branch", style="magenta")
    table.add_column("Dirty", justify="right")
    table.add_column("Umbrella", style="dim")
    table.add_column("Last modified", justify="right")

    from armillary.status_override import get_override

    for p in projects:
        md = p.metadata
        override = get_override(str(p.path))
        status_label = (
            override.value
            if override
            else (md.status.value if md and md.status else "—") or "—"
        )
        branch = (md.branch if md else None) or "—"
        dirty = str(md.dirty_count) if md and md.dirty_count is not None else "—"
        table.add_row(
            status_label,
            p.type.value,
            p.name,
            branch,
            dirty,
            _shorten_home(p.umbrella),
            _humanize_relative_time(p.last_modified),
        )

    Console().print(table)


# Register commands from submodules — must come after `app` is defined.
import armillary.cli_config  # noqa: F401, E402
import armillary.cli_tools  # noqa: F401, E402

if __name__ == "__main__":
    app()
