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
"""

from __future__ import annotations

from http import HTTPStatus
import logging
from pathlib import Path

from .. import assess_all
from ..contracts import Verdict

_logger = logging.getLogger("gigai.scout.server")

RESPONSE_SCHEMA = "scout-find-jobs-assess-all:1"


def _quick_verdicts(home_root: Path, target: Path, profile_id: str) -> dict[str, str | None]:
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
    return verdicts


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
    quick = _quick_verdicts(home_root, target, profile_id)
    added = added_rows(home_root, target, run_id)
    body["counts"] = live_counts(evidence, quick, joins.events, [row.posting.normalized_url for row in added])
    records = [record for record in assess_all.list_records(home_root, target, run_id=run_id) if record.profile_id == profile_id]
    if cancel:
        for record in records:
            if record.status == "running":
                assess_all.cancel(home_root, target, record.record_id)
        records = [record for record in assess_all.list_records(home_root, target, run_id=run_id) if record.profile_id == profile_id]
    if run_input is None or evidence.acquire_output is None:
        body["skip_reason"] = "no_run_input"
        body["job"] = assess_all.summary(records[0]) if records else None
        return body

    rows = tuple(item.posting for item in with_added(evidence.acquire_output.rows, added))
    stored = stored_rank(rows, evidence=evidence, joins=joins, workpad=Path(resolved.path))
    view = RunView(evidence, scores=stored.scores, rank_detail=stored.detail, added_rows=added)
    queue = assess_all.build_queue(
        view.rows,
        run_assessed=set(view.assessments) | set(view.carried_forward),
        not_assessed_reasons={url: row.reason.value for url, row in view.not_assessed.items()},
        already_assessed=quick,
    )
    if only is not None:
        queue = [item for item in queue if item.normalized_url in only][:limit]
    model_target = run_input.model_target.value
    concurrency = assess_all.assess_concurrency()
    body["plan"] = assess_all.plan(
        count=len(queue),
        model_target=model_target,
        concurrency=concurrency,
        job_seconds=assess_all.finished_call_seconds(records),
        run_seconds=assess_all.run_call_seconds(Path(resolved.path) / "runs" / run_id),
    )
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
                    is_assessed=assess_all.stored_for_profile(home_root, target, profile_id),
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


__all__ = ["AssessAllRoutesMixin", "RESPONSE_SCHEMA", "assess_all_request", "live_counts", "wait_for_assess_all"]
