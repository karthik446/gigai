"""Static UI serving from present_api.py (packet A of `gigai scout run`, U13).

The packaged Vite build under ``src/gigai/scout/ui/dist`` is served at ``/``
and ``/assets/*`` via importlib.resources, so this works from an installed
wheel, not just a source checkout. ``/api/*`` behavior is untouched; these
tests only cover the new static-serving surface plus one "still works"
smoke check on an existing API route.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from gigai.scout.find_jobs import present_api
from gigai.scout.find_jobs.present_api import NotWiredBackend, serve


@pytest.fixture
def running_server():
    server = serve(backend=NotWiredBackend(), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            yield client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_index_served_at_root(running_server) -> None:
    response = running_server.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert "<div id=\"root\">" in response.text


def test_asset_served_with_correct_content_type(running_server) -> None:
    index = running_server.get("/").text
    # Pull the built, hashed asset filenames straight out of index.html so
    # this test doesn't hardcode a hash that changes on every UI edit.
    import re

    js_match = re.search(r'src="(/assets/[^"]+\.js)"', index)
    css_match = re.search(r'href="(/assets/[^"]+\.css)"', index)
    assert js_match is not None, index
    assert css_match is not None, index

    js_response = running_server.get(js_match.group(1))
    assert js_response.status_code == 200
    assert js_response.headers["content-type"] == "text/javascript; charset=utf-8"
    assert len(js_response.content) > 0

    css_response = running_server.get(css_match.group(1))
    assert css_response.status_code == 200
    assert css_response.headers["content-type"] == "text/css; charset=utf-8"
    assert len(css_response.content) > 0


@pytest.mark.parametrize(
    "path",
    [
        "/../../../etc/passwd",
        "/assets/../../../../etc/passwd",
        "/%2e%2e/%2e%2e/etc/passwd",
    ],
)
def test_path_traversal_is_rejected(running_server, path: str) -> None:
    response = running_server.get(path)
    assert response.status_code in (404, 400)
    assert "root:" not in response.text


def test_unknown_static_path_is_404(running_server) -> None:
    response = running_server.get("/does-not-exist.txt")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"


def test_missing_dist_returns_503_at_root(running_server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(present_api, "_UI_DIST_RELATIVE_PARTS", ("ui", "dist-does-not-exist"))
    response = running_server.get("/")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "ui_not_built"
    assert "yarn build" in body["error"]["message"]


def test_missing_dist_still_404s_other_static_paths(
    running_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(present_api, "_UI_DIST_RELATIVE_PARTS", ("ui", "dist-does-not-exist"))
    response = running_server.get("/assets/whatever.js")
    assert response.status_code == 404


def test_missing_dist_does_not_break_api_routes(
    running_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(present_api, "_UI_DIST_RELATIVE_PARTS", ("ui", "dist-does-not-exist"))
    response = running_server.get("/api/config")
    # NotWiredBackend raises NotImplementedError inside read_config(), which
    # the do_GET boundary turns into a 500 -- the point here is only that it
    # is *not* the 503 "ui not built" path: /api/* is routed independently
    # of static-dist availability.
    assert response.status_code == 500


def test_api_route_unaffected_by_static_routing(running_server) -> None:
    # /api/* still 404s for an unknown API route rather than falling through
    # to static serving or a 503.
    response = running_server.get("/api/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
