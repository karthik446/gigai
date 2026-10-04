"""0110-9-01 in a real browser on the operator-sized home: Jobs loads, open a job, Back, the list is still there.

0.1.10.8 passed every test and its Jobs page never finished loading on the operator's home: no
test loaded the UI on a home of that size. This one does (`make ui-test-full`; never `make ui-test`):
290,000 postings over 10,350 companies, 2 profiles (`tests/support/operator_home.py`, synthetic,
built once per session in a temporary HOME), the REAL server process with its background threads
running, the REAL list route (nothing is stubbed), cold: nothing has read the list before the page.

The flow and what is pinned:

1. `#/jobs` on the cold server: the count line says "Preparing your postings ... N%" while the
   server builds, then the rows are there; WHILE it builds, a second tab opens Settings (both
   background panels) and an assessed job's page, and both are there (the bug file's addendum:
   the rest of the UI stays usable while the list loads);
2. the first job is opened: its page is there;
3. Back: the rows that were read are shown at once, the same rows, and the count line never says
   "Loading" or "Preparing" again; at most one list request (the refresh in place);
4. the whole way: at most one request in flight for the list, the peek and the status; one build on
   the server; zero console errors, page errors, HTTP >= 400, failed requests; no traceback and no
   500 in the server log.

Every check is collected and the test fails once, at the end, with all of them: a run costs about
a minute, so one red run should say everything that is wrong. Only a step that cannot go on (no
row at all) stops the flow early.

The budgets follow the suite's order (structure, server CPU seconds, wall-clock last and loose);
the numbers they come from are beside them (MEASURED). The timing ceilings (wall-clock, server CPU,
server memory) are reported, and fail the test only with GIGAI_UI_BUDGETS=enforce
(tests/ui/README.md). On 0.1.10.8 (`GIGAI_UI_SERVER_ROOT=<a v0.1.10.8 checkout>`) the same test is
red on its structural checks, and on all of them with `enforce`: see tests/ui/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from urllib.parse import quote
import urllib.request

import pytest

from tests.support.operator_home import DEFAULT_LABEL
from tests.ui import support
from tests.ui.operator_home_ui import SERVER_RSS_MB
from tests.ui.support import FIRST_LOAD_WALL_SECONDS, tid
from tools.media.operator_ui_check import COLD_ROWS_SECONDS, JOB_PAGE_SECONDS, WATCHED

pytestmark = [pytest.mark.ui, pytest.mark.operator_sized]
UI_ORDER = -1  # the COLD flow: the first to read the operator-sized list, before every other test of the run

PREPARING = "Preparing your postings"
WAITING = ("Loading", "Still loading", PREPARING)

#: MEASURED (14-core laptop, 2026-10-03, the full home, three runs: Python 3.11 twice, 3.13 once). The right column is
#: the same test on 0.1.10.8 (GIGAI_UI_SERVER_ROOT on a v0.1.10.8 checkout, two runs), where it fails:
#:   cold rows        9.6 to 11.2 s wall, 9.1 to 9.4 server CPU seconds (the one build)   63 to 67 s, 51 to 76 CPU s, never "preparing"
#:   job page         0.11 to 0.14 s                                                      0.13 s
#:   Back             0.04 to 0.12 s wall, 0.1 to 0.2 CPU seconds, 0 list requests        "Loading postings…" again: rows after 4 s
#:                                                                                        in one run, none in 30 s in the other
#:   list requests    2 on the cold load (the "preparing" answer, then the rows)          2, and 2 peeks in flight at once
#:   server peak RSS  203 to 210 MB                                                       845 MB
#: With the second tab (2026-10-04, three runs, Python 3.11 twice and 3.13 once): cold rows 10.4 to 12.2 s wall, 12.3 to
#: 14.1 server CPU seconds (the second tab's requests are served beside the build); in the second tab, Settings 0.85
#: to 0.87 s and a job page 0.47 to 0.71 s, the list still being prepared when both were there; server peak RSS 229 to
#: 255 MB.
COLD_CPU_SECONDS = 45.0  # about 5x: a CI core is 2 to 3 times slower than this laptop's
COLD_WALL_SECONDS = 60.0  # reported: about 6x; the hard stop is COLD_ROWS_SECONDS (with no row there is nothing to open)
BACK_CPU_SECONDS = 3.0  # a refresh in place of one page at the most, beside whatever the background pipeline does
BACK_WALL_SECONDS = 5.0  # reported, loose: the rows are already in the page's store
BESIDE_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS  # reported: a page in a new tab while the server builds the list
#: How long Back may take before the flow gives up (a patience: the budget is BACK_WALL_SECONDS).
BACK_PATIENCE_SECONDS = 30.0
#: The first read (answered "preparing"), the read after the build, one spare.
COLD_LIST_REQUESTS = 3


def server_json(server, path: str) -> dict:
    with urllib.request.urlopen(server.url + path, timeout=30) as response:
        return json.loads(response.read())


#: The count line has said "Preparing", or rows are already there (a server that never says it is preparing).
PREPARING_OR_ROWS_JS = """() => (window.__gigaiCountLines || []).some((line) => line && line.startsWith('Preparing'))
  || document.querySelectorAll('[data-testid="job-row"]').length > 0"""


def beside_the_build(ui, server, numbers: dict[str, object], check) -> None:
    """While the first tab waits for the list, a second tab opens Settings and an assessed job's page."""

    try:
        ui.page.wait_for_function(PREPARING_OR_ROWS_JS, timeout=int(COLD_ROWS_SECONDS * 1000))
    except Exception:  # Playwright's TimeoutError: the flow's own wait, next, says what the page showed
        return
    if ui.job_rows():
        numbers["beside_the_build"] = "not run: the list was not being prepared when the page was looked at"
        return
    tab = ui.page.context.new_page()
    books = support.Network()
    books.attach(tab)
    try:
        job = server_json(server, f"/api/assessments?profile_id={server.built.profiles[DEFAULT_LABEL]}")["items"][0]["job"]["job_identity"]
        started = time.monotonic()
        tab.goto(server.url + "/#/settings")
        tab.wait_for_selector(f"{tid('background-panel')} [data-role='pipeline-status']")
        tab.wait_for_selector('[data-role="background-settings"] #background-auto-refresh')
        settings_seconds = time.monotonic() - started
        started = time.monotonic()
        tab.goto(server.url + "/#/jobs/" + quote(job, safe=""))
        tab.wait_for_function(support.JOB_PAGE_READY_JS)
        job_seconds = time.monotonic() - started
        numbers["beside_the_build"] = {
            "settings_seconds": round(settings_seconds, 2), "job_page_seconds": round(job_seconds, 2),
            "list_still_preparing_after": ui.job_rows() == 0,
        }
        for name, seconds in (("Settings in a second tab while the list is prepared", settings_seconds), ("a job page in a second tab while the list is prepared", job_seconds)):
            try:
                ui.wall_budget_of(f"{name} (operator-sized)", BESIDE_WALL_SECONDS, seconds)
            except AssertionError as error:  # only with GIGAI_UI_BUDGETS=enforce
                check(False, error)
        problems = books.problems()
        check(not problems, f"the second tab reported {len(problems)} problem(s) while the list was prepared: {problems[:5]}")
    except Exception as error:
        check(False, f"while the list was being prepared, a second tab could not open Settings and a job page: {type(error).__name__}: {str(error).splitlines()[0]}")
    finally:
        tab.close()
        books.drop_open("dropped when the second tab closed")


def test_jobs_load_open_a_job_and_back_on_the_operator_sized_home(operator_ui, operator_server, ui_artifacts: Path, request: pytest.FixtureRequest) -> None:
    ui, server = operator_ui, operator_server
    failures: list[str] = []
    numbers: dict[str, object] = {
        "postings": server.built.postings, "companies": server.built.companies, "profiles": len(server.built.profiles),
        "home_build_seconds": round(server.build_seconds, 1), "home_prebuilt": server.prebuilt, "server_start_seconds": round(server.start_seconds, 2),
        "server_root": str(server.server_root) if server.server_root else None,
    }

    def check(ok: bool, what: object) -> None:
        if not ok:
            failures.append(str(what))

    def measured(value: support.Measured, limit: float) -> None:
        check(value <= limit, f"{value!r} is over {limit}")

    def cpu(name: str, limit: float, first: str, last: str) -> None:
        try:
            ui.cpu_budget(f"{name} (operator-sized)", limit, first, last)
        except AssertionError as error:  # only with GIGAI_UI_BUDGETS=enforce
            failures.append(str(error))

    def wall(name: str, limit: float, first: str, last: str) -> None:
        try:
            ui.wall_budget(f"{name} (operator-sized)", limit, first, last)
        except AssertionError as error:  # only with GIGAI_UI_BUDGETS=enforce
            failures.append(str(error))

    # The home is the operator's SIZE and nothing of the operator's: a temporary HOME, which is the server's HOME.
    import psutil

    assert server.built.postings >= 290_000 and server.built.companies >= 10_000 and len(server.built.profiles) >= 2
    support.refuse_real_home(server.home, environ={})
    server_env = psutil.Process(server.pid).environ()
    assert server_env.get("HOME") == str(server.home) and "GIGAI_HOME" not in server_env, "the server does not run on the temporary HOME"
    assert Path(server.built.home).resolve().is_relative_to(server.home)

    # 1. Jobs on the cold server: "preparing" with a percent, then the rows.
    ui.watch_count_line()
    ui.goto("/#/jobs")
    beside_the_build(ui, server, numbers, check)
    ui.wait_for_jobs_list(timeout_ms=int(COLD_ROWS_SECONDS * 1000))  # the only hard stop: with no row there is nothing to open
    ui.step("loaded")
    cold_lines = [line for line in ui.count_lines() if line]
    preparing = [line for line in cold_lines if line.startswith(PREPARING)]
    numbers["cold_rows_seconds"] = round(ui.wall_seconds_between("start", "loaded"), 2)
    numbers["cold_server_cpu_seconds"] = round(ui.server_cpu_seconds_between("start", "loaded"), 2)
    numbers["count_lines_cold"] = cold_lines[:2] + (["..."] if len(cold_lines) > 4 else []) + cold_lines[-2:]
    numbers["preparing_lines"] = len(preparing)
    check(bool(preparing), f"the page never said it was preparing the postings on the cold server; it said {cold_lines[:4]}")
    rows_first = ui.job_rows()
    first_title = ui.page.locator(f"{tid('job-row')} [data-action='open-job']").first.text_content()
    numbers["rows_first"] = rows_first
    check(rows_first > 1, f"the Jobs list shows {rows_first} row(s) of a home with {server.built.matched_titles} matched titles")
    measured(ui.requests_after("start", WATCHED["list"]), COLD_LIST_REQUESTS)
    cpu("Jobs cold load", COLD_CPU_SECONDS, "start", "loaded")
    wall("Jobs cold load", COLD_WALL_SECONDS, "start", "loaded")

    # 2. Open the first job.
    ui.page.locator(f"{tid('job-row')} [data-action='open-job']").first.click()
    ui.wait_for_job_page(timeout_ms=int(JOB_PAGE_SECONDS * 4 * 1000))  # the title, the state line and the timeline, not a heading alone
    ui.step("opened")
    check("#/jobs/" in ui.page.url, f"the job page's address is {ui.page.url}")
    numbers["job_page_seconds"] = round(ui.wall_seconds_between("loaded", "opened"), 2)
    wall("open a job", JOB_PAGE_SECONDS, "loaded", "opened")

    # 3. Back: the list is still there.
    lines_before_back = len(ui.count_lines())
    ui.page.go_back()
    try:
        ui.wait_for_jobs_list(timeout_ms=int(BACK_PATIENCE_SECONDS * 1000))
    except AssertionError as error:
        failures.append(f"back from the job: {error}")
    else:
        ui.step("back")
        back_lines = [line for line in ui.count_lines()[lines_before_back:] if line]
        numbers["back_rows_seconds"] = round(ui.wall_seconds_between("opened", "back"), 2)
        numbers["count_lines_back"] = back_lines[:4]
        check(not [line for line in back_lines if line.startswith(WAITING)], f"back from the job: the list was loading again; the count line said {back_lines[:4]}")
        check(ui.job_rows() == rows_first, f"back from the job: {ui.job_rows()} rows, {rows_first} had been read")
        back_title = ui.page.locator(f"{tid('job-row')} [data-action='open-job']").first.text_content()
        check(back_title == first_title, f"back from the job: the first row is {back_title!r}, it was {first_title!r}")
        ui.page.wait_for_load_state("networkidle")  # the refresh in place, if there is one, ends before it is counted
        ui.step("settled")
        measured(ui.requests_after("opened", WATCHED["list"]), 1)  # at most the one refresh in place, never a re-list
        cpu("Back to Jobs", BACK_CPU_SECONDS, "opened", "settled")
        wall("Back to Jobs", BACK_WALL_SECONDS, "opened", "back")
        numbers["back_server_cpu_seconds"] = round(ui.server_cpu_seconds_between("opened", "settled"), 2)

    # 4. No pile-up, the background threads were running, and nothing went wrong on the way.
    peaks = {name: ui.network.peak_in_flight(resource) for name, resource in WATCHED.items()}
    numbers["max_in_flight"] = peaks
    numbers["requests_sent"] = {name: int(ui.requests_after("start", resource)) for name, resource in WATCHED.items()}
    numbers["api_requests"] = int(ui.requests_after("start"))
    for resource in WATCHED.values():
        try:
            ui.no_more_than_one_in_flight(resource)
        except AssertionError as error:
            failures.append(str(error))
    pipeline = server_json(server, "/api/pipeline")
    runner = pipeline.get("runner") or {}
    numbers["pipeline"] = {"enabled": (pipeline.get("setting") or {}).get("enabled"), "runner_active": runner.get("active"), "last": (runner.get("last") or {}).get("state")}
    check(numbers["pipeline"]["enabled"] is True and runner.get("active") is True, f"the background pipeline thread was not running: {numbers['pipeline']}")  # type: ignore[index]
    log = server.log_text()
    numbers["server_builds_logged"] = log.count("postings: built in")
    check(numbers["server_builds_logged"] == 1, f"the server built the postings {numbers['server_builds_logged']} times (once is the rule)")
    bad = [line for line in log.splitlines() if "unhandled exception" in line or "Traceback" in line or " 500 " in line]
    check(not bad, f"the server log has an unhandled exception or a 500: {bad[:3]}")
    numbers["server_peak_rss_mb"] = round(ui.peak_server_rss_mb())
    try:
        ui.memory_budget("the server's peak memory, cold load (operator-sized)", SERVER_RSS_MB)
    except AssertionError as error:  # only with GIGAI_UI_BUDGETS=enforce
        failures.append(str(error))
    problems = ui.problems()
    check(not problems, f"{len(problems)} problem(s) in the browser: {problems[:5]}")  # zero console errors, page errors, HTTP >= 400

    numbers["failures"] = failures
    ui_artifacts.mkdir(parents=True, exist_ok=True)
    (ui_artifacts / "operator-sized-jobs.json").write_text(json.dumps(numbers, indent=2), encoding="utf-8")
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("[ui] operator-sized Jobs flow: " + json.dumps({key: value for key, value in numbers.items() if key != "failures"}))
    assert not failures, f"{len(failures)} check(s) failed on the operator-sized home:\n  " + "\n  ".join(failures)
