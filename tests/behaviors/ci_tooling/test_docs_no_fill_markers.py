"""The release gate that no placeholder ships: a '[FILL' marker in the docs or the CHANGELOG fails (0.1.11).

RED on the release branch on purpose until the real-run page is filled from the numbers file.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "gigai-docs" / "src" / "content" / "docs"
PAGE = "scout/real-run-0-1-11"
ACCURACY = "scout/accuracy-0-1-11"


def test_no_fill_marker_in_docs_or_changelog() -> None:
    found: list[str] = []
    for path in [*sorted(DOCS.rglob("*")), ROOT / "CHANGELOG.md"]:
        if not path.is_file() or path.suffix not in {".md", ".mdx"}:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "[FILL" in line:
                found.append(f"{path.relative_to(ROOT)}:{number}")
    assert not found, f"{len(found)} '[FILL' marker(s) still present: {found}"


def test_sidebar_lists_the_real_run_page_right_after_accuracy() -> None:
    config = (ROOT / "gigai-docs" / "astro.config.mjs").read_text(encoding="utf-8")
    items = re.findall(r"'(scout/[^']+)'", config)
    assert ACCURACY in items and PAGE in items
    assert items.index(PAGE) == items.index(ACCURACY) + 1


def test_page_exists_with_its_title() -> None:
    text = (DOCS / f"{PAGE}.md").read_text(encoding="utf-8")
    assert "title: Real-run check and timings (0.1.11)" in text


def test_changelog_and_accuracy_page_link_to_the_page_slug() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "https://karthik446.github.io/gigai/scout/real-run-0-1-11/" in changelog
    accuracy = (DOCS / f"{ACCURACY}.md").read_text(encoding="utf-8")
    assert "](../real-run-0-1-11/)" in accuracy
    assert (DOCS / "scout" / "real-run-0-1-11.md").is_file()
