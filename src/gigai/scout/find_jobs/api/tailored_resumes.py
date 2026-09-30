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

from datetime import datetime, timezone
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ...quick_assess import QuickAssessError
from ...tailored_resume import (
    LINE_CHOICES,
    TailoredResumesListResponse,
    TailorError,
    TailorRequest,
    apply_line_choice,
    list_tailored_resumes,
    run_tailored_resume,
    save_tailor_response,
)
from ...resume_display import load_display, pdf_header
from ...resume_pdf import pdf_file_name, render_pdf
from ..contracts import FindJobsContractError
from .assess import _ERROR_STATUS as _ASSESS_ERROR_STATUS

_ERROR_STATUS: dict[str, HTTPStatus] = {**_ASSESS_ERROR_STATUS, "tailor_timeout": HTTPStatus.GATEWAY_TIMEOUT}


def _status_for(code: str) -> HTTPStatus:
    return _ERROR_STATUS.get(code, HTTPStatus.CONFLICT)


class TailoredResumesRoutesMixin:
    """``Handler`` mixin: ``POST /api/tailored-resumes``, ``GET /api/tailored-resumes`` and ``PUT /api/tailored-resumes/lines``."""

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

    def _handle_post_tailored_resume_pdf(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if type(body) is not dict or set(body) != {"profile_id", "job_identity"}:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "body must be exactly profile_id and job_identity")
            return
        profile_id, job_identity = body["profile_id"], body["job_identity"]
        if not isinstance(profile_id, str) or not profile_id or not isinstance(job_identity, str) or not job_identity:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "profile_id and job_identity must be non-empty strings")
            return
        target = self._tailor_target()
        if target is None:
            return
        home_root = self._backend.home_root
        try:
            items = list_tailored_resumes(home_root, target, profile_id=profile_id, job_identity=job_identity)
        except QuickAssessError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        if not items:
            self._error(HTTPStatus.NOT_FOUND, "tailored_resume_not_found", "no stored tailored resume for that profile and job")
            return
        stored = items[0]
        settings = load_display(home_root)
        fallback = ""
        if settings is None or not settings.name:
            from .resume_display import suggestion_for_profile

            suggestion = suggestion_for_profile(self._backend, None if profile_id == "ephemeral" else profile_id)
            fallback = (suggestion.name if suggestion else "") or (stored.result.header[0].text if stored.result.header else "")
        header = pdf_header(settings, profile_id, fallback)
        try:
            stamp = datetime.fromisoformat(stored.updated_at.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            pdf = render_pdf(stored.result, header, company=stored.job.company, timestamp=stamp)
        except Exception:  # noqa: BLE001 - a render failure is typed, and never echoes the resume
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "pdf_render_failed", "the PDF could not be rendered")
            return
        file_name = pdf_file_name(header.name, stored.job.company)
        self._write_bytes(
            HTTPStatus.OK,
            "application/pdf",
            pdf,
            {"Content-Disposition": f'attachment; filename="{file_name}"'},
        )

    def _handle_put_tailored_resume_line(self) -> None:
        """``PUT /api/tailored-resumes/lines``: show the original or the rewrite of one line."""

        body = self._read_json_body()
        if body is None:
            return
        keys = {"profile_id", "job_identity", "updated_at", "line_id", "use"}
        if type(body) is not dict or set(body) != keys:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "body must be exactly profile_id, job_identity, updated_at, line_id and use")
            return
        if not all(isinstance(body[key], str) and body[key] for key in keys):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "every field must be a non-empty string")
            return
        if body["use"] not in LINE_CHOICES:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "use must be original or rewritten")
            return
        target = self._tailor_target()
        if target is None:
            return
        try:
            items = list_tailored_resumes(
                self._backend.home_root, target, profile_id=body["profile_id"], job_identity=body["job_identity"]
            )
        except QuickAssessError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        if not items:
            self._error(HTTPStatus.NOT_FOUND, "tailored_resume_not_found", "no stored tailored resume for that profile and job")
            return
        stored = items[0]
        if stored.updated_at != body["updated_at"]:
            self._error(HTTPStatus.CONFLICT, "tailored_resume_changed", "a newer tailoring replaced this resume; reload it")
            return
        try:
            updated = apply_line_choice(stored, body["line_id"], body["use"])
        except TailorError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        if updated is not stored:
            save_tailor_response(updated)
        self._write_json(HTTPStatus.OK, updated.to_json())

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
