"""0.1.11.3 item 13: ``POST /api/pdf-header`` -- the Generate PDF form's prefill from the user's own header file.

The person keeps their name and contact details in a JSON file they own (``pdf_header_file``; default
``~/Documents/GigAI/header.json``).  When the Generate PDF form opens, Scout's browser page asks this route
for the file's values, shows them in the form ("Filled from <path>", editable) and sends what the person
left there with the one render request.  GigAI reads the file here and nowhere else in the server; the
values are not stored, logged or cached.

**The browser page's own route, nobody else's.**  It answers only a request that carries this server's own
``Origin``: a browser page always sends ``Origin`` on a write, ``_check_csrf`` has already refused any origin
but this server's, and a request without one (curl, a script, the user's agent: the same rule as
``_story_bank_actor``) is refused with 403 ``forbidden_origin`` before the file is opened.  It is a ``POST``
for that reason (a same-origin ``GET`` carries no ``Origin``), and so that the values never sit in a URL, a
cache or a link an agent is handed.  No other route, brief, record or prompt holds them.

A dedicated route, not the PDF routes' own request: the form has to SHOW the values so the person can
edit them before generating, so they must reach the page; reading the file inside ``POST .../pdf`` would
print values the person never saw and would let any caller of that route (an agent's headerless render
included) make a PDF with them.

It is the one JSON response that does not pass ``outbound_check.redact_payload``: that check would turn the
email, phone and links into tokens, and this response IS the person's own contact details going to their own
form.  It is written with ``Cache-Control: no-store``.  A missing, unreadable or invalid file is a 200 with
``state`` and one plain ``message`` (what is wrong, where the file goes), never an error page.

0.1.11.3 item 14: ``POST /api/pdf-header/save`` -- the form's "Save these details to <path>" button.  The same
caller rule (this server's own ``Origin``, checked before the body is read), and the one place the server writes
the file: ``pdf_header_save``, once per click, 0600, atomic, never over an existing file unless the request carries
``replace`` (the form asks first).  The request's values go into that file and nowhere else: this handler does not
log them, and its answer (``state``, the path, one plain sentence) holds none of them, so it leaves through the
ordinary ``_write_json``.

0.1.11.5 PH: the job page's PREVIEW (``POST /api/tailored-resumes/preview``) shows the person's header from the same
file, read by ``_preview_file_header`` below for that one render: the same caller rule (this server's own ``Origin``,
checked before the file is opened), the same reader and its skipping rules, and the command's rule that a header
needs a name.  The values go into the page PICTURES of that one answer and nowhere else; any other caller, and a
file that cannot make a header, gets the preview's placeholder header, never an error.
"""

from __future__ import annotations

from http import HTTPStatus
import json
from pathlib import Path
from urllib.parse import urlsplit

from ...data_labels import LABELS_HEADER
from ...pdf_header_file import HeaderFileError, default_path, prefill_response, read_header_file, render_form
from ...pdf_header_save import SAVED_FIELDS, HeaderSaveError, save_header_file
from .openapi import response_labels_header


class PdfHeaderRoutesMixin:
    """``Handler`` mixin: ``POST /api/pdf-header`` and ``POST /api/pdf-header/save``."""

    def _handle_post_pdf_header(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if body != {}:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "body must be {}")
            return
        if not self.headers.get("Origin"):
            # Not Scout's page (``_check_csrf`` has already matched an Origin that IS sent): the file is not opened.
            self._log_rejection("forbidden_origin: the header file is read for Scout's own page only")
            self._error(HTTPStatus.FORBIDDEN, "forbidden_origin", "this route answers Scout's own browser page only; an agent makes the PDF without a header")
            return
        home_root = getattr(self._backend, "home_root", None)
        if home_root is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a home path is required")
            return
        payload = json.dumps(prefill_response(read_header_file(default_path(home_root)))).encode("utf-8")
        self._write_bytes(
            HTTPStatus.OK, "application/json", payload,
            {LABELS_HEADER: response_labels_header(self.command or "", urlsplit(self.path).path), "Cache-Control": "no-store"},
        )

    def _preview_file_header(self) -> dict[str, object] | None:
        """The header file's values for ONE preview render (0.1.11.5 PH), or ``None``: the preview shows the placeholder.

        ``None`` for a caller that is not Scout's own page (no ``Origin``: curl, a script, the user's agent; the file is
        then not opened), and for a file that is missing, invalid, all placeholders or has no usable name.  Nobody edits
        a form here, so the values are the command's (``pdf_header_file.render_form``): the file, then the profile's
        sponsorship answer for the work authorization line.  Nothing is written or logged, and the caller returns
        page pictures only."""

        if not self.headers.get("Origin"):
            return None
        home_root, target = getattr(self._backend, "home_root", None), getattr(self._backend, "target", None)
        if home_root is None:
            return None
        found = read_header_file(default_path(home_root))
        if not found.filled:
            return None
        from ..resume_input import read_config_preferences

        try:
            return render_form(found, visa_required=read_config_preferences(target)[0] if target is not None else False)
        except HeaderFileError:
            return None

    def _handle_post_pdf_header_save(self) -> None:
        if not self.headers.get("Origin"):
            # Not Scout's page: the body is not read and nothing is written.
            self._log_rejection("forbidden_origin: the header file is written for Scout's own page only")
            self._error(HTTPStatus.FORBIDDEN, "forbidden_origin", "this route answers Scout's own browser page only; an agent never writes the person's header file")
            return
        body = self._read_json_body()
        if body is None:
            return
        if type(body) is not dict:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be an object")
            return
        if any(key not in (*SAVED_FIELDS, "replace") for key in body):
            # The keys are not echoed: a value typed where a key goes would be.
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", "body holds a key that is not a field of the header file")
            return
        replace = body.get("replace", False)
        if type(replace) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "replace must be true or false")
            return
        home_root = getattr(self._backend, "home_root", None)
        if home_root is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a home path is required")
            return
        path = default_path(home_root)
        try:
            # The folder is made only when it is the default location (~/Documents/GigAI), never a GigAI home.
            saved = save_header_file(
                path, {key: value for key, value in body.items() if key != "replace"}, replace=replace,
                create_folder=path.parent != Path(home_root).expanduser(),
            )
        except HeaderSaveError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", f"your details were not saved: {exc}")
            return
        self._write_json(HTTPStatus.OK, saved.to_json())


__all__ = ["PdfHeaderRoutesMixin"]
