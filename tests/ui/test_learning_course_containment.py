"""0.1.11.10 Part A: a course page cannot reach Scout (real Chromium against the real server).

A learning pathway's course is static HTML generated from postings and web pages, and it runs its own scripts. The
server sends every file of it with a Content-Security-Policy that sandboxes the page with no ``allow-same-origin``
and allows no request (``find_jobs/api/learning.py``). Here a SYNTHETIC course (built in ``tmp_path``) carries a
script that tries everything a hostile page would, and the page must be left with nothing:

1. Opened as its own tab: the course's script FILE ran, its stylesheet and image loaded; ``fetch`` of Scout's API,
   ``fetch`` of another origin (a second local server that would answer anyone) and ``XMLHttpRequest`` all failed,
   and neither server saw a request from the page; ``document.cookie``, ``localStorage`` and ``sessionStorage`` threw
   (Scout's own cookie and stored value were there to be read); ``eval``, an inline ``<script>`` block and an
   ``onerror=`` attribute did not run; the page's origin is ``null``. The results reach the DOM by ``postMessage``.
2. Framed by a page of Scout itself (the one framing the policy allows): the same, and ``window.parent`` cannot be
   read (its document, its storage).
3. The real vendored ``mermaid.min.js`` (read from the course renderer's assets at test time ONLY, never copied
   into this repo; skipped when that file is not on this machine) renders a small flowchart to an ``<svg>``
   under the same policy, with no policy violation.

Its own server and home (``serve()`` on a thread); the session's Scout server is not used.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Iterator
from urllib.parse import urlsplit

import pytest

from gigai.scout import learning_store
from gigai.scout.find_jobs.api.learning import COURSE_CSP

from tests.behaviors.scout_find_jobs.test_m1_end_to_end import _fixture
from tests.behaviors.scout_learning.test_learning_store import make_course

pytestmark = pytest.mark.ui

#: The vendored mermaid.min.js of the course renderer. It is not in this repo: ``GIGAI_UI_MERMAID_JS`` names it, else it is
#: looked for in the maintainers' folder beside this checkout. Not there: the test is skipped.
MERMAID_ENV = "GIGAI_UI_MERMAID_JS"
_BESIDE = Path(__file__).resolve().parents[3] / "orchestrator" / "research" / "role-packet" / "render" / "assets" / "mermaid" / "mermaid.min.js"
WAIT_MS = 20_000

VIOLATIONS_JS = """
window.__violations = [];
document.addEventListener("securitypolicyviolation", function (event) {
  window.__violations.push(event.effectiveDirective);
  document.documentElement.setAttribute("data-violations", window.__violations.join(","));
});
"""

PROBE_JS = """
(function () {
  var out = { script_ran: true, origin: String(window.origin) };
  var pending = 3;
  function attempt(key, read) {
    try { out[key] = "read:" + read(); } catch (error) { out[key] = "blocked:" + error.name; }
  }
  function settle(key, value) {
    out[key] = value;
    pending -= 1;
    if (pending === 0) {
      out.violations = window.__violations.slice();
      window.addEventListener("message", function (event) {
        var box = document.getElementById("probe");
        box.textContent = JSON.stringify(event.data);
        box.setAttribute("data-done", "yes");
      });
      window.postMessage(out, "*");
    }
  }
  attempt("cookie", function () { return document.cookie; });
  attempt("local_storage", function () { return window.localStorage.getItem("scout-secret"); });
  attempt("session_storage", function () { return window.sessionStorage.length; });
  attempt("eval", function () { return eval("1 + 1"); });
  attempt("parent_document", function () { return window.parent === window ? "top" : window.parent.document.title; });
  attempt("parent_storage", function () { return window.parent === window ? "top" : window.parent.localStorage.getItem("scout-secret"); });
  attempt("top_location", function () { return window.top === window ? "top" : window.top.location.href; });
  fetch("/api/learning/pathways").then(
    function (response) { return response.text().then(function (text) { settle("fetch_api", "read:" + text.length); }); },
    function (error) { settle("fetch_api", "blocked:" + error.name); }
  );
  fetch("OTHER_ORIGIN/anything").then(
    function (response) { settle("fetch_other", "read:" + response.status); },
    function (error) { settle("fetch_other", "blocked:" + error.name); }
  );
  try {
    var request = new XMLHttpRequest();
    request.onload = function () { settle("xhr", "read:" + request.status); };
    request.onerror = function () { settle("xhr", "blocked:error"); };
    request.open("GET", "/api/health");
    request.send();
  } catch (error) {
    settle("xhr", "blocked:" + error.name);
  }
})();
"""

PROBE_BODY = (
    '<pre id="probe"></pre>\n'
    '<script>document.documentElement.setAttribute("data-inline", "ran");</script>\n'
    '<img id="broken" src="data:image/png;base64,AAAA" alt="" onerror="document.documentElement.setAttribute(\'data-handler\', \'ran\')">\n'
    '<script src="assets/violations.js"></script>\n<script src="assets/probe.js"></script>\n'
)

DIAGRAM_JS = """
(function () {
  var root = document.documentElement;
  try {
    window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
    window.mermaid.run({ querySelector: ".mermaid" }).then(
      function () { root.setAttribute("data-mermaid", "done"); },
      function (error) { root.setAttribute("data-mermaid", "failed: " + error); }
    );
  } catch (error) {
    root.setAttribute("data-mermaid", "failed: " + error);
  }
})();
"""


class _Other(BaseHTTPRequestHandler):
    """Another origin that would answer any page (CORS open): a request that reaches it is recorded."""

    def do_GET(self) -> None:  # noqa: N802
        self.server.hits.append(self.path)  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def course_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    home, target, _workpad = _fixture(tmp_path)
    other = ThreadingHTTPServer(("127.0.0.1", 0), _Other)
    other.hits = []  # type: ignore[attr-defined]
    server = serve(backend=ScoutFindJobsBackend(home_root=home, target=target), bind=("127.0.0.1", 0))
    threads = [threading.Thread(target=item.serve_forever, daemon=True) for item in (other, server)]
    for thread in threads:
        thread.start()
    try:
        yield SimpleNamespace(
            home=home, target=target, tmp=tmp_path, url=f"http://127.0.0.1:{server.server_address[1]}",
            other_url=f"http://127.0.0.1:{other.server_address[1]}", other=other,
        )
    finally:
        for item in (other, server):
            item.shutdown()
            item.server_close()
        for thread in threads:
            thread.join(10)


@pytest.fixture
def browser_page(ui_browser) -> Iterator[SimpleNamespace]:
    context = ui_browser.new_context()
    page = context.new_page()
    seen = SimpleNamespace(page=page, answered=[], failed=[], console=[])
    # A request the policy refuses is announced by the browser and then fails without leaving it: only an ANSWERED
    # request reached a server.
    page.on("response", lambda response: seen.answered.append(response.url))
    page.on("requestfailed", lambda request: seen.failed.append(f"{urlsplit(request.url).path} {request.failure}"))
    page.on("console", lambda message: seen.console.append(f"{message.type}: {message.text}"))
    try:
        yield seen
    finally:
        context.close()


def _probe_course(served: SimpleNamespace) -> str:
    """Import the synthetic course that carries the probe; its id."""

    course = make_course(
        served.tmp / "probe-course", index_body=PROBE_BODY,
        extra={"assets/violations.js": VIOLATIONS_JS, "assets/probe.js": PROBE_JS.replace("OTHER_ORIGIN", served.other_url)},
    )
    return learning_store.import_course(served.home, served.target, course, "Synthetic role").pathway.id


def _plant_scout_secrets(page, url: str) -> None:
    """A cookie and a stored value on Scout's own origin: there to be read by a page of that origin."""

    planter = page.context.new_page()  # its own tab: the page under test records only the course's requests
    planter.goto(f"{url}/api/health")
    planter.evaluate("() => { window.localStorage.setItem('scout-secret', 'yes'); document.cookie = 'scout=yes; path=/'; }")
    assert planter.evaluate("() => window.localStorage.getItem('scout-secret') + '|' + document.cookie") == "yes|scout=yes"
    planter.close()


def _assert_contained(result: dict[str, object], *, framed: bool) -> None:
    assert result["script_ran"] is True, "the course's own script file must run"
    assert result["origin"] == "null", "the sandbox gives the page an origin that matches nothing"
    for key in ("fetch_api", "fetch_other", "xhr"):
        assert str(result[key]).startswith("blocked:"), (key, result[key])
    for key in ("cookie", "local_storage", "session_storage"):
        assert result[key] == "blocked:SecurityError", (key, result[key])
    assert str(result["eval"]).startswith("blocked:"), result["eval"]
    if framed:
        for key in ("parent_document", "parent_storage", "top_location"):
            assert result[key] == "blocked:SecurityError", (key, result[key])
    else:
        assert result["parent_document"] == "read:top" and result["top_location"] == "read:top"
    violations = result["violations"]
    assert isinstance(violations, list) and violations.count("connect-src") >= 3, violations


def test_a_course_page_opened_as_its_own_tab_runs_its_script_file_and_reaches_nothing(course_server: SimpleNamespace, browser_page: SimpleNamespace) -> None:
    page = browser_page.page
    pathway_id = _probe_course(course_server)
    _plant_scout_secrets(page, course_server.url)

    response = page.goto(f"{course_server.url}/learning/{pathway_id}/index.html")
    assert response is not None and response.status == 200
    assert response.headers["content-security-policy"] == COURSE_CSP
    page.locator("#probe[data-done=yes]").wait_for(timeout=WAIT_MS)
    result = json.loads(page.locator("#probe").inner_text())
    _assert_contained(result, framed=False)

    root = page.locator("html")
    assert root.get_attribute("data-app") == "ran", "the course's own script file ran"
    assert root.get_attribute("data-inline") is None, "an inline <script> block must not run"
    assert root.get_attribute("data-handler") is None, "an onerror= attribute must not run"
    assert page.evaluate("() => getComputedStyle(document.body).fontFamily") == "sans-serif", "the course's stylesheet applies"
    assert page.evaluate("() => document.getElementById('mark').naturalWidth") > 0, "the course's own image loads"

    # Nothing the page tried left the browser: every request that was answered is a file of its own course.
    paths = [urlsplit(url).path for url in browser_page.answered]
    strays = [path for path in paths if not path.startswith(f"/learning/{pathway_id}/")]
    assert paths and not strays, strays
    assert all(url.startswith(course_server.url) for url in browser_page.answered), browser_page.answered
    assert browser_page.failed and all(line.endswith(" csp") for line in browser_page.failed), browser_page.failed  # refused by the policy
    assert course_server.other.hits == [], "the other origin must never be asked"


def test_a_course_page_framed_by_scout_cannot_read_its_parent(course_server: SimpleNamespace, browser_page: SimpleNamespace) -> None:
    page = browser_page.page
    pathway_id = _probe_course(course_server)
    _plant_scout_secrets(page, course_server.url)
    page.goto(f"{course_server.url}/api/health")  # the parent: a page of Scout's own origin
    page.evaluate(
        "(src) => { document.title = 'Scout parent'; const frame = document.createElement('iframe'); frame.id = 'course'; frame.src = src; document.body.appendChild(frame); }",
        f"/learning/{pathway_id}/index.html",
    )
    frame = page.frame_locator("#course")
    frame.locator("#probe[data-done=yes]").wait_for(timeout=WAIT_MS)
    result = json.loads(frame.locator("#probe").inner_text())
    _assert_contained(result, framed=True)
    assert frame.locator("html").get_attribute("data-app") == "ran"
    assert frame.locator("html").get_attribute("data-inline") is None
    assert course_server.other.hits == []
    # The parent, a page of Scout's own origin, still has what the course could not read.
    assert page.evaluate("() => window.localStorage.getItem('scout-secret')") == "yes"


def test_the_real_mermaid_renders_a_flowchart_under_the_course_policy(course_server: SimpleNamespace, browser_page: SimpleNamespace) -> None:
    mermaid = Path(os.environ.get(MERMAID_ENV) or _BESIDE)
    if not mermaid.is_file():
        pytest.skip(f"the vendored mermaid.min.js is not on this machine (set {MERMAID_ENV} to its path); it is never copied into this repo")
    page = browser_page.page
    body = (
        '<pre class="mermaid" id="diagram">flowchart LR\n  A[Start] --> B[End]</pre>\n'
        '<script src="assets/violations.js"></script>\n<script src="assets/mermaid/mermaid.min.js"></script>\n<script src="assets/diagram.js"></script>\n'
    )
    course = make_course(
        course_server.tmp / "diagram-course", index_body=body,
        extra={"assets/violations.js": VIOLATIONS_JS, "assets/mermaid/mermaid.min.js": mermaid.read_bytes(), "assets/diagram.js": DIAGRAM_JS},
    )
    pathway_id = learning_store.import_course(course_server.home, course_server.target, course, "Synthetic role").pathway.id

    response = page.goto(f"{course_server.url}/learning/{pathway_id}/index.html")
    assert response is not None and response.headers["content-security-policy"] == COURSE_CSP
    page.locator("html[data-mermaid]").wait_for(timeout=WAIT_MS)
    assert page.locator("html").get_attribute("data-mermaid") == "done", browser_page.console
    assert page.locator("#diagram svg").count() == 1, "the flowchart is an <svg>"
    drawn = page.locator("#diagram svg").text_content() or ""
    assert "Start" in drawn and "End" in drawn, drawn
    assert page.evaluate("() => document.querySelector('#diagram svg').getBoundingClientRect().width") > 0
    assert page.locator("html").get_attribute("data-violations") is None, "mermaid needs nothing the policy refuses"
    assert not [line for line in browser_page.console if line.startswith("error")], browser_page.console
