"""Unit tests for tools/ci_changes.py: the classification and diff-range
decision logic behind the `changes` job's `detect` step in
.github/workflows/pull_request.yaml.

All git/gh calls are injected (`diff_paths_fn`) or passed as pre-computed
booleans (`before_is_ancestor`, `before_run_conclusion`) -- these tests never
shell out and never touch the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import ci_changes

REPO_ROOT = Path(__file__).resolve().parents[3]
PULL_REQUEST_WORKFLOW_USES = "./.github/workflows/pull_request.yaml"


# ---------------------------------------------------------------------------
# is_code_path / classify_paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/README.markdown",
        "notes.txt",
        "docs/development/evidence/phase-1/G01/terminal-handoff.md",
        "docs/anything/at/any/depth.py",  # docs/** is non-code even for .py
        ".orchestrator/workers/ci-docs-skip.md",
        ".orchestrator/runs/v0.1.8/workers/ci-speed.md",
        ".claude/skills/gigai-orchestrator/SKILL.md",
    ],
)
def test_non_code_paths(path: str) -> None:
    assert ci_changes.is_code_path(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "src/gigai/main.py",
        "tests/behaviors/ci_tooling/test_ci_changes.py",
        "tools/ci_changes.py",
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/pull_request.yaml",
        ".claude/skills/gigai-orchestrator/run.sh",
        ".claude/skills/gigai-orchestrator/local_check.py",
        "Makefile",
    ],
)
def test_code_paths(path: str) -> None:
    assert ci_changes.is_code_path(path) is True


def test_claude_skill_md_non_code_but_sibling_script_is_code() -> None:
    """Exact acceptance case: .claude/skills/x/SKILL.md is non-code but
    .claude/skills/x/run.sh in the same directory is code."""

    assert ci_changes.is_code_path(".claude/skills/x/SKILL.md") is False
    assert ci_changes.is_code_path(".claude/skills/x/run.sh") is True


def test_classify_paths_all_non_code_is_false() -> None:
    assert (
        ci_changes.classify_paths(["README.md", "docs/guide.md", ".orchestrator/x.md"])
        is False
    )


def test_classify_paths_any_code_is_true() -> None:
    assert (
        ci_changes.classify_paths(["README.md", "src/gigai/main.py"]) is True
    )


def test_classify_paths_empty_is_false() -> None:
    assert ci_changes.classify_paths([]) is False


# ---------------------------------------------------------------------------
# decide(): full scenarios
# ---------------------------------------------------------------------------


def _diff_paths_fn(mapping: dict[tuple[str, str], list[str]]):
    def fn(base: str, head: str) -> list[str]:
        return mapping[(base, head)]

    return fn


def test_docs_only_incremental_with_before_passed_skips() -> None:
    """Docs-only incremental push + before passed -> skip (code_changed=False)."""

    diff_paths_fn = _diff_paths_fn(
        {("before123", "sha456"): ["docs/guide.md", "README.md"]}
    )
    decision = ci_changes.decide(
        event_name="pull_request",
        profile="pr",
        action="synchronize",
        base_sha="base789",
        before_sha="before123",
        sha="sha456",
        parent_sha="sha456^",
        before_is_ancestor=True,
        before_run_conclusion="success",
        diff_paths_fn=diff_paths_fn,
    )
    assert decision.code_changed is False
    assert decision.mode == "incremental"
    assert decision.diff_base == "before123"
    assert decision.diff_head == "sha456"


@pytest.mark.parametrize("conclusion", ["cancelled", "failure", None])
def test_docs_only_incremental_with_before_not_passed_runs_whole_pr(conclusion) -> None:
    """Docs-only incremental + before cancelled/failed/unknown -> whole-PR check."""

    diff_paths_fn = _diff_paths_fn(
        {
            ("before123", "sha456"): ["docs/guide.md"],
            ("base789", "sha456"): ["docs/guide.md", "src/gigai/main.py"],
        }
    )
    decision = ci_changes.decide(
        event_name="pull_request",
        profile="pr",
        action="synchronize",
        base_sha="base789",
        before_sha="before123",
        sha="sha456",
        parent_sha="sha456^",
        before_is_ancestor=True,
        before_run_conclusion=conclusion,
        diff_paths_fn=diff_paths_fn,
    )
    assert decision.mode == "whole_pr"
    assert decision.code_changed is True
    assert decision.diff_base == "base789"
    assert decision.diff_head == "sha456"
    assert decision.before_conclusion == conclusion


def test_docs_only_incremental_before_not_passed_and_whole_pr_also_docs_only() -> None:
    """Sanity: if the whole-PR diff is ALSO all non-code, code_changed stays False
    even though we fell back to the whole-PR check (before-run wasn't trusted)."""

    diff_paths_fn = _diff_paths_fn(
        {
            ("before123", "sha456"): ["docs/guide.md"],
            ("base789", "sha456"): ["docs/guide.md", "README.md"],
        }
    )
    decision = ci_changes.decide(
        event_name="pull_request",
        profile="pr",
        action="synchronize",
        base_sha="base789",
        before_sha="before123",
        sha="sha456",
        parent_sha="sha456^",
        before_is_ancestor=True,
        before_run_conclusion="failure",
        diff_paths_fn=diff_paths_fn,
    )
    assert decision.mode == "whole_pr"
    assert decision.code_changed is False


def test_code_in_incremental_diff_runs_without_consulting_before() -> None:
    """Code in the incremental diff -> run, regardless of before-run conclusion."""

    diff_paths_fn = _diff_paths_fn(
        {("before123", "sha456"): ["src/gigai/main.py"]}
    )
    decision = ci_changes.decide(
        event_name="pull_request",
        profile="pr",
        action="synchronize",
        base_sha="base789",
        before_sha="before123",
        sha="sha456",
        parent_sha="sha456^",
        before_is_ancestor=True,
        before_run_conclusion=None,  # unknown; must not matter
        diff_paths_fn=diff_paths_fn,
    )
    assert decision.mode == "incremental"
    assert decision.code_changed is True


def test_force_push_non_ancestor_uses_whole_pr_diff() -> None:
    """before SHA not an ancestor of sha (force-push) -> whole-PR diff."""

    diff_paths_fn = _diff_paths_fn(
        {("base789", "sha456"): ["docs/guide.md"]}
    )
    decision = ci_changes.decide(
        event_name="pull_request",
        profile="pr",
        action="synchronize",
        base_sha="base789",
        before_sha="stale-before",
        sha="sha456",
        parent_sha="sha456^",
        before_is_ancestor=False,
        before_run_conclusion=None,
        diff_paths_fn=diff_paths_fn,
    )
    assert decision.mode == "whole_pr"
    assert decision.diff_base == "base789"
    assert decision.code_changed is False


def test_zero_before_sha_uses_whole_pr_diff() -> None:
    """before SHA all-zero -> whole-PR diff."""

    diff_paths_fn = _diff_paths_fn(
        {("base789", "sha456"): ["src/gigai/main.py"]}
    )
    decision = ci_changes.decide(
        event_name="pull_request",
        profile="pr",
        action="synchronize",
        base_sha="base789",
        before_sha=ci_changes.ZERO_SHA,
        sha="sha456",
        parent_sha="sha456^",
        before_is_ancestor=None,
        before_run_conclusion=None,
        diff_paths_fn=diff_paths_fn,
    )
    assert decision.mode == "whole_pr"
    assert decision.code_changed is True


def test_missing_before_sha_uses_whole_pr_diff() -> None:
    """before SHA empty/missing -> whole-PR diff."""

    diff_paths_fn = _diff_paths_fn(
        {("base789", "sha456"): ["README.md"]}
    )
    decision = ci_changes.decide(
        event_name="pull_request",
        profile="pr",
        action="synchronize",
        base_sha="base789",
        before_sha="",
        sha="sha456",
        parent_sha="sha456^",
        before_is_ancestor=None,
        before_run_conclusion=None,
        diff_paths_fn=diff_paths_fn,
    )
    assert decision.mode == "whole_pr"
    assert decision.code_changed is False


def test_indeterminate_ancestry_fails_safe_to_whole_pr_diff() -> None:
    """Ancestry lookup itself failed (shallow clone etc.) -> whole-PR diff."""

    diff_paths_fn = _diff_paths_fn(
        {("base789", "sha456"): ["src/gigai/main.py"]}
    )
    decision = ci_changes.decide(
        event_name="pull_request",
        profile="pr",
        action="synchronize",
        base_sha="base789",
        before_sha="before123",
        sha="sha456",
        parent_sha="sha456^",
        before_is_ancestor=None,
        before_run_conclusion=None,
        diff_paths_fn=diff_paths_fn,
    )
    assert decision.mode == "whole_pr"


@pytest.mark.parametrize("action", ["opened", "reopened", "ready_for_review"])
def test_opened_reopened_ready_for_review_always_whole_pr_diff(action: str) -> None:
    """opened/reopened/ready_for_review -> whole-PR diff as today, even with
    a `before` that would otherwise look incremental-safe."""

    diff_paths_fn = _diff_paths_fn(
        {("base789", "sha456"): ["docs/guide.md"]}
    )
    decision = ci_changes.decide(
        event_name="pull_request",
        profile="pr",
        action=action,
        base_sha="base789",
        before_sha="before123",
        sha="sha456",
        parent_sha="sha456^",
        before_is_ancestor=True,
        before_run_conclusion="success",
        diff_paths_fn=diff_paths_fn,
    )
    assert decision.mode == "whole_pr"
    assert decision.diff_base == "base789"
    assert decision.diff_head == "sha456"
    assert decision.code_changed is False


@pytest.mark.parametrize("profile", ["release", "full"])
def test_non_pr_profile_always_runs(profile: str) -> None:
    """Non-pr profiles (release/full via workflow_call) always run
    everything, independent of any diff."""

    def diff_paths_fn(_base, _head):  # pragma: no cover - must not be called
        raise AssertionError("diff_paths_fn must not be called for non-pr profiles")

    decision = ci_changes.decide(
        event_name="pull_request",
        profile=profile,
        action="synchronize",
        base_sha="base789",
        before_sha="before123",
        sha="sha456",
        parent_sha="sha456^",
        before_is_ancestor=True,
        before_run_conclusion="success",
        diff_paths_fn=diff_paths_fn,
    )
    assert decision.code_changed is True
    assert decision.mode == "whole_pr"


def test_non_pull_request_event_uses_parent_commit_diff() -> None:
    """e.g. workflow_dispatch on the pr profile: parent-commit diff, as before."""

    diff_paths_fn = _diff_paths_fn(
        {("sha456^", "sha456"): ["README.md"]}
    )
    decision = ci_changes.decide(
        event_name="workflow_dispatch",
        profile="pr",
        action="",
        base_sha="",
        before_sha="",
        sha="sha456",
        parent_sha="sha456^",
        before_is_ancestor=None,
        before_run_conclusion=None,
        diff_paths_fn=diff_paths_fn,
    )
    assert decision.code_changed is False
    assert decision.diff_base == "sha456^"
    assert decision.diff_head == "sha456"


# ---------------------------------------------------------------------------
# build_runs_endpoint(): the gh api query is scoped to pull_request runs
# ---------------------------------------------------------------------------


def test_build_runs_endpoint_filters_to_pull_request_event() -> None:
    """The before-run lookup must not credit a workflow_dispatch run on the
    same SHA (e.g. someone manually running the release/full profile
    against a PR branch) as evidence the pr-profile source suite passed."""

    endpoint = ci_changes.build_runs_endpoint(
        "karthik446/gigai", "pull_request.yaml", "before123"
    )
    assert endpoint == (
        "repos/karthik446/gigai/actions/workflows/pull_request.yaml/runs"
        "?head_sha=before123&event=pull_request"
    )


def test_build_runs_endpoint_uses_given_repo_and_workflow_file() -> None:
    endpoint = ci_changes.build_runs_endpoint(
        "someone/other-repo", "other-workflow.yml", "deadbeef"
    )
    assert endpoint.startswith("repos/someone/other-repo/actions/workflows/other-workflow.yml/runs")
    assert "head_sha=deadbeef" in endpoint
    assert "event=pull_request" in endpoint


# ---------------------------------------------------------------------------
# parse_caller_jobs() / caller_jobs_missing_actions_read(): the minimal
# workflow-job parser used to verify caller permissions, tested against
# synthetic YAML text (no PyYAML in this test environment).
# ---------------------------------------------------------------------------


def test_parse_caller_jobs_finds_matching_uses_with_permissions() -> None:
    workflow_text = """\
name: Example

on:
  workflow_dispatch:

jobs:
  other:
    runs-on: ubuntu-24.04
    steps:
      - run: echo hi

  ci:
    name: Exact-tag CI
    needs: preflight
    uses: ./.github/workflows/pull_request.yaml
    permissions:
      contents: read
      actions: read
    with:
      profile: release
"""
    jobs = ci_changes.parse_caller_jobs(
        workflow_text, "example.yaml", PULL_REQUEST_WORKFLOW_USES
    )
    assert len(jobs) == 1
    assert jobs[0].job_id == "ci"
    assert jobs[0].permissions == {"contents": "read", "actions": "read"}
    assert jobs[0].grants_actions_read() is True


def test_parse_caller_jobs_missing_permissions_block() -> None:
    workflow_text = """\
name: Example

jobs:
  ci:
    needs: preflight
    uses: ./.github/workflows/pull_request.yaml
    with:
      profile: release
"""
    jobs = ci_changes.parse_caller_jobs(
        workflow_text, "example.yaml", PULL_REQUEST_WORKFLOW_USES
    )
    assert len(jobs) == 1
    assert jobs[0].permissions == {}
    assert jobs[0].grants_actions_read() is False


def test_parse_caller_jobs_permissions_without_actions_read() -> None:
    workflow_text = """\
name: Example

jobs:
  ci:
    uses: ./.github/workflows/pull_request.yaml
    permissions:
      contents: read
    with:
      profile: release
"""
    jobs = ci_changes.parse_caller_jobs(
        workflow_text, "example.yaml", PULL_REQUEST_WORKFLOW_USES
    )
    assert jobs[0].grants_actions_read() is False


def test_parse_caller_jobs_ignores_non_matching_uses() -> None:
    workflow_text = """\
name: Example

jobs:
  other:
    uses: ./.github/workflows/some_other_workflow.yaml
"""
    jobs = ci_changes.parse_caller_jobs(
        workflow_text, "example.yaml", PULL_REQUEST_WORKFLOW_USES
    )
    assert jobs == []


def test_parse_caller_jobs_ignores_regular_jobs_with_no_uses() -> None:
    workflow_text = """\
name: Example

jobs:
  build:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
    steps:
      - run: echo hi
"""
    jobs = ci_changes.parse_caller_jobs(
        workflow_text, "example.yaml", PULL_REQUEST_WORKFLOW_USES
    )
    assert jobs == []


def test_caller_jobs_missing_actions_read_across_multiple_workflows() -> None:
    granted = """\
jobs:
  compatibility:
    uses: ./.github/workflows/pull_request.yaml
    permissions:
      contents: read
      actions: read
    with:
      profile: full
"""
    not_granted = """\
jobs:
  ci:
    uses: ./.github/workflows/pull_request.yaml
    permissions:
      contents: read
    with:
      profile: release

  post-release-compatibility:
    uses: ./.github/workflows/pull_request.yaml
    with:
      profile: full
"""
    missing = ci_changes.caller_jobs_missing_actions_read(
        {"granted.yaml": granted, "not_granted.yaml": not_granted},
        PULL_REQUEST_WORKFLOW_USES,
    )
    missing_ids = {(job.workflow_path, job.job_id) for job in missing}
    assert missing_ids == {
        ("not_granted.yaml", "ci"),
        ("not_granted.yaml", "post-release-compatibility"),
    }


# ---------------------------------------------------------------------------
# Live check against this repo's actual workflow files: every job that
# `uses:` pull_request.yaml must grant `actions: read`, or GitHub refuses to
# start the called workflow now that its `changes` job requests that
# permission for the before-run `gh api` lookup.
#
# This is expected to fail against .github/workflows/release.yml until the
# release-pipeline worker's fix lands in this shared worktree; see
# .orchestrator/workers/ci-docs-skip.md for the r1 status of that check at
# the time this packet was tested.
# ---------------------------------------------------------------------------


def _workflow_files() -> list[Path]:
    workflows_dir = REPO_ROOT / ".github" / "workflows"
    return sorted(
        p for p in workflows_dir.iterdir() if p.suffix in {".yml", ".yaml"}
    )


def test_every_caller_of_pull_request_workflow_grants_actions_read() -> None:
    workflow_texts = {
        str(path.relative_to(REPO_ROOT)): path.read_text(encoding="utf-8")
        for path in _workflow_files()
    }
    missing = ci_changes.caller_jobs_missing_actions_read(
        workflow_texts, PULL_REQUEST_WORKFLOW_USES
    )
    assert missing == [], (
        "job(s) call pull_request.yaml without granting actions: read, "
        "which GitHub requires to start the called workflow now that its "
        f"changes job requests that permission: {missing}"
    )
