"""Tests for UI-side launcher compatibility helpers.

These helpers exist to keep the dashboard from crashing when Streamlit
hot-reloads only part of the code and an older `armillary.launcher`
module is still resident in memory.
"""

from __future__ import annotations

from armillary.config import LauncherConfig
from armillary.ui import detail, settings


def test_settings_launcher_compat_falls_back_to_path_when_symbol_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.delattr(settings.launcher_mod, "detect_launcher", raising=False)
    monkeypatch.setattr(settings.shutil, "which", lambda name: "/usr/bin/cursor")

    availability = settings._detect_launcher_compat(
        LauncherConfig(label="Cursor", command="cursor", args=["{path}"])
    )

    assert availability.available is True
    assert availability.mode == "path"
    assert availability.detail == "/usr/bin/cursor"


def test_detail_build_launcher_options_survives_old_launcher_module(monkeypatch) -> None:
    monkeypatch.delattr(detail.launcher_mod, "detect_launcher", raising=False)
    monkeypatch.setattr(detail.shutil, "which", lambda name: "/usr/bin/cursor")

    available, missing, terminal_only, app_labels = detail.build_launcher_options(
        {
            "cursor": LauncherConfig(
                label="Cursor",
                command="cursor",
                args=["{path}"],
                icon="📝",
            )
        }
    )

    assert [opt.target_id for opt in available] == ["cursor"]
    assert available[0].availability_mode == "path"
    assert missing == []
    assert terminal_only == []
    assert app_labels == []
