"""0110-003 P3: ``GET`` / ``PUT /api/resume-display`` -- the name, contact line and per-profile
titles printed on a tailored-resume PDF, plus its layout (0110-017: ``spacing_scale`` 0.7..1.4
and ``auto_fit``).

Stored once per home by ``gigai.scout.resume_display`` (``<home>/scout/resume-display.json``,
0600); this module is only the HTTP wiring.  ``GET`` returns the saved values (or
``saved: false``) plus a local ``suggested`` prefill while nothing is saved -- the prefill
is never written, only a user ``PUT`` saves.  ``GET`` returns personal values, so ``do_GET``
runs the Host check (``_check_host``) before it; ``PUT`` goes through ``_check_csrf``.
Nothing here logs a value.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ...resume_display import (
    KINDS,
    SPACING_DEFAULT,
    SPACING_MAX,
    SPACING_MIN,
    ContactEntry,
    DisplaySettings,
    Suggestion,
    load_display,
    save_display,
    suggest,
    valid_spacing,
)

_PUT_KEYS = frozenset({"name", "contact", "titles", "spacing_scale", "auto_fit"})


def _suggestion_json(suggestion: Suggestion) -> dict[str, object]:
    return {
        "name": suggestion.name,
        "title": suggestion.title,
        "contact": [{"kind": entry.kind, "value": entry.value} for entry in suggestion.contact],
    }


def suggestion_for_profile(backend: object, profile_id: str | None) -> Suggestion | None:
    """The local prefill from a profile's pinned resume header; ``None`` when unreadable.

    ``profile_id=None`` reads the selected profile.  Never raises, never logs a value.
    """

    home_root = getattr(backend, "home_root", None)
    target = getattr(backend, "target", None)
    if home_root is None or target is None:
        return None
    try:
        from ...interview_prep.resume import current_resume

        _identity, data = current_resume(home_root=home_root, requested_target=target, gig_id=None, profile_id=profile_id or None)
        return suggest(data.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - a prefill is a convenience; any failure means "no suggestion"
        return None


def settings_json(settings: DisplaySettings | None, profile_id: str | None, suggestion: Suggestion | None) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "scout-resume-display-response:1",
        "saved": settings is not None,
        "name": settings.name if settings else "",
        "contact": [{"kind": e.kind, "value": e.value} for e in (settings.contact if settings else ())],
        "titles": dict(settings.titles) if settings else {},
        "title": (settings.titles.get(profile_id or "", "") if settings else ""),
        "spacing_scale": settings.spacing_scale if settings else SPACING_DEFAULT,
        "auto_fit": settings.auto_fit if settings else True,
        "updated_at": settings.updated_at if settings else "",
        "kinds": list(KINDS),
    }
    if suggestion is not None:
        body["suggested"] = _suggestion_json(suggestion)
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
        settings = load_display(home_root)
        suggestion: Suggestion | None = None
        needs_title = settings is not None and bool(profile_id) and not settings.titles.get(profile_id or "")
        if settings is None or needs_title:
            found = suggestion_for_profile(self._backend, profile_id)
            if found is not None and settings is not None:
                found = Suggestion(title=found.title)  # saved name/contact are never overridden
            suggestion = found
        self._write_json(HTTPStatus.OK, settings_json(settings, profile_id, suggestion))

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
        current = load_display(home_root) or DisplaySettings()
        name = body.get("name", current.name)
        if not isinstance(name, str):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "name must be a string")
            return
        contact = current.contact
        if "contact" in body:
            raw = body["contact"]
            if type(raw) is not list:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "contact must be an array")
                return
            entries: list[ContactEntry] = []
            for item in raw:
                if type(item) is not dict or not isinstance(item.get("kind"), str) or not isinstance(item.get("value"), str):
                    self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "each contact item needs a string kind and value")
                    return
                if item["kind"] not in KINDS:
                    self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "bad_enum", "contact kind is not supported")
                    return
                entries.append(ContactEntry(item["kind"], item["value"]))
            contact = tuple(entries)
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
            saved = save_display(home_root, DisplaySettings(name, contact, titles, spacing_scale=float(spacing), auto_fit=auto_fit))
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "display_write_failed", "could not save the resume display settings")
            return
        self._write_json(HTTPStatus.OK, settings_json(saved, None, None))


__all__ = ["ResumeDisplayRoutesMixin", "settings_json"]
