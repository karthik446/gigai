"""Classify a pull-request's changed paths and decide which diff the
``changes`` job's ``detect`` step should use.

This is the tested core of the ``detect`` step in
``.github/workflows/pull_request.yaml``. The workflow step is a thin shell
wrapper that gathers the git/gh state and calls this module; the actual
decision logic (path classification, incremental-vs-whole-PR diff choice,
the ``before``-run lookup) lives here so it can be unit tested without a
GitHub Actions runner.

Design:

* ``is_code_path`` / ``classify_paths`` -- pure path classification, no I/O.
* ``decide`` -- pure decision function. Takes the event shape and a
  caller-supplied diff/lookup callables' *results* (not the callables
  themselves) so tests can feed it exact scenarios without touching git or
  the network.
* ``main`` -- the CLI entry point the workflow step calls. Does the actual
  ``git diff`` / ``gh api`` calls, then prints ``GITHUB_OUTPUT`` lines and a
  human-readable log to stdout.

Non-code paths (everything else counts as code, per the spec this module
implements): ``*.md``, ``*.markdown``, ``*.txt``, ``docs/**``,
``.orchestrator/**``, ``.claude/**/*.md``. Note ``.claude/**/*.py`` and
``.claude/**/*.sh`` are code -- only ``.md`` files under ``.claude/`` are
exempt.

Runtime requirements: stdlib only (argparse, dataclasses, fnmatch, os,
subprocess, sys) -- no third-party imports, so it runs on the ``changes``
job's bare runner ``python3`` without a ``setup-python``/``uv sync`` step.
Minimum Python: 3.11, matching this repo's ``pyproject.toml``
``requires-python``; nothing here needs newer syntax (no ``match``
statements, no 3.12-only stdlib). ``ubuntu-24.04`` GitHub-hosted runners
ship Python 3.12 as system ``python3`` by default, which also satisfies
this floor.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

ZERO_SHA = "0000000000000000000000000000000000000000"

# Ordered so the log can say which rule matched; order does not affect the
# code/non-code verdict since patterns don't overlap in a way that matters.
_NON_CODE_GLOBS = (
    "*.md",
    "*.markdown",
    "*.txt",
    "docs/**",
    ".orchestrator/**",
    ".claude/**/*.md",
)


def is_code_path(path: str) -> bool:
    """Return True if ``path`` counts as code (anything not matched below)."""

    normalized = path.replace(os.sep, "/")
    for pattern in _NON_CODE_GLOBS:
        if fnmatch.fnmatch(normalized, pattern):
            return False
        # fnmatch's "**" behaves like "*" (no path-separator awareness), so
        # a bare "docs/**" already matches "docs/foo/bar.py" via "*"
        # semantics on the joined string; this is intentional and covered by
        # tests. ".claude/**/*.md" likewise matches any depth because "*"
        # matches "/" under fnmatch.
    return True


def classify_paths(paths: list[str]) -> bool:
    """Return True if any of ``paths`` is code (i.e. the whole set is not
    all non-code)."""

    return any(is_code_path(p) for p in paths)


@dataclass
class Decision:
    code_changed: bool
    diff_base: str
    diff_head: str
    mode: str  # "whole_pr" | "incremental"
    reason: str
    before_conclusion: str | None = None
    changed_paths: list[str] = field(default_factory=list)

    def log_lines(self) -> list[str]:
        lines = [
            f"diff mode: {self.mode} ({self.diff_base}..{self.diff_head})",
            f"reason: {self.reason}",
        ]
        if self.before_conclusion is not None:
            lines.append(f"before run conclusion: {self.before_conclusion}")
        lines.append(f"code_changed: {self.code_changed}")
        return lines


def decide_non_pr_profile(profile: str, sha: str, parent_sha: str) -> Decision:
    """release/full (or any non-``pr``) profile: always run everything."""

    return Decision(
        code_changed=True,
        diff_base=parent_sha,
        diff_head=sha,
        mode="whole_pr",
        reason=f"non-pr profile ({profile}) always runs the full suite",
    )


def decide_synchronize(
    *,
    base_sha: str,
    before_sha: str,
    sha: str,
    before_is_ancestor: bool | None,
) -> tuple[str, str, str, str]:
    """Decide the diff range for a ``synchronize`` push.

    Returns (diff_base, diff_head, mode, reason_suffix) where mode is
    "incremental" only when it is safe to trust the incremental diff (the
    caller still has to classify the resulting paths and additionally
    require ``before_run_conclusion == "success"`` before skipping).

    This function does not itself decide skip/run -- callers combine its
    output with the incremental path classification and the before-run
    conclusion (passed separately to ``decide``), per the fallback rules:

    * ``before`` missing / all-zero / not an ancestor of ``sha`` -> whole-PR
      diff (force-push or first push case).
    * otherwise -> incremental diff (base..head narrowed to before..sha);
      whether that incremental diff is enough to *skip* the heavy jobs is
      decided by the caller using ``before_run_conclusion``.
    """

    if not before_sha or before_sha == ZERO_SHA:
        return (
            base_sha,
            sha,
            "whole_pr",
            "before SHA missing or all-zero (first push to this PR run)",
        )
    if before_is_ancestor is False:
        return (
            base_sha,
            sha,
            "whole_pr",
            "before SHA is not an ancestor of the head (force-push)",
        )
    if before_is_ancestor is None:
        # Ancestry could not be determined (e.g. shallow history / lookup
        # failure) -- fail safe to the whole-PR diff.
        return (
            base_sha,
            sha,
            "whole_pr",
            "could not determine whether before SHA is an ancestor of head; failing safe",
        )
    return (
        before_sha,
        sha,
        "incremental",
        "before SHA is a valid ancestor; using incremental diff",
    )


def decide(
    *,
    event_name: str,
    profile: str,
    action: str,
    base_sha: str,
    before_sha: str,
    sha: str,
    parent_sha: str,
    before_is_ancestor: bool | None,
    before_run_conclusion: str | None,
    diff_paths_fn,
) -> Decision:
    """Top-level decision function.

    ``diff_paths_fn`` is called as ``diff_paths_fn(diff_base, diff_head)``
    and must return the list of changed paths for that range; injected so
    tests never shell out to git.
    """

    if profile != "pr":
        return decide_non_pr_profile(profile, sha, parent_sha)

    if event_name != "pull_request":
        # workflow_dispatch without a profile override, or any other
        # trigger for the "pr" profile: treat like the parent-commit diff
        # used previously for non-pull_request events.
        paths = diff_paths_fn(f"{sha}^", sha)
        return Decision(
            code_changed=classify_paths(paths),
            diff_base=f"{sha}^",
            diff_head=sha,
            mode="whole_pr",
            reason=f"event '{event_name}' is not pull_request; using parent-commit diff",
            changed_paths=paths,
        )

    if action != "synchronize":
        paths = diff_paths_fn(base_sha, sha)
        return Decision(
            code_changed=classify_paths(paths),
            diff_base=base_sha,
            diff_head=sha,
            mode="whole_pr",
            reason=f"pull_request action '{action}' always uses the whole-PR diff",
            changed_paths=paths,
        )

    diff_base, diff_head, mode, reason_suffix = decide_synchronize(
        base_sha=base_sha,
        before_sha=before_sha,
        sha=sha,
        before_is_ancestor=before_is_ancestor,
    )

    if mode == "whole_pr":
        paths = diff_paths_fn(base_sha, sha)
        return Decision(
            code_changed=classify_paths(paths),
            diff_base=base_sha,
            diff_head=sha,
            mode="whole_pr",
            reason=reason_suffix,
            before_conclusion=before_run_conclusion,
            changed_paths=paths,
        )

    # mode == "incremental": classify the incremental diff, then only
    # actually skip when the incremental diff is all non-code AND the
    # source suite already passed for `before`.
    paths = diff_paths_fn(diff_base, diff_head)
    incremental_is_code = classify_paths(paths)

    if not incremental_is_code and before_run_conclusion == "success":
        return Decision(
            code_changed=False,
            diff_base=diff_base,
            diff_head=diff_head,
            mode="incremental",
            reason=(
                f"{reason_suffix}; incremental diff is all non-code and the "
                "source suite already passed for before SHA -- skipping"
            ),
            before_conclusion=before_run_conclusion,
            changed_paths=paths,
        )

    if not incremental_is_code:
        # Non-code incrementally, but `before`'s run did not succeed (or is
        # unknown) -- cannot trust that the heavy jobs already ran clean,
        # so fall back to the whole-PR diff to decide code_changed.
        whole_paths = diff_paths_fn(base_sha, sha)
        return Decision(
            code_changed=classify_paths(whole_paths),
            diff_base=base_sha,
            diff_head=sha,
            mode="whole_pr",
            reason=(
                f"{reason_suffix}; incremental diff is all non-code but before-run "
                f"conclusion was '{before_run_conclusion}' (not 'success') -- "
                "falling back to whole-PR diff"
            ),
            before_conclusion=before_run_conclusion,
            changed_paths=whole_paths,
        )

    # Incremental diff has code: no need to consult the before-run
    # conclusion at all, just run.
    return Decision(
        code_changed=True,
        diff_base=diff_base,
        diff_head=diff_head,
        mode="incremental",
        reason=f"{reason_suffix}; incremental diff contains code changes",
        before_conclusion=before_run_conclusion,
        changed_paths=paths,
    )


# ---------------------------------------------------------------------------
# CLI: the actual git/gh calls, wired to `decide()` above.
# ---------------------------------------------------------------------------


def _git_diff_paths(base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _git_diff_paths_two_dot(base: str, head: str) -> list[str]:
    # Used only for the "before..sha" incremental range and the legacy
    # parent-commit range, where a plain two-dot diff is what we want
    # (three-dot would diff against the merge-base, which for `before` is
    # `before` itself on a fast-forward push, but degrades badly if history
    # was rewritten -- callers already guard that case via ancestry check).
    result = subprocess.run(
        ["git", "diff", "--name-only", base, head],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _is_ancestor(candidate: str, sha: str) -> bool | None:
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", candidate, sha],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    # 128+ typically means "not a valid commit" (e.g. before SHA unknown to
    # this checkout, shallow clone) -- treat as indeterminate.
    return None


def build_runs_endpoint(repo: str, workflow_file: str, before_sha: str) -> str:
    """Build the `gh api` endpoint used to look up this workflow's run(s)
    for head SHA ``before_sha``, restricted to ``pull_request``-triggered
    runs.

    The ``event=pull_request`` filter matters: without it, a
    ``workflow_dispatch`` run on the same SHA (e.g. someone manually running
    the ``release`` or ``full`` profile against a PR branch) could be picked
    up as "the source suite passed for before", even though that run never
    exercised the ``pr`` profile's matrix. Query string, not ``-f``/``-F``:
    those pass form fields whose GET encoding is unreliable across `gh`
    versions; the endpoint takes plain query params, so build it explicitly.
    """

    return (
        f"repos/{repo}/actions/workflows/{workflow_file}/runs"
        f"?head_sha={before_sha}&event=pull_request"
    )


def _before_run_conclusion(repo: str, workflow_file: str, before_sha: str) -> str | None:
    """Look up the conclusion of this workflow's run(s) for head SHA
    ``before_sha`` via `gh api`. Returns None if it cannot be determined."""

    if not before_sha or before_sha == ZERO_SHA:
        return None
    endpoint = build_runs_endpoint(repo, workflow_file, before_sha)
    try:
        proc = subprocess.run(
            [
                "gh",
                "api",
                endpoint,
                "-q",
                ".workflow_runs[0].conclusion",
            ],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    conclusion = proc.stdout.strip()
    if not conclusion or conclusion == "null":
        return None
    return conclusion


def _diff_paths_two_dot_wrapper(diff_base: str, diff_head: str, whole_pr_base: str) -> list[str]:
    """Pick three-dot (merge-base) semantics for the whole-PR base, two-dot
    for an incremental before..sha range."""

    if diff_base == whole_pr_base:
        return _git_diff_paths(diff_base, diff_head)
    return _git_diff_paths_two_dot(diff_base, diff_head)


# ---------------------------------------------------------------------------
# Caller-permissions check: every workflow job that `uses:` this workflow
# must grant `actions: read`, or the `changes` job's `gh api` lookup (added
# in this file) fails with a permissions error when GitHub starts the
# called workflow. This is a minimal, purpose-built parser -- no YAML
# library is available in the test environment that exercises it (see
# tools/release_notes.py's parse_workflow_job_needs, which this mirrors).
# It understands exactly this repo's layout: top-level job ids at 2-space
# indent under `jobs:`, and a job's own keys (`uses:`, `permissions:`, and
# permission entries under it) at 4-space/6-space indent inside that job.
# It will misparse flow-style job blocks, anchors/aliases, or workflows
# where a job's `permissions:` isn't a simple block mapping; none of those
# appear in this repo's workflows today.
# ---------------------------------------------------------------------------

_JOB_ID_RE = re.compile(r"^  (?P<id>[A-Za-z0-9_-]+):\s*$")
_USES_RE = re.compile(r"^    uses:\s*(?P<value>\S+)\s*$")
_JOB_PERMISSIONS_RE = re.compile(r"^    permissions:\s*$")
_PERMISSION_ENTRY_RE = re.compile(r"^      (?P<key>[A-Za-z0-9_-]+):\s*(?P<value>\S+)\s*$")


@dataclass
class CallerJob:
    workflow_path: str
    job_id: str
    uses: str
    permissions: dict[str, str] = field(default_factory=dict)

    def grants_actions_read(self) -> bool:
        return self.permissions.get("actions") == "read"


def parse_caller_jobs(workflow_text: str, workflow_path: str, target_uses: str) -> list[CallerJob]:
    """Return every top-level job in ``workflow_text`` whose ``uses:`` value
    is exactly ``target_uses``, with whatever job-level ``permissions:``
    block (if any) directly follows its ``uses:``/other 4-space keys."""

    lines = workflow_text.splitlines()
    jobs_at: int | None = None
    for index, line in enumerate(lines):
        if line == "jobs:":
            jobs_at = index
            break
    if jobs_at is None:
        return []

    results: list[CallerJob] = []
    current_job: str | None = None
    current_uses: str | None = None
    current_permissions: dict[str, str] = {}
    in_permissions_block = False

    def _flush() -> None:
        if current_job is not None and current_uses is not None and current_uses == target_uses:
            results.append(
                CallerJob(
                    workflow_path=workflow_path,
                    job_id=current_job,
                    uses=current_uses,
                    permissions=dict(current_permissions),
                )
            )

    for line in lines[jobs_at + 1 :]:
        job_match = _JOB_ID_RE.match(line)
        if job_match:
            _flush()
            current_job = job_match.group("id")
            current_uses = None
            current_permissions = {}
            in_permissions_block = False
            continue
        if current_job is None:
            continue

        uses_match = _USES_RE.match(line)
        if uses_match:
            current_uses = uses_match.group("value")
            in_permissions_block = False
            continue

        if _JOB_PERMISSIONS_RE.match(line):
            in_permissions_block = True
            continue

        if in_permissions_block:
            entry_match = _PERMISSION_ENTRY_RE.match(line)
            if entry_match:
                current_permissions[entry_match.group("key")] = entry_match.group("value")
                continue
            # A line at 4-space indent (another job key) or a blank/job-id
            # line ends the permissions block; a 6-space-indent line that
            # doesn't match the entry pattern (e.g. a comment) is ignored
            # without ending the block.
            if line.strip() == "" or line.startswith("      "):
                continue
            in_permissions_block = False

    _flush()
    return results


def caller_jobs_missing_actions_read(
    workflow_texts: dict[str, str], target_uses: str
) -> list[CallerJob]:
    """Across ``{workflow_path: text}``, return every job that `uses:`
    ``target_uses`` and does not grant ``actions: read``."""

    missing: list[CallerJob] = []
    for path, text in workflow_texts.items():
        for job in parse_caller_jobs(text, path, target_uses):
            if not job.grants_actions_read():
                missing.append(job)
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", default=os.environ.get("EVENT_NAME", ""))
    parser.add_argument("--profile", default=os.environ.get("PROFILE", "pr"))
    parser.add_argument("--action", default=os.environ.get("PR_ACTION", ""))
    parser.add_argument("--base-sha", default=os.environ.get("BASE_SHA", ""))
    parser.add_argument("--before-sha", default=os.environ.get("BEFORE_SHA", ""))
    parser.add_argument("--sha", default=os.environ.get("SHA", ""))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument(
        "--workflow-file",
        default=os.environ.get("WORKFLOW_FILE", "pull_request.yaml"),
    )
    parser.add_argument(
        "--github-output",
        default=os.environ.get("GITHUB_OUTPUT", ""),
        help="Path to append GITHUB_OUTPUT-format key=value lines to.",
    )
    args = parser.parse_args(argv)

    parent_sha = f"{args.sha}^"

    before_is_ancestor = None
    before_run_conclusion = None
    if (
        args.profile == "pr"
        and args.event_name == "pull_request"
        and args.action == "synchronize"
        and args.before_sha
        and args.before_sha != ZERO_SHA
    ):
        before_is_ancestor = _is_ancestor(args.before_sha, args.sha)
        if before_is_ancestor:
            before_run_conclusion = _before_run_conclusion(
                args.repo, args.workflow_file, args.before_sha
            )

    def diff_paths_fn(diff_base: str, diff_head: str) -> list[str]:
        return _diff_paths_two_dot_wrapper(diff_base, diff_head, args.base_sha)

    decision = decide(
        event_name=args.event_name,
        profile=args.profile,
        action=args.action,
        base_sha=args.base_sha,
        before_sha=args.before_sha,
        sha=args.sha,
        parent_sha=parent_sha,
        before_is_ancestor=before_is_ancestor,
        before_run_conclusion=before_run_conclusion,
        diff_paths_fn=diff_paths_fn,
    )

    for line in decision.log_lines():
        print(line)
    if decision.changed_paths:
        print("changed paths:")
        for p in decision.changed_paths:
            print(f"  {p}")

    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"code_changed={'true' if decision.code_changed else 'false'}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
