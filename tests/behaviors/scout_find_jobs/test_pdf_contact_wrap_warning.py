"""0.3.6 (gigai-3jj.4): a contact line that wraps is said plainly, and the over-limit counts come from the real render.

A header of eight contact items wraps its ONE contact line onto a second line, and every extra line costs a line of
the resume.  The render measures how many printed lines the contact block takes (the template's ``<fit-end>``
layout query, no PDF more) and says so in counts only: ``RenderedPdf.contact_lines`` / ``contact_items`` /
``contact_note``, the preview's ``header_note`` / ``contact_lines``, and one plain line of ``gigai scout resume pdf``.
Never in the PDF, never a header value.  A headerless render and the preview's placeholder header say nothing.
Over the page limit, ``tightest_pages`` (the pages at the tightest spacing, same header) and ``lines_to_cut``
(about how many lines to leave out there) let the page tell a reachable limit from an unreachable one.

Synthetic only: invented people on reserved domains, tmp homes.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli
from gigai.scout.resume_display import SPACING_MIN, form_header
from gigai.scout.resume_pdf import _render, contact_line_note, over_limit_note, parse_resume_markdown

from tests.behaviors.scout_find_jobs.test_pdf_density_regression import FORM as SEVEN, resume_markdown
from tests.behaviors.scout_find_jobs.test_resume_preview_spacing import _Server, fx, server  # noqa: F401 - fx and server are fixtures
from tests.support.pipeline_fixtures import PipelineFixture

STAMP = datetime(2026, 11, 24, tzinfo=timezone.utc)

#: The name and EIGHT contact items (every contact field of the Generate PDF form), invented, on reserved domains:
#: too long for one line even at the smallest contact size, so it wraps once.
EIGHT = {
    "name": "Ottilie Vexmoor",
    "email": "ottilie.v@example.invalid",
    "phone": "555-0199",
    "location": "Brindlemoor, ZZ",
    "github": "ovexmoor",
    "linkedin": "ovexmoor",
    "website": "ovexmoor.example.invalid",
    "link": "folio.example.invalid/ov",
    "work_authorization": "US citizen",
}
#: The same eight fields, longer values: the line wraps twice (three printed lines), and the count says three.
LONG_EIGHT = {
    **EIGHT, "phone": "+1 (555) 010-0199", "location": "Brindlemoor Heights, ZZ", "linkedin": "ottilie-vexmoor-7713",
    "website": "ovexmoor-7713.example.invalid", "work_authorization": "US citizen, no sponsorship needed",
}
#: Three short contact items: one line.
THREE = {"name": "Ottilie Vexmoor", "email": "ottilie.v@example.invalid", "phone": "555-0199", "location": "Brindlemoor, ZZ"}
MARKERS = ("Ottilie", "Vexmoor", "VEXMOOR", "ottilie.v@", "0199", "Brindlemoor", "ovexmoor", "folio.example", "US citizen")

SHORT = "## Summary\n\n- Platform engineer with nine years building billing systems.\n\n## Skills\n\n- Python, SQL\n"


def _printed_contact_lines(pdf: bytes) -> int:
    """The PDF's own count: the text lines between the name (the first) and the Summary's line (``SHORT``)."""
    lines = PdfReader(io.BytesIO(pdf)).pages[0].extract_text().split("\n")
    return next(index for index, line in enumerate(lines) if line.startswith("Platform engineer")) - 1


def _render_md(markdown: str, header: dict[str, object] | None, *, spacing: float = 1.0, auto_fit: bool = False, max_pages: int | None = 2, fixed: bool = True):
    sections = parse_resume_markdown(markdown)[1]
    return _render(
        sections, form_header(header) if header is not None else None, company="Acme", timestamp=STAMP,
        spacing_scale=spacing, auto_fit=auto_fit, count_pages=True, max_pages=max_pages, fixed=fixed,
    )


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def _silent(text: str) -> None:
    for marker in MARKERS:
        assert marker not in text, f"{marker!r} is in {text!r}"


def test_an_eight_item_header_wraps_to_two_contact_lines_and_says_so_in_counts_only() -> None:
    rendered = _render_md(SHORT, EIGHT)
    assert (rendered.contact_lines, rendered.contact_items) == (2, 8) == (_printed_contact_lines(rendered.pdf), 8)
    assert rendered.contact_note == contact_line_note(2, 8)
    assert rendered.contact_note == "Your contact line runs onto 2 lines (8 items). Each extra line costs one line of the resume; drop an item to win it back."
    _silent(rendered.contact_note)
    text = _text(rendered.pdf)
    assert "VEXMOOR" in text and "runs onto" not in text and "drop an item" not in text, "the warning is printed in the PDF"
    # Measured from the real layout: the same header at every spacing (the contact line's box never scales).
    assert {_render_md(SHORT, EIGHT, spacing=spacing).contact_lines for spacing in (0.7, 1.0, 1.4)} == {2}
    # Counted, not assumed: longer values wrap twice; A.'s seven-item fixture (a long work authorization) wraps once.
    long = _render_md(SHORT, LONG_EIGHT)
    assert (long.contact_lines, long.contact_items, _printed_contact_lines(long.pdf)) == (3, 8, 3) and "onto 3 lines (8 items)" in str(long.contact_note)
    seven = _render_md(SHORT, SEVEN)
    assert (seven.contact_lines, seven.contact_items, _printed_contact_lines(seven.pdf)) == (2, 7, 2)


def test_a_three_item_header_is_one_contact_line_and_no_warning() -> None:
    rendered = _render_md(SHORT, THREE)
    assert (rendered.contact_lines, rendered.contact_items, rendered.contact_note) == (1, 3, None)


def test_a_headerless_render_measures_no_contact_line() -> None:
    rendered = _render_md(SHORT, None)
    assert (rendered.contact_lines, rendered.contact_items, rendered.contact_note) == (None, 0, None)
    assert (rendered.tightest_pages, rendered.lines_to_cut) == (None, None), "within its limit: no tightest count"


def test_over_the_limit_the_tightest_count_tells_a_reachable_limit_from_an_unreachable_one() -> None:
    # Reachable: 3 pages at the slider's 1.40, 2 at the tightest spacing; the note names the slider.
    reachable = _render_md(resume_markdown(), EIGHT, spacing=1.4)
    assert (reachable.pages, reachable.tightest_pages, reachable.lines_to_cut) == (3, 2, None)
    assert reachable.note == over_limit_note(3, 2, 1.4) and "slider" in reachable.note
    # Unreachable: 4 pages at the slider's 1.40, still 3 at the tightest: the note never points at the slider.
    unreachable = _render_md(resume_markdown(12), EIGHT, spacing=1.4)
    assert unreachable.tightest_pages == 3 and unreachable.pages is not None and unreachable.pages > 2
    assert isinstance(unreachable.lines_to_cut, int) and unreachable.lines_to_cut >= 2
    assert unreachable.note == over_limit_note(unreachable.pages, 2, lines=unreachable.lines_to_cut) and "slider" not in unreachable.note
    # Auto fit over the limit is AT the tightest spacing: its own count.
    auto = _render_md(resume_markdown(12), EIGHT, auto_fit=True, fixed=False)
    assert (auto.spacing_scale, auto.pages, auto.tightest_pages, auto.lines_to_cut) == (SPACING_MIN, 3, 3, unreachable.lines_to_cut)
    # Within the limit: no tightest count.
    assert (_render_md(resume_markdown(), EIGHT, spacing=SPACING_MIN).tightest_pages, _render_md(SHORT, EIGHT).lines_to_cut) == (None, None)


def test_the_preview_answer_carries_the_counts_and_no_header_value(fx: PipelineFixture, server: _Server) -> None:  # noqa: F811
    def preview(**body: object) -> dict:
        response = server.client.post("/api/tailored-resumes/preview", json={**server.key, **body})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        payload = response.json()
        _silent(json.dumps({key: value for key, value in payload.items() if key != "images"}))
        _silent("\n".join(f"{name}: {value}" for name, value in response.headers.items()))
        return payload

    eight = preview(header=EIGHT)
    assert eight["header_shown"] == "form" and eight["contact_lines"] == 2 and eight["header_note"] == contact_line_note(2, 8)
    assert "2 lines" in eight["header_note"] and "8 items" in eight["header_note"]
    three = preview(header=THREE)
    assert (three["contact_lines"], three["header_note"]) == (1, None)
    # The placeholder is not the person's header: no count, no warning.
    placeholder = preview()
    assert (placeholder["header_shown"], placeholder["contact_lines"], placeholder["header_note"]) == ("placeholder", None, None)
    # Over the limit at the slider's spacing, with a tighter spacing that reaches it: the counts the page's hint reads.
    loose = preview(header=EIGHT, spacing_scale=1.4)
    assert (loose["pages"], loose["max_pages"], loose["tightest_pages"], loose["lines_to_cut"]) == (3, 2, 2, None)
    fits = preview(header=EIGHT, spacing_scale=SPACING_MIN)
    assert (fits["pages"], fits["tightest_pages"], fits["lines_to_cut"]) == (2, None, None)


def test_resume_pdf_prints_the_warning_as_a_plain_line_with_counts_only(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    resume, header, out = tmp_path / "resume.md", tmp_path / "my-header.json", tmp_path / "out.pdf"
    resume.write_text(SHORT, encoding="utf-8")
    base = ["scout", "resume", "pdf", "--in", str(resume), "--home", str(home), "--out", str(out), "--header", str(header)]
    for values, lines, note in ((EIGHT, 2, contact_line_note(2, 8)), (THREE, 1, None)):
        # The header file names the form's `link` as one of its `links`.
        in_file = {key: value for key, value in values.items() if key != "link"} | ({"links": [{"label": "Folio", "url": values["link"]}]} if "link" in values else {})
        header.write_text(json.dumps(in_file), encoding="utf-8")
        header.chmod(0o600)
        plain = CliRunner().invoke(cli, base)
        assert plain.exit_code == 0, plain.output
        assert (note in plain.output.splitlines()) if note else ("runs onto" not in plain.output)
        as_json = CliRunner().invoke(cli, [*base, "--json"])
        payload = json.loads(as_json.output)
        assert (payload["contact_lines"], payload["contact_note"]) == (lines, note)
        _silent(plain.output)
        _silent(as_json.output)
        assert "runs onto" not in _text(out.read_bytes())
    headerless = json.loads(CliRunner().invoke(cli, [*base[:-2], "--no-header", "--json"]).output)
    assert (headerless["contact_lines"], headerless["contact_note"]) == (None, None)
