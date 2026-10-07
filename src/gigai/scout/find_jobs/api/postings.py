"""0.1.10.7 M4a: ``GET /api/postings``, ``POST /api/postings/assess`` and ``POST /api/runs/import``.

The run-free way in (``posting_search.py`` and ``run_history.py`` are the
builders; ``gigai scout jobs list|assess|import-runs --json`` print the same
objects).

- ``GET /api/postings`` is the live search over the stored index, across the
  active profiles: no board request, no run, no model call, and the "new
  since" anchor stays. ``history=1`` adds what old find-jobs runs assessed.
  ``sort=newest_posted`` (0110-10-14) orders the rows by the day the posting
  went up, the newest first; ``sort=fit``, the default, is the grid's order.
- ``POST /api/postings/assess`` is "Assess these". Without ``approve: true``
  it answers ``status: "ask"`` (the count and the estimate) and assesses
  nothing; with it the batch is assessed through the job page's own path.
  0110-10-02: postings below the assess threshold are left out and counted
  unless ``include_low_rank: true``.
- ``POST /api/runs/import`` imports what old runs assessed into the read
  model, once per run; a second call imports nothing.

- ``POST /api/postings/rank`` (0.1.11.2) is the Jobs page's "Rank now" and
  "Re-rank latest 100" (``pipeline.rank_now``). ``{}`` reads the state (the
  switch, the day's rank calls, how far the rank is, the job); ``{"mode"}``
  asks (the postings and the calls it would make: the cost) and calls no
  model; ``{"mode", "approve": true}`` starts the job and answers ``202`` at
  once. System data only: ids, counts and codes.

- ``GET /api/postings/ranking`` (0.1.11.3) is the ``ranking`` block and the rank job alone, for the Jobs page to
  poll while a rank runs: two SQL counts a profile, no refresh of the read model, no row read, no write.

- ``GET /api/postings/status`` (0110-9-01) says how the posting read model
  is in this server, from memory alone: it answers at once whatever a build
  is doing.

0110-9-01: a read never waits for a large build of the read model. It is
answered from the rows as stored (a small build, a few companies after an
update, is waited for up to :data:`MODEL_WAIT_SECONDS`), or, when
there are none yet (the first build, once after an upgrade), with ``202``
and the ``scout-postings-status:1`` object (``status: "preparing"`` and the
percent); the build runs once, in its own thread, for every request.

0.1.10.11 (C8): ``POST /api/postings/assess`` selects its postings from the
same stored rows while a large build runs (they are the rows the list
showed). With no stored rows yet it waits for the first build, as before:
only a GET answers ``202``.

No response mixes: these hold posting text (public-untrusted) and nothing
the user wrote.
"""

from __future__ import annotations

from http import HTTPStatus
import os
from urllib.parse import parse_qs, urlsplit

from ...data_labels import LabelError
from ...pipeline.store import PipelineStoreError
from ...posting_search import DEFAULT_LIMIT, PostingModelError, PostingModelPreparing, PostingSearchError, assess_these, search_postings
from ...postings import model_status
from ...run_history import migrate_runs

_ERROR_STATUS = {
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "profile_not_found": HTTPStatus.NOT_FOUND,
    "target_unavailable": HTTPStatus.NOT_FOUND,
    "config_unavailable": HTTPStatus.CONFLICT,
    "assess_batch_running": HTTPStatus.CONFLICT,
}
#: How long a read route waits for a SMALL build of the posting read model (``postings.SMALL_BUILD_BOARDS``) before it
#: answers from what is stored (or 202). A large build is never waited for.
MODEL_WAIT_SECONDS = 10.0
MODEL_WAIT_ENV = "GIGAI_SCOUT_POSTINGS_WAIT_SECONDS"


def model_wait_seconds() -> float:
    raw = os.environ.get(MODEL_WAIT_ENV)
    try:
        return max(0.0, float(raw)) if raw else MODEL_WAIT_SECONDS
    except ValueError:
        return MODEL_WAIT_SECONDS


def preparing_body(progress: dict[str, object]) -> dict[str, object]:
    """What a read answers (202) while the first build runs: the status object, ``status: "preparing"``."""

    return {**progress, "status": "preparing"}


_FLAGS = {"1": True, "true": True, "0": False, "false": False}
_QUERY_KEYS = frozenset({"profile_id", "q", "state", "window", "removed", "history", "include_hidden", "limit", "offset", "sort", "job"})
_RANK_KEYS = frozenset({"mode", "approve"})
_RANK_ERROR_STATUS = {
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "rank_disabled": HTTPStatus.CONFLICT,
    "rank_daily_cap": HTTPStatus.CONFLICT,
    "target_unavailable": HTTPStatus.NOT_FOUND,
    "config_unavailable": HTTPStatus.CONFLICT,
}
_ASSESS_KEYS = frozenset({"jobs", "profile_id", "query", "states", "window", "approve", "again", "actor", "include_low_rank", "background"})


class PostingsRoutesMixin:
    """``Handler`` mixin: ``GET /api/postings``, ``POST /api/postings/assess``, ``POST /api/postings/rank``, ``POST /api/runs/import``."""

    def _postings_target(self):
        target = getattr(self._backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
        return target

    def _postings_answer(self, build) -> None:
        try:
            response = build()
        except PostingModelPreparing as exc:
            self._write_json(HTTPStatus.ACCEPTED, preparing_body(exc.progress))
            return
        except (PostingSearchError, PostingModelError, PipelineStoreError) as exc:
            self._error(_ERROR_STATUS.get(exc.code, HTTPStatus.CONFLICT), exc.code, str(exc))
            return
        except LabelError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "labels_mixed", "the response was withheld: it mixed posting text with private text")
            return
        self._write_json(HTTPStatus.OK, response)

    def _handle_get_postings(self) -> None:
        target = self._postings_target()
        if target is None:
            return
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        unknown = sorted(set(query) - _QUERY_KEYS)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query key: {unknown[0]}")
            return
        flags: dict[str, bool] = {}
        for key in ("removed", "history", "include_hidden"):
            raw = (query.get(key) or ["0"])[0]
            if raw not in _FLAGS:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", f"{key} must be 1 or 0")
                return
            flags[key] = _FLAGS[raw]
        try:
            limit = int((query.get("limit") or [str(DEFAULT_LIMIT)])[0])
            offset = int((query.get("offset") or ["0"])[0])
        except ValueError:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "limit and offset must be whole numbers")
            return
        home_root = self._backend.home_root
        self._postings_answer(
            lambda: search_postings(
                home_root, target, profile_ids=query.get("profile_id"), query=(query.get("q") or [None])[0],
                states=query.get("state"), window=(query.get("window") or [None])[0], limit=limit, offset=offset,
                model_wait=model_wait_seconds(), sort=(query.get("sort") or [None])[0], jobs=query.get("job"), **flags,
            )
        )

    def _handle_get_postings_status(self) -> None:
        target = self._postings_target()
        if target is None:
            return
        if urlsplit(self.path).query:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", "this route takes no query keys")
            return
        self._write_json(HTTPStatus.OK, model_status(self._backend.home_root, target))

    def _handle_get_postings_ranking(self) -> None:
        from ...pipeline import rank_now

        target = self._postings_target()
        if target is None:
            return
        if urlsplit(self.path).query:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", "this route takes no query keys")
            return
        try:
            response = rank_now.ranking_read(self._backend.home_root, target)
        except (PostingModelError, PipelineStoreError) as exc:
            self._error(_ERROR_STATUS.get(exc.code, HTTPStatus.CONFLICT), exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, response)

    def _handle_post_postings_assess(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "the body must be a JSON object")
            return
        unknown = sorted(set(body) - _ASSESS_KEYS)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown key: {unknown[0]}")
            return
        jobs, states = body.get("jobs"), body.get("states")
        for name, value in (("jobs", jobs), ("states", states)):
            if value is not None and (type(value) is not list or any(type(item) is not str for item in value)):
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", f"{name} must be a list of strings")
                return
        approve, again, include_low_rank = body.get("approve", False), body.get("again", False), body.get("include_low_rank", False)
        background = body.get("background", False)
        if type(approve) is not bool or type(again) is not bool or type(include_low_rank) is not bool or type(background) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "approve, again, include_low_rank and background must be true or false")
            return
        texts = {key: body.get(key) for key in ("profile_id", "query", "window", "actor")}
        if any(value is not None and type(value) is not str for value in texts.values()):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "profile_id, query, window and actor must be strings")
            return
        target = self._postings_target()
        if target is None:
            return
        home_root = self._backend.home_root

        def answer(model_wait: float | None, on_live=None) -> dict[str, object]:
            return assess_these(
                home_root, target, jobs=jobs, profile_id=texts["profile_id"], query=texts["query"], states=states,
                window=texts["window"], approve=approve, again=again, decided_by=texts["actor"] or "operator",
                include_low_rank=include_low_rank, model_wait=model_wait, on_live=on_live,
            )

        def build(on_live=None) -> dict[str, object]:
            # 0.1.10.11 (C8): from the rows as stored while a large build runs, as the GET that listed them. With no
            # stored rows yet (the first build) a POST waits for the build, as it always did: only a GET answers 202.
            try:
                return answer(model_wait_seconds(), on_live)
            except PostingModelPreparing:
                return answer(None, on_live)

        if not (approve and background):
            self._postings_answer(build)
            return
        # 0.1.11.5 (ASSESS-01): the approved batch runs on a thread of the server; the request answers 202 as soon as
        # the batch is live. A call that assessed nothing (nothing to assess, a refusal) answers what it always did.
        from ... import assess_batch_job

        job = assess_batch_job.start(home_root, target, build)
        if job.live is not None or not job.done.is_set():
            # A batch was started: 202, also when it has ended already (a fast one): `last` then says how.
            self._write_json(HTTPStatus.ACCEPTED, assess_batch_job.started_body(home_root, target))
            return

        def ended() -> dict[str, object]:
            if job.error is not None:
                raise job.error
            assert job.response is not None
            return job.response

        self._postings_answer(ended)

    def _handle_get_postings_assess_status(self) -> None:
        from ... import assess_batch_job

        target = self._postings_target()
        if target is None:
            return
        if urlsplit(self.path).query:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", "this route takes no query keys")
            return
        self._write_json(HTTPStatus.OK, assess_batch_job.status(self._backend.home_root, target))

    def _handle_post_postings_assess_cancel(self) -> None:
        from ... import assess_batch_job

        body = self._read_json_body()
        if body is None:
            return
        if not (isinstance(body, dict) and not body):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", "this route takes no body keys")
            return
        target = self._postings_target()
        if target is None:
            return
        self._write_json(HTTPStatus.OK, assess_batch_job.cancel(self._backend.home_root, target))

    def _handle_post_postings_rank(self) -> None:
        from ...pipeline import rank_now

        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "the body must be a JSON object")
            return
        unknown = sorted(set(body) - _RANK_KEYS)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown key: {unknown[0]}")
            return
        mode, approve = body.get("mode"), body.get("approve", False)
        if (mode is not None and type(mode) is not str) or type(approve) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "mode must be a string and approve true or false")
            return
        if approve and mode is None:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "approve needs a mode: unranked or latest")
            return
        target = self._postings_target()
        if target is None:
            return
        home_root = self._backend.home_root
        try:
            if approve:
                response = rank_now.start(home_root, target, mode)
            else:
                response = {**rank_now.status(home_root, target, mode=mode, model_wait=model_wait_seconds()), "started": False}
        except (rank_now.RankNowError, PostingModelError, PipelineStoreError) as exc:
            self._error(_RANK_ERROR_STATUS.get(exc.code, HTTPStatus.CONFLICT), exc.code, str(exc))
            return
        # 202: a job was started and runs on; the same call with {} reads how far it is.
        self._write_json(HTTPStatus.ACCEPTED if response["started"] else HTTPStatus.OK, response)

    def _handle_post_runs_import(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not (isinstance(body, dict) and not body):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", "this route takes no body keys")
            return
        target = self._postings_target()
        if target is None:
            return
        home_root = self._backend.home_root
        self._postings_answer(lambda: migrate_runs(home_root, target))


__all__ = ["MODEL_WAIT_ENV", "MODEL_WAIT_SECONDS", "PostingsRoutesMixin", "model_wait_seconds", "preparing_body"]
