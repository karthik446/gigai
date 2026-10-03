"""0110-016: the new-profile form's own resume, run under node.

``ui/src/profileResumeModel.js`` is pure JavaScript, so it runs under the
system ``node`` (LOUD skip when absent); what lives in JSX is read statically.

Pinned: the form defaults to Paste (never the selected profile's resume);
creating stores the pasted/uploaded resume FIRST and pins that ref on the new
profile; "Choose existing" stores nothing; no resume -> nothing is created;
two profiles sharing one resume each get an "intended?" note naming the
earlier one (archived profiles are ignored); the list names the file only
where the API knows it; the form carries the personal-info warning and the
local contact check, and the old "defaults to the currently selected
profile's resume" note is gone.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
MODEL_JS = UI_SRC / "profileResumeModel.js"

NODE_SCRIPT = """
const input = JSON.parse(process.argv[1]);
const m = await import(input.url);

function fakeApi() {
  const calls = [];
  return {
    calls,
    storeResume: async (body) => {
      calls.push(["storeResume", body]);
      return { resume_ref: { record_id: "rec_new", revision_id: "rev_new", content_sha256: "sha256:n" } };
    },
    createProfile: async (body) => {
      calls.push(["createProfile", body]);
      return { profile: { profile_id: "p_new", label: body.label } };
    },
  };
}

const out = { initial: m.initialNewResume() };

const paste = fakeApi();
out.pasteResult = await m.createProfileWithResume(
  { label: " FDE ", titles: ["fde"], resume: { ...m.initialNewResume(), text: "pasted resume" } }, paste);
out.paste = paste.calls;

// 0110-046: the import's "we removed your contact lines" message comes back with the profile.
const stripped = fakeApi();
stripped.storeResume = async () => ({ resume_ref: { record_id: "r", revision_id: "v" }, contact_removed: { removed: { email: 1 }, message: "We removed your contact lines; you'll add them when you make a PDF." } });
out.strippedResult = await m.createProfileWithResume({ label: "S", titles: ["s"], resume: { ...m.initialNewResume(), text: "x" } }, stripped);
out.removedMessages = [m.contactRemovedMessage(null), m.contactRemovedMessage({ contact_removed: null }), m.contactRemovedMessage({ contact_removed: { message: "m" } })];

const upload = fakeApi();
await m.createProfileWithResume(
  { label: "AI", titles: ["ai"], resume: { mode: "upload", text: "x", uploadName: "a.md", uploadBase64: "eA==", existingRef: null } }, upload);
out.upload = upload.calls;

const existing = fakeApi();
await m.createProfileWithResume(
  { label: "Same", titles: ["t"], resume: { mode: "existing", text: "", uploadName: null, uploadBase64: null,
    existingRef: { key: "a/b", record_id: "rec_a", revision_id: "rev_b" } } }, existing);
out.existing = existing.calls;

const none = fakeApi();
try {
  await m.createProfileWithResume({ label: "x", titles: ["t"], resume: m.initialNewResume() }, none);
  out.noneThrew = false;
} catch (error) {
  out.noneThrew = true;
}
out.noneCalls = none.calls;
out.canCreate = {
  ok: m.canCreateProfile({ label: "x", titles: ["t"], resume: { ...m.initialNewResume(), text: "r" } }),
  noResume: m.canCreateProfile({ label: "x", titles: ["t"], resume: m.initialNewResume() }),
  noTitles: m.canCreateProfile({ label: "x", titles: [], resume: { ...m.initialNewResume(), text: "r" } }),
  existingUnchosen: m.canCreateProfile({ label: "x", titles: ["t"], resume: { ...m.initialNewResume(), mode: "existing" } }),
};

out.notes = m.sharedResumeNotes(input.profiles);
out.desc = input.profiles.map((p) => m.resumeDescription(p, input.config));
out.descNoConfig = m.resumeDescription(input.profiles[0], null);
console.log(JSON.stringify(out));
"""


def _profile(pid: str, label: str, record: str, state: str = "active") -> dict:
    return {
        "profile_id": pid,
        "label": label,
        "state": state,
        "resume_ref": {"record_id": record, "revision_id": "rev_1", "content_sha256": "sha256:" + record},
    }


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the new-profile resume model was not run")
    payload = {
        "url": MODEL_JS.as_uri(),
        "profiles": [
            _profile("p1", "Staff Software Engineer", "rec_a"),
            _profile("p2", "Forward Deployed Engineer", "rec_b"),
            _profile("p3", "Staff AI Engineer", "rec_a"),
            _profile("p4", "Old", "rec_b", state="archived"),
        ],
        "config": {
            "resume_label": "staff-swe.md",
            "resume_preview": {"record_id": "rec_a", "revision_id": "rev_1"},
        },
    }
    completed = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT, "--", json.dumps(payload)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_form_defaults_to_paste_not_the_selected_resume(out: dict) -> None:
    assert out["initial"]["mode"] == "paste"
    assert out["initial"]["existingRef"] is None and out["initial"]["text"] == ""


def test_a_pasted_resume_is_stored_first_then_pinned_on_the_new_profile(out: dict) -> None:
    store, create = out["paste"]
    assert store == ["storeResume", {"text": "pasted resume"}]
    assert create[0] == "createProfile"
    assert create[1]["label"] == "FDE" and create[1]["titles"] == ["fde"]
    assert create[1]["resume_record_id"] == "rec_new" and create[1]["resume_revision_id"] == "rev_new"


def test_an_uploaded_file_is_sent_as_its_own_bytes(out: dict) -> None:
    assert out["upload"][0] == ["storeResume", {"file_name": "a.md", "content_base64": "eA=="}]
    assert out["upload"][1][1]["resume_record_id"] == "rec_new"


def test_choosing_an_existing_resume_stores_nothing_and_pins_that_one(out: dict) -> None:
    assert [call[0] for call in out["existing"]] == ["createProfile"]
    body = out["existing"][0][1]
    assert body["resume_record_id"] == "rec_a" and body["resume_revision_id"] == "rev_b"


def test_no_resume_creates_nothing(out: dict) -> None:
    assert out["noneThrew"] is True and out["noneCalls"] == []
    assert out["canCreate"] == {"ok": True, "noResume": False, "noTitles": False, "existingUnchosen": False}


def test_two_profiles_sharing_a_resume_are_flagged_naming_the_earlier_one(out: dict) -> None:
    assert out["notes"] == {"p3": "Uses the same resume as Staff Software Engineer: intended?"}


def test_the_list_names_the_file_only_where_the_api_knows_it(out: dict) -> None:
    assert out["desc"][0] == "Resume: staff-swe.md"
    assert out["desc"][1] == "Resume: its own stored resume"
    assert out["descNoConfig"] == "Resume: its own stored resume"


def test_the_form_uses_the_warning_the_contact_check_and_truthful_copy() -> None:
    view = (UI_SRC / "views" / "ProfilesView.jsx").read_text(encoding="utf-8")
    field = (UI_SRC / "components" / "NewProfileResume.jsx").read_text(encoding="utf-8")
    assert "defaults to the currently selected profile" not in view
    assert "gigai scout resume add" not in view.split("Profile detail")[0]
    assert "<NewProfileResume" in view and "data-role=\"shared-resume-warning\"" in view
    assert "<ResumeWarning" in field and "useResumeCheck" in field and "contactHeadsUp" in field
    assert "Continue anyway" in field


def test_the_import_message_comes_back_with_the_new_profile(out: dict) -> None:
    from gigai.scout.resume_pii import REMOVED_MESSAGE

    assert out["pasteResult"] == {"profile": {"profile_id": "p_new", "label": "FDE"}, "contactRemoved": None}
    assert out["strippedResult"]["contactRemoved"] == REMOVED_MESSAGE
    assert out["removedMessages"] == [None, None, "m"]
    view = (UI_SRC / "views" / "ProfilesView.jsx").read_text(encoding="utf-8")
    assert 'data-role="contact-removed"' in view and "setCreateNote(created.contactRemoved)" in view
