"""Find the green pre-check run on exactly the commit being released.

The pre-check is ``pull_request.yaml`` dispatched with ``profile=release``. It
tests the commit, builds the release files and uploads them as the artifact
``release-dist-<version>``. ``release.yml`` runs no checks of its own: it asks
here for that run and publishes its files. The match is by commit id, not by
tree: the operator releases the branch head, so a head that moved after the
pre-check needs a new pre-check. Anything else fails closed with one line that
names the pre-check to dispatch.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
import json
import os
import subprocess
import sys

GATE_DISPLAY_TITLE = "Pull request (release)"
GATE_WORKFLOW = "pull_request.yaml"
GATE_RUN_LIMIT = 30
DIST_ARTIFACT_PREFIX = "release-dist-"

Run = Mapping[str, object]


class PrecheckError(ValueError):
    """Raised when the commit has no usable green pre-check run."""


def precheck_command(branch: str) -> str:
    """Return the command that dispatches the pre-check on ``branch``."""

    return f"gh workflow run {GATE_WORKFLOW} --ref {branch} -f profile=release"


def dist_artifact_name(version: str) -> str:
    """Return the name of the artifact the pre-check uploads for ``version``."""

    return f"{DIST_ARTIFACT_PREFIX}{version}"


def find_precheck_run(sha: str, branch: str, runs: Iterable[Run]) -> int:
    """Return the id of the newest green pre-check run on exactly ``sha``.

    ``runs`` are ``pull_request.yaml`` runs, newest first: the ones on ``sha``
    and the recent ones on ``branch`` (used only to say that the head moved).
    """

    prechecks = [run for run in runs if run.get("display_title") == GATE_DISPLAY_TITLE]
    on_sha = [run for run in prechecks if run.get("head_sha") == sha]
    for run in on_sha:
        run_id = run.get("id")
        if run.get("status") == "completed" and run.get("conclusion") == "success" and isinstance(run_id, int):
            return run_id
    again = f"dispatch a new one: {precheck_command(branch)}"
    for run in on_sha:
        if run.get("status") != "completed":
            raise PrecheckError(
                f"pre-check run {run.get('id')} on {sha} is still running; "
                "run Release again when it is green"
            )
    if on_sha:
        run = on_sha[0]
        raise PrecheckError(
            f"pre-check run {run.get('id')} on {sha} ended {run.get('conclusion')}, not success; {again}"
        )
    if prechecks:
        run = prechecks[0]
        raise PrecheckError(
            f"{branch} moved after its last pre-check: run {run.get('id')} checked "
            f"{run.get('head_sha')}, the head is now {sha}; {again}"
        )
    raise PrecheckError(f"no pre-check run on {sha}; {again}")


def require_dist_artifact(run_id: int, version: str, branch: str, artifacts: Iterable[Run]) -> None:
    """Require the pre-check run to still hold the release files for ``version``."""

    name = dist_artifact_name(version)
    for artifact in artifacts:
        if artifact.get("name") == name and not artifact.get("expired"):
            return
    raise PrecheckError(
        f"pre-check run {run_id} has no {name} artifact (expired, or not built for {version}); "
        f"dispatch a new one: {precheck_command(branch)}"
    )


def _gh_json(path: str) -> object:
    result = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def _listed(path: str, key: str) -> list[Run]:
    payload = _gh_json(path)
    items = payload.get(key, []) if isinstance(payload, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _github_runs(repository: str, sha: str, branch: str) -> list[Run]:
    runs = f"repos/{repository}/actions/workflows/{GATE_WORKFLOW}/runs?event=workflow_dispatch"
    on_sha = _listed(f"{runs}&head_sha={sha}&per_page=100", "workflow_runs")
    on_branch = _listed(f"{runs}&branch={branch}&per_page={GATE_RUN_LIMIT}", "workflow_runs")
    return on_sha + on_branch


def _github_artifacts(repository: str, run_id: int) -> list[Run]:
    return _listed(f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100", "artifacts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="the full commit id being released")
    parser.add_argument("--branch", required=True, help="the branch the release was started on")
    parser.add_argument("--version", required=True, help="the version in pyproject.toml at the commit")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args(argv)

    try:
        run_id = find_precheck_run(
            args.commit, args.branch, _github_runs(args.repository, args.commit, args.branch)
        )
        require_dist_artifact(
            run_id, args.version, args.branch, _github_artifacts(args.repository, run_id)
        )
    except PrecheckError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as error:
        print(f"::error::pre-check lookup failed, nothing released: {error}", file=sys.stderr)
        return 1
    print(f"run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
