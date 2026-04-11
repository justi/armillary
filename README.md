# workspace

> A project observatory with AI integration — one terminal command, one browser dashboard, all your projects.

**Status:** Planning / pre-alpha. No working code yet.

## What is this?

`workspace` is a **meta layer** over the projects you already have on disk. Launched with a single terminal command, it opens a browser dashboard that:

- **Auto-discovers** every project in your umbrella folders (git repos and loose idea folders)
- **Shows metadata** for each project: last modified, git status, README snippet, ADRs, notes
- **Infers "where it stands"** — ACTIVE / PAUSED / DORMANT / IDEA / IN PROGRESS — from repo activity and file heuristics
- **Launches** each project into your preferred AI/IDE: Claude Code, Codex, Cursor, Zed, VS Code, terminal, Finder
- **Searches** across all projects — literal (ripgrep) by default, optional semantic search via [Khoj](https://khoj.dev)

## Non-goals

`workspace` is **not**:

- Another git GUI — use [Sourcetree](https://www.sourcetreeapp.com/) / [Fork](https://git-fork.com/) / [gita](https://github.com/nosarthur/gita) for that
- Another IDE — Claude Code / Cursor / Zed already cover that
- Another note-taking tool — Obsidian / Logseq / Notion already cover that
- A code editor — it only **launches** projects in external editors
- A cloud service — everything stays local

## Core principle: build blocks + glue

The goal is **maximum reuse of existing tools**, **minimum custom code**.

| Concern | Provided by |
|---|---|
| Web UI | [Streamlit](https://streamlit.io) |
| Git metadata | [GitPython](https://github.com/gitpython-developers/GitPython) |
| Semantic search | [Khoj](https://khoj.dev) (optional) |
| Multi-repo git ops | [gita](https://github.com/nosarthur/gita) (optional) |
| CLI | [typer](https://typer.tiangolo.com/) |
| Cache | `sqlite3` (std lib) |
| Launch | `subprocess` |

Custom code (discovery, status heuristics, launcher config, AI-memory bridge) is estimated at **~350-400 lines of Python**.

## Privacy

`workspace` **never sends data off-device**. The project index, metadata, cache, and config all live only on the user's local disk.

- **Interactive bootstrap:** on first run, the user is asked which umbrella folders to include. The repository itself has no hardcoded paths or user-specific data.
- **No telemetry, no analytics, no external calls** — except for the optional Khoj API (also local) and the AI/IDE launchers the user explicitly configures.
- All documentation uses symbolic placeholders (`<example-folder-a>`, `~/<umbrella-name>`), never real paths.

See [§14 of the development plan](#) for full contributor guidelines on privacy. *(The development plan itself is kept in a private file and is not part of the public repo.)*

## Tech stack

- **Python 3.11+** (cross-platform; primary target macOS, should work on Linux / WSL)
- **Streamlit** — browser UI
- **GitPython** — repo metadata
- **typer** — CLI
- **pydantic** — config validation and data models
- **SQLite** — local metadata cache
- **Khoj API** — optional semantic search backend

## Installation

Not yet installable. This repo currently only contains the plan and README; code to follow.

## Roadmap

- **M1** — scaffolding
- **M2** — auto-discovery scanner (shallow + deep scan, interactive bootstrap)
- **M3** — metadata extraction and status heuristics
- **M4** — Streamlit dashboard UI
- **M5** — configuration and launcher integration
- **M6** — optional Khoj integration for semantic search
- **M7** — Claude Code auto-memory bridge

## License

MIT (see [LICENSE](LICENSE))

## Contributing

The project is in the planning phase and is not yet open for contributions. Once the scaffolding is in place and the initial milestones are complete, contribution guidelines will be added here.
