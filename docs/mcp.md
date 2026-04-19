# MCP server — how it works

`armillary mcp-serve` exposes your indexed project portfolio to AI
coding agents (Claude Code, Cursor, Codex) as [Model Context
Protocol](https://modelcontextprotocol.io/) tools. This document covers
the runtime model, wire protocol, and what actually happens when the
agent calls a tool.

## What runs where

```
┌────────────────────┐         stdio          ┌──────────────────────────┐
│  Claude Code       │   JSON-RPC 2.0         │  armillary mcp-serve     │
│  (or Cursor /      │ ◀──────────────────────▶  (FastMCP over stdio)    │
│   Codex session)   │  newline-framed        │                          │
└────────────────────┘                        └─────────────┬────────────┘
                                                            │
                                                            ▼
                                              ┌──────────────────────────┐
                                              │  SQLite cache            │
                                              │  ~/Library/.../arm.db    │
                                              └──────────────────────────┘
```

- **Transport is stdio.** `mcp-serve` does not open a port and does not
  listen on HTTP. The subprocess reads messages from stdin and writes
  responses to stdout using the MCP wire protocol (JSON-RPC 2.0,
  newline-delimited).
- **The agent owns the process lifetime.** Claude Code launches
  `armillary mcp-serve` as a subprocess at session start (per your
  `~/.claude/mcp.json`), keeps it alive for the duration of the
  session, and sends it `SIGTERM` on exit.
- **The dashboard is independent.** `armillary start` launches a
  Streamlit server; `armillary mcp-serve` runs separately. Both read
  the same SQLite cache; neither depends on the other.

## Configuration

`armillary config --init` writes the MCP entry for you. If you configure
manually, the block is:

```json
{
  "mcpServers": {
    "armillary": {
      "command": "/absolute/path/to/venv/bin/armillary",
      "args": ["mcp-serve"]
    }
  }
}
```

The file location depends on the agent:

- Claude Code — `~/.claude/mcp.json`
- Cursor — `~/.cursor/mcp.json` (or project-local `.cursor/mcp.json`)
- Codex — `~/.codex/config.toml` under `[mcp_servers.armillary]`

## Tools exposed

Five tools are registered via `@mcp.tool()` in `src/armillary/mcp_server.py`:

| Tool | Signature | Typical latency |
|---|---|---|
| `armillary_next` | `() → JSON` | <50 ms |
| `armillary_context` | `(project_name: str) → JSON` | <500 ms (git log runs) |
| `armillary_search` | `(query: str, max_results: int = 20) → JSON` | <50 ms per repo hit |
| `armillary_projects` | `(status_filter: str \| None) → JSON` | <20 ms |
| `armillary_pulse` | `() → plain text` | <30 ms |

Schemas are introspected from the Python function signatures; the agent
receives them in the `tools/list` response during the MCP handshake.

## Lifecycle

1. **Handshake.** Claude Code sends `initialize` as the first message
   on stdin. FastMCP replies with the server's name, version, and
   capabilities, then emits `initialized`.
2. **Tool discovery.** The agent immediately calls `tools/list`. FastMCP
   returns the five schemas so the agent knows what it can invoke. Any
   system-level prompt instructions declared on the `FastMCP()`
   constructor ride along here — armillary's say *"ALWAYS call
   `armillary_next` at the very start of every conversation,"* which is
   why a fresh Claude Code session surfaces your portfolio without you
   asking.
3. **Idle.** The process blocks on `stdin.readline()`. No CPU, no disk
   I/O, ~30–50 MB resident Python overhead.
4. **Tool call.** The agent sends
   `{"method":"tools/call","params":{"name":"armillary_next", ...}}`.
   FastMCP dispatches to the decorated function, which opens a `Cache()`
   context manager, runs its work, returns a JSON string. FastMCP wraps
   the result in a JSON-RPC response and writes it to stdout.
5. **Repeat.** Each call goes back to idle afterward. Cache connection
   is closed between calls — no persistent state in the process memory.
6. **Shutdown.** Agent closes stdin or sends SIGTERM; FastMCP's run loop
   exits and the process dies.

## Cache staleness semantics

Tool responses mix two sources:

- **Live git data** — `armillary_context` runs `git log` and
  `git status` on each call, so recent commits and dirty-file counts
  are always current.
- **Cached metadata** — `work_hours`, `readme_excerpt`, `status` come
  from the SQLite cache, which is only refreshed by `armillary scan`,
  `armillary start` (which runs an incremental pre-scan), or the
  dashboard's *Scan now* button.

Practical consequence: if you rename a project's README and immediately
ask Claude Code for that project's description, the cached excerpt may
still be the old one. Run `armillary scan` (or refresh the dashboard)
after changes that should propagate.

## Observability

MCP is silent by design — the subprocess's stdout is consumed by the
agent, and stderr is typically swallowed. To debug a misbehaving tool
call, run the server by hand:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | .venv/bin/armillary mcp-serve
```

That prints the registered tool schemas straight to your terminal. For
richer traces, pipe `mcp-serve` through `tee` in the `mcp.json` command
field and tail the log.

## Why FastMCP (and which one)

armillary uses `mcp.server.fastmcp.FastMCP` — the implementation that
ships with the official MCP Python SDK (`pip install mcp`). This is not
the standalone `fastmcp` package. Anthropic internalized that codebase
into the official SDK, so armillary's only MCP dependency is `mcp>=1.0`.
