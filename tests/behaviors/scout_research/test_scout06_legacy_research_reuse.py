"""Public same-Gig legacy research reuse across v2-domain and v3-domain Graph Sets."""

from __future__ import annotations

import base64
from copy import deepcopy
from importlib import import_module, resources
from pathlib import Path
import subprocess
from typing import Callable
import uuid

from gigai import external_recording
from gigai.scout import materialization as scout_materialization
from gigai.canonical import (
    canonical_json_bytes,
    digest_imported_bytes,
    parse_json_bytes,
)
from gigai.catalog import CatalogEntry
from gigai.default_init import initialize_defaults
from gigai.lifecycle import approve_offline, propose_graph_set_offline
from gigai.journal import JournalConflictError, read_committed_artifact
from gigai.scout.materialization import _compiled_snapshot, _source_digest
from gigai.scout.template import (
    SCOUT_DEFINITION_VERSION,
    scout_catalog_candidate,
    scout_source_files,
)
from gigai.setup import build_config, run_setup
import pytest


_LEGACY_RESOURCE_ROOT = "tools/cap_00000000-0000-4000-8000-000000000073"
_LEGACY_SCHEMA_PATH = f"{_LEGACY_RESOURCE_ROOT}/research.schema.json"
_LEGACY_SOURCE_PATH = f"{_LEGACY_RESOURCE_ROOT}/research.py"
_V3_RESOURCE_ROOT = "tools/cap_00000000-0000-4000-8000-000000000076"


def _ref(
    path: str, data: bytes, media_type: str = "application/json"
) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": media_type,
        "size_bytes": len(data),
    }


def _env(key: str, value: dict[str, object]) -> dict[str, object]:
    return {
        "origin": "direct_cli",
        "actor": {"kind": "operator", "id": "local-user"},
        "input": value,
        "operation_key": key,
    }


def _assert_committed_ref(
    *, workpad: Path, project_id: str, gig_id: str, ref: dict[str, object], head: str
) -> bytes:
    data, publisher = read_committed_artifact(
        workpad=workpad, project_id=project_id, gig_id=gig_id, path=str(ref["path"]), head=head
    )
    assert publisher
    assert data == (workpad / str(ref["path"])).read_bytes()
    assert digest_imported_bytes(data) == ref["content_sha256"]
    assert len(data) == ref["size_bytes"]
    return data


def _research() -> dict[str, object]:
    capture, review = b"Synthetic role capture.", b"Synthetic independent review."
    return {
        "role_title": "Forward Deployed Engineer",
        "role_summary": "Customer-facing engineering connects deployment work and product feedback.",
        "responsibilities": [
            {
                "responsibility_id": "responsibility_delivery",
                "description": "Deliver technical work with customer teams.",
                "claim_ids": ["claim_delivery"],
            }
        ],
        "variations": [
            {
                "variation_id": "variation_product",
                "description": "Some employers emphasize product feedback.",
                "claim_ids": ["claim_delivery"],
            }
        ],
        "reusable_sections": [
            {
                "section_id": "section_delivery",
                "heading": "Delivery context",
                "content": "Reuse this distinction later.",
                "claim_ids": ["claim_delivery"],
            }
        ],
        "compensation": {
            "status": "unknown",
            "geography": None,
            "currency": None,
            "as_of_date": None,
            "pay_period": None,
            "base_range": None,
            "total_range": None,
            "source_limitations": ["No salary evidence was supplied."],
            "claim_ids": [],
        },
        "sources": [
            {
                "source_id": "source_role",
                "locator": "https://example.test/role",
                "title": "Synthetic role profile",
                "publisher": "Example Labs",
                "kind": "employer",
                "published_date": None,
                "retrieved_date": "2026-09-10",
                "status": "independently_verified",
                "claim_ids": ["claim_delivery"],
                "capture_ref": {
                    "artifact_id": "role_capture",
                    "content_sha256": digest_imported_bytes(capture),
                    "size_bytes": len(capture),
                },
                "verification": {
                    "method": "independent_review",
                    "evidence_ref": {
                        "artifact_id": "role_review",
                        "content_sha256": digest_imported_bytes(review),
                        "size_bytes": len(review),
                    },
                    "actor": {"kind": "reviewer", "id": "synthetic-reviewer"},
                },
            }
        ],
        "claims": [
            {
                "claim_id": "claim_delivery",
                "statement": "The supplied role profile describes customer-team delivery.",
                "source_ids": ["source_role"],
                "status": "independently_verified",
            }
        ],
        "uncertainties": [
            {
                "uncertainty_id": "uncertainty_scope",
                "topic": "Employer specificity",
                "detail": "Scope varies by employer.",
                "claim_ids": [],
            }
        ],
        "questions": [
            {
                "question_id": "question_priority",
                "prompt": "Which delivery emphasis matters most?",
                "reason": "It narrows later tailoring.",
                "claim_ids": [],
            }
        ],
        "checks": [
            {
                "check_id": "check_source",
                "kind": "source_integrity",
                "result": "pass",
                "detail": "Supplied bytes match declared refs.",
                "claim_ids": ["claim_delivery"],
            }
        ],
        "output_roles": ["tailoring_context", "interview_context"],
    }


def _legacy_source() -> dict[str, bytes]:
    source = dict(scout_source_files())
    package = resources.files("gigai.scout").joinpath(
        "data", "tools", "cap_00000000-0000-4000-8000-000000000071"
    )
    source[_LEGACY_SCHEMA_PATH] = package.joinpath("research.schema.json").read_bytes()
    source[_LEGACY_SOURCE_PATH] = package.joinpath("research.py").read_bytes()
    return source


def _resource_prefix(source: dict[str, bytes]) -> str:
    digest = _source_digest(source).removeprefix("sha256:")[:16]
    return f"manifests/software/scout-{SCOUT_DEFINITION_VERSION}-{digest}"


def _with_domain(
    compiled: dict[str, bytes],
    *,
    source: dict[str, bytes],
    schema_id: str,
    validator_id: str,
    schema_path: str,
    validator_path: str,
) -> dict[str, bytes]:
    """Return a coherent compiled fixture whose first definition references its changed contract."""
    changed = dict(compiled)
    output_path = "compiled/research-role/output_contract.json"
    output = parse_json_bytes(changed[output_path])
    assert isinstance(output, dict)
    output["domains"]["research"] = {
        "schema_id": schema_id,
        "schema_ref": _ref(
            f"{_resource_prefix(source)}/{schema_path}", source[schema_path]
        ),
        "validator_id": validator_id,
        "validator_source_ref": _ref(
            f"{_resource_prefix(source)}/{validator_path}",
            source[validator_path],
            "text/x-python",
        ),
    }
    output_data = canonical_json_bytes(output)
    changed[output_path] = output_data
    definition_path = "compiled/first-graph-set-definition.json"
    definition = parse_json_bytes(changed[definition_path])
    assert isinstance(definition, dict)
    descriptor = next(
        item for item in definition["graphs"] if item["graph_id"] == "research-role"
    )
    descriptor["output_contract"] = _ref(
        "research-role/output_contract.json", output_data
    )
    changed[definition_path] = canonical_json_bytes(definition)
    return changed


def _legacy_compiler(original: Callable[..., tuple[dict[str, bytes], tuple[str, ...]]]):
    def compile_legacy(*, gig_id: str, source: dict[str, bytes], uuid_factory):
        compiled, goal_ids = original(
            gig_id=gig_id, source=source, uuid_factory=uuid_factory
        )
        return _with_domain(
            compiled,
            source=source,
            schema_id="urn:gigai:scout:research-packet:2",
            validator_id="scout-role-research:2",
            schema_path=_LEGACY_SCHEMA_PATH,
            validator_path=_LEGACY_SOURCE_PATH,
        ), goal_ids

    return compile_legacy


def _write_v3_successor_definition(
    root: Path, *, gig_id: str, legacy_source: dict[str, bytes]
) -> Path:
    """Author a source input for the public amendment API, not a workpad rewrite."""
    current_source = dict(scout_source_files())
    compiled, _ = _compiled_snapshot(
        gig_id=gig_id, source=current_source, uuid_factory=uuid.uuid4
    )
    compiled = _with_domain(
        compiled,
        source=legacy_source,
        schema_id="urn:gigai:scout:research-packet:3",
        validator_id="scout-role-research:3",
        schema_path=f"{_V3_RESOURCE_ROOT}/research.schema.json",
        validator_path=f"{_V3_RESOURCE_ROOT}/research.py",
    )
    for path, data in compiled.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    definition = parse_json_bytes(compiled["compiled/first-graph-set-definition.json"])
    assert isinstance(definition, dict)
    for key in (
        "schema_version",
        "name",
        "commission",
        "gig_document",
        "creation_manifest",
    ):
        definition.pop(key)
    for descriptor in definition["graphs"]:
        for field in (
            "goal_graph",
            "input_contract",
            "output_contract",
            "permitted_reference_contract",
            "review_contract",
            "evaluation_contract",
            "completion_evidence_contract",
        ):
            descriptor[field]["path"] = "compiled/" + descriptor[field]["path"]
        graph_ref = descriptor["goal_graph"]
        graph_path = root / graph_ref["path"]
        graph = parse_json_bytes(graph_path.read_bytes())
        assert isinstance(graph, dict)
        for goal in graph["goals"]:
            goal["contract"]["path"] = "compiled/" + goal["contract"]["path"]
        graph_data = canonical_json_bytes(graph)
        graph_path.write_bytes(graph_data)
        graph_ref.update(_ref(graph_ref["path"], graph_data))
    definition_path = root / "v3-successor-definition.json"
    definition_path.write_bytes(canonical_json_bytes(definition))
    return definition_path


def _complete_research(
    *,
    home: Path,
    target: Path,
    gig_id: str,
    plan: dict[str, object],
    run: dict[str, object],
    renderer_module: str,
    schema_id: str,
    key: str,
    output_only: bool = False,
) -> tuple[dict[str, object], dict[str, object]]:
    renderer = import_module(renderer_module)
    capture, review = b"Synthetic role capture.", b"Synthetic independent review."
    packet = renderer.build_research_packet(
        project_id=plan["project_id"],
        gig_id=gig_id,
        gig_version=plan["gig_version"],
        graph_id=plan["goal_graph_id"],
        graph_version=1,
        run_id=run["run_id"],
        selected_inputs=plan["inputs"],
        research=_research(),
        artifact_bytes={"role_capture": capture, "role_review": review},
    )
    digest = digest_imported_bytes(packet.markdown)
    output = {
        "kind": "research",
        "markdown": packet.markdown.decode(),
        "sidecar": {
            "document_sha256": digest,
            "output_kind": "research",
            "run_id": run["run_id"],
            "selected_inputs": plan["inputs"],
        },
        "domain_sidecar": {"schema_id": schema_id, "value": packet.sidecar},
        "supporting_artifacts": [
            {
                "artifact_id": name,
                "media_type": "text/plain",
                "content_base64": base64.b64encode(data).decode(),
                "content_sha256": digest_imported_bytes(data),
                "size_bytes": len(data),
            }
            for name, data in {"role_capture": capture, "role_review": review}.items()
        ],
    }
    check = {
        "kind": "research-role-completion",
        "markdown": "# Completion\n\npass\n",
        "sidecar": {
            "evidence_kind": "research-role-completion",
            "run_id": run["run_id"],
            "output_sha256": digest,
            "result": "pass",
        },
    }
    if output_only:
        return output, check
    checkpoint = external_recording.checkpoint_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_env(
            f"{key}-checkpoint",
            {
                "run_id": run["run_id"],
                "parent_checkpoint": None,
                "questions": [],
                "artifact_refs": [output, check],
                "reason": f"{key} fixture completion",
            },
        ),
    )
    submitted = external_recording.submit_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_env(
            f"{key}-submit",
            {
                "run_id": run["run_id"],
                "parent_checkpoint": checkpoint.payload["checkpoint_id"],
                "output_refs": [checkpoint.payload["artifacts"][0]],
                "check_refs": [checkpoint.payload["artifacts"][1]["sidecar"]],
                "disclosure": {"execution": "unobserved", "actor_report": "declared"},
            },
        ),
    )
    return checkpoint.payload, submitted.payload


def test_completed_legacy_v2_research_reuses_through_v3_successor(
    tmp_path: Path, monkeypatch
) -> None:
    """The selected legacy bytes remain v2 while the consuming Run is v3."""
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", target], check=True
    )
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    legacy_source = _legacy_source()
    current = scout_catalog_candidate()
    legacy_entry = CatalogEntry(
        catalog_id=current.catalog_id,
        definition_version="fixture-legacy-v2",
        title=current.title,
        summary=current.summary,
        capabilities=current.capabilities,
        files=legacy_source,
    )
    monkeypatch.setattr(
        scout_materialization, "scout_catalog_candidate", lambda: legacy_entry
    )
    monkeypatch.setattr(
        scout_materialization, "scout_source_files", lambda: legacy_source
    )
    monkeypatch.setattr(
        scout_materialization,
        "_compiled_snapshot",
        _legacy_compiler(_compiled_snapshot),
    )
    initialized = initialize_defaults(
        home_root=home,
        requested_target=target,
        username="owner",
        inventory=(legacy_entry,),
    )
    instance = initialized.instances[0]
    legacy_approval = approve_offline(
        home_root=home,
        requested_target=target,
        gig_id=instance.gig_id,
        proposal_id=str(instance.proposal_id),
    )
    assert legacy_approval.version == 1
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    gig_id = instance.gig_id
    legacy_plan = external_recording.plan_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_env(
            "legacy-v2-plan",
            {
                "graph_selector": "research-role",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [
                    {
                        "family": "role_request",
                        "role_title": "Forward Deployed Engineer",
                        "role_context": "Legacy fixture context",
                    }
                ],
                "output_kinds": ["research"],
                "predecessor": None,
            },
        ),
    )
    assert legacy_plan.payload["gig_version"] == 1
    legacy_contract = parse_json_bytes(
        (workpad / legacy_plan.payload["output_contract"]["path"]).read_bytes()
    )
    assert isinstance(legacy_contract, dict)
    legacy_domain = legacy_contract["domains"]["research"]
    assert legacy_domain["schema_id"] == "urn:gigai:scout:research-packet:2"
    assert legacy_domain["schema_ref"]["path"].endswith(_LEGACY_SCHEMA_PATH)
    legacy_run = external_recording.start_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_env(
            "legacy-v2-start", {"run_plan_id": legacy_plan.payload["run_plan_id"]}
        ),
    )
    legacy_checkpoint, legacy_receipt = _complete_research(
        home=home,
        target=target,
        gig_id=gig_id,
        plan=legacy_plan.payload,
        run=legacy_run.payload,
        renderer_module="gigai.scout.data.tools.cap_00000000-0000-4000-8000-000000000071.research",
        schema_id="urn:gigai:scout:research-packet:2",
        key="legacy-v2",
    )
    assert legacy_receipt["outcome"] == "succeeded"
    selector = {
        "family": "scout_research",
        "run_id": legacy_run.payload["run_id"],
        "receipt_id": legacy_receipt["receipt_id"],
        "output_kind": "research",
    }
    legacy_paths = {
        ref["path"]: (workpad / ref["path"]).read_bytes()
        for ref in [
            legacy_receipt["outputs"][0]["markdown"],
            legacy_receipt["outputs"][0]["sidecar"],
            legacy_receipt["outputs"][0]["domain_sidecar"],
            legacy_checkpoint["artifacts"][1]["sidecar"],
            *(
                entry["ref"]
                for entry in legacy_receipt["outputs"][0]["supporting_artifacts"]
            ),
        ]
    }
    v3_definition = _write_v3_successor_definition(
        tmp_path / "v3-input", gig_id=gig_id, legacy_source=legacy_source
    )
    v3_proposal = propose_graph_set_offline(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        definition_path=v3_definition,
    )
    pending = parse_json_bytes((workpad / "manifests/gig-proposal.json").read_bytes())
    assert isinstance(pending, dict)
    graph_set_data = _assert_committed_ref(
        workpad=workpad, project_id=legacy_plan.payload["project_id"], gig_id=gig_id,
        ref=pending["graph_set"], head=v3_proposal.entry.commit,
    )
    graph_set = parse_json_bytes(graph_set_data)
    assert isinstance(graph_set, dict)
    for descriptor in graph_set["graphs"]:
        for field in (
            "goal_graph", "input_contract", "output_contract", "permitted_reference_contract",
            "review_contract", "evaluation_contract", "completion_evidence_contract",
        ):
            _assert_committed_ref(
                workpad=workpad, project_id=legacy_plan.payload["project_id"], gig_id=gig_id,
                ref=descriptor[field], head=v3_proposal.entry.commit,
            )
        graph_data = _assert_committed_ref(
            workpad=workpad, project_id=legacy_plan.payload["project_id"], gig_id=gig_id,
            ref=descriptor["goal_graph"], head=v3_proposal.entry.commit,
        )
        graph = parse_json_bytes(graph_data)
        assert isinstance(graph, dict)
        for goal in graph["goals"]:
            _assert_committed_ref(
                workpad=workpad, project_id=legacy_plan.payload["project_id"], gig_id=gig_id,
                ref=goal["contract"], head=v3_proposal.entry.commit,
            )
    with pytest.raises(JournalConflictError):
        read_committed_artifact(
            workpad=workpad, project_id=legacy_plan.payload["project_id"], gig_id=gig_id,
            path="manifests/gig-proposal.json", head=v3_proposal.entry.commit,
        )
    v3_approval = approve_offline(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        proposal_id=v3_proposal.proposal_id,
    )
    assert v3_approval.version == 2
    v3_plan = external_recording.plan_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_env(
            "v3-reuse-plan",
            {
                "graph_selector": "research-role",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [
                    {
                        "family": "role_request",
                        "role_title": "Forward Deployed Engineer",
                        "role_context": "Current successor context",
                    },
                    deepcopy(selector),
                ],
                "output_kinds": ["research"],
                "predecessor": None,
            },
        ),
    )
    assert v3_plan.payload["gig_version"] == 2
    selected = next(
        item for item in v3_plan.payload["inputs"] if item["family"] == "scout_research"
    )
    assert selected["run_id"] == legacy_run.payload["run_id"]
    assert (
        selected["domain_binding"]["schema_id"] == "urn:gigai:scout:research-packet:2"
    )
    assert selected["output"] == legacy_receipt["outputs"][0]
    v3_run = external_recording.start_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_env(
            "v3-reuse-start", {"run_plan_id": v3_plan.payload["run_plan_id"]}
        ),
    )
    v3_checkpoint, v3_receipt = _complete_research(
        home=home,
        target=target,
        gig_id=gig_id,
        plan=v3_plan.payload,
        run=v3_run.payload,
        renderer_module="gigai.scout.data.tools.cap_00000000-0000-4000-8000-000000000076.research",
        schema_id="urn:gigai:scout:research-packet:3",
        key="v3-reuse",
    )
    assert v3_receipt["outcome"] == "succeeded"
    assert v3_checkpoint["artifacts"][0]["domain_sidecar"]["path"].startswith(
        f"runs/{v3_run.payload['run_id']}/"
    )
    assert {
        path: (workpad / path).read_bytes() for path in legacy_paths
    } == legacy_paths
    legacy_replay = external_recording.submit_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_env(
            "legacy-v2-submit",
            {
                "run_id": legacy_run.payload["run_id"],
                "parent_checkpoint": legacy_checkpoint["checkpoint_id"],
                "output_refs": [legacy_checkpoint["artifacts"][0]],
                "check_refs": [legacy_checkpoint["artifacts"][1]["sidecar"]],
                "disclosure": {"execution": "unobserved", "actor_report": "declared"},
            },
        ),
    )
    assert legacy_replay.created is False
    assert legacy_replay.payload == legacy_receipt
