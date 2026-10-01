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


def test_pypi_publish_job_receives_only_distributions() -> None:
    workflow = _read_release_workflow()

    assert workflow.count("name: Prepare package-only publisher input") == 1
    assert workflow.count("cp dist/*.whl dist/*.tar.gz publish/") == 1
    assert workflow.count("packages-dir: publish/") == 1
    assert "packages-dir: dist/" not in workflow
    # TestPyPI is not on the release path: the pre-check installs the built files.
    assert "test.pypi.org" not in workflow and "testpypi" not in workflow.lower()


def test_release_job_graph_is_the_fast_path_then_the_post_release_checks() -> None:
    graph = release_notes.parse_workflow_job_needs(_read_release_workflow())

    assert graph == {
        "preflight": [],
        "tag": ["preflight"],
        "publish-pypi": ["preflight", "tag"],
        "github-release": ["preflight", "publish-pypi"],
        "docs": ["preflight", "github-release"],
        "advance-main": ["preflight", "github-release"],
        "verify-pypi": ["preflight", "github-release"],
        "post-release-compatibility": ["preflight", "github-release"],
    }
    # The post-release checks are leaves: nothing waits on them.
    for check in ("verify-pypi", "post-release-compatibility", "advance-main", "docs"):
        assert all(check not in needs for needs in graph.values()), check


def test_post_release_checks_never_fail_or_hold_the_release_run() -> None:
    workflow = _read_release_workflow()
    jobs = release_notes.parse_workflow_jobs(workflow)

    verify = workflow.split("\n  verify-pypi:\n", 1)[1].split("\n  post-release-compatibility:\n", 1)[0]
    assert "    continue-on-error: true\n" in verify
    assert 'uv tool install --refresh "gigai==${VERSION}"' in verify

    # The sweep is started as its own run on the tag (compatibility_job.yaml), not
    # called with `uses:`, so this run does not wait an hour for macOS or take its result.
    sweep = workflow.split("\n  post-release-compatibility:\n", 1)[1]
    assert "    continue-on-error: true\n" in sweep
    assert 'gh workflow run compatibility_job.yaml --repo "${GITHUB_REPOSITORY}" --ref "${TAG}"' in sweep
    assert "uses:" not in sweep
    assert jobs["post-release-compatibility"].permissions == {"actions": "write"}
    compatibility = Path(__file__).resolve().parents[3] / ".github/workflows/compatibility_job.yaml"
    assert "workflow_dispatch:" in compatibility.read_text(encoding="utf-8")


def test_release_jobs_permissions() -> None:
    workflow = _read_release_workflow()
    jobs = release_notes.parse_workflow_jobs(workflow)

    assert "\npermissions:\n  contents: read\n" in workflow
    # No job calls pull_request.yaml: the release runs no CI.
    assert "pull_request.yaml" not in "".join(
        line for line in workflow.splitlines(keepends=True) if "uses:" in line
    )
    # docs.yml's publish job pushes the gh-pages branch.
    assert jobs["docs"].permissions == {"contents": "write"}
    assert jobs["publish-pypi"].permissions == {"id-token": "write"}
    assert jobs["advance-main"].permissions == {"contents": "write"}
    assert jobs["verify-pypi"].permissions == {}


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


def test_four_part_hotfix_version_is_a_valid_release_tag(tmp_path: Path) -> None:
    name, version = release_check.project_metadata(_write_project(tmp_path, "0.1.8.1"))
    assert (name, version) == ("gigai", "0.1.8.1")
    release_check.assert_release_tag(version, "v0.1.8.1")
    with pytest.raises(release_check.ReleaseCheckError, match="does not match"):
        release_check.assert_release_tag(version, "v0.1.8")


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


def test_release_artifacts_must_not_ship_the_docs_site(tmp_path: Path) -> None:
    artifacts = _write_artifacts(tmp_path / "dist")
    release_check.verify_not_shipped(artifacts)

    payload = _metadata()
    with tarfile.open(artifacts.sdist, "w:gz") as archive:
        for name in ("gigai-0.1.0/PKG-INFO", "gigai-0.1.0/gigai-docs/src/content/docs/index.md"):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(release_check.ReleaseCheckError, match="repo-only files"):
        release_check.verify_not_shipped(artifacts)


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


def test_no_release_job_can_be_skipped() -> None:
    """Every release job runs on the implicit success() of its needs.

    Run 36665265416 reported success and published nothing: a job behind a
    skippable job was skipped with it. The release now has no skippable job (no
    dry run, no tag-push path, no optional CI), so no job carries an `if:` and a
    green run means every job ran.
    """

    workflow = _read_release_workflow()
    job_level_ifs = [line for line in workflow.splitlines() if line.startswith("    if:")]
    assert job_level_ifs == []
    assert "cancelled()" not in workflow and "always()" not in workflow


def test_every_post_tag_job_uses_the_tag_and_commit_from_preflight() -> None:
    workflow = _read_release_workflow()
    assert workflow.count("ref: ${{ needs.preflight.outputs.ref }}") == 2  # github-release, docs
    assert workflow.count("COMMIT: ${{ needs.preflight.outputs.commit }}") == 2  # manifest, advance-main
    assert 'echo "ref=refs/tags/v${version}" >> "${GITHUB_OUTPUT}"' in workflow
    assert 'echo "commit=${GITHUB_SHA}" >> "${GITHUB_OUTPUT}"' in workflow
    # the published files come from the tag job's upload, which is the pre-check's download
    assert workflow.count("name: release-dist\n") == 3  # tag uploads; publish-pypi, github-release download
