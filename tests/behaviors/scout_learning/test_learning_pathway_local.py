"""0.1.11.10 shipped lessons, slice 4a: the one locally built page of a shipped course (``learning_pathway_local``).

1. THE COUNTS. The shipped catalog's title phrases, deny words and vocabulary are counted over the user's own
   stored postings by the product's corpus code: numbers small enough to check by hand, an item no posting names
   with 0, the lesson counts by concept key, examples with their company, the plain line of a thin corpus. No
   model is reachable (a model seam that raises, every socket refused, and the module's own text).
2. THE PAGE (learning center M2: built from the CATALOG alone). Without markers it says 'Your resume is not compared yet'
   and holds nothing of a resume; with synthetic markers it draws the personal course's 'You have / Missing' and
   'Production evidence' lines with the master line id. Percentages, bars and 'N postings' chips are the personal
   templates'. Every lesson, subtopic and practice link is an address on the learning center
   (``https://scout.edgelab.one/learn/lessons/<id>/#<subtopic>``), opens in a new tab with ``rel="noopener"``, and is the
   ONLY kind of link that leaves the page; the audit refuses any other address and any other local page. No local
   lesson page exists. The footer prints the catalog's site version. A ``:1`` catalog links nothing and says so.
   A failed build writes nothing. The same inputs give the same bytes.
3. THE RECORD holds dates, digests and numbers: nothing of a posting, nothing of a marker.
4. THE STATE: ``not_built``, ``built``, ``stale`` and what makes a page stale.

Everything is SYNTHETIC: the course is the widget course of ``test_learning_render_shipped.py`` (as a catalog); the postings,
companies, resume lines and ids are made up. No network, no real home, no model.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from gigai.scout import learning_corpus, learning_pathway_local as local, learning_render, learning_shipped, learning_shipped_store as store, learning_store
from gigai.scout.find_jobs import search_index
from gigai.scout.find_jobs.search_index import IndexFilters
from gigai.scout.learning_render import CourseError
from gigai.scout.learning_store import LearningError

from tests.behaviors.scout_learning.test_learning_corpus import NOW, lever_job, seed_board
from tests.behaviors.scout_learning.test_learning_render_shipped import shipped_course
from tests.behaviors.scout_learning.test_learning_store import home_target  # noqa: F401 - the fixture

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")

KEY = "synthetic-widget-engineer"
ROLE = "Synthetic widget engineer"
ASSETS = "/learning/_assets"
BASE = "https://scout.edgelab.one/learn"
SITE = {"base_url": BASE, "site_version": "2026.10.1", "lessons_path": "lessons", "practice_path": "practice", "pathways_path": "pathways"}
EMOJI = "\U0001f600"

#: What a page holds only when a resume was compared (the personal templates' classes and words).
RESUME_STRINGS = (
    "You have:", "Missing:", "Production evidence", 'class="source-id"', "evidence-line", "missing-line", "concept-marker-line",
    "Skim (you have production evidence)", "What your resume shows", 'id="resume-evidence"',
)

WIDGETCO = [
    lever_job("widgetco", 1, "Senior Widget Engineer", 5, "You will ship widgets with Widgetflow. Experience with widget pipelines is required. You know gamma widgets."),
    lever_job("widgetco", 2, "Widget Engineer", 10, "You will review widgets every week. Must have Gearbox and Widgetflow. You will run gamma widgets."),
    lever_job("widgetco", 3, "Widget Engineer", 120, "You will ship widgets to customers. Experience with alpha widgets is a plus."),
    lever_job("widgetco", 4, "Widget Engineer, Intern", 3, "An internship. You will ship widgets with Widgetflow."),  # a deny word
    lever_job("widgetco", 5, "Data Engineer", 3, "Data pipelines with Widgetflow."),  # another title
    lever_job("widgetco", 6, "Widget Engineer", 2, ""),  # no stored text
]


def concept(concept_id: str, *phrases: str, category: str = "tool", display: str | None = None) -> dict:
    return {"id": concept_id, "display": display or concept_id.replace("-", " ").capitalize(), "category": category, "phrases": list(phrases), "technical": category == "tool"}


def make_catalog(course: dict | None = None, *, practice: tuple[str, ...] = ("lesson-alpha", "lesson-gamma"), schema: int = 2, site: dict | None = None) -> dict:
    """The shipped catalog of the synthetic widget course, in the ``:2`` format (``:1``: no site, no versions).

    ``lesson-beta`` is a merged lesson: its id on the site (``library_id``) is another one.
    """

    data = course if course is not None else shipped_course()
    catalog = {
        "schema_version": f"scout-learning-shipped-catalog:{schema}", "role_key": KEY, "role": ROLE,
        "titles": {"phrases": ["widget engineer"], "deny": ["intern"]},
        "vocabulary": [
            concept("widgetflow", "widgetflow", display="Widgetflow"),
            concept("gearbox", "gearbox", display="Gearbox"),
            concept("ship-widgets", "ship widgets", category="responsibility", display="Ship widgets"),
            concept("review-widgets", "review widgets", category="responsibility", display="Review widgets"),
            concept("owns-an-area", "own a widget area", category="seniority", display="Owns a widget area"),
            concept("key-shared", "widget pipelines", category="skill"),
            concept("key-lesson-alpha", "alpha widgets", category="skill"),
            concept("key-lesson-beta", "beta widgets", category="skill"),
            concept("key-lesson-gamma", "gamma widgets", category="skill"),
            concept("key-lesson-delta", "delta widgets", category="skill"),
        ],
        "expectations": {"responsibilities": ["ship-widgets", "review-widgets"], "scope_at_staff": ["owns-an-area"]},
        "technologies": {"Widget tooling": ["widgetflow", "gearbox"]},
        "modules": [{"id": module["id"], "title": module["title"]} for module in data["modules"]],
        "lessons": [
            {"id": lesson["id"], "title": lesson["title"], "module": module["id"], "concept_keys": lesson["concept_keys"], "technologies": lesson["technologies"],
             "has_practice": lesson["id"] in practice, "subtopics": [{"id": sub["id"], "title": sub["title"]} for sub in lesson["subtopics"]]}
            for module in data["modules"] for lesson in module["concepts"]
        ],
    }
    if schema == 2:
        catalog["lessons"] = [
            {**lesson, "prerequisites": [], "depth": None, **({"library_id": "lib-beta"} if lesson["id"] == "lesson-beta" else {})} for lesson in catalog["lessons"]
        ]
        catalog["site"] = dict(site or SITE)
        catalog["pathway_version"], catalog["concept_keys_version"] = 1, 1
    return catalog


def item(concept_id: str, display: str, percent: float, in_90d: int, any_date: int, examples: list[dict] | None = None) -> dict:
    return {"id": concept_id, "display": display, "percent": percent, "in_90d": in_90d, "any_date": any_date, "examples": examples or []}


def make_counts() -> dict:
    """Counts written by hand, in ``count_postings``'s shape (the page tests need no home)."""

    note = "Read: 40 postings of the last 90 days with stored text (of 52 stored for this role's titles; US or unclear location, any work mode)"
    return {
        "schema_version": local.COUNTS_SCHEMA, "role_key": KEY,
        "corpus": {
            "postings": 52, "postings_in_window": 44, "with_text": 47, "with_text_in_window": 40, "window_days": 90, "basis": "last_90_days", "read": 40,
            "filters": {"countries": ["US"], "us_only": True, "work_mode": "any", "area": None}, "note": note,
        },
        "expectations": {
            "responsibilities": [
                item("ship-widgets", "Ship widgets", 62.5, 25, 30, [{"text": "You will ship widgets every week.", "company": "Example Gears Co", "title": "Widget Engineer"}]),
                item("review-widgets", "Review widgets", 0.0, 0, 0),
            ],
            "scope_at_staff": [item("owns-an-area", "Owns a widget area", 12.5, 5, 6)],
        },
        "technologies": {"Widget tooling": [item("widgetflow", "Widgetflow", 80.0, 32, 37), item("gearbox", "Gearbox", 7.5, 3, 3)]},
        "lessons": {
            "lesson-alpha": {"postings_in_90d": 32, "postings_any_date": 37}, "lesson-beta": {"postings_in_90d": 3, "postings_any_date": 3},
            "lesson-gamma": {"postings_in_90d": 0, "postings_any_date": 2}, "lesson-delta": {"postings_in_90d": 0, "postings_any_date": 0},
        },
        "concepts": 10, "concepts_named": 6,
    }


def make_markers() -> dict:
    """Synthetic markers: alpha known whole, beta in part (its lesson label is derived), gamma and delta new."""

    return {
        "lesson-alpha": {"marker": "KNOWN", "evidence": {"id": "e-0a1b2c", "line": "Ran the widget line for three plants"}, "missing": None},
        "lesson-alpha/lesson-alpha-first": {"marker": "KNOWN", "evidence": {"id": "e-0a1b2c", "line": "Ran the widget line for three plants"}, "missing": None},
        "lesson-alpha/lesson-alpha-second": {"marker": "KNOWN", "evidence": {"id": "e-0d4e5f", "line": "Cut widget scrap by a fifth"}, "missing": None},
        "lesson-beta/lesson-beta-first": {"marker": "SOME", "evidence": {"id": "s-112233", "line": "Gearbox"}, "missing": "The line names the tool, not its use in production."},
        "lesson-gamma/lesson-gamma-first": {"marker": "NEW", "evidence": None, "missing": None},
    }


def build(tmp_path: Path, markers: dict | None = None, *, counts: dict | None = None, catalog: dict | None = None, name: str = "out") -> local.PathwayPage:
    out = tmp_path / name / "pathway.html"
    return local.build_pathway_page(catalog or make_catalog(), counts or make_counts(), markers, out, ASSETS)


def lesson_link(lesson_id: str, title: str, anchor: str | None = None) -> str:
    """The anchor the page holds for a lesson (or one of its subtopics) on the learning center."""

    return f'<a href="{BASE}/lessons/{lesson_id}/{"#" + anchor if anchor else ""}" target="_blank" rel="noopener">{title}</a>'


@pytest.fixture
def local_home(home_target: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """A bound home with one synthetic board in its cache and a built index, the synthetic course as the package's
    shipped folder, a US setup, every socket refused and every way to a model raising."""

    home, target = home_target
    seed_board(home, "widgetco", WIDGETCO)
    assert search_index.rebuild_from_index(home).available

    from gigai.scout import proposal_execution
    from gigai.scout.find_jobs import free_search

    monkeypatch.setattr(free_search, "default_config", lambda _home, _target: SimpleNamespace(countries=("US",)))
    monkeypatch.setattr(IndexFilters, "from_config", classmethod(lambda cls, _config, now=None: IndexFilters(countries=("US",), work_mode="remote", area="Remote")))

    def no_model(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the local pathway page is built with no model call")

    def no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the local pathway page is built from this computer: no connection may be opened")

    monkeypatch.setattr(proposal_execution, "resolve_model_adapter", no_model)
    monkeypatch.setattr(learning_corpus.MeteredRoleModel, "__init__", no_model)
    monkeypatch.setattr(learning_corpus.MeteredRoleModel, "ask", no_model)
    monkeypatch.setattr(socket.socket, "connect", no_network)

    root = tmp_path / "shipped-package"
    (root / KEY).mkdir(parents=True)
    (root / KEY / "catalog.json").write_text(json.dumps(make_catalog(), sort_keys=True), encoding="utf-8")  # all that ships: no course.json
    monkeypatch.setattr(store, "package_shipped_dir", lambda: root)
    yield home, target
    search_index.close(home)


# --- 1. the counts ---------------------------------------------------------------------------------------------------


def test_the_catalog_is_counted_over_the_users_own_postings_with_no_model_call(local_home: tuple[Path, Path]) -> None:
    home, target = local_home
    counts = local.count_postings(home, target, make_catalog(), now=NOW)

    assert counts["schema_version"] == local.COUNTS_SCHEMA and counts["role_key"] == KEY
    corpus = counts["corpus"]
    # Postings 1, 2, 3 and 6 are the role's; the intern is denied; 6 has no text; 3 is older than 90 days.
    assert (corpus["postings"], corpus["denied"], corpus["with_text"], corpus["with_text_in_window"]) == (4, 1, 3, 2)
    # Fewer than 25 of the window have text: every date is read, and the note says so plainly, as a generated course does.
    assert (corpus["basis"], corpus["read"]) == ("any_date", 3)
    assert corpus["note"].startswith("Fewer than 25 postings of the last 90 days have stored text (2), so every date is read: 3 postings")
    assert corpus["note"].endswith("Only 3 postings match this role on your stored boards; counts are directional")
    assert corpus["filters"] == {"countries": ["US"], "us_only": True, "work_mode": "any", "area": None}, "the role, not the job search"

    by_id = {found["id"]: found for group in (*counts["expectations"].values(), *counts["technologies"].values()) for found in group}
    numbers = {key: (found["percent"], found["in_90d"], found["any_date"]) for key, found in by_id.items()}
    assert numbers == {
        "ship-widgets": (50.0, 1, 2), "review-widgets": (50.0, 1, 1), "owns-an-area": (0.0, 0, 0), "widgetflow": (100.0, 2, 2), "gearbox": (50.0, 1, 1),
    }
    assert [found["id"] for found in counts["expectations"]["responsibilities"]] == ["ship-widgets", "review-widgets"], "the catalog's order"
    assert list(counts["technologies"]) == ["Widget tooling"] and by_id["owns-an-area"]["examples"] == []
    # An example is a sentence of one of the user's own postings with its company and title: this page is local.
    examples = by_id["ship-widgets"]["examples"]
    assert 1 <= len(examples) <= 2 and examples[0]["text"] == "You will ship widgets with Widgetflow."
    assert examples[0]["company"] and examples[0]["title"] == "Senior Widget Engineer" and set(examples[0]) == {"text", "company", "title"}
    assert all(len(found["examples"]) <= 2 for found in by_id.values())
    # A lesson's count is the largest of its concept keys' (alpha: 'widget pipelines' once, 'alpha widgets' in the old posting).
    assert counts["lessons"] == {
        "lesson-alpha": {"postings_in_90d": 1, "postings_any_date": 1}, "lesson-beta": {"postings_in_90d": 1, "postings_any_date": 1},
        "lesson-gamma": {"postings_in_90d": 2, "postings_any_date": 2}, "lesson-delta": {"postings_in_90d": 1, "postings_any_date": 1},
    }
    assert (counts["concepts"], counts["concepts_named"]) == (10, 7)
    assert local.count_postings(home, target, make_catalog(), now=NOW) == counts, "the same postings give the same counts"


def test_a_catalog_that_is_not_one_is_refused(local_home: tuple[Path, Path]) -> None:
    home, target = local_home
    for broken in ({**make_catalog(), "schema_version": "scout-learning-shipped-course:1"}, {**make_catalog(), "role_key": "lp-0a1b2c3d"}, {**make_catalog(), "titles": {"phrases": [], "deny": []}}):
        with pytest.raises(LearningError) as refused:
            local.count_postings(home, target, broken, now=NOW)
        assert refused.value.code == "invalid_value"


def test_the_module_can_reach_no_model_and_reads_no_resume() -> None:
    source = Path(local.__file__).read_text(encoding="utf-8")
    for word in ("resolve_model_adapter", "MeteredRoleModel", "RoleModel", ".ask(", "proposal_execution", "adapters"):
        assert word not in source, f"{word}: the local page is built with no model call"
    for word in ("master_store", "load_master", "read_master_lines", "read_record", "resume_for_profile", "story"):
        assert word not in source, f"{word}: the markers are handed in; this module reads no resume"


# --- 2. the page -----------------------------------------------------------------------------------------------------


def test_without_markers_the_page_says_the_resume_is_not_compared_and_shows_nothing_of_one(tmp_path: Path) -> None:
    built = build(tmp_path)
    page = built.path.read_text(encoding="utf-8")

    assert built.compared is False and built.tally == {} and built.size_bytes == len(page.encode("utf-8"))
    assert "<h1>Your pathway: Synthetic widget engineer</h1>" in page and "<title>Your pathway: Synthetic widget engineer</title>" in page
    assert f'<div class="your-path" id="your-path">\n<h2>Your path</h2>\n<p class="muted">{local.NOT_COMPARED}</p>\n</div>' in page
    assert local.NOT_COMPARED.startswith("Your resume is not compared yet.")
    for word in RESUME_STRINGS:
        assert word not in page, word
    # The plain corpus line, the user's own numbers.
    assert "Based on 40 of your stored postings in the last 90 days (47 any date) &middot; US only; from the job boards stored on this computer; Read: 40 postings" in page
    # Expectations and technologies as a personal course draws them: bars, percentages, the example with its company.
    assert '<span class="tech-bar-label">Ship widgets</span>' in page and '<span class="tech-bar-pct">62.5%</span>' in page
    assert '<span class="tech-bar-fill" style="width:62.5%"></span>' in page and '<span class="tech-bar-pct">0.0%</span>' in page
    assert "&ldquo;You will ship widgets every week.&rdquo;" in page and "(Example Gears Co &middot; Widget Engineer)" in page
    assert '<a id="tech-widgetflow" href="#tech-widgetflow">Widgetflow</a>' in page and '<span class="tech-bar-pct">80.0%</span>' in page
    assert page.index("<h3>Responsibilities</h3>") < page.index("<h3>Scope at staff level</h3>") < page.index('<h2 id="technologies">')
    # The lessons, each a link to its page on the learning center, with its 'N postings' chip when a posting names it.
    assert f'<li>{lesson_link("lesson-alpha", "Alpha")} <span class="count-chip">32 postings</span></li>' in page
    assert f'<li>{lesson_link("lib-beta", "Beta")} <span class="count-chip">3 postings</span></li>' in page, "a merged lesson links to its library id"
    assert f'<li>{lesson_link("lesson-delta", "Delta")} </li>' in page and page.count('class="count-chip"') == 2
    # The look of the course: the one stylesheet and the scripts at the shared base, no style of its own.
    assert f'<link rel="stylesheet" href="{ASSETS}/course.css">' in page and "<style" not in page
    assert f'<script src="{ASSETS}/course.js" defer></script>' in page and '<a href="pathway.html" class="current">Synthetic widget engineer</a>' in page
    assert "Course &middot; From the GigAI learning center &middot; site version 2026.10.1 &middot; built on this computer &middot; no tracking" in page
    assert "shipped with GigAI" not in page and "Shipped with GigAI" not in page and "index.html" not in page and "concept-" not in page
    assert 'The lessons it links to are on the GigAI learning center' in page and local.NOT_LINKED not in page
    assert built.links_checked >= 14


def test_with_markers_the_page_draws_the_personal_courses_lines_with_the_master_line_id(tmp_path: Path) -> None:
    built = build(tmp_path, make_markers())
    page = built.path.read_text(encoding="utf-8")

    assert built.compared is True and built.tally == {"NEW": 5, "SOME": 1, "KNOWN": 2}
    assert local.NOT_COMPARED not in page and '<div class="your-path" id="your-path">' in page
    # 'Your path' is the personal course's: a known lesson folds under 'Skim', the others are listed.
    skim = page[page.index("<summary>Skim (you have production evidence)</summary>"):]
    assert f'<span class="path-number">1.1</span>{lesson_link("lesson-alpha", "Alpha")}' in skim[: skim.index("</details>")]
    assert f'<ul class="path-list"><li><span class="path-number">1.2</span>{lesson_link("lib-beta", "Beta")}</li></ul>' in page
    # 'You have / Missing' per lesson. Beta has no marker of its own: its label and line come from its subtopics.
    assert '<p class="concept-marker-line">You have: Ran the widget line for three plants</p>' in page
    assert '<p class="concept-marker-line">You have: Gearbox &middot; Missing: The line names the tool, not its use in production.</p>' in page
    # 'Production evidence' per subtopic, with the master line id, beside a link to the subtopic's anchor in the lesson ON THE SITE.
    assert f'<li>{lesson_link("lesson-alpha", "Alpha: second part", "lesson-alpha-second")}' in page
    assert '<p class="evidence-line">Production evidence: <span class="source-id">e-0d4e5f</span> &ldquo;Cut widget scrap by a fifth&rdquo;</p>' in page
    assert '<p class="missing-line">Missing: The line names the tool, not its use in production.</p>' in page
    assert '<p class="evidence-line">Production evidence: <span class="source-id">s-112233</span> &ldquo;Gearbox&rdquo;</p>' in page
    # A subtopic with no entry is new: listed with its link, no line. A new lesson is not in the evidence part at all.
    evidence = page[page.index('id="resume-evidence"'):page.index('<h2 id="expectations">')]
    assert f'<li>{lesson_link("lib-beta", "Beta: second part", "lesson-beta-second")}\n</li>' in evidence
    assert "lesson-gamma" not in evidence and "lesson-delta" not in evidence and "<h3>1. Basics</h3>" in evidence and "2. Practice" not in evidence
    # The counts are still there.
    assert '<span class="tech-bar-pct">62.5%</span>' in page and '<span class="count-chip">32 postings</span>' in page


def test_markers_that_show_nothing_say_every_lesson_is_new(tmp_path: Path) -> None:
    page = build(tmp_path, {}).path.read_text(encoding="utf-8")
    assert "No line of your resume shows a lesson of this course yet: every lesson is new." in page
    assert "Production evidence" not in page and "You have:" not in page and local.NOT_COMPARED not in page


def test_the_subtopic_lines_are_the_renderers_own_and_its_first_line_is_the_heading() -> None:
    # The page takes a subtopic's marker lines from ``render_subtopic`` and leaves the heading out: pin what it leaves.
    sub = {"id": "x-first", "title": "X", "marker": "SOME", "evidence": {"id": "e-0a1b2c", "line": "A line"}, "missing": "A gap.", "body": []}
    drawn = learning_render.render_subtopic(sub, {}, set()).split("\n")
    assert drawn[0] == '<h3 id="subtopic-x-first">X</h3>' and len(drawn) == 3
    assert local._subtopic_marker_lines(sub) == drawn[1:]
    assert local._subtopic_marker_lines({"id": "x-first", "title": "X", "marker": "NEW", "evidence": None, "missing": None}) == []


@pytest.mark.parametrize(
    ("markers", "said"),
    [
        ({"lesson-alpha": {"marker": "MAYBE", "evidence": None, "missing": None}}, "markers[lesson-alpha]: marker must be one of KNOWN, SOME, NEW"),
        ({"lesson-alpha/lesson-alpha-first": {"marker": "KNOWN", "evidence": None, "missing": None}}, "marker 'KNOWN' has no evidence id"),
        ({"lesson-alpha/lesson-alpha-first": {"marker": "NEW", "evidence": {"id": "e-0a1b2c", "line": "A line"}, "missing": None}}, "marker 'NEW' must not carry evidence"),
        ({"lesson-alpha": {"marker": "SOME", "evidence": "e-0a1b2c", "missing": None}}, "evidence must be an id and a line"),
        ({"lesson-omega": {"marker": "NEW", "evidence": None, "missing": None}}, "markers[lesson-omega]: the catalog holds no such lesson or subtopic"),
        ({"lesson-beta/lesson-alpha-first": {"marker": "NEW", "evidence": None, "missing": None}}, "the catalog holds no such lesson or subtopic"),
    ],
)
def test_a_marker_that_is_not_one_is_refused_and_nothing_is_written(tmp_path: Path, markers: dict, said: str) -> None:
    with pytest.raises(CourseError) as refused:
        build(tmp_path, markers)
    assert said in str(refused.value) and not (tmp_path / "out" / "pathway.html").exists()


def test_every_link_leaves_for_the_learning_center_and_no_local_lesson_page_exists(tmp_path: Path) -> None:
    import re

    built = build(tmp_path, make_markers())
    page = built.path.read_text(encoding="utf-8")
    hrefs = re.findall(r'<a [^>]*href="([^"]*)"', page)
    external = [href for href in hrefs if href.startswith("https://")]
    assert external and {href.split("/")[2] for href in external} == {"scout.edgelab.one"}, "one host leaves the page: the learning center's"
    assert all(href.startswith(f"{BASE}/lessons/") or href.startswith(f"{BASE}/practice/") for href in external)
    assert f'{BASE}/lessons/lesson-alpha/' in external and f"{BASE}/lessons/lesson-alpha/#lesson-alpha-second" in external
    assert f"{BASE}/lessons/lib-beta/" in external and f"{BASE}/lessons/lesson-beta/" not in page, "a merged lesson's link is its library id"
    # A practice path is linked from the sidebar only for a lesson that has one.
    assert f"{BASE}/practice/lesson-alpha/" in external and f"{BASE}/practice/lesson-gamma/" in external and f"{BASE}/practice/lesson-beta/" not in external
    assert not [href for href in hrefs if not href.startswith(("https://", "#", "pathway.html"))], "no other local page is linked"
    assert all(href.startswith("#") or href == "pathway.html" or href.startswith(f"{BASE}/") for href in hrefs)
    # Each external anchor opens in a new tab and says noopener; nothing is sent along with it.
    anchors = re.findall(r'<a [^>]*href="https://[^"]*"[^>]*>', page)
    assert anchors and all('target="_blank"' in tag and 'rel="noopener"' in tag for tag in anchors)
    assert not [href for href in external if "?" in href or "referrer" in href]
    # Nothing but the page itself is written.
    assert sorted(entry.name for entry in built.path.parent.iterdir()) == ["pathway.html"]


def test_a_v1_catalog_links_nothing_and_the_page_says_so(tmp_path: Path) -> None:
    built = build(tmp_path, make_markers(), catalog=make_catalog(schema=1))
    page = built.path.read_text(encoding="utf-8")
    assert local.NOT_LINKED in page and "https://" not in page and "target=" not in page
    assert "Lessons" in page and "Alpha" in page and "From the GigAI learning center &middot; built on this computer" in page and "site version" not in page
    assert not [tag for tag in __import__("re").findall(r'<a [^>]*href="([^"]*)"', page) if not tag.startswith(("#", "pathway.html"))]
    # A site block that is not valid is a catalog with no site.
    broken = build(tmp_path, catalog=make_catalog(site={**SITE, "base_url": "http://scout.edgelab.one/learn"}), name="broken").path.read_text(encoding="utf-8")
    assert local.NOT_LINKED in broken and "http://" not in broken


def test_the_audit_itself() -> None:
    site = learning_shipped.catalog_site(make_catalog())
    page = (
        '<a href="#here">b</a><a id="here" href="pathway.html#here">c</a>'
        f'<a href="{BASE}/lessons/x/#y" target="_blank" rel="noopener">ok</a><a href="{BASE}/lessons/x/" target="_blank" rel="noopener noreferrer">ok too</a>'
        '<a href="#gone">e</a><a href="index.html#expectations">d</a><a href="concept-lesson-alpha.html">f</a><a href="../other/index.html">g</a>'
        '<a href="https://docs.example.com/x" target="_blank" rel="noopener">h</a><a href="https://scout.edgelab.one.evil.example/learn/x" target="_blank" rel="noopener">i</a>'
        f'<a href="{BASE}/lessons/z/">no target</a><a href="{BASE}/lessons/w/" target="_blank">no rel</a><script>1</script><p onclick="x()">j</p>'
    )
    problems, checked = local.audit_page(page, site)
    assert checked == 12 and problems == sorted([
        "pathway.html: 1 inline <script> block(s) are not allowed",
        "pathway.html: an onclick= attribute is not allowed",
        "pathway.html: anchor '#gone' does not resolve to an id in 'pathway.html'",
        f"pathway.html: external link '{BASE}/lessons/w/' has no rel=\"noopener\"",
        f"pathway.html: external link '{BASE}/lessons/z/' has no rel=\"noopener\"",
        f"pathway.html: external link '{BASE}/lessons/z/' has no target=\"_blank\"",
        "pathway.html: external link 'https://docs.example.com/x' is not an address of the learning center",
        "pathway.html: external link 'https://scout.edgelab.one.evil.example/learn/x' is not an address of the learning center",
        "pathway.html: href target '../other/index.html' is not a page this page may link to",
        "pathway.html: href target 'concept-lesson-alpha.html' is not a page this page may link to",
        "pathway.html: href target 'index.html' is not a page this page may link to",
    ])
    # With no site, every external link is a problem and the page's own anchors still pass.
    assert local.audit_page('<a href="#a" id="a">a</a><a href="https://scout.edgelab.one/learn/x" target="_blank" rel="noopener">b</a>', None)[0] == [
        "pathway.html: external link 'https://scout.edgelab.one/learn/x' is not an address of the learning center"
    ]


def test_the_same_inputs_give_the_same_bytes_and_the_page_holds_no_date(tmp_path: Path) -> None:
    first = build(tmp_path, make_markers(), name="one").path.read_bytes()
    second = build(tmp_path, make_markers(), name="two").path.read_bytes()
    assert first == second
    text = first.decode("utf-8")
    import re

    assert not re.search(r"\b20\d\d-\d\d-\d\d", text), "no date: when it was built is the record's to say"


def test_glyphs_the_course_refuses_never_reach_the_page(tmp_path: Path) -> None:
    # In a marker (a resume line may hold anything): cleaned as a generated course cleans it.
    markers = {"lesson-alpha/lesson-alpha-first": {"marker": "KNOWN", "evidence": {"id": "e-0a1b2c", "line": f"Ran the line {EMOJI}​ for plants"}, "missing": None}}
    page = build(tmp_path, markers).path.read_text(encoding="utf-8")
    assert EMOJI not in page and "​" not in page and "Ran the line" in page
    # In the shipped data itself: refused, as the course's own render refuses it.
    with pytest.raises(CourseError) as refused:
        build(tmp_path, catalog={**make_catalog(), "role": f"Widget engineer {EMOJI}"}, name="bad")
    assert "disallowed glyph" in str(refused.value) and not (tmp_path / "bad").exists()


def test_the_inputs_must_be_of_one_course_and_the_asset_base_a_plain_path(tmp_path: Path) -> None:
    with pytest.raises(CourseError):
        build(tmp_path, counts={**make_counts(), "role_key": "another-role"})
    with pytest.raises(CourseError):
        local.build_pathway_page(make_catalog(), make_counts(), None, tmp_path / "out" / "pathway.html", "https://cdn.example.com/assets")
    assert not (tmp_path / "out").exists()


# --- 3. the whole build and its record -------------------------------------------------------------------------------


def test_the_whole_build_writes_the_page_in_the_projects_folder_and_a_record_of_numbers(local_home: tuple[Path, Path]) -> None:
    home, target = local_home
    shipped = store.get_course(KEY)
    assert shipped is not None and store.local_page_state(home, target, KEY) == {"state": "not_built", "built_at": None}

    built, counts = local.build_local_page(home, target, shipped, now=NOW)

    learning = learning_store.learning_dir(home, target)
    assert built.path == store.pathway_page_path(home, target, KEY) == learning / "shipped" / KEY / "pathway.html"
    assert store.pathway_page(home, target, KEY) == built.path and built.compared is False
    page = built.path.read_text(encoding="utf-8")
    assert local.NOT_COMPARED in page and "Based on 2 of your stored postings in the last 90 days (3 any date)" in page
    assert "Fewer than 25 postings of the last 90 days have stored text (2), so every date is read" in page and "counts are directional" in page
    assert '<span class="tech-bar-pct">100.0%</span>' in page and '<span class="count-chip">2 postings</span>' in page
    company = counts["expectations"]["responsibilities"][0]["examples"][0]["company"]
    assert f"({company} &middot; Senior Widget Engineer)" in page, "the page is local: an example names its company"
    # The lessons are on the site: nothing was rendered, cached or written but the page and its record.
    assert f'href="{BASE}/lessons/lesson-alpha/"' in page and not (home / "cache" / "scout" / "learning-shipped").exists()
    assert sorted(entry.name for entry in built.path.parent.iterdir()) == ["pathway.html"]

    record_path = learning / "local" / f"{KEY}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record == {
        "schema": "scout-learning-local-page:1", "role_key": KEY, "built_at": "2026-10-09T12:00:00Z", "gigai_version": store.gigai_version(),
        "catalog_digest": local.catalog_digest(make_catalog()), "master": None, "resume_compared": False,
        "corpus": {"postings": 4, "with_text": 3, "with_text_in_window": 2, "read": 3, "basis": "any_date"},
        "concepts": 10, "concepts_named": 7, "lessons": 4, "lessons_named": 4,
    }
    assert record["catalog_digest"].startswith("sha256:") and len(record["catalog_digest"]) == 71
    raw = record_path.read_text(encoding="utf-8").lower()
    for word in (company.lower(), "widgetflow", "ship widgets", "you will", "fewer than", "senior widget engineer"):
        assert word not in raw, f"{word}: the record holds no posting text"
    assert store.local_page_state(home, target, KEY) == {"state": "built", "built_at": "2026-10-09T12:00:00Z"}
    again, _counts = local.build_local_page(home, target, shipped, now=NOW)
    assert again.path.read_text(encoding="utf-8") == page


def test_markers_and_the_master_they_came_from_are_given_together_and_the_record_keeps_only_the_revision(local_home: tuple[Path, Path]) -> None:
    home, target = local_home
    shipped = store.get_course(KEY)
    assert shipped is not None
    master = {"revision": 3, "revision_id": "rev-0a1b2c3d", "content_sha256": "ab" * 32, "written_by": "someone", "updated_at": "2026-10-01T00:00:00Z"}
    for markers, given in ((make_markers(), None), (None, master)):
        with pytest.raises(LearningError):
            local.build_local_page(home, target, shipped, markers=markers, master=given, now=NOW)
    assert store.pathway_page(home, target, KEY) is None

    built, _counts = local.build_local_page(home, target, shipped, markers=make_markers(), master=master, now=NOW)
    assert built.compared and "Production evidence" in built.path.read_text(encoding="utf-8")
    learning = learning_store.learning_dir(home, target)
    record = local.read_local_record(learning, KEY)
    assert record is not None and record["master"] == {"revision_id": "rev-0a1b2c3d", "content_sha256": "ab" * 32} and record["resume_compared"] is True
    raw = local.local_record_path(learning, KEY).read_text(encoding="utf-8")
    for word in ("Ran the widget line", "Cut widget scrap", "e-0a1b2c", "s-112233", "names the tool", "someone", "KNOWN"):
        assert word not in raw, f"{word}: the record holds nothing of a resume"


def test_a_record_that_is_not_one_is_not_read(tmp_path: Path) -> None:
    learning = tmp_path / "learning"
    path = local.local_record_path(learning, KEY)
    assert path == learning / "local" / f"{KEY}.json" and local.read_local_record(learning, KEY) is None
    path.parent.mkdir(parents=True)
    for content in ("not json", "[]", json.dumps({"schema": "another:1", "role_key": KEY}), json.dumps({"schema": local.RECORD_SCHEMA, "role_key": "another-role"})):
        path.write_text(content, encoding="utf-8")
        assert local.read_local_record(learning, KEY) is None
    path.unlink()
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps({"schema": local.RECORD_SCHEMA, "role_key": KEY}), encoding="utf-8")
    path.symlink_to(elsewhere)
    assert local.read_local_record(learning, KEY) is None and local.read_local_record(learning, "../escape") is None
    with pytest.raises(LearningError):
        local.local_record_path(learning, "../escape")


# --- 4. the state ----------------------------------------------------------------------------------------------------


def test_the_state_of_the_page_not_built_built_stale(local_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = local_home
    shipped = store.get_course(KEY)
    assert shipped is not None
    assert local.state(home, target, KEY) == {"state": "not_built", "built_at": None, "reasons": []}
    assert local.state(home, target, "../escape")["state"] == "not_built" and local.state(home, home / "not-a-project", KEY)["state"] == "not_built"

    # A page nobody recorded (not this module's): built, with its file time, and nothing to compare it with.
    page = store.pathway_page_path(home, target, KEY)
    page.parent.mkdir(parents=True)
    page.write_text("<!doctype html><title>mine</title>", encoding="utf-8")
    os.utime(page, (1_800_000_000, 1_800_000_000))
    assert local.state(home, target, KEY) == {"state": "built", "built_at": "2027-01-15T08:00:00Z", "reasons": []}

    master = {"revision_id": "rev-0a1b2c3d", "content_sha256": "ab" * 32}
    _built, counts = local.build_local_page(home, target, shipped, markers=make_markers(), master=master, now=NOW)
    current = {"state": "built", "built_at": "2026-10-09T12:00:00Z", "reasons": []}
    assert local.state(home, target, KEY) == current
    assert local.state(home, target, KEY, catalog=make_catalog(), master=master, corpus=counts["corpus"]) == current

    def stale(**given: Any) -> list[str]:
        found = local.state(home, target, KEY, **given)
        assert found["built_at"] == "2026-10-09T12:00:00Z" and found["state"] == ("stale" if found["reasons"] else "built")
        return found["reasons"]  # type: ignore[return-value]

    # The master the markers came from moved on, or is gone. Not looked at unless the caller says where the master is.
    assert stale(master={"revision_id": "rev-99999999", "content_sha256": "cd" * 32}) == ["master"]
    assert stale(master={**master, "content_sha256": "cd" * 32}) == ["master"] and stale(master=None) == ["master"]
    assert stale(master={**master, "written_by": "anyone", "revision": 9}) == []
    # The postings read: more than 10 percent of the recorded count (3 here), or another set read.
    assert stale(corpus={"read": 3, "basis": "any_date"}) == [] and stale(corpus={"read": 4, "basis": "any_date"}) == ["postings"]
    assert stale(corpus={"read": 2, "basis": "any_date"}) == ["postings"] and stale(corpus={"read": 3, "basis": "last_90_days"}) == ["postings"]
    # Another shipped catalog.
    changed = make_catalog()
    changed["vocabulary"][0]["phrases"].append("widget flow")
    assert stale(catalog=changed) == ["catalog"] and stale(catalog=json.loads(json.dumps(make_catalog(), indent=3))) == []
    (shipped.data_dir / "catalog.json").write_text(json.dumps(changed, sort_keys=True), encoding="utf-8")
    assert stale() == ["catalog"] and store.local_page_state(home, target, KEY) == {"state": "stale", "built_at": "2026-10-09T12:00:00Z"}
    (shipped.data_dir / "catalog.json").write_text(json.dumps(make_catalog(), sort_keys=True), encoding="utf-8")
    assert stale() == []
    # Another GigAI version than the one that built the page.
    monkeypatch.setattr(store, "gigai_version", lambda: "999.0.0")
    assert stale() == ["gigai_version"] and stale(catalog=changed, master=None, corpus={"read": 9, "basis": "any_date"}) == ["gigai_version", "catalog", "master", "postings"]
    assert store.local_page_state(home, target, KEY)["state"] == "stale"
    # Building again makes it current; the page gone makes it not built, whatever the record says.
    local.build_local_page(home, target, shipped, now=datetime(2026, 10, 10, 9, 30, tzinfo=UTC))
    assert local.state(home, target, KEY) == {"state": "built", "built_at": "2026-10-10T09:30:00Z", "reasons": []}
    page.unlink()
    assert local.state(home, target, KEY)["state"] == "not_built" and store.local_page_state(home, target, KEY) == {"state": "not_built", "built_at": None}


def test_the_ten_percent_rule_on_larger_counts_and_a_page_with_no_comparison() -> None:
    record = {"gigai_version": store.gigai_version(), "catalog_digest": "sha256:x", "master": None, "corpus": {"read": 100, "basis": "last_90_days"}}

    def drift(read: int) -> list[str]:
        return local.stale_reasons(record, corpus={"read": read, "basis": "last_90_days"})

    assert drift(110) == [] and drift(90) == [] and drift(111) == ["postings"] and drift(89) == ["postings"]
    assert local.POSTINGS_DRIFT_PERCENT == 10
    # From no posting at all, the first one is a change.
    empty = {**record, "corpus": {"read": 0, "basis": "any_date"}}
    assert local.stale_reasons(empty, corpus={"read": 0, "basis": "any_date"}) == [] and local.stale_reasons(empty, corpus={"read": 1, "basis": "any_date"}) == ["postings"]
    # A page built without a comparison holds nothing of a resume: a master that changes does not make it stale.
    assert local.stale_reasons(record, master={"revision_id": "rev-1", "content_sha256": "ef" * 32}) == [] and local.stale_reasons(record, master=None) == []
