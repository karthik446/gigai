"""release-dispatch: a commit is validated as releasable, by the pre-check and by the release."""

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


def test_a_version_already_on_pypi_is_rejected_without_a_tag_too() -> None:
    _rejects("already on PyPI", on_pypi=True)
    with pytest.raises(release_dispatch.ReleaseDispatchError, match="already on PyPI"):
        release_dispatch.validate_dispatch(SHA, VERSION, replace(GOOD, on_pypi=True), precheck=True)


def test_precheck_rejects_any_existing_tag_even_at_the_same_sha() -> None:
    reusable = replace(GOOD, tag_exists=True, tag_commit=SHA, release_exists=False)
    release_dispatch.validate_dispatch(SHA, VERSION, reusable)
    with pytest.raises(release_dispatch.ReleaseDispatchError, match="not tagged yet"):
        release_dispatch.validate_dispatch(SHA, VERSION, reusable, precheck=True)
    release_dispatch.validate_dispatch(SHA, VERSION, GOOD, precheck=True)


def test_the_branch_the_workflow_started_on_must_be_a_release_branch() -> None:
    release_dispatch.validate_dispatch(SHA, VERSION, GOOD, branch="karthik446/gigai-v0.1.10")
    release_dispatch.validate_dispatch(SHA, VERSION, GOOD, branch="main")
    for branch in ("feature/x", "v0.1.9.1", ""):
        with pytest.raises(release_dispatch.ReleaseDispatchError, match="not a release branch"):
            release_dispatch.validate_dispatch(SHA, VERSION, GOOD, branch=branch)


def test_pypi_is_asked_even_when_no_tag_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if args[0] == "rev-parse" and args[-1].startswith(SHA):
            return subprocess.CompletedProcess([], 0, stdout=SHA + "\n", stderr="")
        if args[0] == "branch":
            return subprocess.CompletedProcess([], 0, stdout="origin/main\n", stderr="")
        if args[0] == "show":
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")
        return subprocess.CompletedProcess([], 1 if args[0] == "rev-parse" else 0, stdout="", stderr="")

    asked: list[str] = []
    monkeypatch.setattr(release_dispatch, "_git", git)
    monkeypatch.setattr(release_dispatch, "_on_pypi", lambda version: asked.append(version) or True)
    facts = release_dispatch.gather_facts(SHA, VERSION)
    assert asked == [VERSION]
    assert facts.tag_exists is False and facts.on_pypi is True and facts.branches == ("main",)


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
    monkeypatch.setattr(release_dispatch, "gather_facts", lambda sha, version, remote: GOOD)
    assert release_dispatch.main(["--sha", SHA, "--version", VERSION, "--branch", "feature/x"]) == 1
    assert "not a release branch" in capsys.readouterr().out
    reusable = replace(GOOD, tag_exists=True, tag_commit=SHA)
    monkeypatch.setattr(release_dispatch, "gather_facts", lambda sha, version, remote: reusable)
    assert release_dispatch.main(["--sha", SHA, "--version", VERSION]) == 0
    capsys.readouterr()
    assert release_dispatch.main(["--sha", SHA, "--version", VERSION, "--precheck"]) == 1
    assert "not tagged yet" in capsys.readouterr().out


def test_there_is_no_patch_or_minor_classification() -> None:
    assert not hasattr(release_dispatch, "release_kind")
    with pytest.raises(SystemExit):
        release_dispatch.main(["--kind", "0.1.10.4"])


def _workflow(name: str = "release.yml") -> str:
    path = Path(__file__).resolve().parents[3] / ".github/workflows" / name
    if not path.is_file():
        pytest.skip("workflows are excluded from the offline container build context")
    return path.read_text(encoding="utf-8")


def _job(text: str, job: str) -> str:
    """Return one top-level job's text, up to the next job."""

    body = text.split(f"\n  {job}:\n", 1)[1]
    following = [index for index in (body.find(f"\n  {name}:\n") for name in release_notes.parse_workflow_jobs(text)) if index != -1]
    return body[: min(following)] if following else body


def test_release_has_no_inputs_and_only_the_operator_trigger() -> None:
    text = _workflow()
    triggers = text.split("\non:\n", 1)[1].split("\nconcurrency:\n", 1)[0]
    assert triggers.strip() == "workflow_dispatch:"
    assert "inputs:" not in text and "inputs." not in text  # neither declared nor read
    assert "\n  push:" not in text and "tags:" not in text and '"v*"' not in text
    assert "dry_run" not in text and "dry run" not in text


def test_release_has_no_classify_job_and_no_approval_environments() -> None:
    text = _workflow()
    jobs = release_notes.parse_workflow_jobs(text)
    assert "classify" not in jobs and "dispatch" not in jobs
    assert "release-patch" not in text and "release-minor" not in text and "--kind" not in text
    # the only environment is PyPI's trusted-publishing one
    assert [line.strip() for line in text.splitlines() if line.startswith("    environment:")] == ["environment: pypi"]
    assert "testpypi" not in text.lower() and "test.pypi.org" not in text


def test_preflight_requires_the_precheck_before_anything_is_written() -> None:
    text = _workflow()
    jobs = release_notes.parse_workflow_jobs(text)
    assert jobs["preflight"].needs == []
    assert jobs["preflight"].permissions == {"contents": "read", "actions": "read"}
    preflight = _job(text, "preflight")
    # the head commit of the branch the operator picked, and the version it declares
    assert 'test "$(git rev-parse HEAD)" = "${GITHUB_SHA}"' in preflight
    assert '[ "${GITHUB_REF_TYPE}" != branch ]' in preflight
    assert "tools/release_gate.py" in preflight
    assert (
        'python3 -m tools.release_dispatch --sha "${GITHUB_SHA}" --version "${VERSION}" '
        '--branch "${GITHUB_REF_NAME}"'
    ) in preflight
    assert "git tag" not in preflight and "git push" not in preflight
    # every job that writes anything waits for preflight
    for job_id, job in jobs.items():
        if job_id != "preflight":
            assert "preflight" in job.needs, job_id


def test_release_tags_the_commit_and_downloads_the_prechecks_dist_without_rebuilding() -> None:
    text = _workflow()
    jobs = release_notes.parse_workflow_jobs(text)
    assert jobs["tag"].needs == ["preflight"]
    assert jobs["tag"].permissions["contents"] == "write" and jobs["tag"].permissions["actions"] == "read"
    tag = _job(text, "tag")
    assert "name: release-dist-${{ needs.preflight.outputs.version }}" in tag
    assert "run-id: ${{ needs.preflight.outputs.precheck_run }}" in tag
    assert "python3 tools/release_check.py verify-checksums" in tag
    assert 'git tag -a "v${RELEASE_VERSION}" -m "GigAI ${RELEASE_VERSION}" "${RELEASE_SHA}"' in tag
    assert "RELEASE_SHA: ${{ needs.preflight.outputs.commit }}" in tag
    # the tested files are in hand and verified before the tag exists
    assert tag.index("actions/download-artifact@v4") < tag.index("verify-checksums") < tag.index("git tag -a")
    # a second run after a partial one reuses a tag that is already on this commit
    assert 'git rev-parse -q --verify "refs/tags/v${RELEASE_VERSION}^{}"' in tag and "reusing it" in tag
    # nothing in the release builds: no uv build, no checksum rewrite, no build job
    assert "uv build" not in text and "write-checksums" not in text and "python -m build" not in text
    assert "build" not in jobs and "smoke-artifacts" not in jobs and "ci" not in jobs
    assert jobs["publish-pypi"].needs == ["preflight", "tag"]
    assert "environment: pypi" in _job(text, "publish-pypi")
    # later jobs release the tag from preflight, never the dispatching branch
    assert "ref: refs/tags/${{ github.ref_name }}" not in text


def test_precheck_builds_and_uploads_the_dist_and_never_tags_or_publishes() -> None:
    text = _workflow("pull_request.yaml")
    jobs = release_notes.parse_workflow_jobs(text)
    assert jobs["release-build"].needs == ["release-preflight"]
    assert jobs["release-smoke-artifacts"].needs == ["release-preflight", "release-build"]
    preflight = _job(text, "release-preflight")
    assert "    if: inputs.profile == 'release'\n" in preflight
    assert "python3 -m tools.release_dispatch --precheck" in preflight
    assert "python3 tools/release_check.py verify-lockfile" in preflight
    build = _job(text, "release-build")
    assert "uv build --no-sources" in build
    for command in ("verify-artifacts", "write-checksums", "verify-checksums"):
        assert f"python tools/release_check.py {command}" in build
    upload = build.split("actions/upload-artifact@v4", 1)[1]
    assert "name: release-dist-${{ needs.release-preflight.outputs.version }}" in upload
    assert "path: dist/" in upload and "if-no-files-found: error" in upload
    smoke = _job(text, "release-smoke-artifacts")
    assert "name: release-dist-${{ needs.release-preflight.outputs.version }}" in smoke
    assert 'uv pip install --python "${root}/venv/bin/python" "${artifact}"' in smoke
    assert "for artifact in dist/*.whl dist/*.tar.gz; do" in smoke
    # never tags, never publishes: no write permission, no token for PyPI, no tag or release command
    for job_id, job in jobs.items():
        assert set(job.permissions.values()) <= {"read"}, job_id
    for forbidden in ("git tag", "git push", "gh release", "pypi-publish", "id-token", "\n    environment:", ": write"):
        assert forbidden not in text, forbidden


def test_main_moves_by_fast_forward_only_after_the_github_release() -> None:
    text = _workflow()
    graph = release_notes.parse_workflow_job_needs(text)
    assert graph["advance-main"] == ["preflight", "github-release"]
    job = _job(text, "advance-main")
    assert 'git merge-base --is-ancestor origin/main "${COMMIT}"' in job
    assert 'git push origin "${COMMIT}:refs/heads/main"' in job
    assert "--force" not in job and "gh pr" not in text
    assert "main left unchanged" in job
    # a failed fast-forward never fails a release that is already published, and says what to run
    assert "continue-on-error: true" in job
    assert "run: git push origin ${COMMIT}:main" in job


def test_rollback_workflow_is_manual_and_defaults_to_a_dry_run() -> None:
    text = _workflow("rollback.yml")
    triggers = text.split("\non:\n", 1)[1].split("\nconcurrency:\n", 1)[0]
    assert triggers.lstrip().startswith("workflow_dispatch:") and "push:" not in triggers
    dry_run = triggers.split("dry_run:", 1)[1]
    assert "default: true" in dry_run
    for job in ("github-release", "docs"):
        assert "!inputs.dry_run" in _job(text, job).split("runs-on:", 1)[0]
    assert "git push --force" not in text and "git tag -d" not in text
