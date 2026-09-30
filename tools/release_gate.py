"""Decide whether the release gate already passed on the tagged commit's tree.

A squash merge gives the tag a new commit id but keeps the tree of the tested
PR head, so the lookup compares tree ids, not commit ids. When no successful
gate run matches, the caller runs the exact-tag CI instead (fail closed).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping
import json
import os
import subprocess
import sys

GATE_DISPLAY_TITLE = "Pull request (release)"
GATE_WORKFLOW = "pull_request.yaml"
GATE_RUN_LIMIT = 30


def find_gate_commit(
    tree: str,
    runs: Iterable[Mapping[str, object]],
    tree_of_commit: Callable[[str], str | None],
) -> str | None:
    """Return the head sha of a successful gate run whose commit has ``tree``."""

    for run in runs:
        if run.get("display_title") != GATE_DISPLAY_TITLE:
            continue
        if run.get("conclusion", "success") != "success":
            continue
        head_sha = run.get("head_sha")
        if not isinstance(head_sha, str) or not head_sha:
            continue
        if tree_of_commit(head_sha) == tree:
            return head_sha
    return None


def _gh_json(path: str) -> object:
    result = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def _github_runs(repository: str) -> list[Mapping[str, object]]:
    payload = _gh_json(
        f"repos/{repository}/actions/workflows/{GATE_WORKFLOW}/runs"
        f"?event=workflow_dispatch&status=success&per_page={GATE_RUN_LIMIT}"
    )
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    return [run for run in runs if isinstance(run, dict)]


def _github_tree(repository: str, sha: str) -> str | None:
    try:
        payload = _gh_json(f"repos/{repository}/git/commits/{sha}")
    except subprocess.CalledProcessError:
        return None
    tree = payload.get("tree") if isinstance(payload, dict) else None
    sha_value = tree.get("sha") if isinstance(tree, dict) else None
    return sha_value if isinstance(sha_value, str) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="the tagged commit")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args(argv)

    tree = subprocess.run(
        ["git", "rev-parse", f"{args.commit}^{{tree}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        gate = find_gate_commit(
            tree,
            _github_runs(args.repository),
            lambda sha: _github_tree(args.repository, sha),
        )
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as error:
        print(f"gate lookup failed, running exact-tag CI: {error}", file=sys.stderr)
        gate = None
    if gate:
        print(f"gate run on {gate} passed for tree {tree}", file=sys.stderr)
    else:
        print(f"no passing gate run for tree {tree}; exact-tag CI will run", file=sys.stderr)
    print(f"gate_green={'true' if gate else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
