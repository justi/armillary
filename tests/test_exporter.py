"""Tests for `armillary.exporter` — compact bridge index + Claude bridge install.

The bridge file is always compact: only ACTIVE/PAUSED projects, Status + Path
columns, max 15 rows, paths shortened with ~.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from armillary.cache import Cache
from armillary.exporter import render_repos_index, write_repos_index
from armillary.models import Project, ProjectMetadata, ProjectType, Status

_NOW = datetime(2026, 4, 11, 12, 0, 0)


def _project(
    name: str,
    *,
    type: ProjectType = ProjectType.GIT,
    metadata: ProjectMetadata | None = None,
    last_modified: datetime | None = None,
    path: Path | None = None,
) -> Project:
    base = path or Path(f"/tmp/{name}")
    return Project(
        path=base,
        name=name,
        type=type,
        umbrella=base.parent,
        last_modified=last_modified or _NOW,
        metadata=metadata,
    )


# --- render_repos_index ---------------------------------------------------


def test_render_empty_cache() -> None:
    out = render_repos_index([], generated_at=_NOW)
    assert "# armillary — projects index" in out
    assert "Cache is empty" in out


def test_render_shows_only_active_and_paused() -> None:
    projects = [
        _project("active1", metadata=ProjectMetadata(status=Status.ACTIVE)),
        _project("paused1", metadata=ProjectMetadata(status=Status.PAUSED)),
        _project("dormant1", metadata=ProjectMetadata(status=Status.DORMANT)),
        _project(
            "idea1", type=ProjectType.IDEA, metadata=ProjectMetadata(status=Status.IDEA)
        ),
    ]
    out = render_repos_index(projects, generated_at=_NOW)
    assert "**2** ACTIVE/PAUSED project(s)" in out
    assert "/tmp/active1" in out
    assert "/tmp/paused1" in out
    assert "/tmp/dormant1" not in out
    assert "/tmp/idea1" not in out


def test_render_caps_at_15_rows() -> None:
    projects = [
        _project(f"proj-{i}", metadata=ProjectMetadata(status=Status.ACTIVE))
        for i in range(20)
    ]
    out = render_repos_index(projects, generated_at=_NOW)
    assert "**15** ACTIVE/PAUSED project(s)" in out
    assert "/tmp/proj-14" in out
    assert "/tmp/proj-15" not in out
    assert "+5 hidden" in out
    assert "5 ACTIVE" in out


def test_render_shows_hidden_count_with_mcp_hint() -> None:
    projects = [
        _project("active", metadata=ProjectMetadata(status=Status.ACTIVE)),
        _project("dormant", metadata=ProjectMetadata(status=Status.DORMANT)),
        _project("idea", metadata=ProjectMetadata(status=Status.IDEA)),
    ]
    out = render_repos_index(projects, generated_at=_NOW)
    assert "+2 hidden" in out
    assert "1 DORMANT" in out
    assert "1 IDEA" in out
    assert "armillary_projects" in out
    assert "MCP" in out


def test_render_shortens_home_paths() -> None:
    home = Path.home()
    projects = [
        _project(
            "myproj",
            path=home / "Projects" / "myproj",
            metadata=ProjectMetadata(status=Status.ACTIVE),
        ),
    ]
    out = render_repos_index(projects, generated_at=_NOW)
    assert "~/Projects/myproj" in out
    assert str(home) not in out


def test_render_has_status_and_path_columns() -> None:
    projects = [
        _project("x", metadata=ProjectMetadata(status=Status.ACTIVE)),
    ]
    out = render_repos_index(projects, generated_at=_NOW)
    assert "| Status | Path |" in out
    assert "| ACTIVE |" in out
    # No old columns
    assert "| Name |" not in out
    assert "| Branch |" not in out
    assert "| Description |" not in out
    assert "| Dirty |" not in out


def test_render_includes_generated_timestamp() -> None:
    projects = [_project("x", metadata=ProjectMetadata(status=Status.ACTIVE))]
    out = render_repos_index(projects, generated_at=_NOW)
    assert "2026-04-11 12:00:00" in out


def test_render_no_hidden_footer_when_all_visible() -> None:
    projects = [
        _project("a", metadata=ProjectMetadata(status=Status.ACTIVE)),
    ]
    out = render_repos_index(projects, generated_at=_NOW)
    assert "DORMANT/IDEA" not in out


# --- write_repos_index round trip ----------------------------------------


def test_write_roundtrip_through_real_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    active_md = ProjectMetadata(
        branch="main",
        dirty_count=2,
        readme_excerpt="Hello",
        status=Status.ACTIVE,
    )
    dormant_md = ProjectMetadata(status=Status.DORMANT)
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("alpha", metadata=active_md)])
        cache.upsert([_project("beta", metadata=dormant_md)])

    output = tmp_path / "out" / "repos-index.md"
    written = write_repos_index(output, db_path=db_path)

    assert written == 2  # total in cache
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "**1** ACTIVE/PAUSED project(s)" in text
    assert "/tmp/alpha" in text
    assert "/tmp/beta" not in text  # dormant filtered out


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("solo", metadata=ProjectMetadata(status=Status.ACTIVE))])

    output = tmp_path / "deep" / "deeper" / "out.md"
    written = write_repos_index(output, db_path=db_path)
    assert written == 1
    assert output.exists()


def test_write_to_empty_cache_still_writes_a_file(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path):
        pass

    output = tmp_path / "empty.md"
    written = write_repos_index(output, db_path=db_path)
    assert written == 0
    assert output.exists()
    text = output.read_text()
    assert "Cache is empty" in text


# --- install_claude_bridge ------------------------------------------------


def test_install_claude_bridge_writes_repos_index(tmp_path: Path) -> None:
    from armillary.exporter import install_claude_bridge

    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)

    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path) as cache:
        cache.upsert(
            [
                _project("alpha", metadata=ProjectMetadata(status=Status.ACTIVE)),
                _project("beta", metadata=ProjectMetadata(status=Status.ACTIVE)),
            ]
        )

    bridge_path, written, appended = install_claude_bridge(
        home=fake_home,
        db_path=db_path,
        with_claude_md=False,
    )

    assert bridge_path == fake_home / ".claude" / "armillary" / "repos-index.md"
    assert bridge_path.exists()
    assert written == 2
    assert appended is False
    text = bridge_path.read_text()
    assert "alpha" in text
    assert "beta" in text
    assert not (fake_home / ".claude" / "CLAUDE.md").exists()


def test_install_claude_bridge_with_claude_md_creates_file(tmp_path: Path) -> None:
    from armillary.exporter import install_claude_bridge

    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)

    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("solo", metadata=ProjectMetadata(status=Status.ACTIVE))])

    _, _, appended = install_claude_bridge(
        home=fake_home,
        db_path=db_path,
        with_claude_md=True,
    )
    assert appended is True

    claude_md = fake_home / ".claude" / "CLAUDE.md"
    assert claude_md.exists()
    content = claude_md.read_text()
    assert "@armillary/repos-index.md" in content
    assert "armillary projects index" in content


def test_install_claude_bridge_with_claude_md_appends_to_existing(
    tmp_path: Path,
) -> None:
    from armillary.exporter import install_claude_bridge

    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    claude_md = fake_home / ".claude" / "CLAUDE.md"
    original_content = "# My rules\n\n- Always use pytest\n- Never mock the DB\n"
    claude_md.write_text(original_content)

    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("solo", metadata=ProjectMetadata(status=Status.ACTIVE))])

    _, _, appended = install_claude_bridge(
        home=fake_home,
        db_path=db_path,
        with_claude_md=True,
    )
    assert appended is True

    new_content = claude_md.read_text()
    assert original_content.rstrip() in new_content
    assert "@armillary/repos-index.md" in new_content
    assert new_content.index("Always use pytest") < new_content.index("@armillary/")


def test_install_claude_bridge_with_claude_md_is_idempotent(tmp_path: Path) -> None:
    from armillary.exporter import install_claude_bridge

    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)

    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("solo", metadata=ProjectMetadata(status=Status.ACTIVE))])

    _, _, first_appended = install_claude_bridge(
        home=fake_home,
        db_path=db_path,
        with_claude_md=True,
    )
    first_content = (fake_home / ".claude" / "CLAUDE.md").read_text()

    _, _, second_appended = install_claude_bridge(
        home=fake_home,
        db_path=db_path,
        with_claude_md=True,
    )
    second_content = (fake_home / ".claude" / "CLAUDE.md").read_text()

    assert first_appended is True
    assert second_appended is False
    assert first_content == second_content


def test_install_claude_bridge_skips_when_import_line_present(
    tmp_path: Path,
) -> None:
    from armillary.exporter import install_claude_bridge

    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    claude_md = fake_home / ".claude" / "CLAUDE.md"
    claude_md.write_text(
        "# My rules\n\n@armillary/repos-index.md\n\n- don't touch this\n"
    )

    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("solo", metadata=ProjectMetadata(status=Status.ACTIVE))])

    original = claude_md.read_text()
    _, _, appended = install_claude_bridge(
        home=fake_home,
        db_path=db_path,
        with_claude_md=True,
    )
    assert appended is False
    assert claude_md.read_text() == original


def test_install_claude_bridge_repairs_marker_only_state(tmp_path: Path) -> None:
    from armillary.exporter import install_claude_bridge

    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    claude_md = fake_home / ".claude" / "CLAUDE.md"
    claude_md.write_text(
        "# My rules\n\n"
        "# armillary projects index (managed by `armillary install-claude-bridge`)\n"
        "# — (user removed the @import here by mistake) —\n"
    )

    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("solo", metadata=ProjectMetadata(status=Status.ACTIVE))])

    _, _, appended = install_claude_bridge(
        home=fake_home,
        db_path=db_path,
        with_claude_md=True,
    )
    assert appended is True
    assert "@armillary/repos-index.md" in claude_md.read_text()


def test_get_claude_bridge_status_when_nothing_is_installed(tmp_path: Path) -> None:
    from armillary.exporter import get_claude_bridge_status

    fake_home = tmp_path / "home"

    status = get_claude_bridge_status(fake_home)

    assert status.bridge_installed is False
    assert status.claude_md_exists is False
    assert status.claude_md_wired is False
    assert status.bridge_path == fake_home / ".claude" / "armillary" / "repos-index.md"
    assert status.claude_md_path == fake_home / ".claude" / "CLAUDE.md"


def test_get_claude_bridge_status_detects_existing_install_and_wiring(
    tmp_path: Path,
) -> None:
    from armillary.exporter import get_claude_bridge_status, install_claude_bridge

    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)

    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("solo", metadata=ProjectMetadata(status=Status.ACTIVE))])

    install_claude_bridge(home=fake_home, db_path=db_path, with_claude_md=True)
    status = get_claude_bridge_status(fake_home)

    assert status.bridge_installed is True
    assert status.claude_md_exists is True
    assert status.claude_md_wired is True
