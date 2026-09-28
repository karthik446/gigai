from __future__ import annotations

from pathlib import Path

import pytest

from tools import release_notes


REPO_ROOT = Path(__file__).resolve().parents[3]


def _read_real_changelog() -> str:
    # The Debian offline image copies the source and test trees but neither
    # CHANGELOG.md nor .git (containers/debian-offline/Dockerfile,
    # .dockerignore). A checkout has .git, so a missing changelog there still
    # fails rather than skips.
    changelog_path = REPO_ROOT / "CHANGELOG.md"
    if not changelog_path.is_file() and not (REPO_ROOT / ".git").exists():
        pytest.skip("CHANGELOG.md is excluded from the offline container build context")
    return changelog_path.read_text(encoding="utf-8")


def test_extracts_0_1_8_section_from_the_real_changelog() -> None:
    changelog = _read_real_changelog()

    notes = release_notes.extract_release_notes(changelog, "0.1.8")

    assert notes.startswith("- Adds Scout's `find-jobs` workflow")
    assert "0.1.7" not in notes.split("\n")[0]
    assert "### 0.1.7" not in notes


_CHANGELOG = """# Title

## Unreleased capability milestones

### Added

- unreleased thing

## Released versions

### 0.2.0

- newest entry
- second line

### 0.1.0

- oldest entry

## Deferred and not advertised

- something else
"""


def test_extracts_middle_section_stopping_at_next_heading() -> None:
    notes = release_notes.extract_release_notes(_CHANGELOG, "0.2.0")
    assert notes == "- newest entry\n- second line"


def test_extracts_last_released_section_stopping_before_next_top_level_heading() -> None:
    notes = release_notes.extract_release_notes(_CHANGELOG, "0.1.0")
    assert notes == "- oldest entry"


def test_missing_version_raises() -> None:
    with pytest.raises(release_notes.ReleaseNotesError, match="no .*9.9.9.* section"):
        release_notes.extract_release_notes(_CHANGELOG, "9.9.9")


def test_empty_section_raises() -> None:
    changelog = (
        "## Released versions\n"
        "\n"
        "### 0.3.0\n"
        "\n"
        "### 0.2.0\n"
        "\n"
        "- entry\n"
    )
    with pytest.raises(release_notes.ReleaseNotesError, match="is empty"):
        release_notes.extract_release_notes(changelog, "0.3.0")


def test_missing_released_versions_heading_raises() -> None:
    with pytest.raises(release_notes.ReleaseNotesError, match="Released versions"):
        release_notes.extract_release_notes("# Title\n\nno sections here\n", "0.1.0")
