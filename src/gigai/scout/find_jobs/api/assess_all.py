"""uat-bug-042: ``POST /api/runs/{run_id}/assess-all`` -- "Assess all new" for one run.

The body, like ``POST /rank``'s:

* ``{}`` -- a page reading the run: starts nothing. It answers the ``plan``
  (how many new postings are not assessed yet, the run's model target, K,
  and a minute figure only when a per-call time was measured), the newest
  job of the run for the selected profile (``job``, with "x of N
  assessed"), and the run's live ``counts``.
* ``{"start": true}`` -- the click. Joins the job running for (run,
  profile), else starts one over the plan's queue. It never waits for a
  model. A run still going is not started on (``skip_reason``
  ``run_not_finished``): its own assess step may be on the same postings.
* ``{"cancel": true}`` -- stops the running job: no new call starts, the
  calls in flight finish, and every finished result is kept.

``counts`` (the Jobs header's Assessed / Matched / Need your answers) are
over the whole run, for the selected profile: a posting's latest verdict is
its quick assessment when the store has one (every "Assess all new" result
and every job-page Assess lands there), else the run's own. ``assessed`` and
``matched`` count as ``run_counts`` does (the run's assessments, not the
ones it carried forward) plus the run's postings assessed since;
``needs_answers`` is the postings whose latest verdict waits on answers and
that have no application recorded.

``skip_reason`` names why nothing can be queued: ``no_profile``,
``no_run_input``, ``run_not_finished``. An empty queue is ``plan.count`` 0.

0110-039: a posting of the run whose stored assessment for the profile was
made with an older prompt, other candidate settings or a story bank that has
since changed (``assessment_basis``) is in the queue too, and ``plan`` says
how many: ``count`` = ``new_count`` + ``stale_count``. Only a current stored
assessment is skipped. A read (``{}``) still starts nothing and calls no
model: a stale assessment is re-assessed by the click, never by a read. A
stale stored assessment the run itself assessed LATER is not counted (the
run's verdict is the one shown).

0110-10-11 (the operator's rule, ``scout_new.BATCH_LIMIT``): one click
assesses the NEWEST 50 of the queue and never more (:func:`newest_queue`: by
the day the posting went up; a posting with no date comes after the dated
ones). ``plan.count`` is then the 50, and ``plan.total`` / ``plan.more_after``
say how many there are in all and how many are left (neither key is there
when the queue is 50 or fewer). The next click takes the next 50: what was
assessed is no longer in the queue.
"""

from __future__ import annotations

from http import HTTPStatus
import logging
from pathlib import Path

from .. import assess_all
from ..contracts import Verdict

_logger = logging.getLogger("gigai.scout.server")

RESPONSE_SCHEMA = "scout-find-jobs-assess-all:1"


def _quick_verdicts(home_root: Path, target: Path, profile_id: str, latest: dict | None = None) -> dict[str, str | None]:
    """The profile's stored verdict per posting. ``latest`` (0110-039), when given, is filled
    with the stored item itself per posting, so the one store read serves the stale check too."""

    from ...quick_assess import QuickAssessError, list_quick_assessments

    try:
        items = list_quick_assessments(home_root, target, profile_id=profile_id)
    except QuickAssessError:
        return {}
    verdicts: dict[str, str | None] = {}
    for item in items:  # newest first: the first seen is the latest
        identity = item.job.job_identity
        if identity not in verdicts:
            verdicts[identity] = item.result.verdict.value if item.result.verdict is not None else None
            if latest is not None:
                latest[identity] = item
    return verdicts


def stale_stored(latest: dict, view, *, home_root: Path, target: Path, resolved) -> set[str]:
    """0110-039: the postings whose stored assessment is the one shown and was made with older settings.

    ``latest`` is the profile's stored item per posting. An item the run
    itself assessed (or carried forward) later is left out: ``job_state``
    shows the newer of the two, the stored one on a tie.
    """

    from ...assessment_basis import BasisCheck
    from ..job_state import _EPOCH, _instant

    check = BasisCheck(home_root=home_root, target=target, resolved=resolved)
    started = getattr(view.evidence, "started_at", None)
    stale: set[str] = set()
    for identity, item in latest.items():
        if check.reason(item) is None:
            continue
        run_at = None
        if identity in view.assessments:
            run_at = started
        elif identity in view.carried_forward:
            run_at = view.carried_forward[identity].from_run_date or started
        stored_at = _instant(item.updated_at or item.created_at) or _EPOCH
        if run_at is not None and (_instant(run_at) or _EPOCH) > stored_at:
            continue
        stale.add(identity)
    return stale


def live_counts(evidence, quick: dict[str, str | None], events, added_urls=()) -> dict[str, int]:
    """Assessed / Matched / Need your answers over the run's postings (see the module docstring).

    ``added_urls`` are the postings added to the run after it ended (``posted_window``).
    """

    acquire = evidence.acquire_output
    assess = evidence.assess_output
    urls = [row.posting.normalized_url for row in (acquire.rows if acquire is not None else ())] + list(added_urls)
    run_verdicts = {
        item.posting.normalized_url: (item.verdict.value if item.verdict is not None else None)
        for item in (assess.assessments if assess is not None else ())
    }
    assessed = matched = needs_answers = 0
    for url in dict.fromkeys(urls):
        if url in quick:
            verdict = quick[url]
        elif url in run_verdicts:
            verdict = run_verdicts[url]
        else:
            continue
        assessed += 1
        if verdict == Verdict.MATCHED_ABOVE_THRESHOLD.value:
            matched += 1
        elif verdict == Verdict.PENDING_USER_ANSWERS.value and not (events or {}).get(url):
            needs_answers += 1
    return {"assessed": assessed, "matched": matched, "needs_answers": needs_answers}


def newest_queue(queue, published: dict[str, str | None]) -> tuple[list, int]:
    """0110-10-11: ``(the newest 50 of the queue, in the queue's own order; how many are left)``.

    ``published`` is each posting's ``published_at``. The newest first; a posting with no date after the dated
    ones; equal dates keep the queue's order (the grid's: likely fits first).
    """

    from ... import scout_new

    limit = scout_new.BATCH_LIMIT
    if len(queue) <= limit:
        return list(queue), 0
    newest = sorted(range(len(queue)), key=lambda index: published.get(queue[index].normalized_url) or "", reverse=True)[:limit]
    return [queue[index] for index in sorted(newest)], len(queue) - limit


def assess_all_request(
    backend, run_id: str, *, start: bool = False, cancel: bool = False, only=None, limit: int | None = None
) -> dict[str, object]:
    """What ``POST /assess-all`` answers, at once. ``LookupError`` when there is no such run.

    0110-019: ``only`` (posting identities) and ``limit`` narrow the queue to
    the first ``limit`` of those postings, in the grid's order: how "Find
    postings from the last N days" assesses what it added and nothing else.
    """

    from ...projection import read_run_evidence
    from ..posted_window import added_rows, with_added
    from .run_reads import RunView, row_joins, stored_rank

    resolved = backend._require_run(run_id)
    home_root, target = Path(backend.home_root), Path(backend.target)
    evidence = read_run_evidence(resolved, run_id)
    joins = row_joins(backend, resolved)
    profile = joins.profile
    run_input = evidence.run_input
    body: dict[str, object] = {"schema_version": RESPONSE_SCHEMA, "run_id": run_id, "plan": None, "job": None, "counts": None, "skip_reason": None}
    if profile is None:
        body["skip_reason"] = "no_profile"
        return body
    profile_id = profile.profile_id
    # The job record is read BEFORE the verdicts the counts come from: a job
    # that finishes in between then reads "running" with counts at least as
    # new, never "complete" with counts that miss its last result.
    records = [record for record in assess_all.list_records(home_root, target, run_id=run_id) if record.profile_id == profile_id]
    if cancel:
        for record in records:
            if record.status == "running":
                assess_all.cancel(home_root, target, record.record_id)
        records = [record for record in assess_all.list_records(home_root, target, run_id=run_id) if record.profile_id == profile_id]
    latest: dict = {}
    quick = _quick_verdicts(home_root, target, profile_id, latest)
    added = added_rows(home_root, target, run_id)
    body["counts"] = live_counts(evidence, quick, joins.events, [row.posting.normalized_url for row in added])
    if run_input is None or evidence.acquire_output is None:
        body["skip_reason"] = "no_run_input"
        body["job"] = assess_all.summary(records[0]) if records else None
        return body

    rows = tuple(item.posting for item in with_added(evidence.acquire_output.rows, added))
    stored = stored_rank(rows, evidence=evidence, joins=joins, workpad=Path(resolved.path))
    view = RunView(evidence, scores=stored.scores, rank_detail=stored.detail, added_rows=added)
    stale = stale_stored(latest, view, home_root=home_root, target=target, resolved=resolved)
    queue = assess_all.build_queue(
        view.rows,
        run_assessed=set(view.assessments) | set(view.carried_forward),
        not_assessed_reasons={url: row.reason.value for url, row in view.not_assessed.items()},
        already_assessed=quick,
        stale=stale,
    )
    if only is not None:
        queue = [item for item in queue if item.normalized_url in only][:limit]
    # 0110-10-11: 50 at a time, the newest first; the plan says the total and what is left.
    total = len(queue)
    queue, later = newest_queue(queue, {row.posting.normalized_url: getattr(row.posting, "published_at", None) for row in view.rows})
    model_target = run_input.model_target.value
    concurrency = assess_all.assess_concurrency()
    body["plan"] = assess_all.plan(
        count=len(queue),
        model_target=model_target,
        concurrency=concurrency,
        job_seconds=assess_all.finished_call_seconds(records),
        run_seconds=assess_all.run_call_seconds(Path(resolved.path) / "runs" / run_id),
        stale_count=sum(1 for item in queue if item.normalized_url in stale),
    )
    if later:
        body["plan"].update({"total": total, "more_after": later})  # type: ignore[union-attr]
    if start and not cancel:
        if not evidence.terminal:
            body["skip_reason"] = "run_not_finished"
        else:
            outcome = assess_all.start_or_join(
                assess_all.JobInput(
                    home_root=home_root,
                    target=target,
                    run_id=run_id,
                    profile_id=profile_id,
                    model_target=model_target,
                    queue=tuple(queue),
                    assess_one=assess_all.quick_assess_one(
                        home_root=home_root, target=target, profile_id=profile_id, model_target=model_target
                    ),
                    concurrency=concurrency,
                    # 0110-039: a stored assessment made with older settings is not "already assessed".
                    is_assessed=assess_all.current_for_profile(home_root, target, profile_id),
                )
            )
            _logger.info(
                "assess all (%s): %s %s (%d queued, K=%d, %s)",
                run_id, outcome.action, outcome.record.record_id if outcome.record else "-", len(queue), concurrency, model_target,
            )
            if outcome.record is not None:
                body["job"] = assess_all.summary(outcome.record)
                return body
    body["job"] = assess_all.summary(records[0]) if records else None
    return body


class AssessAllRoutesMixin:
    """``Handler`` mixin: ``POST /api/runs/{run_id}/assess-all``."""

    def _handle_post_assess_all(self, run_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict) or set(body) - {"start", "cancel"}:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", 'body is {}, {"start": true} or {"cancel": true}')
            return
        start = body.get("start", False)
        cancel = body.get("cancel", False)
        if type(start) is not bool or type(cancel) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "start and cancel must be true or false")
            return
        if getattr(self._backend, "target", None) is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return
        try:
            response = assess_all_request(self._backend, run_id, start=start, cancel=cancel)
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
            return
        self._write_json(HTTPStatus.OK, response)


def wait_for_assess_all(*, timeout: float | None = None) -> bool:
    """Wait until no "assess all" job runs in this process; false when ``timeout`` ran out first."""

    return assess_all.wait_for_jobs(timeout=timeout)


__all__ = ["AssessAllRoutesMixin", "RESPONSE_SCHEMA", "assess_all_request", "live_counts", "newest_queue", "stale_stored", "wait_for_assess_all"]
