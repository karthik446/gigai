"""0110-032: resume markdown in, PDF out -- ``resume_pdf.parse_resume_markdown`` / ``render_markdown_pdf``
and ``gigai scout resume pdf``.  Synthetic resume only; Typst is a required dependency.

What is pinned:

* a stored result's own ``markdown`` (``render_markdown``) renders through ``render_markdown_pdf`` to
  the SAME BYTES as ``render_pdf`` of the result, for the same header, company and timestamp (the UI
  path) -- so the markdown route and the UI path cannot drift;
* the header is the Generate PDF form's, never the markdown's (lines above the first section are
  not printed); without the form (the CLI) the PDF has no header (0110-046);
* invalid markdown, an oversize body and an out-of-range spacing are clear errors that name a line
  number and a rule, never the line's text;
* the CLI renders ``--in`` to a valid PDF, reports pages, and creates nothing under the home.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli
from gigai.scout.resume_display import DisplaySettings, PdfHeader, save_display
from gigai.scout.resume_pdf import (
    MAX_MARKDOWN_BYTES,
    ResumeMarkdownError,
    layout,
    parse_resume_markdown,
    render_markdown_pdf,
    render_pdf,
)
from gigai.scout.tailored_resume import TailoredResume, render_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "resume_pdf_result.json"
STAMP = datetime(2026, 9, 29, tzinfo=timezone.utc)
HEADER = PdfHeader("Riley Example", "Clinical Applications Manager", ())

MARKDOWN = """# Riley Example
riley@example.test | 555-010-0100

## Summary

- Platform engineer with nine years building billing systems. <!-- R3 -->
- Leads small teams and owns releases end to end. <!-- R4 -->

## Experience

### Northwind Health <!-- R6 -->
Staff Engineer | Jun 2020 - Present <!-- R7 -->

- Rebuilt the scheduling service on Python and Postgres, cutting p95 latency by 40%. <!-- R8 -->
- Ran the release calendar for 4 teams. <!-- edited -->

## Skills

- Python, PostgreSQL, Kubernetes · Terraform <!-- R20 -->

## Other

- Volunteer mentor at a local coding club.

Speaks English and Spanish.
"""


def _text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def _result() -> TailoredResume:
    return TailoredResume.from_json(json.loads(FIXTURE.read_text()))


def test_a_stored_results_markdown_renders_to_the_same_bytes_as_the_ui_path() -> None:
    result = _result()
    ui = render_pdf(result, HEADER, company="Northwind", timestamp=STAMP)
    from_markdown = render_markdown_pdf(render_markdown(result), HEADER, company="Northwind", timestamp=STAMP)
    assert from_markdown.pdf.startswith(b"%PDF")
    assert from_markdown.pdf == ui, "the markdown route and the UI path render the same input to the same bytes"
    assert from_markdown.pages == len(PdfReader(io.BytesIO(ui)).pages)
    # With auto fit off and another scale, still the same bytes as the UI path at that scale.
    assert (
        render_markdown_pdf(render_markdown(result), HEADER, company="Northwind", timestamp=STAMP, spacing_scale=0.8, auto_fit=False).pdf
        == render_pdf(result, HEADER, company="Northwind", timestamp=STAMP, spacing_scale=0.8, auto_fit=False)
    )


def test_the_parsed_shape_and_the_header_comes_from_the_form_not_the_markdown() -> None:
    name, sections = parse_resume_markdown(MARKDOWN)
    assert name == "Riley Example"
    assert [section["heading"] for section in sections] == ["SUMMARY", "EXPERIENCE", "SKILLS", "OTHER"]
    summary, experience, skills, other = sections
    assert summary["lines"] == [{"text": "Platform engineer with nine years building billing systems. Leads small teams and owns releases end to end.", "bullet": False}]
    assert experience["entries"] == [
        {
            "heading": [{"text": "Northwind Health", "dates": ""}, {"text": "Staff Engineer", "dates": "Jun 2020 - Present"}],
            "bullets": ["Rebuilt the scheduling service on Python and Postgres, cutting p95 latency by 40%.", "Ran the release calendar for 4 teams."],
        }
    ]
    assert skills["tags"] == ["Python", "PostgreSQL", "Kubernetes", "Terraform"] and skills["lines"] == []
    assert other["lines"] == [{"text": "Volunteer mentor at a local coding club.", "bullet": True}, {"text": "Speaks English and Spanish.", "bullet": False}]

    rendered = render_markdown_pdf(MARKDOWN, PdfHeader("Sam Saved", "", ()), timestamp=STAMP)
    text = _text(rendered.pdf)
    assert rendered.pdf.startswith(b"%PDF") and rendered.pages == len(PdfReader(io.BytesIO(rendered.pdf)).pages) == 1
    assert text.startswith("SAM SAVED\nSUMMARY")
    assert "riley@example.test" not in text and "555-010-0100" not in text and "RILEY EXAMPLE" not in text
    assert "<!--" not in text and "edited" not in text and "Ran the release calendar for 4 teams." in text


def test_a_hard_wrapped_bullet_continues_and_other_bullet_markers_work() -> None:
    _name, sections = parse_resume_markdown("## Experience\n### Acme\n* Built the\n  export pipeline.\n• Second bullet.\n")
    assert sections[0]["entries"][0]["bullets"] == ["Built the export pipeline.", "Second bullet."]


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("", "no resume content"),
        ("Just a paragraph with no sections.\n", "no resume content"),
        ("## Summary\n", "no resume content"),
        ("## Hobbies\n- Chess\n", "line 1: unknown section; use ## Summary, ## Experience, ## Skills, ## Education, ## Projects, ## Other"),
        ("## Summary\n- a\n## Summary\n- b\n", "line 3: the Summary section appears twice"),
        ("## Experience\n- A bullet with no entry.\n", "line 2: start the entry with '### <employer, project or school>' first"),
        ("## Experience\n### Acme\n- one\n\nA stray paragraph.\n", "line 5: text after an entry's bullets"),
        ("## Experience\n### Acme\na\nb\nc\nd\n", "line 6: an entry heading has at most 4 lines"),
        ("## Summary\n### Not an entry section\n", "line 2: '### ' entry headings belong in Experience, Projects or Education"),
        ("## Summary\n- a\n# Late title\n", "line 3: a '# ' title belongs above the first section"),
        ("## Summary\n- bad \x00 byte\n", "line 2: control characters are not allowed"),
    ],
)
def test_invalid_markdown_is_a_clear_error_that_never_echoes_the_text(markdown: str, expected: str) -> None:
    with pytest.raises(ResumeMarkdownError) as caught:
        parse_resume_markdown(markdown)
    assert caught.value.code == "resume_markdown_invalid" and expected in str(caught.value)
    for private in ("Chess", "stray paragraph", "Late title", "Not an entry section"):
        assert private not in str(caught.value)


def test_oversize_markdown_is_refused() -> None:
    with pytest.raises(ResumeMarkdownError) as caught:
        parse_resume_markdown("## Summary\n" + "- line\n" * (MAX_MARKDOWN_BYTES // 7 + 10))
    assert caught.value.code == "resume_markdown_too_large"
    with pytest.raises(ResumeMarkdownError) as caught:
        parse_resume_markdown("## Summary\n" + "x" * (MAX_MARKDOWN_BYTES + 1))
    assert caught.value.code == "resume_markdown_too_large" and str(MAX_MARKDOWN_BYTES) in str(caught.value)


def test_layout_uses_the_saved_settings_unless_the_caller_gives_one() -> None:
    saved = DisplaySettings(spacing_scale=0.9, auto_fit=True)
    assert layout(saved) == (0.9, True)
    assert layout(saved, 1.2) == (1.2, False), "a spacing given alone turns auto fit off"
    assert layout(saved, 1.2, True) == (1.2, True) and layout(saved, None, False) == (0.9, False)
    for bad in (0.69, 1.41, 5):
        with pytest.raises(ValueError, match="spacing must be between 0.7 and 1.4"):
            layout(saved, bad)


# --- the CLI ---------------------------------------------------------------------------------


def _cli(*args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(cli, ["scout", "resume", "pdf", *args, "--json"])
    return result.exit_code, json.loads(result.output.strip().splitlines()[-1])


def test_cli_renders_markdown_headerless_with_the_saved_layout_and_creates_nothing_else(tmp_path: Path) -> None:
    source, out, home = tmp_path / "resume.md", tmp_path / "out" / "resume.pdf", tmp_path / "home"
    source.write_text(MARKDOWN, encoding="utf-8")

    # 0110-046: no header at all (GigAI stores no name or contact details); the markdown's "# Name" never prints.
    code, payload = _cli("--in", str(source), "--out", str(out), "--home", str(home))
    assert code == 0 and payload["ok"] is True and payload["source"] == "markdown" and payload["pages"] == 1
    assert out.read_bytes().startswith(b"%PDF") and payload["bytes"] == out.stat().st_size
    assert _text(out.read_bytes()).startswith("SUMMARY")
    assert not home.exists(), "rendering a file creates no GigAI home or Scout folder"

    # The saved layout is followed; a saved title does not print on a headerless PDF.
    save_display(home, DisplaySettings({"prof_1": "Staff Engineer"}, spacing_scale=0.8, auto_fit=False))
    code, payload = _cli("--in", str(source), "--out", str(out), "--home", str(home), "--profile", "prof_1")
    assert code == 0 and payload["spacing_scale"] == 0.8
    assert _text(out.read_bytes()).startswith("SUMMARY")
    saved_bytes = out.read_bytes()

    # --spacing overrides for this render only; --auto-fit picks the scale.
    code, payload = _cli("--in", str(source), "--out", str(out), "--home", str(home), "--spacing", "1.3")
    assert code == 0 and payload["spacing_scale"] == 1.3 and out.read_bytes() != saved_bytes
    code, payload = _cli("--in", str(source), "--out", str(out), "--home", str(home), "--auto-fit")
    assert code == 0 and 0.7 <= payload["spacing_scale"] <= 1.4

    # stdin works too.
    piped = CliRunner().invoke(cli, ["scout", "resume", "pdf", "--in", "-", "--out", str(out), "--home", str(home), "--json"], input=MARKDOWN)
    assert piped.exit_code == 0 and json.loads(piped.output)["pages"] == 1


def test_cli_errors_are_clear(tmp_path: Path) -> None:
    source, out, home = tmp_path / "resume.md", tmp_path / "resume.pdf", tmp_path / "home"
    source.write_text(MARKDOWN, encoding="utf-8")
    base = ("--out", str(out), "--home", str(home))

    for spacing in ("0.5", "1.5"):
        code, payload = _cli("--in", str(source), *base, "--spacing", spacing)
        assert code == 1 and payload["error"] == {"code": "invalid_value", "message": "spacing must be between 0.7 and 1.4"}
    bad = tmp_path / "bad.md"
    bad.write_text("## Hobbies\n- Chess\n", encoding="utf-8")
    code, payload = _cli("--in", str(bad), *base)
    assert code == 1 and payload["error"]["code"] == "resume_markdown_invalid" and payload["error"]["message"].startswith("line 1: unknown section")
    big = tmp_path / "big.md"
    big.write_text("## Summary\n" + "x" * (MAX_MARKDOWN_BYTES + 1), encoding="utf-8")
    code, payload = _cli("--in", str(big), *base)
    assert code == 1 and payload["error"]["code"] == "resume_markdown_too_large"
    code, payload = _cli("--in", str(tmp_path / "missing.md"), *base)
    assert code == 1 and payload["error"]["code"] == "input_file_unreadable"
    code, payload = _cli(*base)
    assert code == 1 and payload["error"]["message"] == "pass exactly one of --in FILE or --tailored --job-url URL"
    code, payload = _cli("--in", str(source), "--tailored", "--job-url", "https://example.test/j", *base)
    assert code == 1 and payload["error"]["message"] == "pass exactly one of --in FILE or --tailored --job-url URL"
    code, payload = _cli("--tailored", *base)
    assert code == 1 and payload["error"]["message"] == "--tailored and --job-url go together"
    assert not out.exists(), "no PDF is written on an error"
