# Changelog

All notable changes to armillary are tracked here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/) and the project
follows pre-release alpha rules — backwards-compat shims are intentionally
skipped while we are still in 0.x.

## Unreleased

### Added

- **Revive with receipts** — new MCP tool `armillary_revive(project_path)`
  that returns the vanilla `revive show` brief plus a "STEAL_HITS" section
  with up to three quoted code blocks from your other repositories. The
  killer use case: when an agent resumes work on a project, it can quote
  code you already wrote elsewhere instead of rebuilding it from scratch.
  - Why it ships: vanilla `revive` answers "what was I doing?" — but every
    forgotten side project also forgets the code patterns that already
    solved its problems. Pulling matching blocks from your other repos
    turns the brief from "remind me" into "here's the function you wrote
    last January for the same thing".
  - How it works: the tool composes the existing `revive_service.revive_show()`
    output with `steal_service.steal()` matches. Query strategy v0.1 is
    the project name only — empirically the simplest signal that
    delivers cross-repo matches. An earlier draft also folded in the
    last commit subject, but a 6-token AND-FTS query is precision-extreme
    and produced zero matches across a real, sizeable cache.
    Single-token ranking lets underscore- and dash-naming tokenise
    naturally and BM25 picks the best matches. Sanity check on three
    real projects returned 1+ relevant cross-repo block each.
  - Scope decision: only `STEAL_HITS` is in v0.1. Other candidate fields
    (project status, last-touched timestamp, journal entries) were
    deferred — status carries a real risk of eroding trust in the whole
    brief when stale, last-touched is something an agent can read from
    `git log` itself, and journal entries add noise without a proven win.
    A 30-day defeat test gates whether to add them in v0.2.
  - Surface: MCP-only for now (visibility rule satisfied). A dashboard
    surface for the enhanced brief is conditional on the defeat test
    passing in v0.2.

### Changed

- README.md — added a one-line note pointing at the new MCP tool from the
  Revive feature bullet, so MCP-using readers see both the standalone and
  enhanced paths.

### Tests

- New unit + integration coverage for `generate_enhanced_brief`: happy
  path, empty steal results, `ReviveError` propagation,
  project-not-in-cache fallback, top-N capping, no-symbol placeholder,
  `~` expansion, own-repo filtering, repo-prefix collision handling,
  graceful degradation when `steal()` raises, plus three MCP-wrapper
  cases and two end-to-end tests that use a real `Cache` + real
  `CodeIndex` (only the `revive` CLI subprocess is stubbed).

## 2026-04 — recent module split

Refactored three modules above the 400-line target into focused siblings,
keeping public import paths stable:

- `metadata.py` (521 → 110) split into `metadata.py` + `metadata_git.py`
  + `metadata_readme.py` + `metadata_files.py` (PR #34)
- `cache.py` (506 → 393) extracted row mapping into `cache_mapping.py`
  (PR #35)
- `mcp_server.py` (486 → 75) split into `mcp_server.py` + `mcp_helpers.py`
  + `mcp_tools.py` + `mcp_instance.py` (PR #36)

CLAUDE.md tech-debt note updated accordingly.

## 2026-04 — Revive integration (PR #33)

First-class `context-revive` integration: dashboard scaffold + copy-prompt
actions on the project detail page, brief age inline, paired Copy/Launch
buttons. Replaced the earlier headless-runner experiment because the
copy-prompt UX gives users full control over `claude` invocations and
matches `revive`'s "fresh agent session" requirement for the audit pass.
