"""0.3.3 (gigai-0rs): the TAIL of a resume no longer moves whole to a new page.

Before: the last bullet before the short sections, the Skills block and Education's degrees were ONE sticky chain
(the orphan rule of 0.1.11.5, so that the last page always held a bullet).  When the chain (some 215pt at 0.7)
missed the room left on a page by a few points, the whole of it started a new page and the page before the last was
left up to a third empty.  Now (``resume.typ``, PAGE RULES): a bullet is never kept with what follows it, the Skills'
lines break where the page ends, and a short block (Education's degrees, the roles shown by their heading alone) keeps
only what spares a line alone on a page: its title with its first line, its last two lines together (so two degrees
are one piece).  Pinned here on the shape of the report (a Summary, one long role, two or three projects, the Skills,
Education of two degrees), over a sweep of bullet counts so the chain lands just over the edge at several spacings:

- (a) THE PAGE BEFORE A BREAKABLE BLOCK IS FULL: when the next page starts with a bullet or a plain line (a block
  that could have ended the page before), the page before it is short by at most ``FREE_MAX`` points;
- (b) THE PAGE BEFORE A KEPT PIECE IS SHORT BY THAT PIECE AT MOST: when the next page starts with a section title or
  an entry heading, the page before it is short by no more than the piece the rules keep whole (the title with its
  first block, a heading with its role line and first bullet, Education with its two degrees) and its gap;
- (c) AN ENTRY'S HEADING STAYS WITH ITS FIRST BULLET (the rule that matters, kept): the heading and its role line are
  on the page of the entry's first bullet, and no page ends with a heading or a section title.

Synthetic only: an invented person on reserved domains (``tests/support/real_shaped_resume_fixture.py``).
"""

from __future__ import annotations

import functools
import io
from datetime import datetime, timezone

import pytest
from pypdf import PdfReader

from gigai.scout import resume_pdf
from gigai.scout.resume_display import form_header, parse_header_form
from tests.support import real_shaped_resume_fixture as real

STAMP = datetime(2026, 10, 7, tzinfo=timezone.utc)
FORM = {
    "name": "Zora Quillfeather", "email": "zora.quillfeather@example.invalid", "phone": "+1 (555) 010-0142", "location": "Nowhere Springs, Colorado",
    "work_authorization": "VISA: H1B", "linkedin": "linkedin.com/in/zora-quillfeather-staff-engineer",
    "links": [{"label": "GitHub", "url": "github.com/zora-quillfeather"}],
}
HEADER = form_header(parse_header_form(FORM))
LEFT = 65.0  # the page's left margin, in points
MARGIN_Y = 50.0
PAGE_HEIGHT = 792.0  # US Letter
SECTION_TITLE, HEADING, BODY = 7.1, 10.7, 9.5  # type sizes
#: Inter's ascender + descender, as a share of the type size (``asc``, ``desc`` in resume.typ).
EXTENT = 0.96875 + 0.2412109375
#: The spacings swept: the report's (0.7, 0.75), the default and two looser ones.
SPACINGS = (0.7, 0.75, 0.85, 1.0, 1.2)
#: (bullets under the one role, projects, bullets under the last project): a sweep, so the tail lands just over the
#: edge of a page for several of them (before this change: at every spacing above, on both shapes).
SHAPES = tuple((bullets, projects, last) for bullets in range(6, 21) for projects, last in ((3, 2), (2, 1)))
#: (a) the most a page may be left short when the next page starts with a block that could have ended it: a bullet of
#: four printed lines with its gap (57.2 + 2.4pt at 1.4), plus the measure's slack.
FREE_MAX = 65.0
#: (b) the slack on a kept piece's height: the gap above it collapses with the gap below the block before it.
PIECE_SLACK = 6.0
#: A printed line: ``(page, baseline y, kind, section, text)``; kind: title, heading, bullet, wrap (a bullet's further
#: line), other (a plain line: the Skills, a degree's second line).
Line = tuple[int, float, str, str, str]


def markdown(bullets: int, projects: int, last: int) -> str:
    """The report's shape: a Summary, ONE long role of ``bullets`` bullets, ``projects`` projects (the last with ``last``
    bullets, the others one), the Skills, then Education."""

    out = ["## Summary", "", real.SUMMARY, "", "## Experience", ""]
    company, title, dates, _count = real.ROLES[0]
    out += [f"### {company}", f"{title} | {dates}", "", *(f"- {text}" for _lines, text in real.BULLETS[:bullets]), "", "## Projects", ""]
    for number, (name, tech, text) in enumerate(real.PROJECTS[:projects]):
        points = [text, *real.PROJECT_BULLETS[1][: last - 1]] if number == projects - 1 else [text]
        out += [f"### {name}", tech, "", *(f"- {point}" for point in points), ""]
    out += ["## Skills", "", f"- {real.SKILLS}", "", "## Education", ""]
    for school, degree, years in real.DEGREES:
        out += [f"### {school}", f"{degree} | {years}", ""]
    return "\n".join(out)


@functools.lru_cache(maxsize=None)
def _lines(shape: tuple[int, int, int], spacing: float) -> tuple[Line, ...]:
    """The printed lines of the body (from the first section title on), in reading order."""

    pdf = resume_pdf.render_markdown_pdf(markdown(*shape), HEADER, timestamp=STAMP, spacing_scale=spacing, auto_fit=False).pdf
    grouped: dict[tuple[int, float], list[tuple[float, float, str]]] = {}
    for number, page in enumerate(PdfReader(io.BytesIO(pdf)).pages):
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
    """The body's line box at ``spacing`` (``lh-body`` in resume.typ; the floor is 11.5pt: 0.3.5 raised it to 12.5pt,
    0.3.6, gigai-3jj, put it back)."""

    return 14.3 if spacing >= 1.0 else max(11.5, 14.3 - (14.3 - 11.5) * (1.0 - spacing) / 0.3)


def _gap_unit(spacing: float) -> float:
    """The spacing unit ``s`` at ``spacing``, in points (``gap-scale`` in resume.typ: below 1.0 it shrinks twice as fast;
    0.3.5 dropped that, 0.3.6, gigai-3jj, put it back)."""

    return 1.71 * (spacing if spacing >= 1.0 else 1.0 - 2.0 * (1.0 - spacing))


def _box_bottom(y: float, spacing: float) -> float:
    """Where a body line's box ends under its baseline ``y`` (the line box less the glyphs' extent, halved)."""

    return y - (_body_line(spacing) - EXTENT * BODY) / 2


def _free(lines: tuple[Line, ...], page: int, spacing: float) -> float:
    """The points left empty between the last line's box on ``page`` and the bottom margin."""

    return _box_bottom(min(row[1] for row in lines if row[0] == page), spacing) - MARGIN_Y


def _piece(lines: tuple[Line, ...], start: int) -> list[Line]:
    """The lines the rules keep whole from ``lines[start]`` (a section title or an entry heading) on, on its page:
    Education with its degrees; the Skills' title with its first two lines; otherwise through the first bullet."""

    page, _y, _kind, section, text = lines[start]
    rows = [row for row in lines[start:] if row[0] == page]
    if text == "EDUCATION":
        return [row for row in rows if row[3] == section]
    if text == "SKILLS":
        return rows[:3]
    for index, row in enumerate(rows):
        if row[2] == "bullet" and (index + 1 == len(rows) or rows[index + 1][2] != "wrap"):
            return rows[: index + 1]
    return rows


def _bound(lines: tuple[Line, ...], start: int, spacing: float) -> float:
    """(b) The most the page before ``lines[start]`` may be left short: the kept piece's height with the gap above it."""

    piece = _piece(lines, start)
    gap = (15 if piece[0][2] == "title" else 7) * _gap_unit(spacing)
    return PAGE_HEIGHT - MARGIN_Y - _box_bottom(piece[-1][1], spacing) + gap + PIECE_SLACK


@pytest.mark.parametrize("spacing", SPACINGS)
def test_a_page_before_the_last_is_never_left_short_by_more_than_one_kept_piece(spacing: float) -> None:
    over_edge = 0
    for shape in SHAPES:
        lines = _lines(shape, spacing)
        pages = sorted({row[0] for row in lines})
        for page in pages[:-1]:
            start = next(index for index, row in enumerate(lines) if row[0] == page + 1)
            free = _free(lines, page, spacing)
            where = f"{shape} at {spacing:.2f}: page {page + 1} ends {free:.0f}pt above the margin"
            if lines[start][2] in ("title", "heading"):
                # (b) the next page starts with a piece the rules keep whole: the page before it is short by that at most.
                assert free <= _bound(lines, start, spacing), f"{where}; page {page + 2} starts with {lines[start][4][:40]!r}"
            else:
                # (a) the next page starts with a block that could have ended the page before: that page is full.
                assert free <= FREE_MAX, f"{where}; page {page + 2} starts with {lines[start][4][:40]!r}"
                over_edge += 1
    # The sweep reaches the edge: on some shapes a bullet or a plain line starts a page.
    assert over_edge >= 3, over_edge


@pytest.mark.parametrize("spacing", SPACINGS)
def test_a_heading_is_on_the_page_of_its_first_bullet(spacing: float) -> None:
    for shape in SHAPES:
        lines = _lines(shape, spacing)
        where = f"{shape} at {spacing:.2f}"
        headings = 0
        for index, (page, _y, kind, section, text) in enumerate(lines):
            following = lines[index + 1] if index + 1 < len(lines) else None
            if kind == "title":
                assert following is not None and following[0] == page, f"{where}: the title {text} ends page {page + 1}"
            elif kind == "heading" and section in ("EXPERIENCE", "PROJECTS"):
                assert following is not None and following[0] == page, f"{where}: the heading {text[:40]} ends page {page + 1}"
                first = next(row for row in lines[index + 1 :] if row[2] in ("bullet", "heading", "title"))
                assert first[2] == "bullet" and first[0] == page, f"{where}: {text[:40]} is on page {page + 1}, its first bullet on page {first[0] + 1}"
                headings += 1
        assert headings == 1 + shape[1]
        for page in sorted({row[0] for row in lines})[:-1]:
            last = [row for row in lines if row[0] == page][-1]
            assert last[2] not in ("heading", "title"), f"{where}: page {page + 1} ends with {last[4][:40]}"
