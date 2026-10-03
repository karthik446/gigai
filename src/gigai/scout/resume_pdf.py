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

import json
import re
import unicodedata
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
from gigai.scout.tailored_resume import (
    ENTRY_SECTIONS,
    MAX_HEADING_LINES,
    SECTION_HEADINGS,
    _LEADING_MARKERS,
    TailoredLine,
    TailoredResume,
    TailorResponse,
    _display,
    replaced_line,
    shown_text,
)

_PART_MAX = 40
_ROLE_MAX = 60


def _slug(text: str, limit: int = _PART_MAX) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:limit].strip("-")


def pdf_file_name(company: str, role: str, day: date) -> str:
    """``<company>-<role>-<YYYY-MM-DD>.pdf`` (lowercase ASCII, hyphens, each part length-capped);
    ``resume-<YYYY-MM-DD>.pdf`` when neither is known.  Never carries the user's name (0110-046)."""

    parts = [part for part in (_slug(company), _slug(role, _ROLE_MAX)) if part] or ["resume"]
    return "-".join([*parts, day.isoformat()]) + ".pdf"


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
            for entry in section.entries:
                # A copied bullet (the model's copy or a no-loss fallback, 0110-006) prints without its own "- ".
                entries.append({"heading": [_heading_line(l.text) for l in entry.heading], "bullets": [_flat(shown_text(l)) for l in entry.bullets]})
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


def _end(template: bytes, directory: str, data: dict[str, object], scale: float) -> tuple[int, float]:
    """(last page, fill of that page 0..1) where the content ends at ``scale``: Typst's own layout, via query."""
    import typst

    found = json.loads(typst.query(
        template, "<fit-end>", field="value", one=True, font_paths=[directory], ignore_system_fonts=True,
        sys_inputs={"data": json.dumps(data, ensure_ascii=False), "scale": repr(scale)},
    ))
    return int(found["page"]), float(found["fill"])


def fit_scale(measure: Callable[[float], tuple[int, float]]) -> float:
    """Auto fit: the LARGEST scale on the grid that still ends on the fewest pages any scale reaches.

    Content close to one page is pulled onto one page (down to 0.7x, never smaller type); content that needs
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


@dataclass(frozen=True)
class RenderedPdf:
    pdf: bytes
    #: The page count; ``None`` when the caller did not ask for it (``count_pages``) and auto fit did not measure it.
    pages: int | None
    spacing_scale: float


def _render(
    sections: list[dict[str, object]], header: PdfHeader | None, *, company: str, timestamp: datetime, spacing_scale: float, auto_fit: bool,
    count_pages: bool = False,
) -> RenderedPdf:
    """``header`` ``None``: no header, a blank block of the header's height reserved (an agent's PDF)."""
    shown = header or PdfHeader()
    data = {
        "doc_title": " ".join(part for part in ("Resume", company.strip()) if part),
        "name": shown.name,
        "title": shown.title,
        "contact": [{"text": c.text, "url": c.url} for c in shown.contact],
        "blank_header": header is None,
        "sections": sections,
    }
    root = resources.files("gigai.scout").joinpath("data", "resume")
    with ExitStack() as stack:
        directory = str(stack.enter_context(resources.as_file(root)))
        template = (Path(directory) / "resume.typ").read_bytes()
        measured: dict[float, tuple[int, float]] = {}

        def measure(candidate: float) -> tuple[int, float]:
            if candidate not in measured:
                measured[candidate] = _end(template, directory, data, candidate)
            return measured[candidate]

        scale = fit_scale(measure) if auto_fit else clamp_scale(spacing_scale)
        # Auto fit already measured the scale it chose; otherwise the count costs one layout query, only on request.
        pages = measured[scale][0] if scale in measured else (measure(scale)[0] if count_pages else None)
        return RenderedPdf(_compile(template, directory, data, scale, timestamp), pages, scale)


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
                if len(entry["heading"]) >= MAX_HEADING_LINES:  # type: ignore[arg-type]
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


def render_markdown_pdf(
    markdown: str, header: PdfHeader | None, *, timestamp: datetime, spacing_scale: float = SPACING_DEFAULT, auto_fit: bool = True, company: str = "",
) -> RenderedPdf:
    """Resume markdown (``parse_resume_markdown``) through the tailored-resume template, header and auto fit; pages counted."""

    _name, sections = parse_resume_markdown(markdown)
    return _render(sections, header, company=company, timestamp=timestamp, spacing_scale=spacing_scale, auto_fit=auto_fit, count_pages=True)


# --- the header and layout both entry points use ---------------------------------------------


def pdf_header(settings: DisplaySettings | None, profile_id: str | None, form: dict[str, str] | None) -> PdfHeader | None:
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
    stored: TailorResponse, *, home_root: Path, form: dict[str, str] | None = None, spacing_scale: float | None = None,
    auto_fit: bool | None = None, count_pages: bool = False, today: date | None = None,
) -> tuple[RenderedPdf, str]:
    """``(the PDF, its file name)`` for one stored tailored resume: what ``POST /api/tailored-resumes/pdf`` serves.

    ``form`` ``None``: headerless (an agent's or the CLI's render)."""

    profile_id = stored.resume.profile_id or "ephemeral"
    settings = load_display(home_root) or DisplaySettings()
    stamp = datetime.fromisoformat(stored.updated_at.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    scale, fit = layout(settings, spacing_scale, auto_fit)
    rendered = _render(
        _body(stored.result), pdf_header(settings, profile_id, form), company=stored.job.company, timestamp=stamp,
        spacing_scale=scale, auto_fit=fit, count_pages=count_pages,
    )
    return rendered, pdf_file_name(stored.job.company, stored.job.title, today or date.today())


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
    markdown: str, *, home_root: Path, profile_id: str | None = None, form: dict[str, str] | None = None,
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
    "ContactItem",
    "PdfHeader",
    "RenderedPdf",
    "ResumeMarkdownError",
    "clamp_scale",
    "FINISH_LINE",
    "finish_url",
    "fit_scale",
    "layout",
    "markdown_resume_pdf",
    "parse_resume_markdown",
    "pdf_file_name",
    "pdf_header",
    "render_markdown_pdf",
    "render_pdf",
    "stored_resume_pdf",
]
