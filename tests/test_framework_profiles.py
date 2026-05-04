"""Tests for framework_profiles — manifest detection + Profile.accepts."""

from __future__ import annotations

from pathlib import Path

from armillary.framework_profiles import UNKNOWN, Profile, detect_profile

# ----- Profile.accepts ------------------------------------------------------


def test_unknown_profile_accepts_everything() -> None:
    assert UNKNOWN.accepts("anything/here.rb")
    assert UNKNOWN.accepts("db/migrate/2024_init.rb")


def test_include_whitelist_keeps_only_listed_prefixes() -> None:
    p = Profile(name="t", include=("app/", "lib/"))
    assert p.accepts("app/models/user.rb")
    assert p.accepts("lib/services/foo.rb")
    assert not p.accepts("db/migrate/2024_init.rb")
    assert not p.accepts("config/routes.rb")


def test_exclude_overrides_include() -> None:
    p = Profile(
        name="t",
        include=("app/",),
        exclude=("app/assets/builds/",),
    )
    assert p.accepts("app/models/user.rb")
    assert not p.accepts("app/assets/builds/foo.js")


def test_prefix_match_is_directory_aware() -> None:
    """`lib` matches lib/ and lib/x.rb, not library.rb (avoid false positives)."""
    p = Profile(name="t", include=("lib",))
    assert p.accepts("lib/x.rb")
    assert p.accepts("lib")  # exact
    assert not p.accepts("library.rb")
    assert not p.accepts("vendor/lib/x.rb")


# ----- detect_profile -------------------------------------------------------


def test_detect_rails_from_gemfile_with_rails(tmp_path: Path) -> None:
    (tmp_path / "Gemfile").write_text(
        'source "https://rubygems.org"\ngem "rails", "7.1"\n'
    )
    profile = detect_profile(tmp_path)
    assert profile.name == "rails"
    assert "app/" in (profile.include or ())
    assert "spec/integration/" in (profile.include or ())


def test_detect_rails_with_single_quoted_rails_gem(tmp_path: Path) -> None:
    (tmp_path / "Gemfile").write_text("gem 'rails', '~> 7.0'\n")
    assert detect_profile(tmp_path).name == "rails"


def test_detect_ruby_gem_from_gemfile_without_rails(tmp_path: Path) -> None:
    (tmp_path / "Gemfile").write_text('gem "rspec"\ngem "thor"\n')
    profile = detect_profile(tmp_path)
    assert profile.name == "ruby_gem"
    # ruby_gem must not include app/ — that's a Rails-specific dir.
    assert "app/" not in (profile.include or ())


def test_detect_python_web_from_pyproject_with_django(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["django>=5.0"]\n'
    )
    (tmp_path / "src").mkdir()
    profile = detect_profile(tmp_path)
    assert profile.name == "python_web"
    assert "migrations/" in profile.exclude


def test_detect_python_lib_from_plain_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["pydantic>=2"]\n'
    )
    (tmp_path / "src").mkdir()
    assert detect_profile(tmp_path).name == "python_lib"


def test_detect_js_frontend_from_package_json_with_react(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"x","dependencies":{"react":"^18.0.0"}}'
    )
    assert detect_profile(tmp_path).name == "js_frontend"


def test_detect_node_from_plain_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"x","dependencies":{"express":"^4"}}'
    )
    assert detect_profile(tmp_path).name == "node"


def test_no_manifest_falls_back_to_unknown(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# nothing here\n")
    profile = detect_profile(tmp_path)
    assert profile is UNKNOWN
    assert profile.include is None


def test_rails_detection_wins_over_ruby_gem_when_both_signals_present(
    tmp_path: Path,
) -> None:
    """A Rails app technically has both Gemfile + rails dep — must classify as rails."""
    (tmp_path / "Gemfile").write_text('gem "rails"\ngem "rspec"\n')
    assert detect_profile(tmp_path).name == "rails"


# ----- Codex review fixes ---------------------------------------------------


def test_exclude_matches_segment_anywhere_in_path() -> None:
    """`migrations/` excludes Django migrations under src/myapp/, not just root."""
    p = Profile(
        name="t",
        include=("src/", "tests/integration/"),
        exclude=("migrations/",),
    )
    assert not p.accepts("src/myapp/migrations/0001_initial.py")
    assert not p.accepts("migrations/0001_initial.py")
    assert p.accepts("src/myapp/views.py")
    # Segment boundary required — `my_migrations/` is not excluded.
    assert p.accepts("src/my_migrations/foo.py")


def test_multi_segment_exclude_matches_contiguous_run() -> None:
    p = Profile(
        name="t",
        include=("app/",),
        exclude=("app/assets/builds/",),
    )
    assert not p.accepts("app/assets/builds/manifest.js")
    # A stray `app/` deep in the tree does not match — pattern is
    # multi-segment, so contiguity matters.
    assert p.accepts("app/models/user.rb")


def test_pyproject_poetry_syntax_classifies_as_python_web(tmp_path: Path) -> None:
    """Poetry's `django = "^5.0"` form must classify as python_web (Codex #3)."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry.dependencies]\npython = "^3.11"\ndjango = "^5.0"\n'
    )
    (tmp_path / "src").mkdir()
    assert detect_profile(tmp_path).name == "python_web"


def test_pyproject_poetry_does_not_misfire_on_unrelated_keys(tmp_path: Path) -> None:
    """`packages = [...]` must NOT be mistaken for a dep on a flask-shaped key."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\n"
        'packages = ["my_flask_helpers"]\n'
        "[tool.poetry.dependencies]\n"
        'requests = "^2"\n'
    )
    (tmp_path / "src").mkdir()
    assert detect_profile(tmp_path).name == "python_lib"


def test_python_flat_layout_falls_back_to_unknown(tmp_path: Path) -> None:
    """Flat-layout repo (no src/) must classify as UNKNOWN, not python_lib (Codex #2).

    Otherwise the python_lib whitelist drops every source file in
    a repo where the package lives at root.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "mypkg"\ndependencies = ["pydantic"]\n'
    )
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    profile = detect_profile(tmp_path)
    assert profile.name == "unknown"
    # Sanity: the flat package WOULD be indexed under unknown.
    assert profile.accepts("mypkg/foo.py")


def test_manifest_unreadable_falls_back_to_unknown(tmp_path: Path, monkeypatch) -> None:
    """A read failure on Gemfile must NOT silently classify as ruby_gem (Codex #5)."""
    (tmp_path / "Gemfile").write_text('gem "rails"\n')

    def _broken(_self, encoding="utf-8", errors="replace"):  # noqa: ARG001
        raise OSError("torn read")

    monkeypatch.setattr(Path, "read_text", _broken)
    assert detect_profile(tmp_path).name == "unknown"
