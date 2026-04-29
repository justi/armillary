"""Integration with the external `revive` tool (context-revive).

revive is an optional companion binary. armillary surfaces revive state
(per-project + capability) so users can see at a glance which projects
have a revive brief configured and trigger generation/install actions.

We never wrap business logic of revive — only call its CLI and parse
file artifacts. If revive is missing or incompatible, every read-only
function returns a "neutral" status without raising; only the explicit
action functions (revive_show, install_hook_global) raise ReviveError.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BriefState = Literal["configured", "stub", "placeholder", "missing", "unknown"]
HookScope = Literal["none", "project", "global", "both"]

_REQUIRED_SUBCOMMANDS = ("show", "install-hook", "doctor")
_PLACEHOLDER_MARKER = "run `revive init`"
_PURPOSE_RE = re.compile(r"^PURPOSE:\s*(.+)$", re.MULTILINE)
_VERSION_RE = re.compile(r"\b\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.]+)?\b")


class ReviveError(RuntimeError):
    """Raised by explicit action calls on underlying revive CLI failure."""


@dataclass(frozen=True)
class ReviveCapability:
    binary_available: bool
    binary_path: Path | None
    compatible: bool
    version: str | None


@dataclass(frozen=True)
class ReviveStatus:
    static_exists: bool
    brief_state: BriefState
    purpose_line: str | None
    static_path: Path | None
    hook_scope: HookScope


@functools.cache
def probe_capability() -> ReviveCapability:
    """Probe revive availability and basic CLI compatibility once per process."""
    binary = shutil.which("revive")
    if binary is None:
        return ReviveCapability(
            binary_available=False,
            binary_path=None,
            compatible=False,
            version=None,
        )

    binary_path = Path(binary)
    try:
        result = subprocess.run(  # noqa: S603 - args list, no shell
            [binary, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ReviveCapability(
            binary_available=True,
            binary_path=binary_path,
            compatible=False,
            version=None,
        )

    help_text = f"{result.stdout}{result.stderr}"
    compatible = result.returncode == 0 and all(
        subcommand in help_text for subcommand in _REQUIRED_SUBCOMMANDS
    )
    version = _extract_version(help_text)
    return ReviveCapability(
        binary_available=True,
        binary_path=binary_path,
        compatible=compatible,
        version=version,
    )


def project_status(project_path: Path, *, home: Path | None = None) -> ReviveStatus:
    """Inspect project-local revive artifacts without invoking the CLI."""
    static_path = project_path / ".revive" / "static.md"
    hook_scope = _hook_scope(project_path, home or Path.home())

    if not static_path.exists():
        return ReviveStatus(
            static_exists=False,
            brief_state="missing",
            purpose_line=None,
            static_path=None,
            hook_scope=hook_scope,
        )

    try:
        content = static_path.read_text(encoding="utf-8")
    except OSError:
        return ReviveStatus(
            static_exists=True,
            brief_state="unknown",
            purpose_line=None,
            static_path=static_path,
            hook_scope=hook_scope,
        )

    purpose_line = _purpose_line(content)
    if purpose_line is None:
        brief_state: BriefState = "unknown"
    elif _PLACEHOLDER_MARKER in purpose_line:
        brief_state = "placeholder"
    elif not _section_has_bullets(content, "INVARIANTS") and not _section_has_bullets(
        content, "GOTCHAS"
    ):
        # `revive init` ran (PURPOSE auto-extracted from README), but the
        # follow-up `revive suggest` LLM step that fills INVARIANTS and
        # GOTCHAS was never completed. Treat as a stub so the UI can
        # nudge the user toward the missing step.
        brief_state = "stub"
    else:
        brief_state = "configured"

    return ReviveStatus(
        static_exists=True,
        brief_state=brief_state,
        purpose_line=purpose_line,
        static_path=static_path,
        hook_scope=hook_scope,
    )


def revive_show(project_path: Path, *, timeout: float = 5.0) -> str:
    """Run `revive show` for a project and return its brief output."""
    binary = shutil.which("revive")
    if binary is None:
        raise ReviveError("revive binary not found on PATH")

    try:
        result = subprocess.run(  # noqa: S603 - args list, no shell
            [binary, "show"],
            cwd=project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviveError(f"revive show timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise ReviveError("revive binary not found on PATH") from exc
    except OSError as exc:
        raise ReviveError(f"revive show failed: {exc}") from exc

    if result.returncode != 0:
        raise ReviveError(f"revive show failed: {result.stderr.strip()}")
    return result.stdout


def install_hook_global(*, timeout: float = 10.0) -> tuple[bool, str]:
    """Install the revive global hook and return `(success, combined_output)`."""
    binary = shutil.which("revive")
    if binary is None:
        return False, "revive binary not found on PATH"

    try:
        result = subprocess.run(  # noqa: S603 - args list, no shell
            [binary, "install-hook", "--global"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"revive install-hook --global timed out after {timeout}s"
    except FileNotFoundError:
        return False, "revive binary not found on PATH"
    except OSError as exc:
        return False, f"revive install-hook --global failed: {exc}"

    combined_output = f"{result.stdout}{result.stderr}"
    return result.returncode == 0, combined_output


def generate_suggest_prompt(project_path: Path, *, timeout: float = 10.0) -> str:
    """Run `revive suggest` and return its stdout (an LLM-ready prompt).

    The user pastes this into a fresh Claude Code session in the target
    project to fill INVARIANTS / GOTCHAS interactively. Raises
    ``ReviveError`` on missing binary, timeout, or nonzero exit.
    """
    return _run_prompt_command(project_path, "suggest", timeout=timeout)


def generate_audit_prompt(project_path: Path, *, timeout: float = 10.0) -> str:
    """Run `revive audit` and return its stdout.

    Audit is the second-pass gap check. revive's design requires it to
    run in a *fresh* agent session so the clean context window can
    surface non-inferable facts the suggest pass missed. The user pastes
    the returned prompt into a new Claude Code session.
    """
    return _run_prompt_command(project_path, "audit", timeout=timeout)


def run_revive_init(project_path: Path, *, timeout: float = 10.0) -> tuple[bool, str]:
    """Run `revive init` to scaffold .revive/static.md from README/manifest.

    Pure file operation (no LLM). Returns ``(success, combined_output)``.
    Use this to bring a project from "missing" to "placeholder" state
    before generating a suggest prompt.
    """
    binary = shutil.which("revive")
    if binary is None:
        return False, "revive binary not found on PATH"
    try:
        result = subprocess.run(  # noqa: S603 - args list, no shell
            [binary, "init"],
            cwd=project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"revive init timed out after {timeout}s"
    except FileNotFoundError:
        return False, "revive binary not found on PATH"
    except OSError as exc:
        return False, f"revive init failed: {exc}"
    combined_output = f"{result.stdout}{result.stderr}"
    return result.returncode == 0, combined_output


def _run_prompt_command(project_path: Path, subcommand: str, *, timeout: float) -> str:
    binary = shutil.which("revive")
    if binary is None:
        raise ReviveError("revive binary not found on PATH")
    try:
        result = subprocess.run(  # noqa: S603 - args list, no shell
            [binary, subcommand],
            cwd=project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviveError(f"revive {subcommand} timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise ReviveError("revive binary not found on PATH") from exc
    except OSError as exc:
        raise ReviveError(f"revive {subcommand} failed: {exc}") from exc
    if result.returncode != 0:
        raise ReviveError(f"revive {subcommand} failed: {result.stderr.strip()}")
    return result.stdout


def generate_suggest_prompts(
    project_paths: list[Path], *, output_dir: Path, timeout: float = 5.0
) -> list[Path]:
    """Write `revive suggest` output for git repos into `output_dir`."""
    binary = shutil.which("revive")
    if binary is None:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for project_path in project_paths:
        if not (project_path / ".git").exists():
            continue
        try:
            result = subprocess.run(  # noqa: S603 - args list, no shell
                [binary, "suggest"],
                cwd=project_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
        if result.returncode != 0:
            continue

        output_path = output_dir / _slug_for_path(project_path)
        try:
            output_path.write_text(result.stdout, encoding="utf-8")
        except OSError:
            continue
        written.append(output_path)
    return written


def _extract_version(text: str) -> str | None:
    first_line = text.splitlines()[0] if text else ""
    match = _VERSION_RE.search(first_line)
    return match.group(0) if match else None


def _section_has_bullets(content: str, section_name: str) -> bool:
    """Return True if SECTION_NAME has at least one bullet line beneath it.

    Detects the `revive init` stub state: a header like ``INVARIANTS:``
    is present but no indented ``  - `` bullets follow before the next
    section header. Tolerates blank lines between header and bullets.
    """
    in_section = False
    for raw in content.splitlines():
        line = raw.rstrip()
        if not in_section:
            if line.strip() == f"{section_name}:":
                in_section = True
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith("# "):
            return False
        # Section header (e.g. "GOTCHAS:") at column 0: end of section.
        if not line.startswith(" ") and stripped.endswith(":"):
            return False
        if stripped.startswith(("-", "*")):
            return True
    return False


def _purpose_line(content: str) -> str | None:
    match = _PURPOSE_RE.search(content)
    if match is None:
        return None
    purpose = match.group(1).strip()
    return purpose or None


def _hook_scope(project_path: Path, home: Path) -> HookScope:
    project_has_hook = _has_revive_hook(project_path / ".claude" / "settings.json")
    global_has_hook = _has_revive_hook(home / ".claude" / "settings.json")
    if project_has_hook and global_has_hook:
        return "both"
    if project_has_hook:
        return "project"
    if global_has_hook:
        return "global"
    return "none"


def _has_revive_hook(settings_path: Path) -> bool:
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    entries = data.get("hooks", {}).get("UserPromptSubmit", [])
    if not isinstance(entries, list):
        return False

    for entry in entries:
        hooks = entry.get("hooks", []) if isinstance(entry, dict) else []
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            command = hook.get("command") if isinstance(hook, dict) else None
            if isinstance(command, str) and "revive" in command:
                return True
    return False


def _slug_for_path(project_path: Path) -> str:
    digest = hashlib.sha256(str(project_path).encode("utf-8")).hexdigest()[:8]
    return f"{project_path.name}-{digest}.md"


# [EDGE_CASE][low] output_dir creation uses exist_ok=True, so concurrent
# mkdir is benign.
# [EDGE_CASE][low] subprocess args are always passed as lists; shell
# metacharacters in paths are inert.
# [EDGE_CASE][low] UTF-8 text mode is explicit for revive output;
# unexpected encoding still fails at the CLI boundary.
# [EDGE_CASE][low] probe_capability is cached for the process lifetime
# and may become stale if revive is installed or removed mid-process.
# [EDGE_CASE][low] .revive/static.md symlinks are followed for read-only inspection.
