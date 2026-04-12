"""Streamlit dashboard for armillary — entrypoint and routing.

The dashboard is a **read-only consumer of the SQLite cache** for every
rerender. The hot path (filter click, search submit, page navigation)
never walks the filesystem or opens GitPython — that work is bounded
to the explicit "Scan now" button and the per-detail-page `git log`.
Streamlit reruns the whole script on every interaction; anything
expensive in the rerender path would tank a 100+ project view.

The two views (overview table and per-project detail) live in separate
modules and route via `st.query_params["project"]`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# When Streamlit launches this script directly via `streamlit run` it
# does not always see the editable install on `sys.path`, so we add the
# src directory ourselves. The CLI's `armillary start` command always
# invokes `python -m streamlit run <this file>` with the venv's Python,
# so this just makes things robust against other invocation styles.
_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

import streamlit as st  # noqa: E402

st.set_page_config(
    page_title="armillary",
    page_icon=":material/explore:",
    layout="wide",
)

from armillary.ui.detail import _render_project_detail  # noqa: E402
from armillary.ui.overview import _render_overview  # noqa: E402
from armillary.ui.settings import _render_settings_page  # noqa: E402


def main() -> None:
    params = st.query_params
    page = params.get("page")
    project_path = params.get("project")
    if page == "settings":
        _render_settings_page()
    elif project_path:
        _render_project_detail(project_path)
    else:
        _render_overview()


main()
