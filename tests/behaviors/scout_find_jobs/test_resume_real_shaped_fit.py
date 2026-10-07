"""0.1.11.5 (PE): a job's resume of a REAL pick's size, on the END outcome (the PDF pypdf reads back).

The fixtures of part (d) auto-fitted at 0.85 to 1.10; a real 20-bullet pick did not (2 pages only at 0.75, a 3rd page
that held nothing but Education at 0.80, and a degree with a long name on two lines).  Pinned here, on a synthetic
resume of that size (``tests/support/real_shaped_resume_fixture.py``):

- THE FIXTURE has a real pick's size: 20 bullets of two or three printed lines under six employers, four earlier
  roles, two projects with a line of technologies, 60 skills, two degrees with long names;
- THE PAGES at every slider position from 0.70 to 1.40 and the automatic spacing (``PAGES``, ``AUTO_FIT``);
- NO PAGE OF A SHORT BLOCK ALONE: at every slider position every page holds a bullet, whatever ends the resume
  (Education, the Skills, the earlier roles), and a section title is never the last line of a page;
- A DEGREE WITH A LONG NAME IS ONE LINE when it can be: its degree is set a step smaller (never below 8.3pt), the
  school and the years keep their size; one too long for that keeps the school and the degree on one line and puts
  the years on a second; one too long even for that wraps;
- A PROJECT'S TITLE AND ITS LINE OF TECHNOLOGIES are one line when they fit, and the two lines they were when not;
- ONE RENDER PATH: the preview's pictures are the PDF's pages; AN EDITED RESUME is set by the same rules and none of
  its words change.

Synthetic only: an invented person on reserved domains.
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.scout import resume_pdf
from gigai.scout.master_store import import_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.resume_display import SPACING_MAX, SPACING_MIN, form_header, parse_header_form
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import EARLIER_HEADING, list_tailored_resumes, shown_text

from tests.support import real_shaped_resume_fixture as real
from tests.support.pipeline_fixtures import JOB, PipelineFixture, build_pipeline_fixture

STAMP = datetime(2026, 10, 7, tzinfo=timezone.utc)
#: The compact header at its largest (a contact line that wraps): what the PDF prints over the resume.
FORM = {
    "name": "Zora Quillfeather", "email": "zora.quillfeather@example.invalid", "phone": "+1 (555) 010-0142", "location": "Nowhere Springs, Colorado",
    "work_authorization": "VISA: H1B", "linkedin": "linkedin.com/in/zora-quillfeather-staff-engineer",
    "links": [{"label": "GitHub", "url": "github.com/zora-quillfeather"}],
}
HEADER = form_header(parse_header_form(FORM))
#: Every position of the job page's slider.
SLIDER = tuple(round(SPACING_MIN + 0.05 * step, 2) for step in range(round((SPACING_MAX - SPACING_MIN) / 0.05) + 1))
LEFT, RIGHT = 65.0, 547.0  # the page's margins, in points
SECTION_TITLE, HEADING, BODY, SMALLEST = 7.1, 10.7, 9.5, 8.3  # type sizes
Row = tuple[int, float, float, float, str]
#: The fixture's skills that print (the rule of 0.1.11.5 (d): at most 4 lines at 8.3pt, cut from the end).
PRINTED_SKILLS = 39


def _pdf(markdown: str, spacing: float) -> resume_pdf.RenderedPdf:
    return resume_pdf.render_markdown_pdf(markdown, HEADER, timestamp=STAMP, spacing_scale=spacing, auto_fit=False)


def _rows(pdf: bytes) -> list[Row]:
    """Every run of text the PDF prints, in reading order: ``(page, y, x, type size, text)``."""

    rows: list[Row] = []
    for number, page in enumerate(PdfReader(io.BytesIO(pdf)).pages):
        def seen(text: str, cm: list[float], _tm: list[float], _font: object, size: float, number: int = number) -> None:
            if text.strip():
                rows.append((number, round(cm[5], 1), round(cm[4], 1), round(size, 1), text.strip()))

        page.extract_text(visitor_text=seen)
    return rows


def _section(rows: list[Row], title: str) -> list[Row]:
    start = next(index for index, row in enumerate(rows) if row[3] == SECTION_TITLE and row[4] == title)
    end = next((index for index in range(start + 1, len(rows)) if rows[index][3] == SECTION_TITLE), len(rows))
    return rows[start + 1 : end]


def _lines(rows: list[Row]) -> list[str]:
    """The printed lines of ``rows``: the runs that share a page and a baseline, joined."""

    out: dict[tuple[int, float], list[str]] = {}
    for page, y, _x, _size, text in rows:
        out.setdefault((page, y), []).append(text)
    return [" ".join(parts) for parts in out.values()]


_WORD = r"[a-z0-9+#&/%-]+"


def _squashed(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _education(degrees: str, spacing: float = 1.0) -> list[Row]:
    """The Education rows of a small resume whose Education is ``degrees`` (markdown)."""

    markdown = "## Experience\n\n### Larkspur Table Systems\nStaff Engineer | 2022 - Present\n\n- Built the scheduling service.\n\n## Education\n\n" + degrees
    return _section(_rows(_pdf(markdown, spacing).pdf), "EDUCATION")


# --- the fixture ---------------------------------------------------------------------------------------------------


def test_the_fixture_has_the_size_of_a_real_pick() -> None:
    rows = _rows(_pdf(real.resume(), 1.0).pdf)
    experience = _section(rows, "EXPERIENCE")
    bullets: list[int] = []  # the printed lines of each bullet: its "•" line and the lines indented under it
    for _key, group in _grouped(experience).items():
        if any(row[4].startswith("•") for row in group):
            bullets.append(1)
        elif bullets and all(row[2] > LEFT + 10 and row[3] == BODY for row in group):
            bullets[-1] += 1
    assert len(bullets) == 20 and set(bullets) == {2, 3} and bullets.count(3) >= 6, bullets
    assert len([row for row in experience if row[3] == HEADING and row[4] != EARLIER_HEADING.upper()]) == len(real.ROLES) == 6
    assert sum(1 for line in _lines(experience) if any(line.startswith(role.split(" | ")[0]) for role in real.EARLIER)) == 4
    # 60 skills.  The Skills print on at most 4 lines at 8.3pt (0.1.11.5 (d), unchanged here): of this list the first
    # 39 print and the last 21 do not, at every spacing.
    assert len(real.SKILLS.split(", ")) == 60 and _skills(rows) == real.SKILLS.split(", ")[:PRINTED_SKILLS]
    assert [line.split(" | ")[0] for line in _lines(_section(rows, "PROJECTS")) if " | " in line] == [title for title, _tech, _text in real.PROJECTS[:2]]
    summary = _section(rows, "SUMMARY")
    assert round((summary[0][1] - summary[-1][1]) / 14.3) + 1 == 4, "the Summary is not a paragraph of four printed lines"
    assert [row[4] for row in rows if row[3] == SECTION_TITLE] == ["SUMMARY", "EXPERIENCE", "PROJECTS", "SKILLS", "EDUCATION"]


def _skills(rows: list[Row]) -> list[str]:
    return [name.strip() for name in " ".join(row[4] for row in _section(rows, "SKILLS")).split(",")]


def _grouped(rows: list[Row]) -> dict[tuple[int, float], list[Row]]:
    out: dict[tuple[int, float], list[Row]] = {}
    for row in rows:
        out.setdefault((row[0], row[1]), []).append(row)
    return out


# --- the pages -----------------------------------------------------------------------------------------------------

#: Pages at every slider position, header on (measured 2026-10-07).  BEFORE this change (HEAD 315b207e) the same
#: resume: 2 pages at 0.70 and 0.75, 3 from 0.80 up, automatic 0.75.  With the degree, the project line and the
#: orphan rule alone: 2 pages up to 0.80, automatic 0.80; the gaps that tighten faster below 1.0 give 0.85.
PAGES = {0.7: 2, 0.75: 2, 0.8: 2, 0.85: 2, 0.9: 3, 0.95: 3, 1.0: 3, 1.05: 3, 1.1: 3, 1.15: 3, 1.2: 3, 1.25: 3, 1.3: 3, 1.35: 3, 1.4: 3}
AUTO_FIT = 0.85


def test_the_pages_at_every_slider_position_and_the_automatic_spacing() -> None:
    markdown = real.resume()
    assert {spacing: _pdf(markdown, spacing).pages for spacing in SLIDER} == PAGES
    fitted = resume_pdf.render_markdown_pdf(markdown, HEADER, timestamp=STAMP)
    assert (fitted.spacing_scale, fitted.pages) == (AUTO_FIT, 2) and AUTO_FIT >= 0.85, "the target: a real-shaped pick fits 2 pages at 0.85 or looser"
    assert len(PdfReader(io.BytesIO(fitted.pdf)).pages) == 2


# --- no page of a short block alone ----------------------------------------------------------------------------------

#: What ends the resume: Education (a master pick's order), the Skills, the earlier roles, and two longer resumes.
ENDINGS = {
    "education": {},
    "skills": {"order": ("projects", "education", "skills")},
    "skills, no projects": {"order": ("education", "skills")},
    "earlier roles": {"order": ()},
    "five earlier roles": {"earlier": 5},
    "three projects, five earlier roles": {"projects": 3, "earlier": 5},
}


@pytest.mark.parametrize("ending", sorted(ENDINGS))
def test_no_page_holds_only_a_short_block_at_any_slider_position(ending: str) -> None:
    alone: list[str] = []
    for bullets in ((20,) if ENDINGS[ending].get("order") != () else range(14, 21)):  # the earlier roles end a page only at some lengths
        markdown = real.resume(bullets=bullets, **ENDINGS[ending])  # type: ignore[arg-type]
        for spacing in SLIDER:
            rows = _rows(_pdf(markdown, spacing).pdf)
            for page in sorted({row[0] for row in rows}):
                on_page = [row for row in rows if row[0] == page]
                titles = [row[4].title() for row in on_page if row[3] == SECTION_TITLE]
                if not any(row[4].startswith("•") for row in on_page):
                    alone.append(f"{bullets} bullets at {spacing:.2f}: page {page + 1} holds only {' + '.join(titles) or _lines(on_page)[0][:40]}")
                # A section title is never the last line of a page.
                assert on_page[-1][3] != SECTION_TITLE, f"at {spacing:.2f} page {page + 1} ends with the title {on_page[-1][4]}"
            if "EDUCATION" in {row[4] for row in rows if row[3] == SECTION_TITLE}:
                assert len({row[0] for row in _section(rows, "EDUCATION")} | {row[0] for row in rows if row[4] == "EDUCATION"}) == 1, f"Education is split at {spacing:.2f}"
    assert not alone, "\n".join(alone)


# --- a degree with a long name ---------------------------------------------------------------------------------------


def test_a_degree_with_a_long_name_is_one_line_with_its_degree_a_step_smaller() -> None:
    rows = _education("### Example State University\nBachelors, Electronics and Communication Engineering | 2000 - 2004\n")
    assert _lines(rows) == ["EXAMPLE STATE UNIVERSITY | Bachelors, Electronics and Communication Engineering 2000 - 2004"]
    school, degree, years = rows
    # The degree alone is smaller (never below 8.3pt); the school and the years keep their size, the years at the right margin.
    assert (school[3], years[3]) == (HEADING, BODY) and SMALLEST <= degree[3] < BODY, rows
    assert school[2] == LEFT and RIGHT - 60 < years[2] < RIGHT
    # The fixture's own long degrees, at every slider position: this one is always one line.
    for spacing in SLIDER:
        education = _lines(_section(_rows(_pdf(real.resume(), spacing).pdf), "EDUCATION"))
        assert "EXAMPLE STATE UNIVERSITY | Bachelors, Electronics and Communication Engineering 2000 - 2004" in education, education


def test_a_degree_that_fits_keeps_the_size_it_had() -> None:
    rows = _education("### Example State University\nM.S., Information Systems | 2010 - 2012\n")
    assert _lines(rows) == ["EXAMPLE STATE UNIVERSITY | M.S., Information Systems 2010 - 2012"] and [row[3] for row in rows] == [HEADING, BODY, BODY]


def test_a_degree_too_long_for_one_line_puts_only_its_years_on_a_second_line() -> None:
    # The school and its degree fit one line at the smallest size, but not with the years after them.
    rows = _education("### Example State University\nMaster of Science, Computer Science and Information Systems | 2008 - 2010\n")
    assert _lines(rows) == ["EXAMPLE STATE UNIVERSITY | Master of Science, Computer Science and Information Systems", "2008 - 2010"]
    assert SMALLEST <= rows[1][3] <= BODY and rows[2][3] == BODY and RIGHT - 60 < rows[2][2] < RIGHT
    # Too long even without the years: it wraps at the body's size, every word printed, the years at the right margin of its last line.
    rows = _education("### Example Institute of Technology and Applied Sciences\nMaster of Science, Computer Science and Information Systems | 2008 - 2010\n")
    lines = _lines(rows)
    assert len(lines) == 2 and lines[1].endswith("2008 - 2010") and {row[3] for row in rows} == {HEADING, BODY}
    assert _squashed(" ".join(lines)) == _squashed("EXAMPLE INSTITUTE OF TECHNOLOGY AND APPLIED SCIENCES | Master of Science, Computer Science and Information Systems 2008 - 2010")


# --- a project's line of technologies --------------------------------------------------------------------------------


def _projects(entries: str, spacing: float = 1.0) -> list[Row]:
    markdown = "## Experience\n\n### Larkspur Table Systems\nStaff Engineer | 2022 - Present\n\n- Built the scheduling service.\n\n## Projects\n\n" + entries
    return _section(_rows(_pdf(markdown, spacing).pdf), "PROJECTS")


def test_a_projects_title_and_its_technologies_are_one_line_when_they_fit() -> None:
    rows = _projects("### Shiftboard, an open scheduling toolkit\nPython, OR-Tools, FastAPI, PostgreSQL, Docker\n\n- Solves weekly staff rosters.\n")
    assert _lines(rows) == ["Shiftboard, an open scheduling toolkit | Python, OR-Tools, FastAPI, PostgreSQL, Docker", "• Solves weekly staff rosters."]
    assert [row[3] for row in rows[:2]] == [HEADING, BODY]
    # A title with no line under it, and one with two, are set as they were.
    assert _lines(_projects("### Shiftboard\n\n- Solves weekly staff rosters.\n")) == ["Shiftboard", "• Solves weekly staff rosters."]
    assert _lines(_projects("### Shiftboard\nPython, OR-Tools\nMaintainer | 2019 - 2024\n\n- Solves weekly staff rosters.\n"))[:3] == ["Shiftboard", "Python, OR-Tools", "Maintainer 2019 - 2024"]


def test_a_project_too_long_for_one_line_prints_its_two_lines_where_they_were() -> None:
    long = "### Shiftboard, an open scheduling toolkit for clinics and food banks\nPython, OR-Tools, FastAPI, PostgreSQL, Docker, Kubernetes\n\n- Solves weekly staff rosters.\n"
    rows = _projects(long)
    assert _lines(rows) == [
        "Shiftboard, an open scheduling toolkit for clinics and food banks", "Python, OR-Tools, FastAPI, PostgreSQL, Docker, Kubernetes", "• Solves weekly staff rosters.",
    ]
    assert [row[3] for row in rows] == [HEADING, BODY, BODY, BODY]
    # ... exactly where a title with a second line that is never joined (it carries a third) puts them: the same baselines.
    third = _projects(long.replace("Kubernetes\n", "Kubernetes\nMaintainer\n"))
    assert [(row[1], row[2]) for row in rows[:2]] == [(row[1], row[2]) for row in third[:2]]


# --- an edited resume ------------------------------------------------------------------------------------------------


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    fx = build_pipeline_fixture(tmp_path, monkeypatch, resume=real.resume())
    master = tmp_path / "master.md"
    master.write_text(real.resume(projects=3, master=True), encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=master, gig_id=fx.gig.resolved.gig_id).status == "created"
    handed = tmp_path / "handed.md"
    handed.write_text(real.resume(), encoding="utf-8")
    result = CliRunner().invoke(scout_group, ["resume", "store", "--in", str(handed), "--job-url", JOB, "--as", "agent", "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert result.exit_code == 0, result.output
    return fx


def test_an_edited_resume_is_set_by_the_same_rules_and_none_of_its_words_change(fx: PipelineFixture) -> None:
    stored = list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)[0]
    assert stored.edited is not None, "the fixture's resume is not an edited one"
    before = {str(path.relative_to(fx.home_root)): path.read_bytes() for path in sorted(fx.home_root.rglob("*")) if path.is_file()}
    rendered, _name = resume_pdf.stored_resume_pdf(stored, home_root=fx.home_root, target=fx.target, form=FORM, spacing_scale=1.0, count_pages=True, fixed=True)
    preview, _name = resume_pdf.stored_resume_pdf(stored, home_root=fx.home_root, target=fx.target, form=FORM, spacing_scale=1.0, images=True, fixed=True)
    fitted, _name = resume_pdf.stored_resume_pdf(stored, home_root=fx.home_root, target=fx.target, form=FORM, count_pages=True)
    assert {str(path.relative_to(fx.home_root)): path.read_bytes() for path in sorted(fx.home_root.rglob("*")) if path.is_file()} == before, "rendering wrote to the home"
    assert len(preview.page_images) == rendered.pages == len(PdfReader(io.BytesIO(rendered.pdf)).pages) == PAGES[1.0]
    # The same pages and the same automatic spacing as the markdown of the same resume: one render path.
    assert (fitted.spacing_scale, fitted.pages) == (AUTO_FIT, 2)

    rows = _rows(rendered.pdf)
    # THE SAME RULES: the degree that fits a step smaller is one line, a project's technologies are on its title's line,
    # and every page holds a bullet.
    assert "EXAMPLE STATE UNIVERSITY | Bachelors, Electronics and Communication Engineering 2000 - 2004" in _lines(_section(rows, "EDUCATION"))
    assert "Shiftboard, an open scheduling toolkit | Python, OR-Tools, FastAPI, PostgreSQL, Docker" in _lines(_section(rows, "PROJECTS"))
    assert all(any(row[4].startswith("•") for row in rows if row[0] == page) for page in {row[0] for row in rows})
    # NONE OF ITS WORDS CHANGE: every line it stores is printed as stored, and the PDF prints no word it does not store
    # (the section titles and the header aside).
    body = rows[next(index for index, row in enumerate(rows) if row[3] == SECTION_TITLE) :]
    printed = _squashed("".join(row[4] for row in body))
    stored_lines = [
        line for section in stored.result.sections if section.heading != "skills"
        for line in (*section.lines, *(part for entry in section.entries for part in (*entry.heading, *entry.bullets)))
    ]
    assert len(stored_lines) > 40
    # The Skills: the names that print are the stored ones, in their order, from the front (the rule of part (d)).
    assert _skills(rows) == real.SKILLS.split(", ")[:PRINTED_SKILLS]
    for line in stored_lines:
        for part in shown_text(line).split(" | "):
            assert _squashed(part) in printed, f"a stored line is not printed as it is stored: {shown_text(line)!r}"
    titles = {"summary", "experience", "projects", "education", "skills"}
    words = {word for row in body for word in re.findall(_WORD, row[4].casefold())} - titles - {"•", "|"}
    own = {word for text in (real.SKILLS, EARLIER_HEADING, *(shown_text(line) for line in stored_lines)) for word in re.findall(_WORD, text.casefold())}
    assert words <= own, f"the PDF prints words the resume does not store: {sorted(words - own)}"
