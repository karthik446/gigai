from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess
import uuid

import pytest

from gigai.capability_review import (
    CapabilityReviewError,
    _actor,
    _validate_requested_boundary,
    review_local_tool,
)
import gigai.capability_review as capability_review_module
from gigai.capabilities import validate_capability_manifest
from gigai.canonical import (
    canonical_json_bytes,
    canonical_json_digest,
    digest_imported_bytes,
    parse_json_bytes,
)
from gigai.lifecycle import approve_offline
from gigai.journal import JournalArtifact, JournalTransition, run_with_journal_writer
from tests.test_scout05_bundled_tools import (
    _candidate,
    _prepared_manifest,
)
from gigai.scout_bundled_tools import SCOUT_CRUD_CAPABILITY_ID, SCOUT_CRUD_MANIFEST_ID


_REVIEWER = {"kind": "agent", "id": "local-reviewer"}
_OPERATOR = {"kind": "operator", "id": "local-user"}


@pytest.mark.parametrize(
    "actor",
    [
        {"kind": "agent"},
        {"kind": "agent", "model_target": None},
        {"id": "local-reviewer", "model_target": None},
    ],
)
def test_actor_refuses_missing_required_members_without_key_error(actor: dict[str, object]) -> None:
    with pytest.raises(CapabilityReviewError) as refused:
        _actor(actor)
    assert refused.value.code == "capability_review_actor_invalid"


def _fixture(tmp_path: Path) -> tuple[Path, Path, object, Path]:
    home, target, instance, workpad = _candidate(tmp_path)
    assert instance.proposal_id is not None
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=instance.proposal_id,
        gig_id=instance.gig_id,
    )
    return home, target, instance, workpad


def _review_kwargs(
    instance: object,
    workpad: Path,
    *,
    operation_key: str = "cap-review-pass",
    manifest_id: str = SCOUT_CRUD_MANIFEST_ID,
) -> dict[str, object]:
    parent_path = workpad / "manifests/capabilities" / f"{manifest_id}.json"
    parent_bytes = parent_path.read_bytes()
    manifest = parse_json_bytes(parent_bytes)
    assert isinstance(manifest, dict)
    layout = parse_json_bytes((workpad / "manifests/workpad-layout.json").read_bytes())
    assert isinstance(layout, dict)
    from gigai.capability_review import _ref

    return {
        "workpad": workpad,
        "project_id": layout["project_id"],
        "gig_id": instance.gig_id,
        "base_version": 1,
        "base_proposal_id": instance.proposal_id,
        "manifest_id": manifest["manifest_id"],
        "capability_id": SCOUT_CRUD_CAPABILITY_ID,
        "reviewer": _REVIEWER,
        "reviewer_outcome": "passed",
        "reviewer_rationale": "The pinned local native-record source is inert during review and has the admitted boundary.",
        "evidence_refs": ["inventory/source-digest", "review/native-record-boundary"],
        "operator_actor": _OPERATOR,
        "operator_confirmed": True,
        "operation_key": operation_key,
        "parent_manifest_ref": _ref("manifests/capabilities/" + f"{manifest_id}.json", parent_bytes),
    }


def _manifest_snapshot(workpad: Path) -> dict[str, bytes]:
    root = workpad / "manifests/capabilities"
    return {
        path.relative_to(workpad).as_posix(): path.read_bytes()
        for path in sorted(root.glob("*.json"))
    }


def _non_git_workpad_snapshot(workpad: Path) -> dict[str, bytes | None | str]:
    """Capture semantic workpad entries without transient Git internals."""

    snapshot: dict[str, bytes | None | str] = {}
    for path in sorted(workpad.rglob("*")):
        relative = path.relative_to(workpad)
        if ".git" in relative.parts:
            continue
        key = relative.as_posix()
        if path.is_symlink():
            snapshot[key] = "symlink:" + path.readlink().as_posix()
        elif path.is_dir():
            snapshot[key] = None
        elif path.is_file():
            snapshot[key] = path.read_bytes()
    return snapshot


def _publish_pending_manifest_copy(workpad: Path, project_id: str, gig_id: str) -> str:
    parent_path = workpad / "manifests/capabilities" / f"{SCOUT_CRUD_MANIFEST_ID}.json"
    parent = parse_json_bytes(parent_path.read_bytes())
    assert isinstance(parent, dict)
    manifest_id = "capmanifest_00000000-0000-4000-8000-000000000098"
    parent["manifest_id"] = manifest_id
    path = f"manifests/capabilities/{manifest_id}.json"
    payload = canonical_json_bytes(parent)
    artifact_ref = {
        "path": path,
        "content_sha256": digest_imported_bytes(payload),
        "size_bytes": len(payload),
    }

    def publish(writer: object) -> None:
        writer.record(  # type: ignore[attr-defined]
            JournalTransition(
                f"handoff_{uuid.UUID('00000000-0000-4000-8000-000000000098')}",
                "scout_source_materialized",
                "Published a disposable second pending capability manifest for regression coverage.",
                (JournalArtifact(path, payload),),
                {"project_id": project_id, "gig_id": gig_id, "artifact_refs": [artifact_ref]},
            )
        )

    run_with_journal_writer(
        workpad=workpad,
        project_id=project_id,
        gig_id=gig_id,
        operation=publish,
    )
    return manifest_id


def test_pass_publishes_new_reviewed_manifest_and_decision_without_active_mutation(tmp_path: Path) -> None:
    home, target, instance, workpad = _fixture(tmp_path)
    parent_before = (workpad / "manifests/capabilities" / f"{SCOUT_CRUD_MANIFEST_ID}.json").read_bytes()
    result = review_local_tool(**_review_kwargs(instance, workpad))
    assert result.replayed is False
    assert result.reviewed_manifest_ref is not None
    reviewed_path = workpad / str(result.reviewed_manifest_ref["path"])
    reviewed = parse_json_bytes(reviewed_path.read_bytes())
    assert isinstance(reviewed, dict)
    assert reviewed["manifest_id"] != SCOUT_CRUD_MANIFEST_ID
    assert reviewed["gig_id"] == instance.gig_id
    capability = reviewed["capabilities"][0]
    assert capability["availability_state"] == "available"
    assert capability["compatibility"]["status"] == "compatible"
    assert capability["security_review"]["status"] == "passed"
    assert capability["options"][0]["decision"] == "pending"
    assert validate_capability_manifest(reviewed_path.read_bytes()).valid
    assert (workpad / "manifests/capabilities" / f"{SCOUT_CRUD_MANIFEST_ID}.json").read_bytes() == parent_before
    pointer = parse_json_bytes((workpad / "manifests/active-gig-version.json").read_bytes())
    assert isinstance(pointer, dict)
    assert "capability_manifest" not in pointer
    assert result.decision_ref["path"].startswith("manifests/capability-reviews/")
    assert result.decision_ref["path"] == "manifests/capability-reviews/" + reviewed_path.name
    assert result.decision["reviewed_manifest_ref"] == result.reviewed_manifest_ref


def test_pass_replay_authenticates_committed_reviewed_manifest_reference(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="cap-review-ref-integrity")
    first = review_local_tool(**kwargs)
    assert first.reviewed_manifest_ref is not None
    reviewed_path = workpad / str(first.reviewed_manifest_ref["path"])
    reviewed_path.unlink()
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**kwargs)
    assert refused.value.code == "capability_review_authority_unavailable"
    assert len(list((workpad / "manifests/capability-reviews").glob("*.json"))) == 1
    # The committed decision remains, but the replay cannot treat a missing
    # reviewed manifest as an authenticated success.
    subprocess_result = subprocess.run(
        ["git", "-C", str(workpad), "show", "HEAD:" + str(first.decision_ref["path"])],
        check=True,
        capture_output=True,
    )
    assert subprocess_result.stdout


def test_pass_accepts_verified_optional_parent_canonical_digest(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="cap-review-parent-canonical")
    parent = dict(kwargs["parent_manifest_ref"])
    parent_path = workpad / str(parent["path"])
    parent["canonical_sha256"] = canonical_json_digest(parse_json_bytes(parent_path.read_bytes()))
    kwargs["parent_manifest_ref"] = parent
    result = review_local_tool(**kwargs)
    assert result.reviewed_manifest_ref is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("canonical_sha256", "sha256:" + "0" * 64),
        ("unknown", "refuse"),
    ],
)
def test_parent_reference_rejects_unverified_or_unknown_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key=f"cap-review-parent-{field}")
    parent = dict(kwargs["parent_manifest_ref"])
    parent[field] = value
    kwargs["parent_manifest_ref"] = parent
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**kwargs)
    assert refused.value.code == "capability_review_parent_ref_mismatch"
    assert not (workpad / "manifests/capability-reviews").exists()


def test_replay_refuses_divergent_reviewed_manifest_without_new_publication(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="cap-review-ref-divergent")
    first = review_local_tool(**kwargs)
    assert first.reviewed_manifest_ref is not None
    reviewed_path = workpad / str(first.reviewed_manifest_ref["path"])
    reviewed_path.write_bytes(reviewed_path.read_bytes() + b"\n")
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**kwargs)
    assert refused.value.code == "capability_review_authority_unavailable"
    assert len(list((workpad / "manifests/capability-reviews").glob("*.json"))) == 1


def test_replay_refuses_foreign_reviewed_manifest_reference_without_new_publication(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="cap-review-ref-foreign")
    first = review_local_tool(**kwargs)
    decision_path = workpad / str(first.decision_ref["path"])
    decision = parse_json_bytes(decision_path.read_bytes())
    assert isinstance(decision, dict)
    reviewed_ref = decision["reviewed_manifest_ref"]
    assert isinstance(reviewed_ref, dict)
    reviewed_ref["path"] = "manifests/capabilities/capmanifest_00000000-0000-4000-8000-000000000099.json"
    decision_path.write_bytes(canonical_json_bytes(decision))
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**kwargs)
    assert refused.value.code == "capability_review_authority_unavailable"
    assert len(list((workpad / "manifests/capability-reviews").glob("*.json"))) == 1


def test_rejected_review_records_decision_without_fabricating_reviewed_manifest(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="cap-review-reject")
    kwargs["reviewer_outcome"] = "rejected"
    kwargs["reviewer_rationale"] = "The reviewer rejected this source for follow-up inspection."
    kwargs["evidence_refs"] = ["review/rejected"]
    before_manifests = _manifest_snapshot(workpad)
    result = review_local_tool(**kwargs)
    assert result.reviewed_manifest_ref is None
    assert result.decision["reviewer_outcome"] == "rejected"
    assert result.decision["reviewed_manifest_ref"] is None
    assert len(list((workpad / "manifests/capability-reviews").glob("*.json"))) == 1
    assert _manifest_snapshot(workpad) == before_manifests


def test_rejected_review_can_be_followed_by_new_passed_review(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    rejected = _review_kwargs(instance, workpad, operation_key="cap-review-rejected-first")
    rejected["reviewer_outcome"] = "rejected"
    rejected["reviewer_rationale"] = "The reviewer rejected this source for follow-up inspection."
    rejected["evidence_refs"] = ["review/rejected"]
    first = review_local_tool(**rejected)
    assert first.reviewed_manifest_ref is None
    passed = _review_kwargs(instance, workpad, operation_key="cap-review-passed-after-reject")
    second = review_local_tool(**passed)
    assert second.replayed is False
    assert second.reviewed_manifest_ref is not None
    assert len(list((workpad / "manifests/capability-reviews").glob("*.json"))) == 2


def test_rejected_then_passed_replays_original_rejection_by_operation_key(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    rejected = _review_kwargs(instance, workpad, operation_key="cap-review-rejected-replay")
    rejected["reviewer_outcome"] = "rejected"
    rejected["reviewer_rationale"] = "The reviewer rejected this source for follow-up inspection."
    rejected["evidence_refs"] = ["review/rejected"]
    first = review_local_tool(**rejected)
    passed = review_local_tool(
        **_review_kwargs(instance, workpad, operation_key="cap-review-passed-after-replay-reject")
    )
    assert passed.reviewed_manifest_ref is not None
    replay = review_local_tool(**rejected)
    assert replay.replayed is True
    assert replay.decision == first.decision
    assert replay.reviewed_manifest_ref is None
    assert len(list((workpad / "manifests/capability-reviews").glob("*.json"))) == 2


def test_different_pending_parent_can_be_reviewed_without_false_conflict(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    layout = parse_json_bytes((workpad / "manifests/workpad-layout.json").read_bytes())
    assert isinstance(layout, dict)
    second_manifest_id = _publish_pending_manifest_copy(
        workpad,
        str(layout["project_id"]),
        instance.gig_id,
    )
    first = review_local_tool(
        **_review_kwargs(instance, workpad, operation_key="cap-review-first-pending-parent")
    )
    second = review_local_tool(
        **_review_kwargs(
            instance,
            workpad,
            operation_key="cap-review-second-pending-parent",
            manifest_id=second_manifest_id,
        )
    )
    assert first.reviewed_manifest_ref is not None
    assert second.reviewed_manifest_ref is not None
    assert first.reviewed_manifest_ref["path"] != second.reviewed_manifest_ref["path"]


def test_same_pending_parent_different_pass_key_refuses_without_publication(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    first = review_local_tool(**_review_kwargs(instance, workpad, operation_key="cap-review-parent-first"))
    assert first.reviewed_manifest_ref is not None
    before_manifests = _manifest_snapshot(workpad)
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**_review_kwargs(instance, workpad, operation_key="cap-review-parent-second"))
    assert refused.value.code == "capability_review_conflict"
    assert _manifest_snapshot(workpad) == before_manifests
    assert len(list((workpad / "manifests/capability-reviews").glob("*.json"))) == 1


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("operator_confirmed", False, "capability_review_operator_consent_required"),
        ("effects", ["write_target"], "capability_review_effect_refused"),
        ("permissions", {"filesystem": "write_isolated", "network": "read", "credentials": "none"}, "capability_review_effect_refused"),
        ("evidence_refs", [], "capability_review_evidence_required"),
    ],
)
def test_review_refuses_before_writing_without_confirmation_or_boundary(
    tmp_path: Path, field: str, value: object, code: str
) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key=f"cap-review-{field}")
    kwargs[field] = value
    before = _non_git_workpad_snapshot(workpad)
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**kwargs)
    assert refused.value.code == code
    after = _non_git_workpad_snapshot(workpad)
    assert after == before
    assert not (workpad / "manifests/capability-reviews").exists()


def test_operator_consent_refusal_is_preflight_and_preserves_committed_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A denied request must not enter the writer or alter semantic workpad state."""

    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="cap-review-preflight-only")
    kwargs["operator_confirmed"] = False
    authority_paths = (
        "manifests/active-gig-version.json",
        "manifests/gig-proposal.json",
        f"manifests/capabilities/{SCOUT_CRUD_MANIFEST_ID}.json",
        "manifests/template-instance-binding.json",
    )
    before = _non_git_workpad_snapshot(workpad)
    authority_before = {
        path: subprocess.run(
            ["git", "-C", str(workpad), "show", "HEAD:" + path],
            check=True,
            capture_output=True,
        ).stdout
        for path in authority_paths
    }

    def writer_must_not_run(**_kwargs: object) -> object:
        pytest.fail("denied capability review reached the journal writer")

    monkeypatch.setattr(capability_review_module, "run_with_journal_writer", writer_must_not_run)
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**kwargs)

    assert refused.value.code == "capability_review_operator_consent_required"
    assert _non_git_workpad_snapshot(workpad) == before
    assert not (workpad / "manifests/capability-reviews").exists()
    for path, expected in authority_before.items():
        assert subprocess.run(
            ["git", "-C", str(workpad), "show", "HEAD:" + path],
            check=True,
            capture_output=True,
        ).stdout == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [("network_requirement", "read"), ("credential_requirements", ["secret"])],
)
def test_network_or_credentials_use_effect_refused_diagnostic(
    tmp_path: Path, field: str, value: object
) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    capability = deepcopy(_prepared_manifest(workpad)["capabilities"][0])
    assert isinstance(capability, dict)
    capability[field] = value
    with pytest.raises(CapabilityReviewError) as refused:
        _validate_requested_boundary(
            capability,
            capability_id=SCOUT_CRUD_CAPABILITY_ID,
            effects=["write_workpad"],
            permissions={"filesystem": "write_isolated", "network": "none", "credentials": "none"},
            selected_option_id="A",
        )
    assert refused.value.code == "capability_review_effect_refused"


def test_replay_returns_exact_decision_and_conflicting_payload_is_refused(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="cap-review-replay")
    first = review_local_tool(**kwargs)
    replay = review_local_tool(**kwargs)
    assert replay.replayed is True
    assert replay.decision == first.decision
    assert replay.decision_ref == first.decision_ref
    assert replay.reviewed_manifest_ref == first.reviewed_manifest_ref
    conflict = dict(kwargs)
    conflict["reviewer_rationale"] = "Changed after the first review."
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**conflict)
    assert refused.value.code == "capability_review_conflict"
    assert len(list((workpad / "manifests/capability-reviews").glob("*.json"))) == 1


def test_source_change_inside_writer_lock_refuses_without_publication(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="cap-review-source-race")
    source = workpad / "tools" / SCOUT_CRUD_CAPABILITY_ID / "record_tool.py"
    original = source.read_bytes()
    before_manifests = _manifest_snapshot(workpad)

    def mutate_source() -> None:
        source.write_bytes(original + b"\n# changed during review\n")

    kwargs["_before_publication"] = mutate_source
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**kwargs)
    assert refused.value.code == "capability_review_source_changed"
    assert not (workpad / "manifests/capability-reviews").exists()
    assert _manifest_snapshot(workpad) == before_manifests


def test_current_base_change_inside_writer_lock_refuses_without_publication(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="cap-review-version-race")
    pointer_path = workpad / "manifests/active-gig-version.json"
    original = pointer_path.read_bytes()

    def mutate_pointer() -> None:
        pointer = parse_json_bytes(original)
        assert isinstance(pointer, dict)
        pointer["active_version"] = 2
        pointer_path.write_bytes(canonical_json_bytes(pointer))
        subprocess.run(
            ["git", "-C", str(workpad), "add", "manifests/active-gig-version.json"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(workpad), "commit", "--quiet", "-m", "advance disposable pointer"],
            check=True,
            capture_output=True,
        )

    kwargs["_before_publication"] = mutate_pointer
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**kwargs)
    assert refused.value.code == "capability_review_current_version_conflict"
    assert not (workpad / "manifests/capability-reviews").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gig_id", "gig_00000000-0000-4000-8000-000000000099"),
        ("project_id", "project_00000000-0000-4000-8000-000000000099"),
    ],
)
def test_foreign_gig_or_project_refuses_at_journal_boundary(
    tmp_path: Path, field: str, value: str
) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="cap-review-foreign")
    kwargs[field] = value
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**kwargs)
    assert refused.value.code == "capability_review_authority_unavailable"
    assert not (workpad / "manifests/capability-reviews").exists()


def test_malformed_foreign_pointer_refuses_before_service_publication(tmp_path: Path) -> None:
    _home, _target, instance, workpad = _fixture(tmp_path)
    kwargs = _review_kwargs(instance, workpad, operation_key="cap-review-pointer-foreign")
    pointer_path = workpad / "manifests/active-gig-version.json"
    pointer = parse_json_bytes(pointer_path.read_bytes())
    assert isinstance(pointer, dict)
    pointer["gig_id"] = "gig_00000000-0000-4000-8000-000000000099"
    pointer_path.write_bytes(canonical_json_bytes(pointer))
    with pytest.raises(CapabilityReviewError) as refused:
        review_local_tool(**kwargs)
    assert refused.value.code == "capability_review_pointer_invalid"
    assert not (workpad / "manifests/capability-reviews").exists()
