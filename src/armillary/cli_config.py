"""Config command entry point for armillary CLI.

Ceremony helpers (scan, Khoj detection, Claude bridge, YAML rendering)
live in `cli_config_ceremony` to keep this module under 400 lines.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import typer

from armillary import bootstrap
from armillary.cli import app
from armillary.cli_config_ceremony import (
    ask_for_candidate_selection,
    detect_claude_code_and_offer_bridge,
    detect_khoj_and_maybe_enable,
    render_config_yaml,
    run_initial_scan_and_summary,
    show_launcher_availability,
)
from armillary.cli_helpers import _shorten_home_str, shutil_which
from armillary.config import default_config_path


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

    This is the two-phase first-run experience (discover umbrellas, then save config).
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
        chosen = ask_for_candidate_selection(candidates)
        if not chosen:
            typer.secho(
                "No umbrellas selected. Aborting — config file not written.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(1)

    config_path.write_text(render_config_yaml(chosen), encoding="utf-8")
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
        scan_succeeded = run_initial_scan_and_summary(chosen)

    if not skip_launcher_check:
        show_launcher_availability()

    if not skip_khoj_detect:
        detect_khoj_and_maybe_enable(
            config_path, chosen, non_interactive=non_interactive
        )

    if not skip_claude_detect:
        detect_claude_code_and_offer_bridge(
            non_interactive=non_interactive,
            scan_succeeded=scan_succeeded,
        )

    typer.echo("")
    typer.secho("✓ Setup complete. Try:", fg=typer.colors.GREEN, bold=True)
    typer.echo("    armillary start    # browser dashboard")
    typer.echo("    armillary list     # terminal table")


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
