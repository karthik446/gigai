"""Committed-data readers used by the Scout report service.

The report is a view over one :class:`JournalSnapshot`.  These readers are
bound to a resolved Gig only so they can authenticate the bytes in that
snapshot; they never resolve a fresh ``latest`` record while rendering.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..canonical import digest_imported_bytes, parse_json_bytes
from ..external_recording import (
    _checkpoints,
    _recorded_dispatch,
    _read_run,
    _run_receipts,
)
from ..journal import JournalSnapshot
from .find_jobs.contracts import aggregate_status
from .posting_inputs import resolve_discovery_posting_input
from .documents import DocumentRevision, SourceLineage
from .proposal_records import read_proposal_revision
from .projection import ScoutProjectionError, ScoutReaderSet
from ..validators import validate_serialized_contract


def _path(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    candidate = value.get("path")
    if (
        not isinstance(candidate, str)
        or not candidate
        or candidate.startswith("/")
        or "\\" in candidate
        or ".." in Path(candidate).parts
    ):
        return None
    return candidate


def _ref(snapshot: JournalSnapshot, value: object) -> tuple[str, bytes] | None:
    path = _path(value)
    if path is None or path not in snapshot.artifacts:
        return None
    raw = snapshot.artifacts[path]
    if not isinstance(value, Mapping) or value.get("content_sha256") != digest_imported_bytes(raw) or value.get("size_bytes") != len(raw):
        return None
    return path, raw


def _scope(value: Mapping[str, object], project: str, gig: str) -> bool:
    return value.get("project_id") == project and value.get("gig_id") == gig


def _local_scope(value: Mapping[str, object], project: str, gig: str) -> bool:
    """Check local Run scope; committed run-details may omit project_id."""
    return value.get("gig_id") == gig and value.get("project_id") in {None, project}


def _receipt_rows(snapshot: JournalSnapshot, project: str, gig: str) -> Iterable[tuple[dict[str, object], str, dict[str, object]]]:
    for path, raw in sorted(snapshot.artifacts.items()):
        if not (path.startswith("runs/") and "/receipts/" in path and path.endswith(".json")):
            continue
        try:
            receipt = _recorded_dispatch(
                raw,
                v1_schema="external-recording-receipt.schema.json",
                v2_schema="external-recording-receipt-v2.schema.json",
                code="scout_report_run_invalid",
            )
            run_id = receipt.get("run_id")
            run, run_path, _ = _read_run(snapshot, str(run_id))
        except Exception as exc:
            raise ScoutProjectionError("scout_report_run_invalid", "committed Run evidence is invalid") from exc
        if not _scope(run, project, gig):
            continue
        yield receipt, run_path, run


def _opportunity_rows(resolved: Any, snapshot: JournalSnapshot, project: str, gig: str) -> list[dict[str, object]]:
    rows: dict[tuple[str, str], dict[str, object]] = {}
    for receipt, run_path, run in _receipt_rows(snapshot, project, gig):
        if receipt.get("outcome") != "succeeded":
            continue
        run_id = run.get("run_id")
        outputs = receipt.get("outputs")
        if not isinstance(run_id, str) or not isinstance(outputs, list):
            continue
        for output in outputs:
            if not isinstance(output, Mapping) or output.get("kind") != "discovery":
                continue
            domain = _ref(snapshot, output.get("domain_sidecar"))
            if domain is None:
                # v1 records do not carry an authenticated domain sidecar and
                # therefore cannot establish an opportunity identity.
                continue
            try:
                domain_value = parse_json_bytes(domain[1])
                discovery = domain_value.get("value", {}).get("discovery") if isinstance(domain_value, Mapping) else None
                postings = discovery.get("postings") if isinstance(discovery, Mapping) else None
                exclusions = discovery.get("exclusions") if isinstance(discovery, Mapping) else []
                if not isinstance(postings, list):
                    continue
            except (ValueError, AttributeError, TypeError) as exc:
                raise ScoutProjectionError("scout_report_discovery_invalid", "discovery domain evidence is invalid") from exc
            for posting in postings:
                if not isinstance(posting, Mapping):
                    continue
                opportunity_id, snapshot_id = posting.get("opportunity_id"), posting.get("snapshot_id")
                if not isinstance(opportunity_id, str) or not isinstance(snapshot_id, str):
                    continue
                selector = {
                    "family": "scout_discovery",
                    "run_id": run_id,
                    "receipt_id": receipt.get("receipt_id"),
                    "output_kind": "discovery",
                    "opportunity_id": opportunity_id,
                    "snapshot_id": snapshot_id,
                }
                try:
                    resolved_posting = resolve_discovery_posting_input(resolved, snapshot, selector)
                except Exception as exc:
                    raise ScoutProjectionError("scout_report_discovery_invalid", "discovery posting authority could not be resolved") from exc
                source = posting.get("source") if isinstance(posting.get("source"), Mapping) else {}
                key = (opportunity_id, snapshot_id)
                rows[key] = {
                    "opportunity_id": opportunity_id,
                    "snapshot_id": snapshot_id,
                    "title": posting.get("title"),
                    "employer": posting.get("employer"),
                    "source": dict(source),
                    "posting": dict(posting),
                    "acquisition_state": "considered",
                    "acquisition_exclusion": next((dict(item) for item in exclusions if isinstance(item, Mapping) and item.get("opportunity_id") == opportunity_id), None),
                    "run_id": run_id,
                    "run_path": run_path,
                    "receipt_id": receipt.get("receipt_id"),
                    "run_ref": resolved_posting.get("run_ref"),
                    "receipt_ref": resolved_posting.get("receipt_ref"),
                    "checkpoint_ref": resolved_posting.get("checkpoint_ref"),
                    "posting_ref": resolved_posting.get("posting_ref"),
                    "posting_path": (resolved_posting.get("posting_ref") or {}).get("ref", {}).get("path") if isinstance(resolved_posting.get("posting_ref"), Mapping) else None,
                }
    return list(rows.values())


def _proposal_rows(resolved: Any, snapshot: JournalSnapshot, project: str, gig: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path, raw in sorted(snapshot.artifacts.items()):
        if not (path.startswith("records/scout-proposals/") and "/revisions/" in path and path.endswith(".json")):
            continue
        try:
            value = read_proposal_revision(
                resolved=resolved,
                record_id=path.split("/")[2],
                revision_id=path.split("/")[4].removesuffix(".json"),
                snapshot=snapshot,
            )
        except Exception as exc:
            raise ScoutProjectionError("scout_report_proposal_invalid", "committed proposal revision is invalid") from exc
        if not _scope(value, project, gig):
            continue
        opportunity = value["opportunity"]
        posting_ref = opportunity.get("posting_ref")
        posting_path = posting_ref.get("ref", {}).get("path") if isinstance(posting_ref, Mapping) and isinstance(posting_ref.get("ref"), Mapping) else None
        rows.append({
            "opportunity_id": opportunity["opportunity_id"],
            "snapshot_id": opportunity["snapshot_id"],
            "status": value["assessment"]["status"],
            "record_id": value["record_id"],
            "revision_id": value["revision_id"],
            "path": path,
            "assessment": dict(value["assessment"]["proposal"]),
            "proposal": dict(value["assessment"]["proposal"]),
            "content_sha256": value["assessment"]["content_sha256"],
            "input_revisions": list(value["input_revisions"]),
            "answer_associations": list(value["answer_associations"]),
            "invocation": dict(value["invocation"]),
            "method": dict(value["method"]),
            "source": {
                "run_ref": opportunity["run_ref"],
                "receipt_ref": opportunity["receipt_ref"],
                "checkpoint_ref": opportunity["checkpoint_ref"],
                "posting_ref": opportunity["posting_ref"],
            },
            "source_path": posting_path,
        })
    return rows


def _document_rows(snapshot: JournalSnapshot, project: str, gig: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    revisions: dict[tuple[object, object, object, object], dict[str, object]] = {}
    for path, raw in sorted(snapshot.artifacts.items()):
        if not (path.startswith("records/scout-documents/") and "/revisions/" in path and path.endswith("/record.json")):
            continue
        try:
            value = parse_json_bytes(raw)
            descriptor = dict(value) if isinstance(value, Mapping) else {}
            content_ref = descriptor.pop("content_ref", None)
            opportunity = descriptor.get("opportunity")
            if not isinstance(opportunity, Mapping) or not isinstance(descriptor.get("source_lineage"), list):
                raise ValueError("document descriptor is malformed")
            content = _ref(snapshot, content_ref)
            if content is None or digest_imported_bytes(content[1]) != descriptor.get("content_sha256"):
                raise ValueError("document content digest differs")
            lineage = descriptor["source_lineage"]
            if any(not isinstance(item, Mapping) for item in lineage):
                raise ValueError("document source lineage is malformed")
            DocumentRevision(
                opportunity_id=str(opportunity["opportunity_id"]), snapshot_id=str(opportunity["snapshot_id"]),
                document_kind=str(descriptor["document_kind"]), record_id=str(descriptor["record_id"]),
                revision_id=str(descriptor["revision_id"]), content=content[1],
                content_sha256=str(descriptor["content_sha256"]),
                source_lineage=tuple(SourceLineage(str(item["source_id"]), str(item["content_sha256"]), dict(item.get("identity", {}))) for item in lineage),
                checks=dict(descriptor.get("checks", {})), parent_revision_id=descriptor.get("parent_revision_id"),
            )
            row = {**descriptor, "path": path, "content_path": content[0], "opportunity_id": opportunity.get("opportunity_id"), "snapshot_id": opportunity.get("snapshot_id"), "selected": False}
            revisions[(row["document_kind"], row["record_id"], row["revision_id"], row["content_sha256"])] = row
        except Exception as exc:
            raise ScoutProjectionError("scout_report_document_invalid", "committed document revision failed host validation") from exc
    for path, raw in sorted(snapshot.artifacts.items()):
        if not (path.startswith("records/scout-documents/selections/") and path.endswith(".json")):
            continue
        try:
            value = parse_json_bytes(raw)
            version = value.get("selection_version") if isinstance(value, Mapping) else None
            schema = "scout-document-selection-v2.schema.json" if version == "scout-document-selection:2" else "scout-document-selection-v1.schema.json"
            if not validate_serialized_contract(schema, raw).valid:
                raise ValueError("final selection schema is invalid")
            opportunity = value.get("opportunity")
            selected_docs = value.get("documents")
            if not isinstance(opportunity, Mapping) or not isinstance(selected_docs, list):
                raise ValueError("final selection is malformed")
            authoritative = version == "scout-document-selection:2"
            if authoritative:
                source_run = value.get("source_run")
                if not _tailor_result_redeemed(snapshot, source_run, selected_docs, project, gig):
                    raise ValueError("final selection Tailor provenance is unavailable")
            for selected in selected_docs:
                if not isinstance(selected, Mapping):
                    raise ValueError("final selection document is malformed")
                key = tuple(selected.get(name) for name in ("document_kind", "record_id", "revision_id", "content_sha256"))
                document = revisions.get(key)
                if document is None or document.get("opportunity_id") != opportunity.get("opportunity_id") or document.get("snapshot_id") != opportunity.get("snapshot_id"):
                    raise ValueError("final selection document is not a committed matching revision")
                rows.append({**document, "selected": authoritative, "legacy_selection": not authoritative, "selection_path": path})
        except Exception as exc:
            raise ScoutProjectionError("scout_report_document_invalid", "committed final selection could not be redeemed") from exc
    rows.extend(item for key, item in revisions.items() if not any(row.get("record_id") == key[1] and row.get("revision_id") == key[2] for row in rows))
    return rows


def _tailor_result_redeemed(snapshot: JournalSnapshot, source_run: object, selected_docs: list[object], project: str, gig: str) -> bool:
    if not isinstance(source_run, Mapping) or source_run.get("authority") != "tailor_run":
        return False
    run_id, goal_id, invocation_id, output_sha = (source_run.get(key) for key in ("run_id", "goal_id", "invocation_id", "output_sha256"))
    result_path = f"runs/{run_id}/scout-tailor/result.json"
    raw = snapshot.artifacts.get(result_path)
    details_raw = snapshot.artifacts.get(f"runs/{run_id}/run-details.json")
    if raw is None or details_raw is None or not isinstance(output_sha, str):
        return False
    try:
        result, details = parse_json_bytes(raw), parse_json_bytes(details_raw)
    except ValueError:
        return False
    if not isinstance(result, Mapping) or not isinstance(details, Mapping) or result.get("schema_version") != "scout-tailor-run-result:1" or result.get("status") != "complete" or result.get("run_id") != run_id or result.get("goal_id") != goal_id or result.get("invocation_id") != invocation_id or result.get("output_sha256") != output_sha or not _local_scope(details, project, gig):
        return False
    goals = details.get("goals")
    goal = next(
        (item for item in goals if isinstance(item, Mapping) and item.get("goal_id") == goal_id),
        None,
    ) if isinstance(goals, list) else None
    result_ref = {"path": result_path, "content_sha256": digest_imported_bytes(raw), "size_bytes": len(raw)}
    evidence = goal.get("evidence") if isinstance(goal, Mapping) else None
    if (
        not isinstance(goal, Mapping)
        or goal.get("status") != "complete"
        or not isinstance(evidence, list)
        or not any(
            isinstance(item, Mapping)
            and item.get("path") == result_path
            and item.get("content_sha256") == result_ref["content_sha256"]
            and item.get("size_bytes") == result_ref["size_bytes"]
            for item in evidence
        )
    ):
        return False
    result_docs = {(item.get("document_kind"), item.get("record_id"), item.get("revision_id"), item.get("content_sha256")) for item in result.get("documents", []) if isinstance(item, Mapping)}
    return all(tuple(item.get(name) for name in ("document_kind", "record_id", "revision_id", "content_sha256")) in result_docs for item in selected_docs if isinstance(item, Mapping))


def _run_rows(snapshot: JournalSnapshot, project: str, gig: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    run_paths = sorted(path for path in snapshot.artifacts if path.startswith("runs/") and path.endswith("/external-run.json"))
    for path in run_paths:
        try:
            run, _, _ = _read_run(snapshot, path.split("/")[1])
        except Exception as exc:
            raise ScoutProjectionError("scout_report_run_invalid", "committed Run is invalid") from exc
        if not _scope(run, project, gig):
            continue
        receipts = sorted(_run_receipts(snapshot, str(run["run_id"])), key=lambda item: str(item.get("created_at", "")))
        checkpoints = _checkpoints(snapshot, str(run["run_id"]))
        status = "active"
        if receipts:
            status = str(receipts[-1].get("outcome", "unknown"))
        elif checkpoints and any(q.get("state") == "missing" for q in checkpoints[-1].get("questions", []) if isinstance(q, Mapping)):
            status = "waiting_input"
        rows.append({"run_id": run["run_id"], "status": status, "path": path, "operation": (run.get("invocation") or {}).get("operation") if isinstance(run.get("invocation"), Mapping) else None, "run_plan": run.get("run_plan"), "created_at": run.get("created_at")})
    known = {str(item["run_id"]) for item in rows}
    for path in sorted(path for path in snapshot.artifacts if path.startswith("runs/") and path.endswith("/run-details.json")):
        run_id = path.split("/")[1]
        if run_id in known:
            continue
        raw = snapshot.artifacts[path]
        try:
            if not validate_serialized_contract("run-details.schema.json", raw).valid:
                raise ValueError("run details schema is invalid")
            details = parse_json_bytes(raw)
        except Exception as exc:
            raise ScoutProjectionError("scout_report_run_invalid", "local Scout Run details are invalid") from exc
        if not isinstance(details, Mapping) or details.get("run_id") != run_id or not _local_scope(details, project, gig):
            continue
        goals = details.get("goals")
        status = str(details.get("status", "unknown"))
        if isinstance(goals, list):
            states = [str(item.get("status")) for item in goals if isinstance(item, Mapping)]
            if states:
                # Aggregate under the contracts' frozen precedence (interrupted
                # > failed > blocked > cancelled > running > pending >
                # succeeded), not "any complete goal ⇒ succeeded".
                status = aggregate_status(states)
                if status == "running":
                    status = "active"
        result_paths = sorted(
            candidate for candidate in snapshot.artifacts
            if candidate.startswith(f"runs/{run_id}/") and candidate.endswith("/result.json")
        )
        rows.append({"run_id": run_id, "status": status, "path": path, "kind": "scout-local", "operation": "scout-local", "created_at": details.get("created_at"), "result_paths": result_paths})
    return rows


def _evidence_rows(snapshot: JournalSnapshot, project: str, gig: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for run in _run_rows(snapshot, project, gig):
        run_id = run["run_id"]
        for checkpoint in _checkpoints(snapshot, str(run_id)):
            for artifact in checkpoint.get("artifacts", []):
                if not isinstance(artifact, Mapping):
                    continue
                ref = artifact.get("markdown")
                if _ref(snapshot, ref) is None:
                    continue
                rows.append({"evidence_id": f"{run_id}:{checkpoint.get('checkpoint_id')}:{artifact.get('kind')}", "label": artifact.get("kind"), "status": "reported", "path": _path(ref), "run_id": run_id, "checkpoint_id": checkpoint.get("checkpoint_id")})
        for result_path in run.get("result_paths", []) if isinstance(run.get("result_paths"), list) else []:
            raw = snapshot.artifacts.get(result_path)
            if raw is None:
                continue
            try:
                result = parse_json_bytes(raw)
            except ValueError as exc:
                raise ScoutProjectionError("scout_report_run_invalid", "local Scout result is invalid") from exc
            if not isinstance(result, Mapping):
                raise ScoutProjectionError("scout_report_run_invalid", "local Scout result is malformed")
            status = result.get("status")
            if status not in {"complete", "failed", "blocked", "cancelled"}:
                raise ScoutProjectionError("scout_report_run_invalid", "local Scout result status is invalid")
            result_run_id = result.get("run_id")
            goal_id = result.get("goal_id")
            if result_run_id is not None and result_run_id != run_id:
                raise ScoutProjectionError("scout_report_run_invalid", "local Scout result identity is invalid")
            details_raw = snapshot.artifacts.get(str(run.get("path")))
            details = None
            if isinstance(details_raw, bytes):
                try:
                    details = parse_json_bytes(details_raw)
                except ValueError as exc:
                    raise ScoutProjectionError("scout_report_run_invalid", "local Run details are invalid") from exc
            goals = details.get("goals") if isinstance(details, Mapping) else None
            result_ref = {"path": result_path, "content_sha256": digest_imported_bytes(raw), "size_bytes": len(raw)}
            if not isinstance(goal_id, str) and isinstance(goals, list):
                matching_goals = [
                    item for item in goals
                    if isinstance(item, Mapping)
                    and isinstance(item.get("evidence"), list)
                    and any(
                        isinstance(evidence_item, Mapping)
                        and evidence_item.get("path") == result_path
                        and evidence_item.get("content_sha256") == result_ref["content_sha256"]
                        and evidence_item.get("size_bytes") == result_ref["size_bytes"]
                        for evidence_item in item["evidence"]
                    )
                ]
                if len(matching_goals) == 1:
                    goal_id = matching_goals[0].get("goal_id")
            if not isinstance(goal_id, str):
                raise ScoutProjectionError("scout_report_run_invalid", "local Scout result goal is unavailable")
            goal = next((item for item in goals if isinstance(item, Mapping) and item.get("goal_id") == goal_id), None) if isinstance(goals, list) else None
            evidence = goal.get("evidence") if isinstance(goal, Mapping) else None
            if not isinstance(evidence, list) or not any(
                isinstance(item, Mapping)
                and item.get("path") == result_path
                and item.get("content_sha256") == result_ref["content_sha256"]
                and item.get("size_bytes") == result_ref["size_bytes"]
                for item in evidence
            ):
                raise ScoutProjectionError("scout_report_run_invalid", "local Scout result is not authenticated by its Goal")
            rows.append({"evidence_id": f"{run_id}:{result_path}", "label": Path(result_path).parent.name + " result", "status": "reported", "path": result_path, "run_id": run_id, "result_status": status})
    return rows


class _Readers:
    def __init__(self, resolved: Any) -> None:
        self.resolved = resolved

    def opportunities(self, snapshot: JournalSnapshot, project: str, gig: str) -> Iterable[Mapping[str, object]]:
        return _opportunity_rows(self.resolved, snapshot, project, gig)

    def proposals(self, snapshot: JournalSnapshot, project: str, gig: str) -> Iterable[Mapping[str, object]]:
        return _proposal_rows(self.resolved, snapshot, project, gig)

    def documents(self, snapshot: JournalSnapshot, project: str, gig: str) -> Iterable[Mapping[str, object]]:
        return _document_rows(snapshot, project, gig)

    def runs(self, snapshot: JournalSnapshot, project: str, gig: str) -> Iterable[Mapping[str, object]]:
        return _run_rows(snapshot, project, gig)

    def evidence(self, snapshot: JournalSnapshot, project: str, gig: str) -> Iterable[Mapping[str, object]]:
        return _evidence_rows(snapshot, project, gig)


def default_reader_set(resolved: Any) -> ScoutReaderSet:
    """Return production readers bound to one resolved Gig."""
    readers = _Readers(resolved)
    return ScoutReaderSet(
        opportunities=readers.opportunities,
        proposals=readers.proposals,
        documents=readers.documents,
        evidence=readers.evidence,
        runs=readers.runs,
    )


def opportunity_reader(resolved: Any):
    """Return the mutation-time opportunity resolver used by applications."""
    readers = _Readers(resolved)

    def resolve(snapshot: JournalSnapshot, opportunity_id: str, project: str, gig: str) -> Mapping[str, object] | None:
        return next((row for row in readers.opportunities(snapshot, project, gig) if row.get("opportunity_id") == opportunity_id), None)

    return resolve


__all__ = ["default_reader_set", "opportunity_reader"]
