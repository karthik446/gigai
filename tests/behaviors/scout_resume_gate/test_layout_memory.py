"""0.1.10.11 TY: a layout query no longer leaves memory behind for good in the process that made it.

The layout engine (typst 0.15.0) keeps what a layout computed in a cache of the whole process; ``typst.compile``
ages it out on every call and ``typst.query`` never does, so every page measurement (``resume_pdf._end``: the master
selector's fit, a tailoring's length rule, a PDF's auto fit) left 1 to 3 MB behind: one pick from a master resume
(12 to 18 layouts) grew the Scout server by 12 to 30 MB, for good.  ``_end`` now ages the cache out after every
``QUERIES_PER_EVICTION``-th query (``_evict_layout_cache``: an EMPTY document's compile).

Asserted here: THE MEMORY ITSELF (200 layouts in a child process grow it by less than ``GROWTH_BOUND_MB``; without
the eviction they grow it by about 280 MB); the queries are followed by the eviction at that rate, also when a query
fails; the evicting document holds nothing of the resume; and the ANSWERS are the ones the query gives without it,
on every markdown the pick eval's selector measures and on the edge inputs, from one thread and from two threads at
once for 10 seconds (the engine runs outside the interpreter's lock, so an eviction does overlap another thread's
query).  Synthetic fixtures only (``tests/evals/fixtures/pick``).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import typst

from gigai.scout import master_selection as ms
from gigai.scout import resume_pdf
from gigai.scout.master_resume import parse_master
from gigai.scout.resume_pdf import MAX_MARKDOWN_LINES, QUERIES_PER_EVICTION, ResumeMarkdownError, measure_markdown
from tests.evals import run_pick_eval as ev

SMALL = "## Summary\n\nA platform engineer.\n\n## Experience\n\n### Example Co\nEngineer | Jun 2020 - Present\n- Built a delivery workflow.\n"
_LONG_ROLE = "## Experience\n\n### Example Co\n" + "".join(
    f"- Line {number} of a long role, with enough words to wrap onto a second line of the page.\n" for number in range(120)
)
#: Edge inputs: a section with nothing under it beside one with a line; one bullet; a long section (3 pages or more).
EDGES = ("## Summary\n\n## Skills\n\n- Python, Go\n", "## Other\n\n- x\n", _LONG_ROLE)
TOO_LARGE = "## Other\n\n" + "- x\n" * (MAX_MARKDOWN_LINES + 1)
EVICTION = "compile_with_warnings"

#: 200 layouts of a 2-page resume, each with one line changed, after 20 that settle the engine.  Measured (macOS
#: arm64, Python 3.11 and 3.13, typst 0.15.0): 6 MB with the eviction, 278 MB without it.
LAYOUTS = 200
GROWTH_BOUND_MB = 60
_CHILD = r"""
import json, os, subprocess, sys
from gigai.scout.resume_pdf import measure_markdown

def rss_kib():
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            return int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") // 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        return int(subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=30).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

role = "## Experience\n\n### Example Co\n" + "".join(
    f"- Line {number} of a long role, with enough words to wrap onto a second line of the page.\n" for number in range(40)
)
for number in range(20):
    measure_markdown(role + f"- Settling line {number}\n")
start = rss_kib()
pages = {measure_markdown(role + f"- Varying line {number}\n")[0] for number in range(int(sys.argv[1]))}
print(json.dumps({"start_kib": start, "end_kib": rss_kib(), "pages": sorted(pages)}))
"""


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[object, ...], dict[str, object]]]:
    """Every call of the layout engine, in order: ``(name, args, kwargs)``; the process's query count starts over."""

    seen: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
    for name in ("query", "compile", EVICTION):
        real = getattr(typst, name)
        monkeypatch.setattr(typst, name, lambda *a, _real=real, _name=name, **k: (seen.append((_name, a, k)), _real(*a, **k))[1])
    monkeypatch.setattr(resume_pdf, "_queries", itertools.count(1))
    return seen


def _eval_markdowns() -> list[str]:
    """Every markdown the shipped selector lays out for the pick eval's postings on its three master sizes."""

    today = ev.date_of(ev._load("sizes.json")["today"])
    seen: dict[str, None] = {}

    def recording(markdown: str) -> tuple[int, float]:
        seen[markdown] = None
        return ms._shipped_measure(markdown)

    for size in ev.SIZES:
        master = parse_master(ev.master_case(size, "base").markdown)
        raw = ev.profile(size)
        profile = ms.SelectionProfile(titles=tuple(raw["titles"]), base_ids=tuple(raw["base_ids"]), profile_id=raw["profile_id"], label=raw["label"])  # type: ignore[arg-type]
        for post in ev.postings().values():
            ms.select(master, profile, ms.SelectionPosting(post["title"], post["text"]), today=today, measure=recording)
    return list(seen)


def test_two_hundred_layouts_in_one_process_grow_its_memory_by_less_than_the_bound() -> None:
    """THE END OUTCOME, in a child process of its own (this process's memory is other tests' too): resident memory
    after 200 layouts, less the memory before them.  Without the eviction this is about 280 MB (1.4 MB a layout)."""

    source = str(Path(resume_pdf.__file__).resolve().parents[2])  # the ``gigai`` this process imports
    environ = {**os.environ, "PYTHONPATH": os.pathsep.join(part for part in (source, os.environ.get("PYTHONPATH", "")) if part)}
    done = subprocess.run([sys.executable, "-c", _CHILD, str(LAYOUTS)], capture_output=True, text=True, timeout=600, check=False, env=environ)
    assert done.returncode == 0, done.stderr[-2000:]
    answer = json.loads(done.stdout.strip().splitlines()[-1])
    assert answer["pages"] == [2]
    if answer["start_kib"] is None or answer["end_kib"] is None:
        pytest.skip("this system gives no resident memory size (no /proc/self/statm, no ps): the layouts ran, the bound is not checked")
    growth_mb = (answer["end_kib"] - answer["start_kib"]) / 1024
    assert growth_mb < GROWTH_BOUND_MB, f"{LAYOUTS} layouts grew the process by {growth_mb:.0f} MB (the bound is {GROWTH_BOUND_MB} MB)"


def test_the_layout_queries_are_followed_by_an_eviction_that_holds_nothing_of_the_resume(calls: list) -> None:
    assert 1 <= QUERIES_PER_EVICTION <= 3, "what stays in memory grows with this number: measure before raising it"
    first = measure_markdown(SMALL, spacing_scale=ms.FIT_SCALE)
    for _ in range(2 * QUERIES_PER_EVICTION - 1):
        assert measure_markdown(SMALL, spacing_scale=ms.FIT_SCALE) == first

    assert [name for name, _args, _kwargs in calls] == (["query"] * QUERIES_PER_EVICTION + [EVICTION]) * 2
    for name, args, kwargs in calls:
        if name == EVICTION:
            # An empty document, no inputs, no file: the bytes come back and are dropped.
            assert args == (b"",) and kwargs == {"format": "pdf", "ignore_system_fonts": True}


def test_the_selector_s_seam_a_tailoring_s_page_count_and_a_pdf_s_auto_fit_are_all_counted(calls: list) -> None:
    """``_end`` is the ONE place a layout is queried: the selector's seam, ``pages_at`` / ``fewest_pages`` and auto fit."""

    for _ in range(QUERIES_PER_EVICTION):
        assert ms._shipped_measure(SMALL)[0] == 1
    assert [name for name, *_ in calls] == ["query"] * QUERIES_PER_EVICTION + [EVICTION]
    calls.clear()
    rendered = resume_pdf.render_markdown_pdf(SMALL, None, timestamp=datetime(2026, 10, 3, tzinfo=timezone.utc))
    names = [name for name, *_ in calls]
    queries = names.count("query")
    # Auto fit's queries are counted with the selector's; the PDF itself is still ONE compile, the last call.
    assert rendered.pages == 1 and queries >= 2
    assert names.count(EVICTION) == queries // QUERIES_PER_EVICTION and names.count("compile") == 1 and names[-1] == "compile"


def test_a_query_that_fails_is_counted_and_followed_by_the_eviction_all_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    evicted: list[bool] = []

    def failing(*_args: object, **_kwargs: object) -> str:
        raise typst.TypstError("layout failed")

    monkeypatch.setattr(typst, "query", failing)
    monkeypatch.setattr(resume_pdf, "_evict_layout_cache", lambda: evicted.append(True))
    monkeypatch.setattr(resume_pdf, "_queries", itertools.count(1))
    for _ in range(QUERIES_PER_EVICTION):
        with pytest.raises(typst.TypstError):
            measure_markdown(SMALL)
    assert evicted == [True]


def test_an_eviction_that_fails_leaves_the_measurement_standing(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = measure_markdown(SMALL)

    def failing(*_args: object, **_kwargs: object) -> object:
        raise typst.TypstError("no fonts")

    monkeypatch.setattr(typst, EVICTION, failing)
    assert [measure_markdown(SMALL) for _ in range(QUERIES_PER_EVICTION)] == [expected] * QUERIES_PER_EVICTION


def test_the_answers_are_the_ones_without_the_eviction_on_the_eval_s_markdowns_and_the_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    markdowns = [*_eval_markdowns(), *EDGES]
    assert len(markdowns) > 100
    shipped = [ms._shipped_measure(markdown) for markdown in markdowns]
    assert ms._shipped_measure(TOO_LARGE) == (ms.UNMEASURED_PAGES, 1.0)
    with pytest.raises(ResumeMarkdownError) as refused:
        ms._shipped_measure("no sections here\n")
    assert refused.value.code == "resume_markdown_invalid"
    assert {pages for pages, _fill in shipped} >= {1, 2, 3}

    # The same queries with no eviction at all (the code before 0.1.10.11), then with one after EVERY query.
    monkeypatch.setattr(resume_pdf, "_evict_layout_cache", lambda: None)
    assert [ms._shipped_measure(markdown) for markdown in markdowns] == shipped
    assert ms._shipped_measure(TOO_LARGE) == (ms.UNMEASURED_PAGES, 1.0)
    monkeypatch.undo()
    monkeypatch.setattr(resume_pdf, "QUERIES_PER_EVICTION", 1)
    assert [ms._shipped_measure(markdown) for markdown in markdowns] == shipped


def test_two_threads_measuring_for_ten_seconds_with_an_eviction_after_every_query_get_the_answers_of_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server's pipeline workers measure from several threads, and the engine runs outside the interpreter's lock:
    one thread's eviction overlaps the other's query.  Hardest case: an eviction after EVERY query, 10 seconds."""

    markdowns = [_LONG_ROLE[: 40 + 90 * number].rsplit("\n", 1)[0] + "\n" for number in range(1, 40)] + list(EDGES)
    monkeypatch.setattr(resume_pdf, "_evict_layout_cache", lambda: None)
    alone = {markdown: ms._shipped_measure(markdown) for markdown in markdowns}  # one thread, no eviction
    monkeypatch.undo()
    assert len(set(alone.values())) > 20

    evictions: list[int] = []
    real = typst.compile_with_warnings
    monkeypatch.setattr(typst, EVICTION, lambda *a, **k: (evictions.append(1), real(*a, **k))[1])
    monkeypatch.setattr(resume_pdf, "QUERIES_PER_EVICTION", 1)
    deadline = time.monotonic() + 10.0

    def hammer(order: list[str]) -> tuple[int, list[str]]:
        done, wrong = 0, []
        while time.monotonic() < deadline:
            for markdown in order:
                if ms._shipped_measure(markdown) != alone[markdown]:
                    wrong.append(f"markdown #{markdowns.index(markdown)}")
                done += 1
        return done, wrong

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [job.result(timeout=120) for job in (pool.submit(hammer, markdowns), pool.submit(hammer, markdowns[::-1]))]
    assert [wrong for _done, wrong in results] == [[], []]
    assert all(done >= 200 for done, _wrong in results), results
    assert len(evictions) == sum(done for done, _wrong in results)
