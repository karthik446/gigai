"""0110-10-05 C: the "Cut for length" line of the tailored-resume panel (model + panel), under node.

``result.length`` (``tailor_length.LengthFit``) says what a tailoring left out for
length.  What is pinned:

* the model words one line per status and offers the one action: ``Restore`` while
  roles or bullets are left out, ``Cut for length again`` once they are back, none
  when nothing was cut (over the limit, or not measured);
* a resume with no ``length`` has no line at all;
* the panel renders the line and the button (``PUT /api/tailored-resumes/length``),
  no button without a handler, and role text as text (never markup).

The length records are the product's own (``fit_to_pages`` on a synthetic result),
not hand-written JSON.  LOUD skip when ``node`` or the ui ``node_modules`` is missing.
"""

from __future__ import annotations

from datetime import date
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.tailor_length import fit_to_pages, restore_cut
from gigai.scout.tailored_resume import TailorJob, TailoredResume, apply_no_loss, validate_tailored_output

from tests.support.tailor_cases import CASES, copy_everything

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
LENGTH_JS = UI / "src" / "tailorLengthModel.js"
PANEL_JSX = UI / "src" / "components" / "TailoredResumePanel.jsx"
API_JS = UI / "src" / "api.js"


def _settled(case_id: str) -> TailoredResume:
    from gigai.scout.tailored_resume import tailor_context

    case = CASES[case_id]
    job = TailorJob(title=case["title"], company=case["company"], location="", posting_text=case["posting"])
    ctx = tailor_context(case["resume"])
    return apply_no_loss(validate_tailored_output(copy_everything(case["resume"]), job, ctx), job, ctx, today=date(2026, 10, 3))


def _response(result: TailoredResume) -> dict:
    return {"result": result.to_json(), "resume": {"profile_id": "p1"}, "updated_at": "2026-10-03T10:00:00+00:00", "stored_path": "/x.json"}


def _pages(result: TailoredResume) -> int:
    return 2 if sum(len(entry.bullets) for section in result.sections for entry in section.entries) <= 39 else 3


CUT = fit_to_pages(_settled("over_long"), measure=_pages)
RESTORED = restore_cut(CUT)
FITS = fit_to_pages(_settled("control"), measure=lambda _result: 1)
OVER = fit_to_pages(_settled("control"), measure=lambda _result: 3)
UNMEASURED = fit_to_pages(_settled("control"), measure=lambda _result: None)
_WHAT = (
    "Junior Developer — Bellweather Retail (2009–2011); Junior Developer — Dunmore Telecom (2007–2009); 15 older bullets "
    "(3 of Software Engineer — Ostrava Health (2015–2017), 3 of Software Engineer — Pinecrest Travel (2013–2015), "
    "3 of Software Engineer — Harrow Analytics (2011–2013), 3 of Junior Developer — Bellweather Retail (2009–2011), "
    "3 of Junior Developer — Dunmore Telecom (2007–2009))"
)


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; tailor length line check not run")
    return node


def _run(script: str, payload: dict) -> dict:
    completed = subprocess.run([_node(), "--input-type=module", "-e", script, "--", json.dumps(payload)], capture_output=True, text=True, timeout=120, check=False, cwd=UI)
    assert completed.returncode == 0, f"node failed:\n{completed.stderr}"
    return json.loads(completed.stdout)


MODEL_SCRIPT = f"""
import {{ lengthNote }} from {json.dumps(LENGTH_JS.resolve().as_uri())};
const input = JSON.parse(process.argv[1]);
const out = {{}};
for (const [name, response] of Object.entries(input)) out[name] = lengthNote(response);
process.stdout.write(JSON.stringify(out));
"""


def test_the_line_names_what_was_cut_and_offers_the_one_action() -> None:
    assert CUT.length is not None and (CUT.length.pages, CUT.length.full_pages) == (2, 3) and FITS.length is None
    out = _run(MODEL_SCRIPT, {"cut": _response(CUT), "restored": _response(RESTORED), "fits": _response(FITS), "over": _response(OVER), "unmeasured": _response(UNMEASURED), "none": {}})
    assert out["cut"] == {"status": "cut", "text": f"Cut for length (3 pages to 2): {_WHAT}.", "action": {"use": "restore", "label": "Restore"}}
    assert out["restored"] == {
        "status": "restored",
        "text": f"Put back (was cut for length): {_WHAT}. The resume is now 3 pages, over the 2-page limit.",
        "action": {"use": "cut", "label": "Cut for length again"},
    }
    assert out["fits"] is None and out["none"] is None
    assert out["over"] == {"status": "over", "text": "3 pages, over the 2-page limit. Leaving out older roles would not fix it, so nothing was cut.", "action": None}
    assert out["unmeasured"] == {"status": "unmeasured", "text": "Length not checked: the pages could not be measured, so nothing was cut. The 2-page limit applies.", "action": None}


SSR_SCRIPT = f"""
import {{ build }} from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import fs from "node:fs";
const input = JSON.parse(process.argv[1]);
const out = fs.mkdtempSync(path.join(process.cwd(), "node_modules", ".tailor-length-ssr-"));
await build({{ root: process.cwd(), logLevel: "silent", plugins: [react()], build: {{ ssr: {json.dumps(PANEL_JSX.resolve().as_posix())}, outDir: out, emptyOutDir: true, rollupOptions: {{ output: {{ format: "esm", entryFileNames: "panel.mjs" }} }} }} }});
const {{ Preview }} = await import(path.join(out, "panel.mjs"));
const React = (await import("react")).default;
const {{ renderToStaticMarkup }} = await import("react-dom/server");
const render = (response, handler) => renderToStaticMarkup(React.createElement(Preview, {{ response, profileLabel: "p1", promptFor: () => null, onChooseLine: handler ? () => {{}} : null, onLength: handler ? () => {{}} : null }}));
process.stdout.write(JSON.stringify({{ cut: render(input.cut, true), restored: render(input.restored, true), readonly: render(input.cut, false), fits: render(input.fits, true) }}));
fs.rmSync(out, {{ recursive: true, force: true }});
"""


def test_the_panel_shows_the_line_with_restore_and_nothing_when_the_resume_fits() -> None:
    if not (UI / "node_modules" / "vite").is_dir():
        pytest.skip("ui/node_modules missing; panel render check not run")
    evil = _response(CUT)
    evil["result"]["length"]["cut"][0]["role"] = "<script>alert(1)</script> role"
    out = _run(SSR_SCRIPT, {"cut": evil, "restored": _response(RESTORED), "fits": _response(FITS)})
    cut = out["cut"]
    assert 'data-testid="length-note"' in cut and 'data-status="cut"' in cut and "Cut for length (3 pages to 2): " in cut
    assert 'data-action="length-restore"' in cut and ">Restore</button>" in cut
    assert "<script>" not in cut and "&lt;script&gt;alert(1)&lt;/script&gt; role" in cut
    assert "Dunmore Telecom" in cut
    restored = out["restored"]
    assert 'data-status="restored"' in restored and 'data-action="length-cut"' in restored and ">Cut for length again</button>" in restored
    assert 'data-testid="length-note"' in out["readonly"] and "length-restore" not in out["readonly"], "no handler: the line, no button"
    assert 'data-testid="length-note"' not in out["fits"]


def test_the_panel_sends_the_action_to_the_length_route() -> None:
    api, panel = API_JS.read_text(encoding="utf-8"), PANEL_JSX.read_text(encoding="utf-8")
    assert 'request("PUT", "/api/tailored-resumes/length"' in api
    assert "putTailoredResumeLength" in panel and "onLength={changeLength}" in panel and "lengthNote(response)" in panel
