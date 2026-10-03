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
sends to a PDF with the same template as ``POST /api/tailored-resumes/pdf``.
That markdown is used for the render only: no model call, nothing stored, nothing logged
(a refusal names a line number and a rule, never the text).

0110-046: GigAI stores no name or contact details.  Both PDF routes take an optional ``header``
(the Generate PDF form: ``name``, ``email``, ``phone``, ``location``, ``linkedin``, ``link``); its
values fill this one PDF's header and are dropped: never written, logged, cached, or echoed in a
response or an error (the PDF bytes aside; the file name is ``<company>-<role>-<date>.pdf``).
Without ``header`` the PDF has no header and the response carries ``X-GigAI-Finish-Url``: the local
Scout page (``#/pdf/...``) where the person adds their details in the Generate PDF form and downloads.
"""

from __future__ import annotations

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
    tailored_resume_path,
    tailored_resume_write_lock,
)
from ...resume_display import SPACING_MAX, SPACING_MIN, HeaderFormError, parse_header_form, valid_spacing
from ...resume_pdf import (
    MAX_MARKDOWN_BYTES,
    ResumeMarkdownError,
    finish_url,
    markdown_resume_pdf,
    parse_resume_markdown,
    stored_resume_pdf,
)
from ..contracts import FindJobsContractError
from .assess import _ERROR_STATUS as _ASSESS_ERROR_STATUS

_ERROR_STATUS: dict[str, HTTPStatus] = {
    **_ASSESS_ERROR_STATUS,
    "tailor_timeout": HTTPStatus.GATEWAY_TIMEOUT,
    "personal_info_refused": HTTPStatus.UNPROCESSABLE_ENTITY,
}

_RESUME_PDF_KEYS = frozenset({"markdown", "spacing_scale", "auto_fit", "profile_id", "header"})
_TAILORED_PDF_KEYS = frozenset({"profile_id", "job_identity"})
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

    def _header_form(self, body: dict[str, object]) -> tuple[bool, dict[str, str] | None]:
        """``(ok, the form's values or None)``; a 422 is written when ``header`` is unusable (never echoing a value)."""

        if "header" not in body:
            return True, None
        try:
            return True, parse_header_form(body["header"])
        except HeaderFormError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
            return False, None

    def _write_pdf(self, rendered, file_name: str, extra: dict[str, str] | None = None, finish: str | None = None) -> None:
        """The PDF; a headerless one (``finish`` set) names the Scout page that finishes it."""

        headers = {"Content-Disposition": f'attachment; filename="{file_name}"', **(extra or {})}
        if finish is not None:
            headers["X-GigAI-Finish-Url"] = finish
        self._write_bytes(HTTPStatus.OK, "application/pdf", rendered.pdf, headers)

    def _finish_url(self, profile_id: str | None = None, job_identity: str | None = None) -> str:
        return finish_url(f"http://127.0.0.1:{self._bound_port()}", profile_id, job_identity)

    def _handle_post_tailored_resume_pdf(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if type(body) is not dict or not _TAILORED_PDF_KEYS <= set(body) <= _TAILORED_PDF_KEYS | {"header"}:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "body must be exactly profile_id and job_identity (plus header from the Generate PDF form)")
            return
        profile_id, job_identity = body["profile_id"], body["job_identity"]
        if not isinstance(profile_id, str) or not profile_id or not isinstance(job_identity, str) or not job_identity:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "profile_id and job_identity must be non-empty strings")
            return
        ok, form = self._header_form(body)
        if not ok:
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
            rendered, file_name = stored_resume_pdf(items[0], home_root=home_root, form=form)
        except Exception:  # noqa: BLE001 - a render failure is typed, and never echoes the resume or the form
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "pdf_render_failed", "the PDF could not be rendered")
            return
        self._write_pdf(rendered, file_name, finish=None if form is not None else self._finish_url(profile_id, job_identity))

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

        Body ``{markdown, spacing_scale?, auto_fit?, profile_id?, header?}``.  The layout comes from the
        saved display settings, ``spacing_scale`` / ``auto_fit`` override it for this render only;
        ``header`` (the Generate PDF form, 0110-046) fills this PDF's header with the profile's saved
        title, and without it the PDF has no header.  Nothing is stored, sent to a model, or logged.
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
        ok, form = self._header_form(body)
        if not ok:
            return
        home_root = getattr(self._backend, "home_root", None)
        if home_root is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a home path is required")
            return
        try:
            parse_resume_markdown(markdown)
        except ResumeMarkdownError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
            return
        try:
            rendered, file_name = markdown_resume_pdf(
                markdown, home_root=home_root, profile_id=profile_id, form=form, spacing_scale=spacing, auto_fit=auto_fit
            )
        except Exception:  # noqa: BLE001 - a render failure is typed, and never echoes the resume or the form
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "pdf_render_failed", "the PDF could not be rendered")
            return
        self._write_pdf(
            rendered, file_name, {"X-GigAI-Pages": str(rendered.pages), "X-GigAI-Spacing-Scale": f"{rendered.spacing_scale:g}"},
            finish=None if form is not None else self._finish_url(),
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
        home_root = self._backend.home_root

        def stored_items():
            return list_tailored_resumes(home_root, target, profile_id=body["profile_id"], job_identity=body["job_identity"])

        try:
            items = stored_items()
        except QuickAssessError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        if not items:
            self._error(HTTPStatus.NOT_FOUND, "tailored_resume_not_found", "no stored tailored resume for that profile and job")
            return
        # The revision check and the write are one step (0.1.10.7): the resume is read again under the store's write
        # lock, so a tailoring that lands between the two cannot be written over, and this choice cannot be either.
        failure: tuple[HTTPStatus, str, str] | None = None
        updated = None
        with tailored_resume_write_lock(tailored_resume_path(home_root, target, body["profile_id"], body["job_identity"])):
            try:
                items = stored_items()
            except QuickAssessError as exc:
                failure = (_status_for(exc.code), exc.code, str(exc))
            if failure is None and not items:
                failure = (HTTPStatus.NOT_FOUND, "tailored_resume_not_found", "no stored tailored resume for that profile and job")
            if failure is None:
                stored = items[0]
                if stored.updated_at != body["updated_at"]:
                    failure = (HTTPStatus.CONFLICT, "tailored_resume_changed", "a newer tailoring replaced this resume; reload it")
            if failure is None:
                try:
                    if custom:
                        # GigAI keeps no name (0110-046): a strictly name-shaped line is refused (``personal_info_found``).
                        updated = apply_line_edit(stored, body["line_id"], body["text"])
                    else:
                        updated = apply_line_choice(stored, body["line_id"], body["use"])
                except TailorError as exc:
                    failure = (_status_for(exc.code), exc.code, str(exc))
            if failure is None and updated is not stored:
                save_tailor_response(updated)
        if failure is not None:
            self._error(*failure)
            return
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
