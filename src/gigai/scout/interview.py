"""Public Scout interview service facade.

The durable implementation lives in :mod:`scout_interview_records`; this
module is the stable domain import used by callers and future CLI registration.
"""

from __future__ import annotations

from dataclasses import dataclass

from .interview_records import (
    InterviewResult,
    ScoutInterviewError,
    list_interview_preparations,
    prepare_interview,
    read_interview_preparation,
    revise_interview,
    save_interview_feedback,
)


@dataclass(frozen=True)
class InterviewGraph:
    selector: str = "prepare-interview"
    required_inputs: tuple[str, ...] = ("role", "stage_or_format")
    optional_inputs: tuple[str, ...] = ("posting", "research_revisions", "candidate_evidence", "prior_feedback")
    effects: tuple[str, ...] = ("write_workpad",)


INTERVIEW_GRAPH = InterviewGraph()


__all__ = [
    "INTERVIEW_GRAPH", "InterviewGraph", "InterviewResult", "ScoutInterviewError",
    "list_interview_preparations", "prepare_interview", "read_interview_preparation",
    "revise_interview", "save_interview_feedback",
]
