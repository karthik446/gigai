"""Validate the inputs of a manually dispatched release before any tag exists.

``release.yml`` can be started with ``workflow_dispatch`` (``sha`` + ``version``).
Everything is checked against the named commit, not the checkout, and the check
fails closed: the SHA must be a full commit id on ``main`` or a
``karthik446/gigai-v*`` branch, the tag ``v<version>`` must not exist yet, and
``pyproject.toml``, ``CATALOG_REVISION`` and ``CHANGELOG.md`` at that commit must
all name ``<version>``.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase
import re
import subprocess
import tomllib

from tools import release_notes

ALLOWED_BRANCHES = ("main", "karthik446/gigai-v*")

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^[0-9]+(\.[0-9]+)+$")
_CATALOG_REVISION = re.compile(r'^CATALOG_REVISION\s*=\s*"(?P<value>[^"]*)"\s*$', re.MULTILINE)


class ReleaseDispatchError(ValueError):
    """Raised when a dispatched release's inputs cannot be released."""


@dataclass(frozen=True)
class DispatchFacts:
    """What the repository says about the requested ``sha`` and ``version``."""

    resolved_commit: str | None
    branches: tuple[str, ...]
    tag_exists: bool
    pyproject: str
    catalog: str
    changelog: str


def _catalog_revision(catalog: str) -> str:
    match = _CATALOG_REVISION.search(catalog)
    if match is None:
        raise ReleaseDispatchError("src/gigai/catalog.py defines no CATALOG_REVISION")
    return match.group("value")


def _project_version(pyproject: str) -> str:
    project = tomllib.loads(pyproject).get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str) or not version:
        raise ReleaseDispatchError("pyproject.toml [project].version is not a static string")
    return version


def allowed_branches(branches: Iterable[str]) -> list[str]:
    """Return the branches a release may come from."""

    return [
        branch
        for branch in branches
        if any(fnmatchcase(branch, pattern) for pattern in ALLOWED_BRANCHES)
    ]


def validate_dispatch(sha: str, version: str, facts: DispatchFacts) -> None:
    """Raise :class:`ReleaseDispatchError` unless ``sha`` may be released as ``version``."""

    if not _FULL_SHA.match(sha):
        raise ReleaseDispatchError(f"sha {sha!r} must be a full 40-character lowercase commit id")
    if not _VERSION.match(version):
        raise ReleaseDispatchError(f"version {version!r} must look like 0.1.9 or 0.1.9.1")
    if facts.resolved_commit != sha:
        raise ReleaseDispatchError(f"sha {sha} is not a commit in this repository")
    if not allowed_branches(facts.branches):
        raise ReleaseDispatchError(
            f"sha {sha} is on no allowed branch ({', '.join(ALLOWED_BRANCHES)}); "
            f"found on: {', '.join(facts.branches) or 'no branch'}"
        )
    if facts.tag_exists:
        raise ReleaseDispatchError(f"tag v{version} already exists")
    project_version = _project_version(facts.pyproject)
    if project_version != version:
        raise ReleaseDispatchError(
            f"pyproject.toml at {sha} has version {project_version!r}, not {version!r}"
        )
    catalog_revision = _catalog_revision(facts.catalog)
    if catalog_revision != f"v{version}":
        raise ReleaseDispatchError(
            f"CATALOG_REVISION at {sha} is {catalog_revision!r}, not 'v{version}'"
        )
    try:
        release_notes.extract_release_notes(facts.changelog, version)
    except release_notes.ReleaseNotesError as error:
        raise ReleaseDispatchError(f"CHANGELOG at {sha}: {error}") from error


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def _show(sha: str, path: str) -> str:
    result = _git("show", f"{sha}:{path}", check=False)
    return result.stdout if result.returncode == 0 else ""


def gather_facts(sha: str, version: str, remote: str = "origin") -> DispatchFacts:
    """Read the facts from the local clone (needs full history, tags and remote branches)."""

    resolved = _git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", check=False)
    resolved_commit = resolved.stdout.strip() if resolved.returncode == 0 else None
    branches: tuple[str, ...] = ()
    if resolved_commit:
        listed = _git(
            "branch", "-r", "--contains", resolved_commit, "--format=%(refname:short)"
        ).stdout.split()
        prefix = f"{remote}/"
        branches = tuple(
            name.removeprefix(prefix) for name in listed if name.startswith(prefix) and name != f"{prefix}HEAD"
        )
    tag = f"refs/tags/v{version}"
    local_tag = bool(_git("tag", "--list", f"v{version}").stdout.strip())
    remote_tag = bool(_git("ls-remote", "--tags", remote, tag).stdout.strip())
    return DispatchFacts(
        resolved_commit=resolved_commit,
        branches=branches,
        tag_exists=local_tag or remote_tag,
        pyproject=_show(sha, "pyproject.toml"),
        catalog=_show(sha, "src/gigai/catalog.py"),
        changelog=_show(sha, "CHANGELOG.md"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args(argv)
    try:
        validate_dispatch(args.sha, args.version, gather_facts(args.sha, args.version, args.remote))
    except (ReleaseDispatchError, subprocess.CalledProcessError) as error:
        print(f"release dispatch rejected: {error}")
        return 1
    print(f"release dispatch accepted: v{args.version} at {args.sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
