"""Offline G08 proposal lifecycle orchestration.

This module owns lifecycle ordering only.  Identity generation, workpad
provisioning, journal serialization, model selection, and proposal validation
remain with their dedicated G01/G05/G06/G11/G07 modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Callable, Mapping
import uuid

from .adapters.factory import resolve_model_adapter
from .capabilities import capability_manifest_artifact_ref
from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    canonicalize_owned_text,
    digest_imported_bytes,
    generate_entity_id,
    parse_json_front_matter,
    parse_json_bytes,
)
from .config import load_config
from .improvement import validate_improvement_manifest
from .learning import load_learning_records, validate_learning_record
from .journal import (
    JournalArtifact,
    JournalEntry,
    JournalTransition,
    record_transition,
    record_transition_chain,
)
from .proposal_interview import (
    InterviewSession,
    ProposalInterviewError,
    ReferenceDecision,
    build_session,
    approve_session,
    persist_trace,
    session_from_record,
    session_record,
)
from .registry import open_project_registry
from .validators import (
    ValidationReport,
    validate_proposal_workpad,
    validate_serialized_contract,
)
from .workpad import (
    BoundProject,
    ResolvedWorkpad,
    open_locations,
    provision_workpad,
    resolve_bound_project,
    resolve_workpad,
    select_active_workpad,
)


_NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
CreateObserver = Callable[[str], None]


class LifecycleError(RuntimeError):
    """A G08 lifecycle transition cannot truthfully continue."""

    code = "lifecycle_error"


@dataclass(frozen=True)
class CreateResult:
    project_id: str
    gig_id: str
    proposal_id: str
    workpad: Path
    creation_started: JournalEntry
    proposal_ready: JournalEntry
    resumed: bool


@dataclass(frozen=True)
class InterviewStartResult:
    project_id: str
    gig_id: str
    workpad: Path
    session: InterviewSession
    reference_bytes: Mapping[str, bytes]
    creation_started: JournalEntry
    resumed: bool


@dataclass(frozen=True)
class ApprovalResult:
    gig_id: str
    proposal_id: str
    version: int
    sealed_commit: str
    publication_commit: str
    tag: str


@dataclass(frozen=True)
class RevisionResult:
    gig_id: str
    proposal_id: str
    parent_proposal_id: str
    entry: JournalEntry


def start_interview(
    *,
    home_root: Path,
    requested_target: Path | None,
    name: str,
    request: str,
    reference_paths: tuple[Path, ...],
    max_rounds: int = 3,
    improve: bool = False,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> InterviewStartResult:
    """Start or recover a G22 local interview without creating a proposal."""

    if not _NAME.fullmatch(name):
        raise LifecycleError("create name must be a lowercase dashed identifier")
    if not request.strip() or "\0" in request:
        raise LifecycleError("create request must be non-empty and NUL-free")
    home = home_root.expanduser().resolve(strict=False)
    bound = resolve_bound_project(home_root=home, requested_target=requested_target)
    if improve:
        return _start_improve_interview(
            home=home,
            bound=bound,
            request=request,
            reference_paths=reference_paths,
            max_rounds=max_rounds,
            uuid_factory=uuid_factory,
        )
    gig_id = _recoverable_gig_id(home, bound)
    resumed = gig_id is not None
    if gig_id is None:
        gig_id = _allocate_gig_id(home, uuid_factory)
        provisioned = provision_workpad(home_root=home, project_id=bound.project_id, gig_id=gig_id)
        workpad = provisioned.path
    else:
        workpad = _workpad_for_gig(home, bound, gig_id)

    snapshot_path = workpad / "manifests" / "proposal-interview.json"
    if snapshot_path.exists():
        try:
            payload = parse_json_bytes(snapshot_path.read_bytes())
            if not isinstance(payload, dict):
                raise ProposalInterviewError("snapshot is not an object")
            session = session_from_record(payload)
            references = _read_interview_references(workpad, session)
        except (ProposalInterviewError, OSError, ValueError) as exc:
            raise LifecycleError(f"interview recovery failed: {exc}") from exc
        select_active_workpad(
            home_root=home,
            requested_target=bound.target_root,
            gig_id=gig_id,
            allow_semantic_state=True,
        )
        creation_started = _creation_started_entry(workpad)
        return InterviewStartResult(bound.project_id, gig_id, workpad, session, references, creation_started, True)

    if not reference_paths:
        raise LifecycleError("create requires at least one explicit --reference")

    existing_entries = _journal_entries(workpad)
    if existing_entries:
        if len(existing_entries) != 1 or not existing_entries[0].path.name.endswith("-creation-started.txt"):
            raise LifecycleError("recoverable workpad has an unexpected pre-interview journal")
        creation_started = existing_entries[0]
    else:
        creation_started = record_transition(
            workpad=workpad,
            project_id=bound.project_id,
            gig_id=gig_id,
            handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
            transition="creation_started",
            body="GigAI creation started before interview input or proposal effects.",
        )
    select_active_workpad(home_root=home, requested_target=bound.target_root, gig_id=gig_id)

    session_id = _allocate_interview_id("session", uuid_factory)
    request_bytes = request.encode("utf-8")
    request_path = f"review/interviews/{session_id}/request.txt"
    request_artifact = {
        "path": request_path,
        "content_sha256": digest_imported_bytes(request_bytes),
        "media_type": "text/plain",
        "size_bytes": len(request_bytes),
    }
    references: list[ReferenceDecision] = []
    reference_bytes: dict[str, bytes] = {}
    artifacts = [JournalArtifact(request_path, request_bytes)]
    for source in reference_paths:
        source = source.expanduser()
        if source.is_symlink() or not source.is_file():
            raise LifecycleError(f"reference is not a regular non-symlink file: {source}")
        source = source.resolve(strict=True)
        content = source.read_bytes()
        reference_id = _allocate_interview_id("ref", uuid_factory)
        digest = digest_imported_bytes(content)
        references.append(ReferenceDecision(reference_id, digest, "excluded"))
        reference_bytes[reference_id] = content
        artifacts.append(JournalArtifact(f"review/interviews/{session_id}/references/{reference_id}.bin", content))
    session = build_session(
        session_id=session_id,
        project_id=bound.project_id,
        gig_id=gig_id,
        request_kind=name,
        request_artifact=request_artifact,
        request_sha256=request_artifact["content_sha256"],
        references=tuple(references),
        max_rounds=max_rounds,
    )
    snapshot = canonical_json_bytes(session_record(session))
    if not validate_serialized_contract("proposal-interview.schema.json", snapshot).valid:
        raise LifecycleError("initial proposal-interview snapshot failed schema validation")
    artifacts.append(JournalArtifact("manifests/proposal-interview.json", snapshot))
    record_transition(
        workpad=workpad,
        project_id=bound.project_id,
        gig_id=gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="proposal_interview_started",
        body=f"Proposal interview {session_id} is ready for explicit operator input.",
        artifacts=tuple(artifacts),
    )
    _persist_interview_trace(workpad, session)
    return InterviewStartResult(bound.project_id, gig_id, workpad, session, reference_bytes, creation_started, resumed)


def _start_improve_interview(
    *,
    home: Path,
    bound: BoundProject,
    request: str,
    reference_paths: tuple[Path, ...],
    max_rounds: int,
    uuid_factory: Callable[[], uuid.UUID],
) -> InterviewStartResult:
    """Start an explicit G20 improve interview on the existing active Gig."""

    if not request.strip() or "\0" in request:
        raise LifecycleError("improve request must be non-empty and NUL-free")
    if not reference_paths:
        raise LifecycleError("improve requires at least one explicit evidence reference")
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        active = transaction.find_active_workpad(bound.project_id)
    if active is None:
        raise LifecycleError("improve requires an existing active Gig")
    gig_id = active.gig_id
    workpad = _workpad_for_gig(home, bound, gig_id)
    pointer_path = workpad / "manifests" / "active-gig-version.json"
    if not pointer_path.is_file() or pointer_path.is_symlink():
        raise LifecycleError("improve requires an active-version pointer")
    snapshot_path = workpad / "manifests" / "proposal-interview.json"
    if snapshot_path.exists():
        try:
            payload = parse_json_bytes(snapshot_path.read_bytes())
            if not isinstance(payload, dict):
                raise ProposalInterviewError("snapshot is not an object")
            session = session_from_record(payload)
            references = _read_interview_references(workpad, session)
        except (ProposalInterviewError, OSError, ValueError) as exc:
            raise LifecycleError(f"improve interview recovery failed: {exc}") from exc
        return InterviewStartResult(
            bound.project_id,
            gig_id,
            workpad,
            session,
            references,
            _journal_entries(workpad)[-1],
            True,
        )

    session_id = _allocate_interview_id("session", uuid_factory)
    request_bytes = request.encode("utf-8")
    request_path = f"review/interviews/{session_id}/request.txt"
    request_artifact = {
        "path": request_path,
        "content_sha256": digest_imported_bytes(request_bytes),
        "media_type": "text/plain",
        "size_bytes": len(request_bytes),
    }
    references: list[ReferenceDecision] = []
    reference_bytes: dict[str, bytes] = {}
    artifacts = [JournalArtifact(request_path, request_bytes)]
    for source in reference_paths:
        source = source.expanduser()
        if source.is_symlink() or not source.is_file():
            raise LifecycleError(f"improve evidence is not a regular non-symlink file: {source}")
        source = source.resolve(strict=True)
        content = source.read_bytes()
        reference_id = _allocate_interview_id("ref", uuid_factory)
        references.append(ReferenceDecision(reference_id, digest_imported_bytes(content), "excluded"))
        reference_bytes[reference_id] = content
        artifacts.append(JournalArtifact(f"review/interviews/{session_id}/references/{reference_id}.bin", content))
    session = build_session(
        session_id=session_id,
        project_id=bound.project_id,
        gig_id=gig_id,
        request_kind="improve",
        request_artifact=request_artifact,
        request_sha256=request_artifact["content_sha256"],
        references=tuple(references),
        max_rounds=max_rounds,
    )
    snapshot = canonical_json_bytes(session_record(session))
    if not validate_serialized_contract("proposal-interview.schema.json", snapshot).valid:
        raise LifecycleError("initial improve interview snapshot failed schema validation")
    artifacts.append(JournalArtifact("manifests/proposal-interview.json", snapshot))
    entries = _journal_entries(workpad)
    if not entries:
        raise LifecycleError("active Gig workpad has no recoverable journal")
    record_transition(
        workpad=workpad,
        project_id=bound.project_id,
        gig_id=gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="proposal_interview_started",
        body=f"G20 improve interview {session_id} is ready for explicit operator input.",
        artifacts=tuple(artifacts),
    )
    _persist_interview_trace(workpad, session)
    return InterviewStartResult(bound.project_id, gig_id, workpad, session, reference_bytes, entries[-1], False)


def stage_improvement_manifest(
    *,
    home_root: Path,
    requested_target: Path | None,
    manifest: Mapping[str, object] | bytes,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> JournalEntry:
    """Journal one validated G20 manifest before opening its approval session."""

    home = home_root.expanduser().resolve(strict=False)
    resolved = resolve_workpad(
        home_root=home,
        requested_target=requested_target,
        gig_id=None,
        allow_semantic_state=True,
    )
    manifest_payload = manifest if isinstance(manifest, bytes) else canonical_json_bytes(manifest)
    parsed = parse_json_bytes(manifest_payload)
    if not isinstance(parsed, dict):
        raise LifecycleError("improvement manifest is not an object")
    ids = parsed.get("learning_record_ids")
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        raise LifecycleError("improvement manifest has invalid learning record IDs")
    records = load_learning_records(home_root=home, learning_ids=ids)
    validate_improvement_manifest(parsed, records)
    pointer_path = resolved.path / "manifests" / "active-gig-version.json"
    pointer = parse_json_bytes(pointer_path.read_bytes())
    if not isinstance(pointer, dict):
        raise LifecycleError("active-version pointer is invalid")
    if parsed.get("gig_id") != resolved.gig_id or parsed.get("project_id") != resolved.project_id:
        raise LifecycleError("improvement manifest binding does not match active Gig")
    if parsed.get("base_gig_version") != pointer.get("active_version"):
        raise LifecycleError("improvement manifest base version is stale")
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="improvement_manifest_staged",
        body=f"G20 improvement manifest {parsed['manifest_id']} is staged for explicit approval.",
        artifacts=(JournalArtifact("manifests/improvement-manifest.json", canonical_json_bytes(parsed)),),
    )


def persist_interview_session(
    *,
    workpad: Path,
    project_id: str,
    gig_id: str,
    session: InterviewSession,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> JournalEntry:
    """Commit one schema-validated interview snapshot before its next event."""

    snapshot = canonical_json_bytes(session_record(session))
    report = validate_serialized_contract("proposal-interview.schema.json", snapshot)
    if not report.valid:
        codes = ", ".join(item.code for item in report.findings)
        raise LifecycleError(f"proposal-interview snapshot failed validation: {codes}")
    transition = {
        "blocked": "proposal_interview_blocked",
        "approved": "proposal_interview_approved",
    }.get(session.state, "proposal_interview_updated")
    entry = record_transition(
        workpad=workpad,
        project_id=project_id,
        gig_id=gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition=transition,
        body=f"Proposal interview {session.session_id} advanced to {session.state}.",
        artifacts=(JournalArtifact("manifests/proposal-interview.json", snapshot),),
    )
    _persist_interview_trace(workpad, session)
    return entry


def approve_interview_session(
    *,
    home_root: Path,
    requested_target: Path | None,
    start: InterviewStartResult,
    session: InterviewSession,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> InterviewSession:
    """Build and seal the proposal only after the operator approves the interview."""

    if session.state == "approved":
        if session.proposal_id is None:
            raise LifecycleError("approved interview has no proposal identity")
        pointer_path = start.workpad / "manifests" / "active-gig-version.json"
        try:
            pointer = parse_json_bytes(pointer_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise LifecycleError("approved interview has no recoverable active pointer") from exc
        if (
            not isinstance(pointer, dict)
            or pointer.get("approved_proposal_id") != session.proposal_id
        ):
            raise LifecycleError("approved interview points at a different proposal")
        return session
    if session.state != "proposal_ready":
        raise LifecycleError("only a proposal_ready interview can be approved")
    if session.request_kind == "improve":
        return _approve_improve_interview_session(
            home_root=home_root,
            requested_target=requested_target,
            start=start,
            session=session,
            uuid_factory=uuid_factory,
        )
    request_path = start.workpad / str(session.request_artifact["path"])
    try:
        commission = request_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LifecycleError("interview request artifact cannot be read as UTF-8") from exc
    proposal_id = _allocate_local_id(EntityPrefix.GIG_PROPOSAL, uuid_factory)
    artifacts = _build_proposal_artifacts(
        gig_id=start.gig_id,
        project_id=start.project_id,
        proposal_id=proposal_id,
        name=session.request_kind,
        commission=commission,
        model_target="g22-deterministic",
        model_output="G22 proposal assembled from the bounded operator interview.",
        uuid_factory=uuid_factory,
    )
    proposal_bytes = next(
        item.content for item in artifacts if item.path == "manifests/gig-proposal.json"
    )
    approved = approve_session(
        session,
        proposal_id=proposal_id,
        proposal_sha256=digest_imported_bytes(proposal_bytes),
    )
    snapshot = canonical_json_bytes(session_record(approved))
    report = validate_serialized_contract("proposal-interview.schema.json", snapshot)
    if not report.valid:
        codes = ", ".join(item.code for item in report.findings)
        raise LifecycleError(f"approved interview snapshot failed validation: {codes}")
    record_transition(
        workpad=start.workpad,
        project_id=start.project_id,
        gig_id=start.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="proposal_interview_approved",
        body=f"Operator approved interview {session.session_id} as proposal {proposal_id}.",
        artifacts=(*artifacts, JournalArtifact("manifests/proposal-interview.json", snapshot)),
    )
    _persist_interview_trace(start.workpad, approved)
    approve_offline(
        home_root=home_root,
        requested_target=requested_target,
        proposal_id=proposal_id,
        uuid_factory=uuid_factory,
    )
    return approved


def _approve_improve_interview_session(
    *,
    home_root: Path,
    requested_target: Path | None,
    start: InterviewStartResult,
    session: InterviewSession,
    uuid_factory: Callable[[], uuid.UUID],
) -> InterviewSession:
    """Create and approve one G20 proposal through the ordinary lifecycle."""

    request_path = start.workpad / str(session.request_artifact["path"])
    try:
        commission = request_path.read_text(encoding="utf-8")
        manifest_path = start.workpad / "manifests" / "improvement-manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        manifest = parse_json_bytes(manifest_bytes)
        if not isinstance(manifest, dict):
            raise LifecycleError("improvement manifest is not an object")
        ids = manifest.get("learning_record_ids")
        if not isinstance(ids, list):
            raise LifecycleError("improvement manifest has no learning record IDs")
        home = home_root.expanduser().resolve(strict=False)
        records: dict[str, bytes] = {}
        for learning_id in ids:
            if not isinstance(learning_id, str):
                raise LifecycleError("improvement manifest has an invalid learning ID")
            record_path = home / "learning" / "records" / f"{learning_id}.json"
            if record_path.is_symlink() or not record_path.is_file():
                raise LifecycleError("improvement manifest cites a missing learning record")
            record_bytes = record_path.read_bytes()
            validate_learning_record(record_bytes)
            records[learning_id] = record_bytes
        validate_improvement_manifest(manifest, records)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise LifecycleError(f"improvement proposal inputs cannot be read: {exc}") from exc

    pointer_path = start.workpad / "manifests" / "active-gig-version.json"
    pointer = parse_json_bytes(pointer_path.read_bytes())
    current_bytes = (start.workpad / "manifests" / "gig-proposal.json").read_bytes()
    current = parse_json_bytes(current_bytes)
    if not isinstance(pointer, dict) or not isinstance(current, dict):
        raise LifecycleError("improve approval has invalid active proposal state")
    if pointer.get("approved_proposal_id") != current.get("proposal_id"):
        raise LifecycleError("improve approval base proposal is not active")
    if manifest.get("base_gig_version") != pointer.get("active_version"):
        raise LifecycleError("improvement manifest base version is stale")
    if manifest.get("gig_id") != start.gig_id or manifest.get("project_id") != start.project_id:
        raise LifecycleError("improvement manifest binding does not match active Gig")

    proposal_id = _allocate_local_id(EntityPrefix.GIG_PROPOSAL, uuid_factory)
    proposal = dict(current)
    proposal.update(
        {
            "proposal_id": proposal_id,
            "status": "proposed",
            "kind": "improve",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "created_by": {"kind": "gigai", "id": "g20-improve", "model_target": None},
            "base_gig_version": pointer["active_version"],
            "parent_proposal_id": pointer["approved_proposal_id"],
            "change_request": commission,
            "creation_manifest": _artifact_ref(
                "manifests/improvement-manifest.json", "application/json", manifest_bytes
            ),
        }
    )
    artifacts = (JournalArtifact("manifests/improvement-manifest.json", manifest_bytes), JournalArtifact("manifests/gig-proposal.json", canonical_json_bytes(proposal)))
    _validate_workpad_overlay(start.workpad, artifacts)
    proposal_bytes = next(item.content for item in artifacts if item.path == "manifests/gig-proposal.json")
    approved = approve_session(
        session,
        proposal_id=proposal_id,
        proposal_sha256=digest_imported_bytes(proposal_bytes),
    )
    snapshot = canonical_json_bytes(session_record(approved))
    if not validate_serialized_contract("proposal-interview.schema.json", snapshot).valid:
        raise LifecycleError("approved improve interview snapshot failed schema validation")
    record_transition(
        workpad=start.workpad,
        project_id=start.project_id,
        gig_id=start.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="proposal_interview_approved",
        body=f"Operator approved improve interview {session.session_id} as proposal {proposal_id}.",
        artifacts=(*artifacts, JournalArtifact("manifests/proposal-interview.json", snapshot)),
    )
    _persist_interview_trace(start.workpad, approved)
    approve_offline(
        home_root=home_root,
        requested_target=requested_target,
        proposal_id=proposal_id,
        uuid_factory=uuid_factory,
    )
    return approved


def create_offline(
    *,
    home_root: Path,
    requested_target: Path | None,
    name: str,
    commission: str | None = None,
    model_target: str = "offline-default",
    open_editor: bool = True,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    observer: CreateObserver | None = None,
) -> CreateResult:
    """Create one offline, review-only Gig Proposal through the real workpad."""

    if not _NAME.fullmatch(name):
        raise LifecycleError("create name must be a lowercase dashed identifier")
    commission = commission or name
    if not commission.strip() or "\0" in commission:
        raise LifecycleError("create commission must be non-empty and NUL-free")
    observer = observer or (lambda _step: None)
    home = home_root.expanduser().resolve(strict=False)
    bound = resolve_bound_project(home_root=home, requested_target=requested_target)
    resumed = False
    gig_id = _recoverable_gig_id(home, bound)
    if gig_id is None:
        gig_id = _allocate_gig_id(home, uuid_factory)
        observer("after_id_allocation")
        provisioned = provision_workpad(
            home_root=home,
            project_id=bound.project_id,
            gig_id=gig_id,
        )
        workpad = provisioned.path
        observer("after_provisioning")
    else:
        workpad = _workpad_for_gig(home, bound, gig_id)
        resumed = True

    existing_entries = _journal_entries(workpad)
    if _has_proposal(workpad):
        proposal_id = _proposal_id(workpad)
        if len(existing_entries) < 2:
            raise LifecycleError("proposal workpad has incomplete journal state")
        if open_editor:
            open_locations(
                home_root=home,
                requested_target=bound.target_root,
                gig_id=gig_id,
                target_only=False,
                with_target=False,
                allow_semantic_state=True,
            )
        return CreateResult(
            project_id=bound.project_id,
            gig_id=gig_id,
            proposal_id=proposal_id,
            workpad=workpad,
            creation_started=existing_entries[0],
            proposal_ready=existing_entries[1],
            resumed=True,
        )

    if existing_entries:
        if len(existing_entries) != 1 or not existing_entries[0].path.name.endswith(
            "-creation-started.txt"
        ):
            raise LifecycleError(
                "recoverable workpad has an unexpected pre-proposal journal"
            )
        creation_started = existing_entries[0]
    else:
        creation_started = record_transition(
            workpad=workpad,
            project_id=bound.project_id,
            gig_id=gig_id,
            handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
            transition="creation_started",
            body="Offline Gig creation started before any model, editor, or proposal effect.",
        )
        observer("after_creation_started")
    select_active_workpad(
        home_root=home,
        requested_target=bound.target_root,
        gig_id=gig_id,
    )
    observer("after_active_selection")

    config = load_config(home)
    binding = resolve_model_adapter(config, model_target)
    result = binding.port.invoke(binding.request(role="create", prompt="doctor-probe"))
    proposal_id = _allocate_local_id(EntityPrefix.GIG_PROPOSAL, uuid_factory)
    artifacts = _build_proposal_artifacts(
        gig_id=gig_id,
        project_id=bound.project_id,
        proposal_id=proposal_id,
        name=name,
        commission=commission,
        model_target=model_target,
        model_output=result.output_text,
        uuid_factory=uuid_factory,
    )
    _validate_artifacts(artifacts)
    proposal_ready = record_transition(
        workpad=workpad,
        project_id=bound.project_id,
        gig_id=gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="gig_proposal_ready",
        body=f"Offline proposal {proposal_id} is ready for operator review.",
        artifacts=artifacts,
    )
    observer("after_proposal_ready")
    if open_editor:
        open_locations(
            home_root=home,
            requested_target=bound.target_root,
            gig_id=gig_id,
            target_only=False,
            with_target=False,
            allow_semantic_state=True,
        )
    return CreateResult(
        project_id=bound.project_id,
        gig_id=gig_id,
        proposal_id=proposal_id,
        workpad=workpad,
        creation_started=creation_started,
        proposal_ready=proposal_ready,
        resumed=resumed,
    )


def approve_offline(
    *,
    home_root: Path,
    requested_target: Path | None,
    proposal_id: str,
    capability_manifest_id: str | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    observer: CreateObserver | None = None,
) -> ApprovalResult:
    """Approve one pending proposal without starting a Run or Goal."""

    home = home_root.expanduser().resolve(strict=False)
    resolved = resolve_workpad(
        home_root=home,
        requested_target=requested_target,
        gig_id=None,
        allow_semantic_state=True,
    )
    workpad = resolved.path
    proposal_path = workpad / "manifests" / "gig-proposal.json"
    proposal = parse_json_bytes(proposal_path.read_bytes())
    if not isinstance(proposal, dict) or proposal.get("proposal_id") != proposal_id:
        raise LifecycleError("proposal ID does not match the active proposed workpad")
    if proposal.get("status") == "approved":
        return _recover_approved_publication(
            resolved=resolved,
            proposal=proposal,
            proposal_id=proposal_id,
            capability_manifest_id=capability_manifest_id,
            uuid_factory=uuid_factory,
        )
    report = validate_proposal_workpad(workpad)
    if not report.valid:
        raise LifecycleError(
            "proposal is not valid for approval: "
            + ", ".join(finding.code for finding in report.findings)
        )
    if proposal.get("status") not in {"drafting", "proposed"}:
        raise LifecycleError("only a pending proposal can be approved")
    version = _next_version(workpad)
    tag = f"gig-v{version:06d}"
    proposal["status"] = "approved"
    approved_proposal = canonical_json_bytes(proposal)
    approved_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def publish(sealed: JournalEntry) -> JournalTransition:
        _git(workpad, "tag", tag, sealed.commit)
        if observer is not None:
            observer("after_approval_tag")
        pointer = canonical_json_bytes(
            {
                "schema_version": "1.0",
                "gig_id": resolved.gig_id,
                "active_version": version,
                "approved_proposal_id": proposal_id,
                "goal_graph": proposal["goal_graph"],
                "journal_commit": sealed.commit,
                "journal_tag": tag,
                "approved_at": approved_at,
                "approved_by": {
                    "kind": "operator",
                    "id": "local-user",
                    "model_target": None,
                },
            }
        )
        if capability_manifest_id is not None:
            pointer_payload = parse_json_bytes(pointer)
            assert isinstance(pointer_payload, dict)
            pointer_payload["capability_manifest"] = capability_manifest_artifact_ref(
                workpad, capability_manifest_id, gig_id=resolved.gig_id
            )
            pointer = canonical_json_bytes(pointer_payload)
        if not validate_serialized_contract(
            "active-gig-version.schema.json", pointer
        ).valid:
            raise LifecycleError("active-version pointer failed schema validation")
        return JournalTransition(
            _allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
            "gig_accepted",
            f"Gig version {version} is active at sealed commit {sealed.commit}.",
            (JournalArtifact("manifests/active-gig-version.json", pointer),),
        )

    sealed, published = record_transition_chain(
        workpad=workpad,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        first=JournalTransition(
            _allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
            "gig_proposal_approved",
            f"Operator approved proposal {proposal_id} as Gig version {version}.",
            (JournalArtifact("manifests/gig-proposal.json", approved_proposal),),
        ),
        continuation=publish,
    )
    if _git(workpad, "rev-parse", "--verify", tag).stdout.strip() != sealed.commit:
        raise LifecycleError("approval tag does not resolve to the sealed commit")
    return ApprovalResult(
        gig_id=resolved.gig_id,
        proposal_id=proposal_id,
        version=version,
        sealed_commit=sealed.commit,
        publication_commit=published.commit,
        tag=tag,
    )


def _recover_approved_publication(
    *,
    resolved: ResolvedWorkpad,
    proposal: dict[str, object],
    proposal_id: str,
    capability_manifest_id: str | None,
    uuid_factory: Callable[[], uuid.UUID],
) -> ApprovalResult:
    """Publish only the missing Commit B for an already sealed approval."""

    workpad = resolved.path
    sealed_commit = _git(workpad, "rev-parse", "--verify", "HEAD").stdout.strip()
    tags = [
        value
        for value in _git(
            workpad, "tag", "--points-at", sealed_commit
        ).stdout.splitlines()
        if re.fullmatch(r"gig-v[0-9]{6}", value)
    ]
    if len(tags) != 1:
        raise LifecycleError("approved proposal has no unambiguous sealed Gig tag")
    tag = tags[0]
    version = int(tag.removeprefix("gig-v"))
    handoff_paths = _git(
        workpad, "show", "--format=", "--name-only", sealed_commit
    ).stdout.splitlines()
    approved_paths = [
        path for path in handoff_paths if path.endswith("-gig-proposal-approved.txt")
    ]
    if len(approved_paths) != 1:
        raise LifecycleError("approved proposal commit lacks its approval handoff")
    metadata, _body = parse_json_front_matter(
        _git(workpad, "show", f"{sealed_commit}:{approved_paths[0]}").stdout.encode(
            "utf-8"
        )
    )
    if metadata.get("transition") != "gig_proposal_approved":
        raise LifecycleError("sealed approval handoff has the wrong transition")
    pointer_path = workpad / "manifests" / "active-gig-version.json"
    if pointer_path.exists():
        pointer = pointer_path.read_bytes()
        if not validate_serialized_contract(
            "active-gig-version.schema.json", pointer
        ).valid:
            raise LifecycleError("existing active-version pointer is invalid")
        payload = parse_json_bytes(pointer)
        if (
            not isinstance(payload, dict)
            or payload.get("journal_commit") != sealed_commit
        ):
            raise LifecycleError(
                "existing active-version pointer names another approval"
            )
        if capability_manifest_id is not None:
            expected = capability_manifest_artifact_ref(
                workpad, capability_manifest_id, gig_id=resolved.gig_id
            )
            if payload.get("capability_manifest") != expected:
                raise LifecycleError(
                    "existing active-version pointer has another capability manifest"
                )
        return ApprovalResult(
            resolved.gig_id,
            proposal_id,
            version,
            sealed_commit,
            _git(workpad, "rev-parse", "--verify", "HEAD").stdout.strip(),
            tag,
        )
    approved_at = metadata.get("timestamp")
    if not isinstance(approved_at, str):
        raise LifecycleError("sealed approval handoff lacks its timestamp")
    pointer = canonical_json_bytes(
        {
            "schema_version": "1.0",
            "gig_id": resolved.gig_id,
            "active_version": version,
            "approved_proposal_id": proposal_id,
            "goal_graph": proposal["goal_graph"],
            "journal_commit": sealed_commit,
            "journal_tag": tag,
            "approved_at": approved_at,
            "approved_by": {
                "kind": "operator",
                "id": "local-user",
                "model_target": None,
            },
        }
    )
    if capability_manifest_id is not None:
        pointer_payload = parse_json_bytes(pointer)
        assert isinstance(pointer_payload, dict)
        pointer_payload["capability_manifest"] = capability_manifest_artifact_ref(
            workpad, capability_manifest_id, gig_id=resolved.gig_id
        )
        pointer = canonical_json_bytes(pointer_payload)
    if not validate_serialized_contract(
        "active-gig-version.schema.json", pointer
    ).valid:
        raise LifecycleError(
            "recovered active-version pointer failed schema validation"
        )
    published = record_transition(
        workpad=workpad,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="gig_accepted",
        body=f"Recovered active Gig version {version} at sealed commit {sealed_commit}.",
        artifacts=(JournalArtifact("manifests/active-gig-version.json", pointer),),
    )
    return ApprovalResult(
        resolved.gig_id, proposal_id, version, sealed_commit, published.commit, tag
    )


def record_feedback(
    *,
    home_root: Path,
    requested_target: Path | None,
    proposal_id: str,
    feedback: str,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> JournalEntry:
    """Append an operator's exact feedback text to one pending proposal journal."""

    if type(feedback) is not str or not feedback.strip() or "\0" in feedback:
        raise LifecycleError("feedback must be non-empty text without NUL bytes")
    resolved, _proposal = _pending_proposal(home_root, requested_target, proposal_id)
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="gig_proposal_feedback_recorded",
        body=feedback,
    )


def revise_offline(
    *,
    home_root: Path,
    requested_target: Path | None,
    proposal_id: str,
    change_request: str,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> RevisionResult:
    """Create a new pending proposal, preserving the preceding revision in Git."""

    if (
        type(change_request) is not str
        or not change_request.strip()
        or "\0" in change_request
    ):
        raise LifecycleError(
            "revision change request must be non-empty text without NUL bytes"
        )
    resolved, previous = _pending_proposal(home_root, requested_target, proposal_id)
    workpad = resolved.path
    next_proposal_id = _allocate_local_id(EntityPrefix.GIG_PROPOSAL, uuid_factory)
    gig_document = canonicalize_owned_text(
        workpad.joinpath("gig.md").read_text(encoding="utf-8").rstrip("\n")
        + f"\n\n## Revision\n\n{change_request}\n"
    )
    review = canonicalize_owned_text(
        f"# Creation review\n\nRevision of {proposal_id}.\n\n{change_request}\n"
    )
    proposal = dict(previous)
    proposal.update(
        {
            "proposal_id": next_proposal_id,
            "status": "proposed",
            "kind": "amend",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "parent_proposal_id": proposal_id,
            "change_request": change_request,
            "gig_document": _artifact_ref("gig.md", "text/markdown", gig_document),
        }
    )
    artifacts = (
        JournalArtifact("gig.md", gig_document),
        JournalArtifact("reviews/creation-review.md", review),
        JournalArtifact("manifests/gig-proposal.json", canonical_json_bytes(proposal)),
    )
    _validate_workpad_overlay(workpad, artifacts)
    entry = record_transition(
        workpad=workpad,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="gig_proposal_revised",
        body=f"Proposal {next_proposal_id} revises {proposal_id}.",
        artifacts=artifacts,
    )
    return RevisionResult(resolved.gig_id, next_proposal_id, proposal_id, entry)


def reject_offline(
    *,
    home_root: Path,
    requested_target: Path | None,
    proposal_id: str,
    reason: str,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> JournalEntry:
    """Record rejection of one pending proposal without creating a version."""

    if type(reason) is not str or not reason.strip() or "\0" in reason:
        raise LifecycleError(
            "rejection reason must be non-empty text without NUL bytes"
        )
    resolved, proposal = _pending_proposal(home_root, requested_target, proposal_id)
    if (resolved.path / "manifests" / "active-gig-version.json").exists():
        raise LifecycleError("rejection cannot replace an existing active Gig version")
    proposal["status"] = "rejected"
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="gig_proposal_rejected",
        body=reason,
        artifacts=(
            JournalArtifact(
                "manifests/gig-proposal.json", canonical_json_bytes(proposal)
            ),
        ),
    )


def _pending_proposal(
    home_root: Path, requested_target: Path | None, proposal_id: str
) -> tuple[ResolvedWorkpad, dict[str, object]]:
    home = home_root.expanduser().resolve(strict=False)
    resolved = resolve_workpad(
        home_root=home,
        requested_target=requested_target,
        gig_id=None,
        allow_semantic_state=True,
    )
    report = validate_proposal_workpad(resolved.path)
    if not report.valid:
        raise LifecycleError(
            "proposal is not pending and valid: "
            + ", ".join(finding.code for finding in report.findings)
        )
    payload = parse_json_bytes(
        (resolved.path / "manifests" / "gig-proposal.json").read_bytes()
    )
    if not isinstance(payload, dict) or payload.get("proposal_id") != proposal_id:
        raise LifecycleError("proposal ID does not match the active proposed workpad")
    if payload.get("status") not in {"drafting", "proposed"}:
        raise LifecycleError("only a pending proposal can receive this transition")
    return resolved, payload


def _artifact_ref(path: str, media_type: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": media_type,
        "size_bytes": len(data),
    }


def _validate_workpad_overlay(
    workpad: Path, artifacts: tuple[JournalArtifact, ...]
) -> None:
    root = Path(tempfile.mkdtemp(prefix="gigai-g08-revision-"))
    try:
        shutil.copytree(
            workpad, root / "workpad", ignore=shutil.ignore_patterns(".git")
        )
        overlay = root / "workpad"
        for artifact in artifacts:
            path = overlay / artifact.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(artifact.content)
        report = validate_proposal_workpad(overlay)
        if not report.valid:
            codes = ", ".join(finding.code for finding in report.findings)
            raise LifecycleError(f"revised proposal failed G07 validation: {codes}")
    finally:
        shutil.rmtree(root)


def _allocate_gig_id(home: Path, uuid_factory: Callable[[], uuid.UUID]) -> str:
    config = load_config(home)
    registry, _ = open_project_registry(home, create=False)

    def persisted(candidate: str) -> bool:
        if registry.workpad_records() and any(
            item.gig_id == candidate for item in registry.workpad_records()
        ):
            return True
        return any(config.workpad_root.glob(f"projects/*/gigs/{candidate}"))

    return generate_entity_id(
        EntityPrefix.GIG, is_persisted=persisted, uuid_factory=uuid_factory
    )


def _allocate_local_id(
    prefix: EntityPrefix, uuid_factory: Callable[[], uuid.UUID]
) -> str:
    return generate_entity_id(
        prefix, is_persisted=lambda _candidate: False, uuid_factory=uuid_factory
    )


def _recoverable_gig_id(home: Path, bound: BoundProject) -> str | None:
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        active = transaction.find_active_workpad(bound.project_id)
    if active is not None:
        active_workpad = Path(active.workpad_locator)
        if not active_workpad.is_dir() or active_workpad.is_symlink():
            raise LifecycleError(
                "active Gig workpad is unavailable for lifecycle recovery"
            )
        return active.gig_id
    candidates: list[str] = []
    for record in registry.workpad_records():
        if record.project_id != bound.project_id:
            continue
        workpad = Path(record.workpad_locator)
        if not workpad.is_dir() or workpad.is_symlink():
            continue
        head = _git(workpad, "rev-parse", "--verify", "HEAD", check=False)
        if head.returncode != 0 or _is_preproposal_journal(workpad):
            candidates.append(record.gig_id)
    if len(candidates) > 1:
        raise LifecycleError(
            "multiple provisioned-but-unjournaled workpads require recovery"
        )
    return candidates[0] if candidates else None


def _workpad_for_gig(home: Path, bound: BoundProject, gig_id: str) -> Path:
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        record = transaction.find_project_workpad(bound.project_id, gig_id)
    if record is None:
        raise LifecycleError("recovery Gig is not registered to the bound project")
    return Path(record.workpad_locator)


def _build_proposal_artifacts(
    *,
    gig_id: str,
    project_id: str,
    proposal_id: str,
    name: str,
    commission: str,
    model_target: str,
    model_output: str,
    uuid_factory: Callable[[], uuid.UUID],
) -> tuple[JournalArtifact, ...]:
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    goal_a = _allocate_local_id(EntityPrefix.GOAL, uuid_factory)
    goal_b = _allocate_local_id(EntityPrefix.GOAL, uuid_factory)
    goal_a_path = "goals/00-define-scope.md"
    goal_b_path = "goals/01-review-plan.md"
    markdown = {
        "gig.md": canonicalize_owned_text(
            f"# {name}\n\n## Commission\n\n{commission}\n\nStatus: proposed.\n"
        ),
        "goals/README.md": canonicalize_owned_text(
            "# Goals\n\nOffline proposal goals.\n"
        ),
        goal_a_path: canonicalize_owned_text(
            "# Define scope\n\nDefine the offline proposal boundary.\n"
        ),
        goal_b_path: canonicalize_owned_text(
            "# Review plan\n\nReview the proposal before approval.\n"
        ),
        "reviews/creation-review.md": canonicalize_owned_text(
            "# Creation review\n\nAwaiting operator review.\n"
        ),
        "decisions/creation-decisions.md": canonicalize_owned_text(
            "# Creation decisions\n\nOffline fixture path selected.\n"
        ),
    }
    budget = {
        "max_model_calls": 1,
        "max_tool_calls": 0,
        "max_tokens": 64,
        "max_cost": None,
        "currency": None,
        "max_wall_time_ms": 60000,
        "max_parallel_goals": 1,
    }

    def artifact_ref(path: str, media_type: str, data: bytes) -> dict[str, object]:
        return {
            "path": path,
            "content_sha256": digest_imported_bytes(data),
            "media_type": media_type,
            "size_bytes": len(data),
        }

    def goal(
        goal_id: str, ordinal: str, slug: str, title: str, path: str
    ) -> dict[str, object]:
        return {
            "goal_id": goal_id,
            "goal_version": 1,
            "display_ordinal": ordinal,
            "slug": slug,
            "title": title,
            "required": True,
            "activation": "automatic",
            "contract": artifact_ref(path, "text/markdown", markdown[path]),
            "executor": {
                "kind": "local_capability",
                "capability": "gigai.offline",
                "role": None,
                "resolution": "installed",
                "materialized_by": None,
                "blocking_reason": None,
            },
            "tools": [],
            "effects": ["write_workpad"],
            "write_surfaces": ["manifests/"],
            "exclusive_resources": ["proposal-workpad"],
            "budget": budget,
            "verification": {
                "verifier": "gigai.check",
                "acceptance": "Proposal artifacts are valid before presentation.",
                "required_evidence": ["proposal-validation"],
            },
            "outcomes": ["COMPLETE"],
        }

    graph = {
        "schema_version": "1.0",
        "graph_id": _allocate_local_id(EntityPrefix.GRAPH, uuid_factory),
        "gig_id": gig_id,
        "graph_version": 1,
        "created_at": created_at,
        "aggregate_budget": {
            **budget,
            "max_model_calls": 2,
            "max_tokens": 128,
            "max_wall_time_ms": 120000,
        },
        "failure_policy": "fail_gig",
        "goals": [
            goal(goal_a, "G00", "define-scope", "Define scope", goal_a_path),
            goal(goal_b, "G01", "review-plan", "Review plan", goal_b_path),
        ],
        "edges": [
            {
                "edge_id": _allocate_local_id(EntityPrefix.EDGE, uuid_factory),
                "from_goal_id": goal_a,
                "to_goal_id": goal_b,
                "kind": "dependency",
                "on_outcomes": ["COMPLETE"],
                "automatic": True,
            }
        ],
        "entry_goal_ids": [goal_a],
        "terminal_goal_ids": [goal_b],
        "required_completion_evidence": ["proposal-validation"],
    }
    graph_bytes = canonical_json_bytes(graph)
    manifest_bytes = canonical_json_bytes(
        {
            "schema_version": "1.0",
            "creation_mode": "deterministic-offline",
            "model_target": model_target,
            "model_output": model_output,
        }
    )
    proposal = {
        "schema_version": "1.0",
        "proposal_id": proposal_id,
        "gig_id": gig_id,
        "project_id": project_id,
        "name": name,
        "status": "proposed",
        "kind": "create",
        "created_at": created_at,
        "created_by": {
            "kind": "gigai",
            "id": "offline-create",
            "model_target": model_target,
        },
        "base_gig_version": None,
        "parent_proposal_id": None,
        "change_request": None,
        "commission": commission,
        "gig_document": artifact_ref("gig.md", "text/markdown", markdown["gig.md"]),
        "goal_graph": artifact_ref(
            "manifests/goal-graph.json", "application/json", graph_bytes
        ),
        "creation_manifest": artifact_ref(
            "manifests/creation-manifest.json", "application/json", manifest_bytes
        ),
    }
    return tuple(
        [
            *(JournalArtifact(path, data) for path, data in markdown.items()),
            JournalArtifact("manifests/goal-graph.json", graph_bytes),
            JournalArtifact("manifests/creation-manifest.json", manifest_bytes),
            JournalArtifact(
                "manifests/gig-proposal.json", canonical_json_bytes(proposal)
            ),
        ]
    )


def _validate_artifacts(artifacts: tuple[JournalArtifact, ...]) -> None:
    root = Path(tempfile.mkdtemp(prefix="gigai-g08-proposal-"))
    try:
        for artifact in artifacts:
            path = root / artifact.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(artifact.content)
        report: ValidationReport = validate_proposal_workpad(root)
        if not report.valid:
            codes = ", ".join(finding.code for finding in report.findings)
            raise LifecycleError(
                f"offline proposal fixture failed G07 validation: {codes}"
            )
    finally:
        shutil.rmtree(root)


def _has_proposal(workpad: Path) -> bool:
    return (workpad / "manifests" / "gig-proposal.json").is_file()


def _creation_started_entry(workpad: Path) -> JournalEntry:
    entries = _journal_entries(workpad)
    if not entries or not entries[0].path.name.endswith("-creation-started.txt"):
        raise LifecycleError("interview workpad has no recoverable creation handoff")
    return entries[0]


def _read_interview_references(
    workpad: Path, session: InterviewSession
) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    root = workpad / "review" / "interviews" / session.session_id / "references"
    for reference in session.references:
        path = root / f"{reference.reference_id}.bin"
        if path.is_symlink() or not path.is_file():
            raise LifecycleError("interview reference object is missing or redirected")
        content = path.read_bytes()
        if digest_imported_bytes(content) != reference.content_sha256:
            raise LifecycleError("interview reference bytes do not match their digest")
        values[reference.reference_id] = content
    return values


def _persist_interview_trace(workpad: Path, session: InterviewSession) -> None:
    connection = sqlite3.connect(workpad / "state.sqlite")
    try:
        persist_trace(connection, session)
    finally:
        connection.close()


def _allocate_interview_id(prefix: str, uuid_factory: Callable[[], uuid.UUID]) -> str:
    value = uuid_factory()
    if type(value) is not uuid.UUID or value.version != 4:
        raise LifecycleError("interview ID factory must return UUIDv4 values")
    return f"{prefix}_{value}"


def _is_preproposal_journal(workpad: Path) -> bool:
    handoffs = (
        sorted((workpad / "handoffs").glob("*.txt"))
        if (workpad / "handoffs").is_dir()
        else []
    )
    return len(handoffs) == 1 and handoffs[0].name.endswith("-creation-started.txt")


def _proposal_id(workpad: Path) -> str:
    payload = parse_json_bytes(
        (workpad / "manifests" / "gig-proposal.json").read_bytes()
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("proposal_id"), str):
        raise LifecycleError("proposal workpad has no valid proposal identity")
    return payload["proposal_id"]


def _next_version(workpad: Path) -> int:
    path = workpad / "manifests" / "active-gig-version.json"
    if not path.exists():
        return 1
    if path.is_symlink() or not path.is_file():
        raise LifecycleError("active-version pointer is invalid")
    payload = parse_json_bytes(path.read_bytes())
    if not isinstance(payload, dict) or type(payload.get("active_version")) is not int:
        raise LifecycleError("active-version pointer has no valid version")
    return payload["active_version"] + 1


def _journal_entries(workpad: Path) -> tuple[JournalEntry, ...]:
    handoffs = sorted((workpad / "handoffs").glob("*.txt"))
    entries: list[JournalEntry] = []
    for path in handoffs:
        # Existing journal files are already committed; this result is only used
        # when a repeated create opens an unchanged proposal for review.
        entries.append(JournalEntry(int(path.name[:12]), "handoff_recovered", path, ""))
    return tuple(entries)


def _git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        capture_output=True,
        text=True,
        check=check,
        shell=False,
    )


__all__ = [
    "ApprovalResult",
    "CreateResult",
    "InterviewStartResult",
    "LifecycleError",
    "RevisionResult",
    "approve_interview_session",
    "approve_offline",
    "create_offline",
    "persist_interview_session",
    "record_feedback",
    "reject_offline",
    "revise_offline",
    "stage_improvement_manifest",
    "start_interview",
]
