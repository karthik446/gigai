"""0.1.11.10 Part B packet G3: the course renderer (``gigai.scout.learning_render``).

Everything here is built on a SYNTHETIC mini course (``make_mini_course``): 3 modules, 5 lessons, 2 practice-path
files, one Mermaid diagram, one code block, a marker of each kind (KNOWN, SOME, NEW) and real-shaped sources. No
model call, no network: the renderer is a pure function of ``course.json`` (and an optional paths folder) to a
folder of static HTML, and it is deterministic (the same input renders byte-identical output).

1. A good course renders, the output is accepted by the slice-1 importer (``learning_store.import_course``) with
   0 inline-script pages and the right lesson count, and the link audit returns ``[]``.
2. Every validator error from the design (section 5): dead related id, concept without sources and without
   subtopics, a bare-domain source url, a path item without a deep url, KNOWN/SOME with no evidence id, NEW with
   evidence, a bad Mermaid prefix, over the lesson cap.
3. The glyph gate: a zero-width space in an input title is stripped and counted, never an error by itself; a
   smuggled emoji fails the render.
4. Rendering twice from the same input is byte-identical, file for file.
5. The link audit finds an injected dead anchor on an otherwise-good course.
6. The vendored assets are importable from the installed package location (``importlib.resources``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout import learning_render
from gigai.scout.learning_render import CourseError

from tests.behaviors.scout_find_jobs.test_m1_end_to_end import _fixture

ZERO_WIDTH_SPACE = "​"
EMOJI = "\U0001f600"


def mini_course(*, title_glyph: str = "") -> dict:
    """3 modules, 5 lessons: a diagram, a code block, markers of all three kinds, real-shaped sources."""

    def concept(cid: str, title: str, *, marker: str, evidence: dict | None, diagram: bool = False, code: bool = False, related: list[str] | None = None) -> dict:
        out = {
            "id": cid,
            "title": title + title_glyph,
            "postings_in_90d": 4,
            "summary": f"A summary of {title}, written for a senior engineer learning the role.",
            "detail": [{"heading": "Detail", "body": f"More about {title}."}],
            "in_practice": f"How {title} is done on a real team.",
            "technologies": [],
            "diagram": {"source": "flowchart LR\n  A[Start] --> B[End]"} if diagram else None,
            "code": [{"lang": "python", "code": "print('hello')"}] if code else [],
            "sources": [{"title": f"{title} docs", "url": f"https://docs.example.com/{cid}/guide", "kind": "docs"}],
            "related": related or [],
            "marker": {"id": cid, "title": title, "marker": marker, "evidence": evidence, "missing": "Not shown by the cited line." if marker == "SOME" else None},
            "subtopics": [],
        }
        return out

    modules = [
        {
            "id": "mod-foundations",
            "title": "Foundations",
            "intro": "The basics.",
            "concepts": [
                concept("concept-alpha", "Alpha", marker="KNOWN", evidence={"id": "b-000001", "line": "Built alpha pipelines in production."}, diagram=True, related=["concept-beta"]),
                concept("concept-beta", "Beta", marker="SOME", evidence={"id": "b-000002", "line": "Touched beta systems."}, code=True, related=["concept-alpha"]),
            ],
        },
        {
            "id": "mod-practice",
            "title": "Practice",
            "intro": "Doing the work.",
            "concepts": [
                concept("concept-gamma", "Gamma", marker="NEW", evidence=None),
                concept("concept-delta", "Delta", marker="NEW", evidence=None),
            ],
        },
        {
            "id": "mod-advanced",
            "title": "Advanced",
            "intro": "Going deeper.",
            "concepts": [
                concept("concept-epsilon", "Epsilon", marker="NEW", evidence=None),
            ],
        },
    ]
    return {
        "role": "Synthetic MLOps engineer" + title_glyph,
        "corpus": {"postings_90d": 42, "postings_any_date": 90, "scope": "US, remote"},
        "expectations": {
            "responsibilities": [{"display": "Ship pipelines", "percent": 80, "examples": [{"text": "Built a pipeline", "company": "Acme", "title": "MLOps engineer"}]}],
            "scope_at_staff": [{"display": "Owns a platform area", "percent": 60, "examples": []}],
        },
        "technologies": {"orchestration": [{"display": "Airflow", "percent": 70}]},
        "modules": modules,
        "tool_links": {},
    }


def mini_paths(concept_ids: tuple[str, ...] = ("concept-alpha", "concept-beta")) -> dict[str, dict]:
    paths = {}
    for cid in concept_ids:
        paths[cid] = {
            "concept": cid,
            "tool": "ExampleTool",
            "alternatives": "OtherTool",
            "why_this_tool": "It is what the postings name most.",
            "setup": [
                {"n": 1, "do": "Install the tool", "why": "Needed first", "source": {"title": "Install guide", "url": f"https://docs.example.com/{cid}/install"}, "done_when": "The tool runs"},
            ],
            "steps": [
                {"n": 1, "do": "Run a first job", "why": "Confirms setup", "source": {"title": "Quickstart", "url": f"https://docs.example.com/{cid}/quickstart"}, "done_when": "A job finishes"},
            ],
            "sources": [{"title": "Reference", "url": f"https://docs.example.com/{cid}/reference", "kind": "docs"}],
        }
    return paths


def write_course(tmp_path: Path, course: dict, paths: dict[str, dict] | None = None) -> tuple[Path, Path | None]:
    course_path = tmp_path / "course.json"
    course_path.write_text(json.dumps(course), encoding="utf-8")
    paths_dir = None
    if paths:
        paths_dir = tmp_path / "paths"
        paths_dir.mkdir()
        for cid, path in paths.items():
            (paths_dir / f"{cid}.json").write_text(json.dumps(path), encoding="utf-8")
    return course_path, paths_dir


# ---------------------------------------------------------------------------
# A good course renders and is accepted by the slice-1 importer
# ---------------------------------------------------------------------------

def test_a_good_course_renders_with_the_right_page_count_and_assets(tmp_path: Path) -> None:
    course = mini_course()
    course_path, paths_dir = write_course(tmp_path, course, mini_paths())
    out_dir = tmp_path / "out"

    result = learning_render.render_to_folder(course_path, out_dir, paths_dir)

    assert result.pages == 1 + 5 + 2  # index + 5 concept pages + 2 practice pages
    assert (out_dir / "index.html").is_file()
    assert (out_dir / "assets" / "mermaid" / "mermaid.min.js").is_file()


def test_the_rendered_output_imports_with_zero_inline_script_pages_and_the_right_lesson_count(tmp_path: Path) -> None:
    from gigai.scout import learning_import

    course = mini_course()
    course_path, paths_dir = write_course(tmp_path, course, mini_paths())
    out_dir = tmp_path / "out"
    learning_render.render_to_folder(course_path, out_dir, paths_dir)

    home, target, _workpad = _fixture(tmp_path)
    result = learning_import.import_course(home, target, out_dir, "Synthetic MLOps engineer")

    assert result.inline_script_pages == 0
    assert result.pathway.course is not None
    assert result.pathway.course.lessons == 5


def test_the_link_audit_returns_no_problems_on_a_good_course(tmp_path: Path) -> None:
    course = mini_course()
    course_path, paths_dir = write_course(tmp_path, course, mini_paths())
    out_dir = tmp_path / "out"
    learning_render.render_to_folder(course_path, out_dir, paths_dir)

    assert learning_render.audit_links(out_dir) == []


def test_a_technology_the_index_does_not_list_is_plain_text_and_one_it_lists_is_a_link(tmp_path: Path) -> None:
    # 0.1.11.10 G5: a lesson's technology names are a model's own words; one the first page does not list used to be
    # a link to an anchor that is not there, which failed the link audit and with it the whole course.
    course = mini_course()
    listed = next(item["display"] for items in course["technologies"].values() for item in items)
    course["modules"][0]["concepts"][0]["technologies"] = [listed, "Some Tool Nobody Counted"]
    course_path, paths_dir = write_course(tmp_path, course, mini_paths())
    out_dir = tmp_path / "out"
    learning_render.render_to_folder(course_path, out_dir, paths_dir)

    page = (out_dir / "concept-concept-alpha.html").read_text(encoding="utf-8")
    assert f'<a href="index.html#{learning_render.tech_anchor(listed)}">{listed}</a>' in page
    assert "<span>Some Tool Nobody Counted</span>" in page and "some-tool-nobody-counted" not in page
    assert learning_render.audit_links(out_dir) == []
    # a caller that does not say what the index has keeps a link for every name
    assert 'href="index.html#tech-x"' in learning_render.render_tech_tags(["X"], {})


def test_the_index_lists_what_is_not_included_and_why_and_says_nothing_when_nothing_was_dropped(tmp_path: Path) -> None:
    # 0.1.11.10 G5: course.json dropped[] on the first page, in plain words; a title is escaped like every other text.
    course = mini_course()
    assert "Not included" not in learning_render.render_index(course, course["modules"])
    course["dropped"] = [
        {"what": "lesson:feature-stores", "title": "Feature <stores>", "reason": "no_verified_source"},
        {"what": "module:mod-serving", "title": "Serving", "reason": "module_text_failed", "lessons": ["a", "b", "c"]},
        {"what": "path:concept-beta", "reason": "path_unverified"},
        {"what": "lesson:late", "title": "Late", "reason": "some_new_reason"},
    ]
    course_path, paths_dir = write_course(tmp_path, course, mini_paths())
    out_dir = tmp_path / "out"
    learning_render.render_to_folder(course_path, out_dir, paths_dir)

    page = (out_dir / "index.html").read_text(encoding="utf-8")
    assert '<h2 id="not-included">Not included</h2>' in page
    for line in (
        "<li>Lesson Feature &lt;stores&gt;: no source page for it could be verified</li>",
        "<li>Module Serving (3 lessons): its text did not pass the checks, twice</li>",
        "<li>Practice path for concept-beta: too few of its practice steps could be verified</li>",
        "<li>Lesson Late: some new reason</li>",
    ):
        assert line in page, line
    assert learning_render.audit_links(out_dir) == []


def test_the_link_audit_finds_an_injected_dead_anchor(tmp_path: Path) -> None:
    course = mini_course()
    course_path, paths_dir = write_course(tmp_path, course)
    out_dir = tmp_path / "out"
    learning_render.render_to_folder(course_path, out_dir)

    index = out_dir / "index.html"
    index.write_text(index.read_text(encoding="utf-8") + '\n<a href="index.html#no-such-anchor">dead</a>\n', encoding="utf-8")

    problems = learning_render.audit_links(out_dir)
    assert any("no-such-anchor" in p for p in problems), problems


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_rendering_twice_from_the_same_input_is_byte_identical(tmp_path: Path) -> None:
    course = mini_course()
    course_path, paths_dir = write_course(tmp_path, course, mini_paths())
    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"

    learning_render.render_to_folder(course_path, out_a, paths_dir)
    learning_render.render_to_folder(course_path, out_b, paths_dir)

    files_a = sorted(p.relative_to(out_a).as_posix() for p in out_a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(out_b).as_posix() for p in out_b.rglob("*") if p.is_file())
    assert files_a == files_b
    for relative in files_a:
        assert (out_a / relative).read_bytes() == (out_b / relative).read_bytes(), relative


# ---------------------------------------------------------------------------
# Glyph gate
# ---------------------------------------------------------------------------

def test_a_zero_width_space_in_an_input_title_is_stripped_and_counted(tmp_path: Path) -> None:
    course = mini_course(title_glyph=ZERO_WIDTH_SPACE)
    course_path, paths_dir = write_course(tmp_path, course)
    out_dir = tmp_path / "out"

    result = learning_render.render_to_folder(course_path, out_dir)

    assert result.stripped_invisible > 0
    assert ZERO_WIDTH_SPACE not in (out_dir / "index.html").read_text(encoding="utf-8")


def test_a_smuggled_emoji_in_a_field_fails_the_render(tmp_path: Path) -> None:
    course = mini_course(title_glyph=EMOJI)
    course_path, _paths_dir = write_course(tmp_path, course)
    out_dir = tmp_path / "out"

    with pytest.raises(CourseError) as excinfo:
        learning_render.render_to_folder(course_path, out_dir)
    assert any("glyph" in e for e in excinfo.value.errors)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# Each validator error
# ---------------------------------------------------------------------------

def test_a_dead_related_id_is_refused() -> None:
    course = mini_course()
    course["modules"][0]["concepts"][0]["related"] = ["concept-does-not-exist"]

    errors = learning_render.validate_course(course)

    assert any("unknown concept id" in e for e in errors), errors


def test_a_concept_without_sources_and_without_subtopics_is_refused() -> None:
    course = mini_course()
    course["modules"][0]["concepts"][0]["sources"] = []
    course["modules"][0]["concepts"][0]["subtopics"] = []

    errors = learning_render.validate_course(course)

    assert any("without sources and without subtopics" in e or "no sources" in e for e in errors), errors


def test_a_bare_domain_source_url_is_refused() -> None:
    course = mini_course()
    course["modules"][0]["concepts"][0]["sources"] = [{"title": "Home", "url": "https://docs.example.com", "kind": "docs"}]

    errors = learning_render.validate_course(course)

    assert any("bare domain" in e for e in errors), errors


def test_a_path_item_without_a_deep_url_is_refused() -> None:
    path = mini_paths(("concept-alpha",))["concept-alpha"]
    path["steps"][0]["source"]["url"] = ""

    errors = learning_render.validate_path(path, "ctx")

    assert any("no source url" in e for e in errors), errors


def test_a_known_marker_with_no_evidence_id_is_refused() -> None:
    errors = learning_render.validate_marker({"marker": "KNOWN", "evidence": None}, "ctx")
    assert any("has no evidence id" in e for e in errors), errors


def test_a_some_marker_with_no_evidence_id_is_refused() -> None:
    errors = learning_render.validate_marker({"marker": "SOME", "evidence": {}}, "ctx")
    assert any("has no evidence id" in e for e in errors), errors


def test_a_new_marker_that_carries_evidence_is_refused() -> None:
    errors = learning_render.validate_marker({"marker": "NEW", "evidence": {"id": "b-1", "line": "x"}}, "ctx")
    assert any("must not carry evidence" in e for e in errors), errors


def test_a_diagram_with_a_bad_mermaid_prefix_is_refused() -> None:
    course = mini_course()
    course["modules"][0]["concepts"][0]["diagram"] = {"source": "pie title not a flowchart"}

    errors = learning_render.validate_course(course)

    assert any("flowchart, graph or sequenceDiagram" in e for e in errors), errors


def test_more_than_forty_lessons_is_refused() -> None:
    course = mini_course()
    extra_module = {
        "id": "mod-overflow",
        "title": "Overflow",
        "intro": "",
        "concepts": [
            {
                "id": f"concept-overflow-{i}",
                "title": f"Overflow {i}",
                "summary": "Summary.",
                "sources": [{"title": "Docs", "url": f"https://docs.example.com/overflow-{i}/guide", "kind": "docs"}],
                "related": [],
                "subtopics": [],
            }
            for i in range(40)
        ],
    }
    course["modules"].append(extra_module)

    errors = learning_render.validate_course(course)

    assert any("exceeds the cap" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Package data: the vendored assets are importable from the installed location
# ---------------------------------------------------------------------------

def test_the_vendored_assets_exist_via_importlib_resources() -> None:
    assets = learning_render.package_assets_dir()

    assert (assets / "course.js").is_file()
    assert (assets / "theme-init.js").is_file()
    assert (assets / "mermaid" / "mermaid.min.js").is_file()
    assert (assets / "mermaid" / "LICENSE").is_file()
    assert (assets / "hljs" / "highlight.min.js").is_file()
    assert (assets / "hljs" / "LICENSE").is_file()
    assert (assets / "hljs" / "languages" / "python.min.js").is_file()
    assert (assets / "hljs" / "languages" / "go.min.js").is_file()
    assert (assets / "hljs" / "languages" / "bash.min.js").is_file()
    assert (assets / "hljs" / "styles" / "github.min.css").is_file()
    assert (assets / "hljs" / "styles" / "github-dark.min.css").is_file()
    assert (assets / "mermaid" / "mermaid.min.js").stat().st_size > 1_000_000
