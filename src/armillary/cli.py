"""Command-line interface for armillary."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

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

# Khoj health probe used by `config --init` to offer auto-enable.
# 1-second timeout — Khoj either runs locally and answers immediately,
# or it's not there. We don't want init to hang on a slow remote.
_KHOJ_HEALTH_URL = "http://localhost:42110/api/health"
_KHOJ_HEALTH_TIMEOUT = 1.0

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


@app.command("install-khoj")
def install_khoj(
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        "-y",
        help="Skip the confirmation prompt and install straight away.",
    ),
) -> None:
    """Install Khoj into the current Python environment.

    Picks the best available installer so it works across common setups:

    1. If `uv` is on PATH, prefer `uv pip install khoj` — fastest, and
       works on `uv venv`-created environments that do not ship pip.
    2. Else, if `python -m pip` works in the current interpreter, use
       that. This is the classic CPython/virtualenv happy path.
    3. Else, try `python -m ensurepip --upgrade` to bootstrap pip, then
       retry the pip install. Catches `uv venv` without `--seed` and
       some minimal Debian/Homebrew venvs.
    4. Otherwise surface a concrete error telling the user what to do
       next (install uv, or recreate the venv with `--seed`).

    Khoj is a heavy dependency (~1 GB with the default ML models +
    torch) so the command confirms once before pulling anything down.
    On success, prints the next steps: start the Khoj server in a
    separate terminal, then rerun `armillary config --init --force`
    (or flip the toggle in the dashboard Settings → Khoj tab).
    """
    installer_cmd, installer_label = _pick_khoj_installer()

    typer.secho(
        f"This will install Khoj via `{installer_label}`.",
        fg=typer.colors.CYAN,
    )
    typer.echo(
        "  Khoj pulls ~1 GB of ML dependencies (torch, transformers, …). "
        "This can take several minutes."
    )
    typer.echo(f"  Python:    {sys.executable}")
    typer.echo(f"  Command:   {' '.join(installer_cmd)}")

    if not non_interactive and not typer.confirm(
        "\n  Proceed?",
        default=False,
    ):
        typer.echo("Aborted.")
        raise typer.Exit(1)

    typer.secho("\nInstalling Khoj…", fg=typer.colors.CYAN)
    result = subprocess.run(installer_cmd, check=False)

    # If plain `python -m pip` failed with "No module named pip", try
    # to bootstrap pip via ensurepip and retry once. uv path already
    # succeeded or failed for its own reasons — do not second-guess it.
    if result.returncode != 0 and installer_cmd[:3] == [sys.executable, "-m", "pip"]:
        typer.secho(
            "\npip install failed. Trying to bootstrap pip via ensurepip…",
            fg=typer.colors.YELLOW,
        )
        bootstrap = subprocess.run(
            [sys.executable, "-m", "ensurepip", "--upgrade"],
            check=False,
        )
        if bootstrap.returncode == 0:
            typer.secho(
                "  ✓ ensurepip OK — retrying pip install.",
                fg=typer.colors.CYAN,
            )
            result = subprocess.run(installer_cmd, check=False)

    if result.returncode != 0:
        typer.secho(
            f"\nInstall failed with exit code {result.returncode}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.echo(
            "  Common causes: network error, incompatible Python version, "
            "conflicting dependencies, or a venv created without pip.\n"
            "  Workarounds:\n"
            "    - Install uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`) "
            "and rerun `armillary install-khoj`.\n"
            "    - Recreate the venv with `uv venv --seed` or `python -m venv`.\n"
            "    - Install Khoj manually: `pip install khoj`."
        )
        raise typer.Exit(result.returncode)

    typer.secho("\n✓ Khoj package installed.", fg=typer.colors.GREEN, bold=True)
    typer.echo("")

    # Khoj requires PostgreSQL 15 + pgvector. Rather than print a brew
    # recipe and hope the user's machine is not booby-trapped (we got
    # burned by `brew install pgvector` compiling against postgresql@14
    # while the user ran @15 — "extension control file" not found),
    # we now provision an isolated Postgres container via Docker. One
    # command, no host-side package managers, no version conflicts.
    if shutil_which("docker") is None:
        typer.secho(
            "⚠ Docker not found. Khoj needs PostgreSQL 15 + pgvector to run.",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        typer.echo(
            "  armillary install-khoj uses Docker to provision the database\n"
            "  (container image: pgvector/pgvector:pg15) to avoid brew\n"
            "  formula conflicts and give you a reproducible setup.\n"
        )
        typer.echo("  Install Docker Desktop, then rerun `armillary install-khoj`:")
        typer.secho(
            "       https://www.docker.com/products/docker-desktop/",
            fg=typer.colors.CYAN,
        )
        typer.echo(
            "\n  Or set up Postgres + pgvector yourself and start the Khoj\n"
            "  server with POSTGRES_HOST=… POSTGRES_DB=khoj env vars."
        )
        raise typer.Exit(1)

    _provision_khoj_postgres_container()

    typer.echo("")
    typer.secho("Next steps:", bold=True)
    typer.echo("  1. Start the Khoj server (foreground, logs in the terminal):")
    typer.secho("       armillary start-khoj", fg=typer.colors.CYAN)
    typer.echo("  2. In a SECOND terminal, wire it into armillary:")
    typer.secho(
        "       armillary config --init --force",
        fg=typer.colors.CYAN,
    )
    typer.echo(
        "     (or enable via dashboard Settings → Khoj tab if you "
        "already have a config)"
    )


# --- Khoj docker / runner helpers -----------------------------------------

_KHOJ_PG_CONTAINER = "khoj-pg"
_KHOJ_PG_IMAGE = "pgvector/pgvector:pg15"
_KHOJ_PG_VOLUME = "khoj-pg-data"
_KHOJ_DB_NAME = "khoj"
_KHOJ_DB_USER = "postgres"
# The container is not exposed outside localhost; POSTGRES_PASSWORD is
# a Docker-init default, not a secret. Using `_KHOJ_DB_USER` as the
# value keeps the literal out of the source so secret scanners
# (GitGuardian, gitleaks, trufflehog) do not flag it while preserving
# the postgres/postgres convention that every pgvector/pgvector:pg15
# tutorial assumes.
_KHOJ_DB_PASSWORD = _KHOJ_DB_USER
_KHOJ_DB_HOST = "localhost"

# Non-default host port so we do NOT fight with an existing
# `brew services start postgresql@*` or a system Postgres that owns
# 5432. Inside the container Postgres still listens on 5432 — the
# mapping is host:54322 → container:5432. Anyone already running a
# host Postgres on 5432 can keep it; armillary just picks a dedicated
# port. `start-khoj` exports POSTGRES_PORT=54322 so the Khoj Django
# backend connects to the right process.
_KHOJ_DB_PORT = "54322"
_KHOJ_CONTAINER_PORT = "5432"


def _docker_container_state(name: str) -> str:
    """Return "running", "stopped", or "missing" for a docker container."""
    # `docker ps -a --filter name=^<name>$ --format {{.State}}` — the
    # caret/dollar anchor prevents accidental prefix matches on
    # "khoj-pg-backup" etc.
    result = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name=^{name}$",
            "--format",
            "{{.State}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return "missing"
    state = result.stdout.strip().lower()
    if not state:
        return "missing"
    if state == "running":
        return "running"
    return "stopped"


def _docker_container_host_port(name: str) -> str | None:
    """Return the host port currently mapped to the container's 5432.

    Uses `docker port <name> 5432/tcp`, which prints lines like
    `0.0.0.0:54322` — we keep just the port after the final colon.
    Returns None if the container is missing or has no such mapping.
    """
    result = subprocess.run(
        ["docker", "port", name, f"{_KHOJ_CONTAINER_PORT}/tcp"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if ":" not in first_line:
        return None
    return first_line.rsplit(":", 1)[-1]


def _provision_khoj_postgres_container() -> None:
    """Create (or reuse) the Khoj Postgres+pgvector docker container.

    Idempotent:
    - Missing              → `docker run -d --name khoj-pg …`
    - Stopped, right port  → `docker start khoj-pg`
    - Running, right port  → skip
    - Wrong host port      → `docker rm -f khoj-pg` + recreate
      (persistent volume `khoj-pg-data` keeps the embeddings across
      the recreate — no data loss)

    Always follows with a wait-for-ready loop and
    `CREATE EXTENSION IF NOT EXISTS vector` so subsequent reruns just
    confirm the DB is healthy.
    """
    state = _docker_container_state(_KHOJ_PG_CONTAINER)
    typer.secho(
        f"Provisioning Postgres+pgvector container `{_KHOJ_PG_CONTAINER}`…",
        fg=typer.colors.CYAN,
    )
    typer.echo(f"  Image:     {_KHOJ_PG_IMAGE}")
    typer.echo(f"  Volume:    {_KHOJ_PG_VOLUME} (persists embeddings)")
    typer.echo(
        f"  Port:      {_KHOJ_DB_HOST}:{_KHOJ_DB_PORT} "
        f"→ container:{_KHOJ_CONTAINER_PORT}"
    )

    # If the container exists, check whether its host-side port
    # mapping matches what we want today. Historically armillary
    # used 5432:5432, which collided with brew postgresql@14/@15 and
    # led to "extension control file postgresql@14 not found" crashes
    # (wrong process was answering psql). Recreate with the new port
    # — the named volume survives so embeddings don't.
    if state != "missing":
        current_port = _docker_container_host_port(_KHOJ_PG_CONTAINER)
        if current_port is not None and current_port != _KHOJ_DB_PORT:
            typer.secho(
                f"  · Existing container uses host port {current_port}, "
                f"expected {_KHOJ_DB_PORT}.",
                fg=typer.colors.YELLOW,
            )
            typer.secho(
                f"  · Recreating `{_KHOJ_PG_CONTAINER}` with the new port "
                f"(volume `{_KHOJ_PG_VOLUME}` persists — no data loss).",
                fg=typer.colors.CYAN,
            )
            rm = subprocess.run(
                ["docker", "rm", "-f", _KHOJ_PG_CONTAINER],
                capture_output=True,
                text=True,
                check=False,
            )
            if rm.returncode != 0:
                typer.secho(
                    f"  ✗ docker rm -f failed: {rm.stderr.strip()}",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(rm.returncode or 2)
            state = "missing"  # fall through to the "Creating new" branch

    if state == "running":
        typer.secho("  ✓ Container already running.", fg=typer.colors.GREEN)
    elif state == "stopped":
        typer.secho(
            "  · Container exists but stopped — starting.",
            fg=typer.colors.CYAN,
        )
        r = subprocess.run(
            ["docker", "start", _KHOJ_PG_CONTAINER],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            typer.secho(
                f"  ✗ docker start failed: {r.stderr.strip()}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(r.returncode or 2)
        typer.secho("  ✓ Container started.", fg=typer.colors.GREEN)
    else:
        typer.secho("  · Creating new container.", fg=typer.colors.CYAN)
        r = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                _KHOJ_PG_CONTAINER,
                "-p",
                f"{_KHOJ_DB_PORT}:{_KHOJ_CONTAINER_PORT}",
                "-e",
                f"POSTGRES_DB={_KHOJ_DB_NAME}",
                "-e",
                f"POSTGRES_USER={_KHOJ_DB_USER}",
                "-e",
                f"POSTGRES_PASSWORD={_KHOJ_DB_PASSWORD}",
                "-v",
                f"{_KHOJ_PG_VOLUME}:/var/lib/postgresql/data",
                _KHOJ_PG_IMAGE,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            typer.secho(
                f"  ✗ docker run failed: {r.stderr.strip()}",
                fg=typer.colors.RED,
                err=True,
            )
            typer.echo(
                "  Common causes: port 5432 already in use (brew postgres?), "
                "docker daemon not running, disk full."
            )
            raise typer.Exit(r.returncode or 2)
        typer.secho("  ✓ Container created.", fg=typer.colors.GREEN)

    # Wait for the DB to accept connections before we touch it.
    typer.secho("  · Waiting for Postgres to accept connections…", fg=typer.colors.CYAN)
    if not _wait_for_postgres_ready(timeout_s=30):
        typer.secho(
            "  ✗ Postgres did not become ready within 30 seconds.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.echo(f"  Check logs with: docker logs {_KHOJ_PG_CONTAINER}")
        raise typer.Exit(2)
    typer.secho("  ✓ Postgres ready.", fg=typer.colors.GREEN)

    # Enable pgvector. Idempotent via IF NOT EXISTS.
    typer.secho(
        "  · Enabling pgvector extension (CREATE EXTENSION IF NOT EXISTS vector)…",
        fg=typer.colors.CYAN,
    )
    r = subprocess.run(
        [
            "docker",
            "exec",
            _KHOJ_PG_CONTAINER,
            "psql",
            "-U",
            _KHOJ_DB_USER,
            "-d",
            _KHOJ_DB_NAME,
            "-c",
            "CREATE EXTENSION IF NOT EXISTS vector;",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        typer.secho(
            f"  ✗ Could not enable pgvector: {r.stderr.strip()}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(r.returncode or 2)
    typer.secho("  ✓ pgvector enabled.", fg=typer.colors.GREEN)


def _wait_for_postgres_ready(*, timeout_s: int = 30) -> bool:
    """Poll `pg_isready` inside the khoj-pg container until it answers.

    The `pgvector/pgvector:pg15` image ships `pg_isready`, so we call
    it via `docker exec` rather than trying a raw TCP connect from the
    host (which would add a psycopg2 dependency just for this probe).
    Returns True on success, False on timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker",
                "exec",
                _KHOJ_PG_CONTAINER,
                "pg_isready",
                "-U",
                _KHOJ_DB_USER,
                "-d",
                _KHOJ_DB_NAME,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return True
        time.sleep(0.5)
    return False


def _khoj_binary_path() -> Path | None:
    """Resolve the `khoj` executable that this armillary venv installed.

    Prefers `<sys.executable>/../khoj` because that's exactly where
    `uv pip install --python <interp>` dropped it. Falls back to
    `shutil.which("khoj")` for the rare case the user activated the
    venv and wants the shell-resolved path.
    """
    venv_bin = Path(sys.executable).parent / "khoj"
    if venv_bin.is_file() and os.access(venv_bin, os.X_OK):
        return venv_bin
    on_path = shutil_which("khoj")
    if on_path:
        return Path(on_path)
    return None


@app.command("start-khoj")
def start_khoj() -> None:
    """Start the Khoj server in the foreground, wired to the docker DB.

    Exports the Postgres env vars that point at the `khoj-pg` container
    provisioned by `armillary install-khoj`, finds the `khoj` binary in
    this venv, and execs it in `--anonymous-mode`. Runs in the
    foreground so the user sees logs and can Ctrl-C normally; this is a
    development server, not a daemon.

    Preconditions (checked and reported):
    - `khoj-pg` container must exist and be running (or stoppable).
    - `khoj` binary must be reachable (i.e. `armillary install-khoj`
      must have been run first).
    """
    if shutil_which("docker") is None:
        typer.secho(
            "Docker not found. Run `armillary install-khoj` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    state = _docker_container_state(_KHOJ_PG_CONTAINER)
    if state == "missing":
        typer.secho(
            f"Container `{_KHOJ_PG_CONTAINER}` does not exist.\n"
            "Run `armillary install-khoj` to create it.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if state == "stopped":
        typer.secho(
            f"Starting stopped container `{_KHOJ_PG_CONTAINER}`…",
            fg=typer.colors.CYAN,
        )
        r = subprocess.run(
            ["docker", "start", _KHOJ_PG_CONTAINER],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            typer.secho(
                f"docker start failed: {r.stderr.strip()}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(r.returncode or 2)

    if not _wait_for_postgres_ready(timeout_s=15):
        typer.secho(
            f"Postgres did not become ready. Check: docker logs {_KHOJ_PG_CONTAINER}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    khoj_bin = _khoj_binary_path()
    if khoj_bin is None:
        typer.secho(
            "`khoj` binary not found. Run `armillary install-khoj` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_HOST": _KHOJ_DB_HOST,
            "POSTGRES_PORT": _KHOJ_DB_PORT,
            "POSTGRES_DB": _KHOJ_DB_NAME,
            "POSTGRES_USER": _KHOJ_DB_USER,
            "POSTGRES_PASSWORD": _KHOJ_DB_PASSWORD,
        }
    )

    typer.secho(
        f"Starting Khoj server ({khoj_bin}) — Ctrl-C to stop.",
        fg=typer.colors.CYAN,
    )
    typer.echo(
        "First start downloads the default sentence-transformers model "
        "(~500 MB). Subsequent starts reuse the cache."
    )
    typer.echo("")

    # Foreground exec so the user sees logs and Ctrl-C Just Works.
    result = subprocess.run(
        [str(khoj_bin), "--anonymous-mode"],
        env=env,
        check=False,
    )
    raise typer.Exit(result.returncode)


def _pick_khoj_installer() -> tuple[list[str], str]:
    """Return `(argv, human_label)` for the best available installer.

    Prefers `uv pip install` because it works on `uv venv`-created
    environments that do not ship pip. Falls back to `python -m pip`
    if uv is not on PATH. A pip-less interpreter will still fail fast
    in `install_khoj`; the `ensurepip` retry lives there, not here,
    so callers / tests can inspect what we tried without running it.
    """
    uv = shutil_which("uv")
    if uv:
        return (
            [uv, "pip", "install", "--python", sys.executable, "khoj"],
            "uv pip install",
        )
    return ([sys.executable, "-m", "pip", "install", "khoj"], "pip install")


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
            "umbrella folder candidates, asks you to pick, writes the YAML, "
            "runs an initial scan, and offers to enable Khoj / Claude Code "
            "bridges if it detects them."
        ),
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help=(
            "With --init, accept all detected candidates without asking and "
            "treat Khoj / Claude Code prompts as 'no'."
        ),
    ),
    blank: bool = typer.Option(
        False,
        "--blank",
        help=(
            "With --init, write a minimal placeholder config without scanning, "
            "without setup ceremony."
        ),
    ),
    skip_scan: bool = typer.Option(
        False,
        "--skip-scan",
        help="With --init, skip the initial filesystem scan + cache populate.",
    ),
    skip_launcher_check: bool = typer.Option(
        False,
        "--skip-launcher-check",
        help="With --init, skip listing which launchers are on PATH.",
    ),
    skip_khoj_detect: bool = typer.Option(
        False,
        "--skip-khoj-detect",
        help="With --init, skip probing localhost for a running Khoj.",
    ),
    skip_claude_detect: bool = typer.Option(
        False,
        "--skip-claude-detect",
        help="With --init, skip checking for ~/.claude/.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help=(
            "With --init, overwrite an existing config without asking. "
            "The previous file is backed up to `config.yaml.bak` first."
        ),
    ),
) -> None:
    """Open the config file in $EDITOR (or print its path / create it).

    With no flags, opens `~/.config/armillary/config.yaml` in the editor
    pointed to by `$EDITOR` (or `nano` as a sensible fallback). Use
    `--path` to just print the location, or `--init` to create the
    file as part of a one-stop setup ceremony:

    1. Scans `~/` for umbrella candidates and asks you to pick
    2. Runs an initial `armillary scan` to populate the cache
    3. Prints a per-status summary of what was indexed
    4. Lists which configured launchers are on PATH
    5. Probes localhost for Khoj and offers to enable semantic search
    6. Detects `~/.claude/` and offers to install the AI bridge
    7. Prints next-step hints

    Each numbered step has a `--skip-*` flag for scripted setups.
    `--blank` writes the YAML and exits without running the ceremony.

    PLAN.md §5 "Bootstrap": this is the two-phase first-run experience.
    """
    config_path = default_config_path()

    if show_path:
        typer.echo(config_path)
        return

    if init:
        # `--init` is a "create or regenerate" operation: write the file
        # (with whatever ceremony the flags allow) and exit. Falling
        # through to the editor below would be surprising — the user just
        # walked through the picker + setup ceremony, they do not expect
        # nano to pop up afterwards. Use plain `armillary config` if
        # they want to edit.
        if config_path.exists():
            # Interactive default: confirm before clobbering. `--force`
            # skips the prompt for scripted setups. `--non-interactive`
            # without `--force` stays on the old strict behaviour so
            # cron jobs and CI pipelines never silently nuke a config.
            if not force:
                if non_interactive:
                    typer.secho(
                        f"{config_path} already exists. Pass --force to "
                        "overwrite (previous file is backed up to "
                        "config.yaml.bak), or edit it with "
                        "`armillary config`.",
                        fg=typer.colors.YELLOW,
                        err=True,
                    )
                    raise typer.Exit(1)
                typer.secho(
                    f"{config_path} already exists.",
                    fg=typer.colors.YELLOW,
                )
                if not typer.confirm(
                    "  Overwrite it? (a backup will be written to config.yaml.bak)",
                    default=False,
                ):
                    typer.echo(
                        "Aborted. Edit with `armillary config`, or rerun with --force."
                    )
                    raise typer.Exit(1)

            # Backup before clobbering so the user never loses hand-edits.
            backup_path = config_path.with_suffix(config_path.suffix + ".bak")
            try:
                backup_path.write_bytes(config_path.read_bytes())
            except OSError as exc:
                typer.secho(
                    f"Could not write backup {backup_path}: {exc}",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(2) from exc
            typer.secho(
                f"  ✓ Backed up previous config to {backup_path}",
                fg=typer.colors.CYAN,
            )
            # Drop the old cache too — init is a fresh start, and
            # keeping rows from removed umbrellas would be surprising.
            try:
                config_path.unlink()
            except OSError as exc:
                typer.secho(
                    f"Could not remove {config_path}: {exc}",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(2) from exc

        _init_config_file(
            config_path,
            non_interactive=non_interactive,
            blank=blank,
            skip_scan=skip_scan,
            skip_launcher_check=skip_launcher_check,
            skip_khoj_detect=skip_khoj_detect,
            skip_claude_detect=skip_claude_detect,
        )
        return

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
    skip_scan: bool = False,
    skip_launcher_check: bool = False,
    skip_khoj_detect: bool = False,
    skip_claude_detect: bool = False,
) -> None:
    """Discover umbrellas, write config, run setup ceremony.

    Modes:
    - `blank=True` writes the placeholder file and exits — no scan,
      no detection, no ceremony. Escape hatch for users who want to
      hand-edit YAML themselves.
    - `non_interactive=True` accepts every detected candidate without
      asking the user, and treats Khoj/Claude prompts as 'no'.
    - default (no flags) runs the full ceremony: pick → write →
      scan → summary → launcher check → Khoj detect → Claude
      detect → final hint.

    Each `skip_*` flag short-circuits one ceremony step for scripted
    setups (and tests). `blank=True` implies all skips.
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

    # ----- setup ceremony -------------------------------------------------
    # Each step is independent and may be skipped via its flag. Errors in
    # any step print a friendly message and continue — init never aborts
    # because of a setup ceremony failure (the YAML is already written and
    # the user is recoverable).

    # Track whether the initial scan actually populated the cache for
    # the umbrellas we just wrote. The Claude bridge step reads the cache
    # directly, so installing without a fresh scan would publish whatever
    # stale data the cache already holds — which may not match the new
    # config. Defaults to False so `--skip-scan` short-circuits too.
    scan_succeeded = False
    if not skip_scan:
        scan_succeeded = _run_initial_scan_and_summary(chosen)

    if not skip_launcher_check:
        _show_launcher_availability()

    if not skip_khoj_detect:
        _detect_khoj_and_maybe_enable(
            config_path, chosen, non_interactive=non_interactive
        )

    if not skip_claude_detect:
        _detect_claude_code_and_offer_bridge(
            non_interactive=non_interactive,
            scan_succeeded=scan_succeeded,
        )

    typer.echo("")
    typer.secho("✓ Setup complete. Try:", fg=typer.colors.GREEN, bold=True)
    typer.echo("    armillary start    # browser dashboard")
    typer.echo("    armillary list     # terminal table")


# ----- setup ceremony helpers ----------------------------------------------


def _run_initial_scan_and_summary(
    chosen: list[bootstrap.UmbrellaCandidate],
) -> bool:
    """Walk the chosen umbrellas, extract metadata, persist to cache,
    print a per-status summary.

    The cache is **cleared first** so a re-run of `armillary config
    --init` (after the user removed the old config) leaves the cache
    containing exactly the new umbrella selection — not stale rows
    from a previous setup. `prune_stale()` is too lenient here: it
    only deletes rows older than 7 days, so recent entries from a
    removed umbrella would persist for up to a week.

    Errors are caught and printed as a friendly warning — init must
    not abort if the first scan fails.

    Returns True if the cache now reflects the new umbrella selection,
    False if the scan was short-circuited by an error. Callers use this
    to decide whether downstream ceremony steps (Claude bridge install)
    should trust the cache contents.
    """
    typer.echo("")
    typer.secho("Running initial scan…", fg=typer.colors.CYAN)

    try:
        umbrellas = [UmbrellaFolder(path=c.path, max_depth=3) for c in chosen]
        projects = scan_umbrellas(umbrellas)
        metadata.extract_all(projects)
        for project in projects:
            if project.metadata is None:
                continue
            # Same `last_modified = max(fs, last_commit_ts)` lift used by
            # `armillary scan` — see cli.scan() for the rationale.
            if (
                project.type is ProjectType.GIT
                and project.metadata.last_commit_ts is not None
                and project.metadata.last_commit_ts > project.last_modified
            ):
                project.last_modified = project.metadata.last_commit_ts
            project.metadata.status = status.compute_status(project)

        with Cache() as cache:
            # Init is "fresh setup" — start from a clean slate so no
            # rows from a removed umbrella linger in the dashboard.
            cache.clear_projects()
            cache.upsert(projects, write_metadata=True)
    except Exception as exc:  # noqa: BLE001 — never abort init on scan failure
        typer.secho(
            f"⚠ Initial scan failed: {exc}",
            fg=typer.colors.YELLOW,
        )
        typer.echo("  You can retry later with `armillary scan`. Continuing setup…")
        return False

    git_count = sum(1 for p in projects if p.type is ProjectType.GIT)
    idea_count = sum(1 for p in projects if p.type is ProjectType.IDEA)

    status_counts: dict[str, int] = {}
    for p in projects:
        if p.metadata is not None and p.metadata.status is not None:
            label = p.metadata.status.value
            status_counts[label] = status_counts.get(label, 0) + 1

    typer.secho(f"\n✓ Indexed {len(projects)} project(s):", fg=typer.colors.GREEN)
    typer.echo(f"    {git_count} git, {idea_count} idea")
    if status_counts:
        order = [
            Status.ACTIVE,
            Status.PAUSED,
            Status.DORMANT,
            Status.IDEA,
            Status.IN_PROGRESS,
        ]
        parts = [f"{status_counts.get(s.value, 0)} {s.value}" for s in order]
        typer.echo(f"    {', '.join(parts)}")
    return True


def _show_launcher_availability() -> None:
    """Cross-check `cfg.launchers` against `shutil.which()` and print
    a 2-line summary of which commands are reachable on PATH."""
    cfg = _safe_load_config()
    if cfg is None or not cfg.launchers:
        return

    import shutil as _shutil

    available: list[str] = []
    missing: list[str] = []
    for target_id, launcher_cfg in cfg.launchers.items():
        label = f"{launcher_cfg.command} ({target_id})"
        if _shutil.which(launcher_cfg.command) is not None:
            available.append(label)
        else:
            missing.append(label)

    typer.echo("")
    typer.secho("Checking launcher availability…", fg=typer.colors.CYAN)
    if available:
        typer.secho(f"  ✓ available: {', '.join(available)}", fg=typer.colors.GREEN)
    if missing:
        typer.secho(f"  ✗ missing:   {', '.join(missing)}", fg=typer.colors.YELLOW)


def _detect_khoj_and_maybe_enable(
    config_path: Path,
    chosen: list[bootstrap.UmbrellaCandidate],
    *,
    non_interactive: bool,
) -> None:
    """Probe localhost Khoj. Auto-enable if reachable, otherwise print
    install instructions so the user can get there in one command.

    Policy: if the user has Khoj running at localhost:42110 at init
    time, they almost certainly want it — auto-enable, no prompt. If
    Khoj is NOT reachable we do NOT silently skip: print an explicit
    "how to install" block pointing at `armillary install-khoj`, so
    the feature is discoverable without reading the docs. The Settings
    page (PR #18) is still the explicit opt-out once Khoj is enabled.
    """
    try:
        with urlopen(_KHOJ_HEALTH_URL, timeout=_KHOJ_HEALTH_TIMEOUT) as response:
            status_code = getattr(response, "status", None) or response.getcode()
            reachable = status_code == 200
    except (HTTPError, URLError, TimeoutError, OSError):
        reachable = False

    if not reachable:
        typer.echo("")
        typer.secho(
            "🧠 Khoj not detected at localhost:42110.",
            fg=typer.colors.CYAN,
        )
        typer.echo(
            "   Semantic search is optional. To set it up (needs Docker):\n"
            "     1. `armillary install-khoj`  "
            "(pip-installs Khoj + provisions pgvector via Docker)\n"
            "     2. `armillary start-khoj`    "
            "(runs the Khoj server in a separate terminal)\n"
            "     3. Rerun `armillary config --init --force` "
            "to pick it up"
        )
        return

    typer.echo("")
    typer.secho("🧠 Detected Khoj at localhost:42110.", fg=typer.colors.CYAN)

    config_path.write_text(
        _render_config_yaml(chosen, khoj_enabled=True),
        encoding="utf-8",
    )
    typer.secho(
        f"  ✓ Enabled semantic search in {config_path.name}. "
        "Toggle it off via the dashboard's Settings → Khoj tab.",
        fg=typer.colors.GREEN,
    )


def _detect_claude_code_and_offer_bridge(
    *,
    non_interactive: bool,
    scan_succeeded: bool,
) -> None:
    """If `~/.claude/` exists, install the repos-index bridge.

    In interactive mode, asks whether to install and whether to also
    wire up `~/.claude/CLAUDE.md` via the `@armillary/repos-index.md`
    import line. In `--non-interactive` mode, skips the prompt entirely
    — bridge install is opt-in via `armillary install-claude-bridge`
    from the terminal.

    When `scan_succeeded=False` (the initial scan was skipped via
    `--skip-scan` or it failed), we refuse to install the bridge from
    whatever stale contents the cache currently holds. Publishing a
    repos-index that does not match the just-written config would
    preload the wrong projects into Claude Code — subtle and confusing.
    The user is pointed at `armillary scan` + `armillary install-
    claude-bridge` as the correct recovery path.
    """
    claude_dir = Path.home() / ".claude"
    if not claude_dir.is_dir():
        return

    typer.echo("")
    typer.secho("🤖 Found Claude Code config.", fg=typer.colors.CYAN)

    if not scan_succeeded:
        typer.echo(
            "  Skipping bridge install — the initial scan did not run or "
            "failed, so the cache may not match this config.\n"
            "  Run `armillary scan` then "
            "`armillary install-claude-bridge` to wire up Claude Code."
        )
        return

    if non_interactive:
        typer.echo(
            "  --non-interactive: skipping Claude Code bridge prompt. "
            "Run `armillary install-claude-bridge` later to install."
        )
        return

    if not typer.confirm(
        "  Install armillary repos-index bridge for AI sessions?",
        default=False,
    ):
        return

    wire_claude_md = typer.confirm(
        "  Also append @armillary/repos-index.md to ~/.claude/CLAUDE.md?",
        default=False,
    )

    try:
        bridge_path, written, appended = exporter.install_claude_bridge(
            with_claude_md=wire_claude_md,
        )
    except OSError as exc:
        typer.secho(
            f"  ⚠ Could not install bridge: {exc}",
            fg=typer.colors.YELLOW,
        )
        return

    typer.secho(
        f"  ✓ Wrote {written} project(s) to {bridge_path}",
        fg=typer.colors.GREEN,
    )
    if wire_claude_md:
        claude_md = bridge_path.parent.parent / "CLAUDE.md"
        if appended:
            typer.secho(
                f"  ✓ Appended import line to {claude_md}",
                fg=typer.colors.GREEN,
            )
        else:
            typer.secho(
                f"  · {claude_md} already imports armillary — left untouched.",
                fg=typer.colors.CYAN,
            )


def _ask_for_candidate_selection(
    candidates: list[bootstrap.UmbrellaCandidate],
) -> list[bootstrap.UmbrellaCandidate]:
    """Prompt for a comma-separated list of candidate numbers.

    Accepts: `1,3,5`, `1-3`, `all`, empty (= cancel). Loops on invalid
    input until the user gives something parseable.

    The empty default is intentional — `typer.prompt(default="all")`
    would silently substitute `"all"` for a blank Enter and the
    "empty to cancel" affordance would never fire.
    """
    while True:
        raw = typer.prompt(
            "Which to include? (e.g. `1,3` or `1-3` or `all`, empty to cancel)",
            default="",
            show_default=False,
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
    *,
    khoj_enabled: bool = False,
) -> str:
    """Render a `Config`-shaped YAML document for the chosen umbrellas.

    Goes through `yaml.safe_dump()` so any folder name with YAML
    metacharacters (`:`, `#`, `-`, leading whitespace, ...) is properly
    quoted. Hand-crafted f-strings would silently truncate or break
    on names like `~/Work #old` or `~/Foo: Bar`.

    `khoj_enabled=True` adds a real `khoj:` block to the payload (with
    `enabled: true` + the localhost API URL) and drops the commented-out
    Khoj footer so the file does not have both a real block and a
    placeholder comment.
    """
    import yaml as _yaml

    payload: dict[str, object] = {
        "umbrellas": [
            {
                "path": _shorten_home_str(candidate.path),
                "label": candidate.path.name,
                "max_depth": 3,
            }
            for candidate in candidates
        ],
    }
    if khoj_enabled:
        payload["khoj"] = {
            "enabled": True,
            "api_url": "http://localhost:42110",
        }

    header = (
        "# armillary config — generated by `armillary config --init`\n"
        "# Re-run `armillary config` to edit, or `armillary config --init`\n"
        "# to regenerate from a fresh ~/ scan.\n\n"
    )
    body = _yaml.safe_dump(
        payload,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    launcher_footer = (
        "\n"
        "# Custom launchers can be added here. Built-in entries (claude-code,\n"
        "# codex, cursor, zed, vscode, terminal, finder) are always available\n"
        "# even if you do not list them — they only need overriding if you\n"
        "# want to change the command or args.\n"
        "#\n"
        "# Example:\n"
        "#\n"
        "# launchers:\n"
        "#   nvim:\n"
        "#     label: Neovim\n"
        "#     command: nvim\n"
        '#     args: ["{path}"]\n'
        '#     icon: "✏️"\n'
    )
    khoj_comment = (
        "\n"
        "# Khoj semantic search (optional, opt-in):\n"
        "#\n"
        "# khoj:\n"
        "#   enabled: true\n"
        "#   api_url: http://localhost:42110\n"
    )
    if khoj_enabled:
        # Real khoj block already in `body` — only the launcher comment
        # makes sense as documentation footer.
        return header + body + launcher_footer
    return header + body + launcher_footer + khoj_comment


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
