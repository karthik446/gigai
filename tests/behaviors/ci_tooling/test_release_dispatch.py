"""release-dispatch: a manually dispatched release is validated before any tag exists."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

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
    _rejects("already exists", tag_exists=True, tag_commit=None)


def test_existing_tag_at_the_same_sha_without_a_release_is_accepted() -> None:
    release_dispatch.validate_dispatch(
        SHA, VERSION, replace(GOOD, tag_exists=True, tag_commit=SHA, release_exists=False)
    )


def test_existing_tag_at_a_different_sha_is_rejected() -> None:
    _rejects(f"not {SHA}", tag_exists=True, tag_commit="b" * 40)


def test_existing_tag_with_a_github_release_is_rejected() -> None:
    _rejects("GitHub Release exists", tag_exists=True, tag_commit=SHA, release_exists=True)


def test_existing_tag_for_a_version_already_on_pypi_is_rejected() -> None:
    _rejects("on PyPI", tag_exists=True, tag_commit=SHA, on_pypi=True)


def test_pypi_lookup_fails_closed_on_any_unexpected_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __init__(self, status: int) -> None:
            self.status = status

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def http_error(code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError("u", code, "m", None, None)  # type: ignore[arg-type]

    outcomes: list[object] = [Response(200), http_error(404), http_error(503), urllib.error.URLError("dns")]

    def fake_urlopen(*args: object, **kwargs: object) -> Response:
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert release_dispatch._on_pypi("0.1.9.1") is True
    assert release_dispatch._on_pypi("0.1.9.1") is False
    with pytest.raises(release_dispatch.ReleaseDispatchError, match="HTTP 503"):
        release_dispatch._on_pypi("0.1.9.1")
    with pytest.raises(release_dispatch.ReleaseDispatchError, match="cannot check PyPI"):
        release_dispatch._on_pypi("0.1.9.1")


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout="", stderr=stderr)


def test_release_lookup_fails_closed_on_any_unexpected_gh_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter([_completed(0), _completed(1, "release not found"), _completed(1, "HTTP 502")])
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: next(outcomes))
    assert release_dispatch._release_exists("v0.1.9.1") is True
    assert release_dispatch._release_exists("v0.1.9.1") is False
    with pytest.raises(release_dispatch.ReleaseDispatchError, match="HTTP 502"):
        release_dispatch._release_exists("v0.1.9.1")

    def missing_gh(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", missing_gh)
    with pytest.raises(release_dispatch.ReleaseDispatchError, match="cannot check"):
        release_dispatch._release_exists("v0.1.9.1")


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


def test_dispatch_tag_step_reuses_an_existing_tag_at_the_sha() -> None:
    text = _workflow()
    assert 'git rev-parse -q --verify "refs/tags/v${RELEASE_VERSION}^{}"' in text
    assert "reusing it" in text
    # the release lookup needs a token in the validate step
    validate = text.split("- name: Validate sha and version", 1)[1].split("- id: tag", 1)[0]
    assert "GH_TOKEN: ${{ github.token }}" in validate
