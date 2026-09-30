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
from gigai.scout.tailored_resume import ENTRY_SECTIONS, TailoredResume, _display, shown_text

_PART_MAX = 40


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:_PART_MAX].strip("-")


def pdf_file_name(name: str, company: str) -> str:
    who, org = _slug(name), _slug(company)
    parts = [who, "resume", org]
    return "-".join(part for part in parts if part) + ".pdf"


def _body(result: TailoredResume) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    for section in result.sections:
        if section.is_empty():
            continue
        entries: list[dict[str, object]] = []
        lines: list[str] = []
        if section.heading in ENTRY_SECTIONS:
            for entry in section.entries:
                # A copied bullet (the model's copy or a no-loss fallback, 0110-006) prints without its own "- ".
                entries.append({"heading": [_display(l.text) for l in entry.heading], "bullets": [shown_text(l) for l in entry.bullets]})
        else:
            lines = [shown_text(l) for l in section.lines]
        sections.append({"heading": section.heading.upper(), "lines": lines, "entries": entries})
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
