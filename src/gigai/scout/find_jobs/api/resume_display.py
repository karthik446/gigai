"""0110-003 P3: ``GET`` / ``PUT /api/resume-display`` -- the per-profile titles printed under the
name on a resume PDF, plus its layout (0110-017: ``spacing_scale`` 0.7..1.4 and ``auto_fit``).

0110-046: GigAI stores no name or contact details.  The PDF header's name and contact items are
typed in the Generate PDF form for one render (``POST /api/tailored-resumes/pdf`` /
``POST /api/resume/pdf`` with ``header``) and never saved.  ``PUT`` still accepts the old
``name`` / ``contact`` keys so an older client keeps working, but ignores them: nothing of them
is stored, and the response says so (``ignored``).

Stored once per home by ``gigai.scout.resume_display`` (``<home>/scout/resume-display.json``,
0600); this module is only the HTTP wiring.  ``GET`` returns the saved values (or
``saved: false``); there is no ``suggested`` prefill any more (it was parsed from the stored
resume header, which 0110-046 no longer keeps).  ``do_GET`` runs the Host check
(``_check_host``) before it; ``PUT`` goes through ``_check_csrf``.  Nothing here logs a value.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ...resume_display import (
    LEGACY_CONTACT_KEYS,
    SPACING_DEFAULT,
    SPACING_MAX,
    SPACING_MIN,
    DisplaySettings,
    load_display,
    profile_title,
    save_display,
    valid_spacing,
)

_PUT_KEYS = frozenset({"titles", "spacing_scale", "auto_fit", *LEGACY_CONTACT_KEYS})
#: The quiet note a ``PUT`` with an old ``name`` / ``contact`` key gets back.
IGNORED_NOTE = "GigAI no longer stores your name or contact details; you type them when you generate a PDF."


def settings_json(settings: DisplaySettings | None, profile_id: str | None, ignored: list[str] | None = None) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "scout-resume-display-response:1",
        "saved": settings is not None,
        "titles": dict(settings.titles) if settings else {},
        "title": profile_title(settings, profile_id),
        "spacing_scale": settings.spacing_scale if settings else SPACING_DEFAULT,
        "auto_fit": settings.auto_fit if settings else True,
        "updated_at": settings.updated_at if settings else "",
    }
    if ignored:
        body["ignored"] = ignored
        body["note"] = IGNORED_NOTE
    return body


class ResumeDisplayRoutesMixin:
    """``Handler`` mixin: ``GET`` and ``PUT /api/resume-display``."""

    def _display_home(self):
        home_root = getattr(self._backend, "home_root", None)
        if home_root is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a home path is required")
        return home_root

    def _handle_get_resume_display(self) -> None:
        home_root = self._display_home()
        if home_root is None:
            return
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        profile_id = (query.get("profile_id") or [None])[0]
        self._write_json(HTTPStatus.OK, settings_json(load_display(home_root), profile_id))

    def _handle_put_resume_display(self) -> None:
        home_root = self._display_home()
        if home_root is None:
            return
        body = self._read_json_body()
        if body is None:
            return
        if type(body) is not dict:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be an object")
            return
        unknown = sorted(set(body) - _PUT_KEYS)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown key: {unknown[0]}")
            return
        # 0110-046: an older client's name / contact are accepted and dropped unread.
        ignored = sorted(key for key in LEGACY_CONTACT_KEYS if key in body)
        current = load_display(home_root) or DisplaySettings()
        titles = dict(current.titles)
        if "titles" in body:
            raw_titles = body["titles"]
            if type(raw_titles) is not dict or not all(isinstance(v, str) for v in raw_titles.values()):
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "titles must be an object of strings")
                return
            titles.update(raw_titles)  # per-profile merge; an empty value clears that profile's title
        spacing = body.get("spacing_scale", current.spacing_scale)
        if type(spacing) not in (int, float):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "spacing_scale must be a number")
            return
        if not valid_spacing(spacing):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", f"spacing_scale must be between {SPACING_MIN} and {SPACING_MAX}")
            return
        auto_fit = body.get("auto_fit", current.auto_fit)
        if type(auto_fit) is not bool:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "auto_fit must be true or false")
            return
        try:
            saved = save_display(home_root, DisplaySettings(titles, spacing_scale=float(spacing), auto_fit=auto_fit))
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "display_write_failed", "could not save the resume display settings")
            return
        self._write_json(HTTPStatus.OK, settings_json(saved, None, ignored))


__all__ = ["IGNORED_NOTE", "ResumeDisplayRoutesMixin", "settings_json"]
