# armillary

> Total recall for everything you've ever built.
>
> *An armillary sphere is an ancient astronomical instrument — concentric rings modeling the celestial sphere, with a fixed center and orbits turning around it. You are the center, your projects orbit around you, and `armillary` lets you see the whole system at once.*

```text
   50-200 projects over years                    What armillary gives you
   ─────────────────────────                     ────────────────────────

   ~/Projects/                                   🧭  armillary next
     alpha-app/                                      "what should I work on today?"
     speak-faster/      ┌───────────────────┐
     old-prototype/     │                   │    🔄  armillary context <name>
                        │     armillary     │        "where was I on this project?"
   ~/projects_prod/     │                   │
     my-saas/      ────▶│  scan + index +   │──▶ 🔍  armillary search "needle"
     side-project/      │   SQLite cache    │        ripgrep across all repos
                        │                   │
   ~/code/              └───────────────────┘    🤖  MCP server
     experiments/                │                   Claude Code / Cursor query your repos
     ...                        │
                                ▼                📋  armillary list
                     status: ACTIVE / PAUSED /       terminal table, sortable
                             DORMANT / IDEA
```

**Status:** Alpha. Daily-driver-ready on macOS / Linux.

## What is this?

`armillary` is **total recall for prolific builders** — solo developers and creators who accumulate dozens to hundreds of projects over years. Not a dashboard for 5 active projects — a **memory layer** for your entire codebase history.

The daily loop:

```
armillary next      → "what should I work on?"
armillary context   → "where was I on this project?"
armillary search    → "where is this code across all my repos?"
```

Your AI coding agent (Claude Code, Cursor) gets the same data automatically via MCP — no extra commands needed.

### Features

- **Auto-discovers** every project in your umbrella folders (git repos + idea folders)
- **Tracks metadata** — status, branch, commits, work hours, dirty files, README, ADRs, notes
- **Recommends** what to work on — momentum, zombies, forgotten gold (`next`)
- **Restores context** — branch, dirty files, recent commits in sub-second (`context`)
- **Searches** across ALL projects with ripgrep
- **MCP server** — your AI agent knows your full project history
- **Launches** projects into Cursor, VS Code, Zed, Claude Code, terminal, Finder

## Non-goals

`armillary` is **not**:

- A git GUI — use Sourcetree / Fork for that
- An IDE — Claude Code / Cursor / Zed already cover that
- A monitoring tool — use UptimeRobot / Sentry for that
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
# 1. First-run setup: scans ~/ for umbrella folders, runs initial scan,
#    detects Claude Code, configures MCP server.
armillary config --init

# 2. What should I work on today?
armillary next

# 3. Where was I on this project?
armillary context my-project

# 4. Browse — dashboard auto-scans on start.
armillary start
```

## Commands

| Command | What it does |
|---|---|
| `armillary next` | What should I work on today? Momentum, zombies, forgotten gold |
| `armillary context <name>` | Where was I? Branch, dirty files, recent commits — sub-second |
| `armillary search "<query>"` | ripgrep across all projects |
| `armillary list` | Rich terminal table with `--status`, `--type`, `--umbrella` filters |
| `armillary open <name>` | Launch project in configured editor (`--target cursor`/`vscode`/`zed`) |
| `armillary config --init` | First-run setup: umbrella picker → scan → Claude Code bridge → MCP |
| `armillary scan` | Full scan of all umbrellas, persist to cache |
| `armillary start` | Incremental scan + Streamlit dashboard |
| `armillary install-claude-bridge` | Write compact `~/.claude/armillary/repos-index.md` |
| `armillary mcp-serve` | MCP server (stdio) for AI coding agents |

## MCP server for AI coding agents

armillary exposes four MCP tools that Claude Code / Cursor can call:

| Tool | What it does | Speed |
|---|---|---|
| `armillary_next` | What should I work on today? | instant |
| `armillary_context` | Where was I? Branch, dirty files, recent commits | sub-second |
| `armillary_search` | Exact code search: function names, imports, error messages | <10ms |
| `armillary_projects` | List all projects with path, status, description | instant |

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

# 295 tests covering scanner / metadata / status / cache / config /
# launcher / search / exporter / bootstrap / CLI / MCP / next / context
.venv/bin/python -m pytest

# lint + format
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

CI runs pytest + ruff on Python 3.11 and 3.12.

## Architecture

Key design decisions:

- **Three-interface model** — MCP (primary, invisible) > CLI (daily decisions) > Dashboard (companion)
- **Thin Streamlit UI** — presentation only, logic in importable services
- **Incremental scan** — mtime compare, 1–2s vs 20+s full scan
- **SQLite cache** — drop and rebuild, no migrations (`PRAGMA user_version`)
- **Sub-second context** — all git operations local, no network
- **Response safety** — 20k char cap, preview truncation, compact JSON

## License

MIT (see [LICENSE](LICENSE))
