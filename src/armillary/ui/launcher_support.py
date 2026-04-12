"""Shared launcher availability helpers for UI modules.

Keeps detail/settings aligned when Streamlit hot-reloads only part of
the app and an older `armillary.launcher` module instance is still in
memory.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from armillary import launcher as launcher_mod
from armillary.config import LauncherConfig


@dataclass(frozen=True)
class LauncherAvailabilityCompat:
    available: bool
    mode: str
    detail: str | None = None
    app_name: str | None = None


def detect_launcher_compat(config: LauncherConfig) -> LauncherAvailabilityCompat:
    """Use modern launcher detection when available, else fall back to PATH.

    Streamlit may temporarily keep an older `armillary.launcher` module in
    memory across partial hot reloads. The UI must degrade gracefully until
    the process is restarted.
    """

    detect = getattr(launcher_mod, "detect_launcher", None)
    if callable(detect):
        return detect(config)

    resolved = shutil.which(config.command)
    return LauncherAvailabilityCompat(
        available=resolved is not None,
        mode="path" if resolved is not None else "missing",
        detail=resolved,
    )
