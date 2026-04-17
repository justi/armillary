"""Setup ceremony helpers for `armillary config --init`.

Extracted from cli_config.py to keep modules under 400 lines.
Each function handles one step of the init ceremony: scanning,
launcher checks, Claude Code bridge, config rendering, and user
selection prompts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from armillary import bootstrap, exporter, launcher, scan_service
from armillary.cli_helpers import _safe_load_config, _shorten_home_str
from armillary.models import ProjectType, Status, UmbrellaFolder


def run_initial_scan_and_summary(
    chosen: list[bootstrap.UmbrellaCandidate],
) -> bool:
    """Walk the chosen umbrellas, extract metadata, persist to cache,
    print a per-status summary.

    The cache is **cleared first** so a re-run of `armillary config
    --init` (after the user removed the old config) leaves the cache
    containing exactly the new umbrella selection — not stale rows
    from a previous setup.

    Returns True if the cache now reflects the new umbrella selection,
    False if the scan was short-circuited by an error.
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
            Status.STALLED,
            Status.DORMANT,
            Status.IDEA,
            Status.IN_PROGRESS,
        ]
        parts = [f"{status_counts.get(s.value, 0)} {s.value}" for s in order]
        typer.echo(f"    {', '.join(parts)}")
    return True


def show_launcher_availability() -> None:
    """Check launcher availability and print a short summary."""
    cfg = _safe_load_config()
    if cfg is None or not cfg.launchers:
        return

    available: list[str] = []
    missing: list[str] = []
    for target_id, launcher_cfg in cfg.launchers.items():
        label = f"{launcher_cfg.command} ({target_id})"
        availability = launcher.detect_launcher(launcher_cfg)
        if availability.available:
            if availability.mode == "macos-app":
                label = f"{label} via macOS app"
            available.append(label)
        else:
            missing.append(label)

    typer.echo("")
    typer.secho("Checking launcher availability…", fg=typer.colors.CYAN)
    if available:
        typer.secho(f"  ✓ available: {', '.join(available)}", fg=typer.colors.GREEN)
    if missing:
        typer.secho(f"  ✗ missing:   {', '.join(missing)}", fg=typer.colors.YELLOW)


def detect_claude_code_and_offer_bridge(
    *,
    non_interactive: bool,
    scan_succeeded: bool,
) -> None:
    """If `~/.claude/` exists, install the repos-index bridge."""
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

    install_mcp_config(claude_dir)


def install_mcp_config(claude_dir: Path) -> None:
    """Write armillary MCP server config to ~/.claude/mcp.json.

    Idempotent: if the "armillary" key already exists, leave it alone.
    """
    mcp_json_path = claude_dir / "mcp.json"

    armillary_bin = str(Path(sys.executable).parent / "armillary")

    existing: dict[str, object] = {}
    if mcp_json_path.is_file():
        try:
            parsed = json.loads(mcp_json_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                existing = parsed
        except (ValueError, OSError):
            pass

    # Claude Code expects {"mcpServers": {"name": {...}}} wrapper.
    # Migrate legacy format where armillary was at the top level.
    if "armillary" in existing and "mcpServers" not in existing:
        existing.setdefault("mcpServers", {})["armillary"] = existing.pop("armillary")

    servers = existing.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        existing["mcpServers"] = servers

    if "armillary" in servers:
        typer.secho(
            f"  · {mcp_json_path} already has armillary MCP — left untouched.",
            fg=typer.colors.CYAN,
        )
        return

    servers["armillary"] = {
        "command": armillary_bin,
        "args": ["mcp-serve"],
    }

    mcp_json_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_json_path.write_text(
        json.dumps(existing, indent=2) + "\n",
        encoding="utf-8",
    )
    typer.secho(
        f"  ✓ Configured MCP server in {mcp_json_path}",
        fg=typer.colors.GREEN,
    )
    typer.echo(
        "    Claude Code can now call armillary_search, "
        "armillary_next, and armillary_projects."
    )


def ask_for_candidate_selection(
    candidates: list[bootstrap.UmbrellaCandidate],
) -> list[bootstrap.UmbrellaCandidate]:
    """Prompt for a comma-separated list of candidate numbers."""
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
            picks = parse_selection(raw, len(candidates))
        except ValueError as exc:
            typer.secho(f"  {exc}", fg=typer.colors.RED)
            continue
        return [candidates[i - 1] for i in sorted(picks)]


def parse_selection(raw: str, total: int) -> set[int]:
    """Parse a `1,3,5-7` style selection into a set of 1-based indices."""
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


def render_config_yaml(
    candidates: list[bootstrap.UmbrellaCandidate],
) -> str:
    """Render a `Config`-shaped YAML document for the chosen umbrellas."""
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
    return header + body + launcher_footer
