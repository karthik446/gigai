"""run-reads-fast (uat-bug-022): a run's reads, sized for the page that asks.

After a full-catalog run (500 imported postings) the Jobs page stayed empty
for about a minute: ``GET /api/runs/{run_id}/results`` answered every row
with its posting text (3.9 MB) and scored the rows with Jev on the way, and
``.../progress`` carried the same text again (3.7 MB). The no-query form of
both routes is unchanged (``runs.py``); the grid and the job page read
these instead:

* ``GET /api/runs/{run_id}/results?limit=N[&offset=M]`` (N 1..500): one page
  of the run's rows in the grid's own order, in the no-query response's
  shape plus ``total``/``limit``/``offset``, the run's ``counts`` and its
  ``created_at`` (what the grid's header took from ``GET /api/runs``).
  ``payload.rows``, ``payload.assessments``, ``payload.not_assessed`` and
  ``carried_forward_assessments`` hold the page's postings only, and no
  posting carries its ``text``. Each row has ``rank_score``: the Jev score
  already stored for it, or ``null``; there is no ``rank_scores`` list, and
  this read never calls Jev. ``resume_label``/``resume_created_at`` are
  left out: nothing on the page shows them, and resolving them is the
  slowest read a page would make after a run (0.8 s, 114 git subprocesses).
* ``GET /api/runs/{run_id}/posting?url=<normalized_url>``: one posting of
  the run, complete: its text, its assessment (or why it has none) and the
  same row fields as the grid's.
* ``GET /api/runs/{run_id}/progress?summary=1``: the progress response with
  no posting text and no ``boards.skipped_boards`` (the name of every board
  the run did not read, 10,000 names at catalog size; ``boards.skipped`` is
  their count).

Rows are ordered as the grid sorts them (``jobModel.sortJobs``): verdict
group, then the stored Jev score, then the newest posting. The first page
is then the top of the grid, not the first rows acquire happened to list.
The verdict is the run's own (or the one it carried forward); an
assessment made later from a job page is the UI's to merge, as before.

Everything comes from ``projection.read_run_evidence``: one committed
snapshot of the run's directory, kept while the run is finished. What a
page joins its rows to (the selected profile, the application events) is
read once per journal head, like the server's other per-head reads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

from ..contracts import PostingRow, RowOutcome, Verdict
from ..jev_contracts import RankScore
from .server import _logger

RESULTS_PAGE_LIMIT_MAX = 500
# What a read with no ``limit`` is given. The dispatch sends the no-query
# read to ``runs.py`` today, so nothing reaches this yet.
RESULTS_PAGE_LIMIT_DEFAULT = 100
POSTING_RESPONSE_SCHEMA = "scout-find-jobs-run-posting:1"

# jobModel.js VERDICT_ORDER: matched, needs answers, assessed (no verdict),
# not assessed, not a match.
_VERDICT_GROUP = {
    Verdict.MATCHED_ABOVE_THRESHOLD.value: 0,
    Verdict.PENDING_USER_ANSWERS.value: 1,
    "assessed": 2,
    "not_assessed": 3,
    Verdict.NOT_A_MATCH.value: 4,
}


_JOINS_CACHE_LOCK = threading.Lock()
# workpad -> what its rows join to at the journal head it was read at. One
# entry per workpad: a new head replaces the one before it.
_joins_cache: "dict[str, RowJoins]" = {}
# (workpad, record_id, revision_id) -> that resume revision's text. A
# revision's content never changes, so it is read once, not once per head.
_resume_text_cache: dict[tuple[str, str, str], str] = {}


@dataclass(frozen=True)
class RowJoins:
    """What a run's rows are joined to, read at one journal head.

    ``profile`` is the gig's selected profile (``None``: no scores, no job
    state) and ``resume_text`` its resume, which a stored Jev score is filed
    under; ``events`` is every application event by posting identity, or
    ``None`` when the job-state read is not available.
    """

    head: str | None
    profile: object | None
    resume_text: str | None
    events: Mapping[str, list[Mapping[str, object]]] | None


def row_joins(backend, resolved) -> RowJoins:
    from .... import run

    key = str(resolved.path)
    head = run._cheap_workpad_head(resolved.path)
    if head is not None:
        with _JOINS_CACHE_LOCK:
            kept = _joins_cache.get(key)
        if kept is not None and kept.head == head:
            return kept
    try:
        profile = backend._selected_profile()
    except Exception:  # noqa: BLE001 - display-only: no profile means no scores and no job state
        _logger.exception("the selected profile could not be read")
        profile = None
    events = None
    resume_text = None
    if profile is not None:
        resume_text = _resume_text(backend, resolved, profile)
        try:
            from ..job_state import read_application_events

            events = read_application_events(resolved)
        except ImportError:
            events = None
        except Exception:  # noqa: BLE001 - display-only enrichment must never break the read
            _logger.exception("application events could not be read")
    # Reading the profile can commit (its first migration), so the head the
    # joins are filed under is the one after the reads; a head that moved
    # while they ran files nothing.
    after = run._cheap_workpad_head(resolved.path)
    joins = RowJoins(after, profile, resume_text, events)
    if after is not None and after == head:
        with _JOINS_CACHE_LOCK:
            _joins_cache[key] = joins
    return joins


def _resume_text(backend, resolved, profile) -> str | None:
    from .... import private_records

    key = (str(resolved.path), profile.resume_ref.record_id, profile.resume_ref.revision_id)
    with _JOINS_CACHE_LOCK:
        kept = _resume_text_cache.get(key)
    if kept is not None:
        return kept
    try:
        record = private_records.read_record(
            home_root=backend.home_root,
            requested_target=backend.target,
            record_id=profile.resume_ref.record_id,
            revision_id=profile.resume_ref.revision_id,
            content=True,
            gig_id=resolved.gig_id,
        )
    except Exception as exc:  # noqa: BLE001 - display-only: the type is logged, never the resume
        _logger.warning("the profile's resume could not be read for its stored Jev scores: %s", type(exc).__name__)
        return None
    content = record.get("content")
    if not isinstance(content, bytes):
        return None
    text = content.decode("utf-8", errors="replace")
    with _JOINS_CACHE_LOCK:
        _resume_text_cache[key] = text
    return text


def grid_posting(posting: Mapping[str, object]) -> dict[str, object]:
    """A posting as the grid shows it: every field but its ``text``."""

    return {key: value for key, value in posting.items() if key != "text"}


def run_counts(evidence) -> dict[str, int]:
    """``found``/``new``/``assessed``/``matched``, counted from the run's sealed outputs."""

    rows = evidence.acquire_output.rows if evidence.acquire_output is not None else ()
    assessments = evidence.assess_output.assessments if evidence.assess_output is not None else ()
    return {
        "found": len(rows),
        "new": sum(1 for row in rows if row.outcome == RowOutcome.NEW),
        "assessed": len(assessments),
        "matched": sum(1 for item in assessments if item.verdict == Verdict.MATCHED_ABOVE_THRESHOLD),
    }


def stored_rank_scores(
    rows: Sequence[PostingRow], *, evidence, joins: RowJoins, home_root: Path, target: Path
) -> dict[str, RankScore]:
    """The Jev score already stored for each of ``rows``, by ``normalized_url``.

    Read from the score cache (``jev_rank.read_cached_scores``: what a run's
    ranking pass and an explicit ``POST /rank`` paid for) for the selected
    profile's resume, then from the run's own sealed scores when the run
    used that same profile and resume revision. A row with neither has no
    entry. Nothing here asks Jev.
    """

    profile = joins.profile
    if profile is None:
        return {}
    revision_id = profile.resume_ref.revision_id
    scores: dict[str, RankScore] = {}
    if joins.resume_text:
        try:
            from ..jev_rank import read_cached_scores
        except ImportError:  # the cache has no page reader yet: the sealed scores below
            read_cached_scores = None
        if read_cached_scores is not None:
            try:
                cached = read_cached_scores(
                    rows,
                    resume_text=joins.resume_text,
                    profile_id=profile.profile_id,
                    resume_revision_id=revision_id,
                    home_root=home_root,
                    target=target,
                )
                scores = {item.normalized_url: item for item in cached if item.score is not None}
            except Exception:  # noqa: BLE001 - display-only: a page without scores is still the page
                _logger.exception("stored Jev scores could not be read")

    run_input = evidence.run_input
    acquire = evidence.acquire_output
    if run_input is None or acquire is None or not acquire.rank_scores:
        return scores
    pinned = run_input.pinned_resume
    same_resume = pinned is not None and pinned.revision_id == revision_id
    same_profile = run_input.profile_ref is None or run_input.profile_ref.profile_id == profile.profile_id
    if same_resume and same_profile:
        wanted = {row.normalized_url for row in rows}
        for item in acquire.rank_scores:
            if item.score is not None and item.normalized_url in wanted and item.normalized_url not in scores:
                scores[item.normalized_url] = item
    return scores


class RunView:
    """One run's rows, in the grid's order, with what each row joins to."""

    def __init__(self, evidence, *, scores: Mapping[str, RankScore]) -> None:
        self.evidence = evidence
        acquire = evidence.acquire_output
        assess = evidence.assess_output
        self.assessments = {item.posting.normalized_url: item for item in (assess.assessments if assess is not None else ())}
        self.not_assessed = {item.posting.normalized_url: item for item in (assess.not_assessed if assess is not None else ())}
        self.carried_forward = {
            item.normalized_url: item for item in (acquire.carried_forward_assessments if acquire is not None else ())
        }
        self.scores = scores
        rows = acquire.rows if acquire is not None else ()
        self.rows = tuple(sorted(rows, key=self._sort_key))

    def _verdict(self, url: str) -> str:
        result = self.assessments.get(url)
        if result is None:
            carried = self.carried_forward.get(url)
            result = carried.result if carried is not None else None
        if result is None:
            return "not_assessed"
        return result.verdict.value if result.verdict is not None else "assessed"

    def _sort_key(self, row) -> tuple[int, int, bool, tuple[int, ...]]:
        url = row.posting.normalized_url
        score = self.scores.get(url)
        value = score.score if score is not None and score.score is not None else -1
        # Newest first inside one score, a posting with no date last. The UI
        # compares the date's text, so this does too: each character's
        # inverse sorts a later date earlier.
        published_at = row.posting.published_at or ""
        newest_first = tuple(-ord(char) for char in published_at)
        return (_VERDICT_GROUP.get(self._verdict(url), 5), -value, not published_at, newest_first)

    def find(self, url: str):
        return next((row for row in self.rows if row.posting.normalized_url == url), None)

    def row_json(self, row, *, text: bool) -> dict[str, object]:
        posting = row.posting.to_json()
        score = self.scores.get(row.posting.normalized_url)
        return {
            "posting": posting if text else grid_posting(posting),
            "outcome": row.outcome.value,
            "rank_score": score.to_json() if score is not None else None,
        }


def results_page(view: RunView, *, limit: int, offset: int) -> dict[str, object]:
    """The no-query ``/results`` body's shape, for ``view``'s rows ``offset..offset+limit``."""

    from ...projection import present_payload_from

    evidence = view.evidence
    payload = present_payload_from(evidence)
    page = view.rows[offset : offset + limit]
    urls = [row.posting.normalized_url for row in page]
    assessments = [view.assessments[url].to_json() for url in urls if url in view.assessments]
    not_assessed = [
        {"posting": grid_posting(item.posting.to_json()), "reason": item.reason.value}
        for item in (view.not_assessed.get(url) for url in urls)
        if item is not None
    ]
    return {
        "schema_version": "scout-find-jobs-run-results-response:1",
        "run_id": evidence.run_id,
        "total": len(view.rows),
        "limit": limit,
        "offset": offset,
        "created_at": evidence.started_at,
        "counts": run_counts(evidence),
        "payload": {
            "schema_version": payload.schema_version,
            "run_id": payload.run_id,
            "config": payload.config.to_json(),
            "pinned_resume": payload.pinned_resume.to_json() if payload.pinned_resume is not None else None,
            "rows": [view.row_json(row, text=False) for row in page],
            "failures": [item.to_json() for item in payload.failures],
            "assessments": assessments,
            "not_assessed": not_assessed,
            "node_receipts": [item.to_json() for item in payload.node_receipts],
            "status": payload.status.value,
        },
        "carried_forward_assessments": [
            view.carried_forward[url].to_json() for url in urls if url in view.carried_forward
        ],
    }


def posting_rows(view: RunView, url: str) -> dict[str, object] | None:
    """One posting of the run, complete, in a results page's own keys.

    ``None`` when the run has no such posting. A page's keys, so the row
    joins (H-1B, job state) are the page's own functions applied to one row;
    ``posting_detail`` is what the route answers.
    """

    row = view.find(url)
    if row is None:
        return None
    assessment = view.assessments.get(url)
    not_assessed = view.not_assessed.get(url)
    carried = view.carried_forward.get(url)
    return {
        "payload": {
            "rows": [view.row_json(row, text=True)],
            "assessments": [assessment.to_json()] if assessment is not None else [],
            "not_assessed": [{"reason": not_assessed.reason.value}] if not_assessed is not None else [],
        },
        "carried_forward_assessments": [carried.to_json()] if carried is not None else [],
    }


def posting_detail(run_id: str, rows: Mapping[str, object]) -> dict[str, object]:
    """``GET .../posting``'s response, from ``posting_rows``' one row."""

    payload = rows["payload"]
    carried = rows["carried_forward_assessments"]
    return {
        "schema_version": POSTING_RESPONSE_SCHEMA,
        "run_id": run_id,
        "row": payload["rows"][0],  # type: ignore[index]
        "assessment": payload["assessments"][0] if payload["assessments"] else None,  # type: ignore[index]
        "not_assessed_reason": payload["not_assessed"][0]["reason"] if payload["not_assessed"] else None,  # type: ignore[index]
        "carried_forward": carried[0] if carried else None,  # type: ignore[index]
    }


def summarise_progress(progress: Mapping[str, object]) -> dict[str, object]:
    """``run_progress``'s response with no posting text and no list of skipped boards."""

    body = dict(progress)
    boards = progress.get("boards")
    if isinstance(boards, dict):
        body["boards"] = {key: value for key, value in boards.items() if key != "skipped_boards"}
    postings = progress.get("postings")
    if isinstance(postings, list):
        body["postings"] = [grid_posting(item) if isinstance(item, dict) else item for item in postings]
    assessments = progress.get("assessments")
    if isinstance(assessments, list):
        body["assessments"] = [_without_posting_text(item) for item in assessments]
    return body


def _without_posting_text(entry: object) -> object:
    if not isinstance(entry, dict):
        return entry
    assessment = entry.get("assessment")
    if not isinstance(assessment, dict) or not isinstance(assessment.get("posting"), dict):
        return entry
    return {**entry, "assessment": {**assessment, "posting": grid_posting(assessment["posting"])}}


def _query_int(query: dict[str, list[str]], name: str, *, minimum: int, maximum: int | None) -> int | None:
    """``None`` when absent; ``ValueError`` when present but not an integer in range."""

    values = query.get(name)
    if not values:
        return None
    text = values[-1]
    if not text.isascii() or not text.isdigit():
        raise ValueError(name)
    value = int(text)
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(name)
    return value


class RunReadsRoutesMixin:
    """``Handler`` mixin: the paged ``/results``, ``/posting`` and the ``/progress`` summary."""

    def _run_view(self, run_id: str):
        """``(resolved, view, joins)`` for ``run_id``; ``LookupError`` when there is no such run."""

        from ...projection import read_run_evidence

        backend = self._backend
        require_run = getattr(backend, "_require_run", None)
        if require_run is None or getattr(backend, "target", None) is None:
            raise LookupError(run_id)
        resolved = require_run(run_id)
        evidence = read_run_evidence(resolved, run_id)
        joins = row_joins(backend, resolved)
        rows = tuple(item.posting for item in (evidence.acquire_output.rows if evidence.acquire_output is not None else ()))
        scores = stored_rank_scores(
            rows, evidence=evidence, joins=joins, home_root=backend.home_root, target=backend.target
        )
        return resolved, RunView(evidence, scores=scores), joins

    def _run_stored_rank_scores(self, run_id: str) -> tuple[RankScore, ...]:
        """The no-query ``/results``' ``rank_scores``, read and never scored.

        One entry per posting, in the run's own order: its stored score, or
        the unscored entry (``fit`` and ``score`` ``null``) where none is
        stored. Empty when there is nothing to score against or with: no
        selected profile, no resume, or no Jev key. That is what the read
        answered while it still asked Jev for the missing scores.
        """

        from .. import jev_client
        from ..jev_rank import unscored

        _resolved, view, joins = self._run_view(run_id)
        if joins.profile is None or not joins.resume_text:
            return ()
        if not jev_client.has_api_key(home_root=self._backend.home_root):
            return ()
        acquire = view.evidence.acquire_output
        rows = [row.posting for row in (acquire.rows if acquire is not None else ())]
        return tuple(view.scores.get(row.normalized_url) or unscored(row) for row in rows)

    def _join_row_fields(self, body: dict[str, object], *, resolved, view: RunView, joins: RowJoins) -> None:
        """``rows[].h1b`` and ``rows[].job_state``, as the no-query ``/results`` adds them."""

        from .runs import _catalog_h1b_index, attach_h1b

        run_id = view.evidence.run_id
        try:
            attach_h1b(body["payload"]["rows"], _catalog_h1b_index())  # type: ignore[index]
        except Exception:  # noqa: BLE001 - display-only enrichment must never break the read
            _logger.exception("H-1B join skipped for run %s", run_id)
        if joins.profile is None or joins.events is None:
            return  # no resume identity to read the stores for, or no job-state read
        try:
            from ..job_state import JobStateSources
            from .runs import attach_job_states

            backend = self._backend
            attach_job_states(
                body,
                sources=JobStateSources(
                    home_root=backend.home_root, target=backend.target, resolved=resolved, events=joins.events
                ),
                profile_id=joins.profile.profile_id,
                run_started_at=view.evidence.started_at,
            )
        except Exception:  # noqa: BLE001 - display-only enrichment must never break the read
            _logger.exception("job state skipped for run %s", run_id)

    def _handle_get_run_results_page(self, run_id: str) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        unknown = set(query) - {"limit", "offset"}
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query field(s): {sorted(unknown)}")
            return
        try:
            limit = _query_int(query, "limit", minimum=1, maximum=RESULTS_PAGE_LIMIT_MAX)
            offset = _query_int(query, "offset", minimum=0, maximum=None)
        except ValueError as exc:
            self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_value",
                f"{exc} must be a whole number"
                + (f" from 1 to {RESULTS_PAGE_LIMIT_MAX}" if str(exc) == "limit" else " of 0 or more"),
            )
            return
        if limit is None:
            if offset is not None:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "offset needs a limit")
                return
            limit = RESULTS_PAGE_LIMIT_DEFAULT
        try:
            resolved, view, joins = self._run_view(run_id)
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
            return
        body = results_page(view, limit=limit, offset=offset or 0)
        self._join_row_fields(body, resolved=resolved, view=view, joins=joins)
        self._write_json(HTTPStatus.OK, body)

    def _handle_get_run_posting(self, run_id: str) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        unknown = set(query) - {"url"}
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query field(s): {sorted(unknown)}")
            return
        url = query.get("url", [""])[-1]
        if not url:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "url must be the posting's normalized_url")
            return
        try:
            resolved, view, joins = self._run_view(run_id)
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
            return
        rows = posting_rows(view, url)
        if rows is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "the run has no such posting")
            return
        self._join_row_fields(rows, resolved=resolved, view=view, joins=joins)
        self._write_json(HTTPStatus.OK, posting_detail(run_id, rows))

    def _handle_get_run_progress_summary(self, run_id: str) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        unknown = set(query) - {"summary"}
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query field(s): {sorted(unknown)}")
            return
        summary = query.get("summary", [None])[-1]
        if summary not in {None, "0", "1"}:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "summary must be 0 or 1")
            return
        try:
            progress = self._backend.run_progress(run_id)
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
            return
        self._write_json(HTTPStatus.OK, summarise_progress(progress) if summary == "1" else progress)


__all__ = [
    "POSTING_RESPONSE_SCHEMA",
    "RESULTS_PAGE_LIMIT_DEFAULT",
    "RESULTS_PAGE_LIMIT_MAX",
    "RowJoins",
    "RunReadsRoutesMixin",
    "RunView",
    "grid_posting",
    "posting_detail",
    "posting_rows",
    "results_page",
    "row_joins",
    "run_counts",
    "stored_rank_scores",
    "summarise_progress",
]
