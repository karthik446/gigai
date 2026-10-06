"""0.1.11.3 item 13: the Generate PDF form fills itself from the user's own header file. Real server, real browser.

The person's file is `header.json` beside the resumes folder (here `<home>/header.json`, the default of a home that
is not `~/.gigai`; written by this test into the temporary home and removed at its end). Pinned:

- the open form says "Filled from <path>" and every field holds the file's value: name, email, phone, location, the
  LinkedIn link, one field per other link under its label, and the work authorization line;
- the values are EDITABLE and what is in the form is what prints: a name typed over the file's goes into the one
  render request and into the PDF; the file's untouched values print as they are; the file itself is not changed;
- nothing of it is kept by the browser (storage, cookie, address) or written by the server (no file of its home
  but the user's own holds a value);
- a file other users can read still fills the form, with a plain warning; an invalid file and a missing file are
  ONE plain sentence in the form (what is wrong, where the file goes), every field empty and still typeable: never
  an error page. Zero console errors and no HTTP error in the whole flow.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from urllib.parse import quote

import pytest

from tests.behaviors.scout_find_jobs.test_pdf_header_file import FILE, MARKERS

pytestmark = pytest.mark.ui

FORM = '[data-role="generate-pdf-form"]'
SOURCE = '[data-role="pdf-header-source"]'
WARNING = '[data-role="pdf-header-warning"]'
#: The form's inputs with the file in place, in order, and what each holds.
FILLED = {
    "generate-pdf-name": "Zora Quillfeather",
    "generate-pdf-email": "zora.q@example.invalid",
    "generate-pdf-phone": "555-0142-ZQ",
    "generate-pdf-location": "Quillshire, ZZ",
    "generate-pdf-linkedin": "linkedin.com/in/zq-invalid-7731",
    "generate-pdf-link": "",
    "generate-pdf-links-0": "https://github.com/zq-invalid-7731",
    "generate-pdf-links-1": "zq-invalid-7731.example.invalid",
    "generate-pdf-work_authorization": "VISA: H1B (ZQ-7731)",
}
EMPTY = ["generate-pdf-name", "generate-pdf-email", "generate-pdf-phone", "generate-pdf-location", "generate-pdf-linkedin", "generate-pdf-link", "generate-pdf-work_authorization"]
TYPED_NAME = "Riley Formedit"


def _write(path: Path, content: object, mode: int = 0o600) -> None:
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    os.chmod(path, mode)


def _fields(ui) -> dict[str, str]:
    return dict(ui.page.locator(f"{FORM} input").evaluate_all("(fields) => fields.map((field) => [field.id, field.value])"))


def _open(ui, address: str | None, *, filled: bool):
    """Open the page (`address`) or load it afresh (`None`) and wait for the form's line about the header file."""

    ui.goto(address) if address else ui.reload()
    ui.page.locator(FORM).wait_for()
    line = ui.page.locator(f'{FORM} {SOURCE}[data-filled="{"true" if filled else "false"}"]')
    line.wait_for()
    return (line.text_content() or "").strip()


def test_the_header_file_fills_the_generate_pdf_form_and_stays_editable(ui, scout_server) -> None:
    from pypdf import PdfReader

    demo = scout_server.demo
    header_file = scout_server.root / "home" / "header.json"
    assert not header_file.exists(), "the synthetic home starts without a header file"
    address = "/#/pdf/" + quote(demo.hero_profile_id, safe="") + "/" + quote(demo.hero_job, safe="")
    _write(header_file, FILE)
    try:
        # --- filled: "Filled from <path>", every field holds the file's value ---
        said = _open(ui, address, filled=True)
        assert said.startswith("Filled from ") and said.endswith("header.json. Edit anything below before you generate; the file is not changed."), said
        assert "/home/header.json" in said and str(scout_server.home) not in said, "the path is shown as the person types it (~/...)"
        assert _fields(ui) == FILLED
        assert list(_fields(ui)) == list(FILLED), "the file's other links sit between the link fields and the work authorization line"
        labels = ui.page.locator(f'{FORM} [data-role="generate-pdf-file-link"]').evaluate_all("(fields) => fields.map((field) => field.labels[0].textContent.trim())")
        assert labels == ["GitHub", "Link"], "each other link is a field under its own label"
        assert ui.page.locator(f"{FORM} {WARNING}").count() == 0, "a 0600 file has no warning"
        button = ui.page.locator('[data-role="generate-pdf"]')
        assert button.is_enabled(), "the file's name is enough to generate"

        # --- editable: what is in the form is what prints ---
        ui.page.fill("#generate-pdf-name", TYPED_NAME)
        ui.page.fill("#generate-pdf-links-1", "")
        assert _fields(ui) == {**FILLED, "generate-pdf-name": TYPED_NAME, "generate-pdf-links-1": ""}
        ui.settle()
        ui.step("edited")
        with ui.page.expect_download() as waiting:
            with ui.page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/tailored-resumes/pdf")) as sent:
                button.click()
        ui.page.locator('[data-role="pdf-saved"]').wait_for()
        assert ui.writes_after("edited") == ["POST /api/tailored-resumes/pdf"], "the file is read when the form opens, not again"
        assert sent.value.post_data_json["header"] == {
            "name": TYPED_NAME, "email": "zora.q@example.invalid", "phone": "555-0142-ZQ", "location": "Quillshire, ZZ",
            "linkedin": "linkedin.com/in/zq-invalid-7731", "link": "", "work_authorization": "VISA: H1B (ZQ-7731)",
            "links": [{"label": "GitHub", "url": "https://github.com/zq-invalid-7731"}],
        }
        name = waiting.value.suggested_filename
        assert "zora" not in name.lower() and "riley" not in name.lower(), "the file is named for the job"
        text = "".join("".join(page.extract_text() for page in PdfReader(io.BytesIO(Path(waiting.value.path()).read_bytes())).pages).split())
        # 0.1.11.3 items 15/16: ONE contact line: location | work authorization | links | email | phone.
        printed = "".join("Quillshire, ZZ | VISA: H1B (ZQ-7731) | github.com/zq-invalid-7731 | linkedin.com/in/zq-invalid-7731 | zora.q@example.invalid | 555-0142-ZQ".split())
        assert text.startswith("RILEYFORMEDIT"), "the name typed in the form wins over the file's"
        assert printed in text and "VISA:H1B(ZQ-7731)" in text, "the file's untouched values print as they are"
        assert "QUILLFEATHER" not in text.upper() and "zq-invalid-7731.example.invalid" not in text, "what was edited away is not printed"
        assert json.loads(header_file.read_text(encoding="utf-8")) == FILE, "the person's file is not changed"

        # --- kept nowhere: not by the browser, not by the server ---
        kept = ui.page.evaluate("() => JSON.stringify([Object.entries(window.localStorage), Object.entries(window.sessionStorage), document.cookie, window.location.href])")
        cookies = str(ui.page.context.cookies())
        for marker in (*MARKERS, TYPED_NAME):
            assert marker not in kept and marker not in cookies, f"the browser kept {marker!r}"
        holders = sorted(
            f"{path.relative_to(scout_server.home)}: {marker}"
            for path in scout_server.home.rglob("*")
            if path.is_file() and not path.is_symlink() and path != header_file
            for marker in (*MARKERS, TYPED_NAME)
            if marker.encode() in path.read_bytes()
        )
        assert holders == [], "the server's home holds a value of the header file"

        # --- a reload starts from the file again: the edit is not remembered ---
        _open(ui, None, filled=True)
        assert _fields(ui) == FILLED

        # --- a file other users can read: still used, with a plain warning ---
        os.chmod(header_file, 0o644)
        _open(ui, None, filled=True)
        warning = (ui.page.locator(f"{FORM} {WARNING}").text_content() or "").strip()
        assert "can be read by other users of this computer. To keep it to yourself: chmod 600 " in warning and warning.endswith("header.json")
        assert _fields(ui) == FILLED

        # --- an invalid file: one plain sentence, no value, the form empty and typeable ---
        _write(header_file, {**FILE, "phone": ["555-0142-ZQ"]})
        said = _open(ui, None, filled=False)
        assert said.endswith("header.json was not used: phone must be text in double quotes."), said
        assert _fields(ui) == dict.fromkeys(EMPTY, "") and ui.page.locator(f"{FORM} {WARNING}").count() == 0
        page_text = ui.page.locator("body").inner_text()
        for marker in MARKERS:
            assert marker not in page_text, f"the page shows {marker!r} of a file that was not used"
        ui.page.fill("#generate-pdf-name", TYPED_NAME)
        assert ui.page.locator('[data-role="generate-pdf"]').is_enabled(), "the form is still typed by hand"

        # --- no file: one plain sentence that says where it goes ---
        header_file.unlink()
        said = _open(ui, None, filled=False)
        assert said.startswith("There is no header file at ") and "keep your details in" in said and "GigAI only reads it when it makes a PDF." in said, said
        assert _fields(ui) == dict.fromkeys(EMPTY, "")

        ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
    finally:
        header_file.unlink(missing_ok=True)
