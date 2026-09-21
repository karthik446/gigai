"""Closed authority bridge for a Gig-owned Scout Python tool.

The bridge deliberately does *not* import or execute the tool entry.  A
Gig-owned adapter may construct one typed request, but GigAI resolves the
committed active-version pointer, validates the approved manifest inventory,
and delegates publication to the native C1 operation service.  The same
binding is verified again while that service holds the journal writer lock.
"""

from __future__ import annotations

from collections.abc import Mapping
import importlib.util
from pathlib import Path
import stat
import sys
from typing import Callable
import uuid

from .canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from .capabilities import validate_capability_manifest
from .index import JournalIndexError, read_index
from .journal import JournalConflictError, JournalSnapshot, run_with_journal_writer
from .native_records import (
    NativeRecordResult,
    archive_native_record_from_tool,
    create_native_record_from_tool,
    update_native_record_from_tool,
)
from .private_records import PrivateRecordError
from .validators import validate_serialized_contract
from .workpad import ResolvedWorkpad, resolve_workpad, workpad_layout_version


_TOOL_ROOT = "tools"
_ROOT_WRAPPER = "gig.py"
_ALLOWED_OPERATIONS = ("record_archive", "record_create", "record_update")
_ALLOWED_EFFECTS = ("write_workpad",)


class ScoutToolError(PrivateRecordError):
    """Typed, content-redacted refusal from the Scout tool authority bridge."""


def _resolved(*, home_root: Path, requested_target: Path | None, gig_id: str | None) -> ResolvedWorkpad:
    resolved = resolve_workpad(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    if workpad_layout_version(resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id) != 2:
        raise ScoutToolError("tool_workpad_layout_required", "migrate this workpad to layout v2 before tool operations")
    return resolved


def _artifact_ref_matches(snapshot: JournalSnapshot, reference: object) -> bytes:
    if not isinstance(reference, Mapping):
        raise ScoutToolError("tool_authority_unavailable", "committed tool authority is unavailable")
    path = reference.get("path")
    expected = reference.get("content_sha256")
    size = reference.get("size_bytes")
    if not isinstance(path, str) or not path.startswith("manifests/capabilities/") or not path.endswith(".json"):
        raise ScoutToolError("tool_authority_unavailable", "committed tool authority is unavailable")
    data = snapshot.artifacts.get(path)
    if data is None or digest_imported_bytes(data) != expected or len(data) != size or reference.get("media_type") != "application/json":
        raise ScoutToolError("tool_authority_unavailable", "committed tool authority is unavailable")
    return data


def _actor(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "id"}:
        raise ScoutToolError("tool_invocation_invalid", "tool actor is invalid")
    kind, actor_id = value.get("kind"), value.get("id")
    if kind != "agent" or not isinstance(actor_id, str) or not actor_id or len(actor_id) > 255:
        raise ScoutToolError("tool_invocation_invalid", "tool operations require an agent origin")
    return {"kind": kind, "id": actor_id}


def _request(value: Mapping[str, object]) -> dict[str, object]:
    expected = {
        "gig_id",
        "gig_version",
        "capability_id",
        "entry_path",
        "inventory_sha256",
        "operation",
        "effects",
        "actor",
        "operation_key",
        "origin",
    }
    operation = value.get("operation")
    if operation == "record_create":
        expected.add("content")
    elif operation == "record_update":
        expected.update({"record_id", "parent_revision", "content"})
    elif operation == "record_archive":
        expected.update({"record_id", "parent_revision"})
    if "wrapper_ref" in value:
        expected.add("wrapper_ref")
    if set(value) != expected:
        raise ScoutToolError("tool_invocation_invalid", "tool request has unsupported fields")
    request = dict(value)
    if request.get("operation") not in _ALLOWED_OPERATIONS or request.get("origin") != "agent_supplied":
        raise ScoutToolError("tool_operation_refused", "tool operation or origin is not admitted")
    if request.get("effects") != list(_ALLOWED_EFFECTS):
        raise ScoutToolError("tool_effect_refused", "tool effects exceed the approved record operation")
    if not isinstance(request.get("gig_id"), str) or not isinstance(request.get("gig_version"), int):
        raise ScoutToolError("tool_invocation_invalid", "tool Gig identity is invalid")
    if not isinstance(request.get("capability_id"), str) or not isinstance(request.get("entry_path"), str):
        raise ScoutToolError("tool_invocation_invalid", "tool capability identity is invalid")
    if not isinstance(request.get("inventory_sha256"), str) or not isinstance(request.get("operation_key"), str):
        raise ScoutToolError("tool_invocation_invalid", "tool operation identity is invalid")
    if "wrapper_ref" in request:
        wrapper = request["wrapper_ref"]
        if (
            not isinstance(wrapper, Mapping)
            or set(wrapper) != {"path", "content_sha256", "media_type", "size_bytes"}
            or wrapper.get("path") != _ROOT_WRAPPER
            or not isinstance(wrapper.get("content_sha256"), str)
            or wrapper.get("media_type") != "text/x-python"
            or not isinstance(wrapper.get("size_bytes"), int)
            or wrapper["size_bytes"] < 1
        ):
            raise ScoutToolError("tool_wrapper_invalid", "tool wrapper binding is invalid")
        request["wrapper_ref"] = dict(wrapper)
    if request["operation"] in {"record_create", "record_update"} and not isinstance(request.get("content"), Mapping):
        raise ScoutToolError("tool_invocation_invalid", "tool record content is invalid")
    if request["operation"] in {"record_update", "record_archive"}:
        if not isinstance(request.get("record_id"), str) or not isinstance(request.get("parent_revision"), str):
            raise ScoutToolError("tool_invocation_invalid", "tool revision target is invalid")
    request["actor"] = _actor(request["actor"])
    if "content" in request:
        request["content"] = dict(request["content"])
    return request


def _safe_tool_path(root: Path, relative: str) -> Path:
    if not relative.startswith("tools/") or "\\" in relative:
        raise ScoutToolError("tool_inventory_invalid", "tool source path is invalid")
    parts = Path(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ScoutToolError("tool_inventory_invalid", "tool source path is invalid")
    candidate = root.joinpath(*parts)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ScoutToolError("tool_inventory_invalid", "tool source path is invalid") from exc
    return candidate


def _safe_inventory_path(root: Path, relative: str) -> Path:
    if relative == _ROOT_WRAPPER:
        return root / _ROOT_WRAPPER
    return _safe_tool_path(root, relative)


def _regular_file(path: Path, root: Path) -> bytes:
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        try:
            status = current.lstat()
        except OSError as exc:
            raise ScoutToolError("tool_inventory_changed", "approved tool source is unavailable") from exc
        if stat.S_ISLNK(status.st_mode):
            raise ScoutToolError("tool_source_unsafe", "approved tool source contains a link")
    status = path.stat()
    if not stat.S_ISREG(status.st_mode) or status.st_mode & 0o111:
        raise ScoutToolError("tool_source_unsafe", "approved tool source is not a non-executable regular file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ScoutToolError("tool_inventory_changed", "approved tool source is unavailable") from exc


def _inventory(root: Path, capability_id: str, binding: Mapping[str, object]) -> list[dict[str, object]]:
    inventory = binding.get("inventory")
    if not isinstance(inventory, list) or not inventory:
        raise ScoutToolError("tool_binding_invalid", "approved tool inventory is invalid")
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    wrapper_ref = binding.get("wrapper_ref")
    prefix = f"{_TOOL_ROOT}/{capability_id}/"
    for item in inventory:
        if not isinstance(item, Mapping) or set(item) != {"path", "content_sha256", "media_type", "size_bytes"}:
            raise ScoutToolError("tool_binding_invalid", "approved tool inventory is invalid")
        path = item.get("path")
        if not isinstance(path, str) or path in seen:
            raise ScoutToolError("tool_binding_invalid", "approved tool inventory is invalid")
        if path == _ROOT_WRAPPER:
            if not isinstance(wrapper_ref, Mapping) or dict(wrapper_ref) != dict(item):
                raise ScoutToolError("tool_wrapper_invalid", "root wrapper is not explicitly bound")
        elif not path.startswith(prefix):
            raise ScoutToolError("tool_binding_invalid", "approved tool inventory is invalid")
        seen.add(path)
        data = _regular_file(_safe_inventory_path(root, path), root)
        if item.get("content_sha256") != digest_imported_bytes(data) or item.get("size_bytes") != len(data):
            raise ScoutToolError("tool_inventory_changed", "approved tool source changed")
        normalized.append({"path": path, "content_sha256": item["content_sha256"], "media_type": item["media_type"], "size_bytes": item["size_bytes"]})
    if normalized != sorted(normalized, key=lambda item: str(item["path"])):
        raise ScoutToolError("tool_binding_invalid", "approved tool inventory is not canonical")
    if wrapper_ref is not None and _ROOT_WRAPPER not in seen:
        raise ScoutToolError("tool_wrapper_invalid", "root wrapper is not in the approved inventory")
    tool_root = _safe_tool_path(root, f"{_TOOL_ROOT}/{capability_id}")
    actual: set[str] = set()
    try:
        for candidate in tool_root.rglob("*"):
            relative = candidate.relative_to(root).as_posix()
            status = candidate.lstat()
            if stat.S_ISLNK(status.st_mode):
                raise ScoutToolError("tool_source_unsafe", "approved tool source contains a link")
            if stat.S_ISREG(status.st_mode):
                actual.add(relative)
                if status.st_mode & 0o111:
                    raise ScoutToolError("tool_source_unsafe", "tool root has an executable member")
    except OSError as exc:
        raise ScoutToolError("tool_inventory_changed", "approved tool source is unavailable") from exc
    if actual != seen - {_ROOT_WRAPPER}:
        raise ScoutToolError("tool_inventory_changed", "tool root differs from the approved inventory")
    return normalized


def _binding(resolved: ResolvedWorkpad, snapshot: JournalSnapshot, pointer: Mapping[str, object], request: Mapping[str, object]) -> dict[str, object]:
    if not validate_serialized_contract("active-gig-version-v2.schema.json", canonical_json_bytes(dict(pointer))).valid:
        raise ScoutToolError("tool_authority_unavailable", "committed tool authority is unavailable")
    if pointer.get("gig_id") != resolved.gig_id or request["gig_id"] != resolved.gig_id or request["gig_version"] != pointer.get("active_version"):
        raise ScoutToolError("tool_scope_refused", "tool request does not name the active Gig version")
    manifest_ref = pointer.get("capability_manifest")
    manifest_bytes = _artifact_ref_matches(snapshot, manifest_ref)
    if not validate_capability_manifest(manifest_bytes).valid:
        raise ScoutToolError("tool_authority_unavailable", "committed capability authority is unavailable")
    manifest = parse_json_bytes(manifest_bytes)
    if not isinstance(manifest, Mapping) or manifest.get("gig_id") != resolved.gig_id:
        raise ScoutToolError("tool_scope_refused", "capability authority belongs to another Gig")
    capability = next((item for item in manifest.get("capabilities", []) if isinstance(item, Mapping) and item.get("capability_id") == request["capability_id"]), None)
    if capability is None or capability.get("kind") != "tool":
        raise ScoutToolError("tool_capability_refused", "tool capability is not approved")
    binding = capability.get("tool_binding")
    constraints = capability.get("source_constraints")
    permissions = capability.get("permissions")
    if not isinstance(binding, Mapping) or not isinstance(constraints, Mapping) or not isinstance(permissions, Mapping):
        raise ScoutToolError("tool_capability_refused", "tool capability lacks a closed binding")
    if capability.get("declared_effects") != list(_ALLOWED_EFFECTS) or permissions != {"filesystem": "write_isolated", "network": "none", "credentials": "none"}:
        raise ScoutToolError("tool_effect_refused", "tool capability effects are not admitted")
    if capability.get("availability_state") != "available" or capability.get("security_review", {}).get("status") != "passed":
        raise ScoutToolError("tool_capability_refused", "tool capability is not approved for this operation")
    operations = binding.get("operations")
    if (
        binding.get("entry_path") != request["entry_path"]
        or not isinstance(operations, list)
        or operations != sorted(set(operations))
        or request["operation"] not in operations
        or any(item not in _ALLOWED_OPERATIONS for item in operations)
        or binding.get("effects") != list(_ALLOWED_EFFECTS)
    ):
        raise ScoutToolError("tool_binding_invalid", "tool request does not match the approved binding")
    bound_wrapper = binding.get("wrapper_ref")
    requested_wrapper = request.get("wrapper_ref")
    if bound_wrapper is None:
        # A historical tools-only binding remains usable. Its receipt does not
        # gain wrapper provenance merely because a newer copied wrapper passes
        # its local bytes; new bindings that advertise a wrapper take the
        # strict branch below.
        pass
    elif not isinstance(bound_wrapper, Mapping) or requested_wrapper != dict(bound_wrapper):
        raise ScoutToolError("tool_wrapper_invalid", "tool request does not bind the approved root wrapper")
    inventory = _inventory(resolved.path, str(request["capability_id"]), binding)
    inventory_sha256 = digest_imported_bytes(canonical_json_bytes(inventory))
    if binding.get("inventory_sha256") != inventory_sha256 or request["inventory_sha256"] != inventory_sha256:
        raise ScoutToolError("tool_inventory_changed", "tool inventory digest does not match approved bytes")
    entry = next((item for item in inventory if item["path"] == binding.get("entry_path")), None)
    if (
        entry is None
        or entry["content_sha256"] != constraints.get("required_digest")
        or Path(str(binding["entry_path"])).name != constraints.get("required_identity")
    ):
        raise ScoutToolError("tool_binding_invalid", "tool entry does not match its approved source constraint")
    sealed = {
        "manifest_ref": dict(manifest_ref),
        "capability_id": request["capability_id"],
        "gig_version": request["gig_version"],
        "entry_path": request["entry_path"],
        "inventory": inventory,
        "inventory_sha256": inventory_sha256,
        "operation": request["operation"],
        "effects": list(_ALLOWED_EFFECTS),
        "actor": dict(request["actor"]),
    }
    if isinstance(bound_wrapper, Mapping):
        sealed["wrapper_ref"] = dict(bound_wrapper)
    return sealed


def _publication_request(binding: Mapping[str, object], resolved: ResolvedWorkpad) -> dict[str, object]:
    """Construct only the closed request shape needed to re-read authority."""
    request: dict[str, object] = {
        "gig_id": resolved.gig_id,
        "gig_version": binding.get("gig_version"),
        "capability_id": binding.get("capability_id"),
        "entry_path": binding.get("entry_path"),
        "inventory_sha256": binding.get("inventory_sha256"),
        "operation": binding.get("operation"),
        "effects": binding.get("effects"),
        "actor": binding.get("actor"),
        "operation_key": "tool-publication-revalidation",
        "origin": "agent_supplied",
    }
    if "wrapper_ref" in binding:
        request["wrapper_ref"] = binding.get("wrapper_ref")
    if binding.get("operation") in {"record_create", "record_update"}:
        request["content"] = {}
    if binding.get("operation") in {"record_update", "record_archive"}:
        request["record_id"] = "record_00000000-0000-4000-8000-000000000000"
        request["parent_revision"] = "revision_00000000-0000-4000-8000-000000000000"
    return _request(request)


def revalidate_tool_binding_at_publication(*, resolved: ResolvedWorkpad, snapshot: JournalSnapshot, binding: Mapping[str, object]) -> dict[str, object]:
    """Independently resolve a supplied binding from locked committed authority."""
    try:
        pointer = read_index(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
        ).active_version
    except (JournalConflictError, JournalIndexError) as exc:
        raise ScoutToolError("tool_authority_unavailable", "committed tool authority is unavailable") from exc
    if not isinstance(pointer, Mapping):
        raise ScoutToolError("tool_authority_unavailable", "committed tool authority is unavailable")
    return _binding(resolved, snapshot, pointer, _publication_request(binding, resolved))


def _authority_snapshot(resolved: ResolvedWorkpad) -> tuple[Mapping[str, object], JournalSnapshot]:
    try:
        pointer, snapshot = run_with_journal_writer(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            operation=lambda writer: (
                read_index(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id).active_version,
                writer.snapshot(("manifests/capabilities/",)),
            ),
        )
    except (JournalConflictError, JournalIndexError) as exc:
        raise ScoutToolError("tool_authority_unavailable", "committed tool authority is unavailable") from exc
    if not isinstance(pointer, Mapping):
        raise ScoutToolError("tool_authority_unavailable", "committed tool authority is unavailable")
    return pointer, snapshot


def dispatch_native_record_tool(*, home_root: Path, requested_target: Path | None, invocation: Mapping[str, object], gig_id: str | None = None, uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4, _before_publication: Callable[[], None] | None = None) -> NativeRecordResult:
    """Validate an approved Gig-owned tool request and publish its native record.

    No Python source is imported or run here.  The supplied operation is a
    typed record-create request, and the C1 service is the sole journal writer.
    """
    request = _request(invocation)
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    if request["gig_id"] != resolved.gig_id:
        raise ScoutToolError("tool_scope_refused", "tool request belongs to another Gig")
    pointer, snapshot = _authority_snapshot(resolved)
    sealed_binding = _binding(resolved, snapshot, pointer, request)

    common = {
        "home_root": home_root,
        "requested_target": requested_target,
        "actor": request["actor"],
        "operation_key": str(request["operation_key"]),
        "gig_id": resolved.gig_id,
        "tool_binding": sealed_binding,
        "uuid_factory": uuid_factory,
        "_before_tool_revalidation": _before_publication,
    }
    if request["operation"] == "record_create":
        return create_native_record_from_tool(
            **common,
            content=request["content"],
            origin="agent_supplied",
        )
    if request["operation"] == "record_update":
        return update_native_record_from_tool(
            **common,
            record_id=str(request["record_id"]),
            parent_revision=str(request["parent_revision"]),
            content=request["content"],
            origin="agent_supplied",
        )
    return archive_native_record_from_tool(
        **common,
        record_id=str(request["record_id"]),
        parent_revision=str(request["parent_revision"]),
        origin="agent_supplied",
    )


def _entry_candidate(*, resolved: ResolvedWorkpad, pointer: Mapping[str, object], snapshot: JournalSnapshot, capability_id: str, actor: Mapping[str, object], operation_key: str, operation: str, tool_input: Mapping[str, object], wrapper_ref: Mapping[str, object] | None) -> tuple[dict[str, object], dict[str, object]]:
    if not validate_serialized_contract("active-gig-version-v2.schema.json", canonical_json_bytes(dict(pointer))).valid:
        raise ScoutToolError("tool_authority_unavailable", "committed tool authority is unavailable")
    manifest_bytes = _artifact_ref_matches(snapshot, pointer.get("capability_manifest"))
    manifest = parse_json_bytes(manifest_bytes)
    if not isinstance(manifest, Mapping) or manifest.get("gig_id") != resolved.gig_id:
        raise ScoutToolError("tool_authority_unavailable", "committed tool authority is unavailable")
    capability = next((item for item in manifest.get("capabilities", []) if isinstance(item, Mapping) and item.get("capability_id") == capability_id), None)
    binding = capability.get("tool_binding") if isinstance(capability, Mapping) else None
    if not isinstance(binding, Mapping):
        raise ScoutToolError("tool_capability_refused", "tool capability lacks a closed binding")
    candidate_value: dict[str, object] = {
        "gig_id": resolved.gig_id,
        "gig_version": pointer.get("active_version"),
        "capability_id": capability_id,
        "entry_path": binding.get("entry_path"),
        "inventory_sha256": binding.get("inventory_sha256"),
        "operation": operation,
        "effects": list(_ALLOWED_EFFECTS),
        "actor": actor,
        "operation_key": operation_key,
        "origin": "agent_supplied",
    }
    if operation in {"record_create", "record_update"}:
        candidate_value["content"] = {}
    if operation in {"record_update", "record_archive"}:
        candidate_value["record_id"] = tool_input.get("record_id")
        candidate_value["parent_revision"] = tool_input.get("parent_revision")
    if wrapper_ref is not None:
        candidate_value["wrapper_ref"] = dict(wrapper_ref)
    candidate = _request(candidate_value)
    return candidate, _binding(resolved, snapshot, pointer, candidate)


def invoke_approved_tool_entry(*, home_root: Path, requested_target: Path | None, capability_id: str, actor: Mapping[str, object], operation_key: str, tool_input: Mapping[str, object], operation: str = "record_create", gig_id: str | None = None, wrapper_ref: Mapping[str, object] | None = None, uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4) -> NativeRecordResult:
    """Explicitly execute one approved fixture/tool entry and publish its typed result.

    This is not automatic execution, a scheduler, or a sandbox.  It is the
    narrow C3 library entry used when a user or agent explicitly invokes an
    already approved tool; `gig.py` distribution and general initialization
    remain SCOUT-05 work.
    """
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    pointer, snapshot = _authority_snapshot(resolved)
    candidate, binding = _entry_candidate(
        resolved=resolved,
        pointer=pointer,
        snapshot=snapshot,
        capability_id=capability_id,
        actor=actor,
        operation_key=operation_key,
        operation=operation,
        tool_input=tool_input,
        wrapper_ref=wrapper_ref,
    )
    entry_path = _safe_tool_path(resolved.path, str(binding["entry_path"]))
    source = _regular_file(entry_path, resolved.path)
    entry = next(item for item in binding["inventory"] if item["path"] == binding["entry_path"])
    if digest_imported_bytes(source) != entry["content_sha256"]:
        raise ScoutToolError("tool_inventory_changed", "approved tool entry changed before invocation")
    spec = importlib.util.spec_from_file_location(
        f"gigai_approved_tool_{capability_id.removeprefix('cap_').replace('-', '_')}",
        entry_path,
    )
    if spec is None or spec.loader is None:
        raise ScoutToolError("tool_entry_invalid", "approved tool entry cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    previous_bytecode_setting = sys.dont_write_bytecode
    try:
        # The approved inventory is closed.  An import cache in the tool root
        # would itself be an unapproved member on the immediate revalidation.
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ScoutToolError("tool_entry_failed", "approved tool entry failed during explicit invocation") from exc
    finally:
        sys.dont_write_bytecode = previous_bytecode_setting
    builder = getattr(module, "build_native_record_operation", None)
    if not callable(builder):
        raise ScoutToolError("tool_entry_invalid", "approved tool entry lacks the supported operation builder")
    try:
        operation_result = builder(
            {
                "operation": operation,
                "operation_key": operation_key,
                "input": dict(tool_input),
            }
        )
    except Exception as exc:
        raise ScoutToolError("tool_entry_failed", "approved tool entry failed to build an operation") from exc
    expected_result = {
        "record_create": {"operation_key", "content"},
        "record_update": {"operation_key", "record_id", "parent_revision", "content"},
        "record_archive": {"operation_key", "record_id", "parent_revision"},
    }.get(operation)
    if not isinstance(operation_result, Mapping) or set(operation_result) != expected_result:
        raise ScoutToolError("tool_entry_invalid", "approved tool entry returned an unsupported operation")
    invocation = {**candidate, **operation_result}
    return dispatch_native_record_tool(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=resolved.gig_id,
        invocation=invocation,
        uuid_factory=uuid_factory,
    )


__all__ = ["ScoutToolError", "dispatch_native_record_tool", "invoke_approved_tool_entry"]
