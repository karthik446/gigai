"""SCOUT-03 private imports, immutable revisions, and disposable context views.

This module deliberately has no provider, network, package-export, or Run
executor dependency.  Its only authority mutation is one journal transition
that publishes immutable artifacts and an operation receipt together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid
from typing import Callable, Iterable, Mapping

from .canonical import EntityPrefix, canonical_json_bytes, digest_imported_bytes, parse_json_bytes, validate_entity_id
from .index import (
    JournalIndexError,
    database_lock,
    validate_state_database,
)
from .journal import (
    JournalArtifact,
    JournalConflictError,
    JournalSnapshot,
    JournalTransition,
    read_committed_artifact,
    record_transition,
    run_with_journal_writer,
)
from .validators import validate_serialized_contract
from .workpad import WORKPAD_LAYOUT_PATH, WORKPAD_V2_GITIGNORE, ResolvedWorkpad, resolve_workpad, workpad_layout_version


class PrivateRecordError(RuntimeError):
    """Safe, typed error for private record operations."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ImportResult:
    item_id: str
    created: bool
    record: dict[str, object]
    receipt: dict[str, object] | None
    projection_pending: bool = False
    rebuild_action: str | None = None


@dataclass(frozen=True)
class RevisionResult:
    record_id: str
    revision_id: str
    created: bool
    revision: dict[str, object]
    receipt: dict[str, object] | None
    projection_pending: bool = False
    rebuild_action: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _id(prefix: EntityPrefix, factory: callable) -> str:
    value = factory()
    if type(value) is not uuid.UUID or value.version != 4:
        raise PrivateRecordError("private_record_invalid", "ID factory must return UUIDv4")
    return f"{prefix.value}_{value}"


def _ref(path: str, data: bytes, media_type: str) -> dict[str, object]:
    return {"path": path, "content_sha256": digest_imported_bytes(data), "media_type": media_type, "size_bytes": len(data)}


def _label(value: str) -> str:
    rendered = value.strip()
    if not (1 <= len(rendered) <= 120) or any(ord(char) < 32 or char in "\r\n" for char in rendered):
        raise PrivateRecordError("reference_source_unsafe", "label must be 1-120 printable characters")
    return rendered


def _safe_text_file(path: Path, *, limit: int, code_prefix: str) -> tuple[bytes, str, str]:
    if path.is_symlink() or not path.is_file():
        raise PrivateRecordError(f"{code_prefix}_source_unsafe", "source must be one explicit regular file")
    current = path.parent
    while current != current.parent:
        if current.is_symlink():
            raise PrivateRecordError(f"{code_prefix}_source_unsafe", "source parent is redirected")
        current = current.parent
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError as exc:
        raise PrivateRecordError(f"{code_prefix}_source_unsafe", "source is unavailable") from exc
    if len(data) > limit:
        raise PrivateRecordError(f"{code_prefix}_too_large", "source exceeds the private import limit")
    if not data:
        raise PrivateRecordError(f"{code_prefix}_source_unsafe", "source must not be empty")
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PrivateRecordError(f"{code_prefix}_invalid_utf8", "source must be UTF-8 text") from exc
    suffix = path.suffix.lower()
    if suffix not in {".txt", ".md", ".markdown"}:
        raise PrivateRecordError(f"{code_prefix}_media_type_unsupported", "source must be plain text or Markdown")
    return data, ("text/markdown" if suffix in {".md", ".markdown"} else "text/plain"), path.name


def _require_v2(resolved: ResolvedWorkpad) -> None:
    if workpad_layout_version(resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id) != 2:
        raise PrivateRecordError("workpad_layout_migration_required", "migrate this workpad to layout v2 before private record operations")


def migrate_workpad_layout(*, workpad: Path, project_id: str, gig_id: str, uuid_factory: callable = uuid.uuid4) -> str:
    """Explicitly migrate a clean v1 workpad; no provisioner calls this implicitly."""

    version = workpad_layout_version(workpad, project_id=project_id, gig_id=gig_id)
    if version == 2:
        return "already_v2"
    if version != 1:
        raise PrivateRecordError("unsupported_schema_version", "workpad layout is unsupported")
    # v1 semantic roots are preserved.  An ignored draft or any potential v2
    # tracked collision is deliberately not relocated by this migration.
    # Only roots newly admitted by layout v2 are collisions.  v1 already owns
    # tools/ and reports/ (and its existing journal/manifests/run roots), so
    # rejecting those would make a supported v1 Gig impossible to migrate.
    for name in (
        "README.md", "CHANGELOG.md", "gig.py", "goalgraphs", "ui", "docs",
        "references", "run-inputs", "records", "indexes",
    ):
        candidate = workpad / name
        if candidate.exists() or candidate.is_symlink():
            raise PrivateRecordError("workpad_layout_collision", "existing v2 root requires explicit reconciliation")
    marker = {
        "schema_version": "2.0", "layout_version": 2, "project_id": project_id,
        "gig_id": gig_id, "ignore_sha256": digest_imported_bytes(WORKPAD_V2_GITIGNORE),
    }
    marker_bytes = canonical_json_bytes(marker)
    if not validate_serialized_contract("workpad-layout.schema.json", marker_bytes).valid:
        raise PrivateRecordError("workpad_layout_invalid", "derived v2 marker failed strict validation")
    try:
        entry = record_transition(
            workpad=workpad, project_id=project_id, gig_id=gig_id,
            handoff_id=_id(EntityPrefix.HANDOFF, uuid_factory),
            transition="workpad_layout_migrated", body="Migrated the authenticated workpad layout from v1 to v2.",
            artifacts=(JournalArtifact(".gitignore", WORKPAD_V2_GITIGNORE), JournalArtifact(WORKPAD_LAYOUT_PATH, marker_bytes)),
            front_matter={
                "layout_version": 2,
                "layout_marker": WORKPAD_LAYOUT_PATH,
                "artifact_refs": [
                    _ref(".gitignore", WORKPAD_V2_GITIGNORE, "text/plain"),
                    _ref(WORKPAD_LAYOUT_PATH, marker_bytes, "application/json"),
                ],
            },
        )
    except JournalConflictError as exc:
        raise PrivateRecordError("workpad_layout_migration_refused", str(exc)) from exc
    return entry.commit


def _resolved(*, home_root: Path, requested_target: Path | None, gig_id: str | None) -> ResolvedWorkpad:
    resolved = resolve_workpad(home_root=home_root, requested_target=requested_target, gig_id=gig_id, allow_semantic_state=True)
    _require_v2(resolved)
    return resolved


def _receipt_path(operation: str, key: str) -> str:
    if not key or len(key) > 160 or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-" for char in key):
        raise PrivateRecordError("private_operation_invalid", "operation key is invalid")
    return f"records/operations/{operation}-{digest_imported_bytes(key.encode()).removeprefix('sha256:')}.json"


def _read_json(root: Path, path: str, *, code: str) -> dict[str, object]:
    candidate = root / path
    if candidate.is_symlink() or not candidate.is_file():
        raise PrivateRecordError(code, "private record is unavailable")
    try:
        payload = parse_json_bytes(candidate.read_bytes())
    except (OSError, ValueError) as exc:
        raise PrivateRecordError(code, "private record is invalid") from exc
    if not isinstance(payload, dict):
        raise PrivateRecordError(code, "private record is invalid")
    return payload


def _private_snapshot(resolved: ResolvedWorkpad) -> JournalSnapshot:
    try:
        return run_with_journal_writer(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            operation=lambda writer: writer.snapshot(("records/", "references/", "run-inputs/")),
        )
    except JournalConflictError as exc:
        raise PrivateRecordError("private_record_not_authenticated", str(exc)) from exc


def _committed_json(resolved: ResolvedWorkpad, path: str, *, code: str, schema: str | None = None, snapshot: JournalSnapshot | None = None) -> dict[str, object]:
    try:
        if snapshot is None:
            data, _commit = read_committed_artifact(
                workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=path
            )
        else:
            data = snapshot.artifacts[path]
        if schema is not None and not validate_serialized_contract(schema, data).valid:
            raise ValueError
        value = parse_json_bytes(data)
    except (JournalConflictError, KeyError, OSError, ValueError) as exc:
        raise PrivateRecordError(code, "private record is not authenticated") from exc
    if not isinstance(value, dict):
        raise PrivateRecordError(code, "private record is invalid")
    return value


def _existing_receipt(resolved: ResolvedWorkpad, operation: str, key: str, payload_sha: str, snapshot: JournalSnapshot) -> dict[str, object] | None:
    path = _receipt_path(operation, key)
    if path not in snapshot.artifacts:
        return None
    receipt = _committed_json(resolved, path, code="private_operation_conflict", schema="scout-operation-receipt.schema.json", snapshot=snapshot)
    if receipt.get("operation") != operation or receipt.get("operation_key") != key:
        raise PrivateRecordError("private_operation_conflict", "operation receipt identity differs from its path")
    if receipt.get("payload_sha256") != payload_sha:
        raise PrivateRecordError("private_operation_conflict", "operation key was already used with different payload")
    return receipt


def _receipt_for_artifact(resolved: ResolvedWorkpad, operation: str, artifact_path: str, snapshot: JournalSnapshot) -> dict[str, object] | None:
    for path in sorted(item for item in snapshot.artifacts if item.startswith("records/operations/") and item.endswith(".json")):
        receipt = _committed_json(resolved, path, code="private_operation_conflict", schema="scout-operation-receipt.schema.json", snapshot=snapshot)
        if receipt.get("operation") != operation:
            continue
        refs = receipt.get("artifact_refs")
        if isinstance(refs, list) and any(isinstance(item, dict) and item.get("path") == artifact_path for item in refs):
            return receipt
    return None


def _publish(*, resolved: ResolvedWorkpad, operation: str, key: str, payload: dict[str, object], artifacts: tuple[JournalArtifact, ...], transition: str, uuid_factory: callable, parent_record_id: str | None = None, parent_revision: str | None = None, equivalent_artifact_path: Callable[[JournalSnapshot], str | None] | None = None) -> tuple[dict[str, object], bool, bool]:
    payload_sha = digest_imported_bytes(canonical_json_bytes(payload))
    def publish(writer: object) -> tuple[dict[str, object], bool]:
        snapshot = writer.snapshot(("records/", "references/", "run-inputs/"))  # type: ignore[attr-defined]
        existing = _existing_receipt(resolved, operation, key, payload_sha, snapshot)
        if existing is not None:
            return existing, False
        if equivalent_artifact_path is not None:
            existing_path = equivalent_artifact_path(snapshot)
            if existing_path is not None:
                existing = _receipt_for_artifact(resolved, operation, existing_path, snapshot)
                if existing is None:
                    raise PrivateRecordError("private_operation_conflict", "equivalent import lacks its authenticated receipt")
                return existing, False
        if parent_record_id is not None:
            revisions = list_revisions(resolved=resolved, record_id=parent_record_id, snapshot=snapshot)
            current = revisions[-1] if revisions else None
            if (current is None and parent_revision is not None) or (
                current is not None and current.get("revision_id") != parent_revision
            ):
                raise PrivateRecordError("stale_parent", "record update does not name the current parent revision")
        receipt_path = _receipt_path(operation, key)
        receipt = {
            "schema_version": "1.0", "operation_id": _id(EntityPrefix.OPERATION, uuid_factory),
            "project_id": resolved.project_id, "gig_id": resolved.gig_id, "operation": operation,
            "operation_key": key, "payload_sha256": payload_sha, "outcome": "committed",
            "artifact_refs": [_ref(item.path, item.content, "application/json" if item.path.endswith(".json") else "text/plain") for item in artifacts], "created_at": _now(),
        }
        receipt_bytes = canonical_json_bytes(receipt)
        if not validate_serialized_contract("scout-operation-receipt.schema.json", receipt_bytes).valid:
            raise PrivateRecordError("private_operation_invalid", "derived operation receipt failed strict validation")
        all_artifacts = (*artifacts, JournalArtifact(receipt_path, receipt_bytes))
        refs = [_ref(item.path, item.content, "application/json" if item.path.endswith(".json") else "text/plain") for item in all_artifacts]
        try:
            writer.record(JournalTransition(_id(EntityPrefix.HANDOFF, uuid_factory), transition, f"Committed private {operation} operation.", all_artifacts, {"operation": operation, "operation_key": key, "payload_sha256": payload_sha, "artifact_refs": refs}))  # type: ignore[attr-defined]
        except JournalConflictError as exc:
            raise PrivateRecordError("private_operation_conflict", str(exc)) from exc
        return receipt, True

    try:
        receipt, created = run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=publish)
    except JournalConflictError as exc:
        raise PrivateRecordError("private_record_not_authenticated", str(exc)) from exc
    if not created:
        # Retrying an already committed request is also the bounded recovery
        # path for a previous projection failure; it never appends authority.
        try:
            rebuild_scout_projection(resolved=resolved)
        except Exception:
            return receipt, False, True
        return receipt, False, False
    try:
        rebuild_scout_projection(resolved=resolved)
    except Exception:
        # Authority is already sealed.  The retry path above returns the same
        # receipt and callers get an explicit rebuild action rather than an
        # invented failure or a duplicate application event.
        return receipt, True, True
    return receipt, True, False


def import_reference(*, home_root: Path, requested_target: Path | None, kind: str, source: Path, label: str | None = None, gig_id: str | None = None, operation_key: str | None = None, uuid_factory: callable = uuid.uuid4) -> ImportResult:
    if kind not in {"resume", "project_evidence", "role_history", "cover_letter"}:
        raise PrivateRecordError("reference_kind_unsupported", "reference kind is unsupported")
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    data, media_type, basename = _safe_text_file(source, limit=1_048_576, code_prefix="reference")
    digest = digest_imported_bytes(data)
    reference_id = _id(EntityPrefix.REFERENCE, uuid_factory)
    record_path, source_path = f"references/{reference_id}/reference.json", f"references/{reference_id}/source.txt"
    record = {"schema_version":"1.0","reference_id":reference_id,"project_id":resolved.project_id,"state":"sealed","kind":kind,"privacy_class":"private_sensitive","label":_label(label or basename),"origin":"local_file","media_type":media_type,"size_bytes":len(data),"content_sha256":digest,"snapshot":_ref(source_path,data,media_type),"created_at":_now(),"created_by":{"kind":"operator","id":"local-user","model_target":None}}
    encoded = canonical_json_bytes(record)
    if not validate_serialized_contract("reference-record.schema.json", encoded).valid:
        raise PrivateRecordError("reference_invalid", "derived reference failed strict validation")
    key = operation_key or f"reference:{kind}:{digest}"
    def equivalent(snapshot: JournalSnapshot) -> str | None:
        for path in sorted(item for item in snapshot.artifacts if item.startswith("references/ref_") and item.endswith("/reference.json")):
            previous = _committed_json(resolved, path, code="reference_not_found", schema="reference-record.schema.json", snapshot=snapshot)
            if previous.get("project_id") == resolved.project_id and previous.get("kind") == kind and previous.get("content_sha256") == digest and previous.get("media_type") == media_type:
                return path
        return None
    receipt, created, pending = _publish(resolved=resolved, operation="reference_add", key=key, payload={"kind":kind,"digest":digest,"label":record["label"],"media_type":media_type,"actor":record["created_by"]}, artifacts=(JournalArtifact(record_path, encoded), JournalArtifact(source_path, data)), transition="private_reference_imported", uuid_factory=uuid_factory, equivalent_artifact_path=equivalent)
    if not created:
        refs = receipt.get("artifact_refs")
        if not isinstance(refs, list) or not refs or not isinstance(refs[0], dict):
            raise PrivateRecordError("private_operation_conflict", "original reference receipt is malformed")
        original = str(refs[0].get("path", "")).split("/")
        if len(original) != 3:
            raise PrivateRecordError("private_operation_conflict", "original reference receipt is malformed")
        record = _committed_json(resolved, "/".join(original), code="reference_not_found", schema="reference-record.schema.json")
        reference_id = str(record["reference_id"])
    return ImportResult(reference_id, created, record, receipt, pending, "rebuild_index" if pending else None)


def import_run_input(*, home_root: Path, requested_target: Path | None, data: bytes, label: str = "pasted-job-description", media_type: str = "text/plain", gig_id: str | None = None, operation_key: str | None = None, uuid_factory: callable = uuid.uuid4) -> ImportResult:
    if media_type not in {"text/plain", "text/markdown"}:
        raise PrivateRecordError("run_input_kind_unsupported", "Run input media type is unsupported")
    if not (0 < len(data) <= 262144):
        raise PrivateRecordError("run_input_too_large", "Run input exceeds the private import limit")
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PrivateRecordError("run_input_invalid_utf8", "Run input must be UTF-8 text") from exc
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    digest = digest_imported_bytes(data)
    input_id = _id(EntityPrefix.RUN_INPUT, uuid_factory)
    record_path, source_path = f"run-inputs/{input_id}/input.json", f"run-inputs/{input_id}/source.txt"
    record = {"schema_version":"1.0","run_input_id":input_id,"project_id":resolved.project_id,"state":"sealed","kind":"job_description","privacy_class":"private_sensitive","label":_label(label),"origin":"operator_paste","media_type":media_type,"size_bytes":len(data),"content_sha256":digest,"snapshot":_ref(source_path,data,media_type),"created_at":_now(),"created_by":{"kind":"operator","id":"local-user","model_target":None}}
    encoded = canonical_json_bytes(record)
    if not validate_serialized_contract("run-input-record.schema.json", encoded).valid:
        raise PrivateRecordError("run_input_invalid", "derived Run input failed strict validation")
    key = operation_key or f"run-input:{digest}"
    def equivalent(snapshot: JournalSnapshot) -> str | None:
        for path in sorted(item for item in snapshot.artifacts if item.startswith("run-inputs/input_") and item.endswith("/input.json")):
            previous = _committed_json(resolved, path, code="run_input_not_found", schema="run-input-record.schema.json", snapshot=snapshot)
            if previous.get("project_id") == resolved.project_id and previous.get("content_sha256") == digest and previous.get("media_type") == media_type:
                return path
        return None
    receipt, created, pending = _publish(resolved=resolved, operation="run_input_add", key=key, payload={"kind":"job_description","digest":digest,"label":record["label"],"media_type":media_type,"actor":record["created_by"]}, artifacts=(JournalArtifact(record_path, encoded), JournalArtifact(source_path, data)), transition="run_input_imported", uuid_factory=uuid_factory, equivalent_artifact_path=equivalent)
    if not created:
        refs = receipt.get("artifact_refs")
        if not isinstance(refs, list) or not refs or not isinstance(refs[0], dict):
            raise PrivateRecordError("private_operation_conflict", "original Run input receipt is malformed")
        original = str(refs[0].get("path", "")).split("/")
        if len(original) != 3:
            raise PrivateRecordError("private_operation_conflict", "original Run input receipt is malformed")
        record = _committed_json(resolved, "/".join(original), code="run_input_not_found", schema="run-input-record.schema.json")
        input_id = str(record["run_input_id"])
    return ImportResult(input_id, created, record, receipt, pending, "rebuild_index" if pending else None)


def list_imports(*, home_root: Path, requested_target: Path | None, family: str, gig_id: str | None = None) -> list[dict[str, object]]:
    """List safe metadata only; snapshot locations and bytes remain private."""
    if family not in {"reference", "run_input"}:
        raise PrivateRecordError("private_record_invalid", "import family is invalid")
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    prefix, suffix, schema = (
        ("references/ref_", "/reference.json", "reference-record.schema.json")
        if family == "reference" else ("run-inputs/input_", "/input.json", "run-input-record.schema.json")
    )
    result = []
    snapshot = _private_snapshot(resolved)
    for path in sorted(item for item in snapshot.artifacts if item.startswith(prefix) and item.endswith(suffix)):
        record = _committed_json(resolved, path, code=f"{family}_not_found", schema=schema, snapshot=snapshot)
        if record.get("project_id") != resolved.project_id:
            raise PrivateRecordError(f"{family}_project_mismatch", "import record belongs to another project")
        result.append({key:value for key,value in record.items() if key != "snapshot"})
    return result


def read_import(*, home_root: Path, requested_target: Path | None, family: str, item_id: str, gig_id: str | None = None) -> dict[str, object]:
    values = list_imports(home_root=home_root, requested_target=requested_target, family=family, gig_id=gig_id)
    key = "reference_id" if family == "reference" else "run_input_id"
    selected = next((item for item in values if item.get(key) == item_id), None)
    if selected is None:
        raise PrivateRecordError("reference_not_found" if family == "reference" else "run_input_not_found", "requested import was not found")
    return selected


def _content_for_reference(resolved: ResolvedWorkpad, family: str, item_id: str, *, snapshot: JournalSnapshot | None = None) -> tuple[dict[str, object], bytes]:
    prefix, name, record_name = ("references", "ref_", "reference.json") if family == "g45_reference" else ("run-inputs", "input_", "input.json")
    if not item_id.startswith(name):
        raise PrivateRecordError("private_record_invalid", "content ID has the wrong family")
    record_path = f"{prefix}/{item_id}/{record_name}"
    schema = "reference-record.schema.json" if family == "g45_reference" else "run-input-record.schema.json"
    selected = snapshot or _private_snapshot(resolved)
    record = _committed_json(resolved, record_path, code="private_record_not_found", schema=schema, snapshot=selected)
    raw = selected.artifacts.get(record_path)
    if raw is None:
        raise PrivateRecordError("private_record_scope_refused", "selected content record is not committed")
    if record.get("project_id") != resolved.project_id:
        raise PrivateRecordError("private_record_scope_refused", "selected content belongs to another scope or is invalid")
    snapshot = record.get("snapshot")
    if not isinstance(snapshot, dict):
        raise PrivateRecordError("private_record_invalid", "selected content has no snapshot")
    source_path = snapshot.get("path")
    if not isinstance(source_path, str):
        raise PrivateRecordError("private_record_invalid", "selected snapshot is invalid")
    data = selected.artifacts.get(source_path)
    if data is None:
        raise PrivateRecordError("private_record_digest_mismatch", "selected snapshot is not committed")
    if digest_imported_bytes(data) != snapshot.get("content_sha256") or len(data) != snapshot.get("size_bytes"):
        raise PrivateRecordError("private_record_digest_mismatch", "selected snapshot changed or is unavailable")
    return ({"family": family, ("reference_id" if family == "g45_reference" else "run_input_id"): item_id, "record_ref": _ref(record_path, raw, "application/json"), "snapshot_ref": dict(snapshot)}, data)


def create_record(*, home_root: Path, requested_target: Path | None, kind: str, content_family: str, content_id: str, actor: Mapping[str, str], origin: str, operation_key: str, record_id: str | None = None, parent_revision: str | None = None, gig_id: str | None = None, uuid_factory: callable = uuid.uuid4) -> RevisionResult:
    if kind not in {"profile_preferences", "experience_qa", "imported_reference", "supplied_source", "selected_conversation"} or origin not in {"user_reported","imported","inferred","agent_supplied"}:
        raise PrivateRecordError("private_record_invalid", "record kind or origin is unsupported")
    if set(actor) != {"kind", "id"} or actor.get("kind") not in {"operator","agent","gigai"} or not actor.get("id"):
        raise PrivateRecordError("private_record_invalid", "record actor is invalid")
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    content, _content_bytes = _content_for_reference(resolved, content_family, content_id)
    stable_id = record_id or _id(EntityPrefix.RECORD, uuid_factory)
    try:
        validate_entity_id(stable_id, expected_prefix=EntityPrefix.RECORD)
    except ValueError as exc:
        raise PrivateRecordError("private_record_invalid", "record ID is invalid") from exc
    revision_id = _id(EntityPrefix.REVISION, uuid_factory)
    revision = {"schema_version":"1.0","record_id":stable_id,"revision_id":revision_id,"parent_revision":parent_revision,"project_id":resolved.project_id,"gig_id":resolved.gig_id,"kind":kind,"privacy_class":"private_sensitive","origin":origin,"actor":dict(actor),"content":content,"relationships":[],"created_at":_now(),"state":"active"}
    encoded = canonical_json_bytes(revision)
    if not validate_serialized_contract("private-record-revision.schema.json", encoded).valid:
        raise PrivateRecordError("private_record_invalid", "derived record revision failed strict validation")
    path = f"records/{stable_id}/revisions/{revision_id}.json"
    operation = "record_create" if parent_revision is None else "record_update"
    intent = {"parent_revision":parent_revision,"kind":kind,"origin":origin,"actor":dict(actor),"content":content}
    if record_id is not None:
        intent["record_id"] = stable_id
    receipt, created, pending = _publish(resolved=resolved, operation=operation, key=operation_key, payload=intent, artifacts=(JournalArtifact(path, encoded),), transition="private_record_revised", uuid_factory=uuid_factory, parent_record_id=stable_id, parent_revision=parent_revision)
    if not created:
        refs = receipt.get("artifact_refs")
        if not isinstance(refs, list) or not refs or not isinstance(refs[0], dict):
            raise PrivateRecordError("private_operation_conflict", "original record receipt is malformed")
        revision = _committed_json(resolved, str(refs[0].get("path")), code="private_record_not_found", schema="private-record-revision.schema.json")
        stable_id, revision_id = str(revision["record_id"]), str(revision["revision_id"])
    return RevisionResult(stable_id, revision_id, created, revision, receipt, pending, "rebuild_index" if pending else None)


def list_revisions(*, resolved: ResolvedWorkpad, record_id: str, snapshot: JournalSnapshot | None = None) -> list[dict[str, object]]:
    try:
        validate_entity_id(record_id, expected_prefix=EntityPrefix.RECORD)
    except ValueError as exc:
        raise PrivateRecordError("private_record_not_found", "record ID is invalid") from exc
    selected = snapshot or _private_snapshot(resolved)
    prefix = f"records/{record_id}/revisions/"
    values: dict[str, dict[str, object]] = {}
    for path in sorted(item for item in selected.artifacts if item.startswith(prefix) and item.endswith(".json")):
        payload = _committed_json(resolved, path, code="private_record_invalid", schema="private-record-revision.schema.json", snapshot=selected)
        if not isinstance(payload, dict) or payload.get("record_id") != record_id or payload.get("project_id") != resolved.project_id or payload.get("gig_id") != resolved.gig_id:
            raise PrivateRecordError("private_record_scope_refused", "record revision belongs to another scope")
        revision_id = payload.get("revision_id")
        if not isinstance(revision_id, str) or revision_id in values:
            raise PrivateRecordError("private_record_invalid", "record revision identity is invalid")
        values[revision_id] = payload
    if not values:
        return []
    roots = [item for item in values.values() if item.get("parent_revision") is None]
    if len(roots) != 1:
        raise PrivateRecordError("private_record_invalid", "record revision chain has ambiguous roots")
    ordered = [roots[0]]
    seen = {str(roots[0]["revision_id"])}
    while True:
        parent = ordered[-1]["revision_id"]
        children = [item for item in values.values() if item.get("parent_revision") == parent]
        if len(children) > 1:
            raise PrivateRecordError("private_record_invalid", "record revision chain branches")
        if not children:
            break
        child = children[0]
        child_id = str(child["revision_id"])
        if child_id in seen:
            raise PrivateRecordError("private_record_invalid", "record revision chain cycles")
        seen.add(child_id)
        ordered.append(child)
    if len(seen) != len(values):
        raise PrivateRecordError("private_record_invalid", "record revision chain is broken")
    return ordered


def read_record(*, home_root: Path, requested_target: Path | None, record_id: str, revision_id: str | None = None, content: bool = False, gig_id: str | None = None) -> dict[str, object]:
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    selected = _private_snapshot(resolved)
    revisions = list_revisions(resolved=resolved, record_id=record_id, snapshot=selected)
    chosen = next((item for item in revisions if item.get("revision_id") == revision_id), None) if revision_id else (revisions[-1] if revisions else None)
    if chosen is None:
        raise PrivateRecordError("private_record_not_found", "record revision was not found")
    safe = {key: value for key, value in chosen.items() if key not in {"content"}}
    if not content:
        return safe
    reference = chosen["content"]
    assert isinstance(reference, dict)
    family = reference.get("family")
    item_id = reference.get("reference_id") if family == "g45_reference" else reference.get("run_input_id")
    if not isinstance(family, str) or not isinstance(item_id, str):
        raise PrivateRecordError("private_record_invalid", "native content read is not implemented in SCOUT-03")
    _content, authenticated_bytes = _content_for_reference(resolved, family, item_id, snapshot=selected)
    return {**safe, "content": authenticated_bytes}


def rebuild_scout_projection(*, resolved: ResolvedWorkpad) -> None:
    """Rebuild only SCOUT-owned tables and redacted context from committed files."""
    selected = _private_snapshot(resolved)
    journal_head = selected.head
    records: list[dict[str, object]] = []
    record_ids = {
        Path(path).parts[1]
        for path in selected.artifacts
        if len(Path(path).parts) == 4 and Path(path).parts[0] == "records" and Path(path).parts[2] == "revisions"
    }
    for record_id in sorted(record_ids):
        revisions = list_revisions(resolved=resolved, record_id=record_id, snapshot=selected)
        if revisions:
            records.append(revisions[-1])
    operations = [
        (
            path,
            _committed_json(
                resolved, path, code="private_operation_conflict",
                schema="scout-operation-receipt.schema.json", snapshot=selected,
            ),
        )
        for path in sorted(item for item in selected.artifacts if item.startswith("records/operations/") and item.endswith(".json"))
    ]
    context = {"schema_version":"1.0","project_id":resolved.project_id,"gig_id":resolved.gig_id,"journal_head":journal_head,"records":[{"record_id":item["record_id"],"revision_id":item["revision_id"],"kind":item["kind"],"state":item["state"],"location":f"records/{item['record_id']}/revisions/{item['revision_id']}.json","outstanding_questions":[]} for item in records]}
    with database_lock(resolved.path):
        validate_state_database(resolved.path / "state.sqlite")
        connection = sqlite3.connect(resolved.path / "state.sqlite")
        try:
            for table in ("scout_records", "scout_operations", "scout_meta"):
                connection.execute(f"CREATE TABLE IF NOT EXISTS {table} (key TEXT PRIMARY KEY, payload BLOB NOT NULL)")
            connection.execute("DELETE FROM scout_records")
            connection.executemany("INSERT INTO scout_records(key,payload) VALUES (?,?)", [(str(item["record_id"]), canonical_json_bytes({key:value for key,value in item.items() if key != "content"})) for item in records])
            connection.execute("DELETE FROM scout_operations")
            connection.executemany(
                "INSERT INTO scout_operations(key,payload) VALUES (?,?)",
                [(path, canonical_json_bytes(item)) for path, item in operations],
            )
            connection.execute("DELETE FROM scout_meta")
            connection.executemany(
                "INSERT INTO scout_meta(key,payload) VALUES (?,?)",
                (("context", canonical_json_bytes(context)), ("cursor", canonical_json_bytes({"schema_version": "1.0", "journal_head": journal_head}))),
            )
            connection.commit()
        finally:
            connection.close()
        index_path = resolved.path / "indexes"
        if index_path.is_symlink() or (index_path.exists() and not index_path.is_dir()):
            raise JournalIndexError("context index root is unavailable")
        index_path.mkdir(mode=0o700, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".context-", suffix=".tmp", dir=index_path)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(canonical_json_bytes(context))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, index_path / "context.json")
        finally:
            temporary.unlink(missing_ok=True)


def select_exact_inputs(*, home_root: Path, requested_target: Path | None, record_refs: Iterable[tuple[str, str | None]], gig_id: str | None = None) -> list[dict[str, object]]:
    """Resolve caller-named exact revisions; this creates no Plan and no disclosure grant."""
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    selected = []
    seen: set[tuple[object, object]] = set()
    for record_id, revision_id in record_refs:
        value = read_record(home_root=home_root, requested_target=requested_target, record_id=record_id, revision_id=revision_id, content=False, gig_id=resolved.gig_id)
        full = next(item for item in list_revisions(resolved=resolved, record_id=record_id) if item["revision_id"] == value["revision_id"])
        ref = full["content"]
        assert isinstance(ref, dict)
        snapshot = ref.get("snapshot_ref")
        assert isinstance(snapshot, dict)
        identity = (snapshot.get("path"), snapshot.get("content_sha256"))
        if identity not in seen:
            seen.add(identity)
            selected.append({"record_id":record_id,"revision_id":value["revision_id"],"content":ref})
    return selected


__all__ = ["ImportResult", "PrivateRecordError", "RevisionResult", "create_record", "import_reference", "import_run_input", "list_imports", "migrate_workpad_layout", "read_import", "read_record", "rebuild_scout_projection", "select_exact_inputs"]
