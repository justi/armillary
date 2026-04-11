"""YAML config loading for armillary.

The config file lives at `~/.config/armillary/config.yaml` (overridable
via the `ARMILLARY_CONFIG` env var, mostly for tests). It declares:

- which umbrella folders the scanner walks by default
- the launcher catalogue — labels, commands, and how to spell the
  project path on the command line — used by `armillary open`

A missing config file is **not** an error: every accessor falls back
to a sensible default (no umbrellas, the built-in launcher catalogue).
This keeps the first-run experience working without forcing the user
to write a YAML file before they can do anything.

The schema is intentionally permissive — Pydantic validates the bits
we care about (path types, required keys), and ignores anything else
so future fields don't break older installs.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class UmbrellaConfig(BaseModel):
    """One umbrella folder declaration from the config."""

    model_config = ConfigDict(extra="ignore")

    path: Path
    label: str | None = None
    max_depth: int = Field(default=3, ge=1, le=10)


class LauncherConfig(BaseModel):
    """How to invoke one launcher target.

    `command` is the executable. `args` is the list of arguments where
    `{path}` is substituted with the project path at launch time. The
    project path is also passed via `cwd=...` so most editors do not
    need `{path}` in their args at all.

    `terminal=True` marks this launcher as an interactive terminal app
    (e.g. `codex`, `claude-code`, `vim`). The launcher then keeps the
    parent's stdio so the user can actually interact with it, instead
    of detaching into a background process with /dev/null on stdin.
    """

    model_config = ConfigDict(extra="ignore")

    label: str
    command: str
    args: list[str] = Field(default_factory=list)
    icon: str | None = None
    terminal: bool = False


# Built-in launcher catalogue. Users can override or extend it via the
# `launchers:` block in their config.yaml. Keep entries minimal and
# cross-platform-friendly where possible.
_BUILTIN_LAUNCHERS: dict[str, LauncherConfig] = {
    # `claude` and `codex` are interactive terminal apps. They take their
    # working directory from `cwd=...` so we deliberately do NOT pass
    # `{path}` as a positional argument — both CLIs would treat that as
    # an initial prompt, not a target directory. `terminal=True` keeps
    # the parent's stdio attached so the user actually sees the prompt.
    "claude-code": LauncherConfig(
        label="Claude Code",
        command="claude",
        args=[],
        icon="🤖",
        terminal=True,
    ),
    "codex": LauncherConfig(
        label="Codex",
        command="codex",
        args=[],
        icon="⚡",
        terminal=True,
    ),
    "cursor": LauncherConfig(
        label="Cursor",
        command="cursor",
        args=["{path}"],
        icon="📝",
    ),
    "zed": LauncherConfig(
        label="Zed",
        command="zed",
        args=["{path}"],
        icon="🔷",
    ),
    "vscode": LauncherConfig(
        label="VS Code",
        command="code",
        args=["{path}"],
        icon="💙",
    ),
    "terminal": LauncherConfig(
        label="Terminal",
        command="open",
        args=["-a", "Terminal", "{path}"],
        icon="⌨️",
    ),
    "finder": LauncherConfig(
        label="Finder",
        command="open",
        args=["{path}"],
        icon="📁",
    ),
}


class Config(BaseModel):
    """Top-level armillary configuration."""

    model_config = ConfigDict(extra="ignore")

    umbrellas: list[UmbrellaConfig] = Field(default_factory=list)
    launchers: dict[str, LauncherConfig] = Field(
        default_factory=lambda: dict(_BUILTIN_LAUNCHERS)
    )

    @classmethod
    def builtin_launchers(cls) -> dict[str, LauncherConfig]:
        """Return a fresh copy of the built-in launcher catalogue."""
        return dict(_BUILTIN_LAUNCHERS)


def default_config_path() -> Path:
    """Where the config file lives if the caller does not override it."""
    override = os.environ.get("ARMILLARY_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "armillary" / "config.yaml"


def load_config(path: Path | None = None) -> Config:
    """Load the YAML config from `path` (or `default_config_path()`).

    A missing file returns an empty `Config` with the built-in launcher
    catalogue. A malformed file or one we cannot read (permission error,
    accidental directory at the same path, etc.) raises `ConfigError`
    with a friendly message attached so the CLI does not show a Python
    traceback.
    """
    config_path = path or default_config_path()
    if not config_path.exists():
        return Config()

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read {config_path}: {exc}") from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {config_path}: {exc}") from exc

    if raw is None:
        return Config()
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Config root must be a mapping, got {type(raw).__name__} in {config_path}"
        )

    raw["launchers"] = _merge_launchers(raw.get("launchers") or {})

    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid config in {config_path}:\n{exc}") from exc


def _merge_launchers(user_launchers: object) -> dict[str, dict[str, object]]:
    """Merge user-declared launchers with the built-in catalogue per-key.

    A user entry like ``cursor: {command: my-wrapper}`` keeps the
    built-in `label` / `args` / `icon` for the `cursor` launcher and
    only overrides `command`. This matches the starter-file guidance
    that "you only need to override the command or args".

    Whole new entries (e.g. ``nvim: {label: ..., command: ...}``) flow
    through unchanged.
    """
    base: dict[str, dict[str, object]] = {
        key: launcher.model_dump() for key, launcher in _BUILTIN_LAUNCHERS.items()
    }
    if not isinstance(user_launchers, dict):
        return base

    for key, override in user_launchers.items():
        if key in base and isinstance(override, dict):
            base[key] = {**base[key], **override}
        else:
            base[key] = override
    return base


class ConfigError(Exception):
    """Raised when the config file exists but cannot be parsed or validated."""
