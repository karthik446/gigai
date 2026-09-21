"""A replay must not bypass the public operation's selected wire protocol."""

from pathlib import Path

import pytest

from gigai import external_recording as recording
from tests.test_scout04_external_recording import _fixture as legacy_fixture
from tests.test_scout06_research_run_flow import _envelope, _started_v2_run


@pytest.mark.parametrize("operation", ["start", "checkpoint", "cancel"])
def test_v1_cannot_replay_an_existing_v2_operation(tmp_path: Path, operation: str):
    home, target, gig_id, plan, run = _started_v2_run(tmp_path)
    scope = {"home_root": home, "requested_target": target, "gig_id": gig_id}
    if operation == "start":
        request = _envelope("role-start", {"run_plan_id": plan["run_plan_id"]})
    elif operation == "checkpoint":
        request = _envelope("empty-checkpoint", {
            "run_id": run["run_id"], "parent_checkpoint": None,
            "questions": [], "artifact_refs": [], "reason": "still researching",
        })
        recording.checkpoint_v2(**scope, envelope=request)
    else:
        request = _envelope("cancel", {"run_id": run["run_id"], "reason": "operator stopped"})
        recording.cancel_v2(**scope, envelope=request)
    resolved = recording._resolved(**scope)
    before = recording._snapshot(resolved)
    with pytest.raises(recording.ExternalRecordingError) as refusal:
        getattr(recording, operation)(**scope, envelope=request)
    assert refusal.value.code == "external_protocol_downgrade"
    assert recording._snapshot(resolved) == before


@pytest.mark.parametrize("first_version", [1, 2])
def test_plan_replay_does_not_cross_protocols(tmp_path: Path, first_version: int):
    home, target, gig_id, _workpad, input_id = legacy_fixture(tmp_path)
    scope = {"home_root": home, "requested_target": target, "gig_id": gig_id}
    request = _envelope("shared-plan-key", {
        "graph_selector": "career", "gig_version": None, "selection_record": None,
        "input_refs": [{"family": "g45_run_input", "id": input_id}],
        "output_kinds": ["report"], "predecessor": None,
    })
    first, second = (recording.plan, recording.plan_v2) if first_version == 1 else (recording.plan_v2, recording.plan)
    result = first(**scope, envelope=request)
    assert result.created
    resolved = recording._resolved(**scope)
    before = recording._snapshot(resolved)
    with pytest.raises(recording.ExternalRecordingError) as refusal:
        second(**scope, envelope=request)
    assert refusal.value.code == "external_protocol_downgrade"
    assert recording._snapshot(resolved) == before
