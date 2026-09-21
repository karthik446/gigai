from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.comparison import _read_run
from gigai.lifecycle import approve_offline, create_offline, propose_graph_set_offline
from gigai.listing import list_gigs
from gigai.portability import PortabilityError, verify_active_version_portability
from gigai.run import _provider_review_active, launch_run
from gigai.run_plan import RunPlanError, _review_contract, create_run_plan, read_run_plan
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_serialized_contract


def _ref(path: str, data: bytes, media: str = "application/json") -> dict[str, object]:
    return {"path": path, "content_sha256": digest_imported_bytes(data), "media_type": media, "size_bytes": len(data)}


def _write_definition(root: Path, workpad: Path, gig_id: str, *, suffix: str = "") -> Path:
    root.mkdir()
    original = parse_json_bytes((workpad / "manifests/goal-graph.json").read_bytes())
    assert isinstance(original, dict)
    budget = {"max_model_calls": 2, "max_tool_calls": 0, "max_tokens": 8000, "max_cost": "0.50", "currency": "USD", "max_wall_time_ms": 300000, "max_parallel_goals": 1}
    contracts: dict[str, bytes] = {
        "input": canonical_json_bytes({"schema_version": "1.0", "kind": "run_input_contract", "gig_id": gig_id, "fields": ["primary"]}),
        "output": canonical_json_bytes({"schema_version": "1.0", "kind": "run_output_contract", "gig_id": gig_id, "fields": ["report"]}),
        "references": canonical_json_bytes({"schema_version": "1.0", "kind": "permitted_reference_contract", "gig_id": gig_id, "fields": ["explicit"]}),
        "evaluation": canonical_json_bytes({"schema_version": "1.0", "kind": "evaluation_contract", "gig_id": gig_id, "fields": ["deterministic"]}),
        "completion": canonical_json_bytes({"schema_version": "1.0", "kind": "completion_evidence_contract", "gig_id": gig_id, "fields": ["proposal-validation"]}),
    }
    for name, data in contracts.items():
        (root / f"{name}.json").write_bytes(data)
    review_payload = parse_json_bytes(_review_contract("contract_00000000-0000-4000-8000-000000000099", "2026-09-08T00:00:00Z"))
    assert isinstance(review_payload, dict)
    review_payload["evaluator_plan"][0]["stage"] = "deterministic"
    review = canonical_json_bytes(review_payload)
    (root / "review.json").write_bytes(review)
    descriptors = []
    for index, (selector, alias) in enumerate((("career", "role"), ("stock", "market"))):
        graph = deepcopy(original)
        graph["graph_id"] = f"graph_00000000-0000-4000-8000-000000000{index + 10:03d}"
        graph["aggregate_budget"] = budget
        for goal in graph["goals"]:
            goal["budget"] = {"max_model_calls": 1, "max_tool_calls": 0, "max_tokens": 64, "max_cost": "0.25", "currency": "USD", "max_wall_time_ms": 60000, "max_parallel_goals": 1}
            old_ref = goal["contract"]
            old = workpad / old_ref["path"]
            destination = root / f"{selector}-{Path(old_ref['path']).name}"
            destination.write_bytes(old.read_bytes())
            goal["contract"] = _ref(destination.name, destination.read_bytes(), "text/markdown")
        graph_data = canonical_json_bytes(graph)
        graph_path = root / f"{selector}.json"
        graph_path.write_bytes(graph_data)
        descriptors.append({
            "graph_id": selector + suffix, "purpose": f"Structural {selector} fixture.", "aliases": [alias + suffix],
            "routing_summary": f"Explicit {selector} selection.", "goal_graph": _ref(graph_path.name, graph_data),
            "input_contract": _ref("input.json", contracts["input"]), "output_contract": _ref("output.json", contracts["output"]),
            "permitted_reference_contract": _ref("references.json", contracts["references"]),
            "effect_policy": ["write_workpad"], "capability_requirements": ["gigai.offline"],
            "provider_eligibility": {"providers": ["deterministic"]}, "budget": budget,
            "review_contract": _ref("review.json", review), "evaluation_contract": _ref("evaluation.json", contracts["evaluation"]),
            "completion_evidence_contract": _ref("completion.json", contracts["completion"]),
        })
    payload = {"gig_id": gig_id, "graphs": descriptors, "shared_policy": {"effects": ["write_workpad"], "required_capability_ids": ["gigai.offline"], "provider_eligibility": {"providers": ["deterministic"]}, "budget": budget}}
    path = root / "definition.json"
    path.write_bytes(canonical_json_bytes(payload))
    return path


def test_two_graph_propose_approve_plan_history_and_run(tmp_path: Path) -> None:
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(home_root=home, requested_target=target, name="two-graphs", open_editor=False)
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id)
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    proposed = propose_graph_set_offline(home_root=home, requested_target=target, gig_id=created.gig_id, definition_path=_write_definition(tmp_path / "definition-one", workpad, created.gig_id))
    approved = approve_offline(home_root=home, requested_target=target, proposal_id=proposed.proposal_id)
    assert approved.version == 2
    source = tmp_path / "source.md"
    source.write_text("explicit fixture input\n")
    with pytest.raises(RunPlanError, match="graph_selection_required"):
        create_run_plan(home_root=home, requested_target=target, gig_id=created.gig_id, input_paths=(source,))
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=created.gig_id, graph_selector="role", input_paths=(source,))
    assert plan.plan["selected_graph_id"] == "career"
    plan_bytes = (plan.workpad / "run-plans" / plan.run_plan_id / "run-plan.json").read_bytes()
    # A changed Graph Set must become a new version and cannot mutate the v2 Plan.
    proposed_next = propose_graph_set_offline(home_root=home, requested_target=target, gig_id=created.gig_id, definition_path=_write_definition(tmp_path / "definition-two", workpad, created.gig_id, suffix="2"))
    approved_next = approve_offline(home_root=home, requested_target=target, proposal_id=proposed_next.proposal_id)
    assert approved_next.version == 3
    reread = read_run_plan(home_root=home, requested_target=target, gig_id=created.gig_id, run_plan_id=plan.run_plan_id)
    assert (plan.workpad / "run-plans" / plan.run_plan_id / "run-plan.json").read_bytes() == plan_bytes
    run = launch_run(home_root=home, requested_target=target, gig_id=created.gig_id, run_plan_id=reread.run_plan_id, wait=True, operator_consent={"schema_version": "1.0", "kind": "operator_run_consent", "action": "run", "actor": {"kind": "operator", "id": "local-user"}, "source": "direct_cli_confirm"})
    assert run.status == "succeeded"


def test_v2_nested_contracts_and_version_aware_readers(tmp_path: Path) -> None:
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(home_root=home, requested_target=target, name="two-graph-readers", open_editor=False)
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id)
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    proposal = propose_graph_set_offline(home_root=home, requested_target=target, gig_id=created.gig_id, definition_path=_write_definition(tmp_path / "definition", workpad, created.gig_id))
    pending_listing = list_gigs(home_root=home, requested_target=target, all_projects=False)
    assert pending_listing.entries[0].version == "v1"
    assert pending_listing.entries[0].status == "Proposed"
    approve_offline(home_root=home, requested_target=target, proposal_id=proposal.proposal_id)
    source = tmp_path / "source.md"
    source.write_text("explicit fixture input\n")
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=created.gig_id, graph_selector="career", input_paths=(source,))

    mutations = (
        ("classification", lambda item: item.__setitem__("forged", True)),
        ("profile", lambda item: item.__setitem__("profile_id", "unbounded")),
        ("phases", lambda item: item[0].__setitem__("state", "forged")),
        ("participants", lambda item: item[0].__setitem__("roles", ["forged"])),
        ("inputs", lambda item: item[0].pop("snapshot_ref")),
        ("capabilities", lambda item: item.__setitem__("forged", True)),
    )
    for field, mutate in mutations:
        forged = deepcopy(plan.plan)
        mutate(forged[field])
        assert not validate_serialized_contract("run-plan-v2.schema.json", canonical_json_bytes(forged)).valid, field

    listing = list_gigs(home_root=home, requested_target=target, all_projects=False)
    assert listing.entries[0].title == "two-graph-readers"
    assert listing.entries[0].status == "Approved"
    assert listing.entries[0].version == "v2"
    assert not {"proposal_metadata_invalid", "active_version_metadata_invalid"}.intersection(
        diagnostic.code for diagnostic in listing.diagnostics
    )
    with pytest.raises(PortabilityError, match="inspection-only") as portability:
        verify_active_version_portability(workpad)
    assert portability.value.code == "unsupported_schema_version"

    run = launch_run(home_root=home, requested_target=target, gig_id=created.gig_id, run_plan_id=plan.run_plan_id, wait=True, operator_consent={"schema_version": "1.0", "kind": "operator_run_consent", "action": "run", "actor": {"kind": "operator", "id": "local-user"}, "source": "direct_cli_confirm"})
    manifest_path = workpad / "runs" / run.run_id / "run-manifest.json"
    manifest = parse_json_bytes(manifest_path.read_bytes())
    assert isinstance(manifest, dict)
    for mutate in (
        lambda item: item[0].__setitem__("forged", True),
        lambda item: item[0].pop("contract"),
        lambda item: item.append({"forged": True}),
    ):
        forged = deepcopy(manifest)
        mutate(forged["goal_contracts"])
        assert not validate_serialized_contract("run-manifest-v2.schema.json", canonical_json_bytes(forged)).valid
    assert _read_run(workpad, run.run_id)["selected_graph_id"] == "career"

    consent = {"scope": {"gig_id": created.gig_id, "project_id": next(iter(listing.entries)).project_id, "provider_review_requested": True}}
    consent_bytes = canonical_json_bytes(consent)
    consent_path = workpad / "runs" / run.run_id / "operator-consent.json"
    consent_path.write_bytes(consent_bytes)
    manifest["sealed_sources"].append(_ref(f"runs/{run.run_id}/operator-consent.json", consent_bytes))
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    assert _provider_review_active(
        SimpleNamespace(path=workpad, gig_id=created.gig_id, project_id=next(iter(listing.entries)).project_id),
        run.run_id,
        {"status": "preparing", "run_id": run.run_id},
    )
