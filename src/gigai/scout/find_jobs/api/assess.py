"""P5: ``POST /api/assess`` and ``GET /api/assessments`` -- one standalone
assessment (no run), and the stored quick assessments.

Both routes call ``gigai.scout.quick_assess`` directly and read
``home_root``/``target`` straight off the backend (duck-typed, exactly as
``profiles.py`` does); the ``Backend`` protocol, ``ScoutFindJobsBackend``
and ``NotWiredBackend`` are untouched.  ``POST /api/assess`` is synchronous
(operator answer 1): the handler thread blocks for the model call and
returns the ``AssessResponse``; an adapter timeout comes back as a typed
504 ``assess_timeout``, never a hung connection.  The POST goes through the
Handler's existing loopback + CSRF guards before dispatch (C8).

Responses never carry resume text or the full job text (``AssessResponse``
serializes ``text_sha256`` only).  Every error is
``{"error": {"code", "message"}}``; ``_ERROR_STATUS`` maps each code
``quick_assess`` can raise to its status, and an unmapped code still gets a
safe 409 rather than a 500.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ...quick_assess import QuickAssessError, list_quick_assessments, run_quick_assessment
from ..assess_contracts import AssessRequest, AssessmentsListResponse
from ..contracts import FindJobsContractError, Verdict

_ERROR_STATUS: dict[str, HTTPStatus] = {
    # request shape / inputs
    "job_input_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "resume_input_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY,
    "wrong_type": HTTPStatus.UNPROCESSABLE_ENTITY,
    "unknown_key": HTTPStatus.UNPROCESSABLE_ENTITY,
    "missing_key": HTTPStatus.UNPROCESSABLE_ENTITY,
    "bad_enum": HTTPStatus.UNPROCESSABLE_ENTITY,
    "job_text_unavailable": HTTPStatus.UNPROCESSABLE_ENTITY,
    # upstream job fetch
    "job_fetch_failed": HTTPStatus.BAD_GATEWAY,
    # resume / profile / project lookups
    "profile_not_found": HTTPStatus.NOT_FOUND,
    "profile_unavailable": HTTPStatus.NOT_FOUND,
    "resume_unavailable": HTTPStatus.NOT_FOUND,
    "resume_digest_mismatch": HTTPStatus.NOT_FOUND,
    "target_unavailable": HTTPStatus.NOT_FOUND,
    # model
    "model_target_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    "model_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    "model_denied": HTTPStatus.FORBIDDEN,
    "assess_timeout": HTTPStatus.GATEWAY_TIMEOUT,
    "model_output_invalid": HTTPStatus.BAD_GATEWAY,
}


def _status_for(code: str) -> HTTPStatus:
    return _ERROR_STATUS.get(code, HTTPStatus.CONFLICT)


class AssessRoutesMixin:
    """``Handler`` mixin: ``POST /api/assess`` and ``GET /api/assessments``."""

    def _assess_target(self):
        backend = self._backend
        target = getattr(backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return None
        return target

    def _handle_post_assess(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            request = AssessRequest.from_json(body)
        except FindJobsContractError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        target = self._assess_target()
        if target is None:
            return
        try:
            response = run_quick_assessment(request, home_root=self._backend.home_root, target=target)
        except QuickAssessError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, response.to_json())

    def _handle_get_assessments(self) -> None:
        target = self._assess_target()
        if target is None:
            return
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        profile_id = (query.get("profile_id") or [None])[0]
        verdict = (query.get("verdict") or [None])[0]
        if verdict is not None and verdict not in {item.value for item in Verdict}:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "bad_enum", "verdict filter is not a known verdict")
            return
        try:
            items = list_quick_assessments(
                self._backend.home_root, target, profile_id=profile_id or None, verdict=verdict
            )
        except QuickAssessError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, AssessmentsListResponse(items).to_json())


__all__ = ["AssessRoutesMixin"]
