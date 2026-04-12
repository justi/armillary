"""Subprocess-based launcher for opening projects in external tools.

The launcher is a thin wrapper around `subprocess.Popen` that:

1. Looks up a `LauncherConfig` by id (e.g. `"cursor"`, `"vscode"`).
2. Resolves how that launcher can be started:
   - preferred: executable on PATH via `shutil.which()`
   - macOS fallback: known GUI app bundle like `/Applications/Cursor.app`
3. Substitutes `{path}` in the args with the project's resolved path.
4. Spawns the process with `cwd=project.path` so the launched tool
   opens in the right working directory.
5. Returns a `LaunchResult` describing what happened — never raises
   for a "user-fixable" error like a missing binary, so callers can
   surface the message in the UI without try/except gymnastics.

There is intentionally no shell invocation, no string concatenation,
no env-var injection beyond the parent process. The list-of-args
form is a deliberate guard against shell-injection bugs reaching
production.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import LauncherConfig
from .models import Project


@dataclass(frozen=True)
class LaunchResult:
    """What happened when we tried to launch a project.

    `ok=True` means the subprocess started; we do not wait for it to
    finish, so a slow editor opening in a new window still counts as
    success. `ok=False` carries `error` for the user-facing message.
    """

    ok: bool
    target: str
    project_path: Path
    command: list[str] | None = None
    error: str | None = None


@dataclass(frozen=True)
class LauncherAvailability:
    """How a launcher can currently be started on this machine."""

    available: bool
    mode: str  # "path" | "macos-app" | "missing"
    detail: str | None = None
    app_name: str | None = None


_MACOS_APP_FALLBACKS = {
    "cursor": "Cursor",
    "code": "Visual Studio Code",
    "zed": "Zed",
}


def launch(
    project: Project,
    target: str,
    *,
    launchers: dict[str, LauncherConfig],
) -> LaunchResult:
    """Open `project` in the launcher identified by `target`.

    `launchers` is the catalogue from `Config.launchers`. The function
    refuses to run anything not in that catalogue, so users cannot
    smuggle arbitrary commands through the URL / CLI by passing
    `--target some-shell-string`.
    """
    config = launchers.get(target)
    if config is None:
        return LaunchResult(
            ok=False,
            target=target,
            project_path=project.path,
            error=f"Launcher '{target}' is not configured.",
        )

    availability = detect_launcher(config)
    if not availability.available:
        return LaunchResult(
            ok=False,
            target=target,
            project_path=project.path,
            error=(
                f"Launcher '{config.label}' needs either the "
                f"`{config.command}` executable on your PATH"
                + (
                    f" or the macOS app '{availability.app_name}'."
                    if availability.app_name
                    else "."
                )
                + " It was not found."
            ),
        )

    cmd = _resolve_command(config, project.path, availability)

    try:
        if config.terminal:
            # Interactive terminal apps (codex, claude-code, vim, ...)
            # need the parent's stdio inherited so the user can actually
            # talk to them. We use `subprocess.run` (which waits for the
            # child) rather than detaching, because spawning an
            # interactive TTY app in the background just leaves it
            # invisible.
            subprocess.run(  # noqa: S603 — args list, no shell, trusted catalogue
                cmd,
                cwd=str(project.path),
                check=False,
            )
        else:
            # GUI launchers: detach completely so closing the terminal
            # does not kill the editor, and silence stdio (no GUI cares
            # about it).
            subprocess.Popen(  # noqa: S603 — args list, no shell, trusted catalogue
                cmd,
                cwd=str(project.path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except (OSError, ValueError) as exc:
        return LaunchResult(
            ok=False,
            target=target,
            project_path=project.path,
            command=cmd,
            error=f"Failed to spawn {config.command}: {exc}",
        )

    return LaunchResult(
        ok=True,
        target=target,
        project_path=project.path,
        command=cmd,
    )


def detect_launcher(config: LauncherConfig) -> LauncherAvailability:
    """Return whether `config` can be launched on this machine.

    GUI launchers on macOS get a fallback path: if their CLI shim is not
    installed on PATH but the corresponding `.app` bundle is present,
    armillary can still launch them via `open -a`.
    """
    resolved = shutil.which(config.command)
    if resolved is not None:
        return LauncherAvailability(
            available=True,
            mode="path",
            detail=resolved,
        )

    app_name = _macos_app_fallback_name(config)
    if app_name is not None:
        app_bundle = _find_macos_app_bundle(app_name)
        if app_bundle is not None:
            return LauncherAvailability(
                available=True,
                mode="macos-app",
                detail=str(app_bundle),
                app_name=app_name,
            )
        return LauncherAvailability(
            available=False,
            mode="missing",
            app_name=app_name,
        )

    return LauncherAvailability(
        available=False,
        mode="missing",
    )


def _build_command(config: LauncherConfig, project_path: Path) -> list[str]:
    """Substitute `{path}` in each arg with the project's resolved path."""
    path_str = str(project_path)
    return [config.command, *[arg.replace("{path}", path_str) for arg in config.args]]


def _resolve_command(
    config: LauncherConfig,
    project_path: Path,
    availability: LauncherAvailability,
) -> list[str]:
    """Build the actual command list for the chosen launcher runtime."""
    if availability.mode == "macos-app":
        assert availability.app_name is not None
        return ["open", "-a", availability.app_name, str(project_path)]
    return _build_command(config, project_path)


def _macos_app_fallback_name(config: LauncherConfig) -> str | None:
    """Known macOS GUI-app fallback for built-in launcher shapes only."""
    if sys.platform != "darwin" or config.terminal:
        return None
    if config.args != ["{path}"]:
        return None
    return _MACOS_APP_FALLBACKS.get(config.command)


def _find_macos_app_bundle(app_name: str) -> Path | None:
    """Return the `.app` bundle path if installed in a standard location."""
    for base in (Path("/Applications"), Path.home() / "Applications"):
        candidate = base / f"{app_name}.app"
        if candidate.exists():
            return candidate
    return None
