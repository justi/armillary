"""Tests for UI-side launcher compatibility helpers.

These helpers exist to keep the dashboard from crashing when Streamlit
hot-reloads only part of the code and an older `armillary.launcher`
module is still resident in memory.
"""

from __future__ import annotations

from armillary.config import LauncherConfig
from armillary.ui import detail, launcher_support, settings_editors


def test_detect_launcher_compat_falls_back_to_path_when_symbol_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.delattr(launcher_support.launcher_mod, "detect_launcher", raising=False)
    monkeypatch.setattr(launcher_support.shutil, "which", lambda name: "/usr/bin/cursor")

    availability = launcher_support.detect_launcher_compat(
        LauncherConfig(label="Cursor", command="cursor", args=["{path}"])
    )

    assert availability.available is True
    assert availability.mode == "path"
    assert availability.detail == "/usr/bin/cursor"


def test_detail_build_launcher_options_survives_old_launcher_module(
    monkeypatch,
) -> None:
    monkeypatch.delattr(launcher_support.launcher_mod, "detect_launcher", raising=False)
    monkeypatch.setattr(launcher_support.shutil, "which", lambda name: "/usr/bin/cursor")

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


def test_clear_launcher_widget_keys_clears_removed_launcher_state(
    monkeypatch,
) -> None:
    session_state = {
        "launcher_label_cursor": "Cursor",
        "launcher_command_cursor": "cursor",
        "launcher_args_cursor": "{path}",
        "launcher_label_zed": "Zed",
        "launcher_terminal_zed": False,
        "unrelated_key": "keep-me",
    }
    monkeypatch.setattr(
        settings_editors.st,
        "session_state",
        session_state,
        raising=False,
    )

    settings_editors._clear_launcher_widget_keys(["cursor", "zed"])

    assert "launcher_label_cursor" not in session_state
    assert "launcher_command_cursor" not in session_state
    assert "launcher_args_cursor" not in session_state
    assert "launcher_label_zed" not in session_state
    assert "launcher_terminal_zed" not in session_state
    assert session_state["unrelated_key"] == "keep-me"


def test_detail_not_found_renders_back_to_overview_cta(monkeypatch) -> None:
    from armillary.ui import sidebar

    monkeypatch.setattr(sidebar, "_render_nav_sidebar", lambda: None)
    monkeypatch.setattr(detail, "_load_project", lambda _path: None)

    errors: list[tuple[str, str | None]] = []
    buttons: list[str] = []
    navigations: list[str] = []

    def fake_error(message: str, *, icon: str | None = None) -> None:
        errors.append((message, icon))

    def fake_button(label: str, **_kwargs) -> bool:
        buttons.append(label)
        return True

    monkeypatch.setattr(detail.st, "error", fake_error)
    monkeypatch.setattr(detail.st, "button", fake_button)
    monkeypatch.setattr(detail, "go_to_overview", lambda: navigations.append("overview"))

    detail._render_project_detail("/tmp/missing-project")

    assert any("not found in cache" in message for message, _ in errors)
    assert buttons == ["Back to overview"]
    assert navigations == ["overview"]
