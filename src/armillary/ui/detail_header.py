"""Detail-page header: title + status chip + editable purpose quote
+ launcher dropdown + project-age caption. Split out of ``detail.py``
for the 400-line architecture target.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from armillary import launcher as launcher_mod
from armillary.config import Config, LauncherConfig
from armillary.models import Project
from armillary.ui.helpers import _safe_load_config, _shorten_home
from armillary.ui.launcher_support import detect_launcher_compat
from armillary.ui.style import purpose_quote, status_chip


@dataclass(frozen=True)
class LauncherOption:
    """View-model for one entry in the launcher dropdown."""

    target_id: str
    label: str
    availability_mode: str
    detail: str | None = None


def build_launcher_options(
    launchers: dict[str, LauncherConfig],
) -> tuple[list[LauncherOption], list[str], list[str], list[str]]:
    """Filter terminal launchers, detect availability, build display options."""
    available: list[LauncherOption] = []
    missing_labels: list[str] = []
    terminal_only_labels: list[str] = []
    app_labels: list[str] = []
    for target_id, launcher_cfg in launchers.items():
        label = (
            f"{launcher_cfg.icon + ' ' if launcher_cfg.icon else ''}"
            f"{launcher_cfg.label}"
        )
        if launcher_cfg.terminal:
            terminal_only_labels.append(launcher_cfg.label)
            continue
        availability = detect_launcher_compat(launcher_cfg)
        if availability.available:
            available.append(
                LauncherOption(
                    target_id=target_id,
                    label=label,
                    availability_mode=availability.mode,
                    detail=availability.detail,
                )
            )
            if availability.mode == "macos-app":
                app_labels.append(label)
        else:
            missing_labels.append(label)
    return available, missing_labels, terminal_only_labels, app_labels


def _render_project_age(md: object | None) -> None:
    """S5: Show project age + work intensity below the title.

    Intensity = work_hours / active_span (first→last commit).
    Only shown when active span >= 30 days — shorter spans produce
    misleading h/mo values.
    """
    if md is None or not md.first_commit_ts or not md.work_hours:
        return
    from datetime import datetime

    age_days = (datetime.now() - md.first_commit_ts).days
    if age_days <= 0:
        return
    if age_days >= 365:
        age_str = f"{age_days / 365:.1f}y"
    elif age_days >= 30:
        age_str = f"{age_days / 30.44:.0f}mo"
    else:
        age_str = f"{age_days}d"
    intensity_str = ""
    if md.last_commit_ts and md.first_commit_ts:
        span_days = max((md.last_commit_ts - md.first_commit_ts).days, 1)
        if span_days >= 30:
            span_months = span_days / 30.44
            intensity = md.work_hours / span_months
            intensity_str = f" \u00b7 {intensity:.0f} hours/month"
    st.caption(f"Age {age_str}{intensity_str}")


def _render_header_tombstone(project: Project) -> None:
    """Minimal header for ARCHIVED projects — no launcher."""
    md = project.metadata
    st.title(f"{project.name} \u2014 :material/archive: ARCHIVED")
    from armillary.purpose_service import get_purpose

    purpose = get_purpose(str(project.path))
    if purpose:
        st.caption(f"*{purpose}*")
    elif md and md.readme_excerpt:
        from armillary.utils import excerpt_one_liner

        st.caption(f"*{excerpt_one_liner(md.readme_excerpt)}*")
    parts: list[str] = []
    if md and md.work_hours is not None:
        parts.append(f"**{md.work_hours:.0f}h** invested")
    if md and md.commit_count is not None:
        parts.append(f"{md.commit_count} commits")
    _render_project_age(md)
    if parts:
        st.caption(" \u00b7 ".join(parts))


def _render_header_with_launcher(project: Project) -> None:
    """Name + status badge + velocity trend + launcher dropdown."""
    from armillary.ui.detail_glance import _render_glance_strip

    md = project.metadata

    col_title, col_launcher = st.columns([5, 2])
    with col_title:
        st.title(project.name, anchor=False)
        if md and md.status:
            st.markdown(status_chip(md.status.value), unsafe_allow_html=True)

        from armillary.purpose_service import (
            clear_purpose,
            get_purpose,
            set_purpose,
        )

        purpose = get_purpose(str(project.path))
        edit_key = f"_edit_purpose_{project.path}"
        editing = st.session_state.get(edit_key, False)

        if purpose and not editing:
            col_quote, col_edit = st.columns([10, 1])
            with col_quote:
                st.markdown(purpose_quote(purpose), unsafe_allow_html=True)
            with col_edit:
                if st.button(
                    "",
                    icon=":material/edit:",
                    key=f"edit_btn_{project.path}",
                    help="Click to edit purpose",
                    type="secondary",
                ):
                    st.session_state[edit_key] = True
                    st.rerun()
        else:
            if not purpose and md and md.readme_excerpt:
                from armillary.utils import excerpt_one_liner

                st.markdown(
                    purpose_quote(excerpt_one_liner(md.readme_excerpt)),
                    unsafe_allow_html=True,
                )
            new_purpose = st.text_input(
                "Purpose",
                value=purpose or "",
                placeholder="Why does this project exist? One sentence.",
                key=f"purpose_input_{project.path}",
                label_visibility="collapsed",
            )
            trimmed = new_purpose.strip()
            if trimmed and trimmed != (purpose or ""):
                set_purpose(str(project.path), trimmed)
                st.session_state[edit_key] = False
                st.rerun()
            elif not trimmed and purpose:
                clear_purpose(str(project.path))
                st.session_state[edit_key] = False
                st.rerun()

        st.caption(f"`{_shorten_home(project.path)}`")
        _render_project_age(md)
    with col_launcher:
        cfg = _safe_load_config()
        if cfg is not None and cfg.launchers:
            _render_launcher_compact(project, cfg)

    # At-a-glance strip — 5 metric cells (design change #2 for detail)
    _render_glance_strip(project)

    from armillary.purpose_service import get_revenue, set_revenue

    current_rev = get_revenue(str(project.path))
    with st.expander("Set revenue", expanded=False):
        new_rev = st.number_input(
            "Monthly revenue (USD)",
            value=current_rev or 0,
            min_value=0,
            step=10,
            key=f"revenue_{project.path}",
        )
        if st.button("Save", key=f"save_rev_{project.path}"):
            set_revenue(str(project.path), int(new_rev))
            st.rerun()


def _render_launcher_compact(project: Project, cfg: Config) -> None:
    """Compact launcher: selectbox + Open button, top-right."""
    available, missing_labels, terminal_only_labels, app_labels = (
        build_launcher_options(cfg.launchers)
    )

    if terminal_only_labels:
        st.caption(
            f"Terminal: {', '.join(terminal_only_labels)} "
            "\u2014 use CLI `armillary open`"
        )

    if not available:
        return

    options_map = {opt.target_id: opt.label for opt in available}
    st.caption("Open with:")
    target_id = st.selectbox(
        "Open with",
        options=list(options_map),
        format_func=lambda tid: options_map[tid],
        label_visibility="collapsed",
        key=f"launcher_pick_{project.path}",
    )
    clicked = st.button(
        "Open",
        icon=":material/launch:",
        width="stretch",
        key=f"launcher_open_{project.path}",
        type="primary",
    )

    if clicked:
        result = launcher_mod.launch(project, target_id, launchers=cfg.launchers)
        if result.ok:
            st.success(f"Opened in `{target_id}`.")
        else:
            st.error(result.error or "Launch failed.")
