"""Journal publication evidence for the bounded Scout capability authority."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from gigai.capability_review import review_local_tool
from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes, parse_json_front_matter
from gigai.journal import JournalArtifact, JournalConflictError, JournalTransition, read_committed_artifact, run_with_journal_writer
from gigai.scout_bundled_tools import SCOUT_CRUD_MANIFEST_ID

from tests.test_scout05_bundled_tools import _candidate
from tests.test_scout05_capability_review import _fixture, _review_kwargs


def _workpad_ids(workpad: Path) -> tuple[str, str]:
    layout = parse_json_bytes((workpad / "manifests/workpad-layout.json").read_bytes())
    assert isinstance(layout, dict)
    return str(layout["project_id"]), str(layout["gig_id"])


def _publication(
    workpad: Path, path: str, *, expected_publishers: int = 1
) -> tuple[bytes, str, dict[str, object]]:
    project_id, gig_id = _workpad_ids(workpad)
    payload, publisher = read_committed_artifact(
        workpad=workpad,
        project_id=project_id,
        gig_id=gig_id,
        path=path,
        allow_replaced_manifests=True,
    )
    names = subprocess.check_output(
        ["git", "-C", str(workpad), "show", "--format=", "--name-only", publisher],
        text=True,
    ).splitlines()
    handoffs = [name for name in names if name.startswith("handoffs/") and name.endswith(".txt")]
    assert len(handoffs) == 1
    metadata, _body = parse_json_front_matter(
        subprocess.check_output(
            ["git", "-C", str(workpad), "show", f"{publisher}:{handoffs[0]}"],
        )
    )
    refs = metadata.get("artifact_refs")
    assert isinstance(refs, list)
    matching = [item for item in refs if isinstance(item, dict) and item.get("path") == path]
    assert len(matching) == 1
    reference = matching[0]
    assert reference["content_sha256"] == digest_imported_bytes(payload)
    assert reference["size_bytes"] == len(payload)
    assert (workpad / path).read_bytes() == payload
    assert len(
        subprocess.check_output(
            ["git", "-C", str(workpad), "log", "--format=%H", "--", path],
            text=True,
        ).splitlines()
    ) == expected_publishers
    return payload, publisher, metadata


def _publish(
    workpad: Path,
    project_id: str,
    gig_id: str,
    path: str,
    payload: bytes,
    *,
    transition: str,
) -> None:
    reference = {
        "path": path,
        "content_sha256": digest_imported_bytes(payload),
        "media_type": "application/json",
        "size_bytes": len(payload),
    }

    def operation(writer: object) -> None:
        writer.record(  # type: ignore[attr-defined]
            JournalTransition(
                f"handoff_{uuid.uuid4()}",
                transition,
                "Synthetic journal publication for bounded Scout authority evidence.",
                (JournalArtifact(path, payload),),
                {"project_id": project_id, "gig_id": gig_id, "artifact_refs": [reference]},
            ),
            allow_artifact_replacement=True,
        )

    run_with_journal_writer(
        workpad=workpad,
        project_id=project_id,
        gig_id=gig_id,
        operation=operation,
    )


def test_initial_materialization_has_one_authenticated_publisher_and_exact_bytes(
    tmp_path: Path,
) -> None:
    _home, _target, instance, workpad = _candidate(tmp_path)
    project_id, gig_id = _workpad_ids(workpad)
    assert gig_id == instance.gig_id
    path = f"manifests/capabilities/{SCOUT_CRUD_MANIFEST_ID}.json"
    payload, publisher, metadata = _publication(workpad, path)
    assert metadata["transition"] == "scout_source_materialized"
    assert metadata["gig_id"] == gig_id
    manifest = parse_json_bytes(payload)
    assert isinstance(manifest, dict)
    assert manifest["manifest_id"] == SCOUT_CRUD_MANIFEST_ID
    assert manifest["gig_id"] == gig_id
    assert publisher == subprocess.check_output(
        ["git", "-C", str(workpad), "log", "-1", "--format=%H", "--", path],
        text=True,
    ).strip()


def test_reviewed_successor_has_authenticated_review_transition_and_exact_bytes(
    tmp_path: Path,
) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="manifest-publication-evidence")
    result = review_local_tool(**kwargs)
    assert result.reviewed_manifest_ref is not None
    reviewed_path = str(result.reviewed_manifest_ref["path"])
    payload, _publisher, metadata = _publication(workpad, reviewed_path)
    assert metadata["transition"] == "capability_review_decided"
    manifest = parse_json_bytes(payload)
    assert isinstance(manifest, dict)
    assert manifest["manifest_id"] == Path(reviewed_path).stem
    assert manifest["manifest_id"] != SCOUT_CRUD_MANIFEST_ID
    assert manifest["gig_id"] == instance.gig_id


def test_manifest_replacement_accepts_review_transition(
    tmp_path: Path,
) -> None:
    _home, _target, _instance, workpad = _candidate(tmp_path)
    project_id, gig_id = _workpad_ids(workpad)
    path = f"manifests/capabilities/{SCOUT_CRUD_MANIFEST_ID}.json"
    original = parse_json_bytes((workpad / path).read_bytes())
    assert isinstance(original, dict)
    replacement = dict(original)
    replacement["manifest_version"] = int(original["manifest_version"]) + 1
    _publish(
        workpad,
        project_id,
        gig_id,
        path,
        canonical_json_bytes(replacement),
        transition="capability_review_decided",
    )
    payload, _publisher, metadata = _publication(workpad, path, expected_publishers=2)
    assert metadata["transition"] == "capability_review_decided"
    assert payload == canonical_json_bytes(replacement)


def test_manifest_replacement_requires_the_review_transition(
    tmp_path: Path,
) -> None:
    _home, _target, instance, workpad = _candidate(tmp_path)
    project_id, gig_id = _workpad_ids(workpad)
    path = f"manifests/capabilities/{SCOUT_CRUD_MANIFEST_ID}.json"
    original = parse_json_bytes((workpad / path).read_bytes())
    assert isinstance(original, dict)
    replacement = dict(original)
    replacement["manifest_version"] = int(original["manifest_version"]) + 1
    payload = canonical_json_bytes(replacement)
    _publish(workpad, project_id, gig_id, path, payload, transition="scout_source_materialized")
    with pytest.raises(JournalConflictError, match="publishing transition"):
        read_committed_artifact(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            path=path,
            allow_replaced_manifests=True,
        )
    assert instance.gig_id == gig_id


def test_arbitrary_nested_manifest_replacement_is_not_mutable(
    tmp_path: Path,
) -> None:
    _home, _target, _instance, workpad = _candidate(tmp_path)
    project_id, gig_id = _workpad_ids(workpad)
    path = next(workpad.glob("manifests/software/*/source-inventory.json")).relative_to(workpad).as_posix()
    original = parse_json_bytes((workpad / path).read_bytes())
    assert isinstance(original, dict)
    replacement = dict(original)
    replacement["compiler_version"] = str(original["compiler_version"]) + "-replacement"
    _publish(workpad, project_id, gig_id, path, canonical_json_bytes(replacement), transition="scout_source_materialized")
    with pytest.raises(JournalConflictError, match="multiple publishers"):
        read_committed_artifact(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            path=path,
            allow_replaced_manifests=True,
        )


@pytest.mark.parametrize("mutation", ["manifest_id", "gig_id", "schema"])
def test_capability_manifest_identity_and_schema_are_redeemed(
    tmp_path: Path, mutation: str
) -> None:
    _home, _target, _instance, workpad = _candidate(tmp_path)
    project_id, gig_id = _workpad_ids(workpad)
    original_path = f"manifests/capabilities/{SCOUT_CRUD_MANIFEST_ID}.json"
    original = parse_json_bytes((workpad / original_path).read_bytes())
    assert isinstance(original, dict)
    path = f"manifests/capabilities/capmanifest_00000000-0000-4000-8000-00000000009{ {'manifest_id': '3', 'gig_id': '4', 'schema': '5'}[mutation] }.json"
    payload_value = dict(original)
    if mutation == "manifest_id":
        pass
    elif mutation == "gig_id":
        payload_value["gig_id"] = "gig_00000000-0000-4000-8000-000000000099"
    else:
        payload_value["schema_version"] = "9.0"
    _publish(workpad, project_id, gig_id, path, canonical_json_bytes(payload_value), transition="scout_source_materialized")
    with pytest.raises(JournalConflictError, match="owner or schema"):
        read_committed_artifact(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            path=path,
            allow_replaced_manifests=True,
        )
