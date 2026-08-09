"""Durable comparison and bounded handoff records for G18."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Mapping
import uuid

from .canonical import EntityPrefix, canonical_json_bytes, canonical_json_digest, digest_imported_bytes, validate_entity_id
from .journal import JournalArtifact, JournalEntry, record_transition
from .model_execution import ModelInvocationExecution
from .validators import validate_model_exchange
from .workpad import ResolvedWorkpad


class ModelExchangeError(RuntimeError):
    """A comparison or handoff cannot be represented safely."""

    code = "model_exchange_failed"


@dataclass(frozen=True)
class ExchangeExecution:
    """A persisted exchange record and its journal evidence."""

    record: dict[str, object]
    journal_entry: JournalEntry
    input_text: str | None = None


def compare_model_invocations(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    edge_id: str,
    source_goal_id: str,
    receiver_goal_id: str,
    executions: tuple[ModelInvocationExecution, ...],
    uuid_factory=uuid.uuid4,
) -> ExchangeExecution:
    """Persist an independent comparison without choosing a winner."""

    _validate_exchange_ids(run_id, edge_id, source_goal_id, receiver_goal_id)
    if len(executions) < 2:
        raise ModelExchangeError("comparison requires at least two invocations")
    invocation_ids = [str(item.record["invocation_id"]) for item in executions]
    if len(set(invocation_ids)) != len(invocation_ids):
        raise ModelExchangeError("comparison invocations must be independent")
    artifacts = tuple(_source_artifact(resolved.path, item) for item in executions)
    if any(item.result is None or item.record["outcome"] != "succeeded" for item in executions):
        status = "unavailable"
    else:
        outputs = [item.result.output_text for item in executions if item.result is not None]
        status = "agreement" if len(set(outputs)) == 1 else "disagreement"
    adjudication_ref: dict[str, object] | None = None
    journal_artifacts = [
        JournalArtifact(str(item["artifact"]["path"]), (resolved.path / str(item["artifact"]["path"])).read_bytes())
        for item in artifacts
    ]
    if status == "disagreement":
        adjudication_payload = {
            "schema_version": "1.0",
            "kind": "model-comparison-adjudication-input",
            "source_artifacts": list(artifacts),
            "decision": "operator_adjudication_required",
        }
        adjudication_bytes = canonical_json_bytes(adjudication_payload)
        adjudication_path = f"runs/{run_id}/model-exchanges/{edge_id}/adjudication-input.json"
        adjudication_ref = _artifact_ref(adjudication_path, adjudication_bytes)
        journal_artifacts.append(JournalArtifact(adjudication_path, adjudication_bytes))
    record = _exchange_record(
        run_id=run_id,
        edge_id=edge_id,
        source_goal_id=source_goal_id,
        receiver_goal_id=receiver_goal_id,
        kind="comparison",
        source_invocation_ids=invocation_ids,
        source_artifacts=artifacts,
        handoff=None,
        comparison={
            "independent_artifacts": list(artifacts),
            "requires_human_adjudication": status == "disagreement",
            "selected_winner": None,
            "adjudication_input": adjudication_ref,
        },
        status=status,
    )
    return _persist_exchange(
        resolved=resolved,
        run_id=run_id,
        source_goal_id=source_goal_id,
        record=record,
        journal_artifacts=tuple(journal_artifacts),
        uuid_factory=uuid_factory,
    )


def prepare_model_handoff(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    edge_id: str,
    source_goal_id: str,
    receiver_goal_id: str,
    source_execution: ModelInvocationExecution,
    handoff_index: int,
    handoff_cap: int,
    uuid_factory=uuid.uuid4,
) -> ExchangeExecution:
    """Persist one explicit handoff artifact, failing closed at the cap."""

    _validate_exchange_ids(run_id, edge_id, source_goal_id, receiver_goal_id)
    if handoff_index < 1 or handoff_cap < 1:
        raise ModelExchangeError("handoff index and cap must be positive")
    source_artifact = _source_artifact(resolved.path, source_execution)
    blocked = handoff_index > handoff_cap
    if blocked:
        input_payload = {
            "schema_version": "1.0",
            "kind": "model-handoff-blocked",
            "reason": "handoff_limit_exhausted",
            "requested_index": handoff_index,
            "cap": handoff_cap,
            "parent_artifact": source_artifact["artifact"],
        }
        input_text = None
    else:
        if source_execution.result is None or source_execution.record["outcome"] != "succeeded":
            raise ModelExchangeError("only a successful invocation can produce a handoff")
        input_payload = {
            "schema_version": "1.0",
            "kind": "model-handoff-input",
            "source_invocation_id": source_execution.record["invocation_id"],
            "source_goal_id": source_goal_id,
            "content": source_execution.result.output_text,
        }
        input_text = source_execution.result.output_text
    input_bytes = canonical_json_bytes(input_payload)
    input_path = f"runs/{run_id}/model-exchanges/{edge_id}/handoff-input.json"
    input_ref = _artifact_ref(input_path, input_bytes)
    record = _exchange_record(
        run_id=run_id,
        edge_id=edge_id,
        source_goal_id=source_goal_id,
        receiver_goal_id=receiver_goal_id,
        kind="handoff",
        source_invocation_ids=[str(source_execution.record["invocation_id"])],
        source_artifacts=[source_artifact],
        handoff={
            "index": min(handoff_index, handoff_cap),
            "cap": handoff_cap,
            "input_artifact": input_ref,
            "parent_artifact": source_artifact["artifact"],
            "hidden_context": False,
        },
        comparison=None,
        status="blocked" if blocked else "received",
    )
    return _persist_exchange(
        resolved=resolved,
        run_id=run_id,
        source_goal_id=source_goal_id,
        record=record,
        journal_artifacts=(JournalArtifact(input_path, input_bytes),),
        uuid_factory=uuid_factory,
        input_text=input_text,
    )


def _persist_exchange(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    source_goal_id: str,
    record: dict[str, object],
    journal_artifacts: tuple[JournalArtifact, ...],
    uuid_factory,
    input_text: str | None = None,
) -> ExchangeExecution:
    report = validate_model_exchange(record)
    if not report.valid:
        raise ModelExchangeError(
            "constructed model exchange failed validation: "
            + ", ".join(item.code for item in report.findings)
        )
    record_bytes = canonical_json_bytes(record)
    exchange_id = str(record["edge_id"])
    path = f"runs/{run_id}/model-exchanges/{exchange_id}/{record['kind']}.json"
    all_artifacts = list(journal_artifacts)
    all_artifacts.append(JournalArtifact(path, record_bytes))
    entry = record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="goal_completed" if record["status"] != "blocked" else "goal_blocked",
        body=f"G18 model {record['kind']} {exchange_id} terminalized as {record['status']}.",
        artifacts=tuple(all_artifacts),
        front_matter={
            "run_id": run_id,
            "goal_id": source_goal_id,
            "goal_version": 1,
            "outcome": "COMPLETE" if record["status"] != "blocked" else "BLOCKED",
            "actor": {"kind": "gigai", "id": "g18-model-exchange", "model_target": None},
            "evidence": [_artifact_ref(path, record_bytes)],
        },
    )
    return ExchangeExecution(record, entry, input_text)


def _source_artifact(root: Path, execution: ModelInvocationExecution) -> dict[str, object]:
    extensions = execution.record.get("extensions")
    if not isinstance(extensions, list):
        raise ModelExchangeError("invocation has no persisted response artifact")
    value = next(
        (item.get("value") for item in extensions if isinstance(item, Mapping) and item.get("name") == "response_artifact"),
        None,
    )
    if not isinstance(value, Mapping) or not isinstance(value.get("path"), str):
        raise ModelExchangeError("invocation response artifact reference is missing")
    path = root / str(value["path"])
    if path.is_symlink() or not path.is_file():
        raise ModelExchangeError("invocation response artifact is missing")
    content = path.read_bytes()
    if digest_imported_bytes(content) != value.get("content_sha256"):
        raise ModelExchangeError("invocation response artifact digest mismatch")
    return {
        "artifact": dict(value),
        "invocation_id": execution.record["invocation_id"],
        "goal_id": execution.record["goal_id"],
    }


def _exchange_record(**values: object) -> dict[str, object]:
    body = {
        "schema_version": "1.0",
        "record_version": 1,
        "run_id": values["run_id"],
        "edge_id": values["edge_id"],
        "source_goal_id": values["source_goal_id"],
        "receiver_goal_id": values["receiver_goal_id"],
        "kind": values["kind"],
        "source_invocation_ids": list(values["source_invocation_ids"]),
        "source_artifacts": list(values["source_artifacts"]),
        "handoff": values["handoff"],
        "comparison": values["comparison"],
        "status": values["status"],
        "automatic_fallback": False,
        "retry_count": 0,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    body["record_sha256"] = canonical_json_digest(body)
    return {"record_sha256": body.pop("record_sha256"), **body}


def _artifact_ref(path: str, content: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(content),
        "canonical_sha256": canonical_json_digest(json.loads(content)),
        "media_type": "application/json",
        "size_bytes": len(content),
    }


def _validate_exchange_ids(run_id: str, edge_id: str, source_goal_id: str, receiver_goal_id: str) -> None:
    validate_entity_id(run_id, expected_prefix=EntityPrefix.RUN)
    validate_entity_id(edge_id, expected_prefix=EntityPrefix.EDGE)
    validate_entity_id(source_goal_id, expected_prefix=EntityPrefix.GOAL)
    validate_entity_id(receiver_goal_id, expected_prefix=EntityPrefix.GOAL)


def _new_id(prefix: EntityPrefix, uuid_factory) -> str:
    return validate_entity_id(f"{prefix.value}_{uuid_factory()}", expected_prefix=prefix)


__all__ = ["ExchangeExecution", "ModelExchangeError", "compare_model_invocations", "prepare_model_handoff"]
