"""Resolve a posting's text + assess matrix from find-jobs' own Run outputs.

Coordinator redirect (2026-09-24, ``orca orchestration ask``): this
packet's first slice keys prep to the **find-jobs identity**
(``normalized_url``), not ``application_events``' ``opportunity_ref`` --
those are two separate posting-identity systems in this codebase today
(the older D-family discovery graph's ``opportunity_id`` has no posting
body text at all; find-jobs' own acquire/assess Run has both the text and
the requirement matrix). Wiring ``application_events``/``interview_scheduled``
as an automatic trigger is follow-up debt (recorded in the worker report),
not this slice.

Posting text: read directly from the newest (or ``--run``-named) find-jobs
Run's committed acquire output, ``runs/<run_id>/outputs/acquire.json``
(plain file, the same non-journal artifact ``proposal_execution.py``'s
``_read_acquire_rows`` reads) -- reusing the public ``AcquireOutput``
contract to parse it, matching that reader's own shape.

Assess matrix reuse: scan ``records/scout-proposals/`` (journal-committed,
read-only) for an assessment revision whose ``posting.normalized_url``
matches -- the same store ``proposal_records.save_assessment_revision``
writes to. Best-effort: a posting acquired but not yet assessed still
builds a prep from posting text alone (per packet CHANGE 2d, "reusing
assess's requirement x resume matrix for that posting when it exists").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...canonical import parse_json_bytes
from ...journal import run_with_journal_writer
from ...workpad import ResolvedWorkpad
from ..find_jobs.contracts import AcquireOutput, AssessmentResult, FindJobsContractError, PostingRow, RowOutcome


class PostingUnavailableError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PostingInfo:
    __slots__ = ("posting", "run_id", "outcome", "matrix", "matrix_source")

    def __init__(self, *, posting: PostingRow, run_id: str, outcome: RowOutcome, matrix: AssessmentResult | None, matrix_source: str | None) -> None:
        self.posting = posting
        self.run_id = run_id
        self.outcome = outcome
        self.matrix = matrix
        self.matrix_source = matrix_source


def _acquire_path(root: Path, run_id: str) -> Path:
    return root / "runs" / run_id / "outputs" / "acquire.json"


def _load_acquire(root: Path, run_id: str) -> AcquireOutput:
    path = _acquire_path(root, run_id)
    if path.is_symlink() or not path.is_file():
        raise PostingUnavailableError("posting_run_not_found", f"find-jobs run {run_id!r} has no committed acquire output")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return AcquireOutput.from_json(payload)
    except (OSError, ValueError, FindJobsContractError) as exc:
        raise PostingUnavailableError("posting_run_invalid", f"find-jobs run {run_id!r} acquire output is invalid") from exc


def find_assess_matrix(*, resolved: ResolvedWorkpad, normalized_url: str) -> AssessmentResult | None:
    """Best-effort scan of committed assessment revisions for this posting's matrix."""

    def read(writer: Any) -> AssessmentResult | None:
        snapshot = writer.snapshot(("records/scout-proposals/",))
        best: tuple[str, AssessmentResult] | None = None
        for path, raw in snapshot.artifacts.items():
            if not (path.startswith("records/scout-proposals/") and path.endswith(".json")):
                continue
            try:
                value = parse_json_bytes(raw)
            except ValueError:
                continue
            if not isinstance(value, dict) or value.get("schema_version") != "scout-assessment-revision:1":
                continue
            if value.get("project_id") != resolved.project_id or value.get("gig_id") != resolved.gig_id:
                continue
            try:
                assessment = AssessmentResult.from_json(value.get("assessment"))
            except FindJobsContractError:
                continue
            if assessment.posting.normalized_url != normalized_url:
                continue
            created_at = str(value.get("created_at", ""))
            if best is None or created_at > best[0]:
                best = (created_at, assessment)
        return best[1] if best is not None else None

    return run_with_journal_writer(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=read,
    )


def resolve_posting(*, resolved: ResolvedWorkpad, normalized_url: str, run_id: str | None) -> PostingInfo:
    """Resolve one posting by its acquire-normalized URL from a find-jobs Run's acquire output.

    ``run_id=None`` selects the newest Run (by acquire-output mtime) that
    contains this posting. Raises :class:`PostingUnavailableError` when no
    matching posting is found -- this module never guesses posting content.
    """

    root = resolved.path
    run_ids = [run_id] if run_id is not None else _candidate_run_ids(root)
    if not run_ids:
        raise PostingUnavailableError("posting_no_runs", "no find-jobs run with acquire output was found")
    for candidate_run_id in run_ids:
        acquire = _load_acquire(root, candidate_run_id)
        for row in acquire.rows:
            if row.posting.normalized_url == normalized_url:
                matrix = find_assess_matrix(resolved=resolved, normalized_url=normalized_url)
                return PostingInfo(
                    posting=row.posting, run_id=candidate_run_id, outcome=row.outcome,
                    matrix=matrix, matrix_source="assess" if matrix is not None else None,
                )
    raise PostingUnavailableError(
        "posting_not_found",
        f"posting {normalized_url!r} was not found in {'run ' + run_id if run_id else 'any find-jobs run'}'s acquire output",
    )


def _candidate_run_ids(root: Path) -> list[str]:
    runs_dir = root / "runs"
    if not runs_dir.is_dir():
        return []
    candidates = [item for item in runs_dir.iterdir() if item.is_dir() and _acquire_path(root, item.name).is_file()]
    candidates.sort(key=lambda item: _acquire_path(root, item.name).stat().st_mtime, reverse=True)
    return [item.name for item in candidates]


__all__ = ["PostingInfo", "PostingUnavailableError", "find_assess_matrix", "resolve_posting"]
