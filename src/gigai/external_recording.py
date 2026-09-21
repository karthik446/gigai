"""Journal-backed recording of externally performed Scout work.

This module deliberately has no provider adapter.  It seals only the approved
Graph Set, named private inputs, and locally recorded external declarations.
The top-level command and shared schema/identifier registrations are mounted by
the integration owner; ``external_cli`` is independently mountable meanwhile.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import base64
import binascii
from importlib import resources
from pathlib import Path
import uuid
from typing import Callable, Mapping, Sequence

from .canonical import (
    canonical_json_bytes,
    derive_deterministic_id,
    digest_imported_bytes,
    parse_json_bytes,
)
from .graph_set import validate_selection_record
from .journal import (
    JournalArtifact,
    JournalConflictError,
    JournalSnapshot,
    JournalTransition,
    read_committed_artifact,
    run_with_journal_writer,
)
from .private_records import _resolved
from .run import RunError, _resolve_authority, resolve_selected_graph_authority
from .scout_inputs import (
    ScoutInputError,
    assert_single_override_context,
    resolve_external_input,
    revalidate_external_input,
)
from .scout_tailoring import validate_tailoring_request
from .validators import validate_serialized_contract


LIMITS = {
    "max_envelope_bytes": 262144,
    "max_artifact_bytes": 1048576,
    "max_artifacts_per_operation": 32,
    "max_total_bytes_per_operation": 4194304,
    "max_checkpoint_questions": 32,
    "max_checkpoints_per_run": 256,
}
RESEARCH_DOMAIN_SCHEMA_ID = "urn:gigai:scout:research-packet:2"
RESEARCH_VALIDATOR_REF = "scout-role-research:2"
RESEARCH_VALIDATOR_SOURCE = "gigai.scout_research:validate_research_domain"
RESEARCH_V3_DOMAIN_SCHEMA_ID = "urn:gigai:scout:research-packet:3"
RESEARCH_V3_VALIDATOR_REF = "scout-role-research:3"
RESEARCH_V3_VALIDATOR_SOURCE = "gigai.scout_research_v3:validate_research_domain"
DISCOVERY_DOMAIN_SCHEMA_ID = "urn:gigai:scout:discovery-packet:2"
DISCOVERY_VALIDATOR_REF = "scout-job-discovery:2"
DISCOVERY_VALIDATOR_SOURCE = "gigai.scout_discovery:validate_discovery_domain"
TAILORING_DOMAIN_SCHEMA_ID = "urn:gigai:scout:tailoring-packet:1"
TAILORING_VALIDATOR_REF = "scout-application-tailoring:1"
TAILORING_VALIDATOR_SOURCE = "gigai.scout_tailoring:validate_tailoring_domain"
_DOMAIN_ID_LIMIT = 64
TERMINAL = frozenset({"succeeded", "cancelled", "interrupted"})


class ExternalRecordingError(RuntimeError):
    """A redacted, typed external-recording refusal."""

    def __init__(
        self, code: str, message: str, *, next_action: object = "correct_input"
    ) -> None:
        super().__init__(message)
        self.code, self.next_action = code, next_action

    def result(self) -> dict[str, object]:
        return {
            "status": "error",
            "error": {
                "code": self.code,
                "message": str(self),
                "next_action": self.next_action,
            },
        }


def _fixed_research_validator(**kwargs: object) -> None:
    """Dispatch only to the reviewed packaged research bridge by fixed name."""
    try:
        from .scout_research import validate_research_domain
    except ModuleNotFoundError as exc:
        if exc.name != "gigai.scout_research":
            raise
        raise ExternalRecordingError(
            "external_domain_validator_unavailable",
            "fixed research domain validator is not installed",
            next_action="install_reviewed_research_bridge",
        ) from exc
    try:
        validate_research_domain(**kwargs)
    except ExternalRecordingError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", "external_domain_invalid")
        if not isinstance(code, str) or not code:
            code = "external_domain_invalid"
        raise ExternalRecordingError(
            code, "fixed research domain validator refused evidence"
        ) from exc


def _fixed_research_v3_validator(**kwargs: object) -> None:
    """Dispatch only to the reviewed packaged v3 research bridge."""
    try:
        from .scout_research_v3 import validate_research_domain
    except ModuleNotFoundError as exc:
        if exc.name != "gigai.scout_research_v3":
            raise
        raise ExternalRecordingError(
            "external_domain_validator_unavailable",
            "fixed research v3 domain validator is not installed",
            next_action="install_reviewed_research_v3_bridge",
        ) from exc
    try:
        validate_research_domain(**kwargs)
    except ExternalRecordingError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", "external_domain_invalid")
        if not isinstance(code, str) or not code:
            code = "external_domain_invalid"
        raise ExternalRecordingError(
            code, "fixed research v3 domain validator refused evidence"
        ) from exc
def _fixed_discovery_validator(**kwargs: object) -> None:
    """Dispatch only to the reviewed packaged discovery bridge by fixed name."""
    try:
        from .scout_discovery import validate_discovery_domain
    except ModuleNotFoundError as exc:
        if exc.name != "gigai.scout_discovery":
            raise
        raise ExternalRecordingError(
            "external_domain_validator_unavailable",
            "fixed discovery domain validator is not installed",
            next_action="install_reviewed_discovery_bridge",
        ) from exc
    try:
        validate_discovery_domain(**kwargs)
    except ExternalRecordingError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", "external_domain_invalid")
        if not isinstance(code, str) or not code:
            code = "external_domain_invalid"
        raise ExternalRecordingError(
            code, "fixed discovery domain validator refused evidence"
        ) from exc


def _fixed_tailoring_validator(**kwargs: object) -> None:
    """Dispatch only to the reviewed packaged tailoring bridge by fixed name."""
    try:
        from .scout_tailoring import validate_tailoring_domain
    except ModuleNotFoundError as exc:
        if exc.name != "gigai.scout_tailoring":
            raise
        raise ExternalRecordingError(
            "external_domain_validator_unavailable",
            "fixed tailoring domain validator is not installed",
            next_action="install_reviewed_tailoring_bridge",
        ) from exc
    try:
        validate_tailoring_domain(**kwargs)
    except ExternalRecordingError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", "external_domain_invalid")
        if not isinstance(code, str) or not code:
            code = "external_domain_invalid"
        raise ExternalRecordingError(
            code, "fixed tailoring domain validator refused evidence"
        ) from exc
# Domain IDs are a closed dispatch table.  There is no caller registration,
# plugin import, subprocess, network schema lookup, or arbitrary callback.
_FIXED_DOMAIN_VALIDATORS = {
    RESEARCH_DOMAIN_SCHEMA_ID: _fixed_research_validator,
    RESEARCH_V3_DOMAIN_SCHEMA_ID: _fixed_research_v3_validator,
    DISCOVERY_DOMAIN_SCHEMA_ID: _fixed_discovery_validator,
    TAILORING_DOMAIN_SCHEMA_ID: _fixed_tailoring_validator,
}


@dataclass(frozen=True)
class ExternalResult:
    payload: dict[str, object]
    created: bool


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _id(prefix: str, factory: Callable[[], uuid.UUID]) -> str:
    value = factory()
    if value.version != 4:
        raise ExternalRecordingError(
            "external_invocation_invalid", "identifier factory did not return UUIDv4"
        )
    return f"{prefix}_{value}"


def _ref(
    path: str, data: bytes, media_type: str = "application/json"
) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": media_type,
        "size_bytes": len(data),
    }


def _schema(schema: str, payload: dict[str, object]) -> None:
    if not validate_serialized_contract(schema, canonical_json_bytes(payload)).valid:
        raise ExternalRecordingError(
            "external_invocation_invalid",
            "external protocol payload failed strict validation",
        )


def _validate_predecessor(snapshot: JournalSnapshot, predecessor: object) -> None:
    """Accept only an exact prior external-recording object in this Gig."""
    if predecessor is None:
        return
    if not isinstance(predecessor, Mapping):
        raise ExternalRecordingError(
            "external_authority_mismatch", "predecessor reference is invalid"
        )
    kind = predecessor.get("kind")
    if kind == "run_plan":
        run_plan_id = predecessor.get("run_plan_id")
        path = f"run-plans/{run_plan_id}/external-plan.json"
    elif kind == "run":
        run_id = predecessor.get("run_id")
        path = f"runs/{run_id}/external-run.json"
    elif kind == "checkpoint":
        run_id, checkpoint_id = (
            predecessor.get("run_id"),
            predecessor.get("checkpoint_id"),
        )
        path = f"runs/{run_id}/checkpoints/{checkpoint_id}.json"
    else:
        raise ExternalRecordingError(
            "external_authority_mismatch", "predecessor reference is invalid"
        )
    data = snapshot.artifacts.get(path)
    if data is None:
        raise ExternalRecordingError(
            "external_authority_mismatch", "predecessor is not prior Gig evidence"
        )
    schema = str(kind)
    evidence = _recorded_dispatch(
        data,
        v1_schema={
            "run_plan": "external-recording-plan.schema.json",
            "run": "external-recording-run.schema.json",
            "checkpoint": "external-recording-checkpoint.schema.json",
        }[schema],
        v2_schema={
            "run_plan": "external-recording-plan-v2.schema.json",
            "run": "external-recording-run-v2.schema.json",
            "checkpoint": "external-recording-checkpoint-v2.schema.json",
        }[schema],
        code="external_authority_mismatch",
    )
    identity_key = {
        "run_plan": "run_plan_id",
        "run": "run_id",
        "checkpoint": "checkpoint_id",
    }[str(kind)]
    if evidence.get(identity_key) != predecessor.get(identity_key):
        raise ExternalRecordingError(
            "external_authority_mismatch", "predecessor reference changed"
        )


def _json(data: bytes, code: str) -> dict[str, object]:
    try:
        value = parse_json_bytes(data)
    except ValueError as exc:
        raise ExternalRecordingError(code, "recorded evidence is invalid") from exc
    if not isinstance(value, dict):
        raise ExternalRecordingError(code, "recorded evidence is invalid")
    return value


def _recorded(schema: str, data: bytes, code: str) -> dict[str, object]:
    payload = _json(data, code)
    if not validate_serialized_contract(schema, canonical_json_bytes(payload)).valid:
        raise ExternalRecordingError(code, "recorded evidence is not a valid envelope")
    return payload


def _safe_ref(
    snapshot: JournalSnapshot, value: object, *, code: str
) -> tuple[dict[str, object], bytes]:
    if not isinstance(value, Mapping) or set(value) - {
        "path",
        "content_sha256",
        "media_type",
        "size_bytes",
        "canonical_sha256",
    }:
        raise ExternalRecordingError(code, "artifact reference is invalid")
    path, digest, size = (
        value.get("path"),
        value.get("content_sha256"),
        value.get("size_bytes"),
    )
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or ".." in Path(path).parts
        or not isinstance(digest, str)
        or type(size) is not int
    ):
        raise ExternalRecordingError(code, "artifact reference is invalid")
    data = snapshot.artifacts.get(path)
    if data is None or digest_imported_bytes(data) != digest or len(data) != size:
        raise ExternalRecordingError(
            code, "artifact reference is unavailable or changed"
        )
    return dict(value), data


def _validate_envelope(payload: Mapping[str, object]) -> None:
    try:
        size = len(canonical_json_bytes(dict(payload)))
    except ValueError as exc:
        raise ExternalRecordingError(
            "external_invocation_invalid", "operation envelope is invalid"
        ) from exc
    if size > LIMITS["max_envelope_bytes"]:
        raise ExternalRecordingError(
            "external_limit_exceeded", "operation envelope exceeds its recording limit"
        )


def _invocation(
    *,
    operation: str,
    resolved,
    envelope: Mapping[str, object],
    factory: Callable[[], uuid.UUID],
    protocol_version: int = 1,
) -> dict[str, object]:
    if protocol_version not in {1, 2}:
        raise ExternalRecordingError(
            "external_protocol_unsupported",
            "external recording schema version is unsupported",
            next_action="use_supported_external_protocol",
        )
    allowed = {"origin", "actor", "input", "operation_key"}
    if set(envelope) != allowed or operation not in {
        "plan",
        "start",
        "checkpoint",
        "submit",
        "cancel",
    }:
        raise ExternalRecordingError(
            "external_invocation_invalid", "external invocation fields are invalid"
        )
    origin, actor, typed_input, key = (
        envelope["origin"],
        envelope["actor"],
        envelope["input"],
        envelope["operation_key"],
    )
    if (
        origin not in {"agent_invocation", "direct_cli"}
        or not isinstance(actor, Mapping)
        or not isinstance(typed_input, Mapping)
        or not isinstance(key, str)
        or not key
        or len(key) > 160
    ):
        raise ExternalRecordingError(
            "external_invocation_invalid", "external invocation is malformed"
        )
    actor_value = dict(actor)
    if origin == "agent_invocation":
        if (
            set(actor_value)
            not in ({"kind", "id", "session_id"}, {"kind", "id", "session_id", "model"})
            or actor_value.get("kind") != "agent"
            or not all(
                isinstance(actor_value.get(k), str) and actor_value[k]
                for k in ("id", "session_id")
            )
        ):
            raise ExternalRecordingError(
                "external_invocation_invalid",
                "agent origin needs a bounded agent actor and session",
            )
    elif actor_value != {"kind": "operator", "id": "local-user"}:
        raise ExternalRecordingError(
            "external_invocation_invalid", "direct CLI origin may only name local-user"
        )
    _validate_envelope(envelope)
    normalized = {
        "operation": operation,
        "project_id": resolved.project_id,
        "gig_id": resolved.gig_id,
        "origin": origin,
        "actor": {"kind": actor_value["kind"], "id": actor_value["id"]},
        "input": dict(typed_input),
    }
    result = {
        "schema_version": "1.0",
        "invocation_id": _id("inv", factory),
        "operation": operation,
        "project_id": resolved.project_id,
        "gig_id": resolved.gig_id,
        "origin": origin,
        "actor": actor_value,
        "input": dict(typed_input),
        "operation_key": key,
        "payload_sha256": digest_imported_bytes(canonical_json_bytes(normalized)),
        "created_at": _now(),
    }
    schema = (
        "external-recording-invocation-v2.schema.json"
        if protocol_version == 2
        else "external-recording-invocation.schema.json"
    )
    result["schema_version"] = "2.0" if protocol_version == 2 else "1.0"
    _schema(schema, result)
    return result


def _recorded_dispatch(
    data: bytes,
    *,
    v1_schema: str,
    v2_schema: str,
    code: str,
) -> dict[str, object]:
    """Read a versioned external record without coercing v2 into v1."""
    payload = _json(data, code)
    version = payload.get("schema_version")
    if version == "1.0":
        schema = v1_schema
    elif version == "2.0":
        schema = v2_schema
    else:
        raise ExternalRecordingError(
            "external_protocol_unsupported",
            "external recording schema version is unsupported",
            next_action="use_supported_external_protocol",
        )
    if not validate_serialized_contract(schema, canonical_json_bytes(payload)).valid:
        raise ExternalRecordingError(code, "recorded evidence is not a valid envelope")
    return payload


def _authority(resolved, graph_selector: str | None):
    try:
        projection = __import__("gigai.index", fromlist=["read_index"]).read_index(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
        )
        authority = _resolve_authority(resolved, projection, None)
        graph, descriptor = resolve_selected_graph_authority(
            resolved, authority, graph_selector
        )
    except (RunError, ValueError) as exc:
        raise ExternalRecordingError(
            "external_authority_mismatch", "approved Graph Set authority is unavailable"
        ) from exc
    if (
        descriptor is None
        or not isinstance(authority.get("graph_set"), Mapping)
        or not isinstance(authority.get("graph_set_ref"), Mapping)
    ):
        raise ExternalRecordingError(
            "external_authority_mismatch",
            "external recording requires an approved Graph Set",
        )
    return authority, graph, dict(descriptor)


def _snapshot(resolved) -> JournalSnapshot:
    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=lambda writer: writer.snapshot(
            ("references/", "run-inputs/", "records/", "run-plans/", "runs/")
        ),
    )


def _input_ref(
    resolved,
    snapshot: JournalSnapshot,
    item: object,
    *,
    allow_role_request: bool = False,
    allow_research_run: bool = False,
    allow_discovery_posting: bool = False,
    writer=None,
) -> dict[str, object]:
    try:
        return resolve_external_input(
            resolved,
            snapshot,
            item,
            allow_role_request=allow_role_request,
            allow_research_run=allow_research_run,
            allow_discovery_posting=allow_discovery_posting,
            writer=writer,
        )
    except ScoutInputError as exc:
        raise ExternalRecordingError(
            "external_record_not_found", "selected input is absent, foreign, or changed"
        ) from exc


def _revalidate_input(
    resolved,
    snapshot: JournalSnapshot,
    sealed: object,
    *,
    allow_role_request: bool = False,
    allow_research_run: bool = False,
    allow_discovery_posting: bool = False,
    writer=None,
) -> None:
    try:
        revalidate_external_input(
            resolved,
            snapshot,
            sealed,
            allow_role_request=allow_role_request,
            allow_research_run=allow_research_run,
            allow_discovery_posting=allow_discovery_posting,
            writer=writer,
        )
    except ScoutInputError as exc:
        raise ExternalRecordingError(
            "external_input_mismatch", "sealed input changed or is unavailable"
        ) from exc


def _artifact_contract(
    snapshot: JournalSnapshot, ref: object, code: str
) -> dict[str, object]:
    value, _data = _safe_ref(snapshot, ref, code=code)
    return value


def _approved_ref(resolved, ref: object, code: str) -> dict[str, object]:
    """Check an authority-owned immutable descriptor reference without treating
    a mutable index as authority.  ``_resolve_authority`` already authenticated
    the containing approved version; this adds exact-byte/symlink revalidation.
    """
    if not isinstance(ref, Mapping):
        raise ExternalRecordingError(code, "approved contract reference is invalid")
    path = ref.get("path")
    if (
        not isinstance(path, str)
        or path.startswith("/")
        or "\\" in path
        or ".." in Path(path).parts
    ):
        raise ExternalRecordingError(code, "approved contract reference is invalid")
    candidate = resolved.path / path
    current = resolved.path
    for component in Path(path).parts:
        current = current / component
        if current.is_symlink():
            raise ExternalRecordingError(code, "approved contract path is redirected")
    try:
        data = candidate.read_bytes()
    except OSError as exc:
        raise ExternalRecordingError(code, "approved contract is unavailable") from exc
    if digest_imported_bytes(data) != ref.get("content_sha256") or len(data) != ref.get(
        "size_bytes"
    ):
        raise ExternalRecordingError(code, "approved contract changed")
    return dict(ref)


def _committed_ref(resolved, ref: object, code: str) -> tuple[dict[str, object], bytes]:
    """Authenticate an approved nested resource from journal provenance."""
    checked = _approved_ref(resolved, ref, code)
    try:
        data, _publisher = read_committed_artifact(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            path=str(checked["path"]),
        )
    except Exception as exc:
        raise ExternalRecordingError(code, "approved nested resource is not committed authority") from exc
    if digest_imported_bytes(data) != checked["content_sha256"] or len(data) != checked["size_bytes"]:
        raise ExternalRecordingError(code, "approved nested resource changed")
    return checked, data


def _approved_contract(resolved, ref: object) -> dict[str, object]:
    checked = _approved_ref(resolved, ref, "external_authority_mismatch")
    try:
        content = parse_json_bytes((resolved.path / str(checked["path"])).read_bytes())
    except (OSError, ValueError) as exc:
        raise ExternalRecordingError(
            "external_authority_mismatch", "approved contract is unreadable"
        ) from exc
    if not isinstance(content, dict):
        raise ExternalRecordingError(
            "external_authority_mismatch", "approved contract is invalid"
        )
    return {"ref": checked, "content": content}


def _approved_fields(
    resolved, ref: object, expected_kind: str, *, allow_domains: bool = False
) -> set[str]:
    """Recognize only the published simple field-list contract form.

    More expressive contract languages have no accepted external-recording
    evaluation semantics yet; accepting one here would make success depend on
    an agent interpretation rather than sealed authority.
    """
    content = _approved_contract(resolved, ref)["content"]
    contract = content
    if allow_domains and content.get("schema_version") == "2.0":
        if (
            set(content)
            != {"schema_version", "kind", "gig_id", "fields", "domains"}
            or content.get("kind") != expected_kind
            or content.get("gig_id") != resolved.gig_id
            or not isinstance(content.get("fields"), list)
            or not content["fields"]
            or len(content["fields"]) > LIMITS["max_artifacts_per_operation"]
            or not all(
                isinstance(item, str)
                and item
                and len(item) <= 64
                and item.replace("_", "").replace("-", "").isalnum()
                for item in content["fields"]
            )
            or len(set(content["fields"])) != len(content["fields"])
            or not isinstance(content.get("domains"), Mapping)
            or set(content["domains"]) != set(content["fields"])
        ):
            raise ExternalRecordingError(
                "external_authority_mismatch",
                "v2 output contract lacks its closed domain requirements",
            )
        for output_kind in content["fields"]:
            domain = content["domains"].get(output_kind)
            domain_specs = {
                RESEARCH_DOMAIN_SCHEMA_ID: (RESEARCH_VALIDATOR_REF, "research.schema.json", "research.py"),
                RESEARCH_V3_DOMAIN_SCHEMA_ID: (RESEARCH_V3_VALIDATOR_REF, "research.schema.json", "research.py"),
                DISCOVERY_DOMAIN_SCHEMA_ID: (DISCOVERY_VALIDATOR_REF, "discovery.schema.json", "discovery.py"),
                TAILORING_DOMAIN_SCHEMA_ID: (TAILORING_VALIDATOR_REF, "tailoring.schema.json", "tailoring.py"),
            }
            if (
                not isinstance(domain, Mapping)
                or set(domain) != {"schema_id", "schema_ref", "validator_id", "validator_source_ref"}
                or domain.get("schema_id") not in domain_specs
                or domain.get("validator_id") != domain_specs.get(domain.get("schema_id"), (None,))[0]
            ):
                raise ExternalRecordingError(
                    "external_authority_mismatch",
                    "v2 output contract domain binding is not recognized",
                )
            schema_ref = _approved_ref(
                resolved, domain["schema_ref"], "external_authority_mismatch"
            )
            source_ref = _approved_ref(
                resolved, domain["validator_source_ref"], "external_authority_mismatch"
            )
            schema_suffix, source_suffix = domain_specs[domain["schema_id"]][1:]
            if not str(schema_ref["path"]).endswith(schema_suffix) or not str(source_ref["path"]).endswith(source_suffix):
                raise ExternalRecordingError(
                    "external_authority_mismatch",
                    "v2 domain binding does not name packaged resources",
                )
        return set(content["fields"])
    if (
        set(contract) != {"schema_version", "kind", "gig_id", "fields"}
        or contract.get("schema_version") != "1.0"
        or contract.get("kind") != expected_kind
        or contract.get("gig_id") != resolved.gig_id
        or not isinstance(contract.get("fields"), list)
        or not contract["fields"]
        or len(contract["fields"]) > LIMITS["max_artifacts_per_operation"]
        or not all(
            isinstance(field, str)
            and field
            and len(field) <= 64
            and field.replace("_", "").replace("-", "").isalnum()
            for field in contract["fields"]
        )
        or len(set(contract["fields"])) != len(contract["fields"])
    ):
        raise ExternalRecordingError(
            "external_authority_mismatch",
            "approved contract shape is not supported for external recording",
        )
    return set(contract["fields"])


def _journaled(
    writer,
    *,
    transition: str,
    handoff_id: str,
    body: str,
    artifacts: tuple[JournalArtifact, ...],
    revalidate: Callable[[], None] | None = None,
) -> str:
    # Internal service callback only: no invocation or Gig source can provide
    # this value. Run the final authority check under the existing writer lock
    # immediately before handing already-validated artifacts to publication.
    if revalidate is not None:
        revalidate()
    try:
        refs = [
            _ref(
                item.path,
                item.content,
                "application/json" if item.path.endswith(".json") else "text/markdown",
            )
            for item in artifacts
        ]
        return writer.record(
            JournalTransition(
                handoff_id=handoff_id,
                transition=transition,
                body=body,
                artifacts=artifacts,
                front_matter={
                    "actor": {"kind": "agent", "id": "external-recording"},
                    "outcome": "RECORDED",
                    "artifact_refs": refs,
                },
            )
        ).commit
    except JournalConflictError as exc:
        if "immutable artifact already exists" in str(exc):
            raise ExternalRecordingError(
                "external_operation_conflict",
                "immutable external evidence already exists with different bytes",
            ) from exc
        raise ExternalRecordingError(
            "external_reconciliation_required",
            "journal publication needs explicit reconciliation",
            next_action="reconcile_journal",
        ) from exc
    except Exception as exc:
        # This is normally reached only until Astra registers the frozen
        # transition names.  Do not fall back to a weaker existing transition.
        raise ExternalRecordingError(
            "external_reconciliation_required",
            "external recording transition is not available for journal publication",
            next_action="install_external_transition_registry",
        ) from exc


def plan(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    protocol_version: int = 1,
) -> ExternalResult:
    resolved = _resolved(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id
    )
    invocation = _invocation(
        operation="plan", resolved=resolved, envelope=envelope, factory=uuid_factory,
        protocol_version=protocol_version,
    )
    typed = invocation["input"]
    required = {
        "graph_selector",
        "gig_version",
        "selection_record",
        "input_refs",
        "output_kinds",
        "predecessor",
    }
    if (
        set(typed) != required
        or not isinstance(typed["graph_selector"], str)
        or not isinstance(typed["input_refs"], list)
        or not isinstance(typed["output_kinds"], list)
    ):
        raise ExternalRecordingError(
            "external_invocation_invalid", "plan input is invalid"
        )
    authority, graph, descriptor = _authority(resolved, str(typed["graph_selector"]))
    if typed["gig_version"] not in {None, authority["version"]}:
        raise ExternalRecordingError(
            "external_authority_mismatch",
            "requested Gig version is not the approved version",
        )

    def write(writer):
        snap = writer.snapshot(
            ("references/", "run-inputs/", "records/", "run-plans/", "runs/")
        )
        for path, prior in snap.artifacts.items():
            if not path.startswith("run-plans/") or not path.endswith(
                "/external-plan.json"
            ):
                continue
            previous = _recorded_dispatch(
                prior,
                v1_schema="external-recording-plan.schema.json",
                v2_schema="external-recording-plan-v2.schema.json",
                code="external_reconciliation_required",
            )
            prior_invocation = previous.get("invocation")
            if not isinstance(prior_invocation, Mapping):
                continue
            if prior_invocation.get("operation_key") != invocation["operation_key"]:
                continue
            if prior_invocation.get("payload_sha256") == invocation["payload_sha256"]:
                _require_replay_protocol(previous, invocation)
                return ExternalResult(previous, False)
            raise ExternalRecordingError(
                "external_operation_conflict",
                "operation key already records a different plan",
            )
        _validate_predecessor(snap, typed["predecessor"])
        inputs = [
            _input_ref(
                resolved,
                snap,
                item,
                allow_role_request=protocol_version == 2,
                allow_research_run=protocol_version == 2,
                allow_discovery_posting=protocol_version == 2,
                writer=writer,
            )
            for item in typed["input_refs"]
        ]
        try:
            assert_single_override_context(inputs)
        except ScoutInputError as exc:
            raise ExternalRecordingError(
                "external_input_mismatch", "native override inputs are not isolated"
            ) from exc
        if not inputs:
            raise ExternalRecordingError(
                "external_output_missing",
                "plan needs at least one explicit private input",
                next_action="select_inputs",
            )
        if len(inputs) > LIMITS["max_artifacts_per_operation"]:
            raise ExternalRecordingError(
                "external_limit_exceeded", "too many selected inputs"
            )
        tailoring_request = None
        if protocol_version == 2 and str(typed["graph_selector"]) == "tailor-application":
            tailoring_request = _seal_tailoring_request(snap, inputs)
        selection_ref_value = typed["selection_record"]
        selection_artifact: JournalArtifact | None = None
        if selection_ref_value is None:
            # Direct recording may create its own selection in the same atomic
            # publication.  Agent selection requires a separately journaled
            # invocation reference and is therefore never inferred here.
            if invocation["origin"] != "direct_cli":
                raise ExternalRecordingError(
                    "external_authority_mismatch",
                    "agent plan needs an explicit sealed graph selection",
                    next_action="select_graph",
                )
            selection_id = derive_deterministic_id(
                "graph_selection",
                {
                    "gig_id": resolved.gig_id,
                    "gig_version": authority["version"],
                    "graph_set": authority["graph_set_ref"]["content_sha256"],
                    "selected_graph_id": descriptor["graph_id"],
                    "kind": "operator_explicit",
                },
            )
            selection = {
                "schema_version": "1.0",
                "selection_record_id": selection_id,
                "gig_id": resolved.gig_id,
                "gig_version": authority["version"],
                "graph_set": authority["graph_set_ref"],
                "selected_graph_id": descriptor["graph_id"],
                "selected_graph": descriptor["goal_graph"],
                "selection_kind": "operator_explicit",
                "selector": {
                    "kind": "operator",
                    "actor": {
                        "kind": "operator",
                        "id": "local-user",
                        "model_target": None,
                    },
                    "rule_id": None,
                    "rule_version": None,
                },
                "selection_reason": "explicit external direct CLI graph selector",
                "routing_evidence_refs": [],
                "created_at": authority["graph_set"]["created_at"],
            }
            selection_bytes = canonical_json_bytes(selection)
            if not validate_selection_record(
                selection_bytes,
                graph_set=authority["graph_set"],
                gig_id=resolved.gig_id,
                gig_version=authority["version"],
                root=resolved.path,
            ).valid:
                raise ExternalRecordingError(
                    "external_authority_mismatch",
                    "constructed graph selection is invalid",
                )
            selection_ref = _ref(
                f"graph-selections/{selection_id}.json", selection_bytes
            )
            try:
                existing_selection, _commit = read_committed_artifact(
                    workpad=resolved.path,
                    project_id=resolved.project_id,
                    gig_id=resolved.gig_id,
                    path=str(selection_ref["path"]),
                )
            except JournalConflictError as exc:
                if "not committed" not in str(exc):
                    raise ExternalRecordingError(
                        "external_reconciliation_required",
                        "existing graph selection cannot be authenticated",
                        next_action="reconcile_journal",
                    ) from exc
                selection_artifact = JournalArtifact(
                    str(selection_ref["path"]), selection_bytes
                )
            else:
                if existing_selection != selection_bytes:
                    raise ExternalRecordingError(
                        "external_authority_mismatch",
                        "deterministic graph selection has different committed bytes",
                    )
        else:
            if not isinstance(selection_ref_value, Mapping):
                raise ExternalRecordingError(
                    "external_authority_mismatch", "selection reference is invalid"
                )
            selection_path = selection_ref_value.get("path")
            if not isinstance(selection_path, str) or not selection_path.startswith(
                "graph-selections/"
            ):
                raise ExternalRecordingError(
                    "external_authority_mismatch", "selection reference is invalid"
                )
            try:
                selection_bytes, _commit = read_committed_artifact(
                    workpad=resolved.path,
                    project_id=resolved.project_id,
                    gig_id=resolved.gig_id,
                    path=selection_path,
                )
            except Exception as exc:
                raise ExternalRecordingError(
                    "external_authority_mismatch",
                    "selection is not committed authority",
                ) from exc
            selection_ref = _ref(selection_path, selection_bytes)
            if selection_ref != dict(selection_ref_value):
                raise ExternalRecordingError(
                    "external_authority_mismatch", "selection reference changed"
                )
            selection = _json(selection_bytes, "external_authority_mismatch")
            if (
                not validate_selection_record(
                    selection_bytes,
                    graph_set=authority["graph_set"],
                    gig_id=resolved.gig_id,
                    gig_version=authority["version"],
                    root=resolved.path,
                ).valid
                or selection.get("selected_graph_id") != descriptor["graph_id"]
            ):
                raise ExternalRecordingError(
                    "external_authority_mismatch",
                    "sealed selection does not match approved Graph Set",
                )
        output_contract = _approved_ref(
            resolved, descriptor["output_contract"], "external_authority_mismatch"
        )
        check_contract = _approved_ref(
            resolved,
            descriptor["completion_evidence_contract"],
            "external_authority_mismatch",
        )
        approved_outputs = _approved_fields(
            resolved,
            output_contract,
            "run_output_contract",
            allow_domains=True,
        )
        _approved_fields(resolved, check_contract, "completion_evidence_contract")
        if set(typed["output_kinds"]) != approved_outputs:
            raise ExternalRecordingError(
                "external_authority_mismatch",
                "requested output kinds do not match the approved output contract",
            )
        identity = {
            "mode": "external_agent_recording",
            "project_id": resolved.project_id,
            "gig_id": resolved.gig_id,
            "gig_version": authority["version"],
            "graph_set": authority["graph_set_ref"],
            "selected_graph_id": descriptor["graph_id"],
            "selected_graph": descriptor["goal_graph"],
            "selection_record": selection_ref,
            "inputs": inputs,
            "output_contract": output_contract,
            "check_contract": check_contract,
            "effects": ["write_workpad"],
            "limits": LIMITS,
            "predecessor": typed["predecessor"],
            "invocation_payload_sha256": invocation["payload_sha256"],
        }
        if tailoring_request is not None:
            identity["tailoring_request"] = tailoring_request
        plan_id = derive_deterministic_id("run_plan", identity)
        path = f"run-plans/{plan_id}/external-plan.json"
        prior = snap.artifacts.get(path)
        if prior is not None:
            previous = _recorded_dispatch(
                prior,
                v1_schema="external-recording-plan.schema.json",
                v2_schema="external-recording-plan-v2.schema.json",
                code="external_reconciliation_required",
            )
            if (
                previous.get("invocation", {}).get("payload_sha256")
                == invocation["payload_sha256"]
            ):
                _require_replay_protocol(previous, invocation)
                return ExternalResult(previous, False)
            raise ExternalRecordingError(
                "external_operation_conflict",
                "operation key already records a different plan",
            )
        plan_payload = {
            "schema_version": "2.0" if protocol_version == 2 else "1.0",
            "run_plan_id": plan_id,
            "mode": "external_agent_recording",
            "project_id": resolved.project_id,
            "gig_id": resolved.gig_id,
            "gig_version": authority["version"],
            "journal_commit": snap.head,
            "graph_set": authority["graph_set_ref"],
            "selected_graph_id": descriptor["graph_id"],
            "goal_graph_id": graph["graph_id"],
            "selected_graph": descriptor["goal_graph"],
            "selection_record": selection_ref,
            "invocation": invocation,
            "inputs": inputs,
            "output_contract": output_contract,
            "check_contract": check_contract,
            "effects": ["write_workpad"],
            "limits": LIMITS,
            "predecessor": typed["predecessor"],
            "state": "sealed",
            "created_at": _now(),
            "sealed_at": _now(),
        }
        if tailoring_request is not None:
            plan_payload["tailoring_request"] = tailoring_request
        raw = canonical_json_bytes(plan_payload)
        _schema(
            "external-recording-plan-v2.schema.json"
            if protocol_version == 2
            else "external-recording-plan.schema.json",
            plan_payload,
        )
        artifacts = (
            (JournalArtifact(path, raw),)
            if selection_artifact is None
            else (selection_artifact, JournalArtifact(path, raw))
        )
        _journaled(
            writer,
            transition="external_recording_plan_sealed",
            handoff_id=_id("handoff", uuid_factory),
            body=f"External recording Plan {plan_id} sealed.",
            artifacts=artifacts,
        )
        return ExternalResult(plan_payload, True)

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=write,
    )


def plan_v2(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ExternalResult:
    """Seal a role-request-capable Plan with the strict v2 envelope."""
    return plan(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        envelope=envelope,
        uuid_factory=uuid_factory,
        protocol_version=2,
    )


def _read_plan(
    snapshot: JournalSnapshot, run_plan_id: str
) -> tuple[dict[str, object], str, bytes]:
    path = f"run-plans/{run_plan_id}/external-plan.json"
    data = snapshot.artifacts.get(path)
    if data is None:
        if f"run-plans/{run_plan_id}/run-plan.json" in snapshot.artifacts:
            raise ExternalRecordingError(
                "external_authority_mismatch",
                "managed Run Plans are not external recording authority",
            )
        raise ExternalRecordingError(
            "external_record_not_found", "external recording Plan was not found"
        )
    return (
        _recorded_dispatch(
            data,
            v1_schema="external-recording-plan.schema.json",
            v2_schema="external-recording-plan-v2.schema.json",
            code="external_authority_mismatch",
        ),
        path,
        data,
    )


def start(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    protocol_version: int = 1,
) -> ExternalResult:
    resolved = _resolved(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id
    )
    invocation = _invocation(
        operation="start", resolved=resolved, envelope=envelope, factory=uuid_factory,
        protocol_version=protocol_version,
    )
    if set(invocation["input"]) != {"run_plan_id"} or not isinstance(
        invocation["input"].get("run_plan_id"), str
    ):
        raise ExternalRecordingError(
            "external_invocation_invalid", "start input is invalid"
        )

    def write(writer):
        snap = writer.snapshot(
            ("references/", "run-inputs/", "records/", "run-plans/", "runs/")
        )
        replay = _replay(snap, invocation)
        if replay is not None:
            return ExternalResult(replay, False)
        plan_payload, plan_path, plan_bytes = _read_plan(
            snap, str(invocation["input"]["run_plan_id"])
        )
        if (plan_payload.get("schema_version") == "2.0") != (protocol_version == 2):
            raise ExternalRecordingError(
                "external_protocol_downgrade",
                "Plan protocol version does not match this operation",
                next_action="use_matching_external_protocol",
            )
        if (
            plan_payload.get("project_id") != resolved.project_id
            or plan_payload.get("gig_id") != resolved.gig_id
            or plan_payload.get("state") != "sealed"
        ):
            raise ExternalRecordingError(
                "external_authority_mismatch", "Plan scope or state is invalid"
            )
        # Re-resolve every sealed source at this same lock; no stale/symlinked
        # working copy can be redeemed merely because an old Plan exists.
        authority, graph, descriptor = _authority(
            resolved, str(plan_payload["selected_graph_id"])
        )
        if (
            authority["version"] != plan_payload["gig_version"]
            or graph.get("graph_id") != plan_payload["goal_graph_id"]
        ):
            raise ExternalRecordingError(
                "external_authority_mismatch",
                "approved graph authority no longer matches Plan",
            )
        for item in plan_payload.get("inputs", []):
            _revalidate_input(
                resolved,
                snap,
                item,
                allow_role_request=protocol_version == 2,
                allow_research_run=protocol_version == 2,
                allow_discovery_posting=protocol_version == 2,
                writer=writer,
            )
        if plan_payload.get("selected_graph_id") == "tailor-application":
            _tailoring_request_bytes(
                snap, plan_payload.get("inputs", []), plan_payload.get("tailoring_request")
            )
        run_id = derive_deterministic_id(
            "run",
            {
                "run_plan": digest_imported_bytes(plan_bytes),
                "operation": invocation["payload_sha256"],
            },
        )
        path = f"runs/{run_id}/external-run.json"
        if path in snap.artifacts:
            return ExternalResult(
                _recorded_dispatch(
                    snap.artifacts[path],
                    v1_schema="external-recording-run.schema.json",
                    v2_schema="external-recording-run-v2.schema.json",
                    code="external_reconciliation_required",
                ),
                False,
            )
        payload = {
            "schema_version": "2.0" if protocol_version == 2 else "1.0",
            "run_id": run_id,
            "run_plan": _ref(plan_path, plan_bytes),
            "invocation": invocation,
            "mode": "external_agent_recording",
            "project_id": resolved.project_id,
            "gig_id": resolved.gig_id,
            "gig_version": plan_payload["gig_version"],
            "status": "active",
            "started_at": _now(),
        }
        raw = canonical_json_bytes(payload)
        _schema(
            "external-recording-run-v2.schema.json"
            if protocol_version == 2
            else "external-recording-run.schema.json",
            payload,
        )
        _journaled(
            writer,
            transition="external_recording_started",
            handoff_id=_id("handoff", uuid_factory),
            body=f"External recording Run {run_id} started.",
            artifacts=(JournalArtifact(path, raw),),
        )
        return ExternalResult(payload, True)

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=write,
    )


def start_v2(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ExternalResult:
    """Start a Run bound to a strict v2 Plan and invocation."""
    return start(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        envelope=envelope,
        uuid_factory=uuid_factory,
        protocol_version=2,
    )


def _read_run(
    snapshot: JournalSnapshot, run_id: str
) -> tuple[dict[str, object], str, bytes]:
    path = f"runs/{run_id}/external-run.json"
    data = snapshot.artifacts.get(path)
    if data is None:
        raise ExternalRecordingError(
            "external_record_not_found", "external recording Run was not found"
        )
    return (
        _recorded_dispatch(
            data,
            v1_schema="external-recording-run.schema.json",
            v2_schema="external-recording-run-v2.schema.json",
            code="external_reconciliation_required",
        ),
        path,
        data,
    )


def _read_checkpoint_record(snapshot: JournalSnapshot, path: str) -> dict[str, object]:
    data = snapshot.artifacts.get(path)
    if data is None:
        raise ExternalRecordingError(
            "external_record_not_found", "external recording checkpoint was not found"
        )
    return _recorded_dispatch(
        data,
        v1_schema="external-recording-checkpoint.schema.json",
        v2_schema="external-recording-checkpoint-v2.schema.json",
        code="external_reconciliation_required",
    )


def _run_receipts(snapshot: JournalSnapshot, run_id: str) -> list[dict[str, object]]:
    prefix = f"runs/{run_id}/receipts/"
    return [
        _recorded_dispatch(
            data,
            v1_schema="external-recording-receipt.schema.json",
            v2_schema="external-recording-receipt-v2.schema.json",
            code="external_reconciliation_required",
        )
        for path, data in snapshot.artifacts.items()
        if path.startswith(prefix) and path.endswith(".json")
    ]


def _terminal(snapshot: JournalSnapshot, run_id: str) -> bool:
    return any(
        item.get("outcome") in {"succeeded", "cancelled"}
        for item in _run_receipts(snapshot, run_id)
    )


def _require_replay_protocol(
    recorded: Mapping[str, object], invocation: Mapping[str, object]
) -> None:
    """Keep old replay digests intact without returning a foreign wire version."""
    previous = recorded.get("invocation")
    if (
        recorded.get("schema_version") != invocation.get("schema_version")
        or not isinstance(previous, Mapping)
        or previous.get("schema_version") != invocation.get("schema_version")
    ):
        raise ExternalRecordingError(
            "external_protocol_downgrade",
            "recorded operation protocol version does not match this operation",
            next_action="use_matching_external_protocol",
        )


def _replay(
    snapshot: JournalSnapshot, invocation: Mapping[str, object]
) -> dict[str, object] | None:
    for path, data in snapshot.artifacts.items():
        if path.startswith("run-plans/") and path.endswith("/external-plan.json"):
            schema = "external-recording-plan.schema.json"
        elif path.endswith("/external-run.json"):
            schema = "external-recording-run.schema.json"
        elif "/checkpoints/" in path and path.endswith(".json"):
            schema = "external-recording-checkpoint.schema.json"
        elif "/receipts/" in path and path.endswith(".json"):
            schema = "external-recording-receipt.schema.json"
        else:
            continue
        payload = _recorded_dispatch(
            data,
            v1_schema={
                "external-recording-plan.schema.json": "external-recording-plan.schema.json",
                "external-recording-run.schema.json": "external-recording-run.schema.json",
                "external-recording-checkpoint.schema.json": "external-recording-checkpoint.schema.json",
                "external-recording-receipt.schema.json": "external-recording-receipt.schema.json",
            }[schema],
            v2_schema={
                "external-recording-plan.schema.json": "external-recording-plan-v2.schema.json",
                "external-recording-run.schema.json": "external-recording-run-v2.schema.json",
                "external-recording-checkpoint.schema.json": "external-recording-checkpoint-v2.schema.json",
                "external-recording-receipt.schema.json": "external-recording-receipt-v2.schema.json",
            }[schema],
            code="external_reconciliation_required",
        )
        previous = payload.get("invocation")
        if not isinstance(previous, Mapping):
            continue
        if (
            previous.get("operation") != invocation["operation"]
            or previous.get("operation_key") != invocation["operation_key"]
        ):
            continue
        if previous.get("payload_sha256") == invocation["payload_sha256"]:
            _require_replay_protocol(payload, invocation)
            return payload
        raise ExternalRecordingError(
            "external_operation_conflict",
            "operation key already records a different payload",
        )
    return None


def _checkpoints(snapshot: JournalSnapshot, run_id: str) -> list[dict[str, object]]:
    prefix = f"runs/{run_id}/checkpoints/"
    result = [
        _recorded_dispatch(
            data,
            v1_schema="external-recording-checkpoint.schema.json",
            v2_schema="external-recording-checkpoint-v2.schema.json",
            code="external_reconciliation_required",
        )
        for path, data in snapshot.artifacts.items()
        if path.startswith(prefix) and path.endswith(".json")
    ]
    return sorted(result, key=lambda item: int(item["sequence"]))


def _plan_for_run(
    snapshot: JournalSnapshot, run: Mapping[str, object]
) -> dict[str, object]:
    plan_ref, plan_bytes = _safe_ref(
        snapshot, run.get("run_plan"), code="external_authority_mismatch"
    )
    if not str(plan_ref["path"]).startswith("run-plans/"):
        raise ExternalRecordingError(
            "external_authority_mismatch", "Run Plan family is invalid"
        )
    return _recorded_dispatch(
        plan_bytes,
        v1_schema="external-recording-plan.schema.json",
        v2_schema="external-recording-plan-v2.schema.json",
        code="external_authority_mismatch",
    )


def _graph_context(
    resolved, plan: Mapping[str, object]
) -> tuple[str, str, int]:
    """Return graph identity from the Plan's authenticated graph bytes."""
    graph_ref, graph_bytes = _committed_ref(
        resolved, plan.get("selected_graph"), "external_authority_mismatch"
    )
    if not str(graph_ref["path"]).startswith("manifests/"):
        raise ExternalRecordingError(
            "external_authority_mismatch", "sealed graph reference is invalid"
        )
    graph = _json(graph_bytes, "external_authority_mismatch")
    graph_id = graph.get("graph_id")
    graph_version = graph.get("graph_version")
    if (
        graph_id != plan.get("goal_graph_id")
        or type(graph_version) is not int
        or graph_version < 1
        or not isinstance(plan.get("selected_graph_id"), str)
    ):
        raise ExternalRecordingError(
            "external_authority_mismatch", "sealed graph identity is invalid"
        )
    return str(plan["selected_graph_id"]), str(graph_id), graph_version


def _requested_output_kinds(
    resolved, plan: Mapping[str, object], *, protocol_version: int = 1
) -> set[str]:
    contract = plan.get("output_contract")
    if not isinstance(contract, Mapping):
        raise ExternalRecordingError(
            "external_authority_mismatch", "sealed output contract is invalid"
        )
    return _approved_fields(
        resolved,
        contract,
        "run_output_contract",
        allow_domains=protocol_version == 2,
    )


def _seal_tailoring_request(
    snapshot: JournalSnapshot,
    selected_inputs: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Identify exactly one canonical request among explicit G45 Run inputs."""
    candidates: list[tuple[int, Mapping[str, object]]] = []
    seen_run_inputs: set[str] = set()
    for index, selected in enumerate(selected_inputs):
        if not isinstance(selected, Mapping) or selected.get("family") != "g45_run_input":
            continue
        run_input_id = selected.get("run_input_id")
        if not isinstance(run_input_id, str) or run_input_id in seen_run_inputs:
            raise ExternalRecordingError(
                "tailoring_input_mismatch", "tailoring Plan repeats a G45 Run input"
            )
        seen_run_inputs.add(run_input_id)
        ref = selected.get("snapshot_ref")
        _checked, data = _safe_ref(snapshot, ref, code="external_input_mismatch")
        try:
            request = validate_tailoring_request(data)
        except Exception as exc:
            # Ordinary posting/candidate inputs are not request candidates;
            # fixed request-shaped bytes are admitted only when the validator
            # accepts the complete canonical contract.
            if isinstance(exc, ExternalRecordingError):
                raise
            if getattr(exc, "code", None) in {"tailoring_input_invalid", "tailoring_domain_invalid"}:
                try:
                    parsed = parse_json_bytes(data)
                except ValueError:
                    parsed = None
                if isinstance(parsed, Mapping) and (
                    parsed.get("schema_version") == "scout-tailoring-request:1"
                    or {"requested_outputs", "source_roles", "requirements", "claim_evidence"} <= set(parsed)
                ):
                    raise ExternalRecordingError(
                        "tailoring_input_invalid", "tailoring request nested shape is invalid"
                    ) from exc
                continue
            continue
        candidates.append((index, selected))
        roles = request["source_roles"]
        role_indices = {
            int(entry["input_index"])
            for role in ("posting", "candidate_evidence")
            for entry in roles[role]
        }
        if index in role_indices:
            raise ExternalRecordingError(
                "tailoring_input_mismatch",
                "tailoring request input is reused as source evidence",
            )
        if any(input_index >= len(selected_inputs) for input_index in role_indices):
            raise ExternalRecordingError(
                "tailoring_input_mismatch", "tailoring source role index is outside the Plan"
            )
    if not candidates:
        raise ExternalRecordingError(
            "tailoring_input_missing",
            "tailor-application requires one canonical G45 tailoring request input",
        )
    if len(candidates) != 1:
        raise ExternalRecordingError(
            "tailoring_input_mismatch",
            "tailor-application requires exactly one canonical tailoring request input",
        )
    index, selected = candidates[0]
    return {
        "input_index": index,
        "run_input_id": selected["run_input_id"],
        "record_ref": dict(selected["record_ref"]),
        "snapshot_ref": dict(selected["snapshot_ref"]),
    }


def _tailoring_request_bytes(
    snapshot: JournalSnapshot,
    selected_inputs: Sequence[Mapping[str, object]],
    request_ref: object,
) -> bytes:
    """Redeem only the request identity sealed by the tailoring Plan."""
    if not isinstance(request_ref, Mapping) or set(request_ref) != {
        "input_index", "run_input_id", "record_ref", "snapshot_ref"
    }:
        raise ExternalRecordingError(
            "tailoring_input_missing", "tailoring Plan lacks its sealed request reference"
        )
    index = request_ref.get("input_index")
    if type(index) is not int or index < 0 or index >= len(selected_inputs):
        raise ExternalRecordingError("tailoring_input_mismatch", "tailoring request index is invalid")
    selected = selected_inputs[index]
    if (
        not isinstance(selected, Mapping)
        or selected.get("family") != "g45_run_input"
        or selected.get("run_input_id") != request_ref.get("run_input_id")
        or selected.get("record_ref") != request_ref.get("record_ref")
        or selected.get("snapshot_ref") != request_ref.get("snapshot_ref")
    ):
        raise ExternalRecordingError(
            "external_input_mismatch", "sealed tailoring request identity changed"
        )
    checked, data = _safe_ref(snapshot, selected.get("snapshot_ref"), code="external_input_mismatch")
    if checked != dict(request_ref["snapshot_ref"]):
        raise ExternalRecordingError(
            "external_input_mismatch", "sealed tailoring request snapshot changed"
        )
    try:
        validate_tailoring_request(data)
    except Exception as exc:
        code = getattr(exc, "code", "tailoring_input_invalid")
        raise ExternalRecordingError(code, "sealed tailoring request changed or is invalid") from exc
    return data


def _validate_domain_binding(
    resolved,
    plan: Mapping[str, object],
    output_kind: str,
) -> None:
    """Authenticate the contract's exact packaged schema/source resources."""
    contract = _approved_contract(resolved, plan.get("output_contract"))["content"]
    domains = contract.get("domains")
    if not isinstance(domains, Mapping) or not isinstance(domains.get(output_kind), Mapping):
        raise ExternalRecordingError(
            "external_authority_mismatch", "v2 domain binding is missing"
        )
    domain = domains[output_kind]
    schema_ref, schema_bytes = _committed_ref(
        resolved, domain.get("schema_ref"), "external_authority_mismatch"
    )
    source_ref, source_bytes = _committed_ref(
        resolved, domain.get("validator_source_ref"), "external_authority_mismatch"
    )
    specs = {
        RESEARCH_DOMAIN_SCHEMA_ID: (RESEARCH_VALIDATOR_REF, RESEARCH_VALIDATOR_SOURCE, "research.schema.json", "research.py", "scout_research"),
        RESEARCH_V3_DOMAIN_SCHEMA_ID: (RESEARCH_V3_VALIDATOR_REF, RESEARCH_V3_VALIDATOR_SOURCE, "research.schema.json", "research.py", "scout_research_v3"),
        DISCOVERY_DOMAIN_SCHEMA_ID: (DISCOVERY_VALIDATOR_REF, DISCOVERY_VALIDATOR_SOURCE, "discovery.schema.json", "discovery.py", "scout_discovery"),
        TAILORING_DOMAIN_SCHEMA_ID: (TAILORING_VALIDATOR_REF, TAILORING_VALIDATOR_SOURCE, "tailoring.schema.json", "tailoring.py", "scout_tailoring"),
    }
    spec = specs.get(domain.get("schema_id"))
    if (
        spec is None
        or domain.get("validator_id") != spec[0]
        or not str(schema_ref["path"]).endswith(spec[2])
        or not str(source_ref["path"]).endswith(spec[3])
    ):
        raise ExternalRecordingError(
            "external_authority_mismatch",
            "v2 domain binding does not name packaged resources",
        )
    if domain.get("schema_id") == RESEARCH_DOMAIN_SCHEMA_ID:
        from .scout_research import FIXED_DOMAIN_RESOURCES
    elif domain.get("schema_id") == RESEARCH_V3_DOMAIN_SCHEMA_ID:
        from .scout_research_v3 import FIXED_DOMAIN_RESOURCES
    elif domain.get("schema_id") == DISCOVERY_DOMAIN_SCHEMA_ID:
        from .scout_discovery import FIXED_DOMAIN_RESOURCES
    elif domain.get("schema_id") == TAILORING_DOMAIN_SCHEMA_ID:
        from .scout_tailoring import FIXED_DOMAIN_RESOURCES
    else:  # pragma: no cover - specs guard above
        raise ExternalRecordingError(
            "external_domain_validator_unavailable",
            "fixed domain validator is not installed",
            next_action="install_reviewed_domain_bridge",
        )
    if (
        not isinstance(FIXED_DOMAIN_RESOURCES, Mapping)
        or FIXED_DOMAIN_RESOURCES.get("schema_id") != domain.get("schema_id")
        or FIXED_DOMAIN_RESOURCES.get("validator_id") != spec[0]
        or FIXED_DOMAIN_RESOURCES.get("validator_source") != spec[1]
    ):
        raise ExternalRecordingError(
            "external_domain_validator_unavailable",
            "fixed domain resource identity is not recognized",
            next_action="install_reviewed_domain_bridge",
        )
    try:
        trusted_schema = resources.files("gigai").joinpath(
            *str(FIXED_DOMAIN_RESOURCES["schema_resource"]).split("/")
        ).read_bytes()
        trusted_source = resources.files("gigai").joinpath(
            *str(FIXED_DOMAIN_RESOURCES["schema_resource"]).split("/")[:-1],
            spec[3],
        ).read_bytes()
    except (AttributeError, FileNotFoundError, OSError) as exc:
        raise ExternalRecordingError(
            "external_domain_validator_unavailable",
            "fixed domain resource is unavailable",
            next_action="install_reviewed_domain_bridge",
        ) from exc
    if schema_bytes != trusted_schema or source_bytes != trusted_source:
        raise ExternalRecordingError(
            "external_authority_mismatch",
            "v2 domain resources differ from the packaged validator",
        )


def _historical_research_inputs(
    snapshot: JournalSnapshot, selected_inputs: object
) -> list[dict[str, object]]:
    """Redeem exact prior research output bytes for the v3 bridge.

    The selector was already revalidated under this writer.  This second,
    byte-level handoff keeps the domain bridge independent of workpad paths
    and prevents it from treating a reference-only selector as evidence.
    """
    if not isinstance(selected_inputs, list):
        return []
    result: list[dict[str, object]] = []
    for item in selected_inputs:
        if not isinstance(item, Mapping) or item.get("family") != "scout_research":
            continue
        output = item.get("output")
        if not isinstance(output, Mapping):
            raise ExternalRecordingError(
                "external_domain_invalid", "historical research output is malformed"
            )
        _markdown_ref, markdown = _safe_ref(
            snapshot, output.get("markdown"), code="external_domain_invalid"
        )
        _sidecar_ref, sidecar_bytes = _safe_ref(
            snapshot, output.get("sidecar"), code="external_domain_invalid"
        )
        _domain_ref, domain_bytes = _safe_ref(
            snapshot, output.get("domain_sidecar"), code="external_domain_invalid"
        )
        try:
            sidecar = _json(sidecar_bytes, "external_domain_invalid")
            domain = _json(domain_bytes, "external_domain_invalid")
        except ExternalRecordingError:
            raise
        supporting: dict[str, bytes] = {}
        raw_supporting = output.get("supporting_artifacts")
        if not isinstance(raw_supporting, list):
            raise ExternalRecordingError(
                "external_domain_invalid", "historical research supporting evidence is malformed"
            )
        for entry in raw_supporting:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("artifact_id"), str):
                raise ExternalRecordingError(
                    "external_domain_invalid", "historical research supporting ref is malformed"
                )
            _support_ref, supporting_bytes = _safe_ref(
                snapshot, entry.get("ref"), code="external_domain_invalid"
            )
            supporting[str(entry["artifact_id"])] = supporting_bytes
        result.append(
            {
                "selector": dict(item),
                "markdown": markdown,
                "sidecar": sidecar,
                "domain": domain,
                # The selected tuple was fully resolved and authenticated by
                # scout_research_inputs under this writer.  Carry its fixed
                # domain identity to the bridge so historical integrity
                # validation never dispatches from an untrusted sidecar.
                "domain_schema_id": item.get("domain_binding", {}).get("schema_id")
                if isinstance(item.get("domain_binding"), Mapping)
                else None,
                "supporting": supporting,
            }
        )
    return result


def _required_check_kinds(resolved, plan: Mapping[str, object]) -> set[str]:
    contract = plan.get("check_contract")
    if not isinstance(contract, Mapping):
        raise ExternalRecordingError(
            "external_authority_mismatch", "sealed completion contract is invalid"
        )
    return _approved_fields(resolved, contract, "completion_evidence_contract")


def _output_sidecar_is_valid(
    sidecar: Mapping[str, object],
    *,
    run_id: str,
    inputs: object,
    kind: str,
    markdown: bytes,
) -> bool:
    return (
        set(sidecar) == {"document_sha256", "output_kind", "run_id", "selected_inputs"}
        and sidecar.get("run_id") == run_id
        and sidecar.get("output_kind") == kind
        and sidecar.get("document_sha256") == digest_imported_bytes(markdown)
        and sidecar.get("selected_inputs") == inputs
    )


def _check_sidecar_is_valid(
    sidecar: Mapping[str, object], *, run_id: str, kind: str
) -> bool:
    digest = sidecar.get("output_sha256")
    return (
        set(sidecar) == {"evidence_kind", "run_id", "output_sha256", "result"}
        and sidecar.get("evidence_kind") == kind
        and sidecar.get("run_id") == run_id
        and sidecar.get("result") in {"pass", "fail"}
        and isinstance(digest, str)
        and digest.startswith("sha256:")
        and len(digest) == 71
    )


def _validate_domain_input(
    item: Mapping[str, object], *, markdown: bytes
) -> tuple[dict[str, object], dict[str, bytes]]:
    """Validate the generic v2 domain channel and decode bounded bytes.

    Domain meaning belongs to one fixed packaged validator.  This function
    only authenticates transport shape, canonical base64, and content refs;
    it never executes source or trusts an agent-declared callback.
    """
    domain = item.get("domain_sidecar")
    supporting = item.get("supporting_artifacts")
    if (
        not isinstance(domain, Mapping)
        or set(domain) != {"schema_id", "value"}
        or not isinstance(domain.get("schema_id"), str)
        or not isinstance(domain.get("value"), Mapping)
        or not isinstance(supporting, list)
        or len(supporting) > LIMITS["max_artifacts_per_operation"]
    ):
        raise ExternalRecordingError(
            "external_domain_invalid", "typed domain evidence is malformed"
        )
    schema_id = str(domain["schema_id"])
    if schema_id not in _FIXED_DOMAIN_VALIDATORS:
        raise ExternalRecordingError(
            "external_domain_unsupported",
            "typed domain validator is not recognized",
            next_action="use_supported_external_domain",
        )
    decoded: dict[str, bytes] = {}
    for raw in supporting:
        if (
            not isinstance(raw, Mapping)
            or set(raw)
            != {"artifact_id", "media_type", "content_base64", "content_sha256", "size_bytes"}
            or not isinstance(raw.get("artifact_id"), str)
            or not isinstance(raw.get("media_type"), str)
            or not isinstance(raw.get("content_base64"), str)
            or not isinstance(raw.get("content_sha256"), str)
            or type(raw.get("size_bytes")) is not int
        ):
            raise ExternalRecordingError(
                "external_domain_invalid", "supporting artifact encoding is malformed"
            )
        artifact_id = str(raw["artifact_id"])
        if (
            not artifact_id
            or len(artifact_id) > _DOMAIN_ID_LIMIT
            or not artifact_id.replace("_", "").replace("-", "").isalnum()
            or artifact_id in decoded
            or "/" in artifact_id
            or "\\" in artifact_id
        ):
            raise ExternalRecordingError(
                "external_domain_invalid", "supporting artifact identifier is unsafe"
            )
        encoded = str(raw["content_base64"])
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ExternalRecordingError(
                "external_domain_invalid", "supporting artifact encoding is invalid"
            ) from exc
        if (
            base64.b64encode(content).decode("ascii") != encoded
            or len(content) > LIMITS["max_artifact_bytes"]
            or raw["size_bytes"] != len(content)
            or digest_imported_bytes(content) != raw["content_sha256"]
            or not raw["media_type"]
            or len(raw["media_type"]) > 255
        ):
            raise ExternalRecordingError(
                "external_domain_invalid", "supporting artifact bytes do not match their ref"
            )
        decoded[artifact_id] = content
    return {"schema_id": schema_id, "value": dict(domain["value"])}, decoded


def _validate_fixed_domain(
    domain: Mapping[str, object],
    *,
    markdown: bytes,
    supporting: Mapping[str, bytes],
    run_id: str | None = None,
    project_id: str | None = None,
    gig_id: str | None = None,
    gig_version: object = None,
    graph_selector: str | None = None,
    graph_id: str | None = None,
    graph_version: object = None,
    selected_inputs: object = None,
    preference_bytes: Mapping[str, bytes] | None = None,
    historical_inputs: Sequence[Mapping[str, object]] = (),
    source_bytes: Mapping[str, bytes] | None = None,
    request_bytes: bytes | None = None,
) -> None:
    schema_id = domain.get("schema_id")
    validator = _FIXED_DOMAIN_VALIDATORS.get(schema_id)
    if validator is None:
        raise ExternalRecordingError(
            "external_domain_unsupported", "typed domain validator is not recognized"
        )
    kwargs: dict[str, object] = dict(
        value=domain["value"],
        markdown=markdown,
        supporting=supporting,
        run_id=run_id,
        project_id=project_id,
        gig_id=gig_id,
        gig_version=gig_version,
        graph_selector=graph_selector,
        graph_id=graph_id,
        graph_version=graph_version,
        selected_inputs=selected_inputs,
    )
    if schema_id == DISCOVERY_DOMAIN_SCHEMA_ID:
        kwargs["preference_bytes"] = preference_bytes or {}
    if schema_id == RESEARCH_V3_DOMAIN_SCHEMA_ID:
        kwargs["historical_inputs"] = historical_inputs
    if schema_id == TAILORING_DOMAIN_SCHEMA_ID:
        kwargs["source_bytes"] = source_bytes or {}
        kwargs["request_bytes"] = request_bytes
    validator(**kwargs)


def _preference_bytes(snapshot: JournalSnapshot, selected_inputs: object) -> dict[str, bytes]:
    """Extract the authenticated profile blob required by the discovery bridge."""
    if not isinstance(selected_inputs, list):
        return {}
    result: dict[str, bytes] = {}
    for item in selected_inputs:
        if not isinstance(item, Mapping) or item.get("native_kind") != "profile_preferences":
            continue
        content = item.get("content")
        ref = content.get("blob_ref") if isinstance(content, Mapping) else None
        path = ref.get("path") if isinstance(ref, Mapping) else None
        if isinstance(path, str) and path in snapshot.artifacts:
            result[path] = snapshot.artifacts[path]
    return result


def _domain_refs(
    *, root: str, domain: Mapping[str, object], supporting: Mapping[str, bytes]
) -> tuple[JournalArtifact, dict[str, object], list[dict[str, object]]]:
    domain_bytes = canonical_json_bytes(dict(domain))
    domain_path = f"{root}.domain.json"
    domain_ref = _ref(domain_path, domain_bytes)
    published = [JournalArtifact(domain_path, domain_bytes)]
    refs: list[dict[str, object]] = []
    for artifact_id, content in supporting.items():
        path = f"{root}.supporting/{artifact_id}.bin"
        ref = _ref(path, content, "application/octet-stream")
        refs.append({"artifact_id": artifact_id, "ref": ref})
        published.append(JournalArtifact(path, content))
    return tuple(published), domain_ref, refs


def _selected_snapshot_ref(item: Mapping[str, object]) -> Mapping[str, object] | None:
    """Return the G45 imported snapshot ref nested in a sealed input."""
    family = item.get("family")
    if family in {"g45_reference", "g45_run_input"}:
        ref = item.get("snapshot_ref")
    elif family == "scout_record":
        content = item.get("content")
        ref = content.get("snapshot_ref") if isinstance(content, Mapping) else None
    else:
        ref = None
    return ref if isinstance(ref, Mapping) else None


def _tailoring_source_bytes(
    snapshot: JournalSnapshot, selected_inputs: object, domain_value: Mapping[str, object]
) -> dict[str, bytes]:
    """Resolve source roles to exact committed G45 bytes under the writer lock."""
    if not isinstance(selected_inputs, list):
        raise ExternalRecordingError("external_domain_invalid", "tailoring selected inputs are invalid")
    roles = domain_value.get("source_roles")
    if not isinstance(roles, Mapping) or set(roles) != {"posting", "candidate_evidence"}:
        raise ExternalRecordingError("external_domain_invalid", "tailoring source roles are invalid")
    result: dict[str, bytes] = {}
    for role in ("posting", "candidate_evidence"):
        entries = roles.get(role)
        if not isinstance(entries, list) or not entries:
            raise ExternalRecordingError("external_domain_invalid", "tailoring source role is empty")
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {"source_id", "input_index"}:
                raise ExternalRecordingError("external_domain_invalid", "tailoring source role is invalid")
            source_id, input_index = entry.get("source_id"), entry.get("input_index")
            if (
                not isinstance(source_id, str)
                or type(input_index) is not int
                or input_index < 0
                or input_index >= len(selected_inputs)
                or source_id in result
            ):
                raise ExternalRecordingError("external_domain_invalid", "tailoring source role is invalid")
            selected = selected_inputs[input_index]
            if not isinstance(selected, Mapping):
                raise ExternalRecordingError("external_domain_invalid", "tailoring selected input is invalid")
            if selected.get("family") == "scout_discovery_posting":
                posting_ref = selected.get("posting_ref")
                if not isinstance(posting_ref, Mapping):
                    raise ExternalRecordingError("external_input_mismatch", "tailoring discovery posting provenance is unavailable")
                _checked, data = _safe_ref(
                    snapshot, posting_ref.get("ref"), code="external_input_mismatch"
                )
            else:
                ref = _selected_snapshot_ref(selected)
                if ref is None:
                    raise ExternalRecordingError("external_input_mismatch", "tailoring source is not an imported G45 snapshot")
                _checked, data = _safe_ref(snapshot, ref, code="external_input_mismatch")
            result[source_id] = data
    return result


def _safe_persisted_domain(
    snapshot: JournalSnapshot,
    output: Mapping[str, object],
    *,
    run_id: str,
    kind: str,
    markdown: bytes,
    project_id: str | None = None,
    gig_id: str | None = None,
    gig_version: object = None,
    graph_selector: str | None = None,
    graph_id: str | None = None,
    graph_version: object = None,
    selected_inputs: object = None,
    tailoring_request: object = None,
    historical_inputs: Sequence[Mapping[str, object]] = (),
) -> tuple[dict[str, object], dict[str, bytes]]:
    domain_ref, supporting_refs = output.get("domain_sidecar"), output.get(
        "supporting_artifacts"
    )
    checked_domain, domain_bytes = _safe_ref(
        snapshot, domain_ref, code="external_output_invalid"
    )
    if not str(checked_domain["path"]).startswith(f"runs/{run_id}/artifacts/"):
        raise ExternalRecordingError("external_scope_refused", "domain artifact belongs to another Run")
    domain_value = _json(domain_bytes, "external_output_invalid")
    if not isinstance(supporting_refs, list):
        raise ExternalRecordingError("external_output_invalid", "supporting refs are invalid")
    supporting: dict[str, bytes] = {}
    for item in supporting_refs:
        if not isinstance(item, Mapping) or set(item) != {"artifact_id", "ref"}:
            raise ExternalRecordingError("external_output_invalid", "supporting ref is invalid")
        artifact_id = item.get("artifact_id")
        if not isinstance(artifact_id, str) or artifact_id in supporting:
            raise ExternalRecordingError("external_output_invalid", "supporting ref identifier is invalid")
        ref, data = _safe_ref(snapshot, item.get("ref"), code="external_output_invalid")
        if not str(ref["path"]).startswith(f"runs/{run_id}/artifacts/") or not str(ref["path"]).endswith(f".supporting/{artifact_id}.bin"):
            raise ExternalRecordingError("external_scope_refused", "supporting artifact belongs to another Run")
        supporting[artifact_id] = data
    if set(domain_value) != {"schema_id", "value"} or not isinstance(domain_value.get("schema_id"), str) or not isinstance(domain_value.get("value"), Mapping):
        raise ExternalRecordingError("external_output_invalid", "persisted domain sidecar is invalid")
    domain = {"schema_id": domain_value["schema_id"], "value": dict(domain_value["value"])}
    source_bytes = None
    request_bytes = None
    if domain["schema_id"] == TAILORING_DOMAIN_SCHEMA_ID:
        source_bytes = _tailoring_source_bytes(snapshot, selected_inputs, domain["value"])
        request_bytes = _tailoring_request_bytes(snapshot, selected_inputs, tailoring_request)
    _validate_fixed_domain(
        domain,
        markdown=markdown,
        supporting=supporting,
        run_id=run_id,
        project_id=project_id,
        gig_id=gig_id,
        gig_version=gig_version,
        graph_selector=graph_selector,
        graph_id=graph_id,
        graph_version=graph_version,
        selected_inputs=selected_inputs,
        preference_bytes=_preference_bytes(snapshot, selected_inputs),
        historical_inputs=historical_inputs,
        source_bytes=source_bytes,
        request_bytes=request_bytes,
    )
    return domain, supporting


def checkpoint(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ExternalResult:
    return _progress(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        envelope=envelope,
        uuid_factory=uuid_factory,
        terminal=None,
        protocol_version=1,
    )


def checkpoint_v2(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ExternalResult:
    """Record one strict typed-domain checkpoint using the v2 wire shape."""
    return _progress(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        envelope=envelope,
        uuid_factory=uuid_factory,
        terminal=None,
        protocol_version=2,
    )


def cancel(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    protocol_version: int = 1,
) -> ExternalResult:
    return _progress(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        envelope=envelope,
        uuid_factory=uuid_factory,
        terminal="cancelled",
        protocol_version=protocol_version,
    )


def cancel_v2(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ExternalResult:
    """Cancel a v2 Run while preserving the versioned receipt shape."""
    return cancel(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        envelope=envelope,
        uuid_factory=uuid_factory,
        protocol_version=2,
    )


def _progress(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory,
    terminal: str | None,
    protocol_version: int = 1,
) -> ExternalResult:
    operation = "cancel" if terminal else "checkpoint"
    resolved = _resolved(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id
    )
    invocation = _invocation(
        operation=operation,
        resolved=resolved,
        envelope=envelope,
        factory=uuid_factory,
        protocol_version=protocol_version,
    )
    required = (
        {"run_id", "reason"}
        if terminal
        else {"run_id", "parent_checkpoint", "questions", "artifact_refs", "reason"}
    )
    if (
        set(invocation["input"]) != required
        or not isinstance(invocation["input"].get("run_id"), str)
        or not isinstance(invocation["input"].get("reason"), str)
    ):
        raise ExternalRecordingError(
            "external_invocation_invalid", f"{operation} input is invalid"
        )

    def write(writer):
        snap = writer.snapshot(
            (
                "references/",
                "run-inputs/",
                "records/",
                "records/external/",
                "run-plans/",
                "runs/",
            )
        )
        replay = _replay(snap, invocation)
        if replay is not None:
            return ExternalResult(replay, False)
        run, run_path, run_bytes = _read_run(snap, str(invocation["input"]["run_id"]))
        if (run.get("schema_version") == "2.0") != (protocol_version == 2):
            raise ExternalRecordingError(
                "external_protocol_downgrade",
                "Run protocol version does not match this operation",
                next_action="use_matching_external_protocol",
            )
        if _terminal(snap, str(run["run_id"])):
            raise ExternalRecordingError(
                "external_run_terminal", "terminal Run cannot accept more evidence"
            )
        if terminal:
            receipt = {
                "schema_version": "2.0" if protocol_version == 2 else "1.0",
                "receipt_id": _id("receipt", uuid_factory),
                "run_id": run["run_id"],
                "run_plan": run["run_plan"],
                "invocation": invocation,
                "operation_key": invocation["operation_key"],
                "payload_sha256": invocation["payload_sha256"],
                "outcome": "cancelled",
                "outputs": [],
                "checks": [],
                "disclosure": {"execution": "unobserved", "actor_report": "declared"},
                "created_at": _now(),
            }
            _schema(
                "external-recording-receipt-v2.schema.json"
                if protocol_version == 2
                else "external-recording-receipt.schema.json",
                receipt,
            )
            data = canonical_json_bytes(receipt)
            _journaled(
                writer,
                transition="external_recording_cancelled",
                handoff_id=_id("handoff", uuid_factory),
                body=f"External recording Run {run['run_id']} cancelled.",
                artifacts=(
                    JournalArtifact(
                        f"runs/{run['run_id']}/receipts/{receipt['receipt_id']}.json",
                        data,
                    ),
                ),
            )
            return ExternalResult(receipt, True)
        questions = invocation["input"]["questions"]
        artifact_inputs = invocation["input"]["artifact_refs"]
        if (
            not isinstance(questions, list)
            or not isinstance(artifact_inputs, list)
            or len(questions) > 32
            or len(artifact_inputs) > 32
        ):
            raise ExternalRecordingError(
                "external_limit_exceeded", "checkpoint exceeds recording limits"
            )
        if any(
            not isinstance(item, Mapping)
            or set(item) != {"id", "state", "prompt"}
            or item.get("state")
            not in {"missing", "answered", "declined", "not_applicable"}
            for item in questions
        ):
            raise ExternalRecordingError(
                "external_invocation_invalid", "checkpoint questions are invalid"
            )
        checkpoints = _checkpoints(snap, str(run["run_id"]))
        count = len(checkpoints)
        expected_parent = checkpoints[-1]["checkpoint_id"] if checkpoints else None
        if invocation["input"]["parent_checkpoint"] != expected_parent:
            raise ExternalRecordingError(
                "external_checkpoint_conflict",
                "checkpoint parent is not the current checkpoint",
            )
        if count >= 256:
            raise ExternalRecordingError(
                "external_limit_exceeded", "Run reached checkpoint limit"
            )
        checkpoint_id = _id("checkpoint", uuid_factory)
        published: list[JournalArtifact] = []
        artifacts: list[dict[str, object]] = []
        pending_checks: list[dict[str, object]] = []
        total = 0
        plan = _plan_for_run(snap, run)
        for sealed_input in plan.get("inputs", []):
            _revalidate_input(
                resolved,
                snap,
                sealed_input,
                allow_role_request=protocol_version == 2,
                allow_research_run=protocol_version == 2,
                allow_discovery_posting=protocol_version == 2,
                writer=writer,
            )
        historical_research_inputs = _historical_research_inputs(
            snap, plan.get("inputs")
        )
        requested_outputs = _requested_output_kinds(
            resolved, plan, protocol_version=protocol_version
        )
        if protocol_version == 2:
            graph_selector, graph_id, graph_version = _graph_context(resolved, plan)
        else:
            graph_selector, graph_id, graph_version = None, None, None
        requested_checks = _required_check_kinds(resolved, plan)
        for ordinal, item in enumerate(artifact_inputs, start=1):
            item_keys = set(item) if isinstance(item, Mapping) else set()
            is_domain_item = item_keys == {
                "kind",
                "markdown",
                "sidecar",
                "domain_sidecar",
                "supporting_artifacts",
            }
            is_check_item = item_keys == {"kind", "markdown", "sidecar"}
            if (
                not isinstance(item, Mapping)
                or (protocol_version == 1 and not is_check_item)
                or (protocol_version == 2 and not (is_domain_item or is_check_item))
                or not isinstance(item.get("kind"), str)
                or not isinstance(item.get("markdown"), str)
                or not isinstance(item.get("sidecar"), Mapping)
            ):
                raise ExternalRecordingError(
                    "external_invocation_invalid", "checkpoint artifact is invalid"
                )
            markdown = item["markdown"].encode("utf-8")
            sidecar = canonical_json_bytes(dict(item["sidecar"]))
            if (
                b"\x00" in markdown
                or len(markdown) > LIMITS["max_artifact_bytes"]
                or len(sidecar) > LIMITS["max_artifact_bytes"]
            ):
                raise ExternalRecordingError(
                    "external_limit_exceeded",
                    "checkpoint artifact exceeds recording limit",
                )
            total += len(markdown) + len(sidecar)
            root = f"runs/{run['run_id']}/artifacts/{checkpoint_id}/{ordinal:02d}"
            sidecar_value = dict(item["sidecar"])
            domain_value: dict[str, object] | None = None
            supporting: dict[str, bytes] = {}
            domain_published: tuple[JournalArtifact, ...] = ()
            domain_ref: dict[str, object] | None = None
            supporting_refs: list[dict[str, object]] = []
            if protocol_version == 2 and is_domain_item:
                domain_value, supporting = _validate_domain_input(item, markdown=markdown)
                _validate_domain_binding(resolved, plan, str(item["kind"]))
                source_bytes = None
                request_bytes = None
                if domain_value.get("schema_id") == TAILORING_DOMAIN_SCHEMA_ID:
                    source_bytes = _tailoring_source_bytes(snap, plan.get("inputs"), domain_value["value"])
                    request_bytes = _tailoring_request_bytes(
                        snap, plan.get("inputs"), plan.get("tailoring_request")
                    )
                _validate_fixed_domain(
                    domain_value,
                    markdown=markdown,
                    supporting=supporting,
                    run_id=str(run["run_id"]),
                    project_id=resolved.project_id,
                    gig_id=resolved.gig_id,
                    gig_version=plan.get("gig_version"),
                    graph_selector=graph_selector,
                    graph_id=graph_id,
                    graph_version=graph_version,
                    selected_inputs=plan.get("inputs"),
                    preference_bytes=_preference_bytes(snap, plan.get("inputs")),
                    historical_inputs=historical_research_inputs,
                    source_bytes=source_bytes,
                    request_bytes=request_bytes,
                )
                domain_published, domain_ref, supporting_refs = _domain_refs(
                    root=root, domain=domain_value, supporting=supporting
                )
                total += len(canonical_json_bytes(domain_value)) + sum(
                    len(content) for content in supporting.values()
                )
            is_output = (
                _output_sidecar_is_valid(
                    sidecar_value,
                    run_id=str(run["run_id"]),
                    inputs=plan.get("inputs"),
                    kind=str(item["kind"]),
                    markdown=markdown,
                )
                and item["kind"] in requested_outputs
            )
            is_check = (
                _check_sidecar_is_valid(
                    sidecar_value, run_id=str(run["run_id"]), kind=str(item["kind"])
                )
                and item["kind"] in requested_checks
            )
            if not is_output and not is_check:
                raise ExternalRecordingError(
                    "external_output_invalid",
                    "artifact does not satisfy a sealed output or check contract",
                )
            if protocol_version == 2 and is_output and not is_domain_item:
                raise ExternalRecordingError(
                    "external_domain_required",
                    "version-2 outputs require their sealed domain evidence",
                )
            if is_check:
                pending_checks.append(sidecar_value)
            markdown_ref, sidecar_ref = (
                _ref(f"{root}.md", markdown, "text/markdown"),
                _ref(f"{root}.json", sidecar),
            )
            artifacts.append(
                {
                    "kind": item["kind"],
                    "markdown": markdown_ref,
                    "sidecar": sidecar_ref,
                    **(
                        {
                            "domain_sidecar": domain_ref,
                            "supporting_artifacts": supporting_refs,
                        }
                        if protocol_version == 2 and is_domain_item
                        else {}
                    ),
                }
            )
            published.extend(
                (
                    JournalArtifact(str(markdown_ref["path"]), markdown),
                    JournalArtifact(str(sidecar_ref["path"]), sidecar),
                )
            )
            published.extend(domain_published)
        output_digests = {
            artifact["markdown"]["content_sha256"]
            for checkpoint in checkpoints
            for artifact in checkpoint.get("artifacts", [])
            if isinstance(artifact, Mapping)
            and isinstance(artifact.get("markdown"), Mapping)
        } | {
            artifact["markdown"]["content_sha256"]
            for artifact in artifacts
            if isinstance(artifact.get("markdown"), Mapping)
        }
        if any(
            check["output_sha256"] not in output_digests for check in pending_checks
        ):
            raise ExternalRecordingError(
                "external_output_invalid",
                "check evidence does not bind a recorded output",
            )
        if total > LIMITS["max_total_bytes_per_operation"]:
            raise ExternalRecordingError(
                "external_limit_exceeded",
                "checkpoint total artifact bytes exceed recording limit",
            )
        payload = {
            "schema_version": "2.0" if protocol_version == 2 else "1.0",
            "checkpoint_id": checkpoint_id,
            "run_id": run["run_id"],
            "run_plan": run["run_plan"],
            "invocation": invocation,
            "sequence": count + 1,
            "parent_checkpoint": invocation["input"]["parent_checkpoint"],
            "questions": questions,
            "artifacts": artifacts,
            "reason": invocation["input"]["reason"],
            "created_at": _now(),
        }
        data = canonical_json_bytes(payload)
        _schema(
            "external-recording-checkpoint-v2.schema.json"
            if protocol_version == 2
            else "external-recording-checkpoint.schema.json",
            payload,
        )
        transition = (
            "external_recording_waiting_input"
            if any(item["state"] == "missing" for item in questions)
            else "external_recording_checkpointed"
        )

        def revalidate_domains() -> None:
            for kind in sorted({
                str(item["kind"]) for item in artifacts if "domain_sidecar" in item
            }):
                _validate_domain_binding(resolved, plan, kind)

        _journaled(
            writer,
            transition=transition,
            handoff_id=_id("handoff", uuid_factory),
            body=f"External recording checkpoint {checkpoint_id} recorded.",
            revalidate=revalidate_domains if protocol_version == 2 else None,
            artifacts=tuple(
                published
                + [
                    JournalArtifact(
                        f"runs/{run['run_id']}/checkpoints/{checkpoint_id}.json", data
                    )
                ]
            ),
        )
        return ExternalResult(payload, True)

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=write,
    )


def submit(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ExternalResult:
    return _submit(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        envelope=envelope,
        uuid_factory=uuid_factory,
        protocol_version=1,
    )


def submit_v2(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ExternalResult:
    """Submit a strict typed-domain checkpoint without re-publishing it."""
    return _submit(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        envelope=envelope,
        uuid_factory=uuid_factory,
        protocol_version=2,
    )


def _submit(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    envelope: Mapping[str, object],
    uuid_factory: Callable[[], uuid.UUID],
    protocol_version: int,
) -> ExternalResult:
    resolved = _resolved(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id
    )
    invocation = _invocation(
        operation="submit",
        resolved=resolved,
        envelope=envelope,
        factory=uuid_factory,
        protocol_version=protocol_version,
    )
    if set(invocation["input"]) != {
        "run_id",
        "parent_checkpoint",
        "output_refs",
        "check_refs",
        "disclosure",
    }:
        raise ExternalRecordingError(
            "external_invocation_invalid", "submit input is invalid"
        )

    def write(writer):
        snap = writer.snapshot(
            (
                "references/",
                "run-inputs/",
                "records/",
                "records/external/",
                "run-plans/",
                "runs/",
            )
        )
        replay = _replay(snap, invocation)
        if replay is not None:
            return ExternalResult(replay, False)
        run, _path, _bytes = _read_run(snap, str(invocation["input"].get("run_id")))
        if (run.get("schema_version") == "2.0") != (protocol_version == 2):
            raise ExternalRecordingError(
                "external_protocol_downgrade",
                "Run protocol version does not match this operation",
                next_action="use_matching_external_protocol",
            )
        if _terminal(snap, str(run["run_id"])):
            raise ExternalRecordingError(
                "external_run_terminal", "terminal Run cannot be submitted again"
            )
        checkpoints = _checkpoints(snap, str(run["run_id"]))
        expected_parent = checkpoints[-1]["checkpoint_id"] if checkpoints else None
        if invocation["input"].get("parent_checkpoint") != expected_parent:
            raise ExternalRecordingError(
                "external_checkpoint_conflict",
                "submit does not name the current checkpoint",
            )
        plan = _plan_for_run(snap, run)
        for sealed_input in plan.get("inputs", []):
            _revalidate_input(
                resolved,
                snap,
                sealed_input,
                allow_role_request=protocol_version == 2,
                allow_research_run=protocol_version == 2,
                allow_discovery_posting=protocol_version == 2,
                writer=writer,
            )
        missing_questions = (
            [
                {
                    "id": question["id"],
                    "prompt": question["prompt"],
                    "state": "missing",
                }
                for question in checkpoints[-1].get("questions", [])
                if isinstance(question, Mapping) and question.get("state") == "missing"
            ]
            if checkpoints
            else []
        )
        if missing_questions:
            raise ExternalRecordingError(
                "external_successor_required",
                "required checkpoint input needs an explicit successor Plan",
                next_action={
                    "action": "create_successor_plan",
                    "predecessor": {
                        "kind": "checkpoint",
                        "run_id": run["run_id"],
                        "checkpoint_id": checkpoints[-1]["checkpoint_id"],
                    },
                    "missing_inputs": missing_questions,
                    "selected_inputs": plan["inputs"],
                },
            )
        outputs, checks = (
            invocation["input"].get("output_refs"),
            invocation["input"].get("check_refs"),
        )
        if not isinstance(outputs, list) or not outputs:
            raise ExternalRecordingError(
                "external_output_missing",
                "required outputs are missing",
                next_action="submit_required_outputs",
            )
        if not isinstance(checks, list):
            raise ExternalRecordingError(
                "external_output_invalid", "checks are invalid"
            )
        if len(outputs) > 32 or len(checks) > 32:
            raise ExternalRecordingError(
                "external_limit_exceeded", "submit exceeds recording limits"
            )
        # A submitted output must be an exact previously journaled external artifact;
        # URLs, arbitrary paths, and opaque success claims cannot become evidence.
        recorded_outputs: dict[str, list[tuple[object, ...]]] = {}
        for checkpoint in checkpoints:
            for artifact in checkpoint.get("artifacts", []):
                if not isinstance(artifact, Mapping):
                    continue
                kind, markdown, sidecar = (
                    artifact.get("kind"),
                    artifact.get("markdown"),
                    artifact.get("sidecar"),
                )
                if (
                    isinstance(kind, str)
                    and isinstance(markdown, Mapping)
                    and isinstance(sidecar, Mapping)
                ):
                    entry: tuple[object, ...] = (kind, dict(markdown), dict(sidecar))
                    if protocol_version == 2:
                        domain_ref = artifact.get("domain_sidecar")
                        supporting_refs = artifact.get("supporting_artifacts")
                        if domain_ref is None and supporting_refs is None:
                            # Completion checks intentionally use the legacy
                            # three-field tuple inside a v2 checkpoint.
                            recorded_outputs.setdefault(str(markdown.get("path")), []).append(entry)
                            continue
                        if not isinstance(domain_ref, Mapping) or not isinstance(supporting_refs, list):
                            raise ExternalRecordingError(
                                "external_reconciliation_required",
                                "committed v2 checkpoint has incomplete domain refs",
                            )
                        entry += (
                            dict(domain_ref),
                            [dict(item) for item in supporting_refs if isinstance(item, Mapping)],
                        )
                    recorded_outputs.setdefault(str(markdown.get("path")), []).append(entry)
        valid_outputs: list[dict[str, object]] = []
        output_tuples: set[tuple[str, str, str]] = set()
        requested = _requested_output_kinds(
            resolved, plan, protocol_version=protocol_version
        )
        if protocol_version == 2:
            graph_selector, graph_id, graph_version = _graph_context(resolved, plan)
        else:
            graph_selector, graph_id, graph_version = None, None, None
        for item in outputs:
            if (
                not isinstance(item, Mapping)
                or set(item)
                != (
                    {"kind", "markdown", "sidecar", "domain_sidecar", "supporting_artifacts"}
                    if protocol_version == 2
                    else {"kind", "markdown", "sidecar"}
                )
                or not isinstance(item.get("kind"), str)
            ):
                raise ExternalRecordingError(
                    "external_output_invalid", "output reference is invalid"
                )
            markdown_ref, markdown_data = _safe_ref(
                snap, item["markdown"], code="external_output_invalid"
            )
            sidecar_ref, sidecar_data = _safe_ref(
                snap, item["sidecar"], code="external_output_invalid"
            )
            if not str(markdown_ref["path"]).startswith(
                f"runs/{run['run_id']}/artifacts/"
            ) or not str(sidecar_ref["path"]).startswith(
                f"runs/{run['run_id']}/artifacts/"
            ):
                raise ExternalRecordingError(
                    "external_scope_refused", "output artifact belongs to another Run"
                )
            try:
                sidecar_value = parse_json_bytes(sidecar_data)
            except ValueError as exc:
                raise ExternalRecordingError(
                    "external_output_invalid", "output sidecar is not valid JSON"
                ) from exc
            recorded = recorded_outputs.get(str(markdown_ref["path"]), [])
            output_tuple = (
                str(item["kind"]),
                str(markdown_ref["path"]),
                str(sidecar_ref["path"]),
            )
            expected_recorded: tuple[object, ...] = (
                str(item["kind"]),
                markdown_ref,
                sidecar_ref,
            )
            if protocol_version == 2:
                expected_recorded += (
                    dict(item["domain_sidecar"]),
                    [dict(entry) for entry in item["supporting_artifacts"]],
                )
            if (
                not isinstance(sidecar_value, Mapping)
                or not _output_sidecar_is_valid(
                    sidecar_value,
                    run_id=str(run["run_id"]),
                    inputs=plan.get("inputs"),
                    kind=str(item["kind"]),
                    markdown=markdown_data,
                )
                or len(recorded) != 1
                or recorded[0] != expected_recorded
                or output_tuple in output_tuples
            ):
                raise ExternalRecordingError(
                    "external_output_invalid",
                    "output is not an exact sealed checkpoint output tuple",
                )
            if protocol_version == 2:
                _validate_domain_binding(resolved, plan, str(item["kind"]))
                domain, supporting = _safe_persisted_domain(
                    snap,
                    item,
                    run_id=str(run["run_id"]),
                    kind=str(item["kind"]),
                    markdown=markdown_data,
                    project_id=resolved.project_id,
                    gig_id=resolved.gig_id,
                    gig_version=plan.get("gig_version"),
                    graph_selector=graph_selector,
                    graph_id=graph_id,
                graph_version=graph_version,
                    selected_inputs=plan.get("inputs"),
                    tailoring_request=plan.get("tailoring_request"),
                    historical_inputs=_historical_research_inputs(snap, plan.get("inputs")),
                )
                del domain, supporting
            output_tuples.add(output_tuple)
            if protocol_version == 2:
                valid_outputs.append(
                    {
                        "kind": item["kind"],
                        "markdown": markdown_ref,
                        "sidecar": sidecar_ref,
                        "domain_sidecar": dict(item["domain_sidecar"]),
                        "supporting_artifacts": [
                            dict(entry) for entry in item["supporting_artifacts"]
                        ],
                    }
                )
            else:
                valid_outputs.append(
                    {"kind": item["kind"], "markdown": markdown_ref, "sidecar": sidecar_ref}
                )
        if (
            len(valid_outputs) != len(requested)
            or {str(item["kind"]) for item in valid_outputs} != requested
        ):
            raise ExternalRecordingError(
                "external_output_missing",
                "submitted outputs do not satisfy the sealed output kinds",
            )
        check_artifacts: dict[str, tuple[str, dict[str, object]]] = {}
        for checkpoint in checkpoints:
            for artifact in checkpoint.get("artifacts", []):
                if not isinstance(artifact, Mapping):
                    continue
                kind, sidecar = artifact.get("kind"), artifact.get("sidecar")
                if isinstance(kind, str) and isinstance(sidecar, Mapping):
                    check_artifacts[str(sidecar.get("path"))] = (kind, dict(sidecar))
        valid_checks: list[dict[str, object]] = []
        check_kinds: set[str] = set()
        output_digests = {
            str(item["markdown"]["content_sha256"])
            for item in valid_outputs
            if isinstance(item.get("markdown"), Mapping)
        }
        for item in checks:
            check_ref, check_data = _safe_ref(
                snap, item, code="external_output_invalid"
            )
            checkpoint_item = check_artifacts.get(str(check_ref["path"]))
            if checkpoint_item is None or check_ref != checkpoint_item[1]:
                raise ExternalRecordingError(
                    "external_output_invalid", "check is not a recorded check sidecar"
                )
            try:
                check_value = parse_json_bytes(check_data)
            except ValueError as exc:
                raise ExternalRecordingError(
                    "external_output_invalid", "check sidecar is not valid JSON"
                ) from exc
            kind = checkpoint_item[0]
            if (
                not isinstance(check_value, Mapping)
                or not _check_sidecar_is_valid(
                    check_value, run_id=str(run["run_id"]), kind=kind
                )
                or check_value.get("output_sha256") not in output_digests
                or kind not in _required_check_kinds(resolved, plan)
            ):
                raise ExternalRecordingError(
                    "external_output_invalid",
                    "check does not satisfy the sealed contract",
                )
            if check_value.get("result") != "pass":
                raise ExternalRecordingError(
                    "external_output_invalid",
                    "declared required check result is not pass",
                )
            valid_checks.append(check_ref)
            check_kinds.add(kind)
        if check_kinds != _required_check_kinds(resolved, plan):
            raise ExternalRecordingError(
                "external_output_missing",
                "submitted checks do not satisfy the sealed completion contract",
            )
        disclosure = invocation["input"].get("disclosure")
        if (
            not isinstance(disclosure, Mapping)
            or set(disclosure) != {"execution", "actor_report"}
            or disclosure.get("execution") != "unobserved"
        ):
            raise ExternalRecordingError(
                "external_output_invalid", "external disclosure must remain unobserved"
            )
        receipt = {
            "schema_version": "2.0" if protocol_version == 2 else "1.0",
            "receipt_id": _id("receipt", uuid_factory),
            "run_id": run["run_id"],
            "run_plan": run["run_plan"],
            "invocation": invocation,
            "operation_key": invocation["operation_key"],
            "payload_sha256": invocation["payload_sha256"],
            "outcome": "succeeded",
            "outputs": valid_outputs,
            "checks": valid_checks,
            "disclosure": dict(disclosure),
            "created_at": _now(),
        }
        data = canonical_json_bytes(receipt)
        _schema(
            "external-recording-receipt-v2.schema.json"
            if protocol_version == 2
            else "external-recording-receipt.schema.json",
            receipt,
        )

        def revalidate_domains() -> None:
            for kind in sorted({str(item["kind"]) for item in valid_outputs}):
                _validate_domain_binding(resolved, plan, kind)

        _journaled(
            writer,
            transition="external_recording_succeeded",
            handoff_id=_id("handoff", uuid_factory),
            body=f"External recording Run {run['run_id']} succeeded.",
            revalidate=revalidate_domains if protocol_version == 2 else None,
            artifacts=(
                JournalArtifact(
                    f"runs/{run['run_id']}/receipts/{receipt['receipt_id']}.json", data
                ),
            ),
        )
        return ExternalResult(receipt, True)

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=write,
    )


def inspect(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str,
    run_id: str | None = None,
) -> dict[str, object]:
    resolved = _resolved(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id
    )
    snap = _snapshot(resolved)
    if run_id is not None:
        run, _, _ = _read_run(snap, run_id)
        checkpoints = _checkpoints(snap, run_id)
        receipts = _run_receipts(snap, run_id)
        projection = dict(run)
        if receipts:
            projection["status"] = receipts[-1]["outcome"]
        elif checkpoints and any(
            question.get("state") == "missing"
            for question in checkpoints[-1].get("questions", [])
        ):
            projection["status"] = "waiting_input"
        return {"run": projection, "checkpoints": checkpoints, "receipts": receipts}
    return {
        "plans": [
            _recorded_dispatch(
                data,
                v1_schema="external-recording-plan.schema.json",
                v2_schema="external-recording-plan-v2.schema.json",
                code="external_reconciliation_required",
            )
            for path, data in snap.artifacts.items()
            if path.startswith("run-plans/") and path.endswith("/external-plan.json")
        ],
        "runs": [
            _recorded_dispatch(
                data,
                v1_schema="external-recording-run.schema.json",
                v2_schema="external-recording-run-v2.schema.json",
                code="external_reconciliation_required",
            )
            for path, data in snap.artifacts.items()
            if path.endswith("/external-run.json")
        ],
    }


def graphs(
    *, home_root: Path, requested_target: Path | None, gig_id: str
) -> list[dict[str, object]]:
    resolved = _resolved(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id
    )
    try:
        projection = __import__("gigai.index", fromlist=["read_index"]).read_index(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
        )
        authority = _resolve_authority(resolved, projection, None)
    except (RunError, ValueError) as exc:
        raise ExternalRecordingError(
            "external_authority_mismatch", "approved Graph Set authority is unavailable"
        ) from exc
    graph_set = authority.get("graph_set")
    if not isinstance(graph_set, Mapping):
        raise ExternalRecordingError(
            "external_authority_mismatch",
            "external recording requires an approved Graph Set",
        )
    return [
        {
            "graph_id": item["graph_id"],
            "aliases": item["aliases"],
            "purpose": item["purpose"],
        }
        for item in graph_set["graphs"]
        if isinstance(item, Mapping)
    ]


def requirements(
    *, home_root: Path, requested_target: Path | None, gig_id: str, graph_selector: str
) -> dict[str, object]:
    resolved = _resolved(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id
    )
    authority, _graph, descriptor = _authority(resolved, graph_selector)
    return {
        "gig_version": authority["version"],
        "graph_id": descriptor["graph_id"],
        "input_contract": _approved_contract(resolved, descriptor["input_contract"]),
        "output_contract": _approved_contract(resolved, descriptor["output_contract"]),
        "check_contract": _approved_contract(
            resolved, descriptor["completion_evidence_contract"]
        ),
        "next_action": "plan",
    }
