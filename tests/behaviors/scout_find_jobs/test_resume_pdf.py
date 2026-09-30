"""0110-003 P2: the Typst PDF renderer (pypdf golden checks)."""

from __future__ import annotations

import io
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pypdf import PdfReader

from gigai.scout.resume_display import ContactItem, PdfHeader
from gigai.scout.resume_pdf import pdf_file_name, render_pdf
from gigai.scout.tailored_resume import TailoredResume

FIXTURE = Path(__file__).parent / "fixtures" / "resume_pdf_result.json"
STAMP = datetime(2026, 9, 29, tzinfo=timezone.utc)
HEADER = PdfHeader(
    "Riley Example",
    "Clinical Applications Manager",
    (
        ContactItem("Columbus, Ohio", None),
        ContactItem("github.com/riley-example", "https://github.com/riley-example"),
        ContactItem("riley@example.test", "mailto:riley@example.test"),
        ContactItem("555-010-0100", None),
    ),
)


def _result() -> TailoredResume:
    return TailoredResume.from_json(json.loads(FIXTURE.read_text()))


def _pdf(header: PdfHeader = HEADER, result: TailoredResume | None = None) -> bytes:
    return render_pdf(result or _result(), header, company="Northwind", timestamp=STAMP)


def _text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def _uris(data: bytes) -> list[str]:
    out = []
    for page in PdfReader(io.BytesIO(data)).pages:
        for annot in page.get("/Annots") or []:
            action = annot.get_object().get("/A")
            if action and "/URI" in action:
                out.append(str(action["/URI"]))
    return out


def test_name_first_pages_links_and_metadata() -> None:
    data = _pdf()
    assert data.startswith(b"%PDF")
    text = _text(data)
    # The name prints in capitals (the type scale, 0110-017); the metadata keeps it as saved.
    assert text.startswith("RILEY EXAMPLE\nClinical Applications Manager\nColumbus, Ohio | github.com/riley-example | riley@example.test | 555-010-0100")
    assert len(PdfReader(io.BytesIO(data)).pages) <= 2
    assert set(_uris(data)) == {"https://github.com/riley-example", "mailto:riley@example.test"}
    meta = PdfReader(io.BytesIO(data)).metadata
    assert meta.title == "Riley Example resume Northwind" and meta.author == "Riley Example"


def test_fonts_embedded_and_bytes_deterministic() -> None:
    data = _pdf()
    reader = PdfReader(io.BytesIO(data))
    fonts = {str(f.get_object()["/BaseFont"]) for page in reader.pages for f in page["/Resources"]["/Font"].values()}
    assert fonts and all("Inter" in name for name in fonts)
    assert b"/FontFile" in data
    assert _pdf() == data


def test_unicode_and_markup_characters_print_literally() -> None:
    result = _result()
    section = result.sections[0]
    from dataclasses import replace
    from gigai.scout.tailored_resume import TailoredLine

    line = TailoredLine("rewritten", "Ingeniero – 12+ años; “resilient” café ñ ü ł č #hash *star* $dollar", ())
    sections = (replace(section, lines=(line,) + section.lines[1:]),) + result.sections[1:]
    text = _text(_pdf(PdfHeader("José Álvarez-Müller"), replace(result, sections=sections)))
    assert text.startswith("JOSÉ ÁLVAREZ-MÜLLER")
    assert "Ingeniero – 12+ años; “resilient” café ñ ü ł č #hash *star* $dollar" in text


def test_name_only_and_empty_settings_render_without_empty_separator() -> None:
    text = _text(_pdf(PdfHeader(name="Only Name")))
    assert text.startswith("ONLY NAME\n") and "|" not in text.split("\n", 3)[1]
    empty = _pdf(PdfHeader())
    assert empty.startswith(b"%PDF") and "|" not in _text(empty).split("\n")[0]
    titled = _text(_pdf(PdfHeader("N", "T", ())))
    assert "|" not in "\n".join(titled.split("\n")[:2])


def test_render_makes_no_network_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("PDF rendering opened a socket")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    assert _pdf().startswith(b"%PDF")


def test_pdf_file_name() -> None:
    assert pdf_file_name("Riley Example", "Northwind") == "riley-example-resume-northwind.pdf"
    assert pdf_file_name("José Álvarez", "Acme, Inc.") == "jose-alvarez-resume-acme-inc.pdf"
    assert pdf_file_name("", "Northwind") == "resume-northwind.pdf"
    assert pdf_file_name("Riley Example", "") == "riley-example-resume.pdf"
    assert pdf_file_name("", "") == "resume.pdf"
    assert len(pdf_file_name("x" * 100, "y" * 100)) <= 40 + 40 + len("-resume-.pdf")
