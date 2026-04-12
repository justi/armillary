"""Settings page — in-UI editor for umbrellas, launchers, and Khoj."""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from armillary import launcher as launcher_mod
from armillary.config import (
    Config,
    ConfigError,
    LauncherConfig,
    UmbrellaConfig,
    default_config_path,
    load_config,
)
from armillary.ui.actions import save_config_and_refresh
from armillary.ui.helpers import _shorten_home
from armillary.ui.settings_tabs import (
    render_settings_integrations,
    render_settings_khoj,
)

_SETTINGS_TOAST_KEY = "_settings_toast"


@dataclass(frozen=True)
class _LauncherAvailabilityCompat:
    available: bool
    mode: str
    detail: str | None = None
    app_name: str | None = None


def _render_settings_page() -> None:
    """In-UI editor for the YAML config — umbrellas, launchers, Khoj.

    Replaces the "edit YAML by hand" workflow per the user-stated rule
    "what you can't click in the UI doesn't exist". Four tabs, each
    with its own form + Save button. Inline test affordances for the
    things that can be tested without leaving the page (launcher PATH
    check, Khoj health probe).

    Loading the page itself is read-only — the only filesystem writes
    happen on explicit "Save" button clicks.
    """
    from armillary.ui.sidebar import _render_nav_sidebar

    _render_nav_sidebar()

    # P1.2: Show toast feedback from the previous save/add/remove action.
    toast_msg = st.session_state.pop(_SETTINGS_TOAST_KEY, None)
    if toast_msg:
        st.toast(toast_msg)

    st.title(":material/settings: Settings")
    st.caption(f"Editing `{_shorten_home(default_config_path())}`")

    try:
        cfg = load_config()
    except ConfigError as exc:
        msg = str(exc)
        # Surface just the first line (usually the human-readable summary)
        # and hide the full traceback behind an expander.
        short = msg.splitlines()[0] if msg else "Unknown error"
        st.error(f"Config could not be loaded: {short}")
        if len(msg.splitlines()) > 1:
            with st.expander("Full error details"):
                st.code(msg, language="text")
        st.info(
            "Fix the YAML by hand (`armillary config` from a terminal), "
            "then click Reload below."
        )
        if st.button("Reload config", icon=":material/refresh:"):
            st.rerun()
        return

    tabs = st.tabs(
        [
            ":material/folder_open: Umbrellas",
            ":material/launch: Launchers",
            ":material/psychology: Khoj",
            ":material/extension: Integrations",
        ]
    )
    with tabs[0]:
        _render_settings_umbrellas(cfg)
    with tabs[1]:
        _render_settings_launchers(cfg)
    with tabs[2]:
        render_settings_khoj(cfg)
    with tabs[3]:
        render_settings_integrations()


# ----- helpers -------------------------------------------------------------


def _clear_umbrella_widget_keys(count: int) -> None:
    """Remove per-row session_state keys so the next rerun uses fresh values.

    After an add/remove the row indices shift, so stale keys would make
    Streamlit show the wrong values (P1.1).
    """
    for idx in range(count + 1):  # +1 to cover the now-gone row
        for suffix in ("path", "label", "depth"):
            st.session_state.pop(f"umbrella_{suffix}_{idx}", None)


def _clear_launcher_widget_keys(ids: list[str]) -> None:
    """Remove per-launcher session_state keys before rerun (P1.1)."""
    for target_id in ids:
        for suffix in ("label", "command", "icon", "terminal", "args"):
            st.session_state.pop(f"launcher_{suffix}_{target_id}", None)


def _detect_launcher_compat(config: LauncherConfig) -> _LauncherAvailabilityCompat:
    """Use the new launcher detection when available, else degrade gracefully.

    Streamlit can temporarily keep an older `armillary.launcher` module in
    memory across hot reloads. In that case the new UI must not crash on a
    missing `detect_launcher` symbol — fall back to the old PATH-only check
    until the process is restarted.
    """
    detect = getattr(launcher_mod, "detect_launcher", None)
    if callable(detect):
        return detect(config)
    resolved = shutil.which(config.command)
    return _LauncherAvailabilityCompat(
        available=resolved is not None,
        mode="path" if resolved is not None else "missing",
        detail=resolved,
    )


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
                "",
                icon=":material/close:",
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
        _clear_umbrella_widget_keys(len(cfg.umbrellas))
        save_config_and_refresh(cfg)
        return

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
            _clear_umbrella_widget_keys(len(cfg.umbrellas))
            save_config_and_refresh(cfg)
            return

    if st.button(
        "Save changes",
        icon=":material/save:",
        key="umbrellas_save",
        type="primary",
    ):
        cfg.umbrellas = edited
        save_config_and_refresh(cfg)


# ----- Launchers tab -------------------------------------------------------


def _render_settings_launchers(cfg: Config) -> None:
    st.subheader("Launchers")
    st.caption(
        "Tools `armillary open` can spawn. Built-in launchers reappear "
        "after save — they can be customized but not permanently removed."
    )

    builtin_ids = set(Config.builtin_launchers())
    edited: dict[str, LauncherConfig] = {}
    removed_any = False

    for target_id in sorted(cfg.launchers.keys()):
        launcher = cfg.launchers[target_id]
        availability = _detect_launcher_compat(launcher)
        if availability.mode == "path":
            status_label = "CLI on PATH"
        elif availability.mode == "macos-app":
            status_label = "macOS app"
        else:
            status_label = "not detected"
        badge_text = " (built-in)" if target_id in builtin_ids else ""
        status_icon = (
            ":material/check_circle:" if availability.available else ":material/error:"
        )

        icon_char = launcher.icon or "\u00b7"
        with st.expander(
            f"{icon_char} {target_id}{badge_text} \u2014 {status_label}",
            icon=status_icon,
        ):
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
                    "Test",
                    icon=":material/science:",
                    key=f"launcher_test_{target_id}",
                    help="Check whether the command is on PATH (no spawn)",
                )
            with cols_bottom[1]:
                remove_clicked = st.button(
                    "Remove",
                    icon=":material/delete:",
                    key=f"launcher_remove_{target_id}",
                )

            if test_clicked:
                try:
                    test_args = shlex.split(new_args) if new_args.strip() else []
                except ValueError as exc:
                    st.error(f"Could not parse args: {exc}")
                    test_args = launcher.args
                test_availability = _detect_launcher_compat(
                    LauncherConfig(
                        label=new_label,
                        command=new_command,
                        args=test_args,
                        icon=new_icon or None,
                        terminal=new_terminal,
                    )
                )
                if test_availability.mode == "path":
                    st.success(f"Found CLI: `{test_availability.detail}`")
                elif test_availability.mode == "macos-app":
                    st.success(
                        "Found macOS app bundle: "
                        f"`{test_availability.detail}`. armillary will open it via "
                        "`open -a`."
                    )
                else:
                    st.error(
                        f"`{new_command}` was not found on PATH"
                        + (
                            " and no matching macOS app bundle was detected."
                            if new_terminal is False
                            else "."
                        )
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
        _clear_launcher_widget_keys(list(cfg.launchers.keys()))
        save_config_and_refresh(cfg)
        return

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

    if st.button(
        "Save changes",
        icon=":material/save:",
        key="launchers_save",
        type="primary",
    ):
        cfg.launchers = edited
        save_config_and_refresh(cfg)
