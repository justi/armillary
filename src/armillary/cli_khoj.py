"""Khoj install + start commands for armillary CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer

from armillary import khoj_service
from armillary.cli import app
from armillary.cli_helpers import shutil_which
from armillary.config import default_config_path


@app.command("install-khoj")
def install_khoj(
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        "-y",
        help="Skip the confirmation prompt and install straight away.",
    ),
) -> None:
    """Install Khoj into the current Python environment.

    Picks the best available installer so it works across common setups:

    1. If `uv` is on PATH, prefer `uv pip install khoj` — fastest, and
       works on `uv venv`-created environments that do not ship pip.
    2. Else, if `python -m pip` works in the current interpreter, use
       that. This is the classic CPython/virtualenv happy path.
    3. Else, try `python -m ensurepip --upgrade` to bootstrap pip, then
       retry the pip install. Catches `uv venv` without `--seed` and
       some minimal Debian/Homebrew venvs.
    4. Otherwise surface a concrete error telling the user what to do
       next (install uv, or recreate the venv with `--seed`).

    Khoj is a heavy dependency (~1 GB with the default ML models +
    torch) so the command confirms once before pulling anything down.
    On success, prints the next steps: start the Khoj server in a
    separate terminal, then rerun `armillary config --init --force`
    (or flip the toggle in the dashboard Settings → Khoj tab).
    """
    installer = khoj_service.pick_khoj_installer()

    typer.secho(
        f"This will install Khoj via `{installer.label}`.",
        fg=typer.colors.CYAN,
    )
    typer.echo(
        "  Khoj pulls ~1 GB of ML dependencies (torch, transformers, …). "
        "This can take several minutes."
    )
    typer.echo(f"  Python:    {sys.executable}")
    typer.echo(f"  Command:   {' '.join(installer.cmd)}")

    if not non_interactive and not typer.confirm(
        "\n  Proceed?",
        default=False,
    ):
        typer.echo("Aborted.")
        raise typer.Exit(1)

    typer.secho("\nInstalling Khoj…", fg=typer.colors.CYAN)
    result = subprocess.run(installer.cmd, check=False)

    # If plain `python -m pip` failed with "No module named pip", try
    # to bootstrap pip via ensurepip and retry once. uv path already
    # succeeded or failed for its own reasons — do not second-guess it.
    if result.returncode != 0 and installer.cmd[:3] == [sys.executable, "-m", "pip"]:
        typer.secho(
            "\npip install failed. Trying to bootstrap pip via ensurepip…",
            fg=typer.colors.YELLOW,
        )
        bootstrap_result = subprocess.run(
            [sys.executable, "-m", "ensurepip", "--upgrade"],
            check=False,
        )
        if bootstrap_result.returncode == 0:
            typer.secho(
                "  ✓ ensurepip OK — retrying pip install.",
                fg=typer.colors.CYAN,
            )
            result = subprocess.run(installer.cmd, check=False)

    if result.returncode != 0:
        typer.secho(
            f"\nInstall failed with exit code {result.returncode}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.echo(
            "  Common causes: network error, incompatible Python version, "
            "conflicting dependencies, or a venv created without pip.\n"
            "  Workarounds:\n"
            "    - Install uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`) "
            "and rerun `armillary install-khoj`.\n"
            "    - Recreate the venv with `uv venv --seed` or `python -m venv`.\n"
            "    - Install Khoj manually: `pip install khoj`."
        )
        raise typer.Exit(result.returncode)

    typer.secho("\n✓ Khoj package installed.", fg=typer.colors.GREEN, bold=True)
    typer.echo("")

    # Khoj requires PostgreSQL 15 + pgvector. Rather than print a brew
    # recipe and hope the user's machine is not booby-trapped (we got
    # burned by `brew install pgvector` compiling against postgresql@14
    # while the user ran @15 — "extension control file" not found),
    # we now provision an isolated Postgres container via Docker. One
    # command, no host-side package managers, no version conflicts.
    if shutil_which("docker") is None:
        typer.secho(
            "⚠ Docker not found. Khoj needs PostgreSQL 15 + pgvector to run.",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        typer.echo(
            "  armillary install-khoj uses Docker to provision the database\n"
            "  (container image: pgvector/pgvector:pg15) to avoid brew\n"
            "  formula conflicts and give you a reproducible setup.\n"
        )
        typer.echo("  Install Docker Desktop, then rerun `armillary install-khoj`:")
        typer.secho(
            "       https://www.docker.com/products/docker-desktop/",
            fg=typer.colors.CYAN,
        )
        typer.echo(
            "\n  Or set up Postgres + pgvector yourself and start the Khoj\n"
            "  server with POSTGRES_HOST=… POSTGRES_DB=khoj env vars."
        )
        raise typer.Exit(1)

    _provision_khoj_postgres_container_cli()

    # Generate / reuse local admin credentials so Khoj does not drop
    # into its interactive `Email: / Password:` prompt on first run.
    admin_env = khoj_service.ensure_khoj_admin_env()
    admin_env_path = khoj_service.khoj_admin_env_path()
    typer.echo("")
    typer.secho(
        f"✓ Khoj admin credentials at {admin_env_path}",
        fg=typer.colors.GREEN,
    )
    typer.echo(
        f"  Email:     {admin_env['KHOJ_ADMIN_EMAIL']}\n"
        "  Password:  <hidden — see the file above, 0600 perms>"
    )
    typer.echo(
        "  These auto-log in to http://localhost:42110/server/admin "
        "once Khoj is running."
    )

    typer.echo("")
    typer.secho("Next steps:", bold=True)
    typer.echo("  1. Start the Khoj server (foreground, logs in the terminal):")
    typer.secho("       armillary start-khoj", fg=typer.colors.CYAN)
    typer.echo("  2. In a SECOND terminal, wire it into armillary:")
    typer.secho(
        "       armillary config --init --force",
        fg=typer.colors.CYAN,
    )
    typer.echo(
        "     (or enable via dashboard Settings → Khoj tab if you "
        "already have a config)"
    )


# --- Khoj docker CLI wrapper -----------------------------------------------


def _provision_khoj_postgres_container_cli() -> None:
    """Provision the Khoj Postgres container with typer output.

    Wraps :func:`khoj_service.provision_postgres_container` and its
    sub-steps with user-facing ``typer.secho`` messages.
    """
    typer.secho(
        f"Provisioning Postgres+pgvector container `{khoj_service.KHOJ_PG_CONTAINER}`…",
        fg=typer.colors.CYAN,
    )
    typer.echo(f"  Image:     {khoj_service.KHOJ_PG_IMAGE}")
    typer.echo(f"  Volume:    {khoj_service.KHOJ_PG_VOLUME} (persists embeddings)")
    typer.echo(
        f"  Port:      {khoj_service.KHOJ_DB_HOST}:{khoj_service.KHOJ_DB_PORT} "
        f"→ container:{khoj_service.KHOJ_CONTAINER_PORT}"
    )

    try:
        prov = khoj_service.provision_postgres_container()
    except RuntimeError as exc:
        typer.secho(f"  ✗ {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    if prov.port_was_stale:
        typer.secho(
            f"  · Existing container uses host port {prov.old_port}, "
            f"expected {khoj_service.KHOJ_DB_PORT}.",
            fg=typer.colors.YELLOW,
        )
        typer.secho(
            f"  · Recreating `{khoj_service.KHOJ_PG_CONTAINER}` with the new port "
            f"(volume `{khoj_service.KHOJ_PG_VOLUME}` persists — no data loss).",
            fg=typer.colors.CYAN,
        )

    if prov.status == "reused":
        typer.secho("  ✓ Container already running.", fg=typer.colors.GREEN)
    elif prov.status == "started":
        typer.secho("  ✓ Container started.", fg=typer.colors.GREEN)
    elif prov.status in ("created", "recreated"):
        typer.secho("  ✓ Container created.", fg=typer.colors.GREEN)

    # Wait for the DB to accept connections before we touch it.
    typer.secho("  · Waiting for Postgres to accept connections…", fg=typer.colors.CYAN)
    if not khoj_service.wait_for_postgres_ready(timeout_s=30):
        typer.secho(
            "  ✗ Postgres did not become ready within 30 seconds.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.echo(f"  Check logs with: docker logs {khoj_service.KHOJ_PG_CONTAINER}")
        raise typer.Exit(2)
    typer.secho("  ✓ Postgres ready.", fg=typer.colors.GREEN)

    # Enable pgvector. Idempotent via IF NOT EXISTS.
    typer.secho(
        "  · Enabling pgvector extension (CREATE EXTENSION IF NOT EXISTS vector)…",
        fg=typer.colors.CYAN,
    )
    pgv = khoj_service.enable_pgvector()
    if not pgv.ok:
        typer.secho(
            f"  ✗ Could not enable pgvector: {pgv.error}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    typer.secho("  ✓ pgvector enabled.", fg=typer.colors.GREEN)


@app.command("start-khoj")
def start_khoj() -> None:
    """Start the Khoj server in the foreground, wired to the docker DB.

    Exports the Postgres env vars that point at the `khoj-pg` container
    provisioned by `armillary install-khoj`, finds the `khoj` binary in
    this venv, and execs it in `--anonymous-mode`. Runs in the
    foreground so the user sees logs and can Ctrl-C normally; this is a
    development server, not a daemon.

    Preconditions (checked and reported):
    - `khoj-pg` container must exist and be running (or stoppable).
    - `khoj` binary must be reachable (i.e. `armillary install-khoj`
      must have been run first).
    """
    if shutil_which("docker") is None:
        typer.secho(
            "Docker not found. Run `armillary install-khoj` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    state = khoj_service.docker_container_state(khoj_service.KHOJ_PG_CONTAINER)
    if state == "missing":
        typer.secho(
            f"Container `{khoj_service.KHOJ_PG_CONTAINER}` does not exist.\n"
            "Run `armillary install-khoj` to create it.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if state == "stopped":
        typer.secho(
            f"Starting stopped container `{khoj_service.KHOJ_PG_CONTAINER}`…",
            fg=typer.colors.CYAN,
        )
        start_result = khoj_service.start_container(khoj_service.KHOJ_PG_CONTAINER)
        if not start_result.ok:
            typer.secho(
                f"docker start failed: {start_result.error}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)

    if not khoj_service.wait_for_postgres_ready(timeout_s=15):
        typer.secho(
            f"Postgres did not become ready. Check: docker logs "
            f"{khoj_service.KHOJ_PG_CONTAINER}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    khoj_bin = khoj_service.khoj_binary_path()
    if khoj_bin is None:
        typer.secho(
            "`khoj` binary not found. Run `armillary install-khoj` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_HOST": khoj_service.KHOJ_DB_HOST,
            "POSTGRES_PORT": khoj_service.KHOJ_DB_PORT,
            "POSTGRES_DB": khoj_service.KHOJ_DB_NAME,
            "POSTGRES_USER": khoj_service.KHOJ_DB_USER,
            "POSTGRES_PASSWORD": khoj_service.KHOJ_DB_PASSWORD,
            # armillary promises "no telemetry, no
            # analytics, no external calls". Khoj defaults to sending
            # usage stats to khoj.dev — `KHOJ_TELEMETRY_DISABLE=true`
            # short-circuits `upload_telemetry()` via
            # `khoj.utils.state.telemetry_disabled`, so nothing
            # leaves the user's machine. Users who want to contribute
            # telemetry to the Khoj project can unset this manually.
            "KHOJ_TELEMETRY_DISABLE": "true",
        }
    )
    # Inject auto-generated admin credentials so Khoj does NOT drop
    # into its interactive "Email: / Password:" prompt on first run.
    env.update(khoj_service.ensure_khoj_admin_env())

    # Redirect Khoj's stdout/stderr to a log file instead of the
    # terminal. Khoj's startup logs are noisy and confusing —
    # `uvicorn.error` is a logger NAME but reads like a crash to
    # anyone who doesn't know uvicorn internals. We poll the health
    # endpoint instead and show a clean ✓/✗ status.
    log_path = default_config_path().parent / "khoj-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    typer.secho("Starting Khoj server…", fg=typer.colors.CYAN)
    typer.echo(
        "  First start downloads sentence-transformers models (~200 MB).\n"
        "  Subsequent starts reuse the cache and take a few seconds."
    )
    typer.echo(f"  Logs:   {log_path}")
    typer.echo(f"  Admin:  {khoj_service.khoj_admin_env_path()}")

    with open(log_path, "w", encoding="utf-8") as log_fh:
        # `--non-interactive` flips Khoj's `initialization()` into a
        # mode that (a) requires KHOJ_ADMIN_EMAIL / KHOJ_ADMIN_PASSWORD
        # env vars (which we export above), and (b) skips the chat-
        # model questionnaire entirely. Armillary uses Khoj for
        # semantic SEARCH, not chat, so "no chat models" is correct.
        proc = subprocess.Popen(
            [str(khoj_bin), "--anonymous-mode", "--non-interactive"],
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        health = khoj_service.wait_for_khoj_health(proc, log_path)

        if health.process_exited:
            typer.secho(
                "\n✗ Khoj exited before becoming ready.",
                fg=typer.colors.RED,
                err=True,
            )
            _show_log_tail(log_path)
            raise typer.Exit(health.exit_code or 2)

        if health.timed_out:
            typer.secho(
                "\n⚠ Khoj did not respond to health check within 120s.",
                fg=typer.colors.YELLOW,
            )
            typer.echo(
                "  The server is still running (may be downloading models).\n"
                "  Wait for it or check the log:"
            )
            _show_log_tail(log_path)

        if health.ready:
            typer.secho(
                "✓ Khoj running at http://localhost:42110 — Ctrl-C to stop.",
                fg=typer.colors.GREEN,
                bold=True,
            )

        try:
            proc.wait()
        except KeyboardInterrupt:
            typer.echo("")
            typer.secho("Stopping Khoj…", fg=typer.colors.CYAN)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            typer.secho("✓ Khoj stopped.", fg=typer.colors.GREEN)
        raise typer.Exit(proc.returncode or 0)


def _show_log_tail(log_path: Path, lines: int = 8) -> None:
    """Print the last N lines of a log file to help the user debug."""
    tail = khoj_service.show_log_tail(log_path, lines=lines)
    if tail:
        typer.echo(f"  Last {len(tail)} lines of {log_path}:")
        for line in tail:
            typer.echo(f"    {line}")
