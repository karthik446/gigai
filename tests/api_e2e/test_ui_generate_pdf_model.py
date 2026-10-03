"""0110-046: the Generate PDF form, UI side.

``ui/src/generatePdfModel.js`` is pure JavaScript, run under the system ``node``
(LOUD skip when it is not on PATH); the form component is checked statically.
The six values live in the form's state and travel only in the one render
request: no browser storage, no URL, no saved setting.
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
  body: m.headerBody({ name: "  Zora Quillfeather ", email: "zora.q@example.invalid", phone: 5550142, extra: "x", link: "y".repeat(300) }),
  bodyNull: m.headerBody(null),
  can: [m.canGenerate({ name: "Zora" }), m.canGenerate({ name: "  ", email: "a@b.c" }), m.canGenerate(null)],
  exports: Object.keys(m).sort(),
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
        "name": "name", "email": "email", "phone": "tel", "location": "address-level2", "linkedin": "url", "link": "url",
    }
    # a link is typed as text (a browser's type=url check refuses "linkedin.com/in/you")
    assert all(field["type"] != "url" for field in fields)
    assert out["maxValue"] == MAX_VALUE


def test_the_header_body_is_every_field_trimmed_and_capped() -> None:
    out = _run()
    assert out["empty"] == {key: "" for key in HEADER_FIELDS}
    assert out["body"] == {"name": "Zora Quillfeather", "email": "zora.q@example.invalid", "phone": "", "location": "", "linkedin": "", "link": "y" * MAX_VALUE}
    assert out["bodyNull"] == {key: "" for key in HEADER_FIELDS}
    assert out["can"] == [True, False, False]


def test_the_form_carries_the_one_privacy_notice_and_spells_out_none_of_it() -> None:
    """0.1.10.7 K, the one wording pass: the form shows the bold promise and the PDF line, from ``wording.js``.

    Was ``test_the_form_carries_no_privacy_wording`` (the wording was frozen until this pass: "PROMISE" and
    "never stores" had to be absent). The model still exports no wording of its own, and the form spells no
    sentence out: it shows the two shared constants.
    """
    assert _run()["exports"] == ["FIELDS", "MAX_VALUE", "canGenerate", "cleanupNotice", "emptyValues", "headerBody"]
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
    # The only fetches that carry `header` are the two PDF routes.
    assert re.findall(r"body\.header = header", api) == ["body.header = header", "body.header = header"]
    assert 'postPdf("/api/tailored-resumes/pdf", body)' in api and 'postPdf("/api/resume/pdf", body)' in api



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
