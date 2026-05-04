"""Compose revive output with matching code from other repositories.

Query strategy v0.1: the project name is the only signal we send to
``steal()``. Empirically this gives 5–8 cross-repo matches for typical
underscore / dash naming because FTS5's tokeniser splits separators
into meaningful sub-tokens. An earlier draft also folded in the last
commit subject, but a 6-token AND query returns zero hits in practice.
Single-token ranking is the simplest thing that delivers real value.
"""

from __future__ import annotations

from pathlib import Path

from armillary.cache import Cache
from armillary.exclude_service import is_excluded
from armillary.models import Status
from armillary.revive_service import revive_show
from armillary.status_override import get_override
from armillary.steal_service import steal


def generate_enhanced_brief(
    project_path: Path,
    *,
    steal_limit: int = 3,
    timeout: float = 5.0,
) -> str:
    """Compose vanilla `revive show` brief plus matching code blocks.

    Returns the revive brief as markdown. When matches are found, appends
    a STEAL_HITS section with up to ``steal_limit`` quoted blocks from
    other repositories. Propagates ReviveError from ``revive_show``.
    """
    project_path = project_path.expanduser()
    brief = revive_show(project_path, timeout=timeout)
    query = _project_name(project_path)
    if not query:
        return brief

    # Overfetch and filter so we can drop:
    # 1. Hits from the project being revived (the tool promises quotes
    #    from OTHER repos).
    # 2. Hits from repos the user has excluded or archived via the panel.
    #    `armillary_steal` keeps those on purpose (user explicitly mining
    #    their own dead code), but for revive the panel choice should
    #    apply, matching every other MCP tool's behaviour.
    #
    # Multiplier sized for worst case: `steal()` caps at 3 hits per repo
    # in its overfetch pool, so three noise repos (own + excluded dupe +
    # archived dupe) can consume 9 results. `steal_limit * 6` leaves
    # `steal_limit` worth of headroom even in that case.
    own_repo = _resolve(project_path)
    try:
        raw = steal(query, limit=steal_limit * 6)
    except Exception:  # noqa: BLE001 — graceful: enhanced is bonus over vanilla
        # Steal can raise on missing/corrupt code_index.db or a SQLite
        # build without FTS5. The vanilla brief is still useful on its
        # own, so swallow the failure and fall back to brief-only.
        return brief

    results = [r for r in raw if _is_keepable(r.block.repo_path, own_repo)][
        :steal_limit
    ]
    if not results:
        return brief

    entries = []
    for result in results:
        block = result.block
        symbol = block.symbol or "(no symbol)"
        entries.append(
            "\n".join(
                [
                    f"- {result.project_name}/{_display_path(block)}:"
                    f"{block.start_line}-{block.end_line} — {symbol}",
                    f"```{block.language_ext}",
                    block.content,
                    "```",
                ]
            )
        )

    rendered_entries = "\n\n".join(entries)
    return (
        f"{brief}\n\n"
        "## STEAL_HITS — code you wrote in other repos\n\n"
        f"{rendered_entries}"
    )


def _display_path(block: object) -> str:
    """Render ``block.path`` as a short repo-relative string.

    The scanner stores ``CodeBlock.path`` as an absolute path; left raw
    that produces redundant output like ``invoicer//Users/.../invoicer/src/x``.
    Strip the ``repo_path`` prefix when present so the bullet line reads
    ``invoicer/src/x``. Use ``Path.relative_to`` (not raw string
    ``startswith``) to avoid false-positive prefix collisions like
    ``/repos/app`` vs ``/repos/app2/src/x.py``. Fall back to the
    absolute path on any structural surprise so we never lose the
    file pointer.
    """
    block_path = str(getattr(block, "path", ""))
    repo_path = str(getattr(block, "repo_path", ""))
    if not repo_path:
        return block_path
    try:
        rel = Path(block_path).relative_to(repo_path)
    except ValueError:
        return block_path
    return str(rel)


def _resolve(path: Path) -> str:
    """Resolve a path, falling back to the literal string when stat fails."""
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _is_keepable(repo_path: str, own_repo: str) -> bool:
    """True if a steal result should appear in STEAL_HITS.

    Drops the project being revived (own-repo filter), repos the user
    has excluded via the panel, and repos the user archived via status
    override. Other MCP tools honour the same panel choices.
    """
    if _resolve(Path(repo_path)) == own_repo:
        return False
    if is_excluded(repo_path):
        return False
    return get_override(repo_path) is not Status.ARCHIVED


def _project_name(project_path: Path) -> str:
    """Return the cached project name, or the final path part."""
    with Cache() as cache:
        project = cache.get_project(project_path)
    if project is not None:
        return project.name
    return project_path.name
