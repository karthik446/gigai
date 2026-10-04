"""Flow 11 (REPORT.md 5.3): the one-time network notice before the very first Update sources.

The notice is asked once, when the store holds no company yet ("first" is the server's fact: `GET
/api/sources/update` -> `index`). The small home has had an update, and a real first update on the real server
would seed the bundled catalog of real companies (and change the shared home), so this test answers the two
`/api/sources/update` calls itself: the GET with the real server's own answer made empty (`index.status: empty`, no
company indexed), and the POST with a plain "accepted", so NOTHING is started on the server. The page, the dialog
and what the browser remembers are the real ones.

Pinned: Update sources on a never-updated home opens the notice and sends nothing; the notice says the same two
sentences as the CLI (`gigai.scout.wording`) and where background checks are turned off; "Not now" closes it, sends
nothing and remembers nothing, so the next click asks again; "Update sources" in the notice starts the update the
click asked for (ONE POST) and remembers that the notice was read; after that the button starts an update without
asking; the Jobs page's own Update sources asks the same question in a browser that has not seen it. Zero console
errors.

MEASURED (14-core laptop, 2026-10-04, three runs: Python 3.11 twice, 3.13 once): the notice is on screen 0.04 s after
the click.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

from gigai.scout.wording import NETWORK_NOTICE_BODY, NETWORK_NOTICE_LEAD
from tests.ui.support import INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui

STATUS = "/api/sources/update"
SEEN_KEY = "scout.networkNotice.seen"
NOTICE_WALL_SECONDS = INTERACTIVE_WALL_SECONDS


class NeverUpdated:
    """Answers `/api/sources/update` as a home that has never had an update; an update "starts" without the server."""

    def __init__(self) -> None:
        self.status: dict | None = None
        self.posts: list[dict] = []

    def __call__(self, route) -> None:
        request = route.request
        if urlsplit(request.url).path != STATUS:
            route.continue_()
            return
        if self.status is None:
            real = route.fetch().json() if request.method == "GET" else {}
            real["index"] = {**(real.get("index") or {}), "status": "empty", "needs_update": True, "companies_indexed": 0, "last_checked_at": None}
            real["update"], real["running"] = None, False
            self.status = real
        if request.method == "POST":
            self.posts.append(json.loads(request.post_data or "{}"))
            route.fulfill(status=202, content_type="application/json", body=json.dumps(self.status))
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.status))


def seen(ui) -> str | None:
    return ui.page.evaluate(f"() => window.localStorage.getItem('{SEEN_KEY}')")


def test_the_first_update_asks_once(ui) -> None:
    server = NeverUpdated()
    ui.page.route("**/api/sources/update*", server)
    notice = ui.page.locator(tid("network-notice"))
    button = ui.page.locator('[data-role="sources-update"] [data-action="update-sources"]')

    ui.goto("/#/settings")
    ui.page.locator('[data-role="sources-update"]').wait_for()
    ui.page.wait_for_function("""() => { const b = document.querySelector('[data-role="sources-update"] [data-action="update-sources"]'); return b && !b.disabled; }""")
    assert seen(ui) is None and notice.count() == 0

    # The first click asks; nothing is sent.
    ui.step("before-click")
    button.click()
    notice.wait_for()
    ui.step("asked")
    assert server.posts == [] and ui.writes_after("start") == []
    text = " ".join((notice.locator('[data-role="network-notice-text"]').text_content() or "").split())
    assert text == f"{NETWORK_NOTICE_LEAD} {NETWORK_NOTICE_BODY}", "the notice does not say what the CLI says"
    assert notice.locator('[data-role="network-notice-text"] strong').text_content() == NETWORK_NOTICE_LEAD
    settings_line = notice.locator('[data-role="network-notice-settings"]')
    assert "Settings > Background updates" in (settings_line.text_content() or "") and settings_line.locator("a").get_attribute("href") == "#/settings"
    ui.wall_budget("the network notice opens", NOTICE_WALL_SECONDS, "before-click", "asked")

    # Not now: closed, nothing sent, nothing remembered; the next click asks again.
    notice.locator('[data-action="network-notice-cancel"]').click()
    notice.wait_for(state="detached")
    assert server.posts == [] and seen(ui) is None
    button.click()
    notice.wait_for()

    # Update sources (in the notice): the update the click asked for starts, once, and the notice is remembered.
    ui.step("before-continue")
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == STATUS):
        notice.locator('[data-action="network-notice-continue"]').click()
    notice.wait_for(state="detached")
    assert server.posts == [{}] and ui.writes_after("before-continue") == [f"POST {STATUS}"]
    assert seen(ui) == "1"

    # Read once: the button now starts an update without asking.
    ui.page.wait_for_function("""() => { const b = document.querySelector('[data-role="sources-update"] [data-action="update-sources"]'); return b && !b.disabled; }""")
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == STATUS):
        button.click()
    assert notice.count() == 0 and len(server.posts) == 2

    # And it stays read after a reload (this browser), while the store is still empty.
    ui.reload()
    ui.page.locator('[data-role="sources-update"]').wait_for()
    assert seen(ui) == "1"
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests


def test_the_jobs_page_asks_the_same_question(ui) -> None:
    server = NeverUpdated()
    ui.page.route("**/api/sources/update*", server)
    ui.goto("/#/jobs")
    button = ui.page.locator('[data-role="sources-strip"] [data-action="update-sources-strip"]')
    button.wait_for()
    assert seen(ui) is None, "a new browser has not read the notice"
    button.click()
    notice = ui.page.locator(tid("network-notice"))
    notice.wait_for()
    assert notice.locator('[data-role="network-notice-text"] strong').text_content() == NETWORK_NOTICE_LEAD
    notice.locator('[data-action="network-notice-cancel"]').click()
    notice.wait_for(state="detached")
    assert server.posts == [] and seen(ui) is None and ui.writes_after("start") == []
    ui.assert_clean()
