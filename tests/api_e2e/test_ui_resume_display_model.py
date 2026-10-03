"""0.1.10-003 1b / 0110-046: the Resume display settings model and the PDF download, UI side.

``ui/src/resumeDisplayModel.js`` and ``api.js``'s ``pdfFileName`` are pure
JavaScript, run under the system ``node`` (LOUD skip when it is not on PATH).
What lives in JSX is checked statically, by reading the source.  Since 0110-046
the settings hold this profile's title and the layout only: no name or contact
field anywhere (the Generate PDF form: test_ui_generate_pdf_model.py).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"


def _code(path: Path) -> str:
    """The source without its ``//`` comment lines (comments may name what is gone)."""
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("//"))

NODE_SCRIPT = """
const m = await import(process.argv[1]);
const api = await import(process.argv[2]);
console.log(JSON.stringify({
  exports: Object.keys(m).sort(),
  putBody: m.buildPutBody({ title: " Staff ", spacing_scale: 1, auto_fit: true, name: "Jane", contact: [{ kind: "email", value: "j@x.io" }] }, "p1"),
  putNoProfile: m.buildPutBody({ title: "x" }, null),
  putClearsTitle: m.buildPutBody({ title: "" }, "p1"),
  prefillUntitled: m.draftFromResponse({ saved: false, title: "", suggested: { title: "Engineer" } }),
  prefillTitled: m.draftFromResponse({ saved: true, title: "Staff Engineer", suggested: { title: "Other" } }),
  prefillNone: m.draftFromResponse({ saved: false, title: "" }),
  legacyResponse: m.draftFromResponse({ saved: true, name: "Jane Doe", contact: [{ kind: "email", value: "j@x.io" }], title: "Staff", suggested: { name: "Jane", contact: [] } }),
  layoutAuto: m.layoutLine({ title: "Staff Engineer", auto_fit: true, spacing_scale: 1 }),
  layoutManual: m.layoutLine({ title: "", auto_fit: false, spacing_scale: 0.85 }),
  layoutNone: m.layoutLine(null),
  putSpacing: m.buildPutBody({ title: "", spacing_scale: 1.234, auto_fit: false }, null),
  putClampLow: m.buildPutBody({ title: "", spacing_scale: 0.2, auto_fit: false }, null).spacing_scale,
  putClampHigh: m.buildPutBody({ title: "", spacing_scale: 9, auto_fit: false }, null).spacing_scale,
  putClampBad: m.buildPutBody({ title: "", spacing_scale: "x" }, null).spacing_scale,
  getLacks: m.draftFromResponse({ saved: true, title: "" }),
  getHas: m.draftFromResponse({ saved: true, title: "", spacing_scale: 0.85, auto_fit: false }),
  disabledOn: m.spacingDisabled({ auto_fit: true }),
  disabledOff: m.spacingDisabled({ auto_fit: false }),
  label: [m.spacingLabel(1), m.spacingLabel(0.7), m.spacingLabel(1.4)],
  gaps: [0.7, 0.75, 0.9, 1, 1.2, 1.4].map((v) => m.previewGap(v)),
  note: m.PRIVACY_NOTE,
  help: m.AUTO_FIT_HELP,
  fileName: api.pdfFileName('attachment; filename="acme-staff-engineer-2026-10-02.pdf"'),
  fileNameFallback: api.pdfFileName(null),
}));
"""


def _run() -> dict[str, object]:
    if shutil.which("node") is None:
        pytest.skip("LOUD: node is not on PATH; the resume display model was NOT checked")
    done = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT, (UI_SRC / "resumeDisplayModel.js").as_uri(), (UI_SRC / "api.js").as_uri()],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(done.stdout)


def test_the_model_has_no_name_or_contact_helpers() -> None:
    exports = _run()["exports"]
    for gone in ("KINDS", "KIND_LABELS", "previewHeader", "addEntry", "hasContactLine", "headerLine", "savedHeaderLine"):
        assert gone not in exports
    source = "\n".join(line for line in _code(UI_SRC / "resumeDisplayModel.js").splitlines() if "PRIVACY_NOTE =" not in line)
    assert "contact" not in source and "name" not in source


def test_the_put_body_saves_this_profiles_title_and_the_layout_never_a_name_or_contact() -> None:
    out = _run()
    assert out["putBody"] == {"spacing_scale": 1.0, "auto_fit": True, "titles": {"p1": "Staff"}}
    assert "titles" not in out["putNoProfile"]
    assert out["putClearsTitle"] == {"spacing_scale": 1.0, "auto_fit": True, "titles": {"p1": ""}}


def test_a_suggested_title_prefills_once_and_never_overwrites_a_saved_one() -> None:
    out = _run()
    assert out["prefillUntitled"] == {"title": "Engineer", "spacing_scale": 1.0, "auto_fit": True, "prefilled": True}
    assert out["prefillTitled"] == {"title": "Staff Engineer", "spacing_scale": 1.0, "auto_fit": True, "prefilled": False}
    assert out["prefillNone"] == {"title": "", "spacing_scale": 1.0, "auto_fit": True, "prefilled": False}
    # an older server's name/contact never enter the draft
    assert out["legacyResponse"] == {"title": "Staff", "spacing_scale": 1.0, "auto_fit": True, "prefilled": False}


def test_the_layout_reads_as_one_line() -> None:
    out = _run()
    assert out["layoutAuto"] == "Staff Engineer · Auto fit"
    assert out["layoutManual"] == "Spacing 0.85x" and out["layoutNone"] == ""


def test_the_note_and_the_download_file_name() -> None:
    out = _run()
    assert out["note"] == "Stored on this machine only; never sent to a model. Your name and contact details are not stored: you type them when you generate a PDF."
    assert out["fileName"] == "acme-staff-engineer-2026-10-02.pdf" and out["fileNameFallback"] == "resume.pdf"


def test_the_panel_generates_the_pdf_from_the_form_and_has_no_md_link() -> None:
    panel = (UI_SRC / "components" / "TailoredResumePanel.jsx").read_text(encoding="utf-8")
    assert "<GeneratePdfForm" in panel and "postTailoredResumePdf" in panel and "Generate PDF" in panel
    assert "Download .md" not in panel and "saveMarkdown" not in panel and "text/markdown" not in panel
    # 0110-046: no saved header line, no "Add your contact line" link, no read of the display settings
    for gone in ("getResumeDisplay", "Add your contact line", "PDF header:", "savedHeaderLine", "hasContactLine"):
        assert gone not in panel


def test_the_settings_section_is_on_the_profile_page_with_the_note() -> None:
    view = (UI_SRC / "views" / "ProfilesView.jsx").read_text(encoding="utf-8")
    assert "<ResumeDisplayPanel" in view
    panel = (UI_SRC / "components" / "ResumeDisplayPanel.jsx").read_text(encoding="utf-8")
    assert 'id="resume-display"' in panel and "PRIVACY_NOTE" in panel and "putResumeDisplay" in panel
    code = _code(UI_SRC / "components" / "ResumeDisplayPanel.jsx")
    assert 'id="resume-display-name"' not in code and "KIND_LABELS" not in code and "contact" not in code.replace("contact details", "")


def test_the_panel_sits_under_the_profile_card_before_run_history_and_archive() -> None:
    """0110-013: the Resume display panel follows the profile detail card."""

    view = (UI_SRC / "views" / "ProfilesView.jsx").read_text(encoding="utf-8")
    detail = view.index("Profile detail")
    panel = view.index("<ResumeDisplayPanel")
    assert detail < panel < view.index("<h3>Run history</h3>") < view.index("handleArchive}")
    # it is a sibling of the profile card, not inside it: the card is closed first
    assert "</section>" in view[detail:panel]


def test_one_form_serves_the_panel_and_the_wizard_step() -> None:
    panel = (UI_SRC / "components" / "ResumeDisplayPanel.jsx").read_text(encoding="utf-8")
    screen = (UI_SRC / "wizard" / "ResumeDisplayScreen.jsx").read_text(encoding="utf-8")
    assert "export function ResumeDisplayFields" in panel and panel.count("<ResumeDisplayFields") == 1
    assert "ResumeDisplayFields" in screen and "putResumeDisplay" not in screen


def test_the_put_body_carries_spacing_and_auto_fit_as_rounded_numbers() -> None:
    out = _run()
    assert out["putSpacing"]["spacing_scale"] == 1.23 and out["putSpacing"]["auto_fit"] is False
    assert isinstance(out["putSpacing"]["spacing_scale"], float)


def test_spacing_is_clamped_to_the_servers_range_before_sending() -> None:
    from gigai.scout.resume_display import SPACING_MAX, SPACING_MIN

    out = _run()
    assert (SPACING_MIN, SPACING_MAX) == (0.7, 1.4)
    assert out["putClampLow"] == 0.7 and out["putClampHigh"] == 1.4 and out["putClampBad"] == 1.0


def test_a_get_without_the_fields_loads_the_defaults_and_saved_values_load() -> None:
    out = _run()
    assert (out["getLacks"]["spacing_scale"], out["getLacks"]["auto_fit"]) == (1.0, True)
    assert (out["getHas"]["spacing_scale"], out["getHas"]["auto_fit"]) == (0.85, False)


def test_the_slider_is_disabled_exactly_while_auto_fit_is_on() -> None:
    out = _run()
    assert out["disabledOn"] is True and out["disabledOff"] is False
    assert out["label"] == ["1.00x", "0.70x", "1.40x"]


def test_the_preview_gap_grows_with_the_spacing_value() -> None:
    gaps = _run()["gaps"]
    assert gaps == sorted(gaps) and len(set(gaps)) == len(gaps)


def test_the_form_has_the_slider_the_auto_fit_toggle_and_the_schematic_preview() -> None:
    panel = (UI_SRC / "components" / "ResumeDisplayPanel.jsx").read_text(encoding="utf-8")
    assert 'type="range"' in panel and "SPACING_MIN" in panel and "SPACING_STEP" in panel
    assert "disabled={spacingDisabled(draft)}" in panel and 'type="checkbox"' in panel and "Auto fit" in panel
    assert "AUTO_FIT_HELP" in panel and 'data-role="spacing-preview"' in panel and "previewGap(" in panel
    assert _run()["help"] == "Adjusts spacing (never font size) so your resume fills its pages; turn off to use the slider as set."
    assert panel.count("<ResumeDisplayFields") == 1
    css = (UI_SRC / "styles.css").read_text(encoding="utf-8")
    assert ".spacing-preview" in css
