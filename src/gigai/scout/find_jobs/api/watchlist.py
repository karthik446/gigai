"""Q1 (v0.1.9, SCOPE-ADD-2): ``GET``/``POST /api/watchlist`` -- "Add company"
by board URL, and the list the UI shows next to it.

``POST`` accepts ``{"url": "<Greenhouse | Lever | Ashby board or job URL>"}``
and records the board as an active watchlist entry through the ONE function
the CLI (``gigai scout watchlist add``) uses too:
``find_jobs.watchlist.add_company_from_url`` (which itself reuses
``contracts.parse_board_url`` / ``normalize_url`` -- no second host rule
lives here). A URL on any other host is a typed 422
``unsupported_board_host``; an unusable URL is 422 ``invalid_value``. The
add is idempotent (the journal's ``scout_watchlist:<provider>:<token>``
receipt key): a repeat POST for the same board answers 200 with the
ORIGINAL entry, a first-time add answers 201 -- ``created`` in the body says
which, so a UI can word its confirmation honestly.

``GET`` returns every ACTIVE entry (``find_jobs.watchlist.list_active``),
newest-first by ``first_seen.observed_at``, for the "Add company" form's
"already watching" list.

Every mutating call goes through the Handler's existing loopback + CSRF
guards (``do_POST``, same as every other state-changing route in this API).
Typed errors: ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

from http import HTTPStatus

from ....workpad import WorkpadError, resolve_workpad
from ..contracts import WatchlistEntry
from ..watchlist import WatchlistUrlError, add_company_from_url, list_active, watchlist_entry_from_url

WATCHLIST_RESPONSE_SCHEMA = "scout-watchlist-response:1"
WATCHLIST_ADD_RESPONSE_SCHEMA = "scout-watchlist-add-response:1"


def _entry_to_json(entry: WatchlistEntry) -> dict[str, object]:
    return entry.to_json()


class WatchlistRoutesMixin:
    """``Handler`` mixin: ``GET``/``POST /api/watchlist``."""

    def _watchlist_target(self):
        backend = self._backend
        target = getattr(backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return None
        return target

    def _resolve_watchlist_gig(self):
        target = self._watchlist_target()
        if target is None:
            return None
        return resolve_workpad(
            home_root=self._backend.home_root, requested_target=target, gig_id=None, allow_semantic_state=True
        )

    def _handle_get_watchlist(self) -> None:
        try:
            resolved = self._resolve_watchlist_gig()
        except (LookupError, WorkpadError):
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return
        if resolved is None:
            return
        entries = list_active(self._backend.home_root, resolved, resolved.gig_id)
        ordered = sorted(entries, key=lambda item: item.first_seen.observed_at, reverse=True)
        self._write_json(
            HTTPStatus.OK,
            {"schema_version": WATCHLIST_RESPONSE_SCHEMA, "entries": [_entry_to_json(item) for item in ordered]},
        )

    def _handle_post_watchlist(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be a JSON object")
            return
        unknown = set(body) - {"url"}
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown field(s): {sorted(unknown)}")
            return
        url = body.get("url")
        if not isinstance(url, str) or not url.strip():
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "url must be a non-empty string")
            return
        # The URL rule is pure: a foreign host is 422 before any workpad
        # resolution, so a bad URL never surfaces as a 404/500 instead.
        try:
            watchlist_entry_from_url(url)
        except WatchlistUrlError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
            return

        try:
            resolved = self._resolve_watchlist_gig()
        except (LookupError, WorkpadError):
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return
        if resolved is None:
            return

        # Was this board already active before the add? Decides 201 vs 200
        # below -- the add itself is idempotent either way.
        before = {item.watchlist_id for item in list_active(self._backend.home_root, resolved, resolved.gig_id)}
        entry = add_company_from_url(url, self._backend.home_root, resolved, resolved.gig_id)
        created = entry.watchlist_id not in before
        self._write_json(
            HTTPStatus.CREATED if created else HTTPStatus.OK,
            {"schema_version": WATCHLIST_ADD_RESPONSE_SCHEMA, "created": created, "entry": _entry_to_json(entry)},
        )


__all__ = ["WATCHLIST_ADD_RESPONSE_SCHEMA", "WATCHLIST_RESPONSE_SCHEMA", "WatchlistRoutesMixin"]
