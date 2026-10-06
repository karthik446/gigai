"""0.1.11.3 item 14: "Save these details to <path>" in the Generate PDF form, and a header file with placeholders. Real server, real browser.

The file is `header.json` beside the resumes folder (here `<home>/header.json`, the default of a home that is not
`~/.gigai`; written through the page by this test and removed at its end). Pinned:

- the button names the real path and is off until something is typed; opening the form and typing write NOTHING;
- one click is ONE request (`POST /api/pdf-header/save`) and the file then holds exactly what was typed, mode 0600;
  the page says where it saved;
- with a file already there the click changes nothing and the form asks "Replace the existing header.json?":
  Cancel leaves the file as it was, Replace (a second request, with `replace`) writes the new details;
- a reload fills the form from the saved file;
- 0.1.11.3 item 16 (2f): the GitHub / LinkedIn / Website fields take an id (a site address); Save writes them as the
  file's shorthand (`"github": "<id>"`; an address pasted into the LinkedIn field is saved as the id), a reload shows
  the ids, and the PDF generated from them prints `github.com/<id>` without `https://`;
- a file that is all `REPLACE` placeholders fills nothing and says so, with "has no name yet"; a mixed one fills its
  real fields, names the skipped ones, and never shows a placeholder's text in a field;
- nothing of the details is kept by the browser (storage, cookie, address) or in any other file of the server's
  home. Zero console errors and no HTTP error in the whole flow.
"""

from __future__ import annotations

import io
import json
import os
import stat
from pathlib import Path
from urllib.parse import quote

import pytest

from tests.behaviors.scout_find_jobs.test_pdf_header_file import MARKERS
from tests.behaviors.scout_find_jobs.test_pdf_header_save import TEMPLATE

pytestmark = pytest.mark.ui

FORM = '[data-role="generate-pdf-form"]'
SOURCE = '[data-role="pdf-header-source"]'
SAVE = f'{FORM} [data-role="pdf-header-save"]'
ASK = f'{FORM} [data-role="pdf-header-replace"]'
SAID = f'{FORM} [data-role="pdf-header-saved"]'
NOTICE = f'{FORM} [data-role="pdf-header-placeholders"]'
#: Typed into the form by the test (the MARKERS of the header file's tests: no fixture holds them).
TYPED = {
    "name": "Zora Quillfeather",
    "email": "zora.q@example.invalid",
    "phone": "555-0142-ZQ",
    "location": "Quillshire, ZZ",
    "github": "zq-invalid-7731",  # the id alone
    "linkedin": "https://www.linkedin.com/in/zq-invalid-7731/",  # a full address pasted into the id field
    "website": "zq-invalid-7731.example.invalid",
    "link": "zq-invalid-7731.example.invalid/talks",
    "work_authorization": "VISA: H1B (ZQ-7731)",
}
#: What the click sends: the header file's shape, the id fields as typed; "Other link" is the one links row.
SENT = {
    "name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "555-0142-ZQ", "location": "Quillshire, ZZ",
    "github": "zq-invalid-7731", "linkedin": "https://www.linkedin.com/in/zq-invalid-7731/", "website": "zq-invalid-7731.example.invalid",
    "links": [{"label": "Link", "url": "zq-invalid-7731.example.invalid/talks"}],
    "work_authorization": "VISA: H1B (ZQ-7731)",
}
#: The file that click writes: the SHORTHAND (the pasted address saved as the id); links holds only the other link.
WRITTEN = {**SENT, "linkedin": "zq-invalid-7731"}
SECOND_NAME = "Zora Q. Quillfeather"
EMPTY = ["generate-pdf-name", "generate-pdf-email", "generate-pdf-phone", "generate-pdf-location", "generate-pdf-github", "generate-pdf-linkedin", "generate-pdf-website", "generate-pdf-link", "generate-pdf-work_authorization"]


def _fields(ui) -> dict[str, str]:
    return dict(ui.page.locator(f"{FORM} input").evaluate_all("(fields) => fields.map((field) => [field.id, field.value])"))


def _open(ui, address: str | None, *, filled: bool) -> str:
    ui.goto(address) if address else ui.reload()
    ui.page.locator(FORM).wait_for()
    line = ui.page.locator(f'{FORM} {SOURCE}[data-filled="{"true" if filled else "false"}"]')
    line.wait_for()
    return (line.text_content() or "").strip()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_save_these_details_writes_the_header_file_on_a_click_and_asks_before_replacing(ui, scout_server) -> None:
    from pypdf import PdfReader

    demo = scout_server.demo
    header_file = scout_server.root / "home" / "header.json"
    assert not header_file.exists(), "the synthetic home starts without a header file"
    address = "/#/pdf/" + quote(demo.hero_profile_id, safe="") + "/" + quote(demo.hero_job, safe="")
    try:
        # --- no file yet: the button names the real path and is off until something is typed ---
        said = _open(ui, address, filled=False)
        assert said.startswith("There is no header file at "), said
        button = ui.page.locator(SAVE)
        label = (button.text_content() or "").strip()
        assert label.startswith("Save these details to ") and label.endswith("/home/header.json") and str(scout_server.home) not in label, label
        assert label.removeprefix("Save these details to ") in said, "the path the form reads is the path it writes"
        assert button.is_disabled(), "nothing typed, nothing to save"
        ui.settle()
        ui.step("open")
        for key, value in TYPED.items():
            ui.page.fill(f"#generate-pdf-{key}", value)
        ui.settle()
        assert ui.writes_after("open") == [] and not header_file.exists(), "typing writes nothing"
        assert button.is_enabled()

        # --- the click: ONE request, exactly the typed fields, 0600, the path it wrote ---
        ui.step("typed")
        with ui.page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/pdf-header/save")) as sent:
            button.click()
        saved = ui.page.locator(f'{SAID}[data-state="saved"]')
        saved.wait_for()
        ui.settle()
        assert ui.writes_after("typed") == ["POST /api/pdf-header/save"]
        assert sent.value.post_data_json == SENT, "the request is the header file's shape, without replace"
        text = (saved.text_content() or "").strip()
        assert text.startswith("Saved your details to ") and text.endswith("/home/header.json. GigAI keeps no other copy."), text
        assert _read(header_file) == WRITTEN, "the file has exactly the fields typed, the links as the shorthand ids"
        assert "https://" not in header_file.read_text(encoding="utf-8"), "the pasted address is saved as the id"
        assert ui.page.locator(f"{FORM} {SOURCE}").count() == 0, "the line 'There is no header file' is gone once there is one"
        assert stat.S_IMODE(header_file.stat().st_mode) == 0o600
        assert sorted(path.name for path in header_file.parent.iterdir() if "header" in path.name) == ["header.json"], "no temporary file"

        # --- a file is there: the click changes nothing and the form asks; Cancel leaves it ---
        ui.page.fill("#generate-pdf-name", SECOND_NAME)
        ui.step("renamed")
        button.click()
        question = ui.page.locator(ASK)
        question.wait_for()
        assert "Replace the existing header.json?" in (question.text_content() or "")
        assert ui.page.locator(SAVE).count() == 0, "the question stands where the button was"
        ui.settle()
        assert ui.writes_after("renamed") == ["POST /api/pdf-header/save"] and _read(header_file) == WRITTEN, "asked first: the file is as it was"
        ui.step("asked")
        ui.page.locator(f'{FORM} [data-role="pdf-header-replace-no"]').click()
        ui.page.locator(SAVE).wait_for()
        ui.settle()
        assert ui.writes_after("asked") == [] and _read(header_file) == WRITTEN, "Cancel sends nothing and the file is unchanged"
        assert ui.page.locator(ASK).count() == 0 and _fields(ui)["generate-pdf-name"] == SECOND_NAME, "the form keeps what was typed"

        # --- Replace: a second request, with replace, writes the new details ---
        ui.page.locator(SAVE).click()
        ui.page.locator(ASK).wait_for()
        ui.settle()
        ui.step("asked-again")
        with ui.page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/pdf-header/save")) as confirmed:
            ui.page.locator(f'{FORM} [data-role="pdf-header-replace-yes"]').click()
        ui.page.locator(f'{SAID}[data-state="saved"]').wait_for()
        ui.settle()
        assert ui.writes_after("asked-again") == ["POST /api/pdf-header/save"]
        assert confirmed.value.post_data_json == {**SENT, "name": SECOND_NAME, "replace": True}
        assert _read(header_file) == {**WRITTEN, "name": SECOND_NAME} and stat.S_IMODE(header_file.stat().st_mode) == 0o600

        # --- kept nowhere else: not by the browser, not by the server ---
        kept = ui.page.evaluate("() => JSON.stringify([Object.entries(window.localStorage), Object.entries(window.sessionStorage), document.cookie, window.location.href])")
        cookies = str(ui.page.context.cookies())
        for marker in MARKERS:
            assert marker not in kept and marker not in cookies, f"the browser kept {marker!r}"
        holders = sorted(
            f"{path.relative_to(scout_server.home)}: {marker}"
            for path in scout_server.home.rglob("*")
            if path.is_file() and not path.is_symlink() and path != header_file
            for marker in MARKERS
            if marker.encode() in path.read_bytes()
        )
        assert holders == [], "a file of the server's home other than header.json holds a value"

        # --- a reload fills the form from the saved file ---
        said = _open(ui, None, filled=True)
        assert said.startswith("Filled from ") and _fields(ui) == {
            "generate-pdf-name": SECOND_NAME, "generate-pdf-email": "zora.q@example.invalid", "generate-pdf-phone": "555-0142-ZQ",
            "generate-pdf-location": "Quillshire, ZZ",
            # The ids typed are the ids shown; the address pasted into the LinkedIn field comes back as its id.
            "generate-pdf-github": "zq-invalid-7731", "generate-pdf-linkedin": "zq-invalid-7731", "generate-pdf-website": "zq-invalid-7731.example.invalid",
            "generate-pdf-link": "", "generate-pdf-links-0": "zq-invalid-7731.example.invalid/talks", "generate-pdf-work_authorization": "VISA: H1B (ZQ-7731)",
        }
        assert ui.page.locator(NOTICE).count() == 0 and ui.page.locator(SAVE).is_enabled()

        # --- the PDF made from the reloaded form: the ids print as links, without https:// ---
        ui.settle()
        ui.step("reloaded")
        with ui.page.expect_download() as waiting:
            with ui.page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/tailored-resumes/pdf")) as rendered:
                ui.page.locator('[data-role="generate-pdf"]').click()
        ui.page.locator('[data-role="pdf-saved"]').wait_for()
        assert ui.writes_after("reloaded") == ["POST /api/tailored-resumes/pdf"], "generating writes no header file"
        header = rendered.value.post_data_json["header"]
        assert (header["github"], header["linkedin"], header["website"]) == ("zq-invalid-7731", "zq-invalid-7731", "zq-invalid-7731.example.invalid")
        pdf = PdfReader(io.BytesIO(Path(waiting.value.path()).read_bytes()))
        text = "".join("".join(page.extract_text() for page in pdf.pages).split())
        assert "github.com/zq-invalid-7731" in text and "linkedin.com/in/zq-invalid-7731" in text, text[:400]
        assert "https://" not in text and "www." not in text, "the header shows a link without its scheme"
        targets = [annotation.get_object()["/A"]["/URI"] for annotation in pdf.pages[0].get("/Annots", [])]
        assert {"https://github.com/zq-invalid-7731", "https://linkedin.com/in/zq-invalid-7731", "https://zq-invalid-7731.example.invalid"} <= set(targets), "each is clickable to the full address"
        assert _read(header_file) == {**WRITTEN, "name": SECOND_NAME}, "the file is as it was saved"

        # --- a file that is all placeholders: nothing is filled, and the form says so ---
        header_file.write_text(json.dumps(TEMPLATE), encoding="utf-8")
        os.chmod(header_file, 0o600)
        said = _open(ui, None, filled=False)
        assert said.endswith("header.json still has placeholder values: replace the REPLACE: fields (or save your details here)."), said
        assert _fields(ui) == dict.fromkeys(EMPTY, "")
        assert (ui.page.locator(NOTICE).text_content() or "").strip().endswith("header.json has no name yet.")
        assert ui.page.locator('[data-role="generate-pdf"]').is_disabled(), "the form's own rule: no name, no PDF"

        # --- a mixed file: the real fields fill, the skipped ones are named, no placeholder reaches a field ---
        header_file.write_text(json.dumps({**TEMPLATE, "email": "zora.q@example.invalid", "location": "", "links": [{"label": "GitHub", "url": "github.com/zq-invalid-7731"}, {"label": "Site", "url": "REPLACE: your site"}]}), encoding="utf-8")
        _open(ui, None, filled=True)
        assert _fields(ui) == {**dict.fromkeys(EMPTY, ""), "generate-pdf-email": "zora.q@example.invalid", "generate-pdf-github": "zq-invalid-7731"}
        notice = (ui.page.locator(NOTICE).text_content() or "").strip()
        assert "still has placeholder values: replace the REPLACE: fields (or save your details here). Skipped: name, phone, links, work_authorization." in notice
        assert notice.endswith("header.json has no name yet."), notice
        page_text = ui.page.locator("body").inner_text()
        for value in ("your full name", "your phone", "your site", "linkedin.com/in/you", "delete this line"):
            assert value not in page_text, f"the page shows a placeholder's text: {value}"
        assert all("REPLACE" not in value for value in _fields(ui).values())
        assert ui.page.locator('[data-role="generate-pdf"]').is_disabled(), "no name yet: the form's own rule stays"

        ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
    finally:
        header_file.unlink(missing_ok=True)
