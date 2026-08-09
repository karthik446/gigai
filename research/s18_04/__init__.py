"""Offline S18-04 handoff and comparison design helpers."""

from .handoff import (
    Artifact,
    ComparisonResult,
    GoalEdge,
    HandoffResult,
    compare_artifacts,
    send_handoff,
)

__all__ = ["Artifact", "ComparisonResult", "GoalEdge", "HandoffResult", "compare_artifacts", "send_handoff"]
