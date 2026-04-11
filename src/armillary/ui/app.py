"""Main Streamlit app entry point for armillary."""

from __future__ import annotations

import streamlit as st

from armillary import __version__

st.set_page_config(
    page_title="armillary",
    page_icon="🔭",
    layout="wide",
)

st.title("🔭 armillary")
st.caption(f"Project observatory with AI integration — v{__version__}")

st.info(
    "This is the scaffold. No projects are indexed yet. "
    "Auto-discovery, metadata extraction, and the project dashboard arrive "
    "in the next milestones."
)

col1, col2 = st.columns(2)

with col1:
    st.subheader("What is this?")
    st.markdown(
        "`armillary` is a meta layer over all your projects. One terminal "
        "command, one browser dashboard, every repo and idea folder on your "
        "disk — with one-click launchers into Claude Code, Codex, Cursor, "
        "Zed, VS Code, and more."
    )

with col2:
    st.subheader("Roadmap")
    st.markdown(
        """
        - **M1** — scaffolding ✅
        - **M2** — auto-discovery scanner (interactive bootstrap)
        - **M3** — metadata extraction and status heuristics
        - **M4** — Streamlit dashboard UI
        - **M5** — configuration and launchers
        - **M6** — optional Khoj integration
        - **M7** — Claude Code auto-memory bridge
        """
    )

st.divider()

st.markdown(
    "**Repository:** [github.com/justi/armillary](https://github.com/justi/armillary)"
)
