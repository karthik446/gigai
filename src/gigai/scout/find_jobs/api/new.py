"""0.1.10.7 M3a: ``GET /api/new``, ``GET /api/new/yours``, ``POST /api/new`` and ``POST /api/new/seen``.

What is new since the last check. ``scout_new.py`` is the one builder
(``gigai scout new --json`` prints the same objects). Across all active
profiles, from the stored index: no board request.

- ``GET /api/new`` is a read: it never calls a model and never moves the "new
  since" anchor. With new postings that have no assessment it answers
  ``status: "ask"``: the question (count, estimate) and the grid with rank
  only.
- ``GET /api/new/yours`` is the SEPARATE call for the user's own evidence of
  what matches (user-private only, never posting text), for the same postings.
- ``POST /api/new`` answers the question: ``{"assess": true}`` assesses those
  postings (model calls, the job page's own path) and answers the grid with
  scores; ``{"assess": false}`` the grid with rank only. 0110-10-02: a yes
  leaves out the postings below the assess threshold (``low_rank_question``
  counts them); ``"include_low_rank": true`` beside it assesses them too. Either moves the
  anchor after the response is built (not with ``peek`` or ``profile_id``).
- ``POST /api/new/seen`` is "Mark all seen": it moves the same anchor to now.

No response mixes: ``GET`` / ``POST /api/new`` hold posting text
(public-untrusted) and nothing the user wrote; ``GET /api/new/yours`` holds
the user's own text and no posting text.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ...data_labels import LabelError
from ...pipeline.store import PipelineStoreError
from ...scout_new import PostingModelError, PostingModelPreparing, ScoutNewError, mark_all_seen, scout_new, scout_new_yours
from .postings import model_wait_seconds, preparing_body

_ERROR_STATUS = {
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "profile_not_found": HTTPStatus.NOT_FOUND,
    "target_unavailable": HTTPStatus.NOT_FOUND,
    "config_unavailable": HTTPStatus.CONFLICT,
}
_TRUE = frozenset({"1", "true"})
_FALSE = frozenset({"0", "false"})


class NewRoutesMixin:
    """``Handler`` mixin: ``GET /api/new``, ``GET /api/new/yours``, ``POST /api/new``, ``POST /api/new/seen``."""

    def _new_target(self):
        target = getattr(self._backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
        return target

    def _answer_new(
        self, target, *, profile_id, peek: bool, assess: bool | None, since, yours: bool = False, reassess_stale: bool = False,
        model_wait: float | None = None, include_low_rank: bool = False,
    ) -> None:
        try:
            if yours:
                response = scout_new_yours(self._backend.home_root, target, profile_id=profile_id, since=since, model_wait=model_wait)
            else:
                response = scout_new(
                    self._backend.home_root, target, profile_id=profile_id, peek=peek, assess=assess, since=since,
                    reassess_stale=reassess_stale, model_wait=model_wait, include_low_rank=include_low_rank,
                )
        except PostingModelPreparing as exc:
            # 0110-9-01: the first build of the posting read model is running (a GET only): how far it is, never a hang.
            self._write_json(HTTPStatus.ACCEPTED, preparing_body(exc.progress))
            return
        except (ScoutNewError, PostingModelError, PipelineStoreError) as exc:
            self._error(_ERROR_STATUS.get(exc.code, HTTPStatus.CONFLICT), exc.code, str(exc))
            return
        except LabelError:
            # The response would have mixed posting text with the user's own: nothing is sent and the anchor has not moved.
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "labels_mixed", "the response was withheld: it mixed posting text with private text")
            return
        self._write_json(HTTPStatus.OK, response)

    def _handle_get_new(self, *, yours: bool = False) -> None:
        target = self._new_target()
        if target is None:
            return
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        unknown = sorted(set(query) - ({"profile_id", "since"} if yours else {"peek", "profile_id", "since"}))
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query key: {unknown[0]}")
            return
        peek = (query.get("peek") or ["1"])[0]
        if peek not in _TRUE | _FALSE:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "peek must be 1 or 0")
            return
        # A GET is a read: it never moves the anchor, whatever ``peek`` says (POST /api/new and /api/new/seen move it).
        self._answer_new(
            target, profile_id=(query.get("profile_id") or [None])[0], peek=True, assess=None,
            since=(query.get("since") or [None])[0], yours=yours, model_wait=model_wait_seconds(),
        )

    def _handle_get_new_yours(self) -> None:
        self._handle_get_new(yours=True)

    def _handle_post_new(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "the body must be a JSON object")
            return
        unknown = sorted(set(body) - {"assess", "include_low_rank", "peek", "profile_id", "reassess_stale", "since"})
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown key: {unknown[0]}")
            return
        assess, peek = body.get("assess"), body.get("peek", False)
        profile_id, since = body.get("profile_id"), body.get("since")
        reassess_stale, include_low_rank = body.get("reassess_stale", False), body.get("include_low_rank", False)
        if any(type(value) is not bool for value in (assess, peek, reassess_stale, include_low_rank)):
            self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type",
                "assess (required), peek, reassess_stale and include_low_rank must be true or false",
            )
            return
        if any(value is not None and type(value) is not str for value in (profile_id, since)):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "profile_id and since must be strings")
            return
        target = self._new_target()
        if target is None:
            return
        self._answer_new(
            target, profile_id=profile_id, peek=peek, assess=assess, since=since, reassess_stale=reassess_stale,
            include_low_rank=include_low_rank,
        )

    def _handle_post_new_seen(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if body not in ({}, None) and not (isinstance(body, dict) and not body):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", "this route takes no body keys")
            return
        target = self._new_target()
        if target is None:
            return
        try:
            answer = mark_all_seen(self._backend.home_root, target)
        except PipelineStoreError as exc:
            self._error(HTTPStatus.CONFLICT, exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, answer)


__all__ = ["NewRoutesMixin"]
