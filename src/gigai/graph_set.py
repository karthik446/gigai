"""Immutable multi-graph authority, selection, and reference validation.

This module deliberately has no projection dependency.  Callers give it the
bytes read from an approved journal commit; mutable ``state.sqlite`` is never
accepted as Graph Set authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import subprocess
from typing import Mapping

from .canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes, parse_json_front_matter
from .invocation import InvocationValidationError, load_invocation_bytes
from .validators import ValidationFinding, ValidationReport, validate_goal_graph, validate_serialized_contract
from .review import validate_review_contract


class GraphSetError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _report(findings: list[ValidationFinding]) -> ValidationReport:
    return ValidationReport(tuple(sorted(set(findings))))


def _ref_data(root: Path, ref: object, *, code: str) -> bytes:
    if not isinstance(ref, Mapping):
        raise GraphSetError(code, "artifact reference is malformed")
    path = ref.get("path")
    digest = ref.get("content_sha256")
    size = ref.get("size_bytes")
    if not isinstance(path, str) or not isinstance(digest, str) or type(size) is not int:
        raise GraphSetError(code, "artifact reference is malformed")
    relative = Path(path)
    if relative.is_absolute() or "\\" in path or ".." in relative.parts:
        raise GraphSetError(code, "artifact reference path is unsafe")
    candidate = root / relative
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise GraphSetError(code, "artifact reference path is redirected")
    if candidate.is_symlink() or not candidate.is_file():
        raise GraphSetError(code, "artifact reference is unavailable")
    data = candidate.read_bytes()
    if len(data) != size or digest_imported_bytes(data) != digest:
        raise GraphSetError(code, "artifact reference bytes changed")
    return data


def _budget_within(child: object, parent: object) -> bool:
    if not isinstance(child, Mapping) or not isinstance(parent, Mapping):
        return False
    for field in ("max_model_calls", "max_tool_calls", "max_tokens", "max_wall_time_ms", "max_parallel_goals"):
        if type(child.get(field)) is not int or type(parent.get(field)) is not int or child[field] > parent[field]:
            return False
    # ``None`` is an unbounded/unknown cost ceiling only when the parent is too.
    if parent.get("max_cost") is not None and child.get("max_cost") is None:
        return False
    if parent.get("max_cost") is not None:
        try:
            if Decimal(str(child.get("max_cost"))) > Decimal(str(parent.get("max_cost"))):
                return False
        except (InvalidOperation, ValueError):
            return False
    if parent.get("currency") != child.get("currency"):
        return False
    return True


_ATTACHED_CONTRACT_KINDS = {
    "input_contract": "run_input_contract",
    "output_contract": "run_output_contract",
    "permitted_reference_contract": "permitted_reference_contract",
    "evaluation_contract": "evaluation_contract",
    "completion_evidence_contract": "completion_evidence_contract",
}


def _domain_output_contract_valid(payload: Mapping[str, object], *, gig_id: object) -> bool:
    """Admit the closed v2 descriptor shape, not execution or source approval.

    The external recorder authenticates the nested journal artifacts and
    compares their bytes with its fixed supported validator before use.
    Proposal staging does not execute or import the referenced Python.
    """
    if (
        set(payload) != {"schema_version", "kind", "gig_id", "fields", "domains"}
        or payload.get("schema_version") != "2.0"
        or payload.get("kind") != "run_output_contract"
        or payload.get("gig_id") != gig_id
    ):
        return False
    fields, domains = payload.get("fields"), payload.get("domains")
    if (
        not isinstance(fields, list) or not 1 <= len(fields) <= 32
        or not all(isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item) for item in fields)
        or len(set(fields)) != len(fields)
        or not isinstance(domains, Mapping) or set(domains) != set(fields)
    ):
        return False
    for domain in domains.values():
        if (
            not isinstance(domain, Mapping)
            or set(domain) != {"schema_id", "schema_ref", "validator_id", "validator_source_ref"}
            or domain.get("schema_id") not in {
                "urn:gigai:scout:research-packet:2",
                "urn:gigai:scout:research-packet:3",
                "urn:gigai:scout:discovery-packet:2",
                "urn:gigai:scout:tailoring-packet:1",
            }
            or domain.get("validator_id") != {
                "urn:gigai:scout:research-packet:2": "scout-role-research:2",
                "urn:gigai:scout:research-packet:3": "scout-role-research:3",
                "urn:gigai:scout:discovery-packet:2": "scout-job-discovery:2",
                "urn:gigai:scout:tailoring-packet:1": "scout-application-tailoring:1",
            }.get(domain.get("schema_id"))
        ):
            return False
        suffixes = (
            ("research.schema.json", "research.py")
            if domain.get("schema_id") in {"urn:gigai:scout:research-packet:2", "urn:gigai:scout:research-packet:3"}
            else ("discovery.schema.json", "discovery.py")
            if domain.get("schema_id") == "urn:gigai:scout:discovery-packet:2"
            else ("tailoring.schema.json", "tailoring.py")
        )
        for key, media_type, suffix in (
            ("schema_ref", "application/json", suffixes[0]),
            ("validator_source_ref", "text/x-python", suffixes[1]),
        ):
            ref = domain.get(key)
            if not isinstance(ref, Mapping) or set(ref) != {"path", "content_sha256", "media_type", "size_bytes"}:
                return False
            path, digest, size = ref.get("path"), ref.get("content_sha256"), ref.get("size_bytes")
            if (
                not isinstance(path, str) or not 1 <= len(path) <= 4096
                or "\x00" in path or "\\" in path or Path(path).is_absolute()
                or Path(path).as_posix() != path or ".." in Path(path).parts
                or Path(path).name != suffix
                or not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
                or type(size) is not int or not 0 <= size <= 9007199254740991
                or ref.get("media_type") != media_type
            ):
                return False
    return True


def _attached_contract_valid(field: str, content: bytes, *, gig_id: object) -> bool:
    """Accept only the small, declared contract envelopes supported by A02.

    Graph Set attachments are authority inputs, not arbitrary JSON blobs.  The
    review attachment retains its existing strict G15 contract; the remaining
    A02 attachment kinds use a deliberately narrow envelope until their own
    versioned contract families exist.
    """
    if field == "review_contract":
        return validate_review_contract(content).valid
    expected_kind = _ATTACHED_CONTRACT_KINDS.get(field)
    if expected_kind is None:
        return False
    try:
        payload = parse_json_bytes(content)
    except Exception:
        return False
    if isinstance(payload, dict) and payload.get("schema_version") == "2.0":
        return field == "output_contract" and _domain_output_contract_valid(payload, gig_id=gig_id)
    return (
        isinstance(payload, dict)
        and payload.get("schema_version") == "1.0"
        and payload.get("kind") == expected_kind
        and payload.get("gig_id") == gig_id
        and isinstance(payload.get("fields"), list)
        and all(isinstance(value, str) and value for value in payload["fields"])
    )


def validate_graph_set(data: bytes, *, root: Path | None = None) -> ValidationReport:
    report = validate_serialized_contract("gig-graph-set.schema.json", data)
    if not report.valid:
        return report
    parsed = parse_json_bytes(data)
    assert isinstance(parsed, dict)
    findings: list[ValidationFinding] = []
    graphs = parsed.get("graphs")
    shared = parsed.get("shared_policy")
    if not isinstance(graphs, list) or not isinstance(shared, Mapping):
        return _report([ValidationFinding("$", "graph_set_invalid", "Graph Set is malformed")])
    selectors: set[str] = set()
    graph_digests: set[str] = set()
    for index, descriptor in enumerate(graphs):
        location = f"graphs/{index}"
        if not isinstance(descriptor, Mapping):
            continue
        graph_id = descriptor.get("graph_id")
        aliases = descriptor.get("aliases")
        if not isinstance(graph_id, str) or graph_id in selectors:
            findings.append(ValidationFinding(location + "/graph_id", "graph_selector_duplicate", "Graph selectors must be unique"))
        elif graph_id is not None:
            selectors.add(graph_id)
        if isinstance(aliases, list):
            for alias in aliases:
                if not isinstance(alias, str) or alias in selectors:
                    findings.append(ValidationFinding(location + "/aliases", "graph_alias_collision", "Graph aliases cannot collide"))
                else:
                    selectors.add(alias)
        graph_ref = descriptor.get("goal_graph")
        if isinstance(graph_ref, Mapping):
            digest = graph_ref.get("content_sha256")
            if not isinstance(digest, str) or digest in graph_digests:
                findings.append(ValidationFinding(location + "/goal_graph", "graph_digest_duplicate", "Each descriptor must name distinct Goal Graph bytes"))
            else:
                graph_digests.add(digest)
        if not set(descriptor.get("effect_policy", ())).issubset(set(shared.get("effects", ()))):
            findings.append(ValidationFinding(location + "/effect_policy", "graph_policy_widened", "Graph effects exceed the approved Gig ceiling"))
        if not set(descriptor.get("capability_requirements", ())).issubset(set(shared.get("required_capability_ids", ()))):
            findings.append(ValidationFinding(location + "/capability_requirements", "graph_policy_widened", "Graph capabilities exceed the approved Gig ceiling"))
        provider = descriptor.get("provider_eligibility")
        shared_provider = shared.get("provider_eligibility")
        if not isinstance(provider, Mapping) or not isinstance(shared_provider, Mapping) or not set(provider.get("providers", ())).issubset(set(shared_provider.get("providers", ()))):
            findings.append(ValidationFinding(location + "/provider_eligibility", "graph_policy_widened", "Graph provider eligibility exceeds the approved Gig ceiling"))
        if not _budget_within(descriptor.get("budget"), shared.get("budget")):
            findings.append(ValidationFinding(location + "/budget", "graph_policy_widened", "Graph budget exceeds the approved Gig ceiling"))
        if root is not None:
            for field in ("goal_graph", "input_contract", "output_contract", "permitted_reference_contract", "review_contract", "evaluation_contract", "completion_evidence_contract"):
                try:
                    content = _ref_data(root, descriptor.get(field), code="graph_reference_invalid")
                except GraphSetError as exc:
                    findings.append(ValidationFinding(location + "/" + field, exc.code, str(exc)))
                    continue
                if field == "goal_graph":
                    schema = validate_serialized_contract("goal-graph.schema.json", content)
                    if not schema.valid:
                        findings.append(ValidationFinding(location + "/goal_graph", "graph_reference_invalid", "Referenced Goal Graph is invalid"))
                    else:
                        graph = parse_json_bytes(content)
                        if not isinstance(graph, dict) or graph.get("gig_id") != parsed.get("gig_id") or not validate_goal_graph(graph).valid:
                            findings.append(ValidationFinding(location + "/goal_graph", "graph_reference_invalid", "Referenced Goal Graph does not belong to this valid Graph Set"))
                        elif (
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
                            findings.append(ValidationFinding(location + "/goal_graph", "graph_policy_widened", "Actual Goal Graph exceeds its descriptor ceiling"))
                        elif isinstance(graph, Mapping):
                            for goal in graph.get("goals", ()):
                                if not isinstance(goal, Mapping):
                                    continue
                                try:
                                    _ref_data(root, goal.get("contract"), code="graph_reference_invalid")
                                except GraphSetError as exc:
                                    findings.append(ValidationFinding(location + "/goal_graph", exc.code, "Goal Graph contract is unavailable"))
                elif not _attached_contract_valid(field, content, gig_id=parsed.get("gig_id")):
                    findings.append(ValidationFinding(location + "/" + field, "graph_contract_unsupported", "Graph Set attachment has an unsupported or invalid contract form"))
    return _report(findings)


def graph_set_descriptor(graph_set: Mapping[str, object], selector: str) -> dict[str, object] | None:
    for value in graph_set.get("graphs", ()):
        if not isinstance(value, dict):
            continue
        if value.get("graph_id") == selector or selector in value.get("aliases", ()):
            return value
    return None


def selection_schema_name(record: Mapping[str, object]) -> str:
    return "graph-selection-record-v2.schema.json" if record.get("selection_kind") == "agent_explicit" else "graph-selection-record.schema.json"


def validate_selection_record(
    data: bytes,
    *,
    graph_set: Mapping[str, object],
    gig_id: str,
    gig_version: int,
    root: Path | None = None,
) -> ValidationReport:
    parsed = parse_json_bytes(data)
    if not isinstance(parsed, dict):
        return _report([ValidationFinding("$", "graph_selection_invalid", "selection record is not an object")])
    report = validate_serialized_contract(selection_schema_name(parsed), data)
    if not report.valid:
        return report
    findings: list[ValidationFinding] = []
    graph_set_ref = parsed.get("graph_set")
    if not isinstance(graph_set_ref, Mapping) or graph_set_ref.get("content_sha256") != digest_imported_bytes(canonical_json_bytes(graph_set)):
        findings.append(ValidationFinding("graph_set", "graph_selection_mismatch", "selection does not pin the approved Graph Set"))
    if parsed.get("gig_id") != gig_id or parsed.get("gig_version") != gig_version:
        findings.append(ValidationFinding("gig_version", "graph_selection_mismatch", "selection belongs to another Gig version"))
    selected = next((item for item in graph_set.get("graphs", ()) if isinstance(item, dict) and item.get("graph_id") == parsed.get("selected_graph_id")), None)
    if selected is None or parsed.get("selected_graph") != selected.get("goal_graph"):
        findings.append(ValidationFinding("selected_graph", "graph_selection_mismatch", "selection does not match an approved descriptor"))
    kind = parsed.get("selection_kind")
    selector = parsed.get("selector")
    evidence = parsed.get("routing_evidence_refs")
    if not isinstance(selector, Mapping) or not isinstance(evidence, list):
        findings.append(ValidationFinding("selector", "graph_selection_invalid", "selection provenance is malformed"))
    elif kind == "operator_explicit" and (selector.get("kind") != "operator" or not isinstance(selector.get("actor"), Mapping) or selector["actor"].get("kind") != "operator" or selector.get("rule_id") is not None or selector.get("rule_version") is not None or evidence):
        findings.append(ValidationFinding("selector", "graph_selection_invalid", "operator selection must carry only an operator selector"))
    elif kind == "only_member_default" and (len(graph_set.get("graphs", ())) != 1 or selector.get("kind") != "gigai_deterministic" or not isinstance(selector.get("actor"), Mapping) or selector["actor"].get("kind") != "gigai" or selector.get("rule_id") is not None or selector.get("rule_version") is not None or evidence):
        findings.append(ValidationFinding("selector", "graph_selection_invalid", "only-member selection is not applicable"))
    elif kind == "g44_routed" and (selector.get("kind") != "g44_router" or not selector.get("rule_id") or not selector.get("rule_version") or not evidence):
        findings.append(ValidationFinding("selector", "graph_selection_invalid", "routed selection requires sealed G44 evidence"))
    elif kind == "agent_explicit":
        invocation_ref = selector.get("invocation_ref")
        if (
            selector.get("kind") != "agent"
            or not isinstance(selector.get("actor"), Mapping)
            or selector["actor"].get("kind") != "agent"
            or not isinstance(invocation_ref, Mapping)
            or evidence
        ):
            findings.append(ValidationFinding("selector", "graph_selection_invalid", "agent selection requires an agent actor and exact invocation"))
        elif root is not None:
            findings.extend(_agent_invocation_findings(root, invocation_ref, selector["actor"]))
    return _report(findings)


def _agent_invocation_findings(
    root: Path, invocation_ref: Mapping[str, object], actor: Mapping[str, object]
) -> list[ValidationFinding]:
    """Authenticate agent selection provenance at the acceptance boundary.

    The v2 schema records the reference shape.  A Plan/Run acceptance also
    requires the exact referenced invocation bytes to be a committed journal
    artifact with the same agent identity.  This deliberately does not turn an
    agent invocation into operator consent.
    """

    path = invocation_ref.get("path")
    digest = invocation_ref.get("content_sha256")
    size = invocation_ref.get("size_bytes")
    if (
        not isinstance(path, str)
        or not isinstance(digest, str)
        or type(size) is not int
        or Path(path).is_absolute()
        or "\\" in path
        or ".." in Path(path).parts
        or len(Path(path).parts) != 2
        or Path(path).parts[0] != "agent-invocations"
        or not path.endswith(".json")
    ):
        return [ValidationFinding("selector/invocation_ref", "agent_invocation_invalid", "agent invocation reference path is invalid")]
    candidate = root / path
    try:
        _reject_symlink_components(root, candidate)
        payload = candidate.read_bytes()
    except OSError:
        return [ValidationFinding("selector/invocation_ref", "agent_invocation_missing", "agent invocation evidence is unavailable")]
    if candidate.is_symlink() or not candidate.is_file() or len(payload) != size or digest_imported_bytes(payload) != digest:
        return [ValidationFinding("selector/invocation_ref", "agent_invocation_mismatch", "agent invocation bytes differ from the sealed reference")]
    try:
        invocation = load_invocation_bytes(payload)
    except InvocationValidationError:
        return [ValidationFinding("selector/invocation_ref", "agent_invocation_invalid", "agent invocation evidence is not a valid bounded envelope")]
    expected_name = f"{invocation.invocation_id}.json"
    if Path(path).name != expected_name:
        return [ValidationFinding("selector/invocation_ref", "agent_invocation_mismatch", "agent invocation reference does not name its invocation identity")]
    if actor.get("id") != invocation.actor.get("id") or (
        actor.get("session_id") is not None and actor.get("session_id") != invocation.actor.get("session_id")
    ):
        return [ValidationFinding("selector/actor", "agent_invocation_actor_mismatch", "agent selection actor does not match the sealed invocation")]
    if not _journaled_agent_invocation(root, path, payload, invocation.actor):
        return [ValidationFinding("selector/invocation_ref", "agent_invocation_unjournaled", "agent invocation evidence is not authenticated by a matching journal handoff")]
    return []


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise OSError("agent invocation path escapes workpad") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise OSError("agent invocation path is redirected")


def _journaled_agent_invocation(root: Path, relative: str, payload: bytes, actor: Mapping[str, str]) -> bool:
    """Use journal commits, rather than a path prefix, as provenance authority."""

    commits = subprocess.run(
        ["git", "-C", os.fspath(root), "log", "--format=%H", "--", relative],
        capture_output=True, text=True, check=False, shell=False,
    )
    if commits.returncode != 0:
        return False
    for commit in commits.stdout.splitlines():
        shown = subprocess.run(
            ["git", "-C", os.fspath(root), "show", f"{commit}:{relative}"],
            capture_output=True, check=False, shell=False,
        )
        if shown.returncode != 0 or shown.stdout != payload:
            continue
        names = subprocess.run(
            ["git", "-C", os.fspath(root), "show", "--format=", "--name-only", commit],
            capture_output=True, text=True, check=False, shell=False,
        )
        for handoff in (name for name in names.stdout.splitlines() if name.startswith("handoffs/") and name.endswith(".txt")):
            handoff_bytes = subprocess.run(
                ["git", "-C", os.fspath(root), "show", f"{commit}:{handoff}"],
                capture_output=True, check=False, shell=False,
            )
            if handoff_bytes.returncode != 0:
                continue
            try:
                metadata, _body = parse_json_front_matter(handoff_bytes.stdout)
            except ValueError:
                continue
            journal_actor = metadata.get("actor")
            if isinstance(journal_actor, Mapping) and journal_actor.get("kind") == "agent" and journal_actor.get("id") == actor.get("id"):
                return True
    return False


@dataclass(frozen=True)
class SelectedGraph:
    graph_set: dict[str, object]
    descriptor: dict[str, object]
    selection: dict[str, object]
