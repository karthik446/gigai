"""0.1.11.10 Part A: the learning-pathway routes over HTTP against the real supervised server.

``GET /api/learning/pathways``, ``GET /api/learning/pathways/{pathway_id}`` and the course files under
``/learning/{pathway_id}/``. The course is SYNTHETIC (``make_course``, built in ``tmp_path``) and imported through
``learning_store`` while the server runs: the routes read the store's files, nothing is cached.

1. An empty store lists nothing; an unknown id and a text that is not an id are 404; a query key is 422.
2. After an import and a request: the list (newest first) and the single read answer the documented fields, the
   ``done`` one with ``url``, the ``queued`` one with ``course`` and ``url`` null.
3. A course file is served with the EXACT Content-Security-Policy and the three other headers, the right
   Content-Type per kind, its bytes unchanged; ``/learning/<id>/`` is the entry page; ``HEAD`` answers the same
   headers and no body.
4. 404, with no course byte in the answer: an unknown id, a pathway that is ``queued`` or ``failed`` (even with
   files on disk), a missing file, a folder, a dotfile, a file kind that is not served, and every way out of the
   course: ``../``, ``%2e%2e``, ``%2f``, a doubly encoded dot, a backslash, an absolute path, a link.
5. A request whose ``Host`` is not this server is refused (403) on ``/learning/`` like on every other route, for
   ``GET`` and ``HEAD``; ``HEAD`` of a path that is not a course file is 405.
"""

from __future__ import annotations

import dataclasses
import http.client
from pathlib import Path

import httpx
import pytest

from gigai.scout import learning_store

from tests.api_e2e.harness import setup_and_init, start_server, stop_server
from tests.behaviors.scout_learning.test_learning_store import APP_JS, MARK_SVG, SITE_CSS, make_course

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; "
    "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox"
)
SECRET = "SECRET-OUTSIDE-THE-COURSE"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _raw(port: int, method: str, path: str, *, host: str | None = None) -> tuple[int, dict[str, str], bytes]:
    """One request with the path sent exactly as written (httpx would normalise ``..`` away)."""

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.putrequest(method, path, skip_host=True)
        connection.putheader("Host", host or f"127.0.0.1:{port}")
        connection.endheaders()
        response = connection.getresponse()
        return response.status, {name.lower(): value for name, value in response.getheaders()}, response.read()
    finally:
        connection.close()


def _error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status and response.json()["error"]["code"] == code, response.text


def _course_headers(headers: dict[str, str]) -> None:
    assert headers["content-security-policy"] == CSP
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in headers, "no course file is ever offered to another origin"


def test_learning_pathways_are_listed_and_a_done_course_is_served_locked_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client, port = server.client, server.port

        # ---- 1. an empty store ------------------------------------------------------------------------------
        empty = client.get("/api/learning/pathways")
        assert empty.status_code == 200 and empty.json() == {"schema_version": "scout-learning-pathways-response:1", "pathways": [], "total": 0}
        for unknown in ("lp-00000000", "nope", "lp-0000000", "LP-00000000"):
            _error(client.get(f"/api/learning/pathways/{unknown}"), 404, "not_found")
        _error(client.get("/api/learning/pathways", params={"limit": "5"}), 422, "unknown_key")
        _error(client.get("/api/learning/pathways/lp-00000000", params={"x": "1"}), 422, "unknown_key")

        # ---- 2. a done course, a queued request, a failed one --------------------------------------------------
        source = make_course(
            tmp_path / "synthetic-course", lessons=3,
            extra={"assets/data.json": '{"lessons": 3}', "assets/shot.png": PNG, "assets/notes.txt": "plain\n", "assets/app.js.map": "{}", "about.md": "# About\n"},
        )
        cost = {"cli_model_calls": 11, "worker_minutes": 135, "web_fetches": 430, "web_searches": 16, "tokens": None, "note": None}
        done = learning_store.import_course(home, target, source, "MLOps engineer", cost).pathway
        queued = learning_store.create_request(home, target, "Forward deployed engineer")
        failed = learning_store.create_request(home, target, "Product manager")
        with learning_store.write_lock(home, target):
            failed = learning_store.write_pathway(
                home, target, dataclasses.replace(failed, status="failed", error="the model target was not available", revision=2),
            )

        listed = client.get("/api/learning/pathways")
        assert listed.status_code == 200 and listed.headers["x-gigai-labels"] == "user-private"
        body = listed.json()
        assert body["schema_version"] == "scout-learning-pathways-response:1" and body["total"] == 3
        rows = {row["id"]: row for row in body["pathways"]}
        assert [row["id"] for row in body["pathways"]] == [item.id for item in learning_store.list_pathways(home, target)], "newest request first"
        assert all(
            set(row) == {"id", "role_text", "requested_at", "status", "cost", "course", "url", "source", "imported_from", "error", "error_code", "progress"}
            for row in rows.values()
        )
        assert rows[done.id] == {
            "id": done.id, "role_text": "MLOps engineer", "requested_at": done.requested_at, "status": "done", "cost": cost,
            "course": {"entry": "index.html", "lessons": 3, "size_bytes": done.course.size_bytes, "generated_at": None},
            "url": f"/learning/{done.id}/index.html",
            "source": "imported", "imported_from": "synthetic-course", "error": None,
            "error_code": None, "progress": None,  # 0.1.11.10 G5: null for a course nobody generated here
        }
        assert rows[queued.id]["status"] == "queued" and rows[queued.id]["course"] is None and rows[queued.id]["url"] is None
        assert rows[queued.id]["source"] == "requested" and rows[queued.id]["imported_from"] is None and rows[queued.id]["error"] is None
        assert rows[failed.id]["status"] == "failed" and rows[failed.id]["url"] is None
        assert rows[failed.id]["source"] == "requested" and rows[failed.id]["imported_from"] is None
        assert rows[failed.id]["error"] == "the model target was not available"

        one = client.get(f"/api/learning/pathways/{done.id}")
        assert one.status_code == 200
        assert one.json() == {
            "schema_version": "scout-learning-pathway-response:1",
            "pathway": {**done.to_json(), "url": f"/learning/{done.id}/index.html", "error_code": None, "progress": None},
        }
        assert one.json()["pathway"]["imported_from"] == "synthetic-course" and str(tmp_path) not in one.text
        assert client.get(f"/api/learning/pathways/{failed.id}").json()["pathway"]["error"] == "the model target was not available"

        # ---- 3. a course file: the exact policy, the right type, the bytes as imported -----------------------------
        base = f"/learning/{done.id}"
        index = client.get(f"{base}/index.html")
        assert index.status_code == 200 and index.content == (source / "index.html").read_bytes()
        assert index.headers["content-security-policy"] == CSP, "the policy is this string, character for character"
        _course_headers(dict(index.headers))
        assert index.headers["content-type"] == "text/html; charset=utf-8"
        assert client.get(f"{base}/").content == index.content, "the course's own address is its entry page"
        kinds = {
            "concept-1-2-first.html": "text/html; charset=utf-8",
            "assets/app.js": "text/javascript; charset=utf-8",
            "assets/site.css": "text/css; charset=utf-8",
            "assets/mark.svg": "image/svg+xml",
            "assets/data.json": "application/json",
            "assets/app.js.map": "application/json",
            "assets/shot.png": "image/png",
            "assets/notes.txt": "text/plain; charset=utf-8",
            "about.md": "text/plain; charset=utf-8",
            "assets/lib/LICENSE": "text/plain; charset=utf-8",
        }
        for relative, content_type in kinds.items():
            served = client.get(f"{base}/{relative}")
            assert served.status_code == 200, relative
            assert served.headers["content-type"] == content_type, relative
            assert served.content == source.joinpath(*relative.split("/")).read_bytes(), relative
            _course_headers(dict(served.headers))  # an svg and a script carry the same policy as a page
        assert client.get(f"{base}/assets/app.js").text == APP_JS and client.get(f"{base}/assets/site.css").text == SITE_CSS
        assert client.get(f"{base}/assets/mark.svg").text == MARK_SVG
        assert client.get(f"{base}/assets/%61pp.js").text == APP_JS, "an encoded letter is the same file"

        status, headers, payload = _raw(port, "HEAD", f"{base}/index.html")
        assert status == 200 and payload == b"" and headers["content-length"] == str(len(index.content))
        assert headers["content-type"] == "text/html; charset=utf-8"
        _course_headers(headers)
        status, headers, payload = _raw(port, "HEAD", f"{base}/assets/app.js")
        assert status == 200 and payload == b"" and headers["content-type"] == "text/javascript; charset=utf-8"

        # ---- 4. everything else is 404 --------------------------------------------------------------------------
        site = learning_store.course_site_dir(home, target, done.id)
        (site.parent / "secret.txt").write_text(SECRET, encoding="utf-8")
        (home / "secret.txt").write_text(SECRET, encoding="utf-8")
        (site / ".hidden.html").write_text(SECRET, encoding="utf-8")
        (site / "tool.exe").write_text(SECRET, encoding="utf-8")
        (site / "link.txt").symlink_to(site.parent / "secret.txt")
        (site / "linked").symlink_to(site / "assets", target_is_directory=True)
        for half_made in (queued, failed):
            folder = learning_store.course_site_dir(home, target, half_made.id)
            folder.mkdir(parents=True)
            (folder / "index.html").write_text(SECRET, encoding="utf-8")
        not_served = [
            "/learning/lp-00000000/index.html",
            "/learning/nope/index.html",
            f"/learning/{queued.id}/index.html",
            f"/learning/{queued.id}/",
            f"/learning/{failed.id}/index.html",
            f"{base}",
            f"{base}/missing.html",
            f"{base}/assets",
            f"{base}/assets/",
            f"{base}/.hidden.html",
            f"{base}/tool.exe",
            f"{base}/link.txt",
            f"{base}/linked/app.js",
            f"{base}/../secret.txt",
            f"{base}/assets/../../secret.txt",
            f"{base}/../../../../../secret.txt",
            f"{base}/%2e%2e/secret.txt",
            f"{base}/%2E%2E/secret.txt",
            f"{base}/.%2e/secret.txt",
            f"{base}/%2e%2e%2fsecret.txt",
            f"{base}/..%2fsecret.txt",
            f"{base}/assets%2f..%2f..%2fsecret.txt",
            f"{base}/%252e%252e/secret.txt",
            f"{base}/..%5csecret.txt",
            f"{base}/..\\secret.txt",
            f"{base}//etc/passwd",
            f"{base}/%2fetc%2fpasswd",
            f"{base}/{home}/secret.txt",
            f"{base}/index.html%00.png",
            f"{base}/assets//app.js",
            f"{base}/./index.html",
            f"/learning/../api/health",
            f"/learning/{done.id}%2findex.html",
            "/learning/",
            "/learning",
        ]
        for path in not_served:
            status, headers, payload = _raw(port, "GET", path)
            assert SECRET.encode() not in payload, path
            if path == "/learning":
                assert status in (404, 200) and b"Synthetic course" not in payload, path  # not a course route: the UI's own fallthrough
                continue
            assert status == 404, (path, status, payload[:120])
            assert b"Synthetic course" not in payload, path
            status, headers, payload = _raw(port, "HEAD", path)
            assert status == 404 and payload == b"", (path, status)

        # ---- 5. the server's own guards ------------------------------------------------------------------------
        for method in ("GET", "HEAD"):
            for host in ("evil.example.invalid", f"evil.example.invalid:{port}", "127.0.0.1:1", f"192.168.1.10:{port}"):
                status, _headers, payload = _raw(port, method, f"{base}/index.html", host=host)
                assert status == 403 and b"Synthetic course" not in payload, (method, host, status)
        status, _headers, payload = _raw(port, "GET", f"{base}/index.html", host=f"localhost:{port}")
        assert status == 200 and payload == index.content
        status, headers, payload = _raw(port, "HEAD", "/api/health")
        assert status == 405 and payload == b"" and headers["allow"] == "GET"
        for method in ("POST", "PUT", "DELETE"):
            refused = client.request(method, f"{base}/index.html", json={})
            assert refused.status_code in (403, 404, 405), (method, refused.status_code)
            assert "Synthetic course" not in refused.text

        # The stored course is as it was imported: serving wrote nothing.
        assert (site / "index.html").read_bytes() == (source / "index.html").read_bytes()
        assert learning_store.get_pathway(home, target, done.id) == done
    finally:
        stop_server(server)
