"""0.1.11.5 (PB5): where a page BREAKS, on the END outcome (the PDF pypdf reads back).

Before: a role of two or three bullets was one piece (its first bullet stayed with the second, its second-to-last with
the last), and the last entry before the Skills and Education moved with them as one group.  A block that did not fit
left the page, so a page ended with a wide empty band: on the fixture below 15 body lines at the foot of a page in the
middle and 21 on the page before the last, and about 2.2 pages of content on 3 pages at 0.85.  Pinned here, on
synthetic resumes of a real pick's size (``tests/support/real_shaped_resume_fixture.py``, ``split=True``: a role of
two bullets right after the longest role, two projects of three and two bullets under a two-line line of technologies):

- (a) AN ENTRY'S HEADING STAYS WITH ITS FIRST BULLET: the employer and its role line, a project's title and its
  technologies, are on the page of the entry's first bullet, at every slider position; a section title is on the page
  of the first line under it;
- (b) A BULLET IS ONE PIECE: no bullet's lines are on two pages (a bullet of up to four printed lines; a longer one
  may break, two lines or more on each side, as it always could);
- (c) THE EMPTY BAND at the foot of a page is small: under ``BAND_LINES`` body lines on every page but the last two,
  under ``TAIL_BAND_LINES`` on the page before the last (the tail: one bullet, the Skills and Education);
- (d) THE LAST PAGE HOLDS A BULLET (the orphan rule of PE, kept): never Education, the Skills or the earlier roles alone;
- AN ENTRY SPLITS: a role's bullets are on two pages where the page ends inside it, and ONE bullet may end or start a page;
- THE AUTOMATIC SPACING opens looser, and the preview's pictures are the PDF's pages (one render path).

Synthetic only: an invented person on reserved domains.
"""

from __future__ import annotations

import functools
import io
from datetime import datetime, timezone

import pytest
from pypdf import PdfReader

from gigai.scout import resume_pdf
from gigai.scout.resume_display import SPACING_MAX, SPACING_MIN, form_header, parse_header_form
from gigai.scout.tailored_resume import EARLIER_HEADING

from tests.support import real_shaped_resume_fixture as real

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
LEFT = 65.0  # the page's left margin, in points
MARGIN_Y = 50.0
SECTION_TITLE, HEADING, BODY = 7.1, 10.7, 9.5  # type sizes
#: The resumes: the fixture of PE, the shape that wasted the foot of a page, and what may end it.
FIXTURES: dict[str, dict[str, object]] = {
    "real": {},
    "split": {"split": True},
    "split, 16 bullets": {"split": True, "bullets": 16},
    "split, three earlier roles": {"split": True, "earlier": 3},
    "split, five earlier roles": {"split": True, "earlier": 5},
    "split, the Skills last": {"split": True, "order": ("projects", "education", "skills")},
    "split, the earlier roles last": {"split": True, "order": ()},
    "real, the earlier roles last": {"order": ()},
}
#: A printed line: ``(page, baseline y, kind, section, text)``; kind: title, heading, bullet, wrap (a bullet's further line), other.
Line = tuple[int, float, str, str, str]


@functools.lru_cache(maxsize=None)
def _pdf(name: str, spacing: float) -> bytes:
    return resume_pdf.render_markdown_pdf(real.resume(**FIXTURES[name]), HEADER, timestamp=STAMP, spacing_scale=spacing, auto_fit=False).pdf  # type: ignore[arg-type]


@functools.lru_cache(maxsize=None)
def _lines(name: str, spacing: float) -> tuple[Line, ...]:
    """The printed lines of the body (from the first section title on), in reading order."""

    grouped: dict[tuple[int, float], list[tuple[float, float, str]]] = {}
    for number, page in enumerate(PdfReader(io.BytesIO(_pdf(name, spacing))).pages):
        def seen(text: str, cm: list[float], _tm: list[float], _font: object, size: float, number: int = number) -> None:
            if text.strip():
                grouped.setdefault((number, round(cm[5], 1)), []).append((round(cm[4], 1), round(size, 1), text.strip()))

        page.extract_text(visitor_text=seen)
    out: list[Line] = []
    section = ""
    for (page, y), runs in sorted(grouped.items(), key=lambda item: (item[0][0], -item[0][1])):
        text = " ".join(run[2] for run in runs)
        if any(size == SECTION_TITLE for _x, size, _text in runs):
            kind, section = "title", text
        elif any(size == HEADING for _x, size, _text in runs):
            kind = "heading"
        elif any(run[2].startswith("•") for run in runs):
            kind = "bullet"
        elif all(x > LEFT + 10 and size <= BODY for x, size, _text in runs):
            kind = "wrap"
        else:
            kind = "other"
        if section:
            out.append((page, y, kind, section, text))
    return tuple(out)


def _body_line(spacing: float) -> float:
    """The body's line box at ``spacing`` (``lh-body`` in resume.typ)."""

    return 14.3 if spacing >= 1.0 else max(11.5, 14.3 - (14.3 - 11.5) * (1.0 - spacing) / 0.3)


def _bands(name: str, spacing: float) -> list[float]:
    """The empty band at the foot of each page, in body lines: from the last line's box down to the bottom margin."""

    line = _body_line(spacing)
    lines = _lines(name, spacing)
    pages = sorted({row[0] for row in lines})
    return [(min(row[1] for row in lines if row[0] == page) - (line - (0.96875 - 0.2412109375) * BODY) / 2 - MARGIN_Y) / line for page in pages]


def test_the_split_fixture_has_the_shape_that_wasted_a_page() -> None:
    lines = _lines("split", 1.0)
    bullets = [0]
    for _page, _y, kind, _section, _text in lines:
        if kind == "bullet":
            bullets.append(1)
        elif kind == "wrap":
            bullets[-1] += 1
    experience = [row for row in lines if row[3] == "EXPERIENCE"]
    counts: list[int] = []
    for row in experience:
        if row[2] == "heading" and row[4] != EARLIER_HEADING.upper():
            counts.append(0)
        elif row[2] == "bullet":
            counts[-1] += 1
    # Six employers, 20 bullets of two or three printed lines, a role of two bullets right after the longest.
    assert tuple(counts) == real.SPLIT_COUNTS == (6, 2, 4, 3, 3, 2) and sum(counts) == 20
    assert set(bullets[1:]) == {2, 3}, bullets
    # Two projects of three and two bullets, each under its title and TWO lines of technologies.
    projects = [row for row in lines if row[3] == "PROJECTS"]
    kinds = [row[2] for row in projects if row[2] != "wrap"]
    assert kinds == ["title", "heading", "other", "other", "bullet", "bullet", "bullet", "heading", "other", "other", "bullet", "bullet"], kinds
    assert 40 <= len(real.SKILLS.split(", ")) <= 80 and [row[4] for row in lines if row[2] == "title"] == ["SUMMARY", "EXPERIENCE", "PROJECTS", "SKILLS", "EDUCATION"]


# --- (a) a heading stays with its first bullet, (b) a bullet is one piece ------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_a_heading_is_on_the_page_of_its_first_bullet_and_no_bullet_is_split(name: str) -> None:
    for spacing in SLIDER:
        lines = _lines(name, spacing)
        where = f"{name} at {spacing:.2f}"
        headings = 0
        for index, (page, _y, kind, section, text) in enumerate(lines):
            following = lines[index + 1] if index + 1 < len(lines) else None
            if kind == "title":
                # (5) a section title is on the page of the first line under it.
                assert following is not None and following[0] == page, f"{where}: the title {text} ends page {page + 1}"
            elif kind == "heading" and section in ("EXPERIENCE", "PROJECTS"):
                # (1), (4) the heading, every line under it, and the first bullet are on one page.  The title of the
                # roles shown by their heading alone has no bullet: it is on the page of its first role.
                assert following is not None and following[0] == page, f"{where}: the heading {text[:40]} ends page {page + 1}"
                if text != EARLIER_HEADING.upper():
                    first = next(row for row in lines[index + 1 :] if row[2] in ("bullet", "heading", "title"))
                    assert first[2] == "bullet" and first[0] == page, f"{where}: {text[:40]} is on page {page + 1}, its first bullet on page {first[0] + 1}"
                    headings += 1
            elif kind == "wrap" and section in ("EXPERIENCE", "PROJECTS"):
                # (2) a bullet's further line is on the page of the line before it.
                assert lines[index - 1][2] in ("bullet", "wrap") and lines[index - 1][0] == page, f"{where}: a bullet is split before {text[:40]!r}"
        assert headings >= 6
        # No page ends with a heading or a section title.
        for page in sorted({row[0] for row in lines})[:-1]:
            last = [row for row in lines if row[0] == page][-1]
            assert last[2] not in ("heading", "title"), f"{where}: page {page + 1} ends with {last[4][:40]}"


def test_an_entry_splits_across_a_page_break_and_one_bullet_may_end_or_start_a_page() -> None:
    """(3) Before, an entry's first two and last two bullets stayed together: a role of two or three bullets never split."""

    split_entries = 0
    alone: set[str] = set()
    for name in FIXTURES:
        for spacing in SLIDER:
            lines = [row for row in _lines(name, spacing) if row[3] in ("EXPERIENCE", "PROJECTS")]
            entries: list[list[Line]] = []
            for row in lines:
                if row[2] in ("heading", "title"):
                    entries.append([])
                elif row[2] == "bullet" and entries:
                    entries[-1].append(row)
            for bullets in entries:
                pages = [row[0] for row in bullets]
                if len(set(pages)) == 2:
                    split_entries += 1
                    if pages.count(pages[0]) == 1:
                        alone.add("one bullet ends the page")
                    if pages.count(pages[-1]) == 1:
                        alone.add("one bullet starts the page")
                    if len(bullets) <= 3:
                        alone.add("an entry of two or three bullets splits")
    assert split_entries >= 20, split_entries
    assert alone == {"one bullet ends the page", "one bullet starts the page", "an entry of two or three bullets splits"}, alone


# --- (c) the empty band at the foot of a page ----------------------------------------------------------------------------

#: The most a page break may leave empty before the last two pages, in body lines: an entry's heading, its role line and
#: a three-line bullet with their gaps do not fit (rules 1 and 2), so the page ends before them.  Reached: 6.0 (the PE
#: fixture at 1.10).  BEFORE this change: 14.8 (a whole role of three bullets left the page).
BAND_LINES = 7.0
#: The same for the page before the last: the tail (ONE bullet, the Skills and Education, kept together so that the last
#: page holds a bullet) does not fit and starts the last page.  Reached: 14.3 (16 bullets at 0.85).  BEFORE: 21.2 (the
#: last project, whole, moved with the tail).
TAIL_BAND_LINES = 15.0


def test_the_empty_band_at_the_foot_of_a_page_is_small() -> None:
    worst, worst_tail = 0.0, 0.0
    for name in FIXTURES:
        for spacing in SLIDER:
            bands = _bands(name, spacing)
            for page, band in enumerate(bands[:-1]):
                tail = page == len(bands) - 2
                assert band < (TAIL_BAND_LINES if tail else BAND_LINES), f"{name} at {spacing:.2f}: page {page + 1} of {len(bands)} ends {band:.1f} lines above the margin"
                worst, worst_tail = (worst, max(worst_tail, band)) if tail else (max(worst, band), worst_tail)
    # The bounds are the ones reached, not looser.
    assert BAND_LINES - 1.5 < worst and TAIL_BAND_LINES - 1.5 < worst_tail, (worst, worst_tail)


# --- (d) the last page holds a bullet ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_page_holds_a_bullet_and_the_tail_is_in_one_piece(name: str) -> None:
    for spacing in SLIDER:
        lines = _lines(name, spacing)
        for page in sorted({row[0] for row in lines}):
            assert any(row[2] == "bullet" for row in lines if row[0] == page), f"{name} at {spacing:.2f}: page {page + 1} holds no bullet"
        # The short sections that end the resume are on one page, with the last bullet before them.
        ending = [row for row in lines if row[3] in ("SKILLS", "EDUCATION")]
        if ending:
            last_bullet = [row for row in lines if row[2] == "bullet"][-1]
            assert {row[0] for row in ending} == {last_bullet[0]}, f"{name} at {spacing:.2f}: the Skills and Education are not on the last bullet's page"


def test_the_tail_takes_one_bullet_with_it_and_not_the_whole_project() -> None:
    """Before, the last project (title, technologies, every bullet of up to three) moved with the Skills and Education."""

    found = False
    for name in ("split", "split, 16 bullets", "split, the Skills last"):
        for spacing in SLIDER:
            lines = _lines(name, spacing)
            last_page = max(row[0] for row in lines)
            project = [row for row in lines if row[3] == "PROJECTS" and row[2] in ("heading", "bullet")]
            final = project[[row[2] for row in project].index("heading", 1) :]  # the last project: its title and its two bullets
            pages = [row[0] for row in final]
            if pages == [last_page - 1, last_page - 1, last_page]:
                found = True
    assert found, "at no spacing does the last project end one page and its last bullet start the next with the Skills and Education"


# --- the pages and the automatic spacing ---------------------------------------------------------------------------------

#: (the automatic spacing, its pages), measured 2026-10-07.  BEFORE this change: the PE fixture 0.85, split 0.70,
#: split with 16 bullets 0.80, three earlier roles 0.70, five 0.70, the Skills last 0.70, the earlier roles last 1.05 / 1.40.
AUTO_FIT = {
    "real": (0.85, 2),
    "split": (0.75, 2),
    "split, 16 bullets": (0.8, 2),
    "split, three earlier roles": (0.75, 2),
    "split, five earlier roles": (0.7, 2),
    "split, the Skills last": (0.75, 2),
    "split, the earlier roles last": (1.4, 2),
    "real, the earlier roles last": (1.4, 2),
}


def test_the_automatic_spacing_is_as_loose_as_before_or_looser() -> None:
    measured = {}
    for name, keywords in FIXTURES.items():
        fitted = resume_pdf.render_markdown_pdf(real.resume(**keywords), HEADER, timestamp=STAMP)  # type: ignore[arg-type]
        measured[name] = (fitted.spacing_scale, fitted.pages)
    assert measured == AUTO_FIT


def test_the_split_fixture_is_on_fewer_pages_where_a_page_break_wasted_room() -> None:
    # BEFORE this change: 3 pages at 0.75 (now 2), and 4 pages from 1.10 up (now 3).
    pages = {spacing: len(PdfReader(io.BytesIO(_pdf("split", spacing))).pages) for spacing in (0.7, 0.75, 0.85, 1.0, 1.1, 1.4)}
    assert pages == {0.7: 2, 0.75: 2, 0.85: 3, 1.0: 3, 1.1: 3, 1.4: 3}


def test_the_previews_pictures_are_the_pdfs_pages() -> None:
    """ONE render path: the same template, data and spacing give the preview's pictures and the PDF."""

    _name, sections = resume_pdf.parse_resume_markdown(real.resume(split=True))
    for spacing in (0.7, 0.75, 0.85, 1.0, 1.4):
        pictures = resume_pdf._render(sections, HEADER, company="", timestamp=STAMP, spacing_scale=spacing, auto_fit=False, images=True)
        printed = resume_pdf._render(sections, HEADER, company="", timestamp=STAMP, spacing_scale=spacing, auto_fit=False, count_pages=True)
        assert len(pictures.page_images) == printed.pages == len(PdfReader(io.BytesIO(printed.pdf)).pages)


def _long(words: int, bullets: int, spacing: float) -> tuple[int, list[list[str]]]:
    """``(pages, the kinds of each page's lines)`` of one role of ``bullets`` bullets of ``words`` words each."""

    text = " ".join(f"point{number:02d}" for number in range(words))
    markdown = "## Experience\n\n### Larkspur Table Systems\nStaff Engineer | 2022 - Present\n\n" + "\n".join(f"- {text}" for _ in range(bullets)) + "\n"
    rendered = resume_pdf.render_markdown_pdf(markdown, HEADER, timestamp=STAMP, spacing_scale=spacing, auto_fit=False)
    pages: list[list[str]] = []
    for page in PdfReader(io.BytesIO(rendered.pdf)).pages:
        lines: dict[float, list[tuple[float, str]]] = {}
        page.extract_text(visitor_text=lambda text, cm, _tm, _font, _size: lines.setdefault(round(cm[5], 1), []).append((cm[4], text.strip())) if text.strip() else None)
        pages.append(["bullet" if any(run[1].startswith("•") for run in runs) else "wrap" if all(run[0] > LEFT + 10 for run in runs) else "other" for _y, runs in sorted(lines.items(), reverse=True)])
    return rendered.pages or 0, pages


def test_a_bullet_of_four_lines_is_one_piece_and_a_longer_one_may_break() -> None:
    """A page never breaks inside a bullet of up to four printed lines (``whole-lines`` in resume.typ).  A longer one
    breaks as it always could, two lines or more on each side: kept whole, a point of six lines left up to six lines
    empty at the foot of a page (found on the job page's fixture of such points: 3 pages at 0.85 where it had 2)."""

    _pages, four = _long(46, 1, 1.0)
    assert four[0].count("wrap") == 3, "the fixture's bullet is not four printed lines"
    broken = 0
    for bullets in range(11, 16):
        for spacing in (0.7, 0.85, 1.0, 1.2):
            _count, pages = _long(46, bullets, spacing)
            assert all(page[0] != "wrap" for page in pages[1:]), f"{bullets} four-line bullets at {spacing}: a page break falls inside one"
            _count, pages = _long(84, bullets - 4, spacing)  # seven printed lines a bullet
            for before, after in zip(pages, pages[1:]):
                if after[0] == "wrap":
                    broken += 1
                    # Two lines or more of the bullet on each side of the break.
                    assert after[:2] == ["wrap", "wrap"] and before[-2:] in (["bullet", "wrap"], ["wrap", "wrap"]), (bullets, spacing, before[-3:], after[:3])
    assert broken >= 3, "no page break fell inside a seven-line bullet"


def test_a_bullet_taller_than_a_page_is_still_printed_in_full() -> None:
    words = " ".join(f"word{number}" for number in range(1200))
    markdown = f"## Experience\n\n### Larkspur Table Systems\nStaff Engineer | 2022 - Present\n\n- {words}\n"
    rendered = resume_pdf.render_markdown_pdf(markdown, HEADER, timestamp=STAMP, spacing_scale=1.0, auto_fit=False)
    text = " ".join(page.extract_text() for page in PdfReader(io.BytesIO(rendered.pdf)).pages)
    assert rendered.pages >= 2 and "word0 " in text and "word1199" in text.replace("\n", " ")
