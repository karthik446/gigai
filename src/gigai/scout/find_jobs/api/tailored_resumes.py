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

0110-032: ``PUT /api/tailored-resumes/lines`` also takes ``use: "custom"`` with ``text`` (the
caller's own wording for one line; the personal-info check refuses a name or contact line with
422 ``personal_info_refused``), and ``POST /api/resume/pdf`` renders resume markdown the caller
sends to a PDF with the same template and saved header as ``POST /api/tailored-resumes/pdf``.
That markdown is used for the render only: no model call, nothing stored, nothing logged
(a refusal names a line number and a rule, never the text).
"""

from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
import re
from urllib.parse import parse_qs, urlsplit

from ...quick_assess import QuickAssessError
from ...tailored_resume import (
    LINE_USES,
    TailoredResumesListResponse,
    TailorError,
    TailorRequest,
    apply_line_choice,
    apply_line_edit,
    list_tailored_resumes,
    run_tailored_resume,
    save_tailor_response,
)
from ...resume_display import SPACING_MAX, SPACING_MIN, valid_spacing
from ...resume_pdf import (
    MAX_MARKDOWN_BYTES,
    ResumeMarkdownError,
    layout,
    parse_resume_markdown,
    pdf_file_name,
    render_markdown_pdf,
    saved_header,
    stored_resume_pdf,
)
from ..contracts import FindJobsContractError
from .assess import _ERROR_STATUS as _ASSESS_ERROR_STATUS

_ERROR_STATUS: dict[str, HTTPStatus] = {
    **_ASSESS_ERROR_STATUS,
    "tailor_timeout": HTTPStatus.GATEWAY_TIMEOUT,
    "personal_info_refused": HTTPStatus.UNPROCESSABLE_ENTITY,
}

_RESUME_PDF_KEYS = frozenset({"markdown", "spacing_scale", "auto_fit", "profile_id"})
_PROFILE_ID = re.compile(r"\A[A-Za-z0-9_-]+\Z")
#: ``POST /api/resume/pdf``: a body above this is refused before it is parsed (JSON escaping can
#: make ``MAX_MARKDOWN_BYTES`` of markdown several times larger on the wire); above the drain
#: limit it is not even read and the connection closes.
_MAX_RESUME_PDF_BODY = 8 * MAX_MARKDOWN_BYTES
_MAX_DRAIN = 8 * 1024 * 1024


def _status_for(code: str) -> HTTPStatus:
    return _ERROR_STATUS.get(code, HTTPStatus.CONFLICT)


class TailoredResumesRoutesMixin:
    """``Handler`` mixin: ``POST``/``GET /api/tailored-resumes``, ``PUT /api/tailored-resumes/lines``, and the two PDF routes."""

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
        try:
            rendered, file_name = stored_resume_pdf(items[0], home_root=home_root, target=target)
        except Exception:  # noqa: BLE001 - a render failure is typed, and never echoes the resume
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "pdf_render_failed", "the PDF could not be rendered")
            return
        self._write_bytes(
            HTTPStatus.OK,
            "application/pdf",
            rendered.pdf,
            {"Content-Disposition": f'attachment; filename="{file_name}"'},
        )

    def _refuse_large_body(self) -> bool:
        """True (and a 422 written) when the request body is too large to be resume markdown."""

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return False  # ``_read_json_body`` answers the invalid header
        if length <= _MAX_RESUME_PDF_BODY:
            return False
        if length <= _MAX_DRAIN:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        else:
            self.close_connection = True
        self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "resume_markdown_too_large", f"markdown is larger than {MAX_MARKDOWN_BYTES} bytes")
        return True

    def _handle_post_resume_pdf(self) -> None:
        """``POST /api/resume/pdf``: resume markdown in, ``application/pdf`` out (0110-032).

        Body ``{markdown, spacing_scale?, auto_fit?, profile_id?}``.  The header (name, title,
        contact line) and the layout come from the saved display settings, exactly as for a
        stored tailored resume; ``spacing_scale`` / ``auto_fit`` override the saved layout for
        this render only.  Nothing is stored, sent to a model, or logged.
        """

        if self._refuse_large_body():
            return
        body = self._read_json_body()
        if body is None:
            return
        if type(body) is not dict:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be an object")
            return
        unknown = sorted(set(body) - _RESUME_PDF_KEYS)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown key: {unknown[0]}")
            return
        markdown = body.get("markdown")
        if not isinstance(markdown, str):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "markdown must be a string (resume markdown in GigAI's format)")
            return
        spacing = body.get("spacing_scale")
        if spacing is not None and type(spacing) not in (int, float):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "spacing_scale must be a number")
            return
        if spacing is not None and not valid_spacing(spacing):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", f"spacing_scale must be between {SPACING_MIN} and {SPACING_MAX}")
            return
        auto_fit = body.get("auto_fit")
        if auto_fit is not None and type(auto_fit) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "auto_fit must be true or false")
            return
        profile_id = body.get("profile_id")
        if profile_id is not None and (not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id)):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "profile_id must be a profile id")
            return
        home_root = getattr(self._backend, "home_root", None)
        if home_root is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a home path is required")
            return
        try:
            name_hint, _sections = parse_resume_markdown(markdown)
        except ResumeMarkdownError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
            return
        try:
            header, settings = saved_header(home_root, getattr(self._backend, "target", None), profile_id, name_hint)
            scale, fit = layout(settings, spacing, auto_fit)
            rendered = render_markdown_pdf(markdown, header, timestamp=datetime.now(timezone.utc), spacing_scale=scale, auto_fit=fit)
        except Exception:  # noqa: BLE001 - a render failure is typed, and never echoes the resume
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "pdf_render_failed", "the PDF could not be rendered")
            return
        self._write_bytes(
            HTTPStatus.OK,
            "application/pdf",
            rendered.pdf,
            {
                "Content-Disposition": f'attachment; filename="{pdf_file_name(header.name, "")}"',
                "X-GigAI-Pages": str(rendered.pages),
                "X-GigAI-Spacing-Scale": f"{rendered.spacing_scale:g}",
            },
        )

    def _handle_put_tailored_resume_line(self) -> None:
        """``PUT /api/tailored-resumes/lines``: show the original, the rewrite, or the caller's own text of one line."""

        body = self._read_json_body()
        if body is None:
            return
        keys = {"profile_id", "job_identity", "updated_at", "line_id", "use"}
        custom = type(body) is dict and body.get("use") == "custom"
        if type(body) is not dict or set(body) != (keys | {"text"} if custom else keys):
            self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value",
                "body must be exactly profile_id, job_identity, updated_at, line_id and use (plus text when use is custom)",
            )
            return
        if not all(isinstance(body[key], str) and body[key] for key in keys):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "every field must be a non-empty string")
            return
        if body["use"] not in LINE_USES:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "use must be original, rewritten or custom")
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
            if custom:
                # The name the PDF header prints (saved, else the resume's own): a line holding it is refused.
                header, _settings = saved_header(
                    self._backend.home_root, target, body["profile_id"], stored.result.header[0].text if stored.result.header else ""
                )
                updated = apply_line_edit(stored, body["line_id"], body["text"], names=(header.name,))
            else:
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
