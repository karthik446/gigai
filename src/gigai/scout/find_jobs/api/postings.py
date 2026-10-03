"""0.1.10.7 M4a: ``GET /api/postings``, ``POST /api/postings/assess`` and ``POST /api/runs/import``.

The run-free way in (``posting_search.py`` and ``run_history.py`` are the
builders; ``gigai scout jobs list|assess|import-runs --json`` print the same
objects).

- ``GET /api/postings`` is the live search over the stored index, across the
  active profiles: no board request, no run, no model call, and the "new
  since" anchor stays. ``history=1`` adds what old find-jobs runs assessed.
- ``POST /api/postings/assess`` is "Assess these". Without ``approve: true``
  it answers ``status: "ask"`` (the count and the estimate) and assesses
  nothing; with it the batch is assessed through the job page's own path.
- ``POST /api/runs/import`` imports what old runs assessed into the read
  model, once per run; a second call imports nothing.

No response mixes: these hold posting text (public-untrusted) and nothing
the user wrote.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ...data_labels import LabelError
from ...pipeline.store import PipelineStoreError
from ...posting_search import DEFAULT_LIMIT, PostingModelError, PostingSearchError, assess_these, search_postings
from ...run_history import migrate_runs

_ERROR_STATUS = {
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "profile_not_found": HTTPStatus.NOT_FOUND,
    "target_unavailable": HTTPStatus.NOT_FOUND,
    "config_unavailable": HTTPStatus.CONFLICT,
    "assess_batch_running": HTTPStatus.CONFLICT,
}
_FLAGS = {"1": True, "true": True, "0": False, "false": False}
_QUERY_KEYS = frozenset({"profile_id", "q", "state", "window", "removed", "history", "include_hidden", "limit", "offset"})
_ASSESS_KEYS = frozenset({"jobs", "profile_id", "query", "states", "window", "approve", "again", "actor"})


class PostingsRoutesMixin:
    """``Handler`` mixin: ``GET /api/postings``, ``POST /api/postings/assess``, ``POST /api/runs/import``."""

    def _postings_target(self):
        target = getattr(self._backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
        return target

    def _postings_answer(self, build) -> None:
        try:
            response = build()
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
                states=query.get("state"), window=(query.get("window") or [None])[0], limit=limit, offset=offset, **flags,
            )
        )

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
        approve, again = body.get("approve", False), body.get("again", False)
        if type(approve) is not bool or type(again) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "approve and again must be true or false")
            return
        texts = {key: body.get(key) for key in ("profile_id", "query", "window", "actor")}
        if any(value is not None and type(value) is not str for value in texts.values()):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "profile_id, query, window and actor must be strings")
            return
        target = self._postings_target()
        if target is None:
            return
        home_root = self._backend.home_root
        self._postings_answer(
            lambda: assess_these(
                home_root, target, jobs=jobs, profile_id=texts["profile_id"], query=texts["query"], states=states,
                window=texts["window"], approve=approve, again=again, decided_by=texts["actor"] or "operator",
            )
        )

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


__all__ = ["PostingsRoutesMixin"]
