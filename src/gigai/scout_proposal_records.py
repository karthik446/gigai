"""Host-owned immutable Scout proposal and answer associations.

The model result under ``runs/.../scout-proposals`` is an invocation result,
not a durable proposal revision.  This module adds the smallest R1 record
layer without making SQLite authoritative or asking the model to repeat
lineage.  Records are written through the existing journal writer and are
read only when their exact artifact publication can be authenticated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import re
from pathlib import Path
from typing import Callable
import uuid
import json

from .canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes, parse_json_front_matter
from .journal import JournalArtifact, JournalEntry, JournalTransition, JournalWriter, JournalSnapshot, _git, _git_bytes, read_committed_artifact, run_with_journal_writer
from .scout_proposals import ScoutProposalRequest, validate_proposal_output
from .validators import validate_model_invocation, validate_serialized_contract
from .scout_proposal_execution import ScoutProposalExecution, _resolve_sources
from .scout_inputs import resolve_external_input, _record_revision
from .native_records import _sidecar
from .scout_posting_inputs import resolve_discovery_posting_input
from .workpad import ResolvedWorkpad


_ID = re.compile(r"\A(?:record|revision|run|goal)_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_OPPORTUNITY = re.compile(r"\Aopportunity_[0-9a-f]{32}\Z")
_SNAPSHOT = re.compile(r"\Asnapshot_[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_PURPOSES = frozenset({"preferences", "experience", "answer"})
_MAX_BYTES = 512 * 1024
_MAX_INPUTS = 12


class ScoutProposalRecordError(ValueError):
    """Redacted refusal to publish or read an R1 record."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProposalRevisionResult:
    record: dict[str, object]
    created: bool
    entry: JournalEntry | None


@dataclass(frozen=True)
class AnswerAssociationResult:
    association: dict[str, object]
    created: bool
    entry: JournalEntry | None


def _fail(code: str, message: str) -> None:
    raise ScoutProposalRecordError(code, message)


def _id(value: object, prefix: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or not value.startswith(prefix + "_"):
        _fail("proposal_record_invalid", f"{prefix} identity is invalid")
    return value


def _ref(value: object, *, data: bytes | None = None) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"path", "content_sha256", "media_type", "size_bytes"}:
        _fail("proposal_record_invalid", "artifact reference is malformed")
    path, digest, size = value.get("path"), value.get("content_sha256"), value.get("size_bytes")
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or ".." in path.split("/"):
        _fail("proposal_record_invalid", "artifact path is invalid")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest) or type(size) is not int or size < 0 or size > _MAX_BYTES:
        _fail("proposal_record_invalid", "artifact reference bounds are invalid")
    if data is not None and (digest_imported_bytes(data) != digest or len(data) != size):
        _fail("proposal_record_invalid", "artifact digest does not match bytes")
    return dict(value)


def _source_ref(source: Mapping[str, object]) -> dict[str, object]:
    identity = source.get("identity")
    if not isinstance(identity, Mapping):
        _fail("proposal_record_invalid", "source identity is unavailable")
    family = identity.get("family")
    key = "posting_ref" if family == "scout_discovery_posting" else ("blob_ref" if family == "scout_record" else "snapshot_ref")
    candidate = identity.get(key)
    if family == "scout_discovery_posting" and isinstance(candidate, Mapping):
        candidate = candidate.get("ref")
    return _ref(candidate)


def _plain(value: object) -> object:
    """Copy MappingProxyType/nested host mappings into canonical JSON values."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=dict))
    except (TypeError, ValueError, UnicodeError) as exc:
        _fail("proposal_record_invalid", "record value is not canonical data")
        raise AssertionError from exc


def _validate_opportunity(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail("proposal_record_invalid", "opportunity association is invalid")
    expected = {"family", "opportunity_id", "snapshot_id", "run_ref", "receipt_ref", "checkpoint_ref", "posting_ref"}
    if set(value) != expected or value.get("family") != "scout_discovery_posting":
        _fail("proposal_record_invalid", "opportunity association has unknown fields")
    if not isinstance(value.get("opportunity_id"), str) or _OPPORTUNITY.fullmatch(value["opportunity_id"]) is None or not isinstance(value.get("snapshot_id"), str) or _SNAPSHOT.fullmatch(value["snapshot_id"]) is None:
        _fail("proposal_record_invalid", "opportunity identity is invalid")
    for key in ("run_ref", "receipt_ref", "checkpoint_ref"):
        _ref(value[key])
    posting = value["posting_ref"]
    if not isinstance(posting, Mapping) or set(posting) != {"artifact_id", "ref"} or not isinstance(posting.get("artifact_id"), str) or not posting["artifact_id"]:
        _fail("proposal_record_invalid", "posting capture reference is invalid")
    _ref(posting["ref"])
    return {key: value[key] for key in expected}


def _validate_inputs(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value or len(value) > _MAX_INPUTS:
        _fail("proposal_record_invalid", "selected private inputs are out of bounds")
    seen: set[tuple[str, str, str]] = set()
    output: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"record_id", "revision_id", "purpose", "content_sha256", "source_ref"}:
            _fail("proposal_record_invalid", "private input association is malformed")
        record_id, revision_id, purpose, digest = item.get("record_id"), item.get("revision_id"), item.get("purpose"), item.get("content_sha256")
        _id(record_id, "record")
        _id(revision_id, "revision")
        if not isinstance(purpose, str) or purpose not in _PURPOSES or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            _fail("proposal_record_invalid", "private input purpose or digest is invalid")
        _ref(item["source_ref"])
        marker = (record_id, revision_id, purpose)
        if marker in seen:
            _fail("proposal_record_invalid", "private inputs contain duplicate identities")
        seen.add(marker)
        output.append(dict(item))
    return output


def _validate_record(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail("proposal_record_invalid", "proposal record is not an object")
    required = {"schema_version", "record_id", "revision_id", "project_id", "gig_id", "state", "opportunity", "assessment", "input_revisions", "answer_associations", "invocation", "method", "sealed_journal_head", "created_at"}
    if set(value) != required or value.get("schema_version") != "scout-proposal-revision:1" or value.get("state") != "active":
        _fail("proposal_record_invalid", "proposal record fields are not closed")
    _id(value.get("record_id"), "record")
    _id(value.get("revision_id"), "revision")
    if not isinstance(value.get("project_id"), str) or not isinstance(value.get("gig_id"), str) or not isinstance(value.get("sealed_journal_head"), str) or not value["sealed_journal_head"]:
        _fail("proposal_record_invalid", "proposal record scope is invalid")
    _validate_opportunity(value.get("opportunity"))
    assessment = value.get("assessment")
    if not isinstance(assessment, Mapping) or set(assessment) != {"status", "proposal", "content_sha256"} or assessment.get("status") != "complete" or not isinstance(assessment.get("proposal"), Mapping) or not isinstance(assessment.get("content_sha256"), str) or not _DIGEST.fullmatch(assessment["content_sha256"]):
        _fail("proposal_record_invalid", "proposal assessment is incomplete")
    if digest_imported_bytes(canonical_json_bytes(dict(assessment["proposal"]))) != assessment["content_sha256"]:
        _fail("proposal_record_invalid", "proposal assessment digest differs")
    _validate_inputs(value.get("input_revisions"))
    answers = value.get("answer_associations")
    if not isinstance(answers, list) or len(answers) > _MAX_INPUTS:
        _fail("proposal_record_invalid", "answer associations are out of bounds")
    for answer in answers:
        if not isinstance(answer, Mapping) or set(answer) != {"record_id", "revision_id", "question_ids", "purpose", "content_sha256"} or answer.get("purpose") != "answer" or not isinstance(answer.get("question_ids"), list) or not answer["question_ids"] or len(answer["question_ids"]) > 64 or not all(isinstance(item, str) and 1 <= len(item) <= 160 for item in answer["question_ids"]):
            _fail("proposal_record_invalid", "answer association is malformed")
        _id(answer.get("record_id"), "record")
        _id(answer.get("revision_id"), "revision")
        if len(set(answer["question_ids"])) != len(answer["question_ids"]):
            _fail("proposal_record_invalid", "answer question IDs are duplicated")
        if not isinstance(answer.get("content_sha256"), str) or not _DIGEST.fullmatch(answer["content_sha256"]):
            _fail("proposal_record_invalid", "answer digest is invalid")
    invocation = value.get("invocation")
    if not isinstance(invocation, Mapping) or set(invocation) != {"run_id", "goal_id", "invocation_id", "record_sha256"} or not isinstance(invocation.get("invocation_id"), str) or not invocation["invocation_id"] or not isinstance(invocation.get("record_sha256"), str) or not _DIGEST.fullmatch(invocation["record_sha256"]):
        _fail("proposal_record_invalid", "invocation association is malformed")
    _id(invocation.get("run_id"), "run")
    _id(invocation.get("goal_id"), "goal")
    method = value.get("method")
    if not isinstance(method, Mapping) or set(method) != {"kind", "target", "configured_digest"} or method.get("kind") != "ollama_local" or not isinstance(method.get("target"), str) or not method["target"] or not isinstance(method.get("configured_digest"), str) or not _DIGEST.fullmatch(method["configured_digest"]):
        _fail("proposal_record_invalid", "local method identity is malformed")
    schema_report = validate_serialized_contract(
        "scout-proposal-revision.schema.json", canonical_json_bytes(dict(value))
    )
    if not schema_report.valid:
        _fail("proposal_record_invalid", "proposal record failed its versioned schema")
    return dict(value)


def _same_ref(actual: object, expected: object) -> bool:
    return isinstance(actual, Mapping) and isinstance(expected, Mapping) and dict(actual) == dict(expected)


def _redeem_pinned_record(
    *, resolved: ResolvedWorkpad, snapshot: JournalSnapshot, record: Mapping[str, object]
) -> None:
    """Redeem proposal lineage through the existing pinned host resolvers."""
    opportunity = record.get("opportunity")
    if not isinstance(opportunity, Mapping):
        _fail("proposal_lineage_unavailable", "proposal opportunity lineage is unavailable")
    run_ref, receipt_ref, checkpoint_ref = (opportunity.get(key) for key in ("run_ref", "receipt_ref", "checkpoint_ref"))
    refs = (run_ref, receipt_ref, checkpoint_ref)
    paths = []
    for item in refs:
        checked = _ref(item) if isinstance(item, Mapping) else None
        if checked is not None and checked.get("path") in snapshot.artifacts:
            try:
                _ref(item, data=snapshot.artifacts[str(checked["path"])])
            except ScoutProposalRecordError:
                checked = None
        paths.append(checked)
    if any(item is None or item.get("path") not in snapshot.artifacts for item in paths):
        _fail("proposal_lineage_unavailable", "proposal discovery lineage is not committed in the pinned snapshot")
    run_path, receipt_path, checkpoint_path = (str(item["path"]) for item in paths if item is not None)
    run_parts, receipt_parts = Path(run_path).parts, Path(receipt_path).parts
    if len(run_parts) != 3 or run_parts[0] != "runs" or run_parts[2] != "external-run.json" or len(receipt_parts) != 4 or receipt_parts[0] != "runs" or receipt_parts[2] != "receipts":
        _fail("proposal_lineage_invalid", "proposal discovery lineage paths are invalid")
    selector = {
        "family": "scout_discovery",
        "run_id": run_parts[1],
        "receipt_id": receipt_parts[3].removesuffix(".json"),
        "output_kind": "discovery",
        "opportunity_id": opportunity.get("opportunity_id"),
        "snapshot_id": opportunity.get("snapshot_id"),
    }
    try:
        selected = resolve_discovery_posting_input(resolved, snapshot, selector)
    except Exception as exc:
        raise ScoutProposalRecordError("proposal_lineage_unavailable", "proposal discovery lineage could not be redeemed") from exc
    expected_refs = (run_ref, receipt_ref, checkpoint_ref)
    actual_refs = (selected.get("run_ref"), selected.get("receipt_ref"), selected.get("checkpoint_ref"))
    if any(not _same_ref(actual, expected) for actual, expected in zip(actual_refs, expected_refs)):
        _fail("proposal_lineage_mismatch", "proposal discovery references differ from the redeemed posting")
    posting_ref = opportunity.get("posting_ref")
    actual_posting = selected.get("posting_ref")
    if not isinstance(posting_ref, Mapping) or not isinstance(actual_posting, Mapping) or posting_ref.get("artifact_id") != actual_posting.get("artifact_id") or not _same_ref(posting_ref.get("ref"), actual_posting.get("ref")):
        _fail("proposal_lineage_mismatch", "proposal posting reference differs from the redeemed posting")
    if str(checkpoint_path) != str(selected.get("checkpoint_ref", {}).get("path")):
        _fail("proposal_lineage_mismatch", "proposal checkpoint reference differs from the redeemed posting")

    for item in record.get("input_revisions", []):
        if not isinstance(item, Mapping):
            _fail("proposal_lineage_invalid", "proposal private input lineage is malformed")
        record_id, revision_id, source_ref = item.get("record_id"), item.get("revision_id"), item.get("source_ref")
        try:
            revision = _record_revision(resolved, snapshot, record_id, revision_id)
            sidecar, _sidecar_bytes = _sidecar(resolved, snapshot, revision)
            scope = sidecar.get("scope")
            if not isinstance(scope, Mapping):
                raise ValueError
            selected_input = resolve_external_input(resolved, snapshot, {"family": "scout_record", "record_id": record_id, "revision_id": revision_id, "scope": {"mode": scope.get("mode"), "task_context_id": scope.get("task_context_id")}})
        except Exception as exc:
            raise ScoutProposalRecordError("proposal_input_unavailable", "proposal private input could not be redeemed") from exc
        content = selected_input.get("content") if isinstance(selected_input, Mapping) else None
        actual_ref = content.get("blob_ref") if isinstance(content, Mapping) else None
        if not _same_ref(actual_ref, source_ref) or not isinstance(actual_ref, Mapping) or item.get("content_sha256") != actual_ref.get("content_sha256"):
            _fail("proposal_input_mismatch", "proposal private input reference differs from committed bytes")
        purpose = item.get("purpose")
        if purpose == "answer":
            questions = sidecar.get("payload", {}).get("questions", []) if isinstance(sidecar.get("payload"), Mapping) else []
            known = {q.get("question_id") for q in questions if isinstance(q, Mapping) and isinstance(q.get("question_id"), str)}
            for association in record.get("answer_associations", []):
                if isinstance(association, Mapping) and association.get("record_id") == record_id and association.get("revision_id") == revision_id and not set(association.get("question_ids", [])) <= known:
                    _fail("proposal_answer_mismatch", "proposal answer question is not in the redeemed native revision")

    invocation = record.get("invocation")
    if not isinstance(invocation, Mapping):
        _fail("proposal_invocation_unavailable", "proposal invocation lineage is unavailable")
    run_id, invocation_id = invocation.get("run_id"), invocation.get("invocation_id")
    result_path = f"runs/{run_id}/scout-proposals/{invocation_id}/result.json"
    result_raw = snapshot.artifacts.get(result_path)
    if result_raw is None:
        _fail("proposal_invocation_unavailable", "proposal result is not in the pinned snapshot")
    try:
        result = parse_json_bytes(result_raw)
    except ValueError as exc:
        raise ScoutProposalRecordError("proposal_invocation_invalid", "proposal result is invalid") from exc
    result_head = result.get("sealed_journal_head") if isinstance(result, Mapping) else None
    record_head = record.get("sealed_journal_head")
    if (
        not isinstance(result, Mapping)
        or result.get("status") != "complete"
        or result.get("invocation_id") != invocation_id
        or not isinstance(result_head, str)
        or not isinstance(record_head, str)
        or not re.fullmatch(r"[0-9a-f]{40}", result_head)
        or not re.fullmatch(r"[0-9a-f]{40}", record_head)
        or _git(resolved.path, "merge-base", "--is-ancestor", result_head, snapshot.head, check=False).returncode != 0
        or _git(resolved.path, "merge-base", "--is-ancestor", record_head, snapshot.head, check=False).returncode != 0
        or result.get("invocation_record_sha256") != invocation.get("record_sha256")
        or result.get("proposal") != record.get("assessment", {}).get("proposal")
    ):
        _fail("proposal_invocation_mismatch", "proposal result does not bind the recorded assessment")
    if digest_imported_bytes(canonical_json_bytes(dict(result.get("proposal", {})))) != record.get("assessment", {}).get("content_sha256"):
        _fail("proposal_assessment_mismatch", "proposal assessment digest differs from the committed result")
    invocation_record_path = f"runs/{run_id}/model-invocations/{invocation_id}/record.json"
    invocation_record_raw = snapshot.artifacts.get(invocation_record_path)
    if invocation_record_raw is None:
        _fail("proposal_invocation_unavailable", "proposal invocation record is not in the pinned snapshot")
    try:
        invocation_record = parse_json_bytes(invocation_record_raw)
    except ValueError as exc:
        raise ScoutProposalRecordError("proposal_invocation_invalid", "proposal invocation record is invalid") from exc
    if (
        not validate_model_invocation(invocation_record).valid
        or digest_imported_bytes(canonical_json_bytes(dict(invocation_record))) != invocation.get("record_sha256")
        or invocation_record.get("run_id") != run_id
        or invocation_record.get("invocation_id") != invocation_id
    ):
        _fail("proposal_invocation_mismatch", "proposal invocation record does not bind its result")


def answer_association_for_source(*, source: Mapping[str, object], question_ids: list[str]) -> dict[str, object]:
    """Build a closed answer association from one host-selected native source.

    ``source`` is the already authenticated descriptor produced by the
    proposal execution resolver; this helper never accepts model-provided
    record or question identities as authority.
    """
    if source.get("family") != "scout_record" or source.get("native_kind") != "experience_qa":
        _fail("answer_source_invalid", "answers require an experience_qa native revision")
    record_id, revision_id = source.get("record_id"), source.get("revision_id")
    _id(record_id, "record")
    _id(revision_id, "revision")
    if not isinstance(question_ids, list) or not question_ids or len(question_ids) > 64 or not all(isinstance(item, str) and 1 <= len(item) <= 160 for item in question_ids) or len(set(question_ids)) != len(question_ids):
        _fail("answer_association_invalid", "answer question selection is invalid")
    digest = source.get("content_sha256")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        _fail("answer_association_invalid", "answer source digest is invalid")
    return {"record_id": record_id, "revision_id": revision_id, "question_ids": list(question_ids), "purpose": "answer", "content_sha256": digest}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _handoff_ref(path: str, data: bytes) -> dict[str, object]:
    return {"path": path, "content_sha256": digest_imported_bytes(data), "media_type": "application/json", "size_bytes": len(data)}


def _existing_operation(root, operation_key: str) -> dict[str, object] | None:
    names = _git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", "handoffs/").stdout.splitlines()
    for name in names:
        if not name.endswith(".txt"):
            continue
        metadata, _ = parse_json_front_matter(_git_bytes(root, "show", f"HEAD:{name}"))
        if metadata.get("transition") == "private_record_revised" and metadata.get("domain") == "scout-proposal" and metadata.get("operation_key") == operation_key:
            refs = metadata.get("artifact_refs")
            if isinstance(refs, list) and len(refs) == 1 and isinstance(refs[0], Mapping):
                data, _ = read_committed_artifact(workpad=root, project_id=str(metadata.get("project_id", "")), gig_id=str(metadata.get("gig_id", "")), path=str(refs[0].get("path")))
                return _validate_record(parse_json_bytes(data))
    return None


def record_proposal_revision(*, resolved: ResolvedWorkpad, execution: ScoutProposalExecution, posting_selector: Mapping[str, object], private_selectors: tuple[Mapping[str, object], ...], model_target: str, configured_digest: str, uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4) -> ProposalRevisionResult:
    """Persist one host-bound complete result; identical operation keys replay."""
    if execution.result.get("status") != "complete" or not isinstance(execution.result.get("proposal"), Mapping):
        _fail("proposal_result_incomplete", "only a complete local assessment can be recorded")
    try:
        report = validate_proposal_output(execution.result["proposal"])
    except Exception as exc:
        raise ScoutProposalRecordError("proposal_result_invalid", "proposal result failed bounded validation") from exc
    if not report.valid:
        _fail("proposal_result_invalid", "proposal result failed bounded validation")
    if not isinstance(configured_digest, str) or not _DIGEST.fullmatch(configured_digest):
        _fail("proposal_record_invalid", "configured local model digest is invalid")
    invocation = execution.invocation.record
    run_id, goal_id, invocation_id = invocation.get("run_id"), invocation.get("goal_id"), invocation.get("invocation_id")
    _id(run_id, "run")
    _id(goal_id, "goal")
    if not isinstance(invocation_id, str) or not invocation_id:
        _fail("proposal_record_invalid", "invocation identity is invalid")
    posting, private, sealed_head = _resolve_sources(resolved, posting_selector, private_selectors)
    request = ScoutProposalRequest(posting=posting, private_sources=private)
    if execution.result.get("input_lineage") != request.lineage():
        _fail("proposal_lineage_mismatch", "committed source lineage differs from assessment")
    posting_path = str(posting.identity["posting_ref"]["path"])
    artifact_id = posting_path.rsplit("/", 1)[-1].removesuffix(".bin")
    if not artifact_id:
        _fail("proposal_record_invalid", "posting capture identity is unavailable")
    opportunity = _plain({
        "family": "scout_discovery_posting", "opportunity_id": posting.identity["opportunity_id"], "snapshot_id": posting.identity["snapshot_id"],
        "run_ref": posting.identity["run_ref"], "receipt_ref": posting.identity["receipt_ref"], "checkpoint_ref": posting.identity["checkpoint_ref"],
        "posting_ref": {"artifact_id": artifact_id, "ref": posting.identity["posting_ref"]},
    })
    inputs: list[dict[str, object]] = []
    answers: list[dict[str, object]] = []
    for source in private:
        identity = source.identity
        if identity.get("family") != "scout_record":
            continue
        item = {"record_id": identity.get("record_id"), "revision_id": identity.get("revision_id"), "purpose": source.purpose, "content_sha256": source.content_sha256, "source_ref": _plain(identity.get("blob_ref"))}
        inputs.append(item)
        if source.purpose == "answer":
            try:
                sidecar = parse_json_bytes(source.content)
                questions = sidecar.get("payload", {}).get("questions", []) if isinstance(sidecar, Mapping) else []
                qids = [item.get("question_id") for item in questions if isinstance(item, Mapping) and isinstance(item.get("question_id"), str)]
            except Exception:
                qids = []
            if qids:
                answers.append(answer_association_for_source(source={
                    "family": identity.get("family"), "native_kind": identity.get("native_kind"),
                    "record_id": identity.get("record_id"), "revision_id": identity.get("revision_id"),
                    "content_sha256": source.content_sha256,
                }, question_ids=qids))
    inputs = _validate_inputs(inputs)
    if not inputs:
        _fail("proposal_inputs_missing", "at least one authenticated native private revision is required")
    assessment_bytes = canonical_json_bytes(dict(execution.result["proposal"]))
    record_id = f"record_{uuid_factory()}"
    revision_id = f"revision_{uuid_factory()}"
    operation_key = "proposal-" + digest_imported_bytes(canonical_json_bytes({"invocation_id": invocation_id, "posting": opportunity, "inputs": inputs, "answers": answers, "assessment": digest_imported_bytes(assessment_bytes)})).removeprefix("sha256:")
    path = f"records/scout-proposals/{record_id}/revisions/{revision_id}.json"

    def operation(writer: JournalWriter) -> ProposalRevisionResult:
        existing = _existing_operation(writer.root, operation_key)
        if existing is not None:
            return ProposalRevisionResult(existing, False, None)
        record = _validate_record({
            "schema_version": "scout-proposal-revision:1", "record_id": record_id, "revision_id": revision_id,
            "project_id": resolved.project_id, "gig_id": resolved.gig_id, "state": "active", "opportunity": opportunity,
            "assessment": {"status": "complete", "proposal": dict(execution.result["proposal"]), "content_sha256": digest_imported_bytes(assessment_bytes)},
            "input_revisions": inputs, "answer_associations": answers,
            "invocation": {"run_id": run_id, "goal_id": goal_id, "invocation_id": invocation_id, "record_sha256": execution.result.get("invocation_record_sha256")},
            "method": {"kind": "ollama_local", "target": model_target, "configured_digest": configured_digest}, "sealed_journal_head": sealed_head, "created_at": _now(),
        })
        data = canonical_json_bytes(record)
        refs = [_handoff_ref(path, data)]
        entry = writer.record(JournalTransition(
            f"handoff_{uuid_factory()}", "private_record_revised", "Immutable Scout proposal revision recorded.", (JournalArtifact(path, data),),
            {"project_id": resolved.project_id, "gig_id": resolved.gig_id, "run_id": run_id, "goal_id": goal_id, "domain": "scout-proposal", "operation_key": operation_key, "source": "scout-proposal-records", "actor": {"kind": "gigai", "id": "scout-proposal-records", "model_target": model_target}, "outcome": "COMMITTED", "artifact_refs": refs},
        ))
        return ProposalRevisionResult(record, True, entry)

    return run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=operation)


def read_proposal_revision(*, resolved: ResolvedWorkpad, record_id: str, revision_id: str, snapshot: JournalSnapshot | None = None) -> dict[str, object]:
    """Read one exact immutable proposal record from authenticated publication."""
    _id(record_id, "record")
    _id(revision_id, "revision")
    if snapshot is None:
        try:
            return run_with_journal_writer(
                workpad=resolved.path,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
                operation=lambda writer: read_proposal_revision(
                    resolved=resolved,
                    record_id=record_id,
                    revision_id=revision_id,
                    snapshot=writer.snapshot(("records/", "runs/", "run-plans/", "references/", "run-inputs/", "manifests/graph-sets/", "manifests/software/")),
                ),
            )
        except ScoutProposalRecordError:
            raise
        except Exception as exc:
            raise ScoutProposalRecordError("proposal_record_unavailable", "proposal revision is not authenticated") from exc
    path = f"records/scout-proposals/{record_id}/revisions/{revision_id}.json"
    try:
        data = snapshot.artifacts[path]
        value = _validate_record(parse_json_bytes(data))
        _redeem_pinned_record(resolved=resolved, snapshot=snapshot, record=value)
        return value
    except ScoutProposalRecordError:
        raise
    except Exception as exc:
        raise ScoutProposalRecordError("proposal_record_unavailable", "proposal revision is not authenticated") from exc


def validate_proposal_revision(value: object) -> dict[str, object]:
    """Validate a decoded proposal revision without granting publication authority."""
    return _validate_record(value)


__all__ = ["AnswerAssociationResult", "ProposalRevisionResult", "ScoutProposalRecordError", "answer_association_for_source", "read_proposal_revision", "record_proposal_revision", "validate_proposal_revision"]
