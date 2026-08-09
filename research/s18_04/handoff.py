"""Deterministic S18-04 handoff/comparison state evidence.

No provider, process, network, credential, or target operation occurs here.
The module models only artifact and Goal-edge decisions for the spike.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


VALID_COST_STATUS = {"provider_reported", "derived", "unavailable"}


@dataclass(frozen=True)
class GoalEdge:
    edge_id: str
    source_goal_id: str
    target_goal_id: str
    max_handoffs: int = 1

    def __post_init__(self) -> None:
        if not self.edge_id or not self.source_goal_id or not self.target_goal_id:
            raise ValueError("Goal edge identity is required")
        if self.max_handoffs <= 0:
            raise ValueError("Goal edge max_handoffs must be positive")


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    goal_id: str
    output_text: str
    parent_artifact_id: str | None
    provider_family: str
    usage: Mapping[str, object]
    cost_status: str

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.goal_id or not self.output_text:
            raise ValueError("artifact identity, owner, and output are required")
        if self.cost_status not in VALID_COST_STATUS:
            raise ValueError("invalid cost status")


@dataclass(frozen=True)
class HandoffResult:
    status: str
    reason: str
    edge_id: str
    handoff_index: int
    received_artifact: Artifact | None
    fallback_requested: bool


@dataclass(frozen=True)
class ComparisonResult:
    status: str
    independent_artifact_ids: tuple[str, ...]
    adjudication_input: Mapping[str, object] | None


def send_handoff(
    edge: GoalEdge,
    source_artifact: Artifact,
    *,
    handoff_index: int,
    receiver_available: bool,
    cancelled: bool = False,
    fallback_requested: bool = False,
) -> HandoffResult:
    """Decide one bounded handoff; never retries, races, or invokes a provider."""

    if source_artifact.goal_id != edge.source_goal_id:
        return HandoffResult("blocked", "source_parent_mismatch", edge.edge_id, handoff_index, None, False)
    if handoff_index < 1 or handoff_index > edge.max_handoffs:
        return HandoffResult("blocked", "handoff_limit_exhausted", edge.edge_id, handoff_index, None, False)
    if cancelled:
        return HandoffResult("cancelled", "operator_cancelled", edge.edge_id, handoff_index, None, False)
    if not receiver_available:
        return HandoffResult(
            "unavailable",
            "receiver_unavailable_no_fallback",
            edge.edge_id,
            handoff_index,
            None,
            fallback_requested,
        )
    received = Artifact(
        artifact_id=f"{edge.edge_id}-received-{handoff_index}",
        goal_id=edge.target_goal_id,
        output_text=source_artifact.output_text,
        parent_artifact_id=source_artifact.artifact_id,
        provider_family=source_artifact.provider_family,
        usage=dict(source_artifact.usage),
        cost_status=source_artifact.cost_status,
    )
    return HandoffResult("received", "handoff_recorded", edge.edge_id, handoff_index, received, False)


def compare_artifacts(left: Artifact, right: Artifact) -> ComparisonResult:
    """Preserve independent outputs and surface disagreement for adjudication."""

    if left.artifact_id == right.artifact_id:
        raise ValueError("comparison requires independent artifact IDs")
    independent = (left.artifact_id, right.artifact_id)
    if left.output_text == right.output_text:
        return ComparisonResult("agreement", independent, None)
    return ComparisonResult(
        "disagreement",
        independent,
        {
            "requires_human_adjudication": True,
            "independent_artifact_ids": independent,
            "selected_winner": None,
            "left_usage": dict(left.usage),
            "right_usage": dict(right.usage),
            "left_cost_status": left.cost_status,
            "right_cost_status": right.cost_status,
        },
    )


__all__ = ["Artifact", "ComparisonResult", "GoalEdge", "HandoffResult", "compare_artifacts", "send_handoff"]
