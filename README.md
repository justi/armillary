# armillary

> 💎 Never forget a side project again. Scans your folders, tells you what's rotting, and which forgotten hours are worth reviving.
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
                     status: ACTIVE / STALLED /      terminal table, sortable
                             DORMANT / IDEA
                                                 🩺  armillary pulse
                                                     weekly changes across your portfolio
```

**Status:** Alpha. Daily-driver-ready on macOS / Linux.

## See it in action

`armillary next` — what should I work on today?

```ansi
[32m❯[0m [1marmillary next[0m
[90mYesterday:[0m alpha-app, my-saas

[33m🔥 [1;37malpha-app[0m  [90m~/Projects/alpha-app[0m
  [2mDashboard for small SaaS revenue monitoring.[0m
  [1;37m48h invested[0m, last commit [32mtoday[0m — keep shipping.
  Activity      [32m▆▇[0m [90m(6mo)[0m
  [36m→[0m cd ~/Projects/alpha-app

[33m⚠️  [1;37mmy-saas[0m  [90m~/projects_prod/my-saas[0m
  [2mSubscription box for niche hobby market.[0m
  [1;37m32h invested[0m, no commit in [33m18d[0m — kill or ship?
  Activity  [33m▇▅▃▁[0m     [90m(6mo)[0m
  [36m→[0m cd ~/projects_prod/my-saas

[35m💎 [1;37mspeak-faster[0m  [90m~/Projects/speak-faster[0m
  [2mVoice latency benchmark harness for STT engines.[0m
  [1;37m47h invested[0m, abandoned [35m4 months ago[0m. Finish with AI or archive?
  Activity  [90m▆▄▁[0m      [90m(6mo)[0m
  [36m→[0m cd ~/Projects/speak-faster
  [36m→[0m armillary next --skip speak-faster
```

`armillary pulse` — weekly check-in:

```ansi
[32m❯[0m [1marmillary pulse[0m
[1;37mWorked on:[0m
  🔨 [36malpha-app[0m — active this week · [1;37m48h invested[0m
  🔨 [36mresearch-notes[0m — active this week · [1;37m12h invested[0m
  🔨 [36mmy-saas[0m — active this week · [1;37m32h invested[0m

[1;37mWent dormant:[0m
  💤 [90mold-prototype[0m — went dormant this week
  💤 [90mai-playground[0m — went dormant this week

[1;37mUncommitted work:[0m
  ⚠️  [33mmy-saas[0m — 12 uncommitted files
  ⚠️  [33mexperiments[0m — 3 uncommitted files
```

`armillary context <name>` — instant re-entry:

```ansi
[32m❯[0m [1marmillary context my-saas[0m
  [1;37mmy-saas[0m on [36mmain[0m — [33mSTALLED[0m — [1;37m32.0h[0m · [33mtrending down[0m
  [90m~/projects_prod/my-saas[0m
  [2m> Subscription box for niche hobby market.[0m
  Age [1;37m14mo[0m
  Activity      [33m▇▅▃▁[0m [90m(6mo)[0m

  Last session  [1;37m1.4h[0m, 3 commits, 18 days ago

  [1;37mLast commits[0m
  [36ma3f9e12[0m  [90m18 days ago[0m      wip: pricing page redesign
  [36m8c12f04[0m  [90m19 days ago[0m      feat: annual plan discount calc
  [36m2e88a01[0m  [90m3 weeks ago[0m      fix: stripe webhook race condition

  [1;37mRecent branches[0m
  [36mfeat/annual-plans[0m              3 weeks ago
  [36mexperiment/webflow-embed[0m       2 months ago

  4 local branches · [33m2 unmerged[0m
    [33mfeat/annual-plans[0m
    [33mexperiment/webflow-embed[0m
```

Your AI coding agent sees the same data through MCP — ask Claude Code _"what should I work on?"_ and it calls `armillary_next` under the hood.

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
- **Weekly pulse** — what changed, what went dormant, what's waiting (`pulse`)
- **Activity heatmap** — 12-month contribution view, exportable as a shareable HTML card (`card`)
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
| `armillary pulse` | Weekly pulse — what you worked on, what went dormant, what's waiting |
| `armillary search "<query>"` | ripgrep across all projects |
| `armillary list` | Rich terminal table with `--status`, `--type`, `--umbrella` filters |
| `armillary open <name>` | Launch project in configured editor (`--target cursor`/`vscode`/`zed`) |
| `armillary archive <name>` / `activate <name>` | Mark project done / restore to automatic status |
| `armillary exclude <name>` / `include <name>` | Hide / unhide a project across all armillary output |
| `armillary purpose <name>` | Set or show a project's one-line purpose |
| `armillary share` / `card` | Generate shareable tweet / heatmap HTML card |
| `armillary config --init` | First-run setup: umbrella picker → scan → Claude Code bridge → MCP |
| `armillary scan` | Full scan of all umbrellas, persist to cache |
| `armillary start` | Incremental scan + Streamlit dashboard |
| `armillary install-claude-bridge` | Write compact `~/.claude/armillary/repos-index.md` |
| `armillary mcp-serve` | MCP server (stdio) for AI coding agents |

## MCP server for AI coding agents

armillary exposes five MCP tools that Claude Code / Cursor can call:

| Tool | What it does | Speed |
|---|---|---|
| `armillary_next` | What should I work on today? | instant |
| `armillary_context` | Where was I? Branch, dirty files, recent commits | sub-second |
| `armillary_search` | Exact code search: function names, imports, error messages | <10ms |
| `armillary_projects` | List all projects with path, status, description | instant |
| `armillary_pulse` | What changed in my portfolio this week? | instant |

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

# 375 tests covering scanner / metadata / status / cache / config /
# launcher / search / exporter / bootstrap / CLI / MCP / next / context /
# pulse / share / heatmap / transitions / purpose / revenue
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
