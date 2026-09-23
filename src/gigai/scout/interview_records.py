"""Journal-backed interview preparation records for Scout.

Interview preparation is deliberately a small record service.  It accepts
already selected, immutable Scout inputs and records a new revision for every
preparation or feedback change.  It does not select private records, infer an
application, call a model, or execute a copied tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Callable, Mapping, Sequence
import uuid

from jsonschema import Draft202012Validator

from ..canonical import EntityPrefix, canonical_json_bytes, digest_imported_bytes, parse_json_bytes, validate_entity_id
from ..journal import JournalArtifact, JournalConflictError, JournalSnapshot, JournalTransition, run_with_journal_writer
from ..workpad import ResolvedWorkpad, resolve_workpad
from .inputs import resolve_external_input
from ..validators import validate_serialized_contract


class ScoutInterviewError(RuntimeError):
    """Content-free refusal for an invalid interview selection or revision."""

    def __init__(self, code: str, message: str, *, questions: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.questions = tuple(questions)


@dataclass(frozen=True)
class InterviewResult:
    record_id: str
    revision_id: str
    created: bool
    record: Mapping[str, object]


_INTERVIEW_SCHEMA = Path(__file__).parent.parent / "schemas" / "scout-interview-preparation.schema.json"
_SOURCE_FAMILIES = frozenset({"scout_record", "scout_research", "scout_discovery", "scout_interview"})
_SOURCE_KINDS = frozenset({"posting", "research", "experience", "feedback", "preparation"})


def _validate_interview_bytes(data: bytes) -> None:
    """Validate both the frozen local schema and the central registry when present."""
    try:
        value = json.loads(_INTERVIEW_SCHEMA.read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(value).iter_errors(json.loads(data)), key=lambda item: list(item.path))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ScoutInterviewError("interview_schema_unavailable", "interview preparation schema is unavailable") from exc
    if errors:
        raise ScoutInterviewError("interview_schema_invalid", "interview preparation does not meet the closed schema")
    # The comparison lane registers this resource centrally.  Keep the local
    # validator as the publication guard while that shared inventory is being
    # updated, and use the central validator automatically once available.
    report = validate_serialized_contract(_INTERVIEW_SCHEMA.name, data)
    if report.findings and not any(item.code == "unknown_schema" for item in report.findings):
        raise ScoutInterviewError("interview_schema_invalid", "interview preparation failed the central schema")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _id(prefix: EntityPrefix, factory: Callable[[], uuid.UUID]) -> str:
    value = factory()
    if type(value) is not uuid.UUID or value.version != 4:
        raise ScoutInterviewError("interview_invalid", "ID factory must return UUIDv4")
    return f"{prefix.value}_{value}"


def _resolved(*, home_root: Path, requested_target: Path | None, gig_id: str | None) -> ResolvedWorkpad:
    try:
        return resolve_workpad(
            home_root=home_root, requested_target=requested_target, gig_id=gig_id,
            allow_semantic_state=True,
        )
    except Exception as exc:
        raise ScoutInterviewError("interview_workpad_unavailable", "Scout workpad is unavailable") from exc


def _text(value: object, name: str, *, max_length: int = 16_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ScoutInterviewError("interview_invalid", f"{name} must be bounded text")
    if any(ord(char) < 32 and char not in "\t\n" for char in value):
        raise ScoutInterviewError("interview_invalid", f"{name} contains a control character")
    return value


def _ref(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ScoutInterviewError("interview_input_missing", f"{name} must be an immutable record reference")
    result = dict(value)
    family = result.get("family")
    if family not in _SOURCE_FAMILIES:
        raise ScoutInterviewError("interview_input_invalid", f"{name} has an unsupported source family")
    if result.get("kind") not in _SOURCE_KINDS:
        raise ScoutInterviewError("interview_input_missing", f"{name} has no source kind")
    if not isinstance(result.get("artifact_ref"), Mapping):
        raise ScoutInterviewError("interview_input_missing", f"{name} has no exact artifact reference")
    artifact = result["artifact_ref"]
    assert isinstance(artifact, Mapping)
    if (
        not isinstance(artifact.get("path"), str)
        or not isinstance(artifact.get("content_sha256"), str)
        or not isinstance(artifact.get("media_type"), str)
        or type(artifact.get("size_bytes")) is not int
        or artifact.get("size_bytes", -1) < 0
    ):
        raise ScoutInterviewError("interview_input_invalid", f"{name} has an incomplete artifact reference")
    if family in {"scout_record", "scout_interview"}:
        if not isinstance(result.get("record_id"), str) or not isinstance(result.get("revision_id"), str):
            raise ScoutInterviewError("interview_input_missing", f"{name} must name a record and revision")
        try:
            validate_entity_id(result["record_id"])
            validate_entity_id(result["revision_id"])
        except (TypeError, ValueError) as exc:
            raise ScoutInterviewError("interview_input_invalid", f"{name} has a non-canonical record reference") from exc
    else:
        for field in ("run_id", "receipt_id", "output_kind"):
            if not isinstance(result.get(field), str) or not result[field]:
                raise ScoutInterviewError("interview_input_missing", f"{name} has incomplete Run identity")
        try:
            validate_entity_id(result["run_id"])
            validate_entity_id(result["receipt_id"])
        except (TypeError, ValueError) as exc:
            raise ScoutInterviewError("interview_input_invalid", f"{name} has a non-canonical Run identity") from exc
    return result


def _refs(value: object, name: str, *, required: bool = False) -> list[dict[str, object]]:
    if value is None:
        value = []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ScoutInterviewError("interview_input_invalid", f"{name} must be a list")
    result = [_ref(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if required and not result:
        raise ScoutInterviewError("interview_input_missing", f"{name} must not be empty")
    return result


def _selection(selection: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(selection, Mapping):
        raise ScoutInterviewError("interview_input_missing", "selection is required")
    if any(key in selection for key in ("application", "application_state", "applied", "employer_decision")):
        raise ScoutInterviewError("interview_application_state_refused", "interview selection cannot carry application state")
    mode = selection.get("mode")
    if mode not in {"role_only", "opportunity"}:
        raise ScoutInterviewError("interview_selection_invalid", "selection mode must be role_only or opportunity")
    role = selection.get("role")
    if not isinstance(role, Mapping):
        raise ScoutInterviewError("interview_input_missing", "role selection is required")
    role_value = dict(role)
    if not isinstance(role_value.get("title"), str) or not role_value["title"].strip():
        raise ScoutInterviewError("interview_questions_required", "role title is required", questions=("Which role or job family should this preparation target?",))
    posting = selection.get("posting")
    if mode == "opportunity":
        if not isinstance(posting, Mapping):
            raise ScoutInterviewError("interview_snapshot_required", "opportunity preparation requires an exact posting snapshot")
        if not isinstance(posting.get("snapshot_id"), str) and not isinstance(posting.get("content_sha256"), str):
            raise ScoutInterviewError("interview_snapshot_required", "posting must include its immutable snapshot identity")
        posting_value = _ref(posting, "posting")
        posting = posting_value
    elif posting is not None:
        raise ScoutInterviewError("interview_selection_invalid", "role-only preparation cannot select an opportunity")
    stage = selection.get("stage_or_format")
    if stage is None:
        stage = {key: selection[key] for key in ("stage", "format") if key in selection}
    if not isinstance(stage, Mapping):
        raise ScoutInterviewError("interview_questions_required", "stage_or_format is required", questions=("Which interview stage or format should we prepare for?",))
    stage_value = dict(stage)
    if not any(isinstance(stage_value.get(key), str) and stage_value[key].strip() for key in ("stage", "format")):
        raise ScoutInterviewError("interview_questions_required", "stage_or_format must name a stage or format", questions=("Which interview stage or format should we prepare for?",))
    research = _refs(selection.get("research_revisions", selection.get("research")), "research_revisions")
    evidence = _refs(selection.get("candidate_evidence", selection.get("experience")), "candidate_evidence")
    feedback = _refs(selection.get("prior_feedback", selection.get("prior_prep_feedback")), "prior_feedback")
    prior = _refs(selection.get("prior_preparation", selection.get("prior_prep")), "prior_preparation")
    reuse = selection.get("reuse_prior_research")
    if reuse is None:
        reuse = selection.get("reuse_prior_research_confirmed")
    if type(reuse) is not bool:
        raise ScoutInterviewError("interview_input_missing", "reuse_prior_research must be explicit")
    if research and not reuse:
        raise ScoutInterviewError("interview_research_reuse_unclear", "selected research requires explicit reuse confirmation")
    return {
        "mode": mode, "role": role_value, "posting": posting,
        "stage_or_format": stage_value, "research_revisions": research,
        "candidate_evidence": evidence, "prior_feedback": feedback,
        "prior_preparation": prior, "reuse_prior_research": reuse,
    }


def _verify_selected_refs(resolved: ResolvedWorkpad, selection: Mapping[str, object]) -> None:
    """Retained compatibility shim; publication performs the real check under CAS."""
    return None


def _verify_content_refs(resolved: ResolvedWorkpad, content: Mapping[str, object]) -> None:
    return None


def _verify_refs(resolved: ResolvedWorkpad, refs: Sequence[Mapping[str, object]]) -> None:
    return None


def _artifact(snapshot: JournalSnapshot, value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ScoutInterviewError("interview_source_unavailable", f"{name} artifact reference is unavailable")
    path, digest, media, size = value.get("path"), value.get("content_sha256"), value.get("media_type"), value.get("size_bytes")
    if not isinstance(path, str) or path.startswith("/") or any(char == chr(92) for char in path) or ".." in Path(path).parts or not isinstance(digest, str) or not isinstance(media, str) or type(size) is not int:
        raise ScoutInterviewError("interview_source_invalid", f"{name} artifact reference is malformed")
    data = snapshot.artifacts.get(path)
    if data is None or digest_imported_bytes(data) != digest or len(data) != size:
        raise ScoutInterviewError("interview_source_digest_mismatch", f"{name} artifact bytes are not committed")
    return {"path": path, "content_sha256": digest, "media_type": media, "size_bytes": size}


def _source_selector(ref: Mapping[str, object]) -> dict[str, object]:
    family = ref.get("family")
    if family == "scout_record":
        return {key: ref[key] for key in ("family", "record_id", "revision_id", "scope")}
    if family == "scout_research":
        return {key: ref[key] for key in ("family", "run_id", "receipt_id", "output_kind")}
    if family == "scout_discovery":
        return {
            key: ref[key]
            for key in ("family", "run_id", "receipt_id", "output_kind", "opportunity_id", "snapshot_id")
        }
    return dict(ref)


def _authenticate_ref(*, resolved: ResolvedWorkpad, writer: object, snapshot: JournalSnapshot, ref: Mapping[str, object], expected_kind: str, name: str) -> dict[str, object]:
    if ref.get("kind") != expected_kind:
        raise ScoutInterviewError("interview_source_kind_mismatch", f"{name} is not an admitted {expected_kind} source")
    family = ref.get("family")
    artifact = _artifact(snapshot, ref.get("artifact_ref"), name=name)
    if family in {"scout_record", "scout_research", "scout_discovery"}:
        try:
            kwargs = {"allow_research_run": family == "scout_research", "allow_discovery_posting": family == "scout_discovery", "writer": writer}
            current = resolve_external_input(resolved, snapshot, _source_selector(ref), **kwargs)
        except Exception as exc:
            raise ScoutInterviewError("interview_source_unavailable", f"{name} is not an authenticated committed source") from exc
        if current.get("project_id") not in {None, resolved.project_id} or current.get("gig_id") not in {None, resolved.gig_id}:
            raise ScoutInterviewError("interview_source_scope_refused", f"{name} belongs to another project or Gig")
        if family == "scout_record":
            admitted_kind = current.get("native_kind")
            if expected_kind == "experience" and admitted_kind != "experience_qa":
                raise ScoutInterviewError("interview_source_kind_mismatch", f"{name} is not an experience record")
            if expected_kind == "posting" and admitted_kind != "supplied_source":
                raise ScoutInterviewError("interview_source_kind_mismatch", f"{name} is not a supplied posting record")
            if expected_kind not in {"experience", "posting"}:
                raise ScoutInterviewError("interview_source_kind_mismatch", f"{name} has an incompatible native record kind")
            path = f"records/{ref['record_id']}/revisions/{ref['revision_id']}.json"
            data = snapshot.artifacts.get(path)
            if data is None:
                raise ScoutInterviewError("interview_source_unavailable", f"{name} revision is unavailable")
            canonical = {"path": path, "content_sha256": digest_imported_bytes(data), "media_type": "application/json", "size_bytes": len(data)}
        elif family == "scout_research":
            output = current.get("output")
            canonical = _artifact(snapshot, output.get("domain_sidecar") if isinstance(output, Mapping) else None, name=name)
        else:
            if expected_kind != "posting":
                raise ScoutInterviewError("interview_source_kind_mismatch", f"{name} is not a posting source")
            posting_ref = current.get("posting_ref")
            if isinstance(posting_ref, Mapping) and isinstance(posting_ref.get("ref"), Mapping):
                posting_ref = posting_ref["ref"]
            canonical = _artifact(snapshot, posting_ref, name=name)
        if artifact != canonical:
            raise ScoutInterviewError("interview_source_digest_mismatch", f"{name} does not pin authenticated source bytes")
        normalized_ref = dict(ref)
        # opportunity_id selects the discovery posting but is not part of the
        # closed interview source-ref schema; snapshot_id plus the authenticated
        # Run/receipt identity binds the retained source bytes.
        if family == "scout_discovery":
            normalized_ref.pop("opportunity_id", None)
        return {**normalized_ref, "artifact_ref": canonical, "owner": {"project_id": resolved.project_id, "gig_id": resolved.gig_id}}
    if family == "scout_interview":
        path = _record_path(str(ref.get("record_id")), str(ref.get("revision_id")))
        value = _snapshot_json(snapshot, path)
        if value is None or value.get("project_id") != resolved.project_id or value.get("gig_id") != resolved.gig_id:
            raise ScoutInterviewError("interview_source_scope_refused", f"{name} is not owned by this project or Gig")
        if expected_kind == "feedback" and not _is_actual_feedback_revision(snapshot, value):
            raise ScoutInterviewError("interview_source_kind_mismatch", f"{name} is not a feedback revision")
        if expected_kind == "preparation" and value.get("kind") != "scout_interview_preparation":
            raise ScoutInterviewError("interview_source_kind_mismatch", f"{name} is not a preparation revision")
        data = snapshot.artifacts.get(path)
        if data is None:
            raise ScoutInterviewError("interview_source_unavailable", f"{name} revision is unavailable")
        canonical = {"path": path, "content_sha256": digest_imported_bytes(data), "media_type": "application/json", "size_bytes": len(data)}
        if artifact != canonical:
            raise ScoutInterviewError("interview_source_digest_mismatch", f"{name} does not pin the authenticated revision")
        return {**dict(ref), "artifact_ref": canonical, "owner": {"project_id": resolved.project_id, "gig_id": resolved.gig_id}}
    raise ScoutInterviewError("interview_source_invalid", f"{name} source family is unsupported")


def _authenticate_inputs(*, resolved: ResolvedWorkpad, writer: object, snapshot: JournalSnapshot, selection: Mapping[str, object], content: Mapping[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    normalized = dict(selection)
    groups = (("posting", "posting"), ("research_revisions", "research"), ("candidate_evidence", "experience"), ("prior_feedback", "feedback"), ("prior_preparation", "preparation"))
    selected: list[dict[str, object]] = []
    for field, kind in groups:
        raw = normalized.get(field)
        if field == "posting":
            if raw is None:
                continue
            normalized[field] = _authenticate_ref(resolved=resolved, writer=writer, snapshot=snapshot, ref=raw, expected_kind=kind, name=field)
            selected.append(normalized[field])
            continue
        values = raw if isinstance(raw, list) else []
        checked = [_authenticate_ref(resolved=resolved, writer=writer, snapshot=snapshot, ref=item, expected_kind=kind, name=f"{field}[{index}]") for index, item in enumerate(values)]
        normalized[field] = checked
        selected.extend(checked)
    def key(item: Mapping[str, object]) -> tuple[object, ...]:
        artifact = item.get("artifact_ref")
        digest = artifact.get("content_sha256") if isinstance(artifact, Mapping) else None
        return (item.get("family"), item.get("record_id"), item.get("revision_id"), item.get("run_id"), item.get("receipt_id"), digest)
    selected_keys = {key(item) for item in selected}
    result_content = dict(content)
    stories = []
    for index, story in enumerate(result_content.get("evidence_backed_stories", [])):
        if not isinstance(story, Mapping):
            stories.append(story)
            continue
        story_value = dict(story)
        if story_value.get("label") == "hypothetical":
            stories.append(story_value)
            continue
        refs = story_value.get("source_refs")
        if not isinstance(refs, list) or not refs:
            raise ScoutInterviewError("interview_story_unsupported", f"story {index} must cite selected evidence")
        checked = [_authenticate_ref(resolved=resolved, writer=writer, snapshot=snapshot, ref=item, expected_kind="experience", name=f"stories[{index}].source_refs[{position}]") for position, item in enumerate(refs)]
        if any(key(item) not in selected_keys for item in checked):
            raise ScoutInterviewError("interview_story_source_unselected", "every factual story source must be selected candidate evidence")
        story_value["source_refs"] = checked
        for claim_key in ("claims", "metrics", "skills"):
            claims = story_value.get(claim_key, [])
            if not isinstance(claims, list):
                raise ScoutInterviewError("interview_story_unsupported", f"{claim_key} must be an evidence-backed list")
            normalized_claims = []
            for claim_index, claim in enumerate(claims):
                if not isinstance(claim, Mapping) or claim.get("label") != "hypothetical" and not claim.get("source_refs"):
                    raise ScoutInterviewError("interview_story_unsupported", f"{claim_key} entries require source_refs")
                if isinstance(claim, Mapping) and claim.get("label") != "hypothetical":
                    claim_value = dict(claim)
                    if not isinstance(claim.get("source_refs"), list) or not claim["source_refs"]:
                        raise ScoutInterviewError("interview_story_unsupported", f"{claim_key} entries require source_refs")
                    claim_refs = [
                        _authenticate_ref(
                            resolved=resolved, writer=writer, snapshot=snapshot,
                            ref=item, expected_kind="experience",
                            name=f"stories[{index}].{claim_key}[{claim_index}].source_refs[{ref_index}]",
                        )
                        for ref_index, item in enumerate(claim["source_refs"])
                    ]
                    if any(key(item) not in selected_keys for item in claim_refs):
                        raise ScoutInterviewError("interview_story_source_unselected", f"{claim_key} cites unselected evidence")
                    claim_value["source_refs"] = claim_refs
                    normalized_claims.append(claim_value)
                else:
                    normalized_claims.append(claim)
            story_value[claim_key] = normalized_claims
        stories.append(story_value)
    result_content["evidence_backed_stories"] = stories
    return normalized, result_content


def _story(value: object, index: int) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ScoutInterviewError("interview_story_unsupported", f"story {index} must be an object")
    story = dict(value)
    if story.get("label") == "hypothetical" or story.get("kind") == "hypothetical":
        if story.get("label") != "hypothetical":
            raise ScoutInterviewError("interview_hypothetical_unlabelled", "hypothetical examples must be labelled")
        _text(story.get("prompt"), f"stories[{index}].prompt")
        return story
    if story.get("application_state") is not None or story.get("applied") is not None:
        raise ScoutInterviewError("interview_application_state_refused", "interview preparation cannot record application state")
    _refs(story.get("source_refs"), f"stories[{index}].source_refs", required=True)
    if "metrics" in story and not isinstance(story["metrics"], list):
        raise ScoutInterviewError("interview_story_unsupported", "metrics must be an evidence-backed list")
    if "skills" in story and not isinstance(story["skills"], list):
        raise ScoutInterviewError("interview_story_unsupported", "skills must be an evidence-backed list")
    if "text" in story:
        _text(story["text"], f"stories[{index}].text")
    return story


def _content(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ScoutInterviewError("interview_content_invalid", "preparation content must be an object")
    content = dict(value)
    forbidden = {"application", "application_state", "employer_decision", "invented_metrics", "invented_skills"}
    if forbidden.intersection(content):
        raise ScoutInterviewError("interview_application_state_refused", "preparation cannot invent application state or candidate facts")
    stories_value = content.get("evidence_backed_stories", [])
    if not isinstance(stories_value, Sequence) or isinstance(stories_value, (str, bytes, bytearray)):
        raise ScoutInterviewError("interview_content_invalid", "evidence_backed_stories must be a list")
    stories = [_story(item, index) for index, item in enumerate(stories_value)]
    hypothetical = content.get("hypothetical_examples", [])
    if not isinstance(hypothetical, Sequence) or isinstance(hypothetical, (str, bytes, bytearray)):
        raise ScoutInterviewError("interview_content_invalid", "hypothetical_examples must be a list")
    for index, item in enumerate(hypothetical):
        if not isinstance(item, Mapping) or item.get("label") != "hypothetical":
            raise ScoutInterviewError("interview_hypothetical_unlabelled", f"hypothetical_examples[{index}] must be labelled")
    normalized: dict[str, object] = {
        "role_expectations": content.get("role_expectations", []),
        "technical_topics": content.get("technical_topics", []),
        "evidence_backed_stories": stories,
        "hypothetical_examples": [dict(item) for item in hypothetical],
        "practice_questions": content.get("practice_questions", []),
        "interviewer_questions": content.get("interviewer_questions", []),
        "unresolved_gaps": content.get("unresolved_gaps", []),
    }
    for key, items in normalized.items():
        if not isinstance(items, list):
            raise ScoutInterviewError("interview_content_invalid", f"{key} must be a list")
        for item in items:
            if isinstance(item, str):
                _text(item, key)
    return normalized


def _markdown(record: Mapping[str, object]) -> bytes:
    selection = record["selection"]
    assert isinstance(selection, Mapping)
    role = selection["role"]
    assert isinstance(role, Mapping)
    lines = [f"# Interview preparation: {role['title']}", "", f"Mode: {selection['mode']}", f"Stage/format: {selection['stage_or_format']}", ""]
    content = record["content"]
    assert isinstance(content, Mapping)
    headings = (("Role expectations", "role_expectations"), ("Technical topics", "technical_topics"), ("Evidence-backed stories", "evidence_backed_stories"), ("Hypothetical examples", "hypothetical_examples"), ("Practice questions", "practice_questions"), ("Questions for the interviewer", "interviewer_questions"), ("Unresolved gaps", "unresolved_gaps"))
    for title, key in headings:
        lines.extend([f"## {title}", ""])
        items = content[key]
        assert isinstance(items, list)
        if not items:
            lines.append("_None recorded._")
        else:
            for item in items:
                if isinstance(item, Mapping):
                    label = item.get("text") or item.get("prompt") or item.get("question") or item.get("title") or "Recorded item"
                    lines.append(f"- {label}")
                else:
                    lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def _record_path(record_id: str, revision_id: str) -> str:
    return f"records/scout-interviews/{record_id}/revisions/{revision_id}.json"


def _markdown_path(record_id: str, revision_id: str) -> str:
    return f"docs/interviews/{record_id}/{revision_id}/preparation.md"


def _receipt_path(operation_key: str) -> str:
    digest = digest_imported_bytes(operation_key.encode("utf-8")).removeprefix("sha256:")
    return f"records/operations/interview-{digest}.json"


def _snapshot_json(snapshot: JournalSnapshot, path: str) -> dict[str, object] | None:
    raw = snapshot.artifacts.get(path)
    if raw is None:
        return None
    try:
        if path.startswith("records/scout-interviews/") and path.endswith(".json"):
            _validate_interview_bytes(raw)
        value = parse_json_bytes(raw)
    except (ValueError, ScoutInterviewError) as exc:
        raise ScoutInterviewError("interview_authority_invalid", "committed interview artifact is invalid") from exc
    return value if isinstance(value, dict) else None


def _is_actual_feedback_revision(snapshot: JournalSnapshot, value: Mapping[str, object]) -> bool:
    """Recognize feedback without treating cumulative state as proof.

    Revisions share the installed additive schema, so a new subtype field is
    intentionally not introduced here.  A feedback source must instead have a
    parent revision of the same record and a strict, prefix-preserving
    feedback-list extension.  Initial preparation has no parent, and an
    ordinary preparation edit copies the cumulative list, so neither can be
    relabelled as feedback.
    """
    feedback = value.get("feedback")
    parent_revision = value.get("parent_revision")
    record_id = value.get("record_id")
    if (
        not isinstance(feedback, list)
        or not feedback
        or not isinstance(parent_revision, str)
        or not isinstance(record_id, str)
    ):
        return False
    parent = _snapshot_json(snapshot, _record_path(record_id, parent_revision))
    if parent is None or parent.get("record_id") != record_id:
        return False
    previous = parent.get("feedback")
    if not isinstance(previous, list) or len(feedback) <= len(previous):
        return False
    return feedback[: len(previous)] == previous


def _publish(*, resolved: ResolvedWorkpad, record: dict[str, object], markdown: bytes, operation_key: str, transition: str = "private_record_revised", authenticate: Callable[[object, JournalSnapshot, dict[str, object]], dict[str, object]] | None = None, parent_revision: str | None = None) -> InterviewResult:
    # Generated IDs and timestamps are evidence, not operation identity.  This
    # keeps a retry with the same operation key idempotent while preserving the
    # original committed record and revision IDs.
    record_path = _record_path(str(record["record_id"]), str(record["revision_id"]))
    markdown_path = _markdown_path(str(record["record_id"]), str(record["revision_id"]))
    receipt_path = _receipt_path(operation_key)

    def publish(writer: object) -> tuple[dict[str, object], bool]:
        snapshot = writer.snapshot(("records/", "references/", "run-inputs/", "runs/", "docs/"))  # type: ignore[attr-defined]
        old = _snapshot_json(snapshot, receipt_path)
        current = record
        if authenticate is not None:
            current = authenticate(writer, snapshot, current)
        if parent_revision is not None:
            rows = []
            prefix = f"records/scout-interviews/{record['record_id']}/revisions/"
            for path in snapshot.artifacts:
                if path.startswith(prefix) and path.endswith(".json"):
                    value = _snapshot_json(snapshot, path)
                    if value is not None:
                        rows.append(value)
            rows.sort(key=lambda item: str(item.get("created_at", "")))
            if not rows or rows[-1].get("revision_id") != parent_revision:
                raise ScoutInterviewError("interview_stale_parent", "revision does not name the current preparation")
        current_markdown = _markdown(current)
        current_bytes = canonical_json_bytes(current)
        _validate_interview_bytes(current_bytes)
        identity_record = {key: value for key, value in current.items() if key not in {"record_id", "revision_id", "created_at"}}
        payload = {"record": identity_record, "markdown_sha256": digest_imported_bytes(current_markdown)}
        payload_sha = digest_imported_bytes(canonical_json_bytes(payload))
        if old is not None:
            if old.get("payload_sha256") != payload_sha:
                raise ScoutInterviewError("interview_operation_conflict", "operation key was already used with different content")
            # Replay returns the committed bytes exactly and never allocates a
            # second revision.
            return old, False
        receipt = {
            "schema_version": "1.0", "operation": "scout_interview", "operation_key": operation_key,
            "payload_sha256": payload_sha, "outcome": "committed", "record_id": current["record_id"],
            "revision_id": current["revision_id"], "created_at": _now(),
        }
        artifacts = (
            JournalArtifact(record_path, current_bytes),
            JournalArtifact(markdown_path, current_markdown),
            JournalArtifact(receipt_path, canonical_json_bytes(receipt)),
        )
        refs = [{"path": item.path, "content_sha256": digest_imported_bytes(item.content), "size_bytes": len(item.content)} for item in artifacts]
        try:
            writer.record(JournalTransition(
                _id(EntityPrefix.HANDOFF, uuid.uuid4), transition,
                "Committed Scout interview preparation or feedback revision.", artifacts,
                {"operation": "scout_interview", "operation_key": operation_key, "payload_sha256": payload_sha, "artifact_refs": refs},
            ))  # type: ignore[attr-defined]
        except JournalConflictError as exc:
            raise ScoutInterviewError("interview_operation_conflict", "journal refused the interview revision") from exc
        return {**receipt, "_record": current}, True

    try:
        receipt, created = run_with_journal_writer(
            workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=publish,
        )
    except JournalConflictError as exc:
        raise ScoutInterviewError("interview_authority_invalid", "Scout interview journal is not writable") from exc
    if not created:
        stored_record_id = receipt.get("record_id")
        stored_revision_id = receipt.get("revision_id")
        if isinstance(stored_record_id, str) and isinstance(stored_revision_id, str):
            stored = run_with_journal_writer(
                workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
                operation=lambda writer: writer.snapshot((f"records/scout-interviews/{stored_record_id}/",)),
            )
            persisted = _snapshot_json(stored, _record_path(stored_record_id, stored_revision_id))
            if persisted is not None:
                return InterviewResult(stored_record_id, stored_revision_id, False, persisted)
        raise ScoutInterviewError("interview_authority_invalid", "interview replay receipt is incomplete")
    committed = receipt.get("_record")
    return InterviewResult(str(record["record_id"]), str(record["revision_id"]), True, committed if isinstance(committed, Mapping) else record)


def _latest(resolved: ResolvedWorkpad, record_id: str) -> tuple[dict[str, object], bytes]:
    snapshot = run_with_journal_writer(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        operation=lambda writer: writer.snapshot((f"records/scout-interviews/{record_id}/", f"docs/interviews/{record_id}/")),
    )
    rows: list[dict[str, object]] = []
    for path, raw in snapshot.artifacts.items():
        if path.startswith(f"records/scout-interviews/{record_id}/revisions/") and path.endswith(".json"):
            value = _snapshot_json(snapshot, path)
            if value is not None:
                rows.append(value)
    if not rows:
        raise ScoutInterviewError("interview_not_found", "interview preparation record was not found")
    rows.sort(key=lambda item: str(item.get("created_at", "")))
    record = rows[-1]
    revision_id = str(record["revision_id"])
    markdown = snapshot.artifacts.get(_markdown_path(record_id, revision_id))
    if markdown is None:
        raise ScoutInterviewError("interview_authority_invalid", "interview Markdown sidecar is missing")
    return record, markdown


def prepare_interview(
    *, home_root: Path, requested_target: Path | None, gig_id: str | None,
    selection: Mapping[str, object], content: Mapping[str, object],
    operation_key: str, actor: Mapping[str, str] | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> InterviewResult:
    """Create one role-only or exact-posting preparation revision."""
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    normalized_selection = _selection(selection)
    _verify_selected_refs(resolved, normalized_selection)
    normalized_content = _content(content)
    _verify_content_refs(resolved, normalized_content)
    record_id = _id(EntityPrefix.RECORD, uuid_factory)
    revision_id = _id(EntityPrefix.REVISION, uuid_factory)
    record: dict[str, object] = {
        "schema_version": "1.0", "kind": "scout_interview_preparation",
        "record_id": record_id, "revision_id": revision_id, "parent_revision": None,
        "project_id": resolved.project_id, "gig_id": resolved.gig_id, "state": "active",
        "selection": normalized_selection, "content": normalized_content,
        "feedback": [],
        "actor": dict(actor or {"kind": "operator", "id": "local-user"}), "created_at": _now(),
        "provenance": {"kind": "user_selected_inputs", "source_refs": [*normalized_selection["research_revisions"], *normalized_selection["candidate_evidence"]]},
    }
    def authenticate(writer: object, snapshot: JournalSnapshot, current: dict[str, object]) -> dict[str, object]:
        selection_value, content_value = _authenticate_inputs(
            resolved=resolved, writer=writer, snapshot=snapshot,
            selection=current["selection"], content=current["content"],
        )
        current["selection"], current["content"] = selection_value, content_value
        current["provenance"] = {
            "kind": "user_selected_inputs",
            "source_refs": [*selection_value["research_revisions"], *selection_value["candidate_evidence"]],
        }
        return current
    return _publish(resolved=resolved, record=record, markdown=_markdown(record), operation_key=operation_key, authenticate=authenticate)


def revise_interview(
    *, home_root: Path, requested_target: Path | None, gig_id: str | None,
    record_id: str, parent_revision: str, content: Mapping[str, object],
    operation_key: str, actor: Mapping[str, str] | None = None,
    feedback: Sequence[Mapping[str, object]] | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> InterviewResult:
    """Append a focused preparation/answer revision without rewriting history."""
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    previous, _markdown_bytes = _latest(resolved, record_id)
    if previous.get("revision_id") != parent_revision:
        raise ScoutInterviewError("interview_stale_parent", "revision does not name the current preparation")
    normalized = _content(content)
    _verify_content_refs(resolved, normalized)
    prior_feedback = previous.get("feedback", [])
    if not isinstance(prior_feedback, list):
        raise ScoutInterviewError("interview_authority_invalid", "previous feedback is malformed")
    added = [dict(item) for item in (feedback or [])]
    for item in added:
        if not isinstance(item, Mapping) or not isinstance(item.get("text"), str):
            raise ScoutInterviewError("interview_feedback_invalid", "feedback must contain bounded text")
        _text(item["text"], "feedback.text")
    record = dict(previous)
    record.update({
        "revision_id": _id(EntityPrefix.REVISION, uuid_factory), "parent_revision": parent_revision,
        "content": normalized, "feedback": [*prior_feedback, *added],
        "actor": dict(actor or {"kind": "operator", "id": "local-user"}), "created_at": _now(),
    })
    def authenticate(writer: object, snapshot: JournalSnapshot, current: dict[str, object]) -> dict[str, object]:
        selection_value, content_value = _authenticate_inputs(
            resolved=resolved, writer=writer, snapshot=snapshot,
            selection=current.get("selection", {}), content=current["content"],
        )
        current["selection"], current["content"] = selection_value, content_value
        return current
    return _publish(
        resolved=resolved, record=record, markdown=_markdown(record),
        operation_key=operation_key, authenticate=authenticate,
        parent_revision=parent_revision,
    )


def save_interview_feedback(
    *, home_root: Path, requested_target: Path | None, gig_id: str | None,
    record_id: str, parent_revision: str, feedback: Sequence[Mapping[str, object]],
    operation_key: str, actor: Mapping[str, str] | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> InterviewResult:
    """Save practice feedback as feedback, never as evidence of skill."""
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    previous, _ = _latest(resolved, record_id)
    if previous.get("revision_id") != parent_revision:
        raise ScoutInterviewError("interview_stale_parent", "feedback does not name the current preparation")
    content = previous.get("content")
    if not isinstance(content, Mapping):
        raise ScoutInterviewError("interview_authority_invalid", "preparation content is malformed")
    return revise_interview(
        home_root=home_root, requested_target=requested_target, gig_id=gig_id,
        record_id=record_id, parent_revision=parent_revision, content=content,
        feedback=feedback, operation_key=operation_key, actor=actor, uuid_factory=uuid_factory,
    )


def read_interview_preparation(
    *, home_root: Path, requested_target: Path | None, gig_id: str | None,
    record_id: str, revision_id: str | None = None,
) -> dict[str, object]:
    """Read a committed preparation revision and its exact Markdown bytes."""
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    record, markdown = _latest(resolved, record_id)
    if revision_id is not None and record.get("revision_id") != revision_id:
        # Read an historical revision through the authenticated snapshot.
        snapshot = run_with_journal_writer(
            workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
            operation=lambda writer: writer.snapshot((f"records/scout-interviews/{record_id}/", "docs/interviews/")),
        )
        path = _record_path(record_id, revision_id)
        historical = _snapshot_json(snapshot, path)
        if historical is None:
            raise ScoutInterviewError("interview_not_found", "interview revision was not found")
        record = historical
        markdown = snapshot.artifacts.get(_markdown_path(record_id, revision_id))
        if markdown is None:
            raise ScoutInterviewError("interview_authority_invalid", "interview Markdown sidecar is missing")
    return {**record, "markdown": markdown}


def list_interview_preparations(*, home_root: Path, requested_target: Path | None, gig_id: str | None) -> list[dict[str, object]]:
    resolved = _resolved(home_root=home_root, requested_target=requested_target, gig_id=gig_id)
    snapshot = run_with_journal_writer(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        operation=lambda writer: writer.snapshot(("records/scout-interviews/",)),
    )
    latest: dict[str, dict[str, object]] = {}
    for path in snapshot.artifacts:
        if not path.startswith("records/scout-interviews/") or not path.endswith(".json"):
            continue
        value = _snapshot_json(snapshot, path)
        if not value or not isinstance(value.get("record_id"), str):
            continue
        previous = latest.get(value["record_id"])
        if previous is None or str(value.get("created_at", "")) > str(previous.get("created_at", "")):
            latest[value["record_id"]] = value
    return [{key: value for key, value in row.items() if key not in {"content", "provenance"}} for row in sorted(latest.values(), key=lambda item: str(item.get("created_at", "")))]


__all__ = [
    "InterviewResult", "ScoutInterviewError", "list_interview_preparations",
    "prepare_interview", "read_interview_preparation", "revise_interview",
    "save_interview_feedback",
]
