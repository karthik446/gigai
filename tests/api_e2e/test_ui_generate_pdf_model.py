"""0110-046: the Generate PDF form, UI side.

``ui/src/generatePdfModel.js`` is pure JavaScript, run under the system ``node``
(LOUD skip when it is not on PATH); the form component is checked statically.
The values live in the form's state and travel only in the one render
request: no browser storage, no URL, no saved setting.  That holds for the
optional "Work authorization" line too (0.1.11.3 item 6): it starts from the
profile's sponsorship answer every time and an edit is never remembered.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.resume_display import HEADER_FIELDS, MAX_VALUE

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
const m = await import(process.argv[1]);
console.log(JSON.stringify({
  fields: m.FIELDS,
  maxValue: m.MAX_VALUE,
  empty: m.emptyValues(),
  start: [m.startValues({ visaRequired: true }), m.startValues({ visaRequired: false }), m.startValues()],
  prefill: m.WORK_AUTHORIZATION_PREFILL,
  body: m.headerBody({ name: "  Zora Quillfeather ", email: "zora.q@example.invalid", phone: 5550142, extra: "x", link: "y".repeat(300) }),
  bodyNull: m.headerBody(null),
  can: [m.canGenerate({ name: "Zora" }), m.canGenerate({ name: "  ", email: "a@b.c" }), m.canGenerate(null)],
  exports: Object.keys(m).sort(),
  file: (() => {
    const values = { name: "Zora Quillfeather", email: "zora.q@example.invalid", phone: "555-0142-ZQ", location: "Quillshire, ZZ", github: "zq", linkedin: "zq", website: "zq.example.invalid/site", link: "", work_authorization: "VISA: H1B", links: [{ label: "GitHub", url: " github.com/zq/tool " }, "junk", { label: 7, url: "zq.example.invalid" }] };
    const filled = { state: "filled", shown: "~/Documents/GigAI/header.json", message: "Filled from ~/Documents/GigAI/header.json", warning: null, has_work_authorization: true, values };
    const noKey = { ...filled, has_work_authorization: false, values: { ...values, work_authorization: "" } };
    const emptyKey = { ...filled, values: { ...values, work_authorization: "" } };
    const loose = { ...filled, warning: "~/Documents/GigAI/header.json can be read by other users of this computer." };
    const missing = { state: "missing", shown: "~/Documents/GigAI/header.json", message: "There is no header file at ~/Documents/GigAI/header.json.", warning: null, values: null };
    const invalid = { state: "invalid", shown: "~/Documents/GigAI/header.json", message: "~/Documents/GigAI/header.json was not used: phone must be text in double quotes.", warning: null, values: null };
    const start = m.startValues({ visaRequired: true, file: filled });
    return {
      start,
      body: m.headerBody(start),
      edited: m.headerBody({ ...start, name: " Riley Formedit ", work_authorization: "", links: [{ label: "GitHub", url: "" }, { label: "Site", url: "riley.example.invalid" }] }),
      lines: [m.startValues({ visaRequired: true, file: noKey }), m.startValues({ visaRequired: false, file: noKey }), m.startValues({ visaRequired: true, file: emptyKey })].map((v) => v.work_authorization),
      unfilled: [m.startValues({ visaRequired: true, file: missing }), m.startValues({ visaRequired: true, file: invalid }), m.startValues({ visaRequired: true, file: null })],
      sources: [m.headerSource(filled), m.headerSource(loose), m.headerSource(missing), m.headerSource(invalid), m.headerSource(null), m.headerSource({})],
      values: [m.fileValues(filled) === values, m.fileValues(missing), m.fileValues({ state: "filled" }), m.fileValues(null)],
      many: m.headerBody({ name: "x", links: Array.from({ length: 9 }, (_, n) => ({ label: "L", url: "x.invalid/" + n })) }).links.length,
      maxLinks: m.MAX_LINKS,
    };
  })(),
  save: (() => {
    const shown = "~/Documents/GigAI/header.json";
    const typed = { name: " Zora Quillfeather ", email: "zora.q@example.invalid", phone: "", location: "Quillshire, ZZ", github: " https://github.com/zq ", linkedin: " zq ", website: "", link: "zq.example.invalid", work_authorization: "", links: [{ label: "Talks", url: "zq.example.invalid/talks" }, { label: "Site", url: " " }] };
    const sentence = shown + " still has placeholder values: replace the REPLACE: fields (or save your details here).";
    const template = { state: "placeholder", shown, message: sentence, warning: null, has_work_authorization: false, values: null, placeholders: ["name", "email"], notice: sentence, name_note: shown + " has no name yet." };
    const mixed = { state: "filled", shown, message: "Filled from " + shown, warning: null, has_work_authorization: false, placeholders: ["name", "phone"], notice: sentence + " Skipped: name, phone.", name_note: shown + " has no name yet.", values: { name: "", email: "zora.q@example.invalid", phone: "", location: "", linkedin: "", link: "", work_authorization: "", links: [] } };
    return {
      question: m.REPLACE_QUESTION,
      body: m.headerFileBody(typed),
      bodyEmpty: m.headerFileBody(null),
      can: [m.canSave(typed), m.canSave({ name: " " }), m.canSave(null), m.canSave({ work_authorization: "H-1B" }), m.canSave({ links: [{ label: "x", url: "y.invalid" }] }), m.canSave({ github: "zq" }), m.canSave({ linkedin: "zq" }), m.canSave({ website: "zq.example.invalid" }), m.canSave({ github: " " })],
      results: [
        m.saveResult({ state: "saved", message: "Saved your details to " + shown + "." }),
        m.saveResult({ state: "exists", message: "There is already a file at " + shown + "." }),
        m.saveResult({ state: "not_writable", message: "Your details were not saved: GigAI cannot write to ~/Documents/GigAI." }),
        m.saveResult(null),
      ],
      sources: [m.headerSource(template), m.headerSource(mixed)],
      starts: [m.startValues({ visaRequired: true, file: template }), m.startValues({ visaRequired: true, file: mixed })],
    };
  })(),
  notices: [
    m.cleanupNotice({ removed_any: true, shown: false, text: "Removed contact details from 1 stored resume (email 1)." }),
    m.cleanupNotice({ removed_any: true, shown: true, text: "x" }),
    m.cleanupNotice({ removed_any: false, shown: false, text: "x" }),
    m.cleanupNotice(null),
  ],
}));
"""

def _run() -> dict[str, object]:
    if shutil.which("node") is None:
        pytest.skip("LOUD: node is not on PATH; the Generate PDF model was NOT checked")
    done = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT, (UI_SRC / "generatePdfModel.js").as_uri()],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(done.stdout)


def test_six_fields_with_the_standard_autocomplete_tokens_in_the_servers_order() -> None:
    out = _run()
    fields = out["fields"]
    assert [field["key"] for field in fields] == list(HEADER_FIELDS)
    assert {field["key"]: field["autocomplete"] for field in fields} == {
        "name": "name", "email": "email", "phone": "tel", "location": "address-level2",
        "github": "off", "linkedin": "off",  # 0.1.11.3 item 16: an id, not a URL the browser should offer
        "website": "url", "link": "url",
        "work_authorization": "off",  # 0.1.11.3 item 6: not a browser-autofill value; the form prefills it itself
    }
    labels = {field["key"]: field["label"] for field in fields}
    assert (labels["github"], labels["linkedin"], labels["website"], labels["link"]) == ("GitHub", "LinkedIn", "Website", "Other link")
    assert [field["key"] for field in fields if field.get("hint")] == ["github", "linkedin", "work_authorization"]
    # The id fields say what prints; nothing in the form's model expands or checks an id (the server's one reader does).
    hints = {field["key"]: field["hint"] for field in fields if field.get("hint")}
    assert "github.com/<id>" in hints["github"] and "linkedin.com/in/<id>" in hints["linkedin"]
    model = (UI_SRC / "generatePdfModel.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in model.splitlines() if not line.lstrip().startswith("//"))
    assert "https://" not in code and "replace(" not in code and "RegExp" not in code, "no second copy of the shorthand's rules in the page"
    # a link is typed as text (a browser's type=url check refuses "example.com")
    assert all(field["type"] != "url" for field in fields)
    assert out["maxValue"] == MAX_VALUE


def test_the_header_body_is_every_field_trimmed_and_capped() -> None:
    out = _run()
    assert out["empty"] == {key: "" for key in HEADER_FIELDS}
    assert out["body"] == {
        "name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "", "location": "", "github": "", "linkedin": "", "website": "", "link": "y" * MAX_VALUE,
        "work_authorization": "",
    }
    assert out["bodyNull"] == {key: "" for key in HEADER_FIELDS}
    assert out["can"] == [True, False, False]


def test_the_form_carries_the_one_privacy_notice_and_spells_out_none_of_it() -> None:
    """0.1.10.7 K, the one wording pass: the form shows the bold promise and the PDF line, from ``wording.js``.

    Was ``test_the_form_carries_no_privacy_wording`` (the wording was frozen until this pass: "PROMISE" and
    "never stores" had to be absent). The model still exports no wording of its own, and the form spells no
    sentence out: it shows the two shared constants.
    """
    assert _run()["exports"] == [
        "FIELDS", "MAX_LINKS", "MAX_VALUE", "REPLACE_QUESTION", "WORK_AUTHORIZATION_PREFILL", "canGenerate", "canSave", "cleanupNotice", "emptyValues",
        "fileValues", "headerBody", "headerFileBody", "headerSource", "saveResult", "startValues",
    ]
    form = (UI_SRC / "components" / "GeneratePdfForm.jsx").read_text(encoding="utf-8")
    assert 'import { PRIVACY_PDF_LINE, PRIVACY_PROMISE } from "../wording.js";' in form
    assert "<strong>{PRIVACY_PROMISE}</strong> {PRIVACY_PDF_LINE}" in form and 'data-role="pdf-privacy"' in form
    assert "LIMITS" not in form and "never stores" not in form


def test_the_form_keeps_nothing_and_sends_the_values_only_in_the_render_request() -> None:
    form, model = (
        "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("//"))
        for path in (UI_SRC / "components" / "GeneratePdfForm.jsx", UI_SRC / "generatePdfModel.js")
    )
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    for source in (form, model):
        for kept in ("localStorage", "sessionStorage", "document.cookie", "indexedDB", "location.hash", "history.", "console."):
            assert kept not in source, kept
    assert 'autoComplete={field.autocomplete}' in form and 'onSubmit={submit}' in form and "Generate PDF" in form
    assert "render(headerBody(values))" in form
    # 0.1.11.3 item 6: the form starts from the profile's answer each time it mounts, and imports no storage helper.
    assert "useState(() => startValues({ visaRequired }))" in form and "theme.js" not in form and "Storage" not in form
    # 0.1.11.3 item 13: the header file's values come from the server once per open form and live in the same state.
    assert "postPdfHeader().then((file) => {" in form and "setValues(startValues({ visaRequired, file }));" in form
    assert form.count("postPdfHeader(") == 1 and "}, []);" in form, "asked once, when the form opens"
    assert api.count('fetch("/api/pdf-header", { method: "POST"') == 1 and api.count("/api/pdf-header") - api.count("/api/pdf-header/save") == 2, "one caller, and its comment"
    # 0.1.11.3 item 14: the Save button is the one other request that carries the values: one caller, on a click only.
    assert api.count('request("POST", "/api/pdf-header/save"') == 1 and api.count("/api/pdf-header/save") == 1, "one caller"
    assert form.count("postPdfHeaderSave(") == 1 and form.count("saveDetails(") == 3, "the save function, called by the Save and the Replace buttons only"
    assert "onClick={() => saveDetails(false)}" in form and "onClick={() => saveDetails(true)}" in form
    assert "useEffect(() => {" in form and form.count("useEffect(") == 1, "nothing but the prefill runs without a click"
    # The only fetches that carry `header` are the two PDF routes and (0.1.11.5) the job page's preview of that PDF.
    assert re.findall(r"body\.header = header", api) == ["body.header = header"] * 3
    assert 'postPdf("/api/tailored-resumes/pdf", body)' in api and 'postPdf("/api/resume/pdf", body)' in api
    assert 'request("POST", "/api/tailored-resumes/preview", body, { signal })' in api
    # The form tells the job page its values (for that preview) without a request or an effect of its own.
    assert form.count("onValues(held.current)") == 2 and "fetch(" not in form



ROUTING_SCRIPT = """
const r = await import(process.argv[1]);
console.log(JSON.stringify({
  bare: r.parseHash("#/pdf"),
  slash: r.parseHash("#/pdf/"),
  job: r.parseHash("#/pdf/prof_1/https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F101"),
  target: r.parsePdfTarget("prof_1/https://boards.greenhouse.io/acme/jobs/101"),
  empty: [r.parsePdfTarget(undefined), r.parsePdfTarget(""), r.parsePdfTarget("prof_1"), r.parsePdfTarget("prof_1/")],
  hash: r.pdfHash("prof_1", "https://boards.greenhouse.io/acme/jobs/101"),
  hashBare: r.pdfHash(null, null),
}));
"""


def test_the_finish_link_opens_the_generate_pdf_page_for_that_resume(tmp_path: Path) -> None:
    """The server's X-GigAI-Finish-Url / the CLI's link (``resume_pdf.finish_url``) parse back to the profile and job."""
    from urllib.parse import urlsplit

    from gigai.scout.resume_pdf import finish_url
    from tests.api_e2e.test_ui_uat_batch2_model import _routing_without_react

    if shutil.which("node") is None:
        pytest.skip("LOUD: node is not on PATH; the Generate PDF route was NOT checked")
    out = json.loads(subprocess.run(
        ["node", "--input-type=module", "-e", ROUTING_SCRIPT, _routing_without_react(tmp_path).as_uri()],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout)
    job = "https://boards.greenhouse.io/acme/jobs/101"
    assert out["bare"] == {"view": "pdf", "params": {}, "known": True}
    assert out["slash"]["view"] == "pdf"
    assert out["job"] == {"view": "pdf", "params": {"pdfTarget": f"prof_1/{job}"}, "known": True}
    assert out["target"] == {"profileId": "prof_1", "jobIdentity": job}
    assert out["empty"] == [{"profileId": None, "jobIdentity": None}] * 4
    link = finish_url("http://127.0.0.1:8765", "prof_1", job)
    assert link == "http://127.0.0.1:8765" + "/" + out["hash"]
    assert "#" + urlsplit(link).fragment == out["hash"] and finish_url("http://127.0.0.1:8765/") == "http://127.0.0.1:8765/" + out["hashBare"]
    view = (UI_SRC / "views" / "PdfView.jsx").read_text(encoding="utf-8")
    assert "<GeneratePdfForm" in view and "postTailoredResumePdf" in view and "postResumePdf" in view
    app = (UI_SRC / "App.jsx").read_text(encoding="utf-8")
    assert 'route.view === "pdf" && <PdfView target={route.params.pdfTarget} />' in app


def test_the_cleanup_report_shows_once_and_only_when_something_was_removed() -> None:
    assert _run()["notices"] == ["Removed contact details from 1 stored resume (email 1).", None, None, None]
    app = (UI_SRC / "App.jsx").read_text(encoding="utf-8")
    assert "getPrivacyCleanup()" in app and "putPrivacyCleanupShown()" in app and 'data-role="app-notice"' in app


def test_the_work_authorization_line_starts_from_the_profiles_sponsorship_answer() -> None:
    """0.1.11.3 item 6: the profile stores yes/no only, so "yes" prefills a plain sentence and "no" leaves the line empty;
    the six contact values always start empty.  Nothing is read from anywhere else: an edit is never remembered."""
    out = _run()
    blank = {key: "" for key in HEADER_FIELDS}
    assert out["prefill"] == "Requires visa sponsorship"
    assert out["start"] == [{**blank, "work_authorization": "Requires visa sponsorship"}, blank, blank]
    assert not (UI_SRC / "workAuthorizationModel.js").exists(), "no module remembers the wording"


def test_the_header_file_fills_the_form_and_an_edit_wins() -> None:
    """0.1.11.3 item 13: form edits > the header file > the profile's sponsorship answer.

    The file's values (``POST /api/pdf-header``) are what the form STARTS with; the request carries what is in the
    form when Generate is pressed.  The profile's answer fills the work authorization line only when the file has
    no such key at all; a missing or invalid file leaves the form as it was and says one plain sentence."""
    from gigai.scout.pdf_header_file import SPONSORSHIP_DEFAULT
    from gigai.scout.resume_display import MAX_LINKS, parse_header_form

    out = _run()
    file = out["file"]
    assert file["start"] == {
        "name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "555-0142-ZQ", "location": "Quillshire, ZZ",
        "github": "zq", "linkedin": "zq", "website": "zq.example.invalid/site",  # 0.1.11.3 item 16: the file's shorthand fills the id fields
        "link": "", "work_authorization": "VISA: H1B", "links": [{"label": "GitHub", "url": "github.com/zq/tool"}, {"label": "", "url": "zq.example.invalid"}],
    }, "the file's line wins over the profile's answer (visaRequired was true)"
    assert file["body"] == file["start"] and parse_header_form(file["body"]) == file["start"], "untouched, the request carries the file's values and the server takes them"
    # An edit wins: the name typed over, the line cleared, one link emptied (dropped) and one changed.
    assert file["edited"] == {**file["start"], "name": "Riley Formedit", "work_authorization": "", "links": [{"label": "Site", "url": "riley.example.invalid"}]}
    assert file["lines"] == [SPONSORSHIP_DEFAULT, "", ""], "no key: the profile's answer; an empty key: no line"
    assert out["prefill"] == SPONSORSHIP_DEFAULT, "the form and the command line say the same sentence"
    blank = {key: "" for key in HEADER_FIELDS}
    assert file["unfilled"] == [{**blank, "work_authorization": SPONSORSHIP_DEFAULT}] * 3, "no usable file: the form is what it was"
    shown = "~/Documents/GigAI/header.json"
    rest = {"shown": shown, "notice": None, "nameNote": None}  # 0.1.11.3 item 14: the path the Save button names; no placeholders here
    assert file["sources"] == [
        {"filled": True, "text": f"Filled from {shown}", "warning": None, **rest},
        {"filled": True, "text": f"Filled from {shown}", "warning": f"{shown} can be read by other users of this computer.", **rest},
        {"filled": False, "text": f"There is no header file at {shown}.", "warning": None, **rest},
        {"filled": False, "text": f"{shown} was not used: phone must be text in double quotes.", "warning": None, **rest},
        None, None,
    ]
    assert file["values"] == [True, None, None, None]
    assert file["many"] == file["maxLinks"] == MAX_LINKS
    form = (UI_SRC / "components" / "GeneratePdfForm.jsx").read_text(encoding="utf-8")
    assert 'data-role="pdf-header-source"' in form and 'data-role="pdf-header-warning"' in form and 'data-role="generate-pdf-file-link"' in form


def test_the_save_button_sends_the_header_files_shape_and_placeholders_fill_nothing() -> None:
    """0.1.11.3 item 14: what "Save these details to <path>" sends, what the form says back, and a file with ``REPLACE`` placeholders.

    The request is the header FILE's shape (the reader's own rules take it), from what is in the form at the click.
    A file that is all placeholders fills nothing; a mixed one fills its real fields and the form names the rest."""
    from gigai.scout.pdf_header_file import form_values
    from gigai.scout.pdf_header_save import REPLACE_QUESTION, file_content

    save = _run()["save"]
    assert save["question"] == REPLACE_QUESTION == "Replace the existing header.json?"
    assert save["body"] == {
        "name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "", "location": "Quillshire, ZZ",
        # 0.1.11.3 item 16: the id fields go as the file's shorthand keys, as typed (the server saves the id alone);
        # links holds only the other links: the "Other link" field, then the file's own rows.
        "github": "https://github.com/zq", "linkedin": "zq", "website": "",
        "links": [{"label": "Link", "url": "zq.example.invalid"}, {"label": "Talks", "url": "zq.example.invalid/talks"}],
        "work_authorization": "",
    }
    assert list(save["body"]) == ["name", "email", "phone", "location", "github", "linkedin", "website", "links", "work_authorization"]
    # The server writes the shorthand: the pasted address as the id, no empty field (work_authorization excepted).
    assert json.loads(file_content(save["body"])) == {
        "name": "Zora Quillfeather", "email": "zora.q@example.invalid", "location": "Quillshire, ZZ", "github": "zq", "linkedin": "zq",
        "links": [{"label": "Link", "url": "zq.example.invalid"}, {"label": "Talks", "url": "zq.example.invalid/talks"}], "work_authorization": "",
    }
    # Read back, the id fields hold the ids again and the other links are rows.
    values, has_line, placeholders = form_values(json.loads(file_content(save["body"])))
    assert (values["github"], values["linkedin"], values["website"]) == ("zq", "zq", "")
    assert values["links"] == [{"label": "Link", "url": "zq.example.invalid"}, {"label": "Talks", "url": "zq.example.invalid/talks"}]
    assert has_line is True and placeholders == ()
    assert save["bodyEmpty"] == {"name": "", "email": "", "phone": "", "location": "", "github": "", "linkedin": "", "website": "", "links": [], "work_authorization": ""}
    assert save["can"] == [True, False, False, True, True, True, True, True, False], "something typed is enough (an id counts); nothing typed is not"
    shown = "~/Documents/GigAI/header.json"
    assert save["results"] == [
        {"tone": "saved", "text": f"Saved your details to {shown}.", "ask": False},
        {"tone": "ask", "text": f"There is already a file at {shown}.", "ask": True},
        {"tone": "failed", "text": "Your details were not saved: GigAI cannot write to ~/Documents/GigAI.", "ask": False},
        {"tone": "failed", "text": "Your details were not saved.", "ask": False},
    ]
    sentence = f"{shown} still has placeholder values: replace the REPLACE: fields (or save your details here)."
    assert save["sources"] == [
        {"filled": False, "text": sentence, "warning": None, "shown": shown, "notice": None, "nameNote": f"{shown} has no name yet."},
        {"filled": True, "text": f"Filled from {shown}", "warning": None, "shown": shown, "notice": sentence + " Skipped: name, phone.", "nameNote": f"{shown} has no name yet."},
    ]
    blank = {key: "" for key in HEADER_FIELDS}
    assert save["starts"] == [
        {**blank, "work_authorization": "Requires visa sponsorship"},  # all placeholders: the form is what it was
        {**blank, "email": "zora.q@example.invalid", "work_authorization": "Requires visa sponsorship"},  # the real field; the profile's line
    ]
    form = (UI_SRC / "components" / "GeneratePdfForm.jsx").read_text(encoding="utf-8")
    for role in ("pdf-header-save", "pdf-header-replace", "pdf-header-replace-yes", "pdf-header-replace-no", "pdf-header-saved", "pdf-header-placeholders"):
        assert f'data-role="{role}"' in form, role
    assert "`Save these details to ${source.shown}`" in form and "{REPLACE_QUESTION}" in form
