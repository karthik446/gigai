"""Render a stored tailored resume to PDF with Typst, locally and deterministically.

0110-046: GigAI stores no name or contact details.  The header (name, contact items) comes from
the Generate PDF form for ONE render (``resume_display.form_header``), with the saved per-profile
title; without the form (an agent, the CLI) the PDF has no header: a blank block of the same height
is reserved, so the pages are the ones the finished PDF will have.  Never from ``result.header``
or the markdown.  Body lines go through the same ``shown_text`` mapping as the markdown renderer
(a copy -- the model's or a no-loss fallback -- loses its own markers).  ``typst`` is imported
lazily so CLI startup never loads its native library.

0110-032: ``render_markdown_pdf`` renders resume markdown in GigAI's format (what ``render_markdown``
writes: ``## Section``, ``### entry heading``, ``- `` bullets) through the SAME template, header and
auto fit; ``stored_resume_pdf`` is the one stored-resume path the API and ``gigai scout resume pdf
--tailored`` share.  Nothing here calls a model, the network, or a logger, and nothing here writes:
the form's values live only in this call's arguments.  A PDF's file name is
``<company>-<role>-<YYYY-MM-DD>.pdf`` (``pdf_file_name``), never the user's name.
"""

from __future__ import annotations

import itertools
import json
import re
from contextlib import ExitStack
from datetime import date, datetime, timezone
from importlib import resources
from pathlib import Path
from collections.abc import Callable
from urllib.parse import quote

from dataclasses import dataclass

from gigai.scout.resume_display import (
    SPACING_DEFAULT, SPACING_MAX, SPACING_MIN, ContactItem, DisplaySettings, PdfHeader, form_header, load_display, profile_title, valid_spacing,
)
from gigai.scout.resumes_folder import file_name
from gigai.scout.tailored_resume import (
    EARLIER_HEADING,
    ENTRY_SECTIONS,
    LENGTH_RULE,
    MAX_EARLIER_LINES,
    MAX_HEADING_LINES,
    SECTION_HEADINGS,
    _LEADING_MARKERS,
    TailoredLine,
    TailoredResume,
    TailorResponse,
    _display,
    heading_only,
    heading_only_line,
    is_earlier_heading,
    read_tailored_resume,
    replaced_line,
    shown_text,
    tailored_resume_path,
    tailored_resume_write_lock,
)
from gigai.scout.find_jobs.discovery.storage import atomic_write

def pdf_file_name(company: str, role: str, day: date) -> str:
    """``<company>-<role>-<YYYY-MM-DD>.pdf`` (lowercase ASCII, hyphens, each part length-capped);
    ``resume-<YYYY-MM-DD>.pdf`` when neither is known.  Never carries the user's name (0110-046).
    The one naming rule is the resumes folder's (``resumes_folder.file_name``)."""

    return file_name(company, role, day, ".pdf")


_YEAR = re.compile(r"\b(?:19|20)\d\d\b|\bPresent\b", re.IGNORECASE)


def _flat(text: str) -> str:
    """One physical line: hard wraps (newlines, runs of blanks) become single spaces."""
    return " ".join(text.split())


def _source_is_bullet(line: TailoredLine) -> bool:
    """True when the resume line this text came from was a real bullet (a marker other than a heading).

    An edited line (``kind: custom``) keeps the shape of the line it replaced."""
    line = replaced_line(line)
    source = line.text if line.kind == "copy" or not line.refs else line.refs[0].text
    return _LEADING_MARKERS.match(source.lstrip()) is not None and not source.lstrip().startswith("#")


def _covered(line: TailoredLine) -> set[int]:
    """The resume line numbers a copy line prints: what it cites plus the lines that wrap on from it."""
    return {n for ref in line.refs if ref.kind == "resume" for n in (ref.line, *ref.continued_lines)}


def _paragraphs(lines: tuple[TailoredLine, ...]) -> list[dict[str, object]]:
    """Real bullets stay bullets; consecutive non-bullet lines (a hard-wrapped paragraph) join into one.

    A copy line is stored EXPANDED to its wrapped continuation lines (``tailored_resume._resume_ref``), so a
    hard-wrapped paragraph copied line by line stores line 1 = 1..n, line 2 = 2..n, ...; a copy whose first
    resume line an earlier copy already printed is skipped (0110-015), so no text is ever printed twice.
    """
    out: list[dict[str, object]] = []
    printed: set[int] = set()
    for line in lines:
        text = _flat(shown_text(line))
        if not text:
            continue
        if line.kind == "copy" and line.refs and line.refs[0].line in printed:
            continue
        if replaced_line(line).kind == "copy":
            printed |= _covered(replaced_line(line))
        if _source_is_bullet(line):
            out.append({"text": text, "bullet": True})
        elif out and not out[-1]["bullet"]:
            out[-1]["text"] = f"{out[-1]['text']} {text}"
        else:
            out.append({"text": text, "bullet": False})
    return out


_TAG_SEPARATORS = re.compile(r"\s*[·;]\s*|\s*,\s*(?![^()]*\))")


def _tags(paragraphs: list[dict[str, object]]) -> list[str]:
    """Skills as unique chips: split on middle dots, semicolons and commas outside parentheses (a slash group
    such as ``Docker/Kubernetes`` stays one tag, as the resume writes it); order kept, case-insensitive dedupe."""
    seen: set[str] = set()
    tags: list[str] = []
    for paragraph in paragraphs:
        for part in _TAG_SEPARATORS.split(str(paragraph["text"])):
            tag = part.strip().rstrip(".").strip()
            if tag and tag.casefold() not in seen:
                seen.add(tag.casefold())
                tags.append(tag)
    return tags


def _heading_line(text: str) -> dict[str, str]:
    """A role line \"Title | Jun 2022 - Present\" splits so the dates can sit at the right margin."""
    shown = _flat(_display(text))
    head, sep, tail = shown.rpartition(" | ")
    if sep and _YEAR.search(tail):
        return {"text": head, "dates": tail}
    return {"text": shown, "dates": ""}


def _body(result: TailoredResume) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    for section in result.sections:
        if section.is_empty():
            continue
        entries: list[dict[str, object]] = []
        lines: list[dict[str, object]] = []
        if section.heading in ENTRY_SECTIONS:
            earlier = heading_only(section)
            for entry in section.entries:
                if any(entry is role for role in earlier):
                    continue
                # A copied bullet (the model's copy or a no-loss fallback, 0110-006) prints without its own "- ".
                entries.append({"heading": [_heading_line(l.text) for l in entry.heading], "bullets": [_flat(shown_text(l)) for l in entry.bullets]})
            if earlier:
                # 0.1.11.4 item 9: a role with no line shown is ONE line (title, employer | dates) under its own heading,
                # after the roles that show lines.  The same block ``render_markdown`` writes and the parser below reads.
                roles = [_heading_line(heading_only_line([l.text for l in entry.heading])) for entry in earlier]
                entries.append({"heading": [{"text": EARLIER_HEADING, "dates": ""}, *roles], "bullets": []})
        else:
            lines = _paragraphs(section.lines)
        tags = _tags(lines) if section.heading == "skills" else []
        # Organisation names print in capitals (the type scale); a project title keeps its own case (names, URLs).
        caps = section.heading in ("experience", "education")
        sections.append({"heading": section.heading.upper(), "lines": [] if tags else lines, "tags": tags, "entries": entries, "caps": caps})
    return sections


def _epoch(timestamp: datetime) -> int:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return int(timestamp.timestamp())


#: Auto fit's candidate spacing scales, loosest first (a 0.05 grid over the slider's range).
_GRID = tuple(round(SPACING_MAX - 0.05 * i, 2) for i in range(round((SPACING_MAX - SPACING_MIN) / 0.05) + 1))


def _compile(template: bytes, directory: str, data: dict[str, object], scale: float, timestamp: datetime) -> bytes:
    import typst  # lazy: a large native library

    return typst.compile(
        template, format="pdf", font_paths=[directory], ignore_system_fonts=True,
        sys_inputs={"data": json.dumps(data, ensure_ascii=False), "scale": repr(scale)}, timestamp=_epoch(timestamp),
    )


#: The preview's page pictures: pixels per inch (a US Letter page is 1360 pixels wide: sharp at the width the job
#: page shows it, on a dense screen too).
PREVIEW_PPI = 160.0


def _compile_images(template: bytes, directory: str, data: dict[str, object], scale: float) -> tuple[bytes, ...]:
    """The same document as ``_compile`` (template, data, scale), one PNG a page: what the job page's preview shows.

    A picture holds no text and no link, so the header's values are in it only as they print."""
    import typst

    pages = typst.compile(
        template, format="png", ppi=PREVIEW_PPI, font_paths=[directory], ignore_system_fonts=True,
        sys_inputs={"data": json.dumps(data, ensure_ascii=False), "scale": repr(scale)},
    )
    return tuple(pages) if isinstance(pages, list) else (pages,)


def _end(template: bytes, directory: str, data: dict[str, object], scale: float) -> tuple[int, float]:
    """(last page, fill of that page 0..1) where the content ends at ``scale``: Typst's own layout, via query."""
    import typst

    try:
        found = json.loads(typst.query(
            template, "<fit-end>", field="value", one=True, font_paths=[directory], ignore_system_fonts=True,
            sys_inputs={"data": json.dumps(data, ensure_ascii=False), "scale": repr(scale)},
        ))
    finally:
        if next(_queries) % QUERIES_PER_EVICTION == 0:
            _evict_layout_cache()
    return int(found["page"]), float(found["fill"])


#: One layout query in this many is followed by an eviction (``_evict_layout_cache``).  Measured (0.1.10.11 TY, a
#: 2-page pick from a large master, one process per rate, macOS): never = 7.0 ms a layout and +1.7 MB a layout for
#: good; every query = 9.1 ms; every 2nd = 8.4 ms; every 3rd = 8.1 ms; every 5th = 7.9 ms.  What stays is what the
#: queries of the last 10 evictions left (the engine drops what 10 evictions in a row did not use), so it is a level,
#: not a slope: the same after 2,000 layouts as after 1,000, and it grows with this number (the worst loop measured,
#: 1,000 layouts of one large document: +35 MB at 1, +70 MB at 2, +105 MB at 3).
QUERIES_PER_EVICTION = 2
#: Layout queries made by this process (``next`` of a count is one step under the interpreter's lock; a count that
#: slipped by one between two threads would only move an eviction by one query).
_queries = itertools.count(1)


def _evict_layout_cache() -> None:
    """Age out what the layout engine remembers of the queries before this one (0.1.10.11 TY).

    Typst keeps every layout's intermediate results in a cache of the whole process, and only a COMPILE ages it
    out: ``typst.query`` never does (typst 0.15.0), so each query left 1 to 3 MB behind for good: a server that
    picks from a master resume grew by 12 to 30 MB per tailoring.  Compiling an EMPTY document (about 2 ms) is
    that eviction; nothing of the resume is in it and nothing is written.  The ``_with_warnings`` form, so that
    ``typst.compile`` stays "one PDF was made"."""
    import typst

    try:
        typst.compile_with_warnings(b"", format="pdf", ignore_system_fonts=True)
    except typst.TypstError:
        pass  # the measurement stands; the next eviction, or the next PDF, ages the cache out


def fit_scale(measure: Callable[[float], tuple[int, float]]) -> float:
    """Auto fit: the LARGEST scale on the grid that still ends on the fewest pages any scale reaches.

    Content close to one page is pulled onto one page (down to 0.7x, where the body's lines are tighter too
    (0.1.11.5, ``resume.typ``); never smaller type); content that needs
    two pages spreads to the loosest spacing that does not start a third, so page 2 is as full as the range
    allows.  Pages only grow with the scale, so a binary search needs at most 6 measurements (0.7x, 1.4x, 4
    between); the result is a pure function of the content.
    """
    fewest = measure(_GRID[-1])[0]
    if measure(_GRID[0])[0] <= fewest:
        return _GRID[0]
    lo, hi = len(_GRID) - 1, 0  # _GRID[lo] fits in ``fewest`` pages; _GRID[hi] does not
    while lo - hi > 1:
        mid = (lo + hi) // 2
        if measure(_GRID[mid])[0] <= fewest:
            lo = mid
        else:
            hi = mid
    return _GRID[lo]


def clamp_scale(value: float) -> float:
    return min(SPACING_MAX, max(SPACING_MIN, float(value)))


#: How tight a render makes a SAVED spacing so the resume stays on its page limit (``_render``'s ``max_pages``) before
#: it lays the Skills out compactly; only when that is not enough does it go on down to ``SPACING_MIN``, the spacing
#: the length rule measures at.  A saved scale below it is used as saved.
FIT_FLOOR = 0.8


def over_limit_note(pages: int, max_pages: int, at_spacing: float | None = None) -> str:
    """What a person reads when the resume cannot be put on ``max_pages`` pages: plain words, ASCII (a header value).

    ``at_spacing`` (0.1.11.5): the spacing was the person's own (the job page's slider) and a tighter one exists, so
    the sentence names the slider first."""
    limit = f"{max_pages} page{'' if max_pages == 1 else 's'}"
    if at_spacing is not None and at_spacing > SPACING_MIN:
        return (
            f"This resume takes {pages} pages at spacing {at_spacing:.2f}: its limit is {limit}. "
            f"Move the spacing slider on the job's page down, or remove a point there, then generate the PDF again. Or keep it at {pages} pages."
        )
    return (
        f"This resume takes {pages} pages: it does not fit on {limit} even with the tightest spacing. "
        f"To get {limit}, remove a point or two on the job's page, then generate the PDF again. Or keep it at {pages} pages."
    )


@dataclass(frozen=True)
class RenderedPdf:
    pdf: bytes
    #: The page count; ``None`` when the caller did not ask for it (``count_pages``) and auto fit did not measure it.
    pages: int | None
    spacing_scale: float
    #: ``over_limit_note`` when the resume has a page limit and no spacing puts it there; else ``None``.
    note: str | None = None
    #: The page limit the render was held to (``_render``'s ``max_pages``); ``None`` when it had none.
    max_pages: int | None = None
    #: One PNG a page, when the caller asked for the preview's pictures (``images``) instead of the PDF; ``pdf`` is then empty.
    page_images: tuple[bytes, ...] = ()


#: The header lines under the name that a page ESTIMATE keeps room for (0.1.11.3 item 15): the ONE contact line, and
#: that line wrapped once.  The compact header is this tall at most (a line that wraps twice, or a saved title's own
#: line, is one line more: the render's own fit, ``_render``'s ``max_pages``, takes that up), so a resume an estimate
#: puts on N pages prints on N pages with the header on.  A fixed number: an estimate never reads the header's values
#: (GigAI stores none; they belong to the Generate PDF form).  The block itself is the template's (``head-line`` in
#: ``resume.typ``), the one the printed header is made of.
HEADER_RESERVE_LINES = 2
#: What a headerless PDF (an agent's, the CLI's) keeps blank under the name line.
HEADERLESS_LINES = 1


def _data(sections: list[dict[str, object]], header: PdfHeader | None, company: str, *, blank_lines: int = HEADERLESS_LINES) -> dict[str, object]:
    """What the template reads; ``header`` ``None`` reserves a blank block of the header's height (``blank_lines`` under the name).

    The header is compact (0.1.11.3 item 15): ONE contact line.  ``form_header`` orders its items; a work
    authorization given apart (``PdfHeader.work_authorization``) joins that line after its first item, and an
    item with no text is left out, so the line never holds an empty place between two separators."""
    shown = header or PdfHeader()
    contact = [c for c in shown.contact if c.text.strip()]
    if shown.work_authorization.strip():
        contact.insert(min(1, len(contact)), ContactItem(shown.work_authorization.strip(), None))
    return {
        "doc_title": " ".join(part for part in ("Resume", company.strip()) if part),
        "name": shown.name,
        "title": shown.title,
        "contact": [{"text": c.text, "url": c.url} for c in contact],
        "blank_header": header is None,
        "blank_lines": blank_lines,
        "sections": sections,
    }


def _estimate(sections: list[dict[str, object]], spacing_scale: float, header_lines: int, *, printed: bool = False) -> tuple[int, float]:
    """``(last page, fill)`` of ``sections`` under a blank header of ``header_lines`` lines: THE page estimate.

    One definition for every side that fits a resume to a page limit without a header in hand (the pick's fit,
    the tailoring's length rule, an edit's page count): the PDF's own template and layout, the header's block at
    its largest (``HEADER_RESERVE_LINES``).  No PDF is compiled.

    0.1.11.5: an estimate keeps the body's FULL line height at every spacing (the template's ``fixed_lines``), as
    it did before the spacing scale also tightened the lines: what a pick or the length rule budgets does not move,
    and the PDF, whose lines do tighten below 1.0, never takes more pages than the estimate counted.  ``printed``
    measures as the PDF prints instead."""
    root = resources.files("gigai.scout").joinpath("data", "resume")
    with ExitStack() as stack:
        directory = str(stack.enter_context(resources.as_file(root)))
        template = (Path(directory) / "resume.typ").read_bytes()
        data = _data(sections, None, "", blank_lines=header_lines)
        return _end(template, directory, data if printed else {**data, "fixed_lines": True}, clamp_scale(spacing_scale))


def fewest_pages(result: TailoredResume) -> int:
    """The fewest pages ``result`` prints on at any spacing: the count at ``SPACING_MIN``, the tightest auto fit may choose.

    The page estimate (``_estimate``: Typst's own layout, the header's block reserved at its largest), so it is the
    count the finished PDF has; no PDF is compiled.  What the tailoring's length rule reads (``tailor_length``, 0110-10-05)."""
    return _estimate(_body(result), SPACING_MIN, HEADER_RESERVE_LINES)[0]


def pages_at(result: TailoredResume, spacing_scale: float, *, header_lines: int = HEADER_RESERVE_LINES, printed: bool = False) -> int:
    """The pages ``result`` prints on at ``spacing_scale``: ``fewest_pages``'s estimate at a spacing the caller names.

    What the master resume's fit reads (0.1.10.9 master P4, ``tailor_master``): the selector's own page budget
    is 2 pages at ``master_selection.FIT_SCALE``, and a tailoring of its candidates is held to the same one.
    ``header_lines``: the header lines kept blank under the name; more than the default is a tighter budget
    (``pick.shorten_stored``).  ``printed``: as the PDF prints (``_estimate``)."""
    return _estimate(_body(result), spacing_scale, header_lines, printed=printed)[0]


def _render(
    sections: list[dict[str, object]], header: PdfHeader | None, *, company: str, timestamp: datetime, spacing_scale: float, auto_fit: bool,
    count_pages: bool = False, max_pages: int | None = None, fixed: bool = False, images: bool = False,
) -> RenderedPdf:
    """``header`` ``None``: no header, a blank block of the header's height reserved (an agent's PDF).

    ``max_pages`` (0.1.11.3) is the resume's page limit, the one its pick was fitted to: a render that would run
    past it at the saved spacing takes the loosest spacing down to ``FIT_FLOOR`` that stays on it, then the same
    with the Skills chips compact (the template's ``compact_tags``), then compact down to ``SPACING_MIN``.  That
    is what holds a resume to its pages when its estimate did not (a pick made before 0.1.11.3 item 15 measured
    with a two-line header's block; a saved spacing may be looser than the estimate's 0.9; a contact line may wrap
    twice): this is where those are absorbed.
    A resume that already fits renders exactly as before.  One that cannot be put there renders as its layout
    says, with ``RenderedPdf.note``.

    ``fixed`` (0.1.11.5): ``spacing_scale`` is the person's own choice for THIS job (the job page's slider) and is
    used as given: nothing tightens it.  A resume over its limit there still gets the compact Skills at that same
    spacing, else the note.  ``images``: the same document as one PNG a page (the job page's preview) in place of
    the PDF.  ONE definition: the preview and the PDF go through this function, so at the same header and spacing
    they are the same layout and the same pages."""
    root = resources.files("gigai.scout").joinpath("data", "resume")
    with ExitStack() as stack:
        directory = str(stack.enter_context(resources.as_file(root)))
        template = (Path(directory) / "resume.typ").read_bytes()

        def placed(data: dict[str, object], floor: float = FIT_FLOOR) -> tuple[float, int | None]:
            measured: dict[float, tuple[int, float]] = {}

            def measure(candidate: float) -> tuple[int, float]:
                if candidate not in measured:
                    measured[candidate] = _end(template, directory, data, candidate)
                return measured[candidate]

            scale = fit_scale(measure) if auto_fit else clamp_scale(spacing_scale)
            if max_pages is None:
                # Auto fit already measured the scale it chose; otherwise the count costs one layout query, only on request.
                return scale, measured[scale][0] if scale in measured else (measure(scale)[0] if count_pages else None)
            if not auto_fit and not fixed and measure(scale)[0] > max_pages:  # auto fit already ends on the fewest pages any spacing reaches
                tighter = [candidate for candidate in _GRID if floor <= candidate < scale]  # loosest first
                if tighter and measure(tighter[-1])[0] <= max_pages:
                    scale = next(candidate for candidate in tighter if measure(candidate)[0] <= max_pages)
            return scale, measure(scale)[0]

        data = _data(sections, header, company)
        scale, pages = placed(data)
        note = None
        if max_pages is not None and pages is not None and pages > max_pages:
            compact = {**data, "compact_tags": True} if any(section["tags"] for section in sections) else data
            for floor in (() if compact is data else (FIT_FLOOR,)) + (() if fixed else (SPACING_MIN,)):
                tight_scale, tight_pages = placed(compact, floor)
                if tight_pages is not None and tight_pages <= max_pages:
                    data, scale, pages = compact, tight_scale, tight_pages
                    break
            else:
                note = over_limit_note(pages, max_pages, scale if fixed else None)
        if images:
            pictures = _compile_images(template, directory, data, scale)
            return RenderedPdf(b"", len(pictures), scale, note, max_pages, pictures)
        return RenderedPdf(_compile(template, directory, data, scale, timestamp), pages, scale, note, max_pages)


def render_pdf(
    result: TailoredResume, header: PdfHeader | None, *, company: str, timestamp: datetime,
    spacing_scale: float = SPACING_DEFAULT, auto_fit: bool = True,
) -> bytes:
    """``auto_fit`` picks the spacing scale (``fit_scale``); otherwise ``spacing_scale`` is used as given."""
    return _render(_body(result), header, company=company, timestamp=timestamp, spacing_scale=spacing_scale, auto_fit=auto_fit).pdf


# --- resume markdown in, PDF out (0110-032) --------------------------------------------------

#: The markdown a caller may send: bytes of UTF-8, and lines.
MAX_MARKDOWN_BYTES = 64 * 1024
MAX_MARKDOWN_LINES = 600

_COMMENT = re.compile(r"\s*<!--.*?-->\s*\Z")
_HASHES = re.compile(r"\A(#{1,6})\s+(.*)\Z")
_BULLET = re.compile(r"\A[-*•]\s+(.*)\Z")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ResumeMarkdownError(ValueError):
    """The markdown is not a resume in GigAI's format; ``code`` is the API/CLI error code.

    Messages name a line number and the rule, never the line's text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _bad(number: int, message: str) -> None:
    raise ResumeMarkdownError("resume_markdown_invalid", f"line {number}: {message}")


def parse_resume_markdown(markdown: str) -> tuple[str, list[dict[str, object]]]:
    """``(name hint, sections)`` from resume markdown in GigAI's format; ``ResumeMarkdownError`` otherwise.

    The format is what ``tailored_resume.render_markdown`` writes:

    * ``## Summary|Experience|Skills|Education|Projects|Other`` opens a section (each at most once);
    * in Experience, Projects and Education, ``### <heading>`` opens an entry; plain lines under it
      (up to ``MAX_HEADING_LINES`` in all, e.g. ``Title | Jun 2022 - Present``) belong to the heading,
      ``- `` lines are its bullets;
    * in Experience, the entry ``### Earlier experience`` (``tailored_resume.EARLIER_HEADING``) is the block of the
      roles shown by their heading alone: each plain line under it is one role (``Title, Employer | dates``), up to
      ``MAX_EARLIER_LINES``;
    * in Summary, Skills and Other, ``- `` lines and plain lines are the content: Summary prints as
      prose (lines with no blank line between them join into one paragraph), Skills as tags, and in
      Other a ``- `` line is a bullet and a plain line a paragraph;
    * a plain line right under a bullet continues that bullet (a hard wrap);
    * a trailing ``<!-- ... -->`` comment (the source refs) is dropped, text prints literally (inline
      markdown is not interpreted), and anything above the first ``## `` is NOT printed: the PDF header
      comes from the Generate PDF form, never the markdown.  A leading ``# Name`` there is returned as
      the name hint and printed nowhere.

    The sections come back in ``_body``'s shape, so the same template prints them.
    """

    if len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES:
        raise ResumeMarkdownError("resume_markdown_too_large", f"markdown is larger than {MAX_MARKDOWN_BYTES} bytes")
    raw_lines = markdown.splitlines()
    if len(raw_lines) > MAX_MARKDOWN_LINES:
        raise ResumeMarkdownError("resume_markdown_too_large", f"markdown has more than {MAX_MARKDOWN_LINES} lines")
    name = ""
    sections: list[dict[str, object]] = []
    seen: set[str] = set()
    heading = ""  # the open section; "" above the first one
    lines: list[dict[str, object]] = []  # the open lines-section's content
    entries: list[dict[str, object]] = []  # the open entry-section's entries
    after_blank = True
    last_bullet = False  # the previous non-blank line was a bullet (or continued one)

    def close() -> None:
        if not heading or (not lines and not entries):
            return
        tags = _tags(lines) if heading == "skills" else []
        shown = [{"heading": [_heading_line(text) for text in entry["heading"]], "bullets": entry["bullets"]} for entry in entries]  # type: ignore[union-attr]
        sections.append({"heading": heading.upper(), "lines": [] if tags else lines, "tags": tags, "entries": shown, "caps": heading in ("experience", "education")})

    for number, raw in enumerate(raw_lines, 1):
        if _CONTROL.search(raw):
            _bad(number, "control characters are not allowed")
        line = raw
        while _COMMENT.search(line):
            line = _COMMENT.sub("", line)
        line = line.strip()
        if not line:
            after_blank = True
            continue
        blank_before, after_blank = after_blank, False
        hashes = _HASHES.match(line)
        if hashes and len(hashes.group(1)) == 2:
            close()
            heading = _flat(hashes.group(2)).rstrip(":").lower()
            if heading not in SECTION_HEADINGS:
                heading = ""
                _bad(number, "unknown section; use ## " + ", ## ".join(item.capitalize() for item in SECTION_HEADINGS))
            if heading in seen:
                _bad(number, f"the {heading.capitalize()} section appears twice")
            seen.add(heading)
            lines, entries, last_bullet = [], [], False
            continue
        if not heading:
            if hashes and len(hashes.group(1)) == 1 and not name:
                name = _flat(_display(hashes.group(2)))
            continue  # header lines are never printed from the markdown
        if hashes and len(hashes.group(1)) == 1:
            _bad(number, "a '# ' title belongs above the first section")
        in_entries = heading in ENTRY_SECTIONS
        if hashes:
            if not in_entries:
                _bad(number, f"'{hashes.group(1)} ' entry headings belong in Experience, Projects or Education")
            entries.append({"heading": [line], "bullets": []})
            last_bullet = False
            continue
        bullet = _BULLET.match(line)
        text = _flat(bullet.group(1) if bullet else line)
        if not text:
            continue
        if in_entries:
            if not entries:
                _bad(number, "start the entry with '### <employer, project or school>' first")
            entry = entries[-1]
            bullets: list[str] = entry["bullets"]  # type: ignore[assignment]
            if bullet:
                bullets.append(text)
            elif not bullets:
                # The block of roles shown by their heading alone (0.1.11.4 item 9) lists one role on each line under it.
                block = heading == "experience" and is_earlier_heading(entry["heading"][0])  # type: ignore[index]
                if len(entry["heading"]) >= (MAX_EARLIER_LINES + 1 if block else MAX_HEADING_LINES):  # type: ignore[arg-type]
                    _bad(number, f"an entry heading has at most {MAX_HEADING_LINES} lines; bullets start with '- '")
                entry["heading"].append(line)  # type: ignore[union-attr]
            elif last_bullet and not blank_before:
                bullets[-1] = f"{bullets[-1]} {text}"
            else:
                _bad(number, "text after an entry's bullets; start a bullet with '- ' or a new entry with '### '")
            last_bullet = bool(bullets)
            continue
        if not bullet and lines and not blank_before and (last_bullet or not lines[-1]["bullet"]):
            lines[-1]["text"] = f"{lines[-1]['text']} {text}"  # a hard wrap, or the same paragraph
            continue
        as_bullet = bullet is not None and heading != "summary"
        if heading == "summary" and bullet and lines and not blank_before:
            lines[-1]["text"] = f"{lines[-1]['text']} {text}"
        else:
            lines.append({"text": text, "bullet": as_bullet})
        last_bullet = as_bullet
    close()
    if not sections:
        raise ResumeMarkdownError(
            "resume_markdown_invalid",
            "no resume content: add at least one '## ' section (" + ", ".join(item.capitalize() for item in SECTION_HEADINGS) + ") with lines under it",
        )
    return name, sections


def printed_text(markdown: str) -> str:
    """Every text a PDF of ``markdown`` prints (its sections' lines, tags, entry headings and bullets), one per line.

    What the resumes folder checks before it keeps a headerless PDF of this markdown: the lines
    above the first ``## `` section are not printed, so they are not in it."""

    _name, sections = parse_resume_markdown(markdown)
    out: list[str] = []
    for section in sections:
        out.extend(str(line["text"]) for line in section["lines"])  # type: ignore[union-attr]
        out.extend(str(tag) for tag in section["tags"])  # type: ignore[union-attr]
        for entry in section["entries"]:  # type: ignore[union-attr]
            out.extend(" ".join(part for part in (item["text"], item["dates"]) if part) for item in entry["heading"])
            out.extend(str(bullet) for bullet in entry["bullets"])
    return "\n".join(out)


def render_markdown_pdf(
    markdown: str, header: PdfHeader | None, *, timestamp: datetime, spacing_scale: float = SPACING_DEFAULT, auto_fit: bool = True, company: str = "",
) -> RenderedPdf:
    """Resume markdown (``parse_resume_markdown``) through the tailored-resume template, header and auto fit; pages counted."""

    _name, sections = parse_resume_markdown(markdown)
    return _render(sections, header, company=company, timestamp=timestamp, spacing_scale=spacing_scale, auto_fit=auto_fit, count_pages=True)


def measure_markdown(
    markdown: str, *, spacing_scale: float = SPACING_DEFAULT, header_lines: int = HEADER_RESERVE_LINES, printed: bool = False,
) -> tuple[int, float]:
    """``(last page, fill of that page 0..1)`` where resume markdown ends at ``spacing_scale``: one layout query.

    The page estimate (``_estimate``): no header is read, the header's block is reserved at its largest
    (``header_lines``), and no PDF is compiled.  This is how the master resume's selector fits a pick to the
    page budget (0.1.10.9 master P2).  ``printed`` (0.1.11.5): where the PDF itself ends at that spacing (below
    1.0 its body lines are tighter than the estimate's: ``_estimate``)."""

    _name, sections = parse_resume_markdown(markdown)
    return _estimate(sections, spacing_scale, header_lines, printed=printed)


# --- the header and layout both entry points use ---------------------------------------------


def pdf_header(settings: DisplaySettings | None, profile_id: str | None, form: dict[str, object] | None) -> PdfHeader | None:
    """The header for one render: the form's values (``resume_display.parse_header_form``) with the saved
    per-profile title, or ``None`` (headerless) when no form was filled.  Reads nothing, writes nothing."""

    if form is None:
        return None
    return form_header(form, profile_title(settings, profile_id))


def layout(settings: DisplaySettings, spacing_scale: float | None = None, auto_fit: bool | None = None) -> tuple[float, bool]:
    """``(spacing_scale, auto_fit)``: the saved layout unless the caller gives one.

    A ``spacing_scale`` given without ``auto_fit`` turns auto fit off (the scale would otherwise be ignored).
    ``ValueError`` when the scale is outside ``SPACING_MIN``..``SPACING_MAX``.
    """

    if spacing_scale is not None and not valid_spacing(spacing_scale):
        raise ValueError(f"spacing must be between {SPACING_MIN} and {SPACING_MAX}")
    fit = auto_fit if auto_fit is not None else (False if spacing_scale is not None else settings.auto_fit)
    return (float(spacing_scale) if spacing_scale is not None else settings.spacing_scale), fit


def stored_resume_pdf(
    stored: TailorResponse, *, home_root: Path, form: dict[str, object] | None = None, spacing_scale: float | None = None,
    auto_fit: bool | None = None, count_pages: bool = False, today: date | None = None, fixed: bool = False, images: bool = False,
    target: Path | None = None,
) -> tuple[RenderedPdf, str]:
    """``(the PDF, its file name)`` for one stored tailored resume: what ``POST /api/tailored-resumes/pdf`` serves.

    ``form`` ``None``: headerless (an agent's or the CLI's render).

    THE JOB'S OWN SPACING (0.1.11.5): a spacing saved for this job (``job_spacing``: the job page's
    slider) is used, as saved, when the caller names no layout; the saved display layout (auto fit by default)
    applies only to a job with none.  ``fixed``: the ``spacing_scale`` given is such a choice (the slider's value
    in this request).  ``images``: the preview's page pictures in place of the PDF (``_render``).  ``target``
    (every product caller passes it) finds the job's spacing in THIS home's store (``stored_job_path``)."""

    profile_id = stored.resume.profile_id or "ephemeral"
    settings = load_display(home_root) or DisplaySettings()
    stamp = datetime.fromisoformat(stored.updated_at.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    fixed = fixed and spacing_scale is not None
    saved = job_spacing(stored_job_path(stored, home_root, target))
    if spacing_scale is None and auto_fit is None and saved is not None:
        spacing_scale, fixed = saved, True
    scale, fit = layout(settings, spacing_scale, False if fixed else auto_fit)
    # 0110-8-11: the file and the document are named for the company (the index's name), not its board token.
    from .find_jobs.company_names import company_display_name

    company = company_display_name(home_root, stored.job.company) or stored.job.company
    # 0.1.11.3: the PDF stays on the page limit the resume was fitted to (its length record's, else the rule's).
    length = getattr(stored.result, "length", None)
    rendered = _render(
        _body(stored.result), pdf_header(settings, profile_id, form), company=company, timestamp=stamp,
        spacing_scale=scale, auto_fit=fit, count_pages=count_pages, max_pages=length.max_pages if length is not None else LENGTH_RULE.max_pages,
        fixed=fixed and not fit, images=images,
    )
    return rendered, pdf_file_name(company, stored.job.title, today or date.today())


# --- one job's own spacing (0.1.11.5): a small file beside the job's stored resume ---------------------------

#: The suffix of the file that holds ONE job's PDF spacing, beside the job's stored resume (``<digest>.json`` ->
#: ``<digest>.layout``).  A second small file per job, and not a field of the stored resume, so that a GigAI
#: older than 0.1.11.5 opened on the same home reads every stored resume as before: its reader refuses a resume
#: with a key it does not know, and it only looks at ``*.json`` there, so it never sees this file.
JOB_LAYOUT_SUFFIX = ".layout"


def job_layout_path(stored_path: Path | str) -> Path:
    """Where the spacing of the job whose resume is stored at ``stored_path`` is kept."""

    return Path(stored_path).with_suffix(JOB_LAYOUT_SUFFIX)


def stored_job_path(stored: object, home_root: Path, target: Path | None) -> Path | None:
    """Where THIS home's store keeps ``stored``'s resume: the file its spacing sits beside.

    Worked out from the home, the project and the job (``tailored_resume_path``: where ``save_job_spacing`` writes),
    never read from the resume's own recorded ``stored_path``: that names the home the resume was first written in,
    which is another folder once a home has been copied or moved, and the saved spacing was then not found.
    Without ``target`` (a caller with no project in hand) the recorded path is all there is."""

    resume, job = getattr(stored, "resume", None), getattr(stored, "job", None)
    if target is not None and resume is not None and job is not None:
        try:
            return tailored_resume_path(home_root, target, resume.profile_id, job.job_identity)
        except Exception:  # noqa: BLE001 - a folder that is not bound to a project has no store: no spacing is saved there
            return None
    recorded = getattr(stored, "stored_path", None)
    return Path(recorded) if recorded else None


def job_spacing(stored_path: Path | str | None) -> float | None:
    """The spacing saved for the job whose resume is stored at ``stored_path``, or ``None``.

    Tolerant: no file, a symlink, a file that is not ``{"spacing_percent": <whole number>}`` or a value outside the
    slider's range all read as "none saved" (the render then takes the saved display layout: auto fit)."""

    if not stored_path:
        return None
    path = job_layout_path(stored_path)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    percent = raw.get("spacing_percent") if type(raw) is dict else None
    if type(percent) is not int:
        return None
    spacing = round(percent / 100, 2)
    return spacing if valid_spacing(spacing) else None


def save_job_spacing(home_root: Path, target: Path, profile_id: str | None, job_identity: str, spacing_scale: float) -> float | None:
    """Save ``spacing_scale`` as ONE job's PDF spacing (the job page's slider); ``None`` when the job has no stored resume.

    Written to ``job_layout_path`` (atomically, under the store's write lock for that folder) and nowhere else:
    the job's stored resume, its markdown, the jobs folder, the master and the saved display layout are not
    opened for writing.  It stays when the job's resume is picked or stored again (the file is the job's, not
    one pick's), and goes with the profile's folder when the profile is deleted.  ``ValueError`` when the
    spacing is outside the slider's range."""

    if not valid_spacing(spacing_scale):
        raise ValueError(f"spacing must be between {SPACING_MIN} and {SPACING_MAX}")
    spacing = round(float(spacing_scale), 2)
    path = tailored_resume_path(home_root, target, profile_id, job_identity)
    with tailored_resume_write_lock(path):
        if read_tailored_resume(path) is None:
            return None
        layout = job_layout_path(path)
        if layout.is_symlink():
            raise OSError("the job's layout path is a symlink")
        if job_spacing(path) != spacing:
            atomic_write(layout, (json.dumps({"spacing_percent": round(spacing * 100)}) + "\n").encode("utf-8"))
    return spacing


#: Shown with every headerless PDF (0110-046): the page that finishes it.
FINISH_LINE = "Open in Scout to add your name and contact details and download"


def finish_url(base_url: str, profile_id: str | None = None, job_identity: str | None = None) -> str:
    """The Scout page that finishes a headerless PDF: ``<base>/#/pdf/<profile_id>/<job identity>`` for a stored
    tailored resume, ``<base>/#/pdf`` for markdown (the user picks the file there).  ASCII only (a header value)."""

    base = base_url.rstrip("/")
    if profile_id and job_identity:
        return f"{base}/#/pdf/{quote(profile_id, safe='')}/{quote(job_identity, safe='')}"
    return f"{base}/#/pdf"


def markdown_resume_pdf(
    markdown: str, *, home_root: Path, profile_id: str | None = None, form: dict[str, object] | None = None,
    spacing_scale: float | None = None, auto_fit: bool | None = None, now: datetime | None = None,
) -> tuple[RenderedPdf, str]:
    """``(the PDF, its file name)`` for resume markdown: what ``POST /api/resume/pdf`` and ``scout resume pdf --in``
    serve.  ``ResumeMarkdownError`` / ``ValueError`` (spacing) before any render."""

    parse_resume_markdown(markdown)
    settings = load_display(home_root) or DisplaySettings()
    scale, fit = layout(settings, spacing_scale, auto_fit)
    stamp = now or datetime.now(timezone.utc)
    rendered = render_markdown_pdf(markdown, pdf_header(settings, profile_id, form), timestamp=stamp, spacing_scale=scale, auto_fit=fit)
    return rendered, pdf_file_name("", "", stamp.astimezone().date())


__all__ = [
    "MAX_MARKDOWN_BYTES",
    "MAX_MARKDOWN_LINES",
    "PREVIEW_PPI",
    "ContactItem",
    "FIT_FLOOR",
    "HEADER_RESERVE_LINES",
    "PdfHeader",
    "RenderedPdf",
    "ResumeMarkdownError",
    "clamp_scale",
    "fewest_pages",
    "FINISH_LINE",
    "finish_url",
    "fit_scale",
    "layout",
    "markdown_resume_pdf",
    "measure_markdown",
    "over_limit_note",
    "parse_resume_markdown",
    "pdf_file_name",
    "pdf_header",
    "printed_text",
    "render_markdown_pdf",
    "render_pdf",
    "save_job_spacing",
    "job_spacing",
    "job_layout_path",
    "stored_job_path",
    "JOB_LAYOUT_SUFFIX",
    "stored_resume_pdf",
]
