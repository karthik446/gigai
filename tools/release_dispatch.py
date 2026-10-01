"""Validate the inputs of a manually dispatched release before any tag exists.

``release.yml`` can be started with ``workflow_dispatch`` (``sha`` + ``version``).
Everything is checked against the named commit, not the checkout, and the check
fails closed: the SHA must be a full commit id on ``main`` or a
``karthik446/gigai-v*`` branch, and ``pyproject.toml``, ``CATALOG_REVISION`` and
``CHANGELOG.md`` at that commit must all name ``<version>``. The tag ``v<version>``
must not exist yet, except that a re-dispatch after a partial run may reuse a tag
that already peels to exactly ``sha`` while the version is not on PyPI and no
GitHub Release exists for it.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase
import os
import re
import subprocess
import tomllib
import urllib.error
import urllib.request

from tools import release_notes

ALLOWED_BRANCHES = ("main", "karthik446/gigai-v*")

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^[0-9]+(\.[0-9]+)+$")
_PATCH_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[1-9][0-9]*$")
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
    tag_commit: str | None = None
    release_exists: bool = False
    on_pypi: bool = False


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


def release_kind(version: str) -> str:
    """Return ``patch`` for a four-part version (0.1.10.4), else ``minor``.

    A patch releases without a person. Everything else (0.1.11, 0.2.0, 1.0.0,
    and any spelling this does not recognise) needs the operator's approval.
    """

    return "patch" if _PATCH_VERSION.match(version) else "minor"


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
        if facts.release_exists:
            raise ReleaseDispatchError(
                f"tag v{version} already exists and a GitHub Release exists for it"
            )
        if facts.on_pypi:
            raise ReleaseDispatchError(f"tag v{version} already exists and {version} is on PyPI")
        if facts.tag_commit != sha:
            raise ReleaseDispatchError(
                f"tag v{version} already exists at {facts.tag_commit or 'an unknown commit'}, not {sha}"
            )
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
    local = _git("rev-parse", "--verify", "--quiet", f"{tag}^{{}}", check=False)
    local_commit = local.stdout.strip() if local.returncode == 0 else None
    remote_commit = _remote_tag_commit(remote, tag)
    tag_exists = bool(local_commit or remote_commit)
    seen = {commit for commit in (local_commit, remote_commit) if commit}
    # A local and a remote tag that disagree can never match the input sha.
    tag_commit = seen.pop() if len(seen) == 1 else None
    return DispatchFacts(
        resolved_commit=resolved_commit,
        branches=branches,
        tag_exists=tag_exists,
        pyproject=_show(sha, "pyproject.toml"),
        catalog=_show(sha, "src/gigai/catalog.py"),
        changelog=_show(sha, "CHANGELOG.md"),
        tag_commit=tag_commit,
        release_exists=_release_exists(f"v{version}") if tag_exists else False,
        on_pypi=_on_pypi(version) if tag_exists else False,
    )


def _remote_tag_commit(remote: str, tag: str) -> str | None:
    """Return the commit the remote tag peels to, or ``None`` when it has no such tag."""

    listed = _git("ls-remote", "--tags", remote, tag, f"{tag}^{{}}").stdout.splitlines()
    commits = {}
    for line in listed:
        commit, _, ref = line.partition("\t")
        commits[ref] = commit
    return commits.get(f"{tag}^{{}}") or commits.get(tag)


def _on_pypi(version: str, package: str = "gigai") -> bool:
    """Ask PyPI whether ``version`` is published; fail closed on anything but 200 or 404."""

    url = f"https://pypi.org/pypi/{package}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed https URL
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise ReleaseDispatchError(f"cannot check PyPI for {package} {version}: HTTP {error.code}") from error
    except (urllib.error.URLError, OSError) as error:
        raise ReleaseDispatchError(f"cannot check PyPI for {package} {version}: {error}") from error


def _release_exists(tag: str) -> bool:
    """Ask GitHub whether a Release exists for ``tag``; fail closed on any other gh outcome."""

    command = ["gh", "release", "view", tag]
    repository = os.environ.get("GITHUB_REPOSITORY")
    if repository:
        command += ["--repo", repository]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as error:
        raise ReleaseDispatchError(f"cannot check for a GitHub Release for {tag}: {error}") from error
    if result.returncode == 0:
        return True
    if "release not found" in result.stderr.lower():
        return False
    raise ReleaseDispatchError(
        f"cannot check for a GitHub Release for {tag}: {result.stderr.strip() or result.returncode}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", metavar="VERSION", help="print kind=patch or kind=minor and exit")
    parser.add_argument("--sha")
    parser.add_argument("--version")
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args(argv)
    if args.kind is not None:
        print(f"kind={release_kind(args.kind)}")
        return 0
    if not args.sha or not args.version:
        parser.error("--sha and --version are required")
    try:
        validate_dispatch(args.sha, args.version, gather_facts(args.sha, args.version, args.remote))
    except (ReleaseDispatchError, subprocess.CalledProcessError) as error:
        print(f"release dispatch rejected: {error}")
        return 1
    print(f"release dispatch accepted: v{args.version} at {args.sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
