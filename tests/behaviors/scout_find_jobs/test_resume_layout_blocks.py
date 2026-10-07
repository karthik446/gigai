"""0.1.11.5 (d): how four blocks of a job's resume are SET, on the END outcome (the PDF pypdf reads back).

A 20-bullet pick with every block printed took too many lines outside its bullets: two lines a degree, a title over
two or three one-line roles, five or six rows of Skills chips, the Summary as an indented bullet.  Pinned here, on
synthetic data (``tests/support/resume_spacing_fixture.py`` and ``tests/support/pick_cap_fixture.py``):

- A DEGREE IS ONE LINE: the school, its degree after it and the years at the right margin share one baseline in the
  PDF and the preview; the markdown keeps its two lines (they are what a hand-back copies from the master,
  unchanged) and is set the same way, and so is a degree written on one line (``### School | Degree | years``);
- EARLIER EXPERIENCE has no title of its own with 3 roles or fewer (its lines follow the last role, newest first)
  and keeps it with 4 or more;
- SKILLS are plain comma-separated lines, at most 4, what the posting asks for first, no skill twice, nothing that
  the master does not list; a list too long for 4 lines even at 8.3pt is cut from the end and nothing marks the cut;
- THE SUMMARY is a plain paragraph at the left margin (no bullet, no indent);
- ONE RENDER PATH: the stored resume and its own markdown give the template the same data, and the preview's
  pictures are the PDF's pages;
- WHAT IS NOT ONE OF THE FOUR BLOCKS DOES NOT MOVE at 1.0 and above (where the roles and the project end is what the
  template before this change measured, to the last digit);
- THE PAGES: both fixtures with a header fit 2 pages at the automatic spacing, and that spacing is 0.85 or looser;
- AN EDITED RESUME is set by the same rules and none of its words change: every stored line is in the PDF as it is
  stored, and rendering writes nothing.

Synthetic only: an invented person on reserved domains.
"""

from __future__ import annotations

import io
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.scout import master_selection as ms
from gigai.scout import pick, resume_pdf
from gigai.scout.find_jobs.assess_contracts import AssessmentBody
from gigai.scout.find_jobs.contracts import Verdict
from gigai.scout.master_resume import Master, parse_master
from gigai.scout.master_store import import_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.resume_display import form_header, parse_header_form
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import EARLIER_HEADING, EARLIER_TITLE_MIN, TailoredResume, list_tailored_resumes, render_markdown, shown_text

from tests.support import pick_cap_fixture as cap
from tests.support import resume_spacing_fixture as spacing_fixture
from tests.support.pipeline_fixtures import JOB, PipelineFixture, build_pipeline_fixture

TODAY = date(2026, 10, 6)
STAMP = datetime(2026, 10, 6, tzinfo=timezone.utc)
PROFILE = ms.SelectionProfile(titles=("Staff Software Engineer",))
POSTING = ms.SelectionPosting(cap.TITLE, cap.POSTING, "Larkspur Table Systems", "Remote")
#: A header at its largest (a contact line that wraps): what the PDF prints over the resume.
FORM = {
    "name": "Zora Quillfeather", "email": "zora.quillfeather@example.invalid", "phone": "+1 (555) 010-0142", "location": "Nowhere Springs, Colorado",
    "work_authorization": "VISA: H1B", "linkedin": "linkedin.com/in/zora-quillfeather-staff-engineer",
    "links": [{"label": "GitHub", "url": "github.com/zora-quillfeather"}],
}
HEADER = form_header(parse_header_form(FORM))
LEFT = 65.0  # the page's left margin, in points
SECTION_TITLE = 7.1  # the type size of a section title


def _picked(master_markdown: str | None = None, posting: ms.SelectionPosting = POSTING) -> TailoredResume:
    """The code selector's 20-bullet pick from the fixture master (or a variant of it), as it is stored."""

    master: Master = parse_master(master_markdown or cap.master_markdown())
    return pick.settle(master, AssessmentBody((), (), (), verdict=Verdict.MATCHED_ABOVE_THRESHOLD, pick=None), None, TODAY, profile=PROFILE, posting=posting).result


def _pdf(result: TailoredResume, spacing: float = 1.0) -> bytes:
    return resume_pdf.render_pdf(result, None, company="Larkspur", timestamp=STAMP, spacing_scale=spacing, auto_fit=False)


def _rows(pdf: bytes) -> list[tuple[int, float, float, float, str]]:
    """Every run of text the PDF prints, in reading order: ``(page, y, x, type size, text)``."""

    rows: list[tuple[int, float, float, float, str]] = []
    for number, page in enumerate(PdfReader(io.BytesIO(pdf)).pages):
        def seen(text: str, cm: list[float], _tm: list[float], _font: object, size: float, number: int = number) -> None:
            if text.strip():
                rows.append((number, round(cm[5], 1), round(cm[4], 1), round(size, 1), text.strip()))

        page.extract_text(visitor_text=seen)
    return rows


def _section(rows: list[tuple[int, float, float, float, str]], title: str) -> list[tuple[int, float, float, float, str]]:
    """The rows under the section title ``title``, up to the next section title."""

    start = next(index for index, row in enumerate(rows) if row[3] == SECTION_TITLE and row[4] == title)
    end = next((index for index in range(start + 1, len(rows)) if rows[index][3] == SECTION_TITLE), len(rows))
    return rows[start + 1 : end]


def _lines(rows: list[tuple[int, float, float, float, str]]) -> list[str]:
    """The printed lines of ``rows``: the runs that share a page and a baseline, joined."""

    out: dict[tuple[int, float], list[str]] = {}
    for page, y, _x, _size, text in rows:
        out.setdefault((page, y), []).append(text)
    return [" ".join(parts) for parts in out.values()]


def _squashed(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _printed(pdf: bytes) -> str:
    """Everything the PDF prints, with no white space and no case (a PDF wraps lines and prints employers in capitals)."""

    return _squashed("".join(row[4] for row in _rows(pdf)))


# --- (1) a degree is one line -------------------------------------------------------------------------------------


def test_a_degree_is_one_line_in_the_pdf_and_the_preview_whichever_way_the_markdown_writes_it() -> None:
    result = _picked()
    markdown = render_markdown(result)

    printed = _lines(_section(_rows(_pdf(result)), "EDUCATION"))
    assert printed == [
        "EXAMPLE STATE UNIVERSITY | M.S., Information Systems 2010 - 2012",
        "EXAMPLE INSTITUTE OF TECHNOLOGY | B.Tech., Computer Science 2000 - 2004",
    ], "a degree is not ONE line of the PDF (school, degree and years on one baseline)"
    years = [row for row in _section(_rows(_pdf(result)), "EDUCATION") if row[4] in ("2010 - 2012", "2000 - 2004")]
    assert len(years) == 2 and all(row[2] > 400 for row in years), "the years are not at the right margin"

    # ONE render path: the stored resume and its own markdown hand the template the same data, and the preview's
    # pictures are the PDF's pages.
    sections = resume_pdf._body(result)  # noqa: SLF001 - what the template is given
    assert sections == resume_pdf.parse_resume_markdown(markdown)[1]
    for spacing in (0.7, 1.0):
        made = {
            images: resume_pdf._render(sections, HEADER, company="", timestamp=STAMP, spacing_scale=spacing, auto_fit=False, count_pages=True, images=images)  # noqa: SLF001
            for images in (False, True)
        }
        assert len(made[True].page_images) == made[True].pages == made[False].pages == len(PdfReader(io.BytesIO(made[False].pdf)).pages)


def test_a_degree_written_on_two_lines_or_on_one_is_set_on_one_line_and_its_details_stay_lines_of_their_own() -> None:
    head = "## Education\n\n"
    two_lines = head + "### Example State University\nM.S., Information Systems | 2010 - 2012\n"
    one_line = head + "### Example State University | M.S., Information Systems | 2010 - 2012\n"
    assert resume_pdf.parse_resume_markdown(two_lines)[1] == resume_pdf.parse_resume_markdown(one_line)[1]
    made = resume_pdf.render_markdown_pdf(two_lines, None, timestamp=STAMP, spacing_scale=1.0, auto_fit=False).pdf
    assert _lines(_section(_rows(made), "EDUCATION")) == ["EXAMPLE STATE UNIVERSITY | M.S., Information Systems 2010 - 2012"]

    # A third heading line and a line under the degree are not squeezed onto the degree's line: each stays a line.
    detailed = two_lines + "Graduated with distinction\n\n- Thesis on shift scheduling under labour rules.\n"
    assert _lines(_section(_rows(resume_pdf.render_markdown_pdf(detailed, None, timestamp=STAMP, spacing_scale=1.0, auto_fit=False).pdf), "EDUCATION")) == [
        "EXAMPLE STATE UNIVERSITY | M.S., Information Systems 2010 - 2012", "Graduated with distinction", "• Thesis on shift scheduling under labour rules.",
    ]
    # A school with no degree line, and two lines that both carry dates (they cannot be one line), print as they are.
    assert _lines(_section(_rows(resume_pdf.render_markdown_pdf(head + "### Example State University\n", None, timestamp=STAMP).pdf), "EDUCATION")) == ["EXAMPLE STATE UNIVERSITY"]
    both = resume_pdf.parse_resume_markdown(head + "### Example State University | 2010 - 2012\nM.S., Information Systems | 2011 - 2012\n")[1][0]["entries"]
    assert "oneline" not in both[0] and len(both[0]["heading"]) == 2  # type: ignore[index]


# --- (2) the roles shown by their heading alone ---------------------------------------------------------------------


@pytest.mark.parametrize("roles", [1, 2, 3, 4])
def test_earlier_experience_has_no_title_of_its_own_with_three_roles_or_fewer_and_keeps_it_with_four(roles: int) -> None:
    # Drop the later earlier roles first, so each cut ends at the next entry or at the next section.
    markdown = cap.master_markdown()
    for company, _title, _dates in reversed(cap.EARLIER[roles:]):
        start = markdown.index(f"### {company} ")
        following = min(index for index in (markdown.find("\n### ", start), markdown.find("\n## ", start)) if index != -1)
        markdown = markdown[:start] + markdown[following + 1 :]
    result = _picked(markdown)
    listed = [f"{title}, {company} {dates}" for company, title, dates in cap.EARLIER[:roles]]
    experience = _lines(_section(_rows(_pdf(result)), "EXPERIENCE"))
    titled = EARLIER_HEADING.upper() in experience
    assert titled is (roles >= EARLIER_TITLE_MIN), f"{roles} earlier roles: the title is {'printed' if titled else 'not printed'}"
    # One line a role, newest first, the last lines of Experience, with or without the title.
    assert experience[-roles:] == listed
    # The markdown keeps the block under its heading at every size (the rule is how it is SET), and the markdown is
    # set as the stored resume is.
    text = render_markdown(result)
    assert f"### {EARLIER_HEADING}\n" in text
    assert resume_pdf.parse_resume_markdown(text)[1] == resume_pdf._body(result)  # noqa: SLF001


def test_earlier_experience_in_resume_markdown_follows_the_same_rule() -> None:
    def experience(roles: int) -> list[str]:
        text = spacing_fixture.resume(5).replace("\n".join(spacing_fixture.EARLIER), "\n".join(spacing_fixture.EARLIER[:roles]))
        return _lines(_section(_rows(resume_pdf.render_markdown_pdf(text, None, timestamp=STAMP, spacing_scale=1.0, auto_fit=False).pdf), "EXPERIENCE"))

    assert EARLIER_HEADING.upper() not in experience(3) and experience(3)[-3:] == [role.replace(" | ", " ") for role in spacing_fixture.EARLIER[:3]]
    assert experience(4)[-5:] == [EARLIER_HEADING.upper(), *(role.replace(" | ", " ") for role in spacing_fixture.EARLIER)]


# --- (3) the Skills ------------------------------------------------------------------------------------------------


def _skills(pdf: bytes) -> tuple[list[str], list[tuple[int, float, float, float, str]]]:
    """``(the names the PDF prints, the printed rows)`` of the Skills section."""

    rows = _section(_rows(pdf), "SKILLS")
    return [name.strip() for name in " ".join(row[4] for row in rows).split(",") if name.strip()], rows


def _with_skills(*lines: str) -> str:
    whole = f"- {cap.SKILLS} <!-- id:skills-1 -->"
    markdown = cap.master_markdown()
    assert whole in markdown
    return markdown.replace(whole, "\n".join(f"- {line} <!-- id:skills-{number} -->" for number, line in enumerate(lines, 1)))


def test_the_skills_are_plain_lines_at_most_four_with_what_the_posting_asks_for_first() -> None:
    result = _picked()
    names, rows = _skills(_pdf(result))
    assert 1 <= len({(row[0], row[1]) for row in rows}) <= 4, "the Skills take more than 4 lines"
    assert {row[2] for row in rows} == {LEFT} and {row[3] for row in rows} == {9.5}, "the Skills are not plain lines of the body's type at the left margin"
    # What the posting asks for, in the posting's order, then the rest; every skill of the master, each once.
    assert names[:7] == ["Python", "Kafka", "PostgreSQL", "Kubernetes", "Terraform", "React", "GraphQL"]
    master = [name.strip() for name in cap.SKILLS.split(",")]
    assert sorted(names) == sorted(master) and len(set(names)) == len(names)

    # A TypeScript job: TypeScript is first, before Ruby (which this posting does not ask for).
    typescript = ms.SelectionPosting("Staff Software Engineer, Web Platform", "Staff Software Engineer, Web Platform\n\nRequirements:\n- TypeScript in production\n- React\n", "Larkspur", "Remote")
    asked, _rows_ = _skills(_pdf(_picked(posting=typescript)))
    assert asked[0] == "TypeScript" and asked.index("TypeScript") < asked.index("Ruby") and sorted(asked) == sorted(master)


def test_no_skill_is_printed_twice_and_none_is_invented_or_lost() -> None:
    result = _picked(_with_skills("Languages: Python/Ruby, Go, TypeScript", "Web: Ruby/Rails, Ruby, React, CI/CD, typescript", "Data: PostgreSQL, Kafka, Model Context Protocol (MCP)"))
    names, _rows_ = _skills(_pdf(result))
    single = [part for name in names for part in (name.split("/") if name != "CI/CD" else [name])]
    assert len({part.casefold() for part in single}) == len(single), f"a skill is printed twice: {names}"
    stated = {"Python", "Ruby", "Go", "TypeScript", "Rails", "React", "CI/CD", "PostgreSQL", "Kafka", "Model Context Protocol (MCP)"}
    assert set(single) == stated, "a skill was invented or lost"
    assert "Python/Ruby" in names and "Rails" in names and "Ruby" not in names and "Ruby/Rails" not in names


def test_a_list_too_long_for_four_lines_is_cut_from_the_end_and_nothing_marks_the_cut() -> None:
    extra = [f"Toolkit {number:03d}" for number in range(120)]
    result = _picked(_with_skills(cap.SKILLS, ", ".join(extra)))
    names, rows = _skills(_pdf(result))
    assert len({(row[0], row[1]) for row in rows}) == 4, "a long Skills list does not take exactly 4 lines"
    assert {row[3] for row in rows} == {8.3}, "a long list is not set at the smallest Skills size before it is cut"
    whole = [name for name in " ".join(section.lines[0].text for section in result.sections if section.heading == "skills").lstrip("- ").split(", ")]
    assert len(whole) == 156 and 40 < len(names) < len(whole)
    assert names == whole[: len(names)], "what is printed is not the front of the list, in its order"
    assert names[:7] == ["Python", "Kafka", "PostgreSQL", "Kubernetes", "Terraform", "React", "GraphQL"], "what the posting asks for is not first"
    text = " ".join(row[4] for row in rows)
    assert "..." not in text and "…" not in text and "more" not in text.casefold(), "the cut is marked"
    # A list that fits in 4 lines at a smaller size is printed whole at that size.
    some, some_rows = _skills(_pdf(_picked(_with_skills(cap.SKILLS, ", ".join(extra[:10])))))
    assert len(some) == 46 and len({(row[0], row[1]) for row in some_rows}) == 4 and {row[3] for row in some_rows} < {9.0, 8.5, 8.3}


# --- (4) the Summary -----------------------------------------------------------------------------------------------


def test_the_summary_is_a_plain_paragraph_at_the_left_margin() -> None:
    result = _picked()
    stored = next(section for section in result.sections if section.heading == "summary")
    assert stored.lines[0].text.startswith("- "), "the fixture's Summary is not stored as a bullet"
    rows = _section(_rows(_pdf(result)), "SUMMARY")
    assert {row[2] for row in rows} == {LEFT}, "the Summary is indented"
    assert not any("•" in row[4] for row in rows), "the Summary has a bullet"
    assert _squashed(" ".join(row[4] for row in rows)) == _squashed(shown_text(stored.lines[0]))
    # A bullet under a role keeps its bullet and its indent.
    assert any(row[2] > LEFT and row[3] == 9.5 for row in _section(_rows(_pdf(result)), "EXPERIENCE"))


# --- what is not one of the four blocks does not move ----------------------------------------------------------------

#: Where the 20 bullets of the spacing fixture under their five roles, and the project, end (page, fill of that page)
#: as the template BEFORE this change measured it (HEAD 3f1c134f, 2026-10-07).
#: 0.1.11.5 PB5 (an entry splits across pages) moved 1.2 on purpose, from (2, 0.6041618497109827): there a role had left
#: page 1 whole and page 2 began with it; page 1 now runs to its foot and page 2 ends higher.  1.0 and 1.4, where no
#: block had left a page early, are as they were to the last digit.
UNMOVED = {1.0: (2, 0.45052023121387297), 1.2: (2, 0.5126069364161852), 1.4: (2, 0.6239306358381504)}


def test_at_one_and_above_the_roles_their_bullets_and_the_project_end_where_they_did() -> None:
    whole = spacing_fixture.resume(20)
    roles = whole[whole.index("## Experience") : whole.index("## Education")].replace("### Earlier experience\n" + "\n".join(spacing_fixture.EARLIER) + "\n", "")
    assert "## Projects" in roles and "Earlier" not in roles
    for spacing, (page, fill) in UNMOVED.items():
        measured = resume_pdf.measure_markdown(roles, spacing_scale=spacing, printed=True)
        assert measured[0] == page and measured[1] == pytest.approx(fill, abs=1e-12), f"the layout at {spacing} moved: {measured}"


# --- the pages -------------------------------------------------------------------------------------------------------

#: With the header on: pages at four spacings and the automatic spacing.  BEFORE this change (HEAD 3f1c134f): the
#: spacing fixture 3 pages at 1.0 and automatic 0.85; the pick fixture 3 pages at 1.0 and automatic 0.95.
#: 0.1.11.5 PB5 (an entry splits across pages): the pick fixture's automatic spacing is 1.25 (was 1.0: from 1.05 up a
#: role that did not fit page 1 left it whole, and the resume ran to a 3rd page).
PAGES = {
    "spacing_fixture": ({1.0: 2, 0.85: 2, 0.75: 2, 0.7: 2}, 1.05),
    "pick_cap_fixture": ({1.0: 2, 0.85: 2, 0.75: 2, 0.7: 2}, 1.25),
}


def test_a_twenty_bullet_resume_with_every_block_and_a_header_fits_two_pages_at_a_spacing_of_085_or_looser() -> None:
    picked = _picked()
    assert sum(len(entry.bullets) for section in picked.sections if section.heading in ("experience", "projects") for entry in section.entries) == 20
    assert {section.heading for section in picked.sections} >= {"summary", "experience", "projects", "skills", "education"}
    measured = {}
    for name, markdown in (("spacing_fixture", spacing_fixture.resume(20)), ("pick_cap_fixture", render_markdown(picked))):
        pages = {spacing: resume_pdf.render_markdown_pdf(markdown, HEADER, timestamp=STAMP, spacing_scale=spacing, auto_fit=False).pages for spacing in PAGES[name][0]}
        fitted = resume_pdf.render_markdown_pdf(markdown, HEADER, timestamp=STAMP)
        assert fitted.pages == 2 and fitted.spacing_scale >= 0.85, f"{name}: the automatic spacing is {fitted.spacing_scale:g} on {fitted.pages} pages"
        measured[name] = (pages, fitted.spacing_scale)
    assert measured == PAGES


# --- an edited resume --------------------------------------------------------------------------------------------------


def _edited_markdown() -> str:
    """A resume as an agent hands it back: the two-line degrees and the block of THREE earlier roles under its title."""

    return spacing_fixture.resume(20).replace("\n".join(spacing_fixture.EARLIER), "\n".join(spacing_fixture.EARLIER[:3]))


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    fx = build_pipeline_fixture(tmp_path, monkeypatch, resume=_edited_markdown())
    master = tmp_path / "master.md"
    master.write_text(spacing_fixture.resume(30, master=True), encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=master, gig_id=fx.gig.resolved.gig_id).status == "created"
    handed = tmp_path / "handed.md"
    handed.write_text(_edited_markdown(), encoding="utf-8")
    result = CliRunner().invoke(scout_group, ["resume", "store", "--in", str(handed), "--job-url", JOB, "--as", "agent", "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert result.exit_code == 0, result.output
    return fx


def test_an_edited_resume_is_set_by_the_same_rules_and_none_of_its_words_change(fx: PipelineFixture) -> None:
    stored = list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)[0]
    assert stored.edited is not None, "the fixture's resume is not an edited one"
    before = {str(path.relative_to(fx.home_root)): path.read_bytes() for path in sorted(fx.home_root.rglob("*")) if path.is_file()}
    rendered, _name = resume_pdf.stored_resume_pdf(stored, home_root=fx.home_root, target=fx.target, form=None, spacing_scale=1.0, count_pages=True)
    preview, _name = resume_pdf.stored_resume_pdf(stored, home_root=fx.home_root, target=fx.target, form=None, spacing_scale=1.0, images=True)
    assert {str(path.relative_to(fx.home_root)): path.read_bytes() for path in sorted(fx.home_root.rglob("*")) if path.is_file()} == before, "rendering wrote to the home"
    assert len(preview.page_images) == rendered.pages == len(PdfReader(io.BytesIO(rendered.pdf)).pages)

    rows = _rows(rendered.pdf)
    # WHAT IT GAINS: the four rules.
    assert _lines(_section(rows, "EDUCATION")) == [
        "EXAMPLE STATE UNIVERSITY | M.S., Information Systems 2010 - 2012", "EXAMPLE INSTITUTE OF TECHNOLOGY | B.Tech., Computer Science 2000 - 2004",
    ]
    experience = _lines(_section(rows, "EXPERIENCE"))
    assert EARLIER_HEADING.upper() not in experience and experience[-3:] == [role.replace(" | ", " ") for role in spacing_fixture.EARLIER[:3]]
    names, skill_rows = _skills(rendered.pdf)
    assert names == [name.strip() for name in spacing_fixture.SKILLS.split(",")] and len({(row[0], row[1]) for row in skill_rows}) <= 4
    assert {row[2] for row in _section(rows, "SUMMARY")} == {LEFT}
    # WHAT IT LOSES: the three words of the block's title, and nothing else.  Every line it stores is printed as stored.
    printed = _printed(rendered.pdf)
    stored_lines = [
        line for section in stored.result.sections for line in (*section.lines, *(part for entry in section.entries for part in (*entry.heading, *entry.bullets)))
    ]
    assert len(stored_lines) > 40
    for line in stored_lines:
        for part in shown_text(line).split(" | "):
            assert _squashed(part) in printed, f"a stored line is not printed as it is stored: {shown_text(line)!r}"
    assert _squashed(EARLIER_HEADING) not in printed
    # And the PDF prints no word the resume does not store (the section titles aside).
    titles = {"summary", "experience", "projects", "education", "skills"}
    words = {word for row in rows for word in re.findall(r"[a-z0-9.+#&/-]+", row[4].casefold())} - titles - {"•", "|"}
    own = {word for line in stored_lines for word in re.findall(r"[a-z0-9.+#&/-]+", shown_text(line).casefold())}
    assert words <= own, f"the PDF prints words the resume does not store: {sorted(words - own)}"
    assert json.loads(Path(stored.stored_path).read_text(encoding="utf-8"))["markdown"] == stored.markdown
