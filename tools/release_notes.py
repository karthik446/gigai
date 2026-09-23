"""Extract one version's release notes section from ``CHANGELOG.md``."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


class ReleaseNotesError(ValueError):
    """Raised when a version's CHANGELOG section is missing or empty."""


_RELEASED_VERSIONS_HEADING = "## Released versions"
_VERSION_HEADING = re.compile(r"^### (?P<version>\S+)\s*$", re.MULTILINE)
_ANY_HEADING = re.compile(r"^#{2,3} \S", re.MULTILINE)


def extract_release_notes(changelog: str, version: str) -> str:
    """Return the ``### {version}`` section body under "## Released versions".

    The section runs from its ``### {version}`` heading up to (but not
    including) the next ``### `` heading, the next ``## `` heading (the end
    of "Released versions" itself), or end of file. Raises
    :class:`ReleaseNotesError` if the version has no section, or if the
    section body is empty once stripped.
    """

    released_at = changelog.find(_RELEASED_VERSIONS_HEADING)
    if released_at == -1:
        raise ReleaseNotesError(f'CHANGELOG.md has no {_RELEASED_VERSIONS_HEADING!r} heading')
    released_section = changelog[released_at:]

    for heading in _VERSION_HEADING.finditer(released_section):
        if heading.group("version") != version:
            continue
        body_start = heading.end()
        next_heading = _ANY_HEADING.search(released_section, body_start)
        body_end = next_heading.start() if next_heading else len(released_section)
        body = released_section[body_start:body_end].strip("\n")
        if not body.strip():
            raise ReleaseNotesError(f"CHANGELOG.md section for {version!r} is empty")
        return body

    raise ReleaseNotesError(f"CHANGELOG.md has no {'### ' + version!r} section under {_RELEASED_VERSIONS_HEADING!r}")


_JOB_ID = re.compile(r"^  (?P<id>[A-Za-z0-9_-]+):\s*$")
_NEEDS_SCALAR = re.compile(r"^    needs:\s*(?P<value>[A-Za-z0-9_-]+)\s*$")
_NEEDS_LIST = re.compile(r"^    needs:\s*\[(?P<items>[^\]]*)\]\s*$")
_PERMISSIONS_BLOCK_START = re.compile(r"^    permissions:\s*$")
_PERMISSION_ENTRY = re.compile(r"^      (?P<scope>[A-Za-z0-9_-]+):\s*(?P<access>\S+)\s*$")


class WorkflowJob:
    """One top-level job's ``needs:`` list and job-level ``permissions:`` block.

    ``permissions`` is ``{}`` when the job declares no job-level block (it
    then inherits the workflow's top-level ``permissions:``, which this does
    not parse).
    """

    __slots__ = ("needs", "permissions")

    def __init__(self) -> None:
        self.needs: list[str] = []
        self.permissions: dict[str, str] = {}


def parse_workflow_jobs(workflow: str) -> dict[str, WorkflowJob]:
    """Return ``{job_id: WorkflowJob}`` for each top-level job.

    This is a minimal, purpose-built stand-in for ``yaml.safe_load`` — no
    YAML library is available to the test environment that runs this (see
    .orchestrator/workers/release-pipeline.md). It understands exactly the
    layout this repo's workflow files use:

    - top-level job ids are keys at 2-space indent directly under ``jobs:``
    - a job's ``needs:`` is a key at 4-space indent inside that job, either a
      bare scalar or a ``[a, b]`` flow-style list
    - a job's ``permissions:`` is a block-style mapping at 4-space indent,
      whose ``scope: access`` entries sit at 6-space indent immediately below

    It will misparse workflows using flow-style job or permissions blocks,
    anchors/aliases, or a ``needs:`` value split across multiple lines; none
    of those appear in this repo's workflows today.
    """

    lines = workflow.splitlines()
    jobs_at: int | None = None
    for index, line in enumerate(lines):
        if line == "jobs:":
            jobs_at = index
            break
    if jobs_at is None:
        raise ValueError("workflow has no top-level 'jobs:' key")

    jobs: dict[str, WorkflowJob] = {}
    current_job: str = ""
    in_permissions_block = False
    for line in lines[jobs_at + 1 :]:
        job_match = _JOB_ID.match(line)
        if job_match:
            current_job = job_match.group("id")
            jobs[current_job] = WorkflowJob()
            in_permissions_block = False
            continue
        if not current_job:
            continue
        scalar_match = _NEEDS_SCALAR.match(line)
        if scalar_match:
            jobs[current_job].needs = [scalar_match.group("value")]
            in_permissions_block = False
            continue
        list_match = _NEEDS_LIST.match(line)
        if list_match:
            jobs[current_job].needs = [item.strip() for item in list_match.group("items").split(",") if item.strip()]
            in_permissions_block = False
            continue
        if _PERMISSIONS_BLOCK_START.match(line):
            in_permissions_block = True
            continue
        if in_permissions_block:
            entry_match = _PERMISSION_ENTRY.match(line)
            if entry_match:
                jobs[current_job].permissions[entry_match.group("scope")] = entry_match.group("access")
                continue
            in_permissions_block = False
    return jobs


def parse_workflow_job_needs(workflow: str) -> dict[str, list[str]]:
    """Return ``{job_id: [needed_job_id, ...]}`` for each top-level job.

    A thin projection of :func:`parse_workflow_jobs` for callers that only
    need the dependency graph.
    """

    return {job_id: job.needs for job_id, job in parse_workflow_jobs(workflow).items()}


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--version", required=True, help="static version, e.g. 0.1.8 (no leading v)")
    return parser


def main() -> None:
    args = _arguments().parse_args()
    changelog = args.changelog.read_text(encoding="utf-8")
    notes = extract_release_notes(changelog, args.version)
    print(notes)


if __name__ == "__main__":
    main()
