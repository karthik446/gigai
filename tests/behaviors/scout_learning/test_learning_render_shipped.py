"""0.1.11.10 shipped lessons, slice 2: the renderer draws a SHIPPED (generic) course (``learning_render.render_shipped``).

A shipped course is package data: ``course.json`` with no marker, evidence, missing sentence, posting count or
posting sentence, lessons carrying ``concept_keys``, and ``paths/<lesson>.json``. Everything here is a SYNTHETIC
course invented in this file (3 modules, 4 lessons, 2 practice paths). No model call, no network.

1. It renders from the data folder: the pages find the assets and ONE ``course.css`` at the shared base, no page
   holds a ``<style>`` block, and nothing is copied into the course.
2. The shipped index and sidebar: one 'Your pathway' link to ``pathway.html``; no 'Your path' block, no marker,
   evidence or missing line, no postings chip, no percentage, no posting sentence, on any page.
3. The link audit ALLOWS the link to ``pathway.html`` (built locally, later; it may not exist) and SAYS SO:
   ``RenderResult.unchecked_links`` counts those links. Every other link is still checked.
4. The second lock: a course that still carries a resume field or a posting count is refused by name; and the
   shipped page bodies draw none of them even when handed one.
5. The same caps and gates as a personal course (lesson cap, glyph gate, validators), and a failed render removes
   only the pages it wrote.
6. Deterministic: the same data renders the same bytes; the asset base is the only thing a second base changes.
7. Timed: the largest course the cap allows (40 lessons, 40 practice pages) renders well inside a second or two.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gigai.scout import learning_render
from gigai.scout.learning_render import CourseError

from tests.behaviors.scout_learning.test_learning_render import mini_course, mini_paths

ASSET_BASE = "../../_assets"
EMOJI = "\U0001f600"

#: Strings a shipped page must never hold: the personal templates' classes and words (spike section 1).
PERSONAL_STRINGS = (
    'id="your-path"',
    "index.html#your-path",
    "Your path<",
    'class="your-path',
    "concept-marker-line",
    "evidence-line",
    "missing-line",
    'class="source-id"',
    "Production evidence",
    "You have:",
    "Missing:",
    'class="count-chip"',
    'class="curriculum-badge"',
    "tech-bar-pct",
    "tech-bar-fill",
    "examples-list",
    "Based on ",
    "generated offline",
    "<style",
)


def shipped_lesson(lesson_id: str, title: str, *, technologies: list[str] | None = None, related: list[str] | None = None, diagram: bool = False) -> dict:
    return {
        "id": lesson_id,
        "title": title,
        "source": "curriculum",
        "concept_keys": [f"key-{lesson_id}", "key-shared"],
        "summary": f"What {title} is and why a team needs it.",
        "detail": [{"heading": "Detail", "body": f"More about {title}."}],
        "in_practice": f"How a team does {title}.",
        "technologies": technologies or [],
        "diagram": {"source": "flowchart LR\n  A[Start] --> B[End]"} if diagram else None,
        "code": [{"lang": "python", "code": "print('hello')"}],
        "sources": [{"title": f"{title} guide", "url": f"https://docs.example.com/{lesson_id}/guide", "kind": "docs"}],
        "related": related or [],
        "subtopics": [
            {"id": f"{lesson_id}-first", "title": f"{title}: first part", "body": [{"heading": "", "body": f"The first part of {title}."}]},
            {"id": f"{lesson_id}-second", "title": f"{title}: second part", "body": [{"heading": "", "body": f"The second part of {title}."}]},
        ],
    }


def shipped_course() -> dict:
    """3 modules, 4 lessons, in the shipped format: no marker, evidence, missing, count, percentage or example."""

    return {
        "schema_version": "scout-learning-shipped-course:1",
        "role_key": "synthetic-widget-engineer",
        "role": "Synthetic widget engineer",
        "expectations": {
            "responsibilities": [{"id": "ship-widgets", "display": "Ship widgets"}, {"id": "review-widgets", "display": "Review widgets"}],
            "scope_at_staff": [{"id": "owns-an-area", "display": "Owns a widget area"}],
        },
        "technologies": {"Widget tooling": [{"id": "widgetflow", "display": "Widgetflow"}, {"id": "gearbox", "display": "Gearbox"}]},
        "modules": [
            {
                "id": "mod-basics",
                "title": "Basics",
                "intro": "Where widgets start.",
                "concepts": [
                    shipped_lesson("lesson-alpha", "Alpha", technologies=["Widgetflow", "Unlisted tool"], related=["lesson-beta"], diagram=True),
                    shipped_lesson("lesson-beta", "Beta", technologies=["Gearbox"], related=["lesson-alpha"]),
                ],
            },
            {"id": "mod-practice", "title": "Practice", "intro": "Doing it.", "concepts": [shipped_lesson("lesson-gamma", "Gamma")]},
            {"id": "mod-advanced", "title": "Advanced", "intro": "Going deeper.", "concepts": [shipped_lesson("lesson-delta", "Delta")]},
        ],
        "tool_links": {},
        "dropped": [],
    }


def shipped_paths(lesson_ids: tuple[str, ...] = ("lesson-alpha", "lesson-gamma")) -> dict[str, dict]:
    return {
        lesson_id: {
            "concept": lesson_id,
            "tool": "Widgetflow",
            "alternatives": "Gearbox, when the team already runs it.",
            "why_this_tool": "It runs on one laptop, so every step of this lesson can be practised end to end.",
            "setup": [{"n": 1, "do": "Install Widgetflow", "why": "Needed first", "source": {"title": "Install", "url": f"https://docs.example.com/{lesson_id}/install#steps"}, "done_when": "It starts"}],
            "steps": [{"n": 1, "do": "Run a first widget", "why": "Confirms the setup", "source": {"title": "Quickstart", "url": f"https://docs.example.com/{lesson_id}/quickstart#run"}, "done_when": "A widget finishes"}],
            "sources": [{"title": "Reference", "url": f"https://docs.example.com/{lesson_id}/reference", "kind": "docs"}],
        }
        for lesson_id in lesson_ids
    }


def write_data(folder: Path, course: dict, paths: dict[str, dict] | None = None) -> Path:
    """The shipped data folder of one course: ``course.json`` and ``paths/<lesson>.json``."""

    folder.mkdir(parents=True)
    (folder / "course.json").write_text(json.dumps(course, sort_keys=True), encoding="utf-8")
    (folder / "paths").mkdir()
    for lesson_id, path in (paths or {}).items():
        (folder / "paths" / f"{lesson_id}.json").write_text(json.dumps(path, sort_keys=True), encoding="utf-8")
    return folder


def pages_of(out_dir: Path) -> dict[str, str]:
    return {page.name: page.read_text(encoding="utf-8") for page in sorted(out_dir.glob("*.html"))}


@pytest.fixture
def rendered(tmp_path: Path) -> tuple[learning_render.RenderResult, Path]:
    data = write_data(tmp_path / "data", shipped_course(), shipped_paths())
    out_dir = tmp_path / "site"
    return learning_render.render_shipped(data, out_dir, ASSET_BASE), out_dir


# ---------------------------------------------------------------------------
# 1. Pages from data, assets and the stylesheet at the shared base
# ---------------------------------------------------------------------------

def test_a_shipped_course_renders_its_pages_and_copies_no_asset(rendered) -> None:
    result, out_dir = rendered

    names = sorted(p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*"))
    assert names == [
        "concept-lesson-alpha.html",
        "concept-lesson-beta.html",
        "concept-lesson-delta.html",
        "concept-lesson-gamma.html",
        "index.html",
        "practice-lesson-alpha.html",
        "practice-lesson-gamma.html",
    ]
    assert result.pages == 7
    assert result.size_bytes == sum(p.stat().st_size for p in out_dir.glob("*.html"))


def test_every_page_links_one_stylesheet_and_its_scripts_at_the_asset_base(rendered) -> None:
    _, out_dir = rendered

    for name, text in pages_of(out_dir).items():
        assert text.count(f'<link rel="stylesheet" href="{ASSET_BASE}/course.css">') == 1, name
        assert "<style" not in text, name
        assert f'<script src="{ASSET_BASE}/theme-init.js"></script>' in text, name
        assert f'<script src="{ASSET_BASE}/course.js" defer></script>' in text, name
        assert f'<script src="{ASSET_BASE}/mermaid/mermaid.min.js"></script>' in text, name
        assert 'src="assets/' not in text and 'href="assets/' not in text, name


def test_the_stylesheet_is_one_package_file_and_a_personal_page_still_holds_the_same_text_inline(tmp_path: Path) -> None:
    css_file = learning_render.package_assets_dir() / learning_render.COURSE_CSS_NAME
    css = css_file.read_text(encoding="utf-8")
    assert css == learning_render.course_css() and css.startswith(":root") and ".your-path" in css

    course_path = tmp_path / "course.json"
    course_path.write_text(json.dumps(mini_course()), encoding="utf-8")
    learning_render.render_to_folder(course_path, tmp_path / "personal")

    page = (tmp_path / "personal" / "index.html").read_text(encoding="utf-8")
    assert f"<style>\n\n{css}\n</style>" in page and "course.css" not in page
    assert 'src="assets/theme-init.js"' in page
    # a page with its stylesheet inline gets the copy of the assets it always got: no unused course.css in it
    assert (tmp_path / "personal" / "assets" / "course.js").is_file()
    assert not (tmp_path / "personal" / "assets" / "course.css").exists()


def test_a_personal_course_can_link_the_stylesheet_from_its_own_copy_of_the_assets(tmp_path: Path) -> None:
    options = learning_render.RenderOptions(inline_css=False)

    learning_render.build_course(mini_course(), tmp_path / "site", mini_paths(), options=options)

    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="assets/course.css">' in page and "<style" not in page
    assert (tmp_path / "site" / "assets" / "course.css").read_text(encoding="utf-8") == learning_render.course_css()
    assert 'id="your-path"' in page  # still a personal course: only where the stylesheet lives changed


@pytest.mark.parametrize("base, written", [("/learning/_assets/", "/learning/_assets"), ("../../_assets", "../../_assets"), ("_assets", "_assets")])
def test_the_asset_base_is_written_without_a_trailing_slash(tmp_path: Path, base: str, written: str) -> None:
    data = write_data(tmp_path / "data", shipped_course())

    learning_render.render_shipped(data, tmp_path / "site", base)

    assert f'href="{written}/course.css"' in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("base", ["", "/", "https://cdn.example.com/assets", "//cdn.example.com/assets", 'x" onload="y', "my assets"])
def test_an_asset_base_that_is_not_a_plain_path_is_refused(tmp_path: Path, base: str) -> None:
    data = write_data(tmp_path / "data", shipped_course())

    with pytest.raises(CourseError) as excinfo:
        learning_render.render_shipped(data, tmp_path / "site", base)

    assert "asset base" in str(excinfo.value)
    assert not (tmp_path / "site").exists()


# ---------------------------------------------------------------------------
# 2. The shipped index and sidebar
# ---------------------------------------------------------------------------

def test_the_shipped_index_has_one_pathway_link_plain_lists_and_the_lessons(rendered) -> None:
    _, out_dir = rendered
    index = (out_dir / "index.html").read_text(encoding="utf-8")
    main = index.split("<main>", 1)[1].split("</main>", 1)[0]

    assert main.count('<a href="pathway.html">Your pathway</a>') == 1
    assert "Shipped with GigAI" in main
    assert '<h2 id="expectations">Expectations</h2>' in main
    assert "<li>Ship widgets</li><li>Review widgets</li>" in main and "<li>Owns a widget area</li>" in main
    assert '<h2 id="technologies">Technologies</h2>' in main
    assert '<span id="tech-widgetflow">Widgetflow</span><span id="tech-gearbox">Gearbox</span>' in main
    assert '<li><a href="concept-lesson-alpha.html">Alpha</a></li>' in main
    assert "%" not in main and "postings</span>" not in main
    assert "Course &middot; shipped with GigAI &middot; no tracking" in index


def test_the_sidebar_of_every_page_points_at_the_pathway_page_not_at_a_block_of_the_index(rendered) -> None:
    _, out_dir = rendered

    for name, text in pages_of(out_dir).items():
        sidebar = text.split('<nav class="sidebar">', 1)[1].split("</nav>", 1)[0]
        overview = sidebar.split('<ul class="sidebar-overview">', 1)[1].split("</ul>", 1)[0]
        assert overview == (
            '\n<li><a href="pathway.html">Your pathway</a></li>'
            '\n<li><a href="index.html#expectations">Expectations</a></li>'
            '\n<li><a href="index.html#technologies">Technologies</a></li>\n'
        ), name
        assert 'href="practice-lesson-alpha.html"' in sidebar and 'href="practice-lesson-beta.html"' not in sidebar, name


def test_no_page_of_a_shipped_course_holds_a_marker_a_your_path_block_a_count_or_an_inline_style(rendered) -> None:
    _, out_dir = rendered

    for name, text in pages_of(out_dir).items():
        for needle in PERSONAL_STRINGS:
            assert needle not in text, (name, needle)


def test_a_lesson_page_keeps_its_generic_parts(rendered) -> None:
    _, out_dir = rendered
    page = (out_dir / "concept-lesson-alpha.html").read_text(encoding="utf-8")

    assert '<h3 id="subtopic-lesson-alpha-first">Alpha: first part</h3>' in page and "<p>The first part of Alpha.</p>" in page
    assert "<details><summary>Details</summary>" not in page  # no subtopic is folded away as 'known'
    assert '<pre class="mermaid">' in page and 'class="language-python"' in page
    assert '<a href="index.html#tech-widgetflow">Widgetflow</a>' in page  # resolves to the index's plain list
    assert "<span>Unlisted tool</span>" in page  # a name the index does not list stays plain text
    assert '<a href="practice-lesson-alpha.html" class="practice-link-button">Practice this</a>' in page
    assert '<a href="concept-lesson-beta.html">Beta</a>' in page


# ---------------------------------------------------------------------------
# 3. The link audit allows pathway.html and says so
# ---------------------------------------------------------------------------

def test_the_audit_allows_the_link_to_pathway_html_and_the_result_says_how_many(rendered) -> None:
    result, out_dir = rendered

    assert not (out_dir / "pathway.html").exists()
    # one link in every sidebar (7 pages) plus the one in the index body
    assert result.unchecked_links == (("pathway.html", 8),)
    assert learning_render.audit_links(out_dir, local_pages=frozenset({"pathway.html"})) == []


def test_without_the_allowance_the_same_site_fails_the_audit_on_exactly_those_links(rendered) -> None:
    _, out_dir = rendered

    problems = learning_render.audit_links(out_dir)

    assert len(problems) == 8
    assert all("href target 'pathway.html' does not resolve to a file in the site" in problem for problem in problems)


def test_every_other_link_of_a_shipped_course_is_still_checked(rendered) -> None:
    _, out_dir = rendered
    page = out_dir / "concept-lesson-alpha.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace("</main>", '<a href="pathway.html#lesson-alpha">mine</a><a href="concept-lesson-beta.html#nope">dead</a><a href="other.html">gone</a></main>'),
        encoding="utf-8",
    )

    problems = learning_render.audit_links(out_dir, local_pages=frozenset({"pathway.html"}))

    assert problems == [
        "concept-lesson-alpha.html: anchor '#nope' does not resolve to an id in 'concept-lesson-beta.html'",
        "concept-lesson-alpha.html: href target 'other.html' does not resolve to a file in the site",
    ]


def test_a_pathway_page_already_in_the_folder_is_neither_read_nor_removed(tmp_path: Path) -> None:
    data = write_data(tmp_path / "data", shipped_course(), shipped_paths())
    out_dir = tmp_path / "site"
    out_dir.mkdir()
    local = '<html><body><script>var x = 1;</script><a href="nowhere.html#x">dead</a></body></html>'
    (out_dir / "pathway.html").write_text(local, encoding="utf-8")

    result = learning_render.render_shipped(data, out_dir, ASSET_BASE)

    assert result.pages == 7 and result.unchecked_links == (("pathway.html", 8),)
    assert (out_dir / "pathway.html").read_text(encoding="utf-8") == local


def test_a_personal_course_gets_no_allowance(tmp_path: Path) -> None:
    result = learning_render.build_course(mini_course(), tmp_path / "site", mini_paths())

    assert result.unchecked_links == ()
    assert "pathway.html" not in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. The second lock: resume fields and posting counts
# ---------------------------------------------------------------------------

def test_a_personal_course_handed_to_render_shipped_is_refused_with_every_field_named(tmp_path: Path) -> None:
    paths = mini_paths()
    paths["concept-alpha"]["why_this_tool"] = "14 of 171 postings name it."
    paths["concept-beta"]["alternatives"] = "OtherTool. Named in your postings in the last 90 days: OtherTool 5 of 171."
    data = write_data(tmp_path / "data", mini_course(), paths)

    with pytest.raises(CourseError) as excinfo:
        learning_render.render_shipped(data, tmp_path / "site", ASSET_BASE)

    errors = excinfo.value.errors
    for expected in (
        "course: a shipped course carries no 'corpus' counts",
        "expectations.responsibilities[0]: a shipped course carries no 'percent'",
        "expectations.responsibilities[0]: a shipped course carries no 'examples'",
        "technologies.orchestration[0]: a shipped course carries no 'percent'",
        "modules[0].concepts[0] (concept-alpha): a shipped course carries no 'marker'",
        "modules[0].concepts[0] (concept-alpha): a shipped course carries no 'postings_in_90d'",
        "paths/concept-alpha.json: why_this_tool speaks of one machine's postings ('14 of 171 postings'); a shipped path does not",
        "paths/concept-beta.json: alternatives speaks of one machine's postings ('Named in your'); a shipped path does not",
    ):
        assert expected in errors, (expected, errors)
    assert not (tmp_path / "site").exists()


def test_a_subtopic_marker_evidence_or_missing_sentence_is_refused() -> None:
    course = shipped_course()
    sub = course["modules"][0]["concepts"][0]["subtopics"][1]
    sub.update({"marker": "SOME", "evidence": {"id": "b-000001", "line": "A synthetic line."}, "missing": "A synthetic gap."})

    errors = learning_render.validate_shipped_course(course)

    assert errors == [
        f"modules[0].concepts[0] (lesson-alpha).subtopics[1]: a shipped course carries no '{field}'" for field in ("marker", "evidence", "missing")
    ]


def test_a_plain_n_of_m_in_a_path_that_is_not_about_postings_is_not_refused() -> None:
    paths = shipped_paths()
    paths["lesson-alpha"]["alternatives"] = "Gearbox, when 2 of 3 replicas already run it."

    assert learning_render.validate_shipped_course(shipped_course(), paths) == []


def test_the_synthetic_shipped_course_passes_both_validators() -> None:
    assert learning_render.validate_shipped_course(shipped_course(), shipped_paths()) == []
    assert learning_render.validate_course(shipped_course(), shipped_paths()) == []


def test_empty_leftovers_of_the_personal_fields_are_not_an_error() -> None:
    course = shipped_course()
    course["corpus"] = {}
    course["modules"][0]["concepts"][0].update({"marker": None, "postings_in_90d": None})
    course["expectations"]["responsibilities"][0]["examples"] = []

    assert learning_render.validate_shipped_course(course) == []


def test_the_shipped_page_bodies_draw_no_personal_field_even_when_handed_one(tmp_path: Path) -> None:
    """``build_course`` with the shipped options on a PERSONAL course (the validator bypassed): nothing personal is drawn."""

    course = mini_course()
    sub = {"id": "alpha-known", "title": "Known part", "marker": "KNOWN", "evidence": {"id": "b-000001", "line": "Built alpha pipelines in production."}, "body": [{"heading": "", "body": "The body of the known part."}]}
    course["modules"][0]["concepts"][0]["subtopics"] = [sub]
    options = learning_render.shipped_options(ASSET_BASE)

    learning_render.build_course(course, tmp_path / "site", mini_paths(), options=options)

    pages = pages_of(tmp_path / "site")
    for name, text in pages.items():
        for needle in PERSONAL_STRINGS + ("Built alpha pipelines in production.", "Touched beta systems.", "Not shown by the cited line.", "Acme", "4 postings", "80%"):
            assert needle not in text, (name, needle)
    assert "<p>The body of the known part.</p>" in pages["concept-concept-alpha.html"]
    assert not (tmp_path / "site" / "assets").exists()


# ---------------------------------------------------------------------------
# 5. The same caps and gates; a failed render removes only its own pages
# ---------------------------------------------------------------------------

def many_lessons(count: int) -> dict:
    course = shipped_course()
    lessons = [shipped_lesson(f"lesson-{n:02d}", f"Lesson {n:02d}") for n in range(count)]
    course["modules"] = [{"id": f"mod-{m}", "title": f"Module {m}", "intro": "", "concepts": lessons[m * 5:(m + 1) * 5]} for m in range((count + 4) // 5)]
    return course


def test_the_lesson_cap_applies_to_a_shipped_course(tmp_path: Path) -> None:
    data = write_data(tmp_path / "data", many_lessons(learning_render.MAX_LESSONS + 1))

    with pytest.raises(CourseError) as excinfo:
        learning_render.render_shipped(data, tmp_path / "site", ASSET_BASE)

    assert any("exceeds the cap of 40" in error for error in excinfo.value.errors)


def test_the_validators_of_a_personal_course_apply_to_a_shipped_one(tmp_path: Path) -> None:
    course = shipped_course()
    course["modules"][0]["concepts"][0]["related"] = ["lesson-nowhere"]
    course["modules"][0]["concepts"][1]["sources"] = [{"title": "Home", "url": "https://docs.example.com/", "kind": "docs"}]
    paths = shipped_paths()
    paths["lesson-alpha"]["steps"][0]["source"]["url"] = ""
    data = write_data(tmp_path / "data", course, paths)

    with pytest.raises(CourseError) as excinfo:
        learning_render.render_shipped(data, tmp_path / "site", ASSET_BASE)

    text = str(excinfo.value)
    assert "unknown concept id 'lesson-nowhere'" in text and "is a bare domain/home page" in text and "item has no source url" in text


def test_a_failed_shipped_render_removes_its_own_pages_and_nothing_else(tmp_path: Path) -> None:
    course = shipped_course()
    course["modules"][2]["concepts"][0]["summary"] = "A smuggled glyph " + EMOJI
    data = write_data(tmp_path / "data", course, shipped_paths())
    out_dir = tmp_path / "site"
    out_dir.mkdir()
    (out_dir / "pathway.html").write_text("<html><body>mine</body></html>", encoding="utf-8")

    with pytest.raises(CourseError) as excinfo:
        learning_render.render_shipped(data, out_dir, ASSET_BASE)

    assert "disallowed glyph" in str(excinfo.value)
    assert sorted(p.name for p in out_dir.iterdir()) == ["pathway.html"]


@pytest.mark.parametrize("content", ["", "not json", "[1, 2]"])
def test_a_missing_or_broken_course_json_is_a_course_error(tmp_path: Path, content: str) -> None:
    data = tmp_path / "data"
    data.mkdir()
    if content:
        (data / "course.json").write_text(content, encoding="utf-8")

    with pytest.raises(CourseError) as excinfo:
        learning_render.render_shipped(data, tmp_path / "site", ASSET_BASE)

    assert "cannot read course JSON" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 6. Determinism
# ---------------------------------------------------------------------------

def test_rendering_a_shipped_course_twice_is_byte_identical(tmp_path: Path) -> None:
    data = write_data(tmp_path / "data", shipped_course(), shipped_paths())

    first = learning_render.render_shipped(data, tmp_path / "a", ASSET_BASE)
    second = learning_render.render_shipped(data, tmp_path / "b", ASSET_BASE)

    assert sorted(pages_of(tmp_path / "a")) == sorted(pages_of(tmp_path / "b"))
    for name in pages_of(tmp_path / "a"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    assert (first.pages, first.size_bytes, first.unchecked_links) == (second.pages, second.size_bytes, second.unchecked_links)


def test_the_key_order_of_the_data_files_does_not_change_the_pages(tmp_path: Path) -> None:
    course, paths = shipped_course(), shipped_paths()
    data_a = write_data(tmp_path / "data_a", course, paths)
    data_b = tmp_path / "data_b"
    (data_b / "paths").mkdir(parents=True)
    reordered = {key: course[key] for key in reversed(list(course))}
    reordered["modules"] = [{key: module[key] for key in reversed(list(module))} for module in course["modules"]]
    (data_b / "course.json").write_text(json.dumps(reordered, indent=2), encoding="utf-8")
    for lesson_id, path in paths.items():
        (data_b / "paths" / f"{lesson_id}.json").write_text(json.dumps({key: path[key] for key in reversed(list(path))}), encoding="utf-8")

    learning_render.render_shipped(data_a, tmp_path / "a", ASSET_BASE)
    learning_render.render_shipped(data_b, tmp_path / "b", ASSET_BASE)

    assert pages_of(tmp_path / "a") == pages_of(tmp_path / "b")


def test_a_second_asset_base_changes_only_the_asset_references(tmp_path: Path) -> None:
    data = write_data(tmp_path / "data", shipped_course(), shipped_paths())

    learning_render.render_shipped(data, tmp_path / "a", ASSET_BASE)
    learning_render.render_shipped(data, tmp_path / "b", "/learning/_assets")

    pages_a, pages_b = pages_of(tmp_path / "a"), pages_of(tmp_path / "b")
    assert pages_a != pages_b
    assert {name: text.replace(f'"{ASSET_BASE}/', '"/learning/_assets/') for name, text in pages_a.items()} == pages_b


# ---------------------------------------------------------------------------
# 7. Timed
# ---------------------------------------------------------------------------

def test_the_largest_course_the_cap_allows_renders_fast(tmp_path: Path) -> None:
    course = many_lessons(learning_render.MAX_LESSONS)
    lesson_ids = tuple(c["id"] for m in course["modules"] for c in m["concepts"])
    data = write_data(tmp_path / "data", course, shipped_paths(lesson_ids))

    started = time.perf_counter()
    result = learning_render.render_shipped(data, tmp_path / "site", ASSET_BASE)
    seconds = time.perf_counter() - started

    assert result.pages == 1 + 2 * learning_render.MAX_LESSONS
    # measured about 0.1 s here and 0.16 s for a real 67-page course; the bound only catches a render that went quadratic
    assert seconds < 5.0, seconds
