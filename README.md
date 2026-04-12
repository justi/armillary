# armillary

> A project observatory with AI integration — one terminal command, one browser dashboard, all your projects.
>
> *An armillary sphere is an ancient astronomical instrument: concentric rings modeling the celestial sphere, with a fixed center and orbits turning around it. The metaphor fits: you are the center, your projects orbit around you, and `armillary` lets you see the whole system at once.*

```text
   Your scattered projects                              What armillary gives you
   ─────────────────────────                            ────────────────────────

   ~/Projects/                                          📋  armillary list
     alpha-app/                                             terminal table, sortable
     beta-prototype/        ┌───────────────────┐
     research-notes/        │                   │       🌐  armillary start
                            │     armillary     │           browser dashboard, filters
   ~/projects_prod/         │                   │
     client-x/        ────▶ │  scan + index +   │ ────▶ 🔍  armillary search "needle"
     deploy-tool/           │   SQLite cache    │           ripgrep across all files
                            │                   │
   ~/code/                  └───────────────────┘       🚀  armillary open <name>
     experiments/                    │                      Cursor / Zed / VS Code / ...
     ...                             │
                                     ▼                  📤  armillary export-index
                          status: ACTIVE / PAUSED /         Markdown for Claude / Codex
                                  DORMANT / IDEA /
                                  IN_PROGRESS
```

**Status:** Alpha. The full MVP from PLAN.md §5 is implemented and on `main`: scanner with bootstrap, SQLite cache, metadata extraction with status heuristics, Streamlit dashboard with launcher / search / detail views, ripgrep + optional Khoj search, markdown exporter for AI tools. Daily-driver-ready on macOS / Linux.

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

## Prerequisites

- **Python 3.11+** (managed by `uv`)
- **Git** — armillary reads repo metadata through GitPython
- **ripgrep** — the default search backend (`brew install ripgrep`)
- **Docker Desktop** *(optional, but required for semantic search)* — Khoj
  stores its embeddings in PostgreSQL with the pgvector extension, and
  `armillary install-khoj` provisions that database by running the
  official `pgvector/pgvector:pg15` container. Install from
  [docker.com](https://www.docker.com/products/docker-desktop/) if you
  want semantic search; skip it otherwise — ripgrep works just fine.
- A launcher binary on PATH for each entry in `launchers:` you want to
  use (`cursor`, `code`, `zed`, `claude`, `codex`, …).

## Installation

Not yet published to PyPI. To run from source:

```bash
git clone git@github.com:justi/armillary.git
cd armillary
uv sync                       # creates .venv and installs runtime deps
.venv/bin/armillary --help
```

For day-to-day use, install globally with uv so the `armillary`
command is on your PATH everywhere:

```bash
uv tool install --editable .
```

## Quick start

```bash
# 1. First-run bootstrap: scans ~/ for umbrella folder candidates
#    (folders with multiple git repos or conventional names like
#    `Projects`, `repos`, `code`, ...) and lets you pick which to use.
armillary config --init

# 2. Index the projects across every umbrella from your config.
#    Walks the filesystem, extracts git metadata + README + status.
armillary scan

# 3. Browse the index — terminal table or browser dashboard.
armillary list                 # rich table, sortable
armillary list --status ACTIVE # only fresh projects
armillary list --type idea     # only loose notes folders

armillary start                # opens http://localhost:8501
```

### Optional: enable semantic search (Khoj)

If you want the `🧠 Semantic` toggle in the dashboard search bar,
you also need [Khoj](https://khoj.dev) running locally. Khoj needs
Postgres 15 + pgvector, which armillary provisions via Docker:

```bash
# Requires Docker Desktop installed and running.
armillary install-khoj         # pip install khoj + pgvector docker container
armillary start-khoj           # foreground Khoj server (second terminal)
armillary config --init --force  # armillary detects Khoj and auto-enables it
```

Skip this whole section if you're fine with ripgrep — armillary
works perfectly without Khoj.

## What works today

| Command | What it does |
|---|---|
| `armillary config --init` | Scans `~/` for umbrella candidates, interactive picker, writes `~/.config/armillary/config.yaml` |
| `armillary config` | Edits existing config in `$EDITOR` |
| `armillary scan` | Walks umbrellas, extracts metadata (branch, last commit, dirty count, ahead/behind, size, file count, README excerpt, ADRs, notes), computes status, persists to SQLite cache |
| `armillary list` | Rich terminal table from cache, filters: `--status`, `--type`, `--umbrella` |
| `armillary search "<query>"` | ripgrep across every cached project, with `--project` substring filter, optional `--khoj` semantic backend |
| `armillary open <name>` | Launches a project in a configured editor (`--target cursor`/`vscode`/`zed`/`finder`/`terminal`/...) with `cwd` set |
| `armillary start` | Streamlit dashboard reading from cache: filters, search bar, per-project detail page, launcher dropdown, recent commits, README, notes, ADRs |
| `armillary export-index` | Markdown table of all cached projects for ingestion by Claude Code / Codex / any AI tool |
| `armillary install-claude-bridge` | Writes `~/.claude/armillary/repos-index.md` and (with `--with-claude-md`) appends an `@import` line to `~/.claude/CLAUDE.md` so every Claude Code session loads the project table |
| `armillary install-khoj` | Pip-installs the Khoj package **and** provisions a `pgvector/pgvector:pg15` Docker container (`khoj-pg`) with the `vector` extension enabled. Requires Docker. |
| `armillary start-khoj` | Ensures `khoj-pg` is running, exports `POSTGRES_*` env vars, and execs the Khoj server in the foreground |

The status heuristic labels each project as **ACTIVE / PAUSED /
DORMANT / IDEA / IN_PROGRESS** based on commit recency, dirty file
count, and `TODO.md` checkboxes.

## What is **not** implemented yet

These are in PLAN.md but deferred until needed:

- **Auto-write into Claude Code memory** (PLAN.md §6 v2 — M7b). The
  `armillary export-index` command produces the markdown file; the
  remaining piece is wiring it into `~/.claude/CLAUDE.md` automatically.
- **Tags and groups** for user-defined project metadata layered on
  auto-discovery (PLAN.md §6 v2)
- **In-dashboard per-project notes** like "frozen until Q3" (§6 v2)
- **`adr-tools` format support** that parses ADR structures into a
  timeline rather than just listing the files (§6 v2)
- **`SessionStart` hook** that auto-launches the dashboard with
  Claude Code (§6 v2)
- All §7 v3 ideas: graph view of project relationships, link
  detection, weekly changelog aggregator, AI-generated 2-sentence
  descriptions, notifications, multi-machine sync

The plan itself (`PLAN.md`) is gitignored — kept private as a
working document. The scope here is the public commitment.

## Configuration

`armillary config --init` creates `~/.config/armillary/config.yaml`
populated with whatever umbrellas it found under `~/`. The file looks
like:

```yaml
umbrellas:
  - path: ~/Projects
    label: Projects
    max_depth: 3
  - path: ~/projects_prod
    label: projects_prod
    max_depth: 3

# Optional: override a built-in launcher or add your own.
# Built-ins (claude-code, codex, cursor, zed, vscode, terminal, finder)
# are always available.
#
# launchers:
#   nvim:
#     label: Neovim
#     command: nvim
#     args: ["{path}"]
#     icon: "✏️"

# Optional: opt in to Khoj semantic search.
#
# khoj:
#   enabled: true
#   api_url: http://localhost:42110
```

## Development

```bash
# install runtime + dev dependencies (pytest, ruff)
uv sync --extra dev

# run the test suite (225 tests covering scanner / metadata / status /
# cache / config / launcher / search / exporter / bootstrap / CLI)
.venv/bin/python -m pytest

# lint + format
.venv/bin/ruff check .
.venv/bin/ruff format --check .

# focused test runs
.venv/bin/python -m pytest tests/test_scanner.py -v
```

CI runs `pytest` + `ruff check` + `ruff format --check` on every PR
and push to `main` against Python 3.11 and 3.12.

## Roadmap

- ✓ Project scaffolding — CLI, package layout, dashboard stub *(M1)*
- ✓ Auto-discovery scanner *(M2)*
- ✓ Project hardening — CI workflow, ruff config *(M2.5)*
- ✓ SQLite cache + `armillary list` *(M3.1)*
- ✓ Metadata extraction (git info, README, ADRs, notes, ahead/behind, size, file count) and status heuristics *(M3.2)*
- ✓ Streamlit dashboard reading from cache, with search bar, launcher dropdown, recent commits, notes section *(M4)*
- ✓ Configuration file (`armillary config`) and launcher integration (`armillary open`) *(M5)*
- ✓ Search backends — ripgrep default, optional Khoj with automatic fallback *(M6)*
- ✓ `repos-index.md` exporter for AI tools *(M7a)*
- ✓ Interactive bootstrap — `armillary config --init` scans `~/` and prompts for umbrellas
- ◌ Auto-write into Claude Code memory (`@armillary/repos-index.md` import) *(M7b — deferred)*
- ◌ Tags and groups, in-dashboard notes, ADR timeline parsing *(v2 — deferred)*

## License

MIT (see [LICENSE](LICENSE))

## Contributing

The MVP is in place but the project is still single-author and
shaped by daily-use feedback. External contributions are not yet
solicited; contribution guidelines and an issue triage policy will
land once a couple of people other than the author are using it
regularly.

In the meantime, bug reports through GitHub Issues are welcome —
especially "this scanner heuristic gets my filesystem layout wrong"
or "the dashboard does the wrong thing when X". Reproductions
against a fake `tmp_path` tree are most useful.
