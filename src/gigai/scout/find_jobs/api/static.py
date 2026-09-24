"""R0: the packaged UI static-file route, moved out of ``present_api.py``.

MONKEYPATCH TRAP: ``test_present_api_static.py`` patches
``present_api._UI_DIST_RELATIVE_PARTS`` (not this module's own copy), so
``_ui_dist_root`` below reads it back off the ``present_api`` module object
at call time rather than as a name bound into this module's globals -- a
patch on the shim must still take effect here. See ``present_api.py``'s own
docstring for the full rationale.

The packaged UI: built by `yarn build` in src/gigai/scout/ui and committed
under ui/dist (pyproject.toml package-data ships it in the wheel/sdist).
Read via importlib.resources, anchored on the real "gigai.scout" package
and joined onto "ui/dist" (ui/ itself has no __init__.py, so it isn't an
importable package/resource anchor of its own) so this works from an
installed wheel, not just a source checkout.
"""

from __future__ import annotations

import posixpath
from http import HTTPStatus
from importlib import resources
from pathlib import Path

_UI_DIST_ANCHOR_PACKAGE = "gigai.scout"
_UI_DIST_RELATIVE_PARTS = ("ui", "dist")
_UI_MISSING_MESSAGE = (
    "the Scout UI is not built: run `yarn build` in src/gigai/scout/ui "
    "(this is a source checkout without a built UI)"
)

_STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def _static_content_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return _STATIC_CONTENT_TYPES.get(suffix, "application/octet-stream")


def _ui_dist_root():
    """Return the packaged ``ui/dist`` resource root, or ``None`` if unbuilt.

    A source checkout without a build has no ``ui/dist`` directory at all
    (it's gitignored-turned-tracked only once built); an installed wheel
    always has it because package-data ships it. Either way, a missing or
    empty root is treated the same: not built.
    """

    # MONKEYPATCH TRAP (see module docstring): read the relative parts back
    # off gigai.scout.find_jobs.present_api's module globals, not this
    # module's own copy, so a test's monkeypatch.setattr(present_api,
    # "_UI_DIST_RELATIVE_PARTS", ...) still takes effect here.
    from .. import present_api

    try:
        root = resources.files(_UI_DIST_ANCHOR_PACKAGE)
    except ModuleNotFoundError:
        return None
    for part in present_api._UI_DIST_RELATIVE_PARTS:
        root = root.joinpath(part)
    if not root.is_dir():
        return None
    return root


def _resolve_static_resource(path: str):
    """Resolve a URL path to a traversal-safe resource under ``ui/dist``.

    Returns ``None`` if the dist root is missing, the path escapes the
    dist root, or the resource doesn't exist as a file. ``path`` is the
    already-percent-decoded, query-stripped request path (e.g. ``/`` or
    ``/assets/index-abc123.js``).
    """

    root = _ui_dist_root()
    if root is None:
        return None
    normalized = posixpath.normpath(path)
    if normalized in ("/", ".", ""):
        relative = "index.html"
    else:
        relative = normalized.lstrip("/")
    # normpath collapses ".." segments together, so any remaining ".."
    # component means the request tried to climb out of dist/; reject it
    # rather than resolve it away, to fail closed on traversal attempts.
    parts = relative.split("/")
    if ".." in parts or any(not part for part in parts):
        return None
    resource = root
    for part in parts:
        resource = resource.joinpath(part)
    if not resource.is_file():
        return None
    return resource


class StaticRoutesMixin:
    """``Handler`` mixin: the non-``/api/`` static-file fallthrough."""

    def _handle_get_static(self, path: str) -> None:
        if _ui_dist_root() is None:
            if path in ("/", ""):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "ui_not_built", _UI_MISSING_MESSAGE)
                return
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
            return
        resource = _resolve_static_resource(path)
        if resource is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
            return
        body = resource.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _static_content_type(resource.name))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
