"""uat-bug-024 and the stored-resume analysis: the wizard's own rules, under node.

``ui/src/wizard/wizardFinish.js`` and ``wizardState.js`` are pure JavaScript
(no React), run here under the system ``node`` against a fake of
``wizardApi.js`` that records what is sent (the way
``test_ui_wizard_finish_model.py`` does). LOUD skip when ``node`` is not on
PATH. What lives in JSX is checked statically, by reading the source.

What is pinned:

* Finish sends ``PUT /api/setup`` with ``profile_id``: the profile it just
  created or updated, in every case (a first profile, "Update", "Create a
  new profile" next to a selected one, Finish pressed again). With "Create a
  new profile" that id is the NEW profile's, never the selected one's;
* "Analyze resume" sends pasted or uploaded text as ``resume_text``, a
  resume a profile uses as that ``profile_id``, and a stored resume no
  profile uses (the one ``gigai scout resume add`` stored) as ``resume_ref``
  with its two ids and nothing else;
* the wizard no longer refuses to analyse a resume that is in no profile.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

WIZARD_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src" / "wizard"

NODE_SCRIPT = """
const input = JSON.parse(process.argv[1]);
const state = await import(input.stateUrl);
const { finishSetup } = await import(input.finishUrl);

// A fake of wizardApi.js that records what Finish sends.
function fakeApi(server) {
  const calls = [];
  let next = 1;
  return {
    calls,
    storeResume: async (body) => {
      calls.push(["storeResume", body]);
      return { resume_ref: { record_id: "record_new", revision_id: "revision_new", content_sha256: "sha256:new" }, created: true };
    },
    getProfiles: async () => {
      calls.push(["getProfiles"]);
      return { profiles: server.profiles, selected_profile_id: server.selected };
    },
    createProfile: async (body) => {
      calls.push(["createProfile", body]);
      const profile = { profile_id: `profile_made_${next++}`, label: body.label, state: "active", origin: "operator_created",
        titles: body.titles, resume_ref: { record_id: body.resume_record_id, revision_id: body.resume_revision_id } };
      server.profiles = [...server.profiles, profile];
      return { profile };
    },
    updateProfile: async (id, body) => {
      calls.push(["updateProfile", { id, ...body }]);
      const profile = { ...server.profiles.find((item) => item.profile_id === id), label: body.label, titles: body.titles,
        resume_ref: { record_id: body.resume_record_id, revision_id: body.resume_revision_id } };
      server.profiles = server.profiles.map((item) => (item.profile_id === id ? profile : item));
      return { profile };
    },
    selectProfile: async (id) => {
      calls.push(["selectProfile", id]);
      server.selected = id;
      return { selected_profile_id: id };
    },
    putSetup: async (body) => {
      calls.push(["putSetup", body]);
      return { prefs: body };
    },
  };
}

const finishes = {};
for (const [name, scenario] of Object.entries(input.finishes)) {
  const server = { profiles: scenario.profiles || [], selected: scenario.selected || null };
  const loaded = server.profiles.find((item) => item.profile_id === server.selected) || null;
  const runs = [];
  for (let index = 0; index < (scenario.times || 1); index += 1) {
    const api = fakeApi(server);
    const fields = { ...state.initialFields({ prefs: {}, config: null, selectedProfile: loaded, resumes: [] }), ...scenario.fields };
    const result = await finishSetup({ fields, selectedProfile: loaded, existingPrefs: null }, api);
    const put = api.calls.filter(([call]) => call === "putSetup").map(([, body]) => body);
    runs.push({
      names: api.calls.map(([call]) => call),
      put,
      profileId: result.profile.profile_id,
      expected: state.setupBody(fields, null),
    });
  }
  finishes[name] = { runs, profiles: server.profiles.map((item) => item.profile_id), selected: server.selected };
}

const extracts = {};
for (const [name, scenario] of Object.entries(input.extracts)) {
  const resumes = state.existingResumes({ profiles: scenario.profiles, config: scenario.config });
  const fields = {
    ...state.initialFields({ prefs: {}, config: scenario.config, selectedProfile: scenario.selected || null, resumes }),
    ...(scenario.fields || {}),
  };
  if (scenario.choose !== undefined) {
    fields.existingRef = resumes[scenario.choose];
  }
  extracts[name] = { resumeMode: fields.resumeMode, body: state.extractBody(fields, scenario.profiles) };
}

console.log(JSON.stringify({ finishes, extracts }));
"""

PASTED = "Jordan Rivera\nStaff engineer.\n"
SELECTED = {
    "profile_id": "profile_selected",
    "label": "Staff platform",
    "state": "active",
    "origin": "operator_created",
    "resume_ref": {"record_id": "record_old", "revision_id": "revision_old"},
    "titles": ["Staff Software Engineer"],
    "titles_to_avoid": [],
}
OTHER = {
    "profile_id": "profile_other",
    "label": "Management",
    "state": "active",
    "origin": "operator_created",
    "resume_ref": {"record_id": "record_other", "revision_id": "revision_other"},
    "titles": ["Engineering Manager"],
    "titles_to_avoid": [],
}
NEW_FIELDS = {
    "profileMode": "new",
    "profileName": "Management",
    "resumeMode": "paste",
    "resumeText": PASTED,
    "titles": ["Engineering Manager", "Director of Engineering"],
    "titlesToAvoid": ["Intern"],
}
CONFIG = {"default_model_target": "ollama_local", "max_age_days": 60}
CLI_STORED_CONFIG = {
    # GET /api/config on a home where `gigai scout resume add` ran and no
    # profile exists: the newest stored resume, with its file name.
    "config": CONFIG,
    "resume_preview": {"record_id": "record_cli", "revision_id": "revision_cli", "content_sha256": "sha256:cli"},
    "resume_label": "My Resume.md",
    "resume_created_at": "2026-09-28T10:00:00Z",
}
SELECTED_CONFIG = {
    "config": CONFIG,
    "resume_preview": {"record_id": "record_old", "revision_id": "revision_old", "content_sha256": "sha256:old"},
    "resume_label": "old-resume.md",
    "resume_created_at": "2026-09-01T10:00:00Z",
}


def _fixture() -> dict:
    return {
        "stateUrl": (WIZARD_SRC / "wizardState.js").as_uri(),
        "finishUrl": (WIZARD_SRC / "wizardFinish.js").as_uri(),
        "finishes": {
            "first": {"fields": {**NEW_FIELDS, "profileName": "Staff platform"}, "times": 2},
            "update": {
                "fields": {**NEW_FIELDS, "profileMode": "update", "profileName": "Staff platform"},
                "profiles": [SELECTED],
                "selected": "profile_selected",
            },
            "newNextToSelected": {
                "fields": NEW_FIELDS,
                "profiles": [SELECTED],
                "selected": "profile_selected",
                "times": 2,
            },
        },
        "extracts": {
            "pasted": {"profiles": [], "config": None, "fields": {"resumeMode": "paste", "resumeText": PASTED}},
            "uploaded": {
                "profiles": [SELECTED],
                "selected": SELECTED,
                "config": SELECTED_CONFIG,
                "fields": {"resumeMode": "upload", "resumeText": PASTED, "uploadName": "My Resume.md", "modelTarget": "codex_cli"},
            },
            "cliStoredNoProfile": {"profiles": [], "config": CLI_STORED_CONFIG},
            "selectedProfilesResume": {"profiles": [SELECTED, OTHER], "selected": SELECTED, "config": SELECTED_CONFIG},
            "anotherProfilesResume": {"profiles": [SELECTED, OTHER], "selected": SELECTED, "config": SELECTED_CONFIG, "choose": 1},
        },
    }


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the wizard's Finish and Analyze rules were not run")
    completed = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT, "--", json.dumps(_fixture())],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


# --- uat-bug-024: Finish names the profile ---------------------------------------------------


def _only_put(run: dict) -> dict:
    assert run["names"][-1] == "putSetup" and run["names"].count("putSetup") == 1, run["names"]
    (put,) = run["put"]
    return put


def test_every_finish_names_the_profile_it_saved(out: dict) -> None:
    for name, finished in out["finishes"].items():
        for run in finished["runs"]:
            put = _only_put(run)
            assert put["profile_id"] == run["profileId"], name
            # The preferences are what they were; profile_id is the one addition.
            assert {key: value for key, value in put.items() if key != "profile_id"} == run["expected"], name


def test_a_new_profile_next_to_a_selected_one_names_the_new_profile(out: dict) -> None:
    added = out["finishes"]["newNextToSelected"]
    first, second = added["runs"]

    assert first["names"] == ["storeResume", "getProfiles", "createProfile", "putSetup"]
    put = _only_put(first)
    assert put["profile_id"] == first["profileId"] != "profile_selected"
    assert put["roles"] == NEW_FIELDS["titles"] and put["titles_to_avoid"] == NEW_FIELDS["titlesToAvoid"]
    # Finish again: the profile it made, still not the selected one.
    assert second["names"] == ["storeResume", "getProfiles", "updateProfile", "putSetup"]
    assert _only_put(second)["profile_id"] == first["profileId"]
    assert added["profiles"] == ["profile_selected", first["profileId"]]
    assert added["selected"] == "profile_selected"


def test_update_names_the_selected_profile(out: dict) -> None:
    (run,) = out["finishes"]["update"]["runs"]
    assert run["names"] == ["storeResume", "getProfiles", "updateProfile", "putSetup"]
    assert _only_put(run)["profile_id"] == "profile_selected"


def test_a_first_profile_is_named_and_selected(out: dict) -> None:
    first, second = out["finishes"]["first"]["runs"]
    assert first["names"] == ["storeResume", "getProfiles", "createProfile", "selectProfile", "putSetup"]
    assert _only_put(first)["profile_id"] == first["profileId"] == out["finishes"]["first"]["selected"]
    assert _only_put(second)["profile_id"] == first["profileId"]


# --- "Analyze resume": what is sent --------------------------------------------------------


def test_pasted_and_uploaded_text_is_sent_as_text(out: dict) -> None:
    assert out["extracts"]["pasted"]["body"] == {"model_target": "ollama_local", "resume_text": PASTED}
    assert out["extracts"]["uploaded"]["body"] == {"model_target": "codex_cli", "resume_text": PASTED}


def test_a_stored_resume_no_profile_uses_is_sent_by_its_ids(out: dict) -> None:
    stored = out["extracts"]["cliStoredNoProfile"]
    # The wizard starts on it (A1), so "Analyze resume" is one press away.
    assert stored["resumeMode"] == "existing"
    assert stored["body"] == {
        "model_target": "ollama_local",
        "resume_ref": {"record_id": "record_cli", "revision_id": "revision_cli"},
    }


def test_a_resume_a_profile_uses_is_sent_as_that_profile(out: dict) -> None:
    assert out["extracts"]["selectedProfilesResume"]["body"] == {
        "model_target": "ollama_local",
        "profile_id": "profile_selected",
    }
    assert out["extracts"]["anotherProfilesResume"]["body"] == {
        "model_target": "ollama_local",
        "profile_id": "profile_other",
    }


def test_the_wizard_analyses_with_that_body_and_refuses_nothing_itself() -> None:
    source = (WIZARD_SRC / "SetupWizard.jsx").read_text(encoding="utf-8")
    assert "extractResume(extractBody(fields, profiles))" in source
    assert "cannot be analysed" not in source
    assert "not part of a profile" not in source
