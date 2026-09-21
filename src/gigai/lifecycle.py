"""Offline G08 proposal lifecycle orchestration.

This module owns lifecycle ordering only.  Identity generation, workpad
provisioning, journal serialization, model selection, and proposal validation
remain with their dedicated G01/G05/G06/G11/G07 modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from typing import Callable, Mapping
import uuid

from .adapters.factory import resolve_model_adapter
from .builder import GigBuilderError, build_model_draft
from .capabilities import capability_manifest_artifact_ref
from .capability_successor import (
    CapabilitySuccessorApproval,
    CapabilitySuccessorError,
    reviewed_manifest_requires_successor,
    successor_approval_context,
)
from .canonical import (
    CanonicalizationError,
    EntityPrefix,
    canonical_json_bytes,
    canonical_json_digest,
    canonicalize_owned_text,
    digest_imported_bytes,
    generate_entity_id,
    parse_json_front_matter,
    parse_json_bytes,
)
from .config import load_config
from .discovery import build_discovery_artifacts
from .improvement import validate_improvement_manifest
from .learning import load_learning_records, validate_learning_record
from .journal import (
    JournalArtifact,
    JournalConflictError,
    JournalArtifactMissingError,
    JournalEntry,
    JournalTransition,
    JournalWriter,
    read_committed_artifact,
    record_transition,
    record_transition_chain,
    run_with_journal_writer,
)
from .proposal_interview import (
    InterviewSession,
    ProposalInterviewError,
    ReferenceDecision,
    attach_reference_choices,
    build_session,
    approve_session,
    persist_trace,
    session_from_record,
    session_record,
)
from .registry import open_project_registry
from .validators import (
    ValidationFinding,
    ValidationReport,
    validate_gig_builder_session,
    validate_proposal_draft_manifest,
    validate_proposal_workpad,
    validate_serialized_contract,
)
from .graph_set import validate_graph_set
from .workpad import (
    BoundProject,
    ResolvedWorkpad,
    open_locations,
    provision_workpad,
    resolve_bound_project,
    resolve_workpad,
    select_active_workpad,
    workpad_layout_version,
)


_NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
CreateObserver = Callable[[str], None]


class LifecycleError(RuntimeError):
    """A G08 lifecycle transition cannot truthfully continue."""

    code = "lifecycle_error"


_KNOWN_PATH_ALIASES = {
    Path("/var"): Path("/private/var"),
    Path("/tmp"): Path("/private/tmp"),
}


def _definition_source_path(
    value: Path, *, expected_identity: tuple[int, int] | None = None
) -> tuple[Path, Path, tuple[int, int]]:
    """Validate the caller spelling, then return it and its canonical path.

    Link checks deliberately run on the original path before ``resolve``.  The
    two macOS system aliases are accepted only when they resolve to their
    known private locations; arbitrary source links remain refused.
    """

    original = value.expanduser()
    if not original.is_absolute():
        original = Path.cwd() / original
    original = Path(os.path.normpath(os.fspath(original.absolute())))

    def check_components() -> None:
        current = Path(original.anchor)
        for part in original.parts[1:]:
            current /= part
            try:
                info = current.lstat()
            except OSError as exc:
                raise LifecycleError(
                    "graph-set definition must be one regular local JSON file"
                ) from exc
            if stat.S_ISLNK(info.st_mode):
                alias = _KNOWN_PATH_ALIASES.get(current)
                if alias is None:
                    raise LifecycleError(
                        "graph-set definition must be one regular local JSON file"
                    )
                try:
                    if current.resolve(strict=True) != alias:
                        raise LifecycleError(
                            "graph-set definition must be one regular local JSON file"
                        )
                except (OSError, RuntimeError) as exc:
                    raise LifecycleError(
                        "graph-set definition must be one regular local JSON file"
                    ) from exc

    try:
        check_components()
        info = original.stat()
        if not stat.S_ISREG(info.st_mode):
            raise LifecycleError(
                "graph-set definition must be one regular local JSON file"
            )
        resolved = original.resolve(strict=True)
        # Re-check after resolving to close a replacement race between the
        # initial lstat walk and canonicalization.
        check_components()
        current_info = original.stat()
    except LifecycleError:
        raise
    except (OSError, RuntimeError) as exc:
        raise LifecycleError(
            "graph-set definition must be one regular local JSON file"
        ) from exc
    identity = (int(current_info.st_dev), int(current_info.st_ino))
    if expected_identity is not None and identity != expected_identity:
        raise LifecycleError(
            "first Graph Set definition source identity changed before publication"
        )
    return original, resolved, identity


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
class GraphSetProposalResult:
    """A staged, still-pending multi-graph proposal; never an approval."""

    gig_id: str
    proposal_id: str
    workpad: Path
    entry: JournalEntry


@dataclass(frozen=True)
class RevisionResult:
    gig_id: str
    proposal_id: str
    parent_proposal_id: str
    entry: JournalEntry


@dataclass(frozen=True)
class BuilderRecovery:
    """Recovered builder state needed to reopen a browser review safely."""

    proposal_id: str | None
    review: Mapping[str, object]
    builder_ready: bool


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


def select_interview_references(
    *,
    home_root: Path,
    requested_target: Path | None,
    start: InterviewStartResult,
    session: InterviewSession,
    paths: tuple[str, ...],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> tuple[InterviewSession, tuple[str, ...], dict[str, str], dict[str, bytes]]:
    """Resolve operator-entered target-relative paths into pinned references."""

    if session.references:
        raise LifecycleError("interview references have already been selected")
    if not paths:
        raise LifecycleError("enter at least one local reference path")
    bound = resolve_bound_project(home_root=home_root, requested_target=requested_target)
    target_root = bound.target_root.expanduser().resolve(strict=True)
    references: list[ReferenceDecision] = []
    labels: dict[str, str] = {}
    reference_bytes: dict[str, bytes] = {}
    seen: set[Path] = set()
    artifacts: list[JournalArtifact] = []
    for raw_path in paths:
        raw = Path(raw_path).expanduser()
        candidate = raw if raw.is_absolute() else target_root / raw
        if candidate.is_symlink() or not candidate.is_file():
            raise LifecycleError("reference must be an existing regular non-symlink file")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(target_root):
            raise LifecycleError("reference must remain inside the bound target")
        if resolved in seen:
            raise LifecycleError("reference paths must be unique")
        seen.add(resolved)
        content = resolved.read_bytes()
        reference_id = _allocate_interview_id("ref", uuid_factory)
        references.append(
            ReferenceDecision(reference_id, digest_imported_bytes(content), "selected")
        )
        relative_label = resolved.relative_to(target_root).as_posix()
        labels[reference_id] = relative_label
        reference_bytes[reference_id] = content
        artifacts.append(
            JournalArtifact(
                f"review/interviews/{session.session_id}/references/{reference_id}.bin",
                content,
            )
        )
    updated = attach_reference_choices(session, tuple(references))
    record_transition(
        workpad=start.workpad,
        project_id=start.project_id,
        gig_id=start.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="proposal_interview_references_selected",
        body=f"Operator selected {len(references)} explicit local interview reference(s).",
        artifacts=tuple(artifacts),
    )
    return updated, tuple(item.reference_id for item in references), labels, reference_bytes


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


def persist_discovery_manifest(
    *,
    start: InterviewStartResult,
    session: InterviewSession,
    config,
    model_target: str,
    reference_bytes: Mapping[str, bytes],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    observer: CreateObserver | None = None,
) -> JournalEntry:
    """Journal one subordinate G27 discovery manifest for a session revision."""

    improve_context = None
    improve_summary_bytes = None
    manifest_version = 1
    parent_manifest_id = None
    existing_discovery_path = start.workpad / "manifests/gig-discovery-manifest.json"
    if existing_discovery_path.exists():
        try:
            existing_discovery = parse_json_bytes(existing_discovery_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise LifecycleError("existing discovery manifest is not recoverable") from exc
        if not isinstance(existing_discovery, Mapping):
            raise LifecycleError("existing discovery manifest is invalid")
        current_version = existing_discovery.get("manifest_version")
        current_id = existing_discovery.get("manifest_id")
        if type(current_version) is not int or current_version < 1 or not isinstance(current_id, str):
            raise LifecycleError("existing discovery manifest revision is invalid")
        manifest_version = current_version + 1
        parent_manifest_id = current_id
    if session.request_kind == "improve":
        improvement_path = start.workpad / "manifests/improvement-manifest.json"
        pointer_path = start.workpad / "manifests/active-gig-version.json"
        try:
            improvement = parse_json_bytes(improvement_path.read_bytes())
            pointer = parse_json_bytes(pointer_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise LifecycleError("improve discovery context is not recoverable") from exc
        if not isinstance(improvement, Mapping) or not isinstance(pointer, Mapping):
            raise LifecycleError("improve discovery context is invalid")
        learning_ids = improvement.get("learning_record_ids")
        active_version = pointer.get("active_version")
        if (
            not isinstance(learning_ids, list)
            or not learning_ids
            or any(not isinstance(item, str) for item in learning_ids)
            or type(active_version) is not int
            or active_version < 1
        ):
            raise LifecycleError("improve discovery context is incomplete")
        improve_summary = {
            "schema_version": "1.0",
            "kind": "g27_improve_context",
            "learning_record_ids": learning_ids,
            "active_version": active_version,
            "omitted_content_policy": "raw_unselected_and_hidden_context_excluded",
        }
        improve_summary_bytes = canonical_json_bytes(improve_summary)
        improve_context = {
            "learning_record_ids": learning_ids,
            "active_version": active_version,
            "max_source_bytes": len(improve_summary_bytes),
            "omitted_content_policy": "raw_unselected_and_hidden_context_excluded",
        }
    built = build_discovery_artifacts(
        config=config,
        model_target=model_target,
        session=session,
        reference_bytes=reference_bytes,
        improve_context=improve_context,
        improve_summary_bytes=improve_summary_bytes,
        manifest_version=manifest_version,
        parent_manifest_id=parent_manifest_id,
        uuid_factory=uuid_factory,
    )
    return record_transition(
        workpad=start.workpad,
        project_id=start.project_id,
        gig_id=start.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="gig_discovery_manifest_written",
        body=f"G27 discovery manifest recorded for interview {session.session_id}.",
        artifacts=built.artifacts,
        observer=observer,
    )


def approve_interview_session(
    *,
    home_root: Path,
    requested_target: Path | None,
    start: InterviewStartResult,
    session: InterviewSession,
    existing_proposal_id: str | None = None,
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
    if existing_proposal_id is not None:
        proposal_path = start.workpad / "manifests" / "gig-proposal.json"
        try:
            proposal_bytes = proposal_path.read_bytes()
            proposal = parse_json_bytes(proposal_bytes)
        except (OSError, ValueError) as exc:
            raise LifecycleError("model-built proposal is not recoverable") from exc
        if not isinstance(proposal, dict) or proposal.get("proposal_id") != existing_proposal_id:
            raise LifecycleError("model-built proposal identity does not match approval")
        approved = approve_session(
            session,
            proposal_id=existing_proposal_id,
            proposal_sha256=digest_imported_bytes(proposal_bytes),
        )
        snapshot = canonical_json_bytes(session_record(approved))
        report = validate_serialized_contract("proposal-interview.schema.json", snapshot)
        if not report.valid:
            raise LifecycleError("approved builder interview snapshot failed validation")
        record_transition(
            workpad=start.workpad,
            project_id=start.project_id,
            gig_id=start.gig_id,
            handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
            transition="proposal_interview_approved",
            body=f"Operator approved model-built proposal {existing_proposal_id}.",
            artifacts=(JournalArtifact("manifests/proposal-interview.json", snapshot),),
        )
        _persist_interview_trace(start.workpad, approved)
        builder_path = start.workpad / "manifests" / "gig-builder-session.json"
        if builder_path.is_file():
            builder_payload = parse_json_bytes(builder_path.read_bytes())
            if not isinstance(builder_payload, dict):
                raise LifecycleError("builder session snapshot is not recoverable")
            builder_payload["state"] = "approved"
            builder_payload["terminal_reason"] = "operator_approved"
            builder_payload["updated_at"] = approved.updated_at
            builder_bytes = canonical_json_bytes(builder_payload)
            builder_report = validate_serialized_contract(
                "gig-builder-session.schema.json", builder_bytes
            )
            if not builder_report.valid:
                raise LifecycleError("approved builder session failed contract validation")
            record_transition(
                workpad=start.workpad,
                project_id=start.project_id,
                gig_id=start.gig_id,
                handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
                transition="gig_builder_approved",
                body=f"Operator approved Gig builder session {session.session_id}.",
                artifacts=(JournalArtifact("manifests/gig-builder-session.json", builder_bytes),),
            )
        approve_offline(
            home_root=home_root,
            requested_target=requested_target,
            proposal_id=existing_proposal_id,
            uuid_factory=uuid_factory,
        )
        return approved
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


def build_interview_proposal(
    *,
    home_root: Path,
    requested_target: Path | None,
    start: InterviewStartResult,
    session: InterviewSession,
    model_target: str,
    reference_bytes: Mapping[str, bytes],
    network_allowed: bool = False,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> InterviewSession:
    """Research and materialize one reviewable G26 draft, without approval."""

    if session.state != "proposal_ready":
        raise LifecycleError("a complete Gig definition is required before proposal build")
    builder_path = start.workpad / "manifests/gig-builder-session.json"
    if builder_path.is_file():
        try:
            existing_builder = parse_json_bytes(builder_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise LifecycleError("existing builder session snapshot is not recoverable") from exc
        if not isinstance(existing_builder, dict):
            raise LifecycleError("existing builder session snapshot is not an object")
        existing_state = existing_builder.get("state")
        if existing_state == "researching":
            recover_builder_session(start=start, uuid_factory=uuid_factory)
            raise LifecycleError("interrupted builder research was terminalized; start a new session")
        if existing_state in {
            "operator_review",
            "approved",
            "rejected",
            "cancelled",
            "timed_out",
            "unavailable",
            "malformed",
            "budget_exhausted",
            "failed",
            "blocked",
        }:
            raise LifecycleError(
                f"builder session is already terminal or reviewable: {existing_state}"
            )
    config = load_config(home_root)
    commission_path = start.workpad / str(session.request_artifact["path"])
    try:
        commission = commission_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LifecycleError("Gig intent artifact cannot be read as UTF-8") from exc
    try:
        base_selection = _builder_selection(config, model_target)
    except LifecycleError as exc:
        base_selection = _unusable_builder_selection(model_target)
        terminal_reason = "unavailable"
        terminal_payload = _builder_session_record(
            session=session,
            start=start,
            selection=base_selection,
            state=terminal_reason,
            draft_ref=None,
            terminal_reason=terminal_reason,
        )
        terminal_bytes = canonical_json_bytes(terminal_payload)
        terminal_report = validate_gig_builder_session(terminal_bytes)
        if not terminal_report.valid:
            raise LifecycleError(
                "unavailable Gig builder session failed contract validation: "
                + ", ".join(item.code for item in terminal_report.findings)
            ) from exc
        record_transition(
            workpad=start.workpad,
            project_id=start.project_id,
            gig_id=start.gig_id,
            handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
            transition="gig_builder_failed",
            body="Gig builder stopped before research because the selected model is unavailable.",
            artifacts=(JournalArtifact("manifests/gig-builder-session.json", terminal_bytes),),
        )
        raise LifecycleError(str(exc)) from exc
    researching_payload = _builder_session_record(
        session=session,
        start=start,
        selection=base_selection,
        state="researching",
        draft_ref=None,
        terminal_reason=None,
    )
    researching_bytes = canonical_json_bytes(researching_payload)
    researching_report = validate_gig_builder_session(researching_bytes)
    if not researching_report.valid:
        raise LifecycleError("researching Gig builder session failed contract validation")
    record_transition(
        workpad=start.workpad,
        project_id=start.project_id,
        gig_id=start.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="gig_builder_researching",
        body=f"Gig builder is researching with selected target {model_target}.",
        artifacts=(JournalArtifact("manifests/gig-builder-session.json", researching_bytes),),
    )
    try:
        draft, selection = build_model_draft(
            config=config,
            model_target=model_target,
            session=session,
            reference_bytes=reference_bytes,
            intent_text=commission,
            network_allowed=network_allowed,
        )
    except GigBuilderError as exc:
        terminal_state = exc.reason if exc.reason in {
            "cancelled",
            "timed_out",
            "unavailable",
            "malformed",
            "budget_exhausted",
            "blocked",
            "failed",
        } else "failed"
        failed_payload = _builder_session_record(
            session=session,
            start=start,
            selection=base_selection,
            state=terminal_state,
            draft_ref=None,
            terminal_reason=exc.reason,
        )
        failed_bytes = canonical_json_bytes(failed_payload)
        failed_report = validate_gig_builder_session(failed_bytes)
        if not failed_report.valid:
            raise LifecycleError(
                "terminal Gig builder session failed contract validation: "
                + ", ".join(item.code for item in failed_report.findings)
            ) from exc
        record_transition(
            workpad=start.workpad,
            project_id=start.project_id,
            gig_id=start.gig_id,
            handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
            transition="gig_builder_failed",
            body=f"Gig builder stopped before proposal approval: {exc.reason}.",
            artifacts=(JournalArtifact("manifests/gig-builder-session.json", failed_bytes),),
        )
        raise LifecycleError(str(exc)) from exc
    proposal_id = _allocate_local_id(EntityPrefix.GIG_PROPOSAL, uuid_factory)
    model_output = json.dumps(draft.as_dict(), sort_keys=True, separators=(",", ":"))
    artifacts = _build_proposal_artifacts(
        gig_id=start.gig_id,
        project_id=start.project_id,
        proposal_id=proposal_id,
        name=session.request_kind,
        commission=commission,
        model_target=model_target,
        model_output=model_output,
        uuid_factory=uuid_factory,
    )
    proposal_bytes = next(
        item.content for item in artifacts if item.path == "manifests/gig-proposal.json"
    )
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    selection_digest = digest_imported_bytes(canonical_json_bytes(selection))
    endpoint = next(item for item in config.endpoints if item.name == selection["endpoint_name"])
    manifest_id = _allocate_local_id(EntityPrefix.DRAFT_MANIFEST, uuid_factory)
    draft_manifest = {
        "schema_version": "1.0",
        "manifest_version": 1,
        "manifest_id": manifest_id,
        "session_id": session.session_id,
        "project_id": start.project_id,
        "gig_id": start.gig_id,
        "parent_manifest_id": None,
        "model_selection": {**selection, "selection_digest": selection_digest},
        "build": {
            "status": "completed",
            "mode": "deterministic_fixture" if selection["adapter"] == "deterministic" else "configured_model",
            "started_at": created_at,
            "completed_at": created_at,
            "accounting": {
                "model_calls": 1,
                "input_tokens": None,
                "output_tokens": None,
                "elapsed_ms": 0,
                "cost": None,
                "cost_currency": None,
            },
        },
        "proposal_artifact": _artifact_ref(
            "manifests/gig-proposal.json", "application/json", proposal_bytes
        ),
        "research": {
            "summary": draft.summary,
            "citations": list(draft.citations),
            "assumptions": list(draft.assumptions),
            "unresolved_questions": list(draft.unresolved_questions),
        },
        "boundary": {
            "reference_ids": list(session.selected_reference_ids),
            "network": "local_only" if endpoint.adapter == "deterministic" else "configured_provider_only",
            "credential_reference": endpoint.credential,
            "effects": ["write_workpad"],
        },
        "created_at": created_at,
        "updated_at": created_at,
    }
    draft_bytes = canonical_json_bytes(draft_manifest)
    draft_report = validate_proposal_draft_manifest(draft_bytes)
    if not draft_report.valid:
        raise LifecycleError("proposal draft manifest failed contract validation")
    session_record_payload = _builder_session_record(
        session=session,
        start=start,
        selection={**selection, "readiness": "usable", "selection_digest": selection_digest},
        state="operator_review",
        draft_ref=_artifact_ref(
            "manifests/proposal-draft-manifest.json", "application/json", draft_bytes
        ),
        terminal_reason=None,
    )
    session_bytes = canonical_json_bytes(session_record_payload)
    session_report = validate_gig_builder_session(session_bytes)
    if not session_report.valid:
        raise LifecycleError(
            "Gig builder session failed contract validation: "
            + ", ".join(item.code + ":" + item.location for item in session_report.findings)
        )
    _validate_artifacts(artifacts)
    record_transition(
        workpad=start.workpad,
        project_id=start.project_id,
        gig_id=start.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="gig_builder_draft_ready",
        body=f"Model-built proposal draft {proposal_id} is ready for operator review.",
        artifacts=(
            *artifacts,
            JournalArtifact("manifests/proposal-draft-manifest.json", draft_bytes),
            JournalArtifact("manifests/gig-builder-session.json", session_bytes),
        ),
    )
    return session


def recover_builder_session(
    *,
    start: InterviewStartResult,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> BuilderRecovery:
    """Reconcile an interrupted builder before reopening its browser flow.

    A committed ``researching`` snapshot is never retried implicitly. It is
    terminalized as an interrupted failure. A durable review snapshot is
    reopened with its existing proposal identity, so a browser refresh cannot
    allocate another proposal or invoke the model again.
    """

    path = start.workpad / "manifests/gig-builder-session.json"
    if not path.is_file():
        return BuilderRecovery(None, {}, False)
    try:
        payload = parse_json_bytes(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise LifecycleError("builder session snapshot is not recoverable") from exc
    if not isinstance(payload, dict):
        raise LifecycleError("builder session snapshot is not an object")
    state = payload.get("state")
    if state == "researching":
        payload["state"] = "failed"
        payload["terminal_reason"] = "interrupted_build_recovery"
        payload["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        recovered_bytes = canonical_json_bytes(payload)
        report = validate_gig_builder_session(recovered_bytes)
        if not report.valid:
            raise LifecycleError("interrupted builder session failed contract validation")
        record_transition(
            workpad=start.workpad,
            project_id=start.project_id,
            gig_id=start.gig_id,
            handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
            transition="gig_builder_failed",
            body="Gig builder recovered an interrupted research session without retrying it.",
            artifacts=(JournalArtifact("manifests/gig-builder-session.json", recovered_bytes),),
        )
        return BuilderRecovery(None, {}, False)
    if state != "operator_review":
        return BuilderRecovery(None, {}, False)
    draft_ref = payload.get("draft")
    if not isinstance(draft_ref, dict) or not isinstance(draft_ref.get("path"), str):
        raise LifecycleError("reviewable builder session has no draft reference")
    draft_path = start.workpad / draft_ref["path"]
    proposal_path = start.workpad / "manifests/gig-proposal.json"
    try:
        draft = parse_json_bytes(draft_path.read_bytes())
        proposal = parse_json_bytes(proposal_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise LifecycleError("reviewable builder session is missing its proposal artifacts") from exc
    if not isinstance(draft, dict) or not isinstance(proposal, dict):
        raise LifecycleError("reviewable builder artifacts are not objects")
    proposal_id = proposal.get("proposal_id")
    research = draft.get("research")
    if not isinstance(proposal_id, str) or not isinstance(research, dict):
        raise LifecycleError("reviewable builder artifacts are incomplete")
    return BuilderRecovery(proposal_id, research, True)


def _builder_session_record(
    *,
    session: InterviewSession,
    start: InterviewStartResult,
    selection: Mapping[str, object],
    state: str,
    draft_ref: Mapping[str, object] | None,
    terminal_reason: str | None,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "record_version": 1,
        "session_id": session.session_id,
        "project_id": start.project_id,
        "gig_id": start.gig_id,
        "request_kind": "improve" if session.request_kind == "improve" else "create",
        "state": state,
        "revision": session.revision,
        "parent_revision": session.parent_revision,
        "round": session.round,
        "max_rounds": session.max_rounds,
        "intent": {
            "text_artifact": dict(session.request_artifact),
            "content_sha256": session.request_sha256,
            "answered_at": session.updated_at,
            "actor": {"kind": "operator", "id": "local-user"},
        },
        "references": [
            {
                "reference_id": item.reference_id,
                "content_sha256": item.content_sha256,
                "decision": item.decision,
            }
            for item in session.references
        ],
        "questions": [
            {
                "question_id": item.question_id,
                "answer_type": item.answer_type,
                "required": item.required,
                "options": list(item.options),
                "depends_on": list(item.depends_on),
                "rationale": item.rationale,
                "provenance": item.provenance,
            }
            for item in session.questions
        ],
        "answers": [
            {
                "question_id": item.question_id,
                "answer_type": item.answer_type,
                "value": item.value,
                "answered_at": item.answered_at,
            }
            for item in session.answers
        ],
        "model_selection": {
            **dict(selection),
            "selection_actor": {"kind": "operator", "id": "local-user"},
        },
        "policy": {
            "network": "local_only",
            "credential_reference": None,
            "budget": {
                "max_model_calls": 4,
                "max_tool_calls": 0,
                "max_tokens": 4000,
                "max_cost": None,
                "currency": None,
                "max_wall_time_ms": 300000,
                "max_parallel_goals": 1,
            },
            "cancellation": "operator_or_timeout",
        },
        "accounting": {
            "model_calls": 1 if draft_ref is not None else 0,
            "input_tokens": None,
            "output_tokens": None,
            "elapsed_ms": 0,
            "cost": None,
            "cost_currency": None,
        },
        "draft": dict(draft_ref) if draft_ref is not None else None,
        "terminal_reason": terminal_reason,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _builder_selection(config, model_target: str) -> dict[str, object]:
    target = next((item for item in config.model_targets if item.name == model_target), None)
    if target is None:
        raise LifecycleError(f"unknown model target {model_target!r}")
    endpoint = next((item for item in config.endpoints if item.name == target.endpoint), None)
    if endpoint is None:
        raise LifecycleError(f"model target {model_target!r} has no endpoint")
    identity = {
        "target_name": target.name,
        "endpoint_name": endpoint.name,
        "model": target.model,
        "adapter": endpoint.adapter,
    }
    return {
        **identity,
        "readiness": "usable",
        "selection_actor": {"kind": "operator", "id": "local-user"},
        "selection_digest": digest_imported_bytes(canonical_json_bytes(identity)),
    }


def _unusable_builder_selection(model_target: str) -> dict[str, object]:
    """Return a schema-valid non-authoritative selection for terminal failures."""

    requested = model_target if model_target and model_target.replace("-", "").replace("_", "").isalnum() else "unavailable-target"
    identity = {
        "target_name": requested,
        "endpoint_name": "unavailable",
        "model": "unavailable",
        "adapter": "unavailable",
    }
    return {
        **identity,
        "readiness": "unavailable",
        "selection_actor": {"kind": "operator", "id": "local-user"},
        "selection_digest": digest_imported_bytes(canonical_json_bytes(identity)),
    }


def record_builder_state(
    *,
    start: InterviewStartResult,
    session: InterviewSession,
    state: str,
    terminal_reason: str | None,
    transition: str,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> None:
    """Persist a review/rejection state without creating proposal authority."""

    path = start.workpad / "manifests" / "gig-builder-session.json"
    try:
        payload = parse_json_bytes(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise LifecycleError("builder session snapshot is not recoverable") from exc
    if not isinstance(payload, dict):
        raise LifecycleError("builder session snapshot is not an object")
    payload["state"] = state
    payload["terminal_reason"] = terminal_reason
    payload["updated_at"] = session.updated_at
    snapshot = canonical_json_bytes(payload)
    report = validate_gig_builder_session(snapshot)
    if not report.valid:
        raise LifecycleError("updated builder session failed contract validation")
    record_transition(
        workpad=start.workpad,
        project_id=start.project_id,
        gig_id=start.gig_id,
        handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
        transition=transition,
        body=f"Gig builder session {session.session_id} advanced to {state}.",
        artifacts=(JournalArtifact("manifests/gig-builder-session.json", snapshot),),
    )


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
    discovery_path = start.workpad / "manifests/gig-discovery-manifest.json"
    if discovery_path.exists():
        if discovery_path.is_symlink() or not discovery_path.is_file():
            raise LifecycleError("improve discovery manifest is invalid")
        discovery_bytes = discovery_path.read_bytes()
        discovery = parse_json_bytes(discovery_bytes)
        if not isinstance(discovery, Mapping):
            raise LifecycleError("improve discovery manifest is invalid")
        discovery_report = validate_serialized_contract(
            "gig-discovery-manifest.schema.json", discovery_bytes
        )
        if not discovery_report.valid:
            raise LifecycleError("improve discovery manifest failed validation")
        context = discovery.get("improve_context")
        if (
            not isinstance(context, Mapping)
            or list(context.get("learning_record_ids", ())) != ids
            or context.get("active_version") != pointer.get("active_version")
        ):
            raise LifecycleError("improve discovery evidence does not match G20 inputs")

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
    model_output: str | None = None,
    runtime_executables: Mapping[str, str] | None = None,
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

    if model_output is None:
        config = load_config(home)
        binding = resolve_model_adapter(
            config,
            model_target,
            executable_overrides=runtime_executables,
        )
        result = binding.port.invoke(binding.request(role="create", prompt="doctor-probe"))
        proposal_output = result.output_text
    else:
        if not model_output.strip() or "\0" in model_output:
            raise LifecycleError("agent proposal input must be non-empty and NUL-free")
        proposal_output = model_output
    proposal_id = _allocate_local_id(EntityPrefix.GIG_PROPOSAL, uuid_factory)
    artifacts = _build_proposal_artifacts(
        gig_id=gig_id,
        project_id=bound.project_id,
        proposal_id=proposal_id,
        name=name,
        commission=commission,
        model_target=model_target,
        model_output=proposal_output,
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


def propose_graph_set_offline(
    *,
    home_root: Path,
    requested_target: Path | None,
    definition_path: Path,
    gig_id: str | None = None,
    commission: str | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    _first_proposal: bool = False,
    observer: CreateObserver | None = None,
) -> GraphSetProposalResult:
    """Stage a new v2 Graph Set proposal from an explicit local definition.

    The definition is a transport input only.  Every referenced member and
    attachment is checked, copied under the workpad, and journaled with the
    pending proposal in one writer-locked transition.  It can therefore never
    become authority by being edited in its original location afterwards.
    """
    resolved = resolve_workpad(
        home_root=home_root.expanduser().resolve(strict=False),
        requested_target=requested_target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    definition_source_path, definition_path, definition_identity = _definition_source_path(
        definition_path
    )
    try:
        definition_data = definition_path.read_bytes()
        source_set = parse_json_bytes(definition_data)
    except Exception as exc:
        raise LifecycleError("graph-set definition is not valid JSON") from exc
    if not isinstance(source_set, Mapping) or source_set.get("gig_id") != resolved.gig_id:
        raise LifecycleError("graph-set definition must name the resolved existing Gig")
    current_path = resolved.path / "manifests" / "gig-proposal.json"
    current: Mapping[str, object] | None = None
    if current_path.exists():
        if current_path.is_symlink():
            raise LifecycleError("existing Gig proposal authority is redirected")
        try:
            parsed = parse_json_bytes(current_path.read_bytes())
        except Exception as exc:
            raise LifecycleError("existing Gig proposal authority is malformed") from exc
        if not isinstance(parsed, Mapping):
            raise LifecycleError("existing Gig proposal authority is malformed")
        current = parsed
    if _first_proposal:
        if (
            workpad_layout_version(
                resolved.path,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
            )
            != 2
        ):
            raise LifecycleError("first Graph Set proposal requires a provisioned v2 workpad")
        active_pointer = resolved.path / "manifests" / "active-gig-version.json"
        if active_pointer.exists() or active_pointer.is_symlink():
            raise LifecycleError("first Graph Set proposal conflicts with an existing active Gig version")
        if current is not None and (
            current.get("kind") != "create" or current.get("status") != "proposed"
        ):
            raise LifecycleError("first Graph Set proposal conflicts with existing Gig authority")
        active: Mapping[str, object] | None = None
        expected_first_keys = {
            "schema_version",
            "gig_id",
            "name",
            "commission",
            "gig_document",
            "creation_manifest",
            "graphs",
            "shared_policy",
        }
        if set(source_set) != expected_first_keys:
            raise LifecycleError("first Graph Set definition has unsupported fields")
        if source_set.get("schema_version") != "1.0":
            raise LifecycleError("first Graph Set definition has an unsupported schema version")
        name = source_set.get("name")
        first_commission = source_set.get("commission")
        if not isinstance(name, str) or _NAME.fullmatch(name) is None:
            raise LifecycleError("first Graph Set definition has an invalid Gig name")
        if (
            not isinstance(first_commission, str)
            or not first_commission.strip()
            or "\x00" in first_commission
            or len(first_commission) > 20_000
        ):
            raise LifecycleError("first Graph Set definition has an invalid commission")
    else:
        if current is None or current.get("status") != "approved":
            raise LifecycleError("graph-set propose requires an already approved Gig and no pending proposal")
        active_path = resolved.path / "manifests" / "active-gig-version.json"
        try:
            active = parse_json_bytes(active_path.read_bytes())
        except Exception as exc:
            raise LifecycleError("existing Gig has no active approved version") from exc
        if not isinstance(active, Mapping) or type(active.get("active_version")) is not int:
            raise LifecycleError("existing active Gig version is invalid")

    proposal_id = _allocate_local_id(EntityPrefix.GIG_PROPOSAL, uuid_factory)
    prefix = f"manifests/graph-sets/{proposal_id}"
    source_root = definition_path.parent
    staged: dict[str, bytes] = {}
    source_members: list[dict[str, object]] = []

    def verify_source(ref: object) -> tuple[bytes, dict[str, object]]:
        if not isinstance(ref, Mapping):
            raise LifecycleError("graph-set definition reference is malformed")
        path = ref.get("path")
        digest = ref.get("content_sha256")
        size = ref.get("size_bytes")
        if not isinstance(path, str) or not isinstance(digest, str) or type(size) is not int:
            raise LifecycleError("graph-set definition reference is malformed")
        relative = Path(path)
        if relative.is_absolute() or "\\" in path or ".." in relative.parts:
            raise LifecycleError("graph-set definition reference path is unsafe")
        candidate = source_root / relative
        cursor = source_root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise LifecycleError("graph-set definition reference path is redirected")
        if candidate.is_symlink() or not candidate.is_file():
            raise LifecycleError("graph-set definition reference is unavailable")
        data = candidate.read_bytes()
        if len(data) != size or digest_imported_bytes(data) != digest:
            raise LifecycleError("graph-set definition reference bytes changed")
        return data, {
            "path": path,
            "content_sha256": digest,
            "size_bytes": size,
        }

    def safe_source(ref: object) -> bytes:
        data, member = verify_source(ref)
        source_members.append(member)
        return data

    def stage_ref(ref: object, destination: str, *, media_type: str = "application/json") -> dict[str, object]:
        data = safe_source(ref)
        staged[destination] = data
        if isinstance(ref, Mapping) and "canonical_sha256" in ref:
            canonical = ref["canonical_sha256"]
            if canonical is not None:
                if media_type != "application/json":
                    raise LifecycleError("canonical artifact digest requires JSON media")
                try:
                    actual = canonical_json_digest(parse_json_bytes(data))
                except (CanonicalizationError, ValueError) as exc:
                    raise LifecycleError("canonical artifact digest is unavailable") from exc
                if canonical != actual:
                    raise LifecycleError("canonical artifact digest is not authenticated")
            return _artifact_ref(
                destination, media_type, data, canonical_sha256=canonical
            )
        return _artifact_ref(destination, media_type, data)

    def stage_evaluation_contract(
        ref: object,
        destination: str,
        *,
        local_output_ref: object,
        staged_output_ref: object,
    ) -> dict[str, object]:
        """Stage an evaluation contract after binding its local output ref.

        The definition is allowed to name the source-local output contract, but
        the committed evaluation contract must name the immutable staged member.
        This resolves the proposal-id path only after allocation and preserves
        the full authenticated reference identity.
        """
        data = safe_source(ref)
        try:
            payload = parse_json_bytes(data)
        except ValueError as exc:
            raise LifecycleError("evaluation contract is not valid JSON") from exc
        if isinstance(payload, Mapping) and "runtime_comparison" in payload:
            binding = payload.get("runtime_comparison")
            if (
                not isinstance(binding, Mapping)
                or not isinstance(local_output_ref, Mapping)
                or not isinstance(staged_output_ref, Mapping)
            ):
                raise LifecycleError("runtime comparison output contract binding is malformed")
            declared_ref = binding.get("output_contract_ref")
            required_fields = {"path", "content_sha256", "media_type", "size_bytes"}
            allowed_fields = required_fields | {"canonical_sha256"}
            if (
                not isinstance(declared_ref, Mapping)
                or set(declared_ref) - allowed_fields
                or set(local_output_ref) - allowed_fields
                or required_fields - set(declared_ref)
                or required_fields - set(local_output_ref)
                or set(declared_ref) != set(local_output_ref)
                or any(declared_ref.get(field) != local_output_ref.get(field) for field in required_fields)
                or ("canonical_sha256" in local_output_ref and declared_ref.get("canonical_sha256") != local_output_ref.get("canonical_sha256"))
            ):
                raise LifecycleError(
                    "runtime comparison output contract reference shape or identity is not local"
                )
            rewritten = dict(payload)
            rewritten_binding = dict(binding)
            rewritten_output_ref = dict(staged_output_ref)
            if "canonical_sha256" in declared_ref:
                rewritten_output_ref["canonical_sha256"] = declared_ref["canonical_sha256"]
            rewritten_binding["output_contract_ref"] = rewritten_output_ref
            rewritten["runtime_comparison"] = rewritten_binding
            data = canonical_json_bytes(rewritten)
        staged[destination] = data
        return _artifact_ref(destination, "application/json", data)

    graphs = source_set.get("graphs")
    if not isinstance(graphs, list) or not graphs:
        raise LifecycleError("graph-set definition requires at least one graph descriptor")
    descriptors: list[dict[str, object]] = []
    for index, raw in enumerate(graphs):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("graph_id"), str):
            raise LifecycleError("graph-set definition descriptor is malformed")
        graph_id = raw["graph_id"]
        graph_data = safe_source(raw.get("goal_graph"))
        graph = parse_json_bytes(graph_data)
        if not isinstance(graph, dict) or graph.get("gig_id") != resolved.gig_id:
            raise LifecycleError("each staged Goal Graph must belong to the resolved Gig")
        goal_items = graph.get("goals")
        if not isinstance(goal_items, list):
            raise LifecycleError("staged Goal Graph is malformed")
        rewritten_goals: list[dict[str, object]] = []
        for goal_index, goal in enumerate(goal_items):
            if not isinstance(goal, Mapping):
                raise LifecycleError("staged Goal Graph is malformed")
            copied = dict(goal)
            copied["contract"] = stage_ref(
                goal.get("contract"),
                f"{prefix}/{graph_id}/goal-contracts/{goal_index}.md",
                media_type="text/markdown",
            )
            rewritten_goals.append(copied)
        graph = {**graph, "goals": rewritten_goals}
        rewritten_graph = canonical_json_bytes(graph)
        graph_ref = _artifact_ref(f"{prefix}/{graph_id}/goal-graph.json", "application/json", rewritten_graph)
        staged[str(graph_ref["path"])] = rewritten_graph
        descriptor = dict(raw)
        descriptor["goal_graph"] = graph_ref
        output_ref = stage_ref(
            raw.get("output_contract"), f"{prefix}/{graph_id}/output_contract.json"
        )
        descriptor["output_contract"] = output_ref
        for field in (
            "input_contract", "permitted_reference_contract", "review_contract",
            "completion_evidence_contract",
        ):
            descriptor[field] = stage_ref(
                raw.get(field), f"{prefix}/{graph_id}/{field}.json"
            )
        descriptor["evaluation_contract"] = stage_evaluation_contract(
            raw.get("evaluation_contract"),
            f"{prefix}/{graph_id}/evaluation_contract.json",
            local_output_ref=raw.get("output_contract"),
            staged_output_ref=output_ref,
        )
        descriptors.append(descriptor)

    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    graph_set = {
        "schema_version": "1.0",
        "graph_set_id": _allocate_local_id(EntityPrefix.GRAPH_SET, uuid_factory),
        "gig_id": resolved.gig_id,
        "graphs": descriptors,
        "shared_policy": source_set.get("shared_policy"),
        "created_at": created_at,
        "created_by": {"kind": "operator", "id": "local-user", "model_target": None},
    }
    graph_set_data = canonical_json_bytes(graph_set)
    graph_set_ref = _artifact_ref(f"{prefix}/graph-set.json", "application/json", graph_set_data)
    staged[str(graph_set_ref["path"])] = graph_set_data
    validation_root = Path(tempfile.mkdtemp(prefix="gigai-graph-set-stage-"))
    try:
        for path, content in staged.items():
            candidate = validation_root / path
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_bytes(content)
        report = validate_graph_set(graph_set_data, root=validation_root)
    finally:
        shutil.rmtree(validation_root)
    if not report.valid:
        raise LifecycleError("graph-set proposal is invalid: " + ", ".join(f"{item.location}:{item.code}" for item in report.findings))
    if _first_proposal:
        first_gig_document = source_set.get("gig_document")
        first_creation_manifest = source_set.get("creation_manifest")
        gig_document_data = safe_source(first_gig_document)
        try:
            document_text = gig_document_data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LifecycleError("first Graph Set Gig document must be UTF-8 Markdown") from exc
        try:
            canonical_document = canonicalize_owned_text(document_text)
        except CanonicalizationError as exc:
            raise LifecycleError("first Graph Set Gig document is not canonical Markdown") from exc
        if not document_text.strip() or canonical_document != gig_document_data:
            raise LifecycleError("first Graph Set Gig document is not canonical Markdown")
        creation_manifest_data = safe_source(first_creation_manifest)
        try:
            creation_payload = parse_json_bytes(creation_manifest_data)
        except ValueError as exc:
            raise LifecycleError("first Graph Set creation metadata is not valid JSON") from exc
        if (
            not isinstance(creation_payload, Mapping)
            or set(creation_payload) != {
                "schema_version",
                "creation_mode",
                "model_target",
                "model_output",
            }
            or creation_payload.get("schema_version") != "1.0"
            or not all(
                isinstance(creation_payload.get(field), str)
                and "\x00" not in creation_payload[field]
                for field in ("creation_mode", "model_target", "model_output")
            )
        ):
            raise LifecycleError("first Graph Set creation metadata has an unsupported shape")
        gig_document = stage_ref(
            first_gig_document, f"{prefix}/gig.md", media_type="text/markdown"
        )
        creation_manifest = stage_ref(
            first_creation_manifest,
            f"{prefix}/creation-manifest.json",
            media_type="application/json",
        )
    else:
        assert current is not None
        gig_document = current.get("gig_document")
        creation_manifest = current.get("creation_manifest")
        if not isinstance(gig_document, Mapping) or not isinstance(creation_manifest, Mapping):
            raise LifecycleError("existing proposal lacks required immutable artifacts")
    def current_ref(ref: Mapping[str, object]) -> dict[str, object]:
        path = ref.get("path")
        if not isinstance(path, str):
            raise LifecycleError("existing proposal artifact reference is malformed")
        candidate = resolved.path / path
        if candidate.is_symlink() or not candidate.is_file():
            raise LifecycleError("existing proposal artifact is unavailable")
        return _artifact_ref(path, str(ref.get("media_type", "application/octet-stream")), candidate.read_bytes())
    if _first_proposal:
        proposal = {
            "schema_version": "1.0", "proposal_id": proposal_id,
            "gig_id": resolved.gig_id, "project_id": resolved.project_id,
            "name": source_set["name"], "status": "proposed", "kind": "create",
            "created_at": created_at,
            "created_by": {"kind": "operator", "id": "local-user", "model_target": None},
            "base_gig_version": None, "parent_proposal_id": None,
            "change_request": None, "commission": source_set["commission"],
            "gig_document": gig_document, "graph_set": graph_set_ref,
            "creation_manifest": creation_manifest,
        }
    else:
        assert current is not None and active is not None
        proposal = {
            "schema_version": "1.0", "proposal_id": proposal_id,
            "gig_id": resolved.gig_id, "project_id": resolved.project_id,
            "name": current.get("name"), "status": "proposed", "kind": "amend",
            "created_at": created_at,
            "created_by": {"kind": "operator", "id": "local-user", "model_target": None},
            "base_gig_version": active["active_version"],
            "parent_proposal_id": current.get("proposal_id"), "change_request": "Stage a bounded multi-graph Graph Set.",
            "commission": commission if commission is not None else current.get("commission"),
            "gig_document": current_ref(gig_document), "graph_set": graph_set_ref,
            "creation_manifest": current_ref(creation_manifest),
        }
    proposal_data = canonical_json_bytes(proposal)
    if not validate_serialized_contract("gig-proposal-v2.schema.json", proposal_data).valid:
        raise LifecycleError("staged multi-graph proposal failed strict schema validation")
    artifacts = tuple(JournalArtifact(path, data) for path, data in sorted(staged.items()))
    if _first_proposal:
        identity_data = canonical_json_bytes(
            {
                "schema_version": "1.0",
                "kind": "first_graph_set_source_identity",
                "definition_sha256": digest_imported_bytes(definition_data),
                "sources": sorted(source_members, key=lambda item: (str(item["path"]), str(item["content_sha256"]))),
            }
        )
        identity_path = f"{prefix}/first-proposal-inputs.json"
        artifacts += (
            JournalArtifact(identity_path, identity_data),
            JournalArtifact("manifests/gig-proposal.json", proposal_data),
        )

        def revalidate_source_inputs() -> None:
            _definition_source_path(
                definition_source_path,
                expected_identity=definition_identity,
            )
            if definition_source_path.read_bytes() != definition_data:
                raise LifecycleError("first Graph Set definition bytes changed before publication")
            for member in tuple(source_members):
                verify_source(member)

        def publish_first(writer: JournalWriter) -> JournalEntry:
            revalidate_source_inputs()
            try:
                committed_proposal_data, proposal_commit = read_committed_artifact(
                    workpad=resolved.path,
                    project_id=resolved.project_id,
                    gig_id=resolved.gig_id,
                    path="manifests/gig-proposal.json",
                )
            except JournalArtifactMissingError:
                committed_proposal_data = None
                proposal_commit = ""
            except JournalConflictError as exc:
                raise LifecycleError("existing first Graph Set proposal cannot be authenticated") from exc
            if committed_proposal_data is not None:
                candidate = resolved.path / "manifests" / "gig-proposal.json"
                if candidate.is_symlink() or not candidate.is_file() or candidate.read_bytes() != committed_proposal_data:
                    raise LifecycleError("existing first Graph Set proposal working copy differs from committed authority")
                try:
                    existing = parse_json_bytes(committed_proposal_data)
                except ValueError as exc:
                    raise LifecycleError("existing first Graph Set proposal is malformed") from exc
                if (
                    not isinstance(existing, Mapping)
                    or existing.get("gig_id") != resolved.gig_id
                    or existing.get("project_id") != resolved.project_id
                    or existing.get("status") != "proposed"
                    or existing.get("kind") != "create"
                    or existing.get("base_gig_version") is not None
                    or existing.get("parent_proposal_id") is not None
                ):
                    raise LifecycleError("first Graph Set proposal conflicts with committed Gig authority")
                graph_set_ref = existing.get("graph_set")
                if not isinstance(graph_set_ref, Mapping) or not isinstance(graph_set_ref.get("path"), str):
                    raise LifecycleError("existing first Graph Set proposal lacks a Graph Set reference")
                existing_identity_path = str(Path(str(graph_set_ref["path"])).parent / "first-proposal-inputs.json")
                try:
                    existing_identity, _identity_commit = read_committed_artifact(
                        workpad=resolved.path,
                        project_id=resolved.project_id,
                        gig_id=resolved.gig_id,
                        path=existing_identity_path,
                    )
                except JournalArtifactMissingError as exc:
                    raise LifecycleError("existing first Graph Set proposal lacks sealed source identity") from exc
                except JournalConflictError as exc:
                    raise LifecycleError("existing first Graph Set source identity cannot be authenticated") from exc
                identity_candidate = resolved.path / existing_identity_path
                if (
                    identity_candidate.is_symlink()
                    or not identity_candidate.is_file()
                    or identity_candidate.read_bytes() != existing_identity
                ):
                    raise LifecycleError("existing first Graph Set source identity differs from committed authority")
                if existing_identity != identity_data:
                    raise LifecycleError("first Graph Set proposal conflicts with changed source inputs")
                existing_id = existing.get("proposal_id")
                if not isinstance(existing_id, str):
                    raise LifecycleError("existing first Graph Set proposal has no valid identity")
                return JournalEntry(0, "recovered", candidate, proposal_commit)
            if current is not None:
                raise LifecycleError("existing first Graph Set proposal is not committed authority")
            return writer.record(
                JournalTransition(
                    _allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
                    "gig_graph_set_proposed",
                    f"First Graph Set proposal {proposal_id} staged; direct approval is still required.",
                    artifacts,
                    {
                        "artifact_refs": [
                            _artifact_ref(
                                artifact.path,
                                "text/markdown"
                                if artifact.path.endswith(".md")
                                else "application/json",
                                artifact.content,
                            )
                            for artifact in artifacts
                        ]
                    },
                )
            )

        entry = run_with_journal_writer(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            operation=publish_first,
        )
        if entry.handoff_id == "recovered":
            recovered = parse_json_bytes((resolved.path / "manifests" / "gig-proposal.json").read_bytes())
            assert isinstance(recovered, Mapping)
            proposal_id = str(recovered["proposal_id"])
        if observer is not None:
            observer("after_first_graph_set_proposed")
    else:
        artifacts += (JournalArtifact("manifests/gig-proposal.json", proposal_data),)
        entry = record_transition(
            workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
            handoff_id=_allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
            transition="gig_graph_set_proposed",
            body=f"Graph Set proposal {proposal_id} staged; direct approval is still required.",
            artifacts=artifacts,
            front_matter={
                "artifact_refs": [
                    _artifact_ref(
                        artifact.path,
                        "text/markdown" if artifact.path.endswith(".md") else "application/json",
                        artifact.content,
                    )
                    for artifact in artifacts
                ]
            },
        )
    return GraphSetProposalResult(resolved.gig_id, proposal_id, resolved.path, entry)


def propose_first_graph_set_offline(
    *,
    home_root: Path,
    requested_target: Path | None,
    definition_path: Path,
    gig_id: str,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    observer: CreateObserver | None = None,
) -> GraphSetProposalResult:
    """Stage the first pending v2 Graph Set for one explicit provisioned Gig."""

    if not isinstance(gig_id, str) or not gig_id:
        raise LifecycleError("first Graph Set proposal requires an explicit Gig ID")
    return propose_graph_set_offline(
        home_root=home_root,
        requested_target=requested_target,
        definition_path=definition_path,
        gig_id=gig_id,
        uuid_factory=uuid_factory,
        _first_proposal=True,
        observer=observer,
    )


def approve_offline(
    *,
    home_root: Path,
    requested_target: Path | None,
    proposal_id: str,
    capability_manifest_id: str | None = None,
    gig_id: str | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    observer: CreateObserver | None = None,
) -> ApprovalResult:
    """Approve one pending proposal without starting a Run or Goal."""

    home = home_root.expanduser().resolve(strict=False)
    resolved = resolve_workpad(
        home_root=home,
        requested_target=requested_target,
        gig_id=gig_id,
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
    is_v2 = validate_serialized_contract(
        "gig-proposal-v2.schema.json", canonical_json_bytes(proposal)
    ).valid
    report = (
        validate_graph_set_proposal_workpad(workpad, proposal)
        if is_v2
        else validate_proposal_workpad(workpad)
    )
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
    successor_approval: CapabilitySuccessorApproval | None = None

    def preflight_successor() -> None:
        nonlocal successor_approval
        try:
            successor_approval = successor_approval_context(
                workpad=workpad,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
                proposal_id=proposal_id,
                capability_manifest_id=capability_manifest_id,
            )
            if (
                successor_approval is None
                and capability_manifest_id is not None
                and reviewed_manifest_requires_successor(
                    workpad=workpad,
                    project_id=resolved.project_id,
                    gig_id=resolved.gig_id,
                    manifest_id=capability_manifest_id,
                )
            ):
                raise LifecycleError(
                    "reviewed capability manifest requires its authenticated successor"
                )
        except CapabilitySuccessorError as exc:
            raise LifecycleError(str(exc)) from exc

    def publish(sealed: JournalEntry) -> JournalTransition:
        _git(workpad, "tag", tag, sealed.commit)
        if observer is not None:
            observer("after_approval_tag")
        pointer_payload: dict[str, object] = {
            "schema_version": "1.0",
            "gig_id": resolved.gig_id,
            "active_version": version,
            "approved_proposal_id": proposal_id,
            "journal_commit": sealed.commit,
            "journal_tag": tag,
            "approved_at": approved_at,
            "approved_by": {
                "kind": "operator",
                "id": "local-user",
                "model_target": None,
            },
        }
        if is_v2:
            pointer_payload["graph_set"] = proposal["graph_set"]
        else:
            pointer_payload["goal_graph"] = proposal["goal_graph"]
        manifest_ref = (
            successor_approval.reviewed_manifest_ref
            if successor_approval is not None
            else capability_manifest_artifact_ref(
                workpad, capability_manifest_id, gig_id=resolved.gig_id
            )
            if capability_manifest_id is not None
            else _existing_capability_manifest_ref(workpad, resolved.gig_id)
        )
        if manifest_ref is not None:
            pointer_payload["capability_manifest"] = manifest_ref
        pointer = canonical_json_bytes(pointer_payload)
        if not validate_serialized_contract(
            "active-gig-version-v2.schema.json" if is_v2 else "active-gig-version.schema.json", pointer
        ).valid:
            raise LifecycleError("active-version pointer failed schema validation")
        artifacts = [JournalArtifact("manifests/active-gig-version.json", pointer)]
        # A newly selected manifest is part of the accepted-version authority,
        # not merely a working-tree convenience for the pointer ref.  Later
        # versions carry the immutable reference without republishing it.
        if capability_manifest_id is not None and successor_approval is None:
            manifest_path = manifest_ref["path"]
            assert isinstance(manifest_path, str)
            manifest_bytes = (workpad / manifest_path).read_bytes()
            if (
                digest_imported_bytes(manifest_bytes) != manifest_ref["content_sha256"]
                or len(manifest_bytes) != manifest_ref["size_bytes"]
            ):
                raise LifecycleError("capability manifest changed during approval")
            artifacts.append(JournalArtifact(manifest_path, manifest_bytes))
        return JournalTransition(
            _allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
            "gig_accepted",
            f"Gig version {version} is active at sealed commit {sealed.commit}.",
            tuple(artifacts),
            {
                "gig_version": version,
                "artifact_refs": [
                    {
                        "path": artifact.path,
                        "content_sha256": digest_imported_bytes(artifact.content),
                        "media_type": "application/json",
                        "size_bytes": len(artifact.content),
                    }
                    for artifact in artifacts
                ],
            },
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
        preflight=preflight_successor,
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
    pointer_path = workpad / "manifests" / "active-gig-version.json"
    sealed_commit = _git(workpad, "rev-parse", "--verify", "HEAD").stdout.strip()
    # After Commit B, HEAD no longer points at the tagged Commit A.  A
    # successful recovery can therefore be replayed only by following its
    # already-authenticated pointer back to the sealed approval commit.  A
    # stale base pointer after a crash is deliberately ignored because its
    # approved proposal ID differs from this recovery request.
    if pointer_path.is_file() and not pointer_path.is_symlink():
        try:
            pointer_hint = parse_json_bytes(pointer_path.read_bytes())
        except (CanonicalizationError, OSError):
            pointer_hint = None
        if (
            isinstance(pointer_hint, dict)
            and pointer_hint.get("approved_proposal_id") == proposal_id
            and isinstance(pointer_hint.get("journal_commit"), str)
        ):
            sealed_commit = str(pointer_hint["journal_commit"])
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
    is_v2 = validate_serialized_contract(
        "gig-proposal-v2.schema.json", canonical_json_bytes(proposal)
    ).valid

    def recover(writer: JournalWriter) -> ApprovalResult:
        # Recovery validation and the missing Commit B publication share this
        # writer lock; a swapped sidecar/source cannot pass outside the lock.
        try:
            successor_approval = successor_approval_context(
                workpad=workpad,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
                proposal_id=proposal_id,
                capability_manifest_id=capability_manifest_id,
                allow_approved=True,
                allow_published=True,
            )
            if (
                successor_approval is None
                and capability_manifest_id is not None
                and reviewed_manifest_requires_successor(
                    workpad=workpad,
                    project_id=resolved.project_id,
                    gig_id=resolved.gig_id,
                    manifest_id=capability_manifest_id,
                )
            ):
                raise LifecycleError(
                    "reviewed capability manifest requires its authenticated successor"
                )
        except CapabilitySuccessorError as exc:
            raise LifecycleError(str(exc)) from exc
        payload: dict[str, object] | None = None
        if pointer_path.exists():
            pointer = pointer_path.read_bytes()
            if not validate_serialized_contract(
                "active-gig-version-v2.schema.json" if is_v2 else "active-gig-version.schema.json", pointer
            ).valid:
                raise LifecycleError("existing active-version pointer is invalid")
            parsed_payload = parse_json_bytes(pointer)
            if not isinstance(parsed_payload, dict):
                raise LifecycleError("existing active-version pointer is malformed")
            payload = parsed_payload
        if payload is not None and payload.get("journal_commit") == sealed_commit:
            if payload.get("approved_proposal_id") != proposal_id:
                raise LifecycleError(
                    "existing active-version pointer names another approval"
                )
            if successor_approval is not None:
                if payload.get("capability_manifest") != successor_approval.reviewed_manifest_ref:
                    raise LifecycleError(
                        "existing active-version pointer has another capability manifest"
                    )
            elif capability_manifest_id is not None:
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
        if payload is None:
            if version != 1:
                raise LifecycleError("existing active-version pointer is unavailable")
        else:
            is_previous_pointer = payload.get("active_version") == version - 1
            if is_v2:
                is_previous_pointer = is_previous_pointer and payload.get(
                    "approved_proposal_id"
                ) == proposal.get("parent_proposal_id")
            if not is_previous_pointer:
                raise LifecycleError("existing active-version pointer names another approval")
        approved_at = metadata.get("timestamp")
        if not isinstance(approved_at, str):
            raise LifecycleError("sealed approval handoff lacks its timestamp")
        pointer_payload: dict[str, object] = {
            "schema_version": "1.0",
            "gig_id": resolved.gig_id,
            "active_version": version,
            "approved_proposal_id": proposal_id,
            "journal_commit": sealed_commit,
            "journal_tag": tag,
            "approved_at": approved_at,
            "approved_by": {
                "kind": "operator",
                "id": "local-user",
                "model_target": None,
            },
        }
        if is_v2:
            pointer_payload["graph_set"] = proposal["graph_set"]
        else:
            pointer_payload["goal_graph"] = proposal["goal_graph"]
        manifest_ref = (
            successor_approval.reviewed_manifest_ref
            if successor_approval is not None
            else capability_manifest_artifact_ref(
                workpad, capability_manifest_id, gig_id=resolved.gig_id
            )
            if capability_manifest_id is not None
            else _existing_capability_manifest_ref(workpad, resolved.gig_id)
        )
        if manifest_ref is not None:
            pointer_payload["capability_manifest"] = manifest_ref
        pointer = canonical_json_bytes(pointer_payload)
        if not validate_serialized_contract(
            "active-gig-version-v2.schema.json" if is_v2 else "active-gig-version.schema.json", pointer
        ).valid:
            raise LifecycleError(
                "recovered active-version pointer failed schema validation"
            )
        artifacts = [JournalArtifact("manifests/active-gig-version.json", pointer)]
        if capability_manifest_id is not None and successor_approval is None:
            manifest_path = manifest_ref["path"]
            assert isinstance(manifest_path, str)
            manifest_bytes = (workpad / manifest_path).read_bytes()
            if (
                digest_imported_bytes(manifest_bytes) != manifest_ref["content_sha256"]
                or len(manifest_bytes) != manifest_ref["size_bytes"]
            ):
                raise LifecycleError("capability manifest changed during recovery")
            artifacts.append(JournalArtifact(manifest_path, manifest_bytes))
        return ApprovalResult(
            resolved.gig_id,
            proposal_id,
            version,
            sealed_commit,
            writer.record(
                JournalTransition(
                    _allocate_local_id(EntityPrefix.HANDOFF, uuid_factory),
                    "gig_accepted",
                    f"Recovered active Gig version {version} at sealed commit {sealed_commit}.",
                    tuple(artifacts),
                    {
                        "gig_version": version,
                        "artifact_refs": [
                            {
                                "path": artifact.path,
                                "content_sha256": digest_imported_bytes(artifact.content),
                                "media_type": "application/json",
                                "size_bytes": len(artifact.content),
                            }
                            for artifact in artifacts
                        ],
                    },
                ),
                # Successor recovery publishes only the missing pointer; the
                # already reviewed manifest must never be re-journaled.  The
                # legacy branch preserves its historical identical-artifact
                # replay behavior.
                allow_artifact_replacement=(
                    pointer_path.exists()
                    or (
                        capability_manifest_id is not None
                        and successor_approval is None
                    )
                ),
            ).commit,
            tag,
        )

    return run_with_journal_writer(
        workpad=workpad,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=recover,
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
    gig_id: str | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> JournalEntry:
    """Record rejection of one pending proposal without creating a version.

    ``gig_id`` is optional for compatibility with the historical active-Gig
    selection path. When supplied, workpad resolution is exact and never falls
    back to the target's active Gig or scans other Gigs.
    """

    if type(reason) is not str or not reason.strip() or "\0" in reason:
        raise LifecycleError(
            "rejection reason must be non-empty text without NUL bytes"
        )
    resolved, proposal = _pending_proposal(
        home_root, requested_target, proposal_id, gig_id=gig_id
    )
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
    home_root: Path,
    requested_target: Path | None,
    proposal_id: str,
    *,
    gig_id: str | None = None,
) -> tuple[ResolvedWorkpad, dict[str, object]]:
    home = home_root.expanduser().resolve(strict=False)
    resolved = resolve_workpad(
        home_root=home,
        requested_target=requested_target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    report = validate_pending_proposal_workpad(resolved.path)
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
    if payload.get("gig_id") != resolved.gig_id:
        raise LifecycleError("proposal Gig ID does not match the selected Gig")
    if payload.get("status") not in {"drafting", "proposed"}:
        raise LifecycleError("only a pending proposal can receive this transition")
    return resolved, payload


_ARTIFACT_CANONICAL_UNSET = object()


def _artifact_ref(
    path: str,
    media_type: str,
    data: bytes,
    *,
    canonical_sha256: str | None | object = _ARTIFACT_CANONICAL_UNSET,
) -> dict[str, object]:
    reference = {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": media_type,
        "size_bytes": len(data),
    }
    if canonical_sha256 is not _ARTIFACT_CANONICAL_UNSET:
        reference["canonical_sha256"] = canonical_sha256
    return reference


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

    return allocate_gig_id(is_persisted=persisted, uuid_factory=uuid_factory)


def allocate_gig_id(
    *, is_persisted: Callable[[str], bool], uuid_factory: Callable[[], uuid.UUID]
) -> str:
    """Allocate a canonical Gig identity for lifecycle-owned callers."""

    return generate_entity_id(
        EntityPrefix.GIG, is_persisted=is_persisted, uuid_factory=uuid_factory
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


def validate_graph_set_proposal_workpad(workpad: Path, proposal: Mapping[str, object]) -> ValidationReport:
    """Validate a pending v2 proposal without treating a mutable projection as authority."""
    graph_set_ref = proposal.get("graph_set")
    if not isinstance(graph_set_ref, Mapping):
        return ValidationReport((ValidationFinding("graph_set", "graph_set_invalid", "proposal has no Graph Set reference"),))
    path = graph_set_ref.get("path")
    if not isinstance(path, str):
        return ValidationReport((ValidationFinding("graph_set", "graph_set_invalid", "proposal Graph Set reference is malformed"),))
    candidate = workpad / path
    if candidate.is_symlink() or not candidate.is_file():
        return ValidationReport((ValidationFinding("graph_set", "graph_set_invalid", "proposal Graph Set is unavailable"),))
    report = validate_graph_set(candidate.read_bytes(), root=workpad)
    if not report.valid:
        return report
    if graph_set_ref.get("content_sha256") != digest_imported_bytes(candidate.read_bytes()):
        return ValidationReport((ValidationFinding("graph_set", "graph_set_invalid", "proposal Graph Set digest changed"),))
    graph_set = parse_json_bytes(candidate.read_bytes())
    if not isinstance(graph_set, Mapping) or graph_set.get("gig_id") != proposal.get("gig_id"):
        return ValidationReport((ValidationFinding("graph_set", "graph_set_invalid", "proposal and Graph Set have different Gig identities"),))
    return ValidationReport(())


def validate_pending_proposal_workpad(workpad: Path) -> ValidationReport:
    """Dispatch validation to the proposal schema's matching workpad validator."""
    try:
        proposal_bytes = (workpad / "manifests" / "gig-proposal.json").read_bytes()
        proposal = parse_json_bytes(proposal_bytes)
    except (OSError, ValueError):
        return validate_proposal_workpad(workpad)
    if isinstance(proposal, Mapping) and validate_serialized_contract(
        "gig-proposal-v2.schema.json", canonical_json_bytes(proposal)
    ).valid:
        return validate_graph_set_proposal_workpad(workpad, proposal)
    return validate_proposal_workpad(workpad)


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
    # G22 shares state.sqlite with rebuildable SCOUT projections.  It takes the
    # same database lock as index publication; this function never takes the
    # journal writer lock, preserving the documented journal -> database order.
    from .index import database_lock

    with database_lock(workpad):
        connection = sqlite3.connect(workpad / "state.sqlite")
        try:
            persist_trace(connection, session, workpad=workpad, already_locked=True)
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


def _existing_capability_manifest_ref(
    workpad: Path, gig_id: str
) -> Mapping[str, object] | None:
    """Carry an existing approved pointer reference into the next version."""

    path = workpad / "manifests" / "active-gig-version.json"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise LifecycleError("active-version pointer is invalid")
    payload = parse_json_bytes(path.read_bytes())
    if not isinstance(payload, dict):
        raise LifecycleError("active-version pointer is invalid")
    reference = payload.get("capability_manifest")
    if reference is None:
        return None
    if not isinstance(reference, Mapping):
        raise LifecycleError("active-version capability manifest reference is invalid")
    path_value = reference.get("path")
    if not isinstance(path_value, str):
        raise LifecycleError("active-version capability manifest reference is invalid")
    path = Path(path_value)
    if len(path.parts) != 3 or path.parts[0:2] != ("manifests", "capabilities") or not path.parts[2].endswith(".json"):
        raise LifecycleError("active-version capability manifest reference is invalid")
    manifest_id = path.parts[2][:-5]
    expected = capability_manifest_artifact_ref(workpad, manifest_id, gig_id=gig_id)
    if dict(reference) != expected:
        raise LifecycleError("active-version capability manifest reference is stale or invalid")
    return expected


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
    "GraphSetProposalResult",
    "InterviewStartResult",
    "LifecycleError",
    "RevisionResult",
    "approve_interview_session",
    "approve_offline",
    "create_offline",
    "persist_discovery_manifest",
    "persist_interview_session",
    "propose_graph_set_offline",
    "record_feedback",
    "reject_offline",
    "revise_offline",
    "stage_improvement_manifest",
    "start_interview",
]
