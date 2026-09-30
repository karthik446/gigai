"""0.1.10-003 1b: the Resume display settings model and the PDF download, UI side.

``ui/src/resumeDisplayModel.js`` and ``api.js``'s ``pdfFileName`` are pure
JavaScript, run under the system ``node`` (LOUD skip when it is not on PATH).
What lives in JSX is checked statically, by reading the source.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.resume_display import KINDS

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
const m = await import(process.argv[1]);
const api = await import(process.argv[2]);
const c = (kind, value) => ({ kind, value });
const full = [c("location", "Austin, TX"), c("work_authorization", "VISA: H1B"), c("linkedin", "https://www.linkedin.com/in/jane/"),
  c("github", "github.com/jane"), c("email", "jane@example.com"), c("phone", "+1 555 123 4567")];
const list = [c("email", "a@x.io"), c("phone", "555"), c("github", "gh")];
const saved = { saved: true, name: "Saved Name", title: "", contact: [c("email", "saved@x.io")],
  suggested: { name: "Other Name", title: "Staff Engineer", contact: [c("email", "other@x.io")] } };
console.log(JSON.stringify({
  kinds: m.KINDS,
  previewFull: m.previewHeader({ name: " Jane Doe ", title: "Staff Engineer", contact: full }),
  previewSkips: m.previewHeader({ name: "Jane", title: "", contact: [c("location", ""), c("email", "j@x.io"), c("phone", "   "), c("github", "github.com/j")] }),
  previewNameOnly: m.previewHeader({ name: "Jane", title: "", contact: [] }),
  previewEmpty: m.previewHeader({ name: "", title: "", contact: [c("email", "")] }),
  up: m.moveEntry(list, 1, -1).map((e) => e.kind),
  down: m.moveEntry(list, 0, 1).map((e) => e.kind),
  topUp: m.moveEntry(list, 0, -1).map((e) => e.kind),
  bottomDown: m.moveEntry(list, 2, 1).map((e) => e.kind),
  moveKeepsInput: list.map((e) => e.kind),
  removed: m.removeEntry(list, 1).map((e) => e.kind),
  edited: m.setEntryValue(list, 0, "new@x.io")[0].value,
  addSingleUseDup: m.addEntry(list, "email").length,
  addLinkTwice: m.addEntry(m.addEntry(list, "link"), "link").map((e) => e.kind),
  available: m.availableKinds(list),
  putBody: m.buildPutBody({ name: " Jane ", title: " Staff ", contact: [c("email", " j@x.io "), c("phone", ""), c("link", "example.com")] }, "p1"),
  putNoProfile: m.buildPutBody({ name: "Jane", title: "x", contact: [] }, null),
  putClearsTitle: m.buildPutBody({ name: "", title: "", contact: [] }, "p1"),
  prefillUnsaved: m.draftFromResponse({ saved: false, name: "", title: "", contact: [], suggested: { name: "Jane Doe", title: "Engineer", contact: [c("email", "j@x.io")] } }),
  prefillSaved: m.draftFromResponse(saved),
  prefillNone: m.draftFromResponse({ saved: false, name: "", title: "", contact: [] }),
  hasContactEmpty: m.hasContactLine({ saved: false, contact: [] }),
  hasContactBlank: m.hasContactLine({ saved: true, contact: [c("email", " ")] }),
  hasContact: m.hasContactLine({ saved: true, contact: [c("email", "j@x.io")] }),
  note: m.PRIVACY_NOTE,
  fileName: api.pdfFileName('attachment; filename="jane-doe-resume-acme.pdf"'),
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


def test_the_kinds_are_the_servers() -> None:
    assert _run()["kinds"] == list(KINDS)


def test_the_preview_prints_the_header_as_the_pdf_does() -> None:
    out = _run()
    assert out["previewFull"] == {
        "name": "Jane Doe",
        "title": "Staff Engineer",
        "items": ["Austin, TX", "VISA: H1B", "www.linkedin.com/in/jane", "github.com/jane", "jane@example.com", "+1 555 123 4567"],
        "contactLine": "Austin, TX | VISA: H1B | www.linkedin.com/in/jane | github.com/jane | jane@example.com | +1 555 123 4567",
    }


def test_empty_items_are_skipped_with_no_double_pipes() -> None:
    out = _run()
    assert out["previewSkips"]["contactLine"] == "j@x.io | github.com/j"
    assert out["previewNameOnly"] == {"name": "Jane", "title": "", "items": [], "contactLine": ""}
    assert out["previewEmpty"]["contactLine"] == "" and "||" not in out["previewSkips"]["contactLine"]


def test_reorder_moves_one_step_and_never_mutates_or_leaves_the_list() -> None:
    out = _run()
    assert out["up"] == ["phone", "email", "github"]
    assert out["down"] == ["phone", "email", "github"]
    assert out["topUp"] == ["email", "phone", "github"] and out["bottomDown"] == ["email", "phone", "github"]
    assert out["moveKeepsInput"] == ["email", "phone", "github"]
    assert out["removed"] == ["email", "github"] and out["edited"] == "new@x.io"


def test_single_use_kinds_are_added_once_and_links_repeat() -> None:
    out = _run()
    assert out["addSingleUseDup"] == 3
    assert out["addLinkTwice"] == ["email", "phone", "github", "link", "link"]
    assert "email" not in out["available"] and "link" in out["available"]


def test_the_put_body_saves_this_profiles_title_and_only_non_empty_values() -> None:
    out = _run()
    assert out["putBody"] == {
        "name": "Jane",
        "contact": [{"kind": "email", "value": "j@x.io"}, {"kind": "link", "value": "example.com"}],
        "titles": {"p1": "Staff"},
    }
    assert "titles" not in out["putNoProfile"]
    assert out["putClearsTitle"] == {"name": "", "contact": [], "titles": {"p1": ""}}


def test_suggested_prefills_once_and_never_overwrites_saved_values() -> None:
    out = _run()
    assert out["prefillUnsaved"] == {"name": "Jane Doe", "title": "Engineer", "contact": [{"kind": "email", "value": "j@x.io"}], "prefilled": True}
    # saved name/contact stay; only an empty title takes the suggestion
    assert out["prefillSaved"] == {"name": "Saved Name", "title": "Staff Engineer", "contact": [{"kind": "email", "value": "saved@x.io"}], "prefilled": True}
    assert out["prefillNone"] == {"name": "", "title": "", "contact": [], "prefilled": False}


def test_the_contact_hint_shows_only_with_no_contact_items() -> None:
    out = _run()
    assert (out["hasContactEmpty"], out["hasContactBlank"], out["hasContact"]) == (False, False, True)


def test_the_note_and_the_download_file_name() -> None:
    out = _run()
    assert out["note"] == "Stored on this machine only; added to your PDF locally."
    assert out["fileName"] == "jane-doe-resume-acme.pdf" and out["fileNameFallback"] == "resume.pdf"


def test_the_panel_downloads_the_pdf_and_has_no_md_link() -> None:
    panel = (UI_SRC / "components" / "TailoredResumePanel.jsx").read_text(encoding="utf-8")
    assert "Download PDF" in panel and "postTailoredResumePdf" in panel and "Add your contact line" in panel
    assert "Download .md" not in panel and "saveMarkdown" not in panel and "text/markdown" not in panel


def test_the_settings_section_is_on_the_profile_page_with_the_note() -> None:
    view = (UI_SRC / "views" / "ProfilesView.jsx").read_text(encoding="utf-8")
    assert "<ResumeDisplayPanel" in view
    panel = (UI_SRC / "components" / "ResumeDisplayPanel.jsx").read_text(encoding="utf-8")
    assert 'id="resume-display"' in panel and "PRIVACY_NOTE" in panel and "putResumeDisplay" in panel
