"""Journal-authoritative application events (SCOUT-09)."""

from __future__ import annotations
from datetime import UTC, datetime
import uuid
from pathlib import Path
import re
import subprocess
from typing import Any
from .canonical import (
    canonical_json_bytes,
    digest_imported_bytes,
    parse_json_bytes,
    parse_json_front_matter,
)
from .journal import (
    JournalArtifact,
    JournalConflictError,
    JournalSnapshot,
    JournalTransition,
    read_committed_artifact,
    run_with_journal_writer,
)
from .validators import validate_serialized_contract
from .workpad import ResolvedWorkpad, resolve_workpad

EVENT_KINDS = {
    "saved",
    "applied",
    "interview_scheduled",
    "offer_received",
    "rejected",
    "withdrawn",
}
_SCOUT_DOCUMENT_KINDS = frozenset({"resume", "cover_letter"})
_SCOUT_DOCUMENT_ID = re.compile(r"^record_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SCOUT_REVISION_ID = re.compile(r"^revision_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SCOUT_RUN_ID = re.compile(r"^run_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SCOUT_GOAL_ID = re.compile(r"^goal_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SCOUT_INVOCATION_ID = re.compile(r"^inv_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SCOUT_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ApplicationEventError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _uuid(prefix):
    return f"{prefix}_{uuid.uuid4()}"


def _digest(value):
    return digest_imported_bytes(canonical_json_bytes(value))


def _scope(data):
    return {
        "project_id": data["project_id"],
        "gig_id": data["gig_id"],
        "opportunity_ref": data["opportunity_ref"],
        "event_kind": data["event_kind"],
        "occurred_at": data["occurred_at"],
        "timezone": data["timezone"],
        "document_refs": data["document_refs"],
        "notes": data.get("notes"),
        "supersedes": data.get("supersedes"),
    }


def _resolved(home_root, target, gig):
    return resolve_workpad(
        home_root=home_root,
        requested_target=target,
        gig_id=gig,
        allow_semantic_state=True,
    )


def _journal_sequence(root: Path, path: str) -> int:
    commits = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%H", "--", path],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    ).stdout.splitlines()
    if len(commits) != 1:
        raise ApplicationEventError(
            "application_event_publication_invalid",
            "application event has ambiguous journal publication",
        )
    names = subprocess.run(
        ["git", "-C", str(root), "show", "--format=", "--name-only", commits[0]],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    ).stdout.splitlines()
    handoffs = [
        name for name in names if name.startswith("handoffs/") and name.endswith(".txt")
    ]
    if len(handoffs) != 1:
        raise ApplicationEventError(
            "application_event_publication_invalid",
            "application event publication lacks one handoff",
        )
    raw = subprocess.run(
        ["git", "-C", str(root), "show", f"{commits[0]}:{handoffs[0]}"],
        capture_output=True,
        check=False,
        shell=False,
    ).stdout
    metadata, _ = parse_json_front_matter(raw)
    sequence = metadata.get("sequence")
    if type(sequence) is not int or sequence < 1:
        raise ApplicationEventError(
            "application_event_publication_invalid",
            "application event publication lacks journal sequence",
        )
    return sequence


def _validate_event(value, path, project_id, gig_id):
    if (
        not isinstance(value, dict)
        or not validate_serialized_contract(
            "application-event.schema.json", canonical_json_bytes(value)
        ).valid
    ):
        raise ApplicationEventError(
            "application_event_invalid",
            "committed application event failed strict schema validation",
        )
    event_id = value.get("event_id")
    if (
        Path(path).name != f"{event_id}.json"
        or value.get("project_id") != project_id
        or value.get("gig_id") != gig_id
    ):
        raise ApplicationEventError(
            "application_event_scope_refused",
            "committed application event path or scope differs",
        )
    requested = _digest(_scope(value))
    if value.get("requested_event_sha256") != requested:
        raise ApplicationEventError(
            "application_event_hash_mismatch",
            "requested event digest differs from event content",
        )
    semantic = dict(value)
    for key in ("event_id", "operation_key", "payload_sha256", "recorded_at"):
        semantic.pop(key, None)
    if value.get("payload_sha256") != _digest(semantic):
        raise ApplicationEventError(
            "application_event_hash_mismatch",
            "payload digest differs from event content",
        )
    evidence = value.get("request_evidence")
    actor = value.get("actor")
    if (
        not isinstance(evidence, dict)
        or evidence.get("kind") != "direct_event_command"
        or evidence.get("command") != "gigai application record"
        or evidence.get("scope_digest") != value.get("requested_event_sha256")
        or evidence.get("actor") != actor
        or not isinstance(actor, dict)
        or actor.get("kind") != "operator"
        or evidence.get("recorded_at") != value.get("recorded_at")
    ):
        raise ApplicationEventError(
            "application_request_mismatch",
            "direct application evidence is not bound to the event",
        )
    return value


def _events(snapshot: JournalSnapshot, project_id: str, gig_id: str):
    result = []
    for path, raw in snapshot.artifacts.items():
        if path.startswith("records/applications/events/"):
            try:
                result.append(
                    _validate_event(parse_json_bytes(raw), path, project_id, gig_id)
                )
            except Exception as exc:
                raise ApplicationEventError(
                    "application_event_invalid",
                    "committed application event is invalid",
                ) from exc
    return result


def _scout_document_ref(ref: object, *, snapshot: JournalSnapshot, project_id: str,
                        gig_id: str, opportunity_ref: str) -> None:
    """Authenticate one versioned Scout document ref and its Tailor Run.

    Legacy imported refs remain handled by ``_redeem_documents``.  A Scout
    ref is a separate closed shape: it names the exact document revision,
    opportunity tuple, and host-owned completed Tailor result that generated
    it.
    """
    required = {
        "kind", "record_id", "revision_id", "document_kind", "content_sha256",
        "opportunity_ref", "snapshot_id", "tailor_run_id", "tailor_goal_id",
        "tailor_invocation_id",
    }
    if not isinstance(ref, dict) or set(ref) != required or ref.get("kind") != "scout_document":
        raise ApplicationEventError("application_document_ref_invalid", "Scout document reference shape is invalid")
    record_id, revision_id, document_kind = ref.get("record_id"), ref.get("revision_id"), ref.get("document_kind")
    snapshot_id, ref_opportunity = ref.get("snapshot_id"), ref.get("opportunity_ref")
    digest = ref.get("content_sha256")
    run_id, goal_id, invocation_id = ref.get("tailor_run_id"), ref.get("tailor_goal_id"), ref.get("tailor_invocation_id")
    if (
        not isinstance(record_id, str) or _SCOUT_DOCUMENT_ID.fullmatch(record_id) is None
        or not isinstance(revision_id, str) or _SCOUT_REVISION_ID.fullmatch(revision_id) is None
        or document_kind not in _SCOUT_DOCUMENT_KINDS
        or not isinstance(snapshot_id, str) or re.fullmatch(r"snapshot_[0-9a-f]{32}", snapshot_id) is None
        or not isinstance(ref_opportunity, str) or ref_opportunity != opportunity_ref
        or not isinstance(digest, str) or _SCOUT_SHA256.fullmatch(digest) is None
        or not isinstance(run_id, str) or _SCOUT_RUN_ID.fullmatch(run_id) is None
        or not isinstance(goal_id, str) or _SCOUT_GOAL_ID.fullmatch(goal_id) is None
        or not isinstance(invocation_id, str) or _SCOUT_INVOCATION_ID.fullmatch(invocation_id) is None
    ):
        raise ApplicationEventError("application_document_ref_invalid", "Scout document reference identity is invalid")
    record_path = f"records/scout-documents/{record_id}/revisions/{revision_id}/record.json"
    record_raw = snapshot.artifacts.get(record_path)
    if record_raw is None:
        raise ApplicationEventError("application_document_ref_missing", "Scout document revision is not committed authority")
    try:
        record = parse_json_bytes(record_raw)
    except Exception as exc:
        raise ApplicationEventError("application_document_ref_invalid", "Scout document revision is malformed") from exc
    if not isinstance(record, dict):
        raise ApplicationEventError("application_document_ref_invalid", "Scout document revision is malformed")
    content_ref = record.get("content_ref")
    descriptor = dict(record)
    descriptor.pop("content_ref", None)
    opportunity = descriptor.get("opportunity")
    if (
        not validate_serialized_contract("scout-document-revision-v1.schema.json", canonical_json_bytes(descriptor)).valid
        or descriptor.get("record_id") != record_id
        or descriptor.get("revision_id") != revision_id
        or descriptor.get("document_kind") != document_kind
        or not isinstance(opportunity, dict)
        or opportunity.get("opportunity_id") != ref_opportunity
        or opportunity.get("snapshot_id") != snapshot_id
        or descriptor.get("content_sha256") != digest
    ):
        raise ApplicationEventError("application_document_scope_refused", "Scout document revision identity differs")
    lineage = descriptor.get("source_lineage")
    if isinstance(lineage, list):
        for item in lineage:
            identity = item.get("identity") if isinstance(item, dict) else None
            if isinstance(identity, dict) and (
                identity.get("project_id") not in (None, project_id)
                or identity.get("gig_id") not in (None, gig_id)
            ):
                raise ApplicationEventError("application_document_scope_refused", "Scout document lineage belongs to another scope")
    content_path = content_ref.get("path") if isinstance(content_ref, dict) else None
    content_raw = snapshot.artifacts.get(content_path) if isinstance(content_path, str) else None
    if (
        not isinstance(content_ref, dict)
        or content_path != f"records/scout-documents/{record_id}/revisions/{revision_id}/{document_kind}.md"
        or content_raw is None
        or content_ref.get("content_sha256") != digest
        or content_ref.get("size_bytes") != len(content_raw)
        or digest_imported_bytes(content_raw) != digest
    ):
        raise ApplicationEventError("application_document_digest_mismatch", "Scout document digest differs from committed bytes")
    result_path = f"runs/{run_id}/scout-tailor/result.json"
    result_raw = snapshot.artifacts.get(result_path)
    try:
        result = parse_json_bytes(result_raw) if result_raw is not None else None
    except Exception as exc:
        raise ApplicationEventError("application_document_ref_invalid", "Tailor result provenance is malformed") from exc
    generated = {
        (item.get("document_kind"), item.get("record_id"), item.get("revision_id"), item.get("content_sha256"))
        for item in result.get("documents", [])
        if isinstance(item, dict)
    } if isinstance(result, dict) and isinstance(result.get("documents"), list) else set()
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != "scout-tailor-run-result:1"
        or result.get("status") != "complete"
        or result.get("run_id") != run_id
        or result.get("goal_id") != goal_id
        or result.get("invocation_id") != invocation_id
        or (document_kind, record_id, revision_id, digest) not in generated
    ):
        raise ApplicationEventError("application_document_provenance_mismatch", "Scout document was not generated by the named Tailor Run")


def validate_application_links(
    event: dict[str, Any],
    *,
    snapshot: JournalSnapshot,
    project_id: str,
    gig_id: str,
    opportunity_reader: Any | None = None,
) -> dict[str, Any]:
    """Validate an event's committed document and opportunity identities.

    The event writer uses :func:`_redeem_documents` while holding the journal
    lock.  Projection/report readers call this snapshot-only companion so a
    dashboard cannot turn a forged revision or model-owned opportunity ID into
    a link.  ``opportunity_reader`` is intentionally explicit: R1 supplies the
    reviewed discovery reader, while a missing reader yields an unresolved
    link rather than treating the event as verified.
    """
    try:
        checked = _validate_event(event, f"records/applications/events/{event.get('event_id')}.json", project_id, gig_id)
    except (ApplicationEventError, AttributeError, TypeError) as exc:
        if isinstance(exc, ApplicationEventError):
            raise
        raise ApplicationEventError("application_event_invalid", "application event is malformed") from exc
    # Validation annotates the reader result with local verification state;
    # never mutate the event object that the writer will serialize as its
    # immutable schema-closed artifact.
    checked = dict(checked)
    for ref in checked.get("document_refs", []):
        if not isinstance(ref, dict):
            raise ApplicationEventError("application_document_ref_invalid", "document reference must be an object")
        if ref.get("kind") == "scout_document":
            _scout_document_ref(
                ref, snapshot=snapshot, project_id=project_id, gig_id=gig_id,
                opportunity_ref=str(checked.get("opportunity_ref")),
            )
            continue
        record_id, revision_id, digest = ref.get("record_id"), ref.get("revision_id"), ref.get("content_sha256")
        revision_path = f"records/{record_id}/revisions/{revision_id}.json"
        raw = snapshot.artifacts.get(revision_path)
        if raw is None or not validate_serialized_contract("private-record-revision.schema.json", raw).valid:
            raise ApplicationEventError("application_document_ref_missing", "document revision is not committed authority")
        revision = parse_json_bytes(raw)
        content = revision.get("content") if isinstance(revision, dict) else None
        snapshot_ref = content.get("snapshot_ref") if isinstance(content, dict) else None
        if (
            not isinstance(revision, dict)
            or revision.get("record_id") != record_id
            or revision.get("revision_id") != revision_id
            or revision.get("project_id") != project_id
            or revision.get("gig_id") != gig_id
            or not isinstance(snapshot_ref, dict)
        ):
            raise ApplicationEventError("application_document_scope_refused", "document revision belongs to another scope")
        source_path = snapshot_ref.get("path")
        content_bytes = snapshot.artifacts.get(source_path) if isinstance(source_path, str) else None
        if (
            content_bytes is None
            or digest_imported_bytes(content_bytes) != snapshot_ref.get("content_sha256")
            or len(content_bytes) != snapshot_ref.get("size_bytes")
            or digest != snapshot_ref.get("content_sha256")
        ):
            raise ApplicationEventError("application_document_digest_mismatch", "document reference digest differs from committed bytes")
    if opportunity_reader is None:
        checked["opportunity_verified"] = False
        return checked
    try:
        selected = opportunity_reader(snapshot, checked["opportunity_ref"], project_id, gig_id)
    except TypeError:
        selected = opportunity_reader(snapshot, project_id, gig_id)
    if isinstance(selected, (list, tuple, set)):
        selected = next(
            (
                item
                for item in selected
                if isinstance(item, dict)
                and item.get("opportunity_id") == checked["opportunity_ref"]
            ),
            None,
        )
    if selected is None:
        raise ApplicationEventError("application_opportunity_missing", "application event does not name a committed opportunity")
    if isinstance(selected, dict) and selected.get("opportunity_id") != checked["opportunity_ref"]:
        raise ApplicationEventError("application_opportunity_mismatch", "selected opportunity identity differs")
    checked["opportunity_verified"] = True
    return checked


def _redeem_documents(source, snapshot, project_id, gig_id, workpad):
    for ref in source.get("document_refs", []):
        if not isinstance(ref, dict):
            raise ApplicationEventError(
                "application_document_ref_invalid",
                "document reference must be an object",
            )
        if ref.get("kind") == "scout_document":
            _scout_document_ref(
                ref, snapshot=snapshot, project_id=project_id, gig_id=gig_id,
                opportunity_ref=str(source.get("opportunity_ref")),
            )
            continue
        rid, vid, digest = (
            ref.get("record_id"),
            ref.get("revision_id"),
            ref.get("content_sha256"),
        )
        path = f"records/{rid}/revisions/{vid}.json"
        raw = snapshot.artifacts.get(path)
        if (
            raw is None
            or not validate_serialized_contract(
                "private-record-revision.schema.json", raw
            ).valid
        ):
            raise ApplicationEventError(
                "application_document_ref_missing",
                "document revision is not committed authority",
            )
        revision = parse_json_bytes(raw)
        if (
            revision.get("record_id") != rid
            or revision.get("revision_id") != vid
            or revision.get("project_id") != project_id
            or revision.get("gig_id") != gig_id
        ):
            raise ApplicationEventError(
                "application_document_scope_refused",
                "document revision belongs to another scope",
            )
        content = revision.get("content")
        snapshot_ref = content.get("snapshot_ref") if isinstance(content, dict) else None
        if not isinstance(snapshot_ref, dict):
            raise ApplicationEventError(
                "application_document_ref_invalid",
                "document revision has no committed snapshot reference",
            )
        source_path = snapshot_ref.get("path")
        expected_size = snapshot_ref.get("size_bytes")
        if not isinstance(source_path, str) or type(expected_size) is not int:
            raise ApplicationEventError(
                "application_document_ref_invalid",
                "document snapshot reference is malformed",
            )
        try:
            committed, _publisher = read_committed_artifact(
                workpad=workpad,
                project_id=project_id,
                gig_id=gig_id,
                path=source_path,
                head=snapshot.head,
            )
        except Exception as exc:
            raise ApplicationEventError(
                "application_document_ref_missing",
                "document snapshot bytes are not committed authority",
            ) from exc
        if (
            digest_imported_bytes(committed) != snapshot_ref.get("content_sha256")
            or len(committed) != expected_size
            or snapshot_ref.get("content_sha256") != digest
        ):
            raise ApplicationEventError(
                "application_document_digest_mismatch",
                "document reference digest differs from committed document bytes",
            )
        if ref.get("opportunity_ref") not in (None, source["opportunity_ref"]):
            raise ApplicationEventError(
                "application_document_scope_refused",
                "document reference opportunity differs",
            )


def _validate_input(data):
    if not isinstance(data, dict):
        raise ApplicationEventError(
            "application_event_invalid", "input must be a JSON object"
        )
    required = {"opportunity_ref", "event_kind", "occurred_at", "timezone"}
    if not required <= data.keys():
        raise ApplicationEventError(
            "application_event_invalid", "input lacks required application scope"
        )
    if "request_evidence" in data:
        raise ApplicationEventError(
            "application_request_evidence_required",
            "direct command constructs operator consent; supplied agent evidence is not accepted",
        )
    if data["event_kind"] not in EVENT_KINDS:
        raise ApplicationEventError(
            "application_event_invalid", "unsupported event kind"
        )
    refs = data.get("document_refs", [])
    if not isinstance(refs, list) or len(refs) > 32 or any(not isinstance(item, dict) for item in refs):
        raise ApplicationEventError("application_document_ref_invalid", "document_refs must be a bounded object list")
    if any(item.get("kind") not in (None, "scout_document") for item in refs):
        raise ApplicationEventError("application_document_ref_invalid", "document reference kind is unsupported")
    if not isinstance(data["opportunity_ref"], str) or not re.fullmatch(
        r"opportunity_[0-9a-f]{32}", data["opportunity_ref"]
    ):
        raise ApplicationEventError(
            "application_opportunity_ref_invalid",
            "opportunity_ref must be a committed Scout opportunity identity",
        )
    try:
        dt = datetime.fromisoformat(str(data["occurred_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApplicationEventError(
            "application_date_required", "occurred_at must resolve to an ISO date-time"
        ) from exc
    if dt.tzinfo is None:
        raise ApplicationEventError(
            "application_date_required", "occurred_at must include timezone offset"
        )
    if data.get("supersedes") is not None and not isinstance(data["supersedes"], str):
        raise ApplicationEventError(
            "application_event_invalid", "supersedes must be an event ID"
        )
    if len(str(data.get("notes", ""))) > 4000:
        raise ApplicationEventError(
            "application_event_invalid", "notes exceed 4000 characters"
        )


def record_application(
    *, resolved: ResolvedWorkpad, data: dict[str, Any], confirm: bool = False,
    opportunity_reader: Any | None = None,
) -> dict[str, Any]:
    if not confirm:
        raise ApplicationEventError(
            "application_confirmation_required",
            "direct application recording requires --confirm",
        )
    _validate_input(data)
    operation_key = data.get("operation_key")
    if not isinstance(operation_key, str) or not operation_key:
        raise ApplicationEventError(
            "application_operation_required", "operation_key is required"
        )
    project_id, gig_id = resolved.project_id, resolved.gig_id
    source = dict(data)
    source["project_id"] = project_id
    source["gig_id"] = gig_id
    source.setdefault("document_refs", [])
    source.setdefault("notes", None)
    source.setdefault("supersedes", None)
    requested = _digest(_scope(source))
    event_id = source.get("event_id") or _uuid("event")
    if not isinstance(event_id, str) or not re.fullmatch(
        r"event_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        event_id,
    ):
        raise ApplicationEventError(
            "application_event_identity_invalid",
            "event_id must be a UUIDv4 event identity",
        )
    recorded = _now()
    evidence = {
        "kind": "direct_event_command",
        "scope_digest": requested,
        "actor": {"kind": "operator", "id": "local-user"},
        "recorded_at": recorded,
        "command": "gigai application record",
    }
    event = {
        "schema_version": "1.0",
        "event_id": event_id,
        "project_id": project_id,
        "gig_id": gig_id,
        "opportunity_ref": source["opportunity_ref"],
        "event_kind": source["event_kind"],
        "occurred_at": source["occurred_at"],
        "timezone": source["timezone"],
        "recorded_at": recorded,
        "document_refs": source["document_refs"],
        "notes": source["notes"],
        "supersedes": source["supersedes"],
        "request_evidence": evidence,
        "requested_event_sha256": requested,
        "operation_key": operation_key,
        "payload_sha256": "",
        "actor": evidence["actor"],
    }
    semantic = dict(event)
    semantic.pop("event_id")
    semantic.pop("operation_key")
    semantic.pop("payload_sha256")
    semantic.pop("recorded_at")
    event["payload_sha256"] = _digest(semantic)
    event_bytes = canonical_json_bytes(event)
    report = validate_serialized_contract("application-event.schema.json", event_bytes)
    if not report.valid:
        raise ApplicationEventError(
            "application_event_invalid", "event failed strict schema validation"
        )
    event_path = f"records/applications/events/{event_id}.json"
    receipt_path = f"records/operations/application-record-{digest_imported_bytes(operation_key.encode()).removeprefix('sha256:')}.json"

    def publish(writer):
        # Include the immutable discovery Run tree when it exists so the
        # mutation can authenticate opportunity_ref against the same pinned
        # view used by the report.  Legacy event-only Gigs remain readable and
        # explicitly unresolved; they do not gain fabricated authority.
        # Discovery opportunity authentication needs the immutable approved
        # Graph Set contracts referenced by its sealed Plan; these live under
        # manifests and are journaled alongside the Run tree.
        snap = writer.snapshot(("records/", "runs/", "run-plans/", "references/", "run-inputs/", "manifests/"))
        resolver = opportunity_reader
        if resolver is None and any(path.startswith("runs/") and "/receipts/" in path for path in snap.artifacts):
            from .scout_report_readers import opportunity_reader as make_opportunity_reader
            resolver = make_opportunity_reader(resolved)
        if resolver is not None:
            try:
                validate_application_links(
                    event,
                    snapshot=snap,
                    project_id=project_id,
                    gig_id=gig_id,
                    opportunity_reader=resolver,
                )
            except ApplicationEventError:
                raise
        existing = None
        if receipt_path in snap.artifacts:
            existing = parse_json_bytes(snap.artifacts[receipt_path])
            if not isinstance(existing, dict) or not isinstance(
                existing.get("event"), dict
            ):
                raise ApplicationEventError(
                    "application_receipt_invalid",
                    "committed application receipt is malformed",
                )
            original = _validate_event(
                existing["event"],
                f"records/applications/events/{existing['event'].get('event_id')}.json",
                project_id,
                gig_id,
            )
            committed_event_path = f"records/applications/events/{original.get('event_id')}.json"
            committed_event = snap.artifacts.get(committed_event_path)
            if committed_event is None:
                raise ApplicationEventError(
                    "application_receipt_invalid",
                    "application receipt event is not separately committed",
                )
            try:
                committed_value = _validate_event(
                    parse_json_bytes(committed_event), committed_event_path, project_id, gig_id
                )
            except ApplicationEventError as exc:
                raise ApplicationEventError(
                    "application_receipt_invalid",
                    "application receipt event artifact is invalid",
                ) from exc
            if (
                Path(receipt_path).name
                != f"application-record-{digest_imported_bytes(str(original.get('operation_key')).encode()).removeprefix('sha256:')}.json"
                or original.get("operation_key") != operation_key
                or existing.get("operation_key") != original.get("operation_key")
                or existing.get("requested_event_sha256") != requested
                or existing.get("payload_sha256") != original.get("payload_sha256")
                or canonical_json_bytes(existing["event"])
                != canonical_json_bytes(committed_value)
            ):
                raise ApplicationEventError(
                    "application_operation_conflict",
                    "operation key requested event conflicts",
                )
            return {"status": "already_recorded", "event": original}
        _redeem_documents(source, snap, project_id, gig_id, resolved.path)
        prior = _events(snap, project_id, gig_id)
        if any(event.get("event_id") == event_id for event in prior):
            raise ApplicationEventError(
                "application_event_identity_conflict",
                "event ID is already committed under another operation",
            )
        if source["supersedes"]:
            target = next(
                (e for e in prior if e.get("event_id") == source["supersedes"]), None
            )
            if (
                target is None
                or target.get("opportunity_ref") != source["opportunity_ref"]
            ):
                raise ApplicationEventError(
                    "application_correction_invalid",
                    "correction must supersede an earlier same-opportunity event",
                )
            if event_id == source["supersedes"]:
                raise ApplicationEventError(
                    "application_correction_cycle", "correction cycle is not allowed"
                )
        receipt = {
            "schema_version": "1.0",
            "operation_key": operation_key,
            "requested_event_sha256": requested,
            "payload_sha256": event["payload_sha256"],
            "event": event,
        }
        handoff = _uuid("handoff")
        entry = writer.record(
            JournalTransition(
                handoff,
                "private_record_revised",
                "Recorded explicit application event; no outbound effect.",
                (
                    JournalArtifact(event_path, event_bytes),
                    JournalArtifact(receipt_path, canonical_json_bytes(receipt)),
                ),
                {
                    "artifact_refs": [
                        {
                            "path": event_path,
                            "content_sha256": digest_imported_bytes(event_bytes),
                            "media_type": "application/json",
                            "size_bytes": len(event_bytes),
                        },
                        {
                            "path": receipt_path,
                            "content_sha256": digest_imported_bytes(
                                canonical_json_bytes(receipt)
                            ),
                            "media_type": "application/json",
                            "size_bytes": len(canonical_json_bytes(receipt)),
                        },
                    ]
                },
            )
        )
        return {
            "status": "recorded",
            "event": event,
            "journal_sequence": entry.sequence,
        }

    try:
        return run_with_journal_writer(
            workpad=resolved.path,
            project_id=project_id,
            gig_id=gig_id,
            operation=publish,
        )
    except ApplicationEventError:
        raise
    except JournalConflictError as exc:
        raise ApplicationEventError("application_journal_conflict", str(exc)) from exc


def read_application(
    *, resolved: ResolvedWorkpad, opportunity_ref: str | None = None
) -> dict[str, Any]:
    def read(writer):
        snapshot = writer.snapshot(("records/applications/events/",))
        ordered = sorted(
            (
                (event, _journal_sequence(writer.root, path))
                for path, raw in snapshot.artifacts.items()
                for event in [
                    _validate_event(
                        parse_json_bytes(raw),
                        path,
                        resolved.project_id,
                        resolved.gig_id,
                    )
                ]
            ),
            key=lambda pair: (
                datetime.fromisoformat(
                    str(pair[0]["occurred_at"]).replace("Z", "+00:00")
                ).astimezone(UTC),
                pair[1],
            ),
        )
        groups = {
            str(key): [
                event for event, _seq in ordered if event.get("opportunity_ref") == key
            ]
            for key in {event.get("opportunity_ref") for event, _seq in ordered}
        }
        if opportunity_ref is not None:
            selected = groups.get(opportunity_ref, [])
            superseded = {e.get("supersedes") for e in selected}
            active = [e for e in selected if e.get("event_id") not in superseded]
            return {
                "events": selected,
                "current_status": active[-1]["event_kind"] if active else None,
                "history_count": len(selected),
            }
        statuses = {}
        for key, selected in groups.items():
            superseded = {e.get("supersedes") for e in selected}
            active = [e for e in selected if e.get("event_id") not in superseded]
            statuses[key] = active[-1]["event_kind"] if active else None
        return {
            "events": [event for event, _seq in ordered],
            "statuses": statuses,
            "history_count": len(ordered),
        }

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=read,
    )
