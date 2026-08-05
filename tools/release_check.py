"""Fail-closed checks for a versioned GigAI release."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from email.parser import BytesParser
import hashlib
from pathlib import Path
import re
import tarfile
import tomllib
import zipfile


class ReleaseCheckError(ValueError):
    """Raised when release metadata or artifacts disagree."""


@dataclass(frozen=True)
class ReleaseArtifacts:
    """The one wheel and source distribution eligible for a release."""

    wheel: Path
    sdist: Path


def project_metadata(project_file: Path) -> tuple[str, str]:
    """Return the static project name and version from ``pyproject.toml``."""

    with project_file.open("rb") as handle:
        project = tomllib.load(handle).get("project")
    if not isinstance(project, dict):
        raise ReleaseCheckError("pyproject.toml must define a [project] table")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name:
        raise ReleaseCheckError("[project].name must be a nonempty static string")
    if not isinstance(version, str) or not version:
        raise ReleaseCheckError("[project].version must be a nonempty static string")
    return name, version


def normalized_distribution_name(name: str) -> str:
    """Return the normalized filename component for a distribution name."""

    return re.sub(r"[-_.]+", "-", name).lower()


def assert_release_tag(version: str, tag: str) -> None:
    """Require the exact annotated-tag spelling for the static version."""

    expected = f"v{version}"
    if tag != expected:
        raise ReleaseCheckError(
            f"release tag {tag!r} does not match static project version {version!r}; "
            f"expected {expected!r}"
        )


def verify_lockfile_project(lock_file: Path, name: str, version: str) -> None:
    """Require one editable project record with the static version in ``uv.lock``."""

    with lock_file.open("rb") as handle:
        packages = tomllib.load(handle).get("package")
    if not isinstance(packages, list):
        raise ReleaseCheckError(f"{lock_file} must define [[package]] records")
    project_records = [
        package
        for package in packages
        if isinstance(package, dict)
        and normalized_distribution_name(str(package.get("name", "")))
        == normalized_distribution_name(name)
        and package.get("source") == {"editable": "."}
    ]
    if len(project_records) != 1:
        raise ReleaseCheckError(
            f"expected exactly one editable {name!r} project entry in {lock_file}; "
            f"found {len(project_records)}"
        )
    locked_version = project_records[0].get("version")
    if locked_version != version:
        raise ReleaseCheckError(
            f"{lock_file} project version {locked_version!r} does not match {version!r}"
        )


def _one(paths: list[Path], description: str) -> Path:
    if len(paths) != 1:
        raise ReleaseCheckError(f"expected exactly one {description}; found {len(paths)}")
    return paths[0]


def release_artifacts(dist: Path, name: str, version: str) -> ReleaseArtifacts:
    """Find exactly one wheel and one source distribution for ``name==version``."""

    normalized_name = normalized_distribution_name(name)
    wheel = _one(
        sorted(dist.glob(f"{normalized_name}-{version}-*.whl")),
        f"wheel for {name} {version}",
    )
    sdist = _one(
        sorted(dist.glob(f"{normalized_name}-{version}.tar.gz")),
        f"source distribution for {name} {version}",
    )
    return ReleaseArtifacts(wheel=wheel, sdist=sdist)


def _metadata_values(metadata: bytes, source: Path) -> tuple[str, str]:
    parsed = BytesParser().parsebytes(metadata)
    name = parsed.get("Name")
    version = parsed.get("Version")
    if not name or not version:
        raise ReleaseCheckError(f"{source} metadata must contain Name and Version")
    return name, version


def _wheel_metadata(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        candidates = [member for member in archive.namelist() if member.endswith(".dist-info/METADATA")]
        member = _one([Path(candidate) for candidate in candidates], f"METADATA record in {path.name}")
        return _metadata_values(archive.read(member.as_posix()), path)


def _sdist_metadata(path: Path) -> tuple[str, str]:
    with tarfile.open(path, "r:gz") as archive:
        candidates = [
            member
            for member in archive.getmembers()
            if member.name.endswith("/PKG-INFO") and member.name.count("/") == 1
        ]
        member = _one([Path(candidate.name) for candidate in candidates], f"PKG-INFO record in {path.name}")
        extracted = archive.extractfile(member.as_posix())
        if extracted is None:
            raise ReleaseCheckError(f"could not read {member} from {path}")
        return _metadata_values(extracted.read(), path)


def verify_artifacts(artifacts: ReleaseArtifacts, name: str, version: str) -> None:
    """Require wheel and sdist metadata to equal the static project metadata."""

    for artifact, read_metadata in (
        (artifacts.wheel, _wheel_metadata),
        (artifacts.sdist, _sdist_metadata),
    ):
        artifact_name, artifact_version = read_metadata(artifact)
        if normalized_distribution_name(artifact_name) != normalized_distribution_name(name):
            raise ReleaseCheckError(
                f"{artifact.name} metadata name {artifact_name!r} does not match {name!r}"
            )
        if artifact_version != version:
            raise ReleaseCheckError(
                f"{artifact.name} metadata version {artifact_version!r} does not match {version!r}"
            )


def sha256(path: Path) -> str:
    """Return the lowercase SHA-256 digest of one release artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksum_manifest(artifacts: ReleaseArtifacts) -> str:
    """Return stable SHA-256 records for precisely the releasable artifacts."""

    records = sorted(
        (artifact.name, sha256(artifact)) for artifact in (artifacts.wheel, artifacts.sdist)
    )
    return "".join(f"{digest}  {name}\n" for name, digest in records)


def write_checksums(dist: Path, artifacts: ReleaseArtifacts) -> Path:
    """Write the release-asset checksum manifest in stable filename order."""

    target = dist / "SHA256SUMS"
    target.write_text(checksum_manifest(artifacts), encoding="utf-8")
    return target


def verify_checksums(dist: Path, artifacts: ReleaseArtifacts) -> None:
    """Require ``SHA256SUMS`` to name and hash only the verified artifacts."""

    path = dist / "SHA256SUMS"
    if not path.is_file():
        raise ReleaseCheckError(f"missing checksum manifest: {path}")
    expected = checksum_manifest(artifacts)
    actual = path.read_text(encoding="utf-8")
    if actual != expected:
        raise ReleaseCheckError(
            f"{path} does not match the verified release artifacts and their SHA-256 digests"
        )


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    commands = parser.add_subparsers(dest="command", required=True)
    tag = commands.add_parser("verify-tag", help="require --tag to match [project].version")
    tag.add_argument("--tag", required=True)
    commands.add_parser("verify-lockfile", help="verify the editable project entry in uv.lock")
    commands.add_parser("verify-artifacts", help="verify exactly one wheel and sdist")
    commands.add_parser("write-checksums", help="verify artifacts and write dist/SHA256SUMS")
    commands.add_parser("verify-checksums", help="verify dist/SHA256SUMS against release artifacts")
    return parser


def main() -> None:
    args = _arguments().parse_args()
    name, version = project_metadata(args.project)
    if args.command == "verify-tag":
        assert_release_tag(version, args.tag)
        print(f"verified release tag {args.tag} for {name} {version}")
        return
    if args.command == "verify-lockfile":
        verify_lockfile_project(args.lock, name, version)
        print(f"verified {args.lock} project entry for {name} {version}")
        return

    artifacts = release_artifacts(args.dist, name, version)
    verify_artifacts(artifacts, name, version)
    if args.command == "write-checksums":
        checksum_path = write_checksums(args.dist, artifacts)
        print(f"wrote {checksum_path} for {name} {version}")
    elif args.command == "verify-checksums":
        verify_checksums(args.dist, artifacts)
        print(f"verified {args.dist / 'SHA256SUMS'} for {name} {version}")
    else:
        print(f"verified release artifacts for {name} {version}")


if __name__ == "__main__":
    main()
