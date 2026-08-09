"""Deterministic, offline validators for proposed Gig workpads and Goal Graphs."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .canonical import (
    CanonicalizationError,
    EntityPrefix,
    canonical_json_bytes,
    canonicalize_owned_text,
    digest_imported_bytes,
    parse_json_bytes,
    validate_entity_id,
)


SCHEMA_NAMES = (
    "addressed-artifact.schema.json",
    "adjudication.schema.json",
    "active-gig-version.schema.json",
    "capability-installation.schema.json",
    "capability-manifest.schema.json",
    "common.schema.json",
    "feedback.schema.json",
    "finding.schema.json",
    "gig-proposal.schema.json",
    "goal-graph.schema.json",
    "handoff-frontmatter.schema.json",
    "model-exchange.schema.json",
    "model-invocation.schema.json",
    "report.schema.json",
    "review-bundle.schema.json",
    "review-contract.schema.json",
    "run-brief-frontmatter.schema.json",
    "run-details.schema.json",
    "run-manifest.schema.json",
    "review-loop.schema.json",
    "trace.schema.json",
)
_WRITING_EFFECTS = frozenset({"write_target", "write_workpad", "external_write"})
_PROPOSAL_PATHS = {
    "gig_document": ("gig.md", "text/markdown"),
    "goal_graph": ("manifests/goal-graph.json", "application/json"),
    "creation_manifest": ("manifests/creation-manifest.json", "application/json"),
}
_REQUIRED_MARKDOWN = (
    "gig.md",
    "goals/README.md",
    "reviews/creation-review.md",
    "decisions/creation-decisions.md",
)


@dataclass(frozen=True, order=True)
class ValidationFinding:
    """A stable, share-safe diagnostic without a host filesystem path."""

    location: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "location": self.location, "message": self.message}


@dataclass(frozen=True)
class ValidationReport:
    findings: tuple[ValidationFinding, ...]

    @property
    def valid(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {
            "findings": [item.as_dict() for item in self.findings],
            "valid": self.valid,
        }


def _report(findings: Iterable[ValidationFinding]) -> ValidationReport:
    return ValidationReport(tuple(sorted(set(findings))))


def _schema_registry() -> tuple[dict[str, dict[str, Any]], Registry]:
    root = resources.files("gigai.schemas")
    schemas: dict[str, dict[str, Any]] = {}
    registry = Registry()
    for name in SCHEMA_NAMES:
        contents = json.loads(root.joinpath(name).read_text(encoding="utf-8"))
        schemas[name] = contents
        registry = registry.with_resource(
            contents["$id"], Resource.from_contents(contents)
        )
    return schemas, registry


def validate_serialized_contract(schema_name: str, data: bytes) -> ValidationReport:
    """Validate exact canonical JSON bytes against one packaged schema resource."""

    if schema_name not in SCHEMA_NAMES:
        return _report(
            (ValidationFinding("$", "unknown_schema", "schema is not packaged"),)
        )
    try:
        instance = parse_json_bytes(data)
    except CanonicalizationError as exc:
        return _report((ValidationFinding("$", "invalid_json", str(exc)),))
    schemas, registry = _schema_registry()
    validator = Draft202012Validator(
        schemas[schema_name], registry=registry, format_checker=FormatChecker()
    )
    findings = []
    for error in sorted(
        validator.iter_errors(instance), key=lambda item: list(item.absolute_path)
    ):
        pointer = "$" + "".join(f"/{_pointer(part)}" for part in error.absolute_path)
        findings.append(ValidationFinding(pointer, "schema_invalid", error.message))
    return _report(findings)


def validate_model_invocation(data: Mapping[str, Any] | bytes) -> ValidationReport:
    """Validate a model invocation schema and its terminal/boundary semantics."""

    payload = data if isinstance(data, bytes) else canonical_json_bytes(data)
    report = validate_serialized_contract("model-invocation.schema.json", payload)
    if not report.valid:
        return report
    instance = parse_json_bytes(payload)
    findings: list[ValidationFinding] = []
    expected_finish = {
        "succeeded": "completed",
        "partial": "partial",
        "failed": "failed",
        "cancelled": "cancelled",
        "timeout": "timeout",
        "unavailable": "unavailable",
        "blocked": "blocked",
    }[instance["outcome"]]
    if instance["finish"] != expected_finish:
        findings.append(
            ValidationFinding(
                "finish",
                "terminal_finish_mismatch",
                "finish must match the terminal outcome",
            )
        )
    boundary = instance["boundary"]
    if boundary["redaction"]["result"] == "failed" and instance["outcome"] != "blocked":
        findings.append(
            ValidationFinding(
                "boundary/redaction/result",
                "redaction_failure_not_blocked",
                "a failed redaction check must produce a blocked invocation",
            )
        )
    if boundary["network"]["result"] == "denied" and instance["outcome"] != "blocked":
        findings.append(
            ValidationFinding(
                "boundary/network/result",
                "network_denial_not_blocked",
                "a denied network check must produce a blocked invocation",
            )
        )
    return _report(findings)


def validate_model_exchange(data: Mapping[str, Any] | bytes) -> ValidationReport:
    """Validate a model handoff/comparison record and cross-field limits."""

    payload = data if isinstance(data, bytes) else canonical_json_bytes(data)
    report = validate_serialized_contract("model-exchange.schema.json", payload)
    if not report.valid:
        return report
    instance = parse_json_bytes(payload)
    findings: list[ValidationFinding] = []
    if instance["kind"] == "handoff":
        handoff = instance["handoff"]
        if handoff is None:
            findings.append(
                ValidationFinding("handoff", "handoff_missing", "handoff records require handoff details")
            )
        elif handoff["index"] > handoff["cap"]:
            findings.append(
                ValidationFinding(
                    "handoff/index",
                    "handoff_limit_exhausted",
                    "handoff index cannot exceed the fixed edge cap",
                )
            )
        if instance["status"] != "received" and instance["status"] not in {"cancelled", "unavailable", "blocked"}:
            findings.append(
                ValidationFinding(
                    "status",
                    "handoff_status_mismatch",
                    "handoff records must report received or a terminal blocked outcome",
                )
            )
    if instance["kind"] == "comparison":
        comparison = instance["comparison"]
        if comparison is None:
            findings.append(
                ValidationFinding("comparison", "comparison_missing", "comparison records require comparison details")
            )
        else:
            invocation_ids = [item["invocation_id"] for item in comparison["independent_artifacts"]]
            if len(set(invocation_ids)) != len(invocation_ids):
                findings.append(
                    ValidationFinding(
                        "comparison/independent_artifacts",
                        "comparison_not_independent",
                        "comparison artifacts must come from distinct invocations",
                    )
                )
            if instance["status"] == "disagreement":
                if not comparison["requires_human_adjudication"]:
                    findings.append(
                        ValidationFinding(
                            "comparison/requires_human_adjudication",
                            "disagreement_without_adjudication",
                            "disagreement requires human adjudication",
                        )
                    )
                if comparison["adjudication_input"] is None:
                    findings.append(
                        ValidationFinding(
                            "comparison/adjudication_input",
                            "disagreement_without_input",
                            "disagreement requires an adjudication input artifact",
                        )
                    )
        if instance["status"] not in {"agreement", "disagreement"}:
            findings.append(
                ValidationFinding(
                    "status",
                    "comparison_status_mismatch",
                    "comparison records must report agreement or disagreement",
                )
            )
    return _report(findings)


def validate_goal_graph(data: Mapping[str, Any]) -> ValidationReport:
    """Validate the semantic rules that JSON Schema cannot express."""

    findings: list[ValidationFinding] = []
    goals = list(data.get("goals", []))
    edges = list(data.get("edges", []))
    by_id: dict[str, Mapping[str, Any]] = {}
    seen_ordinals: set[str] = set()
    seen_slugs: set[str] = set()
    for index, goal in enumerate(goals):
        location = f"goals/{index}"
        goal_id = goal.get("goal_id")
        if not isinstance(goal_id, str):
            findings.append(
                ValidationFinding(
                    location, "invalid_goal_id", "goal_id must be a string"
                )
            )
            continue
        try:
            validate_entity_id(goal_id, expected_prefix=EntityPrefix.GOAL)
        except CanonicalizationError:
            findings.append(
                ValidationFinding(
                    location + "/goal_id",
                    "noncanonical_goal_id",
                    "goal_id is not canonical",
                )
            )
        if goal_id in by_id:
            findings.append(
                ValidationFinding(
                    location + "/goal_id",
                    "duplicate_goal_id",
                    "goal_id occurs more than once",
                )
            )
        else:
            by_id[goal_id] = goal
        for field, seen, code in (
            ("display_ordinal", seen_ordinals, "duplicate_display_ordinal"),
            ("slug", seen_slugs, "duplicate_slug"),
        ):
            value = goal.get(field)
            if isinstance(value, str):
                if value in seen:
                    findings.append(
                        ValidationFinding(
                            location + "/" + field,
                            code,
                            f"{field} occurs more than once",
                        )
                    )
                seen.add(value)
        if (
            not isinstance(goal.get("goal_version"), int)
            or goal.get("goal_version", 0) < 1
        ):
            findings.append(
                ValidationFinding(
                    location + "/goal_version",
                    "invalid_goal_version",
                    "goal_version must be a positive integer",
                )
            )
        if set(goal.get("effects", ())) & _WRITING_EFFECTS and not goal.get(
            "write_surfaces"
        ):
            findings.append(
                ValidationFinding(
                    location + "/write_surfaces",
                    "missing_write_surface",
                    "a writing Goal must declare a nonempty write surface",
                )
            )
        if not (set(goal.get("effects", ())) & _WRITING_EFFECTS) and goal.get(
            "write_surfaces"
        ):
            findings.append(
                ValidationFinding(
                    location + "/write_surfaces",
                    "unexpected_write_surface",
                    "a non-writing Goal must not declare a write surface",
                )
            )

    known = set(by_id)
    adjacency: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)
    edge_ids: set[str] = set()
    for index, edge in enumerate(edges):
        location = f"edges/{index}"
        edge_id = edge.get("edge_id")
        source = edge.get("from_goal_id")
        target = edge.get("to_goal_id")
        if edge_id in edge_ids:
            findings.append(
                ValidationFinding(
                    location + "/edge_id",
                    "duplicate_edge_id",
                    "edge_id occurs more than once",
                )
            )
        edge_ids.add(edge_id)
        if source not in known or target not in known:
            findings.append(
                ValidationFinding(
                    location,
                    "unknown_edge_endpoint",
                    "edge endpoint does not name a Goal",
                )
            )
            continue
        outcomes = set(by_id[source].get("outcomes", ()))
        edge_outcomes = set(edge.get("on_outcomes", ()))
        if edge.get("automatic") and not edge_outcomes:
            findings.append(
                ValidationFinding(
                    location + "/on_outcomes",
                    "missing_automatic_outcome",
                    "an automatic edge must name at least one source outcome",
                )
            )
        if not edge_outcomes.issubset(outcomes):
            findings.append(
                ValidationFinding(
                    location + "/on_outcomes",
                    "undeclared_outcome",
                    "edge names an outcome its source cannot produce",
                )
            )
        adjacency[source].add(target)
        reverse[target].add(source)

    for index, goal in enumerate(goals):
        if not isinstance(goal, Mapping):
            continue
        location = f"goals/{index}"
        _validate_resolution(
            goal.get("executor"),
            location + "/executor",
            known,
            adjacency,
            goal.get("goal_id"),
            findings,
        )
        for tool_index, tool in enumerate(goal.get("tools", [])):
            _validate_resolution(
                tool,
                f"{location}/tools/{tool_index}",
                known,
                adjacency,
                goal.get("goal_id"),
                findings,
            )

    _validate_reachability(data, known, adjacency, reverse, findings)
    _validate_budgets(data, goals, findings)
    _validate_parallelism(goals, adjacency, findings)
    _validate_terminal_evidence(data, known, adjacency, findings)
    return _report(findings)


def validate_proposal_workpad(root: Path) -> ValidationReport:
    """Validate a non-executable proposal tree without mutating it or using a network."""

    root = Path(root)
    findings: list[ValidationFinding] = []
    if not root.is_dir() or root.is_symlink():
        return _report(
            (
                ValidationFinding(
                    ".", "invalid_workpad", "workpad must be a real directory"
                ),
            )
        )
    for relative in (
        *_REQUIRED_MARKDOWN,
        "manifests/gig-proposal.json",
        "manifests/goal-graph.json",
        "manifests/creation-manifest.json",
    ):
        data = _read(root, relative, findings)
        if data is not None and relative.endswith(".md"):
            _validate_owned_markdown(relative, data, findings)
    proposal_bytes = _read(root, "manifests/gig-proposal.json", findings)
    graph_bytes = _read(root, "manifests/goal-graph.json", findings)
    proposal: Any = None
    graph: Any = None
    if proposal_bytes is not None:
        findings.extend(
            validate_serialized_contract(
                "gig-proposal.schema.json", proposal_bytes
            ).findings
        )
        proposal = _parse("manifests/gig-proposal.json", proposal_bytes, findings)
    if graph_bytes is not None:
        findings.extend(
            validate_serialized_contract("goal-graph.schema.json", graph_bytes).findings
        )
        graph = _parse("manifests/goal-graph.json", graph_bytes, findings)
        if isinstance(graph, Mapping):
            findings.extend(validate_goal_graph(graph).findings)
    if isinstance(proposal, Mapping):
        if proposal.get("status") not in {"drafting", "proposed"}:
            findings.append(
                ValidationFinding(
                    "manifests/gig-proposal.json/status",
                    "proposal_not_pending",
                    "proposal must be drafting or proposed before approval",
                )
            )
        for field, (relative, media_type) in _PROPOSAL_PATHS.items():
            _validate_artifact_ref(
                proposal.get(field), field, relative, media_type, root, findings
            )
    if (
        isinstance(proposal, Mapping)
        and isinstance(graph, Mapping)
        and proposal.get("gig_id") != graph.get("gig_id")
    ):
        findings.append(
            ValidationFinding(
                "manifests/gig-proposal.json/gig_id",
                "gig_id_mismatch",
                "proposal and graph must name the same Gig",
            )
        )
    if isinstance(graph, Mapping):
        _validate_goal_markdown(root, graph, findings)
    return _report(findings)


def _validate_resolution(
    value: Any,
    location: str,
    known: set[str],
    adjacency: Mapping[str, set[str]],
    consumer: Any,
    findings: list[ValidationFinding],
) -> None:
    if not isinstance(value, Mapping):
        return
    resolution = value.get("resolution")
    producer = value.get("materialized_by")
    reason = value.get("blocking_reason")
    if resolution == "materialized":
        if (
            producer not in known
            or not isinstance(consumer, str)
            or consumer not in _reachable({producer}, adjacency)
        ):
            findings.append(
                ValidationFinding(
                    location + "/materialized_by",
                    "unknown_materializer",
                    "materialized reference must name an earlier Goal",
                )
            )
        if reason is not None:
            findings.append(
                ValidationFinding(
                    location + "/blocking_reason",
                    "materialized_reason",
                    "materialized resolution must not have a blocking reason",
                )
            )
    elif resolution == "blocking":
        if producer is not None or not isinstance(reason, str) or not reason.strip():
            findings.append(
                ValidationFinding(
                    location,
                    "invalid_blocking_resolution",
                    "blocking resolution requires a reason and no materializer",
                )
            )
    elif resolution == "installed":
        if producer is not None or reason is not None:
            findings.append(
                ValidationFinding(
                    location,
                    "invalid_installed_resolution",
                    "installed resolution must not name a materializer or reason",
                )
            )


def _validate_reachability(
    data: Mapping[str, Any],
    known: set[str],
    adjacency: Mapping[str, set[str]],
    reverse: Mapping[str, set[str]],
    findings: list[ValidationFinding],
) -> None:
    entries, terminals = (
        set(data.get("entry_goal_ids", ())),
        set(data.get("terminal_goal_ids", ())),
    )
    for field, values in (
        ("entry_goal_ids", entries),
        ("terminal_goal_ids", terminals),
    ):
        for value in values - known:
            findings.append(
                ValidationFinding(
                    field, "unknown_goal_reference", f"{value} does not name a Goal"
                )
            )
    if not entries:
        findings.append(
            ValidationFinding(
                "entry_goal_ids",
                "missing_entry_path",
                "graph must declare an entry Goal",
            )
        )
    if not terminals:
        findings.append(
            ValidationFinding(
                "terminal_goal_ids",
                "missing_terminal_path",
                "graph must declare a terminal Goal",
            )
        )
    reachable = _reachable(entries & known, adjacency)
    for goal in data.get("goals", ()):
        goal_id = goal.get("goal_id")
        if goal.get("required") and goal_id not in reachable:
            findings.append(
                ValidationFinding(
                    "goals",
                    "unreachable_required_goal",
                    "a required Goal is unreachable from every entry",
                )
            )
    if terminals and not (terminals & known) <= reachable:
        findings.append(
            ValidationFinding(
                "terminal_goal_ids",
                "unreachable_terminal",
                "a terminal Goal is unreachable",
            )
        )
    if _has_cycle(known, adjacency):
        findings.append(
            ValidationFinding(
                "edges", "graph_cycle", "dependency or recovery edges form a cycle"
            )
        )
    for terminal in terminals & known:
        if adjacency.get(terminal):
            findings.append(
                ValidationFinding(
                    "terminal_goal_ids",
                    "nonterminal_outgoing_edge",
                    "a terminal Goal must not have an outgoing edge",
                )
            )
    incoming_dependencies: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for edge in data.get("edges", ()):
        if isinstance(edge, Mapping) and edge.get("kind") == "dependency":
            target = edge.get("to_goal_id")
            if isinstance(target, str):
                incoming_dependencies[target].append(edge)
    for goal_id, predecessors in incoming_dependencies.items():
        if len(predecessors) < 2:
            continue
        if goal_id in entries:
            findings.append(
                ValidationFinding(
                    "entry_goal_ids",
                    "join_entry",
                    "a join Goal cannot be an entry Goal",
                )
            )
        sources = [edge.get("from_goal_id") for edge in predecessors]
        if len(sources) != len(set(sources)):
            findings.append(
                ValidationFinding(
                    "edges",
                    "invalid_join_predecessors",
                    "a join must name each predecessor Goal exactly once",
                )
            )


def _validate_budgets(
    data: Mapping[str, Any], goals: list[Any], findings: list[ValidationFinding]
) -> None:
    aggregate = data.get("aggregate_budget")
    if not isinstance(aggregate, Mapping):
        return
    for field in (
        "max_model_calls",
        "max_tool_calls",
        "max_tokens",
        "max_wall_time_ms",
    ):
        total = sum(
            item.get("budget", {}).get(field, 0)
            for item in goals
            if isinstance(item, Mapping)
        )
        if isinstance(aggregate.get(field), int) and total > aggregate[field]:
            findings.append(
                ValidationFinding(
                    "aggregate_budget/" + field,
                    "impossible_budget",
                    "sum of per-Goal budgets exceeds aggregate budget",
                )
            )
    costs = [
        item.get("budget", {}).get("max_cost")
        for item in goals
        if isinstance(item, Mapping)
    ]
    if aggregate.get("max_cost") is None and any(cost is not None for cost in costs):
        findings.append(
            ValidationFinding(
                "aggregate_budget/max_cost",
                "impossible_budget",
                "aggregate cost must be set when a Goal declares a cost",
            )
        )
    elif aggregate.get("max_cost") is not None:
        try:
            if sum(
                (Decimal(cost) for cost in costs if cost is not None), Decimal()
            ) > Decimal(aggregate["max_cost"]):
                findings.append(
                    ValidationFinding(
                        "aggregate_budget/max_cost",
                        "impossible_budget",
                        "sum of per-Goal costs exceeds aggregate budget",
                    )
                )
        except (InvalidOperation, TypeError):
            findings.append(
                ValidationFinding(
                    "aggregate_budget/max_cost",
                    "invalid_budget",
                    "cost is not a decimal value",
                )
            )


def _validate_parallelism(
    goals: list[Any],
    adjacency: Mapping[str, set[str]],
    findings: list[ValidationFinding],
) -> None:
    for left_index, left in enumerate(goals):
        if not isinstance(left, Mapping):
            continue
        for right in goals[left_index + 1 :]:
            if not isinstance(right, Mapping):
                continue
            left_id, right_id = left.get("goal_id"), right.get("goal_id")
            if (
                not isinstance(left_id, str)
                or not isinstance(right_id, str)
                or right_id in _reachable({left_id}, adjacency)
                or left_id in _reachable({right_id}, adjacency)
            ):
                continue
            overlap = set(left.get("write_surfaces", ())) & set(
                right.get("write_surfaces", ())
            )
            shared_lock = set(left.get("exclusive_resources", ())) & set(
                right.get("exclusive_resources", ())
            )
            if overlap and not shared_lock:
                findings.append(
                    ValidationFinding(
                        "goals",
                        "incompatible_parallel_surfaces",
                        "independent Goals overlap a write surface without common exclusive resource",
                    )
                )


def _validate_terminal_evidence(
    data: Mapping[str, Any],
    known: set[str],
    adjacency: Mapping[str, set[str]],
    findings: list[ValidationFinding],
) -> None:
    required = set(data.get("required_completion_evidence", ()))
    goals = {
        goal.get("goal_id"): goal
        for goal in data.get("goals", ())
        if isinstance(goal, Mapping)
    }
    for goal_id in set(data.get("terminal_goal_ids", ())) & known:
        evidence = set(
            goals[goal_id].get("verification", {}).get("required_evidence", ())
        )
        if not required <= evidence:
            findings.append(
                ValidationFinding(
                    "terminal_goal_ids",
                    "incomplete_terminal_evidence",
                    "terminal Goal does not require all Gig completion evidence",
                )
            )


def _read(root: Path, relative: str, findings: list[ValidationFinding]) -> bytes | None:
    path = root / relative
    if path.is_symlink() or not path.is_file():
        findings.append(
            ValidationFinding(
                relative,
                "missing_required_artifact",
                "required regular file is missing",
            )
        )
        return None
    return path.read_bytes()


def _parse(location: str, data: bytes, findings: list[ValidationFinding]) -> Any:
    try:
        return parse_json_bytes(data)
    except CanonicalizationError:
        findings.append(
            ValidationFinding(
                location, "invalid_json", "artifact is not canonical JSON"
            )
        )
        return None


def _validate_owned_markdown(
    relative: str, data: bytes, findings: list[ValidationFinding]
) -> None:
    try:
        text = data.decode("utf-8")
        if canonicalize_owned_text(text) != data:
            raise ValueError
    except (UnicodeDecodeError, CanonicalizationError, ValueError):
        findings.append(
            ValidationFinding(
                relative,
                "noncanonical_markdown",
                "Markdown must be UTF-8, LF-normalized, and end in one newline",
            )
        )


def _validate_artifact_ref(
    value: Any,
    field: str,
    expected_path: str,
    media_type: str,
    root: Path,
    findings: list[ValidationFinding],
) -> None:
    location = "manifests/gig-proposal.json/" + field
    if not isinstance(value, Mapping):
        return
    if value.get("path") != expected_path or value.get("media_type") != media_type:
        findings.append(
            ValidationFinding(
                location,
                "artifact_ref_mismatch",
                "proposal artifact reference names the wrong path or media type",
            )
        )
        return
    data = _read(root, expected_path, findings)
    if data is not None and (
        value.get("content_sha256") != digest_imported_bytes(data)
        or value.get("size_bytes") != len(data)
    ):
        findings.append(
            ValidationFinding(
                location,
                "artifact_digest_mismatch",
                "proposal artifact digest or size does not match exact bytes",
            )
        )


def _validate_goal_markdown(
    root: Path, graph: Mapping[str, Any], findings: list[ValidationFinding]
) -> None:
    expected: set[str] = set()
    for goal in graph.get("goals", ()):
        if not isinstance(goal, Mapping):
            continue
        ordinal, slug = goal.get("display_ordinal"), goal.get("slug")
        if not isinstance(ordinal, str) or not isinstance(slug, str):
            continue
        relative = f"goals/{int(ordinal[1:]):02d}-{slug}.md"
        expected.add(relative)
        data = _read(root, relative, findings)
        if data is not None:
            _validate_owned_markdown(relative, data, findings)
            ref = goal.get("contract")
            if (
                not isinstance(ref, Mapping)
                or ref.get("path") != relative
                or ref.get("media_type") != "text/markdown"
                or ref.get("content_sha256") != digest_imported_bytes(data)
                or ref.get("size_bytes") != len(data)
            ):
                findings.append(
                    ValidationFinding(
                        relative,
                        "goal_contract_mismatch",
                        "Goal contract reference does not match its Markdown bytes",
                    )
                )
    actual = (
        {"goals/" + path.name for path in (root / "goals").glob("*.md")}
        if (root / "goals").is_dir()
        else set()
    )
    extras = actual - expected - {"goals/README.md"}
    for relative in sorted(extras):
        findings.append(
            ValidationFinding(
                relative,
                "unreferenced_goal_markdown",
                "Goal Markdown is not represented by the graph",
            )
        )


def _reachable(starts: set[str], adjacency: Mapping[str, set[str]]) -> set[str]:
    reached = set(starts)
    queue = deque(starts)
    while queue:
        for target in adjacency.get(queue.popleft(), set()):
            if target not in reached:
                reached.add(target)
                queue.append(target)
    return reached


def _has_cycle(nodes: set[str], adjacency: Mapping[str, set[str]]) -> bool:
    indegree = {node: 0 for node in nodes}
    for targets in adjacency.values():
        for target in targets:
            indegree[target] += 1
    queue = deque(sorted(node for node, count in indegree.items() if count == 0))
    count = 0
    while queue:
        source = queue.popleft()
        count += 1
        for target in adjacency.get(source, set()):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return count != len(nodes)


def _pointer(part: Any) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")


__all__ = [
    "SCHEMA_NAMES",
    "ValidationFinding",
    "ValidationReport",
    "validate_goal_graph",
    "validate_model_exchange",
    "validate_model_invocation",
    "validate_proposal_workpad",
    "validate_serialized_contract",
]
