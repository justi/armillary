"""Framework-aware code-indexing profiles (ADR 0031).

Detects what kind of project a repo is — Rails, Django, Next, plain
Python lib, etc. — from its manifest files, and returns a ``Profile``
that tells :func:`code_block_service.build_blocks_for_repo` which
directories to feed into the 40-line window machinery.

The fallback profile is ``unknown`` (``include`` = ``None``), which
preserves ADR 0027's original "index everything tracked" behaviour
for repos with no recognisable manifest. Profiles are plain data —
adding a new one means a new entry in :data:`_PROFILES` plus a
detection branch in :func:`detect_profile`. No plugin registry, no
YAML, no runtime config.

Public API: :func:`detect_profile`, :class:`Profile`, :func:`UNKNOWN`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Profile:
    """Indexing profile for a repo: which dirs are code, which are noise.

    ``include`` is a whitelist of path prefixes (relative to repo root,
    POSIX-style, no leading slash). When ``None`` the indexer walks the
    whole tree (current ``unknown`` behaviour). When set, only files
    whose path starts with one of these prefixes are indexed.

    ``exclude`` always wins over ``include`` — useful when a profile
    whitelists ``app/`` but wants to skip ``app/assets/builds/``.
    """

    name: str
    include: tuple[str, ...] | None
    exclude: tuple[str, ...] = field(default_factory=tuple)

    def accepts(self, rel_path: str) -> bool:
        """True if ``rel_path`` (POSIX, no leading slash) survives filtering.

        Includes are anchored to the repo root — ``app/`` whitelists
        ``app/...`` but not ``vendor/app/...``, because we want a
        Rails-shaped layout, not just any directory called "app".

        Excludes are matched **anywhere** in the path: ``migrations/``
        excludes a Django migrations folder whether it lives at the
        root (Django flat-layout) or under ``src/myapp/migrations/``
        (src-layout). The single trip through this matcher per file
        is fine; we are post-`git ls-files` already.
        """
        normalised = rel_path.lstrip("/")
        for ex in self.exclude:
            if _matches_anywhere(normalised, ex):
                return False
        if self.include is None:
            return True
        return any(_matches_prefix(normalised, inc) for inc in self.include)


def _matches_prefix(rel_path: str, prefix: str) -> bool:
    """Match ``rel_path`` against a root-anchored directory prefix.

    A prefix ending with ``/`` matches any path *under* that directory;
    a prefix without ``/`` matches that exact path or any descendant
    (``lib`` matches ``lib/foo.rb`` but not ``library.rb``).
    """
    norm_prefix = prefix.rstrip("/")
    if not norm_prefix:
        return True
    return rel_path == norm_prefix or rel_path.startswith(norm_prefix + "/")


def _matches_anywhere(rel_path: str, pattern: str) -> bool:
    """Match ``pattern`` as a contiguous run of segments anywhere in the path.

    ``migrations/`` matches ``foo/migrations/x.py`` and ``migrations/x.py``,
    but not ``my_migrations/`` (segment boundary required).
    ``app/assets/builds/`` matches only the contiguous run, so a stray
    ``app/`` deep in the tree does not accidentally pull in everything.
    """
    norm = pattern.rstrip("/")
    if not norm:
        return True
    if _matches_prefix(rel_path, norm):
        return True
    needle = "/" + norm + "/"
    if needle in rel_path:
        return True
    return rel_path.endswith("/" + norm)


# ----- profiles -------------------------------------------------------------


UNKNOWN = Profile(name="unknown", include=None, exclude=())

_PROFILES: dict[str, Profile] = {
    "rails": Profile(
        name="rails",
        include=(
            "app/",
            "lib/",
            "spec/integration/",
            "spec/system/",
            "spec/features/",
            "test/integration/",
            "test/system/",
        ),
        exclude=(
            "app/assets/builds/",
            "app/assets/images/",
        ),
    ),
    "ruby_gem": Profile(
        name="ruby_gem",
        include=(
            "lib/",
            "spec/integration/",
            "spec/system/",
            "test/integration/",
        ),
        exclude=(),
    ),
    "python_web": Profile(
        name="python_web",
        include=(
            "src/",
            "tests/integration/",
            "tests/e2e/",
        ),
        exclude=(
            "migrations/",
            "static/",
            "media/",
        ),
    ),
    "python_lib": Profile(
        name="python_lib",
        include=(
            "src/",
            "tests/integration/",
            "tests/e2e/",
        ),
        exclude=(),
    ),
    "js_frontend": Profile(
        name="js_frontend",
        include=(
            "src/",
            "app/",
            "pages/",
            "components/",
        ),
        exclude=(
            "public/",
            ".next/",
            "out/",
        ),
    ),
    "node": Profile(
        name="node",
        include=(
            "src/",
            "lib/",
            "bin/",
        ),
        exclude=(
            "dist/",
            "coverage/",
        ),
    ),
}


# ----- detection ------------------------------------------------------------


def detect_profile(repo_path: Path) -> Profile:
    """Pick the right :class:`Profile` for a repo by inspecting manifests.

    Detection order matters: Rails before plain Ruby (a Rails app has
    a Gemfile too); framework-specific Python before plain Python; JS
    frontend before plain Node. Anything we can't classify, *or* a
    manifest we can't read, falls back to :data:`UNKNOWN` so the
    indexer keeps current behaviour rather than silently classifying
    a torn file as the negative branch.
    """
    try:
        gemfile = repo_path / "Gemfile"
        if gemfile.is_file():
            content = _safe_read(gemfile)
            if content is None:
                return UNKNOWN
            if _gemfile_mentions_rails(content):
                return _PROFILES["rails"]
            return _PROFILES["ruby_gem"]

        pyproject = repo_path / "pyproject.toml"
        if pyproject.is_file():
            content = _safe_read(pyproject)
            if content is None:
                return UNKNOWN
            if _pyproject_mentions_web(content):
                return _python_profile(repo_path, _PROFILES["python_web"])
            return _python_profile(repo_path, _PROFILES["python_lib"])

        package_json = repo_path / "package.json"
        if package_json.is_file():
            content = _safe_read(package_json)
            if content is None:
                return UNKNOWN
            if _package_json_mentions_frontend(content):
                return _PROFILES["js_frontend"]
            return _PROFILES["node"]
    except OSError:
        return UNKNOWN

    return UNKNOWN


def _python_profile(repo_path: Path, base: Profile) -> Profile:
    """Adjust a Python profile for src-layout vs flat-layout.

    The ``src/`` whitelist works for src-layout repos but indexes
    nothing on flat-layout repos (where the package lives at
    ``<root>/<package>/``). When ``src/`` does not exist we fall back
    to :data:`UNKNOWN` rather than apply a whitelist that would drop
    everything — silently indexing zero files is worse than indexing
    everything tracked.
    """
    if (repo_path / "src").is_dir():
        return base
    return UNKNOWN


# ----- manifest sniffers ----------------------------------------------------


def _safe_read(path: Path) -> str | None:
    """Read a manifest as text; ``None`` signals a torn read.

    A read failure (permission, race, encoding without ``replace`` —
    we use ``replace`` so genuine encoding errors do not surface) is
    distinguishable from an empty file; callers should treat ``None``
    as "I can't classify" and fall back to :data:`UNKNOWN`. Returning
    ``""`` would silently take the negative branch of every sniffer
    and misclassify the repo as ``ruby_gem`` / ``python_lib`` / ``node``.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _gemfile_mentions_rails(content: str) -> bool:
    """Match ``gem "rails"`` / ``gem 'rails'`` (any version pin)."""
    lower = content.lower()
    return ('gem "rails"' in lower) or ("gem 'rails'" in lower)


_PYTHON_WEB_DISTS: tuple[str, ...] = ("django", "fastapi", "flask", "starlette")
_JS_FRONTEND_DISTS: tuple[str, ...] = ("react", "next", "vue", "svelte", "nuxt")


def _pyproject_mentions_web(content: str) -> bool:
    """Match a dependency on django / fastapi / flask / starlette.

    Covers both common spellings:

    * PEP 621 / PDM style — quoted strings like ``"django>=5.0"`` in
      a ``dependencies`` array.
    * Poetry style — unquoted keys like ``django = "^5.0"`` under
      ``[tool.poetry.dependencies]``.

    For Poetry we look for ``<dist> =`` at the start of any line, so
    a substring like ``packages = ["my_django_helpers"]`` cannot
    misfire. Scope matches the single-quoted variants too (rare in
    pyproject but cheap to support) and is case-insensitive.
    """
    lower = content.lower()
    if any(f'"{dist}' in lower for dist in _PYTHON_WEB_DISTS):
        return True
    return _has_poetry_dep(lower, _PYTHON_WEB_DISTS)


def _package_json_mentions_frontend(content: str) -> bool:
    """Match react / next / vue / svelte / nuxt as deps.

    Same opening-quote prefix trick as :func:`_pyproject_mentions_web`
    so ``"react": "^18"`` and ``"react-dom"`` both classify as
    js_frontend.
    """
    lower = content.lower()
    return any(f'"{dist}' in lower for dist in _JS_FRONTEND_DISTS)


def _has_poetry_dep(lower_content: str, dists: tuple[str, ...]) -> bool:
    """True if any ``<dist> =`` line appears (Poetry / PDM legacy syntax).

    Inspects the content line-by-line to make the anchor explicit:
    ``django = "^5.0"`` matches, ``# django = "^5.0"`` does not, and
    a TOML key like ``packages = [...]`` cannot be mistaken for a
    dependency. A single-quoted variant is also accepted.
    """
    for raw_line in lower_content.splitlines():
        line = raw_line.lstrip()
        if not line or line.startswith("#"):
            continue
        for dist in dists:
            if line.startswith(f"{dist} =") or line.startswith(f"{dist}="):
                return True
    return False
