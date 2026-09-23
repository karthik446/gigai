"""Prepare and authenticate a reviewed capability successor.

Preparation creates an ordinary v2 amendment proposal and an immutable
sidecar that carries the reviewed capability authority.  Approval remains a
separate lifecycle action; this module never selects a Gig, changes a pointer,
executes source, or publishes the reviewed manifest a second time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import uuid

from .canonical import canonical_json_bytes, digest_imported_bytes
from .capabilities import CapabilityManifestError, capability_manifest_artifact_ref, validate_capability_manifest
from .capability_review import (
    CapabilityReviewError,
    _authenticated_reviewed_manifest,
    _committed_file,
    _parse,
    _ref,
    _validate_artifact_ref,
    _validate_base_proposal,
    _validate_pointer,
    _validate_review_decision,
)
from .journal import (
    JournalArtifact,
    JournalArtifactMissingError,
    JournalConflictError,
    JournalTransition,
    JournalSnapshot,
    _git_bytes,
    read_committed_artifact,
    run_with_journal_writer,
)
from .scout.tools import ScoutToolError, _inventory
from .validators import validate_serialized_contract


_SCHEMA = "capability-successor-binding.schema.json"
_TRANSITION = "capability_successor_prepared"
_PROPOSAL_ID = re.compile(
    r"^gp_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CAPABILITY_ID = re.compile(
    r"^cap_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MANIFEST_ID = re.compile(
    r"^capmanifest_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ALLOWED_REF_FIELDS = frozenset(
    {"path", "content_sha256", "canonical_sha256", "media_type", "size_bytes"}
)
_REQUIRED_REF_FIELDS = frozenset(
    {"path", "content_sha256", "media_type", "size_bytes"}
)
_ALLOWED_OPERATIONS = frozenset({"record_archive", "record_create", "record_update"})
_ALLOWED_EFFECTS = ["write_workpad"]
_ALLOWED_PERMISSIONS = {
    "filesystem": "write_isolated",
    "network": "none",
    "credentials": "none",
}
_STABLE_REVIEWED_CAPABILITY_FIELDS = (
    "capability_id",
    "goal_ids",
    "kind",
    "name",
    "requested_version",
    "source_constraints",
    "declared_effects",
    "permissions",
    "credential_requirements",
    "network_requirement",
    "tool_binding",
)


class CapabilitySuccessorError(ValueError):
    """Typed, redacted refusal from successor preparation/approval checks."""

    code = "capability_successor_refused"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass(frozen=True)
class CapabilitySuccessorResult:
    proposal: dict[str, object]
    proposal_ref: dict[str, object]
    binding: dict[str, object]
    binding_ref: dict[str, object]
    decision_ref: dict[str, object]
    reviewed_manifest_ref: dict[str, object]
    replayed: bool


@dataclass(frozen=True)
class CapabilitySuccessorApproval:
    proposal: dict[str, object]
    binding: dict[str, object]
    binding_ref: dict[str, object]
    reviewed_manifest_ref: dict[str, object]
    capability_id: str


def _schema(payload: bytes) -> None:
    report = validate_serialized_contract(_SCHEMA, payload)
    if any(item.code == "unknown_schema" for item in report.findings):
        raise CapabilitySuccessorError(
            "capability successor schema is not registered",
            code="capability_successor_schema_unregistered",
        )
    if not report.valid:
        raise CapabilitySuccessorError(
            "capability successor binding is invalid",
            code="capability_successor_binding_invalid",
        )


def _id(value: object, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CapabilitySuccessorError("capability successor identity is invalid", code=code)
    return value


def _operation_key(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", value) is None:
        raise CapabilitySuccessorError("capability successor operation key is invalid", code="capability_successor_operation_invalid")
    return value


def _snapshot_ref(snapshot: JournalSnapshot, reference: object, *, prefix: str, code: str) -> bytes:
    if not isinstance(reference, Mapping):
        raise CapabilitySuccessorError("capability successor artifact reference is invalid", code=code)
    path = reference.get("path")
    if (
        not isinstance(path, str)
        or not path.startswith(prefix)
        or path.startswith("/")
        or "\\" in path
        or ".." in Path(path).parts
    ):
        raise CapabilitySuccessorError("capability successor artifact reference is invalid", code=code)
    payload = snapshot.artifacts.get(path)
    if not isinstance(payload, bytes):
        raise CapabilitySuccessorError("capability successor artifact is unavailable", code=code)
    expected = _ref(path, payload)
    try:
        _validate_artifact_ref(reference, expected, payload, code=code)
    except ValueError as exc:
        if isinstance(exc, CapabilitySuccessorError):
            raise
        raise CapabilitySuccessorError("capability successor artifact is not authenticated", code=code) from exc
    return payload


def _review_check(check: Callable[[], object], *, code: str) -> object:
    """Translate the review module's typed refusals at this service boundary."""

    try:
        return check()
    except CapabilityReviewError as exc:
        raise CapabilitySuccessorError(str(exc), code=code) from exc


def _source_binding(capability: Mapping[str, object], capability_id: str) -> dict[str, object]:
    binding = capability.get("tool_binding")
    if not isinstance(binding, Mapping):
        raise CapabilitySuccessorError("reviewed capability binding is invalid", code="capability_successor_source_invalid")
    inventory = binding.get("inventory")
    operations = binding.get("operations")
    effects = binding.get("effects")
    permissions = capability.get("permissions")
    entry_path = binding.get("entry_path")
    wrapper_ref = binding.get("wrapper_ref")
    inventory_sha256 = binding.get("inventory_sha256")
    if (
        not isinstance(inventory, list)
        or not isinstance(operations, list)
        or not isinstance(effects, list)
        or not isinstance(permissions, Mapping)
        or not isinstance(entry_path, str)
        or not isinstance(inventory_sha256, str)
        or (wrapper_ref is not None and not isinstance(wrapper_ref, Mapping))
    ):
        raise CapabilitySuccessorError("reviewed capability binding is invalid", code="capability_successor_source_invalid")
    if (
        effects != _ALLOWED_EFFECTS
        or dict(permissions) != _ALLOWED_PERMISSIONS
        or not operations
        or operations != sorted(set(operations))
        or any(item not in _ALLOWED_OPERATIONS for item in operations)
    ):
        raise CapabilitySuccessorError("reviewed capability effects are unsupported", code="capability_successor_effect_refused")
    if capability.get("capability_id") != capability_id:
        raise CapabilitySuccessorError("reviewed capability identity is invalid", code="capability_successor_source_invalid")
    normalized_inventory = [dict(item) for item in inventory if isinstance(item, Mapping)]
    if len(normalized_inventory) != len(inventory):
        raise CapabilitySuccessorError("reviewed capability inventory is invalid", code="capability_successor_source_invalid")
    return {
        "capability_id": capability_id,
        "entry_path": entry_path,
        "wrapper_ref": dict(wrapper_ref) if isinstance(wrapper_ref, Mapping) else None,
        "inventory": normalized_inventory,
        "inventory_sha256": inventory_sha256,
        "operations": list(operations),
        "effects": list(effects),
        "permissions": dict(permissions),
    }


def _review_authority(
    *,
    workpad: Path,
    snapshot: JournalSnapshot,
    project_id: str,
    gig_id: str,
    base_version: int,
    base_proposal_id: str,
    capability_id: str,
    decision_ref: Mapping[str, object],
    reviewed_manifest_ref: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    reviewed_path = reviewed_manifest_ref.get("path")
    if not isinstance(reviewed_path, str) or not reviewed_path.endswith(".json"):
        raise CapabilitySuccessorError("reviewed manifest reference is invalid", code="capability_successor_review_invalid")
    reviewed_manifest_id = Path(reviewed_path).stem
    _id(reviewed_manifest_id, _MANIFEST_ID, "capability_successor_review_invalid")
    expected_decision_path = f"manifests/capability-reviews/{reviewed_manifest_id}.json"
    if decision_ref.get("path") != expected_decision_path:
        raise CapabilitySuccessorError("review decision linkage is invalid", code="capability_successor_review_invalid")
    decision_bytes = _snapshot_ref(
        snapshot,
        decision_ref,
        prefix="manifests/capability-reviews/",
        code="capability_successor_review_invalid",
    )
    _review_check(
        lambda: _validate_review_decision(decision_bytes),
        code="capability_successor_review_invalid",
    )
    decision = _parse(decision_bytes, code="capability_successor_review_invalid")
    if (
        decision.get("project_id") != project_id
        or decision.get("gig_id") != gig_id
        or decision.get("base_version") != base_version
        or decision.get("base_proposal_id") != base_proposal_id
        or decision.get("capability_id") != capability_id
        or decision.get("reviewer_outcome") != "passed"
    ):
        raise CapabilitySuccessorError("review decision does not match the approved base", code="capability_successor_review_invalid")
    _snapshot_ref(
        snapshot,
        reviewed_manifest_ref,
        prefix="manifests/capabilities/",
        code="capability_successor_review_invalid",
    )
    reviewed = _review_check(
        lambda: _authenticated_reviewed_manifest(snapshot, reviewed_manifest_ref, gig_id=gig_id),
        code="capability_successor_review_invalid",
    )
    assert isinstance(reviewed, dict)
    if decision.get("reviewed_manifest_ref") != dict(reviewed_manifest_ref):
        raise CapabilitySuccessorError("reviewed manifest reference differs from decision", code="capability_successor_review_invalid")
    parent_ref = decision.get("parent_manifest_ref")
    if not isinstance(parent_ref, Mapping):
        raise CapabilitySuccessorError("review decision parent reference is invalid", code="capability_successor_review_invalid")
    parent_bytes = _snapshot_ref(
        snapshot,
        parent_ref,
        prefix="manifests/capabilities/",
        code="capability_successor_review_invalid",
    )
    if parent_ref != decision.get("parent_manifest_ref"):
        raise CapabilitySuccessorError("review decision parent reference is invalid", code="capability_successor_review_invalid")
    parent = _parse(parent_bytes, code="capability_successor_parent_invalid")
    if (
        parent.get("manifest_id") == reviewed.get("manifest_id")
        or parent.get("gig_id") != gig_id
        or parent.get("manifest_id") is None
        or not validate_capability_manifest(parent_bytes).valid
    ):
        raise CapabilitySuccessorError("pending parent manifest is invalid", code="capability_successor_parent_invalid")
    if decision.get("parent_manifest_ref") != parent_ref:
        raise CapabilitySuccessorError("review decision parent reference is invalid", code="capability_successor_review_invalid")
    capabilities = reviewed.get("capabilities")
    if not isinstance(capabilities, list):
        raise CapabilitySuccessorError("reviewed capability manifest is invalid", code="capability_successor_review_invalid")
    capability = next(
        (item for item in capabilities if isinstance(item, Mapping) and item.get("capability_id") == capability_id),
        None,
    )
    if not isinstance(capability, Mapping):
        raise CapabilitySuccessorError("reviewed capability is absent", code="capability_successor_review_invalid")
    if capability.get("availability_state") != "available" or capability.get("security_review", {}).get("status") != "passed":
        raise CapabilitySuccessorError("reviewed capability is not passed and available", code="capability_successor_review_invalid")
    source = _source_binding(capability, capability_id)
    try:
        actual_inventory = _inventory(workpad, capability_id, capability["tool_binding"])
    except (ScoutToolError, KeyError, TypeError) as exc:
        raise CapabilitySuccessorError("reviewed capability source is unavailable", code="capability_successor_source_changed") from exc
    if actual_inventory != source["inventory"] or digest_imported_bytes(canonical_json_bytes(actual_inventory)) != source["inventory_sha256"]:
        raise CapabilitySuccessorError("reviewed capability source changed", code="capability_successor_source_changed")
    decision_source = decision.get("source_binding")
    if not isinstance(decision_source, Mapping) or dict(decision_source) != {
        "inventory_sha256": source["inventory_sha256"],
        "operations": source["operations"],
        "effects": source["effects"],
        "permissions": source["permissions"],
    }:
        raise CapabilitySuccessorError("review decision source binding differs", code="capability_successor_review_invalid")
    operator = decision.get("operator_consent")
    if not isinstance(operator, Mapping) or operator.get("confirmed") is not True or operator.get("effects") != _ALLOWED_EFFECTS or operator.get("permissions") != _ALLOWED_PERMISSIONS:
        raise CapabilitySuccessorError("review decision lacks explicit effect consent", code="capability_successor_effect_refused")
    return dict(decision), dict(reviewed), dict(parent), source


def _input_payload(
    *,
    project_id: str,
    gig_id: str,
    base_version: int,
    base_proposal_id: str,
    capability_id: str,
    operation_key: str,
    decision_ref: Mapping[str, object],
    reviewed_manifest_ref: Mapping[str, object],
    parent_manifest_ref: Mapping[str, object],
    source_binding: Mapping[str, object],
) -> dict[str, object]:
    return {
        "project_id": project_id,
        "gig_id": gig_id,
        "base_version": base_version,
        "base_proposal_id": base_proposal_id,
        "capability_id": capability_id,
        "operation_key": operation_key,
        "decision_ref": dict(decision_ref),
        "reviewed_manifest_ref": dict(reviewed_manifest_ref),
        "parent_manifest_ref": dict(parent_manifest_ref),
        "source_binding": dict(source_binding),
    }


def _proposal_ref(path: str, payload: bytes) -> dict[str, object]:
    return _ref(path, payload)


def _preflight_successor_artifacts(
    workpad: Path,
    artifacts: tuple[JournalArtifact, ...],
    *,
    expected_current_proposal: bytes,
) -> None:
    """Allow replacement only for the mutable current proposal destination."""

    current_path = workpad / "manifests/gig-proposal.json"
    if (
        current_path.is_symlink()
        or not current_path.is_file()
        or current_path.read_bytes() != expected_current_proposal
    ):
        raise CapabilitySuccessorError(
            "current proposal changed before successor publication",
            code="capability_successor_current_conflict",
        )
    for artifact in artifacts:
        if artifact.path == "manifests/gig-proposal.json":
            continue
        destination = workpad / artifact.path
        if destination.exists() or destination.is_symlink():
            raise CapabilitySuccessorError(
                "immutable successor artifact already exists",
                code="capability_successor_publication_conflict",
            )


def _current_base(
    *, workpad: Path, project_id: str, gig_id: str, base_version: int, base_proposal_id: str
) -> tuple[Mapping[str, object], Mapping[str, object], bytes, bytes]:
    try:
        pointer_bytes = _committed_file(
            workpad,
            "manifests/active-gig-version.json",
            code="capability_successor_current_conflict",
        )
    except CapabilityReviewError as exc:
        raise CapabilitySuccessorError(
            "approved base authority is unavailable",
            code="capability_successor_current_conflict",
        ) from exc
    pointer = _review_check(
        lambda: _validate_pointer(pointer_bytes),
        code="capability_successor_current_conflict",
    )
    assert isinstance(pointer, Mapping)
    if (
        pointer.get("gig_id") != gig_id
        or pointer.get("active_version") != base_version
        or pointer.get("approved_proposal_id") != base_proposal_id
        or pointer.get("capability_manifest") is not None
    ):
        raise CapabilitySuccessorError("approved base authority does not match", code="capability_successor_current_conflict")
    try:
        proposal_bytes = _committed_file(
            workpad,
            "manifests/gig-proposal.json",
            code="capability_successor_current_conflict",
        )
    except CapabilityReviewError as exc:
        raise CapabilitySuccessorError(
            "approved base proposal is unavailable",
            code="capability_successor_current_conflict",
        ) from exc
    proposal = _parse(proposal_bytes, code="capability_successor_base_invalid")
    if not validate_serialized_contract("gig-proposal-v2.schema.json", proposal_bytes).valid:
        raise CapabilitySuccessorError("approved base proposal is not v2", code="capability_successor_base_invalid")
    if proposal.get("proposal_id") == base_proposal_id:
        _review_check(
            lambda: _validate_base_proposal(proposal_bytes, base_proposal_id),
            code="capability_successor_base_invalid",
        )
    elif not (
        proposal.get("status") == "proposed"
        and proposal.get("parent_proposal_id") == base_proposal_id
    ):
        raise CapabilitySuccessorError("approved base proposal is unavailable", code="capability_successor_base_invalid")
    if proposal.get("gig_id") != gig_id or proposal.get("project_id") != project_id:
        raise CapabilitySuccessorError("approved base proposal ownership differs", code="capability_successor_current_conflict")
    return pointer, proposal, pointer_bytes, proposal_bytes


def _sidecar_result(
    *, snapshot: JournalSnapshot, path: str, payload: bytes, expected_input: str,
    workpad: Path, project_id: str, gig_id: str, base_version: int, base_proposal_id: str,
    capability_id: str, operation_key: str, decision_ref: Mapping[str, object],
    reviewed_manifest_ref: Mapping[str, object],
) -> CapabilitySuccessorResult:
    _schema(payload)
    binding = _parse(payload, code="capability_successor_binding_invalid")
    if (
        binding.get("operation_key") != operation_key
        or binding.get("input_sha256") != expected_input
        or binding.get("project_id") != project_id
        or binding.get("gig_id") != gig_id
        or binding.get("base_gig_version") != base_version
        or binding.get("base_proposal_id") != base_proposal_id
        or binding.get("parent_proposal_id") != base_proposal_id
    ):
        raise CapabilitySuccessorError("capability successor operation key conflicts", code="capability_successor_conflict")
    if binding.get("decision_ref") != dict(decision_ref) or binding.get("reviewed_manifest_ref") != dict(reviewed_manifest_ref):
        raise CapabilitySuccessorError("capability successor request conflicts", code="capability_successor_conflict")
    pending_ref = binding.get("pending_proposal_ref")
    pending_bytes = _snapshot_ref(snapshot, pending_ref, prefix="manifests/capability-successors/", code="capability_successor_authority_unavailable")
    pending = _parse(pending_bytes, code="capability_successor_authority_unavailable")
    if pending.get("proposal_id") != binding.get("proposal_id") or pending.get("status") != "proposed":
        raise CapabilitySuccessorError("pending successor proposal is invalid", code="capability_successor_authority_unavailable")
    try:
        pointer_bytes = _committed_file(
            workpad,
            "manifests/active-gig-version.json",
            code="capability_successor_current_conflict",
        )
    except CapabilityReviewError as exc:
        raise CapabilitySuccessorError(
            "successor base authority is unavailable",
            code="capability_successor_current_conflict",
        ) from exc
    pointer = _review_check(
        lambda: _validate_pointer(pointer_bytes),
        code="capability_successor_current_conflict",
    )
    assert isinstance(pointer, Mapping)
    expected_pointer = binding.get("base_pointer_ref")
    _review_check(
        lambda: _validate_artifact_ref(
            expected_pointer,
            _ref("manifests/active-gig-version.json", pointer_bytes),
            pointer_bytes,
            code="capability_successor_current_conflict",
        ),
        code="capability_successor_current_conflict",
    )
    if pointer.get("gig_id") != gig_id or pointer.get("active_version") != base_version or pointer.get("approved_proposal_id") != base_proposal_id or pointer.get("capability_manifest") is not None:
        raise CapabilitySuccessorError("approved base authority changed", code="capability_successor_current_conflict")
    decision, reviewed, _parent, source = _review_authority(
        workpad=workpad,
        snapshot=snapshot,
        project_id=project_id,
        gig_id=gig_id,
        base_version=base_version,
        base_proposal_id=base_proposal_id,
        capability_id=capability_id,
        decision_ref=decision_ref,
        reviewed_manifest_ref=reviewed_manifest_ref,
    )
    if binding.get("source_binding") != source:
        raise CapabilitySuccessorError("successor source binding changed", code="capability_successor_source_changed")
    try:
        current_bytes = _committed_file(
            workpad,
            "manifests/gig-proposal.json",
            code="capability_successor_authority_unavailable",
        )
    except CapabilityReviewError as exc:
        raise CapabilitySuccessorError(
            "current successor proposal is unavailable",
            code="capability_successor_authority_unavailable",
        ) from exc
    current = _parse(current_bytes, code="capability_successor_authority_unavailable")
    if current != pending:
        raise CapabilitySuccessorError("pending successor proposal changed", code="capability_successor_authority_unavailable")
    proposal_ref = _proposal_ref(str(pending_ref["path"]), pending_bytes)
    binding_ref = _ref(path, payload)
    return CapabilitySuccessorResult(dict(pending), proposal_ref, dict(binding), binding_ref, dict(decision_ref), dict(reviewed_manifest_ref), True)


def prepare_capability_successor(
    *,
    workpad: Path,
    project_id: str,
    gig_id: str,
    base_version: int,
    base_proposal_id: str,
    capability_id: str,
    decision_ref: Mapping[str, object],
    reviewed_manifest_ref: Mapping[str, object],
    operation_key: str,
    proposal_id: str | None = None,
    now: str | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> CapabilitySuccessorResult:
    """Prepare one reviewed capability successor without approving it."""

    if type(base_version) is not int or base_version < 1:
        raise CapabilitySuccessorError("base version is invalid", code="capability_successor_base_invalid")
    _id(base_proposal_id, _PROPOSAL_ID, "capability_successor_base_invalid")
    _id(capability_id, _CAPABILITY_ID, "capability_successor_source_invalid")
    _operation_key(operation_key)
    if proposal_id is not None:
        _id(proposal_id, _PROPOSAL_ID, "capability_successor_identity_invalid")
    timestamp = now or datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def operation(writer: object) -> CapabilitySuccessorResult:
        snapshot = writer.snapshot(("manifests/capabilities/", "manifests/capability-reviews/", "manifests/capability-successors/"))  # type: ignore[attr-defined]
        # First authenticate the review and derive the complete source binding.
        decision, reviewed, _parent, source = _review_authority(
            workpad=workpad,
            snapshot=snapshot,
            project_id=project_id,
            gig_id=gig_id,
            base_version=base_version,
            base_proposal_id=base_proposal_id,
            capability_id=capability_id,
            decision_ref=decision_ref,
            reviewed_manifest_ref=reviewed_manifest_ref,
        )
        parent_ref = decision["parent_manifest_ref"]
        assert isinstance(parent_ref, Mapping)
        request = _input_payload(
            project_id=project_id,
            gig_id=gig_id,
            base_version=base_version,
            base_proposal_id=base_proposal_id,
            capability_id=capability_id,
            operation_key=operation_key,
            decision_ref=decision_ref,
            reviewed_manifest_ref=reviewed_manifest_ref,
            parent_manifest_ref=parent_ref,
            source_binding=source,
        )
        input_sha = digest_imported_bytes(canonical_json_bytes(request))
        sidecars = []
        for path, payload in sorted(snapshot.artifacts.items()):
            if not path.startswith("manifests/capability-successors/") or not path.endswith(".json") or "/" in path.removeprefix("manifests/capability-successors/"):
                continue
            sidecars.append((path, payload, _parse(payload, code="capability_successor_binding_invalid")))
        key_matches = [item for item in sidecars if item[2].get("operation_key") == operation_key]
        if len(key_matches) > 1:
            raise CapabilitySuccessorError("operation key has multiple successor bindings", code="capability_successor_conflict")
        if key_matches:
            path, payload, _binding = key_matches[0]
            return _sidecar_result(
                snapshot=snapshot,
                path=path,
                payload=payload,
                expected_input=input_sha,
                workpad=workpad,
                project_id=project_id,
                gig_id=gig_id,
                base_version=base_version,
                base_proposal_id=base_proposal_id,
                capability_id=capability_id,
                operation_key=operation_key,
                decision_ref=decision_ref,
                reviewed_manifest_ref=reviewed_manifest_ref,
            )
        pointer, base_proposal, pointer_bytes, base_proposal_bytes = _current_base(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            base_version=base_version,
            base_proposal_id=base_proposal_id,
        )
        if sidecars or base_proposal.get("status") != "approved":
            raise CapabilitySuccessorError("another pending successor already exists", code="capability_successor_pending_conflict")
        next_proposal_id = proposal_id or f"gp_{uuid_factory()}"
        _id(next_proposal_id, _PROPOSAL_ID, "capability_successor_identity_invalid")
        proposal = dict(base_proposal)
        proposal.update(
            {
                "proposal_id": next_proposal_id,
                "status": "proposed",
                "kind": "amend",
                "base_gig_version": base_version,
                "parent_proposal_id": base_proposal_id,
                "change_request": f"Attach reviewed local capability {capability_id} as a successor.",
                "created_at": timestamp,
                "created_by": {"kind": "gigai", "id": "capability-successor", "model_target": None},
            }
        )
        proposal_bytes = canonical_json_bytes(proposal)
        if not validate_serialized_contract("gig-proposal-v2.schema.json", proposal_bytes).valid:
            raise CapabilitySuccessorError("successor proposal failed strict validation", code="capability_successor_proposal_invalid")
        pending_path = f"manifests/capability-successors/{next_proposal_id}/proposal.json"
        pending_ref = _ref(pending_path, proposal_bytes)
        base_pointer_ref = _ref("manifests/active-gig-version.json", pointer_bytes)
        base_proposal_ref = _ref("manifests/gig-proposal.json", base_proposal_bytes)
        binding_value: dict[str, object] = {
            "schema_version": "1.0",
            "proposal_id": next_proposal_id,
            "project_id": project_id,
            "gig_id": gig_id,
            "operation_key": operation_key,
            "input_sha256": input_sha,
            "base_gig_version": base_version,
            "base_proposal_id": base_proposal_id,
            "parent_proposal_id": base_proposal_id,
            "base_pointer_ref": base_pointer_ref,
            "base_proposal_ref": base_proposal_ref,
            "pending_proposal_ref": pending_ref,
            "parent_manifest_ref": dict(parent_ref),
            "decision_ref": dict(decision_ref),
            "reviewed_manifest_ref": dict(reviewed_manifest_ref),
            "source_binding": source,
            "created_at": timestamp,
        }
        binding_bytes = canonical_json_bytes(binding_value)
        _schema(binding_bytes)
        sidecar_path = f"manifests/capability-successors/{next_proposal_id}.json"
        artifacts = (
            JournalArtifact("manifests/gig-proposal.json", proposal_bytes),
            JournalArtifact(pending_path, proposal_bytes),
            JournalArtifact(sidecar_path, binding_bytes),
        )
        _preflight_successor_artifacts(
            workpad,
            artifacts,
            expected_current_proposal=base_proposal_bytes,
        )
        refs = [
            {"path": item.path, "content_sha256": digest_imported_bytes(item.content), "media_type": "application/json", "size_bytes": len(item.content)}
            for item in artifacts
        ]
        try:
            writer.record(  # type: ignore[attr-defined]
                JournalTransition(
                    f"handoff_{uuid_factory()}",
                    _TRANSITION,
                    f"Prepared reviewed capability successor proposal {next_proposal_id}; direct approval remains required.",
                    artifacts,
                    {"project_id": project_id, "gig_id": gig_id, "artifact_refs": refs},
                ),
                # The explicit preflight above proves that only the mutable
                # current proposal may be replaced; immutable sidecars and
                # pending copies retain collision refusal.
                allow_artifact_replacement=True,
            )
        except JournalConflictError as exc:
            raise CapabilitySuccessorError(
                "successor publication encountered a journal conflict",
                code="capability_successor_publication_conflict",
            ) from exc
        return CapabilitySuccessorResult(
            proposal,
            _ref("manifests/gig-proposal.json", proposal_bytes),
            binding_value,
            _ref(sidecar_path, binding_bytes),
            dict(decision_ref),
            dict(reviewed_manifest_ref),
            False,
        )

    try:
        return run_with_journal_writer(workpad=workpad, project_id=project_id, gig_id=gig_id, operation=operation)
    except CapabilitySuccessorError:
        raise
    except JournalConflictError as exc:
        raise CapabilitySuccessorError("successor authority is unavailable", code="capability_successor_authority_unavailable") from exc


def _committed_binding_exists(
    *, workpad: Path, project_id: str, gig_id: str, proposal_id: str
) -> bool:
    """Determine successor authority from its immutable journal publication.

    A mutable sidecar's presence, absence, or contents are never this decision.
    The later `_committed_file` read still requires its working bytes to match
    the proven HEAD artifact before that authority can be used.
    """

    path = f"manifests/capability-successors/{proposal_id}.json"
    try:
        read_committed_artifact(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            path=path,
        )
    except JournalArtifactMissingError:
        return False
    except JournalConflictError as exc:
        raise CapabilitySuccessorError(
            "successor binding is unavailable",
            code="capability_successor_authority_unavailable",
        ) from exc
    return True


def reviewed_manifest_requires_successor(
    *, workpad: Path, project_id: str, gig_id: str, manifest_id: str
) -> bool:
    """Recognize only committed generic-review provenance for a manifest.

    An ordinary legacy manifest has no decision at the manifest-keyed review
    path and remains outside this guard.  A generic reviewed manifest carries
    both immutable artifacts; any missing, conflicting, or mismatched part is
    an authority refusal rather than permission to take the legacy branch.

    A fresh working manifest is not authority.  It can only trigger a refusal
    when its stable binding matches an authenticated generic review, or when it
    asserts that it was produced by the generic review service.  This catches a
    renamed reviewed copy without converting every historical ``passed``
    legacy manifest into a generic-review manifest.
    """

    _id(manifest_id, _MANIFEST_ID, "capability_successor_manifest_invalid")
    manifest_path = f"manifests/capabilities/{manifest_id}.json"
    decision_path = f"manifests/capability-reviews/{manifest_id}.json"
    try:
        manifest_bytes, _manifest_commit = read_committed_artifact(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            path=manifest_path,
        )
    except JournalArtifactMissingError:
        return _uncommitted_manifest_requires_successor(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            manifest_id=manifest_id,
        )
    except JournalConflictError as exc:
        raise CapabilitySuccessorError(
            "reviewed manifest authority is unavailable",
            code="capability_successor_authority_unavailable",
        ) from exc
    manifest = _review_check(
        lambda: _parse(
            manifest_bytes, code="capability_successor_authority_unavailable"
        ),
        code="capability_successor_authority_unavailable",
    )
    assert isinstance(manifest, Mapping)
    if manifest.get("manifest_id") != manifest_id or manifest.get("gig_id") != gig_id:
        raise CapabilitySuccessorError(
            "reviewed manifest authority differs from the requested Gig",
            code="capability_successor_authority_unavailable",
        )
    if not validate_capability_manifest(manifest_bytes).valid:
        raise CapabilitySuccessorError(
            "reviewed manifest authority is invalid",
            code="capability_successor_authority_unavailable",
        )
    try:
        decision_bytes, _decision_commit = read_committed_artifact(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            path=decision_path,
        )
    except JournalArtifactMissingError:
        creator = manifest.get("created_by")
        if (
            isinstance(creator, Mapping)
            and creator.get("kind") == "gigai"
            and creator.get("id") == "capability-review"
        ):
            raise CapabilitySuccessorError(
                "reviewed manifest decision authority is unavailable",
                code="capability_successor_authority_unavailable",
            )
        return False
    except JournalConflictError as exc:
        raise CapabilitySuccessorError(
            "reviewed manifest decision authority is unavailable",
            code="capability_successor_authority_unavailable",
        ) from exc
    _review_check(
        lambda: _validate_review_decision(decision_bytes),
        code="capability_successor_authority_unavailable",
    )
    decision = _review_check(
        lambda: _parse(
            decision_bytes, code="capability_successor_authority_unavailable"
        ),
        code="capability_successor_authority_unavailable",
    )
    assert isinstance(decision, Mapping)
    expected_ref = _ref(manifest_path, manifest_bytes)
    _review_check(
        lambda: _validate_artifact_ref(
            decision.get("reviewed_manifest_ref"),
            expected_ref,
            manifest_bytes,
            code="capability_successor_authority_unavailable",
        ),
        code="capability_successor_authority_unavailable",
    )
    if (
        decision.get("project_id") != project_id
        or decision.get("gig_id") != gig_id
        or decision.get("reviewer_outcome") != "passed"
    ):
        raise CapabilitySuccessorError(
            "reviewed manifest decision authority differs from the requested Gig",
            code="capability_successor_authority_unavailable",
        )
    return True


def _uncommitted_manifest_requires_successor(
    *, workpad: Path, project_id: str, gig_id: str, manifest_id: str
) -> bool:
    """Refuse a renamed generic-reviewed manifest without trusting its bytes.

    The selected working manifest is read only through the capability manifest
    regular-file guard.  Its projection is compared exclusively to committed
    passed review decisions and their exact reviewed manifest artifacts.  A
    match means that a caller must use that review's authenticated successor;
    no working value grants approval authority.
    """

    try:
        candidate_ref = capability_manifest_artifact_ref(
            workpad, manifest_id, gig_id=gig_id
        )
        candidate_path = candidate_ref["path"]
        if not isinstance(candidate_path, str):
            raise CapabilityManifestError("capability manifest path is invalid")
        candidate_bytes = (workpad / candidate_path).read_bytes()
    except (CapabilityManifestError, OSError) as exc:
        raise CapabilitySuccessorError(
            "selected capability manifest is unavailable",
            code="capability_successor_authority_unavailable",
        ) from exc
    if (
        digest_imported_bytes(candidate_bytes) != candidate_ref["content_sha256"]
        or len(candidate_bytes) != candidate_ref["size_bytes"]
    ):
        raise CapabilitySuccessorError(
            "selected capability manifest changed during approval",
            code="capability_successor_authority_unavailable",
        )
    candidate = _review_check(
        lambda: _parse(
            candidate_bytes, code="capability_successor_authority_unavailable"
        ),
        code="capability_successor_authority_unavailable",
    )
    assert isinstance(candidate, Mapping)
    if candidate.get("manifest_id") != manifest_id or candidate.get("gig_id") != gig_id:
        raise CapabilitySuccessorError(
            "selected capability manifest differs from the requested Gig",
            code="capability_successor_authority_unavailable",
        )
    candidate_schema = candidate.get("schema_version")
    candidate_capabilities = _stable_reviewed_capability_identities(candidate)
    for reviewed_schema, reviewed_capability in _committed_generic_reviewed_capabilities(
        workpad=workpad, project_id=project_id, gig_id=gig_id
    ):
        if (
            candidate_schema == reviewed_schema
            and reviewed_capability in candidate_capabilities
        ):
            return True
    if _claims_generic_review(candidate):
        raise CapabilitySuccessorError(
            "reviewed manifest decision authority is unavailable",
            code="capability_successor_authority_unavailable",
        )
    return False


def _claims_generic_review(manifest: Mapping[str, object]) -> bool:
    """Recognize only the explicit generic-service creator as a refusal clue."""

    creator = manifest.get("created_by")
    return (
        isinstance(creator, Mapping)
        and creator.get("kind") == "gigai"
        and creator.get("id") == "capability-review"
    )


def _stable_reviewed_capability_identities(manifest: Mapping[str, object]) -> tuple[bytes, ...]:
    """Project each capability independently for reviewed-binding containment."""

    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not all(
        isinstance(capability, Mapping) for capability in capabilities
    ):
        raise CapabilitySuccessorError(
            "reviewed manifest authority is invalid",
            code="capability_successor_authority_unavailable",
        )
    return tuple(
        _stable_reviewed_capability_identity(capability)
        for capability in capabilities
    )


def _stable_reviewed_capability_identity(capability: Mapping[str, object]) -> bytes:
    """Project source/effect authority, excluding presentation and review prose."""

    return canonical_json_bytes(
        {
            key: capability.get(key)
            for key in _STABLE_REVIEWED_CAPABILITY_FIELDS
        }
    )


def _committed_generic_reviewed_capabilities(
    *, workpad: Path, project_id: str, gig_id: str
) -> tuple[tuple[object, bytes], ...]:
    """Return only capability bindings authenticated by passed generic reviews."""

    try:
        names = _git_bytes(
            workpad,
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            "HEAD",
            "--",
            "manifests/capability-reviews/",
        ).split(b"\0")
    except JournalConflictError as exc:
        raise CapabilitySuccessorError(
            "reviewed manifest authority is unavailable",
            code="capability_successor_authority_unavailable",
        ) from exc
    reviewed: list[tuple[object, bytes]] = []
    for encoded_path in names:
        if not encoded_path:
            continue
        try:
            decision_path = encoded_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CapabilitySuccessorError(
                "reviewed manifest authority is unavailable",
                code="capability_successor_authority_unavailable",
            ) from exc
        parts = Path(decision_path).parts
        if (
            len(parts) != 3
            or parts[:2] != ("manifests", "capability-reviews")
            or not decision_path.endswith(".json")
        ):
            raise CapabilitySuccessorError(
                "reviewed manifest authority is unavailable",
                code="capability_successor_authority_unavailable",
            )
        reviewed_id = Path(decision_path).stem
        if _MANIFEST_ID.fullmatch(reviewed_id) is None:
            continue
        try:
            decision_bytes, _decision_commit = read_committed_artifact(
                workpad=workpad,
                project_id=project_id,
                gig_id=gig_id,
                path=decision_path,
            )
        except (JournalArtifactMissingError, JournalConflictError) as exc:
            raise CapabilitySuccessorError(
                "reviewed manifest authority is unavailable",
                code="capability_successor_authority_unavailable",
            ) from exc
        _review_check(
            lambda: _validate_review_decision(decision_bytes),
            code="capability_successor_authority_unavailable",
        )
        decision = _review_check(
            lambda: _parse(
                decision_bytes, code="capability_successor_authority_unavailable"
            ),
            code="capability_successor_authority_unavailable",
        )
        assert isinstance(decision, Mapping)
        if (
            decision.get("project_id") != project_id
            or decision.get("gig_id") != gig_id
            or decision.get("reviewer_outcome") != "passed"
        ):
            continue
        reference = decision.get("reviewed_manifest_ref")
        if not isinstance(reference, Mapping):
            raise CapabilitySuccessorError(
                "reviewed manifest decision authority is unavailable",
                code="capability_successor_authority_unavailable",
            )
        reference_path = reference.get("path")
        expected_path = f"manifests/capabilities/{reviewed_id}.json"
        if reference_path != expected_path:
            raise CapabilitySuccessorError(
                "reviewed manifest decision authority is unavailable",
                code="capability_successor_authority_unavailable",
            )
        try:
            manifest_bytes, _manifest_commit = read_committed_artifact(
                workpad=workpad,
                project_id=project_id,
                gig_id=gig_id,
                path=expected_path,
            )
        except (JournalArtifactMissingError, JournalConflictError) as exc:
            raise CapabilitySuccessorError(
                "reviewed manifest authority is unavailable",
                code="capability_successor_authority_unavailable",
            ) from exc
        expected_ref = _ref(expected_path, manifest_bytes)
        _review_check(
            lambda: _validate_artifact_ref(
                reference,
                expected_ref,
                manifest_bytes,
                code="capability_successor_authority_unavailable",
            ),
            code="capability_successor_authority_unavailable",
        )
        if not validate_capability_manifest(manifest_bytes).valid:
            raise CapabilitySuccessorError(
                "reviewed manifest authority is invalid",
                code="capability_successor_authority_unavailable",
            )
        manifest = _review_check(
            lambda: _parse(
                manifest_bytes, code="capability_successor_authority_unavailable"
            ),
            code="capability_successor_authority_unavailable",
        )
        assert isinstance(manifest, Mapping)
        if manifest.get("manifest_id") != reviewed_id or manifest.get("gig_id") != gig_id:
            raise CapabilitySuccessorError(
                "reviewed manifest authority differs from the requested Gig",
                code="capability_successor_authority_unavailable",
            )
        reviewed_capability = _validate_review_source_binding(manifest, decision)
        reviewed.append(
            (
                manifest.get("schema_version"),
                _stable_reviewed_capability_identity(reviewed_capability),
            )
        )
    return tuple(reviewed)


def _validate_review_source_binding(
    manifest: Mapping[str, object], decision: Mapping[str, object]
) -> Mapping[str, object]:
    """Require the review decision's pinned source facts to match its manifest."""

    capability_id = decision.get("capability_id")
    capabilities = manifest.get("capabilities")
    if not isinstance(capability_id, str) or not isinstance(capabilities, list):
        raise CapabilitySuccessorError(
            "reviewed manifest decision authority is unavailable",
            code="capability_successor_authority_unavailable",
        )
    matches = [
        capability
        for capability in capabilities
        if isinstance(capability, Mapping)
        and capability.get("capability_id") == capability_id
    ]
    if len(matches) != 1:
        raise CapabilitySuccessorError(
            "reviewed manifest decision authority is unavailable",
            code="capability_successor_authority_unavailable",
        )
    capability = matches[0]
    binding = capability.get("tool_binding")
    source_binding = decision.get("source_binding")
    if not isinstance(binding, Mapping) or not isinstance(source_binding, Mapping):
        raise CapabilitySuccessorError(
            "reviewed manifest decision authority is unavailable",
            code="capability_successor_authority_unavailable",
        )
    expected = {
        "inventory_sha256": binding.get("inventory_sha256"),
        "operations": binding.get("operations"),
        "effects": binding.get("effects"),
        "permissions": capability.get("permissions"),
    }
    if dict(source_binding) != expected:
        raise CapabilitySuccessorError(
            "reviewed manifest decision source authority is unavailable",
            code="capability_successor_authority_unavailable",
        )
    return capability


def successor_approval_context(
    *,
    workpad: Path,
    project_id: str,
    gig_id: str,
    proposal_id: str,
    capability_manifest_id: str | None,
    allow_approved: bool = False,
    allow_published: bool = False,
) -> CapabilitySuccessorApproval | None:
    """Authenticate a prepared successor while the lifecycle writer lock is held."""

    _id(proposal_id, _PROPOSAL_ID, "capability_successor_identity_invalid")
    if not _committed_binding_exists(
        workpad=workpad,
        project_id=project_id,
        gig_id=gig_id,
        proposal_id=proposal_id,
    ):
        return None
    try:
        sidecar_bytes = _committed_file(
            workpad,
            f"manifests/capability-successors/{proposal_id}.json",
            code="capability_successor_authority_unavailable",
        )
    except CapabilityReviewError as exc:
        raise CapabilitySuccessorError(
            "successor binding is unavailable",
            code="capability_successor_authority_unavailable",
        ) from exc
    _schema(sidecar_bytes)
    binding = _parse(sidecar_bytes, code="capability_successor_binding_invalid")
    if binding.get("proposal_id") != proposal_id or binding.get("project_id") != project_id or binding.get("gig_id") != gig_id:
        raise CapabilitySuccessorError("successor binding ownership differs", code="capability_successor_authority_unavailable")
    reviewed_ref = binding.get("reviewed_manifest_ref")
    decision_ref = binding.get("decision_ref")
    capability_id = binding.get("source_binding", {}).get("capability_id") if isinstance(binding.get("source_binding"), Mapping) else None
    _id(capability_id, _CAPABILITY_ID, "capability_successor_binding_invalid")
    if capability_manifest_id is not None:
        _id(capability_manifest_id, _MANIFEST_ID, "capability_successor_manifest_invalid")
        if not isinstance(reviewed_ref, Mapping) or Path(str(reviewed_ref.get("path", ""))).stem != capability_manifest_id:
            raise CapabilitySuccessorError("requested capability manifest differs from successor", code="capability_successor_manifest_invalid")
    try:
        pointer_bytes = _committed_file(
            workpad,
            "manifests/active-gig-version.json",
            code="capability_successor_current_conflict",
        )
    except CapabilityReviewError as exc:
        raise CapabilitySuccessorError(
            "successor base authority is unavailable",
            code="capability_successor_current_conflict",
        ) from exc
    pointer = _review_check(
        lambda: _validate_pointer(pointer_bytes),
        code="capability_successor_current_conflict",
    )
    assert isinstance(pointer, Mapping)
    published = (
        allow_published
        and pointer.get("approved_proposal_id") == proposal_id
        and pointer.get("active_version") == binding.get("base_gig_version", 0) + 1
    )
    if published:
        if pointer.get("capability_manifest") != reviewed_ref:
            raise CapabilitySuccessorError(
                "published successor pointer differs from binding",
                code="capability_successor_current_conflict",
            )
    elif pointer.get("gig_id") != gig_id or pointer.get("active_version") != binding.get("base_gig_version") or pointer.get("approved_proposal_id") != binding.get("base_proposal_id"):
        raise CapabilitySuccessorError("successor base authority changed", code="capability_successor_current_conflict")
    if not isinstance(reviewed_ref, Mapping) or not isinstance(decision_ref, Mapping):
        raise CapabilitySuccessorError("successor review references are invalid", code="capability_successor_binding_invalid")
    snapshot = _capture_snapshot_for_approval(workpad, project_id, gig_id)
    decision, reviewed, _parent, source = _review_authority(
        workpad=workpad,
        snapshot=snapshot,
        project_id=project_id,
        gig_id=gig_id,
        base_version=int(binding["base_gig_version"]),
        base_proposal_id=str(binding["base_proposal_id"]),
        capability_id=str(capability_id),
        decision_ref=decision_ref,
        reviewed_manifest_ref=reviewed_ref,
    )
    if source != binding.get("source_binding"):
        raise CapabilitySuccessorError("successor source binding changed", code="capability_successor_source_changed")
    pending_ref = binding.get("pending_proposal_ref")
    pending_bytes = _snapshot_ref(snapshot, pending_ref, prefix="manifests/capability-successors/", code="capability_successor_authority_unavailable")
    pending = _parse(pending_bytes, code="capability_successor_authority_unavailable")
    try:
        current_bytes = _committed_file(
            workpad,
            "manifests/gig-proposal.json",
            code="capability_successor_authority_unavailable",
        )
    except CapabilityReviewError as exc:
        raise CapabilitySuccessorError(
            "current successor proposal is unavailable",
            code="capability_successor_authority_unavailable",
        ) from exc
    current = _parse(current_bytes, code="capability_successor_authority_unavailable")
    expected_status = "approved" if allow_approved else "proposed"
    expected = dict(pending)
    expected["status"] = expected_status
    if current != expected:
        raise CapabilitySuccessorError("current successor proposal differs from binding", code="capability_successor_authority_unavailable")
    if not allow_approved and current.get("status") != "proposed":
        raise CapabilitySuccessorError("successor proposal is not pending", code="capability_successor_pending_conflict")
    return CapabilitySuccessorApproval(
        dict(current),
        dict(binding),
        _ref(f"manifests/capability-successors/{proposal_id}.json", sidecar_bytes),
        dict(reviewed_ref),
        str(capability_id),
    )


def _capture_snapshot_for_approval(workpad: Path, project_id: str, gig_id: str) -> JournalSnapshot:
    """Capture immutable capability/review/successor authority for approval."""

    from .journal import _capture_committed_snapshot

    try:
        return _capture_committed_snapshot(
            workpad,
            project_id,
            gig_id,
            ("manifests/capabilities/", "manifests/capability-reviews/", "manifests/capability-successors/"),
        )
    except JournalConflictError as exc:
        raise CapabilitySuccessorError("successor authority is unavailable", code="capability_successor_authority_unavailable") from exc


__all__ = [
    "CapabilitySuccessorApproval",
    "CapabilitySuccessorError",
    "CapabilitySuccessorResult",
    "prepare_capability_successor",
    "reviewed_manifest_requires_successor",
    "successor_approval_context",
]
