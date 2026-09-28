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

journal-read-scope: the watchlist holds one record per board, ~10k once the
company catalog is seeded, so the full list is megabytes. Two query modes
keep a caller that does not need all of it off that cost:

* ``?summary=1`` answers ``{"schema_version": "scout-watchlist-summary:1",
  "total": N}`` -- the count of active entries, no entries.
* ``?limit=N`` (1..500) with an optional ``?offset=M`` answers one page of
  the same newest-first order, plus ``total``/``limit``/``offset``.

With no query the response is unchanged (every active entry). The ordered
list is kept per workpad, keyed by the workpad's exact journal head (the
mechanism ``server.py``'s selected-profile cache uses): a request against
an unchanged journal re-reads nothing, and any commit misses.

Every mutating call goes through the Handler's existing loopback + CSRF
guards (``do_POST``, same as every other state-changing route in this API).
Typed errors: ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

from http import HTTPStatus
import threading
from urllib.parse import parse_qs, urlsplit

from ....workpad import WorkpadError, resolve_workpad
from ..contracts import WatchlistEntry
from ..watchlist import WatchlistUrlError, add_company_from_url, list_active, watchlist_entry_from_url

WATCHLIST_RESPONSE_SCHEMA = "scout-watchlist-response:1"
WATCHLIST_ADD_RESPONSE_SCHEMA = "scout-watchlist-add-response:1"
WATCHLIST_SUMMARY_RESPONSE_SCHEMA = "scout-watchlist-summary:1"
WATCHLIST_PAGE_LIMIT_MAX = 500

_ACTIVE_ENTRIES_CACHE_LOCK = threading.Lock()
# One entry per workpad: ``path -> (journal head, entries newest first)``. A
# new head replaces the previous one, so this never holds more than the
# current list for each workpad this process serves.
_active_entries_cache: dict[str, tuple[str, tuple[dict[str, object], ...]]] = {}


def _entry_to_json(entry: WatchlistEntry) -> dict[str, object]:
    return entry.to_json()


def _active_entries_newest_first(home_root, resolved) -> tuple[dict[str, object], ...]:
    from .... import run

    key = str(resolved.path)
    head = run._cheap_workpad_head(resolved.path)
    if head is not None:
        with _ACTIVE_ENTRIES_CACHE_LOCK:
            cached = _active_entries_cache.get(key)
        if cached is not None and cached[0] == head:
            return cached[1]
    entries = list_active(home_root, resolved, resolved.gig_id)
    ordered = tuple(
        _entry_to_json(item)
        for item in sorted(entries, key=lambda item: item.first_seen.observed_at, reverse=True)
    )
    # Stored only when the head did not move during the read: a list read
    # at a newer head must never be filed under the older one.
    if head is not None and run._cheap_workpad_head(resolved.path) == head:
        with _ACTIVE_ENTRIES_CACHE_LOCK:
            _active_entries_cache[key] = (head, ordered)
    return ordered


def _query_int(query: dict[str, list[str]], name: str, *, minimum: int, maximum: int | None) -> int | None:
    """``None`` when absent; ``ValueError`` when present but not an integer in range."""

    values = query.get(name)
    if not values:
        return None
    text = values[-1]
    if not text.isascii() or not text.isdigit():
        raise ValueError(name)
    value = int(text)
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(name)
    return value


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
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        unknown = set(query) - {"summary", "limit", "offset"}
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query field(s): {sorted(unknown)}")
            return
        summary = query.get("summary", [None])[-1]
        if summary not in {None, "0", "1"}:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "summary must be 0 or 1")
            return
        try:
            limit = _query_int(query, "limit", minimum=1, maximum=WATCHLIST_PAGE_LIMIT_MAX)
            offset = _query_int(query, "offset", minimum=0, maximum=None)
        except ValueError as exc:
            self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_value",
                f"{exc} must be a whole number"
                + (f" from 1 to {WATCHLIST_PAGE_LIMIT_MAX}" if str(exc) == "limit" else " of 0 or more"),
            )
            return
        if summary == "1" and (limit is not None or offset is not None):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "summary takes no limit or offset")
            return
        if offset is not None and limit is None:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "offset needs a limit")
            return

        ordered = _active_entries_newest_first(self._backend.home_root, resolved)
        if summary == "1":
            self._write_json(HTTPStatus.OK, {"schema_version": WATCHLIST_SUMMARY_RESPONSE_SCHEMA, "total": len(ordered)})
            return
        if limit is None:
            self._write_json(HTTPStatus.OK, {"schema_version": WATCHLIST_RESPONSE_SCHEMA, "entries": list(ordered)})
            return
        start = offset or 0
        self._write_json(
            HTTPStatus.OK,
            {
                "schema_version": WATCHLIST_RESPONSE_SCHEMA,
                "entries": list(ordered[start : start + limit]),
                "total": len(ordered),
                "limit": limit,
                "offset": start,
            },
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


__all__ = [
    "WATCHLIST_ADD_RESPONSE_SCHEMA",
    "WATCHLIST_PAGE_LIMIT_MAX",
    "WATCHLIST_RESPONSE_SCHEMA",
    "WATCHLIST_SUMMARY_RESPONSE_SCHEMA",
    "WatchlistRoutesMixin",
]
