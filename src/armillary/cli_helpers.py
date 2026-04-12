"""Shared helpers used by multiple CLI modules."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer

from armillary.config import Config, ConfigError, load_config
from armillary.models import UmbrellaFolder
from armillary.utils import shorten_home as _shorten_home_from_utils


def _shorten_home(path: Path) -> str:
    """Replace the user's home prefix with `~` for display."""
    return _shorten_home_from_utils(path)


def _shorten_home_str(path: Path) -> str:
    """Return a string with `~` substituted for `$HOME` if applicable."""
    return _shorten_home_from_utils(path)


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
