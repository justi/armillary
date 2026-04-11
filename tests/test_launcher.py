"""Tests for the subprocess-based launcher.

We never spawn real editors. Each test monkeypatches `subprocess.Popen`
and `shutil.which` to verify that:

- the right command list is constructed
- {path} substitution happens
- cwd is set to the project's directory
- missing executables produce a `LaunchResult(ok=False, ...)` instead
  of crashing
- unknown launcher ids are rejected before any subprocess is touched
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from armillary import launcher
from armillary.config import LauncherConfig
from armillary.models import Project, ProjectType


def _project(path: Path) -> Project:
    return Project(
        path=path.resolve(),
        name=path.name,
        type=ProjectType.GIT,
        umbrella=path.parent.resolve(),
        last_modified=datetime.now(),
    )


@pytest.fixture
def launchers() -> dict[str, LauncherConfig]:
    return {
        "cursor": LauncherConfig(
            label="Cursor",
            command="cursor",
            args=["{path}"],
        ),
        "vscode": LauncherConfig(
            label="VS Code",
            command="code",
            args=["{path}"],
        ),
        "terminal": LauncherConfig(
            label="Terminal",
            command="open",
            args=["-a", "Terminal", "{path}"],
        ),
    }


# --- happy path -----------------------------------------------------------


def test_launch_substitutes_path_and_sets_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launchers: dict[str, LauncherConfig],
) -> None:
    project = _project(tmp_path / "alpha")
    project.path.mkdir(parents=True, exist_ok=True)

    captured: dict[str, Any] = {}

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    class FakePopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

    monkeypatch.setattr(launcher.shutil, "which", fake_which)
    monkeypatch.setattr(launcher.subprocess, "Popen", FakePopen)

    result = launcher.launch(project, "cursor", launchers=launchers)

    assert result.ok is True
    assert result.target == "cursor"
    assert result.error is None
    assert captured["cmd"] == ["cursor", str(project.path)]
    assert captured["kwargs"]["cwd"] == str(project.path)
    # Defensive: never use shell=True or merge stdin/stdout
    assert captured["kwargs"]["start_new_session"] is True


def test_launch_handles_multi_arg_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launchers: dict[str, LauncherConfig],
) -> None:
    project = _project(tmp_path / "thing")
    project.path.mkdir(parents=True, exist_ok=True)

    captured: dict[str, Any] = {}

    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda cmd, **kwargs: captured.setdefault("cmd", cmd),
    )

    result = launcher.launch(project, "terminal", launchers=launchers)
    assert result.ok is True
    assert captured["cmd"] == ["open", "-a", "Terminal", str(project.path)]


# --- error paths ----------------------------------------------------------


def test_launch_unknown_target_returns_error(
    tmp_path: Path, launchers: dict[str, LauncherConfig]
) -> None:
    project = _project(tmp_path / "x")
    result = launcher.launch(project, "fictional-editor", launchers=launchers)
    assert result.ok is False
    assert result.error is not None
    assert "fictional-editor" in result.error
    assert "not configured" in result.error


def test_launch_missing_executable_returns_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launchers: dict[str, LauncherConfig],
) -> None:
    project = _project(tmp_path / "x")

    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)

    def must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Popen called despite missing executable")

    monkeypatch.setattr(launcher.subprocess, "Popen", must_not_run)

    result = launcher.launch(project, "cursor", launchers=launchers)
    assert result.ok is False
    assert result.error is not None
    assert "cursor" in result.error.lower()
    assert "not found" in result.error.lower()


def test_launch_popen_failure_is_caught(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launchers: dict[str, LauncherConfig],
) -> None:
    project = _project(tmp_path / "x")
    project.path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("simulated permission denied")

    monkeypatch.setattr(launcher.subprocess, "Popen", boom)

    result = launcher.launch(project, "cursor", launchers=launchers)
    assert result.ok is False
    assert result.error is not None
    assert "permission denied" in result.error.lower()


# --- terminal launchers ---------------------------------------------------


def test_terminal_launcher_uses_run_with_inherited_stdio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex / Claude Code style: keep parent stdio attached.

    Regression for Codex review P1: launching `codex` or `claude-code`
    with detached stdio leaves them invisible. The launcher must use
    `subprocess.run` (waiting + inherited stdio) for `terminal=True`
    entries.
    """
    project = _project(tmp_path / "thing")
    project.path.mkdir(parents=True, exist_ok=True)

    captured: dict[str, Any] = {}

    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

        class _R:
            returncode = 0

        return _R()

    def must_not_popen(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Popen called for a terminal launcher")

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(launcher.subprocess, "Popen", must_not_popen)

    terminal_launchers = {
        "codex": LauncherConfig(
            label="Codex",
            command="codex",
            args=[],
            terminal=True,
        ),
    }
    result = launcher.launch(project, "codex", launchers=terminal_launchers)

    assert result.ok is True
    assert captured["cmd"] == ["codex"]
    # subprocess.run does NOT redirect stdio, so the user can interact
    assert "stdin" not in captured["kwargs"]
    assert "stdout" not in captured["kwargs"]
    assert "stderr" not in captured["kwargs"]
    assert captured["kwargs"]["cwd"] == str(project.path)


def test_gui_launcher_still_uses_popen_with_devnull(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launchers: dict[str, LauncherConfig],
) -> None:
    """Sanity: GUI launchers (terminal=False, the default) keep using
    Popen + DEVNULL + start_new_session so they detach properly."""
    project = _project(tmp_path / "x")
    project.path.mkdir(parents=True, exist_ok=True)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_popen(cmd: list[str], **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs

    def must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess.run called for a GUI launcher")

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher.subprocess, "run", must_not_run)

    launcher.launch(project, "cursor", launchers=launchers)
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["stdin"] is launcher.subprocess.DEVNULL


def test_launch_never_uses_shell_string(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launchers: dict[str, LauncherConfig],
) -> None:
    """Defense-in-depth: the command must be a list, never a string."""
    project = _project(tmp_path / "x")
    project.path.mkdir(parents=True, exist_ok=True)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda cmd, **kwargs: captured.setdefault("cmd", cmd),
    )

    launcher.launch(project, "cursor", launchers=launchers)
    assert isinstance(captured["cmd"], list)
    # And no `shell=True` smuggled into kwargs
    assert "shell" not in {
        k for k, v in (captured.get("kwargs", {}) or {}).items() if v
    }
