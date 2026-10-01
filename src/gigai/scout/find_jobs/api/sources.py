"""N11-C: ``POST``/``GET /api/sources/update`` -- "Update sources".

Refreshing the sources is long work (one conditional request per watchlist
board at polite pacing: minutes), so it follows the pattern Discover already
uses in this API, not a blocking request:

* ``POST /api/sources/update`` starts the update on a background thread and
  answers ``202 {"update_id", "status": "running"}`` at once; ``409
  sources_update_running`` while one is live (``{"force": true}`` in the
  body overrides a stale one). The body may be empty. The update is
  incremental (boards checked within the stale window are left alone);
  ``{"full_refresh": true}`` asks every board.
* ``GET /api/sources/update`` answers ``200 {"running", "update", "index"}``:
  ``update`` is the running (or last finished) update's snapshot, ``null``
  when none has ever run; ``index`` says whether a search can read the
  stored postings (``needs_update`` + the message to show when it cannot).
  0110-025 adds ``background`` (additive): the hourly refresh (on or off,
  its state, the last update and who started it, the next tick time, whether
  an update is in progress) and the tag-store and text-index counts.
  0110-026 adds ``snapshot`` (additive): the metadata snapshot in use (its
  ``as_of`` and where it came from), the last attempt and its result, and
  whether the download is turned on. Reading it makes no request.

The update itself is ``find_jobs.sources_update`` (the CLI's ``gigai scout
sources update`` calls the same function); the snapshot lives beside the
company index under the GigAI home, so both routes are read-only on the
workpad apart from the watchlist seeding an acquire pass already does.

Every mutating call goes through the Handler's existing loopback + CSRF
guards. Typed errors: ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

from http import HTTPStatus

from ..sources_update import (
    SOURCES_UPDATE_STATUS_SCHEMA,
    SourcesUpdateRunningError,
    read_status,
    start_background_update,
)
from .server import _logger

SOURCES_UPDATE_START_SCHEMA = "scout-sources-update-start-response:1"


def _board_http_client():
    # The same client (and the same inert-unless-set test transport seam)
    # a find-jobs run's acquire node uses.
    from ..bindings import _http_client

    return _http_client()


class SourcesRoutesMixin:
    """``Handler`` mixin: ``POST``/``GET /api/sources/update``."""

    def _sources_home(self):
        home_root = getattr(self._backend, "home_root", None)
        if home_root is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a GigAI home is required")
        return home_root

    def _sources_target(self):
        target = getattr(self._backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return None
        return target

    def _handle_post_sources_update(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be a JSON object")
            return
        unknown = set(body) - {"force", "full_refresh"}
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown field(s): {sorted(unknown)}")
            return
        force = body.get("force", False)
        if not isinstance(force, bool):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "force must be true or false")
            return
        full_refresh = body.get("full_refresh", False)
        if not isinstance(full_refresh, bool):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "full_refresh must be true or false")
            return
        home_root = self._sources_home()
        if home_root is None:
            return
        target = self._sources_target()
        if target is None:
            return
        # The roles only decide which Greenhouse descriptions are fetched
        # now; without a usable config the update still lists every board.
        try:
            config, _config_bytes = self._backend.read_config()
        except Exception as exc:  # noqa: BLE001 - missing/invalid config or profile: list the boards anyway
            _logger.warning("sources update: no usable find-jobs config (%s); listing boards only", type(exc).__name__)
            config = None

        def _finished(snapshot: dict[str, object]) -> None:
            log = _logger.warning if snapshot.get("status") == "failed" else _logger.info
            log(
                "sources update %s: update_id=%s boards=%s %s",
                snapshot.get("status"),
                snapshot.get("update_id"),
                snapshot.get("boards"),
                snapshot.get("summary"),
            )

        try:
            update_id = start_background_update(
                home_root=home_root,
                target=target,
                client_factory=_board_http_client,
                config=config,
                force=force,
                full_refresh=full_refresh,
                on_finished=_finished,
            )
        except SourcesUpdateRunningError as exc:
            _logger.warning("sources update failed to start: %s", exc.code)
            self._error(HTTPStatus.CONFLICT, exc.code, str(exc))
            return
        _logger.info("sources update started: update_id=%s", update_id)
        self._write_json(
            HTTPStatus.ACCEPTED,
            {"schema_version": SOURCES_UPDATE_START_SCHEMA, "update_id": update_id, "status": "running"},
        )

    def _handle_get_sources_update(self) -> None:
        home_root = self._sources_home()
        if home_root is None:
            return
        from ..refresh_tick import background_status
        from ..snapshot import snapshot_status

        target = getattr(self._backend, "target", None)
        status = read_status(home_root)
        status["background"] = background_status(
            home_root,
            target,
            ticker=getattr(self.server, "refresh_ticker", None),
        )
        status["snapshot"] = snapshot_status(home_root, target)
        self._write_json(HTTPStatus.OK, status)


__all__ = ["SOURCES_UPDATE_START_SCHEMA", "SOURCES_UPDATE_STATUS_SCHEMA", "SourcesRoutesMixin"]
