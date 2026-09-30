"""Q3 (v0.1.9): ``POST /api/tailored-resumes`` and ``GET /api/tailored-resumes``
-- one tailored resume (markdown) for one posting, and the stored ones.

Both routes call ``gigai.scout.tailored_resume`` directly and read
``home_root``/``target`` straight off the backend (duck-typed, exactly as
``api/assess.py`` does); the ``Backend`` protocol, ``ScoutFindJobsBackend``
and ``NotWiredBackend`` are untouched.  The POST is synchronous: the handler
thread blocks for the model call (one retry on a rejected answer) and
returns the ``TailorResponse``; an adapter timeout comes back as a typed
504 ``tailor_timeout``.  The POST goes through the Handler's existing
loopback + CSRF guards before dispatch.

Responses never carry the posting text (``TailorResponse`` serializes the
job's ``text_sha256`` only).  They DO carry resume-derived text -- the
copied and rewritten lines and every ref's cited source text -- because
that is the product (README privacy line).  Every error is
``{"error": {"code", "message"}}`` with the same code -> status map the
assess routes use (``api/assess.py``'s ``_ERROR_STATUS``) plus
``tailor_timeout``; an unmapped code still gets a safe 409.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ...quick_assess import QuickAssessError
from ...tailored_resume import TailoredResumesListResponse, TailorRequest, list_tailored_resumes, run_tailored_resume
from ..contracts import FindJobsContractError
from .assess import _ERROR_STATUS as _ASSESS_ERROR_STATUS

_ERROR_STATUS: dict[str, HTTPStatus] = {**_ASSESS_ERROR_STATUS, "tailor_timeout": HTTPStatus.GATEWAY_TIMEOUT}


def _status_for(code: str) -> HTTPStatus:
    return _ERROR_STATUS.get(code, HTTPStatus.CONFLICT)


class TailoredResumesRoutesMixin:
    """``Handler`` mixin: ``POST /api/tailored-resumes`` and ``GET /api/tailored-resumes``."""

    def _tailor_target(self):
        backend = self._backend
        target = getattr(backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return None
        return target

    def _handle_post_tailored_resumes(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            request = TailorRequest.from_json(body)
        except FindJobsContractError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        target = self._tailor_target()
        if target is None:
            return
        try:
            response = run_tailored_resume(request, home_root=self._backend.home_root, target=target)
        except QuickAssessError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, response.to_json())

    def _handle_get_tailored_resumes(self) -> None:
        target = self._tailor_target()
        if target is None:
            return
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        profile_id = (query.get("profile_id") or [None])[0]
        job_identity = (query.get("job_identity") or [None])[0]
        try:
            items = list_tailored_resumes(
                self._backend.home_root, target, profile_id=profile_id or None, job_identity=job_identity or None
            )
        except QuickAssessError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, TailoredResumesListResponse(items).to_json())


__all__ = ["TailoredResumesRoutesMixin"]
