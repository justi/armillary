# armillary

> A project observatory with AI integration — one terminal command, one browser dashboard, all your projects.
>
> *An armillary sphere is an ancient astronomical instrument: concentric rings modeling the celestial sphere, with a fixed center and orbits turning around it. The metaphor fits: you are the center, your projects orbit around you, and `armillary` lets you see the whole system at once.*

**Status:** Pre-alpha. Scanner and CLI scaffolding work; dashboard, metadata, status, launcher, and search are in progress.

## What is this?

`armillary` is a **meta layer** over the projects you already have on disk. Launched with a single terminal command, it opens a browser dashboard that:

- **Auto-discovers** every project in your umbrella folders (git repos and loose idea folders)
- **Shows metadata** for each project: last modified, git status, README snippet, ADRs, notes
- **Infers "where it stands"** — ACTIVE / PAUSED / DORMANT / IDEA / IN PROGRESS — from repo activity and file heuristics
- **Launches** each project into your preferred AI/IDE: Claude Code, Codex, Cursor, Zed, VS Code, terminal, Finder
- **Searches** across all projects — literal (ripgrep) by default, optional semantic search via [Khoj](https://khoj.dev)

## Non-goals

`armillary` is **not**:

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

`armillary` **never sends data off-device**. The project index, metadata, cache, and config all live only on the user's local disk.

- **Interactive bootstrap:** on first run, the user is asked which umbrella folders to include. The repository itself has no hardcoded paths or user-specific data.
- **No telemetry, no analytics, no external calls** — except for the optional Khoj API (also local) and the AI/IDE launchers the user explicitly configures.
- All documentation uses symbolic placeholders (`<example-folder-a>`, `~/<umbrella-name>`), never real paths.

The development plan with the full contributor guidelines on privacy is kept in a private file (`PLAN.md`, gitignored) and is not part of the public repo.

## Tech stack

- **Python 3.11+** (cross-platform; primary target macOS, should work on Linux / WSL)
- **Streamlit** — browser UI
- **GitPython** — repo metadata
- **typer** — CLI
- **pydantic** — config validation and data models
- **SQLite** — local metadata cache
- **Khoj API** — optional semantic search backend

## Installation

Not yet published to PyPI. To run from source:

```bash
git clone git@github.com:justi/armillary.git
cd armillary
uv sync                # creates .venv and installs runtime deps
.venv/bin/armillary --help
```

## Quick start

The auto-discovery scanner is the first piece that works end-to-end.
Point it at one or more umbrella folders and it prints every project
it finds as JSON:

```bash
# scan a single umbrella folder
.venv/bin/armillary scan -u ~/Projects

# scan several umbrellas at once
.venv/bin/armillary scan -u ~/Projects -u ~/ideas

# limit recursion depth (default 3, allowed 1..10)
.venv/bin/armillary scan -u ~/Projects --max-depth 5

# pipe to a file
.venv/bin/armillary scan -u ~/Projects > projects.json
```

Each entry contains the resolved path, name, type (`git` or `idea`),
which umbrella found it, and a best-effort `last_modified` timestamp.

You can also launch the (currently placeholder) dashboard:

```bash
.venv/bin/armillary start
# → opens http://localhost:8501
# → telemetry is disabled by default per the privacy commitment above
```

The remaining commands (`list`, `search`, `open`, `config`) are stubs
that print "not implemented yet" and the milestone they belong to.

## Development

```bash
# install runtime + dev dependencies (pytest, ruff)
uv sync --extra dev

# run the test suite
.venv/bin/python -m pytest

# run a single file with verbose output
.venv/bin/python -m pytest tests/test_scanner.py -v
```

## Roadmap

- ✓ Project scaffolding (CLI, package layout, dashboard stub)
- ✓ Auto-discovery scanner (`armillary scan`, JSON output)
- → Project hardening (CI workflow, ruff config)
- ◌ SQLite cache + `armillary list` from cache
- ◌ Metadata extraction (git info, README, ADRs) and status heuristics
- ◌ Streamlit dashboard reading from cache
- ◌ Configuration file and launcher integration
- ◌ Khoj integration for semantic search *(optional)*
- ◌ `repos-index.md` exporter for AI tools

## License

MIT (see [LICENSE](LICENSE))

## Contributing

The project is in early development and not yet open for external
contributions. Once the dashboard, cache, and launcher milestones are
in place, contribution guidelines will be added here.
