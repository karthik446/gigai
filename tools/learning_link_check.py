"""Release-time link check of the SHIPPED learning courses (0.1.11.10 shipped lessons, slice 6).

A shipped course is package data: ``<course folder>/course.json`` and ``<course folder>/paths/<lesson id>.json``.
Both carry links to public documentation that were verified when the course was generated. Documentation moves.
This step re-verifies every one of those links before a release and writes a REPORT. It is not user-facing (no
``gigai`` command), it calls no model, and it writes nothing into the package: a dead link is fixed by a person
(or by regenerating that one path), never by this tool.

    GIGAI_LINK_CHECK=1 make learning-link-check LINK_CHECK_COURSES=<folder> [LINK_CHECK_OUT=<folder>]
    GIGAI_LINK_CHECK=1 uv run --locked python tools/learning_link_check.py <course folder>... [--out <folder>]

Without ``GIGAI_LINK_CHECK=1`` it prints what it WOULD fetch (links, pages, hosts, the time estimate) and stops
with exit code 2: it talks to other people's servers, so it never runs in a default lane.

WHAT IS FETCHED, AND HOW. Each distinct page (a URL without its fragment) is asked for ONCE with a ``GET``: a
``HEAD`` does not prove that an anchor still exists. Every request goes through the generator's own outbound
policy, ``gigai.scout.learning_fetch.PageFetcher``: ``https`` only, a public address, each redirect hop vetted,
``robots.txt`` honoured for every host, at most ``RATE_PER_HOST_PER_MINUTE`` requests a minute to one host, at most
``FETCH_BUDGET`` requests per course. Hosts are asked in parallel and the pages of one host one after the other
(a redirect that leaves its host is the one request that can meet another host's; the rate limit still counts
it). A page that answered ``429`` (or did not answer at all) is asked again later in the run, :data:`RETRY_WAITS`.

WHAT IS CHECKED. A practice-path item links to ``page#anchor`` and stores the section phrase it was verified
with (``source.verified.section_found``). The item is re-checked by the generator's own
``gigai.scout.learning_paths.Verifier``: the anchor id is still on the page, else the section phrase still is
(under a new id), and for an item that cites a whole page, the page is still a deep page with the same title.
A lesson's source and a path's source list are pages: they must still answer and not have become a home page.

EVERY LINK IS ONE OF:

* ``ok``: answered 200 where it was stored, anchor and section found;
* ``redirected``: answered after a redirect (``now`` is where it is today);
* ``dead``: did not answer 200 (``http_404``, ``network_error``, ``not_html``, ...), ``reason`` says which;
* ``rate_limited``: still ``429`` after the retries (not a verdict on the link);
* ``anchor_gone``: the page answered but the anchor id is not on it (``now`` is set when the section phrase was
  found under another id: the link needs that id);
* ``robots_refused``: the host's ``robots.txt`` disallows the page or could not be read (not asked);
* ``not_checked``: the fetch budget ended before the page was reached.

``verified`` on a link says whether the generator's rule would still KEEP it. A practice path needs
``learning_paths.STEPS_MIN`` steps and ``SETUP_MIN`` setup items: the report lists the paths that fall below
that once their lost items are dropped (``paths_below_minimum``), and those that would only if the links that
could not be judged (rate-limited, robots, not checked) are lost too (``paths_unresolved``).

The report is ``<out>/report.json`` (everything) and ``<out>/report.txt`` (the short text that is also printed).
The exit code is 0 whenever the report was written: the release note decides what a dead link means.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import urlsplit

from gigai.scout import learning_fetch as lf
from gigai.scout import learning_paths as lp
from gigai.scout.learning_course import course_urls, usable_url
from gigai.scout.learning_render import load_paths

SCHEMA_REPORT = "scout-learning-link-check:1"
CONSENT_ENV = "GIGAI_LINK_CHECK"

OK = "ok"
REDIRECTED = "redirected"
DEAD = "dead"
RATE_LIMITED = "rate_limited"
ANCHOR_GONE = "anchor_gone"
ROBOTS_REFUSED = "robots_refused"
NOT_CHECKED = "not_checked"
#: Every class, mildest first: a URL stored in several places is reported with the worst of its results.
STATUSES = (OK, REDIRECTED, NOT_CHECKED, ROBOTS_REFUSED, RATE_LIMITED, ANCHOR_GONE, DEAD)
#: Not a verdict on the link: it could not be judged in this run.
UNJUDGED = frozenset({RATE_LIMITED, ROBOTS_REFUSED, NOT_CHECKED})

_HTTP_429 = "http_429"
#: A page with one of these answers is asked again later in the run.
RETRY_ERRORS = frozenset({_HTTP_429, lf.NETWORK_ERROR})
#: Seconds waited before each retry pass (one rate window, then two).
RETRY_WAITS = (lf.RATE_WINDOW_SECONDS, 2 * lf.RATE_WINDOW_SECONDS)
#: Hosts asked at the same time. One request at a time goes to any one host, whatever this is.
HOST_WORKERS = 32

LESSON_SOURCE, TOOL_LINK, PATH_SETUP, PATH_STEP, PATH_SOURCE = "lesson_source", "tool_link", "setup", "steps", "path_source"
_ITEM_MINIMUM = {PATH_STEP: lp.STEPS_MIN, PATH_SETUP: lp.SETUP_MIN}


class LinkCheckError(ValueError):
    """The input is not a shipped course folder, or the report may not be written where it was asked."""


@dataclass(frozen=True)
class Link:
    """One stored link. ``lesson`` is ``""`` for a course's ``tool_links``; ``n`` is a path item's number."""

    course: str
    lesson: str
    part: str
    url: str
    n: int | None = None
    #: What the link was verified with: the section phrase of a path item (``""``: none stored).
    section: str = ""
    title: str = ""

    @property
    def page(self) -> str:
        return self.url.split("#", 1)[0].strip()

    @property
    def anchor(self) -> str:
        return self.url.split("#", 1)[1] if "#" in self.url else ""


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# The links a shipped course folder carries
# ---------------------------------------------------------------------------


def find_courses(folders: Sequence[Path]) -> list[Path]:
    """The course folders named: a folder that holds ``course.json``, or the ones directly under a folder that does not."""

    found: list[Path] = []
    for folder in folders:
        folder = Path(folder)
        inside = [folder] if (folder / "course.json").is_file() else sorted(path.parent for path in folder.glob("*/course.json"))
        if not inside:
            raise LinkCheckError(f"{folder}: no course.json here or one folder down (a shipped course is course.json plus paths/)")
        found.extend(path for path in inside if path not in found)
    return found


def course_links(course_dir: Path) -> list[Link]:
    """Every external link of one shipped course, in the order it is stored."""

    course_dir = Path(course_dir)
    try:
        course = json.loads((course_dir / "course.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LinkCheckError(f"{course_dir / 'course.json'}: not readable JSON ({type(exc).__name__})") from exc
    if not isinstance(course, Mapping):
        raise LinkCheckError(f"{course_dir / 'course.json'}: not a JSON object")
    role_key = course.get("role_key")
    key = role_key if isinstance(role_key, str) and role_key else course_dir.name
    links: list[Link] = []
    lesson_ids: list[str] = []
    for module in course.get("modules") or []:
        for lesson in (module.get("concepts") or []) if isinstance(module, Mapping) else []:
            if not isinstance(lesson, Mapping) or not isinstance(lesson.get("id"), str):
                continue
            lesson_ids.append(lesson["id"])
            links.extend(Link(key, lesson["id"], LESSON_SOURCE, url) for url in course_urls(lesson))
    tool_links = course.get("tool_links")
    for url in tool_links.values() if isinstance(tool_links, Mapping) else []:
        if isinstance(url, str):
            links.append(Link(key, "", TOOL_LINK, url))
    try:
        paths = load_paths(course_dir / "paths", set(lesson_ids))
    except (OSError, ValueError) as exc:
        raise LinkCheckError(f"{course_dir / 'paths'}: a path file is not readable JSON ({type(exc).__name__})") from exc
    for lesson_id in lesson_ids:
        path = paths.get(lesson_id)
        if not isinstance(path, Mapping):
            continue
        for part in (PATH_SETUP, PATH_STEP):
            for index, item in enumerate(path.get(part) or [], 1):
                source = item.get("source") if isinstance(item, Mapping) else None
                source = source if isinstance(source, Mapping) else {}
                verified = source.get("verified")
                verified = verified if isinstance(verified, Mapping) else {}
                links.append(Link(
                    key, lesson_id, part, str(source.get("url") or ""), index,
                    section=str(verified.get("section_found") or ""), title=str(source.get("title") or ""),
                ))
        for source in path.get("sources") or []:
            if isinstance(source, Mapping) and isinstance(source.get("url"), str):
                links.append(Link(key, lesson_id, PATH_SOURCE, source["url"]))
    return links


# ---------------------------------------------------------------------------
# The plan: what will be fetched, and how long that takes at least
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """What a run will ask for. ``pages`` is ``{host: [page URL]}``, each page once, in stored order."""

    courses: tuple[str, ...]
    links: int
    pages: Mapping[str, tuple[str, ...]]
    budget: int

    @property
    def page_count(self) -> int:
        return sum(len(urls) for urls in self.pages.values())

    @property
    def busiest(self) -> tuple[str, int]:
        return max(((host, len(urls)) for host, urls in self.pages.items()), key=lambda item: item[1], default=("", 0))

    @property
    def minutes(self) -> float:
        """The floor of the run: the busiest host's pages at the rate one host is asked at (hosts run in parallel)."""

        return self.busiest[1] / lf.RATE_PER_HOST_PER_MINUTE

    def lines(self) -> list[str]:
        host, pages = self.busiest
        lines = [
            f"Learning link check: {len(self.courses)} course(s) ({', '.join(self.courses)}), "
            f"{self.links} distinct links on {self.page_count} pages of {len(self.pages)} hosts.",
            f"It sends one GET per page and reads each host's robots.txt first: at most {lf.RATE_PER_HOST_PER_MINUTE} requests a minute "
            f"per host, one at a time per host, hosts in parallel, at most {self.budget} requests in all. No model call.",
            f"Estimated time: at least {self.minutes:.1f} min (busiest host {host or '-'}: {pages} pages); "
            f"a page that answers 429 is asked again after {RETRY_WAITS[0]:.0f} s and {RETRY_WAITS[1]:.0f} s.",
        ]
        if self.page_count + len(self.pages) > self.budget:
            lines.append(f"The budget of {self.budget} requests is smaller than the pages and robots files to ask for: the rest is reported not_checked.")
        lines.extend(f"  {host}: {len(urls)} pages" for host, urls in sorted(self.pages.items(), key=lambda item: (-len(item[1]), item[0])))
        return lines


def make_plan(links: Sequence[Link], *, budget: int | None = None) -> Plan:
    pages: dict[str, list[str]] = {}
    for link in links:
        found = pages.setdefault(_host(link.page), [])
        if link.page not in found:
            found.append(link.page)
    courses = tuple(dict.fromkeys(link.course for link in links))
    return Plan(
        courses=courses, links=len({link.url for link in links}), pages={host: tuple(urls) for host, urls in pages.items()},
        budget=budget if budget is not None else lf.FETCH_BUDGET * max(1, len(courses)),
    )


# ---------------------------------------------------------------------------
# The fetch: hosts in parallel, one request per host at a time, a 429 again later
# ---------------------------------------------------------------------------


def _not_checked(url: str) -> lf.Fetched:
    return lf.Fetched(requested_url=url, url=url, error=NOT_CHECKED)


def _ask_host(fetcher: lf.PageFetcher, urls: Sequence[str]) -> dict[str, lf.Fetched]:
    """The pages of ONE host, one after the other. A spent budget leaves the rest ``not_checked``."""

    found: dict[str, lf.Fetched] = {}
    for url in urls:
        try:
            found[url] = fetcher.fetch_page(url)
        except lf.FetchStopped:
            found[url] = _not_checked(url)
    return found


def fetch_pages(
    fetcher: lf.PageFetcher, pages: Mapping[str, Sequence[str]], *, workers: int = HOST_WORKERS,
    sleep: Callable[[float], object] = time.sleep, say: Callable[[str], object] = lambda _text: None,
) -> dict[str, lf.Fetched]:
    """``{page URL: what it answered}``. Each host's pages are one job; ``workers`` jobs run at a time."""

    fetched: dict[str, lf.Fetched] = {}

    def ask(groups: Mapping[str, Sequence[str]]) -> None:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(groups) or 1))) as pool:
            for found in pool.map(lambda urls: _ask_host(fetcher, urls), list(groups.values())):
                fetched.update(found)

    ask(pages)
    for wait in RETRY_WAITS:
        again = {host: [url for url in urls if fetched[url].error in RETRY_ERRORS] for host, urls in pages.items()}
        again = {host: urls for host, urls in again.items() if urls}
        if not again or fetcher.budget.left == 0:
            break
        say(f"{sum(len(urls) for urls in again.values())} page(s) answered 429 or not at all: asking again in {wait:.0f} s")
        sleep(wait)
        ask(again)
    return fetched


# ---------------------------------------------------------------------------
# The verdict on each link
# ---------------------------------------------------------------------------


class _Fetched:
    """What :class:`learning_paths.Verifier` is handed as its fetcher: the answers of this run, never the network."""

    def __init__(self, fetched: Mapping[str, lf.Fetched]) -> None:
        self._fetched = fetched

    def fetch_page(self, url: str) -> lf.Fetched:
        return self._fetched.get(url) or _not_checked(url)


def _page_status(fetched: lf.Fetched) -> tuple[str, str]:
    """``(class, reason)`` of a page by its answer alone (no anchor looked at)."""

    error = fetched.error
    if error is None:
        return (REDIRECTED if fetched.redirects else OK), ""
    if error == NOT_CHECKED:
        return NOT_CHECKED, "the fetch budget ended first"
    if error == _HTTP_429:
        return RATE_LIMITED, error
    if error in (lf.ROBOTS_DISALLOWED, lf.ROBOTS_UNKNOWN):
        return ROBOTS_REFUSED, error
    return DEAD, error


def judge(link: Link, fetched: lf.Fetched, verifier: lp.Verifier) -> dict[str, Any]:
    """The report row of one stored link: ``status``, ``verified`` (the generator's rule would keep it), ``reason``, ``now``."""

    status, reason = _page_status(fetched)
    row: dict[str, Any] = {
        "course": link.course, "lesson": link.lesson, "part": link.part, "n": link.n, "url": link.url, "host": _host(link.page),
        "status": status, "verified": False, "reason": reason, "now": None, "http": fetched.status,
    }
    if status not in (OK, REDIRECTED):
        return row
    stored = link.url.strip()
    if link.part in _ITEM_MINIMUM:
        # A page-level citation was stored with the page's title as its section phrase (learning_paths.check_answer).
        citation = lp.Citation(0, link.page, link.anchor or None, link.section, link.title if link.anchor else link.section)
        now, why = verifier.check(citation)
        if now is None:
            left = bool(fetched.redirects) and (usable_url(fetched.url) is None or _host(fetched.url) != _host(link.page))
            row.update(status=REDIRECTED if left else ANCHOR_GONE, reason=why or "", now=fetched.url if left else None)
            return row
        row["verified"] = True
        if now != stored:
            row["now"] = now
            if now.split("#", 1)[1:] != stored.split("#", 1)[1:]:
                row.update(status=ANCHOR_GONE, reason=f"the section '{link.section}' is under another anchor today")
        return row
    if status == REDIRECTED:
        row["now"] = fetched.url
    # A lesson source that now lands on a site's home page is the page gone (learning_course.gather_sources: home_page).
    row["verified"] = link.part == TOOL_LINK or usable_url(fetched.url) is not None
    if not row["verified"]:
        row["reason"] = "home_page"
    return row


def _worst(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return max(rows, key=lambda row: STATUSES.index(row["status"]))


def _counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Distinct stored URLs by class: a URL stored in several places counts once, as its worst result."""

    by_url: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_url.setdefault(row["url"], []).append(row)
    tally = Counter(_worst(found)["status"] for found in by_url.values())
    return {"links": len(by_url), "pages": len({row["url"].split("#", 1)[0] for row in rows}), "by_status": {status: tally.get(status, 0) for status in STATUSES}}


def _listed(rows: Sequence[Mapping[str, Any]], status: str) -> list[dict[str, Any]]:
    """The distinct URLs of one class, with every place that stores each."""

    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["status"] != status:
            continue
        entry = found.setdefault(row["url"], {"url": row["url"], "reason": row["reason"], "now": row["now"], "verified": row["verified"], "used_by": []})
        where = "/".join(part for part in (row["course"], row["lesson"]) if part)
        entry["used_by"].append(f"{where} {row['part']}" + (f" {row['n']}" if row["n"] is not None else ""))
    return list(found.values())


def path_shortfalls(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(below the minimum, unresolved)``: the practice paths that lose too many verified items.

    A path is BELOW when its stored items minus the lost ones (dead, anchor gone, moved away) are fewer than
    ``learning_paths.STEPS_MIN`` steps or ``SETUP_MIN`` setup items; UNRESOLVED when that only happens if the
    items that could not be judged in this run are lost too.
    """

    paths: dict[tuple[str, str], dict[str, dict[str, int]]] = {}
    for row in rows:
        if row["part"] not in _ITEM_MINIMUM:
            continue
        tally = paths.setdefault((row["course"], row["lesson"]), {part: {"stored": 0, "verified": 0, "lost": 0, "unjudged": 0} for part in _ITEM_MINIMUM})[row["part"]]
        tally["stored"] += 1
        tally["verified" if row["verified"] else "unjudged" if row["status"] in UNJUDGED else "lost"] += 1
    below: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for (course, lesson), parts in paths.items():
        entry = {"course": course, "lesson": lesson, **parts, "minimum": dict(_ITEM_MINIMUM)}
        if any(parts[part]["stored"] - parts[part]["lost"] < minimum for part, minimum in _ITEM_MINIMUM.items()):
            below.append(entry)
        elif any(parts[part]["verified"] < minimum for part, minimum in _ITEM_MINIMUM.items()):
            unresolved.append(entry)
    return below, unresolved


# ---------------------------------------------------------------------------
# The run and its report
# ---------------------------------------------------------------------------


def check_links(
    links: Sequence[Link], network: lf.Fetcher, *, plan: Plan | None = None, workers: int = HOST_WORKERS, robots: Any = None,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], object] = time.sleep,
    say: Callable[[str], object] = lambda _text: None, today: str | None = None,
) -> dict[str, Any]:
    """Fetch every page of ``links`` once through the outbound policy, judge every link, and return the report.

    ``network`` is the one thing that talks to the outside (``learning_fetch.HttpxFetcher``; a test passes a fake
    web). ``clock`` and ``sleep`` are the rate limit's and the retry wait's.
    """

    plan = plan or make_plan(links)
    fetcher = lf.PageFetcher(network, robots=robots, budget=lf.FetchBudget(plan.budget), clock=clock, sleep=sleep)
    started = clock()
    fetched = fetch_pages(fetcher, plan.pages, workers=workers, sleep=sleep, say=say)
    answers = _Fetched(fetched)
    verifiers: dict[str, lp.Verifier] = {}
    rows = [judge(link, answers.fetch_page(link.page), verifiers.setdefault(_host(link.page), lp.Verifier(answers, _host(link.page)))) for link in links]
    below, unresolved = path_shortfalls(rows)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_REPORT,
        "checked": today or datetime.date.today().isoformat(),
        "totals": {**_counts(rows), "hosts": len(plan.pages), "requests": fetcher.requests, "robots_requests": fetcher.robots_requests, "seconds": round(clock() - started, 1)},
        "courses": {course: _counts([row for row in rows if row["course"] == course]) for course in plan.courses},
        "hosts": {host: _counts([row for row in rows if row["host"] == host]) for host in sorted(plan.pages)},
        "minimum": dict(_ITEM_MINIMUM),
        "paths_below_minimum": below,
        "paths_unresolved": unresolved,
    }
    for status in STATUSES[1:]:
        report[status] = _listed(rows, status)
    report["links"] = rows
    return report


def _tally_text(counts: Mapping[str, Any]) -> str:
    return ", ".join(f"{status} {counts['by_status'][status]}" for status in STATUSES if counts["by_status"][status] or status == OK)


def report_text(report: Mapping[str, Any]) -> str:
    """The short text of a report: the counts per course and per host, the dead links, the paths below the minimum."""

    totals = report["totals"]
    lines = [
        f"Learning link check, {report['checked']}: {totals['links']} links on {totals['pages']} pages of {totals['hosts']} hosts, "
        f"{totals['requests']} requests ({totals['robots_requests']} robots files), {totals['seconds'] / 60:.1f} min.",
        f"  {_tally_text(totals)}",
        "Per course:",
        *(f"  {course}: {counts['links']} links, {_tally_text(counts)}" for course, counts in report["courses"].items()),
        "Per host:",
        *(f"  {host or '(no host)'}: {counts['links']} links, {_tally_text(counts)}" for host, counts in report["hosts"].items()),
    ]
    for status, heading in ((DEAD, "Dead links"), (ANCHOR_GONE, "Anchors gone"), (REDIRECTED, "Redirected"), (RATE_LIMITED, "Still rate-limited"), (ROBOTS_REFUSED, "Refused by robots.txt"), (NOT_CHECKED, "Not checked")):
        found = report[status]
        lines.append(f"{heading}: {len(found)}")
        for entry in found:
            detail = "; ".join(part for part in (entry["reason"], f"now {entry['now']}" if entry["now"] else "") if part)
            lines.append(f"  {entry['url']}" + (f" ({detail})" if detail else "") + f" [{'; '.join(entry['used_by'])}]")
    minimum = report["minimum"]
    for name, heading in (
        ("paths_below_minimum", f"Paths below the minimum ({minimum[PATH_STEP]} steps, {minimum[PATH_SETUP]} setup items) once their lost links are dropped"),
        ("paths_unresolved", "Paths below the minimum only if the links that could not be judged are lost too"),
    ):
        lines.append(f"{heading}: {len(report[name])}")
        lines.extend(
            f"  {entry['course']}/{entry['lesson']}: steps {entry[PATH_STEP]['verified']} of {entry[PATH_STEP]['stored']} verified "
            f"({entry[PATH_STEP]['lost']} lost), setup {entry[PATH_SETUP]['verified']} of {entry[PATH_SETUP]['stored']} ({entry[PATH_SETUP]['lost']} lost)"
            for entry in report[name]
        )
    return "\n".join(lines) + "\n"


def report_dir(out_dir: Path, course_dirs: Sequence[Path] = ()) -> Path:
    """Where the report goes. Refused inside a course folder or the package: this step only reports."""

    out_dir = Path(out_dir).resolve()
    package = Path(lf.__file__).resolve().parents[1]
    for kept in (package, *(Path(folder).resolve() for folder in course_dirs)):
        if out_dir == kept or kept in out_dir.parents:
            raise LinkCheckError(f"{out_dir}: the report is not written inside {kept} (nothing is written into the package)")
    return out_dir


def write_report(report: Mapping[str, Any], out_dir: Path, course_dirs: Sequence[Path] = ()) -> tuple[Path, Path]:
    """``<out_dir>/report.json`` (everything) and ``<out_dir>/report.txt`` (:func:`report_text`)."""

    out_dir = report_dir(out_dir, course_dirs)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_file, text_file = out_dir / "report.json", out_dir / "report.txt"
    json_file.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    text_file.write_text(report_text(report), encoding="utf-8")
    return json_file, text_file


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Re-verify every external link of the shipped learning courses (a release step; report only).")
    parser.add_argument("courses", nargs="+", type=Path, help="a shipped course folder (course.json plus paths/), or the folder that holds several")
    parser.add_argument("--out", type=Path, default=Path("build/learning-link-check"), help="where report.json and report.txt are written")
    return parser


def main(argv: Sequence[str] | None = None, *, network: lf.Fetcher | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = _arguments().parse_args(argv)
    environ = os.environ if environ is None else environ
    try:
        course_dirs = find_courses(args.courses)
        links = [link for folder in course_dirs for link in course_links(folder)]
        report_dir(args.out, course_dirs)  # refused before a request is sent, not after
    except LinkCheckError as exc:
        print(f"learning link check: {exc}", file=sys.stderr)
        return 2
    plan = make_plan(links)
    print("\n".join(plan.lines()), flush=True)  # the estimate comes first, before any request
    if environ.get(CONSENT_ENV) != "1":
        print(f"refusing the learning link check: nothing was fetched; set {CONSENT_ENV}=1 explicitly to send these requests", file=sys.stderr)
        return 2
    owned = network is None
    network = network or lf.HttpxFetcher()
    try:
        report = check_links(links, network, plan=plan, say=lambda text: print(text, flush=True))
    finally:
        if owned:
            network.close()  # type: ignore[attr-defined]
    json_file, text_file = write_report(report, args.out, course_dirs)
    print(report_text(report), end="")
    print(f"report: {json_file} and {text_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
