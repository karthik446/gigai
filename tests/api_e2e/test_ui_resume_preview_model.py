"""0.1.11.5 (a): the job page's resume preview model, UI side.

``ui/src/resumePreviewModel.js`` is pure JavaScript, run under the system ``node`` (LOUD skip when it is not on
PATH): the page count in plain words ("3 pages" when 2 is not reached, with the way back), the slider's range, the
header a preview request carries. What lives in JSX is checked statically, by reading the source: the preview is
asked for by ONE effect (never on a page read), an answer that is not for the latest request is dropped, the pages
on screen stay while the next are made, and nothing is kept in the browser's storage.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
const m = await import(process.argv[1]);
const answer = (pages, spacing_scale, extra = {}) => ({ pages, max_pages: 2, spacing_scale, saved: false, note: null, image_type: "image/png", images: Array(pages).fill("QUJD"), spacing: { min: 0.7, max: 1.4, step: 0.05 }, ...extra });
console.log(JSON.stringify({
  labels: [m.pagesLabel(1), m.pagesLabel(2), m.pagesLabel(3)],
  fits: m.pagesLine(m.previewOf(answer(2, 0.85))),
  over: m.pagesLine(m.previewOf(answer(3, 1.0))),
  overAtTightest: m.pagesLine(m.previewOf(answer(3, 0.7))),
  noLimit: m.pagesLine(m.previewOf(answer(3, 1.0, { max_pages: null }))),
  none: m.pagesLine(null),
  preview: m.previewOf(answer(2, 0.85, { saved: true, note: "A note." })),
  notOne: [m.previewOf(null), m.previewOf({ pages: 2 }), m.previewOf({ images: [] })],
  range: m.sliderRange(null),
  headerNamed: m.previewHeader({ name: "Zora Quillfeather", email: "" }),
  headerUnnamed: m.previewHeader({ name: "", email: "zora.q@example.invalid" }),
  keys: [m.headerKey(null), m.headerKey({ name: "A" }) === m.headerKey({ name: "A" }), m.headerKey({ name: "A" }) === m.headerKey({ name: "B" })],
  texts: [m.UPDATING_TEXT, m.LOADING_TEXT],
  waits: [m.SLIDER_DEBOUNCE_MS, m.HEADER_DEBOUNCE_MS],
}));
"""


def _run() -> dict[str, object]:
    if shutil.which("node") is None:
        pytest.skip("LOUD: node is not on PATH; the resume preview model was NOT checked")
    done = subprocess.run(["node", "--input-type=module", "-e", NODE_SCRIPT, (UI_SRC / "resumePreviewModel.js").as_uri()], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(done.stdout)


def test_the_page_count_is_said_in_plain_words() -> None:
    out = _run()
    assert out["labels"] == ["1 page", "2 pages", "3 pages"]
    assert out["fits"] == {"text": "2 pages", "over": False, "hint": None}
    assert out["over"] == {"text": "3 pages", "over": True, "hint": "Move the slider left to reach 2 pages."}
    assert out["overAtTightest"] == {"text": "3 pages", "over": True, "hint": "This is the tightest spacing: the resume does not reach 2 pages. Shorten it, or keep it at 3 pages."}
    assert out["noLimit"] == {"text": "3 pages", "over": False, "hint": None} and out["none"] is None
    assert out["texts"] == ["Updating the preview", "Making the preview"]


def test_the_answer_is_read_into_pictures_a_spacing_and_the_slider_range() -> None:
    out = _run()
    preview = out["preview"]
    assert preview["images"] == ["data:image/png;base64,QUJD"] * 2 and (preview["pages"], preview["maxPages"], preview["spacing"], preview["saved"], preview["note"]) == (2, 2, 0.85, True, "A note.")
    assert preview["range"] == out["range"] == {"min": 0.7, "max": 1.4, "step": 0.05}
    assert out["notOne"] == [None, None, None]
    # A header goes with the request only when it has a name (what Generate PDF needs too); the same header asks nothing new.
    assert out["headerNamed"] == {"name": "Zora Quillfeather", "email": ""} and out["headerUnnamed"] is None
    assert out["keys"] == ["", True, False]
    assert 0 < out["waits"][0] <= 500 and 0 < out["waits"][1] <= 1000, "the slider and the form are debounced, briefly"


def test_the_preview_is_asked_for_by_one_effect_and_keeps_nothing() -> None:
    code = lambda path: "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("//"))  # noqa: E731
    preview, panel, model = (code(UI_SRC / name) for name in ("components/ResumePreview.jsx", "components/JobResumePanel.jsx", "resumePreviewModel.js"))
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert preview.count("postResumePreview(") == 1 and preview.count("useEffect(") == 1, "one request, in one effect"
    # 0.1.11.5 (b): `content` is the stored resume's text: a change of its lines asks again too.
    assert "}, [profileId, jobIdentity, wanted, slider, content]);" in preview, "asked again only for another job, header, slider value or resume text"
    assert "if (sequence.current !== mine) {" in preview and "controller.abort()" in preview and "clearTimeout(timer)" in preview, "a stale answer is dropped, a stale request cancelled"
    assert "setPreview(null)" not in preview, "the pages on screen are never cleared while the next are made"
    assert api.count('request("POST", "/api/tailored-resumes/preview"') == 1 and "postResumePreview" not in panel, "the panel itself asks nothing"
    for source in (preview, model):
        for kept in ("localStorage", "sessionStorage", "document.cookie", "indexedDB", "location.hash", "history.", "console."):
            assert kept not in source, kept
    # Generate PDF sends the spacing on screen.
    apply = code(UI_SRC / "components" / "ApplyPanel.jsx")
    assert "postTailoredResumePdf({ profileId: state.profileId, jobIdentity: state.jobIdentity, header, spacingScale: spacing })" in apply
