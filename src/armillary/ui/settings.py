"""Settings page — in-UI editor for umbrellas, launchers, and Khoj."""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

import streamlit as st

from armillary.config import (
    Config,
    ConfigError,
    KhojConfigBlock,
    LauncherConfig,
    UmbrellaConfig,
    default_config_path,
    load_config,
)
from armillary.ui.actions import go_to_overview, save_config_and_refresh
from armillary.ui.helpers import _shorten_home


def _render_settings_page() -> None:
    """In-UI editor for the YAML config — umbrellas, launchers, Khoj.

    Replaces the "edit YAML by hand" workflow per the user-stated rule
    "what you can't click in the UI doesn't exist". Three tabs, each
    with its own form + Save button. Inline test affordances for the
    things that can be tested without leaving the page (launcher PATH
    check, Khoj health probe).

    Loading the page itself is read-only — the only filesystem writes
    happen on explicit "Save" button clicks.
    """
    if st.button("← Back to overview", key="settings_back"):
        go_to_overview()

    st.title("⚙️ Settings")
    st.caption(f"Editing `{_shorten_home(default_config_path())}`")

    try:
        cfg = load_config()
    except ConfigError as exc:
        st.error(f"Config could not be loaded:\n\n```\n{exc}\n```")
        st.info(
            "Fix the YAML by hand (`armillary config` from a terminal), "
            "then click Reload below."
        )
        if st.button("🔄 Reload config"):
            st.rerun()
        return

    tabs = st.tabs(["Umbrellas", "Launchers", "Khoj"])
    with tabs[0]:
        _render_settings_umbrellas(cfg)
    with tabs[1]:
        _render_settings_launchers(cfg)
    with tabs[2]:
        _render_settings_khoj(cfg)


# ----- Umbrellas tab -------------------------------------------------------


def _render_settings_umbrellas(cfg: Config) -> None:
    st.subheader("Umbrellas")
    st.caption(
        "Folders the scanner walks. Each entry becomes a `-u` argument "
        "for `armillary scan`."
    )

    edited: list[UmbrellaConfig] = []
    removed_any = False

    if not cfg.umbrellas:
        st.info(
            "_No umbrellas configured. Add one below or run "
            "`armillary config --init` from your terminal._"
        )

    for idx, umbrella in enumerate(cfg.umbrellas):
        cols = st.columns([4, 2, 1, 1])
        with cols[0]:
            new_path = st.text_input(
                "Path",
                value=str(umbrella.path),
                key=f"umbrella_path_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
        with cols[1]:
            new_label = st.text_input(
                "Label",
                value=umbrella.label or "",
                key=f"umbrella_label_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
        with cols[2]:
            new_depth = st.number_input(
                "Max depth",
                min_value=1,
                max_value=10,
                value=umbrella.max_depth,
                key=f"umbrella_depth_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
        with cols[3]:
            # Spacer for vertical alignment with the input rows above
            if idx == 0:
                st.write("")
            remove = st.button(
                "✕",
                key=f"umbrella_remove_{idx}",
                help="Remove this umbrella",
            )
        if remove:
            # `st.button` is True only on the rerun triggered by this click.
            # We must persist immediately — if we merely skip the row and
            # wait for the Save button, the next rerun sees `remove=False`
            # again and the row comes back.
            removed_any = True
        else:
            edited.append(
                UmbrellaConfig(
                    path=Path(new_path),
                    label=new_label or None,
                    max_depth=int(new_depth),
                )
            )

    if removed_any:
        cfg.umbrellas = edited
        save_config_and_refresh(cfg)
        return

    st.divider()
    st.markdown("**Add umbrella**")
    add_cols = st.columns([4, 2, 1, 1])
    with add_cols[0]:
        new_path = st.text_input(
            "New path",
            value="",
            placeholder="~/Projects",
            key="umbrella_add_path",
        )
    with add_cols[1]:
        new_label = st.text_input(
            "New label",
            value="",
            placeholder="(auto)",
            key="umbrella_add_label",
        )
    with add_cols[2]:
        new_depth = st.number_input(
            "Depth",
            min_value=1,
            max_value=10,
            value=3,
            key="umbrella_add_depth",
        )
    with add_cols[3]:
        st.write("")
        add_clicked = st.button("Add", key="umbrella_add_btn")

    if add_clicked:
        if not new_path.strip():
            st.error("Path cannot be empty.")
        else:
            expanded = Path(new_path).expanduser()
            label = new_label.strip() or expanded.name
            edited.append(
                UmbrellaConfig(
                    path=expanded,
                    label=label,
                    max_depth=int(new_depth),
                )
            )
            cfg.umbrellas = edited
            save_config_and_refresh(cfg)
            return

    st.divider()
    if st.button("💾 Save changes", key="umbrellas_save", type="primary"):
        cfg.umbrellas = edited
        save_config_and_refresh(cfg)


# ----- Launchers tab -------------------------------------------------------


def _render_settings_launchers(cfg: Config) -> None:
    st.subheader("Launchers")
    st.caption(
        "Tools `armillary open` can spawn. Built-in entries are always "
        "available even when removed from this list — they reappear "
        "after a save."
    )

    edited: dict[str, LauncherConfig] = {}
    removed_any = False

    for target_id in sorted(cfg.launchers.keys()):
        launcher = cfg.launchers[target_id]
        on_path = shutil.which(launcher.command) is not None
        status = "🟢 on PATH" if on_path else "🔴 missing"

        with st.expander(f"{launcher.icon or '·'} {target_id} — {status}"):
            cols_top = st.columns([3, 3, 1, 1])
            with cols_top[0]:
                new_label = st.text_input(
                    "Label",
                    value=launcher.label,
                    key=f"launcher_label_{target_id}",
                )
            with cols_top[1]:
                new_command = st.text_input(
                    "Command",
                    value=launcher.command,
                    key=f"launcher_command_{target_id}",
                )
            with cols_top[2]:
                new_icon = st.text_input(
                    "Icon",
                    value=launcher.icon or "",
                    key=f"launcher_icon_{target_id}",
                )
            with cols_top[3]:
                new_terminal = st.checkbox(
                    "Terminal",
                    value=launcher.terminal,
                    key=f"launcher_terminal_{target_id}",
                    help=(
                        "Mark interactive terminal apps (codex, claude-code) "
                        "so the dashboard hides them and the CLI keeps stdio."
                    ),
                )

            new_args = st.text_input(
                "Args (space-separated, use `{path}` for project path)",
                value=" ".join(shlex.quote(a) for a in launcher.args),
                key=f"launcher_args_{target_id}",
            )

            cols_bottom = st.columns([1, 1, 4])
            with cols_bottom[0]:
                test_clicked = st.button(
                    "🧪 Test",
                    key=f"launcher_test_{target_id}",
                    help="Check whether the command is on PATH (no spawn)",
                )
            with cols_bottom[1]:
                remove_clicked = st.button(
                    "✕ Remove",
                    key=f"launcher_remove_{target_id}",
                )

            if test_clicked:
                resolved = shutil.which(new_command)
                if resolved:
                    st.success(f"Found: `{resolved}`")
                else:
                    st.error(
                        f"`{new_command}` is not on PATH. Install it or "
                        "fix the command above."
                    )

            if remove_clicked:
                # Same button-state problem as umbrellas: we must persist
                # the deletion immediately, not on a later Save click.
                removed_any = True
            else:
                try:
                    parsed_args = shlex.split(new_args) if new_args.strip() else []
                except ValueError as exc:
                    st.error(f"Could not parse args: {exc}")
                    parsed_args = launcher.args
                edited[target_id] = LauncherConfig(
                    label=new_label,
                    command=new_command,
                    args=parsed_args,
                    icon=new_icon or None,
                    terminal=new_terminal,
                )

    if removed_any:
        cfg.launchers = edited
        save_config_and_refresh(cfg)
        return

    st.divider()
    st.markdown("**Add custom launcher**")
    with st.form("launcher_add_form", clear_on_submit=True):
        a_cols = st.columns([2, 3, 3])
        with a_cols[0]:
            new_id = st.text_input("ID", placeholder="nvim")
        with a_cols[1]:
            new_label = st.text_input("Label", placeholder="Neovim")
        with a_cols[2]:
            new_command = st.text_input("Command", placeholder="nvim")

        b_cols = st.columns([4, 2, 1])
        with b_cols[0]:
            new_args = st.text_input("Args", value="{path}", placeholder="{path}")
        with b_cols[1]:
            new_icon = st.text_input("Icon", value="", placeholder="✏️")
        with b_cols[2]:
            new_terminal = st.checkbox("Terminal", value=False)

        add_clicked = st.form_submit_button("Add")

    if add_clicked:
        cleaned_id = new_id.strip()
        if not cleaned_id:
            st.error("ID cannot be empty.")
        elif cleaned_id in edited:
            st.error(f"Launcher id `{cleaned_id}` already exists.")
        elif not new_command.strip():
            st.error("Command cannot be empty.")
        else:
            try:
                parsed_args = shlex.split(new_args) if new_args.strip() else []
            except ValueError as exc:
                st.error(f"Could not parse args: {exc}")
                return
            edited[cleaned_id] = LauncherConfig(
                label=new_label.strip() or cleaned_id,
                command=new_command.strip(),
                args=parsed_args,
                icon=new_icon or None,
                terminal=new_terminal,
            )
            cfg.launchers = edited
            save_config_and_refresh(cfg)
            return

    st.divider()
    if st.button("💾 Save changes", key="launchers_save", type="primary"):
        cfg.launchers = edited
        save_config_and_refresh(cfg)


# ----- Khoj tab ------------------------------------------------------------


def _render_settings_khoj(cfg: Config) -> None:
    st.subheader("Khoj semantic search")
    st.caption(
        "Optional. When enabled, the search bar gets a 🧠 Semantic toggle "
        "that calls a local Khoj instance instead of ripgrep."
    )

    # When Khoj is not yet set up, surface the install path up front so
    # users don't have to go hunting for "how do I turn this on". The
    # block is expanded by default in the disabled state and collapses
    # once the user flips the checkbox — still one click away.
    with st.expander(
        "📦 How to install Khoj",
        expanded=not cfg.khoj.enabled,
    ):
        st.markdown(
            "Khoj needs **PostgreSQL 15+ with the pgvector extension** — "
            "it does not support SQLite. armillary provisions the "
            "database for you via **Docker** (pgvector/pgvector:pg15 "
            "image) so there's no host-side package manager conflict. "
            "You need Docker Desktop running."
        )
        st.markdown("**1. Install the Khoj Python package + provision Postgres:**")
        st.code("armillary install-khoj", language="bash")
        st.caption(
            "Runs `uv pip install khoj`, creates the `khoj-pg` Docker "
            "container, and enables pgvector."
        )
        st.markdown("**2. Start the Khoj server in a separate terminal:**")
        st.code("armillary start-khoj", language="bash")
        st.caption(
            "Foreground process; exports the Postgres env vars and "
            "execs `khoj --anonymous-mode`. First start downloads the "
            "sentence-transformers model (~500 MB)."
        )
        st.markdown(
            "**3.** Once the server responds at `http://localhost:42110`, "
            "flip **Enable Khoj** below and hit **💾 Save changes**. "
            "Use the 🧪 Test connection button to verify."
        )

    enabled = st.checkbox(
        "Enable Khoj",
        value=cfg.khoj.enabled,
        key="khoj_enabled",
    )
    api_url = st.text_input(
        "API URL",
        value=cfg.khoj.api_url,
        key="khoj_api_url",
    )
    api_key = st.text_input(
        "API key (optional, sent as Bearer token)",
        value=cfg.khoj.api_key or "",
        type="password",
        key="khoj_api_key",
    )
    timeout_seconds = st.slider(
        "Timeout (seconds)",
        min_value=0.5,
        max_value=60.0,
        value=cfg.khoj.timeout_seconds,
        step=0.5,
        key="khoj_timeout",
    )

    cols = st.columns([1, 1, 4])
    with cols[0]:
        test_clicked = st.button("🧪 Test connection", key="khoj_test")
    with cols[1]:
        save_clicked = st.button("💾 Save changes", key="khoj_save", type="primary")

    if test_clicked:
        _test_khoj_connection(api_url, api_key, timeout_seconds)

    if save_clicked:
        cfg.khoj = KhojConfigBlock(
            enabled=enabled,
            api_url=api_url,
            api_key=api_key or None,
            timeout_seconds=timeout_seconds,
        )
        save_config_and_refresh(cfg)

    # Admin panel credentials — visible only when `armillary install-khoj`
    # has already generated the `khoj-admin.env` file. These auto-log the
    # user into http://localhost:42110/server/admin; there is no reason
    # to hunt through a dotfile to find them.
    _render_khoj_admin_credentials()


def _render_khoj_admin_credentials() -> None:
    """Show the auto-generated Khoj admin credentials read-only.

    Reads `~/.config/armillary/khoj-admin.env` via the same helper
    `start-khoj` uses, so we always surface exactly what Khoj is
    booting with. Password is masked behind an expander so casual
    onlookers do not see it over the user's shoulder.
    """
    from armillary.khoj_service import khoj_admin_env_path, load_khoj_admin_env

    env = load_khoj_admin_env()
    if env is None:
        return

    st.divider()
    st.markdown("**Khoj admin panel credentials**")
    st.caption(
        "Auto-generated by `armillary install-khoj`. Use these to log "
        "into `http://localhost:42110/server/admin` once the Khoj "
        "server is running. To rotate, delete the file below and "
        "re-run `install-khoj`."
    )
    email = env.get("KHOJ_ADMIN_EMAIL", "—")
    password = env.get("KHOJ_ADMIN_PASSWORD", "—")
    cred_cols = st.columns([2, 3])
    with cred_cols[0]:
        st.text_input(
            "Admin email",
            value=email,
            key="khoj_admin_email_readonly",
            disabled=True,
        )
    with cred_cols[1], st.expander("Show password"):
        st.code(password, language=None)
    st.caption(f"Stored at `{khoj_admin_env_path()}`")


def _test_khoj_connection(api_url: str, api_key: str, timeout: float) -> None:
    """Probe `<api_url>/api/health` with the form-supplied timeout.

    Treats any 2xx status as success. Catches the same exception family
    as the CLI's init Khoj-detect step plus `KhojResponseError` for
    safety.
    """
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    try:
        url = f"{api_url.rstrip('/')}/api/health"
        request = Request(url)
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            if 200 <= int(status) < 300:
                st.success(f"Khoj responded with HTTP {status}.")
            else:
                st.error(f"Khoj responded with HTTP {status}.")
    except HTTPError as exc:
        st.error(f"Khoj returned HTTP {exc.code}: {exc.reason}")
    except (URLError, TimeoutError, OSError) as exc:
        st.error(f"Khoj unreachable at {api_url}: {exc}")
    except Exception as exc:  # noqa: BLE001 — surface anything weird
        st.error(f"Khoj test failed: {exc}")
