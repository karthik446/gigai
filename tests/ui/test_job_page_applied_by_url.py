"""0.1.11.6 (APPLIED-01): a job page opened BY ITS ADDRESS always opens, whatever the posting's application state.

Real server and page (the small home: two profiles, no model call, no network); synthetic postings only.

The page used to find a posting no run or assessment of the SELECTED profile carries by searching the Jobs list
(`GET /api/postings?limit=200`, then the Removed list). Since 0.1.11.5 that list leaves out the postings with an
application, so such a page said "We have no stored posting at this address" and the application could not be moved
on (Rejected, ...). The page now asks for the one posting by its address (`GET /api/postings?job=<address>`), which
hides nothing; the list still leaves applied postings out.

This flow CHANGES the shared home (application events are append-only), so it runs late (`UI_ORDER`); the selected
profile is put back.

- applied, assessed on the default profile, ALSO matched (never assessed) by the selected profile: the page opens by
  its address and from the Applications page's link, shows "Applied" and the next states, and "Rejected" is recorded;
- applied, assessed on the default profile, NOT matched by the selected profile: the page opens;
- applied, assessed by no profile: the page opens (the default profile selected);
- a posting the list's first 200 rows do not hold (every list answer has it taken out here): the page opens;
- each open is ONE read by address and no read of a 200-row list.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from tests.ui.jobs_page import LIST

pytestmark = pytest.mark.ui

UI_ORDER = 86  # changes the shared home (application events): after the applied-badge flow, before "Assess all" and the profile's delete

PAGE_BADGE = '.job-page [data-role="application-badge"]'
NOT_FOUND = "We have no stored posting at this address"
NEXT_STATES = ["interview_scheduled", "offer_received", "rejected", "withdrawn"]
SELECT = 'select[aria-label="Profile"]'


def _rows(ui) -> list[dict]:
    return ui.server_json(f"{LIST}?limit=200")["postings"]["rows"]


def _free(ui, scout_server, wanted) -> str:
    """A listed posting with no application (and not the hero job) that `wanted(states by profile)` accepts."""

    for row in _rows(ui):
        states = {item["profile_id"]: item["state"] for item in row["profiles"]}
        if row["job_identity"] != scout_server.demo.hero_job and row["application"] is None and wanted(states):
            return row["job_identity"]
    raise AssertionError("the small home has no posting of the shape this flow needs")


def _profiles(scout_server) -> tuple[str, str]:
    default = scout_server.demo.hero_profile_id
    other = next(value for value in scout_server.demo.profiles.values() if value != default)
    return default, other


def _apply(ui, job: str) -> None:
    ui.server_json("/api/applications", {"job_identity": job, "event_kind": "applied"})
    assert job not in [row["job_identity"] for row in _rows(ui)], "0.1.11.5: the list leaves an applied posting out"


def _select(ui, profile_id: str) -> None:
    """Choose the profile in the top bar, as the user does (the Jobs list is of every profile and has no selector)."""

    ui.goto("/#/assess")
    ui.page.locator(SELECT).wait_for()
    ui.settle()
    if ui.page.locator(SELECT).input_value() != profile_id:
        with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == "/api/profiles/selection"):
            ui.page.locator(SELECT).select_option(profile_id)
        ui.settle()
    assert ui.page.locator(SELECT).input_value() == profile_id


class _Reads:
    """The postings reads the page sends from here on: the queries of `GET /api/postings`."""

    def __init__(self, ui) -> None:
        self.queries: list[dict[str, list[str]]] = []
        self._page, self._seen = ui.page, lambda request: self._add(request)
        ui.page.on("request", self._seen)

    def _add(self, request) -> None:
        parts = urlsplit(request.url)
        if request.method == "GET" and parts.path == LIST:
            self.queries.append(parse_qs(parts.query))

    def stop(self) -> None:
        self._page.remove_listener("request", self._seen)

    def by_address(self) -> list[str]:
        return [query["job"][0] for query in self.queries if "job" in query]

    def lists(self) -> list[dict[str, list[str]]]:
        return [query for query in self.queries if "job" not in query]


def _opens(ui, job: str, *, by: str = "address") -> None:
    """The job's page is shown (never the not-found panel), after ONE read by address and no list read."""

    reads = _Reads(ui)
    try:
        ui.page.goto("about:blank")  # a fresh load: the page holds no row from an earlier open
        if by == "address":
            ui.goto("/#/jobs/" + quote(job, safe=""))
        else:
            ui.goto("/#/applications")
            link = ui.page.locator(f"a[href='#/jobs/{quote(job, safe='')}']").first
            link.wait_for()
            ui.settle()
            reads.queries.clear()
            link.click()
        ui.page.wait_for_function(
            "(words) => !!document.querySelector('.job-page .job-title') || Array.from(document.querySelectorAll('.panel h2')).some((h) => h.textContent.includes(words))",
            arg=NOT_FOUND,
        )
        ui.settle()
        assert ui.page.locator(".panel h2", has_text=NOT_FOUND).count() == 0, f"the page says '{NOT_FOUND}' for {job} (opened by {by})"
        ui.wait_for_job_page()
        assert reads.by_address() == [job], f"one read by address, not {reads.queries}"
        assert reads.lists() == [], f"no list is searched for one posting: {reads.lists()}"
    finally:
        reads.stop()


def _offers_the_next_states(ui, status: str = "applied") -> None:
    ui.page.locator(PAGE_BADGE).wait_for()
    assert ui.page.locator(PAGE_BADGE).get_attribute("data-status") == status
    offered = [ui.page.locator(f'.job-page [data-event="{kind}"]').count() for kind in NEXT_STATES]
    assert offered == [1, 1, 1, 1], f"Interview scheduled / Offer received / Rejected / Withdrawn: {dict(zip(NEXT_STATES, offered))}"


@pytest.fixture
def selection(ui, scout_server):
    """The default and the other profile; the default is selected again afterwards."""

    default, other = _profiles(scout_server)
    try:
        yield default, other
    finally:
        ui.server_json("/api/profiles/selection", {"profile_id": default})


def test_applied_on_the_default_profile_and_matched_by_the_selected_one_opens_and_can_be_rejected(ui, scout_server, selection) -> None:
    default, other = selection
    job = _free(ui, scout_server, lambda states: states.get(default) not in (None, "not_assessed") and states.get(other) == "not_assessed")
    _apply(ui, job)
    _select(ui, other)

    _opens(ui, job)
    _offers_the_next_states(ui)
    _opens(ui, job, by="the Applications page")
    _offers_the_next_states(ui)

    with ui.page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == "/api/applications") as posted:
        ui.page.click('.job-page [data-event="rejected"]')
    assert posted.value.ok, posted.value.text()
    ui.page.wait_for_function("() => (document.querySelector('.job-page [data-role=\"application-badge\"]') || {}).dataset?.status === 'rejected'")
    applied = ui.server_json(f"{LIST}?limit=200&state=applied")["postings"]["rows"]
    assert {row["job_identity"]: row["application"]["status"] for row in applied}.get(job) == "rejected"
    # Rejected is still an application: the page opens again, the list still leaves it out.
    _opens(ui, job)
    assert ui.page.locator(PAGE_BADGE).get_attribute("data-status") == "rejected"
    assert job not in [row["job_identity"] for row in _rows(ui)]
    ui.assert_clean()


def test_applied_on_the_default_profile_and_not_matched_by_the_selected_one_opens(ui, scout_server, selection) -> None:
    default, other = selection
    job = _free(ui, scout_server, lambda states: states.get(default) not in (None, "not_assessed") and other not in states)
    _apply(ui, job)
    _select(ui, other)
    _opens(ui, job)
    _offers_the_next_states(ui)
    _opens(ui, job, by="the Applications page")
    ui.assert_clean()


def test_applied_and_assessed_by_no_profile_opens(ui, scout_server, selection) -> None:
    default, _other = selection
    job = _free(ui, scout_server, lambda states: set(states.values()) == {"not_assessed"})
    _apply(ui, job)
    _select(ui, default)
    _opens(ui, job)
    _offers_the_next_states(ui)
    ui.assert_clean()


def test_a_posting_the_first_200_rows_of_the_list_do_not_hold_opens(ui, scout_server, selection) -> None:
    """Every LIST answer has the posting taken out (it is "at row 250"); the read by address is the server's own."""

    default, _other = selection
    job = _free(ui, scout_server, lambda states: set(states.values()) == {"not_assessed"})
    _select(ui, default)

    def without_the_job(route) -> None:
        if route.request.method != "GET" or "job" in parse_qs(urlsplit(route.request.url).query):
            route.continue_()
            return
        response = route.fetch()
        body = response.json()
        if response.status == 200 and "postings" in body:
            body["postings"]["rows"] = [row for row in body["postings"]["rows"] if row["job_identity"] != job]
        route.fulfill(status=response.status, content_type="application/json", body=json.dumps(body))

    ui.page.goto("about:blank")
    ui.page.route("**/api/postings?*", without_the_job)
    try:
        _opens(ui, job)
    finally:
        ui.page.unroute_all()
    ui.assert_clean()
