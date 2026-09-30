"""Render a stored tailored resume to PDF with Typst, locally and deterministically.

The header comes from display settings (``resume_display.pdf_header``), never from
``result.header``.  Body lines go through the same ``shown_text`` mapping as the markdown
renderer (a copy -- the model's or a no-loss fallback -- loses its own markers).  ``typst`` is imported lazily so CLI startup never loads its native library.
"""

from __future__ import annotations

import json
import re
import unicodedata
from contextlib import ExitStack
from datetime import datetime, timezone
from importlib import resources

from gigai.scout.resume_display import ContactItem, PdfHeader
from gigai.scout.tailored_resume import ENTRY_SECTIONS, _LEADING_MARKERS, TailoredLine, TailoredResume, _display, shown_text

_PART_MAX = 40


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:_PART_MAX].strip("-")


def pdf_file_name(name: str, company: str) -> str:
    who, org = _slug(name), _slug(company)
    parts = [who, "resume", org]
    return "-".join(part for part in parts if part) + ".pdf"


_YEAR = re.compile(r"\b(?:19|20)\d\d\b|\bPresent\b", re.IGNORECASE)


def _flat(text: str) -> str:
    """One physical line: hard wraps (newlines, runs of blanks) become single spaces."""
    return " ".join(text.split())


def _source_is_bullet(line: TailoredLine) -> bool:
    """True when the resume line this text came from was a real bullet (a marker other than a heading)."""
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
        if line.kind == "copy":
            printed |= _covered(line)
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
        sections.append({"heading": section.heading.upper(), "lines": [] if tags else lines, "tags": tags, "entries": entries})
    return sections


def _epoch(timestamp: datetime) -> int:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return int(timestamp.timestamp())


def render_pdf(result: TailoredResume, header: PdfHeader, *, company: str, timestamp: datetime) -> bytes:
    import typst  # lazy: a large native library

    doc_title = " ".join(part for part in (header.name, "resume", company.strip()) if part)
    data = {
        "doc_title": doc_title,
        "name": header.name,
        "title": header.title,
        "contact": [{"text": c.text, "url": c.url} for c in header.contact],
        "sections": _body(result),
    }
    root = resources.files("gigai.scout").joinpath("data", "resume")
    with ExitStack() as stack:
        directory = stack.enter_context(resources.as_file(root))
        template = (directory / "resume.typ").read_bytes()
        return typst.compile(
            template,
            format="pdf",
            font_paths=[str(directory)],
            ignore_system_fonts=True,
            sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
            timestamp=_epoch(timestamp),
        )


__all__ = ["ContactItem", "PdfHeader", "pdf_file_name", "render_pdf"]
