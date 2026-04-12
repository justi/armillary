"""Shared utility helpers used by both CLI and UI layers."""

from __future__ import annotations

from pathlib import Path

from armillary.config import Config, ConfigError, load_config


def shorten_home(path: Path) -> str:
    """Replace the user's home directory prefix with ``~``."""
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home) :] if s.startswith(home) else s


def safe_load_config() -> Config | None:
    """Load the config file, returning *None* on any :class:`ConfigError`.

    Both CLI and UI call this when they need the config but can degrade
    gracefully if it is missing or broken.
    """
    try:
        return load_config()
    except ConfigError:
        return None
