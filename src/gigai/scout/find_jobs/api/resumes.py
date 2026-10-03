"""uat-bug-020: ``POST /api/resumes`` -- the setup wizard stores the resume itself.

Body: exactly one of

* ``{"text": "<the pasted resume>"}``, or
* ``{"file_name": "<name>.md", "content_base64": "<the file's bytes>"}`` (a
  file the browser read; ``.txt``, ``.md`` or ``.markdown``, the formats
  ``gigai scout resume add`` accepts).

The resume is imported through ``resume_import``, the same function
``gigai scout resume add`` calls, so it becomes the same kind of reference
and record. Response: ``201`` when something was stored, ``200`` when this
exact resume already was (idempotent by content), with ``{"resume_ref":
{"record_id", "revision_id", "content_sha256"}, "label", "created", "contact_removed"}``
(0110-046: the import stores the resume with its name and contact lines removed;
``contact_removed`` is ``{"removed": {kind: count}, "message"}`` or null). The
ids are what ``POST /api/profiles`` / ``PUT /api/profiles/{id}`` take as
``resume_record_id`` / ``resume_revision_id``; this route never touches a
profile.

The resume stays on this machine: the route is loopback + CSRF guarded like
every other mutating route, calls no model and no network, and never echoes
or logs a byte of the resume or its file name (the log line carries the
source kind and whether anything new was stored).
"""

from __future__ import annotations

import base64
import binascii
from http import HTTPStatus
from typing import Mapping

from ....private_records import PrivateRecordError
from ....workpad import WorkpadError
from ...resume_import import (
    PASTED_RESUME_FILE_NAME,
    RESUME_MAX_BYTES,
    ImportedResume,
    ResumeImportError,
    import_resume_bytes,
    safe_resume_file_name,
)
from ...resume_pii import REMOVED_MESSAGE
from .server import _logger

SCHEMA_VERSION = "scout-resume-import-response:1"

SOURCE_PASTED = "pasted"
SOURCE_UPLOADED = "uploaded"

_ERROR_STATUS: dict[str, HTTPStatus] = {
    "wrong_type": HTTPStatus.UNPROCESSABLE_ENTITY,
    "unknown_key": HTTPStatus.UNPROCESSABLE_ENTITY,
    "resume_input_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "resume_invalid_utf8": HTTPStatus.UNPROCESSABLE_ENTITY,
    "resume_media_type_unsupported": HTTPStatus.UNPROCESSABLE_ENTITY,
    "resume_too_large": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    "target_unavailable": HTTPStatus.NOT_FOUND,
}

# private_records' own refusals for the same conditions this module checks
# first (kept mapped: the import path stays the authority).
_IMPORT_ERROR_CODES: dict[str, str] = {
    "reference_too_large": "resume_too_large",
    "reference_invalid_utf8": "resume_invalid_utf8",
    "reference_media_type_unsupported": "resume_media_type_unsupported",
    "reference_source_unsafe": "resume_input_invalid",
}


def _status_for(code: str) -> HTTPStatus:
    return _ERROR_STATUS.get(code, HTTPStatus.CONFLICT)


def parse_request(body: object) -> tuple[bytes, str, str]:
    """``(resume bytes, file name, source kind)`` from a JSON body."""

    if not isinstance(body, Mapping):
        raise ResumeImportError("wrong_type", "request body must be a JSON object")
    unknown = set(body) - {"text", "file_name", "content_base64"}
    if unknown:
        raise ResumeImportError("unknown_key", f"unknown field(s): {sorted(unknown)}")
    has_text = "text" in body
    has_file = "file_name" in body or "content_base64" in body
    if has_text == has_file:
        raise ResumeImportError(
            "resume_input_invalid", "pass exactly one of text, or file_name with content_base64"
        )

    if has_text:
        text = body["text"]
        if not isinstance(text, str):
            raise ResumeImportError("wrong_type", "text must be a string")
        try:
            data = text.encode("utf-8")
        except UnicodeEncodeError:
            raise ResumeImportError("resume_invalid_utf8", "the resume must be UTF-8 text") from None
        file_name, source = PASTED_RESUME_FILE_NAME, SOURCE_PASTED
    else:
        raw_name = body.get("file_name")
        encoded = body.get("content_base64")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ResumeImportError("resume_input_invalid", "file_name must be a non-empty string")
        if not isinstance(encoded, str):
            raise ResumeImportError("resume_input_invalid", "content_base64 must be a string")
        file_name = safe_resume_file_name(raw_name)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ResumeImportError("resume_input_invalid", "content_base64 is not valid base64") from None
        source = SOURCE_UPLOADED

    if len(data) > RESUME_MAX_BYTES:
        raise ResumeImportError("resume_too_large", "the resume is larger than 1 MB")
    try:
        decoded = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ResumeImportError("resume_invalid_utf8", "the resume must be UTF-8 text") from None
    if not decoded.strip():
        raise ResumeImportError("resume_input_invalid", "the resume is empty")
    return data, file_name, source


def resume_response(resume: ImportedResume) -> dict[str, object]:
    """The public shape: ids, digest and label, never the resume's text."""

    return {
        "schema_version": SCHEMA_VERSION,
        "resume_ref": {
            "record_id": resume.record_id,
            "revision_id": resume.revision_id,
            "content_sha256": resume.content_sha256,
        },
        "label": resume.label,
        "created": resume.created,
        # 0110-046: the contact lines the import removed and discarded (counts only), or null.
        "contact_removed": {"removed": resume.contact_removed, "message": REMOVED_MESSAGE} if resume.contact_removed else None,
    }


class ResumesRoutesMixin:
    """``Handler`` mixin: ``POST /api/resumes``."""

    def _handle_post_resumes(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            data, file_name, source = parse_request(body)
        except ResumeImportError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return

        backend = self._backend
        target = getattr(backend, "target", None)
        home_root = getattr(backend, "home_root", None)
        if target is None or home_root is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return

        try:
            resume = import_resume_bytes(
                home_root=home_root, requested_target=target, data=data, file_name=file_name
            )
        except ResumeImportError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        except PrivateRecordError as exc:
            code = _IMPORT_ERROR_CODES.get(exc.code, exc.code)
            _logger.warning("resume not stored: source=%s code=%s", source, exc.code)
            self._error(_status_for(code), code, str(exc))
            return
        except WorkpadError as exc:
            _logger.warning("resume not stored: source=%s code=%s", source, getattr(exc, "code", "workpad_unavailable"))
            self._error(
                HTTPStatus.NOT_FOUND,
                "target_unavailable",
                "no Scout gig is available for this folder; run `gigai scout run` to set it up",
            )
            return

        # The source kind and whether anything new was stored -- never the
        # resume's text, size or file name.
        _logger.info("resume stored: source=%s created=%s", source, resume.created)
        self._write_json(
            HTTPStatus.CREATED if resume.created else HTTPStatus.OK,
            resume_response(resume),
        )


__all__ = [
    "SCHEMA_VERSION",
    "SOURCE_PASTED",
    "SOURCE_UPLOADED",
    "ResumesRoutesMixin",
    "parse_request",
    "resume_response",
]
