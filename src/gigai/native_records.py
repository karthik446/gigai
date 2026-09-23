"""Native Scout records published through the settled C1 journal service.

This module owns only the structured sidecar and native-facing operations.  It
never treats SQLite as authority, reads chat history, or writes a journal file
outside :func:`run_with_journal_writer`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
import re
import uuid
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from .canonical import EntityPrefix, canonical_json_bytes, digest_imported_bytes, parse_json_bytes, validate_entity_id
from .journal import JournalArtifact, JournalConflictError, JournalSnapshot, JournalTransition, run_with_journal_writer
from .private_records import PrivateRecordError, rebuild_scout_projection
from .validators import validate_serialized_contract
from .workpad import ResolvedWorkpad, resolve_workpad, workpad_layout_version


_SCHEMA_PATH = Path(__file__).with_name("schemas") / "native-record-content.schema.json"
_TASK_CONTEXT = re.compile(r"^task_context_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_KIND = frozenset({"profile_preferences", "experience_qa", "imported_reference", "supplied_source", "selected_conversation"})


@dataclass(frozen=True)
class NativeRecordResult:
    record_id: str
    revision_id: str
    created: bool
    state: str
    receipt: dict[str, object] | None
    task_context_id: str | None
    projection_pending: bool = False
    rebuild_action: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _id(prefix: EntityPrefix, factory: Callable[[], uuid.UUID]) -> str:
    value = factory()
    if type(value) is not uuid.UUID or value.version != 4:
        raise PrivateRecordError("native_record_invalid", "ID factory must return UUIDv4")
    return f"{prefix.value}_{value}"


def _ref(path: str, data: bytes) -> dict[str, object]:
    return {"path": path, "content_sha256": digest_imported_bytes(data), "media_type": "application/json", "size_bytes": len(data)}


def _resolved(*, home_root: Path, requested_target: Path | None, gig_id: str | None) -> ResolvedWorkpad:
    resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
    if workpad_layout_version(resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id) != 2:
        raise PrivateRecordError("workpad_layout_migration_required", "migrate this workpad to layout v2 before native record operations")
    return resolved


def _receipt_path(operation: str, key: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", key):
        raise PrivateRecordError("native_operation_invalid", "operation key is invalid")
    return f"records/operations/{operation}-{digest_imported_bytes(key.encode()).removeprefix('sha256:')}.json"


def _schema() -> Draft202012Validator:
    try:
        value = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PrivateRecordError("native_record_invalid", "native content schema is unavailable") from exc
    return Draft202012Validator(value)


def _validate_content(content: Mapping[str, object]) -> dict[str, object]:
    value = dict(content)
    errors = sorted(_schema().iter_errors(value), key=lambda item: list(item.path))
    if errors:
        raise PrivateRecordError("native_record_invalid", "native content does not meet the closed record contract")
    if len(canonical_json_bytes(value)) > 262_144:
        raise PrivateRecordError("native_record_too_large", "native content exceeds the sidecar limit")
    kind = value.get("kind")
    payload = value.get("payload")
    if kind not in _KIND or not isinstance(payload, dict):
        raise PrivateRecordError("native_record_invalid", "native content kind is invalid")
    _semantic_content(value)
    return value


def _fact(value: object, *, context_required: bool = False, evidence_required: bool = False) -> None:
    if not isinstance(value, dict):
        raise PrivateRecordError("native_record_invalid", "preference fact is invalid")
    state, primary, context = value.get("state"), value.get("value"), value.get("context")
    if state in {"unknown", "declined", "conflicting"} and primary is not None:
        raise PrivateRecordError("native_record_invalid", "uncertain facts must have a null primary value")
    if state == "known" and primary is None:
        raise PrivateRecordError("native_record_invalid", "known facts require an explicit value")
    if context_required and state == "known" and (not isinstance(context, str) or not context.strip()):
        raise PrivateRecordError("native_record_invalid", "known employer or eligibility facts require context")
    provenance = value.get("provenance")
    source_refs = provenance.get("source_refs") if isinstance(provenance, dict) else None
    if evidence_required and state == "known" and (not isinstance(source_refs, list) or not source_refs):
        raise PrivateRecordError("native_record_invalid", "known employer sponsorship requires exact source evidence")
    conflict_refs = value.get("conflict_refs")
    if state == "conflicting" and (not isinstance(conflict_refs, list) or not conflict_refs):
        raise PrivateRecordError("native_record_invalid", "conflicting facts require referenced conflict evidence")


def _semantic_content(value: dict[str, object]) -> None:
    kind, payload = value["kind"], value["payload"]
    assert isinstance(payload, dict)
    if kind == "profile_preferences":
        hard = payload.get("hard_constraints")
        soft = payload.get("soft_priorities")
        if not isinstance(hard, dict) or not isinstance(soft, dict) or set(hard) & set(soft):
            raise PrivateRecordError("native_record_invalid", "hard constraints and soft priorities must be separate")
        for fact in [*hard.values(), *soft.values()]:
            _fact(fact)
        _fact(payload.get("sponsorship_need"))
        _fact(payload.get("employer_sponsorship"), context_required=True, evidence_required=True)
        _fact(payload.get("eligibility"), context_required=True)
    elif kind == "experience_qa":
        seen: set[str] = set()
        for question in payload.get("questions", []):
            if not isinstance(question, dict) or question.get("question_id") in seen:
                raise PrivateRecordError("native_record_invalid", "experience question identity is invalid")
            seen.add(str(question["question_id"]))
            state, answer, provenance = question.get("state"), question.get("answer"), question.get("provenance")
            if state == "answered" and (not isinstance(answer, str) or not isinstance(provenance, dict)):
                raise PrivateRecordError("native_record_invalid", "answered questions require answer and provenance")
            if state in {"missing", "declined", "not_applicable"} and answer is not None:
                raise PrivateRecordError("native_record_invalid", "unanswered questions must not contain answer text")
            if state in {"declined", "not_applicable"} and not isinstance(provenance, dict):
                raise PrivateRecordError("native_record_invalid", "declined or inapplicable questions require provenance")
    elif kind == "supplied_source":
        for source in payload.get("sources", []):
            if not isinstance(source, dict):
                raise PrivateRecordError("native_record_invalid", "supplied source is invalid")
            verified, verification = source.get("status") == "independently_verified", source.get("verification_ref")
            if verified != isinstance(verification, dict):
                raise PrivateRecordError("native_record_invalid", "verification references are required only for verified sources")
    elif kind == "selected_conversation":
        source = payload.get("source")
        if not isinstance(source, dict):
            raise PrivateRecordError("native_record_invalid", "conversation source is invalid")
        if source.get("source_kind") == "declared_session_reference" and not isinstance(source.get("session_reference"), str):
            raise PrivateRecordError("native_record_invalid", "declared conversation source needs an opaque reference")


def _snapshot_json(snapshot: JournalSnapshot, path: str, *, schema: str | None = None) -> dict[str, object]:
    try:
        data = snapshot.artifacts[path]
        if schema and not validate_serialized_contract(schema, data).valid:
            raise ValueError
        value = parse_json_bytes(data)
    except (KeyError, ValueError) as exc:
        raise PrivateRecordError("native_record_not_authenticated", "committed native evidence is invalid") from exc
    if not isinstance(value, dict):
        raise PrivateRecordError("native_record_not_authenticated", "committed native evidence is invalid")
    return value


def _chain(resolved: ResolvedWorkpad, snapshot: JournalSnapshot, record_id: str) -> list[dict[str, object]]:
    try:
        validate_entity_id(record_id, expected_prefix=EntityPrefix.RECORD)
    except ValueError as exc:
        raise PrivateRecordError("native_record_not_found", "record ID is invalid") from exc
    prefix = f"records/{record_id}/revisions/"
    values: dict[str, dict[str, object]] = {}
    for path in snapshot.artifacts:
        if path.startswith(prefix) and path.endswith(".json"):
            item = _snapshot_json(snapshot, path, schema="private-record-revision.schema.json")
            if item.get("record_id") != record_id or item.get("project_id") != resolved.project_id or item.get("gig_id") != resolved.gig_id:
                raise PrivateRecordError("native_record_scope_refused", "record revision belongs to another scope")
            revision_id = item.get("revision_id")
            if not isinstance(revision_id, str) or revision_id in values:
                raise PrivateRecordError("native_record_invalid", "record revision identity is invalid")
            values[revision_id] = item
    if not values:
        raise PrivateRecordError("native_record_not_found", "native record was not found")
    roots = [item for item in values.values() if item.get("parent_revision") is None]
    if len(roots) != 1:
        raise PrivateRecordError("native_record_invalid", "native record chain has ambiguous roots")
    ordered, seen = [roots[0]], {str(roots[0]["revision_id"])}
    while True:
        children = [item for item in values.values() if item.get("parent_revision") == ordered[-1]["revision_id"]]
        if len(children) > 1:
            raise PrivateRecordError("native_record_invalid", "native record chain branches")
        if not children:
            break
        child = children[0]
        if str(child["revision_id"]) in seen:
            raise PrivateRecordError("native_record_invalid", "native record chain cycles")
        ordered.append(child)
        seen.add(str(child["revision_id"]))
    if len(seen) != len(values):
        raise PrivateRecordError("native_record_invalid", "native record chain is broken")
    return ordered


def _sidecar(resolved: ResolvedWorkpad, snapshot: JournalSnapshot, revision: Mapping[str, object]) -> tuple[dict[str, object], bytes]:
    content = revision.get("content")
    if not isinstance(content, dict) or content.get("family") != "jsl_blob":
        raise PrivateRecordError("native_record_invalid", "record does not point to native content")
    blob = content.get("blob_ref")
    if not isinstance(blob, dict) or not isinstance(blob.get("path"), str):
        raise PrivateRecordError("native_record_invalid", "native content reference is invalid")
    path = blob["path"]
    record_id, revision_id = revision.get("record_id"), revision.get("revision_id")
    if path != f"records/{record_id}/blobs/{revision_id}.json":
        raise PrivateRecordError("native_record_scope_refused", "native sidecar path is invalid")
    data = snapshot.artifacts.get(path)
    if data is None or digest_imported_bytes(data) != blob.get("content_sha256") or blob.get("size_bytes") != len(data) or content.get("content_sha256") != digest_imported_bytes(data):
        raise PrivateRecordError("native_record_not_authenticated", "native sidecar digest is invalid")
    try:
        parsed = parse_json_bytes(data)
    except ValueError as exc:
        raise PrivateRecordError("native_record_invalid", "native sidecar is invalid") from exc
    if not isinstance(parsed, dict):
        raise PrivateRecordError("native_record_invalid", "native sidecar is invalid")
    return _validate_content(parsed), data


def _existing_receipt(snapshot: JournalSnapshot, operation: str, key: str, payload_sha: str) -> dict[str, object] | None:
    path = _receipt_path(operation, key)
    if path not in snapshot.artifacts:
        return None
    receipt = _snapshot_json(snapshot, path, schema="scout-operation-receipt.schema.json")
    if receipt.get("operation") != operation or receipt.get("operation_key") != key or receipt.get("payload_sha256") != payload_sha:
        raise PrivateRecordError("native_operation_conflict", "operation key was already used with different payload")
    return receipt


def _authoritative_media_type(snapshot: JournalSnapshot, path: str) -> str:
    """Resolve media from a committed owner; filename suffixes are not authority."""
    parts = Path(path).parts
    if len(parts) == 3 and parts[0] == "references" and parts[1].startswith("ref_"):
        owner_path = f"references/{parts[1]}/reference.json"
        owner = _snapshot_json(snapshot, owner_path, schema="reference-record.schema.json")
        if parts[2] == "reference.json" and path == owner_path:
            return "application/json"
        source = owner.get("snapshot")
        if parts[2] == "source.txt" and isinstance(source, dict) and source.get("path") == path and isinstance(source.get("media_type"), str):
            return source["media_type"]
    if len(parts) == 3 and parts[0] == "run-inputs" and parts[1].startswith("input_"):
        owner_path = f"run-inputs/{parts[1]}/input.json"
        owner = _snapshot_json(snapshot, owner_path, schema="run-input-record.schema.json")
        if parts[2] == "input.json" and path == owner_path:
            return "application/json"
        source = owner.get("snapshot")
        if parts[2] == "source.txt" and isinstance(source, dict) and source.get("path") == path and isinstance(source.get("media_type"), str):
            return source["media_type"]
    if len(parts) == 4 and parts[0] == "records" and parts[2] == "blobs" and parts[3].startswith("revision_") and parts[3].endswith(".json"):
        revision_id = parts[3].removesuffix(".json")
        revision = _snapshot_json(snapshot, f"records/{parts[1]}/revisions/{revision_id}.json", schema="private-record-revision.schema.json")
        content = revision.get("content")
        blob = content.get("blob_ref") if isinstance(content, dict) else None
        if isinstance(blob, dict) and blob.get("path") == path and isinstance(blob.get("media_type"), str):
            return blob["media_type"]
    raise PrivateRecordError("native_record_scope_refused", "native source media has no authoritative owner")


def _assert_refs_authentic(content: object, snapshot: JournalSnapshot) -> None:
    """Verify supplied source/provenance refs against this pinned C1 snapshot."""
    if isinstance(content, list):
        for item in content:
            _assert_refs_authentic(item, snapshot)
        return
    if not isinstance(content, dict):
        return
    if set(("path", "content_sha256", "media_type", "size_bytes")) <= set(content):
        path, expected, size, media_type = content["path"], content["content_sha256"], content["size_bytes"], content["media_type"]
        if not isinstance(path, str) or not isinstance(expected, str) or not isinstance(size, int) or not isinstance(media_type, str):
            raise PrivateRecordError("native_record_scope_refused", "native source reference is invalid")
        data = snapshot.artifacts.get(path)
        if data is None or digest_imported_bytes(data) != expected or len(data) != size:
            raise PrivateRecordError("native_record_scope_refused", "native source reference is not committed exact evidence")
        if media_type != _authoritative_media_type(snapshot, path):
            raise PrivateRecordError("native_record_scope_refused", "native source media does not match committed evidence")
        return
    for item in content.values():
        _assert_refs_authentic(item, snapshot)


def _from_receipt(resolved: ResolvedWorkpad, snapshot: JournalSnapshot, receipt: Mapping[str, object]) -> NativeRecordResult:
    refs = receipt.get("artifact_refs")
    if not isinstance(refs, list):
        raise PrivateRecordError("native_operation_conflict", "native receipt is malformed")
    path = next((item.get("path") for item in refs if isinstance(item, dict) and isinstance(item.get("path"), str) and "/revisions/" in item["path"]), None)
    if not isinstance(path, str):
        raise PrivateRecordError("native_operation_conflict", "native receipt lacks its revision")
    revision = _snapshot_json(snapshot, path, schema="private-record-revision.schema.json")
    sidecar, _ = _sidecar(resolved, snapshot, revision)
    scope = sidecar["scope"]
    assert isinstance(scope, dict)
    return NativeRecordResult(str(revision["record_id"]), str(revision["revision_id"]), False, str(revision["state"]), dict(receipt), scope.get("task_context_id") if isinstance(scope.get("task_context_id"), str) else None)


def _publish(*, resolved: ResolvedWorkpad, operation_name: str, operation_key: str, intent: dict[str, object], record_id: str, revision_id: str, parent_revision: str | None, sidecar: dict[str, object], origin: str, actor: Mapping[str, str], state: str, relationships: list[dict[str, object]], uuid_factory: Callable[[], uuid.UUID], tool_binding: Mapping[str, object] | None = None, before_tool_revalidation: Callable[[], None] | None = None) -> NativeRecordResult:
    sidecar_bytes = canonical_json_bytes(sidecar)
    sidecar_path = f"records/{record_id}/blobs/{revision_id}.json"
    revision_path = f"records/{record_id}/revisions/{revision_id}.json"
    revision = {"schema_version": "1.0", "record_id": record_id, "revision_id": revision_id, "parent_revision": parent_revision, "project_id": resolved.project_id, "gig_id": resolved.gig_id, "kind": sidecar["kind"], "privacy_class": "private_sensitive", "origin": origin, "actor": dict(actor), "content": {"family": "jsl_blob", "blob_ref": _ref(sidecar_path, sidecar_bytes), "content_sha256": digest_imported_bytes(sidecar_bytes)}, "relationships": relationships, "created_at": _now(), "state": state}
    revision_bytes = canonical_json_bytes(revision)
    if not validate_serialized_contract("private-record-revision.schema.json", revision_bytes).valid:
        raise PrivateRecordError("native_record_invalid", "native revision failed the accepted outer contract")
    payload_sha = digest_imported_bytes(canonical_json_bytes(intent))

    def transaction(writer: Any) -> tuple[NativeRecordResult, bool]:
        snapshot = writer.snapshot(("records/", "references/", "run-inputs/", "manifests/capabilities/"))
        receipt = _existing_receipt(snapshot, operation_name, operation_key, payload_sha)
        if receipt is not None:
            return _from_receipt(resolved, snapshot, receipt), False
        if tool_binding is not None:
            if before_tool_revalidation is not None:
                before_tool_revalidation()
            if tool_binding.get("operation") != operation_name or tool_binding.get("actor") != dict(actor):
                raise PrivateRecordError("tool_binding_invalid", "tool binding does not match the requested native operation")
            # This is intentionally resolved by the shared publisher, not by a
            # caller-provided callback.  A direct caller can supply bytes but
            # cannot mint a binding that does not match committed authority.
            from .scout.tools import revalidate_tool_binding_at_publication

            verified_binding = dict(
                revalidate_tool_binding_at_publication(
                    resolved=resolved, snapshot=snapshot, binding=tool_binding
                )
            )
            if verified_binding != dict(tool_binding or {}):
                raise PrivateRecordError("tool_binding_invalid", "approved tool binding changed before publication")
        _assert_refs_authentic(sidecar, snapshot)
        scope = sidecar["scope"]
        assert isinstance(scope, dict)
        if scope["mode"] == "run_override":
            for path in snapshot.artifacts:
                if "/revisions/" not in path or not path.endswith(".json"):
                    continue
                existing_revision = _snapshot_json(snapshot, path, schema="private-record-revision.schema.json")
                if existing_revision.get("record_id") == record_id:
                    continue
                if not isinstance(existing_revision.get("content"), dict) or existing_revision["content"].get("family") != "jsl_blob":
                    continue
                existing_sidecar, _ = _sidecar(resolved, snapshot, existing_revision)
                existing_scope = existing_sidecar["scope"]
                if isinstance(existing_scope, dict) and existing_scope.get("mode") == "run_override" and existing_scope.get("task_context_id") == scope["task_context_id"]:
                    raise PrivateRecordError("native_record_scope_refused", "task context already belongs to a distinct override")
        if parent_revision is not None:
            current = _chain(resolved, snapshot, record_id)[-1]
            if current.get("revision_id") != parent_revision:
                raise PrivateRecordError("stale_parent", "native update does not name the current parent revision")
            if current.get("state") == "archived":
                raise PrivateRecordError("native_record_archived", "archived native records cannot be revised")
        receipt_path = _receipt_path(operation_name, operation_key)
        artifacts = (JournalArtifact(revision_path, revision_bytes), JournalArtifact(sidecar_path, sidecar_bytes))
        receipt = {"schema_version": "1.0", "operation_id": _id(EntityPrefix.OPERATION, uuid_factory), "project_id": resolved.project_id, "gig_id": resolved.gig_id, "operation": operation_name, "operation_key": operation_key, "payload_sha256": payload_sha, "outcome": "committed", "artifact_refs": [_ref(item.path, item.content) for item in artifacts], "created_at": _now()}
        if tool_binding is not None:
            receipt["tool_binding"] = dict(tool_binding)
        receipt_bytes = canonical_json_bytes(receipt)
        if not validate_serialized_contract("scout-operation-receipt.schema.json", receipt_bytes).valid:
            raise PrivateRecordError("native_operation_invalid", "native receipt failed the settled C1 contract")
        all_artifacts = (*artifacts, JournalArtifact(receipt_path, receipt_bytes))
        refs = [_ref(item.path, item.content) for item in all_artifacts]
        try:
            writer.record(JournalTransition(_id(EntityPrefix.HANDOFF, uuid_factory), "private_record_archived" if state == "archived" else "private_record_revised", "Committed native private record operation.", all_artifacts, {"operation": operation_name, "operation_key": operation_key, "payload_sha256": payload_sha, "artifact_refs": refs}))
        except JournalConflictError as exc:
            raise PrivateRecordError("native_operation_conflict", str(exc)) from exc
        scope = sidecar["scope"]
        assert isinstance(scope, dict)
        return NativeRecordResult(record_id, revision_id, True, state, receipt, scope.get("task_context_id") if isinstance(scope.get("task_context_id"), str) else None), True

    try:
        result, created = run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=transaction)
    except JournalConflictError as exc:
        raise PrivateRecordError("native_record_not_authenticated", str(exc)) from exc
    try:
        rebuild_scout_projection(resolved=resolved)
    except Exception:
        return NativeRecordResult(**{**result.__dict__, "projection_pending": True, "rebuild_action": "rebuild_index"})
    return result


def _actor(value: Mapping[str, str]) -> dict[str, str]:
    actor = dict(value)
    if set(actor) != {"kind", "id"} or actor["kind"] not in {"operator", "agent", "gigai"} or not actor["id"] or len(actor["id"]) > 255:
        raise PrivateRecordError("native_record_invalid", "native actor is invalid")
    return actor


def _scope(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise PrivateRecordError("native_record_invalid", "native scope is invalid")
    scope = dict(value)
    if scope.get("mode") == "saved_default" and scope == {"mode": "saved_default", "task_context_id": None, "base": None}:
        return scope
    if scope.get("mode") == "run_override" and isinstance(scope.get("task_context_id"), str) and _TASK_CONTEXT.fullmatch(scope["task_context_id"]) and isinstance(scope.get("base"), dict):
        base = scope["base"]
        if set(base) == {"record_id", "revision_id"}:
            record_id, revision_id = base["record_id"], base["revision_id"]
            if not isinstance(record_id, str) or not isinstance(revision_id, str):
                raise PrivateRecordError("native_record_invalid", "native override base IDs are invalid")
            try:
                validate_entity_id(record_id, expected_prefix=EntityPrefix.RECORD)
                validate_entity_id(revision_id, expected_prefix=EntityPrefix.REVISION)
            except ValueError as exc:
                raise PrivateRecordError("native_record_invalid", "native override base IDs are invalid") from exc
            return {"mode": "run_override", "task_context_id": scope["task_context_id"], "base": {"record_id": record_id, "revision_id": revision_id}}
    raise PrivateRecordError("native_record_invalid", "native scope is invalid")


def _create_native_record(*, home_root: Path, requested_target: Path | None, content: Mapping[str, object], actor: Mapping[str, str], origin: str, operation_key: str, scope: Mapping[str, object] | None = None, record_id: str | None = None, gig_id: str | None = None, uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4, _generated_task_context: bool = False, tool_binding: Mapping[str, object] | None = None, before_tool_revalidation: Callable[[], None] | None = None) -> NativeRecordResult:
    if origin not in {"user_reported", "imported", "inferred", "agent_supplied"}:
        raise PrivateRecordError("native_record_invalid", "native origin is invalid")
    resolved, actor_value = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id), _actor(actor)
    native = _validate_content(content)
    actual_scope = _scope(scope if scope is not None else native.get("scope"))
    native["scope"] = actual_scope
    stable_id = record_id or _id(EntityPrefix.RECORD, uuid_factory)
    try:
        validate_entity_id(stable_id, expected_prefix=EntityPrefix.RECORD)
    except ValueError as exc:
        raise PrivateRecordError("native_record_invalid", "native record ID is invalid") from exc
    revision_id = _id(EntityPrefix.REVISION, uuid_factory)
    relationships: list[dict[str, object]] = []
    if actual_scope["mode"] == "run_override":
        base = actual_scope["base"]
        assert isinstance(base, dict)
        # Confirm the pinned base is a committed exact revision before publication.
        snapshot = run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=lambda writer: writer.snapshot(("records/", "references/", "run-inputs/")))
        chain = _chain(resolved, snapshot, str(base["record_id"]))
        if not any(item.get("revision_id") == base["revision_id"] for item in chain):
            raise PrivateRecordError("native_record_scope_refused", "override base is not a committed revision")
        relationships.append({"relation": "task_override_base", "record_id": str(base["record_id"]), "revision_id": str(base["revision_id"])})
    intent_scope = actual_scope
    if _generated_task_context:
        # Generated identities must not turn an identical retry into a new
        # operation.  The committed sidecar retains the allocated exact ID;
        # the normalized receipt intent records that allocation was requested.
        intent_scope = {**actual_scope, "task_context_id": "allocated"}
    intent_content = native if not _generated_task_context else {**native, "scope": intent_scope}
    intent = {"kind": native["kind"], "content_sha256": digest_imported_bytes(canonical_json_bytes(intent_content)), "scope": intent_scope, "origin": origin, "actor": actor_value}
    if tool_binding is not None:
        intent["tool_binding"] = dict(tool_binding)
    if record_id is not None:
        intent["record_id"] = stable_id
    return _publish(resolved=resolved, operation_name="record_create", operation_key=operation_key, intent=intent, record_id=stable_id, revision_id=revision_id, parent_revision=None, sidecar=native, origin=origin, actor=actor_value, state="active", relationships=relationships, uuid_factory=uuid_factory, tool_binding=tool_binding, before_tool_revalidation=before_tool_revalidation)


def create_native_record(*, home_root: Path, requested_target: Path | None, content: Mapping[str, object], actor: Mapping[str, str], origin: str, operation_key: str, scope: Mapping[str, object] | None = None, record_id: str | None = None, gig_id: str | None = None, uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4, _generated_task_context: bool = False) -> NativeRecordResult:
    """Publish a direct built-in native operation without a tool authority binding."""
    return _create_native_record(home_root=home_root, requested_target=requested_target, content=content, actor=actor, origin=origin, operation_key=operation_key, scope=scope, record_id=record_id, gig_id=gig_id, uuid_factory=uuid_factory, _generated_task_context=_generated_task_context)


def create_native_record_from_tool(*, home_root: Path, requested_target: Path | None, content: Mapping[str, object], actor: Mapping[str, str], origin: str, operation_key: str, gig_id: str | None, tool_binding: Mapping[str, object], uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4, _before_tool_revalidation: Callable[[], None] | None = None) -> NativeRecordResult:
    """Publish one already-authorized tool request through the shared C1 service.

    This is intentionally not a general tool runner.  The publisher resolves
    the supplied binding independently through :mod:`gigai.scout.tools` while
    holding the journal writer lock; callers cannot supply a validator.
    """
    return _create_native_record(home_root=home_root, requested_target=requested_target, content=content, actor=actor, origin=origin, operation_key=operation_key, gig_id=gig_id, uuid_factory=uuid_factory, tool_binding=tool_binding, before_tool_revalidation=_before_tool_revalidation)


def update_native_record_from_tool(*, home_root: Path, requested_target: Path | None, record_id: str, parent_revision: str, content: Mapping[str, object], actor: Mapping[str, str], origin: str, operation_key: str, gig_id: str | None, tool_binding: Mapping[str, object], uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4, _before_tool_revalidation: Callable[[], None] | None = None) -> NativeRecordResult:
    """Append one approved agent-tool revision through the C1 publisher."""
    return _update_native_record(home_root=home_root, requested_target=requested_target, record_id=record_id, parent_revision=parent_revision, content=content, actor=actor, origin=origin, operation_key=operation_key, gig_id=gig_id, uuid_factory=uuid_factory, tool_binding=tool_binding, before_tool_revalidation=_before_tool_revalidation)


def archive_native_record_from_tool(*, home_root: Path, requested_target: Path | None, record_id: str, parent_revision: str, actor: Mapping[str, str], origin: str, operation_key: str, gig_id: str | None, tool_binding: Mapping[str, object], uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4, _before_tool_revalidation: Callable[[], None] | None = None) -> NativeRecordResult:
    """Append an approved agent-tool tombstone; no record bytes are deleted."""
    return _archive_native_record(home_root=home_root, requested_target=requested_target, record_id=record_id, parent_revision=parent_revision, actor=actor, origin=origin, operation_key=operation_key, gig_id=gig_id, uuid_factory=uuid_factory, tool_binding=tool_binding, before_tool_revalidation=_before_tool_revalidation)


def create_task_override(**kwargs: Any) -> NativeRecordResult:
    """Create an isolated override and allocate a retry-stable context in its receipt."""
    base = kwargs.pop("base", None)
    if not isinstance(base, Mapping):
        raise PrivateRecordError("native_record_invalid", "override base is required")
    base_value = dict(base)
    supplied_scope = kwargs.pop("scope", None)
    generated = supplied_scope is None
    if generated:
        factory = kwargs.get("uuid_factory", uuid.uuid4)
        scope = _scope({"mode": "run_override", "task_context_id": _id(EntityPrefix.TASK_CONTEXT, factory), "base": base_value})
    else:
        scope = _scope(supplied_scope)
        if scope.get("mode") != "run_override" or scope.get("base") != _scope({"mode": "run_override", "task_context_id": "task_context_00000000-0000-4000-8000-000000000000", "base": base_value})["base"]:
            raise PrivateRecordError("native_record_invalid", "explicit override scope must match its base")
    return create_native_record(**kwargs, scope=scope, _generated_task_context=generated)


def _update_native_record(*, home_root: Path, requested_target: Path | None, record_id: str, parent_revision: str, content: Mapping[str, object], actor: Mapping[str, str], origin: str, operation_key: str, gig_id: str | None = None, uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4, tool_binding: Mapping[str, object] | None = None, before_tool_revalidation: Callable[[], None] | None = None) -> NativeRecordResult:
    resolved, actor_value = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id), _actor(actor)
    native = _validate_content(content)
    snapshot = run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=lambda writer: writer.snapshot(("records/", "references/", "run-inputs/")))
    chain = _chain(resolved, snapshot, record_id)
    parent = next((item for item in chain if item.get("revision_id") == parent_revision), None)
    if parent is None:
        raise PrivateRecordError("stale_parent", "native update does not name a committed parent revision")
    old, _ = _sidecar(resolved, snapshot, parent)
    if native.get("kind") != old.get("kind") or native.get("scope") != old.get("scope"):
        raise PrivateRecordError("native_record_scope_refused", "updates cannot change native kind or scope")
    revision_id = _id(EntityPrefix.REVISION, uuid_factory)
    intent = {"record_id": record_id, "parent_revision": parent_revision, "kind": native["kind"], "content_sha256": digest_imported_bytes(canonical_json_bytes(native)), "scope": native["scope"], "origin": origin, "actor": actor_value}
    if tool_binding is not None:
        intent["tool_binding"] = dict(tool_binding)
    return _publish(resolved=resolved, operation_name="record_update", operation_key=operation_key, intent=intent, record_id=record_id, revision_id=revision_id, parent_revision=parent_revision, sidecar=native, origin=origin, actor=actor_value, state="active", relationships=list(parent.get("relationships", [])), uuid_factory=uuid_factory, tool_binding=tool_binding, before_tool_revalidation=before_tool_revalidation)


def update_native_record(*, home_root: Path, requested_target: Path | None, record_id: str, parent_revision: str, content: Mapping[str, object], actor: Mapping[str, str], origin: str, operation_key: str, gig_id: str | None = None, uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4) -> NativeRecordResult:
    return _update_native_record(home_root=home_root, requested_target=requested_target, record_id=record_id, parent_revision=parent_revision, content=content, actor=actor, origin=origin, operation_key=operation_key, gig_id=gig_id, uuid_factory=uuid_factory)


def _archive_native_record(*, home_root: Path, requested_target: Path | None, record_id: str, parent_revision: str, actor: Mapping[str, str], origin: str | None, operation_key: str, gig_id: str | None = None, uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4, tool_binding: Mapping[str, object] | None = None, before_tool_revalidation: Callable[[], None] | None = None) -> NativeRecordResult:
    resolved, actor_value = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id), _actor(actor)
    snapshot = run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=lambda writer: writer.snapshot(("records/", "references/", "run-inputs/")))
    chain = _chain(resolved, snapshot, record_id)
    parent = next((item for item in chain if item.get("revision_id") == parent_revision), None)
    if parent is None:
        raise PrivateRecordError("stale_parent", "native archive does not name a committed parent revision")
    native, _ = _sidecar(resolved, snapshot, parent)
    revision_id = _id(EntityPrefix.REVISION, uuid_factory)
    actual_origin = str(parent["origin"]) if origin is None else origin
    intent = {"record_id": record_id, "parent_revision": parent_revision, "kind": native["kind"], "content_sha256": digest_imported_bytes(canonical_json_bytes(native)), "scope": native["scope"], "origin": actual_origin, "actor": actor_value}
    if tool_binding is not None:
        intent["tool_binding"] = dict(tool_binding)
    return _publish(resolved=resolved, operation_name="record_archive", operation_key=operation_key, intent=intent, record_id=record_id, revision_id=revision_id, parent_revision=parent_revision, sidecar=native, origin=actual_origin, actor=actor_value, state="archived", relationships=list(parent.get("relationships", [])), uuid_factory=uuid_factory, tool_binding=tool_binding, before_tool_revalidation=before_tool_revalidation)


def archive_native_record(*, home_root: Path, requested_target: Path | None, record_id: str, parent_revision: str, actor: Mapping[str, str], operation_key: str, gig_id: str | None = None, uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4) -> NativeRecordResult:
    return _archive_native_record(home_root=home_root, requested_target=requested_target, record_id=record_id, parent_revision=parent_revision, actor=actor, origin=None, operation_key=operation_key, gig_id=gig_id, uuid_factory=uuid_factory)


def read_native_record(*, home_root: Path, requested_target: Path | None, record_id: str, revision_id: str | None = None, content: bool = False, gig_id: str | None = None) -> dict[str, object]:
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    snapshot = run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=lambda writer: writer.snapshot(("records/", "references/", "run-inputs/")))
    chain = _chain(resolved, snapshot, record_id)
    chosen = next((item for item in chain if item.get("revision_id") == revision_id), None) if revision_id else chain[-1]
    if chosen is None:
        raise PrivateRecordError("native_record_not_found", "native record revision was not found")
    sidecar, raw = _sidecar(resolved, snapshot, chosen)
    metadata = {key: chosen[key] for key in ("record_id", "revision_id", "parent_revision", "project_id", "gig_id", "kind", "privacy_class", "origin", "actor", "relationships", "created_at", "state")}
    metadata["scope"] = sidecar["scope"]
    return {**metadata, "content": raw} if content else metadata


def _native_rows(resolved: ResolvedWorkpad, snapshot: JournalSnapshot, *, include_archived: bool) -> list[tuple[dict[str, object], dict[str, object]]]:
    ids = sorted({Path(path).parts[1] for path in snapshot.artifacts if len(Path(path).parts) == 4 and Path(path).parts[0] == "records" and Path(path).parts[2] == "revisions"})
    result: list[tuple[dict[str, object], dict[str, object]]] = []
    for record_id in ids:
        current = _chain(resolved, snapshot, record_id)[-1]
        content = current.get("content")
        if not isinstance(content, dict) or content.get("family") != "jsl_blob":
            continue
        sidecar, _ = _sidecar(resolved, snapshot, current)
        if current["state"] == "archived" and not include_archived:
            continue
        result.append((current, sidecar))
    return result


def list_native_records(*, home_root: Path, requested_target: Path | None, gig_id: str | None = None, include_archived: bool = False) -> list[dict[str, object]]:
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    snapshot = run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=lambda writer: writer.snapshot(("records/", "references/", "run-inputs/")))
    return [{"record_id": current["record_id"], "revision_id": current["revision_id"], "kind": current["kind"], "state": current["state"], "scope": sidecar["scope"], "created_at": current["created_at"]} for current, sidecar in _native_rows(resolved, snapshot, include_archived=include_archived)]


def native_context(*, home_root: Path, requested_target: Path | None, gig_id: str | None = None) -> dict[str, object]:
    """Return the closed, payload-free context view for a fresh session."""
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    snapshot = run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=lambda writer: writer.snapshot(("records/", "references/", "run-inputs/")))
    native_rows = _native_rows(resolved, snapshot, include_archived=False)
    rows = [{"record_id": current["record_id"], "revision_id": current["revision_id"], "kind": current["kind"], "state": current["state"], "scope": sidecar["scope"], "created_at": current["created_at"]} for current, sidecar in native_rows]
    outstanding = []
    for current, content in native_rows:
        if current["kind"] != "experience_qa":
            continue
        payload = content["payload"]
        assert isinstance(payload, dict)
        outstanding.extend({"record_id": current["record_id"], "question_id": item["question_id"], "state": item["state"]} for item in payload["questions"] if item["state"] == "missing")
    return {"schema_version": "1.0", "records": rows, "outstanding_questions": outstanding}


__all__ = ["NativeRecordResult", "archive_native_record", "archive_native_record_from_tool", "create_native_record", "create_native_record_from_tool", "create_task_override", "list_native_records", "native_context", "read_native_record", "update_native_record", "update_native_record_from_tool"]
