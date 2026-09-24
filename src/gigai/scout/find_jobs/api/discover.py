"""R0: the Discover-companies routes (``POST /api/discover``,
``GET /api/discover/latest``), moved out of ``present_api.py`` verbatim.
"""

from __future__ import annotations

from http import HTTPStatus

from .common import _days_ago
from .server import DiscoveryConflictError, DiscoveryUnavailableError, SetupPrefsMissingError, _logger


class DiscoverRoutesMixin:
    """``Handler`` mixin: ``POST /api/discover``, ``GET /api/discover/latest``."""

    def _handle_post_discover(self) -> None:
        # uat-bug-003 follow-up: "discover finished"/"discover failed" is
        # logged by the backend's start_discovery, once run_discovery
        # returns (or raises) -- that is the first point cost_usd is
        # known at all; this progress callback only ever sees
        # run_discovery's raw, small progress-step events (no cost_usd),
        # so it is never the right place to log the terminal outcome and
        # no longer duplicates it here.
        def _on_progress(event: dict[str, object]) -> None:
            pass

        try:
            request_id = self._backend.start_discovery(_on_progress)
        except DiscoveryUnavailableError as exc:
            _logger.warning("discover failed to start: discovery_unavailable (%s)", exc)
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "discovery_unavailable", str(exc))
            return
        except DiscoveryConflictError as exc:
            _logger.warning("discover failed to start: discovery_running (%s)", exc)
            self._error(HTTPStatus.CONFLICT, "discovery_running", str(exc))
            return
        except SetupPrefsMissingError as exc:
            _logger.warning("discover failed to start: prefs_missing (%s)", exc)
            self._error(HTTPStatus.NOT_FOUND, "prefs_missing", str(exc))
            return
        _logger.info("discover started: discovery_id=%s", request_id)
        self._write_json(HTTPStatus.ACCEPTED, {"discovery_id": request_id})

    def _handle_get_discover_latest(self) -> None:
        try:
            result_json = self._backend.latest_discovery()
            running = self._backend.discovery_running()
        except DiscoveryUnavailableError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "discovery_unavailable", str(exc))
            return
        days_ago = None
        if result_json is not None:
            finished_at = result_json.get("finished_at")
            if isinstance(finished_at, str):
                days_ago = _days_ago(finished_at)
        self._write_json(
            HTTPStatus.OK,
            {
                "schema_version": "scout-find-jobs-discover-latest-response:1",
                "result": result_json,
                "running": running,
                "days_ago": days_ago,
            },
        )
