"""0110-046 packet 4: ``GET`` / ``PUT /api/privacy/cleanup`` -- the one-time contact cleanup's report.

The cleanup itself runs at ``gigai scout run`` and when the server process starts
(``present_api._start_contact_cleanup``; ``contact_cleanup.run_cleanup`` is idempotent and never raises).
``GET`` only reads its stored report: what kinds and how many contact details were removed, and where
(counts only, never a value), with ``shown`` (``status: "not_run"`` before the first cleanup). The UI shows
the report once and then ``PUT``s ``{"shown": true}``. ``GET`` runs the Host check like every read of the
home's settings; ``PUT`` goes through ``_check_csrf``. Nothing here logs a value.
"""

from __future__ import annotations

from http import HTTPStatus

from ...contact_cleanup import REPORT_SCHEMA, current_report, mark_shown


class PrivacyCleanupRoutesMixin:
    """``Handler`` mixin: ``GET`` and ``PUT /api/privacy/cleanup``."""

    def _handle_get_privacy_cleanup(self) -> None:
        home_root = getattr(self._backend, "home_root", None)
        if home_root is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a home path is required")
            return
        self._write_json(HTTPStatus.OK, current_report(home_root) or {"schema_version": REPORT_SCHEMA, "status": "not_run", "removed_any": False, "shown": True})

    def _handle_put_privacy_cleanup(self) -> None:
        home_root = getattr(self._backend, "home_root", None)
        if home_root is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a home path is required")
            return
        body = self._read_json_body()
        if body is None:
            return
        if type(body) is not dict or set(body) != {"shown"} or body["shown"] is not True:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", 'body must be exactly {"shown": true}')
            return
        report = current_report(home_root)
        if report is None:
            self._error(HTTPStatus.CONFLICT, "cleanup_not_run", "the contact cleanup has not run yet; GET /api/privacy/cleanup first")
            return
        if not mark_shown(home_root):
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "cleanup_state_unwritable", "could not record that the report was shown")
            return
        self._write_json(HTTPStatus.OK, current_report(home_root) or report)


__all__ = ["PrivacyCleanupRoutesMixin"]
