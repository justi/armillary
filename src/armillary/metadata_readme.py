"""README extraction: find the first README candidate in a project and
return its first paragraph as plain text. Split out of ``metadata.py``
for the 400-line architecture target (ADR 0001 rule 3).
"""

from __future__ import annotations

import re
from pathlib import Path

# README candidates in priority order. The first existing file wins.
README_CANDIDATES = (
    "README.md",
    "README.rst",
    "README.txt",
    "README",
    "readme.md",
)

_README_EXCERPT_MAX_CHARS = 280

_HEADER_RE = re.compile(r"^#{1,6}\s")
_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def _extract_readme_excerpt(project_path: Path) -> str | None:
    """Find the first README in `project_path` and return its first
    paragraph or so as plain text. Returns None if no README exists.
    """
    for name in README_CANDIDATES:
        readme = project_path / name
        if readme.is_file():
            try:
                content = readme.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            return _first_paragraph_plain(content)
    return None


def _first_paragraph_plain(markdown: str) -> str | None:
    """Strip headers, code blocks, and inline markdown from the first
    non-empty paragraph and clamp to ~280 characters.
    """
    in_code_fence = False
    paragraph: list[str] = []

    for raw_line in markdown.splitlines():
        line = raw_line.strip()

        if line.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue

        if not line:
            if paragraph:
                break
            continue

        if _HEADER_RE.match(line):
            continue

        # Strip simple inline markdown noise so the excerpt reads naturally.
        line = _INLINE_LINK_RE.sub(r"\1", line)
        line = _INLINE_CODE_RE.sub(r"\1", line)
        paragraph.append(line)

    if not paragraph:
        return None
    text = " ".join(paragraph).strip()
    if len(text) <= _README_EXCERPT_MAX_CHARS:
        return text
    cut = text[:_README_EXCERPT_MAX_CHARS]
    # Avoid cutting mid-word if there is a sensible space to break on.
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"
