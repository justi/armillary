"""Command-line interface for armillary."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from armillary import bootstrap, exporter, launcher, metadata, status
from armillary.cache import Cache
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
    umbrellas = _resolve_umbrellas(umbrella, max_depth)
    if not umbrellas:
        typer.secho(
            "No umbrellas to scan. Pass `-u <path>` or add an `umbrellas:` "
            "block to your config (`armillary config`).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    projects = scan_umbrellas(umbrellas)

    if not no_metadata:
        metadata.extract_all(projects)
        for project in projects:
            if project.metadata is None:
                continue
            # The scanner already excludes `.git/` from `last_modified` to
            # avoid GitPython's `git status` side effect. But for a freshly
            # cloned repo, every file has mtime = clone time, which makes
            # the scanner's signal say "today" even though the last real
            # activity was years ago. We therefore reconcile both signals:
            # `last_modified` = max(scanner mtime, last_commit_ts).
            #
            # - Edit-after-commit:    scanner mtime > commit ts  → scanner wins ✓
            # - Untouched since commit: scanner mtime ≈ commit ts → either
            # - Freshly cloned old repo: scanner mtime > commit ts → scanner
            #   wins ("today, I cloned this") which is actually fine — the
            #   user explicitly chose to bring it onto disk today
            #
            # This matches the OR semantics of PLAN.md §5 status heuristic
            # ("ACTIVE = commit in last 7 days OR file modification in last
            # 7 days"). Status compute also uses both signals.
            if (
                project.type is ProjectType.GIT
                and project.metadata.last_commit_ts is not None
                and project.metadata.last_commit_ts > project.last_modified
            ):
                project.last_modified = project.metadata.last_commit_ts
            project.metadata.status = status.compute_status(project)

    payload = [p.model_dump(mode="json") for p in projects]
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))

    if not no_cache:
        with Cache() as cache:
            cache.upsert(projects, write_metadata=not no_metadata)
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


@app.command()
def config(
    show_path: bool = typer.Option(
        False,
        "--path",
        help="Print the config file path and exit.",
    ),
    init: bool = typer.Option(
        False,
        "--init",
        help=(
            "Create a config at the default path. By default scans `~/` for "
            "umbrella folder candidates and asks you to pick which to include."
        ),
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="With --init, accept all detected candidates without asking.",
    ),
    blank: bool = typer.Option(
        False,
        "--blank",
        help="With --init, write a minimal placeholder config without scanning.",
    ),
) -> None:
    """Open the config file in $EDITOR (or print its path / create it).

    With no flags, opens `~/.config/armillary/config.yaml` in the editor
    pointed to by `$EDITOR` (or `nano` as a sensible fallback). Use
    `--path` to just print the location, or `--init` to create the
    file:

    - `--init` (default): scans `~/` for umbrella candidates (folders
      with multiple git repos or conventional names like `Projects`,
      `repos`, `code`), asks you to pick, writes the selection.
    - `--init --non-interactive`: same scan, takes all candidates.
    - `--init --blank`: writes a minimal placeholder file without scanning.

    PLAN.md §5 "Bootstrap": this is the two-phase first-run experience.
    """
    config_path = default_config_path()

    if show_path:
        typer.echo(config_path)
        return

    if init and not config_path.exists():
        _init_config_file(config_path, non_interactive=non_interactive, blank=blank)

    if not config_path.exists():
        typer.secho(
            f"{config_path} does not exist. "
            "Run `armillary config --init` to create it.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1)

    # `$EDITOR` is allowed to carry arguments (e.g. `code --wait`,
    # `vim -f`). Split it like a shell would so the executable name
    # can be looked up on PATH and the rest get passed straight through.
    editor_value = os.environ.get("EDITOR", "nano")
    try:
        editor_argv = shlex.split(editor_value)
    except ValueError as exc:
        typer.secho(
            f"$EDITOR ({editor_value!r}) is not a valid shell-quoted "
            f"command: {exc}. Set $EDITOR or edit {config_path} manually.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from exc
    if not editor_argv:
        typer.secho(
            f"$EDITOR is empty. Set $EDITOR or edit {config_path} manually.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    if shutil_which(editor_argv[0]) is None:
        typer.secho(
            f"$EDITOR ({editor_argv[0]!r}) is not on PATH. "
            f"Set $EDITOR or edit {config_path} manually.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    result = subprocess.run([*editor_argv, str(config_path)], check=False)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


def _init_config_file(
    config_path: Path,
    *,
    non_interactive: bool,
    blank: bool,
) -> None:
    """Discover umbrella candidates, ask the user, write the config.

    The three modes (`--blank`, `--non-interactive`, default interactive)
    differ only in how umbrellas are chosen — the YAML render is the same
    for all of them.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if blank:
        config_path.write_text(_BLANK_STARTER_CONFIG_YAML, encoding="utf-8")
        typer.secho(
            f"Created {config_path} with a placeholder umbrella. "
            "Edit it before running `armillary scan`.",
            fg=typer.colors.GREEN,
        )
        return

    typer.secho("Scanning ~ for umbrella folder candidates…", fg=typer.colors.CYAN)
    candidates = bootstrap.discover_umbrella_candidates()

    if not candidates:
        typer.secho(
            "No umbrella candidates found under ~. Falling back to a "
            "blank config — edit it manually.",
            fg=typer.colors.YELLOW,
        )
        config_path.write_text(_BLANK_STARTER_CONFIG_YAML, encoding="utf-8")
        typer.secho(f"Created {config_path}", fg=typer.colors.GREEN)
        return

    typer.echo("")
    typer.secho(
        f"Found {len(candidates)} candidate(s):",
        bold=True,
    )
    for i, candidate in enumerate(candidates, 1):
        marker = "✓" if candidate.name_match else " "
        line = (
            f"  [{i:>2}] {marker} "
            f"{_shorten_home_str(candidate.path)}  "
            f"({candidate.git_count} git, {candidate.idea_count} idea)"
        )
        typer.echo(line)
    typer.echo("")

    if non_interactive:
        chosen = candidates
        typer.secho(
            f"--non-interactive: taking all {len(chosen)} candidate(s).",
            fg=typer.colors.CYAN,
        )
    else:
        chosen = _ask_for_candidate_selection(candidates)
        if not chosen:
            typer.secho(
                "No umbrellas selected. Aborting — config file not written.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(1)

    config_path.write_text(_render_config_yaml(chosen), encoding="utf-8")
    typer.secho(
        f"\n✓ Wrote {config_path} with {len(chosen)} umbrella(s):",
        fg=typer.colors.GREEN,
    )
    for candidate in chosen:
        typer.echo(
            f"    - {_shorten_home_str(candidate.path)}  "
            f"({candidate.git_count} git, {candidate.idea_count} idea)"
        )
    typer.echo("\nNext: armillary scan")


def _ask_for_candidate_selection(
    candidates: list[bootstrap.UmbrellaCandidate],
) -> list[bootstrap.UmbrellaCandidate]:
    """Prompt for a comma-separated list of candidate numbers.

    Accepts: `1,3,5`, `1-3`, `all`, empty (= cancel). Loops on invalid
    input until the user gives something parseable.
    """
    while True:
        raw = typer.prompt(
            "Which to include? (e.g. `1,3` or `1-3` or `all`, empty to cancel)",
            default="all",
            show_default=True,
        ).strip()
        if not raw:
            return []
        if raw.lower() == "all":
            return list(candidates)
        try:
            picks = _parse_selection(raw, len(candidates))
        except ValueError as exc:
            typer.secho(f"  {exc}", fg=typer.colors.RED)
            continue
        return [candidates[i - 1] for i in sorted(picks)]


def _parse_selection(raw: str, total: int) -> set[int]:
    """Parse a `1,3,5-7` style selection into a set of 1-based indices.

    Raises ValueError on any out-of-range or non-numeric token.
    """
    out: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo_str, hi_str = token.split("-", 1)
            try:
                lo = int(lo_str)
                hi = int(hi_str)
            except ValueError as exc:
                raise ValueError(f"Invalid range '{token}'") from exc
            if lo < 1 or hi > total or lo > hi:
                raise ValueError(f"Range '{token}' is out of bounds (1..{total})")
            out.update(range(lo, hi + 1))
        else:
            try:
                n = int(token)
            except ValueError as exc:
                raise ValueError(f"Not a number: '{token}'") from exc
            if n < 1 or n > total:
                raise ValueError(f"Number '{n}' is out of bounds (1..{total})")
            out.add(n)
    if not out:
        raise ValueError("Empty selection")
    return out


def _render_config_yaml(
    candidates: list[bootstrap.UmbrellaCandidate],
) -> str:
    """Render a `Config`-shaped YAML document for the chosen umbrellas."""
    lines = [
        "# armillary config — generated by `armillary config --init`",
        "# Re-run `armillary config` to edit, or `armillary config --init`",
        "# to regenerate from a fresh ~/ scan.",
        "",
        "umbrellas:",
    ]
    for candidate in candidates:
        display_path = _shorten_home_str(candidate.path)
        lines.append(f"  - path: {display_path}")
        lines.append(f"    label: {candidate.path.name}")
        lines.append("    max_depth: 3")
    lines.extend(
        [
            "",
            "# Custom launchers can be added here. Built-in entries (claude-code,",
            "# codex, cursor, zed, vscode, terminal, finder) are always available",
            "# even if you do not list them — they only need overriding if you",
            "# want to change the command or args.",
            "#",
            "# Example:",
            "#",
            "# launchers:",
            "#   nvim:",
            "#     label: Neovim",
            "#     command: nvim",
            '#     args: ["{path}"]',
            '#     icon: "✏️"',
            "",
            "# Khoj semantic search (optional, opt-in):",
            "#",
            "# khoj:",
            "#   enabled: true",
            "#   api_url: http://localhost:42110",
            "",
        ]
    )
    return "\n".join(lines)


def _shorten_home_str(path: Path) -> str:
    """Return a string with `~` substituted for `$HOME` if applicable."""
    home = str(Path.home())
    s = str(path)
    if s.startswith(home):
        return "~" + s[len(home) :]
    return s


_BLANK_STARTER_CONFIG_YAML = """\
# armillary config — generated placeholder
# Edit the umbrellas list and re-run `armillary scan`.

umbrellas:
  - path: ~/Projects
    label: Projects
    max_depth: 3

# Custom launchers can be added here. Built-in entries (claude-code,
# codex, cursor, zed, vscode, terminal, finder) are always available
# even if you do not list them — they only need overriding if you
# want to change the command or args.
#
# Example:
#
# launchers:
#   nvim:
#     label: Neovim
#     command: nvim
#     args: ["{path}"]
#     icon: "✏️"
"""


def _resolve_umbrellas(
    cli_umbrellas: list[Path] | None,
    cli_max_depth: int,
) -> list[UmbrellaFolder]:
    """Combine `--umbrella` flags with the umbrellas declared in config.

    CLI flags take precedence — if the user passes any `-u`, the config
    is ignored entirely so they can override per-invocation. With no
    `-u`, every umbrella from the config is used (each entry can carry
    its own `max_depth`).
    """
    if cli_umbrellas:
        return [UmbrellaFolder(path=p, max_depth=cli_max_depth) for p in cli_umbrellas]

    cfg = _safe_load_config()
    if cfg is None:
        return []
    return [
        UmbrellaFolder(path=u.path, label=u.label, max_depth=u.max_depth)
        for u in cfg.umbrellas
    ]


def _safe_load_config() -> Config | None:
    """Load the config file, printing a friendly error to stderr on failure."""
    try:
        return load_config()
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        return None


def shutil_which(name: str) -> str | None:
    """Lazy import shim so the CLI module stays cheap to import."""
    import shutil

    return shutil.which(name)


if __name__ == "__main__":
    app()
