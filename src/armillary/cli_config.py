"""Config command + full init ceremony for armillary CLI."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import typer

from armillary import bootstrap, exporter, scan_service
from armillary.cli import app
from armillary.cli_helpers import (
    _safe_load_config,
    _shorten_home_str,
    shutil_which,
)
from armillary.config import default_config_path
from armillary.models import ProjectType, Status, UmbrellaFolder

# Khoj health probe used by `config --init` to offer auto-enable.
# 1-second timeout — Khoj either runs locally and answers immediately,
# or it's not there. We don't want init to hang on a slow remote.
_KHOJ_HEALTH_URL = "http://localhost:42110/api/health"
_KHOJ_HEALTH_TIMEOUT = 1.0


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
        projects = scan_service.initial_scan(umbrellas)
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
