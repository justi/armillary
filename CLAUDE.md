# armillary — project rules

## Stack

- Python 3.11+, typer CLI, Streamlit dashboard, GitPython, Pydantic v2, SQLite, ruff, PyYAML
- Tests: `.venv/bin/python -m pytest -q` (ALWAYS run before completing a task)
- Lint: `.venv/bin/ruff check .` + `.venv/bin/ruff format --check .`
- Dashboard at `src/armillary/ui/` — Streamlit, routed via `st.query_params`

## Architecture rules (ADR 0001)

These rules are mandatory for every change. They exist to keep the
codebase maintainable as it grows. Violating them creates tech debt
that compounds fast.

### 1. Streamlit is a thin presentation layer

Code that imports `streamlit` may ONLY:
- render widgets and layouts
- read/write `st.session_state` and `st.query_params`
- display success/error messages
- call application services (never implement domain logic inline)

If you need an `if/else` longer than 5 lines that is not about
rendering, it belongs in a service module, not in the UI.

### 2. Application logic lives in small, importable services

Operations like scanning, searching, saving config, computing
metrics, or running a launcher must be plain functions in service
modules under `src/armillary/`. They must be importable and
testable without Streamlit.

The UI calls services. Services never import `streamlit`.

### 3. One module, one responsibility

Do NOT grow `app.py`, `cli.py`, or any file beyond ~400 lines. If a
function does not fit naturally in the current module, create a new
one. The target layout for the dashboard:

```
ui/
  app.py          — entrypoint + routing (thin)
  overview.py     — project table + filters
  detail.py       — single project view
  settings.py     — config editor tabs
  search.py       — search bar + results
  sidebar.py      — sidebar with filters + action buttons
  helpers.py      — shared small utilities (_shorten_home, etc.)
```

### 4. Prefer typed models over dict[str, Any]

Data crossing layer boundaries must use dataclasses, Pydantic models,
or NamedTuples — not bare dicts. `dict[str, Any]` is acceptable only
for short-lived local transforms (< 10 lines scope).

### 5. Module import must not execute the application

Importing a module must have zero side effects: no filesystem access,
no network calls, no `st.set_page_config()` outside the entrypoint.
The only exception is Streamlit's `app.py` entrypoint where
`st.set_page_config()` is required by Streamlit at the top.

### 6. Cache exposes small, purposeful reads

`Cache` should offer methods matched to real UI needs:
- `get_project(path)` — single project by path
- `last_scan_time()` — latest `last_scanned_at` across all rows
- `overview_rows()` — lightweight read-model for the overview table

Do not force views to load all projects and filter in Python when
SQL can do it.

### 7. Centralize shared UI actions

Repeatable sequences (save config + clear cache + rerun, trigger
scan + show spinner + refresh, navigate between views) must live
in shared helpers. Do not duplicate 5-line action sequences across
multiple view functions.

### 8. Testability is a hard requirement

New logic must be testable without running Streamlit. If a function
is hard to test, extract the logic out of the rendering code.

### 9. Performance follows architecture

Do not optimize at the cost of readability. First: good APIs, typed
data, testable functions, small modules. Then optimize the measured
hot spots within those boundaries.

## Incremental scan (ADR 0002)

`armillary start` runs an incremental pre-scan: compare mtime against
cache, extract metadata only for changed projects. Full scan available
via `armillary scan` or the dashboard "Scan now" button.

## Cache schema (ADR 0004)

No migrations. `PRAGMA user_version` bump → drop + rebuild table.
Fields not used in WHERE/ORDER go into `metadata_json` blob. New
fields must NOT require a schema version bump unless they need their
own SQL column.

## Visibility rule

Every user-facing feature must be clickable in the UI or set up during
`config --init`. CLI-only / YAML-only features do not count as shipped.

## Commit conventions

- Run tests + lint before every commit
- Commit messages: imperative mood, explain WHY not WHAT
- Co-Authored-By line for AI-assisted commits
