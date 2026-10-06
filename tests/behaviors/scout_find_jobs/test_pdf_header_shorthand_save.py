"""0.1.11.3 item 16 (packet 2f): the Generate PDF form's GitHub / LinkedIn / Website fields, and Save writing the SHORTHAND.

The form holds an id for GitHub and LinkedIn and a site address for Website.  "Save these details" writes them as
the header file's shorthand keys (``"github": "<id>"``, ``"linkedin": "<id>"``, ``"website": "<value>"``), with
``links`` only for the other links; a field left empty is left out; a full address typed in an id field is saved
as the id.  A file Save wrote fills the form with the same values, and ``gigai scout resume pdf --header`` prints
the same compact line from it.  An old ``links`` file still fills the id fields.

One reader of the shorthand (``resume_display.shorthand_value``): the file, the form's values and Save all use it.

Synthetic values and tmp folders only.
"""

from __future__ import annotations

import ast
import io
import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.cli import cli
from gigai.scout import pdf_header_file, pdf_header_save, resume_display
from gigai.scout.pdf_header_file import STATE_FILLED, read_header_file
from gigai.scout.pdf_header_save import SAVED_FIELDS, STATE_SAVED, HeaderSaveError, file_content, save_header_file
from gigai.scout.resume_display import form_header, parse_header_form
from gigai.scout.resume_pdf import render_markdown_pdf

from tests.behaviors.scout_find_jobs.test_pdf_header_file import MARKERS

MARKDOWN = "## Summary\n\n- Platform engineer with nine years building billing systems.\n\n## Skills\n\n- Python, SQL\n"
STAMP = datetime(2026, 10, 6, tzinfo=timezone.utc)
ID = "zq-invalid-7731"
SITE = "zq-invalid-7731.example.invalid"
#: What the form's Save sends (``generatePdfModel.headerFileBody``): every field, the empty ones too.
FORM_BODY = {
    "name": "Zora Quillfeather", "email": "", "phone": "", "location": "Quillshire, ZZ",
    "github": ID, "linkedin": ID, "website": SITE, "links": [], "work_authorization": "VISA: H1B (ZQ-7731)",
}
ONE_LINE = f"Quillshire, ZZ | VISA: H1B (ZQ-7731) | github.com/{ID} | {SITE} | linkedin.com/in/{ID}"


def _saved(tmp_path: Path, details: dict[str, object]) -> Path:
    path = tmp_path / "header.json"
    assert save_header_file(path, details, replace=True).state == STATE_SAVED
    return path


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def _targets(pdf: bytes) -> list[str]:
    return [annotation.get_object()["/A"]["/URI"] for annotation in PdfReader(io.BytesIO(pdf)).pages[0].get("/Annots", [])]


def test_save_writes_the_shorthand_exactly(tmp_path: Path) -> None:
    path = _saved(tmp_path, FORM_BODY)
    # The file's content, byte for byte: the ids alone, no links key (there is no other link), no empty field.
    assert path.read_text(encoding="utf-8") == (
        "{\n"
        '  "name": "Zora Quillfeather",\n'
        '  "location": "Quillshire, ZZ",\n'
        f'  "github": "{ID}",\n'
        f'  "linkedin": "{ID}",\n'
        f'  "website": "{SITE}",\n'
        '  "work_authorization": "VISA: H1B (ZQ-7731)"\n'
        "}\n"
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and sorted(item.name for item in tmp_path.iterdir()) == ["header.json"]
    assert SAVED_FIELDS == pdf_header_file.FILE_FIELDS and {"github", "linkedin", "website"} <= set(SAVED_FIELDS)


def test_links_holds_only_the_links_that_are_not_github_linkedin_or_website(tmp_path: Path) -> None:
    # The id fields are empty here: the same links arrive as rows (an old file's rows, or the "Other link" field).
    details = {
        "name": "Zora Quillfeather",
        "links": [
            {"label": "Link", "url": f"https://github.com/{ID}"},
            {"label": "Profile", "url": f"https://www.linkedin.com/in/{ID}/"},
            {"label": "Website", "url": f"https://www.{SITE}/"},
            {"label": "Talks", "url": f"{SITE}/talks"},
            {"label": "Repo", "url": f"github.com/{ID}/tool"},  # a repository is not a profile: it stays a link
        ],
        "work_authorization": "",
    }
    assert json.loads(_saved(tmp_path, details).read_text(encoding="utf-8")) == {
        "name": "Zora Quillfeather", "github": ID, "linkedin": ID, "website": SITE,
        "links": [{"label": "Talks", "url": f"{SITE}/talks"}, {"label": "Repo", "url": f"github.com/{ID}/tool"}],
        "work_authorization": "",
    }


@pytest.mark.parametrize(
    ("key", "typed", "saved"),
    [
        ("github", f"https://github.com/{ID}", ID),
        ("github", f"http://www.github.com/{ID}/?tab=repositories", ID),
        ("github", f"github.com/{ID}", ID),
        ("github", f" @{ID} ", ID),
        ("linkedin", f"https://www.linkedin.com/in/{ID}/", ID),
        ("linkedin", f"linkedin.com/in/{ID}", ID),
        ("linkedin", f"https://uk.linkedin.com/in/{ID}?trk=x", ID),
        ("website", f"https://www.{SITE}/", SITE),
        ("website", f"{SITE}/work", f"{SITE}/work"),
    ],
)
def test_a_full_address_typed_in_an_id_field_is_saved_as_the_id(tmp_path: Path, key: str, typed: str, saved: str) -> None:
    content = json.loads(_saved(tmp_path, {"name": "Zora Quillfeather", key: typed}).read_text(encoding="utf-8"))
    assert content == {"name": "Zora Quillfeather", key: saved, "work_authorization": ""}
    assert "https://" not in json.dumps(content) and "www." not in json.dumps(content)


def test_no_empty_and_no_placeholder_value_is_written(tmp_path: Path) -> None:
    content = json.loads(_saved(tmp_path, {**FORM_BODY, "github": "  ", "linkedin": "", "website": "", "location": ""}).read_text(encoding="utf-8"))
    # Empty fields are left out; work_authorization alone is kept when empty (there, empty means "no line").
    assert content == {"name": "Zora Quillfeather", "work_authorization": "VISA: H1B (ZQ-7731)"}
    assert json.loads(_saved(tmp_path, {"name": "Zora Quillfeather", "work_authorization": ""}).read_text(encoding="utf-8")) == {"name": "Zora Quillfeather", "work_authorization": ""}
    for key in ("github", "linkedin", "website"):
        target = tmp_path / f"{key}.json"
        with pytest.raises(HeaderSaveError, match="a value still starts with REPLACE") as refused:
            save_header_file(target, {**FORM_BODY, key: "REPLACE: your id"})
        assert not target.exists() and all(marker not in str(refused.value) for marker in MARKERS)
    with pytest.raises(HeaderSaveError, match="there is nothing to save"):
        file_content({"github": " ", "linkedin": "", "website": "", "links": [], "work_authorization": ""})


@pytest.mark.parametrize(("key", "typed"), [("github", "zq invalid 7731"), ("github", f"gitlab.example.invalid/{ID}"), ("linkedin", "in/zq invalid 7731"), ("website", ID)])
def test_a_value_that_is_not_an_id_is_refused_by_the_fields_name_and_nothing_is_written(tmp_path: Path, key: str, typed: str) -> None:
    path = tmp_path / "header.json"
    with pytest.raises(HeaderSaveError) as refused:
        save_header_file(path, {**FORM_BODY, key: typed})
    assert str(refused.value).startswith(f"{key} must be ") and not path.exists()
    assert all(marker not in str(refused.value) for marker in MARKERS), "the rule, never the value"
    # The sentence holds nothing shaped like an address: the API's outbound check leaves it as written.
    assert ".com" not in str(refused.value) or key == "website"


def test_a_saved_file_fills_the_form_with_the_same_values_and_saves_to_the_same_bytes(tmp_path: Path) -> None:
    typed = {**FORM_BODY, "email": "zora.q@example.invalid", "phone": "555-0142-ZQ", "links": [{"label": "Talks", "url": f"{SITE}/talks"}]}
    path = _saved(tmp_path, typed)
    first = path.read_bytes()
    found = read_header_file(path)
    assert found.state == STATE_FILLED and found.warning is None and found.placeholders == () and found.has_work_authorization
    # save -> reload: every field of the form holds what was typed (the form's "Other link" field is the one a file never fills).
    assert found.values == {**typed, "link": ""}
    # reload -> save: the form's values, sent as the form sends them, write the same file.
    again = {key: value for key, value in found.values.items() if key != "link"}
    assert file_content(again) == first
    # A file saved from pasted addresses reads back as the ids.
    pasted = _saved(tmp_path, {"name": "Zora Quillfeather", "github": f"https://github.com/{ID}", "linkedin": f"https://www.linkedin.com/in/{ID}/", "website": f"https://{SITE}"})
    values = read_header_file(pasted).values
    assert (values["github"], values["linkedin"], values["website"]) == (ID, ID, SITE) and values["links"] == []


def test_an_old_links_file_still_fills_the_id_fields(tmp_path: Path) -> None:
    path = tmp_path / "header.json"
    path.write_text(json.dumps({
        "name": "Zora Quillfeather",
        "links": [
            {"label": "GitHub", "url": f"https://github.com/{ID}"},
            {"label": "LinkedIn", "url": f"linkedin.com/in/{ID}"},
            {"label": "Website", "url": SITE},
            {"label": "Talks", "url": f"{SITE}/talks"},
        ],
    }), encoding="utf-8")
    path.chmod(0o600)
    values = read_header_file(path).values
    assert (values["github"], values["linkedin"], values["website"]) == (ID, ID, SITE)
    assert values["links"] == [{"label": "Talks", "url": f"{SITE}/talks"}], "the other links keep their own fields"
    # The shorthand key wins over a links row; a second profile of the same site stays a link.
    path.write_text(json.dumps({"github": ID, "links": [{"label": "Work", "url": "github.com/zq-work"}]}), encoding="utf-8")
    values = read_header_file(path).values
    assert values["github"] == ID and values["links"] == [{"label": "Work", "url": "github.com/zq-work"}]


def test_the_form_and_the_command_print_the_same_compact_line_from_a_saved_file(tmp_path: Path) -> None:
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    (work / "resume.md").write_text(MARKDOWN, encoding="utf-8")
    # Saved from addresses pasted into the id fields.
    path = _saved(work, {**FORM_BODY, "github": f"https://github.com/{ID}", "linkedin": f"https://www.linkedin.com/in/{ID}/", "website": f"https://www.{SITE}/"})

    # The form: the file's values, as the page sends them back in the render request.
    form = _text(render_markdown_pdf(MARKDOWN, form_header(parse_header_form(read_header_file(path).values)), timestamp=STAMP, auto_fit=False).pdf)
    # The command: the same file.
    made = CliRunner().invoke(cli, ["scout", "resume", "pdf", "--in", str(work / "resume.md"), "--home", str(home), "--out", str(work / "out.pdf"), "--header", str(path), "--json"])
    assert made.exit_code == 0, made.output
    pdf = (work / "out.pdf").read_bytes()
    command = _text(pdf)

    for text in (form, command):
        # Without white space: where a long contact line wraps is the layout's business.
        assert "".join(text.split()).startswith("".join(("ZORA QUILLFEATHER" + ONE_LINE).split())), text[:300]
        assert "https://" not in text and "www." not in text
    assert f"github.com/{ID}" in command and f"linkedin.com/in/{ID}" in command
    # Displayed without a scheme, clickable to the full address.
    assert _targets(pdf) == [f"https://github.com/{ID}", f"https://{SITE}", f"https://linkedin.com/in/{ID}"]


def test_the_form_never_refuses_a_pdf_over_a_link_that_is_not_an_id() -> None:
    # What the LinkedIn field held before item 16 (any address) still prints, as the plain link it is.
    odd = parse_header_form({"name": "Zora Quillfeather", "linkedin": "linkedin.example.invalid/in/zq", "github": "gitlab.example.invalid/zq"})
    assert [(item.text, item.url) for item in form_header(odd).contact] == [
        ("gitlab.example.invalid/zq", "https://gitlab.example.invalid/zq"), ("linkedin.example.invalid/in/zq", "https://linkedin.example.invalid/in/zq"),
    ]
    # An id, or its address, prints as the profile link.
    for typed in (ID, f"https://www.linkedin.com/in/{ID}/"):
        values = parse_header_form({"name": "Zora Quillfeather", "linkedin": typed})
        assert values["linkedin"] == ID and [item.text for item in form_header(values).contact] == [f"linkedin.com/in/{ID}"]


def test_one_reader_of_the_shorthand_and_nothing_new_reads_or_writes_the_file() -> None:
    """The expander is ``resume_display.shorthand_value``: no other module of the package holds a copy of its rules."""

    for module in (pdf_header_file, pdf_header_save):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert not {"_shorthand", "_bare", "shorthand_value", "shorthand_link"} & defined, f"{module.__name__} has its own expander"
    assert pdf_header_file.shorthand_value is resume_display.shorthand_value
    # The save still writes through one function, and still does not read the file.
    source = Path(pdf_header_save.__file__).read_text(encoding="utf-8")
    assert "read_header_file" not in source and "read_bytes" not in source and "read_text" not in source
