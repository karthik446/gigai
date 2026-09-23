from __future__ import annotations

from email.message import EmailMessage
import io
from pathlib import Path
import tarfile
import zipfile

import pytest

from tools import release_check, release_notes


def _read_release_workflow() -> str:
    workflow_path = Path(__file__).resolve().parents[3] / ".github/workflows/release.yml"
    if not workflow_path.is_file():
        pytest.skip("release workflow is excluded from the offline container build context")
    return workflow_path.read_text(encoding="utf-8")


def test_pypi_publish_jobs_receive_only_distributions() -> None:
    workflow = _read_release_workflow()

    assert workflow.count("name: Prepare package-only publisher input") == 2
    assert workflow.count("cp dist/*.whl dist/*.tar.gz publish/") == 2
    assert workflow.count("packages-dir: publish/") == 2
    assert "packages-dir: dist/" not in workflow
    assert "--index https://test.pypi.org/simple" in workflow
    assert "--index https://pypi.org/simple" in workflow
    assert "--index-strategy unsafe-best-match" in workflow


def test_release_job_graph_does_not_let_verify_pypi_block_the_release() -> None:
    graph = release_notes.parse_workflow_job_needs(_read_release_workflow())

    expected_jobs = {
        "preflight",
        "build",
        "publish-pypi",
        "verify-pypi",
        "github-release",
        "post-release-compatibility",
        "verify-testpypi",
    }
    assert expected_jobs <= graph.keys(), sorted(expected_jobs - graph.keys())

    # The Release is created right after publish-pypi succeeds; a slow or
    # flaky post-publish install check must not be able to withhold it.
    assert "verify-pypi" not in graph["github-release"]
    assert set(graph["github-release"]) == {"preflight", "build", "publish-pypi"}

    # verify-pypi becomes a post-release check gated on the Release existing.
    assert "github-release" in graph["verify-pypi"]

    # publish-pypi is still gated on the pre-publish TestPyPI staging check.
    assert "verify-testpypi" in graph["publish-pypi"]

    # The full post-release compatibility matrix must not wait on the
    # post-release install check either.
    assert "verify-pypi" not in graph["post-release-compatibility"]


def test_reusable_workflow_callers_grant_the_called_jobs_permissions() -> None:
    # pull_request.yaml's `changes` job requests `actions: read` (it looks up
    # the previous PR run via `gh api`) in addition to `contents: read`.
    # release.yml's top-level `permissions:` is `contents: read` only, so the
    # two jobs that call pull_request.yaml via `uses:` must each grant
    # `actions: read` at the job level, or GitHub refuses to start the called
    # workflow.
    jobs = release_notes.parse_workflow_jobs(_read_release_workflow())

    for caller in ("ci", "post-release-compatibility"):
        assert jobs[caller].permissions == {"contents": "read", "actions": "read"}, caller


def _write_project(path: Path, version: str = "0.1.0") -> Path:
    project = path / "pyproject.toml"
    project.write_text(
        "[project]\nname = \"gigai\"\nversion = \"" + version + "\"\n",
        encoding="utf-8",
    )
    return project


def _metadata(name: str = "gigai", version: str = "0.1.0") -> bytes:
    message = EmailMessage()
    message["Metadata-Version"] = "2.3"
    message["Name"] = name
    message["Version"] = version
    return message.as_bytes()


def _write_lock(path: Path, version: str = "0.1.0") -> Path:
    lock = path / "uv.lock"
    lock.write_text(
        "[[package]]\n"
        'name = "gigai"\n'
        f'version = "{version}"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    return lock


def _write_artifacts(
    directory: Path,
    *,
    wheel_name: str = "gigai-0.1.0-py3-none-any.whl",
    sdist_name: str = "gigai-0.1.0.tar.gz",
    metadata_name: str = "gigai",
    metadata_version: str = "0.1.0",
) -> release_check.ReleaseArtifacts:
    directory.mkdir()
    wheel = directory / wheel_name
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("gigai-0.1.0.dist-info/METADATA", _metadata(metadata_name, metadata_version))
    sdist = directory / sdist_name
    payload = _metadata(metadata_name, metadata_version)
    info = tarfile.TarInfo("gigai-0.1.0/PKG-INFO")
    info.size = len(payload)
    with tarfile.open(sdist, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(payload))
        egg_info = tarfile.TarInfo("gigai-0.1.0/src/gigai.egg-info/PKG-INFO")
        egg_info.size = len(payload)
        archive.addfile(egg_info, io.BytesIO(payload))
    return release_check.ReleaseArtifacts(wheel=wheel, sdist=sdist)


def test_project_metadata_and_exact_release_tag(tmp_path: Path) -> None:
    name, version = release_check.project_metadata(_write_project(tmp_path))
    assert (name, version) == ("gigai", "0.1.0")
    release_check.assert_release_tag(version, "v0.1.0")
    with pytest.raises(release_check.ReleaseCheckError, match="does not match"):
        release_check.assert_release_tag(version, "v0.1.1")


def test_lockfile_project_version_must_match_static_project_version(tmp_path: Path) -> None:
    release_check.verify_lockfile_project(_write_lock(tmp_path), "gigai", "0.1.0")
    with pytest.raises(release_check.ReleaseCheckError, match="project version"):
        release_check.verify_lockfile_project(_write_lock(tmp_path, "0.1.1"), "gigai", "0.1.0")


def test_release_artifacts_and_metadata_match_project(tmp_path: Path) -> None:
    artifacts = _write_artifacts(tmp_path / "dist")
    found = release_check.release_artifacts(tmp_path / "dist", "gigai", "0.1.0")
    assert found == artifacts
    release_check.verify_artifacts(found, "gigai", "0.1.0")


def test_release_artifacts_reject_ambiguous_wheels(tmp_path: Path) -> None:
    _write_artifacts(tmp_path / "dist")
    (tmp_path / "dist" / "gigai-0.1.0-py2.py3-none-any.whl").write_bytes(b"duplicate")
    with pytest.raises(release_check.ReleaseCheckError, match="exactly one wheel"):
        release_check.release_artifacts(tmp_path / "dist", "gigai", "0.1.0")


def test_release_artifacts_reject_metadata_version_drift(tmp_path: Path) -> None:
    artifacts = _write_artifacts(tmp_path / "dist", metadata_version="0.1.1")
    with pytest.raises(release_check.ReleaseCheckError, match="metadata version"):
        release_check.verify_artifacts(artifacts, "gigai", "0.1.0")


def test_checksum_manifest_is_stable_and_contains_only_release_artifacts(tmp_path: Path) -> None:
    artifacts = _write_artifacts(tmp_path / "dist")
    checksums = release_check.write_checksums(tmp_path / "dist", artifacts)
    expected = "".join(
        f"{release_check.sha256(artifact)}  {artifact.name}\n"
        for artifact in sorted((artifacts.wheel, artifacts.sdist), key=lambda path: path.name)
    )
    assert checksums.read_text(encoding="utf-8") == expected
    release_check.verify_checksums(tmp_path / "dist", artifacts)


def test_checksum_manifest_rejects_tampering(tmp_path: Path) -> None:
    artifacts = _write_artifacts(tmp_path / "dist")
    checksums = release_check.write_checksums(tmp_path / "dist", artifacts)
    checksums.write_text("0" * 64 + "  gigai-0.1.0-py3-none-any.whl\n", encoding="utf-8")

    with pytest.raises(release_check.ReleaseCheckError, match="does not match"):
        release_check.verify_checksums(tmp_path / "dist", artifacts)
