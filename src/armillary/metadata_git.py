"""Git-derived metadata: branch, head commit, dirty count, ahead/behind,
commit stats, velocity (ADR 0017), monthly activity, first commit.
Split out of ``metadata.py`` for the 400-line architecture target
(ADR 0001 rule 3).
"""

from __future__ import annotations

import contextlib
import time
from datetime import datetime
from pathlib import Path

import git

from .models import ProjectMetadata

# Maximum gap between two consecutive commits that still counts as
# "working time". Gaps longer than this are assumed to be breaks
# (lunch, errands, next day) and excluded from the sum. 4 hours is
# conservative — a 5-hour gap between commits almost certainly means
# the developer took a real break, not that they sat coding the
# whole time without committing.
_WORK_SESSION_GAP_SECONDS = 4 * 3600  # 4 hours

_WORK_HOURS_COMMIT_LIMIT = 2000

_VELOCITY_WEEKS = 4
_SECONDS_PER_WEEK = 7 * 86400

_ACTIVITY_MONTHS = 6
_SECONDS_PER_MONTH = 30 * 86400


def _fill_git_fields(repo_path: Path, md: ProjectMetadata) -> None:
    """Populate the git-specific fields on `md` from a repo at `repo_path`.

    Wrapped by `extract()` in a try/except, so individual exceptions
    here are fine — they just abort the rest of the git fill.
    """
    repo = git.Repo(repo_path)

    # Branch name (None when in detached HEAD state, e.g. mid-rebase).
    if not repo.head.is_detached:
        try:
            md.branch = repo.active_branch.name
        except (TypeError, ValueError):
            md.branch = None

    # HEAD commit — use rev-parse via repo.head.commit which is cheap.
    head = repo.head.commit
    md.last_commit_sha = head.hexsha
    md.last_commit_ts = datetime.fromtimestamp(head.committed_date)
    md.last_commit_author = head.author.name

    # Dirty count: anything that would show up under `git status`.
    # `index.diff(None)` covers unstaged working-tree edits, but misses
    # files that are staged-but-uncommitted — we need `index.diff("HEAD")`
    # for those, otherwise `git add some-file && armillary scan` would
    # incorrectly look like a clean repo.
    try:
        unstaged = len(repo.index.diff(None))
    except Exception:  # noqa: BLE001
        unstaged = 0
    try:
        staged = len(repo.index.diff("HEAD"))
    except Exception:  # noqa: BLE001
        staged = 0
    try:
        untracked = len(repo.untracked_files)
    except Exception:  # noqa: BLE001
        untracked = 0
    md.dirty_count = unstaged + staged + untracked

    # Ahead/behind vs upstream tracking branch. None of these fields
    # apply for repos with no upstream configured (the common case for
    # local-only branches), so an absent tracking branch leaves both
    # fields as None rather than 0.
    if md.branch is not None:
        with contextlib.suppress(Exception):
            tracking = repo.active_branch.tracking_branch()
            if tracking is not None:
                md.ahead = sum(1 for _ in repo.iter_commits(f"{tracking}..HEAD"))
                md.behind = sum(1 for _ in repo.iter_commits(f"HEAD..{tracking}"))

    # Commit count + estimated work hours from commit timestamps.
    with contextlib.suppress(Exception):
        md.commit_count, md.work_hours = _compute_commit_stats(repo)

    # S1: Commit velocity — 4-week window (ADR 0017).
    with contextlib.suppress(Exception):
        md.commit_velocity, md.velocity_trend = _compute_velocity(repo)

    # S5: First commit timestamp (ADR 0017).
    with contextlib.suppress(Exception):
        md.first_commit_ts = _first_commit_timestamp(repo)

    # Monthly activity: commit counts per month, last 6 months.
    with contextlib.suppress(Exception):
        md.monthly_commits = _monthly_activity(repo)

    # S6: Branch count + has_remote (ADR 0017).
    with contextlib.suppress(Exception):
        md.branch_count = len(repo.branches)
    with contextlib.suppress(Exception):
        md.has_remote = bool(repo.remotes)


def _compute_commit_stats(repo: git.Repo) -> tuple[int, float]:
    """Return (commit_count, estimated_work_hours).

    Two separate git invocations, both fast:

    1. `git rev-list --count --all` for the exact commit count.
       Reads from the packfile index — instant even on 100k+ repos.
    2. `git log --format=%at -n 2000` for the work-hours estimate.
       Limited to the most recent 2000 commits so large repos
       (ensembl: 21k, matchmaker: 6k) don't block the scan for
       seconds. 2000 commits covers months of active work which is
       more than enough for an orientation metric.

    Work-hours algorithm: sort timestamps ascending, sum inter-commit
    gaps shorter than `_WORK_SESSION_GAP_SECONDS` (4 h). Gaps longer
    than that are assumed to be breaks (lunch, sleep, next day).
    """
    count_raw = repo.git.rev_list("--count", "--all")
    commit_count = int(count_raw.strip()) if count_raw.strip() else 0

    if commit_count == 0:
        return 0, 0.0

    raw = repo.git.log("--format=%at", f"-n{_WORK_HOURS_COMMIT_LIMIT}")
    if not raw.strip():
        return commit_count, 0.0

    timestamps = sorted(int(ts) for ts in raw.strip().splitlines())

    total_work_seconds = 0
    for i in range(1, len(timestamps)):
        gap = timestamps[i] - timestamps[i - 1]
        if 0 < gap < _WORK_SESSION_GAP_SECONDS:
            total_work_seconds += gap

    work_hours = round(total_work_seconds / 3600, 1)
    return commit_count, work_hours


def _compute_velocity(repo: git.Repo) -> tuple[list[int], str]:
    """Return (commit_counts_per_week, trend) for the last 4 weeks.

    commit_counts_per_week: [week4_ago, week3_ago, week2_ago, week1_ago]
    trend: "rising" / "falling" / "flat" / "dead"
    """
    now = int(time.time())
    raw = repo.git.log(
        "--format=%at",
        f"--since={_VELOCITY_WEEKS * 7} days ago",
    )
    if not raw.strip():
        return [0] * _VELOCITY_WEEKS, "dead"

    timestamps = [int(ts) for ts in raw.strip().splitlines()]
    buckets = [0] * _VELOCITY_WEEKS
    for ts in timestamps:
        age = now - ts
        week_idx = min(age // _SECONDS_PER_WEEK, _VELOCITY_WEEKS - 1)
        # bucket 0 = oldest week, bucket 3 = most recent
        buckets[_VELOCITY_WEEKS - 1 - week_idx] += 1

    return buckets, _classify_trend(buckets)


def _classify_trend(buckets: list[int]) -> str:
    """Classify a 4-week velocity vector into a human-readable trend."""
    if all(b == 0 for b in buckets):
        return "dead"
    # Compare first half vs second half
    first_half = sum(buckets[: len(buckets) // 2])
    second_half = sum(buckets[len(buckets) // 2 :])
    if second_half > first_half * 1.5:
        return "rising"
    if first_half > second_half * 1.5:
        return "falling"
    return "flat"


def _monthly_activity(repo: git.Repo) -> list[int]:
    """Return commit counts per month for the last 6 months [oldest..newest]."""
    now = int(time.time())
    raw = repo.git.log(
        "--format=%at",
        f"--since={_ACTIVITY_MONTHS * 30} days ago",
    )
    buckets = [0] * _ACTIVITY_MONTHS
    if not raw.strip():
        return buckets
    for line in raw.strip().splitlines():
        age = now - int(line)
        idx = min(age // _SECONDS_PER_MONTH, _ACTIVITY_MONTHS - 1)
        buckets[_ACTIVITY_MONTHS - 1 - idx] += 1
    return buckets


def _first_commit_timestamp(repo: git.Repo) -> datetime | None:
    """Return the timestamp of the very first commit in the repo.

    Note: `git log --reverse -1` does NOT work — `-1` limits BEFORE
    reverse is applied. Use `--diff-filter` with rev-list instead.
    """
    raw = repo.git.rev_list("--max-parents=0", "HEAD", "--format=%at")
    if not raw.strip():
        return None
    # rev-list --format outputs "commit <sha>\n<format>" per entry;
    # take the first timestamp line (oldest root commit).
    for line in raw.strip().splitlines():
        if line.startswith("commit "):
            continue
        try:
            return datetime.fromtimestamp(int(line.strip()))
        except ValueError:
            continue
    return None
