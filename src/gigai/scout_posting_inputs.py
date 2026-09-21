"""Read-only resolution of a completed Scout discovery posting.

The caller supplies a pinned journal snapshot (or an already-held journal
writer through the hydration helper).  This module never searches for a
latest Run, reads editable Gig files, trusts sidecar content bytes, or writes
selection/application state.  Only the fixed packaged discovery bridge and
the inventoried ``.074`` source/schema bytes are accepted as authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from pathlib import Path
import re

from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    digest_imported_bytes,
    parse_json_bytes,
    validate_entity_id,
)
from .journal import JournalSnapshot, JournalWriter, read_committed_artifact, run_with_journal_writer
from .scout_template import SCOUT_DISCOVERY_SCHEMA_PATH, SCOUT_DISCOVERY_SOURCE_PATH
from .workpad import ResolvedWorkpad


_RAW_KEYS = frozenset(
    {"family", "run_id", "receipt_id", "output_kind", "opportunity_id", "snapshot_id"}
)
_OUTPUT_KEYS = frozenset(
    {"kind", "markdown", "sidecar", "domain_sidecar", "supporting_artifacts"}
)
_REF_KEYS = frozenset({"path", "content_sha256", "media_type", "size_bytes"})
_DOMAIN_KEYS = frozenset({"schema_id", "schema_ref", "validator_id", "validator_source_ref"})
_DOMAIN_SCHEMA_ID = "urn:gigai:scout:discovery-packet:2"
_VALIDATOR_ID = "scout-job-discovery:2"
_VALIDATOR_SOURCE = "gigai.scout_discovery:validate_discovery_domain"
_SCHEMA_RESOURCE = "data/scout/tools/cap_00000000-0000-4000-8000-000000000074/discovery.schema.json"
_SOURCE_RESOURCE = "data/scout/tools/cap_00000000-0000-4000-8000-000000000074/discovery.py"
_OPPORTUNITY = re.compile(r"^opportunity_[0-9a-f]{32}$")
_SNAPSHOT = re.compile(r"^snapshot_[0-9a-f]{32}$")
_TERMINAL = frozenset({"succeeded", "cancelled", "interrupted"})


class ScoutPostingInputError(ValueError):
    """Typed, content-free refusal for an unavailable discovery posting."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise ScoutPostingInputError(code, message)


def _identity(value: object, prefix: EntityPrefix, *, name: str) -> str:
    if not isinstance(value, str):
        _fail("posting_input_invalid", f"{name} identity is invalid")
    try:
        validate_entity_id(value, expected_prefix=prefix)
    except Exception as exc:
        raise ScoutPostingInputError("posting_input_invalid", f"{name} identity is invalid") from exc
    return value


def _custom_identity(value: object, pattern: re.Pattern[str], *, name: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail("posting_input_invalid", f"{name} identity is invalid")
    return value


def _ref(snapshot: JournalSnapshot, value: object, *, code: str) -> tuple[dict[str, object], bytes]:
    if not isinstance(value, Mapping) or set(value) != _REF_KEYS:
        _fail(code, "posting artifact reference is malformed")
    path, digest, size = value.get("path"), value.get("content_sha256"), value.get("size_bytes")
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or ".." in Path(path).parts
        or not isinstance(digest, str)
        or type(size) is not int
        or size < 0
    ):
        _fail(code, "posting artifact reference is malformed")
    data = snapshot.artifacts.get(path)
    if data is None or digest_imported_bytes(data) != digest or len(data) != size:
        _fail(code, "posting artifact reference is unavailable or changed")
    return dict(value), data


def _record(snapshot: JournalSnapshot, path: str, *, kind: str) -> dict[str, object]:
    data = snapshot.artifacts.get(path)
    if data is None:
        _fail("posting_input_not_found", f"completed discovery {kind} is unavailable")
    try:
        payload = parse_json_bytes(data)
        if not isinstance(payload, Mapping) or canonical_json_bytes(payload) != data:
            raise ValueError("non-canonical record")
        from . import external_recording

        schemas = {
            "plan": ("external-recording-plan.schema.json", "external-recording-plan-v2.schema.json"),
            "run": ("external-recording-run.schema.json", "external-recording-run-v2.schema.json"),
            "checkpoint": ("external-recording-checkpoint.schema.json", "external-recording-checkpoint-v2.schema.json"),
            "receipt": ("external-recording-receipt.schema.json", "external-recording-receipt-v2.schema.json"),
        }[kind]
        return external_recording._recorded_dispatch(
            data, v1_schema=schemas[0], v2_schema=schemas[1], code="posting_input_refused"
        )
    except ScoutPostingInputError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", "posting_input_refused")
        if code == "external_protocol_unsupported":
            code = "posting_input_unsupported"
        raise ScoutPostingInputError(code, f"discovery {kind} is not a valid v2 record") from exc


def _v2(record: Mapping[str, object], *, kind: str) -> None:
    if record.get("schema_version") != "2.0":
        _fail("posting_input_unsupported", f"discovery {kind} is not a v2 record")


def _invocation(
    record: Mapping[str, object], *, operation: str, project_id: str, gig_id: str,
    run_id: str | None = None, run_plan_id: str | None = None,
) -> Mapping[str, object]:
    invocation = record.get("invocation")
    if (
        not isinstance(invocation, Mapping)
        or invocation.get("operation") != operation
        or invocation.get("project_id") != project_id
        or invocation.get("gig_id") != gig_id
        or not isinstance(invocation.get("input"), Mapping)
    ):
        _fail("posting_input_refused", f"historical {operation} invocation identity is invalid")
    inputs = invocation["input"]
    if run_id is not None and inputs.get("run_id") != run_id:
        _fail("posting_input_refused", "historical invocation Run identity differs")
    if run_plan_id is not None and inputs.get("run_plan_id") != run_plan_id:
        _fail("posting_input_refused", "historical invocation Plan identity differs")
    return invocation


def _graph(snapshot: JournalSnapshot, plan: Mapping[str, object], *, resolved: ResolvedWorkpad) -> dict[str, object]:
    graph_ref, graph_data = _ref(snapshot, plan.get("selected_graph"), code="posting_input_refused")
    try:
        graph = parse_json_bytes(graph_data)
    except ValueError as exc:
        raise ScoutPostingInputError("posting_input_refused", "historical selected graph is invalid") from exc
    if not isinstance(graph, Mapping) or canonical_json_bytes(graph) != graph_data:
        _fail("posting_input_refused", "historical selected graph is invalid")
    invocation = plan.get("invocation")
    selector = invocation.get("input", {}).get("graph_selector") if isinstance(invocation, Mapping) and isinstance(invocation.get("input"), Mapping) else None
    graph_id = graph.get("graph_id")
    if (
        not isinstance(graph_id, str)
        or graph_id != plan.get("goal_graph_id")
        or plan.get("selected_graph_id") != selector
        or selector != "find-jobs"
        or type(graph.get("graph_version")) is not int
        or graph.get("graph_version") < 1
    ):
        _fail("posting_input_refused", "historical selected graph identity is invalid")
    return {
        "selected_graph_id": plan["selected_graph_id"],
        "goal_graph_id": graph_id,
        "graph_version": graph["graph_version"],
        "graph_selector": selector,
        "graph_ref": graph_ref,
    }


def _domain_binding(snapshot: JournalSnapshot, plan: Mapping[str, object]) -> dict[str, object]:
    contract_ref, contract_data = _ref(snapshot, plan.get("output_contract"), code="posting_input_refused")
    try:
        contract = parse_json_bytes(contract_data)
    except ValueError as exc:
        raise ScoutPostingInputError("posting_input_refused", "historical output contract is invalid") from exc
    if (
        not isinstance(contract, Mapping)
        or canonical_json_bytes(contract) != contract_data
        or set(contract) != {"schema_version", "kind", "gig_id", "fields", "domains"}
        or contract.get("schema_version") != "2.0"
        or contract.get("kind") != "run_output_contract"
        or contract.get("gig_id") != plan.get("gig_id")
        or contract.get("fields") != ["discovery"]
        or not isinstance(contract.get("domains"), Mapping)
        or set(contract["domains"]) != {"discovery"}
    ):
        _fail("posting_input_refused", "historical discovery output contract is not exact")
    binding = contract["domains"].get("discovery")
    if not isinstance(binding, Mapping) or set(binding) != _DOMAIN_KEYS:
        _fail("posting_input_refused", "historical discovery domain binding is incomplete")
    if binding.get("schema_id") != _DOMAIN_SCHEMA_ID or binding.get("validator_id") != _VALIDATOR_ID:
        _fail("posting_input_unsupported", "historical discovery validator is unsupported")
    schema_ref, schema_bytes = _ref(snapshot, binding.get("schema_ref"), code="posting_input_refused")
    source_ref, source_bytes = _ref(snapshot, binding.get("validator_source_ref"), code="posting_input_refused")
    if not str(schema_ref["path"]).endswith(SCOUT_DISCOVERY_SCHEMA_PATH) or not str(source_ref["path"]).endswith(SCOUT_DISCOVERY_SOURCE_PATH):
        _fail("posting_input_refused", "historical discovery validator paths are invalid")
    try:
        trusted_schema = resources.files("gigai").joinpath(*_SCHEMA_RESOURCE.split("/")).read_bytes()
        trusted_source = resources.files("gigai").joinpath(*_SOURCE_RESOURCE.split("/")).read_bytes()
        from .scout_discovery import validate_discovery_domain
    except (AttributeError, FileNotFoundError, OSError, ImportError) as exc:
        raise ScoutPostingInputError("posting_input_validator_unavailable", "fixed discovery validator is unavailable") from exc
    if schema_bytes != trusted_schema or source_bytes != trusted_source:
        _fail("posting_input_refused", "historical discovery validator bytes differ from the fixed package")
    return {
        "schema_id": binding["schema_id"], "schema_ref": schema_ref,
        "validator_id": binding["validator_id"], "validator_source_ref": source_ref,
        "validator": validate_discovery_domain, "contract_ref": contract_ref,
    }


def _check_contract(snapshot: JournalSnapshot, plan: Mapping[str, object]) -> set[str]:
    _contract_ref, contract_data = _ref(snapshot, plan.get("check_contract"), code="posting_input_refused")
    try:
        contract = parse_json_bytes(contract_data)
    except ValueError as exc:
        raise ScoutPostingInputError("posting_input_refused", "historical completion contract is invalid") from exc
    if (
        not isinstance(contract, Mapping)
        or canonical_json_bytes(contract) != contract_data
        or set(contract) != {"schema_version", "kind", "gig_id", "fields"}
        or contract.get("schema_version") != "1.0"
        or contract.get("kind") != "completion_evidence_contract"
        or contract.get("gig_id") != plan.get("gig_id")
        or not isinstance(contract.get("fields"), list)
        or not contract["fields"]
        or not all(isinstance(item, str) and item for item in contract["fields"])
        or len(set(contract["fields"])) != len(contract["fields"])
    ):
        _fail("posting_input_refused", "historical completion contract is invalid")
    return set(contract["fields"])


def _checkpoint_history(
    snapshot: JournalSnapshot, *, run_id: str, plan_ref: Mapping[str, object],
    project_id: str, gig_id: str,
) -> list[tuple[dict[str, object], str]]:
    history: list[tuple[dict[str, object], str]] = []
    prefix = f"runs/{run_id}/checkpoints/"
    for path in snapshot.artifacts:
        if path.startswith(prefix) and path.endswith(".json"):
            checkpoint = _record(snapshot, path, kind="checkpoint")
            _v2(checkpoint, kind="checkpoint")
            checkpoint_id = checkpoint.get("checkpoint_id")
            if not isinstance(checkpoint_id, str) or path != f"{prefix}{checkpoint_id}.json":
                _fail("posting_input_refused", "historical checkpoint identity does not match its path")
            if checkpoint.get("run_id") != run_id or checkpoint.get("run_plan") != plan_ref:
                _fail("posting_input_refused", "historical checkpoint Plan or Run identity differs")
            _invocation(checkpoint, operation="checkpoint", project_id=project_id, gig_id=gig_id, run_id=run_id)
            history.append((checkpoint, path))
    history.sort(key=lambda item: item[0].get("sequence", 0))
    parent = None
    for sequence, (checkpoint, _path) in enumerate(history, start=1):
        if checkpoint.get("sequence") != sequence or checkpoint.get("parent_checkpoint") != parent:
            _fail("posting_input_refused", "historical checkpoint sequence or parent is invalid")
        parent = checkpoint.get("checkpoint_id")
    return history


def _output(
    snapshot: JournalSnapshot, output: Mapping[str, object], *, run_id: str,
    checkpoint_id: str,
) -> tuple[dict[str, object], dict[str, bytes], dict[str, object]]:
    if set(output) != _OUTPUT_KEYS or output.get("kind") != "discovery":
        _fail("posting_input_refused", "historical discovery output tuple is malformed")
    markdown_ref, markdown = _ref(snapshot, output.get("markdown"), code="posting_input_refused")
    sidecar_ref, sidecar_bytes = _ref(snapshot, output.get("sidecar"), code="posting_input_refused")
    domain_ref, domain_bytes = _ref(snapshot, output.get("domain_sidecar"), code="posting_input_refused")
    expected = f"runs/{run_id}/artifacts/{checkpoint_id}/"
    if not all(str(ref["path"]).startswith(expected) for ref in (markdown_ref, sidecar_ref, domain_ref)):
        _fail("posting_input_refused", "historical discovery output escaped its Run")
    try:
        sidecar = parse_json_bytes(sidecar_bytes)
        domain = parse_json_bytes(domain_bytes)
    except ValueError as exc:
        raise ScoutPostingInputError("posting_input_refused", "historical discovery sidecar is invalid") from exc
    if (
        not isinstance(sidecar, Mapping)
        or canonical_json_bytes(sidecar) != sidecar_bytes
        or set(sidecar) != {"document_sha256", "output_kind", "run_id", "selected_inputs"}
        or sidecar.get("document_sha256") != digest_imported_bytes(markdown)
        or sidecar.get("output_kind") != "discovery"
        or sidecar.get("run_id") != run_id
        or not isinstance(domain, Mapping)
        or canonical_json_bytes(domain) != domain_bytes
        or set(domain) != {"schema_id", "value"}
        or domain.get("schema_id") != _DOMAIN_SCHEMA_ID
        or not isinstance(domain.get("value"), Mapping)
    ):
        _fail("posting_input_refused", "historical discovery sidecars do not bind their tuple")
    supporting_refs = output.get("supporting_artifacts")
    if not isinstance(supporting_refs, list) or len(supporting_refs) > 32:
        _fail("posting_input_refused", "historical discovery supporting refs are malformed")
    supporting: dict[str, bytes] = {}
    normalized_supporting: list[dict[str, object]] = []
    for item in supporting_refs:
        if not isinstance(item, Mapping) or set(item) != {"artifact_id", "ref"}:
            _fail("posting_input_refused", "historical discovery supporting ref is malformed")
        artifact_id = item.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id or artifact_id in supporting:
            _fail("posting_input_refused", "historical discovery supporting ref identity is invalid")
        ref, data = _ref(snapshot, item.get("ref"), code="posting_input_refused")
        if not str(ref["path"]).startswith(expected) or not str(ref["path"]).endswith(f".supporting/{artifact_id}.bin"):
            _fail("posting_input_refused", "historical discovery supporting ref escaped its Run")
        supporting[artifact_id] = data
        normalized_supporting.append({"artifact_id": artifact_id, "ref": ref})
    return (
        {"kind": "discovery", "markdown": markdown_ref, "sidecar": sidecar_ref, "domain_sidecar": domain_ref, "supporting_artifacts": normalized_supporting},
        supporting,
        dict(domain["value"]),
    )


def _preference_bytes(snapshot: JournalSnapshot, selected_inputs: object) -> dict[str, bytes]:
    if not isinstance(selected_inputs, list):
        _fail("posting_input_refused", "historical discovery selected inputs are malformed")
    result: dict[str, bytes] = {}
    for item in selected_inputs:
        if not isinstance(item, Mapping) or item.get("native_kind") != "profile_preferences":
            continue
        content = item.get("content")
        ref = content.get("blob_ref") if isinstance(content, Mapping) else None
        if not isinstance(ref, Mapping):
            _fail("posting_input_refused", "historical discovery profile input is malformed")
        checked, data = _ref(snapshot, ref, code="posting_input_refused")
        result[str(checked["path"])] = data
    if len(result) != 1:
        _fail("posting_input_refused", "historical discovery profile input is ambiguous")
    return result


def _resolve(resolved: ResolvedWorkpad, snapshot: JournalSnapshot, raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping) or set(raw) != _RAW_KEYS:
        _fail("posting_input_invalid", "discovery posting selector must contain exactly its closed fields")
    if raw.get("family") != "scout_discovery" or raw.get("output_kind") != "discovery":
        _fail("posting_input_invalid", "discovery posting selector is unsupported")
    run_id = _identity(raw.get("run_id"), EntityPrefix.RUN, name="Run")
    receipt_id = _identity(raw.get("receipt_id"), EntityPrefix.RECEIPT, name="receipt")
    opportunity_id = _custom_identity(raw.get("opportunity_id"), _OPPORTUNITY, name="opportunity")
    snapshot_id = _custom_identity(raw.get("snapshot_id"), _SNAPSHOT, name="snapshot")
    receipt_path = f"runs/{run_id}/receipts/{receipt_id}.json"
    receipt = _record(snapshot, receipt_path, kind="receipt")
    _v2(receipt, kind="receipt")
    if receipt.get("receipt_id") != receipt_id or receipt.get("run_id") != run_id:
        _fail("posting_input_refused", "receipt identity does not match its path")
    if receipt.get("outcome") != "succeeded":
        _fail("posting_input_not_terminal", "discovery Run does not have a succeeded terminal receipt")
    terminal_receipts = []
    receipt_prefix = f"runs/{run_id}/receipts/"
    for path in snapshot.artifacts:
        if path.startswith(receipt_prefix) and path.endswith(".json"):
            candidate = _record(snapshot, path, kind="receipt")
            _v2(candidate, kind="receipt")
            if candidate.get("outcome") in _TERMINAL:
                terminal_receipts.append(candidate)
    if len(terminal_receipts) != 1:
        _fail("posting_input_refused", "discovery Run does not have one unique terminal receipt")
    receipt_invocation = _invocation(receipt, operation="submit", project_id=resolved.project_id, gig_id=resolved.gig_id, run_id=run_id)
    receipt_ref = {"path": receipt_path, "content_sha256": digest_imported_bytes(snapshot.artifacts[receipt_path]), "media_type": "application/json", "size_bytes": len(snapshot.artifacts[receipt_path])}
    run_path = f"runs/{run_id}/external-run.json"
    run = _record(snapshot, run_path, kind="run")
    _v2(run, kind="run")
    if run.get("run_id") != run_id or run.get("project_id") != resolved.project_id or run.get("gig_id") != resolved.gig_id:
        _fail("posting_input_scope_mismatch", "discovery Run belongs to another scope")
    plan_ref, _ = _ref(snapshot, run.get("run_plan"), code="posting_input_refused")
    plan_path = plan_ref["path"]
    if not isinstance(plan_path, str) or not plan_path.startswith("run-plans/") or not plan_path.endswith("/external-plan.json"):
        _fail("posting_input_refused", "discovery Run Plan reference is invalid")
    plan = _record(snapshot, plan_path, kind="plan")
    _v2(plan, kind="plan")
    plan_id = plan_path.split("/")[1]
    if plan.get("run_plan_id") != plan_id or plan.get("project_id") != resolved.project_id or plan.get("gig_id") != resolved.gig_id:
        _fail("posting_input_scope_mismatch", "discovery Plan belongs to another scope")
    _invocation(plan, operation="plan", project_id=resolved.project_id, gig_id=resolved.gig_id)
    _invocation(run, operation="start", project_id=resolved.project_id, gig_id=resolved.gig_id, run_plan_id=plan_id)
    if run.get("gig_version") != plan.get("gig_version") or receipt_invocation.get("input", {}).get("run_id") != run_id:
        _fail("posting_input_refused", "historical Plan, Run, and receipt versions differ")
    if run.get("run_plan") != plan_ref or receipt.get("run_plan") != plan_ref:
        _fail("posting_input_refused", "discovery Plan provenance differs across records")
    outputs = receipt.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], Mapping) or outputs[0].get("kind") != "discovery":
        _fail("posting_input_refused", "discovery output tuple is not unique")
    output = outputs[0]
    history = _checkpoint_history(snapshot, run_id=run_id, plan_ref=plan_ref, project_id=resolved.project_id, gig_id=resolved.gig_id)
    matched: list[tuple[dict[str, object], str]] = []
    for checkpoint, path in history:
        for candidate in checkpoint.get("artifacts", []):
            if isinstance(candidate, Mapping) and candidate.get("kind") == "discovery" and dict(candidate) == dict(output):
                matched.append((checkpoint, path))
    if len(matched) != 1:
        _fail("posting_input_refused", "discovery output is not one exact committed checkpoint tuple")
    checkpoint, checkpoint_path = matched[0]
    checkpoint_id = checkpoint.get("checkpoint_id")
    if not isinstance(checkpoint_id, str) or checkpoint_path != f"runs/{run_id}/checkpoints/{checkpoint_id}.json":
        _fail("posting_input_refused", "discovery checkpoint identity does not match its path")
    binding = _domain_binding(snapshot, plan)
    graph = _graph(snapshot, plan, resolved=resolved)
    output_tuple, supporting, domain_value = _output(snapshot, output, run_id=run_id, checkpoint_id=checkpoint_id)
    checks = receipt.get("checks")
    if not isinstance(checks, list) or not checks:
        _fail("posting_input_refused", "succeeded discovery receipt lacks checks")
    required_checks = _check_contract(snapshot, plan)
    checkpoint_checks: dict[str, Mapping[str, object]] = {}
    for history_checkpoint, _history_path in history:
        for item in history_checkpoint.get("artifacts", []):
            if (
                isinstance(item, Mapping)
                and set(item) == {"kind", "markdown", "sidecar"}
                and isinstance(item.get("sidecar"), Mapping)
                and isinstance(item["sidecar"].get("path"), str)
            ):
                checkpoint_checks[item["sidecar"]["path"]] = item
    check_kinds: set[str] = set()
    output_digest = output.get("markdown", {}).get("content_sha256") if isinstance(output.get("markdown"), Mapping) else None
    for check in checks:
        check_ref, check_bytes = _ref(snapshot, check, code="posting_input_refused")
        check_item = checkpoint_checks.get(check_ref["path"])
        if check_item is None or check_item.get("sidecar") != check_ref:
            _fail("posting_input_refused", "discovery receipt check is not a committed checkpoint check")
        try:
            check_value = parse_json_bytes(check_bytes)
        except ValueError as exc:
            raise ScoutPostingInputError("posting_input_refused", "discovery completion check is invalid") from exc
        if (
            not isinstance(check_value, Mapping)
            or canonical_json_bytes(check_value) != check_bytes
            or set(check_value) != {"evidence_kind", "run_id", "output_sha256", "result"}
            or check_value.get("run_id") != run_id
            or check_value.get("evidence_kind") != check_item.get("kind")
            or check_value.get("result") != "pass"
            or check_value.get("output_sha256") != output_digest
        ):
            _fail("posting_input_refused", "discovery completion check did not pass")
        check_kinds.add(str(check_value["evidence_kind"]))
    if check_kinds != required_checks:
        _fail("posting_input_refused", "historical discovery checks do not cover the sealed contract")
    selected_inputs = plan.get("inputs")
    preference_bytes = _preference_bytes(snapshot, selected_inputs)
    try:
        binding["validator"](
            value=domain_value, markdown=snapshot.artifacts[output_tuple["markdown"]["path"]],
            supporting=supporting, preference_bytes=preference_bytes, run_id=run_id,
            project_id=resolved.project_id, gig_id=resolved.gig_id,
            gig_version=plan["gig_version"], graph_id=graph["goal_graph_id"],
            graph_selector=graph["graph_selector"], graph_version=graph["graph_version"],
            selected_inputs=selected_inputs,
        )
    except ScoutPostingInputError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", "posting_input_refused")
        raise ScoutPostingInputError(code, "historical discovery domain does not validate") from exc
    discovery = domain_value.get("discovery")
    if not isinstance(discovery, Mapping) or not isinstance(discovery.get("postings"), list):
        _fail("posting_input_refused", "historical discovery postings are unavailable")
    matches = [item for item in discovery["postings"] if isinstance(item, Mapping) and item.get("opportunity_id") == opportunity_id and item.get("snapshot_id") == snapshot_id]
    if len(matches) != 1:
        _fail("posting_input_not_found", "requested discovery posting snapshot is unavailable")
    posting = matches[0]
    source = posting.get("source")
    capture = source.get("capture_ref") if isinstance(source, Mapping) else None
    if not isinstance(source, Mapping) or not isinstance(capture, Mapping) or not isinstance(capture.get("artifact_id"), str):
        _fail("posting_input_incomplete", "requested discovery posting has no captured source bytes")
    artifact_id = capture["artifact_id"]
    posting_bytes = supporting.get(artifact_id)
    if posting_bytes is None:
        _fail("posting_input_refused", "requested discovery posting capture is unavailable")
    posting_ref = next((item for item in output_tuple["supporting_artifacts"] if item["artifact_id"] == artifact_id), None)
    if posting_ref is None:
        _fail("posting_input_refused", "requested discovery posting capture ref is not committed")
    run_ref = {"path": run_path, "content_sha256": digest_imported_bytes(snapshot.artifacts[run_path]), "media_type": "application/json", "size_bytes": len(snapshot.artifacts[run_path])}
    checkpoint_ref = {"path": checkpoint_path, "content_sha256": digest_imported_bytes(snapshot.artifacts[checkpoint_path]), "media_type": "application/json", "size_bytes": len(snapshot.artifacts[checkpoint_path])}
    return {
        "family": "scout_discovery_posting", "run_id": run_id, "receipt_id": receipt_id,
        "output_kind": "discovery", "project_id": resolved.project_id, "gig_id": resolved.gig_id,
        "gig_version": plan["gig_version"], "run_plan_id": plan["run_plan_id"], "run_plan_ref": plan_ref,
        "run_ref": run_ref, "receipt_ref": receipt_ref, "checkpoint_id": checkpoint_id,
        "checkpoint_ref": checkpoint_ref, "opportunity_id": opportunity_id, "snapshot_id": snapshot_id,
        "selected_graph": graph,
        "posting": dict(posting), "posting_ref": posting_ref, "posting_bytes": posting_bytes,
        "discovery_output": output_tuple,
        "domain_binding": {key: value for key, value in binding.items() if key != "validator"},
    }


def resolve_discovery_posting_input(
    resolved: ResolvedWorkpad, snapshot: JournalSnapshot, raw: object
) -> dict[str, object]:
    """Resolve one exact completed discovery posting from pinned bytes."""
    try:
        return _resolve(resolved, snapshot, raw)
    except ScoutPostingInputError:
        raise
    except Exception as exc:
        raise ScoutPostingInputError("posting_input_refused", "discovery posting input is malformed or unauthenticated") from exc


def _ref_path(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    path = value.get("path")
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or ".." in Path(path).parts:
        return None
    return path


def _checkpoint_path(path: str, *, run_id: str) -> str | None:
    artifact_prefix = f"runs/{run_id}/artifacts/"
    if path.startswith(artifact_prefix):
        suffix = path[len(artifact_prefix):]
        checkpoint_id = suffix.split("/", 1)[0]
        if checkpoint_id and "/" in suffix:
            return f"runs/{run_id}/checkpoints/{checkpoint_id}.json"
    checkpoint_prefix = f"runs/{run_id}/checkpoints/"
    if path.startswith(checkpoint_prefix):
        checkpoint_id = path[len(checkpoint_prefix):].split("/", 1)[0].removesuffix(".json")
        if checkpoint_id:
            return f"{checkpoint_prefix}{checkpoint_id}.json"
    return None


def hydrate_discovery_posting_input_snapshot(
    resolved: ResolvedWorkpad, raw: object, *, writer: JournalWriter | None = None
) -> JournalSnapshot:
    """Hydrate exact discovery records while optionally holding one writer lock."""
    try:
        if not isinstance(raw, Mapping) or set(raw) != _RAW_KEYS:
            _fail("posting_input_invalid", "discovery posting selector must contain exactly its closed fields")
        if raw.get("family") != "scout_discovery" or raw.get("output_kind") != "discovery":
            _fail("posting_input_invalid", "discovery posting selector is unsupported")
        run_id = _identity(raw.get("run_id"), EntityPrefix.RUN, name="Run")
        receipt_id = _identity(raw.get("receipt_id"), EntityPrefix.RECEIPT, name="receipt")
        receipt_path = f"runs/{run_id}/receipts/{receipt_id}.json"

        def operation(locked: JournalWriter) -> JournalSnapshot:
            bootstrap = locked.snapshot((f"runs/{run_id}/", "run-plans/"))
            receipt = _record(bootstrap, receipt_path, kind="receipt")
            run_path = f"runs/{run_id}/external-run.json"
            run = _record(bootstrap, run_path, kind="run")
            plan_ref, _ = _ref(bootstrap, run.get("run_plan"), code="posting_input_refused")
            plan_path = _ref_path(plan_ref)
            if plan_path is None:
                _fail("posting_input_refused", "historical discovery Run Plan ref is malformed")
            plan = _record(bootstrap, plan_path, kind="plan")
            exact: set[str] = {receipt_path, run_path, plan_path}
            run_prefix = f"runs/{run_id}/"
            exact.update(path for path in bootstrap.artifacts if path.startswith(f"{run_prefix}checkpoints/") or path.startswith(f"{run_prefix}receipts/"))
            for value in (plan.get("selected_graph"), plan.get("output_contract"), plan.get("check_contract")):
                path = _ref_path(value)
                if path is None:
                    _fail("posting_input_refused", "historical discovery Plan ref is malformed")
                exact.add(path)
            outputs = receipt.get("outputs")
            if not isinstance(outputs, list):
                _fail("posting_input_refused", "historical discovery receipt outputs are malformed")
            for item in outputs:
                if isinstance(item, Mapping) and item.get("kind") == "discovery":
                    for key in ("markdown", "sidecar", "domain_sidecar"):
                        path = _ref_path(item.get(key))
                        if path is None:
                            _fail("posting_input_refused", "historical discovery output ref is malformed")
                        exact.add(path)
                    supporting = item.get("supporting_artifacts")
                    if not isinstance(supporting, list):
                        _fail("posting_input_refused", "historical discovery supporting refs are malformed")
                    for artifact in supporting:
                        path = _ref_path(artifact.get("ref")) if isinstance(artifact, Mapping) else None
                        if path is None:
                            _fail("posting_input_refused", "historical discovery supporting ref is malformed")
                        exact.add(path)
            checks = receipt.get("checks")
            if not isinstance(checks, list):
                _fail("posting_input_refused", "historical discovery receipt checks are malformed")
            for check in checks:
                path = _ref_path(check)
                if path is None:
                    _fail("posting_input_refused", "historical discovery completion check ref is malformed")
                exact.add(path)
            for path in tuple(exact):
                checkpoint = _checkpoint_path(path, run_id=run_id)
                if checkpoint is not None:
                    exact.add(checkpoint)
            contract_path = _ref_path(plan.get("output_contract"))
            if contract_path is None:
                _fail("posting_input_refused", "historical discovery output contract ref is malformed")
            contract_data, _ = read_committed_artifact(workpad=locked.root, project_id=locked.project_id, gig_id=locked.gig_id, path=contract_path, head=bootstrap.head)
            try:
                contract = parse_json_bytes(contract_data)
            except ValueError as exc:
                raise ScoutPostingInputError("posting_input_refused", "historical discovery output contract is invalid") from exc
            domains = contract.get("domains") if isinstance(contract, Mapping) else None
            discovery = domains.get("discovery") if isinstance(domains, Mapping) else None
            if not isinstance(discovery, Mapping):
                _fail("posting_input_refused", "historical discovery domain binding is absent")
            for key in ("schema_ref", "validator_source_ref"):
                path = _ref_path(discovery.get(key))
                if path is None:
                    _fail("posting_input_refused", "historical discovery validator ref is malformed")
                exact.add(path)
            selected_inputs = plan.get("inputs")
            if isinstance(selected_inputs, list):
                for item in selected_inputs:
                    content = item.get("content") if isinstance(item, Mapping) else None
                    blob = content.get("blob_ref") if isinstance(content, Mapping) else None
                    path = _ref_path(blob)
                    if path is not None:
                        exact.add(path)
            hydrated: dict[str, bytes] = {}
            for path in sorted(exact):
                data, _ = read_committed_artifact(workpad=locked.root, project_id=locked.project_id, gig_id=locked.gig_id, path=path, head=bootstrap.head)
                hydrated[path] = data
            return JournalSnapshot(bootstrap.head, hydrated)

        if writer is not None:
            if writer.root != resolved.path or writer.project_id != resolved.project_id or writer.gig_id != resolved.gig_id:
                _fail("posting_input_scope_mismatch", "provided journal writer belongs to another workpad")
            return operation(writer)
        return run_with_journal_writer(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=operation)
    except ScoutPostingInputError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", "posting_input_refused")
        raise ScoutPostingInputError(code, "discovery posting snapshot hydration was refused") from exc


def resolve_discovery_posting_input_from_journal(
    resolved: ResolvedWorkpad, raw: object, *, writer: JournalWriter | None = None
) -> dict[str, object]:
    """Hydrate and resolve one exact discovery posting under a pinned writer."""
    snapshot = hydrate_discovery_posting_input_snapshot(resolved, raw, writer=writer)
    return resolve_discovery_posting_input(resolved, snapshot, raw)


__all__ = [
    "ScoutPostingInputError", "hydrate_discovery_posting_input_snapshot",
    "resolve_discovery_posting_input", "resolve_discovery_posting_input_from_journal",
]
