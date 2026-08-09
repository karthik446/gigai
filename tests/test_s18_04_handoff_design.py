from __future__ import annotations

import ast
from pathlib import Path

from research.s18_04.handoff import Artifact, GoalEdge, compare_artifacts, send_handoff


def _artifact(artifact_id: str, goal_id: str, text: str, *, cost_status: str = "unavailable") -> Artifact:
    return Artifact(artifact_id, goal_id, text, None, "fixture-provider", {"input_tokens": 2, "output_tokens": 3}, cost_status)


def test_goal_edge_handoff_preserves_parentage_and_usage_cost() -> None:
    edge = GoalEdge("edge-1", "goal-source", "goal-receiver", max_handoffs=1)
    source = _artifact("artifact-source", "goal-source", "review output")
    result = send_handoff(edge, source, handoff_index=1, receiver_available=True)
    assert result.status == "received"
    assert result.received_artifact is not None
    assert result.received_artifact.goal_id == "goal-receiver"
    assert result.received_artifact.parent_artifact_id == "artifact-source"
    assert result.received_artifact.usage == source.usage
    assert result.received_artifact.cost_status == "unavailable"


def test_disagreement_keeps_both_outputs_and_creates_adjudication_input() -> None:
    left = _artifact("left", "goal-left", "finding A", cost_status="provider_reported")
    right = _artifact("right", "goal-right", "finding B")
    result = compare_artifacts(left, right)
    assert result.status == "disagreement"
    assert result.independent_artifact_ids == ("left", "right")
    assert result.adjudication_input["selected_winner"] is None
    assert result.adjudication_input["left_cost_status"] == "provider_reported"


def test_cancellation_and_unavailable_receiver_are_terminal_without_fallback() -> None:
    edge = GoalEdge("edge-1", "goal-source", "goal-receiver")
    source = _artifact("source", "goal-source", "output")
    cancelled = send_handoff(edge, source, handoff_index=1, receiver_available=True, cancelled=True)
    unavailable = send_handoff(edge, source, handoff_index=1, receiver_available=False, fallback_requested=True)
    assert cancelled.status == "cancelled"
    assert cancelled.received_artifact is None
    assert unavailable.status == "unavailable"
    assert unavailable.reason == "receiver_unavailable_no_fallback"
    assert unavailable.received_artifact is None
    assert unavailable.fallback_requested is True


def test_handoff_limit_and_parent_mismatch_fail_closed() -> None:
    edge = GoalEdge("edge-1", "goal-source", "goal-receiver", max_handoffs=1)
    source = _artifact("source", "goal-source", "output")
    assert send_handoff(edge, source, handoff_index=2, receiver_available=True).reason == "handoff_limit_exhausted"
    wrong_parent = _artifact("wrong", "other-goal", "output")
    assert send_handoff(edge, wrong_parent, handoff_index=1, receiver_available=True).reason == "source_parent_mismatch"


def test_comparison_requires_independent_artifacts() -> None:
    artifact = _artifact("same", "goal", "output")
    try:
        compare_artifacts(artifact, artifact)
    except ValueError as error:
        assert "independent" in str(error)
    else:
        raise AssertionError("same-artifact comparison was not rejected")


def test_research_design_has_no_effectful_imports_or_fallback_implementation() -> None:
    source = Path(__file__).parents[1].joinpath("research/s18_04/handoff.py").read_text()
    tree = ast.parse(source)
    imported = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not imported.intersection({"socket", "subprocess", "httpx", "urllib", "requests"})
