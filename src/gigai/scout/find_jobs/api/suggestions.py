"""0.1.11 N5 (SPEC 4.4): the job routes of the chat step.

* ``GET /api/jobs/suggestions?url=&profile_id=&status=``: one job's suggestions, as stored.
* ``POST /api/jobs/suggestions`` ``{job_url, action: add | resolve | dismiss, ...}``: an agent or the user adds
  one, sets one to done (with what settled it) or dismisses one. Resolving is recorded, never a delete.
* ``POST /api/job-resumes/pick`` ``{job_url, action: refresh | draft | use_proposed | dismiss_proposed}``: one
  explicit step on the job's resume; the answer is what is stored after it.
* ``GET /api/jobs/brief?url=&part=yours|posting&profile_id=``: one part of the agent's brief. Two parts, never
  one response: ``yours`` holds only what the user wrote and ids (``user-private``), ``posting`` only the posting
  and what a model derived from it (``public-untrusted``). The body's ``label`` and ``_labels`` say which.

Every handler calls ``job_actions`` / ``job_brief``, the functions ``gigai scout suggestions``, ``gigai scout
resume pick`` and ``gigai scout resume brief`` call: a route and its command cannot drift. No route here calls a
model or fetches anything. The two ``GET`` routes only read: nothing is recomputed and nothing is written. A write
names its writer like every other write (``actor`` in the body, else ``X-GigAI-Actor``, else the Scout UI is the
operator and anything else the agent).

Errors are ``{"error": {"code", "message"}}``. ``501 pick_not_available``: this GigAI does not hold the step that
picks a stored job's resume again (``job_resume_port.NotBuilt``); the message says what to do instead.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ... import job_actions, job_brief
from ...data_labels import LabelError
from ...job_resume_port import NotBuilt
from ...quick_assess import QuickAssessError
from ...story_bank import StoryBankError
from ..contracts import FindJobsContractError

_UNPROCESSABLE = (
    "invalid_value", "wrong_type", "unknown_key", "bad_enum", "personal_info_refused", "unknown_line", "unknown_requirement", "job_input_invalid",
)
_NOT_FOUND = ("assessment_missing", "suggestions_not_found", "suggestion_not_found", "no_proposed_resume", "target_unavailable", "profile_not_found")
_NOT_IMPLEMENTED = ("pick_not_available",)
_ERROR_STATUS: dict[str, HTTPStatus] = {
    **dict.fromkeys(_UNPROCESSABLE, HTTPStatus.UNPROCESSABLE_ENTITY),
    **dict.fromkeys(_NOT_FOUND, HTTPStatus.NOT_FOUND),
    **dict.fromkeys(_NOT_IMPLEMENTED, HTTPStatus.NOT_IMPLEMENTED),
}

_SUGGESTION_ACTIONS = ("add", "resolve", "dismiss")
_SUGGESTION_KEYS = frozenset({"job_url", "profile_id", "action", "actor", "kind", "why", "line", "requirement", "posting_phrase", "suggestion_id", "how", "ref"})
_PICK_KEYS = frozenset({"job_url", "profile_id", "action"})
#: What each suggestion action reads besides the job (a key of another action is ``invalid_value``, never ignored).
_ACTION_KEYS: dict[str, frozenset[str]] = {
    "add": frozenset({"kind", "why", "line", "requirement", "posting_phrase"}),
    "resolve": frozenset({"suggestion_id", "how", "ref"}),
    "dismiss": frozenset({"suggestion_id"}),
}
_ERRORS = (job_actions.JobActionError, job_brief.BriefError, NotBuilt, QuickAssessError, StoryBankError, FindJobsContractError, LabelError)


def _status_for(code: str) -> HTTPStatus:
    return _ERROR_STATUS.get(code, HTTPStatus.CONFLICT)


class JobSuggestionsRoutesMixin:
    """``Handler`` mixin: ``GET`` / ``POST /api/jobs/suggestions``, ``POST /api/job-resumes/pick``, ``GET /api/jobs/brief``."""

    def _job_route_target(self):
        target = getattr(self._backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
        return target

    def _job_route_error(self, exc: Exception) -> None:
        code = getattr(exc, "code", None) or "invalid_value"
        self._error(_status_for(code), code, str(exc))

    def _job_route_query(self, allowed: frozenset[str]) -> dict[str, str] | None:
        """The query's values by name (``url`` required); a 422 is written for an unknown key or a missing ``url``."""

        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        unknown = sorted(set(query) - allowed)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query field: {unknown[0]}; allowed: {', '.join(sorted(allowed))}")
            return None
        values = {name: found[-1].strip() for name, found in query.items()}
        if not values.get("url"):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "url must be the posting URL (raw or normalized)")
            return None
        return values

    def _job_route_body(self, allowed: frozenset[str]) -> dict[str, object] | None:
        """The JSON body as an object of strings (``job_url`` and ``action`` required); a 422 is written otherwise."""

        body = self._read_json_body()
        if body is None:
            return None
        if type(body) is not dict:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be an object")
            return None
        unknown = sorted(set(body) - allowed)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown key: {unknown[0]}; allowed: {', '.join(sorted(allowed))}")
            return None
        wrong = sorted(name for name, value in body.items() if value is not None and not isinstance(value, str))
        if wrong:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", f"{wrong[0]} must be a string")
            return None
        if not str(body.get("job_url") or "").strip() or not str(body.get("action") or "").strip():
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "job_url (the posting's link) and action are required strings")
            return None
        return body

    # ------------------------------------------------------------------ GET /api/jobs/suggestions

    def _handle_get_job_suggestions(self) -> None:
        query = self._job_route_query(frozenset({"url", "profile_id", "status"}))
        if query is None:
            return
        target = self._job_route_target()
        if target is None:
            return
        try:
            body = job_actions.list_suggestions(
                self._backend.home_root, target, query["url"], profile_id=query.get("profile_id") or None, status=query.get("status") or None,
            )
        except _ERRORS as exc:
            self._job_route_error(exc)
            return
        self._write_json(HTTPStatus.OK, body)

    # ------------------------------------------------------------------ POST /api/jobs/suggestions

    def _handle_post_job_suggestions(self) -> None:
        body = self._job_route_body(_SUGGESTION_KEYS)
        if body is None:
            return
        action = str(body["action"]).strip()
        if action not in _SUGGESTION_ACTIONS:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "action must be one of: " + ", ".join(_SUGGESTION_ACTIONS))
            return
        others = sorted(name for name in set().union(*_ACTION_KEYS.values()) - _ACTION_KEYS[action] if body.get(name) is not None)
        if others:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", f"{others[0]} does not go with action {action}")
            return
        target = self._job_route_target()
        if target is None:
            return
        home_root = self._backend.home_root
        job_url, profile_id = str(body["job_url"]).strip(), body.get("profile_id") or None
        try:
            # Who wrote it: the body's actor, the X-GigAI-Actor header, else the UI (operator) or anything else (agent).
            actor = self._story_bank_actor(body.get("actor"))
            if action == "add":
                answer = job_actions.add_suggestion(
                    home_root, target, job_url, kind=str(body.get("kind") or ""), why=str(body.get("why") or ""), actor=actor, profile_id=profile_id,  # type: ignore[arg-type]
                    line=body.get("line") or None, requirement=body.get("requirement") or None, posting_phrase=body.get("posting_phrase") or None,  # type: ignore[arg-type]
                )
            elif action == "resolve":
                answer = job_actions.resolve_suggestion(
                    home_root, target, job_url, str(body.get("suggestion_id") or ""), how=str(body.get("how") or ""), actor=actor, profile_id=profile_id,  # type: ignore[arg-type]
                    ref=body.get("ref") or None,  # type: ignore[arg-type]
                )
            else:
                answer = job_actions.dismiss_suggestion(home_root, target, job_url, str(body.get("suggestion_id") or ""), actor=actor, profile_id=profile_id)  # type: ignore[arg-type]
        except _ERRORS as exc:
            self._job_route_error(exc)
            return
        self._write_json(HTTPStatus.OK, answer)

    # ------------------------------------------------------------------ POST /api/job-resumes/pick

    def _handle_post_job_resume_pick(self) -> None:
        body = self._job_route_body(_PICK_KEYS)
        if body is None:
            return
        target = self._job_route_target()
        if target is None:
            return
        try:
            answer = job_actions.pick_action(
                self._backend.home_root, target, str(body["job_url"]).strip(), str(body["action"]).strip(), profile_id=body.get("profile_id") or None,  # type: ignore[arg-type]
            )
        except _ERRORS as exc:
            self._job_route_error(exc)
            return
        self._write_json(HTTPStatus.OK, answer)

    # ------------------------------------------------------------------ GET /api/jobs/brief

    def _handle_get_job_brief(self) -> None:
        query = self._job_route_query(frozenset({"url", "part", "profile_id"}))
        if query is None:
            return
        target = self._job_route_target()
        if target is None:
            return
        try:
            part = job_brief.brief(
                self._backend.home_root, target, query["url"], profile_id=query.get("profile_id") or None, part=query.get("part") or job_brief.PART_YOURS,
            )
        except _ERRORS as exc:
            self._job_route_error(exc)
            return
        self._write_json(HTTPStatus.OK, part)


__all__ = ["JobSuggestionsRoutesMixin"]
