"""Headless orchestration of Claude Code to auto-fill .revive/static.md.

armillary acts as a control panel for revive: when the user clicks
"Generate brief" on a project, this module spawns the `claude` CLI
(headless mode) in that project's directory with the prompt produced
by `revive suggest`. Claude reads the repo and edits static.md in
place. We capture before/after snapshots to produce a diff for the
UI to confirm before the change is finalized.

Hard guardrails (panel-mandated, do not bypass):
1. Refuse to run on a dirty git working tree.
2. Always create a .revive/static.md.bak before invocation; the
   public accept/reject helpers manage its lifecycle.
3. Constrain Claude to Read/Edit/Write tools only and cap turns.
"""

from __future__ import annotations

import difflib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SkippedReason = str | None


@dataclass(frozen=True)
class GenerationResult:
    success: bool
    diff: str
    before: str
    after: str
    error: str | None
    skipped_reason: SkippedReason


def claude_available() -> bool:
    """Return True when the `claude` CLI is available on PATH."""
    return shutil.which("claude") is not None


def git_is_clean(project_path: Path) -> bool:
    """Return True only for repos with an empty `git status --porcelain`."""
    if not (project_path / ".git").is_dir():
        return False
    try:
        result = subprocess.run(  # noqa: S603 - args list, no shell
            ["git", "status", "--porcelain"],
            cwd=project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    return result.returncode == 0 and result.stdout == ""


def generate_brief(
    project_path: Path,
    *,
    timeout: float = 180.0,
    max_turns: int = 10,
) -> GenerationResult:
    """Run `revive suggest` and a restricted Claude session for one project."""
    project_path = project_path.resolve()
    if not claude_available():
        return _skipped("claude_missing")

    revive_binary = shutil.which("revive")
    if revive_binary is None:
        return _skipped("revive_missing")

    if not (project_path / ".git").is_dir():
        return _skipped("not_git_repo")
    if not git_is_clean(project_path):
        return _skipped("dirty_tree")

    static_path = project_path / ".revive" / "static.md"
    backup_path = static_path.with_name("static.md.bak")
    was_missing_marker = static_path.with_name("static.md.was_missing")
    static_existed_originally = static_path.exists()

    if not static_existed_originally:
        # `revive suggest` refuses to run without a scaffolded static.md.
        # `revive init` is a pure file-op (auto-extracts PURPOSE from
        # README/manifest, no LLM call) so we can run it transparently
        # before the suggest+claude pass for projects in "missing" state.
        try:
            init_result = subprocess.run(  # noqa: S603 - args list, no shell
                [revive_binary, "init"],
                cwd=project_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            return _failed(
                before="", after="", diff="", error=f"revive init failed: {exc}"
            )
        if init_result.returncode != 0:
            return _failed(
                before="",
                after="",
                diff="",
                error=f"revive init failed: {init_result.stderr.strip()}",
            )
        # Drop a marker so reject_proposal() can roll the project back to
        # its missing state. Without this, reject would leave Claude's
        # filled static.md on disk because the absent .bak file makes the
        # restore helper think there is nothing to do.
        try:
            was_missing_marker.parent.mkdir(parents=True, exist_ok=True)
            was_missing_marker.touch()
        except OSError as exc:
            return _failed(
                before="",
                after="",
                diff="",
                error=f"failed to write was_missing marker: {exc}",
            )

    had_before = static_path.exists()
    try:
        before = static_path.read_text(encoding="utf-8") if had_before else ""
    except OSError as exc:
        return _failed(
            before="", after="", diff="", error=f"failed to read static.md: {exc}"
        )

    try:
        prompt_result = subprocess.run(  # noqa: S603 - args list, no shell
            [revive_binary, "suggest"],
            cwd=project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return _failed(
            before=before,
            after=before,
            diff="",
            error=f"revive suggest failed: {exc}",
        )
    if prompt_result.returncode != 0:
        return _failed(
            before=before,
            after=before,
            diff="",
            error=f"revive suggest failed: {prompt_result.stderr.strip()}",
        )

    # Only back up when there was a real prior state. If `revive init`
    # just scaffolded the file in this run, there is no original to
    # restore — reject leaves the scaffold in place; users can `rm -rf
    # .revive/` to fully revert.
    if static_existed_originally:
        try:
            shutil.copyfile(static_path, backup_path)
        except OSError as exc:
            return _failed(
                before=before, after=before, diff="", error=f"backup failed: {exc}"
            )

    turn_limit = min(max_turns, 10)
    try:
        claude_result = subprocess.run(  # noqa: S603 - args list, no shell
            [
                "claude",
                "-p",
                prompt_result.stdout,
                "--allowed-tools",
                "Read,Edit,Write",
                "--max-turns",
                str(turn_limit),
            ],
            cwd=project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _restore_original(static_path, backup_path, had_before)
        return _failed(
            before=before,
            after=before if had_before else "",
            diff="",
            error=f"claude timed out after {timeout}s",
        )
    except (FileNotFoundError, OSError) as exc:
        _restore_original(static_path, backup_path, had_before)
        return _failed(
            before=before,
            after=before if had_before else "",
            diff="",
            error=f"claude failed: {exc}",
        )

    if claude_result.returncode != 0:
        _restore_original(static_path, backup_path, had_before)
        stderr_tail = claude_result.stderr.strip()
        return _failed(
            before=before,
            after=before if had_before else "",
            diff="",
            error=f"claude exited with {claude_result.returncode}: {stderr_tail}",
        )

    try:
        after = static_path.read_text(encoding="utf-8") if static_path.exists() else ""
    except OSError as exc:
        _restore_original(static_path, backup_path, had_before)
        return _failed(
            before=before,
            after=before if had_before else "",
            diff="",
            error=f"failed to read static.md: {exc}",
        )

    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
        )
    )
    if after == before:
        # Claude made no edits. There is nothing to accept or reject, so
        # drop any leftover backup or missing-marker — the project state
        # is unchanged from the moment generate_brief was called (modulo
        # an init scaffold, which we leave on disk so the user can keep
        # using `revive` without rerunning init).
        if backup_path.exists():
            backup_path.unlink()
        if was_missing_marker.exists():
            was_missing_marker.unlink()
    return GenerationResult(
        success=True,
        diff=diff,
        before=before,
        after=after,
        error=None,
        skipped_reason=None,
    )


def accept_proposal(project_path: Path) -> None:
    """Discard rollback artefacts after the user accepts a proposal.

    Deletes both the static.md backup and the missing-marker if
    present. Idempotent — safe to call when neither exists.
    """
    project_path = project_path.resolve()
    static_path = project_path / ".revive" / "static.md"
    backup_path = static_path.with_name("static.md.bak")
    marker_path = static_path.with_name("static.md.was_missing")
    if backup_path.exists():
        backup_path.unlink()
    if marker_path.exists():
        marker_path.unlink()


def reject_proposal(project_path: Path) -> None:
    """Roll back the most recent generate_brief() write.

    Two paths:
    - If the project had a real `.revive/static.md` before the run, the
      backup was written; restore from it and delete the backup.
    - If the project was originally missing (the missing-marker exists),
      delete the freshly scaffolded static.md and any stray backup so
      the project returns to its pre-run state.

    Idempotent if neither marker nor backup is present.
    """
    project_path = project_path.resolve()
    static_path = project_path / ".revive" / "static.md"
    backup_path = static_path.with_name("static.md.bak")
    marker_path = static_path.with_name("static.md.was_missing")
    if marker_path.exists():
        if static_path.exists():
            static_path.unlink()
        if backup_path.exists():
            backup_path.unlink()
        marker_path.unlink()
        return
    if not backup_path.exists():
        return
    shutil.copyfile(backup_path, static_path)
    backup_path.unlink()


def _skipped(reason: str) -> GenerationResult:
    return GenerationResult(
        success=False,
        diff="",
        before="",
        after="",
        error=None,
        skipped_reason=reason,
    )


def _failed(*, before: str, after: str, diff: str, error: str) -> GenerationResult:
    return GenerationResult(
        success=False,
        diff=diff,
        before=before,
        after=after,
        error=error,
        skipped_reason=None,
    )


def _restore_original(static_path: Path, backup_path: Path, had_before: bool) -> None:
    if had_before and backup_path.exists():
        shutil.copyfile(backup_path, static_path)
        backup_path.unlink()
        return
    if static_path.exists():
        static_path.unlink()
    # Clean up the missing-marker too if the run is rolling back; the
    # project is fully reset to "no .revive/static.md".
    marker = static_path.with_name("static.md.was_missing")
    if marker.exists():
        marker.unlink()


# [EDGE_CASE][medium] Concurrent generate_brief calls race on static.md
#   and static.md.bak.
# [EDGE_CASE][low] An existing static.md.bak is overwritten when a new
#   backup is created.
# [EDGE_CASE][medium] Claude may edit files outside static.md; v1 only
#   snapshots static.md.
# [EDGE_CASE][low] static.md is read as UTF-8 and may preserve non-ASCII
#   or a BOM verbatim.
# [EDGE_CASE][low] project_path is normalized with Path.resolve() once at
#   function entry.
