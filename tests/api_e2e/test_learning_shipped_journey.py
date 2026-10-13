"""0.1.11.10 shipped lessons, slice 3 (learning center M2): the course catalogs that ship inside GigAI, over HTTP against the real supervised server.

``GET /api/learning/pathways`` (the shipped rows), ``GET /api/learning/pathways/{pathway_id}`` for a role key, the
ONE page under ``/learning/shipped/{role key}/`` (``pathway.html``) and the shared assets under ``/learning/_assets/``.
The lessons are on the public learning center: every other page of a shipped course is a 404 here.

The shipped course is SYNTHETIC (``make_shipped``, a catalog written into ``tmp_path``). The server is a child process, so it
is pointed at that folder through ``GIGAI_SCOUT_LEARNING_SHIPPED_DIR``, which ``learning_shipped_store`` reads only
while both test seams are on (the harness starts the server with ``--allow-test-seams``).

1. The list answers the shipped course FIRST, with ``source: "shipped"``, and the user's own course after it as
   before; the single read answers the shipped row for its role key; cancel and resume of a role key are 404.
   The row's ``url`` is the course's pathway on the learning center (an address for the browser: the server never
   fetches it). Listing renders nothing.
2. No lesson, practice or entry page of a shipped course is served, nothing is rendered or cached, and
   ``/learning/shipped/<role key>/`` is a 404; ``HEAD`` answers 404 with no body.
3. A shared asset is served by its exact name with the EXACT Content-Security-Policy and the three other headers of
   a generated course and the package file's bytes; the pathway page names only assets that are served.
4. ``pathway.html`` is the one page from the user's home: a plain page saying it is not built yet while there is
   none (with a link to the course on the learning center), the user's own file once it is there (and the list then
   says ``built``).
5. 404, with no byte of anything else: an unknown role, a pathway id, every way out of the two folders (``../``,
   ``%2e%2e``, ``%2f``, a doubly encoded dot, a backslash, an absolute path), a folder, a lesson page. No answer is a 5xx.
6. A request whose ``Host`` is not this server is refused (403), for ``GET`` and ``HEAD``.

Slices 4b and 5b, the second journey: ``POST /api/learning/shipped/{role_key}/pathway`` builds the user's own
pathway page. The resume is always the reference, so a build always compares it with the course in one model call;
the server is another process, so that call is answered by the script file ``GIGAI_SCOUT_LEARNING_TEST_SCRIPT``
names (read only while both test seams are on), as in ``test_learning_generate_journey.py``. The master resume is
SYNTHETIC (an invented person), stored with ``gigai scout resume master init --from``.

7. The write guards of every POST apply (JSON only, this server's own origin and host); a body key that is not
   ``approve`` and an ``approve`` that is not true or false are refused; an unknown role key, a pathway id and a
   role key in another case are 404. Nothing is sent or built by any of them.
8. Without ``approve`` the answer is the estimate and the consent line; nothing is sent, stored or started.
9. ``approve`` starts the build (202, ``local_page.build`` running); a second approval and the generation of a
   course are 409 ``learning_running`` while it runs; then the row says ``built``, the page is served under the
   course's policy with what the resume shows, and the record holds the master revision and what the build took.
   A second build gives the same page.
10. No answer of the route, no row and no record holds resume text.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
import sqlite3
import time

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import learning_pathway_build, learning_pathway_local, learning_render, learning_shipped_store, learning_store
from gigai.scout.find_jobs import search_index

from tests.api_e2e.harness import setup_and_init, start_server, stop_server
from tests.api_e2e.test_learning_journey import CSP, SECRET, _course_headers, _error, _raw
from tests.behaviors.scout_learning.test_learning_corpus import lever_job, seed_board
from tests.behaviors.scout_learning.test_learning_shipped_store import BASE, KEY, ROLE, make_shipped
from tests.behaviors.scout_learning.test_learning_store import make_course
from tests.support.latency import latency_bound


def test_a_shipped_course_is_listed_first_links_out_and_serves_only_its_own_page_locked_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    shipped_root = tmp_path / "shipped-package"
    make_shipped(shipped_root)
    monkeypatch.setenv("GIGAI_SCOUT_LEARNING_SHIPPED_DIR", str(shipped_root))
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client, port = server.client, server.port
        base = f"/learning/shipped/{KEY}"
        public = f"{BASE}/pathways/{KEY}/"
        shipped_row = {
            "id": KEY, "role_text": ROLE, "requested_at": None, "status": "done", "cost": None,
            "course": {"entry": None, "lessons": 4, "size_bytes": None, "generated_at": None},
            "url": public, "source": "shipped", "imported_from": None, "error": None, "error_code": None, "progress": None,
            "label": "From the GigAI learning center",
            "local_page": {"state": "not_built", "built_at": None, "reasons": [], "build": None, "url": f"{base}/pathway.html"},
        }

        # ---- 1. the list and the single read --------------------------------------------------------------------
        listed = client.get("/api/learning/pathways")
        assert listed.status_code == 200 and listed.headers["x-gigai-labels"] == "user-private"
        assert listed.json() == {"schema_version": "scout-learning-pathways-response:1", "pathways": [shipped_row], "total": 1}
        own = learning_store.import_course(home, target, make_course(tmp_path / "own-course", lessons=3), "Gadget engineer", None).pathway
        rows = client.get("/api/learning/pathways").json()["pathways"]
        assert [row["id"] for row in rows] == [KEY, own.id] and rows[0] == shipped_row
        assert rows[1]["source"] == "imported" and "label" not in rows[1] and "local_page" not in rows[1], "the user's own row is as it was"

        one = client.get(f"/api/learning/pathways/{KEY}")
        assert one.status_code == 200 and one.json() == {"schema_version": "scout-learning-pathway-response:1", "pathway": shipped_row}
        _error(client.get("/api/learning/pathways/no-such-role"), 404, "not_found")
        _error(client.get(f"/api/learning/pathways/{KEY}", params={"x": "1"}), 422, "unknown_key")
        for action in ("cancel", "resume"):
            _error(client.post(f"/api/learning/pathways/{KEY}/{action}", json={}), 404, "not_found")
        cache = home / "cache" / "scout" / "learning-shipped"
        assert not cache.exists(), "listing and reading a row render nothing"

        # ---- 2. no lesson page is served: the lessons are on the learning center ---------------------------------
        for name in ("index.html", "concept-lesson-alpha.html", "concept-lesson-beta.html", "practice-lesson-alpha.html", "practice-lesson-gamma.html", "course.json", "catalog.json", ""):
            for method in ("GET", "HEAD"):
                status, _headers, payload = _raw(port, method, f"{base}/{name}")
                assert status == 404 and ROLE.encode() not in payload and (method == "GET" or payload == b""), (method, name, status)
        assert not cache.exists(), "nothing is rendered or cached by asking for a page"

        # ---- 3. the shared assets ------------------------------------------------------------------------------
        package_assets = learning_render.package_assets_dir()
        kinds = {
            "course.css": "text/css; charset=utf-8",
            "course.js": "text/javascript; charset=utf-8",
            "theme-init.js": "text/javascript; charset=utf-8",
            "mermaid/mermaid.min.js": "text/javascript; charset=utf-8",
            "hljs/styles/github.min.css": "text/css; charset=utf-8",
            "hljs/LICENSE": "text/plain; charset=utf-8",
        }
        for relative, content_type in kinds.items():
            served = client.get(f"/learning/_assets/{relative}")
            assert served.status_code == 200 and served.headers["content-type"] == content_type, relative
            assert served.content == package_assets.joinpath(*relative.split("/")).read_bytes(), relative
            _course_headers(dict(served.headers))  # a script and a stylesheet carry the same policy as a page
        status, headers, payload = _raw(port, "HEAD", "/learning/_assets/course.css")
        assert status == 200 and payload == b"" and headers["content-length"] == str((package_assets / "course.css").stat().st_size)
        _course_headers(headers)
        named = set(re.findall(r'(?:href|src)="(/learning/_assets/[^"]+)"', client.get(f"{base}/pathway.html").text))
        assert {"/learning/_assets/course.css", "/learning/_assets/theme-init.js"} <= named
        for address in sorted(named):
            assert client.get(address).status_code == 200, f"a page names an asset that is not served: {address}"

        # ---- 4. pathway.html: the one page from the user's home ------------------------------------------------
        absent = client.get(f"{base}/pathway.html")
        assert absent.status_code == 200 and absent.headers["content-type"] == "text/html; charset=utf-8"
        _course_headers(dict(absent.headers))
        assert "Your pathway page is not built yet" in absent.text and ROLE in absent.text
        assert f'<a href="{public}" target="_blank" rel="noopener">' in absent.text and 'href="index.html"' not in absent.text
        assert "<script>" not in absent.text and "style=" not in absent.text
        status, headers, payload = _raw(port, "HEAD", f"{base}/pathway.html")
        assert status == 200 and payload == b"" and headers["content-length"] == str(len(absent.content))
        assert not cache.exists()

        mine = learning_shipped_store.pathway_page_path(home, target, KEY)
        mine.parent.mkdir(parents=True)
        mine.write_text("<!doctype html><title>mine</title><p>MY LOCAL PATHWAY</p>", encoding="utf-8")
        os.utime(mine, (1_800_000_000, 1_800_000_000))
        built = client.get(f"{base}/pathway.html")
        assert built.status_code == 200 and built.content == mine.read_bytes()
        _course_headers(dict(built.headers))
        assert client.get("/api/learning/pathways").json()["pathways"][0]["local_page"] == {
            "state": "built", "built_at": "2027-01-15T08:00:00Z", "reasons": [], "build": None, "url": f"{base}/pathway.html",
        }
        assert client.get(f"{base}/index.html").status_code == 404, "the page of the user's own does not bring the lessons back"

        # ---- 5. everything else is 404 --------------------------------------------------------------------------
        (home / "secret.txt").write_text(SECRET, encoding="utf-8")
        (home / "secret.html").write_text(SECRET, encoding="utf-8")
        not_served = [
            "/learning/shipped/no-such-role/index.html",
            "/learning/shipped/no-such-role/pathway.html",
            "/learning/shipped/no-such-role/",
            f"/learning/shipped/{own.id}/index.html",
            f"/learning/shipped/{KEY.upper()}/index.html",
            f"/learning/shipped/{KEY}",
            "/learning/shipped/",
            "/learning/shipped",
            f"/learning/{KEY}/index.html",
            f"{base}/missing.html",
            f"{base}/index.html",
            f"{base}/concept-lesson-alpha.html",
            f"{base}/practice-lesson-alpha.html",
            f"{base}/sub",
            f"{base}/.render.json",
            f"{base}/../secret.html",
            f"{base}/../../../../../secret.txt",
            f"{base}/%2e%2e/secret.html",
            f"{base}/.%2e/secret.html",
            f"{base}/..%2fsecret.html",
            f"{base}/%252e%252e/secret.html",
            f"{base}/..%5csecret.html",
            f"{base}/..\\secret.html",
            f"{base}//etc/passwd",
            f"{base}/%2fetc%2fpasswd",
            f"{base}/{home}/secret.txt",
            f"{base}/index.html%00.png",
            f"{base}/./index.html",
            f"{base}/pathway.html/",
            f"{base}/../{KEY}/index.html",
            f"/learning/shipped/{KEY}%2findex.html",
            f"/learning/shipped/../shipped/{KEY}/index.html",
            "/learning/_assets/",
            "/learning/_assets",
            "/learning/_assets/missing.js",
            "/learning/_assets/hljs",
            "/learning/_assets/hljs/",
            "/learning/_assets/hljs//highlight.min.js",
            "/learning/_assets/./course.css",
            "/learning/_assets/../assets/course.css",
            "/learning/_assets/hljs/../course.css",
            "/learning/_assets/%2e%2e/assets/course.css",
            "/learning/_assets/..%2f..%2flearning_render.py",
            "/learning/_assets/%252e%252e/course.css",
            "/learning/_assets/..\\course.css",
            "/learning/_assets/COURSE.CSS",
            "/learning/_assets/course.css%00.png",
            "/learning/_assets/course.css/",
            f"/learning/_assets/{home}/secret.txt",
            "/learning/_assets//etc/passwd",
        ]
        for path in not_served:
            for method in ("GET", "HEAD"):
                status, _headers, payload = _raw(port, method, path)
                assert status == 404, (method, path, status, payload[:120])
                assert SECRET.encode() not in payload and ROLE.encode() not in payload, (method, path)
                assert method == "GET" or payload == b"", path

        # ---- 6. the server's own guards ------------------------------------------------------------------------
        for method in ("GET", "HEAD"):
            for path in (f"{base}/pathway.html", "/learning/_assets/course.css"):
                for host in ("evil.example.invalid", f"evil.example.invalid:{port}", f"192.168.1.10:{port}"):
                    status, _headers, payload = _raw(port, method, path, host=host)
                    assert status == 403 and ROLE.encode() not in payload, (method, path, host, status)
        for method in ("POST", "PUT", "DELETE"):
            for path in (f"{base}/pathway.html", "/learning/_assets/course.css"):
                refused = client.request(method, path, json={})
                assert refused.status_code in (403, 404, 405) and ROLE not in refused.text, (method, path, refused.status_code)

        # Serving wrote nothing: the package folder (the catalog alone) and the user's page are as they were.
        assert {entry.name for entry in (shipped_root / KEY).iterdir()} == {"catalog.json"} and not cache.exists()
        assert mine.read_text(encoding="utf-8").endswith("MY LOCAL PATHWAY</p>")
    finally:
        stop_server(server)


#: Three stored postings of the synthetic role (one without text) and one of another title.
WIDGETCO = [
    lever_job("widgetco", 1, "Senior Widget Engineer", 5, "You will ship widgets with Widgetflow. Experience with widget pipelines is required."),
    lever_job("widgetco", 2, "Widget Engineer", 10, "You will review widgets every week. Must have Widgetflow."),
    lever_job("widgetco", 3, "Widget Engineer", 2, ""),
    lever_job("widgetco", 4, "Data Engineer", 3, "Data pipelines with Widgetflow."),
]
#: A synthetic master: an invented person, no contact data.
QUOTE = "Ran the Widgetflow line for three widget plants"
MASTER = f"""<!-- gigai-master:1 -->

## Summary

- Widget engineer with 9 years of experience in widget plants. <!-- id:sum-widget -->

## Experience

### Widget Lab <!-- id:r-widgetlab -->
Engineer | Jan 2024 - Present
- {QUOTE}. <!-- id:b-widgetflow -->

## Skills

- Tools: Gearbox, Widgetflow <!-- id:s-tools -->
"""
#: What the scripted model answers to the comparison: one KNOWN with a verbatim quote, one KNOWN it invented.
MARKERS = {
    "markers": {
        "lesson-alpha/lesson-alpha-first": {"marker": "KNOWN", "evidence": {"id": "b-widgetflow", "line": QUOTE}, "missing": None},
        "lesson-gamma/lesson-gamma-first": {"marker": "KNOWN", "evidence": {"id": "b-widgetflow", "line": "Ran gamma widgets at scale"}, "missing": None},
    }
}
STAMP = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"


def _wait_for_build(client, key: str) -> dict:
    """The course's ``local_page`` once no build of it runs any more."""

    deadline = time.monotonic() + latency_bound(120.0)
    while time.monotonic() < deadline:
        local = client.get(f"/api/learning/pathways/{key}").json()["pathway"]["local_page"]
        if local["build"] is None or local["build"]["status"] != "running":
            return local
        time.sleep(0.05)
    raise AssertionError(f"the build did not end: {local}")


@pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")
def test_the_users_own_pathway_page_is_built_on_approval_with_the_resume_compared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    source = tmp_path / "master-in.md"
    source.write_text(MASTER, encoding="utf-8")
    made = CliRunner().invoke(cli, ["scout", "resume", "master", "init", "--from", str(source), "--home", str(home), "--target", str(target), "--json"])
    assert made.exit_code == 0, made.output
    seed_board(home, "widgetco", WIDGETCO)
    assert search_index.rebuild_from_index(home).available
    search_index.close(home)
    shipped_root = tmp_path / "shipped-package"
    make_shipped(shipped_root)
    monkeypatch.setenv("GIGAI_SCOUT_LEARNING_SHIPPED_DIR", str(shipped_root))
    script = tmp_path / "learning-script.json"
    script.write_text(json.dumps({"delay_seconds": 0.6, "answers": [{"when": ["You are comparing the lessons of a COURSE"], "answer": MARKERS}]}), encoding="utf-8")
    monkeypatch.setenv("GIGAI_SCOUT_LEARNING_TEST_SCRIPT", str(script))
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client, port = server.client, server.port
        base = f"/learning/shipped/{KEY}"
        build = f"/api/learning/shipped/{KEY}/pathway"
        learning = learning_store.learning_dir(home, target)
        mine = learning_shipped_store.pathway_page_path(home, target, KEY)
        not_built = {"state": "not_built", "built_at": None, "reasons": [], "build": None, "url": f"{base}/pathway.html"}
        assert client.get(f"/api/learning/pathways/{KEY}").json()["pathway"]["local_page"] == not_built

        # ---- 7. the guards and the refusals: nothing is sent or built -------------------------------------------
        refused = client.post(build, json={"approve": True}, headers={"Origin": "http://evil.example"})
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden_origin", refused.text
        refused = client.post(build, json={"approve": True}, headers={"Host": f"evil.example.invalid:{port}"})
        assert refused.status_code == 403, refused.text
        refused = client.post(build, content=b'{"approve": true}', headers={"Content-Type": "text/plain"})
        assert refused.status_code == 415 and refused.json()["error"]["code"] == "unsupported_media_type", refused.text
        _error(client.post(build, json={"approve": True, "resume": False}), 422, "unknown_key")
        _error(client.post(build, json={"approve": "yes"}), 422, "wrong_type")
        _error(client.post(build, json=[]), 422, "wrong_type")
        for unknown in ("no-such-role", "lp-0a1b2c3d", KEY.upper(), "..%2f..%2fsecret"):
            _error(client.post(f"/api/learning/shipped/{unknown}/pathway", json={"approve": True}), 404, "not_found")
        for path in (f"/api/learning/shipped/{KEY}", f"/api/learning/shipped/{KEY}/pathway/again", "/api/learning/shipped//pathway"):
            assert client.post(path, json={"approve": True}).status_code == 404, path
        assert client.get(build).status_code == 404 and client.put(build, json={}).status_code in (404, 405)

        # ---- 8. the estimate first -----------------------------------------------------------------------------
        asked = client.post(build, json={})
        assert asked.status_code == 200 and asked.headers["x-gigai-labels"] == "user-private", asked.text
        ask = asked.json()
        assert set(ask) == {"schema_version", "status", "role_key", "role_text", "resume", "estimate", "sends"}
        assert (ask["schema_version"], ask["status"], ask["role_key"], ask["role_text"]) == ("scout-learning-pathway-build:1", "ask", KEY, ROLE)
        assert ask["resume"] == {"lines": 3} and ask["sends"] == learning_pathway_build.CONSENT
        assert set(ask["estimate"]) == {"calls", "tokens_in", "tokens_out", "seconds"} and ask["estimate"]["calls"] == 1
        assert client.post(build, json={"approve": False}).json() == ask
        assert not mine.exists() and not (learning / "local").exists(), "asking and every refusal above wrote nothing"
        assert client.get(f"/api/learning/pathways/{KEY}").json()["pathway"]["local_page"] == not_built

        # ---- 9. approval builds --------------------------------------------------------------------------------
        started = client.post(build, json={"approve": True})
        assert started.status_code == 202 and started.headers["x-gigai-labels"] == "user-private", started.text
        body = started.json()
        assert set(body) == {"schema_version", "pathway"} and body["schema_version"] == "scout-learning-pathway-response:1"
        row = body["pathway"]
        assert row["id"] == KEY and row["source"] == "shipped" and row["url"] == f"{BASE}/pathways/{KEY}/"
        running = row["local_page"]
        assert running["state"] == "not_built" and running["build"]["status"] == "running" and re.fullmatch(STAMP, running["build"]["started_at"])
        _error(client.post(build, json={"approve": True}), 409, "learning_running")
        _error(client.post("/api/learning/pathways", json={"role_text": "Widget engineer", "approve": True}), 409, "learning_running")
        assert client.post(build, json={}).status_code == 200, "asking is still answered while a build runs"

        local = _wait_for_build(client, KEY)
        assert local["state"] == "built" and local["reasons"] == [] and local["build"] is None and re.fullmatch(STAMP, local["built_at"])
        listed = client.get("/api/learning/pathways").json()["pathways"][0]
        assert listed["id"] == KEY and listed["local_page"] == local

        page = client.get(f"{base}/pathway.html")
        assert page.status_code == 200 and page.headers["content-security-policy"] == CSP and page.content == mine.read_bytes()
        _course_headers(dict(page.headers))
        assert f"Your pathway: {ROLE}" in page.text and "(2 any date)" in page.text
        assert "What your resume shows" in page.text and "Production evidence" in page.text and QUOTE in page.text and "b-widgetflow" in page.text
        assert "Ran gamma widgets at scale" not in page.text, "a quote that is not in the resume line never reaches the page"
        assert learning_pathway_local.NOT_COMPARED not in page.text and "<script>" not in page.text
        # Every link that leaves the page is an address on the learning center, in a new tab; the one local link is the page itself.
        hrefs = re.findall(r'<a [^>]*href="([^"#][^"]*)"', page.text)
        assert any(href.startswith(f"{BASE}/lessons/lesson-alpha/") for href in hrefs) and f"{BASE}/lessons/lesson-alpha/#lesson-alpha-first" in hrefs
        assert all(href.startswith(f"{BASE}/") or href == "pathway.html" for href in hrefs), sorted(set(hrefs))
        assert all('target="_blank" rel="noopener"' in tag for tag in re.findall(rf'<a [^>]*href="{re.escape(BASE)}[^>]*>', page.text))
        assert "site version 2026.10.1" in page.text and "From the GigAI learning center" in page.text
        assert client.get(f"{base}/concept-lesson-alpha.html").status_code == 404, "the lesson page is not served here"

        record = learning_pathway_local.read_local_record(learning, KEY)
        assert record is not None and record["resume_compared"] is True and record["corpus"]["with_text"] == 2
        assert set(record["master"]) == {"revision_id", "content_sha256"} and record["master"]["content_sha256"].startswith("sha256:")
        assert record["cost"] == {"calls": 1, "tokens_in": None, "tokens_out": None}, "the scripted model reports no tokens"
        assert not learning_pathway_build.state_path(learning, KEY).exists()

        again = client.post(build, json={"approve": True})
        assert again.status_code == 202, again.text
        assert _wait_for_build(client, KEY)["state"] == "built"
        assert client.get(f"{base}/pathway.html").content == page.content, "the same postings and the same resume give the same page"
        _error(client.post("/api/learning/pathways", json={"role_text": "   ", "approve": True}), 422, "invalid_value")  # the slot is free again

        # ---- 10. no resume text in an answer, a row or a record -------------------------------------------------
        texts = [asked.text, started.text, again.text, client.get("/api/learning/pathways").text, json.dumps(record)]
        for text in texts:
            for word in (QUOTE, "b-widgetflow", "s-tools", "9 years of experience", "Gearbox"):
                assert word not in text, f"{word}: resume text is on the user's own page and nowhere else"
    finally:
        stop_server(server)
