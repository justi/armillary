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
     deploy-tool/           │   SQLite cache    │           ripgrep + semantic search
                            │                   │
   ~/code/                  └───────────────────┘       🚀  armillary open <name>
     experiments/                    │                      Cursor / Zed / VS Code / ...
     ...                             │
                                     ▼                  🤖  armillary mcp-serve
                          status: ACTIVE / PAUSED /         AI agents query your repos
                                  DORMANT / IDEA /
                                  IN_PROGRESS
```

**Status:** Alpha. Daily-driver-ready on macOS / Linux. Scanner, SQLite cache, metadata extraction with status heuristics, Streamlit dashboard, ripgrep search, MCP server for AI coding agents, Claude Code bridge.

## What is this?

`armillary` is a **knowledge layer** for solo entrepreneurs and developers who accumulate dozens to hundreds of projects over years. It's not a dashboard for 5 active projects — it's **archaeological memory for your entire codebase history**.

- **Auto-discovers** every project in your umbrella folders (git repos and loose idea folders)
- **Shows metadata** for each: status, branch, commits, work hours, dirty files, README, ADRs, notes
- **Infers "where it stands"** — ACTIVE / PAUSED / DORMANT / IDEA / IN PROGRESS
- **Launches** each project into Cursor, VS Code, Zed, Claude Code, Codex, terminal, Finder
- **Searches** across ALL projects — literal (ripgrep)
- **MCP server** — Claude Code / Cursor query your repos programmatically ("search before build")
- **Claude Code bridge** — every AI session knows your full project table automatically

## Non-goals

`armillary` is **not**:

- Another git GUI — use [Sourcetree](https://www.sourcetreeapp.com/) / [Fork](https://git-fork.com/) for that
- Another IDE — Claude Code / Cursor / Zed already cover that
- Another note-taking tool — Obsidian / Logseq already cover that
- A code editor — it only **launches** projects in external editors
- A cloud service — everything stays local, offline-first

## Prerequisites

- **Python 3.11+** (managed by `uv`)
- **Git** — armillary reads repo metadata through GitPython
- **ripgrep** — the search backend (`brew install ripgrep`)

## Installation

Not yet published to PyPI. To run from source:

```bash
git clone git@github.com:justi/armillary.git
cd armillary
uv sync
.venv/bin/armillary --help
```

## Quick start

```bash
# 1. First-run setup: scans ~/ for umbrella folders, runs initial
#    scan, detects Claude Code, configures MCP server.
armillary config --init

# 2. Browse — dashboard auto-scans on start.
armillary start                # opens http://localhost:8501
```

That's it. Two commands from zero to dashboard.

The init ceremony:
1. Discovers umbrella folder candidates under `~/`
2. Interactive picker — choose which folders to scan
3. Runs initial scan + metadata extraction
4. Checks launcher availability (Cursor, VS Code, etc.)
5. Detects `~/.claude/` → installs repos-index bridge + **configures MCP server** in `mcp.json`

## Commands

| Command | What it does |
|---|---|
| `armillary config --init` | First-run setup: umbrella picker → scan → Claude Code bridge → MCP config |
| `armillary config` | Edit config in `$EDITOR` |
| `armillary start` | Incremental scan + Streamlit dashboard |
| `armillary scan` | Full scan of all umbrellas, persist to cache |
| `armillary list` | Rich terminal table with `--status`, `--type`, `--umbrella` filters |
| `armillary next` | What should I work on today? 3 suggestions: momentum, zombies, forgotten gold |
| `armillary search "<query>"` | ripgrep across all projects |
| `armillary open <name>` | Launch project in configured editor (`--target cursor`/`vscode`/`zed`/...) |
| `armillary install-claude-bridge` | Write compact `~/.claude/armillary/repos-index.md` + optional CLAUDE.md import |
| `armillary mcp-serve` | MCP server (stdio) — AI agents query your repos |

## MCP server for AI coding agents

armillary exposes three MCP tools that Claude Code / Cursor / Codex can call:

| Tool | Backend | Use case | Speed |
|---|---|---|---|
| `armillary_next` | SQLite cache | What should I work on today? Momentum, zombies, forgotten gold | instant |
| `armillary_search` | ripgrep | Exact matches: function names, imports, error messages | <10ms |
| `armillary_projects` | SQLite cache | List all projects with metadata, optional status filter | instant |

Every result includes project metadata (path, status, description) so the AI agent can assess whether to reuse code.

`armillary config --init` auto-configures MCP in `~/.claude/mcp.json`. Or manually:

```json
{
  "mcpServers": {
    "armillary": {
      "command": "/path/to/venv/bin/armillary",
      "args": ["mcp-serve"]
    }
  }
}
```

## Privacy

`armillary` **never sends data off-device**. Project index, metadata, cache, and config all live on your local disk.

- No telemetry, no analytics, no external calls
- All documentation uses symbolic placeholders, never real paths

## Development

```bash
uv sync --extra dev

# 313 tests covering scanner / metadata / status / cache / config /
# launcher / search / exporter / bootstrap / CLI / MCP / next
.venv/bin/python -m pytest

# lint + format
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

CI runs pytest + ruff on Python 3.11 and 3.12.

## Architecture

Key design decisions:

- **Thin Streamlit UI** — presentation only, logic in importable services
- **Incremental scan** — mtime compare, 1–2s vs 20+s full scan
- **SQLite cache** — drop and rebuild, no migrations (`PRAGMA user_version`)
- **Work-hours estimation** — commit timestamp gaps (4h threshold)
- **MCP server** — search + project listing for AI agents
- **Response safety** — 20k char cap, preview truncation, compact JSON

## License

MIT (see [LICENSE](LICENSE))
