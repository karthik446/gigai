"""The flows that are about SIZE, on the operator-sized home (`make ui-test-full`; never `make ui-test`).

290,000 postings over 10,350 companies, 2 profiles, about 600 matched (`tests/support/operator_home.py`, synthetic,
built once per session), the REAL server process with its background threads running, nothing stubbed. The cold
load is `test_operator_sized_jobs.py`, which runs first (`UI_ORDER`); these run on the server it left warm, and say
so: each opens Jobs with the cold patience and applies its warm budgets only when the page did not have to wait for
the build.

The background rank lane ranks about 200 not-assessed postings every half minute for the first minutes of this
server, which MOVES rows in the list while these tests run. So a page is compared with the answer the server gave
that page (`jobs_page`), never with a second read of the list a moment later.

1. Real pages (0110-10-01, the real route; `test_jobs_pagination.py` is the same page on an answered list): page 2
   is rows 51-100 (ONE request, `offset=50`), the address says `page=2`; a job is opened from page 2 and Back lands on
   page 2 with the same rows and no "Loading"; the page's own "Jobs" link lands on page 2 as well; the last page holds
   the rest and Next is off.
2. The chips, for their cost at this size: New, 7 days, 30 days, a profile, a state: one request each. And the
   weak-fit chip (0110-10-02) on the real list: there, off, with the server's count, and the default list asks for
   no state.
3. Answers (0.1.10.8: the first `POST /api/answers` on a home of this size took 266 s): the agent writes an answer
   with a source through the real route, and the page shows it "Written by your agent · source: ...".

Every test ends on the same checks: at most one request in flight for the list, the peek and the status; the
server's peak memory under its ceiling; zero console errors, page errors, HTTP >= 400.

MEASURED (14-core laptop, 2026-10-04, three runs: Python 3.11 twice, 3.13 once): warm Jobs load 0.6 to 1.3 s wall,
0.7 to 1.4 server CPU seconds; page 2: 0.15 to 0.18 s, 0.1 CPU seconds; the last page (offset 600): 0.6 to 0.9 s, 0.7
to 1.1 CPU seconds; Back 0.03 s, 0 list requests; a chip 0.11 to 0.19 s, 0.07 to 0.13 CPU seconds; the agent's answer
2.9 to 3.7 s wall, 2.1 to 2.7 CPU seconds. The server's peak memory is in the run's budget report.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from tests.ui import jobs_page
from tests.ui.jobs_page import LIST, WAITING, identities, pressed
from tests.ui.operator_home_ui import SERVER_RSS_MB
from tests.ui.support import FIRST_LOAD_WALL_SECONDS, INTERACTIVE_WALL_SECONDS, tid
from tools.media.operator_ui_check import COLD_ROWS_SECONDS, WATCHED

pytestmark = [pytest.mark.ui, pytest.mark.operator_sized]

HOME = "operator-sized"
WARM_LOAD_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS
WARM_LOAD_CPU_SECONDS = 5.0  # 0.7 to 1.4 measured
PAGE_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
PAGE_CPU_SECONDS = 4.0  # page 2: 0.1 measured; the LAST page (offset 600): 0.7 to 1.1; the rank lane's own work lands in the same process
OPEN_JOB_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
BACK_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
BACK_CPU_SECONDS = 3.0  # as test_operator_sized_jobs.py
CHIP_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
CHIP_CPU_SECONDS = 2.0  # 0.07 to 0.13 measured
ANSWER_WRITE_WALL_SECONDS = 15.0  # 2.9 to 3.7 s measured; 266 s on 0.1.10.8
ANSWER_WRITE_CPU_SECONDS = 10.0  # 2.1 to 2.7 measured
ANSWERS_PAGE_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS

PAGER_JS = """([page, count]) => {
  const pager = document.querySelector('[data-testid="pager"]');
  return !!pager && pager.dataset.page === String(page)
    && document.querySelectorAll('[data-testid="job-row"]').length === count
    && !((document.querySelector('[data-role="postings-count"]') || {}).textContent || 'Loading').startsWith('Loading')
    && (document.querySelector('[data-role="postings-count"]') || {dataset: {}}).dataset.refreshing !== 'true';
}"""


def open_jobs(ui) -> dict:
    """Open Jobs and return the server's answer the page drew its rows from; the warm budgets apply on a warm server."""

    ui.watch_count_line()
    with ui.page.expect_response(lambda response: urlsplit(response.url).path == LIST and response.status == 200, timeout=int(COLD_ROWS_SECONDS * 1000)) as answered:
        ui.goto("/#/jobs")
    ui.wait_for_jobs_list(timeout_ms=int(COLD_ROWS_SECONDS * 1000))
    ui.step("loaded")
    if not [line for line in ui.count_lines() if line and line.startswith("Preparing")]:
        ui.cpu_budget(f"Jobs load, warm ({HOME})", WARM_LOAD_CPU_SECONDS, "start", "loaded")
        ui.wall_budget(f"Jobs load, warm ({HOME})", WARM_LOAD_WALL_SECONDS, "start", "loaded")
    ui.settle()
    return answered.value.json()


def the_usual_end(ui) -> None:
    for resource in WATCHED.values():
        ui.no_more_than_one_in_flight(resource)
    ui.memory_budget(f"the server's peak memory ({HOME})", SERVER_RSS_MB)
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests


def change_page(ui, selector: str, page: int, step: str) -> dict:
    """Click a pager control; return the server's answer for that page (ONE request) once its rows are shown."""

    before = f"before-{step}"
    ui.step(before)
    with ui.page.expect_response(lambda response: urlsplit(response.url).path == LIST) as answered:
        ui.page.click(selector)
    answer = answered.value.json()
    ui.page.wait_for_function(PAGER_JS, arg=[page, answer["counts"]["shown"]])
    ui.step(step)
    assert ui.requests_between(before, step, LIST) == 1
    assert identities(jobs_page.shown(ui)["rows"]) == identities(answer["postings"]["rows"])
    ui.cpu_budget(f"page change {step} ({HOME})", PAGE_CPU_SECONDS, before, step)
    ui.wall_budget(f"page change {step} ({HOME})", PAGE_WALL_SECONDS, before, step)
    answer["asked"] = parse_qs(urlsplit(answered.value.url).query)
    return answer


def test_real_pages_on_the_operator_sized_home(operator_ui) -> None:
    ui = operator_ui
    first = open_jobs(ui)
    matched = first["counts"]["matched"]
    pages = -(-matched // 50)
    assert matched > 150, f"the operator-sized home matches {matched} postings: too few for several pages"
    assert ui.job_rows() == 50 and jobs_page.count_line(ui) == f"Showing 1-50 of {matched} postings"
    pager = ui.page.locator(tid("pager"))
    assert pager.get_attribute("data-page") == "1" and pager.get_attribute("data-pages") == str(pages)
    assert ui.page.locator(tid("pager-prev")).is_disabled()

    # Page 2 is rows 51-100: one request for them, and the address says so.
    second = change_page(ui, tid("pager-next"), 2, "page2")
    assert second["asked"] == {"limit": ["50"], "offset": ["50"]}
    assert "page=2" in ui.page.url and jobs_page.count_line(ui) == f"Showing 51-100 of {matched} postings"
    page_two = jobs_page.shown(ui)["rows"]
    assert len(page_two) == 50

    # Open a job from page 2 and come Back: page 2, the same rows, at once.
    picked = page_two[4]
    ui.page.locator(f"{tid('job-row')} [data-action='open-job']").nth(4).click()
    ui.wait_for_job_page()
    ui.step("opened")
    assert ui.page.locator(".job-page .job-title").text_content() == picked["title"]
    assert "#/jobs/" in ui.page.url and "page=" not in ui.page.url
    ui.wall_budget(f"open a job from page 2 ({HOME})", OPEN_JOB_WALL_SECONDS, "page2", "opened")
    ui.settle()
    ui.step("job-settled")
    seen = len(ui.count_lines())
    ui.page.go_back()
    ui.page.wait_for_function(PAGER_JS, arg=[2, 50])
    ui.step("back")
    assert "page=2" in ui.page.url, f"Back from the job did not land on page 2: {ui.page.url}"
    assert identities(jobs_page.shown(ui)["rows"]) == identities(page_two), "back from the job: page 2 does not show the rows it had"
    assert jobs_page.count_line(ui) == f"Showing 51-100 of {matched} postings"
    said = [line for line in ui.count_lines()[seen:] if line]
    assert not [line for line in said if line.startswith(WAITING)], f"back from the job: the list was loading again; the count line said {said}"
    ui.settle()
    ui.step("back-settled")
    assert ui.requests_between("job-settled", "back-settled", LIST) <= 1  # at most the one refresh in place, never a re-list
    ui.cpu_budget(f"Back to page 2 ({HOME})", BACK_CPU_SECONDS, "job-settled", "back-settled")
    ui.wall_budget(f"Back to page 2 ({HOME})", BACK_WALL_SECONDS, "job-settled", "back")

    # The job page's own "Jobs" link is a plain #/jobs: it lands on the page the list was left on.
    ui.page.locator(f"{tid('job-row')} [data-action='open-job']").nth(4).click()
    ui.wait_for_job_page()
    ui.settle()
    ui.step("opened-again")
    ui.page.click(".job-page a.back-link")
    ui.page.wait_for_function(PAGER_JS, arg=[2, 50])
    ui.page.wait_for_function("() => window.location.hash.includes('page=2')")
    assert identities(jobs_page.shown(ui)["rows"]) == identities(page_two)
    ui.settle()
    assert ui.requests_after("opened-again", LIST) <= 1

    # The last page holds the rest, and there is no next one.
    last = change_page(ui, f"{tid('pager-page')}[data-page='{pages}']", pages, "last-page")
    rest = matched - 50 * (pages - 1)
    assert last["asked"] == {"limit": ["50"], "offset": [str(50 * (pages - 1))]}
    assert ui.job_rows() == rest and jobs_page.count_line(ui) == f"Showing {50 * (pages - 1) + 1}-{matched} of {matched} postings"
    assert ui.page.locator(tid("pager-next")).is_disabled() and f"page={pages}" in ui.page.url

    assert ui.writes_after("start") == [], "turning pages is a read: nothing may be written"
    the_usual_end(ui)


def test_the_chips_on_the_operator_sized_home(operator_ui) -> None:
    ui = operator_ui
    first = open_jobs(ui)
    matched = first["counts"]["matched"]
    profiles = first["profiles"]

    # 0110-10-02 on the real list: the weak-fit chip is there, off, with the server's count; the default list asks for no state.
    weak = ui.page.locator('[data-role="state-filter"] [data-state="weak_fit"]')
    assert weak.get_attribute("aria-pressed") == "false"
    assert (weak.text_content() or "").split() == ["Weak", "fit", str(first["counts"]["weak_fit"])]
    asked = [item.path for item in ui.network.order if item.resource == LIST]
    assert asked and all("state=" not in path for path in asked), asked
    assert "weak_fit" not in {row["state"] for row in jobs_page.shown(ui)["rows"]}

    def chip(selector: str, step: str, query: str) -> tuple[dict, dict]:
        return jobs_page.click_chip(ui, selector, step, query, home=HOME, cpu_seconds=CHIP_CPU_SECONDS, wall_seconds=CHIP_WALL_SECONDS)

    def all_back() -> None:
        ui.page.wait_for_function("() => window.location.hash === '#/jobs'")
        ui.page.wait_for_function(jobs_page.SETTLED_ROWS_JS, arg=50)
        ui.settle()

    new_chip, week_chip, month_chip = tid("time-chip-new"), tid("time-chip-7d"), tid("time-chip-30d")
    truth, page = chip(new_chip, "new", "window=new")
    assert truth["counts"]["matched"] == first["counts"]["new"] > 0
    assert all(row["isNew"] for row in page["rows"]) and pressed(ui, new_chip)
    chip(week_chip, "7d", "window=7d")  # (the generator's clock is fixed: this window empties a week after its NOW)
    assert pressed(ui, week_chip) and not pressed(ui, new_chip)
    chip(month_chip, "30d", "window=30d")
    ui.page.click(month_chip)
    all_back()

    second = profiles[1]
    truth, page = chip('[data-role="profile-filter-chip"] >> nth=1', "profile", f"profile_id={second['profile_id']}")
    assert truth["counts"]["matched"] == second["matched"] < matched
    assert all(second["label"] in row["profiles"] for row in page["rows"])
    ui.page.click('[data-role="profile-filter-chip"] >> nth=1')
    all_back()

    needs = '[data-role="state-filter"] [data-state="needs_answers"]'
    truth, page = chip(needs, "needs_answers", "state=needs_answers")
    assert truth["counts"]["matched"] > 0 and {row["state"] for row in page["rows"]} == {"needs_answers"}
    assert ui.page.locator('.stat-tile:has-text("Postings") .stat-value').first.text_content() == str(matched), "the header total changed with a filter on"

    assert ui.writes_after("start") == []
    the_usual_end(ui)


def test_the_agent_writes_an_answer_and_the_page_shows_it_on_the_operator_sized_home(operator_ui) -> None:
    ui = operator_ui
    source = "browser-flow agent, operator-sized home"
    ui.goto("/#/answers")
    ui.page.locator('[data-role="answers"]').wait_for()
    ui.step("open")
    ui.wall_budget(f"Answers and stories page ({HOME})", ANSWERS_PAGE_WALL_SECONDS, "start", "open")
    ui.settle()

    # The agent writes through the real route. On 0.1.10.8 the first write on a home of this size took 266 s.
    ui.step("before-write")
    written = ui.server_json(
        "/api/answers",
        {"question_id": "kafka:operations", "question": "Have you operated Kafka in production?", "answer": "Yes. Three years running a 12-broker cluster.", "actor": "agent", "source": source},
        timeout=COLD_ROWS_SECONDS,
    )["answer"]
    ui.step("written")
    question_id = written["question_id"]
    try:
        ui.cpu_budget(f"the agent writes an answer ({HOME})", ANSWER_WRITE_CPU_SECONDS, "before-write", "written")
        ui.wall_budget(f"the agent writes an answer ({HOME})", ANSWER_WRITE_WALL_SECONDS, "before-write", "written")
        ui.page.click('[data-action="refresh"]')
        row = ui.page.locator(f'[data-role="answers"] li[data-item-id="{question_id}"]')
        row.wait_for()
        lines = row.locator("p.muted.small").all_text_contents()
        assert f"Written by your agent · source: {source} · updated {written['updated_at'][:10]}" in lines, lines
    finally:
        ui.server_json(f"/api/answers/{question_id.replace(':', '%3A')}?revision={written['revision']}", method="DELETE")
    the_usual_end(ui)
