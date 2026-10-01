"""release-gate: a release needs a green pre-check run on exactly its commit.

Pure: no network, no git process. The run and artifact records have the shape
of the GitHub REST API's (``id``, ``head_sha``, ``display_title``, ``status``,
``conclusion``; ``name``, ``expired``).
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tools import release_gate

HEAD = "a" * 40
OLDER = "b" * 40
BRANCH = "karthik446/gigai-v0.1.10"
VERSION = "0.1.10.4"
TITLE = release_gate.GATE_DISPLAY_TITLE
COMMAND = f"gh workflow run pull_request.yaml --ref {BRANCH} -f profile=release"


def _run(
    run_id: int,
    sha: str = HEAD,
    title: str = TITLE,
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict[str, object]:
    return {"id": run_id, "head_sha": sha, "display_title": title, "status": status, "conclusion": conclusion}


def _rejects(match: str, runs: list[dict[str, object]]) -> str:
    with pytest.raises(release_gate.PrecheckError, match=match) as raised:
        release_gate.find_precheck_run(HEAD, BRANCH, runs)
    message = str(raised.value)
    assert "\n" not in message
    return message


def test_a_green_precheck_on_the_head_commit_is_found() -> None:
    runs = [_run(3, sha=OLDER), _run(2), _run(1)]
    assert release_gate.find_precheck_run(HEAD, BRANCH, runs) == 2
    # a red or running run on the same commit does not hide a green one
    runs = [_run(5, status="in_progress", conclusion=None), _run(4, conclusion="failure"), _run(2)]
    assert release_gate.find_precheck_run(HEAD, BRANCH, runs) == 2


def test_no_precheck_at_all_fails_and_names_the_dispatch() -> None:
    message = _rejects("no pre-check run", [])
    assert HEAD in message and message.endswith(COMMAND)


def test_head_moved_after_the_check_fails() -> None:
    """A green pre-check on an older commit of the branch does not cover the new head."""

    message = _rejects("moved after its last pre-check", [_run(7, sha=OLDER)])
    assert "run 7" in message and OLDER in message and HEAD in message
    assert message.endswith(COMMAND)


def test_a_red_precheck_on_the_head_fails() -> None:
    message = _rejects("ended failure", [_run(8, conclusion="failure"), _run(7, sha=OLDER)])
    assert "run 8" in message and message.endswith(COMMAND)
    _rejects("ended cancelled", [_run(9, conclusion="cancelled")])


def test_a_running_precheck_on_the_head_fails_without_asking_for_another() -> None:
    for status in ("queued", "in_progress"):
        message = _rejects("still running", [_run(8, status=status, conclusion=None)])
        assert "run 8" in message and "gh workflow run" not in message


def test_only_the_release_profile_counts() -> None:
    runs = [_run(1, title="Pull request (pr)"), _run(2, title="Pull request (full)")]
    _rejects("no pre-check run", runs)


def test_the_precheck_run_must_still_hold_the_release_files() -> None:
    name = release_gate.dist_artifact_name(VERSION)
    assert name == "release-dist-0.1.10.4"
    release_gate.require_dist_artifact(2, VERSION, BRANCH, [{"name": name, "expired": False}])
    for artifacts in ([], [{"name": name, "expired": True}], [{"name": "release-dist-0.1.10.3", "expired": False}]):
        with pytest.raises(release_gate.PrecheckError, match="has no release-dist-0.1.10.4 artifact") as raised:
            release_gate.require_dist_artifact(2, VERSION, BRANCH, artifacts)
        assert str(raised.value).endswith(COMMAND)


def _main(monkeypatch: pytest.MonkeyPatch, runs: object, artifacts: object = ()) -> int:
    def github_runs(repository: str, sha: str, branch: str) -> list[dict[str, object]]:
        if isinstance(runs, BaseException):
            raise runs
        return list(runs)  # type: ignore[call-overload]

    monkeypatch.setattr(release_gate, "_github_runs", github_runs)
    monkeypatch.setattr(release_gate, "_github_artifacts", lambda repository, run_id: list(artifacts))  # type: ignore[call-overload]
    return release_gate.main(["--commit", HEAD, "--branch", BRANCH, "--version", VERSION, "--repository", "o/r"])


def test_main_prints_the_run_id_as_a_github_output_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts = [{"name": "release-dist-0.1.10.4", "expired": False}]
    assert _main(monkeypatch, [_run(42)], artifacts) == 0
    assert capsys.readouterr().out == "run_id=42\n"


def test_main_fails_with_one_error_line_and_no_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failures: list[tuple[object, object]] = [
        ([], ()),
        ([_run(7, sha=OLDER)], ()),
        ([_run(8, conclusion="failure")], ()),
        ([_run(42)], ()),  # green, but the release files are gone
        (subprocess.CalledProcessError(1, ["gh"]), ()),  # the lookup itself failed: fail closed
    ]
    for runs, artifacts in failures:
        assert _main(monkeypatch, runs, artifacts) == 1
        captured = capsys.readouterr()
        assert captured.out == ""  # nothing reaches GITHUB_OUTPUT
        assert captured.err.startswith("::error::") and captured.err.count("\n") == 1


def _workflow(name: str) -> str:
    path = Path(__file__).resolve().parents[3] / ".github/workflows" / name
    if not path.is_file():
        pytest.skip("workflows are excluded from the offline container build context")
    return path.read_text(encoding="utf-8")


def test_release_workflow_looks_up_the_precheck_and_never_runs_the_checks() -> None:
    text = _workflow("release.yml")
    assert (
        'python3 tools/release_gate.py --commit "${GITHUB_SHA}" --branch "${GITHUB_REF_NAME}" '
        '--version "${VERSION}" >> "${GITHUB_OUTPUT}"'
    ) in text
    assert "precheck_run: ${{ steps.precheck.outputs.run_id }}" in text
    # no job calls the CI workflow, and nothing here runs a test suite
    assert "uses: ./.github/workflows/pull_request.yaml" not in text
    assert "make test" not in text and "pytest" not in text


def test_precheck_run_title_is_the_one_the_lookup_matches() -> None:
    text = _workflow("pull_request.yaml")
    assert "run-name: Pull request (${{ inputs.profile || 'pr' }})" in text
    assert TITLE == "Pull request (release)"
