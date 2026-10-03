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


def test_the_form_carries_no_privacy_wording() -> None:
    """The privacy wording is rewritten later in one pass (operator, 2026-10-02): the form has labels and a button only."""
    assert _run()["exports"] == ["FIELDS", "MAX_VALUE", "canGenerate", "emptyValues", "headerBody"]
    form = (UI_SRC / "components" / "GeneratePdfForm.jsx").read_text(encoding="utf-8")
    assert "PROMISE" not in form and "LIMITS" not in form and "never stores" not in form


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

