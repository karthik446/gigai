"""Typed, inert compilation of the bundled Scout candidate into one Gig.

This module is intentionally a closed built-in boundary: it accepts only the
bundled Scout catalog candidate and never imports, executes, or dynamically
discovers user supplied Python.  It journals immutable software snapshots;
editable root copies are derived conveniences and are never execution approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import tempfile
from types import MappingProxyType
from typing import Callable, Mapping
import uuid

from .canonical import EntityPrefix, canonical_json_bytes, canonical_json_digest, digest_imported_bytes, generate_entity_id, parse_json_bytes
from .capabilities import CapabilityManifestError, materialize_capability_manifest
from .catalog import CatalogEntry
from .journal import JournalArtifact, JournalArtifactMissingError, JournalConflictError, JournalReconciliationRequired, read_committed_artifact, record_transition
from .lifecycle import LifecycleError, propose_first_graph_set_offline
from .scout_template import (SCOUT_DEFINITION_VERSION, SCOUT_DISCOVERY_SCHEMA_PATH,
                              SCOUT_DISCOVERY_SOURCE_PATH, SCOUT_OPERATION_GRAPHS,
                              SCOUT_RESEARCH_SOURCE_PATH, SCOUT_RESEARCH_SCHEMA_PATH,
                              SCOUT_TAILORING_SOURCE_PATH, SCOUT_TAILORING_SCHEMA_PATH,
                              ScoutTemplateComparison, scout_catalog_candidate,
                              scout_source_files)
from .scout_bundled_tools import SCOUT_CRUD_MANIFEST_ID, prepared_scout_crud_manifest


class ScoutMaterializationError(RuntimeError):
    """A candidate source snapshot or proposal cannot be safely prepared."""


@dataclass(frozen=True)
class ScoutMaterialization:
    source_digest: str
    inventory_ref: dict[str, object]
    proposal_id: str
    proposal_ref: dict[str, object]
    approval_state: str
    customized_paths: tuple[str, ...]


@dataclass(frozen=True)
class ScoutTemplateUpdate:
    """One explicit source update decision; no Gig/Run selection is changed."""

    decision: str
    source_digest: str
    applied_paths: tuple[str, ...]
    preserved_paths: tuple[str, ...]
    handoff_id: str | None


@dataclass(frozen=True)
class ScoutSourceSnapshot:
    """Authenticated portable source bytes from one committed journal head.

    This is a read-only package boundary for callers such as interview Run
    preparation.  It returns the exact source/inventory members already
    journaled for a Gig; it never reads the current package, installs a
    capability, or treats the inventory as activation authority.
    """

    inventory_ref: dict[str, object]
    journal_commit: str
    source_digest: str
    members: Mapping[str, bytes]


_BUDGET = {
    "max_model_calls": 2,
    "max_tool_calls": 0,
    "max_tokens": 8000,
    "max_cost": "0.50",
    "currency": "USD",
    "max_wall_time_ms": 300000,
    "max_parallel_goals": 1,
}
_ALLOWED_ROOTS = {"README.md", "CHANGELOG.md", "gig.py", "goalgraphs", "tools", "ui"}
_COMPILER_VERSION = "scout-candidate-compiler:5"


def is_scout_candidate(entry: CatalogEntry) -> bool:
    """Recognize only the exact bundled candidate, never an arbitrary hook."""

    candidate = scout_catalog_candidate()
    return (
        entry.catalog_id == candidate.catalog_id
        and entry.definition_version == candidate.definition_version
        and entry.package_id == candidate.package_id
        and entry.entry_content_digest == candidate.entry_content_digest
    )


def _artifact_ref(path: str, media_type: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": media_type,
        "size_bytes": len(data),
    }


def _id(prefix: EntityPrefix, factory: Callable[[], uuid.UUID]) -> str:
    return generate_entity_id(prefix, is_persisted=lambda _candidate: False, uuid_factory=factory)


def _source_digest(source: Mapping[str, bytes]) -> str:
    return canonical_json_digest(
        {
            "compiler_version": _COMPILER_VERSION,
            "definition_version": SCOUT_DEFINITION_VERSION,
            "members": [
                {
                    "path": path,
                    "content_sha256": digest_imported_bytes(data),
                    "size_bytes": len(data),
                }
                for path, data in sorted(source.items())
            ],
        }
    )


def _inventory_member_rows(members: Mapping[str, bytes]) -> list[dict[str, object]]:
    """Render the canonical, complete inventory member list."""
    return [
        {
            "path": path,
            "content_sha256": digest_imported_bytes(data),
            "size_bytes": len(data),
        }
        for path, data in sorted(members.items())
    ]


def _validate_existing_inventory(
    workpad: Path, project_id: str, gig_id: str, inventory_path: str,
    payload: Mapping[str, object], inventory_data: bytes,
    *, head: str | None = None,
) -> dict[str, bytes]:
    """Validate every inventory row against its one pinned journal commit."""
    if not isinstance(payload.get("members"), list):
        raise ScoutMaterializationError("Scout software inventory members are malformed")
    if head is None:
        _data, head = read_committed_artifact(
            workpad=workpad, project_id=project_id, gig_id=gig_id, path=inventory_path
        )
    prefix = inventory_path.removesuffix("/source-inventory.json") + "/"
    rows: dict[str, tuple[str, int]] = {}
    members: dict[str, bytes] = {}
    for row in payload["members"]:
        if not isinstance(row, Mapping) or set(row) != {"path", "content_sha256", "size_bytes"}:
            raise ScoutMaterializationError("Scout software inventory member row is malformed")
        path = row.get("path")
        digest = row.get("content_sha256")
        size = row.get("size_bytes")
        if not isinstance(path, str) or Path(path).is_absolute() or "\\" in path or ".." in Path(path).parts or not isinstance(digest, str) or type(size) is not int or path in rows:
            raise ScoutMaterializationError("Scout software inventory member row is invalid")
        member_path = prefix + path
        try:
            data, _publisher = read_committed_artifact(
                workpad=workpad, project_id=project_id, gig_id=gig_id,
                path=member_path, head=head,
            )
        except Exception as exc:
            raise ScoutMaterializationError("Scout software inventory member is not committed authority") from exc
        if digest_imported_bytes(data) != digest or len(data) != size:
            raise ScoutMaterializationError("Scout software inventory member bytes differ from authority")
        rows[path] = (digest, size)
        members[path] = data
    listed = subprocess.run(
        ["git", "-C", str(workpad), "ls-tree", "-r", "-z", "--name-only", head, "--", prefix],
        check=False, capture_output=True, shell=False,
    )
    if listed.returncode != 0:
        raise ScoutMaterializationError("Scout software inventory members cannot be enumerated")
    actual = {
        item.decode("utf-8").removeprefix(prefix)
        for item in listed.stdout.split(b"\0") if item
    }
    actual.discard("source-inventory.json")
    if actual != set(rows):
        raise ScoutMaterializationError("Scout software inventory member set differs from authority")
    return members


def _review_contract(contract_id: str, created_at: str) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": "1.0",
            "contract_id": contract_id,
            "contract_version": 1,
            "created_at": created_at,
            "created_by": {"kind": "gigai", "id": "scout-candidate-compiler", "model_target": None},
            "name": "scout-candidate-review",
            "question": "Review the declared external actor outputs against the selected Scout inputs.",
            "reference_roles": ["primary"],
            "criteria": [{
                "criterion_id": "criterion_scoped_output",
                "description": "The output names the selected inputs, evidence and unresolved questions.",
                "severity": "high",
                "required_evidence": ["sealed-input"],
                "citation_requirement": "required",
                "evaluator_ids": ["evaluator_scout"],
            }],
            "severity_model": {"levels": ["info", "low", "medium", "high", "critical"], "ordering": ["info", "low", "medium", "high", "critical"]},
            "evidence_requirements": ["sealed-input"],
            "output_shape": {"machine_media_type": "application/json", "human_media_type": "text/markdown", "required_sections": ["findings"]},
            "clarification_policy": "block_run",
            "cycle_cap": 1,
            "escalation_policy": "operator",
            "allowed_effects": ["write_workpad"],
            "evaluator_plan": [{"evaluator_id": "evaluator_scout", "evaluator_version": "1", "stage": "deterministic"}],
            "redaction_policy": {"mode": "local_only", "policy_version": "scout-candidate-1", "detector_version": None},
        }
    )


def _compiled_snapshot(
    *, gig_id: str, source: Mapping[str, bytes], uuid_factory: Callable[[], uuid.UUID]
) -> tuple[dict[str, bytes], tuple[str, ...]]:
    """Compile all five declared selectors to schema-valid, unapproved graphs."""

    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    compiled: dict[str, bytes] = {}
    descriptors: list[dict[str, object]] = []
    goal_ids: list[str] = []
    source_digest = _source_digest(source).removeprefix("sha256:")
    software_prefix = f"manifests/software/scout-{SCOUT_DEFINITION_VERSION}-{source_digest[:16]}"
    for ordinal, item in enumerate(SCOUT_OPERATION_GRAPHS):
        graph_id = _id(EntityPrefix.GRAPH, uuid_factory)
        goal_id = _id(EntityPrefix.GOAL, uuid_factory)
        goal_ids.append(goal_id)
        completion_kind = (
            "tailoring-completion" if item.selector == "tailor-application"
            else f"{item.selector}-completion"
        )
        contracts_root = f"compiled/{item.selector}"
        instructions = source[item.instructions_path]
        compiled_goal_contract = f"{contracts_root}/goal-contract.md"
        compiled[compiled_goal_contract] = instructions
        goal_contract = _artifact_ref(
            f"{item.selector}/goal-contract.md", "text/markdown", instructions
        )
        graph = {
            "schema_version": "1.0",
            "graph_id": graph_id,
            "gig_id": gig_id,
            "graph_version": 1,
            "created_at": created_at,
            "aggregate_budget": _BUDGET,
            "failure_policy": "fail_gig",
            "goals": [{
                "goal_id": goal_id,
                "goal_version": 1,
                "display_ordinal": f"G{ordinal:02d}",
                "slug": item.selector,
                "title": item.title,
                "required": True,
                "activation": "automatic",
                "contract": goal_contract,
                "executor": {"kind": "local_capability", "capability": "gigai.offline", "role": None, "resolution": "installed", "materialized_by": None, "blocking_reason": None},
                "tools": [],
                "effects": ["write_workpad"],
                "write_surfaces": ["docs/", "runs/"],
                "exclusive_resources": ["scout-candidate"],
                "budget": {**_BUDGET, "max_model_calls": 1, "max_tokens": 4000, "max_wall_time_ms": 120000},
                "verification": {"verifier": "gigai.check", "acceptance": "Required outputs and declared evidence are present.", "required_evidence": [completion_kind]},
                "outcomes": ["COMPLETE"],
            }],
            "edges": [],
            "entry_goal_ids": [goal_id],
            "terminal_goal_ids": [goal_id],
            "required_completion_evidence": [completion_kind],
        }
        contract_values = {
            "input_contract": {"schema_version": "1.0", "kind": "run_input_contract", "gig_id": gig_id, "fields": [*item.required_inputs, *item.optional_inputs]},
            "output_contract": {"schema_version": "1.0", "kind": "run_output_contract", "gig_id": gig_id, "fields": list(item.outputs)},
            "permitted_reference_contract": {"schema_version": "1.0", "kind": "permitted_reference_contract", "gig_id": gig_id, "fields": ["selected_revisions"]},
            "evaluation_contract": {"schema_version": "1.0", "kind": "evaluation_contract", "gig_id": gig_id, "fields": ["external_actor_declared"]},
            "completion_evidence_contract": {"schema_version": "1.0", "kind": "completion_evidence_contract", "gig_id": gig_id, "fields": [f"{item.selector}-completion"]},
        }
        if item.selector == "research-role":
            contract_values["output_contract"] = {
                "schema_version": "2.0", "kind": "run_output_contract",
                "gig_id": gig_id, "fields": ["research"],
                "domains": {"research": {
                    "schema_id": "urn:gigai:scout:research-packet:3",
                    "schema_ref": _artifact_ref(
                        f"{software_prefix}/{SCOUT_RESEARCH_SCHEMA_PATH}",
                        "application/json", source[SCOUT_RESEARCH_SCHEMA_PATH],
                    ),
                    "validator_id": "scout-role-research:3",
                    "validator_source_ref": _artifact_ref(
                        f"{software_prefix}/{SCOUT_RESEARCH_SOURCE_PATH}",
                        "text/x-python", source[SCOUT_RESEARCH_SOURCE_PATH],
                    ),
                }},
            }
        elif item.selector == "find-jobs":
            contract_values["output_contract"] = {
                "schema_version": "2.0", "kind": "run_output_contract",
                "gig_id": gig_id, "fields": ["discovery"],
                "domains": {"discovery": {
                    "schema_id": "urn:gigai:scout:discovery-packet:2",
                    "schema_ref": _artifact_ref(
                        f"{software_prefix}/{SCOUT_DISCOVERY_SCHEMA_PATH}",
                        "application/json", source[SCOUT_DISCOVERY_SCHEMA_PATH],
                    ),
                    "validator_id": "scout-job-discovery:2",
                    "validator_source_ref": _artifact_ref(
                        f"{software_prefix}/{SCOUT_DISCOVERY_SOURCE_PATH}",
                        "text/x-python", source[SCOUT_DISCOVERY_SOURCE_PATH],
                    ),
                }},
            }
        elif item.selector == "tailor-application":
            contract_values["output_contract"] = {
                "schema_version": "2.0", "kind": "run_output_contract",
                "gig_id": gig_id, "fields": ["tailoring"],
                "domains": {"tailoring": {
                    "schema_id": "urn:gigai:scout:tailoring-packet:1",
                    "schema_ref": _artifact_ref(
                        f"{software_prefix}/{SCOUT_TAILORING_SCHEMA_PATH}",
                        "application/json", source[SCOUT_TAILORING_SCHEMA_PATH],
                    ),
                    "validator_id": "scout-application-tailoring:1",
                    "validator_source_ref": _artifact_ref(
                        f"{software_prefix}/{SCOUT_TAILORING_SOURCE_PATH}",
                        "text/x-python", source[SCOUT_TAILORING_SOURCE_PATH],
                    ),
                }},
            }
            contract_values["completion_evidence_contract"] = {
                "schema_version": "1.0", "kind": "completion_evidence_contract",
                "gig_id": gig_id, "fields": ["tailoring-completion"],
            }
        elif item.selector == "proposal-assessment":
            # This is an assessment operation, not document Tailoring.  Keep
            # the existing closed v1 output envelope until a versioned
            # proposal-assessment domain contract is intentionally introduced;
            # no historical .075 source or schema is reused here.
            contract_values["completion_evidence_contract"] = {
                "schema_version": "1.0", "kind": "completion_evidence_contract",
                "gig_id": gig_id, "fields": ["proposal-assessment-completion"],
            }
        graph_path = f"{contracts_root}/goal-graph.json"
        definition_graph_path = f"{item.selector}/goal-graph.json"
        graph_data = canonical_json_bytes(graph)
        compiled[graph_path] = graph_data
        descriptor: dict[str, object] = {
            "graph_id": item.selector,
            "purpose": item.purpose,
            "aliases": [],
            "routing_summary": item.title,
            "goal_graph": _artifact_ref(definition_graph_path, "application/json", graph_data),
            "effect_policy": ["write_workpad"],
            "capability_requirements": ["gigai.offline"],
            "provider_eligibility": {"providers": ["deterministic"]},
            "budget": _BUDGET,
        }
        for field, value in contract_values.items():
            path = f"{contracts_root}/{field}.json"
            data = canonical_json_bytes(value)
            compiled[path] = data
            descriptor[field] = _artifact_ref(
                f"{item.selector}/{field}.json", "application/json", data
            )
        review_path = f"{contracts_root}/review-contract.json"
        review = _review_contract(f"contract_{uuid_factory()}", created_at)
        compiled[review_path] = review
        descriptor["review_contract"] = _artifact_ref(
            f"{item.selector}/review-contract.json", "application/json", review
        )
        descriptors.append(descriptor)
    gig_document = source["README.md"]
    creation = canonical_json_bytes({"schema_version": "1.0", "creation_mode": "scout-candidate-compiler", "model_target": "none", "model_output": "Bundled Scout authoring source compiled without execution."})
    compiled["compiled/gig.md"] = gig_document
    compiled["compiled/creation-manifest.json"] = creation
    definition = {
        "schema_version": "1.0",
        "gig_id": gig_id,
        "name": "scout",
        "commission": "Prepare the bundled Scout candidate for direct operator approval; no Run or tool execution is authorized.",
        "gig_document": _artifact_ref("gig.md", "text/markdown", gig_document),
        "creation_manifest": _artifact_ref("creation-manifest.json", "application/json", creation),
        "graphs": descriptors,
        "shared_policy": {"effects": ["write_workpad"], "required_capability_ids": ["gigai.offline"], "provider_eligibility": {"providers": ["deterministic"]}, "budget": _BUDGET},
    }
    compiled["compiled/first-graph-set-definition.json"] = canonical_json_bytes(definition)
    return compiled, tuple(goal_ids)


def _safe_root_path(workpad: Path, relative: str) -> Path:
    value = Path(relative)
    if (
        value.is_absolute()
        or "\\" in relative
        or ".." in value.parts
        or not value.parts
        or value.parts[0] not in _ALLOWED_ROOTS
    ):
        raise ScoutMaterializationError("Scout source member path is unsafe")
    candidate = workpad / value
    cursor = workpad
    for part in value.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ScoutMaterializationError("Scout source destination is redirected")
    return candidate


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def materialize_scout_template_update(
    *,
    workpad: Path,
    project_id: str,
    gig_id: str,
    comparison: ScoutTemplateComparison,
    decision: str,
    source: Mapping[str, bytes] | None = None,
    baseline: Mapping[str, bytes] | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> ScoutTemplateUpdate:
    """Apply only an explicit, reviewable template update.

    Customized and local-only files are always preserved.  The update gets a
    new immutable software snapshot under the journal; it never edits the
    original snapshot, active pointer, consent, graph selection, or private
    history.  Defer is a pure no-op.
    """
    if not isinstance(comparison, ScoutTemplateComparison):
        raise ScoutMaterializationError("template update comparison is invalid")
    if decision not in {"adopt", "defer"}:
        raise ScoutMaterializationError("template update decision must be adopt or defer")
    expected = {
        path: data
        for path, data in dict(source or scout_source_files()).items()
        if not path.startswith("definition/")
    }
    source_digest = _source_digest(expected)
    preserved = tuple(sorted(set(comparison.customized) | set(comparison.local_only)))
    if decision == "defer":
        return ScoutTemplateUpdate("defer", source_digest, (), preserved, None)
    applied = tuple(sorted(set(comparison.updated) | set(comparison.missing)))
    for path in applied:
        _safe_root_path(workpad, path)
        candidate = workpad / path
        if path in comparison.updated and baseline is not None:
            if not candidate.is_file() or candidate.read_bytes() != baseline.get(path):
                raise ScoutMaterializationError(
                    "template working copy changed after comparison; rerun comparison"
                )
        elif path in comparison.missing and (candidate.exists() or candidate.is_symlink()):
            raise ScoutMaterializationError(
                "template working copy changed after comparison; rerun comparison"
            )
    update_prefix = f"manifests/software/updates/scout-{source_digest.removeprefix('sha256:')[:16]}"
    inventory_path = f"{update_prefix}/source-inventory.json"
    inventory_data = canonical_json_bytes({
        "schema_version": "1.0",
        "kind": "scout_software_inventory_update",
        "template_id": "scout",
        "gig_id": gig_id,
        "definition_version": SCOUT_DEFINITION_VERSION,
        "source_digest": source_digest,
        "members": _inventory_member_rows(expected),
        "preserved_customizations": list(preserved),
    })
    artifacts = tuple(
        JournalArtifact(f"{update_prefix}/{path}", data)
        for path, data in sorted(expected.items())
    ) + (JournalArtifact(inventory_path, inventory_data),)
    handoff = record_transition(
        workpad=workpad,
        project_id=project_id,
        gig_id=gig_id,
        handoff_id=_id(EntityPrefix.HANDOFF, uuid_factory),
        transition="scout_source_materialized",
        body="Explicit Scout template update adopted; customized files and prior history were preserved.",
        artifacts=artifacts,
        front_matter={
            "update_kind": "template_update_adopted",
            "source_digest": source_digest,
            "applied_paths": list(applied),
            "preserved_paths": list(preserved),
            "automatic_active_selection": False,
        },
        allow_artifact_replacement=False,
    )
    for path in applied:
        _atomic_write(workpad / path, expected[path])
    return ScoutTemplateUpdate("adopt", source_digest, applied, preserved, handoff.handoff_id)


def _read_snapshot(
    *, workpad: Path, project_id: str, gig_id: str, inventory_path: str
) -> tuple[dict[str, object], bytes] | None:
    try:
        data, _commit = read_committed_artifact(workpad=workpad, project_id=project_id, gig_id=gig_id, path=inventory_path)
    except JournalArtifactMissingError:
        return None
    except (JournalConflictError, JournalReconciliationRequired) as exc:
        raise ScoutMaterializationError("Scout software inventory cannot be authenticated") from exc
    current = workpad / inventory_path
    if current.is_symlink() or not current.is_file() or current.read_bytes() != data:
        raise ScoutMaterializationError("Scout software inventory differs from committed authority")
    try:
        payload = parse_json_bytes(data)
    except ValueError as exc:
        raise ScoutMaterializationError("Scout software inventory is malformed") from exc
    if not isinstance(payload, dict):
        raise ScoutMaterializationError("Scout software inventory is malformed")
    return payload, data


def read_scout_source_snapshot(
    *,
    workpad: Path,
    project_id: str,
    gig_id: str,
    inventory_ref: Mapping[str, object],
) -> ScoutSourceSnapshot:
    """Read portable Scout source members from one authenticated journal head.

    ``inventory_ref`` is caller input, but only its exact path/digest/size are
    accepted.  The inventory and every listed member are then read from the
    same committed journal head, so a portable interview asset cannot combine
    source bytes from one version with an inventory from another.  Returned
    bytes are inert package material; no active pointer, approval, consent, or
    executable selection is restored by this helper.
    """

    path = inventory_ref.get("path")
    expected_digest = inventory_ref.get("content_sha256")
    expected_size = inventory_ref.get("size_bytes")
    if (
        not isinstance(path, str)
        or not path.startswith("manifests/software/")
        or not path.endswith("/source-inventory.json")
        or "\\" in path
        or "//" in path
        or ".." in Path(path).parts
        or not isinstance(expected_digest, str)
        or type(expected_size) is not int
        or inventory_ref.get("media_type") != "application/json"
    ):
        raise ScoutMaterializationError("Scout software inventory reference is invalid")
    try:
        inventory_data, journal_commit = read_committed_artifact(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            path=path,
        )
    except (JournalArtifactMissingError, JournalConflictError, JournalReconciliationRequired) as exc:
        raise ScoutMaterializationError("Scout software inventory cannot be authenticated") from exc
    if (
        digest_imported_bytes(inventory_data) != expected_digest
        or len(inventory_data) != expected_size
    ):
        raise ScoutMaterializationError("Scout software inventory reference digest differs")
    current = workpad / path
    if current.is_symlink() or not current.is_file() or current.read_bytes() != inventory_data:
        raise ScoutMaterializationError("Scout software inventory differs from committed authority")
    try:
        payload = parse_json_bytes(inventory_data)
    except ValueError as exc:
        raise ScoutMaterializationError("Scout software inventory is malformed") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("kind") != "scout_software_inventory"
        or payload.get("compiler_version") != _COMPILER_VERSION
        or not isinstance(payload.get("source_digest"), str)
    ):
        raise ScoutMaterializationError("Scout software inventory is not portable source authority")
    members = _validate_existing_inventory(
        workpad,
        project_id,
        gig_id,
        path,
        payload,
        inventory_data,
        head=journal_commit,
    )
    for member_path, data in members.items():
        if member_path.startswith(("records/", "references/", "runs/", "reports/", "manifests/")):
            raise ScoutMaterializationError("Scout source inventory contains private or authority material")
        if member_path == "state.sqlite" or member_path.startswith(".git/"):
            raise ScoutMaterializationError("Scout source inventory contains private state")
    return ScoutSourceSnapshot(
        inventory_ref=_artifact_ref(path, "application/json", inventory_data),
        journal_commit=journal_commit,
        source_digest=payload["source_digest"],
        members=MappingProxyType(members),
    )


def repair_prepared_scout_capability_manifest(
    *,
    workpad: Path,
    project_id: str,
    gig_id: str,
    inventory_ref: Mapping[str, object],
    source_digest: str,
) -> None:
    """Restore only a missing derived pending manifest from sealed source.

    This deliberately does not compile current package source, copy editable
    files, re-stage a Graph Set, or infer review approval.  It is safe only
    for the exact current compiler inventory referenced by the v2 binding.
    """

    path = inventory_ref.get("path")
    expected_digest = inventory_ref.get("content_sha256")
    expected_size = inventory_ref.get("size_bytes")
    if (
        not isinstance(path, str)
        or not path.startswith("manifests/software/")
        or not path.endswith("/source-inventory.json")
        or not isinstance(expected_digest, str)
        or not isinstance(expected_size, int)
        or inventory_ref.get("media_type") != "application/json"
    ):
        raise ScoutMaterializationError("Scout software inventory reference is invalid")
    try:
        inventory_data, _inventory_commit = read_committed_artifact(
            workpad=workpad, project_id=project_id, gig_id=gig_id, path=path
        )
    except (JournalArtifactMissingError, JournalConflictError, JournalReconciliationRequired) as exc:
        raise ScoutMaterializationError("Scout software inventory cannot be authenticated") from exc
    current_inventory = workpad / path
    if (
        digest_imported_bytes(inventory_data) != expected_digest
        or len(inventory_data) != expected_size
        or current_inventory.is_symlink()
        or not current_inventory.is_file()
        or current_inventory.read_bytes() != inventory_data
    ):
        raise ScoutMaterializationError("Scout software inventory differs from committed authority")
    try:
        inventory = parse_json_bytes(inventory_data)
    except ValueError as exc:
        raise ScoutMaterializationError("Scout software inventory is malformed") from exc
    if (
        not isinstance(inventory, dict)
        or inventory.get("kind") != "scout_software_inventory"
        or inventory.get("compiler_version") != _COMPILER_VERSION
        or inventory.get("source_digest") != source_digest
    ):
        raise ScoutMaterializationError("Scout software inventory is not eligible for derived manifest repair")
    sealed_path = path.removesuffix("/source-inventory.json") + "/compiled/prepared-capability-manifest.json"
    try:
        manifest_data, _manifest_commit = read_committed_artifact(
            workpad=workpad, project_id=project_id, gig_id=gig_id, path=sealed_path
        )
    except (JournalArtifactMissingError, JournalConflictError, JournalReconciliationRequired) as exc:
        raise ScoutMaterializationError("prepared Scout capability manifest cannot be authenticated") from exc
    sealed_manifest = workpad / sealed_path
    if (
        sealed_manifest.is_symlink()
        or not sealed_manifest.is_file()
        or sealed_manifest.read_bytes() != manifest_data
    ):
        raise ScoutMaterializationError("prepared Scout capability manifest differs from committed authority")
    try:
        manifest = parse_json_bytes(manifest_data)
    except ValueError as exc:
        raise ScoutMaterializationError("prepared Scout capability manifest is malformed") from exc
    if not isinstance(manifest, dict) or manifest.get("manifest_id") != SCOUT_CRUD_MANIFEST_ID:
        raise ScoutMaterializationError("prepared Scout capability manifest identity differs")
    try:
        materialize_capability_manifest(workpad, manifest)
    except CapabilityManifestError as exc:
        raise ScoutMaterializationError("prepared Scout capability manifest is invalid") from exc


def materialize_scout_candidate(
    *,
    home_root: Path,
    requested_target: Path | None,
    workpad: Path,
    project_id: str,
    gig_id: str,
    entry: CatalogEntry,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    observer: Callable[[str], None] | None = None,
) -> ScoutMaterialization:
    """Journal source, repair only missing editable copies, and stage first proposal."""

    if not is_scout_candidate(entry):
        raise ScoutMaterializationError("unsupported candidate materialization request")
    observer = observer or (lambda _step: None)
    source = dict(scout_source_files())
    source_digest = _source_digest(source)
    version = f"scout-{SCOUT_DEFINITION_VERSION}-{source_digest.removeprefix('sha256:')[:16]}"
    prefix = f"manifests/software/{version}"
    inventory_path = f"{prefix}/source-inventory.json"
    compiled, goal_ids = _compiled_snapshot(gig_id=gig_id, source=source, uuid_factory=uuid_factory)
    prepared_manifest_path = "compiled/prepared-capability-manifest.json"
    prepared_manifest_workpad_path = (
        f"manifests/capabilities/{SCOUT_CRUD_MANIFEST_ID}.json"
    )
    prepared_manifest_data = canonical_json_bytes(
        prepared_scout_crud_manifest(gig_id=gig_id, goal_ids=goal_ids, source=source)
    )
    compiled[prepared_manifest_path] = prepared_manifest_data
    expected_members = {**source, **compiled}
    inventory = {
        "schema_version": "1.0",
        "kind": "scout_software_inventory",
        "template_id": "scout",
        "definition_version": SCOUT_DEFINITION_VERSION,
        "source_digest": source_digest,
        "compiler_version": _COMPILER_VERSION,
        "members": _inventory_member_rows(expected_members),
    }
    inventory_data = canonical_json_bytes(inventory)
    sealed_inventory_data = inventory_data
    existing = _read_snapshot(workpad=workpad, project_id=project_id, gig_id=gig_id, inventory_path=inventory_path)
    if existing is None:
        for path in source:
            if path.startswith("definition/"):
                continue
            destination = _safe_root_path(workpad, path)
            if destination.exists() or destination.is_symlink():
                raise ScoutMaterializationError("Scout source destination collides before materialization")
        artifacts = tuple(
            JournalArtifact(f"{prefix}/{path}", data)
            for path, data in sorted(expected_members.items())
        ) + (
            JournalArtifact(inventory_path, inventory_data),
            JournalArtifact(prepared_manifest_workpad_path, prepared_manifest_data),
        )
        record_transition(
            workpad=workpad,
            project_id=project_id,
            gig_id=gig_id,
            handoff_id=_id(EntityPrefix.HANDOFF, uuid_factory),
            transition="scout_source_materialized",
            body="Bundled Scout candidate source was copied as inert software authority; approval remains required.",
            artifacts=artifacts,
            front_matter={"artifact_refs": [_artifact_ref(item.path, "text/markdown" if item.path.endswith(".md") else "application/json", item.content) for item in artifacts]},
        )
        observer("source_snapshot_published")
    else:
        existing_payload, _existing_data = existing
        sealed_inventory_data = _existing_data
        try:
            _validate_existing_inventory(
                workpad, project_id, gig_id, inventory_path,
                existing_payload, _existing_data,
            )
        except (JournalConflictError, JournalReconciliationRequired) as exc:
            raise ScoutMaterializationError("Scout software inventory cannot be authenticated") from exc
        if (
            existing_payload.get("schema_version") != "1.0"
            or existing_payload.get("kind") != "scout_software_inventory"
            or existing_payload.get("template_id") != "scout"
            or existing_payload.get("definition_version") != SCOUT_DEFINITION_VERSION
            or existing_payload.get("source_digest") != source_digest
            or existing_payload.get("compiler_version") != _COMPILER_VERSION
        ):
            raise ScoutMaterializationError("Scout source snapshot conflicts with the pinned candidate inventory")
        sealed_manifest_path = f"{prefix}/{prepared_manifest_path}"
        try:
            prepared_manifest_data, _manifest_commit = read_committed_artifact(
                workpad=workpad,
                project_id=project_id,
                gig_id=gig_id,
                path=sealed_manifest_path,
            )
        except (JournalArtifactMissingError, JournalConflictError, JournalReconciliationRequired) as exc:
            raise ScoutMaterializationError("prepared Scout capability manifest cannot be authenticated") from exc
        current_manifest = workpad / sealed_manifest_path
        if (
            current_manifest.is_symlink()
            or not current_manifest.is_file()
            or current_manifest.read_bytes() != prepared_manifest_data
        ):
            raise ScoutMaterializationError("prepared Scout capability manifest differs from committed authority")
    customized: list[str] = []
    for path, data in source.items():
        if path.startswith("definition/"):
            continue
        destination = _safe_root_path(workpad, path)
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise ScoutMaterializationError("Scout source destination is not a regular file")
            if destination.read_bytes() != data:
                customized.append(path)
            continue
        _atomic_write(destination, data)
    observer("source_working_copies_published")
    try:
        prepared_manifest = parse_json_bytes(prepared_manifest_data)
        if not isinstance(prepared_manifest, dict):
            raise ValueError("prepared capability manifest is not an object")
        if prepared_manifest.get("manifest_id") != SCOUT_CRUD_MANIFEST_ID:
            raise ValueError("prepared capability manifest identity differs")
        materialize_capability_manifest(workpad, prepared_manifest)
    except (CapabilityManifestError, ValueError) as exc:
        raise ScoutMaterializationError("prepared Scout capability manifest is invalid") from exc
    observer("capability_manifest_prepared")
    definition = workpad / prefix / "compiled/first-graph-set-definition.json"
    try:
        proposal = propose_first_graph_set_offline(
            home_root=home_root,
            requested_target=requested_target,
            definition_path=definition,
            gig_id=gig_id,
            uuid_factory=uuid_factory,
        )
    except LifecycleError as exc:
        raise ScoutMaterializationError(str(exc)) from exc
    proposal_path = "manifests/gig-proposal.json"
    proposal_data, _proposal_commit = read_committed_artifact(workpad=workpad, project_id=project_id, gig_id=gig_id, path=proposal_path)
    observer("candidate_proposal_prepared")
    return ScoutMaterialization(
        source_digest=source_digest,
        inventory_ref=_artifact_ref(inventory_path, "application/json", sealed_inventory_data),
        proposal_id=proposal.proposal_id,
        proposal_ref=_artifact_ref(proposal_path, "application/json", proposal_data),
        approval_state="unapproved",
        customized_paths=tuple(sorted(customized)),
    )
