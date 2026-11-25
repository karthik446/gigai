"""0.3.6 fix C (gigai-3jj.5): the independent read's findings on the resume PDF, pinned.

- A plain fixed-spacing render (``render_pdf``: a download that reports no count) makes ONE compile and no layout
  query, so it has no contact-line count; a caller that reports the count (``count_pages``) gets it.
- ``gigai scout resume pdf --in FILE --auto-fit`` is held to the length rule's page limit, as a stored resume is: a
  resume that cannot reach it prints at the tightest spacing and the note names the lines to leave out.  A spacing
  given without auto fit is the person's own: used as given, no note.
- A resume over its limit with no bullet to measure a line by gets no line count (``lines_to_cut`` ``None``) and a
  note that names no number.

Every value is invented (the density test's synthetic resume and header); the headers are on reserved domains."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.resume_display import SPACING_DEFAULT, SPACING_MAX, SPACING_MIN, form_header
from gigai.scout.resume_pdf import _render, over_limit_note, parse_resume_markdown

from tests.behaviors.scout_find_jobs.test_pdf_density_regression import FORM, STAMP, _text, resume_markdown


def _sections(markdown: str) -> list[dict[str, object]]:
    return parse_resume_markdown(markdown)[1]


def test_a_plain_fixed_spacing_render_makes_one_compile_and_reports_no_contact_count(monkeypatch: pytest.MonkeyPatch) -> None:
    import typst

    calls: list[str] = []
    for name in ("query", "compile"):
        real = getattr(typst, name)
        monkeypatch.setattr(typst, name, lambda *a, _real=real, _name=name, **k: (calls.append(_name), _real(*a, **k))[1])
    sections, header = _sections(resume_markdown()), form_header(FORM)
    plain = _render(sections, header, company="Acme", timestamp=STAMP, spacing_scale=1.0, auto_fit=False)
    assert calls == ["compile"] and plain.contact_lines is None and plain.contact_note is None
    assert plain.contact_items == 7  # the name is not a contact item
    calls.clear()
    counted = _render(sections, header, company="Acme", timestamp=STAMP, spacing_scale=1.0, auto_fit=False, count_pages=True)
    assert calls == ["query", "compile"] and counted.contact_lines == 2 and counted.pdf == plain.pdf


def _cli_pdf(tmp_path: Path, markdown: str, *flags: str) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    resume, header, out = tmp_path / "resume.md", tmp_path / "my-header.json", tmp_path / "out.pdf"
    resume.write_text(markdown, encoding="utf-8")
    header.write_text(json.dumps(FORM), encoding="utf-8")
    header.chmod(0o600)
    run = CliRunner().invoke(cli, ["scout", "resume", "pdf", "--in", str(resume), "--home", str(home), "--out", str(out), "--header", str(header), *flags, "--json"])
    assert run.exit_code == 0, run.output
    return json.loads(run.output)


def test_resume_pdf_in_with_auto_fit_puts_a_reachable_resume_on_two_pages(tmp_path: Path) -> None:
    payload = _cli_pdf(tmp_path, resume_markdown(), "--auto-fit")
    assert payload["pages"] == 2 and SPACING_MIN <= payload["spacing_scale"] <= SPACING_MAX, payload
    assert payload["note"] is None


def test_resume_pdf_in_with_auto_fit_prints_an_unreachable_resume_at_the_tightest_spacing_and_names_the_lines(tmp_path: Path) -> None:
    """The read (0.3.6 #3): 3 pages at 0.85 and no note; 0.3.5: 4 pages at 1.40.  A markdown file has no job page:
    the note sends the person to the resume itself, and names no slider."""
    payload = _cli_pdf(tmp_path, resume_markdown(12), "--auto-fit")
    assert (payload["pages"], payload["spacing_scale"]) == (3, SPACING_MIN), payload
    said = re.search(r"leave out about (\d+) lines? in the resume, then generate the PDF again\.", payload["note"] or "")
    assert said, payload["note"]
    assert payload["note"] == over_limit_note(3, 2, lines=int(said.group(1)), where=None)
    assert "job's page" not in payload["note"] and "slider" not in payload["note"]
    # With no count (no bullet to measure by), the same place, no number.
    assert "To get 2 pages, shorten the resume, then generate the PDF again." in over_limit_note(3, 2, where=None)


def test_resume_pdf_in_with_a_spacing_and_no_auto_fit_keeps_the_persons_spacing(tmp_path: Path) -> None:
    """``--spacing`` alone turns auto fit off: the spacing as given, never tightened behind the person, no note."""
    payload = _cli_pdf(tmp_path, resume_markdown(12), "--spacing", str(SPACING_MAX))
    assert payload["spacing_scale"] == SPACING_MAX and payload["pages"] > 2 and payload["note"] is None, payload


#: A Summary of 14 paragraphs of some 900 characters and nothing else: over two pages, and not one bullet.
NO_BULLETS = "## Summary\n\n" + "\n\n".join(_text(seed, 900) for seed in range(14)) + "\n"


@pytest.mark.parametrize("auto_fit", [True, False])
def test_a_resume_over_its_limit_with_no_bullet_gets_no_line_count(auto_fit: bool) -> None:
    """The read (0.3.6 #4): "leave out about 1 line", which would not take 3 pages to 2."""
    sections = _sections(NO_BULLETS)
    assert not any(line["bullet"] for section in sections for line in section["lines"])  # type: ignore[index, union-attr]
    spacing = SPACING_DEFAULT if auto_fit else SPACING_MAX
    rendered = _render(sections, None, company="Acme", timestamp=STAMP, spacing_scale=spacing, auto_fit=auto_fit, count_pages=True, max_pages=2)
    assert rendered.pages is not None and rendered.pages > 2 and rendered.tightest_pages is not None and rendered.tightest_pages > 2
    assert rendered.lines_to_cut is None
    assert rendered.note == over_limit_note(rendered.pages, 2)
    assert "To get 2 pages, shorten the resume on the job's page" in rendered.note and not re.search(r"about \d", rendered.note)
    # The preview's pictures answer the same: ``lines_to_cut`` null (the job page then asks to shorten the resume).
    preview = _render(sections, None, company="Acme", timestamp=STAMP, spacing_scale=spacing, auto_fit=auto_fit, max_pages=2, images=True)
    assert (preview.pages, preview.lines_to_cut, preview.note) == (rendered.pages, None, rendered.note)
