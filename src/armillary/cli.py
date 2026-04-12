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

from armillary import exporter, launcher, scan_service
from armillary.cache import Cache
from armillary.cli_helpers import (
    _humanize_relative_time,
    _resolve_umbrellas,
    _safe_load_config,
    _shorten_home,
)
from armillary.config import (
    Config,
    ConfigError,
    default_config_path,
    load_config,
)
from armillary.models import ProjectType, Status, UmbrellaFolder
from armillary.scanner import scan as scan_umbrellas
from armillary.search import KhojConfig, KhojSearch, LiteralSearch

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
        help="Filter by computed status (ACTIVE, PAUSED, DORMANT, IDEA, IN_PROGRESS).",
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

    for p in projects:
        md = p.metadata
        status_label = (md.status.value if md and md.status else "—") or "—"
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
    use_khoj: bool = typer.Option(
        False,
        "--khoj",
        help=(
            "Use the Khoj semantic search backend instead of ripgrep. "
            "Falls back to ripgrep if Khoj is unreachable. Only enabled "
            "if `khoj.enabled` is true in your config."
        ),
    ),
) -> None:
    """Search across indexed project files (literal `ripgrep` by default).

    Without flags, runs `rg <query>` over every cached project (or a
    subset filtered by `--project`). With `--khoj`, posts to the Khoj
    REST API configured in `~/.config/armillary/config.yaml` and falls
    back to ripgrep on any error so the dashboard never breaks.
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

    backend = _build_search_backend(cfg, use_khoj=use_khoj)
    if backend is None:
        raise typer.Exit(2)

    console = Console()
    total_hits = 0
    for project in projects:
        try:
            hits = backend.search(query, root=project.path, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 — KhojResponseError, URLError, etc.
            typer.secho(
                f"Search backend ({backend.name}) failed: {exc}. "
                "Install ripgrep (`brew install ripgrep`) for an automatic "
                "fallback, or check that the Khoj server is reachable.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2) from exc
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


def _build_search_backend(
    cfg: Config, *, use_khoj: bool
) -> LiteralSearch | KhojSearch | None:
    if not use_khoj:
        if not LiteralSearch.is_available():
            typer.secho(
                "ripgrep (`rg`) is not on PATH. "
                "Install it (`brew install ripgrep`) or run with `--khoj`.",
                fg=typer.colors.RED,
                err=True,
            )
            return None
        return LiteralSearch()

    if not cfg.khoj.enabled:
        typer.secho(
            "Khoj is not enabled. Set `khoj.enabled: true` in "
            f"{default_config_path()} first.",
            fg=typer.colors.RED,
            err=True,
        )
        return None

    # When ripgrep is also available we wire it in as the fallback so any
    # transient Khoj failure degrades to literal search instead of "no
    # matches". With no ripgrep on PATH we deliberately leave fallback as
    # None — KhojSearch will then raise on errors and the CLI surfaces a
    # clear "Khoj is broken AND there is no fallback" message rather than
    # silently returning empty.
    fallback: LiteralSearch | None = (
        LiteralSearch() if LiteralSearch.is_available() else None
    )
    return KhojSearch(
        config=KhojConfig(
            api_url=cfg.khoj.api_url,
            api_key=cfg.khoj.api_key,
            timeout_seconds=cfg.khoj.timeout_seconds,
        ),
        fallback=fallback,
    )


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
    """Open a project in the configured launcher.

    Looks the project up in cache by name (case-insensitive substring),
    resolves the launcher catalogue from the config, and spawns the
    target tool with `cwd` set to the project's directory.
    """
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


@app.command("export-index")
def export_index(
    output: Path = typer.Argument(
        Path("repos-index.md"),
        help="Where to write the markdown index (default: ./repos-index.md).",
    ),
    title: str = typer.Option(
        "armillary — projects index",
        "--title",
        help="Heading for the generated document.",
    ),
) -> None:
    """Export every cached project as a markdown table for AI tools.

    The output is a self-contained `.md` file: heading, generation
    timestamp, project count, and one row per project with name,
    type, status, branch, dirty count, last commit date, last
    modified, path, and README excerpt. Drop it into a Claude Code
    session, a Codex prompt, or any other tool that can read markdown.

    This is the safe half of PLAN.md M7. Auto-writing into AI tool
    memory directories is deferred until we have an explicit contract
    for those paths.
    """
    written = exporter.write_repos_index(output, title=title)
    if written == 0:
        typer.secho(
            f"Wrote {output} but the cache is empty. Run `armillary scan` first.",
            fg=typer.colors.YELLOW,
        )
        return
    typer.secho(f"Wrote {written} project(s) to {output}", fg=typer.colors.GREEN)


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
    """Write the repos-index for Claude Code at `~/.claude/armillary/repos-index.md`.

    Claude Code reads `CLAUDE.md` files as implicit context. This command
    writes a fresh project table to `~/.claude/armillary/repos-index.md`
    and (with `--with-claude-md`) wires it into the top-level CLAUDE.md
    via the documented `@file` import syntax. The result: every Claude
    Code session started from your home already knows what armillary
    has indexed, no manual copy-paste required.
    """
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


# Register commands from submodules — must come after `app` is defined.
import armillary.cli_config  # noqa: F401, E402
import armillary.cli_khoj  # noqa: F401, E402

if __name__ == "__main__":
    app()
