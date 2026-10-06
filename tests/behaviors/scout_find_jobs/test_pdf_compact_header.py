"""0.1.11.3 items 15/16: the PDF header is COMPACT, and the header file takes a shorthand for the links.

Read from the PDF that comes back (pypdf: the text, the type size of each run, the link targets):

- the header is the name line and ONE contact line: location | work authorization | links | email | phone, the
  work authorization INSIDE that line;
- a link prints without ``https://`` or ``www.`` and its target is the full URL;
- a field left empty, or holding only spaces, leaves no separator behind (never `` |  | ``, never a leading or
  trailing one);
- a line too long for the page is set SMALLER before it wraps (never below 8pt), on one line; only a line too long
  even there wraps, at the full size;
- the page estimate's blank header block is as tall as the tallest compact header (a contact line wrapped once): one definition, the template's.

The header file's shorthand (``pdf_header_file.form_values``): ``github`` / ``linkedin`` take the id alone (or the
address, read as the id), ``website`` a site address; ``links`` keeps working beside them; one link named twice is
there once; a placeholder (``REPLACE...``) is skipped; a value that is not an id is refused by the field's name,
never by its value.

Synthetic only: an invented person on reserved domains.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import pytest
from pypdf import PdfReader

from gigai.scout import pdf_header_file
from gigai.scout.resume_display import PdfHeader, ContactItem, form_header, parse_header_form
from gigai.scout.resume_pdf import HEADER_RESERVE_LINES, _data, _end, parse_resume_markdown, render_markdown_pdf

STAMP = datetime(2026, 10, 6, tzinfo=timezone.utc)
MARKDOWN = "## Summary\n\nEngineer with twelve years on data platforms.\n\n## Skills\n\n- Python, Go\n"
NAME = "Zora Quillfeather"
FORM = {
    "name": NAME, "email": "zq@example.invalid", "phone": "555-0142-ZQ", "location": "Quillshire, ZZ",
    "work_authorization": "VISA: H1B", "linkedin": "https://www.linkedin.com/in/zora-q/", "link": "",
    "links": [{"label": "GitHub", "url": "https://github.com/zora-q"}],
}
ONE_LINE = "Quillshire, ZZ | VISA: H1B | github.com/zora-q | linkedin.com/in/zora-q | zq@example.invalid | 555-0142-ZQ"
FULL_SIZE, FLOOR = 9.5, 8.0


def _header(form: dict[str, object], title: str = ""):
    """``(the header's lines under the name, the type size of every run of them, the link targets)`` of the PDF."""

    rendered = render_markdown_pdf(MARKDOWN, form_header(parse_header_form(form), title), timestamp=STAMP, auto_fit=False)
    page = PdfReader(io.BytesIO(rendered.pdf)).pages[0]
    runs: list[tuple[float, str]] = []

    def visit(text: str, _cm: object, tm: list[float], _font: object, size: float) -> None:
        if text.strip():
            runs.append((round(abs(size * tm[3]), 2), text.strip()))

    text = page.extract_text(visitor_text=visit)
    lines = [line.strip() for line in text.split("SUMMARY")[0].splitlines() if line.strip()]
    assert lines[0] == NAME.upper()
    stop = next(index for index, (_size, run) in enumerate(runs) if run == "SUMMARY")
    targets = [annotation.get_object()["/A"]["/URI"] for annotation in page.get("/Annots", [])]
    return lines[1:], [size for size, _run in runs[1:stop]], targets


def test_the_header_is_the_name_and_one_contact_line_with_the_work_authorization_inside_it() -> None:
    lines, sizes, targets = _header(FORM)
    assert lines == [ONE_LINE], lines
    assert set(sizes) == {FULL_SIZE}, "a line that fits is not set smaller"
    # No https:// or www. is printed; every link's target is the full URL, so the text is clickable.
    assert "https://" not in ONE_LINE and "www." not in ONE_LINE
    assert targets == ["https://github.com/zora-q", "https://linkedin.com/in/zora-q", "mailto:zq@example.invalid"]


def test_the_saved_title_keeps_its_own_line_above_the_contact_line() -> None:
    lines, _sizes, _targets = _header(FORM, "Staff Software Engineer, Data Platform")
    assert lines == ["Staff Software Engineer, Data Platform", ONE_LINE]


@pytest.mark.parametrize(("empty", "expected"), [
    (("location",), "VISA: H1B | github.com/zora-q | linkedin.com/in/zora-q | zq@example.invalid | 555-0142-ZQ"),
    (("work_authorization", "links"), "Quillshire, ZZ | linkedin.com/in/zora-q | zq@example.invalid | 555-0142-ZQ"),
    (("phone", "email"), "Quillshire, ZZ | VISA: H1B | github.com/zora-q | linkedin.com/in/zora-q"),
    (("location", "work_authorization", "links", "linkedin", "email"), "555-0142-ZQ"),
])
def test_a_field_left_empty_leaves_no_separator(empty: tuple[str, ...], expected: str) -> None:
    form = {**FORM, **{key: ([] if key == "links" else "   ") for key in empty}}
    lines, _sizes, _targets = _header(form)
    assert lines == [expected]
    assert "|  |" not in lines[0] and "| |" not in lines[0] and not lines[0].startswith("|") and not lines[0].endswith("|")


def test_a_header_given_as_items_never_prints_an_empty_place() -> None:
    """The renderer's own guard (``_data``), for a header that is not the form's: an item with no text is left out,
    and a work authorization given apart joins the one line."""

    header = PdfHeader(NAME, "", (ContactItem("Quillshire, ZZ", None), ContactItem("  ", None), ContactItem("", "https://example.invalid"), ContactItem("555-0142-ZQ", None)), "VISA: H1B")
    data = _data(parse_resume_markdown(MARKDOWN)[1], header, "")
    assert [item["text"] for item in data["contact"]] == ["Quillshire, ZZ", "VISA: H1B", "555-0142-ZQ"]
    assert "work_authorization" not in data, "the template prints ONE contact line"


def test_a_long_line_is_set_smaller_before_it_wraps_and_wraps_only_when_that_is_not_enough() -> None:
    # A little too long for the page at the full size: ONE line, smaller, never below the floor.
    longer = {**FORM, "links": [{"label": "GitHub", "url": "github.com/zora-quillfeather"}], "location": "Quillshire Springs, ZZ"}
    lines, sizes, _targets = _header(longer)
    assert len(lines) == 1, lines
    assert len(set(sizes)) == 1 and FLOOR <= sizes[0] < FULL_SIZE, sizes

    # Too long even at the floor: it wraps (the last resort), at the full size, and nothing is lost.
    longest = {**longer, "links": [{"label": "GitHub", "url": "github.com/zora-quillfeather"}, {"label": "Site", "url": "https://www.zora-quillfeather.example.invalid/portfolio/selected-work"}]}
    lines, sizes, _targets = _header(longest)
    assert len(lines) == 2 and set(sizes) == {FULL_SIZE}, (lines, sizes)
    assert "555-0142-ZQ" in lines[1] and "zora-quillfeather.example.invalid/portfolio/selected-work".replace("-", "") in "".join(lines).replace("-", "").replace(" ", "")


def test_the_estimate_s_blank_header_is_as_tall_as_the_tallest_header_it_stands_for() -> None:
    """ONE definition of the header's height (the template's ``head-line``): the page estimate's blank block ends
    the page exactly where the compact header at its largest does: the name, and a contact line wrapped once."""

    assert HEADER_RESERVE_LINES == 2
    sections = parse_resume_markdown(MARKDOWN)[1]
    wrapped = {**FORM, "links": [{"label": "GitHub", "url": "github.com/zora-quillfeather-zq"}, {"label": "Site", "url": "https://www.zora-quillfeather.example.invalid/portfolio/selected-work"}]}
    largest = form_header(parse_header_form(wrapped), "")
    with resources.as_file(resources.files("gigai.scout").joinpath("data", "resume")) as directory:
        template = (Path(directory) / "resume.typ").read_bytes()

        def end(data: dict[str, object]) -> tuple[int, float]:
            page, fill = _end(template, str(directory), data, 0.9)
            return page, round(fill, 4)

        blank = end(_data(sections, None, "", blank_lines=HEADER_RESERVE_LINES))
        real = end(_data(sections, largest, ""))
        # The same line boxes; the blank block also keeps the gap under each line, the wrapped line has none: 2 units (3pt).
        assert blank[0] == real[0] and 0 <= blank[1] - real[1] < 0.006, (blank, real)
        # ... and a one-line header ends higher: the estimate is never the smaller one.
        assert end(_data(sections, form_header(parse_header_form(FORM), ""), ""))[1] < blank[1]


# --- the header file's shorthand ------------------------------------------------------------------------------------


def _values(raw: dict[str, object]) -> dict[str, object]:
    return pdf_header_file.form_values(raw)[0]


@pytest.mark.parametrize("given", ["zora-q", "@zora-q", "github.com/zora-q", "https://github.com/zora-q/", "http://www.github.com/zora-q?tab=repositories"])
def test_github_takes_the_id_alone_or_the_address_read_as_the_id(given: str) -> None:
    values = _values({"github": given})
    assert values["github"] == "zora-q" and values["links"] == [], "2f: the form's GitHub field holds the id alone"
    assert [item.text for item in form_header(values).contact] == ["github.com/zora-q"]


@pytest.mark.parametrize("given", ["zora-q", "in/zora-q", "linkedin.com/in/zora-q", "https://www.linkedin.com/in/zora-q/", "https://uk.linkedin.com/in/zora-q"])
def test_linkedin_takes_the_id_alone_or_the_address_read_as_the_id(given: str) -> None:
    values = _values({"linkedin": given})
    assert values["linkedin"] == "zora-q" and values["links"] == [], "2f: the form's LinkedIn field holds the id alone"
    assert [item.text for item in form_header(values).contact] == ["linkedin.com/in/zora-q"]


@pytest.mark.parametrize(("given", "url"), [("zora.example.invalid", "zora.example.invalid"), ("https://www.zora.example.invalid/work/", "zora.example.invalid/work")])
def test_website_takes_a_site_address(given: str, url: str) -> None:
    values = _values({"website": given})
    assert values["website"] == url and values["links"] == []
    assert [item.text for item in form_header(values).contact] == [url]


def test_the_shorthand_prints_as_clickable_links_in_the_one_line() -> None:
    values = _values({
        "name": NAME, "email": "zq@example.invalid", "phone": "555-0142-ZQ", "location": "Quillshire, ZZ", "work_authorization": "VISA: H1B",
        "github": "zora-q", "linkedin": "zora-q",
    })
    lines, _sizes, targets = _header(values)
    assert lines == [ONE_LINE]
    assert targets[:2] == ["https://github.com/zora-q", "https://linkedin.com/in/zora-q"]


def test_links_keeps_working_beside_the_shorthand_and_a_link_named_twice_is_there_once() -> None:
    values = _values({
        "github": "zora-q", "linkedin": "zora-q", "website": "zora.example.invalid",
        "links": [
            {"label": "GitHub", "url": "https://www.github.com/Zora-Q/"},  # the github shorthand's own link
            {"label": "LinkedIn", "url": "linkedin.com/in/zora-q"},  # the linkedin shorthand's own link
            {"label": "Talks", "url": "example.invalid/talks"},
            {"label": "Talks again", "url": "https://example.invalid/talks/"},
        ],
    })
    assert (values["github"], values["linkedin"], values["website"]) == ("zora-q", "zora-q", "zora.example.invalid")
    assert values["links"] == [{"label": "Talks", "url": "example.invalid/talks"}]
    assert [item.text for item in form_header(values).contact] == ["github.com/zora-q", "zora.example.invalid", "example.invalid/talks", "linkedin.com/in/zora-q"]
    # The old form alone still works (2f): a GitHub or LinkedIn profile link fills that field, the rest are rows under their labels.
    old = _values({"links": [{"label": "LinkedIn", "url": "linkedin.com/in/zora-q"}, {"label": "GitHub", "url": "github.com/zora-q"}, {"label": "Repo", "url": "github.com/zora-q/tool"}]})
    assert (old["github"], old["linkedin"]) == ("zora-q", "zora-q") and old["links"] == [{"label": "Repo", "url": "github.com/zora-q/tool"}]
    # The form's own guard: a link the form holds twice (a field and a row) prints once.
    twice = form_header(parse_header_form({"name": NAME, "linkedin": "linkedin.com/in/zora-q", "links": [{"label": "L", "url": "https://www.linkedin.com/in/zora-q/"}]}))
    assert [item.text for item in twice.contact] == ["linkedin.com/in/zora-q"]


def test_a_placeholder_shorthand_is_skipped() -> None:
    values = _values({"name": NAME, "github": "REPLACE_WITH_YOUR_GITHUB_ID", "linkedin": "REPLACE: your id", "website": "REPLACE"})
    assert (values["github"], values["linkedin"], values["website"]) == ("", "", "") and values["links"] == []


@pytest.mark.parametrize(("key", "given"), [("github", "zora q/secret-9931"), ("github", "gitlab.example.invalid/secret-9931"), ("linkedin", "in/secret 9931"), ("website", "secret-9931"), ("github", 7)])
def test_a_shorthand_that_is_not_an_id_is_refused_by_the_field_s_name_never_its_value(key: str, given: object, tmp_path: Path) -> None:
    path = tmp_path / "header.json"
    path.write_text(json.dumps({"name": NAME, key: given}), encoding="utf-8")
    path.chmod(0o600)
    found = pdf_header_file.read_header_file(path)
    assert found.state == pdf_header_file.STATE_INVALID and found.values is None
    assert key in found.message and "secret-9931" not in found.message and "9931" not in found.message


def test_more_links_than_the_form_holds_are_refused() -> None:
    rows = [{"label": f"L{n}", "url": f"example.invalid/{n}"} for n in range(pdf_header_file.MAX_LINKS)]
    # 2f: the shorthand fields are fields of their own, so six other links fit beside them ...
    values = _values({"github": "zora-q", "linkedin": "zora-q", "website": "zora.example.invalid", "links": rows})
    assert len(values["links"]) == pdf_header_file.MAX_LINKS and parse_header_form(values)["links"] == rows
    # ... and a seventh is refused, as before.
    with pytest.raises(ValueError, match="more than 6 links"):
        _values({"github": "zora-q", "links": [*rows, {"label": "L6", "url": "example.invalid/6"}]})
