"""Coordinator-owned compatibility checks, independent of Scout implementation.

These digests were captured before SCOUT-02 implementation. New schema resources
may be added, but changing an inventory alongside an old schema must not erase
the compatibility assertion.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from importlib import resources
from pathlib import Path

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.graph_set import validate_graph_set, validate_selection_record
from gigai.lifecycle import approve_offline, create_offline
from gigai.run_plan import _identity_projection, create_run_plan, read_run_plan
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_serialized_contract


LEGACY_SCHEMA_DIGESTS = {
    "gig-proposal.schema.json": "515f16368059c7d8d4bf88cb47d8fc0df63afc50a51e13c8c75601c013f134b3",
    "active-gig-version.schema.json": "634af1729f4dbfe1f8564fd9d31b6c5cb8c56a2e1c7b80393e59ae877f76f5e1",
    "run-plan.schema.json": "0c3ba1cc9c6095e0468dc3b7476878d1ee55bf579b0ddc8fd46b1f7d82d3b1cb",
    "run-manifest.schema.json": "a14126ac4943e71980371eb215fbc191434cfb0fb2f2761259a0faabb36af24f",
    "run-details.schema.json": "c2388d917e08cfcc0860ecd3a20b389be4f434aadde6b21ffa18ee4d6457111f",
    "handoff-frontmatter.schema.json": "126e608ed9e9bfdcf2fb7fad1514005f8ca886fdfaf30522d93234a02d3a8247",
}


@pytest.mark.parametrize("name,expected", LEGACY_SCHEMA_DIGESTS.items())
def test_pre_scout_strict_schema_bytes_are_unchanged(name: str, expected: str) -> None:
    payload = resources.files("gigai.schemas").joinpath(name).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == expected


def _ref(path: str) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": "sha256:" + "a" * 64,
        "size_bytes": 2,
        "media_type": "application/json",
    }


def test_agent_selection_schema_accepts_an_agent_not_a_forged_operator() -> None:
    """The legacy common actor enum must not force agents to impersonate users."""
    selection = {
        "schema_version": "1.0",
        "selection_record_id": "graph_selection_00000000-0000-4000-8000-000000000001",
        "gig_id": "gig_00000000-0000-4000-8000-000000000002",
        "gig_version": 1,
        "graph_set": _ref("manifests/graph-set.json"),
        "selected_graph_id": "research-role",
        "selected_graph": _ref("manifests/research-role.json"),
        "selection_kind": "agent_explicit",
        "selector": {
            "kind": "agent",
            "actor": {"kind": "agent", "id": "codex"},
            "rule_id": None,
            "rule_version": None,
            "invocation_ref": _ref("invocations/selection.json"),
        },
        "selection_reason": "Explicitly requested role research.",
        "routing_evidence_refs": [],
        "created_at": "2026-09-08T17:00:00Z",
    }
    report = validate_serialized_contract(
        "graph-selection-record-v2.schema.json", canonical_json_bytes(selection)
    )
    assert report.valid, report.findings

    selection["operator_consent"] = {"source": "direct_cli_confirm"}
    report = validate_serialized_contract(
        "graph-selection-record-v2.schema.json", canonical_json_bytes(selection)
    )
    assert not report.valid


def _graph_set_policy_fixture() -> dict[str, object]:
    budget = {
        "max_model_calls": 4,
        "max_tool_calls": 0,
        "max_tokens": 4000,
        "max_cost": "10.00",
        "currency": "USD",
        "max_wall_time_ms": 300000,
        "max_parallel_goals": 1,
    }
    descriptor = {
        "graph_id": "research-role",
        "purpose": "Structural policy fixture, not executed research.",
        "aliases": ["research"],
        "routing_summary": "Explicit role research selection.",
        "goal_graph": _ref("manifests/research-role.json"),
        "input_contract": _ref("manifests/input.json"),
        "output_contract": _ref("manifests/output.json"),
        "permitted_reference_contract": _ref("manifests/references.json"),
        "effect_policy": ["write_workpad"],
        "capability_requirements": ["gigai.offline"],
        "provider_eligibility": {"providers": ["deterministic"]},
        "budget": deepcopy(budget),
        "review_contract": _ref("manifests/review.json"),
        "evaluation_contract": _ref("manifests/evaluation.json"),
        "completion_evidence_contract": _ref("manifests/completion.json"),
    }
    return {
        "schema_version": "1.0",
        "graph_set_id": "graph_set_00000000-0000-4000-8000-000000000001",
        "gig_id": "gig_00000000-0000-4000-8000-000000000002",
        "graphs": [descriptor],
        "shared_policy": {
            "effects": ["write_workpad"],
            "required_capability_ids": ["gigai.offline"],
            "provider_eligibility": {"providers": ["deterministic"]},
            "budget": budget,
        },
        "created_at": "2026-09-08T17:00:00Z",
        "created_by": {"kind": "operator", "id": "local-user"},
    }


@pytest.mark.parametrize("cost", ["10.01", "100.00", None])
def test_graph_cost_cannot_widen_shared_ceiling(cost: str | None) -> None:
    graph_set = _graph_set_policy_fixture()
    baseline = validate_graph_set(canonical_json_bytes(graph_set))
    assert baseline.valid, baseline.findings
    graph_set["graphs"][0]["budget"]["max_cost"] = cost
    report = validate_graph_set(canonical_json_bytes(graph_set))
    assert not report.valid
    assert any(finding.code == "graph_policy_widened" for finding in report.findings)


def test_graph_cost_comparison_is_numeric_not_lexical() -> None:
    graph_set = _graph_set_policy_fixture()
    graph_set["graphs"][0]["budget"]["max_cost"] = "9.00"
    report = validate_graph_set(canonical_json_bytes(graph_set))
    assert report.valid, report.findings


def _operator_selection(graph_set: dict[str, object]) -> dict[str, object]:
    graph_set_data = canonical_json_bytes(graph_set)
    reference = _ref("manifests/graph-set.json")
    reference["content_sha256"] = digest_imported_bytes(graph_set_data)
    reference["size_bytes"] = len(graph_set_data)
    return {
        "schema_version": "1.0",
        "selection_record_id": "graph_selection_00000000-0000-4000-8000-000000000003",
        "gig_id": graph_set["gig_id"],
        "gig_version": 1,
        "graph_set": reference,
        "selected_graph_id": "research-role",
        "selected_graph": deepcopy(graph_set["graphs"][0]["goal_graph"]),
        "selection_kind": "operator_explicit",
        "selector": {
            "kind": "operator",
            "actor": {"kind": "operator", "id": "local-user"},
            "rule_id": None,
            "rule_version": None,
        },
        "selection_reason": "Explicit fixture selection.",
        "routing_evidence_refs": [],
        "created_at": "2026-09-08T17:00:00Z",
    }


def _selection_report(selection: dict[str, object], graph_set: dict[str, object]):
    return validate_selection_record(
        canonical_json_bytes(selection),
        graph_set=graph_set,
        gig_id=graph_set["gig_id"],
        gig_version=1,
    )


def test_persisted_selection_uses_canonical_selector_not_alias() -> None:
    graph_set = _graph_set_policy_fixture()
    selection = _operator_selection(graph_set)
    baseline = _selection_report(selection, graph_set)
    assert baseline.valid, baseline.findings
    selection["selected_graph_id"] = "research"
    assert not _selection_report(selection, graph_set).valid


@pytest.mark.parametrize("actor", [None, {"kind": "operator", "id": "local-user"}])
def test_only_member_selection_requires_deterministic_gigai_actor(actor) -> None:
    graph_set = _graph_set_policy_fixture()
    selection = _operator_selection(graph_set)
    selection["selection_kind"] = "only_member_default"
    selection["selector"]["kind"] = "gigai_deterministic"
    selection["selector"]["actor"] = {"kind": "gigai", "id": "graph-selector"}
    baseline = _selection_report(selection, graph_set)
    assert baseline.valid, baseline.findings
    selection["selector"]["actor"] = actor
    assert not _selection_report(selection, graph_set).valid


def test_legacy_plan_identity_has_no_new_empty_graph_fields(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    ))
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(
        home_root=home, requested_target=target,
        name="legacy-identity-check", open_editor=False,
    )
    approve_offline(
        home_root=home, requested_target=target, proposal_id=created.proposal_id,
    )
    source = tmp_path / "input.md"
    source.write_text("Explicit local fixture input.\n", encoding="utf-8")
    plan = create_run_plan(
        home_root=home, requested_target=target, gig_id=created.gig_id,
        input_paths=(source,),
    )
    assert plan.plan["plan_version"] == 1
    projection = _identity_projection(plan.plan)
    assert not {
        "graph_set_sha256", "selected_graph_id", "selection_sha256",
        "selected_graph_sha256", "selection_record_sha256",
    }.intersection(projection)
    original_bytes = (plan.workpad / "run-plans" / plan.run_plan_id / "run-plan.json").read_bytes()
    reread = read_run_plan(
        home_root=home, requested_target=target, gig_id=created.gig_id,
        run_plan_id=plan.run_plan_id,
    )
    assert reread.content_sha256 == digest_imported_bytes(original_bytes)
    assert (plan.workpad / "run-plans" / plan.run_plan_id / "run-plan.json").read_bytes() == original_bytes
