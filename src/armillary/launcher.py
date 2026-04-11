"""Subprocess-based launcher for opening projects in external tools.

The launcher is a thin wrapper around `subprocess.Popen` that:

1. Looks up a `LauncherConfig` by id (e.g. `"cursor"`, `"vscode"`).
2. Verifies the executable exists on PATH via `shutil.which()`.
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

    if shutil.which(config.command) is None:
        return LaunchResult(
            ok=False,
            target=target,
            project_path=project.path,
            error=(
                f"Launcher '{config.label}' needs the `{config.command}` "
                "executable on your PATH, but it was not found."
            ),
        )

    cmd = _build_command(config, project.path)

    try:
        # Detach from the parent so closing the terminal does not kill
        # the editor. We don't capture stdout/stderr — most launchers
        # are GUIs that don't print anything useful anyway.
        subprocess.Popen(  # noqa: S603 — args list, no shell, command from trusted catalogue
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


def _build_command(config: LauncherConfig, project_path: Path) -> list[str]:
    """Substitute `{path}` in each arg with the project's resolved path."""
    path_str = str(project_path)
    return [config.command, *[arg.replace("{path}", path_str) for arg in config.args]]
