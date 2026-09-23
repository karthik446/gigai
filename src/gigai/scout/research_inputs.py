"""Read-only resolution of a completed external Scout research output.

This module deliberately accepts a caller-owned :class:`JournalSnapshot`.  It
does not inspect the active Gig pointer, search for a latest Run, read mutable
working-tree bytes, or import code from a Gig.  The only validator it executes
is the fixed packaged v2 research bridge.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from pathlib import Path

from ..canonical import (
    EntityPrefix,
    canonical_json_bytes,
    digest_imported_bytes,
    parse_json_bytes,
    validate_entity_id,
)
from ..journal import JournalSnapshot, JournalWriter, read_committed_artifact, run_with_journal_writer
from ..workpad import ResolvedWorkpad


_RAW_KEYS = frozenset({"family", "run_id", "receipt_id", "output_kind"})
_OUTPUT_KEYS = frozenset(
    {"kind", "markdown", "sidecar", "domain_sidecar", "supporting_artifacts"}
)
_REF_KEYS = frozenset({"path", "content_sha256", "media_type", "size_bytes"})
_DOMAIN_KEYS = frozenset(
    {"schema_id", "schema_ref", "validator_id", "validator_source_ref"}
)
_DOMAIN_SCHEMA_ID = "urn:gigai:scout:research-packet:2"
_VALIDATOR_ID = "scout-role-research:2"
_VALIDATOR_SOURCE = "gigai.scout.research:validate_research_domain"
_SCHEMA_RESOURCE = (
    "scout/data/tools/cap_00000000-0000-4000-8000-000000000071/research.schema.json"
)
_V3_DOMAIN_SCHEMA_ID = "urn:gigai:scout:research-packet:3"
_V3_VALIDATOR_ID = "scout-role-research:3"
_V3_VALIDATOR_SOURCE = "gigai.scout.research_v3:validate_research_domain"
_V3_SCHEMA_RESOURCE = (
    "scout/data/tools/cap_00000000-0000-4000-8000-000000000076/research.schema.json"
)


class ResearchInputError(ValueError):
    """Typed refusal for an unavailable or unauthenticated research input."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise ResearchInputError(code, message)


def _identity(value: object, prefix: EntityPrefix, *, name: str) -> str:
    if not isinstance(value, str):
        _fail("research_input_invalid", f"{name} identity is invalid")
    try:
        validate_entity_id(value, expected_prefix=prefix)
    except Exception as exc:
        raise ResearchInputError("research_input_invalid", f"{name} identity is invalid") from exc
    return value


def _external():
    # Lazy import keeps this module safe to wire into scout_inputs later.
    from .. import external_recording

    return external_recording


def _ref(snapshot: JournalSnapshot, value: object, *, code: str) -> tuple[dict[str, object], bytes]:
    if not isinstance(value, Mapping) or set(value) != _REF_KEYS:
        _fail(code, "research artifact reference is malformed")
    path = value.get("path")
    digest = value.get("content_sha256")
    size = value.get("size_bytes")
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
        _fail(code, "research artifact reference is malformed")
    data = snapshot.artifacts.get(path)
    if data is None or digest_imported_bytes(data) != digest or len(data) != size:
        _fail(code, "research artifact reference is unavailable or changed")
    return dict(value), data


def _record(snapshot: JournalSnapshot, path: str, *, kind: str) -> dict[str, object]:
    data = snapshot.artifacts.get(path)
    if data is None:
        _fail("research_input_not_found", f"completed research {kind} is unavailable")
    try:
        payload = parse_json_bytes(data)
    except ValueError as exc:
        raise ResearchInputError("research_input_refused", f"research {kind} is invalid") from exc
    if not isinstance(payload, dict):
        _fail("research_input_refused", f"research {kind} is invalid")
    try:
        if canonical_json_bytes(payload) != data:
            _fail("research_input_refused", f"research {kind} is not canonical")
    except ResearchInputError:
        raise
    except Exception as exc:
        raise ResearchInputError("research_input_refused", f"research {kind} is malformed") from exc
    external = _external()
    try:
        schemas = {
            "plan": ("external-recording-plan.schema.json", "external-recording-plan-v2.schema.json"),
            "run": ("external-recording-run.schema.json", "external-recording-run-v2.schema.json"),
            "checkpoint": ("external-recording-checkpoint.schema.json", "external-recording-checkpoint-v2.schema.json"),
            "receipt": ("external-recording-receipt.schema.json", "external-recording-receipt-v2.schema.json"),
        }[kind]
        return external._recorded_dispatch(
            data, v1_schema=schemas[0], v2_schema=schemas[1], code="research_input_refused"
        )
    except Exception as exc:
        if isinstance(exc, ResearchInputError):
            raise
        code = getattr(exc, "code", "research_input_refused")
        if code == "external_protocol_unsupported":
            code = "research_input_unsupported"
        raise ResearchInputError(code, f"research {kind} is not a valid v2 record") from exc


def _v2(record: Mapping[str, object], *, kind: str) -> None:
    if record.get("schema_version") != "2.0":
        _fail("research_input_unsupported", f"research {kind} is not a v2 record")


def _json_ref(snapshot: JournalSnapshot, value: object, *, code: str) -> tuple[dict[str, object], dict[str, object]]:
    ref, data = _ref(snapshot, value, code=code)
    try:
        parsed = parse_json_bytes(data)
    except ValueError as exc:
        raise ResearchInputError(code, "research JSON artifact is invalid") from exc
    if not isinstance(parsed, dict) or canonical_json_bytes(parsed) != data:
        _fail(code, "research JSON artifact is not canonical")
    return ref, parsed


def _fixed_domain_binding(
    snapshot: JournalSnapshot,
    plan: Mapping[str, object],
    resolved: ResolvedWorkpad,
) -> dict[str, object]:
    contract_ref, contract = _json_ref(
        snapshot, plan.get("output_contract"), code="research_input_refused"
    )
    if (
        set(contract) != {"schema_version", "kind", "gig_id", "fields", "domains"}
        or contract.get("schema_version") != "2.0"
        or contract.get("kind") != "run_output_contract"
        or contract.get("gig_id") != resolved.gig_id
        or contract.get("fields") != ["research"]
        or not isinstance(contract.get("domains"), Mapping)
        or set(contract["domains"]) != {"research"}
    ):
        _fail("research_input_refused", "historical research output contract is not the exact sealed v2 contract")
    domains = contract.get("domains")
    if not isinstance(domains, Mapping) or not isinstance(domains.get("research"), Mapping):
        _fail("research_input_refused", "historical research domain binding is absent")
    binding = dict(domains["research"])
    if set(binding) != _DOMAIN_KEYS:
        _fail("research_input_unsupported", "historical research domain binding is incomplete")
    identity = (binding.get("schema_id"), binding.get("validator_id"))
    versions = {
        (_DOMAIN_SCHEMA_ID, _VALIDATOR_ID): (_SCHEMA_RESOURCE, "gigai.scout.research", "validate_research_domain"),
        (_V3_DOMAIN_SCHEMA_ID, _V3_VALIDATOR_ID): (_V3_SCHEMA_RESOURCE, "gigai.scout.research_v3", "validate_research_domain"),
    }
    spec = versions.get(identity)
    if spec is None:
        _fail("research_input_validator_unsupported", "historical research validator is unsupported")
    if not isinstance(binding.get("schema_ref"), Mapping) or not isinstance(binding.get("validator_source_ref"), Mapping):
        _fail("research_input_refused", "historical research validator references are malformed")
    schema_ref, schema_bytes = _ref(snapshot, binding["schema_ref"], code="research_input_refused")
    source_ref, source_bytes = _ref(snapshot, binding["validator_source_ref"], code="research_input_refused")
    if not str(schema_ref["path"]).endswith("research.schema.json") or not str(source_ref["path"]).endswith("research.py"):
        _fail("research_input_refused", "historical research validator paths are invalid")
    try:
        module = __import__(f"gigai.{spec[1].removeprefix('gigai.')}", fromlist=[spec[2]])
        validate_research_domain = getattr(module, spec[2])
        trusted_schema = resources.files("gigai").joinpath(*spec[0].split("/")).read_bytes()
        trusted_source_path = resources.files("gigai").joinpath(*spec[0].split("/")[:-1], "research.py")
        trusted_source = trusted_source_path.read_bytes()
    except (AttributeError, FileNotFoundError, OSError, ImportError) as exc:
        raise ResearchInputError(
            "research_input_validator_unavailable", "fixed historical research validator is unavailable"
        ) from exc
    if schema_bytes != trusted_schema or source_bytes != trusted_source:
        _fail("research_input_refused", "historical research validator bytes differ from the fixed package")
    return {
        "schema_id": binding["schema_id"],
        "schema_ref": schema_ref,
        "validator_id": binding["validator_id"],
        "validator_source_ref": source_ref,
        "validator": validate_research_domain,
        "contract_ref": contract_ref,
    }


def _graph(snapshot: JournalSnapshot, plan: Mapping[str, object]) -> dict[str, object]:
    graph_ref, graph = _json_ref(snapshot, plan.get("selected_graph"), code="research_input_refused")
    graph_id = graph.get("graph_id")
    graph_version = graph.get("graph_version")
    selected = plan.get("selected_graph_id")
    goal = plan.get("goal_graph_id")
    invocation = plan.get("invocation")
    original = invocation.get("input") if isinstance(invocation, Mapping) else None
    selector = original.get("graph_selector") if isinstance(original, Mapping) else None
    if (
        not isinstance(graph_id, str)
        or graph_id != goal
        or not isinstance(selected, str)
        or selected != selector
        or type(graph_version) is not int
        or graph_version < 1
        or selector != "research-role"
    ):
        _fail("research_input_refused", "historical selected graph identity is invalid")
    return {
        "selected_graph_id": selected,
        "goal_graph_id": graph_id,
        "graph_version": graph_version,
        "graph_selector": selector,
        "graph_ref": graph_ref,
    }


def _supporting(
    snapshot: JournalSnapshot,
    output: Mapping[str, object],
    *,
    run_id: str,
    checkpoint_id: str,
) -> list[dict[str, object]]:
    raw = output.get("supporting_artifacts")
    if not isinstance(raw, list):
        _fail("research_input_refused", "historical supporting refs are malformed")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"artifact_id", "ref"}:
            _fail("research_input_refused", "historical supporting ref is malformed")
        artifact_id = item.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id or artifact_id in seen:
            _fail("research_input_refused", "historical supporting ref identity is invalid")
        ref, _data = _ref(snapshot, item.get("ref"), code="research_input_refused")
        expected = f"runs/{run_id}/artifacts/{checkpoint_id}/"
        if not str(ref["path"]).startswith(expected) or not str(ref["path"]).endswith(f".supporting/{artifact_id}.bin"):
            _fail("research_input_refused", "historical supporting ref escaped its Run")
        seen.add(artifact_id)
        result.append({"artifact_id": artifact_id, "ref": ref})
    return result


def _output(
    snapshot: JournalSnapshot,
    output: Mapping[str, object],
    *,
    run_id: str,
    checkpoint_id: str,
    selected_inputs: object,
    validator: object,
    graph: Mapping[str, object],
    plan: Mapping[str, object],
) -> dict[str, object]:
    if set(output) != _OUTPUT_KEYS or output.get("kind") != "research":
        _fail("research_input_refused", "historical research output tuple is malformed")
    markdown_ref, markdown = _ref(snapshot, output.get("markdown"), code="research_input_refused")
    sidecar_ref, sidecar_bytes = _ref(snapshot, output.get("sidecar"), code="research_input_refused")
    domain_ref, domain = _json_ref(snapshot, output.get("domain_sidecar"), code="research_input_refused")
    expected = f"runs/{run_id}/artifacts/{checkpoint_id}/"
    if not all(str(ref["path"]).startswith(expected) for ref in (markdown_ref, sidecar_ref, domain_ref)):
        _fail("research_input_refused", "historical research output ref escaped its Run")
    try:
        sidecar = parse_json_bytes(sidecar_bytes)
    except ValueError as exc:
        raise ResearchInputError("research_input_refused", "historical research sidecar is invalid") from exc
    if (
        not isinstance(sidecar, dict)
        or canonical_json_bytes(sidecar) != sidecar_bytes
        or set(sidecar) != {"document_sha256", "output_kind", "run_id", "selected_inputs"}
        or sidecar.get("document_sha256") != digest_imported_bytes(markdown)
        or sidecar.get("output_kind") != "research"
        or sidecar.get("run_id") != run_id
        or sidecar.get("selected_inputs") != selected_inputs
    ):
        _fail("research_input_refused", "historical research sidecar does not bind its tuple")
    if set(domain) != {"schema_id", "value"} or domain.get("schema_id") not in {_DOMAIN_SCHEMA_ID, _V3_DOMAIN_SCHEMA_ID} or not isinstance(domain.get("value"), Mapping):
        _fail("research_input_refused", "historical research domain sidecar is malformed")
    supporting_refs = _supporting(snapshot, output, run_id=run_id, checkpoint_id=checkpoint_id)
    supporting = {
        item["artifact_id"]: snapshot.artifacts[item["ref"]["path"]]
        for item in supporting_refs
    }
    try:
        validator(
            value=domain["value"], markdown=markdown, supporting=supporting,
            run_id=run_id, project_id=plan["project_id"], gig_id=plan["gig_id"],
            gig_version=plan["gig_version"], graph_id=graph["goal_graph_id"],
            graph_selector=graph["graph_selector"], graph_version=graph["graph_version"],
            selected_inputs=selected_inputs,
        )
    except Exception as exc:
        code = getattr(exc, "code", "research_input_refused")
        raise ResearchInputError(code, "historical research domain does not validate") from exc
    return {
        "kind": "research", "markdown": markdown_ref, "sidecar": sidecar_ref,
        "domain_sidecar": domain_ref, "supporting_artifacts": supporting_refs,
    }


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
        _fail("research_input_refused", f"historical {operation} invocation identity is invalid")
    inputs = invocation["input"]
    if run_id is not None and inputs.get("run_id") != run_id:
        _fail("research_input_refused", "historical invocation Run identity differs")
    if run_plan_id is not None and inputs.get("run_plan_id") != run_plan_id:
        _fail("research_input_refused", "historical invocation Plan identity differs")
    return invocation


def _check_contract(
    snapshot: JournalSnapshot, plan: Mapping[str, object], resolved: ResolvedWorkpad
) -> set[str]:
    contract_ref, contract = _json_ref(
        snapshot, plan.get("check_contract"), code="research_input_refused"
    )
    if (
        set(contract) != {"schema_version", "kind", "gig_id", "fields"}
        or contract.get("schema_version") != "1.0"
        or contract.get("kind") != "completion_evidence_contract"
        or contract.get("gig_id") != resolved.gig_id
        or not isinstance(contract.get("fields"), list)
        or not contract["fields"]
        or not all(isinstance(item, str) and item for item in contract["fields"])
        or len(set(contract["fields"])) != len(contract["fields"])
    ):
        _fail("research_input_refused", "historical completion check contract is invalid")
    del contract_ref
    return set(contract["fields"])


def _checkpoint_history(
    snapshot: JournalSnapshot,
    *,
    run_id: str,
    plan_ref: Mapping[str, object],
    project_id: str,
    gig_id: str,
) -> list[tuple[dict[str, object], str]]:
    history: list[tuple[dict[str, object], str]] = []
    prefix = f"runs/{run_id}/checkpoints/"
    for path in snapshot.artifacts:
        if path.startswith(prefix) and path.endswith(".json"):
            checkpoint = _record(snapshot, path, kind="checkpoint")
            _v2(checkpoint, kind="checkpoint")
            checkpoint_id = checkpoint.get("checkpoint_id")
            if not isinstance(checkpoint_id, str) or path != f"{prefix}{checkpoint_id}.json":
                _fail("research_input_refused", "historical checkpoint identity does not match its path")
            if (
                checkpoint.get("run_id") != run_id
                or checkpoint.get("run_plan") != plan_ref
            ):
                _fail("research_input_refused", "historical checkpoint Plan or Run identity differs")
            _invocation(
                checkpoint, operation="checkpoint", project_id=project_id,
                gig_id=gig_id, run_id=run_id,
            )
            history.append((checkpoint, path))
    history.sort(key=lambda item: item[0].get("sequence", 0))
    parent = None
    for sequence, (checkpoint, _path) in enumerate(history, start=1):
        if checkpoint.get("sequence") != sequence or checkpoint.get("parent_checkpoint") != parent:
            _fail("research_input_refused", "historical checkpoint sequence or parent is invalid")
        parent = checkpoint["checkpoint_id"]
    return history


def _resolve(resolved: ResolvedWorkpad, snapshot: JournalSnapshot, raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping) or set(raw) != _RAW_KEYS:
        _fail("research_input_invalid", "research selector must contain exactly its closed fields")
    if raw.get("family") != "scout_research" or raw.get("output_kind") != "research":
        _fail("research_input_invalid", "research selector is unsupported")
    run_id = _identity(raw.get("run_id"), EntityPrefix.RUN, name="Run")
    receipt_id = _identity(raw.get("receipt_id"), EntityPrefix.RECEIPT, name="receipt")
    receipt_path = f"runs/{run_id}/receipts/{receipt_id}.json"
    receipt = _record(snapshot, receipt_path, kind="receipt")
    _v2(receipt, kind="receipt")
    if receipt.get("receipt_id") != receipt_id or receipt.get("run_id") != run_id:
        _fail("research_input_refused", "receipt identity does not match its path")
    if receipt.get("outcome") != "succeeded":
        _fail("research_input_not_terminal", "research Run does not have a succeeded terminal receipt")
    terminal_receipts = []
    receipt_prefix = f"runs/{run_id}/receipts/"
    for path in snapshot.artifacts:
        if path.startswith(receipt_prefix) and path.endswith(".json"):
            candidate = _record(snapshot, path, kind="receipt")
            _v2(candidate, kind="receipt")
            if candidate.get("outcome") in {"succeeded", "cancelled"}:
                terminal_receipts.append(candidate)
    if len(terminal_receipts) != 1:
        _fail("research_input_refused", "research Run does not have one unique terminal receipt")
    receipt_invocation = _invocation(
        receipt, operation="submit", project_id=resolved.project_id,
        gig_id=resolved.gig_id, run_id=run_id,
    )
    receipt_ref = {"path": receipt_path, "content_sha256": digest_imported_bytes(snapshot.artifacts[receipt_path]), "media_type": "application/json", "size_bytes": len(snapshot.artifacts[receipt_path])}
    run_path = f"runs/{run_id}/external-run.json"
    run = _record(snapshot, run_path, kind="run")
    _v2(run, kind="run")
    if run.get("run_id") != run_id or run.get("project_id") != resolved.project_id or run.get("gig_id") != resolved.gig_id:
        _fail("research_input_scope_mismatch", "research Run belongs to another scope")
    plan_ref, _plan_bytes = _ref(snapshot, run.get("run_plan"), code="research_input_refused")
    plan_path = plan_ref["path"]
    if not isinstance(plan_path, str) or not plan_path.startswith("run-plans/") or not plan_path.endswith("/external-plan.json"):
        _fail("research_input_refused", "research Run Plan reference is invalid")
    plan = _record(snapshot, plan_path, kind="plan")
    _v2(plan, kind="plan")
    plan_id = plan_path.split("/")[1]
    if plan.get("run_plan_id") != plan_id or plan.get("project_id") != resolved.project_id or plan.get("gig_id") != resolved.gig_id:
        _fail("research_input_scope_mismatch", "research Plan belongs to another scope")
    _invocation(
        plan, operation="plan", project_id=resolved.project_id,
        gig_id=resolved.gig_id,
    )
    _invocation(
        run, operation="start", project_id=resolved.project_id,
        gig_id=resolved.gig_id, run_plan_id=plan_id,
    )
    if (
        run.get("gig_version") != plan.get("gig_version")
        or receipt_invocation.get("input", {}).get("run_id") != run_id
    ):
        _fail("research_input_refused", "historical Plan, Run, and receipt versions differ")
    if run.get("run_plan") != plan_ref or receipt.get("run_plan") != plan_ref:
        _fail("research_input_refused", "research Plan provenance differs across records")
    if not isinstance(receipt.get("outputs"), list):
        _fail("research_input_refused", "research receipt outputs are malformed")
    outputs = [item for item in receipt["outputs"] if isinstance(item, Mapping) and item.get("kind") == "research"]
    if len(outputs) != 1:
        _fail("research_input_refused", "research output tuple is not unique")
    output = outputs[0]
    history = _checkpoint_history(
        snapshot, run_id=run_id, plan_ref=plan_ref,
        project_id=resolved.project_id, gig_id=resolved.gig_id,
    )
    # Locate and authenticate the exact checkpoint tuple before running the
    # fixed bridge against its committed Markdown and sidecar bytes.
    matched: list[tuple[dict[str, object], str]] = []
    for checkpoint, path in history:
        for candidate in checkpoint.get("artifacts", []):
            if isinstance(candidate, Mapping) and candidate.get("kind") == "research" and dict(candidate) == dict(output):
                matched.append((checkpoint, path))
    if len(matched) != 1:
        _fail("research_input_refused", "research output is not one exact committed checkpoint tuple")
    checkpoint, checkpoint_path = matched[0]
    checkpoint_id = checkpoint.get("checkpoint_id")
    if not isinstance(checkpoint_id, str) or checkpoint_path != f"runs/{run_id}/checkpoints/{checkpoint_id}.json":
        _fail("research_input_refused", "research checkpoint identity does not match its path")
    domain_binding = _fixed_domain_binding(snapshot, plan, resolved)
    graph = _graph(snapshot, plan)
    output_tuple = _output(
        snapshot, output, run_id=run_id, checkpoint_id=checkpoint_id,
        selected_inputs=plan.get("inputs"), validator=domain_binding["validator"], graph=graph, plan=plan,
    )
    checks = receipt.get("checks")
    if not isinstance(checks, list) or not checks:
        _fail("research_input_refused", "succeeded research receipt lacks checks")
    required_checks = _check_contract(snapshot, plan, resolved)
    checkpoint_checks = {}
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
    output_digest = output["markdown"].get("content_sha256") if isinstance(output.get("markdown"), Mapping) else None
    for check in checks:
        ref, data = _ref(snapshot, check, code="research_input_refused")
        if ref["path"] not in checkpoint_checks or checkpoint_checks[ref["path"]]["sidecar"] != ref:
            _fail("research_input_refused", "receipt check is not a committed checkpoint check")
        try:
            value = parse_json_bytes(data)
        except ValueError as exc:
            raise ResearchInputError("research_input_refused", "research check sidecar is invalid") from exc
        check_item = checkpoint_checks[ref["path"]]
        if (
            not isinstance(value, Mapping)
            or set(value) != {"evidence_kind", "run_id", "output_sha256", "result"}
            or value.get("run_id") != run_id
            or value.get("evidence_kind") != check_item.get("kind")
            or value.get("result") != "pass"
            or not isinstance(value.get("output_sha256"), str)
            or value.get("output_sha256") != output_digest
        ):
            _fail("research_input_refused", "research completion check did not pass")
        check_kinds.add(str(value["evidence_kind"]))
    if check_kinds != required_checks:
        _fail("research_input_refused", "historical completion checks do not cover the sealed contract")
    run_ref = {"path": run_path, "content_sha256": digest_imported_bytes(snapshot.artifacts[run_path]), "media_type": "application/json", "size_bytes": len(snapshot.artifacts[run_path])}
    checkpoint_ref = {"path": checkpoint_path, "content_sha256": digest_imported_bytes(snapshot.artifacts[checkpoint_path]), "media_type": "application/json", "size_bytes": len(snapshot.artifacts[checkpoint_path])}
    return {
        "family": "scout_research", "run_id": run_id, "receipt_id": receipt_id, "output_kind": "research",
        "project_id": resolved.project_id, "gig_id": resolved.gig_id, "gig_version": plan["gig_version"],
        "run_plan_id": plan["run_plan_id"], "run_plan_ref": plan_ref, "run_ref": run_ref,
        "receipt_ref": receipt_ref, "checkpoint_id": checkpoint_id, "checkpoint_ref": checkpoint_ref,
        "selected_graph": graph, "output": output_tuple,
        "domain_binding": {key: value for key, value in domain_binding.items() if key != "validator"},
    }


def resolve_research_input(
    resolved: ResolvedWorkpad, snapshot: JournalSnapshot, raw: object
) -> dict[str, object]:
    """Resolve one exact completed v2 research selector from committed bytes."""
    try:
        return _resolve(resolved, snapshot, raw)
    except ResearchInputError:
        raise
    except Exception as exc:
        raise ResearchInputError(
            "research_input_refused", "research input is malformed or unauthenticated"
        ) from exc


def revalidate_research_input(
    resolved: ResolvedWorkpad, snapshot: JournalSnapshot, sealed: object
) -> None:
    """Revalidate a previously normalized selector against the same snapshot."""
    try:
        if not isinstance(sealed, Mapping):
            _fail("research_input_invalid", "sealed research input is invalid")
        required = {
            "family", "run_id", "receipt_id", "output_kind", "project_id", "gig_id", "gig_version",
            "run_plan_id", "run_plan_ref", "run_ref", "receipt_ref", "checkpoint_id", "checkpoint_ref",
            "selected_graph", "output", "domain_binding",
        }
        if set(sealed) != required:
            _fail("research_input_invalid", "sealed research input shape is invalid")
        raw = {key: sealed[key] for key in ("family", "run_id", "receipt_id", "output_kind")}
        current = _resolve(resolved, snapshot, raw)
        if current != dict(sealed):
            _fail("research_input_mismatch", "sealed research input changed or is unavailable")
    except ResearchInputError:
        raise
    except Exception as exc:
        raise ResearchInputError(
            "research_input_refused", "sealed research input is malformed or unauthenticated"
        ) from exc


def _ref_path(value: object) -> str | None:
    """Return a syntactically safe path from a record reference, if present."""
    if not isinstance(value, Mapping):
        return None
    path = value.get("path")
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        return None
    if ".." in Path(path).parts:
        return None
    return path


def _checkpoint_path(path: str, *, run_id: str) -> str | None:
    artifact_prefix = f"runs/{run_id}/artifacts/"
    if path.startswith(artifact_prefix):
        checkpoint_id = path[len(artifact_prefix):].split("/", 1)[0]
        if checkpoint_id and "/" in path[len(artifact_prefix):]:
            return f"runs/{run_id}/checkpoints/{checkpoint_id}.json"
    checkpoint_prefix = f"runs/{run_id}/checkpoints/"
    if path.startswith(checkpoint_prefix):
        checkpoint_id = path[len(checkpoint_prefix):].split("/", 1)[0].removesuffix(".json")
        if checkpoint_id:
            return f"{checkpoint_prefix}{checkpoint_id}.json"
    return None


def hydrate_research_input_snapshot(
    resolved: ResolvedWorkpad, raw: object, *, writer: JournalWriter | None = None
) -> JournalSnapshot:
    """Hydrate committed research refs under one pinned writer HEAD.

    The small Run-family bootstrap is used only to discover the sealed Plan,
    receipt, and checkpoint references.  Every checkpoint and receipt record
    under that Run is retained and authenticated, while unrelated manifests
    remain outside the snapshot.  Pass an already-held ``JournalWriter`` from
    a caller's publication transaction to avoid nested lock acquisition.
    """
    try:
        if not isinstance(raw, Mapping) or set(raw) != _RAW_KEYS:
            _fail("research_input_invalid", "research selector must contain exactly its closed fields")
        if raw.get("family") != "scout_research" or raw.get("output_kind") != "research":
            _fail("research_input_invalid", "research selector is unsupported")
        run_id = _identity(raw.get("run_id"), EntityPrefix.RUN, name="Run")
        receipt_id = _identity(raw.get("receipt_id"), EntityPrefix.RECEIPT, name="receipt")
        receipt_path = f"runs/{run_id}/receipts/{receipt_id}.json"

        def operation(writer):
            # This bootstrap establishes the writer-locked HEAD and verifies
            # all committed bytes in the Run family before exact hydration.
            bootstrap = writer.snapshot((f"runs/{run_id}/", "run-plans/"))
            receipt = _record(bootstrap, receipt_path, kind="receipt")
            run_path = f"runs/{run_id}/external-run.json"
            run = _record(bootstrap, run_path, kind="run")
            plan_ref, _ = _ref(bootstrap, run.get("run_plan"), code="research_input_refused")
            plan_path = plan_ref["path"]
            if not isinstance(plan_path, str):
                _fail("research_input_refused", "research Run Plan reference is invalid")
            plan = _record(bootstrap, plan_path, kind="plan")

            exact: set[str] = {receipt_path, run_path, plan_path}
            # History completeness is an authority requirement, not something
            # inferred from the selected output/check tuple.  Retain every
            # committed checkpoint and receipt at this pinned HEAD so omitted
            # predecessors and competing terminal records are visible.
            run_prefix = f"runs/{run_id}/"
            exact.update(
                path for path in bootstrap.artifacts
                if path.startswith(f"{run_prefix}checkpoints/")
                or path.startswith(f"{run_prefix}receipts/")
            )
            for value in (
                plan.get("selected_graph"), plan.get("output_contract"),
                plan.get("check_contract"),
            ):
                path = _ref_path(value)
                if path is None:
                    _fail("research_input_refused", "historical Plan reference is malformed")
                exact.add(path)

            outputs = receipt.get("outputs")
            checks = receipt.get("checks")
            if not isinstance(outputs, list) or not isinstance(checks, list):
                _fail("research_input_refused", "historical receipt references are malformed")
            for item in outputs:
                if isinstance(item, Mapping) and item.get("kind") == "research":
                    for key in ("markdown", "sidecar", "domain_sidecar"):
                        path = _ref_path(item.get(key))
                        if path is None:
                            _fail("research_input_refused", "historical output reference is malformed")
                        exact.add(path)
                    supporting = item.get("supporting_artifacts")
                    if not isinstance(supporting, list):
                        _fail("research_input_refused", "historical supporting refs are malformed")
                    for artifact in supporting:
                        path = _ref_path(artifact.get("ref")) if isinstance(artifact, Mapping) else None
                        if path is None:
                            _fail("research_input_refused", "historical supporting ref is malformed")
                        exact.add(path)
            for check in checks:
                path = _ref_path(check)
                if path is None:
                    _fail("research_input_refused", "historical check reference is malformed")
                exact.add(path)

            # Checkpoint history is needed for exact output/checkpoint binding,
            # including a check published in a later checkpoint.
            for path in tuple(exact):
                checkpoint = _checkpoint_path(path, run_id=run_id)
                if checkpoint is not None:
                    exact.add(checkpoint)

            # The sealed output contract points to the fixed schema and source;
            # read the contract at this HEAD before discovering those exact refs.
            contract_path = _ref_path(plan.get("output_contract"))
            if contract_path is None:
                _fail("research_input_refused", "historical output contract reference is malformed")
            contract_data, _ = read_committed_artifact(
                workpad=writer.root, project_id=writer.project_id, gig_id=writer.gig_id,
                path=contract_path, head=bootstrap.head,
            )
            contract = parse_json_bytes(contract_data)
            if not isinstance(contract, Mapping):
                _fail("research_input_refused", "historical output contract is invalid")
            domains = contract.get("domains")
            research = domains.get("research") if isinstance(domains, Mapping) else None
            if not isinstance(research, Mapping):
                _fail("research_input_refused", "historical research domain binding is absent")
            for key in ("schema_ref", "validator_source_ref"):
                path = _ref_path(research.get(key))
                if path is None:
                    _fail("research_input_refused", "historical validator reference is malformed")
                exact.add(path)

            hydrated: dict[str, bytes] = {}
            for path in sorted(exact):
                data, _ = read_committed_artifact(
                    workpad=writer.root, project_id=writer.project_id, gig_id=writer.gig_id,
                    path=path, head=bootstrap.head,
                )
                hydrated[path] = data
            return JournalSnapshot(bootstrap.head, hydrated)

        if writer is not None:
            if writer.root != resolved.path or writer.project_id != resolved.project_id or writer.gig_id != resolved.gig_id:
                _fail("research_input_scope_mismatch", "provided journal writer belongs to another workpad")
            return operation(writer)
        return run_with_journal_writer(
            workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
            operation=operation,
        )
    except ResearchInputError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", "research_input_refused")
        raise ResearchInputError(code, "research snapshot hydration was refused") from exc


def resolve_research_input_from_journal(
    resolved: ResolvedWorkpad, raw: object, *, writer: JournalWriter | None = None
) -> dict[str, object]:
    """Hydrate and resolve using one standalone or caller-held writer lock."""
    snapshot = hydrate_research_input_snapshot(resolved, raw, writer=writer)
    return resolve_research_input(resolved, snapshot, raw)


__all__ = [
    "ResearchInputError", "hydrate_research_input_snapshot",
    "resolve_research_input", "resolve_research_input_from_journal",
    "revalidate_research_input",
]
