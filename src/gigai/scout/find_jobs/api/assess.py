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
serializes ``text_sha256`` only).  The request's optional ``origin``
(``quick_assess`` | ``job_page``) is passed through as it came; what is
stored is ``quick_assess._origin_for``'s rule.  Every error is
``{"error": {"code", "message"}}``; ``_ERROR_STATUS`` maps each code
``quick_assess`` can raise to its status, and an unmapped code still gets a
safe 409 rather than a 500.

uat-bug-018: each ``GET /api/assessments`` item carries an additive
``job_state`` ``{state, since, next_events}`` (``job_state.py``): the job's
state for the resume the item was assessed with, where the item itself is
the latest assessment. Added to the served JSON only; the stored file and
``AssessResponse`` are untouched. A failure to derive it leaves the items
as they were.

0110-039: a served assessment carries ``basis_stale`` (true | false) and,
when true, ``basis_stale_reason`` (``older_prompt`` | ``settings_changed`` |
``story_bank_changed``): whether it was made with what its profile would be
assessed with now (``assessment_basis``). Derived on read, never stored, and
no model is called. ``POST /api/assess`` answers ``basis_stale: false`` for
an assessment made with the profile's own settings. In the list, a stale
item's ``job_state`` carries ``assessment_stale`` with the same reason.
"""

from __future__ import annotations

from http import HTTPStatus
import logging
from urllib.parse import parse_qs, urlsplit

from ...quick_assess import QuickAssessError, list_quick_assessments, run_quick_assessment
from ..assess_contracts import AssessRequest, AssessmentsListResponse
from ..contracts import FindJobsContractError, Verdict
from ..job_state import JobStateSources

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
    "posting_requirements_unreadable": HTTPStatus.UNPROCESSABLE_ENTITY,
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
    # 0110-8-09: the model answered, the answer could not be written
    "assessment_not_stored": HTTPStatus.INTERNAL_SERVER_ERROR,
}


_logger = logging.getLogger("gigai.scout.server")


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
        # 0110-034: a near match from the profile's story bank, per open question.
        from ... import story_bank
        from ...assessment_basis import BasisCheck

        served = story_bank.attach_suggestions(response.to_json(), home_root=self._backend.home_root, target=target)
        served.update(BasisCheck(home_root=self._backend.home_root, target=target).served(response))
        self._write_json(HTTPStatus.OK, served)

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
        body = AssessmentsListResponse(items).to_json()
        served = body.get("items")
        if isinstance(served, list):
            # Computed on read (one read of the user's answers), never stored.
            from ... import story_bank

            banks: dict[str, tuple[story_bank.BankEntry, ...]] = {}
            for row in served:
                if isinstance(row, dict):
                    story_bank.attach_suggestions(row, home_root=self._backend.home_root, target=target, cache=banks)
        try:
            self._attach_assessment_job_states(target, items, body)
        except Exception:  # noqa: BLE001 - display-only enrichment must never break the list
            _logger.exception("job state skipped for the assessments list")
        self._write_json(HTTPStatus.OK, body)

    def _attach_assessment_job_states(self, target, items, body: dict[str, object]) -> None:
        served = body.get("items")
        if not items or not isinstance(served, list) or len(served) != len(items):
            return
        from ....workpad import resolve_workpad

        backend = self._backend
        resolved = resolve_workpad(
            home_root=backend.home_root, requested_target=target, gig_id=None, allow_semantic_state=True
        )
        sources = JobStateSources(home_root=backend.home_root, target=target, resolved=resolved)
        for item, row in zip(items, served):
            if isinstance(row, dict):
                state = sources.state_for(item.job.job_identity, profile_id=item.resume.profile_id, quick=item)
                row["job_state"] = state.to_json()
                row.update(sources.basis.served(item))  # 0110-039: settings read once per resume identity


__all__ = ["AssessRoutesMixin"]
