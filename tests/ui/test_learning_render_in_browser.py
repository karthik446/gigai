"""0.1.11.10 Part B packet G3: a RENDERED course runs under the real Scout server and the slice-1 CSP.

A SYNTHETIC mini course (``tests.behaviors.scout_learning.test_learning_render.mini_course``/``mini_paths``) is
rendered by the product renderer (``learning_render.render_to_folder``) and imported into a real, small synthetic
home (the same ``_fixture`` helper ``tests/ui/test_learning_course_containment.py`` and
``tests/api_e2e/test_learning_journey.py`` use), then opened in a real Chromium against the real Scout server
(``find_jobs.api.learning.COURSE_CSP``, no ``allow-same-origin``, ``script-src 'self'`` with no
``'unsafe-inline'``/``'unsafe-eval'``). Asserted on the live page:

1. no console error;
2. the Mermaid diagram of the first concept page renders to an ``<svg>``;
3. the code block of the second concept page is syntax-highlighted (``hljs`` classes applied);
4. the theme toggle flips ``data-theme`` on ``<html>`` (light is the initial value);
5. the sidebar search filters the lesson list.

Its own server and home; the session's shared demo home is not used. ``-m ui -n 0`` (``make ui-test``); a missing
Chromium is a skip unless ``GIGAI_UI_REQUIRED=1``.
"""

from __future__ import annotations

from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Iterator

import pytest

from gigai.scout import learning_render, learning_store

from tests.behaviors.scout_find_jobs.test_m1_end_to_end import _fixture
from tests.behaviors.scout_learning.test_learning_render import mini_course, mini_paths

pytestmark = pytest.mark.ui

WAIT_MS = 20_000


@pytest.fixture
def rendered_course(tmp_path: Path) -> Path:
    import json

    course = mini_course()
    course_path = tmp_path / "course.json"
    course_path.write_text(json.dumps(course), encoding="utf-8")
    paths_dir = tmp_path / "paths"
    paths_dir.mkdir()
    for cid, path in mini_paths().items():
        (paths_dir / f"{cid}.json").write_text(json.dumps(path), encoding="utf-8")

    out_dir = tmp_path / "site"
    learning_render.render_to_folder(course_path, out_dir, paths_dir)
    return out_dir


@pytest.fixture
def course_server(tmp_path: Path, rendered_course: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    home, target, _workpad = _fixture(tmp_path)
    pathway = learning_store.import_course(home, target, rendered_course, "Synthetic MLOps engineer").pathway
    server = serve(backend=ScoutFindJobsBackend(home_root=home, target=target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(
            home=home, target=target, tmp=tmp_path, pathway_id=pathway.id,
            url=f"http://127.0.0.1:{server.server_address[1]}",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


@pytest.fixture
def browser_page(ui_browser) -> Iterator[SimpleNamespace]:
    context = ui_browser.new_context()
    page = context.new_page()
    seen = SimpleNamespace(page=page, console=[])
    page.on("console", lambda message: seen.console.append(f"{message.type}: {message.text}"))
    try:
        yield seen
    finally:
        context.close()


def _concept_url(course_server: SimpleNamespace, concept_id: str) -> str:
    return f"{course_server.url}/learning/{course_server.pathway_id}/{learning_render.concept_page_filename(concept_id)}"


def test_no_console_error_on_the_index_page(course_server: SimpleNamespace, browser_page: SimpleNamespace) -> None:
    page = browser_page.page
    response = page.goto(f"{course_server.url}/learning/{course_server.pathway_id}/index.html")
    assert response is not None and response.status == 200
    from gigai.scout.find_jobs.api.learning import COURSE_CSP

    assert response.headers["content-security-policy"] == COURSE_CSP
    page.wait_for_load_state("networkidle")
    errors = [line for line in browser_page.console if line.startswith("error")]
    assert not errors, errors


def test_the_mermaid_diagram_renders_to_an_svg(course_server: SimpleNamespace, browser_page: SimpleNamespace) -> None:
    page = browser_page.page
    page.goto(_concept_url(course_server, "concept-alpha"))
    page.locator(".mermaid svg").wait_for(timeout=WAIT_MS)
    assert page.locator(".mermaid svg").count() == 1
    drawn = page.locator(".mermaid svg").text_content() or ""
    assert "Start" in drawn and "End" in drawn, drawn
    errors = [line for line in browser_page.console if line.startswith("error")]
    assert not errors, errors


def test_the_code_block_is_syntax_highlighted(course_server: SimpleNamespace, browser_page: SimpleNamespace) -> None:
    page = browser_page.page
    page.goto(_concept_url(course_server, "concept-beta"))
    block = page.locator("pre code[data-lang]").first
    block.wait_for(timeout=WAIT_MS)
    page.wait_for_function(
        "(el) => el.hasAttribute('data-highlighted')", arg=block.element_handle(), timeout=WAIT_MS,
    )
    assert block.get_attribute("data-highlighted") is not None
    assert block.locator(".hljs-string, .hljs-keyword, .hljs-built_in").count() > 0


def test_the_theme_toggle_flips_data_theme(course_server: SimpleNamespace, browser_page: SimpleNamespace) -> None:
    page = browser_page.page
    page.goto(f"{course_server.url}/learning/{course_server.pathway_id}/index.html")
    html = page.locator("html")
    assert html.get_attribute("data-theme") == "light"
    page.locator("#theme-toggle").click()
    page.wait_for_function("() => document.documentElement.getAttribute('data-theme') === 'dark'", timeout=WAIT_MS)
    assert html.get_attribute("data-theme") == "dark"
    assert (page.locator(".theme-toggle-label").text_content() or "").strip() == "Dark"
    page.locator("#theme-toggle").click()
    page.wait_for_function("() => document.documentElement.getAttribute('data-theme') === 'light'", timeout=WAIT_MS)


def test_the_sidebar_search_filters_the_lesson_list(course_server: SimpleNamespace, browser_page: SimpleNamespace) -> None:
    page = browser_page.page
    page.goto(f"{course_server.url}/learning/{course_server.pathway_id}/index.html")
    page.locator("#sidebar-search-input").wait_for(timeout=WAIT_MS)
    all_visible = page.locator("nav.sidebar ul.module-lessons li:not(.lesson-hidden)").count()
    assert all_visible == 5

    page.locator("#sidebar-search-input").fill("gamma")
    page.wait_for_function(
        "() => document.querySelectorAll('nav.sidebar ul.module-lessons li:not(.lesson-hidden)').length === 1",
        timeout=WAIT_MS,
    )
    visible = page.locator("nav.sidebar ul.module-lessons li:not(.lesson-hidden)")
    assert visible.count() == 1
    assert "gamma" in (visible.first.text_content() or "").lower()

    page.locator("#sidebar-search-input").fill("")
    page.wait_for_function(
        "() => document.querySelectorAll('nav.sidebar ul.module-lessons li:not(.lesson-hidden)').length === 5",
        timeout=WAIT_MS,
    )
