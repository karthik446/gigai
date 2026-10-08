"""0.1.11.7 FS1: ``GET /api/search``, the free search over every stored posting.

``find_jobs/free_search.py`` is the builder; ``gigai scout jobs search --json``
prints the same object. Typed titles (comma separated, the strict rule),
company and location words (WHOLE words), the default profile's filters unless
``all=1``, newest posted first, a page of 50. It takes NO profile: the search
is not a profile's list. It makes no board request, calls no model, refreshes
nothing and writes nothing.

PAGE FIRST, COUNT AFTER: without ``count=1`` the rows are answered as soon as
the page is read and ``counts.total`` is null (unless the scan answered, which
knows it); the same request with ``count=1`` adds ``counts.total``,
``total_all`` and ``hidden``.

No response mixes: posting text (public-untrusted) beside ids, counts, codes
and the profiles' labels.
"""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from ...data_labels import LabelError
from ..free_search import DEFAULT_LIMIT, FreeSearchError, SearchRequest, search, to_json

_FLAGS = {"1": True, "true": True, "0": False, "false": False}
_QUERY_KEYS = frozenset({"title", "company", "location", "all", "removed", "limit", "offset", "count"})
_ERROR_STATUS = {"invalid_value": HTTPStatus.UNPROCESSABLE_ENTITY, "config_unavailable": HTTPStatus.CONFLICT}


class SearchRoutesMixin:
    """``Handler`` mixin: ``GET /api/search``."""

    def _handle_get_search(self) -> None:
        target = getattr(self._backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
        unknown = sorted(set(query) - _QUERY_KEYS)
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown query key: {unknown[0]}")
            return
        flags: dict[str, bool] = {}
        for key in ("all", "removed", "count"):
            raw = (query.get(key) or ["0"])[0]
            if raw not in _FLAGS:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", f"{key} must be 1 or 0")
                return
            flags[key] = _FLAGS[raw]
        try:
            limit = int((query.get("limit") or [str(DEFAULT_LIMIT)])[0])
            offset = int((query.get("offset") or ["0"])[0])
        except ValueError:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "limit and offset must be whole numbers")
            return
        try:
            request = SearchRequest.typed(
                query.get("title"), company=query.get("company"), location=query.get("location"), show_all=flags["all"],
                include_removed=flags["removed"], limit=limit, offset=offset, count=flags["count"],
            )
            response = to_json(search(self._backend.home_root, request, target=target))
        except FreeSearchError as exc:
            self._error(_ERROR_STATUS.get(exc.code, HTTPStatus.CONFLICT), exc.code, str(exc))
            return
        except LabelError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "labels_mixed", "the response was withheld: it mixed posting text with private text")
            return
        self._write_json(HTTPStatus.OK, response)


__all__ = ["SearchRoutesMixin"]
