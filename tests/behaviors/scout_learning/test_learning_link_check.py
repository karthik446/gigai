"""0.1.11.10 shipped lessons, slice 6: the release-time link check (``tools/learning_link_check.py``).

A FAKE WEB (a table of answers, through the real outbound policy of ``learning_fetch``) over a SYNTHETIC shipped
course folder. No network, no model. Every host, page, tool and lesson here is made up.

1. THE LINKS of a course folder: lesson sources, path items with their anchor and section phrase, path sources.
2. THE CLASSES: ok, redirected, dead, 429 then ok (asked again later), still 429, anchor gone (and the section
   found under another id), robots refusal, not checked when the budget ends.
3. THE PATHS that fall below ``learning_paths.STEPS_MIN`` / ``SETUP_MIN`` once their lost links are dropped.
4. THE POLICY: each page is asked once, the 11th request to a host waits for the rate window, hosts run in
   parallel while one host is never asked twice at the same time.
5. THE COMMAND: the plan is printed first; without ``GIGAI_LINK_CHECK=1`` nothing is fetched; the report is two
   files outside the package and the course folder.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import threading

import pytest

from gigai.scout import learning_fetch as lf
from gigai.scout import learning_paths as lp
from gigai.scout.find_jobs import outbound_guard
from gigai.scout.find_jobs.outbound_guard import PublicTarget
from gigai.scout.learning_fetch import RawResponse
from tools import learning_link_check as llc

PUBLIC = "93.184.216.34"
HTML = {"content-type": "text/html; charset=utf-8"}
DOCS = "https://docs.toolx.example"
OTHER = "https://guide.other.example"
TEXT = "Plain words of a documentation page. " * 20


def page(title: str, headings: Mapping[str, str] | None = None) -> RawResponse:
    body = "".join(f'<h2 id="{anchor}">{text}</h2><p>{TEXT}</p>' for anchor, text in (headings or {}).items()) or f"<p>{TEXT}</p>"
    return RawResponse(200, HTML, f"<html><head><title>{title}</title></head><body>{body}</body></html>".encode("utf-8"))


def redirect(location: str) -> RawResponse:
    return RawResponse(301, {"location": location})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FakeWeb:
    """The network as a table ``url -> answer`` (a list is one answer per request, the last one repeated). Unknown: 404."""

    def __init__(self, pages: Mapping[str, object], clock: Clock | None = None) -> None:
        self.pages = dict(pages)
        self.asked: list[str] = []
        self.at: list[float] = []
        self.clock = clock

    def get(self, target: PublicTarget, *, max_bytes: int) -> RawResponse:
        self.asked.append(target.url)
        if self.clock is not None:
            self.at.append(self.clock())
        answer = self.pages.get(target.url, RawResponse(404))
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, RawResponse)
        return answer

    def pages_asked(self) -> list[str]:
        return [url for url in self.asked if not url.endswith("/robots.txt")]


@pytest.fixture(autouse=True)
def names(monkeypatch: pytest.MonkeyPatch) -> None:
    """The name lookup as a table: every host is public. No resolver is asked."""

    monkeypatch.setattr(outbound_guard, "resolve_host", lambda host, timeout=None: (PUBLIC,))


# ---------------------------------------------------------------------------
# A synthetic shipped course folder
# ---------------------------------------------------------------------------

STEP_PAGES = 4


def item(n: int, url: str, section: str) -> dict[str, object]:
    return {
        "n": n, "do": f"Do thing {n}.", "why": "It shows the idea.", "done_when": "It works.",
        "source": {"title": "ToolX guide", "url": url, "verified": {"http": 200, "section_found": section, "checked": "2026-01-01"}},
    }


def step_url(n: int) -> str:
    return f"{DOCS}/guide/page-{n % STEP_PAGES}#step-{n}"


def make_path(lesson_id: str, *, steps: int = lp.STEPS_MIN, setup: int = lp.SETUP_MIN) -> dict[str, object]:
    return {
        "concept": lesson_id, "tool": "ToolX", "alternatives": "", "why_this_tool": "It is the tool of the lesson.",
        "setup": [item(n, f"{DOCS}/guide/install#setup-{n}", f"Setup {n}") for n in range(1, setup + 1)],
        "steps": [item(n, step_url(n), f"Step {n}") for n in range(1, steps + 1)],
        "sources": [{"title": "Install", "url": f"{DOCS}/guide/install", "kind": "doc"}],
    }


def make_course(root: Path, key: str = "toolx-engineer", *, lessons: Mapping[str, dict[str, object] | None] | None = None) -> Path:
    """A shipped course folder: ``lessons`` is ``{lesson id: its path, or None for a lesson with no practice page}``."""

    lessons = {"tracking-runs": make_path("tracking-runs")} if lessons is None else lessons
    folder = root / key
    (folder / "paths").mkdir(parents=True)
    course = {
        "schema_version": "scout-learning-shipped-course:1", "role_key": key, "role": "ToolX engineer", "tool_links": {},
        "modules": [{
            "id": "m1", "title": "Module one",
            "concepts": [
                {"id": lesson_id, "title": lesson_id, "sources": [{"kind": "doc", "title": "Overview", "url": f"{OTHER}/docs/overview"}]}
                for lesson_id in lessons
            ],
        }],
    }
    (folder / "course.json").write_text(json.dumps(course), encoding="utf-8")
    for lesson_id, path in lessons.items():
        if path is not None:
            (folder / "paths" / f"{lesson_id}.json").write_text(json.dumps(path), encoding="utf-8")
    return folder


def good_web(steps: int = lp.STEPS_MIN, setup: int = lp.SETUP_MIN) -> dict[str, object]:
    """Every page the default course links to, as it was when the course was generated."""

    pages: dict[str, object] = {
        f"{OTHER}/docs/overview": page("Overview", {"intro": "Intro"}),
        f"{DOCS}/guide/install": page("Install", {f"setup-{n}": f"Setup {n}" for n in range(1, setup + 1)}),
    }
    for number in range(STEP_PAGES):
        pages[f"{DOCS}/guide/page-{number}"] = page(f"Page {number}", {f"step-{n}": f"Step {n}" for n in range(1, steps + 1) if n % STEP_PAGES == number})
    return pages


def run(folder: Path, web: FakeWeb, clock: Clock | None = None, **options: object) -> dict:
    clock = clock or Clock()
    links = llc.course_links(folder)
    return llc.check_links(links, web, workers=1, clock=clock, sleep=clock.sleep, today="2026-02-02", **options)  # type: ignore[arg-type]


def rows(report: Mapping, **wanted: object) -> list[dict]:
    return [row for row in report["links"] if all(row[key] == value for key, value in wanted.items())]


# ---------------------------------------------------------------------------
# 1. The links of a course folder
# ---------------------------------------------------------------------------


def test_the_links_of_a_course_folder_are_its_lesson_sources_its_path_items_and_its_path_sources(tmp_path: Path) -> None:
    folder = make_course(tmp_path)
    links = llc.course_links(folder)
    assert [link.part for link in links].count(llc.PATH_STEP) == lp.STEPS_MIN
    assert [link.part for link in links].count(llc.PATH_SETUP) == lp.SETUP_MIN
    source = next(link for link in links if link.part == llc.LESSON_SOURCE)
    assert (source.course, source.lesson, source.url) == ("toolx-engineer", "tracking-runs", f"{OTHER}/docs/overview")
    first = next(link for link in links if link.part == llc.PATH_STEP)
    assert (first.n, first.url, first.page, first.anchor, first.section) == (1, step_url(1), f"{DOCS}/guide/page-1", "step-1", "Step 1")
    assert [link.url for link in links if link.part == llc.PATH_SOURCE] == [f"{DOCS}/guide/install"]

    plan = llc.make_plan(links)
    assert plan.courses == ("toolx-engineer",)
    assert {host: len(urls) for host, urls in plan.pages.items()} == {"guide.other.example": 1, "docs.toolx.example": STEP_PAGES + 1}
    assert plan.busiest == ("docs.toolx.example", STEP_PAGES + 1)
    assert plan.minutes == (STEP_PAGES + 1) / lf.RATE_PER_HOST_PER_MINUTE
    assert plan.budget == lf.FETCH_BUDGET


def test_a_folder_of_course_folders_is_every_course_in_it_and_a_folder_with_none_is_refused(tmp_path: Path) -> None:
    first = make_course(tmp_path, "a-role")
    second = make_course(tmp_path, "b-role")
    assert llc.find_courses([tmp_path]) == [first, second]
    assert llc.find_courses([second, tmp_path]) == [second, first]
    with pytest.raises(llc.LinkCheckError, match="no course.json"):
        llc.find_courses([tmp_path / "paths-only"])


# ---------------------------------------------------------------------------
# 2. The classes
# ---------------------------------------------------------------------------


def test_a_course_whose_pages_all_answer_is_ok_and_each_page_is_asked_once(tmp_path: Path) -> None:
    web = FakeWeb(good_web())
    report = run(make_course(tmp_path), web)
    assert {row["status"] for row in report["links"]} == {llc.OK}
    assert all(row["verified"] for row in report["links"])
    assert sorted(web.pages_asked()) == sorted(good_web())  # one GET per page, however many items cite it
    assert web.asked.count(f"{DOCS}/robots.txt") == 1 and web.asked.count(f"{OTHER}/robots.txt") == 1
    totals = report["totals"]
    assert (totals["pages"], totals["hosts"], totals["requests"], totals["robots_requests"]) == (STEP_PAGES + 2, 2, STEP_PAGES + 4, 2)
    assert totals["links"] == lp.STEPS_MIN + lp.SETUP_MIN + 2 and totals["by_status"][llc.OK] == totals["links"]
    assert report["courses"]["toolx-engineer"]["by_status"][llc.OK] == totals["links"]
    assert report["hosts"]["docs.toolx.example"]["links"] == lp.STEPS_MIN + lp.SETUP_MIN + 1
    assert report["hosts"]["guide.other.example"] == {"links": 1, "pages": 1, "by_status": {**dict.fromkeys(llc.STATUSES, 0), llc.OK: 1}}
    assert report[llc.DEAD] == [] and report["paths_below_minimum"] == [] and report["paths_unresolved"] == []
    assert report["schema_version"] == llc.SCHEMA_REPORT and report["checked"] == "2026-02-02"


def test_a_page_that_moved_on_its_host_is_redirected_and_still_verified_by_its_anchor(tmp_path: Path) -> None:
    pages = good_web()
    pages[f"{DOCS}/v2/page-1"] = pages[f"{DOCS}/guide/page-1"]
    pages[f"{DOCS}/guide/page-1"] = redirect("/v2/page-1")
    pages[f"{OTHER}/docs/overview"] = redirect(f"{OTHER}/")  # a lesson source that now lands on the home page
    pages[f"{OTHER}/"] = page("Home")
    report = run(make_course(tmp_path), FakeWeb(pages))
    moved = rows(report, part=llc.PATH_STEP, n=1)[0]
    assert (moved["status"], moved["verified"], moved["now"]) == (llc.REDIRECTED, True, f"{DOCS}/v2/page-1#step-1")
    home = rows(report, part=llc.LESSON_SOURCE)[0]
    assert (home["status"], home["verified"], home["reason"], home["now"]) == (llc.REDIRECTED, False, "home_page", f"{OTHER}/")
    assert report["totals"]["by_status"][llc.REDIRECTED] == 3  # steps 1 and 5 on page-1, and the lesson source
    assert {entry["url"] for entry in report[llc.REDIRECTED]} == {step_url(1), step_url(5), f"{OTHER}/docs/overview"}
    assert report["paths_below_minimum"] == []


def test_a_path_item_whose_page_moved_to_another_host_is_redirected_and_not_verified(tmp_path: Path) -> None:
    pages = good_web()
    pages[f"{DOCS}/guide/page-2"] = redirect(f"{OTHER}/docs/page-2")
    pages[f"{OTHER}/docs/page-2"] = page("Page 2", {"step-2": "Step 2", "step-6": "Step 6"})
    report = run(make_course(tmp_path), FakeWeb(pages))
    moved = rows(report, part=llc.PATH_STEP, n=2)[0]
    assert (moved["status"], moved["verified"], moved["now"]) == (llc.REDIRECTED, False, f"{OTHER}/docs/page-2")
    assert "another host" in moved["reason"]


def test_a_page_that_no_longer_answers_is_dead_with_its_reason_and_where_it_is_used(tmp_path: Path) -> None:
    pages = good_web()
    del pages[f"{DOCS}/guide/page-3"]  # 404 now
    pages[f"{OTHER}/docs/overview"] = RawResponse(200, {"content-type": "application/pdf"}, b"%PDF")
    report = run(make_course(tmp_path), FakeWeb(pages))
    dead = {entry["url"]: entry for entry in report[llc.DEAD]}
    assert set(dead) == {step_url(3), step_url(7), f"{OTHER}/docs/overview"}
    assert dead[step_url(3)]["reason"] == "http_404" and dead[step_url(3)]["used_by"] == ["toolx-engineer/tracking-runs steps 3"]
    assert dead[f"{OTHER}/docs/overview"]["reason"] == lf.NOT_HTML
    assert report["hosts"]["docs.toolx.example"]["by_status"][llc.DEAD] == 2
    assert report["courses"]["toolx-engineer"]["by_status"][llc.DEAD] == 3
    assert not any(row["verified"] for row in rows(report, status=llc.DEAD))


def test_a_429_is_asked_again_later_in_the_run_and_is_ok_when_it_then_answers(tmp_path: Path) -> None:
    pages = good_web()
    pages[f"{DOCS}/guide/page-0"] = [RawResponse(429), pages[f"{DOCS}/guide/page-0"]]
    web, clock, said = FakeWeb(pages), Clock(), []
    report = run(make_course(tmp_path), web, clock, say=said.append)
    assert {row["status"] for row in report["links"]} == {llc.OK}
    assert report[llc.RATE_LIMITED] == []
    assert web.pages_asked().count(f"{DOCS}/guide/page-0") == 2
    assert web.pages_asked()[-1] == f"{DOCS}/guide/page-0"  # after every other page, not straight away
    assert clock.slept == [llc.RETRY_WAITS[0]]
    assert said == ["1 page(s) answered 429 or not at all: asking again in 60 s"]


def test_a_page_that_keeps_answering_429_is_rate_limited_not_dead_and_leaves_its_path_unresolved(tmp_path: Path) -> None:
    pages = good_web()
    pages[f"{DOCS}/guide/page-0"] = RawResponse(429)
    pages[f"{OTHER}/docs/overview"] = [lf.FetchNetworkError("ConnectTimeout"), pages[f"{OTHER}/docs/overview"]]
    web, clock = FakeWeb(pages), Clock()
    report = run(make_course(tmp_path), web, clock)
    assert web.pages_asked().count(f"{DOCS}/guide/page-0") == 1 + len(llc.RETRY_WAITS)
    assert sum(clock.slept) == sum(llc.RETRY_WAITS)
    limited = rows(report, status=llc.RATE_LIMITED)
    assert {row["n"] for row in limited} == {4, 8} and not any(row["verified"] for row in limited)
    assert report[llc.DEAD] == []
    assert rows(report, part=llc.LESSON_SOURCE)[0]["status"] == llc.OK  # no answer the first time, asked again
    assert report["paths_below_minimum"] == []
    unresolved = report["paths_unresolved"]
    assert [(entry["course"], entry["lesson"]) for entry in unresolved] == [("toolx-engineer", "tracking-runs")]
    assert unresolved[0]["steps"] == {"stored": lp.STEPS_MIN, "verified": lp.STEPS_MIN - 2, "lost": 0, "unjudged": 2}


def test_an_anchor_that_left_its_page_is_anchor_gone_by_the_generators_own_check(tmp_path: Path) -> None:
    pages = good_web()
    # step-1 is gone altogether; step-5's section is still there under a new id
    pages[f"{DOCS}/guide/page-1"] = page("Page 1", {"fifth-step": "Step 5"})
    report = run(make_course(tmp_path), FakeWeb(pages))
    gone = rows(report, part=llc.PATH_STEP, n=1)[0]
    assert (gone["status"], gone["verified"], gone["now"], gone["http"]) == (llc.ANCHOR_GONE, False, None, 200)
    assert "#step-1" in gone["reason"]
    renamed = rows(report, part=llc.PATH_STEP, n=5)[0]
    assert (renamed["status"], renamed["verified"], renamed["now"]) == (llc.ANCHOR_GONE, True, f"{DOCS}/guide/page-1#fifth-step")
    assert {entry["url"] for entry in report[llc.ANCHOR_GONE]} == {step_url(1), step_url(5)}
    assert report[llc.DEAD] == []

    # the verdict is learning_paths.Verifier's: the same citation, the same answer
    fetched = lf.PageFetcher(FakeWeb(pages), clock=Clock(), sleep=lambda _seconds: None).fetch_page(f"{DOCS}/guide/page-1")

    class Answers:
        def fetch_page(self, url: str) -> lf.Fetched:
            return fetched

    verifier = lp.Verifier(Answers(), "docs.toolx.example")
    assert verifier.check(lp.Citation(0, f"{DOCS}/guide/page-1", "step-1", "Step 1", "ToolX guide")) == (None, gone["reason"])
    assert verifier.check(lp.Citation(0, f"{DOCS}/guide/page-1", "step-5", "Step 5", "ToolX guide"))[0] == renamed["now"]


def test_a_page_level_item_is_verified_by_its_title_and_fails_when_the_title_changed(tmp_path: Path) -> None:
    path = make_path("tracking-runs")
    path["steps"][0] = item(1, f"{DOCS}/guide/concepts", "Concepts of ToolX")  # type: ignore[index]
    path["steps"][1] = item(2, f"{DOCS}/guide/tutorial", "The tutorial")  # type: ignore[index]
    pages = good_web()
    pages[f"{DOCS}/guide/concepts"] = page("Concepts of ToolX")
    pages[f"{DOCS}/guide/tutorial"] = page("Something else entirely")
    report = run(make_course(tmp_path, lessons={"tracking-runs": path}), FakeWeb(pages))
    kept, changed = rows(report, part=llc.PATH_STEP, n=1)[0], rows(report, part=llc.PATH_STEP, n=2)[0]
    assert (kept["status"], kept["verified"]) == (llc.OK, True)
    assert (changed["status"], changed["verified"]) == (llc.ANCHOR_GONE, False) and "title changed" in changed["reason"]


def test_a_host_whose_robots_file_disallows_the_page_is_not_asked_and_is_robots_refused(tmp_path: Path) -> None:
    pages = good_web()
    pages[f"{DOCS}/robots.txt"] = RawResponse(200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /guide/\n")
    web = FakeWeb(pages)
    report = run(make_course(tmp_path), web)
    assert web.pages_asked() == [f"{OTHER}/docs/overview"]  # nothing under the disallowed host was asked
    refused = rows(report, host="docs.toolx.example")
    assert {row["status"] for row in refused} == {llc.ROBOTS_REFUSED} and {row["reason"] for row in refused} == {lf.ROBOTS_DISALLOWED}
    assert report[llc.DEAD] == [] and report["paths_below_minimum"] == []
    assert len(report["paths_unresolved"]) == 1  # not judged is not lost
    assert report["hosts"]["docs.toolx.example"]["by_status"][llc.ROBOTS_REFUSED] == lp.STEPS_MIN + lp.SETUP_MIN + 1


def test_pages_the_budget_did_not_reach_are_not_checked_never_dead(tmp_path: Path) -> None:
    folder = make_course(tmp_path)
    links = llc.course_links(folder)
    web, clock = FakeWeb(good_web()), Clock()
    report = llc.check_links(links, web, plan=llc.make_plan(links, budget=4), workers=1, clock=clock, sleep=clock.sleep)
    assert len(web.asked) == 4 and report["totals"]["requests"] == 4
    assert report["totals"]["by_status"][llc.NOT_CHECKED] > 0 and report[llc.DEAD] == []
    assert all(row["reason"] == "the fetch budget ended first" for row in rows(report, status=llc.NOT_CHECKED))
    assert "smaller than the pages and robots files" in "\n".join(llc.make_plan(links, budget=4).lines())


def test_an_http_link_is_dead_without_a_request(tmp_path: Path) -> None:
    path = make_path("tracking-runs")
    path["sources"] = [{"title": "Old", "url": "http://docs.toolx.example/guide/old", "kind": "doc"}]
    web = FakeWeb(good_web())
    report = run(make_course(tmp_path, lessons={"tracking-runs": path}), web)
    old = rows(report, part=llc.PATH_SOURCE)[0]
    assert (old["status"], old["reason"]) == (llc.DEAD, lf.NOT_HTTPS)
    assert not any(url.startswith("http://") for url in web.asked)


# ---------------------------------------------------------------------------
# 3. The paths below the minimum
# ---------------------------------------------------------------------------


def test_a_path_that_loses_a_step_it_cannot_spare_is_listed_below_the_minimum(tmp_path: Path) -> None:
    lessons = {"tracking-runs": make_path("tracking-runs"), "serving": make_path("serving", steps=lp.STEPS_MIN + 2), "reading-only": None}
    pages = good_web(steps=lp.STEPS_MIN + 2)
    pages[f"{DOCS}/guide/page-3"] = page("Page 3", {"step-7": "Step 7"})  # step-3 is gone: one lost step in each path
    report = run(make_course(tmp_path, lessons=lessons), FakeWeb(pages))
    below = report["paths_below_minimum"]
    assert [(entry["course"], entry["lesson"]) for entry in below] == [("toolx-engineer", "tracking-runs")]  # "serving" had two to spare
    assert below[0]["steps"] == {"stored": lp.STEPS_MIN, "verified": lp.STEPS_MIN - 1, "lost": 1, "unjudged": 0}
    assert below[0]["setup"] == {"stored": lp.SETUP_MIN, "verified": lp.SETUP_MIN, "lost": 0, "unjudged": 0}
    assert below[0]["minimum"] == {"steps": lp.STEPS_MIN, "setup": lp.SETUP_MIN} == report["minimum"]
    assert report["paths_unresolved"] == []
    text = llc.report_text(report)
    assert f"Paths below the minimum ({lp.STEPS_MIN} steps, {lp.SETUP_MIN} setup items) once their lost links are dropped: 1" in text
    assert f"  toolx-engineer/tracking-runs: steps {lp.STEPS_MIN - 1} of {lp.STEPS_MIN} verified (1 lost)" in text


def test_a_path_that_loses_a_setup_item_is_below_the_minimum_too(tmp_path: Path) -> None:
    pages = good_web()
    pages[f"{DOCS}/guide/install"] = page("Install", {"setup-1": "Setup 1"})
    report = run(make_course(tmp_path), FakeWeb(pages))
    assert [entry["setup"]["lost"] for entry in report["paths_below_minimum"]] == [1]


# ---------------------------------------------------------------------------
# 4. The policy: the rate per host, hosts in parallel, one request per host at a time
# ---------------------------------------------------------------------------


def test_the_eleventh_request_to_a_host_waits_for_the_rate_window_and_the_order_is_kept(tmp_path: Path) -> None:
    path = make_path("tracking-runs")
    wanted = [f"{DOCS}/ref/topic-{n}" for n in range(14)]
    path["sources"] = [{"title": f"Topic {n}", "url": url, "kind": "doc"} for n, url in enumerate(wanted)]
    pages = good_web()
    pages.update({url: page("Topic", {"a": "A"}) for url in wanted})
    clock = Clock()
    web = FakeWeb(pages, clock)
    report = run(make_course(tmp_path, lessons={"tracking-runs": path}), web, clock)
    assert report[llc.DEAD] == []
    host = [(url, at) for url, at in zip(web.asked, web.at) if url.startswith(DOCS)]
    assert [url for url, _at in host if "/ref/" in url] == wanted  # asked in the order they are stored
    times = [at for _url, at in host]
    assert len(times) == 1 + 1 + STEP_PAGES + len(wanted)  # robots, install, the step pages, the topics
    assert times[: lf.RATE_PER_HOST_PER_MINUTE] == [1000.0] * lf.RATE_PER_HOST_PER_MINUTE
    assert all(later - earlier >= lf.RATE_WINDOW_SECONDS for earlier, later in zip(times, times[lf.RATE_PER_HOST_PER_MINUTE :]))
    other = [at for url, at in zip(web.asked, web.at) if url.startswith(OTHER)]
    assert other == [1000.0, 1000.0]  # the other host's robots file and page: no wait of its own


def test_hosts_are_asked_in_parallel_and_one_host_is_never_asked_twice_at_once(tmp_path: Path) -> None:
    class Meeting(FakeWeb):
        """Each host's first page waits (up to 5 s) for the other host to be mid-request: both must be in flight together."""

        def __init__(self, pages: Mapping[str, object]) -> None:
            super().__init__(pages)
            self.lock = threading.Lock()
            self.in_flight: dict[str, int] = {}
            self.most: dict[str, int] = {}
            self.arrived = {"docs.toolx.example": threading.Event(), "guide.other.example": threading.Event()}
            self.met: list[bool] = []

        def get(self, target: PublicTarget, *, max_bytes: int) -> RawResponse:
            with self.lock:
                self.in_flight[target.host] = self.in_flight.get(target.host, 0) + 1
                self.most[target.host] = max(self.most.get(target.host, 0), self.in_flight[target.host])
                answer = super().get(target, max_bytes=max_bytes)
            if not target.url.endswith("/robots.txt") and not self.arrived[target.host].is_set():
                self.arrived[target.host].set()
                other = next(event for host, event in self.arrived.items() if host != target.host)
                self.met.append(other.wait(5))
            with self.lock:
                self.in_flight[target.host] -= 1
            return answer

    web = Meeting(good_web())
    links = llc.course_links(make_course(tmp_path))
    report = llc.check_links(links, web, workers=llc.HOST_WORKERS)
    assert {row["status"] for row in report["links"]} == {llc.OK}
    assert web.met == [True, True]  # each host's job saw the other host's request in flight
    assert web.most == {"docs.toolx.example": 1, "guide.other.example": 1}


# ---------------------------------------------------------------------------
# 5. The command
# ---------------------------------------------------------------------------


def test_without_the_consent_variable_the_plan_is_printed_and_nothing_is_fetched(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = make_course(tmp_path / "courses")
    web = FakeWeb(good_web())
    out = tmp_path / "out"
    assert llc.main([str(folder), "--out", str(out)], network=web, environ={}) == 2
    captured = capsys.readouterr()
    assert web.asked == [] and not out.exists()
    assert f"{lp.STEPS_MIN + lp.SETUP_MIN + 2} distinct links on {STEP_PAGES + 2} pages of 2 hosts" in captured.out
    assert f"Estimated time: at least {(STEP_PAGES + 1) / lf.RATE_PER_HOST_PER_MINUTE:.1f} min (busiest host docs.toolx.example: {STEP_PAGES + 1} pages)" in captured.out
    assert f"  docs.toolx.example: {STEP_PAGES + 1} pages" in captured.out and "No model call." in captured.out
    assert "set GIGAI_LINK_CHECK=1 explicitly" in captured.err


def test_with_consent_the_estimate_comes_first_and_the_report_is_two_files_outside_the_course(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = make_course(tmp_path / "courses")
    before = {path: path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}
    pages = good_web()
    del pages[f"{DOCS}/guide/page-3"]
    out = tmp_path / "out"
    assert llc.main([str(tmp_path / "courses"), "--out", str(out)], network=FakeWeb(pages), environ={llc.CONSENT_ENV: "1"}) == 0
    printed = capsys.readouterr().out
    assert printed.index("Estimated time: at least") < printed.index("Learning link check, ")
    assert sorted(path.name for path in out.iterdir()) == ["report.json", "report.txt"]
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == llc.SCHEMA_REPORT and len(report[llc.DEAD]) == 2
    text = (out / "report.txt").read_text(encoding="utf-8")
    assert text in printed
    assert "Per course:\n  toolx-engineer: " in text and "Per host:\n  docs.toolx.example: " in text
    assert f"Dead links: 2\n  {step_url(3)} (http_404) [toolx-engineer/tracking-runs steps 3]" in text
    assert {path: path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()} == before  # the course is not written


def test_the_report_is_refused_inside_the_course_folder_or_the_package_before_any_request(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = make_course(tmp_path)
    web = FakeWeb(good_web())
    package = Path(lf.__file__).resolve().parents[1]
    for out in (folder / "report", package / "scout" / "data" / "learning" / "link-check"):
        assert llc.main([str(folder), "--out", str(out)], network=web, environ={llc.CONSENT_ENV: "1"}) == 2
        assert "nothing is written into the package" in capsys.readouterr().err
        assert not out.exists()
    assert web.asked == []


def test_a_folder_that_is_not_a_course_is_refused_with_exit_code_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert llc.main([str(tmp_path)], network=FakeWeb({}), environ={llc.CONSENT_ENV: "1"}) == 2
    assert "no course.json" in capsys.readouterr().err


def test_the_tool_reuses_the_generators_policy_and_checks_and_holds_no_copy() -> None:
    source = Path(llc.__file__).read_text(encoding="utf-8")
    assert "lf.PageFetcher(" in source and "lp.Verifier(" in source
    assert llc._ITEM_MINIMUM == {"steps": lp.STEPS_MIN, "setup": lp.SETUP_MIN}
    for rebuilt in ("HTMLParser", "urlopen", "httpx", "requests.get", "robotparser"):
        assert rebuilt not in source  # no second fetcher, parser or robots reader
    makefile = (Path(llc.__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\nlearning-link-check:\n", 1)[1].split("\n\n", 1)[0]
    assert "tools/learning_link_check.py" in target and "GIGAI_LINK_CHECK" in target
    lanes = makefile.split("\ntest: ", 1)[1].split("\n", 1)[0]
    assert "learning-link-check" not in lanes  # never part of the default lane
