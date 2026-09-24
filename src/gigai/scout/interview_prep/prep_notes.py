"""Prep notes: resume points to lead with, gaps to prepare an answer for.

Reuses assess's requirement x resume matrix (``find_jobs.contracts.
AssessmentResult.matrix``, ``RequirementMatrixRow``) for this posting when
one exists (``posting.find_assess_matrix``) -- packet CHANGE 2d. When no
matrix exists (posting acquired but never assessed), returns empty notes
with ``matrix_source=None`` rather than guessing from the resume text
directly; a later slice may add a standalone extraction, not this one.
"""

from __future__ import annotations

from ..find_jobs.contracts import AssessmentResult, MatrixStatus
from .types import PrepNotes


def build_prep_notes(matrix: AssessmentResult | None) -> PrepNotes:
    if matrix is None:
        return PrepNotes()
    resume_points: list[str] = []
    gaps: list[str] = []
    for row in matrix.matrix:
        if row.status == MatrixStatus.MET and row.resume_evidence:
            resume_points.append(f"{row.requirement}: {row.resume_evidence[0]}")
        elif row.status in (MatrixStatus.PARTIAL, MatrixStatus.GAP):
            gaps.append(row.requirement)
    return PrepNotes(resume_points=tuple(resume_points), gaps=tuple(gaps), matrix_source="assess")


__all__ = ["build_prep_notes"]
