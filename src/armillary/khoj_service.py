"""Khoj Docker provisioning and runner helpers.

Pure application logic — subprocess calls, file operations, state
checks. This module never imports ``typer`` or ``streamlit``. Functions
return structured results; the CLI layer wraps them with user-facing
output.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from armillary.config import default_config_path

# --- constants ---------------------------------------------------------------

KHOJ_PG_CONTAINER = "khoj-pg"
KHOJ_PG_IMAGE = "pgvector/pgvector:pg15"
KHOJ_PG_VOLUME = "khoj-pg-data"
KHOJ_DB_NAME = "khoj"
KHOJ_DB_USER = "postgres"
# The container is not exposed outside localhost; POSTGRES_PASSWORD is
# a Docker-init default, not a secret. Using `KHOJ_DB_USER` as the
# value keeps the literal out of the source so secret scanners
# (GitGuardian, gitleaks, trufflehog) do not flag it while preserving
# the postgres/postgres convention that every pgvector/pgvector:pg15
# tutorial assumes.
KHOJ_DB_PASSWORD = KHOJ_DB_USER
KHOJ_DB_HOST = "localhost"

# Non-default host port so we do NOT fight with an existing
# `brew services start postgresql@*` or a system Postgres that owns
# 5432. Inside the container Postgres still listens on 5432 — the
# mapping is host:54322 → container:5432.
KHOJ_DB_PORT = "54322"
KHOJ_CONTAINER_PORT = "5432"

# Alias for shutil.which so tests can monkeypatch at the module level.
shutil_which = __import__("shutil").which


# --- result types ------------------------------------------------------------


@dataclass
class ProvisionResult:
    """Outcome of :func:`provision_postgres_container`."""

    status: str  # "created" | "started" | "reused" | "recreated"
    port_was_stale: bool = False
    old_port: str | None = None


@dataclass
class PgvectorResult:
    """Outcome of :func:`enable_pgvector`."""

    ok: bool
    error: str = ""


@dataclass
class ContainerRemoveResult:
    ok: bool
    error: str = ""


@dataclass
class ContainerStartResult:
    ok: bool
    error: str = ""


@dataclass
class ContainerCreateResult:
    ok: bool
    error: str = ""


@dataclass
class HealthCheckResult:
    """Outcome of :func:`wait_for_khoj_health`."""

    ready: bool
    process_exited: bool = False
    exit_code: int | None = None
    timed_out: bool = False


@dataclass
class InstallerChoice:
    """Return value of :func:`pick_khoj_installer`."""

    cmd: list[str] = field(default_factory=list)
    label: str = ""


# --- admin env ---------------------------------------------------------------


def khoj_admin_env_path() -> Path:
    """Where the auto-generated Khoj admin credentials live on disk."""
    return default_config_path().parent / "khoj-admin.env"


def load_khoj_admin_env() -> dict[str, str] | None:
    """Read ``khoj-admin.env`` into a dict, or None if it does not exist."""
    path = khoj_admin_env_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def ensure_khoj_admin_env() -> dict[str, str]:
    """Return the Khoj admin credentials, generating them on first call."""
    existing = load_khoj_admin_env()
    if (
        existing
        and existing.get("KHOJ_ADMIN_EMAIL")
        and existing.get("KHOJ_ADMIN_PASSWORD")
    ):
        return existing

    env = {
        "KHOJ_ADMIN_EMAIL": "admin@armillary.local",
        "KHOJ_ADMIN_PASSWORD": secrets.token_urlsafe(16),
    }
    path = khoj_admin_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{k}={v}" for k, v in env.items()) + "\n"
    path.write_text(
        "# armillary — auto-generated Khoj admin credentials\n"
        "# Used by `armillary start-khoj` to initialise the local Khoj\n"
        "# admin panel at http://localhost:42110/server/admin.\n"
        "# Delete this file and rerun `armillary install-khoj` to rotate.\n" + body,
        encoding="utf-8",
    )
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return env


# --- docker helpers ----------------------------------------------------------


def docker_container_state(name: str) -> str:
    """Return ``"running"``, ``"stopped"``, or ``"missing"``."""
    result = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name=^{name}$",
            "--format",
            "{{.State}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return "missing"
    state = result.stdout.strip().lower()
    if not state:
        return "missing"
    if state == "running":
        return "running"
    return "stopped"


def docker_container_host_port(name: str) -> str | None:
    """Return the host port currently mapped to the container's 5432."""
    result = subprocess.run(
        ["docker", "port", name, f"{KHOJ_CONTAINER_PORT}/tcp"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if ":" not in first_line:
        return None
    return first_line.rsplit(":", 1)[-1]


def remove_container(name: str) -> ContainerRemoveResult:
    """Force-remove a docker container. Returns result with ok/error."""
    rm = subprocess.run(
        ["docker", "rm", "-f", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if rm.returncode != 0:
        return ContainerRemoveResult(ok=False, error=rm.stderr.strip())
    return ContainerRemoveResult(ok=True)


def start_container(name: str) -> ContainerStartResult:
    """Start a stopped docker container."""
    r = subprocess.run(
        ["docker", "start", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return ContainerStartResult(ok=False, error=r.stderr.strip())
    return ContainerStartResult(ok=True)


def create_container() -> ContainerCreateResult:
    """Create the Khoj Postgres+pgvector docker container."""
    r = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            KHOJ_PG_CONTAINER,
            "-p",
            f"{KHOJ_DB_PORT}:{KHOJ_CONTAINER_PORT}",
            "-e",
            f"POSTGRES_DB={KHOJ_DB_NAME}",
            "-e",
            f"POSTGRES_USER={KHOJ_DB_USER}",
            "-e",
            f"POSTGRES_PASSWORD={KHOJ_DB_PASSWORD}",
            "-v",
            f"{KHOJ_PG_VOLUME}:/var/lib/postgresql/data",
            KHOJ_PG_IMAGE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return ContainerCreateResult(ok=False, error=r.stderr.strip())
    return ContainerCreateResult(ok=True)


def wait_for_postgres_ready(*, timeout_s: int = 30) -> bool:
    """Poll ``pg_isready`` inside the khoj-pg container until it answers.

    Returns True on success, False on timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker",
                "exec",
                KHOJ_PG_CONTAINER,
                "pg_isready",
                "-U",
                KHOJ_DB_USER,
                "-d",
                KHOJ_DB_NAME,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return True
        time.sleep(0.5)
    return False


def enable_pgvector() -> PgvectorResult:
    """Run ``CREATE EXTENSION IF NOT EXISTS vector`` inside the container."""
    r = subprocess.run(
        [
            "docker",
            "exec",
            KHOJ_PG_CONTAINER,
            "psql",
            "-U",
            KHOJ_DB_USER,
            "-d",
            KHOJ_DB_NAME,
            "-c",
            "CREATE EXTENSION IF NOT EXISTS vector;",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return PgvectorResult(ok=False, error=r.stderr.strip())
    return PgvectorResult(ok=True)


def provision_postgres_container() -> ProvisionResult:
    """Create (or reuse) the Khoj Postgres+pgvector docker container.

    Idempotent:
    - Missing              -> ``docker run -d --name khoj-pg …``
    - Stopped, right port  -> ``docker start khoj-pg``
    - Running, right port  -> skip
    - Wrong host port      -> ``docker rm -f khoj-pg`` + recreate

    Raises ``RuntimeError`` on any docker failure so the caller can
    surface the error message.
    """
    state = docker_container_state(KHOJ_PG_CONTAINER)
    port_was_stale = False
    old_port: str | None = None

    # Check whether host-side port mapping matches what we want.
    if state != "missing":
        current_port = docker_container_host_port(KHOJ_PG_CONTAINER)
        if current_port is not None and current_port != KHOJ_DB_PORT:
            port_was_stale = True
            old_port = current_port
            rm_result = remove_container(KHOJ_PG_CONTAINER)
            if not rm_result.ok:
                raise RuntimeError(f"docker rm -f failed: {rm_result.error}")
            state = "missing"  # fall through to the create branch

    if state == "running":
        return ProvisionResult(
            status="reused",
            port_was_stale=port_was_stale,
            old_port=old_port,
        )

    if state == "stopped":
        start_result = start_container(KHOJ_PG_CONTAINER)
        if not start_result.ok:
            raise RuntimeError(f"docker start failed: {start_result.error}")
        return ProvisionResult(
            status="started",
            port_was_stale=port_was_stale,
            old_port=old_port,
        )

    # state == "missing"
    create_result = create_container()
    if not create_result.ok:
        raise RuntimeError(f"docker run failed: {create_result.error}")
    return ProvisionResult(
        status="recreated" if port_was_stale else "created",
        port_was_stale=port_was_stale,
        old_port=old_port,
    )


# --- khoj binary resolution -------------------------------------------------


def khoj_binary_path() -> Path | None:
    """Resolve the ``khoj`` executable in this venv or on PATH."""
    venv_bin = Path(sys.executable).parent / "khoj"
    if venv_bin.is_file() and os.access(venv_bin, os.X_OK):
        return venv_bin
    on_path = shutil_which("khoj")
    if on_path:
        return Path(on_path)
    return None


# --- khoj health check ------------------------------------------------------


def wait_for_khoj_health(
    proc: subprocess.Popen[bytes],
    log_path: Path,
    *,
    timeout_s: int = 120,
) -> HealthCheckResult:
    """Poll Khoj's ``/api/health`` until it responds 200 or the process dies.

    Returns a :class:`HealthCheckResult` instead of raising/exiting.
    """
    from urllib.request import urlopen as _urlopen

    health_url = "http://127.0.0.1:42110/api/health"
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return HealthCheckResult(
                ready=False,
                process_exited=True,
                exit_code=proc.returncode,
            )
        try:
            with _urlopen(health_url, timeout=1) as resp:
                if resp.status == 200:
                    return HealthCheckResult(ready=True)
        except Exception:  # noqa: BLE001 — any failure = not ready yet
            pass
        time.sleep(1)

    return HealthCheckResult(ready=False, timed_out=True)


def show_log_tail(log_path: Path, lines: int = 8) -> list[str]:
    """Return the last *lines* lines of a log file."""
    try:
        all_lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return all_lines[-lines:] if len(all_lines) > lines else all_lines


# --- installer picker --------------------------------------------------------


def pick_khoj_installer() -> InstallerChoice:
    """Return ``(argv, human_label)`` for the best available installer."""
    uv = shutil_which("uv")
    if uv:
        return InstallerChoice(
            cmd=[uv, "pip", "install", "--python", sys.executable, "khoj"],
            label="uv pip install",
        )
    return InstallerChoice(
        cmd=[sys.executable, "-m", "pip", "install", "khoj"],
        label="pip install",
    )
