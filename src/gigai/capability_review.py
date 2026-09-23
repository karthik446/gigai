"""Explicit review and effect consent for one local native-record tool.

This module is deliberately a small authority adapter around the existing
manifest validator, source inventory checker, and journal writer.  It never
imports or executes source, installs a package, calls a provider, changes the
active Gig, or edits a parent manifest.  The repository root registers the
capability-review schema and ``capability_review_decided`` transition.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
import re
from pathlib import Path
import subprocess
import uuid

from .canonical import (
    CanonicalizationError,
    canonical_json_bytes,
    canonical_json_digest,
    digest_imported_bytes,
    parse_json_bytes,
)
from .capabilities import validate_capability_manifest
from .journal import (
    JournalArtifact,
    JournalConflictError,
    JournalTransition,
    run_with_journal_writer,
)
from .scout.tools import ScoutToolError, _inventory
from .validators import validate_serialized_contract


_SCHEMA = "capability-review-decision.schema.json"
_REVIEW_TRANSITION = "capability_review_decided"
_DECISION_ID = re.compile(
    r"^capreview_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MANIFEST_ID = re.compile(
    r"^capmanifest_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CAPABILITY_ID = re.compile(
    r"^cap_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_PROPOSAL_ID = re.compile(
    r"^gp_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ALLOWED_OPERATIONS = frozenset({"record_archive", "record_create", "record_update"})
_ALLOWED_EFFECTS = ["write_workpad"]
_ALLOWED_PERMISSIONS = {
    "filesystem": "write_isolated",
    "network": "none",
    "credentials": "none",
}
_ARTIFACT_REF_FIELDS = frozenset(
    {"path", "content_sha256", "canonical_sha256", "media_type", "size_bytes"}
)
_REQUIRED_ARTIFACT_REF_FIELDS = frozenset(
    {"path", "content_sha256", "media_type", "size_bytes"}
)


class CapabilityReviewError(ValueError):
    """Typed, content-redacted refusal from the generic review service."""

    code = "capability_review_refused"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass(frozen=True)
class CapabilityReviewResult:
    """The immutable decision and optional reviewed manifest references."""

    decision: dict[str, object]
    decision_ref: dict[str, object]
    reviewed_manifest_ref: dict[str, object] | None
    parent_manifest_ref: dict[str, object]
    replayed: bool


def _actor(value: Mapping[str, object], *, operator: bool = False) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise CapabilityReviewError("review actor is invalid", code="capability_review_actor_invalid")
    actor = dict(value)
    if set(actor) - {"kind", "id", "model_target"} or not {"kind", "id"} <= set(actor):
        raise CapabilityReviewError("review actor is invalid", code="capability_review_actor_invalid")
    if not isinstance(actor["kind"], str) or not isinstance(actor["id"], str) or not actor["id"]:
        raise CapabilityReviewError("review actor is invalid", code="capability_review_actor_invalid")
    if operator and actor["kind"] != "operator":
        raise CapabilityReviewError("effect consent requires a direct operator", code="capability_review_operator_required")
    if "model_target" in actor and actor["model_target"] is not None and not isinstance(actor["model_target"], str):
        raise CapabilityReviewError("review actor is invalid", code="capability_review_actor_invalid")
    return actor


def _ref(path: str, payload: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(payload),
        "media_type": "application/json",
        "size_bytes": len(payload),
    }


def _validate_artifact_ref(
    reference: object,
    expected: Mapping[str, object],
    payload: bytes,
    *,
    code: str,
) -> None:
    """Check an artifact reference, including an optional canonical digest."""

    if not isinstance(reference, Mapping):
        raise CapabilityReviewError("artifact reference is invalid", code=code)
    if (
        not _REQUIRED_ARTIFACT_REF_FIELDS <= set(reference)
        or not set(reference) <= _ARTIFACT_REF_FIELDS
        or any(reference.get(key) != expected.get(key) for key in _REQUIRED_ARTIFACT_REF_FIELDS)
    ):
        raise CapabilityReviewError("artifact reference is not authenticated", code=code)
    if "canonical_sha256" not in reference or reference["canonical_sha256"] is None:
        return
    try:
        canonical_digest = canonical_json_digest(parse_json_bytes(payload))
    except CanonicalizationError as exc:
        raise CapabilityReviewError("artifact canonical digest is unavailable", code=code) from exc
    if reference["canonical_sha256"] != canonical_digest:
        raise CapabilityReviewError("artifact canonical digest is not authenticated", code=code)


def _artifact(snapshot: object, path: str) -> bytes:
    artifacts = getattr(snapshot, "artifacts", None)
    payload = artifacts.get(path) if isinstance(artifacts, Mapping) else None
    if not isinstance(payload, bytes):
        raise CapabilityReviewError("required committed authority is unavailable", code="capability_review_authority_unavailable")
    return payload


def _authenticated_reviewed_manifest(
    snapshot: object, reference: object, *, gig_id: str
) -> dict[str, object]:
    if not isinstance(reference, Mapping):
        raise CapabilityReviewError(
            "reviewed manifest reference is invalid",
            code="capability_review_manifest_ref_invalid",
        )
    path = reference.get("path")
    if (
        not isinstance(path, str)
        or not path.startswith("manifests/capabilities/")
        or not path.endswith(".json")
        or len(Path(path).parts) != 3
    ):
        raise CapabilityReviewError(
            "reviewed manifest reference is invalid",
            code="capability_review_manifest_ref_invalid",
        )
    payload = _artifact(snapshot, path)
    _validate_artifact_ref(
        reference,
        _ref(path, payload),
        payload,
        code="capability_review_manifest_ref_invalid",
    )
    manifest = _parse(payload, code="capability_review_manifest_invalid")
    manifest_id = Path(path).name.removesuffix(".json")
    if manifest.get("manifest_id") != manifest_id or manifest.get("gig_id") != gig_id:
        raise CapabilityReviewError(
            "reviewed manifest identity is invalid",
            code="capability_review_manifest_ref_invalid",
        )
    if not validate_capability_manifest(payload).valid:
        raise CapabilityReviewError(
            "reviewed manifest is invalid",
            code="capability_review_manifest_invalid",
        )
    return dict(manifest)


def _committed_file(workpad: Path, path: str, *, code: str) -> bytes:
    """Read a mutable-current artifact only when its working bytes equal HEAD."""

    candidate = workpad / path
    current = workpad
    try:
        for part in Path(path).parts:
            current = current / part
            if current.is_symlink():
                raise CapabilityReviewError("working authority is redirected", code=code)
        working = candidate.read_bytes()
        result = subprocess.run(
            ["git", "-C", str(workpad), "show", f"HEAD:{path}"],
            capture_output=True,
            check=False,
            shell=False,
        )
    except OSError as exc:
        raise CapabilityReviewError("committed authority is unavailable", code=code) from exc
    if result.returncode != 0 or result.stdout != working:
        raise CapabilityReviewError("working authority differs from committed bytes", code=code)
    return working


def _parse(payload: bytes, *, code: str) -> Mapping[str, object]:
    try:
        value = parse_json_bytes(payload)
    except CanonicalizationError as exc:
        raise CapabilityReviewError("committed authority is malformed", code=code) from exc
    if not isinstance(value, Mapping):
        raise CapabilityReviewError("committed authority is malformed", code=code)
    return value


def _validate_review_decision(payload: bytes) -> None:
    report = validate_serialized_contract(_SCHEMA, payload)
    if any(item.code == "unknown_schema" for item in report.findings):
        raise CapabilityReviewError(
            "capability review schema is not registered",
            code="capability_review_schema_unregistered",
        )
    if not report.valid:
        raise CapabilityReviewError(
            "capability review decision is invalid",
            code="capability_review_decision_invalid",
        )


def _validate_pointer(pointer_bytes: bytes) -> Mapping[str, object]:
    pointer = _parse(pointer_bytes, code="capability_review_pointer_invalid")
    if not (
        validate_serialized_contract("active-gig-version-v2.schema.json", pointer_bytes).valid
        or validate_serialized_contract("active-gig-version.schema.json", pointer_bytes).valid
    ):
        raise CapabilityReviewError("active Gig authority is invalid", code="capability_review_pointer_invalid")
    return pointer


def _validate_base_proposal(proposal_bytes: bytes, proposal_id: str) -> None:
    proposal = _parse(proposal_bytes, code="capability_review_proposal_invalid")
    if proposal.get("proposal_id") != proposal_id or proposal.get("status") != "approved":
        raise CapabilityReviewError("approved base proposal is unavailable", code="capability_review_base_invalid")
    if not (
        validate_serialized_contract("gig-proposal-v2.schema.json", proposal_bytes).valid
        or validate_serialized_contract("gig-proposal.schema.json", proposal_bytes).valid
    ):
        raise CapabilityReviewError("approved base proposal is invalid", code="capability_review_proposal_invalid")


def _validate_requested_boundary(
    capability: Mapping[str, object],
    *,
    capability_id: str,
    effects: Sequence[str],
    permissions: Mapping[str, str],
    selected_option_id: str,
) -> tuple[Mapping[str, object], Mapping[str, object], list[dict[str, object]]]:
    if capability.get("capability_id") != capability_id:
        raise CapabilityReviewError("capability identity does not match", code="capability_review_capability_invalid")
    if capability.get("kind") != "tool":
        raise CapabilityReviewError("capability kind is unsupported", code="capability_review_unsupported_kind")
    if capability.get("declared_effects") != list(effects) or list(effects) != _ALLOWED_EFFECTS:
        raise CapabilityReviewError("capability effects are unsupported", code="capability_review_effect_refused")
    if dict(capability.get("permissions", {})) != dict(permissions) or dict(permissions) != _ALLOWED_PERMISSIONS:
        raise CapabilityReviewError("capability permissions are unsupported", code="capability_review_effect_refused")
    if capability.get("network_requirement") != "none" or capability.get("credential_requirements") != []:
        raise CapabilityReviewError("capability effects are unsupported", code="capability_review_effect_refused")
    constraints = capability.get("source_constraints")
    binding = capability.get("tool_binding")
    if not isinstance(constraints, Mapping) or not isinstance(binding, Mapping):
        raise CapabilityReviewError("capability lacks an inventoried tool binding", code="capability_review_binding_invalid")
    if constraints.get("allowed_source_kinds") != ["local_artifact"]:
        raise CapabilityReviewError("capability source kind is unsupported", code="capability_review_source_refused")
    operations = binding.get("operations")
    if (
        not isinstance(operations, list)
        or not operations
        or operations != sorted(set(operations))
        or any(operation not in _ALLOWED_OPERATIONS for operation in operations)
    ):
        raise CapabilityReviewError("capability operations are unsupported", code="capability_review_operation_refused")
    if binding.get("effects") != _ALLOWED_EFFECTS:
        raise CapabilityReviewError("capability effects are unsupported", code="capability_review_effect_refused")
    options = capability.get("options")
    if not isinstance(options, list):
        raise CapabilityReviewError("capability options are invalid", code="capability_review_binding_invalid")
    selected = next((item for item in options if isinstance(item, Mapping) and item.get("option_id") == selected_option_id), None)
    if not isinstance(selected, Mapping) or selected.get("kind") != "use_available" or selected.get("decision") != "pending":
        raise CapabilityReviewError("selected capability option is unavailable", code="capability_review_option_refused")
    inventory = binding.get("inventory")
    if not isinstance(inventory, list):
        raise CapabilityReviewError("capability inventory is invalid", code="capability_review_binding_invalid")
    return constraints, binding, [dict(item) for item in inventory if isinstance(item, Mapping)]


def review_local_tool(
    *,
    workpad: Path,
    project_id: str,
    gig_id: str,
    base_version: int,
    base_proposal_id: str,
    manifest_id: str,
    capability_id: str,
    reviewer: Mapping[str, object],
    reviewer_outcome: str,
    reviewer_rationale: str,
    evidence_refs: Sequence[str],
    operator_actor: Mapping[str, object],
    operator_confirmed: bool,
    effects: Sequence[str] = ("write_workpad",),
    permissions: Mapping[str, str] = _ALLOWED_PERMISSIONS,
    selected_option_id: str = "A",
    operation_key: str,
    now: str | None = None,
    decision_id: str | None = None,
    reviewed_manifest_id: str | None = None,
    parent_manifest_ref: Mapping[str, object] | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    _before_publication: Callable[[], None] | None = None,
) -> CapabilityReviewResult:
    """Review one committed local native-record tool without activating it.

    A passed review requires positive reviewer evidence *and* a separate
    direct operator confirmation.  A rejected review records only the
    immutable decision and returns no reviewed manifest.  The service accepts
    no source path or executable callback: all source bytes come from the
    committed manifest inventory and are inspected as inert bytes.
    """

    if type(base_version) is not int or base_version < 1:
        raise CapabilityReviewError("approved base version is required", code="capability_review_base_required")
    if not _PROPOSAL_ID.fullmatch(base_proposal_id):
        raise CapabilityReviewError("base proposal identity is invalid", code="capability_review_base_invalid")
    if not _MANIFEST_ID.fullmatch(manifest_id) or not _CAPABILITY_ID.fullmatch(capability_id):
        raise CapabilityReviewError("capability identity is invalid", code="capability_review_identity_invalid")
    if type(operation_key) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", operation_key):
        raise CapabilityReviewError("operation key is invalid", code="capability_review_operation_invalid")
    if reviewer_outcome not in {"passed", "rejected"}:
        raise CapabilityReviewError("review outcome is invalid", code="capability_review_outcome_invalid")
    if type(reviewer_rationale) is not str or not reviewer_rationale.strip() or len(reviewer_rationale) > 4000:
        raise CapabilityReviewError("review rationale is required", code="capability_review_evidence_required")
    evidence = tuple(evidence_refs)
    if any(type(item) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}", item) for item in evidence):
        raise CapabilityReviewError("review evidence references are invalid", code="capability_review_evidence_invalid")
    if reviewer_outcome == "passed" and not evidence:
        raise CapabilityReviewError("a passed review requires evidence", code="capability_review_evidence_required")
    reviewer_actor = _actor(reviewer)
    operator = _actor(operator_actor, operator=True)
    if operator_confirmed is not True:
        raise CapabilityReviewError("direct operator effect consent is required", code="capability_review_operator_consent_required")
    if list(effects) != _ALLOWED_EFFECTS or dict(permissions) != _ALLOWED_PERMISSIONS:
        raise CapabilityReviewError("requested effects are unsupported", code="capability_review_effect_refused")
    if decision_id is not None and not _DECISION_ID.fullmatch(decision_id):
        raise CapabilityReviewError("decision identity is invalid", code="capability_review_identity_invalid")
    if reviewed_manifest_id is not None and not _MANIFEST_ID.fullmatch(reviewed_manifest_id):
        raise CapabilityReviewError("reviewed manifest identity is invalid", code="capability_review_identity_invalid")
    if reviewed_manifest_id == manifest_id:
        raise CapabilityReviewError("reviewed manifest must be a new identity", code="capability_review_identity_conflict")
    timestamp = now or datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def operation(writer: object) -> CapabilityReviewResult:
        # The active pointer and proposal are intentionally republished as a
        # version advances, so read them as current committed files. Immutable
        # capability/source families remain in the authenticated snapshot.
        # Tool source is an editable ignored copy; its committed authority is
        # the manifest inventory, so inspect it with _inventory below rather
        # than asking the journal snapshot to treat ignored files as artifacts.
        snapshot = writer.snapshot(("manifests/capabilities/", "manifests/capability-reviews/"))  # type: ignore[attr-defined]
        pointer = _validate_pointer(_committed_file(workpad, "manifests/active-gig-version.json", code="capability_review_pointer_invalid"))
        if pointer.get("gig_id") != gig_id or pointer.get("active_version") != base_version or pointer.get("approved_proposal_id") != base_proposal_id:
            raise CapabilityReviewError("approved base version does not match current authority", code="capability_review_current_version_conflict")
        if pointer.get("capability_manifest") is not None:
            raise CapabilityReviewError("current Gig already has capability authority", code="capability_review_current_version_conflict")
        _validate_base_proposal(_committed_file(workpad, "manifests/gig-proposal.json", code="capability_review_proposal_invalid"), base_proposal_id)
        parent_path = f"manifests/capabilities/{manifest_id}.json"
        parent_bytes = _artifact(snapshot, parent_path)
        parent_report = validate_capability_manifest(parent_bytes)
        if not parent_report.valid:
            raise CapabilityReviewError("parent capability manifest is invalid", code="capability_review_parent_invalid")
        parent = _parse(parent_bytes, code="capability_review_parent_invalid")
        if parent.get("manifest_id") != manifest_id or parent.get("gig_id") != gig_id:
            raise CapabilityReviewError("parent capability manifest identity is invalid", code="capability_review_parent_invalid")
        capabilities = parent.get("capabilities")
        if not isinstance(capabilities, list):
            raise CapabilityReviewError("parent capability manifest is invalid", code="capability_review_parent_invalid")
        capability = next((item for item in capabilities if isinstance(item, Mapping) and item.get("capability_id") == capability_id), None)
        if not isinstance(capability, Mapping):
            raise CapabilityReviewError("capability is absent from parent manifest", code="capability_review_capability_invalid")
        _constraints, binding, inventory = _validate_requested_boundary(
            capability,
            capability_id=capability_id,
            effects=effects,
            permissions=permissions,
            selected_option_id=selected_option_id,
        )
        if capability.get("security_review", {}).get("status") != "pending":
            raise CapabilityReviewError("parent capability is not pending review", code="capability_review_parent_not_pending")
        try:
            actual_inventory = _inventory(workpad, capability_id, binding)
        except ScoutToolError as exc:
            raise CapabilityReviewError(str(exc), code=f"capability_review_{exc.code}") from exc
        if actual_inventory != inventory or digest_imported_bytes(canonical_json_bytes(actual_inventory)) != binding.get("inventory_sha256"):
            raise CapabilityReviewError("source inventory changed", code="capability_review_source_changed")
        actual_parent_ref = _ref(parent_path, parent_bytes)
        if parent_manifest_ref is not None:
            _validate_artifact_ref(
                parent_manifest_ref,
                actual_parent_ref,
                parent_bytes,
                code="capability_review_parent_ref_mismatch",
            )
        request = {
            "project_id": project_id,
            "gig_id": gig_id,
            "base_version": base_version,
            "base_proposal_id": base_proposal_id,
            "manifest_id": manifest_id,
            "capability_id": capability_id,
            "reviewer": reviewer_actor,
            "reviewer_outcome": reviewer_outcome,
            "reviewer_rationale": reviewer_rationale,
            "evidence_refs": list(evidence),
            "operator_consent": {"confirmed": True, "actor": operator, "effects": list(effects), "permissions": dict(permissions)},
            "selected_option_id": selected_option_id,
            "effects": list(effects),
            "permissions": dict(permissions),
            "operation_key": operation_key,
            "parent_manifest_ref": actual_parent_ref,
            "source_inventory_sha256": binding.get("inventory_sha256"),
        }
        request_sha = digest_imported_bytes(canonical_json_bytes(request))
        decisions: list[tuple[str, bytes, Mapping[str, object]]] = []
        for path, payload in sorted(snapshot.artifacts.items()):
            if not path.startswith("manifests/capability-reviews/") or not path.endswith(".json"):
                continue
            try:
                prior = _parse(payload, code="capability_review_decision_invalid")
            except CapabilityReviewError:
                raise
            decisions.append((path, payload, prior))

        # Resolve the exact operation key across all committed decisions first.
        # A capmanifest-keyed passed decision sorts before a capreview-keyed
        # rejected decision, so combining this with the duplicate-parent scan
        # would incorrectly refuse a valid replay.
        key_matches = [item for item in decisions if item[2].get("operation_key") == operation_key]
        if len(key_matches) > 1:
            raise CapabilityReviewError("operation key has multiple committed decisions", code="capability_review_conflict")
        if key_matches:
            path, payload, prior = key_matches[0]
            if prior.get("request_sha256") != request_sha:
                raise CapabilityReviewError("operation key was reused with different inputs", code="capability_review_conflict")
            _validate_review_decision(payload)
            prior_ref = _ref(path, payload)
            reviewed_ref = prior.get("reviewed_manifest_ref")
            if reviewer_outcome == "passed":
                _authenticated_reviewed_manifest(snapshot, reviewed_ref, gig_id=gig_id)
                if not isinstance(reviewed_ref, Mapping):
                    raise CapabilityReviewError("reviewed manifest reference is invalid", code="capability_review_manifest_ref_invalid")
                expected_decision_path = "manifests/capability-reviews/" + Path(str(reviewed_ref["path"])).name
                if path != expected_decision_path:
                    raise CapabilityReviewError("review decision linkage is invalid", code="capability_review_manifest_ref_invalid")
            return CapabilityReviewResult(dict(prior), prior_ref, dict(reviewed_ref) if isinstance(reviewed_ref, Mapping) else None, actual_parent_ref, True)

        # Only an authenticated passed decision for this exact pending parent
        # reserves a successor.  A passed decision for a different parent in
        # the same Gig/capability is a legitimate separate review.
        for path, payload, prior in decisions:
            if (
                prior.get("reviewer_outcome") == "passed"
                and prior.get("project_id") == project_id
                and prior.get("gig_id") == gig_id
                and prior.get("capability_id") == capability_id
            ):
                _validate_review_decision(payload)
                prior_parent_ref = prior.get("parent_manifest_ref")
                if not isinstance(prior_parent_ref, Mapping):
                    raise CapabilityReviewError(
                        "committed passed review parent reference is invalid",
                        code="capability_review_conflict",
                    )
                if any(
                    prior_parent_ref.get(key) != actual_parent_ref.get(key)
                    for key in _REQUIRED_ARTIFACT_REF_FIELDS
                ):
                    continue
                _validate_artifact_ref(
                    prior_parent_ref,
                    actual_parent_ref,
                    parent_bytes,
                    code="capability_review_conflict",
                )
                _authenticated_reviewed_manifest(
                    snapshot,
                    prior.get("reviewed_manifest_ref"),
                    gig_id=gig_id,
                )
                if not isinstance(prior.get("reviewed_manifest_ref"), Mapping):
                    raise CapabilityReviewError("reviewed manifest reference is invalid", code="capability_review_conflict")
                expected_decision_path = "manifests/capability-reviews/" + Path(str(prior["reviewed_manifest_ref"]["path"])).name
                if path != expected_decision_path:
                    raise CapabilityReviewError("review decision linkage is invalid", code="capability_review_conflict")
                raise CapabilityReviewError(
                    "pending parent already has an authenticated passed review",
                    code="capability_review_conflict",
                )
        if _before_publication is not None:
            _before_publication()
        final_pointer = _validate_pointer(
            _committed_file(
                workpad,
                "manifests/active-gig-version.json",
                code="capability_review_current_version_conflict",
            )
        )
        if final_pointer != pointer:
            raise CapabilityReviewError(
                "approved base version changed during review",
                code="capability_review_current_version_conflict",
            )
        _validate_base_proposal(
            _committed_file(
                workpad,
                "manifests/gig-proposal.json",
                code="capability_review_current_version_conflict",
            ),
            base_proposal_id,
        )
        try:
            final_inventory = _inventory(workpad, capability_id, binding)
        except ScoutToolError as exc:
            raise CapabilityReviewError(str(exc), code="capability_review_source_changed") from exc
        if final_inventory != actual_inventory:
            raise CapabilityReviewError("source inventory changed during review", code="capability_review_source_changed")

        decision_value: dict[str, object] = {
            "schema_version": "1.0",
            "decision_id": decision_id or f"capreview_{uuid_factory()}",
            "decision_version": 1,
            "project_id": project_id,
            "gig_id": gig_id,
            "base_version": base_version,
            "base_proposal_id": base_proposal_id,
            "capability_id": capability_id,
            "operation_key": operation_key,
            "request_sha256": request_sha,
            "parent_manifest_ref": actual_parent_ref,
            "reviewed_manifest_ref": None,
            "reviewer": reviewer_actor,
            "reviewer_outcome": reviewer_outcome,
            "reviewer_rationale": reviewer_rationale,
            "evidence_refs": list(evidence),
            "operator_consent": {"confirmed": True, "actor": operator, "effects": list(effects), "permissions": dict(permissions)},
            "source_binding": {"inventory_sha256": binding["inventory_sha256"], "operations": list(binding["operations"]), "effects": list(effects), "permissions": dict(permissions)},
            "created_at": timestamp,
        }
        reviewed_bytes: bytes | None = None
        reviewed_ref: dict[str, object] | None = None
        if reviewer_outcome == "passed":
            reviewed = deepcopy(dict(parent))
            reviewed["manifest_id"] = reviewed_manifest_id or f"capmanifest_{uuid_factory()}"
            reviewed["manifest_version"] = int(parent["manifest_version"]) + 1
            reviewed["created_at"] = timestamp
            reviewed["created_by"] = {"kind": "gigai", "id": "capability-review", "model_target": None}
            reviewed_capabilities = reviewed["capabilities"]
            assert isinstance(reviewed_capabilities, list)
            reviewed_capability = next(item for item in reviewed_capabilities if isinstance(item, dict) and item.get("capability_id") == capability_id)
            reviewed_capability["availability_state"] = "available"
            reviewed_capability["compatibility"] = {"status": "compatible", "reason": "Deterministic local source and native-record boundary checks passed."}
            reviewed_capability["security_review"] = {
                "status": "passed",
                "checks": ["reviewer_evidence", "source_containment", "source_inventory", "native_record_effect_allowlist"],
                "reason": reviewer_rationale,
            }
            reviewed_bytes = canonical_json_bytes(reviewed)
            if not validate_capability_manifest(reviewed_bytes).valid:
                raise CapabilityReviewError("reviewed capability manifest is invalid", code="capability_review_manifest_invalid")
            reviewed_path = f"manifests/capabilities/{reviewed['manifest_id']}.json"
            reviewed_ref = _ref(reviewed_path, reviewed_bytes)
            decision_value["reviewed_manifest_ref"] = reviewed_ref
        decision_bytes = canonical_json_bytes(decision_value)
        _validate_review_decision(decision_bytes)
        decision_path = (
            f"manifests/capability-reviews/{Path(str(reviewed_ref['path'])).name}"
            if reviewed_ref is not None
            else f"manifests/capability-reviews/{decision_value['decision_id']}.json"
        )
        artifacts = [JournalArtifact(decision_path, decision_bytes)]
        if reviewed_bytes is not None and reviewed_ref is not None:
            artifacts.append(JournalArtifact(str(reviewed_ref["path"]), reviewed_bytes))
        try:
            writer.record(  # type: ignore[attr-defined]
                JournalTransition(
                    f"handoff_{uuid_factory()}",
                    _REVIEW_TRANSITION,
                    "Recorded a local capability review and explicit operator effect consent.",
                    tuple(artifacts),
                    {"project_id": project_id, "gig_id": gig_id, "operation_key": operation_key, "artifact_refs": [{"path": item.path, "content_sha256": digest_imported_bytes(item.content), "size_bytes": len(item.content)} for item in artifacts]},
                )
            )
        except JournalConflictError as exc:
            raise CapabilityReviewError("capability review publication is unavailable", code="capability_review_transition_unregistered") from exc
        decision_ref = _ref(str(artifacts[0].path), decision_bytes)
        return CapabilityReviewResult(decision_value, decision_ref, reviewed_ref, actual_parent_ref, False)

    try:
        return run_with_journal_writer(workpad=workpad, project_id=project_id, gig_id=gig_id, operation=operation)
    except CapabilityReviewError:
        raise
    except JournalConflictError as exc:
        raise CapabilityReviewError("capability review authority is unavailable", code="capability_review_authority_unavailable") from exc


__all__ = ["CapabilityReviewError", "CapabilityReviewResult", "review_local_tool"]
