"""The bounded host caller for a local Scout proposal assessment.

This module is deliberately small: authority resolution stays in the existing
completed-discovery and committed-input resolvers, model transport stays in
``run_model_invocation``, and this host caller only joins their exact bytes,
validates the model assessment, and records a private result.  It does not
create a resume, Tailor action, application state, or an approval pointer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from . import model_execution
from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    digest_imported_bytes,
    parse_json_front_matter,
    parse_json_bytes,
    validate_entity_id,
)
from .journal import (
    JournalArtifact,
    JournalConflictError,
    JournalEntry,
    JournalTransition,
    _git,
    _git_bytes,
    JournalSnapshot,
    JournalWriter,
    read_committed_artifact,
    run_with_journal_writer,
)
from .model_targets import ModelTargetResolutionError, resolve_model_target
from .roles import RoleError, require_registered
from .scout_inputs import ScoutInputError, resolve_external_input
from .scout_posting_inputs import (
    ScoutPostingInputError,
    resolve_discovery_posting_input_from_journal,
)
from .scout_proposals import (
    ProposalSource,
    ScoutProposalError,
    ScoutProposalRequest,
    build_proposal_prompt,
    validate_proposal_output,
)
from .workpad import ResolvedWorkpad
from .config import GigAIConfig
from .model_execution import (
    InvocationBudget,
    InvocationPolicy,
    ModelInvocationExecution,
    SelectedReference,
)
from .validators import validate_goal_graph, validate_serialized_contract
from .validators import validate_model_invocation


_ROLE = "reviewer"
_MAX_PRIVATE_SOURCES = 12
_MAX_RESULT_BYTES = 512 * 1024
_PRIVATE_PURPOSES = frozenset({"preferences", "experience", "answer"})
_REFERENCE_KINDS = frozenset({"resume", "project_evidence", "role_history", "cover_letter"})
_TERMINAL_GOAL_STATES = frozenset({"complete", "failed", "blocked", "cancelled"})


class ScoutProposalExecutionError(ValueError):
    """Content-free refusal before or during a local proposal assessment."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ScoutProposalExecution:
    """The invocation evidence and host-owned private assessment result."""

    invocation: ModelInvocationExecution
    result: dict[str, object]
    result_entry: JournalEntry


def execute_local_proposal(
    *,
    resolved: ResolvedWorkpad,
    config: GigAIConfig,
    run_id: str,
    goal_id: str,
    model_target: str,
    posting_selector: Mapping[str, object],
    private_selectors: tuple[Mapping[str, object], ...],
    local_allowed: bool,
    budget: InvocationBudget | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ScoutProposalExecution:
    """Assess one authenticated posting against selected private revisions.

    All selected source bytes are resolved under one caller-held writer.  The
    existing local-only model execution path performs target identity checks,
    invocation journaling, and transport closure.  A ``ref_`` G45 reference is
    required as an invocation evidence anchor because the accepted invocation
    contract only admits canonical reference IDs; native/discovery sources
    remain host-bound in the result lineage rather than receiving fabricated
    durable IDs.
    """

    try:
        validate_entity_id(run_id, expected_prefix=EntityPrefix.RUN)
        validate_entity_id(goal_id, expected_prefix=EntityPrefix.GOAL)
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_identity_invalid", "Run or goal identity is invalid"
        ) from exc
    if not isinstance(posting_selector, Mapping) or not isinstance(
        private_selectors, tuple
    ):
        raise ScoutProposalExecutionError(
            "execution_input_invalid", "proposal source selectors are invalid"
        )
    if not private_selectors or len(private_selectors) > _MAX_PRIVATE_SOURCES:
        raise ScoutProposalExecutionError(
            "execution_input_invalid",
            "proposal needs a bounded private source selection",
        )
    # A canonical Run/Goal identifier is not execution authority.  Resolve
    # the committed owning Run, its sealed graph membership, active goal, and
    # narrow write-workpad effect before touching the model boundary.
    _require_active_goal(resolved, run_id, goal_id)
    try:
        target = resolve_model_target(config, model_target)
    except ModelTargetResolutionError as exc:
        raise ScoutProposalExecutionError(
            "local_target_invalid", "configured model target is unavailable"
        ) from exc
    if target.endpoint.adapter != "ollama_local":
        raise ScoutProposalExecutionError(
            "local_target_required",
            "proposal execution requires the configured local Ollama target",
        )
    if not target.target.model_digest:
        raise ScoutProposalExecutionError(
            "local_target_invalid", "configured local model identity is incomplete"
        )
    if not local_allowed:
        raise ScoutProposalExecutionError(
            "local_runtime_denied", "explicit local runtime permission is required"
        )
    try:
        require_registered(_ROLE, namespace="model_invocation")
    except RoleError as exc:
        raise ScoutProposalExecutionError(
            "invocation_role_unavailable", "registered reviewer role is unavailable"
        ) from exc

    try:
        posting, private_sources, sealed_head = _resolve_sources(
            resolved, posting_selector, private_selectors
        )
        request = ScoutProposalRequest(posting=posting, private_sources=private_sources)
        prompt = build_proposal_prompt(request)
    except (ScoutPostingInputError, ScoutInputError, ScoutProposalError) as exc:
        raise ScoutProposalExecutionError(
            "source_resolution_refused",
            "selected proposal sources are unavailable or unauthenticated",
        ) from exc

    # Native and discovery sources have no legacy ref_ identity.  Preserve
    # their exact host-owned identity in the additive invocation descriptor
    # contract; never manufacture a G45 reference merely to satisfy v1.
    references = tuple(
        SelectedReference(
            # Source handles are host-created transport labels, not durable
            # authority IDs. Using one for every selected source makes the
            # v3 descriptor set a complete one-to-one binding, while the
            # descriptor identity digest still records the real G45/native
            # identity resolved above.
            reference_id=source.handle,
            path=str(
                source.identity.get("snapshot_ref", source.identity.get("posting_ref", {})).get("path")
                if isinstance(source.identity.get("snapshot_ref", source.identity.get("posting_ref", {})), Mapping)
                else source.handle
            ),
            content=source.content,
            content_sha256=source.content_sha256,
            media_type=str(
                source.identity.get("snapshot_ref", source.identity.get("posting_ref", {})).get("media_type", "text/plain")
                if isinstance(source.identity.get("snapshot_ref", source.identity.get("posting_ref", {})), Mapping)
                else "text/plain"
            ),
        )
        for source in (request.posting, *request.private_sources)
    )
    selected_ids = tuple(item.reference_id for item in references)
    source_descriptors = tuple(
        _source_descriptor(source, item.reference_id)
        for source, item in zip((request.posting, *request.private_sources), references)
    )
    _require_active_goal(resolved, run_id, goal_id)
    try:
        execution = model_execution.run_model_invocation(
            resolved=resolved,
            config=config,
            run_id=run_id,
            goal_id=goal_id,
            model_target=model_target,
            role=_ROLE,
            prompt=prompt,
            references=references,
                selected_reference_ids=selected_ids,
                policy=InvocationPolicy(
                    allowed_reference_ids=frozenset(selected_ids),
                    local_allowed=True,
                    offline=True,
                    selected_source_descriptors=source_descriptors,
                ),
            budget=budget,
            uuid_factory=uuid_factory,
            # Proposal validation belongs to this host caller.  Generic G18
            # invocation evidence must not terminalize the proposal Goal
            # before its domain result is known.
            commit_goal_transition=False,
        )
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "invocation_failed", "local proposal invocation failed"
        ) from exc

    # Persist invocation evidence before domain parsing/publication.  This is
    # intentionally a non-terminal receipt: a competing cancellation or
    # publication conflict must not erase the request/response/usage record.
    _publish_invocation_attempt(
        resolved=resolved,
        run_id=run_id,
        goal_id=goal_id,
        model_target=model_target,
        execution=execution,
        uuid_factory=uuid_factory,
    )
    result = _host_result(
        execution=execution,
        request=request,
        sealed_head=sealed_head,
    )
    result_bytes = canonical_json_bytes(result)
    if len(result_bytes) > _MAX_RESULT_BYTES:
        raise ScoutProposalExecutionError(
            "proposal_result_too_large", "private proposal result exceeds its bound"
        )
    invocation_id = str(execution.record["invocation_id"])
    path = f"runs/{run_id}/scout-proposals/{invocation_id}/result.json"
    result_ref = _ref(path, result_bytes)
    entry = _publish_result(
        resolved=resolved,
        run_id=run_id,
        goal_id=goal_id,
        model_target=model_target,
        result=result,
        result_ref=result_ref,
        invocation_artifacts=execution.artifacts,
        uuid_factory=uuid_factory,
    )
    return ScoutProposalExecution(execution, result, entry)


def _source_descriptor(source: ProposalSource, source_id: str) -> dict[str, object]:
    """Build a closed, digest-only descriptor from authenticated host data."""
    identity = json.loads(json.dumps(source.identity, ensure_ascii=False, default=dict))
    return {
        "source_id": source_id,
        "family": source.family,
        "purpose": source.purpose,
        "content_sha256": source.content_sha256,
        "identity_sha256": digest_imported_bytes(canonical_json_bytes(identity)),
    }


def _publish_invocation_attempt(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    goal_id: str,
    model_target: str,
    execution: ModelInvocationExecution,
    uuid_factory: Callable[[], uuid.UUID],
) -> JournalEntry:
    """Commit one non-terminal, strictly authenticated invocation receipt."""

    record = execution.record
    invocation_id = record.get("invocation_id")
    if (
        not isinstance(invocation_id, str)
        or record.get("run_id") != run_id
        or record.get("goal_id") != goal_id
        or not validate_model_invocation(record).valid
    ):
        raise ScoutProposalExecutionError(
            "invocation_evidence_invalid", "local invocation evidence is not admissible"
        )
    artifacts = tuple(execution.artifacts)
    allowed_prefix = f"runs/{run_id}/model-invocations/{invocation_id}/"
    if not artifacts or any(
        not item.path.startswith(allowed_prefix)
        or item.path.rsplit("/", 1)[-1] not in {"request.json", "record.json", "response.json"}
        for item in artifacts
    ):
        raise ScoutProposalExecutionError(
            "invocation_evidence_invalid", "local invocation artifacts are not scoped"
        )
    refs = [_ref(item.path, item.content) for item in artifacts]

    def operation(writer: JournalWriter) -> JournalEntry:
        for artifact in artifacts:
            path = writer.root / artifact.path
            if path.exists() and path.read_bytes() != artifact.content:
                raise ScoutProposalExecutionError(
                    "invocation_evidence_conflict", "local invocation artifact changed"
                )
            if path.exists():
                raise ScoutProposalExecutionError(
                    "invocation_evidence_conflict", "local invocation artifact is already published"
                )
        return writer.record(
            JournalTransition(
                _new_handoff(uuid_factory),
                "proposal_invocation_recorded",
                f"Proposal invocation {invocation_id} evidence recorded before domain publication.",
                artifacts,
                {
                    "run_id": run_id,
                    "goal_id": goal_id,
                    "invocation_id": invocation_id,
                    "model_target": model_target,
                    "source": "scout-proposal-execution",
                    "actor": {
                        "kind": "gigai",
                        "id": "scout-proposal-execution",
                        "model_target": model_target,
                    },
                    "outcome": "INVOCATION_EVIDENCE",
                    "artifact_refs": refs,
                },
            )
        )

    try:
        return run_with_journal_writer(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            operation=operation,
        )
    except ScoutProposalExecutionError:
        raise
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "invocation_evidence_refused", "local invocation evidence could not be committed"
        ) from exc


def read_proposal_invocation_attempts(
    resolved: ResolvedWorkpad, run_id: str, goal_id: str
) -> tuple[dict[str, object], ...]:
    """Read proposal receipts from one authenticated, pinned journal HEAD.

    A committed receipt is not authority merely because its JSON and record
    IDs have the right shape.  The receipt's closed artifact set is checked
    against the exact bytes published by that receipt, and the record's own
    request/response references must describe those same bytes.  ``HEAD`` is
    captured once so a later writer cannot mix generations while this reader
    is evaluating one result.
    """

    try:
        head = _git(resolved.path, "rev-parse", "HEAD").stdout.strip()
        if not head:
            raise ValueError("journal has no committed HEAD")
        names = _git(
            resolved.path, "ls-tree", "-r", "--name-only", head, "--", "handoffs/"
        ).stdout.splitlines()
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "invocation_evidence_unavailable", "proposal invocation history is unavailable"
        ) from exc
    records: list[dict[str, object]] = []
    seen_invocations: set[str] = set()
    for name in names:
        if not name.endswith(".txt"):
            continue
        try:
            metadata, _body = parse_json_front_matter(
                _git_bytes(resolved.path, "show", f"{head}:{name}")
            )
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation receipt is unreadable"
            ) from exc
        if (
            metadata.get("transition") != "proposal_invocation_recorded"
            or metadata.get("run_id") != run_id
            or metadata.get("goal_id") != goal_id
        ):
            continue
        invocation_id = metadata.get("invocation_id")
        model_target = metadata.get("model_target")
        actor = metadata.get("actor")
        if (
            not isinstance(invocation_id, str)
            or not isinstance(model_target, str)
            or metadata.get("source") != "scout-proposal-execution"
            or actor
            != {
                "kind": "gigai",
                "id": "scout-proposal-execution",
                "model_target": model_target,
            }
            or invocation_id in seen_invocations
        ):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation receipt identity is invalid"
            )
        seen_invocations.add(invocation_id)
        receipt_refs = metadata.get("artifact_refs")
        if not isinstance(receipt_refs, list) or not receipt_refs:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation receipt lacks artifact references"
            )
        normalized_refs: dict[str, dict[str, object]] = {}
        prefix = f"runs/{run_id}/model-invocations/{invocation_id}/"
        for value in receipt_refs:
            if not isinstance(value, Mapping) or set(value) != {
                "path", "content_sha256", "media_type", "size_bytes"
            }:
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal receipt artifact reference is malformed"
                )
            path = value.get("path")
            if (
                not isinstance(path, str)
                or not path.startswith(prefix)
                or path.rsplit("/", 1)[-1] not in {"request.json", "record.json", "response.json"}
                or path in normalized_refs
                or not isinstance(value.get("content_sha256"), str)
                or type(value.get("size_bytes")) is not int
                or value.get("media_type") != "application/json"
            ):
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal receipt artifact set is invalid"
                )
            normalized_refs[path] = dict(value)
        record_path = f"{prefix}record.json"
        request_path = f"{prefix}request.json"
        if record_path not in normalized_refs or request_path not in normalized_refs:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal receipt must include request and record artifacts"
            )
        try:
            receipt_commit = _git(
                resolved.path, "log", "--format=%H", "-1", head, "--", name
            ).stdout.strip()
            if not receipt_commit:
                raise ValueError("receipt publisher is unavailable")
            artifacts: dict[str, bytes] = {}
            for path, reference in normalized_refs.items():
                data, publisher = read_committed_artifact(
                    workpad=resolved.path,
                    project_id=resolved.project_id,
                    gig_id=resolved.gig_id,
                    path=path,
                    head=head,
                )
                if publisher != receipt_commit:
                    raise JournalConflictError("proposal receipt artifact has another publisher")
                if (
                    reference["content_sha256"] != digest_imported_bytes(data)
                    or reference["size_bytes"] != len(data)
                ):
                    raise JournalConflictError("proposal receipt artifact digest differs")
                artifacts[path] = data
            record = parse_json_bytes(artifacts[record_path])
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation receipt artifacts are unavailable"
            ) from exc
        if (
            not isinstance(record, dict)
            or record.get("run_id") != run_id
            or record.get("goal_id") != goal_id
            or record.get("invocation_id") != invocation_id
            or record.get("configured_selector") != model_target
            or not validate_model_invocation(record).valid
        ):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation record owner or target binding is invalid"
            )
        request = record.get("request")
        if not isinstance(request, Mapping):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal invocation request is missing"
            )
        request_ref = request.get("request_artifact")
        if not _same_artifact_ref(request_ref, normalized_refs[request_path]):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal request artifact reference does not match receipt"
            )
        try:
            request_payload = parse_json_bytes(artifacts[request_path])
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal request artifact is unreadable"
            ) from exc
        if (
            not isinstance(request_payload, Mapping)
            or request_payload.get("schema_version") != "1.0"
            or request_payload.get("role") != record.get("role")
            or request_payload.get("input_sha256") is None
            or request_payload.get("blocked_reason") is not None
            and record.get("outcome") == "succeeded"
            or request.get("request_sha256") != digest_imported_bytes(artifacts[request_path])
        ):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal request content does not match its record"
            )
        selected_ids = record.get("request", {}).get("selected_references")
        request_ids = request_payload.get("selected_reference_ids")
        if (
            not isinstance(selected_ids, list)
            or not isinstance(request_ids, list)
            or request_ids != [item.get("reference_id") for item in selected_ids]
        ):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal request selections do not match its record"
            )
        source_descriptors = request.get("selected_source_descriptors")
        request_source_descriptors = request_payload.get("selected_source_descriptors")
        if source_descriptors is None:
            if request_source_descriptors is not None:
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal request has unclaimed source descriptors"
                )
        elif request_source_descriptors != source_descriptors:
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal source descriptors do not match request bytes"
            )
        response_ref = _response_artifact(record)
        response_path = f"{prefix}response.json"
        if response_ref is None:
            if response_path in normalized_refs:
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal receipt has an unclaimed response artifact"
                )
        elif response_path not in normalized_refs or not _same_artifact_ref(
            response_ref, normalized_refs[response_path]
        ):
            raise ScoutProposalExecutionError(
                "invocation_evidence_invalid", "proposal response artifact reference does not match receipt"
            )
        else:
            try:
                response_payload = parse_json_bytes(artifacts[response_path])
            except Exception as exc:
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal response artifact is unreadable"
                ) from exc
            if (
                not isinstance(response_payload, Mapping)
                or response_payload.get("schema_version") != "1.0"
                or not isinstance(response_payload.get("output_text"), str)
                or response_payload.get("resolved_model") != record.get("resolved_model")
            ):
                raise ScoutProposalExecutionError(
                    "invocation_evidence_invalid", "proposal response content does not match its record"
                )
        records.append(record)
    return tuple(records)


def _require_active_goal(
    resolved: ResolvedWorkpad, run_id: str, goal_id: str
) -> None:
    """Require committed scheduler ownership before local model invocation."""

    def operation(writer: JournalWriter) -> None:
        _validate_active_goal_bytes(
            resolved,
            _committed_run_bytes(writer.root, run_id),
            run_id,
            goal_id,
        )

    try:
        run_with_journal_writer(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            operation=operation,
        )
    except ScoutProposalExecutionError:
        raise
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_authority_refused",
            "proposal Run/Goal is not committed active scheduler authority",
        ) from exc


def _committed_run_bytes(root: Path, run_id: str) -> tuple[bytes, bytes]:
    details_path = f"runs/{run_id}/run-details.json"
    graph_path = f"runs/{run_id}/goal-graph.json"
    try:
        return (
            _git_bytes(root, "show", f"HEAD:{details_path}"),
            _git_bytes(root, "show", f"HEAD:{graph_path}"),
        )
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_authority_refused",
            "proposal Run details or sealed Goal Graph is unavailable",
        ) from exc


def _validate_active_goal_bytes(
    resolved: ResolvedWorkpad,
    run_bytes: tuple[bytes, bytes],
    run_id: str,
    goal_id: str,
) -> None:
    details_bytes, graph_bytes = run_bytes
    if not validate_serialized_contract("run-details.schema.json", details_bytes).valid:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Run details are invalid"
        )
    if not validate_serialized_contract("goal-graph.schema.json", graph_bytes).valid:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Goal Graph is invalid"
        )
    try:
        details = parse_json_bytes(details_bytes)
        graph = parse_json_bytes(graph_bytes)
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Run authority is unreadable"
        ) from exc
    if not isinstance(details, Mapping) or not isinstance(graph, Mapping):
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Run authority is malformed"
        )
    if (
        details.get("run_id") != run_id
        or details.get("gig_id") != resolved.gig_id
        or details.get("status") != "running"
        or details.get("goal_graph_sha256") != digest_imported_bytes(graph_bytes)
    ):
        raise ScoutProposalExecutionError(
            "execution_authority_refused",
            "proposal Run is not an active committed owner",
        )
    if _terminal_goal_handoff_exists(resolved.path, run_id, goal_id):
        raise ScoutProposalExecutionError(
            "execution_authority_refused",
            "requested Goal already has a committed terminal transition",
        )
    semantic = validate_goal_graph(graph)
    if not semantic.valid:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Goal Graph is not admitted"
        )
    goals = graph.get("goals")
    goal_details = details.get("goals")
    if not isinstance(goals, list) or not isinstance(goal_details, list):
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "proposal Goal membership is unavailable"
        )
    goal = next((item for item in goals if isinstance(item, Mapping) and item.get("goal_id") == goal_id), None)
    detail = next((item for item in goal_details if isinstance(item, Mapping) and item.get("goal_id") == goal_id), None)
    if not isinstance(goal, Mapping) or not isinstance(detail, Mapping):
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "requested Goal is not a member of the sealed graph"
        )
    if detail.get("status") != "running" or detail.get("outcome") is not None:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "requested Goal is not eligible for proposal execution"
        )
    effects = goal.get("effects")
    if effects != ["write_workpad"]:
        raise ScoutProposalExecutionError(
            "execution_effect_refused", "proposal Goal does not permit the private workpad effect"
        )


def _terminal_goal_handoff_exists(root: Path, run_id: str, goal_id: str) -> bool:
    """Treat a committed terminal handoff as authoritative over stale details."""

    try:
        names = _git(
            root, "ls-tree", "-r", "--name-only", "HEAD", "--", "handoffs/", check=False
        ).stdout.splitlines()
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_authority_refused", "committed Goal terminal history is unavailable"
        ) from exc
    latest_started = -1
    latest_terminal = -1
    for name in sorted(names):
        if not name.startswith("handoffs/") or not name.endswith(".txt"):
            continue
        try:
            metadata, _body = parse_json_front_matter(
                _git_bytes(root, "show", f"HEAD:{name}")
            )
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "execution_authority_refused", "committed Goal terminal history is unreadable"
            ) from exc
        if metadata.get("run_id") != run_id or metadata.get("goal_id") != goal_id:
            continue
        try:
            sequence = int(Path(name).name.split("-", 1)[0])
        except (ValueError, IndexError):
            continue
        transition = metadata.get("transition")
        if transition == "goal_started":
            latest_started = max(latest_started, sequence)
        elif transition in {"goal_completed", "goal_failed", "goal_blocked"}:
            latest_terminal = max(latest_terminal, sequence)
    return latest_terminal > latest_started


def _terminalize_goal_details(
    details: dict[str, object],
    goal_id: str,
    result: Mapping[str, object],
    evidence: list[dict[str, object]],
) -> None:
    """Materialize Goal outcome alongside the terminal handoff artifact."""

    goals = details.get("goals")
    if not isinstance(goals, list):
        raise ScoutProposalExecutionError(
            "execution_publication_refused", "proposal Run details have no Goal state"
        )
    detail = next(
        (item for item in goals if isinstance(item, dict) and item.get("goal_id") == goal_id),
        None,
    )
    if not isinstance(detail, dict):
        raise ScoutProposalExecutionError(
            "execution_publication_refused", "proposal Goal details are unavailable"
        )
    complete = result.get("status") == "complete"
    detail.update(
        {
            "status": "complete" if complete else "failed",
            "outcome": "COMPLETE" if complete else "FAILED",
            "finished_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "evidence": evidence,
            "errors": []
            if complete
            else [
                {
                    "code": "proposal_result_invalid",
                    "message": "local proposal assessment failed bounded validation",
                    "retryable": False,
                    "invocation_id": result.get("invocation_id"),
                }
            ],
        }
    )
    # Only this Goal is terminalized.  A proposal caller does not own whole-Run
    # completion while other sealed Goals remain pending.
    goal_sets = {
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
    for item in goals:
        if not isinstance(item, Mapping):
            continue
        state = item.get("status")
        aggregate = "active" if state in {"running", "verifying"} else state
        if aggregate in goal_sets and isinstance(item.get("goal_id"), str):
            goal_sets[aggregate].append(item["goal_id"])
    details["goal_sets"] = goal_sets

def _publish_result(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    goal_id: str,
    model_target: str,
    result: Mapping[str, object],
    result_ref: Mapping[str, object],
    invocation_artifacts: tuple[JournalArtifact, ...],
    uuid_factory: Callable[[], uuid.UUID],
) -> JournalEntry:
    """Publish invocation + domain result in one authorized Goal transition."""

    path = str(result_ref["path"])
    result_bytes = canonical_json_bytes(dict(result))
    transition = "goal_completed" if result.get("status") == "complete" else "goal_failed"

    def operation(writer: JournalWriter) -> JournalEntry:
        run_bytes = _committed_run_bytes(writer.root, run_id)
        _validate_active_goal_bytes(
            resolved,
            run_bytes,
            run_id,
            goal_id,
        )
        try:
            details = parse_json_bytes(run_bytes[0])
            graph = parse_json_bytes(run_bytes[1])
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "execution_publication_refused", "proposal Run state is unreadable"
            ) from exc
        if not isinstance(details, dict) or not isinstance(graph, Mapping):
            raise ScoutProposalExecutionError(
                "execution_publication_refused", "proposal Run state is malformed"
            )
        refs = [
            _ref(item.path, item.content)
            for item in (*invocation_artifacts, JournalArtifact(path, result_bytes))
        ]
        details_ref = _ref(f"runs/{run_id}/run-details.json", b"")
        # Replace the placeholder digest after the materialized details bytes
        # are built; it is included in the same immutable transition.
        _terminalize_goal_details(details, goal_id, result, refs)
        details_bytes = canonical_json_bytes(details)
        details_ref = _ref(f"runs/{run_id}/run-details.json", details_bytes)
        refs.append(details_ref)
        graph_goals = graph.get("goals")
        graph_goal = next(
            (item for item in graph_goals or [] if isinstance(item, Mapping) and item.get("goal_id") == goal_id),
            {},
        )
        # Invocation artifacts were committed by the non-terminal attempt
        # receipt.  Reuse only byte-identical files; the result/details files
        # remain the sole new artifacts in this Goal terminal transition.
        for artifact in (*invocation_artifacts, JournalArtifact(path, result_bytes)):
            if (writer.root / artifact.path).exists():
                if (writer.root / artifact.path).read_bytes() != artifact.content:
                    raise ScoutProposalExecutionError(
                        "execution_publication_refused",
                        "proposal invocation or result artifact changed",
                    )
        return writer.record(
            JournalTransition(
                _new_handoff(uuid_factory),
                transition,
                f"Scout local proposal assessment terminalized as {result.get('status') }.",
                (
                    JournalArtifact(path, result_bytes),
                    JournalArtifact(details_ref["path"], details_bytes),
                ),
                {
                    "run_id": run_id,
                    "goal_id": goal_id,
                    "gig_id": resolved.gig_id,
                    "gig_version": details.get("gig_version"),
                    "goal_version": graph_goal.get("goal_version"),
                    "goal_graph_sha256": digest_imported_bytes(run_bytes[1]),
                    "outcome": "COMPLETE" if transition == "goal_completed" else "FAILED",
                    "actor": {
                        "kind": "gigai",
                        "id": "scout-proposal-execution",
                        "model_target": model_target,
                    },
                    "artifact_refs": refs,
                },
            ),
            allow_artifact_replacement=True,
        )

    try:
        return run_with_journal_writer(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            operation=operation,
        )
    except ScoutProposalExecutionError:
        raise
    except Exception as exc:
        raise ScoutProposalExecutionError(
            "execution_publication_refused", "proposal result could not be published safely"
        ) from exc


def _resolve_sources(
    resolved: ResolvedWorkpad,
    posting_selector: Mapping[str, object],
    private_selectors: tuple[Mapping[str, object], ...],
) -> tuple[ProposalSource, tuple[ProposalSource, ...], str]:
    def operation(
        writer: JournalWriter,
    ) -> tuple[ProposalSource, tuple[ProposalSource, ...], str]:
        posting_value = resolve_discovery_posting_input_from_journal(
            resolved, posting_selector, writer=writer
        )
        snapshot = writer.snapshot(("records/", "references/", "run-inputs/"))
        posting = _posting_source(posting_value)
        private_items: list[ProposalSource] = []
        for index, descriptor in enumerate(private_selectors):
            if set(descriptor) != {"purpose", "selector"}:
                raise ScoutProposalExecutionError(
                    "private_source_purpose_required",
                    "each private source requires an explicit host-owned purpose",
                )
            purpose = descriptor.get("purpose")
            selector = descriptor.get("selector")
            if type(purpose) is not str or purpose not in _PRIVATE_PURPOSES:
                raise ScoutProposalExecutionError(
                    "private_source_purpose_invalid",
                    "private source purpose is not admitted",
                )
            if not isinstance(selector, Mapping):
                raise ScoutProposalExecutionError(
                    "private_source_selector_invalid",
                    "private source selector is invalid",
                )
            resolved_input = resolve_external_input(resolved, snapshot, selector)
            private_items.append(
                _private_source(
                    snapshot,
                    resolved_input,
                    purpose=purpose,
                    handle=f"source_{index + 2}",
                )
            )
        private = tuple(private_items)
        return posting, private, snapshot.head

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=operation,
    )


def _posting_source(value: Mapping[str, object]) -> ProposalSource:
    posting_ref = value.get("posting_ref")
    if not isinstance(posting_ref, Mapping) or not isinstance(
        posting_ref.get("ref"), Mapping
    ):
        raise ScoutProposalExecutionError(
            "posting_source_invalid", "discovery posting capture reference is invalid"
        )
    identity = {
        "family": "scout_discovery_posting",
        "run_id": value.get("run_id"),
        "receipt_id": value.get("receipt_id"),
        "checkpoint_id": value.get("checkpoint_id"),
        "opportunity_id": value.get("opportunity_id"),
        "snapshot_id": value.get("snapshot_id"),
        "run_ref": value.get("run_ref"),
        "receipt_ref": value.get("receipt_ref"),
        "checkpoint_ref": value.get("checkpoint_ref"),
        "posting_ref": posting_ref["ref"],
    }
    content = value.get("posting_bytes")
    if not isinstance(content, bytes):
        raise ScoutProposalExecutionError(
            "posting_source_invalid", "discovery posting bytes are unavailable"
        )
    digest = digest_imported_bytes(content)
    if identity["posting_ref"].get("content_sha256") != digest:
        raise ScoutProposalExecutionError(
            "posting_source_invalid", "discovery posting bytes changed"
        )
    return ProposalSource(
        "source_1", "posting", "scout_discovery_posting", identity, content, digest
    )


def _private_source(
    snapshot: JournalSnapshot,
    value: Mapping[str, object],
    *,
    purpose: str,
    handle: str,
) -> ProposalSource:
    family = value.get("family")
    content_ref: Mapping[str, object] | None = None
    identity: dict[str, object]
    if purpose not in _PRIVATE_PURPOSES:
        raise ScoutProposalExecutionError(
            "private_source_purpose_invalid", "private source purpose is not admitted"
        )
    if family in {"g45_reference", "g45_run_input"}:
        key = "reference_id" if family == "g45_reference" else "run_input_id"
        if not isinstance(value.get(key), str) or not isinstance(
            value.get("snapshot_ref"), Mapping
        ):
            raise ScoutProposalExecutionError(
                "private_source_invalid", "G45 source descriptor is invalid"
            )
        content_ref = value["snapshot_ref"]
        identity = {
            "family": family,
            key: value[key],
            "snapshot_ref": dict(content_ref),
            "content_sha256": value.get("snapshot_ref", {}).get("content_sha256"),
        }
        record_ref = value.get("record_ref")
        if not isinstance(record_ref, Mapping) or not isinstance(record_ref.get("path"), str):
            raise ScoutProposalExecutionError(
                "private_source_invalid", "G45 source record authority is unavailable"
            )
        record_bytes = snapshot.artifacts.get(str(record_ref["path"]))
        if record_bytes is None:
            raise ScoutProposalExecutionError(
                "private_source_invalid", "G45 source record authority is unavailable"
            )
        try:
            record = parse_json_bytes(record_bytes)
        except Exception as exc:
            raise ScoutProposalExecutionError(
                "private_source_invalid", "G45 source record authority is invalid"
            ) from exc
        source_kind = record.get("kind") if isinstance(record, Mapping) else None
        if family == "g45_reference":
            if source_kind not in _REFERENCE_KINDS or purpose != "experience":
                raise ScoutProposalExecutionError(
                    "private_source_purpose_mismatch",
                    "imported reference evidence is admitted only as explicit experience",
                )
        elif source_kind == "job_description":
            raise ScoutProposalExecutionError(
                "private_source_purpose_mismatch",
                "job-description Run input cannot establish an answer purpose",
            )
    elif family == "scout_record":
        content = value.get("content")
        if not isinstance(content, Mapping) or content.get("family") != "jsl_blob":
            raise ScoutProposalExecutionError(
                "private_source_invalid", "native source descriptor is invalid"
            )
        blob = content.get("blob_ref")
        if not isinstance(blob, Mapping):
            raise ScoutProposalExecutionError(
                "private_source_invalid", "native source blob reference is invalid"
            )
        content_ref = blob
        identity = {
            "family": family,
            "record_id": value.get("record_id"),
            "revision_id": value.get("revision_id"),
            "native_kind": value.get("native_kind"),
            # The pure DTO intentionally carries the closed scope selector;
            # the resolver has already authenticated any native override base.
            "scope": {
                "mode": value.get("scope", {}).get("mode"),
                "task_context_id": value.get("scope", {}).get("task_context_id"),
            }
            if isinstance(value.get("scope"), Mapping)
            else value.get("scope"),
            "blob_ref": dict(blob),
        }
        native_kind = value.get("native_kind")
        if (native_kind == "profile_preferences" and purpose != "preferences") or (
            native_kind == "experience_qa" and purpose not in {"experience", "answer"}
        ):
            raise ScoutProposalExecutionError(
                "private_source_purpose_mismatch",
                "native source kind does not match its explicit private purpose",
            )
    else:
        raise ScoutProposalExecutionError(
            "private_source_invalid", "private source family is unsupported"
        )
    content = _read_ref(snapshot, content_ref)
    digest = digest_imported_bytes(content)
    if content_ref.get("content_sha256") != digest:
        raise ScoutProposalExecutionError(
            "private_source_invalid", "private source bytes changed"
        )
    # Handles are prompt-local, not durable identity.
    return ProposalSource(handle, purpose, family, identity, content, digest)  # type: ignore[arg-type]


def _read_ref(snapshot: JournalSnapshot, ref: Mapping[str, object] | None) -> bytes:
    if not isinstance(ref, Mapping):
        raise ScoutProposalExecutionError(
            "source_ref_invalid", "source artifact reference is invalid"
        )
    path = ref.get("path")
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or ".." in Path(path).parts
    ):
        raise ScoutProposalExecutionError(
            "source_ref_invalid", "source artifact path is invalid"
        )
    data = snapshot.artifacts.get(path)
    if (
        data is None
        or ref.get("content_sha256") != digest_imported_bytes(data)
        or ref.get("size_bytes") != len(data)
    ):
        raise ScoutProposalExecutionError(
            "source_ref_invalid", "source artifact is unavailable or changed"
        )
    return data


def _host_result(
    *,
    execution: ModelInvocationExecution,
    request: ScoutProposalRequest,
    sealed_head: str,
) -> dict[str, object]:
    invocation_id = str(execution.record["invocation_id"])
    base: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "scout-proposal-execution",
        "invocation_id": invocation_id,
        "invocation_record_sha256": digest_imported_bytes(
            canonical_json_bytes(execution.record)
        ),
        "request_sha256": execution.record["request"]["request_sha256"],
        "input_lineage": request.lineage(),
        "sealed_journal_head": sealed_head,
        "status": "failed",
        "proposal": None,
        "error": None,
    }
    response_artifact = _response_artifact(execution.record)
    if response_artifact is not None:
        base["response_artifact"] = response_artifact
    if execution.result is None or execution.record.get("outcome") != "succeeded":
        base["error"] = {
            "code": "invocation_not_succeeded",
            "message": "local invocation did not complete successfully",
        }
        return base
    report = validate_proposal_output(execution.result.output_text, request=request)
    if not report.valid:
        base["error"] = {
            "code": "proposal_output_invalid",
            "message": "local assessment failed bounded validation",
        }
        return base
    try:
        proposal = json_load_object(execution.result.output_text)
    except Exception:
        base["error"] = {
            "code": "proposal_output_invalid",
            "message": "local assessment is not a JSON object",
        }
        return base
    base["status"] = "complete"
    base["proposal"] = proposal
    return base


def _response_artifact(record: Mapping[str, object]) -> Mapping[str, object] | None:
    extensions = record.get("extensions")
    if not isinstance(extensions, list):
        return None
    for extension in extensions:
        if (
            not isinstance(extension, Mapping)
            or extension.get("name") != "response_artifact"
        ):
            continue
        value = extension.get("value")
        if isinstance(value, Mapping):
            return dict(value)
    return None


def json_load_object(value: str) -> dict[str, object]:
    import json

    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("proposal output is not an object")
    return decoded


def _ref(path: str, content: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(content),
        "media_type": "application/json",
        "size_bytes": len(content),
    }


def _same_artifact_ref(left: object, right: Mapping[str, object]) -> bool:
    """Compare the authenticated reference identity shared by v1/v2 records."""

    if not isinstance(left, Mapping):
        return False
    return all(left.get(key) == right.get(key) for key in (
        "path", "content_sha256", "media_type", "size_bytes"
    ))


def _new_handoff(factory: Callable[[], uuid.UUID]) -> str:
    return f"handoff_{factory()}"


__all__ = [
    "ScoutProposalExecution",
    "ScoutProposalExecutionError",
    "execute_local_proposal",
]
