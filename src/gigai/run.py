"""The bounded, deterministic G13/G14 Run lifecycle.

Authority is resolved from committed journal bytes before a Run ID or
directory is allocated. G14 schedules the sealed Graph one automatic
``local_capability`` Goal at a time; workers write only Run-scoped proof
artifacts and never invoke a provider, shell, or target process.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import copy
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import traceback
import uuid
from typing import Callable, Mapping

from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    derive_deterministic_id,
    digest_imported_bytes,
    digest_owned_text,
    generate_entity_id,
    parse_json_bytes,
    parse_json_front_matter,
    validate_entity_id,
)
from .config import load_config
from .index import read_index
from .journal import JournalArtifact, JournalConflictError, JournalEntry, JournalTransition, JournalWriter, record_transition, run_with_journal_writer
from .model_discovery import recorded_target_readiness, resolve_target_readiness
from .model_targets import resolve_model_target
from .validators import (
    validate_goal_graph,
    validate_model_invocation,
    validate_serialized_contract,
)
from .graph_set import (
    _attached_contract_valid,
    _budget_within,
    graph_set_descriptor,
    validate_graph_set,
    validate_selection_record,
)
from .graph_node_registry import lookup as lookup_graph_node
from .scout.find_jobs.contracts import (
    ASSESS_LOCAL_EFFECTS,
    AcquireInput,
    AcquireOutput,
    ArtifactRef,
    AssessInput,
    FindJobsConfig,
    FindJobsRunInput,
    GoalError,
    ModelTarget,
    NodeContext,
    NodeFailure,
    NodeReceipt,
    NodeStatus,
    PresentInput,
    Producer,
    PinnedResume,
    RunRequest,
    SelectionReasonCode,
    UsageBlock,
    aggregate_status,
)
from .workpad import ResolvedWorkpad, resolve_workpad


class RunError(RuntimeError):
    """A Run cannot truthfully be prepared or observed."""


class _RunInterrupted(RunError):
    """Execution stopped because the sealed target observation diverged."""


class _PreScheduleFailure(RunError):
    """A sealed Graph cannot be scheduled before any Goal starts."""


RunObserver = Callable[[str], None]


@dataclass(frozen=True)
class RunResult:
    run_id: str
    gig_id: str
    gig_version: int
    workpad: Path
    run_path: Path
    status: str
    run_started: JournalEntry
    terminal: JournalEntry | None


@dataclass(frozen=True)
class ProposalRunRequest:
    """Sealed host-owned inputs for one local Scout proposal Run.

    This is intentionally a closed caller DTO: no prompt, arbitrary operation,
    or hosted target can enter the scheduler.  Source bytes are resolved later
    by the authenticated proposal service from these exact selectors.
    """

    graph_selector: str
    model_target: str
    posting_selector: Mapping[str, object]
    private_selectors: tuple[Mapping[str, object], ...]
    local_allowed: bool = True
    budget: object | None = None


@dataclass(frozen=True)
class TailorRunRequest:
    """Sealed host inputs for one explicit local Tailor Run.

    Selectors are authority handles only; the Tailor lane hydrates their
    committed bytes after the Run has been allocated and before invoking the
    configured local target.  This is deliberately separate from the proposal
    assessment operation.
    """

    graph_selector: str
    model_target: str
    posting_selector: Mapping[str, object]
    private_selectors: tuple[Mapping[str, object], ...]
    requested_outputs: tuple[str, ...] = ("resume",)
    proposal_selector: Mapping[str, object] | None = None
    answer_selectors: tuple[Mapping[str, object], ...] = ()
    local_allowed: bool = True
    budget: object | None = None


@dataclass(frozen=True)
class InterviewRunRequest:
    """Sealed local inputs for the Scout prepare-interview graph entry."""

    graph_selector: str
    selection: Mapping[str, object]
    content: Mapping[str, object]
    operation_key: str


@dataclass(frozen=True)
class _FindJobsRunExecution:
    """Validated find-jobs inputs handed to the common Run sealing path."""

    request: RunRequest
    config: FindJobsConfig
    config_bytes: bytes
    pinned_resume: PinnedResume
    input_bytes: bytes


@dataclass(frozen=True)
class ResumeDetails:
    """``resolve_newest_resume``'s pick, plus the reference's display metadata.

    ``pinned`` is the exact same ``PinnedResume`` ``resolve_newest_resume``
    returns (record_id/revision_id/content digest) -- a sealed shape used by
    the find-jobs run input contract. ``label``/``created_at`` come from the
    winning ``g45_reference`` import record (uat-bug-004: the Configuration
    card shows these instead of raw ids) and are additive display-only
    fields, never part of the sealed run input.
    """

    pinned: PinnedResume
    label: str | None
    created_at: str | None


_RESUME_DETAILS_CACHE_LOCK = threading.Lock()
# uat-bug-008: resolving the newest resume replays every committed
# records/references/run-inputs artifact (one `git log` per path -- see
# journal._capture_committed_snapshot) each time it runs, so a few dozen
# journal commits made this take seconds, and /api/config called it twice
# (once via resume_preview, once via resume_metadata). Cache the resolved
# ResumeDetails per workpad, keyed by the workpad's exact git HEAD: any new
# commit (including `gigai scout resume add`) changes HEAD and misses the
# cache, so a cached entry can never serve a resume that predates the
# newest commit. The HEAD read itself is one cheap `git rev-parse`, not the
# expensive snapshot walk.
_resume_details_cache: dict[tuple[str, str], "ResumeDetails | RunError"] = {}


def _cheap_workpad_head(root: Path) -> str | None:
    """One cheap ``git rev-parse HEAD`` against the workpad -- never the

    expensive committed-artifact snapshot walk. Returns ``None`` (never
    cached) if the workpad has no commits yet or git is unavailable, so a
    lookup failure always falls through to the real resolution below.
    """

    try:
        result = _git(root, "rev-parse", "--verify", "HEAD", check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    head = result.stdout.strip()
    return head or None


def resolve_newest_resume_details(
    home_root: Path, target: Path | None
) -> ResumeDetails:
    """Resolve the newest committed ``resume`` record, its exact revision,

    and the reference's display metadata (label + created date). The single
    implementation of "which resume wins" lives here; ``resolve_newest_resume``
    is a thin wrapper returning just the sealed ``PinnedResume`` piece.

    Cached per workpad, keyed by its exact git HEAD (see
    ``_resume_details_cache``) -- a fresh commit always misses.
    """

    from . import private_records
    from .scout.inputs import _record_revision

    try:
        resolved = resolve_workpad(
            home_root=home_root,
            requested_target=target,
            gig_id=None,
            allow_semantic_state=True,
        )
    except Exception as exc:
        raise RunError("find_jobs_resume_required: committed resume records are unavailable") from exc

    cache_key: tuple[str, str] | None = None
    head = _cheap_workpad_head(resolved.path)
    if head is not None:
        cache_key = (str(resolved.path), head)
        with _RESUME_DETAILS_CACHE_LOCK:
            cached = _resume_details_cache.get(cache_key)
        if cached is not None:
            if isinstance(cached, RunError):
                raise cached
            return cached

    def _cache_error(message: str) -> RunError:
        error = RunError(message)
        if cache_key is not None:
            with _RESUME_DETAILS_CACHE_LOCK:
                _resume_details_cache[cache_key] = error
        return error

    try:
        imports = private_records.list_imports(
            home_root=home_root,
            requested_target=target,
            family="reference",
            gig_id=resolved.gig_id,
        )
    except Exception as exc:
        raise _cache_error(
            "find_jobs_resume_required: committed resume records are unavailable"
        ) from exc

    resume_imports = [
        item
        for item in imports
        if item.get("kind") == "resume" and isinstance(item.get("reference_id"), str)
    ]
    if not resume_imports:
        raise _cache_error("find_jobs_resume_required: no committed resume is available")
    resume_imports.sort(
        key=lambda item: (str(item.get("created_at", "")), str(item.get("reference_id", "")))
    )
    snapshot = private_records._private_snapshot(resolved)
    record_ids = sorted(
        {
            Path(path).parts[1]
            for path in snapshot.artifacts
            if len(Path(path).parts) == 4
            and Path(path).parts[0] == "records"
            and Path(path).parts[1].startswith("record_")
            and Path(path).parts[2] == "revisions"
        }
    )
    linked: list[tuple[dict[str, object], str, str, dict[str, object]]] = []
    for imported in resume_imports:
        reference_id = imported["reference_id"]
        assert isinstance(reference_id, str)
        for record_id in record_ids:
            try:
                revisions = private_records.list_revisions(
                    resolved=resolved, record_id=record_id, snapshot=snapshot
                )
            except Exception:
                continue
            if not revisions:
                continue
            revision = revisions[-1]
            content = revision.get("content")
            if not isinstance(content, Mapping):
                continue
            if content.get("family") != "g45_reference" or content.get("reference_id") != reference_id:
                continue
            revision_id = revision.get("revision_id")
            if not isinstance(revision_id, str):
                continue
            linked.append((imported, record_id, revision_id, dict(content)))
    if not linked:
        raise _cache_error(
            "find_jobs_resume_required: no committed resume record revision is available"
        )
    imported, record_id, revision_id, content_ref = max(
        linked,
        key=lambda item: (
            str(item[0].get("created_at", "")),
            str(item[2]),
        ),
    )
    try:
        # This is the exact Scout input-chain guard used by other private
        # record consumers.  Imported references use g45_reference content and
        # therefore do not have a native sidecar; private_records.list_revisions
        # remains the authenticated fallback for that frozen C1 shape.
        try:
            _record_revision(resolved, snapshot, record_id, revision_id)
        except Exception:
            pass
        selected = private_records.read_record(
            home_root=home_root,
            requested_target=target,
            record_id=record_id,
            revision_id=revision_id,
            content=True,
            gig_id=resolved.gig_id,
        )
        content = selected.get("content")
        if not isinstance(content, bytes):
            raise ValueError("resume content is unavailable")
        digest = digest_imported_bytes(content)
        snapshot_ref = content_ref.get("snapshot_ref")
        if isinstance(snapshot_ref, Mapping) and snapshot_ref.get("content_sha256") != digest:
            raise ValueError("resume content digest differs from its committed snapshot")
        label = imported.get("label")
        created_at = imported.get("created_at")
        result = ResumeDetails(
            pinned=PinnedResume(record_id, revision_id, digest),
            label=label if isinstance(label, str) else None,
            created_at=created_at if isinstance(created_at, str) else None,
        )
    except Exception as exc:
        raise _cache_error(
            "find_jobs_resume_required: selected resume is unavailable"
        ) from exc
    if cache_key is not None:
        with _RESUME_DETAILS_CACHE_LOCK:
            _resume_details_cache[cache_key] = result
    return result


def resolve_newest_resume(
    home_root: Path, target: Path | None
) -> PinnedResume:
    """Resolve the newest committed ``resume`` record and exact revision."""

    return resolve_newest_resume_details(home_root, target).pinned


_ZERO_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "cost": None,
    "currency": None,
    "cost_status": "not_applicable",
}
_TERMINAL_GOAL_STATES = frozenset({"complete", "failed", "blocked", "cancelled"})


def launch_run(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str | None = None,
    version: int | None = None,
    wait: bool = False,
    invocation_argv: tuple[str, ...] = ("gigai", "run"),
    operator_consent: Mapping[str, object] | None = None,
    run_plan_id: str | None = None,
    execute_provider_review: bool = False,
    proposal_execution: ProposalRunRequest | None = None,
    tailor_execution: TailorRunRequest | None = None,
    interview_execution: InterviewRunRequest | None = None,
    find_jobs_execution: _FindJobsRunExecution | None = None,
    ui_loopback_verified: bool = False,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    observer: RunObserver | None = None,
) -> RunResult:
    """Prepare, commit, and execute one deterministic Run."""

    observer = observer or (lambda _step: None)
    resolved = resolve_workpad(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    selected_plan = None
    if run_plan_id is not None:
        from .run_plan import RunPlanError, read_run_plan

        try:
            # Read and digest all sealed plan sources before consulting the
            # journal projection, so a tamper refusal remains deterministic
            # even when the workpad is consequently divergent.
            selected_plan = read_run_plan(
                home_root=home_root,
                requested_target=requested_target,
                gig_id=resolved.gig_id,
                run_plan_id=run_plan_id,
            )
        except RunPlanError as exc:
            raise RunError(f"{exc.code}: {exc}") from exc
        if version is None and isinstance(selected_plan.plan.get("gig_version"), int):
            # A sealed Plan names an immutable approved version.  Do not let a
            # newer active Graph Set replace its selected graph at redemption.
            version = selected_plan.plan["gig_version"]
    projection = read_index(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
    )
    authority = _resolve_authority(resolved, projection, version)
    proposal = authority["proposal"]
    selector = (
        "find-jobs-functional"
        if find_jobs_execution is not None
        else (
            (proposal_execution or tailor_execution or interview_execution).graph_selector
            if proposal_execution is not None
            or tailor_execution is not None
            or interview_execution is not None
            else (
                selected_plan.plan.get("selected_graph_id")
                if selected_plan is not None
                and isinstance(selected_plan.plan.get("selected_graph_id"), str)
                else None
            )
        )
    )
    graph, selected_descriptor = resolve_selected_graph_authority(
        resolved, authority, selector
    )
    proposal_request_bytes: bytes | None = None
    proposal_config: object | None = None
    proposal_budget: object | None = None
    if sum(
        item is not None
        for item in (
            proposal_execution,
            tailor_execution,
            interview_execution,
            find_jobs_execution,
        )
    ) > 1:
        raise RunError("run_input_invalid: domain entries cannot be combined")
    if find_jobs_execution is not None:
        if selected_plan is not None or execute_provider_review:
            raise RunError(
                "find_jobs_run_authority_refused: find-jobs cannot combine a review Plan or provider execution"
            )
        if operator_consent is None:
            raise RunError(
                "find_jobs_run_consent_required: direct local UI confirmation is required"
            )
    elif proposal_execution is not None or tailor_execution is not None or interview_execution is not None:
        if selected_plan is not None or execute_provider_review:
            raise RunError(
                "proposal_run_authority_refused: proposal entry cannot combine a review Plan or provider execution"
            )
        # Resolve configuration once, before sealing.  The proposal branch
        # never reloads mutable config after ``run_started``; the sealed
        # request and this pinned object are its only execution inputs.
        proposal_config = None if interview_execution is not None else load_config(home_root)
        active_request = proposal_execution or tailor_execution
        proposal_budget = copy.deepcopy(active_request.budget) if active_request is not None else None
        if interview_execution is not None:
            proposal_request_bytes = _validate_interview_run_entry(
                authority=authority, graph=graph, selected_descriptor=selected_descriptor,
                request=interview_execution, resolved=resolved,
            )
        elif proposal_execution is not None:
            proposal_request_bytes = _validate_proposal_run_entry(
                resolved=resolved,
                authority=authority,
                graph=graph,
                selected_descriptor=selected_descriptor,
                request=proposal_execution,
                config=proposal_config,
            )
        else:
            proposal_request_bytes = _validate_tailor_run_entry(
                resolved=resolved,
                authority=authority,
                graph=graph,
                selected_descriptor=selected_descriptor,
                request=tailor_execution,
                config=proposal_config,
            )
        if operator_consent is None:
            raise RunError(
                "proposal_run_consent_required: direct local confirmation is required"
            )
    target_before = _target_observation(resolved)
    run_plan_ref: dict[str, object] | None = None
    if run_plan_id is not None:
        assert selected_plan is not None
        _validate_plan_handoff(
            resolved=resolved,
            plan=selected_plan.plan,
            plan_id=run_plan_id,
            plan_digest=selected_plan.content_sha256,
            gig_version=authority["version"],
            graph=graph,
            authority=authority,
            selected_descriptor=selected_descriptor,
            config=load_config(home_root),
        )
        plan_bytes = (
            resolved.path / "run-plans" / run_plan_id / "run-plan.json"
        ).read_bytes()
        run_plan_ref = {
            "path": f"run-plans/{run_plan_id}/run-plan.json",
            "content_sha256": digest_imported_bytes(plan_bytes),
            "media_type": "application/json",
            "size_bytes": len(plan_bytes),
        }
        if operator_consent is None:
            raise RunError(
                "run_plan_consent_mismatch: Run Plan handoff requires direct --confirm consent"
            )
    redeemed_consent = None
    if operator_consent is not None:
        _validate_operator_consent(
            operator_consent, ui_loopback_verified=ui_loopback_verified
        )
        redeemed_consent = {
            **dict(operator_consent),
            "confirmation_id": f"confirm_{uuid_factory()}",
            "redeemed_before_allocation": True,
            "scope": {
                "project_id": resolved.project_id,
                "gig_id": resolved.gig_id,
                "gig_version": authority["version"],
                "target_kind": resolved.target_kind,
                "target_observation_sha256": target_before["observation_sha256"],
                **(
                    {
                        "run_plan_id": run_plan_id,
                        "run_plan_content_sha256": run_plan_ref["content_sha256"],
                        "provider_review_requested": execute_provider_review,
                    }
                    if run_plan_ref is not None
                    else {}
                ),
            },
        }
    run_id = _allocate_run_id(resolved.path, uuid_factory)
    run_path = resolved.path / "runs" / run_id
    run_path.mkdir(parents=True, mode=0o700)
    provider_lease: int | None = None
    try:
        if execute_provider_review and selected_plan is not None:
            provider_lease = _acquire_provider_review_lease(resolved, run_id)
            if provider_lease is None:
                raise RunError("provider review already has an active caller")
        prepared = _prepare_records(
            resolved=resolved,
            run_id=run_id,
            gig_version=authority["version"],
            authority_commit=authority["commit"],
            graph=graph,
            proposal=proposal,
            graph_authority=(authority if selected_descriptor is not None else None),
            selected_descriptor=selected_descriptor,
            target_before=target_before,
            invocation_argv=invocation_argv,
            operator_consent=redeemed_consent,
            run_plan_ref=run_plan_ref,
            proposal_request_bytes=proposal_request_bytes,
            operation_request_kind=(
                "find-jobs"
                if find_jobs_execution is not None
                else (
                    "tailor"
                    if tailor_execution is not None
                    else ("interview" if interview_execution is not None else "proposal")
                )
            ),
            find_jobs_execution=find_jobs_execution,
        )
        observer("after_brief_write")
        observer("after_manifest_seal")
        observer("after_initial_run_details")
        manifest_digest = digest_imported_bytes(
            prepared[f"runs/{run_id}/run-manifest.json"]
        )
        started = record_transition(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            handoff_id=_new_id(EntityPrefix.HANDOFF, uuid_factory),
            transition="run_started",
            body=f"Run {run_id} sealed and ready for deterministic execution.",
            artifacts=tuple(
                JournalArtifact(path, data) for path, data in prepared.items()
            ),
            front_matter={
                "gig_version": authority["version"],
                "run_id": run_id,
                "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)),
                "source_manifest_sha256": manifest_digest,
                "outcome": "SEALED",
                "actor": {"kind": "operator", "id": "local-user"},
            },
            observer=observer,
        )
        observer("after_run_started_commit")
        if interview_execution is not None:
            return _execute_interview_run(
                resolved=resolved, authority=authority, graph=graph,
                run_id=run_id, run_path=run_path, started=started,
                manifest_digest=manifest_digest, request_bytes=proposal_request_bytes,
                home_root=home_root, requested_target=requested_target,
                uuid_factory=uuid_factory,
            )
        if proposal_execution is not None:
            goal_id = _proposal_entry_goal(graph)
            if proposal_request_bytes is None:
                raise RunError("proposal_run_input_invalid: sealed request is unavailable")
            sealed_proposal_request = parse_json_bytes(proposal_request_bytes)
            if (
                not isinstance(sealed_proposal_request, Mapping)
                or sealed_proposal_request.get("kind") != "scout_proposal_run_request"
                or sealed_proposal_request.get("graph_selector") != "proposal-assessment"
                or not isinstance(sealed_proposal_request.get("posting_selector"), Mapping)
                or not isinstance(sealed_proposal_request.get("private_selectors"), list)
            ):
                raise RunError("proposal_run_input_invalid: sealed request is malformed")
            proposal_started = _mark_proposal_goal_started(
                resolved=resolved,
                run_id=run_id,
                gig_version=authority["version"],
                graph=graph,
                manifest_digest=manifest_digest,
                parent_handoff_id=started.handoff_id,
            )
            try:
                from .scout.proposal_execution import execute_local_proposal

                proposal_result = execute_local_proposal(
                    resolved=resolved,
                    config=proposal_config,
                    run_id=run_id,
                    goal_id=goal_id,
                    model_target=str(sealed_proposal_request["model_target"]),
                    posting_selector=dict(sealed_proposal_request["posting_selector"]),
                    private_selectors=tuple(
                        dict(item) for item in sealed_proposal_request["private_selectors"]
                    ),
                    local_allowed=bool(sealed_proposal_request["local_allowed"]),
                    budget=proposal_budget,
                    uuid_factory=uuid_factory,
                )
                # The invocation result is authoritative evidence even when
                # the domain assessment is invalid.  In that case the
                # proposal execution already published exactly one goal_failed
                # transition; never ask the immutable proposal-record writer
                # to publish a successful revision from that failed result.
                if proposal_result.result.get("status") == "complete":
                    # Persist the immutable host-owned proposal revision while
                    # the exact sealed selectors/configuration are in scope.
                    # This association is separate from Tailor and is never
                    # derived from model-returned lineage.
                    from .scout.proposal_records import record_proposal_revision

                    target = resolve_model_target(
                        proposal_config, str(sealed_proposal_request["model_target"])
                    )
                    configured_digest = _canonical_model_digest(target.target.model_digest)
                    if configured_digest is None:
                        raise RunError(
                            "proposal_run_target_invalid: configured local model identity is incomplete"
                        )
                    record_proposal_revision(
                        resolved=resolved,
                        execution=proposal_result,
                        posting_selector=dict(sealed_proposal_request["posting_selector"]),
                        private_selectors=tuple(
                            dict(item) for item in sealed_proposal_request["private_selectors"]
                        ),
                        model_target=str(sealed_proposal_request["model_target"]),
                        configured_digest=configured_digest,
                        uuid_factory=uuid_factory,
                    )
            except BaseException as exc:
                if isinstance(exc, Exception):
                    failed = _mark_proposal_goal_failed(
                        resolved=resolved,
                        run_id=run_id,
                        gig_version=authority["version"],
                        graph=graph,
                        manifest_digest=manifest_digest,
                        parent_handoff_id=proposal_started.handoff_id,
                    )
                    details = parse_json_bytes(
                        (run_path / "run-details.json").read_bytes()
                    )
                    if failed is None:
                        # A failed domain result owns its Goal terminalization,
                        # so a later local publication error is not a competing
                        # writer.  Finish the owning Run once, but preserve a
                        # genuinely competing terminal Run outcome.
                        if not isinstance(details, Mapping) or details.get("status") not in {
                            "succeeded", "failed", "blocked", "cancelled", "interrupted"
                        }:
                            goal_terminal = _latest_goal_terminal_entry(resolved, run_id, goal_id)
                            if goal_terminal is None:
                                raise RunError("proposal_run_authority_refused: competing terminal state is unavailable")
                            try:
                                terminal = _finish_run(
                                    resolved,
                                    run_id,
                                    authority["version"],
                                    graph,
                                    dict(details),
                                    "failed",
                                    goal_terminal.handoff_id,
                                    goal_terminal.handoff_id,
                                )
                            except (_RunInterrupted, JournalConflictError):
                                terminal = _recover_proposal_run_terminal(
                                    resolved=resolved,
                                    run_id=run_id,
                                    gig_version=authority["version"],
                                    graph=graph,
                                    parent_handoff_id=goal_terminal.handoff_id,
                                    status="interrupted",
                                )
                                return RunResult(
                                    run_id, resolved.gig_id, authority["version"], resolved.path,
                                    run_path, "interrupted", started, terminal,
                                )
                            return RunResult(
                                run_id, resolved.gig_id, authority["version"], resolved.path,
                                run_path, "failed", started, terminal,
                            )
                        status = str(details["status"])
                        return RunResult(
                            run_id, resolved.gig_id, authority["version"], resolved.path,
                            run_path, status, started, _latest_terminal_entry(resolved, run_id),
                        )
                    final_status = "failed"
                    try:
                        terminal = _finish_run(
                            resolved,
                            run_id,
                            authority["version"],
                            graph,
                            details,
                            "failed",
                            failed.handoff_id,
                            proposal_started.handoff_id,
                        )
                    except (_RunInterrupted, JournalConflictError):
                        final_status = "interrupted"
                        terminal = _recover_proposal_run_terminal(
                            resolved=resolved,
                            run_id=run_id,
                            gig_version=authority["version"],
                            graph=graph,
                            parent_handoff_id=failed.handoff_id,
                            status="interrupted",
                        )
                    return RunResult(
                        run_id,
                        resolved.gig_id,
                        authority["version"],
                        resolved.path,
                        run_path,
                        final_status,
                        started,
                        terminal,
                    )
                raise
            details = parse_json_bytes((run_path / "run-details.json").read_bytes())
            terminal_status = (
                "succeeded" if proposal_result.result.get("status") == "complete" else "failed"
            )
            final_status = terminal_status
            try:
                terminal = _finish_run(
                    resolved,
                    run_id,
                    authority["version"],
                    graph,
                    details,
                    terminal_status,
                    proposal_result.result_entry.handoff_id,
                    proposal_result.result_entry.handoff_id,
                )
            except (_RunInterrupted, JournalConflictError):
                final_status = "interrupted"
                terminal = _recover_proposal_run_terminal(
                    resolved=resolved,
                    run_id=run_id,
                    gig_version=authority["version"],
                    graph=graph,
                    parent_handoff_id=proposal_result.result_entry.handoff_id,
                    status="interrupted",
                )
            return RunResult(
                run_id,
                resolved.gig_id,
                authority["version"],
                resolved.path,
                run_path,
                final_status,
                started,
                terminal,
            )
        if tailor_execution is not None:
            return _execute_tailor_run(
                resolved=resolved,
                authority=authority,
                graph=graph,
                run_id=run_id,
                run_path=run_path,
                started=started,
                manifest_digest=manifest_digest,
                request_bytes=proposal_request_bytes,
                config=proposal_config,
                budget=proposal_budget,
                uuid_factory=uuid_factory,
            )
        if selected_plan is not None:
            review_bridge = _materialize_run_review_bridge(
                resolved=resolved,
                run_id=run_id,
                plan=selected_plan.plan,
                plan_id=run_plan_id,
                plan_digest=run_plan_ref["content_sha256"] if run_plan_ref else None,
                gig_version=authority["version"],
                manifest_digest=manifest_digest,
                parent_handoff_id=started.handoff_id,
                observer=observer,
            )
            if execute_provider_review:
                from .provider_review import (
                    execute_provider_review as _execute_provider_review,
                )

                review_started = _mark_provider_review_running(
                    resolved=resolved,
                    run_id=run_id,
                    gig_version=authority["version"],
                    graph=graph,
                    manifest_digest=manifest_digest,
                    parent_handoff_id=review_bridge.handoff_id,
                )
                try:
                    provider_result = _execute_provider_review(
                        home_root=home_root,
                        requested_target=requested_target,
                        gig_id=resolved.gig_id,
                        run_id=run_id,
                        run_plan_id=run_plan_id,
                    )
                except BaseException as exc:
                    terminal_status = _provider_review_terminal_status(
                        resolved,
                        run_id,
                        "failed" if isinstance(exc, Exception) else "interrupted",
                    )
                    terminal = _finish_provider_review(
                        resolved=resolved,
                        run_id=run_id,
                        gig_version=authority["version"],
                        graph=graph,
                        parent_handoff_id=review_started.handoff_id,
                        run_plan_id=run_plan_id,
                        status=terminal_status,
                        error=_provider_review_error(exc),
                    )
                    if isinstance(exc, Exception):
                        return RunResult(
                            run_id,
                            resolved.gig_id,
                            authority["version"],
                            resolved.path,
                            run_path,
                            terminal_status,
                            started,
                            terminal,
                        )
                    raise
                terminal_status = _provider_review_terminal_status(
                    resolved,
                    run_id,
                    "succeeded" if provider_result.status == "complete" else "blocked",
                )
                terminal = _finish_provider_review(
                    resolved=resolved,
                    run_id=run_id,
                    gig_version=authority["version"],
                    graph=graph,
                    parent_handoff_id=review_started.handoff_id,
                    run_plan_id=run_plan_id,
                    status=terminal_status,
                )
                return RunResult(
                    run_id,
                    resolved.gig_id,
                    authority["version"],
                    resolved.path,
                    run_path,
                    terminal_status,
                    started,
                    terminal,
                )
        process = multiprocessing.get_context("spawn").Process(
            target=_worker_entry,
            args=(
                resolved,
                run_id,
                authority["version"],
                graph,
                target_before,
                started.handoff_id,
                manifest_digest,
            ),
            daemon=False,
        )
        process.start()
        if not wait:
            return RunResult(
                run_id,
                resolved.gig_id,
                authority["version"],
                resolved.path,
                run_path,
                "running",
                started,
                None,
            )
        process.join()
        if process.exitcode != 0:
            terminal = _mark_interrupted(resolved, run_id, authority["version"], graph)
            return RunResult(
                run_id,
                resolved.gig_id,
                authority["version"],
                resolved.path,
                run_path,
                "interrupted",
                started,
                terminal,
            )
        details = parse_json_bytes((run_path / "run-details.json").read_bytes())
        if isinstance(details, dict) and details.get("status") in {
            "failed",
            "blocked",
            "cancelled",
            "interrupted",
        }:
            return RunResult(
                run_id,
                resolved.gig_id,
                authority["version"],
                resolved.path,
                run_path,
                str(details["status"]),
                started,
                _latest_terminal_entry(resolved, run_id),
            )
        return RunResult(
            run_id,
            resolved.gig_id,
            authority["version"],
            resolved.path,
            run_path,
            "succeeded",
            started,
            None,
        )
    except Exception:
        if provider_lease is not None:
            os.close(provider_lease)
            provider_lease = None
            if (run_path / "run-details.json").is_file():
                _recover_abandoned_provider_review(resolved, run_id)
        # A failed preparation must not leave an apparently addressable Run.
        started_files = tuple((resolved.path / "handoffs").glob("*-run-started.txt"))
        if run_path.exists() and not started_files:
            _remove_tree(run_path)
        raise
    finally:
        if provider_lease is not None:
            os.close(provider_lease)


def launch_find_jobs_run(
    *,
    home_root: Path,
    target: Path | None,
    run_request: RunRequest,
    config_bytes: bytes,
    ui_loopback_verified: bool,
) -> str:
    """Seal and launch one local find-jobs Run from the API boundary.

    The API has already parsed the request and performed its peer check, but
    this boundary repeats the configuration identity check before allocating a
    Run ID.  The worker receives only the canonical DTO snapshot and the exact
    pinned resume triple; it never reloads the mutable target configuration or
    selects a newer resume at execution time.
    """

    if not isinstance(run_request, RunRequest):
        raise RunError("find_jobs_run_input_invalid: run_request is not a RunRequest")
    if type(config_bytes) is not bytes:
        raise RunError("find_jobs_config_invalid: config_bytes must be bytes")
    try:
        config = FindJobsConfig.from_json(parse_json_bytes(config_bytes))
    except Exception as exc:
        raise RunError("find_jobs_config_invalid: config snapshot is invalid") from exc
    config_digest = config.digest()
    if config_digest != run_request.config_digest:
        raise RunError("find_jobs_config_digest_mismatch: config digest does not match run request")
    canonical_config_bytes = canonical_json_bytes(config.to_json())
    try:
        pinned_resume = resolve_newest_resume(home_root, target)
    except RunError:
        raise
    sealed_input = FindJobsRunInput(
        config=config,
        config_digest=config_digest,
        selection_cap=run_request.selection_cap,
        selection_rule=run_request.selection_rule,
        model_target=run_request.model_target,
        pinned_resume=pinned_resume,
    )
    execution = _FindJobsRunExecution(
        request=run_request,
        config=config,
        config_bytes=canonical_config_bytes,
        pinned_resume=pinned_resume,
        input_bytes=canonical_json_bytes(sealed_input.to_json()),
    )
    result = launch_run(
        home_root=home_root,
        requested_target=target,
        invocation_argv=("gigai", "find-jobs", "run"),
        operator_consent=run_request.consent.to_json(),
        find_jobs_execution=execution,
        ui_loopback_verified=ui_loopback_verified,
        wait=False,
    )
    return result.run_id


def read_run_details(
    *,
    home_root: Path,
    requested_target: Path | None,
    run_id: str,
    gig_id: str | None = None,
) -> dict[str, object]:
    """Read only the durable terminal or preparation state for one Run."""

    resolved = resolve_workpad(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    try:
        validate_entity_id(run_id, expected_prefix=EntityPrefix.RUN)
    except ValueError as exc:
        raise RunError("run_id must be canonical") from exc
    path = resolved.path / "runs" / run_id / "run-details.json"
    _reject_symlinked_components(resolved.path, path, "Run details path is unsafe")
    if path.is_symlink() or not path.is_file():
        external = resolved.path / "runs" / run_id / "external-run.json"
        if external.is_file() and not external.is_symlink():
            raise RunError(
                "external_run_family_refused: external recording Runs must be read through gigai external inspect"
            )
        raise RunError("Run details are unavailable")
    payload = _read_committed_run_details(resolved, path)
    if not isinstance(payload, dict):
        raise RunError("Run details are not an object")
    if payload.get("status") in {"preparing", "running"} and _provider_review_active(
        resolved, run_id, payload
    ):
        _recover_abandoned_provider_review(resolved, run_id)
        payload = _read_committed_run_details(resolved, path)
    elif payload.get("status") in {"preparing", "running"}:
        target_ref = payload.get("target_before")
        if isinstance(target_ref, dict):
            try:
                if _target_observation(resolved) != _read_artifact_json(
                    resolved.path, target_ref
                ):
                    graph_payload = parse_json_bytes(
                        (
                            resolved.path / "runs" / run_id / "goal-graph.json"
                        ).read_bytes()
                    )
                    if isinstance(graph_payload, dict):
                        _mark_interrupted(
                            resolved,
                            run_id,
                            int(payload["gig_version"]),
                            graph_payload,
                        )
                        payload = _read_committed_run_details(resolved, path)
            except (OSError, ValueError, RunError):
                pass
    report = validate_serialized_contract(
        "run-details.schema.json", canonical_json_bytes(payload)
    )
    if not report.valid:
        raise RunError("Run details failed schema validation")
    return payload


def _read_committed_run_details(resolved: ResolvedWorkpad, path: Path) -> object:
    """Never expose artifact replacement as an already committed Run outcome.

    Journal publication replaces files before commit. An interrupted writer
    must be explicitly reconciled through the existing journal workflow; status
    reads do not adopt its transaction or claim its uncommitted terminal state.
    A concurrent live writer can also cause this transient, retryable refusal.
    """
    data = path.read_bytes()
    relative = path.relative_to(resolved.path).as_posix()
    try:
        committed = _git_bytes(resolved.path, "show", f"HEAD:{relative}")
    except subprocess.CalledProcessError as exc:
        raise RunError(
            "run_details_reconciliation_required: Run details are not committed"
        ) from exc
    if data != committed:
        raise RunError(
            "run_details_reconciliation_required: Run details differ from the journal; retry if a writer is active, otherwise reconcile the journal"
        )
    return parse_json_bytes(committed)


def _provider_review_active(
    resolved: ResolvedWorkpad, run_id: str, details: Mapping[str, object]
) -> bool:
    """Leave an active provider review to its own terminalizer.

    The ordinary detached-worker reconciliation marks Graph Goals failed.  That
    is not truthful while this synchronous provider lifecycle is active because
    none of those Goals was ever scheduled.
    """

    if (
        details.get("status") not in {"preparing", "running"}
        or details.get("run_id") != run_id
    ):
        return False
    run_root = resolved.path / "runs" / run_id
    manifest_path = run_root / "run-manifest.json"
    consent_path = run_root / "operator-consent.json"
    for path in (manifest_path, consent_path):
        try:
            _reject_symlinked_components(
                resolved.path, path, "provider review path is unsafe"
            )
        except RunError:
            return False
        if path.is_symlink() or not path.is_file():
            return False
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = parse_json_bytes(manifest_bytes)
        consent_bytes = consent_path.read_bytes()
        consent = parse_json_bytes(consent_bytes)
    except (OSError, ValueError):
        return False
    scope = consent.get("scope") if isinstance(consent, Mapping) else None
    return bool(
        isinstance(manifest, Mapping)
        and (
            validate_serialized_contract(
                "run-manifest.schema.json", manifest_bytes
            ).valid
            or validate_serialized_contract(
                "run-manifest-v2.schema.json", manifest_bytes
            ).valid
        )
        and manifest.get("run_id") == run_id
        and manifest.get("gig_id") == resolved.gig_id
        and any(
            isinstance(ref, Mapping)
            and ref.get("path") == f"runs/{run_id}/operator-consent.json"
            and ref.get("content_sha256") == digest_imported_bytes(consent_bytes)
            for ref in manifest.get("sealed_sources", [])
        )
        and isinstance(scope, Mapping)
        and scope.get("gig_id") == resolved.gig_id
        and scope.get("project_id") == resolved.project_id
        and scope.get("provider_review_requested") is True
    )


def _validate_proposal_run_entry(
    *,
    resolved: ResolvedWorkpad,
    authority: Mapping[str, object],
    graph: Mapping[str, object],
    selected_descriptor: Mapping[str, object] | None,
    request: ProposalRunRequest,
    config: object,
) -> bytes:
    """Validate the closed proposal entry before allocating a Run directory."""

    if request.graph_selector != "proposal-assessment":
        raise RunError(
            "proposal_run_authority_refused: only the approved proposal-assessment entry is supported"
        )
    if (
        selected_descriptor is None
        or selected_descriptor.get("graph_id") != "proposal-assessment"
        or not isinstance(authority.get("graph_set"), Mapping)
    ):
        raise RunError(
            "proposal_run_authority_refused: proposal requires an approved selected Graph Set member"
        )
    goals = graph.get("goals")
    entries = graph.get("entry_goal_ids")
    terminal_ids = graph.get("terminal_goal_ids")
    evidence = graph.get("required_completion_evidence")
    if (
        not isinstance(goals, list)
        or len(goals) != 1
        or not isinstance(entries, list)
        or len(entries) != 1
        or not isinstance(terminal_ids, list)
        or terminal_ids != entries
        or not isinstance(evidence, list)
        or len(evidence) != 1
        or evidence != ["proposal-assessment-completion"]
    ):
        raise RunError(
            "proposal_run_authority_refused: proposal Graph must have one exact entry and terminal Goal"
        )
    entry_id = entries[0]
    entry = next(
        (item for item in goals if isinstance(item, Mapping) and item.get("goal_id") == entry_id),
        None,
    )
    if (
        not isinstance(entry, Mapping)
        or entry.get("slug") != "proposal-assessment"
        or entry.get("required") is not True
        or entry.get("effects") != ["write_workpad"]
        or entry.get("activation") != "automatic"
        or entry.get("tools") != []
        or not isinstance(entry.get("executor"), Mapping)
        or entry["executor"].get("kind") != "local_capability"
        or entry["executor"].get("capability") != "gigai.offline"
    ):
        raise RunError(
            "proposal_run_effect_refused: selected proposal Goal lacks write_workpad permission"
        )
    if not isinstance(request.model_target, str) or not request.model_target:
        raise RunError("proposal_run_target_invalid: local model target is required")
    try:
        target = resolve_model_target(config, request.model_target)
    except Exception as exc:
        raise RunError("proposal_run_target_invalid: configured local target is unavailable") from exc
    canonical_digest = _canonical_model_digest(target.target.model_digest)
    if target.endpoint.adapter != "ollama_local" or canonical_digest is None:
        raise RunError(
            "proposal_run_target_invalid: proposal entry requires an identified local Ollama target"
        )
    if type(request.local_allowed) is not bool or not request.local_allowed:
        raise RunError("proposal_run_permission_refused: local runtime permission is required")
    if not isinstance(request.posting_selector, Mapping) or set(request.posting_selector) != {
        "family", "run_id", "receipt_id", "output_kind", "opportunity_id", "snapshot_id"
    } or request.posting_selector.get("family") != "scout_discovery" or not isinstance(
        request.private_selectors, tuple
    ) or not request.private_selectors:
        raise RunError("proposal_run_input_invalid: explicit posting and private selectors are required")
    for item in request.private_selectors:
        if not isinstance(item, Mapping) or set(item) != {"purpose", "selector"}:
            raise RunError("proposal_run_input_invalid: private selectors are malformed")
        if item.get("purpose") not in {"preferences", "experience", "answer"} or not isinstance(item.get("selector"), Mapping):
            raise RunError("proposal_run_input_invalid: private selector purpose is invalid")
    try:
        payload = {
            "schema_version": "1.0",
            "kind": "scout_proposal_run_request",
            "graph_selector": request.graph_selector,
            "selected_graph_id": selected_descriptor.get("graph_id"),
            "selected_graph_sha256": selected_descriptor.get("goal_graph", {}).get("content_sha256")
            if isinstance(selected_descriptor.get("goal_graph"), Mapping)
            else None,
            "gig_version": authority.get("version"),
            "model_target": request.model_target,
            "endpoint": target.endpoint.name,
            "endpoint_base_url": target.endpoint.base_url,
            "adapter": target.endpoint.adapter,
            "model": target.target.model,
            "model_digest": canonical_digest,
            "target_bounds": {
                "context_tokens": target.target.context_tokens or 4_096,
                "max_output_tokens": target.target.max_output_tokens,
                "max_response_bytes": target.target.max_response_bytes or 1 * 1024 * 1024,
            },
            "local_allowed": True,
            "posting_selector": dict(request.posting_selector),
            "private_selectors": [dict(item) for item in request.private_selectors],
        }
        raw = canonical_json_bytes(payload)
    except Exception as exc:
        raise RunError("proposal_run_input_invalid: selectors are not canonical JSON") from exc
    if payload["selected_graph_sha256"] is None:
        raise RunError("proposal_run_authority_refused: selected Graph digest is unavailable")
    return raw


def _validate_tailor_run_entry(
    *,
    resolved: ResolvedWorkpad,
    authority: Mapping[str, object],
    graph: Mapping[str, object],
    selected_descriptor: Mapping[str, object] | None,
    request: TailorRunRequest | None,
    config: object,
) -> bytes:
    """Validate the explicit Tailor operation before allocating a Run."""
    if request is None or request.graph_selector != "tailor-application":
        raise RunError("tailor_run_authority_refused: tailor-application is required")
    if (
        selected_descriptor is None
        or selected_descriptor.get("graph_id") != "tailor-application"
        or not isinstance(authority.get("graph_set"), Mapping)
    ):
        raise RunError("tailor_run_authority_refused: selected Tailor Graph is not approved")
    goals, entries, terminals = graph.get("goals"), graph.get("entry_goal_ids"), graph.get("terminal_goal_ids")
    if (
        not isinstance(goals, list) or len(goals) != 1
        or not isinstance(entries, list) or len(entries) != 1
        or not isinstance(terminals, list) or terminals != entries
        or graph.get("required_completion_evidence") != ["tailoring-completion"]
    ):
        raise RunError("tailor_run_authority_refused: Tailor Graph must have one terminal entry Goal")
    entry = next((item for item in goals if isinstance(item, Mapping) and item.get("goal_id") == entries[0]), None)
    executor = entry.get("executor") if isinstance(entry, Mapping) else None
    if (
        not isinstance(entry, Mapping) or entry.get("slug") != "tailor-application"
        or entry.get("required") is not True or entry.get("effects") != ["write_workpad"]
        or entry.get("tools") != [] or not isinstance(executor, Mapping)
        or executor.get("kind") != "local_capability" or executor.get("capability") != "gigai.offline"
    ):
        raise RunError("tailor_run_effect_refused: selected Tailor Goal lacks the required local effect")
    if not isinstance(request.model_target, str) or not request.model_target:
        raise RunError("tailor_run_target_invalid: local model target is required")
    try:
        target = resolve_model_target(config, request.model_target)
    except Exception as exc:
        raise RunError("tailor_run_target_invalid: configured local target is unavailable") from exc
    digest = _canonical_model_digest(target.target.model_digest)
    if target.endpoint.adapter != "ollama_local" or digest is None:
        raise RunError("tailor_run_target_invalid: Tailor requires an identified local Ollama target")
    if type(request.local_allowed) is not bool or not request.local_allowed:
        raise RunError("tailor_run_permission_refused: explicit local permission is required")
    outputs = request.requested_outputs
    if type(outputs) is not tuple or not outputs or len(outputs) > 2 or any(item not in {"resume", "cover_letter"} for item in outputs) or len(set(outputs)) != len(outputs):
        raise RunError("tailor_run_input_invalid: requested document outputs are invalid")
    if not isinstance(request.posting_selector, Mapping) or request.posting_selector.get("family") != "scout_discovery":
        raise RunError("tailor_run_input_invalid: an authenticated discovery posting is required")
    selectors = (*request.private_selectors, *(
        {"purpose": "answer", "selector": item} for item in request.answer_selectors
    ))
    if not selectors:
        raise RunError("tailor_run_input_invalid: explicit candidate evidence is required")
    for item in selectors:
        if not isinstance(item, Mapping) or set(item) != {"purpose", "selector"} or item.get("purpose") not in {"preferences", "experience", "answer"} or not isinstance(item.get("selector"), Mapping):
            raise RunError("tailor_run_input_invalid: private source selector is malformed")
    proposal = request.proposal_selector
    if proposal is not None and (not isinstance(proposal, Mapping) or set(proposal) != {"record_id", "revision_id"}):
        raise RunError("tailor_run_input_invalid: saved proposal selector is malformed")
    payload = {
        "schema_version": "1.0", "kind": "scout_tailor_run_request",
        "graph_selector": request.graph_selector,
        "selected_graph_id": selected_descriptor.get("graph_id"),
        "selected_graph_sha256": selected_descriptor.get("goal_graph", {}).get("content_sha256") if isinstance(selected_descriptor.get("goal_graph"), Mapping) else None,
        "gig_version": authority.get("version"), "model_target": request.model_target,
        "endpoint": target.endpoint.name, "endpoint_base_url": target.endpoint.base_url,
        "adapter": target.endpoint.adapter, "model": target.target.model,
        "model_digest": digest, "target_bounds": {
            "context_tokens": target.target.context_tokens or 4_096,
            "max_output_tokens": target.target.max_output_tokens,
            "max_response_bytes": target.target.max_response_bytes or 1 * 1024 * 1024,
        }, "local_allowed": True, "requested_outputs": list(outputs),
        "posting_selector": dict(request.posting_selector),
        "private_selectors": [dict(item) for item in request.private_selectors],
        "answer_selectors": [dict(item) for item in request.answer_selectors],
        "proposal_selector": None if proposal is None else dict(proposal),
    }
    if payload["selected_graph_sha256"] is None:
        raise RunError("tailor_run_authority_refused: selected Tailor Graph digest is unavailable")
    return canonical_json_bytes(payload)


def _validate_interview_run_entry(
    *,
    authority: Mapping[str, object],
    graph: Mapping[str, object],
    selected_descriptor: Mapping[str, object] | None,
    request: InterviewRunRequest,
    resolved: ResolvedWorkpad,
) -> bytes:
    """Seal the actual selected prepare-interview Graph inputs before Run allocation."""
    if request.graph_selector != "prepare-interview":
        raise RunError("interview_run_authority_refused: prepare-interview is required")
    if (
        selected_descriptor is None
        or selected_descriptor.get("graph_id") != "prepare-interview"
        or not isinstance(authority.get("graph_set"), Mapping)
    ):
        raise RunError("interview_run_authority_refused: selected interview Graph is not approved")
    goals, entries, terminals = graph.get("goals"), graph.get("entry_goal_ids"), graph.get("terminal_goal_ids")
    if not isinstance(goals, list) or len(goals) != 1 or not isinstance(entries, list) or len(entries) != 1 or not isinstance(terminals, list) or terminals != entries:
        raise RunError("interview_run_authority_refused: interview Graph must have one terminal entry Goal")
    entry = goals[0]
    executor = entry.get("executor") if isinstance(entry, Mapping) else None
    if (
        not isinstance(entry, Mapping) or entry.get("slug") != "prepare-interview"
        or entry.get("effects") != ["write_workpad"] or entry.get("activation") != "automatic"
        or entry.get("tools") != [] or not isinstance(executor, Mapping)
        or executor.get("kind") != "local_capability" or executor.get("capability") not in {"gigai.offline", "gigai.deterministic"}
    ):
        raise RunError("interview_run_effect_refused: selected interview Goal lacks the required local effect")
    if not isinstance(request.selection, Mapping) or not isinstance(request.content, Mapping):
        raise RunError("interview_run_input_invalid: selection and content must be objects")
    if not isinstance(request.operation_key, str) or not request.operation_key.strip() or len(request.operation_key) > 160:
        raise RunError("interview_run_input_invalid: operation key is invalid")
    try:
        return canonical_json_bytes({
            "schema_version": "1.0",
            "kind": "scout_interview_run_request",
            "graph_selector": request.graph_selector,
            "selected_graph_id": selected_descriptor.get("graph_id"),
            "selected_graph_sha256": selected_descriptor.get("goal_graph", {}).get("content_sha256")
            if isinstance(selected_descriptor.get("goal_graph"), Mapping) else None,
            "gig_version": authority.get("version"),
            "operation_key": request.operation_key,
            "selection": dict(request.selection),
            "content": dict(request.content),
        })
    except Exception as exc:
        raise RunError("interview_run_input_invalid: selection is not canonical JSON") from exc


def _execute_interview_run(
    *,
    resolved: ResolvedWorkpad,
    authority: Mapping[str, object],
    graph: dict[str, object],
    run_id: str,
    run_path: Path,
    started: JournalEntry,
    manifest_digest: str,
    request_bytes: bytes | None,
    home_root: Path,
    requested_target: Path | None,
    uuid_factory: Callable[[], uuid.UUID],
) -> RunResult:
    """Execute prepare-interview through the selected Graph-owned Run."""
    if request_bytes is None:
        raise RunError("interview_run_input_invalid: sealed request is unavailable")
    sealed = parse_json_bytes(request_bytes)
    if not isinstance(sealed, Mapping) or sealed.get("kind") != "scout_interview_run_request":
        raise RunError("interview_run_input_invalid: sealed request is malformed")
    goal_id = _proposal_entry_goal(graph)
    started_goal = _mark_proposal_goal_started(
        resolved=resolved, run_id=run_id, gig_version=authority["version"],
        graph=graph, manifest_digest=manifest_digest, parent_handoff_id=started.handoff_id,
    )
    try:
        from .scout.interview_records import prepare_interview
        result = prepare_interview(
            home_root=home_root,
            requested_target=requested_target,
            gig_id=resolved.gig_id,
            selection=dict(sealed["selection"]),
            content=dict(sealed["content"]),
            operation_key=str(sealed["operation_key"]),
            uuid_factory=uuid_factory,
        )
        payload = {
            "schema_version": "1.0", "kind": "scout_interview_run_result",
            "status": "complete", "run_id": run_id, "goal_id": goal_id,
            "record_id": result.record_id, "revision_id": result.revision_id,
        }
        result_entry = _publish_interview_result(
            resolved=resolved, run_id=run_id, goal_id=goal_id, graph=graph,
            gig_version=authority["version"], manifest_digest=manifest_digest,
            result=payload, parent_handoff_id=started_goal.handoff_id,
            uuid_factory=uuid_factory,
        )
        details = parse_json_bytes((run_path / "run-details.json").read_bytes())
        terminal = _finish_run(resolved, run_id, authority["version"], graph, details, "succeeded", result_entry.handoff_id, result_entry.handoff_id)
        return RunResult(run_id, resolved.gig_id, authority["version"], resolved.path, run_path, "succeeded", started, terminal)
    except Exception:
        failed = _mark_proposal_goal_failed(
            resolved=resolved, run_id=run_id, gig_version=authority["version"],
            graph=graph, manifest_digest=manifest_digest, parent_handoff_id=started_goal.handoff_id,
        )
        if failed is None:
            raise
        details = parse_json_bytes((run_path / "run-details.json").read_bytes())
        terminal = _finish_run(resolved, run_id, authority["version"], graph, details, "failed", failed.handoff_id, started_goal.handoff_id)
        return RunResult(run_id, resolved.gig_id, authority["version"], resolved.path, run_path, "failed", started, terminal)


def _publish_interview_result(
    *, resolved: ResolvedWorkpad, run_id: str, goal_id: str, graph: Mapping[str, object],
    gig_version: int, manifest_digest: str, result: Mapping[str, object],
    parent_handoff_id: str, uuid_factory: Callable[[], uuid.UUID],
) -> JournalEntry:
    result_data = canonical_json_bytes(dict(result))
    result_path = f"runs/{run_id}/scout-interview/result.json"
    def operation(writer: JournalWriter) -> JournalEntry:
        details_bytes, graph_bytes = _committed_run_bytes(writer.root, run_id)
        _validate_active_goal_bytes(resolved, (details_bytes, graph_bytes), run_id, goal_id)
        details = parse_json_bytes(details_bytes)
        if not isinstance(details, dict):
            raise RunError("interview_result_publication_refused: Run details are unavailable")
        detail = next((item for item in details.get("goals", []) if isinstance(item, dict) and item.get("goal_id") == goal_id), None)
        if not isinstance(detail, dict):
            raise RunError("interview_result_publication_refused: interview Goal is unavailable")
        evidence = _artifact_ref(result_path, "application/json", result_data)
        detail.update({"status": "complete", "outcome": "COMPLETE", "finished_at": _now(), "evidence": [evidence], "errors": []})
        _refresh_details(details, {item["goal_id"]: item for item in details["goals"]}, dict(graph), "running")
        details_data = canonical_json_bytes(details)
        return writer.record(JournalTransition(
            _new_id(EntityPrefix.HANDOFF, uuid_factory), "goal_completed",
            "Scout interview preparation Goal completed with a journal-backed record.",
            (JournalArtifact(result_path, result_data), JournalArtifact(f"runs/{run_id}/run-details.json", details_data)),
            _goal_front_matter(gig_version, run_id, next(item for item in graph["goals"] if item.get("goal_id") == goal_id), digest_imported_bytes(graph_bytes), manifest_digest, parent_handoff_id, "COMPLETE", [evidence]),
        ), allow_artifact_replacement=True)
    from .scout.proposal_execution import _committed_run_bytes, _validate_active_goal_bytes
    return run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=operation)


def _execute_tailor_run(
    *, resolved: ResolvedWorkpad, authority: Mapping[str, object], graph: dict[str, object],
    run_id: str, run_path: Path, started: JournalEntry, manifest_digest: str,
    request_bytes: bytes | None, config: object | None, budget: object | None,
    uuid_factory: Callable[[], uuid.UUID],
) -> RunResult:
    """Run one authenticated local Tailor operation and persist its documents."""
    if request_bytes is None or config is None:
        raise RunError("tailor_run_input_invalid: sealed Tailor request is unavailable")
    sealed = parse_json_bytes(request_bytes)
    if not isinstance(sealed, Mapping) or sealed.get("kind") != "scout_tailor_run_request":
        raise RunError("tailor_run_input_invalid: sealed Tailor request is malformed")
    goal_id = _proposal_entry_goal(graph)
    started_goal = _mark_proposal_goal_started(resolved=resolved, run_id=run_id, gig_version=authority["version"], graph=graph, manifest_digest=manifest_digest, parent_handoff_id=started.handoff_id)
    holder: dict[str, object] = {}
    try:
        from .scout.proposal_execution import _resolve_sources
        from .scout.tailor_selection import TailorAnswer, TailorProposal, TailorSelection, TailorSource, build_tailoring_request
        from .scout.tailor_execution import execute_tailor
        from .scout.document_records import record_document_revision
        from .scout.documents import prepare_document_revision
        from .scout.tailoring import decode_tailoring_bundle
        from .adapters.port import InvocationResult

        private_selectors = tuple(dict(item) for item in sealed.get("private_selectors", []))
        private_selectors += tuple({"purpose": "answer", "selector": item} for item in sealed.get("answer_selectors", []))
        posting, private, sealed_head = _resolve_sources(resolved, dict(sealed["posting_selector"]), private_selectors)
        def _json_identity(value: Mapping[str, object]) -> dict[str, object]:
            """Materialize immutable resolver identities for JSON artifacts."""
            return json.loads(json.dumps(value, default=dict))

        sources = [TailorSource("posting_1", "posting", posting.content, posting.content_sha256, _json_identity(posting.identity))]
        # Keep the host-owned semantic purpose alongside the Tailor transport
        # role.  The v3 invocation descriptor contract accepts the closed
        # source purposes (posting/preferences/experience/answer), while the
        # Tailor selection intentionally groups all private values under its
        # candidate_evidence role.
        descriptor_purposes = ["posting"]
        answers: list[object] = []
        for index, source in enumerate(private, start=1):
            sources.append(TailorSource(f"candidate_{index}", "candidate_evidence", source.content, source.content_sha256, _json_identity(source.identity)))
            descriptor_purposes.append(source.purpose)
            if source.purpose == "answer" and source.family == "scout_record":
                sidecar = parse_json_bytes(source.content)
                questions = sidecar.get("payload", {}).get("questions", []) if isinstance(sidecar, Mapping) else []
                qids = tuple(item.get("question_id") for item in questions if isinstance(item, Mapping) and isinstance(item.get("question_id"), str))
                if qids:
                    answers.append(TailorAnswer(source.identity["record_id"], source.identity["revision_id"], qids, source.content, source.content_sha256, _json_identity(source.identity)))
        proposal = None
        proposal_selector = sealed.get("proposal_selector")
        if isinstance(proposal_selector, Mapping):
            proposal = TailorProposal(**_tailor_saved_proposal(resolved, proposal_selector))
        selection = TailorSelection(str(posting.identity["opportunity_id"]), str(posting.identity["snapshot_id"]), tuple(sealed["requested_outputs"]), tuple(sources), proposal=proposal, answers=tuple(answers))
        request_payload = build_tailoring_request(selection)
        # These are invocation-local handles, not durable reference IDs. The
        # additive v3 invocation contract admits source_N handles for native
        # and discovery sources while preserving host-owned identities below.
        selected_ids = tuple(f"source_{index + 1}" for index in range(len(sources)))
        references = tuple(
            __import__("gigai.model_execution", fromlist=["SelectedReference"]).SelectedReference(
                selected_ids[index], f"scout/{source.source_id}", source.content, source.content_sha256
            ) for index, source in enumerate(sources)
        )
        descriptors = tuple(
            {
                "source_id": selected_ids[index],
                "family": source.identity.get("family", "scout_record"),
                "purpose": descriptor_purposes[index],
                "content_sha256": source.content_sha256,
                # Resolver identities use immutable mapping proxies.  Convert
                # the complete host-owned identity to ordinary JSON values
                # before hashing; shallow dict() would leave nested proxies
                # and make an authenticated selection unusable.
                "identity_sha256": digest_imported_bytes(
                    canonical_json_bytes(
                        json.loads(json.dumps(source.identity, default=dict))
                    )
                ),
            }
            for index, source in enumerate(sources)
        )
        class _Port:
            def invoke(self, invocation):
                holder["execution"] = __import__("gigai.model_execution", fromlist=["run_model_invocation","InvocationPolicy"]).run_model_invocation(
                    resolved=resolved, config=config, run_id=run_id, goal_id=goal_id, model_target=str(sealed["model_target"]), role=invocation.role, prompt=invocation.prompt, references=references, selected_reference_ids=selected_ids,
                    policy=__import__("gigai.model_execution", fromlist=["InvocationPolicy"]).InvocationPolicy(allowed_reference_ids=frozenset(selected_ids), local_allowed=True, offline=True, selected_source_descriptors=descriptors), budget=budget, uuid_factory=uuid_factory, commit_goal_transition=False)
                result = holder["execution"].result
                if not isinstance(result, InvocationResult):
                    raise RuntimeError("local Tailor invocation did not return a result")
                return result
        tailor = execute_tailor(selection, request_payload, port=_Port(), target_name=str(sealed["model_target"]), endpoint_name=str(sealed["endpoint"]), model=str(sealed["model"]), target_capabilities=frozenset({"text"}), local_allowed=True, max_output_tokens=int(sealed["target_bounds"]["max_output_tokens"]))
        execution = holder.get("execution")
        if not isinstance(execution, __import__("gigai.model_execution", fromlist=["ModelInvocationExecution"]).ModelInvocationExecution):
            raise RunError("tailor_invocation_invalid: invocation evidence is unavailable")
        _publish_tailor_invocation_attempt(resolved, run_id, goal_id, str(sealed["model_target"]), execution, uuid_factory)
        holder["attempt_recorded"] = True
        documents = decode_tailoring_bundle(tailor.output_bundle, selection.requested_outputs)
        doc_refs: list[dict[str, object]] = []
        for kind, content in documents.items():
            record_id, revision_id = f"record_{uuid_factory()}", f"revision_{uuid_factory()}"
            revision = prepare_document_revision(selection, kind, record_id, revision_id, content)
            recorded = record_document_revision(resolved=resolved, revision=revision, invocation={"run_id": run_id, "goal_id": goal_id, "invocation_id": execution.record["invocation_id"], "output_sha256": tailor.output_sha256}, operation_key=f"tailor-{execution.record['invocation_id']}-{kind}", uuid_factory=uuid_factory)
            doc_refs.append({"document_kind": kind, "record_id": record_id, "revision_id": revision_id, "content_sha256": revision.content_sha256, "recorded": recorded.created})
        result = {"schema_version": "scout-tailor-run-result:1", "status": "complete", "run_id": run_id, "goal_id": goal_id, "invocation_id": execution.record["invocation_id"], "model_target": str(sealed["model_target"]), "output_sha256": tailor.output_sha256, "documents": doc_refs, "sealed_journal_head": sealed_head}
        result_entry = _publish_tailor_result(resolved=resolved, run_id=run_id, goal_id=goal_id, graph=graph, gig_version=authority["version"], manifest_digest=manifest_digest, result=result, parent_handoff_id=started_goal.handoff_id, uuid_factory=uuid_factory)
        details = parse_json_bytes((run_path / "run-details.json").read_bytes())
        terminal = _finish_run(resolved, run_id, authority["version"], graph, details, "succeeded", result_entry.handoff_id, result_entry.handoff_id)
        return RunResult(run_id, resolved.gig_id, authority["version"], resolved.path, run_path, "succeeded", started, terminal)
    except Exception as exc:
        # Bundle validation and later private-document publication can fail
        # after the local call has already committed its invocation artifacts.
        # Preserve that evidence as a non-terminal receipt before terminalizing
        # the Goal; never make a malformed/partial model output look like a
        # successful document result.
        execution = holder.get("execution")
        if (
            isinstance(execution, __import__("gigai.model_execution", fromlist=["ModelInvocationExecution"]).ModelInvocationExecution)
            and not holder.get("attempt_recorded")
        ):
            _publish_tailor_invocation_attempt(
                resolved, run_id, goal_id, str(sealed["model_target"]), execution, uuid_factory
            )
            holder["attempt_recorded"] = True
        if isinstance(exc, RunError):
            error = exc
        else:
            error = RunError("tailor_run_failed: local Tailor execution failed")
            error.__cause__ = exc
        failed = _mark_proposal_goal_failed(resolved=resolved, run_id=run_id, gig_version=authority["version"], graph=graph, manifest_digest=manifest_digest, parent_handoff_id=started_goal.handoff_id)
        details = parse_json_bytes((run_path / "run-details.json").read_bytes())
        if failed is None:
            raise error
        terminal = _finish_run(resolved, run_id, authority["version"], graph, details, "failed", failed.handoff_id, started_goal.handoff_id)
        return RunResult(run_id, resolved.gig_id, authority["version"], resolved.path, run_path, "failed", started, terminal)


def _tailor_saved_proposal(resolved: ResolvedWorkpad, selector: Mapping[str, object]) -> dict[str, object]:
    from .scout.tailor_selection import hydrate_saved_proposal
    proposal = hydrate_saved_proposal(resolved=resolved, record_id=str(selector["record_id"]), revision_id=str(selector["revision_id"]))
    return {"record_id": proposal.record_id, "revision_id": proposal.revision_id, "content": proposal.content, "content_sha256": proposal.content_sha256, "identity": proposal.identity}


def _publish_tailor_invocation_attempt(resolved: ResolvedWorkpad, run_id: str, goal_id: str, model_target: str, execution: object, uuid_factory: Callable[[], uuid.UUID]) -> JournalEntry:
    artifacts = tuple(execution.artifacts)
    invocation_id = execution.record["invocation_id"]
    refs = [_artifact_ref(item.path, "application/json", item.content) for item in artifacts]
    return record_transition(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, handoff_id=_new_id(EntityPrefix.HANDOFF, uuid_factory), transition="tailor_invocation_recorded", body=f"Tailor invocation {invocation_id} evidence recorded.", artifacts=artifacts, front_matter={"run_id": run_id, "goal_id": goal_id, "invocation_id": invocation_id, "model_target": model_target, "actor": {"kind": "gigai", "id": "scout-tailor-execution", "model_target": model_target}, "artifact_refs": refs, "outcome": "INVOCATION_EVIDENCE"})


def _publish_tailor_result(*, resolved: ResolvedWorkpad, run_id: str, goal_id: str, graph: Mapping[str, object], gig_version: int, manifest_digest: str, result: Mapping[str, object], parent_handoff_id: str, uuid_factory: Callable[[], uuid.UUID]) -> JournalEntry:
    from .scout.proposal_execution import _committed_run_bytes, _validate_active_goal_bytes
    result_data = canonical_json_bytes(dict(result))
    result_path = f"runs/{run_id}/scout-tailor/result.json"
    def operation(writer: JournalWriter) -> JournalEntry:
        run_bytes = _committed_run_bytes(writer.root, run_id)
        _validate_active_goal_bytes(resolved, run_bytes, run_id, goal_id)
        details = parse_json_bytes(run_bytes[0])
        if not isinstance(details, dict):
            raise RunError("tailor_result_publication_refused: Run details are unavailable")
        detail = next((item for item in details.get("goals", []) if isinstance(item, dict) and item.get("goal_id") == goal_id), None)
        if not isinstance(detail, dict):
            raise RunError("tailor_result_publication_refused: Tailor Goal is unavailable")
        detail.update({"status": "complete" if result.get("status") == "complete" else "failed", "outcome": "COMPLETE" if result.get("status") == "complete" else "FAILED", "finished_at": _now(), "evidence": [{"path": result_path, "content_sha256": digest_imported_bytes(result_data), "media_type": "application/json", "size_bytes": len(result_data)}], "errors": []})
        _refresh_details(details, {item["goal_id"]: item for item in details["goals"]}, dict(graph), "running")
        details_data = canonical_json_bytes(details)
        refs = [{"path": result_path, "content_sha256": digest_imported_bytes(result_data), "media_type": "application/json", "size_bytes": len(result_data)}, {"path": f"runs/{run_id}/run-details.json", "content_sha256": digest_imported_bytes(details_data), "media_type": "application/json", "size_bytes": len(details_data)}]
        return writer.record(
            JournalTransition(
                _new_id(EntityPrefix.HANDOFF, uuid_factory),
                "goal_completed",
                "Scout Tailor Goal completed with its private document records.",
                (
                    JournalArtifact(result_path, result_data),
                    JournalArtifact(f"runs/{run_id}/run-details.json", details_data),
                ),
                {
                    "run_id": run_id,
                    "goal_id": goal_id,
                    "gig_version": gig_version,
                    "goal_graph_sha256": digest_imported_bytes(run_bytes[1]),
                    "source_manifest_sha256": manifest_digest,
                    "parent_handoff_ids": [parent_handoff_id],
                    "outcome": "COMPLETE",
                    "actor": {
                        "kind": "gigai",
                        "id": "scout-tailor-execution",
                        "model_target": result.get("model_target"),
                    },
                    "artifact_refs": refs,
                },
            ),
            allow_artifact_replacement=True,
        )
    return run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=operation)


def _canonical_model_digest(value: object) -> str | None:
    """Accept the config parser's bare digest normalization boundary too."""

    if not isinstance(value, str):
        return None
    bare = value.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", bare):
        return None
    return f"sha256:{bare}"


def _proposal_entry_goal(graph: Mapping[str, object]) -> str:
    entries = graph.get("entry_goal_ids")
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], str):
        raise RunError("proposal_run_authority_refused: proposal entry Goal is unavailable")
    return entries[0]


def _mark_proposal_goal_started(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: Mapping[str, object],
    manifest_digest: str,
    parent_handoff_id: str,
) -> JournalEntry:
    goal_id = _proposal_entry_goal(graph)
    path = resolved.path / "runs" / run_id / "run-details.json"
    details = parse_json_bytes(path.read_bytes())
    if not isinstance(details, dict) or not isinstance(details.get("goals"), list):
        raise RunError("proposal_run_authority_refused: Run details are unavailable")
    detail = next((item for item in details["goals"] if item.get("goal_id") == goal_id), None)
    if not isinstance(detail, dict) or detail.get("status") not in {"ready", "pending"}:
        raise RunError("proposal_run_authority_refused: proposal entry Goal is not schedulable")
    detail.update({"status": "running", "started_at": _now(), "outcome": None})
    goal_details = {item["goal_id"]: item for item in details["goals"]}
    _refresh_details(details, goal_details, graph, "running")
    goal = next(item for item in graph["goals"] if item["goal_id"] == goal_id)
    data = canonical_json_bytes(details)
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
        transition="goal_started",
        body=f"Proposal Goal {goal_id} started through the sealed local entry.",
        artifacts=(JournalArtifact(f"runs/{run_id}/run-details.json", data),),
        front_matter=_goal_front_matter(
            gig_version,
            run_id,
            goal,
            digest_imported_bytes(canonical_json_bytes(graph)),
            manifest_digest,
            parent_handoff_id,
            "STARTED",
        ),
    )


def _mark_proposal_goal_failed(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: Mapping[str, object],
    manifest_digest: str,
    parent_handoff_id: str,
) -> JournalEntry | None:
    goal_id = _proposal_entry_goal(graph)
    def operation(writer: object) -> JournalEntry | None:
        # Re-read committed history while holding the journal writer.  A stale
        # local exception must not append a second terminal Goal transition or
        # overwrite a competitor's succeeded/cancelled Run state.
        names = _git(writer.root, "ls-tree", "-r", "--name-only", "HEAD", "--", "handoffs/", check=False).stdout.splitlines()
        for name in names:
            if not name.endswith(".txt"):
                continue
            try:
                metadata, _body = parse_json_front_matter(_git_bytes(writer.root, "show", f"HEAD:{name}"))
            except Exception as exc:
                raise RunError("proposal_run_authority_refused: terminal Goal history is unreadable") from exc
            if metadata.get("run_id") == run_id and metadata.get("goal_id") == goal_id and metadata.get("transition") in {"goal_completed", "goal_failed", "goal_blocked"}:
                return None
        data = _git_bytes(writer.root, "show", f"HEAD:runs/{run_id}/run-details.json")
        details = parse_json_bytes(data)
        if not isinstance(details, dict) or not isinstance(details.get("goals"), list):
            raise RunError("proposal_run_authority_refused: Run details are unavailable")
        detail = next((item for item in details["goals"] if isinstance(item, dict) and item.get("goal_id") == goal_id), None)
        if not isinstance(detail, dict):
            raise RunError("proposal_run_authority_refused: proposal Goal is unavailable")
        if detail.get("status") in _TERMINAL_GOAL_STATES or detail.get("outcome") is not None:
            return None
        detail.update({"status": "failed", "outcome": "FAILED", "finished_at": _now(), "errors": [{"code": "proposal_execution_failed", "message": "proposal execution failed before result publication", "retryable": False, "invocation_id": None}]})
        goal_details = {item["goal_id"]: item for item in details["goals"]}
        _refresh_details(details, goal_details, graph, "failed")
        goal = next(item for item in graph["goals"] if item["goal_id"] == goal_id)
        updated = canonical_json_bytes(details)
        return writer.record(
            JournalTransition(
                _new_id(EntityPrefix.HANDOFF, uuid.uuid4),
                "goal_failed",
                f"Proposal Goal {goal_id} failed before result publication.",
                (JournalArtifact(f"runs/{run_id}/run-details.json", updated),),
                _goal_front_matter(gig_version, run_id, goal, digest_imported_bytes(canonical_json_bytes(graph)), manifest_digest, parent_handoff_id, "FAILED"),
            ),
            allow_artifact_replacement=True,
        )
    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=operation,
    )


def _acquire_provider_review_lease(
    resolved: ResolvedWorkpad, run_id: str
) -> int | None:
    """Local liveness only: a kernel lock, never portable execution authority.

    Held from before run_started through terminal publication. Process death
    releases it; independent status readers contend on the same stable inode.
    The file is disposable Git metadata and is never unlinked while in use.
    """
    validate_entity_id(run_id, expected_prefix=EntityPrefix.RUN)
    path = resolved.path / ".git" / f"gigai-provider-{run_id}.lock"
    _reject_symlinked_components(resolved.path, path, "provider lease path is unsafe")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RunError("provider lease is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def _recover_abandoned_provider_review(resolved: ResolvedWorkpad, run_id: str) -> None:
    """Journal an interruption, not provider success or fabricated Goal work.

    Recovery deliberately does not import provider result/invocation artifacts:
    the caller may have died while writing them or authenticating them. Journal
    preflight still refuses dirty/tampered evidence; it is never adopted here.
    """
    lease = _acquire_provider_review_lease(resolved, run_id)
    if lease is None:
        return
    try:
        prefix = f"runs/{run_id}"
        path = resolved.path / prefix / "run-details.json"
        _reject_symlinked_components(
            resolved.path, path, "provider Run details path is unsafe"
        )
        data = path.read_bytes()
        details = parse_json_bytes(data)
        if not isinstance(details, dict) or details.get("status") not in {
            "preparing",
            "running",
        }:
            return
        for relative in (
            f"{prefix}/run-details.json",
            f"{prefix}/run-manifest.json",
            f"{prefix}/operator-consent.json",
        ):
            candidate = resolved.path / relative
            _reject_symlinked_components(
                resolved.path, candidate, "provider recovery path is unsafe"
            )
            if candidate.read_bytes() != _git_bytes(
                resolved.path, "show", f"HEAD:{relative}"
            ):
                raise RunError(
                    "provider recovery requires journal-authenticated Run records"
                )
        if not _provider_review_active(resolved, run_id, details):
            raise RunError("provider recovery consent is unavailable")
        details["status"] = "interrupted"
        details["finished_at"] = _now()
        details["execution_summary"] = (
            "Provider caller exited before terminal publication; evidence preserved, offline Goals not executed."
        )
        details["aggregate_usage"] = {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cost": None,
            "currency": None,
            "cost_status": "unavailable",
        }
        details["model_errors"] = [
            *details.get("model_errors", []),
            {
                "code": "provider_review_abandoned",
                "message": "Provider execution lease was released before terminal publication.",
                "retryable": False,
                "invocation_id": None,
            },
        ]
        details["next_actions"] = [
            "Inspect preserved provider evidence and reconcile any journal conflict before creating a fresh sealed Run Plan; no automatic retry occurred."
        ]
        terminal_path = f"{prefix}/terminal-handoff.md"
        terminal_bytes = canonicalize_evidence(
            f"Run {run_id}: abandoned provider review interrupted. No offline Goals executed; no automatic retry.\n"
        )
        details["terminal_handoff"] = _artifact_ref(
            terminal_path, "text/markdown", terminal_bytes
        )
        record_transition(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
            transition="run_interrupted",
            body=f"Run {run_id} recovered as interrupted after provider caller abandonment.",
            artifacts=(
                JournalArtifact(
                    f"{prefix}/run-details.json", canonical_json_bytes(details)
                ),
                JournalArtifact(terminal_path, terminal_bytes),
            ),
            front_matter={
                "gig_version": details["gig_version"],
                "run_id": run_id,
                "outcome": "INTERRUPTED",
                "actor": {
                    "kind": "gigai",
                    "id": "g43.1-provider-review",
                    "model_target": None,
                },
            },
        )
    finally:
        os.close(lease)


def _resolve_authority(
    resolved: ResolvedWorkpad, projection: object, requested_version: int | None
) -> dict[str, object]:
    active = getattr(projection, "active_version", None)
    if not isinstance(active, dict):
        raise RunError("no active approved Gig version")
    active_version = active.get("active_version")
    if type(active_version) is not int:
        raise RunError("active Gig version is invalid")
    active_schema = (
        "active-gig-version-v2.schema.json"
        if "graph_set" in active
        else "active-gig-version.schema.json"
    )
    active_report = validate_serialized_contract(
        active_schema, canonical_json_bytes(active)
    )
    if not active_report.valid or active.get("gig_id") != resolved.gig_id:
        raise RunError("active-version authority is invalid")
    if requested_version is None:
        version = active_version
        commit = active.get("journal_commit")
    else:
        if type(requested_version) is not int or requested_version < 1:
            raise RunError("--version must be a positive integer")
        version = requested_version
        tag = f"gig-v{version:06d}"
        tag_result = _git(resolved.path, "rev-parse", "--verify", tag, check=False)
        if tag_result.returncode != 0:
            raise RunError("requested Gig version is not approved")
        commit = tag_result.stdout.strip()
    if not isinstance(commit, str) or not commit:
        raise RunError("approved Gig version has no journal commit")
    tag_name = (
        active.get("journal_tag")
        if requested_version is None
        else f"gig-v{version:06d}"
    )
    tag_result = _git(
        resolved.path, "rev-parse", "--verify", str(tag_name), check=False
    )
    if tag_result.returncode != 0 or tag_result.stdout.strip() != commit:
        raise RunError("approved Gig authority is divergent from its immutable tag")
    proposal_bytes = _git_bytes(
        resolved.path, "show", f"{commit}:manifests/gig-proposal.json"
    )
    proposal = parse_json_bytes(proposal_bytes)
    if not isinstance(proposal, dict):
        raise RunError("approved Gig authority is malformed")
    if validate_serialized_contract(
        "gig-proposal-v2.schema.json", proposal_bytes
    ).valid:
        graph_set_ref = proposal.get("graph_set")
        if not isinstance(graph_set_ref, Mapping) or not isinstance(
            graph_set_ref.get("path"), str
        ):
            raise RunError("approved Graph Set authority is malformed")
        graph_set_bytes = _git_bytes(
            resolved.path, "show", f"{commit}:{graph_set_ref['path']}"
        )
        if digest_imported_bytes(graph_set_bytes) != graph_set_ref.get(
            "content_sha256"
        ):
            raise RunError("approved Graph Set bytes do not match the proposal")
        graph_set = parse_json_bytes(graph_set_bytes)
        if (
            not isinstance(graph_set, dict)
            or not validate_graph_set(graph_set_bytes).valid
        ):
            raise RunError("approved Graph Set failed revalidation")
        return {
            "version": version,
            "commit": commit,
            "proposal": proposal,
            "graph_set": graph_set,
            "graph_set_ref": dict(graph_set_ref),
            "graph": None,
        }
    graph_bytes = _git_bytes(
        resolved.path, "show", f"{commit}:manifests/goal-graph.json"
    )
    graph = parse_json_bytes(graph_bytes)
    if not isinstance(graph, dict):
        raise RunError("approved Gig authority is malformed")
    return {"version": version, "commit": commit, "proposal": proposal, "graph": graph}


def _validate_authority(
    resolved: ResolvedWorkpad, graph: object, proposal: object
) -> None:
    if not isinstance(graph, dict) or not isinstance(proposal, dict):
        raise RunError("approved authority is malformed")
    graph_report = validate_serialized_contract(
        "goal-graph.schema.json", canonical_json_bytes(graph)
    )
    proposal_schema = (
        "gig-proposal-v2.schema.json"
        if "graph_set" in proposal
        else "gig-proposal.schema.json"
    )
    proposal_report = validate_serialized_contract(
        proposal_schema, canonical_json_bytes(proposal)
    )
    semantic = validate_goal_graph(graph)
    if not graph_report.valid or not proposal_report.valid or not semantic.valid:
        raise RunError("approved Goal Graph failed revalidation")
    if (
        proposal.get("status") != "approved"
        or proposal.get("gig_id") != resolved.gig_id
    ):
        raise RunError("approved proposal authority is inconsistent")
    if "graph_set" not in proposal and proposal.get("goal_graph", {}).get(
        "content_sha256"
    ) != digest_imported_bytes(canonical_json_bytes(graph)):
        raise RunError("approved proposal does not pin the Goal Graph")
    goals = graph.get("goals", [])
    entries = set(graph.get("entry_goal_ids", []))
    if not isinstance(goals, list) or not entries:
        raise RunError("approved Goal Graph has no ready entry Goal")
    if len(goals) != len(
        {goal.get("goal_id") for goal in goals if isinstance(goal, dict)}
    ):
        raise RunError("approved Goal Graph has duplicate Goals")


def resolve_selected_graph_authority(
    resolved: ResolvedWorkpad, authority: Mapping[str, object], selector: str | None
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Resolve journal-tagged multi-graph authority before Plan/Run allocation.

    The returned descriptor is ``None`` only for byte-identical v1 authority.
    Every descriptor reference is re-read from the immutable approval commit,
    which prevents a current workpad file or projection from substituting it.
    """
    graph = authority.get("graph")
    if isinstance(graph, dict):
        _validate_authority(resolved, graph, authority.get("proposal"))
        return graph, None
    graph_set = authority.get("graph_set")
    commit = authority.get("commit")
    if not isinstance(graph_set, dict) or not isinstance(commit, str):
        raise RunError("approved Graph Set authority is malformed")
    for descriptor in graph_set.get("graphs", ()):
        if not isinstance(descriptor, Mapping):
            raise RunError("approved Graph Set descriptor is malformed")
        for field in (
            "goal_graph",
            "input_contract",
            "output_contract",
            "permitted_reference_contract",
            "review_contract",
            "evaluation_contract",
            "completion_evidence_contract",
        ):
            ref = descriptor.get(field)
            if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
                raise RunError("approved Graph Set reference is malformed")
            path = str(ref["path"])
            if Path(path).is_absolute() or "\\" in path or ".." in Path(path).parts:
                raise RunError("approved Graph Set reference path is unsafe")
            data = _git_bytes(resolved.path, "show", f"{commit}:{path}")
            if digest_imported_bytes(data) != ref.get("content_sha256") or len(
                data
            ) != ref.get("size_bytes"):
                raise RunError("approved Graph Set reference changed or is unavailable")
            if field != "goal_graph" and not _attached_contract_valid(
                field, data, gig_id=resolved.gig_id
            ):
                raise RunError(
                    "approved Graph Set attachment has an unsupported or invalid contract form"
                )
    graphs = graph_set.get("graphs")
    if not isinstance(graphs, list):
        raise RunError("approved Graph Set has no graphs")
    if selector is None:
        if len(graphs) != 1:
            raise RunError("graph_selection_required: approved Gig has multiple graphs")
        descriptor = graphs[0] if isinstance(graphs[0], dict) else None
    else:
        descriptor = graph_set_descriptor(graph_set, selector)
    if not isinstance(descriptor, dict):
        raise RunError(
            "graph_selection_invalid: graph selector is not available in the approved Graph Set"
        )
    graph_ref = descriptor.get("goal_graph")
    assert isinstance(graph_ref, Mapping) and isinstance(graph_ref.get("path"), str)
    graph_bytes = _git_bytes(resolved.path, "show", f"{commit}:{graph_ref['path']}")
    graph = parse_json_bytes(graph_bytes)
    if not isinstance(graph, dict):
        raise RunError("selected Goal Graph is malformed")
    _validate_authority(resolved, graph, authority.get("proposal"))
    if (
        not _budget_within(graph.get("aggregate_budget"), descriptor.get("budget"))
        or not set(
            effect
            for goal in graph.get("goals", ())
            if isinstance(goal, Mapping)
            for effect in goal.get("effects", ())
        ).issubset(set(descriptor.get("effect_policy", ())))
        or not set(
            goal.get("executor", {}).get("capability")
            for goal in graph.get("goals", ())
            if isinstance(goal, Mapping)
            and isinstance(goal.get("executor"), Mapping)
            and isinstance(goal["executor"].get("capability"), str)
        ).issubset(set(descriptor.get("capability_requirements", ())))
    ):
        raise RunError("selected Goal Graph exceeds the approved descriptor ceiling")
    for goal in graph.get("goals", ()):
        if not isinstance(goal, Mapping) or not isinstance(
            goal.get("contract"), Mapping
        ):
            raise RunError("selected Goal Graph has malformed goal contract authority")
        contract_ref = goal["contract"]
        contract_path = contract_ref.get("path")
        if (
            not isinstance(contract_path, str)
            or Path(contract_path).is_absolute()
            or "\\" in contract_path
            or ".." in Path(contract_path).parts
        ):
            raise RunError("selected Goal Graph contract path is unsafe")
        contract_data = _git_bytes(resolved.path, "show", f"{commit}:{contract_path}")
        if digest_imported_bytes(contract_data) != contract_ref.get(
            "content_sha256"
        ) or len(contract_data) != contract_ref.get("size_bytes"):
            raise RunError("selected Goal Graph contract changed or is unavailable")
    return graph, descriptor


def _validate_plan_handoff(
    *,
    resolved: ResolvedWorkpad,
    plan: Mapping[str, object],
    plan_id: str,
    plan_digest: str,
    gig_version: int,
    graph: dict[str, object],
    authority: Mapping[str, object],
    selected_descriptor: dict[str, object] | None,
    config: object,
) -> None:
    """Refuse changed plan evidence before a Run ID can be allocated."""

    if (
        plan.get("state") != "sealed"
        or plan.get("run_plan_id") != plan_id
        or plan.get("project_id") != resolved.project_id
        or plan.get("gig_id") != resolved.gig_id
        or plan.get("gig_version") != gig_version
    ):
        raise RunError(
            "run_plan_authority_refused: plan is not sealed for this approved Gig"
        )
    if plan.get("journal_commit") is not None and plan.get(
        "journal_commit"
    ) != _authority_commit_for_plan(plan, resolved, gig_version):
        raise RunError(
            "run_plan_authority_refused: plan is pinned to a different approved authority commit"
        )
    graph_ref = plan.get("goal_graph")
    if not isinstance(graph_ref, dict) or graph_ref.get(
        "content_sha256"
    ) != digest_imported_bytes(canonical_json_bytes(graph)):
        raise RunError(
            "run_plan_input_mismatch: plan does not pin the approved Goal Graph"
        )
    if selected_descriptor is not None:
        graph_set_ref = authority.get("graph_set_ref")
        if (
            plan.get("graph_set") != graph_set_ref
            or plan.get("selected_graph_id") != selected_descriptor.get("graph_id")
            or plan.get("selected_graph") != selected_descriptor.get("goal_graph")
        ):
            raise RunError(
                "run_plan_authority_refused: plan graph selection does not match approved authority"
            )
    plan_path = resolved.path / "run-plans" / plan_id / "run-plan.json"
    _reject_symlinked_components(
        resolved.path,
        plan_path,
        "run_plan_input_mismatch: plan path contains a symlinked component",
    )
    if (
        plan_path.is_symlink()
        or digest_imported_bytes(plan_path.read_bytes()) != plan_digest
    ):
        raise RunError("run_plan_digest_mismatch: plan bytes changed after sealing")
    from .run_plan import _identity_projection

    if derive_deterministic_id("run_plan", _identity_projection(plan)) != plan_id:
        raise RunError(
            "run_plan_digest_mismatch: plan identity does not match its sealed projection"
        )
    sources = plan.get("sealed_sources")
    if not isinstance(sources, list) or not sources:
        raise RunError("run_plan_invalid: plan has no sealed sources")
    source_pairs: set[tuple[object, object]] = set()
    source_data: dict[str, bytes] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise RunError("run_plan_invalid: plan source is malformed")
        relative = source.get("path")
        expected = source.get("content_sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise RunError("run_plan_invalid: plan source is malformed")
        if Path(relative).parts and Path(relative).parts[0] in {
            "references",
            "run-inputs",
            "records",
            "docs",
        }:
            raise RunError(
                "private_provider_disclosure_refused: private selected inputs require the later restricted external-recording boundary"
            )
        pair = (relative, expected)
        if pair in source_pairs:
            raise RunError("run_plan_invalid: plan contains duplicate sealed sources")
        source_pairs.add(pair)
        candidate = resolved.path / relative
        if (
            Path(relative).is_absolute()
            or "\\" in relative
            or ".." in Path(relative).parts
        ):
            raise RunError("run_plan_input_mismatch: plan source path is unsafe")
        try:
            _reject_symlinked_components(
                resolved.path,
                candidate,
                "run_plan_input_mismatch: plan source path contains a symlinked component",
            )
            candidate.resolve(strict=False).relative_to(resolved.path.resolve())
        except ValueError as exc:
            raise RunError(
                "run_plan_input_mismatch: plan source escapes the workpad"
            ) from exc
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or digest_imported_bytes(candidate.read_bytes()) != expected
        ):
            raise RunError(
                "run_plan_input_mismatch: sealed plan source changed or is unavailable"
            )
        source_data[relative] = candidate.read_bytes()
    if selected_descriptor is not None:
        selection_ref = plan.get("selection_record")
        if (
            not isinstance(selection_ref, Mapping)
            or not isinstance(selection_ref.get("path"), str)
            or selection_ref["path"] not in source_data
        ):
            raise RunError(
                "run_plan_authority_refused: graph selection evidence is not sealed"
            )
        selection_bytes = source_data[str(selection_ref["path"])]
        graph_set = authority.get("graph_set")
        if (
            not isinstance(graph_set, dict)
            or not validate_selection_record(
                selection_bytes,
                graph_set=graph_set,
                gig_id=resolved.gig_id,
                gig_version=gig_version,
                root=resolved.path,
            ).valid
        ):
            raise RunError(
                "run_plan_authority_refused: graph selection evidence is invalid"
            )
    graph_ref = plan.get("goal_graph")
    contract_ref = plan.get("review_contract")
    if (
        not isinstance(graph_ref, Mapping)
        or source_data.get(graph_ref.get("path")) is None
    ):
        raise RunError(
            "run_plan_authority_refused: plan Goal Graph reference is not sealed"
        )
    if (
        selected_descriptor is None
        and graph_ref.get("path") != "manifests/goal-graph.json"
    ):
        raise RunError(
            "run_plan_authority_refused: legacy Plan Goal Graph reference is not authoritative"
        )
    if selected_descriptor is not None:
        selected_ref = selected_descriptor.get("goal_graph")
        if not isinstance(selected_ref, Mapping) or selected_ref.get(
            "content_sha256"
        ) != graph_ref.get("content_sha256"):
            raise RunError(
                "run_plan_authority_refused: Plan graph snapshot differs from selected authority"
            )
    if (
        not isinstance(contract_ref, Mapping)
        or source_data.get(contract_ref.get("path")) is None
    ):
        raise RunError("run_plan_invalid: plan review contract is not sealed")
    discovery_refs = plan.get("discovery_snapshot_refs")
    if not isinstance(discovery_refs, list) or not discovery_refs:
        raise RunError("run_plan_invalid: plan discovery identity is missing")
    discovery_sources = [
        source_data[path] for path in source_data if path.endswith("/discovery.json")
    ]
    from .run_plan import _discovery_identity_digest

    if not any(
        _discovery_identity_digest(data) in discovery_refs for data in discovery_sources
    ):
        raise RunError("run_plan_input_mismatch: discovery snapshot identity changed")
    participants = plan.get("participants", [])
    for participant in participants:
        if not isinstance(participant, Mapping):
            raise RunError("run_plan_invalid: participant is malformed")
        target_name = participant.get("model_target_id")
        if not isinstance(target_name, str):
            raise RunError("run_plan_invalid: participant target is malformed")
        readiness = resolve_target_readiness(config, target_name)
        if readiness.readiness == "configured":
            readiness = (
                recorded_target_readiness(config.home_root, config, target_name)
                or readiness
            )
        if readiness.readiness != "usable":
            raise RunError(
                "run_plan_authority_refused: assigned target is no longer usable"
            )
        target_ref = participant.get("target_configuration_ref")
        try:
            current_target = resolve_model_target(config, target_name)
            current_target_bytes = canonical_json_bytes(
                {
                    "schema_version": "1.0",
                    "target_id": target_name,
                    "endpoint": current_target.endpoint.name,
                    "adapter": current_target.endpoint.adapter,
                    "model": current_target.target.model,
                    "capabilities": list(current_target.target.capabilities),
                    "readiness": "usable",
                }
            )
        except (RuntimeError, ValueError) as exc:
            raise RunError(
                "run_plan_authority_refused: assigned target cannot be resolved"
            ) from exc
        if (
            not isinstance(target_ref, Mapping)
            or target_ref.get("content_sha256")
            != digest_imported_bytes(current_target_bytes)
            or participant.get("provider_id") != current_target.endpoint.name
        ):
            raise RunError(
                "run_plan_authority_refused: assigned target configuration changed"
            )
        for field in ("target_configuration_ref", "discovery_ref"):
            reference = participant.get(field)
            if (
                not isinstance(reference, Mapping)
                or reference.get("path") not in source_data
            ):
                raise RunError(
                    "run_plan_input_mismatch: participant evidence reference is not sealed"
                )
    for item in plan.get("inputs", ()):
        if not isinstance(item, Mapping):
            raise RunError("run_plan_invalid: input is malformed")
        record_ref = item.get("record_ref")
        snapshot_ref = item.get("snapshot_ref")
        if (
            not isinstance(record_ref, Mapping)
            or not isinstance(snapshot_ref, Mapping)
            or record_ref.get("path") not in source_data
            or snapshot_ref.get("path") not in source_data
        ):
            raise RunError(
                "run_plan_input_mismatch: input evidence reference is not sealed"
            )
    for run_manifest in (resolved.path / "runs").glob("run_*/run-manifest.json"):
        if run_manifest.is_symlink() or not run_manifest.is_file():
            continue
        try:
            manifest = parse_json_bytes(run_manifest.read_bytes())
        except Exception:
            continue
        if isinstance(manifest, dict) and any(
            isinstance(item, dict)
            and item.get("path") == f"run-plans/{plan_id}/run-plan.json"
            and item.get("content_sha256") == plan_digest
            for item in manifest.get("sealed_sources", [])
        ):
            raise RunError("run_plan_already_handed_off: sealed plan already has a Run")


def _reject_symlinked_components(root: Path, candidate: Path, message: str) -> None:
    """Reject symlinked parents as well as a symlink leaf for authority paths."""
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise RunError(message) from exc
    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise RunError(message)


def _authority_commit_for_plan(
    plan: Mapping[str, object], resolved: ResolvedWorkpad, gig_version: int
) -> str | None:
    """Resolve the immutable tag again for a pinned plan commitment."""
    tag_result = _git(
        resolved.path, "rev-parse", "--verify", f"gig-v{gig_version:06d}", check=False
    )
    return tag_result.stdout.strip() if tag_result.returncode == 0 else None


def _validate_operator_consent(
    consent: Mapping[str, object], *, ui_loopback_verified: bool = False
) -> None:
    """Validate the caller envelope before server-side scope synthesis."""
    allowed = {
        "schema_version",
        "kind",
        "action",
        "actor",
        "source",
        "invocation_id",
        "occurrence_id",
    }
    source = consent.get("source")
    if set(consent) - allowed or source not in {
        "direct_cli_confirm",
        "direct_local_ui_confirm",
    }:
        raise RunError("Run consent must come from direct local operator confirmation")
    if source == "direct_local_ui_confirm" and ui_loopback_verified is not True:
        raise RunError("Run consent from the local UI requires a verified loopback peer")
    if (
        consent.get("schema_version") != "1.0"
        or consent.get("kind") != "operator_run_consent"
        or consent.get("action") != "run"
    ):
        raise RunError("Run consent envelope is invalid")
    actor = consent.get("actor")
    if (
        not isinstance(actor, Mapping)
        or actor.get("kind") != "operator"
        or actor.get("id") != "local-user"
        or set(actor) - {"kind", "id"}
    ):
        raise RunError("Run consent actor is invalid")


def _prepare_records(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    authority_commit: str,
    graph: dict[str, object],
    proposal: dict[str, object],
    target_before: dict[str, object],
    invocation_argv: tuple[str, ...],
    operator_consent: Mapping[str, object] | None = None,
    run_plan_ref: dict[str, object] | None = None,
    graph_authority: Mapping[str, object] | None = None,
    selected_descriptor: Mapping[str, object] | None = None,
    proposal_request_bytes: bytes | None = None,
    operation_request_kind: str = "proposal",
    find_jobs_execution: _FindJobsRunExecution | None = None,
) -> dict[str, bytes]:
    run_dir = f"runs/{run_id}"
    goals = [item for item in graph["goals"] if isinstance(item, dict)]
    graph_bytes = canonical_json_bytes(graph)
    capability = canonical_json_bytes(
        {"capability": "gigai.offline", "version": "1", "executor": "local_capability"}
    )
    target_bytes = canonical_json_bytes(target_before)
    now = _now()
    budget = graph["aggregate_budget"]
    sealed_effects = {"write_workpad"}
    if find_jobs_execution is not None:
        local_model = (
            find_jobs_execution.request.model_target == ModelTarget.OLLAMA_LOCAL
        )
        for goal in goals:
            effects = {
                effect for effect in goal.get("effects", ()) if isinstance(effect, str)
            }
            if local_model and goal.get("slug") == "assess":
                effects = set(ASSESS_LOCAL_EFFECTS)
            sealed_effects.update(effects)
    sealed_effect_list = sorted(sealed_effects)

    def artifact(path: str, media: str, data: bytes) -> dict[str, object]:
        return {
            "path": path,
            "content_sha256": digest_imported_bytes(data),
            "media_type": media,
            "size_bytes": len(data),
        }

    source_ref = artifact(
        f"{run_dir}/sealed/offline-capability.json", "application/json", capability
    )
    proposal_ref = (
        artifact(
            f"{run_dir}/sealed/{operation_request_kind}-execution-request.json",
            "application/json",
            proposal_request_bytes,
        )
        if proposal_request_bytes is not None
        else None
    )
    proposal_selection_ref: dict[str, object] | None = None
    proposal_selection_bytes: bytes | None = None
    if proposal_request_bytes is not None and graph_authority is not None and selected_descriptor is not None:
        graph_set = graph_authority.get("graph_set")
        graph_set_ref = graph_authority.get("graph_set_ref")
        if not isinstance(graph_set, Mapping) or not isinstance(graph_set_ref, Mapping):
            raise RunError("proposal_run_authority_refused: Graph Set selection authority is unavailable")
        selection_id = derive_deterministic_id(
            "graph_selection",
            {
                "gig_id": resolved.gig_id,
                "gig_version": gig_version,
                "graph_set": graph_set_ref.get("content_sha256"),
                "selected_graph_id": selected_descriptor.get("graph_id"),
                "kind": "operator_explicit",
            },
        )
        selection = {
            "schema_version": "1.0",
            "selection_record_id": selection_id,
            "gig_id": resolved.gig_id,
            "gig_version": gig_version,
            "graph_set": graph_set_ref,
            "selected_graph_id": selected_descriptor.get("graph_id"),
            "selected_graph": selected_descriptor.get("goal_graph"),
            "selection_kind": "operator_explicit",
            "selector": {
                "kind": "operator",
                "actor": {"kind": "operator", "id": "local-user", "model_target": None},
                "rule_id": None,
                "rule_version": None,
            },
            "selection_reason": "explicit proposal-assessment operation",
            "routing_evidence_refs": [],
            "created_at": graph_set.get("created_at", now),
        }
        selection_data = canonical_json_bytes(selection)
        proposal_selection_bytes = selection_data
        if not validate_selection_record(
            selection_data,
            graph_set=graph_set,
            gig_id=resolved.gig_id,
            gig_version=gig_version,
        ).valid:
            raise RunError("proposal_run_authority_refused: proposal Graph selection is invalid")
        proposal_selection_ref = artifact(
            f"{run_dir}/sealed/proposal-graph-selection.json",
            "application/json",
            selection_data,
        )
    find_jobs_input_ref: dict[str, object] | None = None
    find_jobs_input_bytes: bytes | None = None
    find_jobs_config_ref: dict[str, object] | None = None
    find_jobs_config_bytes: bytes | None = None
    find_jobs_selection_ref: dict[str, object] | None = None
    find_jobs_selection_bytes: bytes | None = None
    if find_jobs_execution is not None:
        find_jobs_input_bytes = find_jobs_execution.input_bytes
        find_jobs_config_bytes = find_jobs_execution.config_bytes
        find_jobs_input_ref = artifact(
            f"{run_dir}/sealed/find-jobs-run-input.json",
            "application/json",
            find_jobs_input_bytes,
        )
        find_jobs_config_ref = artifact(
            f"{run_dir}/sealed/find-jobs-config.json",
            "application/json",
            find_jobs_config_bytes,
        )
        if graph_authority is not None and selected_descriptor is not None:
            graph_set = graph_authority.get("graph_set")
            graph_set_ref = graph_authority.get("graph_set_ref")
            if not isinstance(graph_set, Mapping) or not isinstance(graph_set_ref, Mapping):
                raise RunError(
                    "find_jobs_run_authority_refused: Graph Set selection authority is unavailable"
                )
            selection_id = derive_deterministic_id(
                "graph_selection",
                {
                    "gig_id": resolved.gig_id,
                    "gig_version": gig_version,
                    "graph_set": graph_set_ref.get("content_sha256"),
                    "selected_graph_id": selected_descriptor.get("graph_id"),
                    "kind": "find_jobs_functional",
                },
            )
            selection = {
                "schema_version": "1.0",
                "selection_record_id": selection_id,
                "gig_id": resolved.gig_id,
                "gig_version": gig_version,
                "graph_set": graph_set_ref,
                "selected_graph_id": selected_descriptor.get("graph_id"),
                "selected_graph": selected_descriptor.get("goal_graph"),
                "selection_kind": "operator_explicit",
                "selector": {
                    "kind": "operator",
                    "actor": {"kind": "operator", "id": "local-user", "model_target": None},
                    "rule_id": None,
                    "rule_version": None,
                },
                "selection_reason": "sealed functional find-jobs traversal",
                "routing_evidence_refs": [],
                "created_at": graph_set.get("created_at", now),
            }
            selection_data = canonical_json_bytes(selection)
            if not validate_selection_record(
                selection_data,
                graph_set=graph_set,
                gig_id=resolved.gig_id,
                gig_version=gig_version,
            ).valid:
                raise RunError(
                    "find_jobs_run_authority_refused: find-jobs Graph selection is invalid"
                )
            find_jobs_selection_bytes = selection_data
            find_jobs_selection_ref = artifact(
                f"{run_dir}/sealed/find-jobs-graph-selection.json",
                "application/json",
                selection_data,
            )
    consent_bytes = (
        canonical_json_bytes(dict(operator_consent))
        if operator_consent is not None
        else None
    )
    consent_ref = (
        artifact(f"{run_dir}/operator-consent.json", "application/json", consent_bytes)
        if consent_bytes is not None
        else None
    )
    target_ref = artifact(
        f"{run_dir}/target-before.json", "application/json", target_bytes
    )
    graph_ref = artifact(f"{run_dir}/goal-graph.json", "application/json", graph_bytes)
    brief_body = (
        f"# Run {run_id}\n\nScout find-jobs execution; operator-selected sources, target, and resume are sealed.\n"
        if find_jobs_execution is not None
        else (
            f"# Run {run_id}\n\nScout local {operation_request_kind} execution; operator-selected sources and target are sealed.\n"
            if proposal_request_bytes is not None
            else f"# Run {run_id}\n\nDeterministic workpad-only execution.\n"
        )
    )
    brief_meta = {
        "schema_version": "1.0",
        "run_id": run_id,
        "gig_id": resolved.gig_id,
        "gig_version": gig_version,
        "created_at": now,
        "invoked_by": {"kind": "operator", "id": "local-user"},
        "invocation_argv": list(invocation_argv),
        "goal_graph": graph_ref,
        "target": {
            "kind": "git" if resolved.target_kind == "git" else "directory",
            "root": "bound-target",
            "git_head": target_before.get("git_head"),
            "status_sha256": target_before.get("status_sha256"),
            "observation_sha256": target_before["observation_sha256"],
        },
        "profile": "default",
        "resolved_models": [],
        "resolved_tools": [],
        "effects": sealed_effect_list,
        "aggregate_budget": budget,
        "input_canonical_sha256": digest_imported_bytes(graph_bytes),
        "body_sha256": digest_owned_text(brief_body),
        "run_manifest_path": f"{run_dir}/run-manifest.json",
    }
    brief = _front_matter(brief_meta, brief_body)
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "gig_id": resolved.gig_id,
        "gig_version": gig_version,
        "authority": "run_invocation",
        "status": "sealed",
        "sealed_at": now,
        "invoked_by": {"kind": "operator", "id": "local-user"},
        "invocation_argv": list(invocation_argv),
        "run_brief": artifact(f"{run_dir}/run-brief.md", "text/markdown", brief),
        "goal_graph": graph_ref,
        "goal_contracts": [
            {
                "goal_id": goal["goal_id"],
                "goal_version": goal["goal_version"],
                "contract": artifact(
                    f"{run_dir}/{goal['contract']['path']}",
                    "text/markdown",
                    _git_bytes(
                        resolved.path,
                        "show",
                        f"{authority_commit}:{goal['contract']['path']}",
                    ),
                ),
            }
            for goal in goals
        ],
        "target_observation": target_ref,
        "profile": "default",
        "resolved_models": [],
        "resolved_tools": [],
        "sealed_sources": [
            source_ref,
            *([run_plan_ref] if run_plan_ref else []),
            *([consent_ref] if consent_ref else []),
            *([proposal_ref] if proposal_ref else []),
            *([proposal_selection_ref] if proposal_selection_ref else []),
            *([find_jobs_input_ref] if find_jobs_input_ref else []),
            *([find_jobs_config_ref] if find_jobs_config_ref else []),
            *([find_jobs_selection_ref] if find_jobs_selection_ref else []),
        ],
        "effects": sealed_effect_list,
        "aggregate_budget": budget,
        "input_canonical_sha256": digest_imported_bytes(graph_bytes),
    }
    if graph_authority is not None and selected_descriptor is not None:
        manifest["graph_set"] = graph_authority["graph_set_ref"]
        manifest["selected_graph_id"] = selected_descriptor["graph_id"]
        manifest["selected_graph"] = selected_descriptor["goal_graph"]
        if run_plan_ref is not None:
            plan = parse_json_bytes(
                (resolved.path / str(run_plan_ref["path"])).read_bytes()
            )
            if not isinstance(plan, Mapping):
                raise RunError("graph-selected Run Plan is malformed")
            manifest["selection_record"] = plan["selection_record"]
        elif proposal_selection_ref is not None:
            manifest["selection_record"] = proposal_selection_ref
        elif find_jobs_selection_ref is not None:
            manifest["selection_record"] = find_jobs_selection_ref
        else:
            raise RunError("graph-selected Run requires sealed selection evidence")
    manifest_bytes = canonical_json_bytes(manifest)
    brief_metadata, _brief_body = parse_json_front_matter(brief)
    brief_report = validate_serialized_contract(
        "run-brief-frontmatter.schema.json", canonical_json_bytes(brief_metadata)
    )
    if not brief_report.valid:
        raise RunError(
            "Run Brief failed schema validation: "
            + ",".join(item.code + ":" + item.message for item in brief_report.findings)
        )
    manifest_report = validate_serialized_contract(
        "run-manifest-v2.schema.json"
        if graph_authority is not None
        else "run-manifest.schema.json",
        manifest_bytes,
    )
    if not manifest_report.valid:
        raise RunError(
            "Run manifest failed schema validation: "
            + ",".join(
                item.code + ":" + item.message for item in manifest_report.findings
            )
        )
    marked_goals = [
        {**goal, "_entry": goal["goal_id"] in set(graph["entry_goal_ids"])}
        for goal in goals
    ]
    initial = _details(
        run_id,
        resolved.gig_id,
        gig_version,
        digest_imported_bytes(graph_bytes),
        marked_goals,
        budget,
        target_ref,
        now,
        status="preparing",
    )
    if not validate_serialized_contract(
        "run-details.schema.json", canonical_json_bytes(initial)
    ).valid:
        raise RunError("initial RunDetails failed schema validation")
    initial["critical_path"] = _critical_path(graph)
    return {
        f"{run_dir}/run-brief.md": brief,
        f"{run_dir}/run-manifest.json": manifest_bytes,
        f"{run_dir}/run-details.json": canonical_json_bytes(initial),
        f"{run_dir}/goal-graph.json": graph_bytes,
        f"{run_dir}/target-before.json": target_bytes,
        f"{run_dir}/sealed/offline-capability.json": capability,
        **(
            {f"{run_dir}/operator-consent.json": consent_bytes}
            if consent_bytes is not None
            else {}
        ),
        **(
            {f"{run_dir}/sealed/proposal-execution-request.json": proposal_request_bytes}
            if proposal_request_bytes is not None
            else {}
        ),
        **(
            {f"{run_dir}/sealed/proposal-graph-selection.json": proposal_selection_bytes}
            if proposal_selection_bytes is not None
            else {}
        ),
        **(
            {f"{run_dir}/sealed/find-jobs-run-input.json": find_jobs_input_bytes}
            if find_jobs_input_bytes is not None
            else {}
        ),
        **(
            {f"{run_dir}/sealed/find-jobs-config.json": find_jobs_config_bytes}
            if find_jobs_config_bytes is not None
            else {}
        ),
        **(
            {f"{run_dir}/sealed/find-jobs-graph-selection.json": find_jobs_selection_bytes}
            if find_jobs_selection_bytes is not None
            else {}
        ),
        **{
            f"{run_dir}/{goal['contract']['path']}": _git_bytes(
                resolved.path, "show", f"{authority_commit}:{goal['contract']['path']}"
            )
            for goal in goals
        },
    }


def _materialize_run_review_bridge(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    plan: Mapping[str, object],
    plan_id: str,
    plan_digest: object,
    gig_version: int,
    manifest_digest: str,
    parent_handoff_id: str,
    observer: RunObserver,
) -> JournalEntry:
    """Create the initial G43 review records only after ``run_started``."""
    contract_ref = plan.get("review_contract")
    if not isinstance(contract_ref, Mapping) or not isinstance(
        contract_ref.get("path"), str
    ):
        raise RunError("run_plan_invalid: review contract reference is unavailable")
    contract_path = resolved.path / str(contract_ref["path"])
    if contract_path.is_symlink() or not contract_path.is_file():
        raise RunError("run_plan_input_mismatch: review contract source is unavailable")
    contract = parse_json_bytes(contract_path.read_bytes())
    if not isinstance(contract, Mapping) or not isinstance(
        contract.get("contract_id"), str
    ):
        raise RunError("run_plan_invalid: review contract is malformed")
    contract_id = str(contract["contract_id"])
    participants = [
        item for item in plan.get("participants", ()) if isinstance(item, Mapping)
    ]
    reviewers = [item for item in participants if "reviewer" in item.get("roles", ())]
    verifiers = [item for item in participants if "verifier" in item.get("roles", ())]
    if not reviewers or not verifiers:
        raise RunError(
            "run_plan_invalid: review bridge requires reviewer and verifier participants"
        )
    now = _now()
    bundle_id = derive_deterministic_id(
        "bundle", {"run_id": run_id, "plan_id": plan_id, "contract_id": contract_id}
    )
    loop_id = derive_deterministic_id(
        "loop", {"run_id": run_id, "bundle_id": bundle_id, "contract_id": contract_id}
    )
    trace_id = derive_deterministic_id("trace", {"run_id": run_id, "loop_id": loop_id})
    report_id = derive_deterministic_id(
        "report", {"run_id": run_id, "loop_id": loop_id}
    )
    evidence_payload = canonical_json_bytes(
        {
            "run_id": run_id,
            "run_plan_id": plan_id,
            "run_plan_content_sha256": plan_digest,
            "sealed_sources": plan.get("sealed_sources", []),
        }
    )
    evidence_local = "review/evidence/sealed-input.json"
    evidence_full = f"runs/{run_id}/{evidence_local}"
    evidence_ref = _artifact_ref(evidence_local, "application/json", evidence_payload)
    reference_id = derive_deterministic_id(
        "ref", {"run_id": run_id, "kind": "sealed-input"}
    )
    bundle = {
        "schema_version": "1.0",
        "bundle_id": bundle_id,
        "bundle_version": 1,
        "created_at": now,
        "created_by": {"kind": "gigai", "id": "g43-bridge", "model_target": None},
        "name": "sealed-run-review",
        "question": "Are the sealed Run inputs consistent with the approved Gig requirements?",
        "references": [
            {
                "reference_id": reference_id,
                "role": "primary",
                "kind": "other",
                "path": evidence_local,
                "media_type": "application/json",
                "content_sha256": evidence_ref["content_sha256"],
                "canonical_sha256": evidence_ref["content_sha256"],
                "size_bytes": evidence_ref["size_bytes"],
                "provenance": {
                    "source_kind": "generated",
                    "locator": f"run-plan:{plan_id}",
                    "acquired_at": now,
                    "acquisition_method": "g43-run-bridge",
                    "source_revision": None,
                },
                "sensitivity": "restricted",
                "redaction_status": "approved_local_only",
            }
        ],
        "tool_requirements": None,
        "redaction_policy": {
            "mode": "local_only",
            "allowed_reference_ids": [reference_id],
            "policy_version": "g43-1",
            "detector_version": None,
        },
    }
    bundle_bytes = canonical_json_bytes(bundle)
    finding_ids = [
        derive_deterministic_id(
            "finding", {"run_id": run_id, "participant_id": item["participant_id"]}
        )
        for item in reviewers
    ]
    finding_records: list[tuple[str, bytes, str]] = []
    for finding_id, participant in zip(finding_ids, reviewers, strict=True):
        evaluator = {
            "evaluator_id": "evaluator_g43",
            "evaluator_version": "g43-1",
            "stage": "deterministic",
        }
        finding = {
            "schema_version": "1.0",
            "finding_id": finding_id,
            "finding_version": 1,
            "criterion_id": "criterion_requirements",
            "status": "open",
            "severity": "info",
            "title": "Run-scoped review is ready for evaluator execution",
            "description": "The sealed Run Plan has been handed to Run authority; provider-backed review remains pending.",
            "evidence": [
                {
                    "reference_id": reference_id,
                    "content_sha256": evidence_ref["content_sha256"],
                    "locator": "sealed_sources",
                    "quote": None,
                }
            ],
            "evaluator": evaluator,
            "source_evaluators": [evaluator],
            "trace_id": trace_id,
            "confidence": "1.0",
            "disagreement": {"present": False, "peer_finding_ids": [], "summary": None},
            "created_at": now,
        }
        data = canonical_json_bytes(finding)
        finding_records.append(
            (
                f"runs/{run_id}/review/findings/{finding_id}/v1-open.json",
                data,
                finding_id,
            )
        )
    trace_payload = canonical_json_bytes(
        {"run_id": run_id, "finding_ids": finding_ids, "plan_id": plan_id}
    )
    trace = {
        "schema_version": "1.0",
        "trace_id": trace_id,
        "trace_version": 1,
        "created_at": now,
        "bundle_id": bundle_id,
        "contract_id": contract_id,
        "run_id": run_id,
        "goal_id": None,
        "invocation_id": None,
        "events": [
            {
                "sequence": 1,
                "kind": "review_bridge_materialized",
                "payload_sha256": digest_imported_bytes(trace_payload),
                "evaluator_id": None,
            }
        ],
        "redaction_policy": "local_only",
        "variable_fields": ["created_at"],
    }
    trace_bytes = canonical_json_bytes(trace)
    verification_ids: list[str] = []
    verification_records: list[tuple[str, bytes]] = []
    for verifier in verifiers:
        verification_id = derive_deterministic_id(
            "verification",
            {"run_id": run_id, "participant_id": verifier["participant_id"]},
        )
        verification_ids.append(verification_id)
        outcomes = [
            {
                "finding_id": finding_id,
                "status": "unverified",
                "evidence_refs": [evidence_ref],
                "reason": "Verification is pending provider-backed execution.",
            }
            for finding_id in finding_ids
        ]
        record = {
            "schema_version": "1.0",
            "verification_id": verification_id,
            "run_id": run_id,
            "gig_id": resolved.gig_id,
            "bundle_id": bundle_id,
            "contract_id": contract_id,
            "verifier_participant_id": verifier["participant_id"],
            "verifier_target_id": verifier["model_target_id"],
            "source_finding_ids": finding_ids,
            "outcomes": outcomes,
            "created_at": now,
        }
        verification_records.append(
            (
                f"runs/{run_id}/review/verification/{verification_id}.json",
                canonical_json_bytes(record),
            )
        )
    adjudication_ids: list[str] = []
    adjudication_records: list[tuple[str, bytes]] = []
    adjudicate_required = any(
        item.get("phase") == "adjudicate" and item.get("required")
        for item in plan.get("phases", ())
        if isinstance(item, Mapping)
    )
    if adjudicate_required:
        adjudication_id = derive_deterministic_id(
            "adjudication", {"run_id": run_id, "loop_id": loop_id}
        )
        adjudication_ids.append(adjudication_id)
        adjudication = {
            "schema_version": "1.0",
            "adjudication_id": adjudication_id,
            "adjudication_version": 1,
            "created_at": now,
            "actor": {"kind": "gigai", "id": "g43-bridge", "model_target": None},
            "decisions": [
                {
                    "finding_id": finding_id,
                    "decision": "deferred",
                    "rationale": "Adjudication is pending independent evaluator evidence.",
                }
                for finding_id in finding_ids
            ],
        }
        adjudication_records.append(
            (
                f"runs/{run_id}/review/adjudications/{adjudication_id}.json",
                canonical_json_bytes(adjudication),
            )
        )
    report_base = {
        "schema_version": "1.1",
        "report_id": report_id,
        "report_version": 1,
        "created_at": now,
        "bundle_id": bundle_id,
        "contract_id": contract_id,
        "trace_ids": [trace_id],
        "finding_ids": finding_ids,
        "feedback_ids": [],
        "adjudication_ids": adjudication_ids,
        "verification_ids": verification_ids,
        "status": "incomplete",
        "human_report": _artifact_ref(
            f"review/reports/{report_id}.md",
            "text/markdown",
            canonicalize_evidence(
                f"# Review {loop_id}\n\nProvider-backed review and verification are pending.\n"
            ),
        ),
    }
    report_base["machine_report_sha256"] = digest_imported_bytes(
        canonical_json_bytes(report_base)
    )
    report_bytes = canonical_json_bytes(report_base)
    human_bytes = canonicalize_evidence(
        f"# Review {loop_id}\n\nProvider-backed review and verification are pending.\n"
    )
    loop = {
        "schema_version": "1.1",
        "loop_id": loop_id,
        "loop_version": 1,
        "run_id": run_id,
        "gig_id": resolved.gig_id,
        "bundle_id": bundle_id,
        "contract_id": contract_id,
        "state": "reviewing",
        "cycle_cap": 1,
        "cycle_count": 0,
        "stage_sequence": [{"state": "reviewing", "sequence": 1}],
        "finding_ids": finding_ids,
        "report_ids": [report_id],
        "feedback_ids": [],
        "adjudication_ids": adjudication_ids,
        "trace_ids": [trace_id],
        "verification_ids": verification_ids,
        "addressed_artifact_ids": [],
        "created_at": now,
        "updated_at": now,
    }
    loop_bytes = canonical_json_bytes(loop)
    records: list[JournalArtifact] = [
        JournalArtifact(evidence_full, evidence_payload),
        JournalArtifact(f"runs/{run_id}/review/bundle.json", bundle_bytes),
        *[JournalArtifact(path, data) for path, data, _finding_id in finding_records],
        JournalArtifact(f"runs/{run_id}/review/traces/{trace_id}.json", trace_bytes),
        *[JournalArtifact(path, data) for path, data in verification_records],
        *[JournalArtifact(path, data) for path, data in adjudication_records],
        JournalArtifact(f"runs/{run_id}/review/reports/{report_id}.json", report_bytes),
        JournalArtifact(f"runs/{run_id}/review/reports/{report_id}.md", human_bytes),
        JournalArtifact(f"runs/{run_id}/review/review-loop.json", loop_bytes),
    ]
    schemas = (
        ("review-bundle.schema.json", [bundle_bytes]),
        ("finding.schema.json", [data for _path, data, _id in finding_records]),
        ("trace.schema.json", [trace_bytes]),
        (
            "verification-record.schema.json",
            [data for _path, data in verification_records],
        ),
        ("adjudication.schema.json", [data for _path, data in adjudication_records]),
        ("report.schema.json", [report_bytes]),
        ("review-loop.schema.json", [loop_bytes]),
    )
    for schema_name, payloads in schemas:
        for payload in payloads:
            report = validate_serialized_contract(schema_name, payload)
            if not report.valid:
                raise RunError(
                    "run_review_bridge_invalid: "
                    + ",".join(item.code for item in report.findings)
                )
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
        transition="run_review_loop_materialized",
        body=f"Run {run_id} materialized its G43 review loop and verification bridge.",
        artifacts=tuple(records),
        front_matter={
            "gig_version": gig_version,
            "run_id": run_id,
            "goal_graph_sha256": None,
            "source_manifest_sha256": manifest_digest,
            "outcome": "SEALED",
            "actor": {"kind": "gigai", "id": "g43-bridge", "model_target": None},
            "parent_handoff_ids": [parent_handoff_id],
            "evidence": [
                _artifact_ref(
                    path,
                    "text/markdown" if path.endswith(".md") else "application/json",
                    data,
                )
                for path, data in [(item.path, item.content) for item in records]
            ],
        },
        observer=observer,
    )


def _mark_provider_review_running(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    manifest_digest: str,
    parent_handoff_id: str,
) -> JournalEntry:
    """Make the synchronous provider pass observable before invoking it.

    Provider review is not a Goal in the sealed offline Graph.  In particular,
    this update deliberately leaves the Graph's Goal records untouched rather
    than inventing a completed local-capability Goal for provider work.
    """

    details_path = resolved.path / "runs" / run_id / "run-details.json"
    details = parse_json_bytes(details_path.read_bytes())
    if not isinstance(details, dict):
        raise RunError("provider review Run details are unavailable")
    details["status"] = "running"
    details["execution_summary"] = (
        "Provider-backed document review is active; sealed offline Goals have not run."
    )
    details["finished_at"] = None
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
        transition="run_review_loop_materialized",
        body=f"Run {run_id} started its provider-backed document review.",
        artifacts=(
            JournalArtifact(
                f"runs/{run_id}/run-details.json", canonical_json_bytes(details)
            ),
        ),
        front_matter={
            "gig_version": gig_version,
            "run_id": run_id,
            "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)),
            "source_manifest_sha256": manifest_digest,
            "parent_handoff_ids": [parent_handoff_id],
            "outcome": "STARTED",
            "actor": {
                "kind": "gigai",
                "id": "g43.1-provider-review",
                "model_target": None,
            },
        },
    )


def _finish_provider_review(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    parent_handoff_id: str,
    run_plan_id: str,
    status: str,
    error: dict[str, object] | None = None,
) -> JournalEntry:
    """Terminalize only the opted-in provider-review lifecycle."""

    if status not in {"succeeded", "blocked", "failed", "interrupted"}:
        raise RunError("provider review terminal status is invalid")
    details_path = resolved.path / "runs" / run_id / "run-details.json"
    details = parse_json_bytes(details_path.read_bytes())
    if not isinstance(details, dict):
        raise RunError("provider review Run details are unavailable")
    target_after = _target_observation(resolved)
    target_bytes = canonical_json_bytes(target_after)
    target_ref = _artifact_ref(
        f"runs/{run_id}/target-after.json", "application/json", target_bytes
    )
    result = _provider_review_result(resolved, run_id, run_plan_id)
    if status == "succeeded" and (result is None or result.get("status") != "complete"):
        raise RunError("provider success requires its complete journaled result")
    evidence = _provider_review_evidence(resolved, run_id, run_plan_id, result)
    report_path = (
        f"runs/{run_id}/provider-reviews/{run_plan_id}/report.md"
        if result is not None
        else None
    )
    terminal_path = f"runs/{run_id}/terminal-handoff.md"
    terminal_bytes = canonicalize_evidence(
        f"Run {run_id} provider-review terminal status: {status}. "
        "Sealed offline Goals were not executed.\n"
    )
    terminal_ref = _artifact_ref(terminal_path, "text/markdown", terminal_bytes)
    details["status"] = status
    details["finished_at"] = _now()
    details["target_after"] = target_ref
    details["terminal_handoff"] = terminal_ref
    details["workpad_commit"] = _git(resolved.path, "rev-parse", "HEAD").stdout.strip()
    details["aggregate_usage"] = _aggregate_provider_usage(resolved, run_id)
    details["execution_summary"] = (
        f"Provider-backed document review terminalized as {status}; "
        "sealed offline Goals were not executed."
    )
    details["next_actions"] = _provider_review_next_actions(status, result, report_path)
    if error is not None:
        model_errors = details.get("model_errors")
        details["model_errors"] = [
            *(model_errors if isinstance(model_errors, list) else []),
            error,
        ]
    data = canonical_json_bytes(details)
    transition = (
        "run_succeeded"
        if status == "succeeded"
        else "run_interrupted"
        if status == "interrupted"
        else "run_failed"
    )
    outcome = "COMPLETE" if status == "succeeded" else status.upper()
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
        transition=transition,
        body=(
            f"Run {run_id} provider-backed document review terminalized as {status}."
            + (f" Report: {report_path}." if report_path is not None else "")
        ),
        artifacts=(
            JournalArtifact(f"runs/{run_id}/target-after.json", target_bytes),
            JournalArtifact(f"runs/{run_id}/run-details.json", data),
            JournalArtifact(terminal_path, terminal_bytes),
        ),
        front_matter={
            "gig_version": gig_version,
            "run_id": run_id,
            "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)),
            "parent_handoff_ids": [parent_handoff_id],
            "outcome": outcome,
            "actor": {
                "kind": "gigai",
                "id": "g43.1-provider-review",
                "model_target": None,
            },
            "evidence": [*evidence, terminal_ref],
            "usage": details["aggregate_usage"],
        },
    )


def _provider_review_error(exc: BaseException) -> dict[str, object]:
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or not code or not code.replace("_", "").isalnum():
        code = (
            "provider_review_interrupted"
            if not isinstance(exc, Exception)
            else "provider_review_exception"
        )
    return {
        "code": code,
        "message": "Provider-backed review did not complete; inspect its journaled evidence.",
        "retryable": False,
        "invocation_id": None,
    }


def _provider_review_terminal_status(
    resolved: ResolvedWorkpad, run_id: str, requested: str
) -> str:
    """Preserve the sealed target-observation boundary for provider Runs."""

    details = parse_json_bytes(
        (resolved.path / "runs" / run_id / "run-details.json").read_bytes()
    )
    if not isinstance(details, dict) or not isinstance(
        details.get("target_before"), dict
    ):
        raise RunError("provider review target-before evidence is unavailable")
    if _target_observation(resolved) != _read_artifact_json(
        resolved.path, details["target_before"]
    ):
        return "interrupted"
    return requested


def _provider_review_result(
    resolved: ResolvedWorkpad, run_id: str, run_plan_id: str
) -> dict[str, object] | None:
    """Read the one terminal provider result, refusing unsafe or foreign bytes."""

    path = _safe_provider_artifact_path(resolved, run_id, run_plan_id, "result.json")
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise RunError("provider review result is unavailable")
    try:
        result = parse_json_bytes(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise RunError("provider review result is malformed") from exc
    if (
        not isinstance(result, dict)
        or result.get("run_id") != run_id
        or result.get("run_plan_id") != run_plan_id
        or result.get("status") not in {"complete", "blocked"}
        or type(result.get("finding_count")) is not int
        or result["finding_count"] < 0
    ):
        raise RunError("provider review result is malformed or foreign")
    return result


def _provider_review_evidence(
    resolved: ResolvedWorkpad,
    run_id: str,
    run_plan_id: str,
    result: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    """Return bounded, terminal evidence rather than an untrusted tree walk."""

    paths: list[Path] = []
    if result is not None:
        for name in ("result.json", "report.json", "review-loop.json"):
            path = _safe_provider_artifact_path(resolved, run_id, run_plan_id, name)
            if path.is_symlink() or not path.is_file():
                raise RunError("provider review terminal evidence is incomplete")
            _require_journaled_provider_evidence(
                resolved, path, run_id, "g43.1-provider-review"
            )
            paths.append(path)
    paths.extend(
        path for path, _record in _provider_invocation_records(resolved, run_id)
    )
    refs: list[dict[str, object]] = []
    for path in paths:
        data = path.read_bytes()
        refs.append(
            _artifact_ref(
                path.relative_to(resolved.path).as_posix(),
                (
                    "text/markdown"
                    if path.suffix == ".md"
                    else "text/plain"
                    if path.suffix == ".txt"
                    else "application/json"
                ),
                data,
            )
        )
    return refs


def _aggregate_provider_usage(
    resolved: ResolvedWorkpad, run_id: str
) -> dict[str, object]:
    """Aggregate recorded invocation usage without converting unknown cost to zero."""

    usages = [
        record["usage"]
        for _path, record in _provider_invocation_records(resolved, run_id)
    ]

    def total(field: str) -> int | None:
        values = [usage.get(field) for usage in usages]
        return (
            sum(values)
            if values and all(type(value) is int and value >= 0 for value in values)
            else None
        )

    return {
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "total_tokens": total("total_tokens"),
        "cost": None,
        "currency": None,
        "cost_status": "unavailable" if usages else "not_applicable",
    }


def _provider_invocation_records(
    resolved: ResolvedWorkpad, run_id: str
) -> list[tuple[Path, dict[str, object]]]:
    root = resolved.path / "runs" / run_id / "model-invocations"
    _reject_symlinked_components(
        resolved.path, root, "provider invocation path is unsafe"
    )
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise RunError("provider invocation path is unsafe")
    records: list[tuple[Path, dict[str, object]]] = []
    for child in sorted(root.iterdir()):
        _reject_symlinked_components(
            resolved.path, child, "provider invocation path is unsafe"
        )
        if child.is_symlink() or not child.is_dir():
            raise RunError("provider invocation record is malformed")
        try:
            validate_entity_id(child.name, expected_prefix=EntityPrefix.INVOCATION)
        except ValueError as exc:
            raise RunError("provider invocation record is malformed") from exc
        path = child / "record.json"
        _reject_symlinked_components(
            resolved.path, path, "provider invocation path is unsafe"
        )
        if path.is_symlink() or not path.is_file():
            raise RunError("provider invocation record is missing")
        data = path.read_bytes()
        report = validate_model_invocation(data)
        try:
            record = parse_json_bytes(data)
        except (OSError, ValueError) as exc:
            raise RunError("provider invocation record is malformed") from exc
        if (
            not report.valid
            or not isinstance(record, dict)
            or record.get("run_id") != run_id
            or record.get("invocation_id") != child.name
            or not isinstance(record.get("usage"), dict)
        ):
            raise RunError("provider invocation record is malformed or foreign")
        _require_journaled_provider_evidence(
            resolved, path, run_id, "g18-model-execution"
        )
        records.append((path, record))
    return records


def _safe_provider_artifact_path(
    resolved: ResolvedWorkpad, run_id: str, run_plan_id: str, name: str
) -> Path:
    root = resolved.path / "runs" / run_id / "provider-reviews" / run_plan_id
    _reject_symlinked_components(
        resolved.path, root, "provider review evidence path is unsafe"
    )
    path = root / name
    _reject_symlinked_components(
        resolved.path, path, "provider review evidence path is unsafe"
    )
    return path


def _require_journaled_provider_evidence(
    resolved: ResolvedWorkpad, path: Path, run_id: str, actor_id: str
) -> None:
    """Authenticate bounded evidence against the journal commit that wrote it."""

    relative = path.relative_to(resolved.path).as_posix()
    data = path.read_bytes()
    commits = _git(
        resolved.path, "log", "--format=%H", "--", relative, check=False
    ).stdout.splitlines()
    for commit in commits:
        try:
            if _git_bytes(resolved.path, "show", f"{commit}:{relative}") != data:
                continue
        except subprocess.CalledProcessError:
            continue
        changed = _git(
            resolved.path, "show", "--format=", "--name-only", commit, check=False
        ).stdout.splitlines()
        for handoff in changed:
            if not handoff.startswith("handoffs/") or not handoff.endswith(".txt"):
                continue
            try:
                metadata, _body = parse_json_front_matter(
                    _git_bytes(resolved.path, "show", f"{commit}:{handoff}")
                )
            except (subprocess.CalledProcessError, ValueError):
                continue
            actor = metadata.get("actor")
            if (
                metadata.get("run_id") == run_id
                and metadata.get("gig_id") == resolved.gig_id
                and metadata.get("transition")
                in {"goal_completed", "goal_blocked", "goal_failed"}
                and isinstance(actor, Mapping)
                and actor.get("kind") == "gigai"
                and actor.get("id") == actor_id
            ):
                return
    raise RunError("provider review evidence is not authenticated by the journal")


def _provider_review_next_actions(
    status: str, result: Mapping[str, object] | None, report_path: str | None
) -> list[str]:
    actions = [f"Review provider report: {report_path}"] if report_path else []
    if status == "blocked":
        actions.append(
            "Repair the blocked review input and create a fresh sealed Run Plan."
        )
    elif status == "failed":
        actions.append(
            "Inspect journaled provider evidence before creating a fresh sealed Run Plan."
        )
    elif status == "interrupted":
        actions.append(
            "Target observation changed; reconcile it before creating a fresh sealed Run Plan."
        )
    elif result is not None and result.get("finding_count", 0) > 0:
        actions.append("Review findings; provider completion is not no-fix acceptance.")
    else:
        actions.append(
            "A no-fix decision, if appropriate, requires separate direct closeout."
        )
    return actions


def _execute_deterministic(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    target_before: dict[str, object],
    run_started_handoff_id: str,
    manifest_digest: str,
) -> JournalEntry:
    """Run the sealed graph with one deterministic Goal at a time."""
    run_dir = resolved.path / "runs" / run_id
    details_path = run_dir / "run-details.json"
    details = parse_json_bytes(details_path.read_bytes())
    if not isinstance(details, dict):
        raise _PreScheduleFailure("Run details are unavailable")
    graph_digest = digest_imported_bytes(canonical_json_bytes(graph))
    sealed_graph_bytes = (run_dir / "goal-graph.json").read_bytes()
    if digest_imported_bytes(sealed_graph_bytes) != graph_digest:
        raise _PreScheduleFailure("sealed Run Graph digest diverged before scheduling")
    manifest_bytes = (run_dir / "run-manifest.json").read_bytes()
    if digest_imported_bytes(manifest_bytes) != manifest_digest:
        raise _PreScheduleFailure(
            "sealed Run manifest digest diverged before scheduling"
        )
    manifest = parse_json_bytes(manifest_bytes)
    if (
        not isinstance(manifest, dict)
        or manifest.get("goal_graph", {}).get("content_sha256") != graph_digest
    ):
        raise _PreScheduleFailure("Run manifest does not pin the sealed Goal Graph")
    find_jobs_input = _read_find_jobs_run_input(resolved, run_id)
    model_target = (
        find_jobs_input.get("model_target")
        if isinstance(find_jobs_input, Mapping)
        else None
    )
    _validate_scheduler_policy(graph, model_target=model_target)
    goals = {goal["goal_id"]: goal for goal in graph["goals"]}
    goal_details = {goal["goal_id"]: goal for goal in details["goals"]}
    previous_handoff = run_started_handoff_id
    while True:
        ready = _ready_goals(graph, goal_details)
        if not ready:
            incomplete = [
                item
                for item in goal_details.values()
                if item["status"] not in {"complete", "failed", "blocked", "cancelled"}
            ]
            if incomplete:
                blocked = _blocked_by_terminal_outcome(graph, goal_details)
                if blocked:
                    for goal_id in blocked:
                        previous_handoff = _record_goal_terminal(
                            resolved,
                            run_id,
                            gig_version,
                            graph,
                            details,
                            goals[goal_id],
                            goal_details[goal_id],
                            "blocked",
                            previous_handoff,
                            "blocked_by_predecessor",
                        )
                    continue
                raise _PreScheduleFailure("sealed Goal Graph has no schedulable Goal")
            terminal_status = _terminal_status(goal_details)
            return _finish_run(
                resolved,
                run_id,
                gig_version,
                graph,
                details,
                terminal_status,
                previous_handoff,
                previous_handoff,
            )
        goal_id = ready[0]
        goal = goals[goal_id]
        detail = goal_details[goal_id]
        now = _now()
        detail.update({"status": "running", "started_at": now})
        _refresh_details(details, goal_details, graph, "running")
        started_bytes = canonical_json_bytes(details)
        started = record_transition(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
            transition="goal_started",
            body=f"Goal {goal_id} started sequential deterministic execution.",
            artifacts=(
                JournalArtifact(f"runs/{run_id}/run-details.json", started_bytes),
            ),
            front_matter=_goal_front_matter(
                gig_version,
                run_id,
                goal,
                graph_digest,
                manifest_digest,
                previous_handoff,
                "STARTED",
            ),
        )
        previous_handoff = started.handoff_id
        registered_binding = _registered_goal_binding(graph, goal)
        try:
            evidence = _execute_goal(
                resolved,
                run_id,
                goal_id,
                target_before,
                goal=goal,
                graph=graph,
                manifest_digest=manifest_digest,
                started_at=now,
            )
            if registered_binding is not None:
                _apply_registered_receipt_to_detail(
                    resolved, run_id, goal, detail, expected_status=NodeStatus.COMPLETE.value
                )
                evidence = detail["evidence"][0]
            else:
                detail.update(
                    {
                        "status": "complete",
                        "outcome": "COMPLETE",
                        "finished_at": _now(),
                        "evidence": [evidence],
                    }
                )
            _refresh_details(details, goal_details, graph, "running")
            completed_bytes = canonical_json_bytes(details)
            goal_evidence = [
                item for item in detail.get("evidence", []) if isinstance(item, Mapping)
            ]
            completed = record_transition(
                workpad=resolved.path,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
                handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
                transition="goal_completed",
                body=f"Goal {goal_id} completed with outcome COMPLETE.",
                artifacts=(
                    JournalArtifact(f"runs/{run_id}/run-details.json", completed_bytes),
                    *_evidence_artifacts(resolved, goal_evidence),
                ),
                front_matter=_goal_front_matter(
                    gig_version,
                    run_id,
                    goal,
                    graph_digest,
                    manifest_digest,
                    previous_handoff,
                    "COMPLETE",
                    goal_evidence,
                ),
            )
            previous_handoff = completed.handoff_id
        except _RunInterrupted:
            raise
        except Exception as exc:
            if registered_binding is not None and _node_receipt_exists(
                resolved, run_id, goal
            ):
                _apply_registered_receipt_to_detail(
                    resolved, run_id, goal, detail, expected_status=NodeStatus.FAILED.value
                )
            else:
                detail.update(
                    {
                        "status": "failed",
                        "errors": [
                            {
                                "code": "goal_execution_failed",
                                "message": str(exc),
                                "retryable": False,
                                "invocation_id": None,
                            }
                        ],
                        "finished_at": _now(),
                    }
                )
            _refresh_details(details, goal_details, graph, "failed")
            failed_bytes = canonical_json_bytes(details)
            goal_evidence = [
                item for item in detail.get("evidence", []) if isinstance(item, Mapping)
            ]
            failed = record_transition(
                workpad=resolved.path,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
                handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
                transition="goal_failed",
                body=f"Goal {goal_id} failed during deterministic execution.",
                artifacts=(
                    JournalArtifact(f"runs/{run_id}/run-details.json", failed_bytes),
                    *_evidence_artifacts(resolved, goal_evidence),
                ),
                front_matter=_goal_front_matter(
                    gig_version,
                    run_id,
                    goal,
                    graph_digest,
                    manifest_digest,
                    previous_handoff,
                    "FAILED",
                    goal_evidence,
                ),
            )
            terminal = _finish_run(
                resolved,
                run_id,
                gig_version,
                graph,
                details,
                "failed",
                failed.handoff_id,
                previous_handoff,
            )
            return terminal


def _registered_goal_binding(
    graph: Mapping[str, object], goal: Mapping[str, object]
) -> object | None:
    executor = goal.get("executor")
    if not isinstance(executor, Mapping):
        return None
    if executor.get("kind") != "local_capability":
        return None
    return lookup_graph_node(
        graph.get("graph_id"),
        graph.get("graph_version"),
        goal.get("slug"),
        executor.get("capability"),
    )


def _registered_node_paths(
    resolved: ResolvedWorkpad, run_id: str, goal: Mapping[str, object]
) -> tuple[Path, Path]:
    slug = goal.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", slug):
        raise RunError("registered node slug is invalid")
    run_root = resolved.path / "runs" / run_id
    output = run_root / "outputs" / f"{slug}.json"
    receipt = run_root / "receipts" / f"{slug}.json"
    _reject_symlinked_components(resolved.path, output, "registered node output path is unsafe")
    _reject_symlinked_components(resolved.path, receipt, "registered node receipt path is unsafe")
    return output, receipt


def _node_receipt_exists(
    resolved: ResolvedWorkpad, run_id: str, goal: Mapping[str, object]
) -> bool:
    try:
        _output_path, receipt_path = _registered_node_paths(resolved, run_id, goal)
    except RunError:
        return False
    return receipt_path.is_file() and not receipt_path.is_symlink()


def _read_registered_receipt(
    resolved: ResolvedWorkpad, run_id: str, goal: Mapping[str, object]
) -> tuple[NodeReceipt, dict[str, object]]:
    _output_path, receipt_path = _registered_node_paths(resolved, run_id, goal)
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise RunError("registered node receipt is unavailable")
    try:
        receipt = NodeReceipt.from_json(parse_json_bytes(receipt_path.read_bytes()))
    except Exception as exc:
        raise RunError("registered node receipt is invalid") from exc
    return receipt, _artifact_ref(
        receipt_path.relative_to(resolved.path).as_posix(),
        "application/json",
        receipt_path.read_bytes(),
    )


def _apply_registered_receipt_to_detail(
    resolved: ResolvedWorkpad,
    run_id: str,
    goal: Mapping[str, object],
    detail: dict[str, object],
    *,
    expected_status: str,
) -> None:
    receipt, receipt_ref = _read_registered_receipt(resolved, run_id, goal)
    if receipt.status.value != expected_status:
        raise RunError("registered node receipt status is inconsistent")
    projected = receipt.to_goal_details()
    projected_evidence = [
        item for item in projected.get("evidence", []) if isinstance(item, Mapping)
    ]
    if not any(item.get("path") == receipt_ref.get("path") for item in projected_evidence):
        projected_evidence.append(receipt_ref)
    projected["evidence"] = projected_evidence
    detail.clear()
    detail.update(projected)


def _evidence_artifacts(
    resolved: ResolvedWorkpad, refs: list[Mapping[str, object]]
) -> tuple[JournalArtifact, ...]:
    artifacts: list[JournalArtifact] = []
    seen: set[str] = set()
    for ref in refs:
        path_value = ref.get("path")
        if not isinstance(path_value, str) or path_value in seen:
            continue
        seen.add(path_value)
        path = resolved.path / path_value
        _reject_symlinked_components(resolved.path, path, "node evidence path is unsafe")
        if not path.is_file() or path.is_symlink():
            raise RunError("node evidence is unavailable")
        data = path.read_bytes()
        if digest_imported_bytes(data) != ref.get("content_sha256"):
            raise RunError("node evidence digest diverged")
        artifacts.append(JournalArtifact(path_value, data))
    return tuple(artifacts)


def _effective_goal_effects(
    goal: Mapping[str, object], *, model_target: object | None = None
) -> frozenset[str]:
    """Return the effects admitted for this invocation of one Goal."""

    effects = goal.get("effects", ())
    if not isinstance(effects, (list, tuple, set, frozenset)):
        raise _PreScheduleFailure("Goal effect set is malformed")
    if any(type(effect) is not str or not effect for effect in effects):
        raise _PreScheduleFailure("Goal effect set is malformed")
    effective = frozenset(effects)
    if (
        goal.get("slug") == "assess"
        and str(model_target) == ModelTarget.OLLAMA_LOCAL.value
    ):
        return frozenset(ASSESS_LOCAL_EFFECTS)
    return effective


def _validate_scheduler_policy(
    graph: dict[str, object], *, model_target: object | None = None
) -> None:
    budget = graph.get("aggregate_budget", {})
    if budget.get("max_parallel_goals") != 1:
        raise _PreScheduleFailure("parallel Goal capacity is unsupported by G14")
    if graph.get("failure_policy") != "fail_gig":
        raise _PreScheduleFailure("failure policy is unsupported by G14")
    if any(edge.get("kind") == "recovery" for edge in graph.get("edges", [])):
        raise _PreScheduleFailure("recovery edges are unsupported by G14")
    if any(
        edge.get("kind") == "dependency" and not edge.get("automatic")
        for edge in graph.get("edges", [])
    ):
        raise _PreScheduleFailure("manual dependency edges are unsupported by G14")
    for goal in graph.get("goals", []):
        if goal.get("activation") != "automatic":
            raise _PreScheduleFailure("operator-gated Goals are unsupported by G14")
        executor = goal.get("executor", {})
        if not isinstance(executor, Mapping):
            raise _PreScheduleFailure("Goal executor is unsupported by G14")
        if executor.get("kind") != "local_capability":
            raise _PreScheduleFailure("Goal executor is unsupported by G14")
        capability = executor.get("capability")
        binding = lookup_graph_node(
            graph.get("graph_id"),
            graph.get("graph_version"),
            goal.get("slug"),
            capability,
        )
        if binding is None:
            if capability not in {"gigai.offline", "gigai.deterministic"}:
                raise _PreScheduleFailure("Goal executor is unsupported by G14")
            if goal.get("effects") != ["write_workpad"]:
                raise _PreScheduleFailure("Goal declares an unsafe effect set")
            continue
        try:
            declared = frozenset(goal.get("effects", ()))
            effective = _effective_goal_effects(goal, model_target=model_target)
        except (TypeError, _PreScheduleFailure) as exc:
            raise _PreScheduleFailure("Goal declares a malformed effect set") from exc
        if not declared.issubset(binding.declared_effects):
            raise _PreScheduleFailure("Goal declares an undeclared effect")
        if not effective.issubset(binding.declared_effects):
            raise _PreScheduleFailure("Goal declares an unsafe effect set")


def _ready_goals(
    graph: dict[str, object], details: dict[str, dict[str, object]]
) -> list[str]:
    edges = [
        edge for edge in graph.get("edges", []) if edge.get("kind") == "dependency"
    ]
    entries = set(graph.get("entry_goal_ids", []))
    ready = []
    for goal in graph.get("goals", []):
        goal_id = goal["goal_id"]
        if (
            details[goal_id]["status"] != "pending"
            and details[goal_id]["status"] != "ready"
        ):
            continue
        incoming = [edge for edge in edges if edge.get("to_goal_id") == goal_id]
        if goal_id not in entries and not incoming:
            continue
        if all(
            details[edge["from_goal_id"]]["status"] == "complete"
            and details[edge["from_goal_id"]].get("outcome")
            in edge.get("on_outcomes", [])
            for edge in incoming
        ):
            ready.append(goal_id)
    return sorted(ready)


def _terminal_status(details: dict[str, dict[str, object]]) -> str:
    return aggregate_status(item.get("status", "pending") for item in details.values())


def _critical_path(graph: dict[str, object]) -> list[str]:
    goals = {goal["goal_id"] for goal in graph.get("goals", [])}
    incoming = {goal_id: [] for goal_id in goals}
    for edge in graph.get("edges", []):
        if edge.get("kind") == "dependency":
            incoming.setdefault(edge["to_goal_id"], []).append(edge["from_goal_id"])

    def paths(goal_id: str, seen: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
        if goal_id in seen:
            return []
        parents = incoming.get(goal_id, [])
        if not parents:
            return [(goal_id,)]
        candidates = []
        for parent in parents:
            candidates.extend(paths(parent, seen + (goal_id,)))
        return [path + (goal_id,) for path in candidates]

    terminals = graph.get("terminal_goal_ids", []) or sorted(goals)
    candidates = [path for terminal in terminals for path in paths(terminal)]
    if not candidates:
        return []
    return list(min(candidates, key=lambda path: (-len(path), path)))


def _blocked_by_terminal_outcome(
    graph: dict[str, object], details: dict[str, dict[str, object]]
) -> list[str]:
    blocked = []
    for edge in graph.get("edges", []):
        if edge.get("kind") != "dependency":
            continue
        source = details[edge["from_goal_id"]]
        target = details[edge["to_goal_id"]]
        if source["status"] in {
            "failed",
            "blocked",
            "cancelled",
            "complete",
        } and target["status"] in {"pending", "ready"}:
            if source["status"] != "complete" or source.get("outcome") not in edge.get(
                "on_outcomes", []
            ):
                blocked.append(edge["to_goal_id"])
    return sorted(set(blocked))


def _read_find_jobs_run_input(
    resolved: ResolvedWorkpad, run_id: str
) -> dict[str, object] | None:
    path = resolved.path / "runs" / run_id / "sealed" / "find-jobs-run-input.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        payload = parse_json_bytes(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise _PreScheduleFailure("sealed find-jobs Run input is invalid") from exc
    if not isinstance(payload, dict):
        raise _PreScheduleFailure("sealed find-jobs Run input is invalid")
    return payload


def _node_model_target(run_input: Mapping[str, object] | None) -> ModelTarget:
    value = run_input.get("model_target") if run_input is not None else None
    try:
        return ModelTarget(str(value))
    except ValueError:
        return ModelTarget.OLLAMA_LOCAL


def _build_node_context(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    graph: Mapping[str, object],
    goal: Mapping[str, object],
    target_before: Mapping[str, object],
    manifest_digest: str,
    run_input: Mapping[str, object] | None,
) -> NodeContext:
    graph_id = graph.get("graph_id")
    graph_version = graph.get("graph_version")
    goal_slug = goal.get("slug")
    if not isinstance(graph_id, str) or not isinstance(graph_version, int) or not isinstance(goal_slug, str):
        raise RunError("registered node identity is unavailable")
    operation_key = (
        run_input.get("operation_key") if run_input is not None else None
    )
    if not isinstance(operation_key, str) or not operation_key:
        operation_key = f"find-jobs:{goal_slug}:{run_id}"
    observation_digest = target_before.get("observation_sha256")
    if not isinstance(observation_digest, str):
        raise RunError("registered node target observation is unavailable")
    consent_path = resolved.path / "runs" / run_id / "operator-consent.json"
    consent_ref = (
        consent_path.relative_to(resolved.path).as_posix()
        if consent_path.is_file() and not consent_path.is_symlink()
        else "none"
    )
    return NodeContext(
        run_id=run_id,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        graph_id=graph_id,
        graph_version=graph_version,
        goal_slug=goal_slug,
        manifest_digest=manifest_digest,
        operation_key=operation_key,
        target_observation_digest=observation_digest,
        workpad_path=str(resolved.path),
        redeemed_consent_ref=consent_ref,
        model_target=_node_model_target(run_input),
    )


def _sealed_node_input(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    goal: Mapping[str, object],
    run_input: Mapping[str, object] | None,
) -> object:
    """Build the frozen Scout DTO for one registered goal.

    A graph may provide an explicit ``input`` object in tests or in a future
    graph compiler.  The functional graph instead derives assess/present input
    from the prior sealed node output and the immutable Run input.
    """

    slug = goal.get("slug")
    explicit = goal.get("input")
    if isinstance(explicit, Mapping):
        if slug == "acquire":
            return AcquireInput.from_json(dict(explicit))
        if slug == "assess":
            return AssessInput.from_json(dict(explicit))
        if slug == "present":
            return PresentInput.from_json(dict(explicit))
        return dict(explicit)
    if run_input is None:
        return {}
    if slug == "acquire":
        payload = {
            "schema_version": "scout-find-jobs-acquire-input:1",
            "config": run_input.get("config"),
            "config_digest": run_input.get("config_digest"),
            "prior_batch_digest": run_input.get("prior_batch_digest"),
            "rows": run_input.get("rows", []),
            "selection_cap": run_input.get("selection_cap"),
            "selection_rule": run_input.get("selection_rule"),
        }
        return AcquireInput.from_json(payload)
    if slug == "assess":
        acquire_path = resolved.path / "runs" / run_id / "outputs" / "acquire.json"
        if acquire_path.is_symlink() or not acquire_path.is_file():
            raise RunError("registered assess input is unavailable")
        try:
            acquire = AcquireOutput.from_json(parse_json_bytes(acquire_path.read_bytes()))
            pinned = PinnedResume.from_json(run_input["pinned_resume"])
        except Exception as exc:
            raise RunError("registered assess input is invalid") from exc
        reasons: dict[str, str] = {}
        outcomes = {
            row.posting.normalized_url: row.outcome.value for row in acquire.rows
        }
        for posting in acquire.selected_postings:
            reason = outcomes.get(posting.normalized_url)
            if reason not in {
                SelectionReasonCode.NEW.value,
                SelectionReasonCode.EDITED.value,
            }:
                reason = SelectionReasonCode.NEW.value
            reasons[posting.normalized_url] = reason
        payload = {
            "schema_version": "scout-find-jobs-assess-input:1",
            "acquire_batch_ref": acquire.batch_ref,
            "acquire_output_digest": acquire.digest(),
            "selected_postings": [item.to_json() for item in acquire.selected_postings],
            "selection_cap": run_input.get("selection_cap"),
            "selection_reasons": reasons,
            "pinned_resume": pinned.to_json(),
            "target": run_input.get("target", str(resolved.target_root)),
            "model_target": run_input.get("model_target"),
            "answer_association_version": "scout-answer-association:1",
        }
        try:
            return AssessInput.from_json(payload)
        except Exception as exc:
            raise RunError("registered assess input is invalid") from exc
    if slug == "present":
        outputs_dir = resolved.path / "runs" / run_id / "outputs"
        receipts_dir = resolved.path / "runs" / run_id / "receipts"
        receipts: list[NodeReceipt] = []
        for prior_slug in ("acquire", "assess"):
            receipt_path = receipts_dir / f"{prior_slug}.json"
            if receipt_path.is_symlink() or not receipt_path.is_file():
                continue
            try:
                receipt = NodeReceipt.from_json(parse_json_bytes(receipt_path.read_bytes()))
            except Exception as exc:
                raise RunError("registered present input is invalid") from exc
            receipts.append(receipt)
        batch_ref = ""
        acquire_path = outputs_dir / "acquire.json"
        if acquire_path.is_file() and not acquire_path.is_symlink():
            try:
                batch_ref = AcquireOutput.from_json(
                    parse_json_bytes(acquire_path.read_bytes())
                ).batch_ref
            except Exception as exc:
                raise RunError("registered present input is invalid") from exc
        assessment_ref = (
            "runs/" + run_id + "/outputs/assess.json"
            if (outputs_dir / "assess.json").is_file()
            else None
        )
        return PresentInput(batch_ref, assessment_ref, tuple(receipts))
    return dict(run_input)


def _node_output_bytes(output: object) -> bytes:
    to_json = getattr(output, "to_json", None)
    value = to_json() if callable(to_json) else output
    if not isinstance(value, (Mapping, list, tuple, str, int, float, bool, type(None))):
        raise RunError("registered node output is not serializable")
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise RunError("registered node output is not serializable") from exc


def _registered_producer(
    binding: object, context: NodeContext
) -> Producer:
    capability = getattr(binding, "capability", None)
    if not isinstance(capability, str) or not capability:
        raise RunError("registered node capability is invalid")
    return Producer(
        callable=capability,
        version="1",
        actor="scheduler",
        model_target=context.model_target,
        adapter="local_capability",
    )


_MAX_FAILURE_MESSAGE = 300


def _redacted_failure_message(exc: BaseException) -> str:
    """A bounded, content-free failure message: exception class + str(exc).

    U21: the receipt and run-details goal errors must carry *something*
    specific enough to diagnose a run without ever risking resume text,
    posting text, or secrets in a record that may be shared/inspected. The
    exception's own message is the closest thing to "specific" that is safe
    to keep here — this call site never sees raw model output or private
    record bytes, only exceptions raised by parsing/adapter code — and it is
    hard-truncated regardless.
    """
    exc_class = type(exc).__name__
    detail = str(exc).strip().replace("\n", " ")
    message = f"{exc_class}: {detail}" if detail else exc_class
    if len(message) > _MAX_FAILURE_MESSAGE:
        message = message[: _MAX_FAILURE_MESSAGE - 1].rstrip() + "…"
    return message


def _write_node_failure_log(
    resolved: ResolvedWorkpad, run_id: str, goal: Mapping[str, object], exc: BaseException
) -> None:
    """Write the full traceback to ``runs/<run_id>/logs/<goal_slug>.log`` (U21).

    This is the durable, unredacted diagnostic counterpart to the bounded
    receipt/goal-error message: local-only, never surfaced through the API
    or UI, so it may hold the full exception chain and traceback.
    """
    slug = goal.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", slug):
        return
    log_path = resolved.path / "runs" / run_id / "logs" / f"{slug}.log"
    try:
        _reject_symlinked_components(resolved.path, log_path, "registered node log path is unsafe")
        log_path.parent.mkdir(mode=0o700, exist_ok=True)
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"--- {_now()} ---\n{rendered}\n")
    except OSError:
        pass


def _write_registered_failure_receipt(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    goal: Mapping[str, object],
    context: NodeContext,
    binding: object,
    started_at: str,
    exc: BaseException | None = None,
) -> None:
    _output_path, receipt_path = _registered_node_paths(resolved, run_id, goal)
    message = _redacted_failure_message(exc) if exc is not None else "registered node execution failed"
    receipt = NodeReceipt(
        goal_id=str(goal.get("goal_id")),
        goal_version=int(goal.get("goal_version", 1)),
        executor=str(getattr(binding, "capability", "local_capability")),
        node_slug=str(goal.get("slug")),
        operation_key=context.operation_key,
        status=NodeStatus.FAILED,
        outcome="FAILED",
        errors=(GoalError("node_execution_failed", message, False, None),),
        evidence=(),
        producer=_registered_producer(binding, context),
        usage=None,
        started_at=started_at,
        finished_at=_now(),
        failure=NodeFailure("node_execution_failed", message),
    )
    receipt_bytes = canonical_json_bytes(receipt.to_json())
    receipt_path.parent.mkdir(mode=0o700, exist_ok=True)
    receipt_path.write_bytes(receipt_bytes)


def _execute_goal(
    resolved: ResolvedWorkpad,
    run_id: str,
    goal_id: str,
    target_before: dict[str, object],
    *,
    goal: Mapping[str, object] | None = None,
    graph: Mapping[str, object] | None = None,
    manifest_digest: str | None = None,
    started_at: str | None = None,
) -> dict[str, object]:
    """Execute one Goal, dispatching only an explicitly registered binding."""

    if goal is None or graph is None:
        run_dir = resolved.path / "runs" / run_id
        evidence_path = run_dir / "evidence" / f"{goal_id}.txt"
        evidence_path.parent.mkdir(mode=0o700, exist_ok=True)
        evidence = canonicalize_evidence(f"gigai-offline-ok:{goal_id}\n")
        evidence_path.write_bytes(evidence)
        if _target_observation(resolved) != target_before:
            raise _RunInterrupted("target changed during deterministic execution")
        return _artifact_ref(
            evidence_path.relative_to(resolved.path).as_posix(), "text/plain", evidence
        )

    binding = _registered_goal_binding(graph, goal)
    if binding is None:
        run_dir = resolved.path / "runs" / run_id
        evidence_path = run_dir / "evidence" / f"{goal_id}.txt"
        evidence_path.parent.mkdir(mode=0o700, exist_ok=True)
        evidence = canonicalize_evidence(f"gigai-offline-ok:{goal_id}\n")
        evidence_path.write_bytes(evidence)
        if _target_observation(resolved) != target_before:
            raise _RunInterrupted("target changed during deterministic execution")
        return _artifact_ref(
            evidence_path.relative_to(resolved.path).as_posix(), "text/plain", evidence
        )

    run_input = _read_find_jobs_run_input(resolved, run_id)
    context = _build_node_context(
        resolved=resolved,
        run_id=run_id,
        graph=graph,
        goal=goal,
        target_before=target_before,
        manifest_digest=manifest_digest or "",
        run_input=run_input,
    )
    started = started_at or _now()
    try:
        node_input = _sealed_node_input(
            resolved=resolved, run_id=run_id, goal=goal, run_input=run_input
        )
        output = getattr(binding, "callable")(context, node_input)
        output_bytes = _node_output_bytes(output)
        output_path, receipt_path = _registered_node_paths(resolved, run_id, goal)
        output_ref = _artifact_ref(
            output_path.relative_to(resolved.path).as_posix(),
            "application/json",
            output_bytes,
        )
        output_path.parent.mkdir(mode=0o700, exist_ok=True)
        output_path.write_bytes(output_bytes)
        if _target_observation(resolved) != target_before:
            raise _RunInterrupted("target changed during deterministic execution")
        usage = getattr(output, "usage", None)
        if not isinstance(usage, UsageBlock):
            usage = None
        producer = getattr(output, "producer", None)
        if not isinstance(producer, Producer):
            producer = _registered_producer(binding, context)
        receipt = NodeReceipt(
            goal_id=str(goal.get("goal_id")),
            goal_version=int(goal.get("goal_version", 1)),
            executor=str(getattr(binding, "capability")),
            node_slug=str(goal.get("slug")),
            operation_key=context.operation_key,
            status=NodeStatus.COMPLETE,
            outcome="COMPLETE",
            errors=(),
            evidence=(
                # The output ref is the authenticated node evidence.  The
                # receipt ref is added to goal_details after serialization so
                # it can carry its own non-recursive digest.
                ArtifactRef(
                    str(output_ref["path"]),
                    str(output_ref["content_sha256"]),
                    str(output_ref["media_type"]),
                    int(output_ref["size_bytes"]),
                ),
            ),
            producer=producer,
            usage=usage,
            started_at=started,
            finished_at=_now(),
            failure=None,
        )
        receipt_bytes = canonical_json_bytes(receipt.to_json())
        receipt_path.parent.mkdir(mode=0o700, exist_ok=True)
        receipt_path.write_bytes(receipt_bytes)
        return output_ref
    except _RunInterrupted:
        raise
    except Exception as exc:
        _write_node_failure_log(resolved, run_id, goal, exc)
        try:
            _write_registered_failure_receipt(
                resolved=resolved,
                run_id=run_id,
                goal=goal,
                context=context,
                binding=binding,
                started_at=started,
                exc=exc,
            )
        except Exception:
            pass
        raise RunError("registered node execution failed") from exc


def _goal_front_matter(
    gig_version: int,
    run_id: str,
    goal: dict[str, object],
    graph_digest: str,
    manifest_digest: str | None,
    parent: str,
    outcome: str,
    evidence: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "gig_version": gig_version,
        "run_id": run_id,
        "goal_id": goal["goal_id"],
        "goal_version": goal["goal_version"],
        "goal_graph_sha256": graph_digest,
        "source_manifest_sha256": manifest_digest,
        "parent_handoff_ids": [parent],
        "outcome": outcome,
        "evidence": evidence or [],
        "usage": dict(_ZERO_USAGE),
        "actor": {"kind": "gigai", "id": "deterministic", "model_target": None},
    }


def _refresh_details(
    details: dict[str, object],
    goal_details: dict[str, dict[str, object]],
    graph: dict[str, object],
    status: str,
) -> None:
    details["status"] = status
    details["critical_path"] = _critical_path(graph)
    details["goals"] = list(goal_details.values())
    details["goal_sets"] = {
        key: []
        for key in (
            "pending",
            "ready",
            "active",
            "complete",
            "failed",
            "blocked",
            "gated",
            "cancelled",
        )
    }
    for goal_id, goal in goal_details.items():
        state = goal["status"]
        aggregate = "active" if state in {"running", "verifying"} else state
        if aggregate in details["goal_sets"]:
            details["goal_sets"][aggregate].append(goal_id)


def _record_goal_terminal(
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    details: dict[str, object],
    goal: dict[str, object],
    detail: dict[str, object],
    state: str,
    parent: str,
    outcome: str,
) -> str:
    detail.update(
        {
            "status": state,
            "finished_at": _now(),
            "errors": [
                {
                    "code": "blocked_by_predecessor",
                    "message": "predecessor outcome is not accepted",
                    "retryable": False,
                    "invocation_id": None,
                }
            ],
        }
    )
    goal_details = {item["goal_id"]: item for item in details["goals"]}
    _refresh_details(details, goal_details, graph, "blocked")
    entry = record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
        transition="goal_blocked",
        body=f"Goal {goal['goal_id']} was blocked by a predecessor outcome.",
        artifacts=(
            JournalArtifact(
                f"runs/{run_id}/run-details.json", canonical_json_bytes(details)
            ),
        ),
        front_matter=_goal_front_matter(
            gig_version,
            run_id,
            goal,
            digest_imported_bytes(canonical_json_bytes(graph)),
            None,
            parent,
            outcome,
        ),
    )
    return entry.handoff_id


def _finish_run(
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    details: dict[str, object],
    status: str,
    parent: str,
    previous: str,
) -> JournalEntry:
    target_after = _target_observation(resolved)
    target_before = details["target_before"]
    if target_after != _read_artifact_json(resolved.path, target_before):
        raise _RunInterrupted("target changed before Run terminalization")
    target_bytes = canonical_json_bytes(target_after)
    target_ref = _artifact_ref(
        f"runs/{run_id}/target-after.json", "application/json", target_bytes
    )
    terminal_path = f"runs/{run_id}/terminal-handoff.md"
    terminal_bytes = canonicalize_evidence(f"Run {run_id} terminal status: {status}.\n")
    terminal_ref = _artifact_ref(terminal_path, "text/markdown", terminal_bytes)
    details["status"] = status
    details["finished_at"] = _now()
    details["target_after"] = target_ref
    details["terminal_handoff"] = terminal_ref
    details["workpad_commit"] = _git(resolved.path, "rev-parse", "HEAD").stdout.strip()
    details["execution_summary"] = (
        f"Sequential deterministic scheduler completed with status {status}."
    )
    data = canonical_json_bytes(details)
    transition = "run_succeeded" if status == "succeeded" else "run_failed"
    entry = record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
        transition=transition,
        body=f"Run {run_id} terminalized with status {status}.",
        artifacts=(
            JournalArtifact(f"runs/{run_id}/target-after.json", target_bytes),
            JournalArtifact(f"runs/{run_id}/run-details.json", data),
            JournalArtifact(terminal_path, terminal_bytes),
        ),
        front_matter={
            "gig_version": gig_version,
            "run_id": run_id,
            "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)),
            "parent_handoff_ids": [previous],
            "outcome": "COMPLETE" if status == "succeeded" else "FAILED",
            "actor": {"kind": "gigai", "id": "scheduler", "model_target": None},
        },
    )
    return entry


def _recover_proposal_run_terminal(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    parent_handoff_id: str,
    status: str,
) -> JournalEntry:
    """Terminalize a proposal Run after result evidence already committed.

    This narrow recovery preserves the domain Goal/result state and records an
    interrupted owning Run when target observation or journal publication loses
    the normal scheduler finish race.  It intentionally never emits another
    Goal transition or overwrites an already terminal Run.
    """

    def operation(writer: object) -> JournalEntry:
        committed = _git_bytes(writer.root, "show", f"HEAD:runs/{run_id}/run-details.json")
        details = parse_json_bytes(committed)
        if not isinstance(details, dict):
            raise RunError("proposal_run_recovery_refused: Run details are malformed")
        if details.get("status") not in {"preparing", "running"}:
            existing = _latest_terminal_entry(resolved, run_id)
            if existing is not None:
                return existing
            raise RunError("proposal_run_recovery_refused: Run terminal history is unavailable")
        terminal_path = f"runs/{run_id}/terminal-handoff.md"
        terminal_bytes = canonicalize_evidence(
            f"Run {run_id} terminal status: {status}; proposal result evidence preserved.\n"
        )
        terminal_ref = _artifact_ref(terminal_path, "text/markdown", terminal_bytes)
        details["status"] = status
        details["finished_at"] = _now()
        details["target_after"] = None
        details["terminal_handoff"] = terminal_ref
        details["workpad_commit"] = _git(writer.root, "rev-parse", "HEAD").stdout.strip()
        details["execution_summary"] = (
            "Proposal result evidence was committed, but Run terminalization was interrupted; "
            "no Goal transition was repeated."
        )
        data = canonical_json_bytes(details)
        return writer.record(
            JournalTransition(
                _new_id(EntityPrefix.HANDOFF, uuid.uuid4),
                "run_interrupted",
                f"Run {run_id} was interrupted after proposal result evidence publication.",
                (
                    JournalArtifact(f"runs/{run_id}/run-details.json", data),
                    JournalArtifact(terminal_path, terminal_bytes),
                ),
                {
                    "gig_version": gig_version,
                    "run_id": run_id,
                    "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)),
                    "parent_handoff_ids": [parent_handoff_id],
                    "outcome": "INTERRUPTED",
                    "actor": {"kind": "gigai", "id": "scheduler", "model_target": None},
                },
            ),
            allow_artifact_replacement=True,
        )

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=operation,
    )


def _read_artifact_json(root: Path, ref: dict[str, object]) -> object:
    path = root / str(ref["path"])
    return parse_json_bytes(path.read_bytes())


def _worker_entry(
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    target_before: dict[str, object],
    run_started_handoff_id: str,
    manifest_digest: str,
) -> None:
    try:
        _execute_deterministic(
            resolved=resolved,
            run_id=run_id,
            gig_version=gig_version,
            graph=graph,
            target_before=target_before,
            run_started_handoff_id=run_started_handoff_id,
            manifest_digest=manifest_digest,
        )
    except _PreScheduleFailure as exc:
        try:
            _record_preschedule_failure(
                resolved, run_id, gig_version, graph, str(exc), run_started_handoff_id
            )
        except BaseException:
            pass
        return
    except _RunInterrupted:
        try:
            _mark_interrupted(resolved, run_id, gig_version, graph)
        except BaseException:
            pass
        return
    except BaseException:
        # Detached launches have no parent waiting on the worker. Make every
        # ordinary worker failure terminal in the child before propagating the
        # failure so wait=False cannot strand a Run at preparing.
        try:
            _mark_interrupted(resolved, run_id, gig_version, graph)
        except BaseException:
            # Preserve the original non-zero worker exit. The parent-side
            # waiter can still reconcile the durable state when it is present.
            pass
        raise


def _record_preschedule_failure(
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    message: str,
    parent: str,
) -> JournalEntry:
    path = resolved.path / "runs" / run_id / "run-details.json"
    details = parse_json_bytes(path.read_bytes())
    if not isinstance(details, dict):
        raise RunError("Run details are unavailable")
    details["status"] = "failed"
    details["finished_at"] = _now()
    details["execution_summary"] = f"Run rejected before Goal scheduling: {message}"
    data = canonical_json_bytes(details)
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
        transition="run_failed",
        body=f"Run {run_id} was rejected before Goal scheduling: {message}",
        artifacts=(JournalArtifact(f"runs/{run_id}/run-details.json", data),),
        front_matter={
            "gig_version": gig_version,
            "run_id": run_id,
            "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)),
            "parent_handoff_ids": [parent],
            "outcome": "UNSUPPORTED_SCHEDULING_POLICY",
            "actor": {"kind": "gigai", "id": "scheduler", "model_target": None},
        },
    )


def _mark_interrupted(
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
) -> JournalEntry:
    details_path = resolved.path / "runs" / run_id / "run-details.json"
    details = parse_json_bytes(details_path.read_bytes())
    if not isinstance(details, dict):
        raise RunError("interrupted Run details are unavailable")
    if details.get("status") == "interrupted":
        existing = _existing_interruption_entry(resolved, run_id)
        if existing is not None:
            return existing
        raise RunError("interrupted Run handoff is unavailable")
    details["status"] = "interrupted"
    details["finished_at"] = _now()
    details["execution_summary"] = (
        "Worker exited before producing a terminal result; evidence was preserved and no retry was attempted."
    )
    details["goal_sets"] = {
        "pending": [],
        "ready": [],
        "active": [],
        "complete": [],
        "failed": [],
        "blocked": [],
        "gated": [],
        "cancelled": [],
    }
    for goal in details.get("goals", []):
        goal["status"] = "failed"
        goal["errors"] = [
            {
                "code": "worker_interrupted",
                "message": "deterministic worker exited before terminalization",
                "retryable": False,
                "invocation_id": None,
            }
        ]
    data = canonical_json_bytes(details)
    preserved = [
        JournalArtifact(path.relative_to(resolved.path).as_posix(), path.read_bytes())
        for path in (resolved.path / "runs" / run_id).rglob("*")
        if path.is_file() and path != details_path
    ]
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
        transition="run_interrupted",
        body=f"Run {run_id} was interrupted; no automatic retry was performed.",
        artifacts=tuple(
            [JournalArtifact(f"runs/{run_id}/run-details.json", data), *preserved]
        ),
        front_matter={
            "gig_version": gig_version,
            "run_id": run_id,
            "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)),
            "outcome": "INTERRUPTED",
        },
    )


def _existing_interruption_entry(
    resolved: ResolvedWorkpad, run_id: str
) -> JournalEntry | None:
    for path in sorted((resolved.path / "handoffs").glob("*-run-interrupted.txt")):
        try:
            metadata, _body = parse_json_front_matter(path.read_bytes())
            sequence = int(path.name[:12])
        except (OSError, ValueError, TypeError):
            continue
        if (
            metadata.get("transition") == "run_interrupted"
            and metadata.get("run_id") == run_id
            and isinstance(metadata.get("handoff_id"), str)
        ):
            commit = _git(resolved.path, "rev-parse", "HEAD").stdout.strip()
            return JournalEntry(sequence, metadata["handoff_id"], path, commit)
    return None


def _latest_terminal_entry(
    resolved: ResolvedWorkpad, run_id: str
) -> JournalEntry | None:
    for path in sorted((resolved.path / "handoffs").glob("*-*.txt"), reverse=True):
        try:
            metadata, _body = parse_json_front_matter(path.read_bytes())
            if metadata.get("run_id") != run_id:
                continue
            transition = metadata.get("transition")
            if transition not in {"run_succeeded", "run_failed", "run_interrupted"}:
                continue
            sequence = int(path.name.split("-", 1)[0])
            handoff_id = metadata.get("handoff_id")
            if not isinstance(handoff_id, str):
                continue
            return JournalEntry(
                sequence,
                handoff_id,
                path,
                _git(resolved.path, "rev-parse", "HEAD").stdout.strip(),
            )
        except (OSError, ValueError, TypeError):
            continue
    return None


def _latest_goal_terminal_entry(
    resolved: ResolvedWorkpad, run_id: str, goal_id: str
) -> JournalEntry | None:
    """Return the owning Goal's terminal handoff for local recovery."""
    for path in sorted((resolved.path / "handoffs").glob("*-*.txt"), reverse=True):
        try:
            metadata, _body = parse_json_front_matter(path.read_bytes())
            if (
                metadata.get("run_id") != run_id
                or metadata.get("goal_id") != goal_id
                or metadata.get("transition") not in {"goal_completed", "goal_failed", "goal_blocked"}
            ):
                continue
            sequence = int(path.name.split("-", 1)[0])
            handoff_id = metadata.get("handoff_id")
            if not isinstance(handoff_id, str):
                continue
            return JournalEntry(
                sequence,
                handoff_id,
                path,
                _git(resolved.path, "rev-parse", "HEAD").stdout.strip(),
            )
        except (OSError, ValueError, TypeError):
            continue
    return None


def _details(
    run_id: str,
    gig_id: str,
    version: int,
    graph_digest: str,
    goals: list[dict[str, object]],
    budget: dict[str, object],
    target_ref: dict[str, object],
    timestamp: str,
    *,
    status: str,
    target_before: dict[str, object] | None = None,
    evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    entry_ids = set()
    # Entry Goals are ready at preparation; all other Goals remain pending.
    # The caller supplies the graph's entry set through the temporary marker.
    for goal in goals:
        if goal.get("_entry") is True:
            entry_ids.add(goal["goal_id"])
    clean_goals = []
    for goal in goals:
        if "_entry" in goal:
            goal = {key: value for key, value in goal.items() if key != "_entry"}
        clean_goals.append(goal)
    empty = {
        "pending": [
            goal["goal_id"] for goal in clean_goals if goal["goal_id"] not in entry_ids
        ],
        "ready": sorted(entry_ids),
        "active": [],
        "complete": [],
        "failed": [],
        "blocked": [],
        "gated": [],
        "cancelled": [],
    }
    goal_details = [
        {
            "goal_id": goal["goal_id"],
            "goal_version": goal["goal_version"],
            "executor": goal["executor"]["capability"],
            "status": "ready" if goal["goal_id"] in entry_ids else "pending",
            "outcome": None,
            "errors": [],
            "evidence": [],
            "usage": dict(_ZERO_USAGE),
            "started_at": None,
            "finished_at": None,
        }
        for goal in clean_goals
    ]
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "gig_id": gig_id,
        "gig_version": version,
        "goal_graph_sha256": graph_digest,
        "status": status,
        "started_at": timestamp,
        "finished_at": timestamp if status == "succeeded" else None,
        "goal_sets": empty,
        "goals": goal_details,
        "critical_path": [goal["goal_id"] for goal in clean_goals],
        "realized_max_parallel_goals": 1,
        "execution_summary": "Deterministic local capability completed without provider, network, subprocess, or target effects."
        if status == "succeeded"
        else "Run preparation sealed; execution has not started.",
        "tool_errors": [],
        "model_errors": [],
        "aggregate_usage": dict(_ZERO_USAGE),
        "remaining_budget": dict(budget),
        "target_before": target_before or target_ref,
        "target_after": target_ref if status == "succeeded" else None,
        "completion_audit": {"status": "missing", "path": None},
        "terminal_handoff": None,
        "workpad_commit": None,
        "next_actions": [],
    }


def _target_observation(resolved: ResolvedWorkpad) -> dict[str, object]:
    if resolved.target_kind == "git":
        head = (
            _git(resolved.target_root, "rev-parse", "HEAD", check=False).stdout.strip()
            or None
        )
        status = _git(
            resolved.target_root, "status", "--porcelain", check=False
        ).stdout.encode()
        status_digest = digest_imported_bytes(status) if status else None
    else:
        head = None
        status_digest = None
    payload = {
        "schema_version": "1.0",
        "kind": resolved.target_kind,
        "root": "bound-target",
        "git_head": head,
        "status_sha256": status_digest,
    }
    payload["observation_sha256"] = digest_imported_bytes(canonical_json_bytes(payload))
    return payload


def _artifact_ref(path: str, media: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": media,
        "size_bytes": len(data),
    }


def _front_matter(metadata: dict[str, object], body: str) -> bytes:
    from .canonical import render_json_front_matter

    return render_json_front_matter(metadata, body)


def canonicalize_evidence(text: str) -> bytes:
    from .canonical import canonicalize_owned_text

    return canonicalize_owned_text(text)


def _allocate_run_id(workpad: Path, uuid_factory: Callable[[], uuid.UUID]) -> str:
    runs = workpad / "runs"
    return generate_entity_id(
        EntityPrefix.RUN,
        is_persisted=lambda value: (runs / value).exists(),
        uuid_factory=uuid_factory,
    )


def _new_id(prefix: EntityPrefix, uuid_factory: Callable[[], uuid.UUID]) -> str:
    return generate_entity_id(
        prefix, is_persisted=lambda _value: False, uuid_factory=uuid_factory
    )


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        capture_output=True,
        text=True,
        check=check,
        shell=False,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )


def _git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        capture_output=True,
        check=True,
        shell=False,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    return result.stdout


__all__ = [
    "ProposalRunRequest",
    "TailorRunRequest",
    "RunError",
    "RunResult",
    "launch_find_jobs_run",
    "launch_run",
    "read_run_details",
    "resolve_newest_resume",
    "resolve_newest_resume_details",
    "ResumeDetails",
]
