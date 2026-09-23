from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from gigai.canonical import (
    canonical_json_bytes,
    canonical_json_digest,
    digest_imported_bytes,
)
from gigai.catalog import package_bytes
from gigai.package import PackageError, export_package, inspect_package, install_package
from gigai.scout.template import scout_catalog_candidate
from tests.behaviors.installed_release.test_g41_package_boundary import _setup
from tests.behaviors.scout_assessment.test_g22_proposal_interview_contract import _record as proposal_interview_record
from tests.behaviors.scout_proposals_tools.test_scout04_external_recording import external_schema_fixtures


_PROJECT = "project_00000000-0000-4000-8000-000000000001"
_GIG = "gig_00000000-0000-4000-8000-000000000001"
_RECORD = "record_00000000-0000-4000-8000-000000000001"
_REVISION = "revision_00000000-0000-4000-8000-000000000001"
_REF = "ref_00000000-0000-4000-8000-000000000001"
_INPUT = "input_00000000-0000-4000-8000-000000000001"
_SHA = "sha256:" + "0" * 64


def _artifact(path: str) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": _SHA,
        "media_type": "application/json",
        "size_bytes": 1,
    }


def _reference_record() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "reference_id": _REF,
        "project_id": _PROJECT,
        "state": "sealed",
        "kind": "resume",
        "privacy_class": "private_sensitive",
        "label": "candidate resume",
        "origin": "local_file",
        "media_type": "text/markdown",
        "size_bytes": 1,
        "content_sha256": _SHA,
        "snapshot": _artifact(f"references/{_REF}/source.txt"),
        "created_at": "2026-09-09T00:00:00Z",
        "created_by": {"kind": "operator", "id": "local-user"},
    }


def _native_content() -> dict[str, object]:
    fact = {
        "state": "unknown",
        "value": None,
        "context": None,
        "provenance": {"kind": "user_reported", "source_refs": []},
        "conflict_refs": [],
    }
    return {
        "schema_version": "1.0",
        "kind": "profile_preferences",
        "scope": {"mode": "saved_default", "task_context_id": None, "base": None},
        "payload": {
            "hard_constraints": {
                "geography": deepcopy(fact),
                "work_mode": deepcopy(fact),
                "seniority": deepcopy(fact),
                "employment_type": deepcopy(fact),
                "compensation": deepcopy(fact),
            },
            "soft_priorities": {},
            "sponsorship_need": deepcopy(fact),
            "employer_sponsorship": deepcopy(fact),
            "eligibility": deepcopy(fact),
        },
    }


def _private_revision() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "record_id": _RECORD,
        "revision_id": _REVISION,
        "parent_revision": None,
        "project_id": _PROJECT,
        "gig_id": _GIG,
        "kind": "imported_reference",
        "privacy_class": "private_sensitive",
        "origin": "imported",
        "actor": {"kind": "operator", "id": "local-user"},
        "content": {
            "family": "g45_reference",
            "reference_id": _REF,
            "record_ref": _artifact(f"references/{_REF}/reference.json"),
            "snapshot_ref": _artifact(f"references/{_REF}/source.txt"),
        },
        "relationships": [],
        "created_at": "2026-09-09T00:00:00Z",
        "state": "active",
    }


def _operation_receipt() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "operation_id": "operation_00000000-0000-4000-8000-000000000001",
        "project_id": _PROJECT,
        "gig_id": _GIG,
        "operation": "record_create",
        "operation_key": "package-privacy-fixture",
        "payload_sha256": _SHA,
        "outcome": "committed",
        "artifact_refs": [_artifact(f"records/{_RECORD}/revisions/{_REVISION}.json")],
        "created_at": "2026-09-09T00:00:00Z",
    }


def _write_package(root: Path, extra: dict[str, bytes] | None = None) -> Path:
    files = package_bytes(scout_catalog_candidate())
    manifest = files.pop("package.json")
    payload = dict(json.loads(manifest))
    files.update(extra or {})
    inventory = [
        {
            "path": path,
            "content_sha256": digest_imported_bytes(data),
            "size_bytes": len(data),
        }
        for path, data in sorted(files.items())
    ]
    payload["files"] = inventory
    payload["content_digest"] = canonical_json_digest(inventory)
    root.mkdir(parents=True)
    for relative, data in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (root / "package.json").write_bytes(canonical_json_bytes(payload))
    return root


def test_actual_scout_source_package_round_trips_without_private_provenance(
    tmp_path: Path,
) -> None:
    source = _write_package(tmp_path / scout_catalog_candidate().package_id)
    inspection = inspect_package(source)
    exported = export_package(
        source_package=source, destination=tmp_path / "export" / inspection.package_id
    )
    assert exported.status == "exported"
    assert (
        inspect_package(exported.destination).content_digest
        == inspection.content_digest
    )


def test_inert_template_docs_and_supporting_python_remain_portable(
    tmp_path: Path,
) -> None:
    root = _write_package(
        tmp_path / scout_catalog_candidate().package_id,
        {
            "docs/template-notes.md": b"# Template notes\n\nNo user data.\n",
            "support/normalize.py": b"def normalize(value):\n    return value.strip()\n",
        },
    )
    assert inspect_package(root).package_id == scout_catalog_candidate().package_id


@pytest.mark.parametrize(
    ("relative", "payload"),
    [
        ("docs/renamed-reference.json", _reference_record()),
        ("definition/renamed-revision.json", _private_revision()),
        ("ui/renamed-native-content.json", _native_content()),
        ("support/renamed-operation-receipt.json", _operation_receipt()),
        (
            "assets/renamed-external-receipt.json",
            external_schema_fixtures()["external-recording-receipt.schema.json"],
        ),
    ],
)
def test_renamed_typed_private_provenance_refuses_before_export(
    tmp_path: Path, relative: str, payload: dict[str, object]
) -> None:
    source = _write_package(
        tmp_path / scout_catalog_candidate().package_id,
        {relative: canonical_json_bytes(payload)},
    )
    destination = tmp_path / "export" / source.name
    with pytest.raises(PackageError) as inspected:
        inspect_package(source)
    assert inspected.value.code == "private_provenance_refused"
    with pytest.raises(PackageError) as exported:
        export_package(source_package=source, destination=destination)
    assert exported.value.code == "private_provenance_refused"
    assert not destination.exists()


@pytest.mark.parametrize(
    "relative",
    [
        f"references/{_REF}/source.txt",
        f"run-inputs/{_INPUT}/source.txt",
        f"docs/{_RECORD}/{_REVISION}/draft.md",
        "indexes/context.json",
        "reports/scout/current.json",
    ],
)
def test_canonical_private_paths_refuse_untyped_private_bytes(
    tmp_path: Path, relative: str
) -> None:
    source = _write_package(
        tmp_path / scout_catalog_candidate().package_id,
        {relative: b"synthetic private payload\n"},
    )
    destination = tmp_path / "export" / source.name
    with pytest.raises(PackageError) as refusal:
        export_package(source_package=source, destination=destination)
    assert refusal.value.code == "private_provenance_refused"
    assert not destination.exists()


def test_private_package_refuses_before_install_destination(tmp_path: Path) -> None:
    source = _write_package(
        tmp_path / "source" / scout_catalog_candidate().package_id,
        {"docs/renamed-reference.json": canonical_json_bytes(_reference_record())},
    )
    home, target = _setup(tmp_path / "destination")
    with pytest.raises(PackageError) as refusal:
        install_package(
            home_root=home,
            requested_target=target,
            source_package=source,
        )
    assert refusal.value.code == "private_provenance_refused"
    assert not (target / ".gigai" / "packages" / source.name).exists()


@pytest.mark.parametrize(
    "relative",
    [
        "manifests/proposal-interview.json",
        "manifests/gig-proposal.json",
        "manifests/gig-builder-session.json",
        "manifests/active-gig-version.json",
        "manifests/proposal-draft-manifest.json",
        "manifests/improvement-manifest.json",
        "manifests/gig-discovery-manifest.json",
    ],
)
def test_private_manifests_refuse_actual_inspect_export_and_install(
    tmp_path: Path, relative: str
) -> None:
    private_manifest = canonical_json_bytes(proposal_interview_record())
    source = _write_package(
        tmp_path / "source" / scout_catalog_candidate().package_id,
        {relative: private_manifest},
    )
    export_destination = tmp_path / "export" / source.name
    home, target = _setup(tmp_path / "install")
    package_manifest = json.loads((source / "package.json").read_bytes())
    assert package_manifest["content_digest"] == canonical_json_digest(
        package_manifest["files"]
    )
    entry = next(item for item in package_manifest["files"] if item["path"] == relative)
    assert entry["content_sha256"] == digest_imported_bytes(private_manifest)
    assert entry["size_bytes"] == len(private_manifest)

    with pytest.raises(PackageError) as inspected:
        inspect_package(source)
    assert inspected.value.code == "private_provenance_refused"

    with pytest.raises(PackageError) as exported:
        export_package(source_package=source, destination=export_destination)
    assert exported.value.code == "private_provenance_refused"
    assert not export_destination.exists()

    with pytest.raises(PackageError) as installed:
        install_package(
            home_root=home,
            requested_target=target,
            source_package=source,
        )
    assert installed.value.code == "private_provenance_refused"
    assert not (target / ".gigai" / "packages" / source.name).exists()
