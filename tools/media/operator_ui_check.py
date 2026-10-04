"""The Jobs page in a REAL browser on an operator-sized synthetic home (0110-9-01; a release check).

0.1.10.8 passed every test and its Jobs page never finished loading on the
operator's home (290,000 postings, 10,350 companies): no test loaded the UI
on a home of that size. This does, before every release:

    make operator-ui-check          (about 2 minutes; prints the numbers, exits non-zero on a failure)

It builds the synthetic home (``tests/support/operator_home.py``: no request,
no real home is read, HOME is a temporary directory), starts the real Scout
server process on it with its background threads running, and drives the
bundled UI with Playwright (Chromium, as ``make media`` installs it):

1. the Jobs page loads from a COLD server: the "preparing" message with its
   percent is seen, then the rows;
2. ONE job is opened and its page is there; the pipeline route answers;
3. BACK: the rows are there at once (no "Loading postings…", no reload);
4. the Background panel (Settings) opens while the list is refreshed;
5. open-and-back three more times.

It asserts (a) the wall times below, (b) NO PILE-UP: never more than one
request in flight for the list, the peek or the status, counted from the
browser's own request events, and a bounded number of list requests in all,
(c) zero console errors, page errors and failed responses. The numbers go to
``<out>/operator-ui-check.json`` and the screenshots beside it.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]

#: Wall-clock bounds, in seconds (measured on the worker's laptop, 2026-10-03: see the printed numbers).
COLD_ROWS_SECONDS = float(os.environ.get("GIGAI_OPERATOR_UI_COLD_SECONDS", "120"))
JOB_PAGE_SECONDS = 5.0
BACK_ROWS_SECONDS = 1.0
BACKGROUND_PANEL_SECONDS = 5.0
ROUTE_SECONDS = 1.0
#: The pipeline route asked from a job page, beside the dozen requests the page itself sends as it opens.
JOB_PAGE_ROUTE_SECONDS = 2.0
#: One list read on the first load (plus one after "preparing"), and at most one refresh for each return.
MAX_LIST_REQUESTS = 8
WATCHED = {"list": "/api/postings", "peek": "/api/new", "status": "/api/postings/status"}
VIEWPORT = {"width": 1280, "height": 800}


class CheckFailed(RuntimeError):
    pass


def _browsers_path(real_home: Path) -> str:
    if sys.platform == "darwin":
        return str(real_home / "Library" / "Caches" / "ms-playwright")
    return str(Path(os.environ.get("XDG_CACHE_HOME") or real_home / ".cache") / "ms-playwright")


class _Requests:
    """The browser's own request events: what is in flight per watched resource, and what failed."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.open: dict[object, tuple[str, float]] = {}
        self.max_in_flight = {name: 0 for name in WATCHED}
        self.max_api_in_flight = 0
        self.sent = {name: 0 for name in WATCHED}
        self.durations: dict[str, list[float]] = {}
        self.problems: list[str] = []
        self.aborted = 0

    def _kind(self, url: str) -> str | None:
        path = urlsplit(url).path
        return next((name for name, route in WATCHED.items() if path == route), None)

    def started(self, request) -> None:
        path = urlsplit(request.url).path
        if not path.startswith("/api/"):
            return
        self.open[request] = (path, time.monotonic())
        kind = self._kind(request.url)
        if kind is not None:
            self.sent[kind] += 1
        for name, route in WATCHED.items():
            count = sum(1 for found, _at in self.open.values() if found == route)
            self.max_in_flight[name] = max(self.max_in_flight[name], count)
        self.max_api_in_flight = max(self.max_api_in_flight, len(self.open))

    def finished(self, request) -> None:
        found = self.open.pop(request, None)
        if found is not None:
            self.durations.setdefault(found[0], []).append(time.monotonic() - found[1])

    def failed(self, request) -> None:
        found = self.open.pop(request, None)
        failure = request.failure or ""
        if "ERR_ABORTED" in failure:
            self.aborted += 1  # the page left: the store cancels what it had in flight
        elif found is not None:
            self.problems.append(f"request failed: {request.method} {found[0]} ({failure})")

    def response(self, response) -> None:
        if response.status >= 400:
            self.problems.append(f"{response.status} {response.request.method} {response.url.replace(self.base, '')}")


def _start_server(home: str, target: str, log_path: Path) -> tuple[subprocess.Popen, str]:
    from tests.support import operator_home

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    log = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "gigai.scout.find_jobs.present_api", "--home", home, "--target", target, "--port", str(port), "--allow-test-seams"],
        env=dict(os.environ, **operator_home.SEAM_ENV), stdout=log, stderr=subprocess.STDOUT,
    )
    return process, f"http://127.0.0.1:{port}"


def run(out: Path, *, postings: int, companies: int, log=print) -> dict[str, object]:
    from playwright.sync_api import sync_playwright

    from tests.support import operator_home

    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="gigai-operator-ui-")).resolve() / "op"
    built = operator_home.build(root, postings=postings, companies=companies, log=lambda line: log(f"  {line}"))
    log(f"[operator-ui] home: {built.postings} postings x {built.companies} companies in {built.build_seconds} s")
    process, base = _start_server(built.home, built.target, out / "server.log")
    numbers: dict[str, object] = {"postings": built.postings, "companies": built.companies, "home_build_seconds": built.build_seconds}
    failures: list[str] = []
    requests = _Requests(base)
    console: list[str] = []

    def check(ok: bool, what: str) -> None:
        if not ok:
            failures.append(what)

    row = '[data-testid="job-row"]'
    count_line = '[data-role="postings-count"]'
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce").new_page()
            page.on("console", lambda message: console.append(f"console {message.type}: {message.text}") if message.type == "error" else None)
            page.on("pageerror", lambda error: console.append(f"page error: {error}"))
            page.on("request", requests.started)
            page.on("requestfinished", requests.finished)
            page.on("requestfailed", requests.failed)
            page.on("response", requests.response)

            def timed_fetch(path: str) -> float:
                return float(page.evaluate("async (path) => { const t = performance.now(); const r = await fetch(path); await r.text(); return (performance.now() - t) / 1000; }", path))

            # 1. The Jobs page on a cold server: "preparing" with a percent, then the rows.
            deadline = time.monotonic() + 60
            while True:
                try:
                    page.goto(base + "/#/jobs")
                    break
                except Exception:  # noqa: BLE001 - the server is still starting: try again until the deadline
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.2)
            started = time.monotonic()
            seen: list[str] = []
            shot_preparing = False
            while not page.locator(row).count():
                text = page.locator(count_line).inner_text() if page.locator(count_line).count() else ""
                if text and (not seen or seen[-1] != text):
                    seen.append(text)
                if "Preparing your postings" in text and not shot_preparing:
                    page.screenshot(path=str(out / "01-jobs-preparing.png"))
                    shot_preparing = True
                    numbers["route_during_build_seconds"] = {path: round(timed_fetch(path), 3) for path in ("/api/health", "/api/pipeline", "/api/profiles")}
                if time.monotonic() - started > COLD_ROWS_SECONDS:
                    page.screenshot(path=str(out / "00-failed.png"))
                    (out / "00-failed.txt").write_text(page.inner_text("body") + "\n" + "\n".join(console + requests.problems), encoding="utf-8")
                    raise CheckFailed(f"the Jobs page showed no row in {COLD_ROWS_SECONDS} s; it said: {seen[-3:]}")
                time.sleep(0.05)
            numbers["cold_rows_seconds"] = round(time.monotonic() - started, 2)
            numbers["count_line_texts"] = seen[:3] + (["..."] if len(seen) > 6 else []) + seen[-3:]
            preparing = [text for text in seen if "Preparing your postings" in text]
            check(bool(preparing), f"the page never said it was preparing the postings; it said {seen[:5]}")
            check(len({text for text in preparing}) >= 2, f"the preparing percent never moved: {preparing[:5]}")
            during = numbers.get("route_during_build_seconds") or {}
            check(all(seconds < ROUTE_SECONDS for seconds in during.values()), f"a route was slow during the build: {during}")  # type: ignore[union-attr]
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(out / "02-jobs-loaded.png"))
            rows_first = page.locator(row).count()
            numbers["rows_first"] = rows_first
            first_title = page.locator(row).first.inner_text().splitlines()[0]

            def open_and_back(step: int) -> dict[str, float]:
                lists_before = requests.sent["list"]
                at = time.monotonic()
                page.locator(f"{row} a.button").first.click()
                page.wait_for_selector('[data-testid="step-timeline"], [data-testid="job-page"], .job-page, h1', timeout=int(JOB_PAGE_SECONDS * 1000))
                page.wait_for_function("location.hash.startsWith('#/jobs/')", timeout=int(JOB_PAGE_SECONDS * 1000))
                opened = time.monotonic() - at
                pipeline = timed_fetch("/api/pipeline")
                if step == 0:
                    page.wait_for_load_state("networkidle")
                    page.screenshot(path=str(out / "03-job-page.png"))
                at = time.monotonic()
                page.go_back()
                page.wait_for_selector(row, timeout=int(BACK_ROWS_SECONDS * 1000))
                back = time.monotonic() - at
                text = page.locator(count_line).inner_text()
                check("Loading postings" not in text and "Preparing" not in text, f"back from a job: the list was loading again ({text!r})")
                check(page.locator(row).count() == rows_first, "back from a job: the rows were not the rows that had been read")
                check(page.locator(row).first.inner_text().splitlines()[0] == first_title, "back from a job: another first row")
                if step == 0:
                    page.screenshot(path=str(out / "04-jobs-back.png"))
                page.wait_for_load_state("networkidle")
                return {"job_page": round(opened, 3), "pipeline_route": round(pipeline, 3), "back_rows": round(back, 3), "list_requests": requests.sent["list"] - lists_before}

            # 2-3. Open ONE job, go BACK.
            trips = [open_and_back(0)]
            check(trips[0]["list_requests"] <= 1, f"open a job and go back fired {trips[0]['list_requests']} list requests")

            # 4. The Background panel while the list is refreshed in place.
            page.evaluate("fetch('/api/postings?limit=200')")  # a heavy read of the list, in flight beside the panel
            at = time.monotonic()
            page.goto(base + "/#/settings")
            page.wait_for_selector('[data-testid="background-panel"]', timeout=int(BACKGROUND_PANEL_SECONDS * 1000))
            numbers["background_panel_seconds"] = round(time.monotonic() - at, 3)
            numbers["routes_beside_a_list_read_seconds"] = {path: round(timed_fetch(path), 3) for path in ("/api/pipeline", "/api/settings/background", "/api/health")}
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(out / "05-background-panel.png"))
            page.goto(base + "/#/jobs")
            page.wait_for_selector(row, timeout=int(BACK_ROWS_SECONDS * 1000))

            # 5. Three more.
            for step in range(1, 4):
                trips.append(open_and_back(step))
            numbers["open_and_back"] = trips
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(20)
        except subprocess.TimeoutExpired:
            process.kill()

    numbers["requests_sent"] = dict(requests.sent)
    numbers["max_in_flight"] = dict(requests.max_in_flight)
    numbers["max_api_in_flight"] = requests.max_api_in_flight
    numbers["aborted_on_leaving"] = requests.aborted
    numbers["slowest_seconds"] = {path: round(max(found), 3) for path, found in sorted(requests.durations.items()) if path in ("/api/postings", "/api/new", "/api/postings/status", "/api/pipeline", "/api/pipeline/job", "/api/jobs")}
    numbers["console_errors"] = console
    numbers["failed_responses"] = requests.problems
    server_log = (out / "server.log").read_text(encoding="utf-8")
    numbers["server_builds_logged"] = server_log.count("postings: built in")
    numbers["server_unhandled"] = sum(1 for line in server_log.splitlines() if "unhandled exception" in line or "Traceback" in line or " 500 " in line)

    # (a) wall times
    check(max(trip["job_page"] for trip in trips) < JOB_PAGE_SECONDS, f"a job page took too long: {trips}")
    check(max(trip["back_rows"] for trip in trips) < BACK_ROWS_SECONDS, f"back to the list took too long: {trips}")
    check(max(trip["pipeline_route"] for trip in trips) < JOB_PAGE_ROUTE_SECONDS, f"the pipeline route was slow on a job page: {trips}")
    beside = numbers["routes_beside_a_list_read_seconds"]
    check(all(seconds < ROUTE_SECONDS for seconds in beside.values()), f"a route was slow beside a list read: {beside}")  # type: ignore[union-attr]
    # (b) no pile-up
    check(all(count <= 1 for count in requests.max_in_flight.values()), f"more than one request in flight for a resource: {requests.max_in_flight}")
    check(requests.sent["list"] <= MAX_LIST_REQUESTS, f"{requests.sent['list']} list requests in all (at most {MAX_LIST_REQUESTS})")
    check(sum(trip["list_requests"] for trip in trips) <= len(trips), f"the returns fired more than one refresh each: {trips}")
    check(numbers["server_builds_logged"] == 1, f"the server built the postings {numbers['server_builds_logged']} times")
    # (c) nothing went wrong on the way
    check(not console, f"console errors: {console[:5]}")
    check(not requests.problems, f"failed responses: {requests.problems[:5]}")
    check(numbers["server_unhandled"] == 0, "the server log has an unhandled exception or a 500")
    numbers["failures"] = failures
    (out / "operator-ui-check.json").write_text(json.dumps(numbers, indent=2), encoding="utf-8")
    return numbers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPO / "build" / "operator-ui-check")
    parser.add_argument("--postings", type=int, default=290_000)
    parser.add_argument("--companies", type=int, default=10_350)
    args = parser.parse_args(argv)

    # Before HOME moves: where Playwright keeps its browsers for the real user. Nothing else of the real home is used.
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _browsers_path(Path.home()))
    os.environ["HOME"] = str(Path(tempfile.mkdtemp(prefix="gigai-operator-ui-home-")).resolve())
    os.environ.pop("GIGAI_HOME", None)
    sys.path.insert(0, str(REPO))

    try:
        numbers = run(args.out.resolve(), postings=args.postings, companies=args.companies)
    except CheckFailed as error:
        print(f"[operator-ui] FAILED: {error}", file=sys.stderr)
        return 1
    print(json.dumps({key: value for key, value in numbers.items() if key != "failures"}, indent=2))
    if numbers["failures"]:
        print("[operator-ui] FAILED:\n  " + "\n  ".join(numbers["failures"]), file=sys.stderr)  # type: ignore[arg-type]
        return 1
    print(f"[operator-ui] OK: numbers and screenshots in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
