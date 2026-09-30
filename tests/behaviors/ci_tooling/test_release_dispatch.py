"""release-dispatch: a manually dispatched release is validated before any tag exists."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tools import release_dispatch, release_notes

SHA = "a" * 40
VERSION = "0.1.9.1"
PYPROJECT = f'[project]\nname = "gigai"\nversion = "{VERSION}"\n'
CATALOG = f'CATALOG_REVISION = "v{VERSION}"\n'
CHANGELOG = f"# Changelog\n\n## Released versions\n\n### {VERSION}\n\n- a fix\n"

GOOD = release_dispatch.DispatchFacts(
    resolved_commit=SHA,
    branches=("karthik446/gigai-v0.1.10", "other"),
    tag_exists=False,
    pyproject=PYPROJECT,
    catalog=CATALOG,
    changelog=CHANGELOG,
)


def _rejects(match: str, sha: str = SHA, version: str = VERSION, **changes: object) -> None:
    facts = replace(GOOD, **changes)
    with pytest.raises(release_dispatch.ReleaseDispatchError, match=match):
        release_dispatch.validate_dispatch(sha, version, facts)


def test_good_inputs_are_accepted() -> None:
    release_dispatch.validate_dispatch(SHA, VERSION, GOOD)
    release_dispatch.validate_dispatch(SHA, VERSION, replace(GOOD, branches=("main",)))


def test_bad_sha_is_rejected() -> None:
    _rejects("40-character", sha="abc1234")
    _rejects("40-character", sha="A" * 40)
    _rejects("not a commit", resolved_commit=None)
    _rejects("not a commit", resolved_commit="b" * 40)


def test_bad_version_is_rejected() -> None:
    _rejects("must look like", version="v0.1.9.1")


def test_branch_not_allowed() -> None:
    _rejects("no allowed branch", branches=("feature/x", "karthik446/other"))
    _rejects("no allowed branch", branches=())


def test_tag_exists() -> None:
    _rejects("already exists", tag_exists=True)


def test_pyproject_version_mismatch() -> None:
    _rejects("pyproject.toml", pyproject='[project]\nname = "gigai"\nversion = "0.1.9"\n')


def test_catalog_revision_mismatch_or_missing() -> None:
    _rejects("CATALOG_REVISION", catalog='CATALOG_REVISION = "v0.1.9"\n')
    _rejects("no CATALOG_REVISION", catalog="OTHER = 1\n")


def test_missing_changelog_entry() -> None:
    _rejects("CHANGELOG", changelog="# Changelog\n\n## Released versions\n\n### 0.1.9\n\n- x\n")
    _rejects("CHANGELOG", changelog="")


def test_allowed_branch_patterns() -> None:
    assert release_dispatch.allowed_branches(
        ["main", "karthik446/gigai-v0.1.10", "karthik446/x", "mainline"]
    ) == ["main", "karthik446/gigai-v0.1.10"]


def test_main_reports_rejection_and_acceptance(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(release_dispatch, "gather_facts", lambda sha, version, remote: GOOD)
    assert release_dispatch.main(["--sha", SHA, "--version", VERSION]) == 0
    assert "accepted" in capsys.readouterr().out
    monkeypatch.setattr(
        release_dispatch, "gather_facts", lambda sha, version, remote: replace(GOOD, tag_exists=True)
    )
    assert release_dispatch.main(["--sha", SHA, "--version", VERSION]) == 1
    assert "already exists" in capsys.readouterr().out


def _workflow() -> str:
    path = Path(__file__).resolve().parents[3] / ".github/workflows/release.yml"
    if not path.is_file():
        pytest.skip("release workflow is excluded from the offline container build context")
    return path.read_text(encoding="utf-8")


def test_dispatch_job_validates_then_tags_and_dry_run_stops_before_tagging() -> None:
    text = _workflow()
    jobs = release_notes.parse_workflow_jobs(text)
    assert jobs["dispatch"].permissions == {"contents": "write"}
    assert "workflow_dispatch:" in text and "- \"v*\"" in text
    assert "python -m tools.release_dispatch" in text
    assert "if: ${{ !inputs.dry_run }}" in text
    assert "git tag -a" in text
    graph = release_notes.parse_workflow_job_needs(text)
    assert graph["preflight"] == ["dispatch"]
    # every later job releases the tag from preflight, never the dispatching branch
    assert "ref: refs/tags/${{ github.ref_name }}" not in text
