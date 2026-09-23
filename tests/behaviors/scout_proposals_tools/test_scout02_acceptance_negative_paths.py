"""Coordinator checks of public refusal paths after independent worker handoff."""

from pathlib import Path
import subprocess

import pytest

from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.graph_set import validate_selection_record
from gigai.lifecycle import LifecycleError, approve_offline, create_offline, propose_graph_set_offline
from gigai.run import RunError, launch_run
from gigai.run_plan import RunPlanError, create_run_plan
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from tests.behaviors.scout_proposals_tools.test_scout02_graph_set_flow import _ref, _write_definition
from tests.behaviors.scout_proposals_tools.test_scout02_review_corrections import _invocation, _selection


def _workspace(tmp_path: Path):
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(home_root=home, requested_target=target, name="negative-paths", open_editor=False)
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id)
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    return home, target, created, workpad


def _authority(workpad: Path):
    commit = subprocess.run(["git", "-C", str(workpad), "rev-parse", "HEAD"], capture_output=True, check=True).stdout
    return (
        commit,
        (workpad / "manifests/gig-proposal.json").read_bytes(),
        (workpad / "manifests/active-gig-version.json").read_bytes(),
        sorted(path.name for path in (workpad / "runs").glob("*")),
    )


@pytest.mark.parametrize("defect", ["tampered_member", "missing_member", "foreign_member", "alias_collision", "widened_budget", "widened_actual_graph", "traversal", "symlink"])
def test_invalid_graph_bundle_does_not_change_approved_authority(tmp_path: Path, defect: str) -> None:
    home, target, created, workpad = _workspace(tmp_path)
    definition = _write_definition(tmp_path / "bundle", workpad, created.gig_id)
    payload = parse_json_bytes(definition.read_bytes())
    member = definition.parent / "career.json"
    if defect == "tampered_member":
        member.write_bytes(member.read_bytes() + b"\n")
    elif defect == "missing_member":
        member.rename(definition.parent / "not-the-member.json")
    elif defect in {"foreign_member", "widened_actual_graph"}:
        graph = parse_json_bytes(member.read_bytes())
        if defect == "foreign_member":
            graph["gig_id"] = "gig_00000000-0000-4000-8000-000000000099"
        else:
            graph["aggregate_budget"]["max_model_calls"] = 3
        member.write_bytes(canonical_json_bytes(graph))
        payload["graphs"][0]["goal_graph"] = _ref(member.name, member.read_bytes())
    elif defect == "alias_collision":
        payload["graphs"][1]["aliases"] = ["role"]
    elif defect == "widened_budget":
        payload["graphs"][0]["budget"]["max_model_calls"] = 3
    elif defect == "traversal":
        payload["graphs"][0]["goal_graph"]["path"] = "../career.json"
    else:
        actual = definition.parent / "actual-career.json"
        member.rename(actual)
        member.symlink_to(actual)
    definition.write_bytes(canonical_json_bytes(payload))
    before = _authority(workpad)
    with pytest.raises(LifecycleError):
        propose_graph_set_offline(home_root=home, requested_target=target, gig_id=created.gig_id, definition_path=definition)
    assert _authority(workpad) == before


def test_missing_unknown_selector_and_no_consent_allocate_no_run(tmp_path: Path) -> None:
    home, target, created, workpad = _workspace(tmp_path)
    proposed = propose_graph_set_offline(home_root=home, requested_target=target, gig_id=created.gig_id, definition_path=_write_definition(tmp_path / "bundle", workpad, created.gig_id))
    approve_offline(home_root=home, requested_target=target, proposal_id=proposed.proposal_id)
    source = tmp_path / "input.md"
    source.write_text("explicit input\n")
    before = _authority(workpad)
    for selector in (None, "unknown-graph"):
        with pytest.raises(RunPlanError):
            create_run_plan(home_root=home, requested_target=target, gig_id=created.gig_id, graph_selector=selector, input_paths=(source,))
        assert _authority(workpad) == before
    plan = create_run_plan(home_root=home, requested_target=target, gig_id=created.gig_id, graph_selector="career", input_paths=(source,))
    before_run = _authority(workpad)
    with pytest.raises(RunError, match="consent"):
        launch_run(home_root=home, requested_target=target, gig_id=created.gig_id, run_plan_id=plan.run_plan_id, wait=True)
    assert _authority(workpad) == before_run


def test_valid_but_unjournaled_invocation_is_rejected(tmp_path: Path) -> None:
    payload = _invocation()
    path = tmp_path / "agent-invocations/inv_agent-selection.json"
    path.parent.mkdir()
    path.write_bytes(payload)
    graph_set, selection = _selection(tmp_path, payload)
    report = validate_selection_record(selection, graph_set=graph_set, gig_id="gig_00000000-0000-4000-8000-000000000001", gig_version=2, root=tmp_path)
    assert {finding.code for finding in report.findings} == {"agent_invocation_unjournaled"}


@pytest.mark.parametrize("field", ["gig_id", "gig_version", "graph_set", "selected_graph"])
def test_selection_cannot_substitute_foreign_authority(tmp_path: Path, field: str) -> None:
    graph_set, selection = _selection(tmp_path, _invocation())
    record = parse_json_bytes(selection)
    if field == "gig_id":
        record[field] = "gig_00000000-0000-4000-8000-000000000099"
    elif field == "gig_version":
        record[field] = 3
    else:
        record[field]["content_sha256"] = "sha256:" + "0" * 64
    report = validate_selection_record(canonical_json_bytes(record), graph_set=graph_set, gig_id="gig_00000000-0000-4000-8000-000000000001", gig_version=2)
    assert "graph_selection_mismatch" in {finding.code for finding in report.findings}


def test_both_graphs_run_independently_in_one_approved_gig(tmp_path: Path) -> None:
    home, target, created, workpad = _workspace(tmp_path)
    proposed = propose_graph_set_offline(home_root=home, requested_target=target, gig_id=created.gig_id, definition_path=_write_definition(tmp_path / "bundle", workpad, created.gig_id))
    approve_offline(home_root=home, requested_target=target, proposal_id=proposed.proposal_id)
    runs = []
    for selector in ("career", "stock"):
        source = tmp_path / f"{selector}-input.md"
        source.write_text(f"explicit {selector} fixture input\n")
        plan = create_run_plan(home_root=home, requested_target=target, gig_id=created.gig_id, graph_selector=selector, input_paths=(source,))
        assert plan.plan["selected_graph_id"] == selector
        run = launch_run(home_root=home, requested_target=target, gig_id=created.gig_id, run_plan_id=plan.run_plan_id, wait=True, operator_consent={"schema_version": "1.0", "kind": "operator_run_consent", "action": "run", "actor": {"kind": "operator", "id": "local-user"}, "source": "direct_cli_confirm"})
        assert run.status == "succeeded"
        manifest = parse_json_bytes((workpad / "runs" / run.run_id / "run-manifest.json").read_bytes())
        assert manifest["selected_graph_id"] == selector
        assert manifest["gig_id"] == created.gig_id
        assert manifest["gig_version"] == 2
        runs.append(run.run_id)
    assert len(set(runs)) == 2
