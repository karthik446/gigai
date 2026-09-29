"""uat-bug-020: the setup wizard's words and rules, run under node.

``ui/src/wizard/wizardState.js`` and ``wizardFinish.js`` are pure JavaScript
(no React), so this test runs them under the system ``node`` the way
``test_ui_uat_batch2_model.py`` does (no JS test runner) and asserts on the
JSON the script prints. LOUD skip when ``node`` is not on PATH. What lives
in JSX is checked statically, by reading the source.

What is pinned:

* the last screen names only what is missing: a key line appears only when
  the server said that key is not set (never when the key state could not be
  read); the OpenRouter line only for the OpenRouter model; the ``ollama
  serve`` line only when Ollama is the chosen model, and not when an
  extraction already answered through it in this session;
* there is no "Run these commands" block: no command list is built
  (``buildCommands`` / ``commandsAsText`` are gone), FinishScreen renders
  the hint lines and nothing else as a command, and no wizard screen sends
  the user to ``gigai scout resume add``;
* Finish (``wizardFinish.finishSetup``) stores the resume first, then saves
  the profile WITH that resume, selects a first profile, then saves the
  preferences; an uploaded file is sent as its own bytes; an existing resume
  is not stored again; pressing Finish again updates the profile it already
  made; a new profile next to a selected one is not selected;
* (A1) a resume that is already stored is offered and used: the one ``gigai
  scout resume add`` stored before any profile existed (``GET /api/config``'s
  ``resume_preview`` with no profile selected) is first in "Choose existing",
  the wizard starts on it, and Finish pins it without storing anything;
* (A2) no Discovery step: the step list and the Review have no discovery,
  cadence or budget entry, the screens ask for neither, and ``PUT
  /api/setup`` still gets the saved values (or the defaults: 7 days, $0.50).
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.resume_import import RESUME_MAX_BYTES, RESUME_SUFFIXES
from gigai.secrets_catalog import KNOWN_SERVICES

WIZARD_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src" / "wizard"
WIZARD_STATE_JS = WIZARD_SRC / "wizardState.js"
WIZARD_FINISH_JS = WIZARD_SRC / "wizardFinish.js"

NODE_SCRIPT = """
const input = JSON.parse(process.argv[1]);
const state = await import(input.stateUrl);
const { finishSetup } = await import(input.finishUrl);

const hints = Object.fromEntries(
  Object.entries(input.hints).map(([name, args]) => [name, state.setupHints(args).map((line) => line.id)]),
);
const lines = state.setupHints(input.hints.nothingSet);

// A fake of wizardApi.js that records what Finish sends.
function fakeApi(server) {
  const calls = [];
  let next = 1;
  const record = (name, body) => calls.push(body === undefined ? [name] : [name, body]);
  return {
    calls,
    storeResume: async (body) => {
      record("storeResume", body);
      return { resume_ref: { record_id: "record_new", revision_id: "revision_new", content_sha256: "sha256:new" }, created: true };
    },
    getProfiles: async () => {
      record("getProfiles");
      return { profiles: server.profiles, selected_profile_id: server.selected };
    },
    createProfile: async (body) => {
      record("createProfile", body);
      const profile = { profile_id: `profile_${next++}`, label: body.label, state: "active", origin: "operator_created",
        resume_ref: { record_id: body.resume_record_id, revision_id: body.resume_revision_id } };
      server.profiles = [...server.profiles, profile];
      return { profile };
    },
    updateProfile: async (id, body) => {
      record("updateProfile", { id, ...body });
      const profile = { ...server.profiles.find((item) => item.profile_id === id), label: body.label,
        resume_ref: { record_id: body.resume_record_id, revision_id: body.resume_revision_id } };
      server.profiles = server.profiles.map((item) => (item.profile_id === id ? profile : item));
      return { profile };
    },
    selectProfile: async (id) => {
      if (server.failSelection) {
        server.failSelection = false;
        throw new Error("selection failed");
      }
      record("selectProfile", id);
      server.selected = id;
      return { selected_profile_id: id };
    },
    putSetup: async (body) => {
      record("putSetup", { roles: body.roles, max_age_days: body.max_age_days, model_target: body.model_target });
      return { prefs: body };
    },
  };
}

async function finish(scenario) {
  const server = { profiles: scenario.profiles || [], selected: scenario.selected || null, failSelection: Boolean(scenario.failSelection) };
  const out = [];
  for (let index = 0; index < (scenario.times || 1); index += 1) {
    const api = fakeApi(server);
    const selected = server.profiles.find((item) => item.profile_id === server.selected) || null;
    // The wizard keeps its fields (and what it loaded) across a Finish that
    // failed; a wizard opened again loads the profile selected by then.
    const selectedAtLoad = scenario.keepLoad ? scenario.loaded || null : selected;
    const fields = { ...state.initialFields({ prefs: {}, config: null, selectedProfile: selectedAtLoad }), ...scenario.fields };
    let error = null;
    let result = null;
    try {
      result = await finishSetup({ fields, selectedProfile: selectedAtLoad, existingPrefs: null }, api);
    } catch (caught) {
      error = caught.message;
    }
    out.push({ calls: api.calls, error, profileId: result && result.profile.profile_id, selected: result && result.selectedProfileId });
  }
  return { runs: out, profiles: server.profiles.map((item) => item.profile_id), selected: server.selected };
}

const finishes = {};
for (const [name, scenario] of Object.entries(input.finishes)) {
  finishes[name] = await finish(scenario);
}

// A1: what the wizard offers and starts with, per load.
const loads = {};
for (const [name, load] of Object.entries(input.loads)) {
  const resumes = state.existingResumes({ profiles: load.profiles, config: load.config });
  const fields = state.initialFields({ prefs: load.prefs || {}, config: load.config, selectedProfile: load.selected || null, resumes });
  const filled = { ...fields, profileName: "Staff platform", titles: ["Staff Engineer"] };
  const api = fakeApi({ profiles: load.profiles || [], selected: load.selected ? load.selected.profile_id : null });
  const result = await finishSetup({ fields: filled, selectedProfile: load.selected || null, existingPrefs: load.saved || null }, api);
  loads[name] = {
    resumes: resumes.map((item) => ({ key: item.key, name: item.name, profileLabel: item.profileLabel })),
    resumeMode: fields.resumeMode,
    existingKey: fields.existingRef && fields.existingRef.key,
    screen1Complete: state.screenIsComplete(1, filled),
    resumeRow: state.reviewRows(filled, resumes).find(([key]) => key === "Resume")[1],
    calls: api.calls,
    resumeRef: result.resumeRef,
  };
}

// A2: the steps, the Review and what PUT /api/setup gets.
const reviewFields = { ...state.initialFields({ prefs: {}, config: null, selectedProfile: null, resumes: [] }), ...input.finishes.freshPaste.fields };
const savedPrefs = { cadence_days: 3, budget_usd_per_session: 1.25 };
const steps = {
  names: state.STEPS,
  review: state.reviewRows(reviewFields, []).map(([key]) => key),
  complete: [1, 2, 3, 4].map((step) => state.screenIsComplete(step, reviewFields)),
  firstRun: state.setupBody(reviewFields, null),
  edit: state.setupBody({ ...state.initialFields({ prefs: savedPrefs, config: null, selectedProfile: null, resumes: [] }), ...input.finishes.freshPaste.fields }, savedPrefs),
};

// uat-bug-038: the one model choice: preselected from the saved default
// target, else Codex; Finish sends it as `model_target`.
const pick = (config) => state.initialFields({ prefs: {}, config, selectedProfile: null, resumes: [] }).modelTarget;
const modelChoice = {
  savedClaude: pick({ config: { default_model_target: "claude_cli" } }),
  savedOllama: pick({ config: { default_model_target: "ollama_local" } }),
  noConfig: pick(null),
  unknownSaved: pick({ config: { default_model_target: "nope" } }),
  sent: state.setupBody({ ...reviewFields, modelTarget: "claude_cli" }, null).model_target,
  labels: state.MODEL_TARGETS.map((target) => state.modelTargetLabel(target)),
};

console.log(JSON.stringify({
  modelChoice,
  hints,
  lines,
  exports: Object.keys(state).sort(),
  pattern: input.names.map((name) => state.RESUME_FILE_PATTERN.test(name)),
  maxBytes: state.RESUME_MAX_BYTES,
  base64: state.bytesToBase64(new Uint8Array(input.bytes)),
  finishes,
  loads,
  steps,
}));
"""

PASTED = "Jordan Rivera\nStaff engineer.\n"
UPLOADED = "# Jordan Rivera\n\nStaff engineer — Go.\n".encode("utf-8")
ALL_SET = {"exa": True, "openrouter": True, "openai": True}
NONE_SET = {"exa": False, "openrouter": False, "openai": False}
PASTE_FIELDS = {"profileName": " Staff platform ", "resumeMode": "paste", "resumeText": PASTED, "titles": ["Staff Engineer"]}
EXISTING_PROFILE = {
    "profile_id": "profile_existing",
    "label": "Existing",
    "state": "active",
    "origin": "migrated_default",
    "resume_ref": {"record_id": "record_old", "revision_id": "revision_old"},
    "titles": ["Engineer"],
    "titles_to_avoid": [],
}


CLI_STORED_CONFIG = {
    # GET /api/config on a home where `gigai scout resume add` ran and no
    # profile exists: the newest stored resume, with its file name.
    "config": {"default_model_target": "ollama_local", "max_age_days": 60},
    "resume_preview": {"record_id": "record_cli", "revision_id": "revision_cli", "content_sha256": "sha256:cli"},
    "resume_label": "kar-staff-resume.md",
    "resume_created_at": "2026-09-28T10:00:00Z",
}
NO_RESUME_CONFIG = {
    "config": {"default_model_target": "ollama_local", "max_age_days": 60},
    "resume_preview": None,
    "resume_label": None,
    "resume_created_at": None,
}
SELECTED_CONFIG = {
    "config": {"default_model_target": "ollama_local", "max_age_days": 60},
    "resume_preview": {"record_id": "record_old", "revision_id": "revision_old", "content_sha256": "sha256:old"},
    "resume_label": "old-resume.md",
    "resume_created_at": "2026-09-01T10:00:00Z",
}


def _fixture() -> dict:
    return {
        "loads": {
            "cliStoredNoProfile": {"profiles": [], "config": CLI_STORED_CONFIG},
            "nothingStored": {"profiles": [], "config": NO_RESUME_CONFIG},
            "noConfig": {"profiles": [], "config": None},
            "selectedProfile": {"profiles": [EXISTING_PROFILE], "selected": EXISTING_PROFILE, "config": SELECTED_CONFIG},
        },
        "stateUrl": WIZARD_STATE_JS.as_uri(),
        "finishUrl": WIZARD_FINISH_JS.as_uri(),
        "names": ["resume.txt", "Resume.MD", "cv.markdown", "resume.pdf", "resume.docx", "resume.text", "resume"],
        "bytes": list(UPLOADED),
        "hints": {
            "allSetOpenRouter": {"modelTarget": "openrouter_api", "keys": ALL_SET, "extraction": None},
            "allSetCodex": {"modelTarget": "codex_cli", "keys": ALL_SET, "extraction": None},
            "allSetOllama": {"modelTarget": "ollama_local", "keys": ALL_SET, "extraction": None},
            "ollamaAnswered": {"modelTarget": "ollama_local", "keys": ALL_SET, "extraction": {"model_target": "ollama_local"}},
            "ollamaAfterAnotherModel": {"modelTarget": "ollama_local", "keys": ALL_SET, "extraction": {"model_target": "codex_cli"}},
            # SCOPE-ADD-3: an older server still answers a `jev` key; the
            # wizard never names it (Jev is gone from the product).
            "staleJevUnset": {"modelTarget": "codex_cli", "keys": {**NONE_SET, "jev": False}, "extraction": None, "exaEnabled": True},
            "exaUnset": {"modelTarget": "codex_cli", "keys": {**ALL_SET, "exa": False}, "extraction": None, "exaEnabled": True},
            # uat-bug-033: Exa is optional and off by default: its key is
            # never named unless the saved config turns Exa on.
            "exaUnsetExaOff": {"modelTarget": "codex_cli", "keys": {**ALL_SET, "exa": False}, "extraction": None},
            "exaUnsetExaOffExplicit": {"modelTarget": "codex_cli", "keys": NONE_SET, "extraction": None, "exaEnabled": False},
            "nothingSet": {"modelTarget": "openrouter_api", "keys": NONE_SET, "extraction": None, "exaEnabled": True},
            "nothingSetCodex": {"modelTarget": "codex_cli", "keys": NONE_SET, "extraction": None, "exaEnabled": True},
            "keysUnknown": {"modelTarget": "codex_cli", "keys": None, "extraction": None},
            "keysUnknownOllama": {"modelTarget": "ollama_local", "keys": None, "extraction": None},
        },
        "finishes": {
            "freshPaste": {"fields": PASTE_FIELDS, "times": 2},
            "freshUpload": {
                "fields": {
                    **PASTE_FIELDS,
                    "resumeMode": "upload",
                    "uploadName": "Jordan Rivera.md",
                    "uploadBase64": base64.b64encode(UPLOADED).decode("ascii"),
                    "resumeText": UPLOADED.decode("utf-8"),
                },
            },
            "selectionFailsThenRetry": {"fields": PASTE_FIELDS, "times": 2, "failSelection": True, "keepLoad": True},
            "newNextToSelected": {
                "fields": {**PASTE_FIELDS, "profileMode": "new"},
                "profiles": [EXISTING_PROFILE],
                "selected": "profile_existing",
                "loaded": EXISTING_PROFILE,
                "keepLoad": True,
                "times": 2,
            },
            "updateSelected": {
                "fields": PASTE_FIELDS,
                "profiles": [EXISTING_PROFILE],
                "selected": "profile_existing",
            },
            "defaultProfileAppears": {
                # Nothing was selected when the wizard opened; storing the
                # first resume made the server's default profile.
                "fields": PASTE_FIELDS,
                "profiles": [EXISTING_PROFILE],
                "selected": "profile_existing",
                "loaded": None,
                "keepLoad": True,
            },
            "existingResume": {
                "fields": {
                    **PASTE_FIELDS,
                    "resumeMode": "existing",
                    "resumeText": "",
                    "existingRef": {"key": "record_old/revision_old", "record_id": "record_old", "revision_id": "revision_old"},
                },
                "profiles": [EXISTING_PROFILE],
                "selected": "profile_existing",
            },
        },
    }


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the wizard's Finish rules were not run")
    completed = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT, "--", json.dumps(_fixture())],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


# --- the last screen: only what is missing ------------------------------------------


def test_nothing_is_listed_when_nothing_is_missing(out: dict) -> None:
    assert out["hints"]["allSetOpenRouter"] == []
    assert out["hints"]["allSetCodex"] == []
    assert out["hints"]["ollamaAnswered"] == []


def test_a_key_is_named_only_when_it_is_not_set(out: dict) -> None:
    assert out["hints"]["exaUnset"] == ["exa"]
    assert out["hints"]["nothingSet"] == ["openrouter", "exa"]
    # The OpenRouter key matters only to the OpenRouter model.
    assert out["hints"]["nothingSetCodex"] == ["exa"]
    # SCOPE-ADD-3: no Jev key hint, even from an older server's answer.
    assert out["hints"]["staleJevUnset"] == ["exa"]
    # The key state could not be read: nothing is claimed to be missing.
    assert out["hints"]["keysUnknown"] == []


def test_the_exa_key_is_never_named_while_exa_is_off(out: dict) -> None:
    """uat-bug-033: a model target is the only requirement; the wizard never
    asks for an Exa key unless the saved config has Exa on."""

    assert out["hints"]["exaUnsetExaOff"] == []
    assert out["hints"]["exaUnsetExaOffExplicit"] == []


def test_ollama_is_named_only_when_it_is_the_chosen_model(out: dict) -> None:
    assert out["hints"]["allSetOllama"] == ["ollama"]
    assert out["hints"]["keysUnknownOllama"] == ["ollama"]
    assert out["hints"]["ollamaAfterAnotherModel"] == ["ollama"]
    for name in ("allSetOpenRouter", "allSetCodex", "staleJevUnset", "nothingSet", "keysUnknown"):
        assert "ollama" not in out["hints"][name], name
    # An extraction that just answered through Ollama shows it running.
    assert out["hints"]["ollamaAnswered"] == []


def test_the_hint_lines_say_what_to_run(out: dict) -> None:
    by_id = {line["id"]: line for line in out["lines"]}
    assert "jev" not in by_id
    assert all("jev" not in json.dumps(line).lower() for line in out["lines"])
    assert by_id["exa"]["text"] == "Exa key not set"
    assert by_id["exa"]["command"] == "gigai secrets add exa"
    assert by_id["exa"]["note"].startswith("optional")
    assert by_id["openrouter"]["command"] == "gigai secrets add openrouter"
    # Every key the wizard names is one `gigai secrets add` accepts.
    for line in out["lines"]:
        assert line["command"].removeprefix("gigai secrets add ") in KNOWN_SERVICES


# --- no "Run these commands" block ------------------------------------------------------


def test_no_command_list_is_built(out: dict) -> None:
    assert "buildCommands" not in out["exports"]
    assert "commandsAsText" not in out["exports"]
    assert "setupHints" in out["exports"]


def test_the_wizard_screens_have_no_command_block() -> None:
    finish = (WIZARD_SRC / "FinishScreen.jsx").read_text(encoding="utf-8")
    assert "Run these commands" not in finish
    assert "Copy all" not in finish and "clipboard" not in finish
    assert 'data-role="setup-hints"' in finish
    # The section is rendered only when a line exists.
    assert "missing.length > 0 &&" in finish
    for name in ("FinishScreen.jsx", "ResumeScreen.jsx", "SetupWizard.jsx", "TargetScreen.jsx", "CompaniesScreen.jsx"):
        source = (WIZARD_SRC / name).read_text(encoding="utf-8")
        for gone in ("uv tool install", "gigai scout install", "gigai scout run", "resume add", "run this wizard again"):
            assert gone not in source, f"{name} still says {gone!r}"
    state = WIZARD_STATE_JS.read_text(encoding="utf-8")
    for gone in ("uv tool install", "gigai scout install", "gigai scout run"):
        assert gone not in state


def test_finish_leaves_the_wizard_as_soon_as_it_has_saved() -> None:
    wizard = (WIZARD_SRC / "SetupWizard.jsx").read_text(encoding="utf-8")
    assert "await finishSetup(" in wizard
    saved = wizard.index("setSaved(done);")
    assert "onDone(done);" in wizard[saved : saved + 80]
    assert "No resume is stored yet" not in wizard


# --- the upload: the formats and the size the import accepts ------------------------------


def test_the_upload_accepts_what_the_import_accepts(out: dict) -> None:
    assert out["pattern"] == [True, True, True, False, False, False, False]
    assert set(RESUME_SUFFIXES) == {".txt", ".md", ".markdown"}
    assert out["maxBytes"] == RESUME_MAX_BYTES
    assert base64.b64decode(out["base64"]) == UPLOADED


# --- Finish ----------------------------------------------------------------------------------


def _names(run: dict) -> list[str]:
    return [call[0] for call in run["calls"]]


def test_finish_stores_the_pasted_resume_then_the_profile_with_it(out: dict) -> None:
    fresh = out["finishes"]["freshPaste"]
    first, second = fresh["runs"]
    assert first["error"] is None
    assert _names(first) == ["storeResume", "getProfiles", "createProfile", "selectProfile", "putSetup"]
    assert first["calls"][0] == ["storeResume", {"text": PASTED}]
    assert first["calls"][2][1] == {
        "label": "Staff platform",
        "titles": ["Staff Engineer"],
        "titles_to_avoid": [],
        "resume_record_id": "record_new",
        "resume_revision_id": "revision_new",
    }
    assert first["calls"][3] == ["selectProfile", first["profileId"]]
    assert first["calls"][4][1] == {"roles": ["Staff Engineer"], "max_age_days": 60, "model_target": "codex_cli"}

    # The wizard opened again: the selected profile is updated, none is made.
    assert _names(second) == ["storeResume", "getProfiles", "updateProfile", "putSetup"]
    assert second["calls"][2][1]["id"] == first["profileId"]
    assert fresh["profiles"] == [first["profileId"]] and fresh["selected"] == first["profileId"]


def test_finish_sends_an_uploaded_file_as_its_own_bytes(out: dict) -> None:
    (run,) = out["finishes"]["freshUpload"]["runs"]
    name, body = run["calls"][0]
    assert name == "storeResume"
    assert set(body) == {"file_name", "content_base64"}
    assert body["file_name"] == "Jordan Rivera.md"
    assert base64.b64decode(body["content_base64"]) == UPLOADED


def test_finish_pressed_again_after_a_failure_makes_no_second_profile(out: dict) -> None:
    retried = out["finishes"]["selectionFailsThenRetry"]
    first, second = retried["runs"]
    assert first["error"] == "selection failed"
    assert _names(first) == ["storeResume", "getProfiles", "createProfile"]
    assert second["error"] is None
    assert _names(second) == ["storeResume", "getProfiles", "updateProfile", "selectProfile", "putSetup"]
    assert len(retried["profiles"]) == 1
    assert retried["selected"] == retried["profiles"][0] == second["profileId"]


def test_a_new_profile_next_to_a_selected_one_is_not_selected(out: dict) -> None:
    added = out["finishes"]["newNextToSelected"]
    first, second = added["runs"]
    assert _names(first) == ["storeResume", "getProfiles", "createProfile", "putSetup"]
    # Finish again with the same name and resume: that profile, not a third.
    assert _names(second) == ["storeResume", "getProfiles", "updateProfile", "putSetup"]
    assert second["calls"][2][1]["id"] == first["profileId"]
    assert added["profiles"] == ["profile_existing", first["profileId"]]
    assert added["selected"] == "profile_existing"


def test_update_writes_the_selected_profile_with_the_new_resume(out: dict) -> None:
    (run,) = out["finishes"]["updateSelected"]["runs"]
    assert _names(run) == ["storeResume", "getProfiles", "updateProfile", "putSetup"]
    assert run["calls"][2][1]["id"] == "profile_existing"
    assert run["calls"][2][1]["resume_record_id"] == "record_new"


def test_the_servers_default_profile_becomes_the_wizards_profile(out: dict) -> None:
    appeared = out["finishes"]["defaultProfileAppears"]
    (run,) = appeared["runs"]
    assert _names(run) == ["storeResume", "getProfiles", "updateProfile", "putSetup"]
    assert run["calls"][2][1]["id"] == "profile_existing"
    assert run["calls"][2][1]["label"] == "Staff platform"
    assert appeared["profiles"] == ["profile_existing"]


def test_an_existing_resume_is_not_stored_again(out: dict) -> None:
    (run,) = out["finishes"]["existingResume"]["runs"]
    assert _names(run) == ["getProfiles", "updateProfile", "putSetup"]
    assert run["calls"][1][1]["resume_record_id"] == "record_old"
    assert run["calls"][1][1]["resume_revision_id"] == "revision_old"


# --- A1: a resume that is already stored is offered and used --------------------------------


def test_a_resume_stored_from_the_cli_is_offered_and_used(out: dict) -> None:
    load = out["loads"]["cliStoredNoProfile"]
    assert load["resumes"] == [{"key": "record_cli/revision_cli", "name": "kar-staff-resume.md", "profileLabel": None}]
    assert load["resumeMode"] == "existing" and load["existingKey"] == "record_cli/revision_cli"
    assert load["screen1Complete"] is True
    assert load["resumeRow"] == "kar-staff-resume.md"
    # Finish stores nothing: it pins the stored resume on the new profile.
    assert _names(load) == ["getProfiles", "createProfile", "selectProfile", "putSetup"]
    assert load["calls"][1][1]["resume_record_id"] == "record_cli"
    assert load["calls"][1][1]["resume_revision_id"] == "revision_cli"
    assert load["resumeRef"] == {"record_id": "record_cli", "revision_id": "revision_cli"}


def test_the_selected_profiles_resume_is_the_one_the_wizard_starts_with(out: dict) -> None:
    load = out["loads"]["selectedProfile"]
    assert load["resumes"] == [{"key": "record_old/revision_old", "name": "old-resume.md", "profileLabel": "Existing"}]
    assert load["resumeMode"] == "existing" and load["existingKey"] == "record_old/revision_old"
    assert _names(load) == ["getProfiles", "updateProfile", "putSetup"]
    assert load["calls"][1][1]["resume_record_id"] == "record_old"


def test_with_no_stored_resume_the_wizard_starts_on_paste(out: dict) -> None:
    for name in ("nothingStored", "noConfig"):
        load = out["loads"][name]
        assert load["resumes"] == [], name
        assert load["resumeMode"] == "paste" and load["existingKey"] is None, name
        assert load["screen1Complete"] is False, name


# --- A2: no Discovery step ------------------------------------------------------------------


def test_the_steps_and_the_review_have_no_discovery_cadence_or_budget(out: dict) -> None:
    steps = out["steps"]
    assert steps["names"] == ["Resume", "Target", "Companies", "Review"]
    assert steps["review"] == [
        "Profile",
        "Resume",
        "Extracted by",
        "Tech stack",
        "Seniority",
        "Target titles",
        "Titles to avoid",
        "Countries",
        "Work mode",
        "Visa sponsorship required",
        "Posting age",
        "Exclude companies",
        "Always watch",
    ]
    for name in steps["names"] + steps["review"]:
        lowered = name.lower()
        assert "discover" not in lowered and "cadence" not in lowered and "budget" not in lowered, name
    assert steps["complete"] == [True, True, True, True]


def test_the_screens_ask_for_no_cadence_and_no_budget() -> None:
    for name in ("FinishScreen.jsx", "SetupWizard.jsx", "StepIndicator.jsx", "ResumeScreen.jsx", "TargetScreen.jsx", "CompaniesScreen.jsx"):
        source = (WIZARD_SRC / name).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("//"))
        for gone in ("Discovery", "cadence", "Cadence", "budget", "Budget", "wz-cadence", "wz-budget"):
            assert gone not in code, f"{name} still has {gone!r}"
    finish = (WIZARD_SRC / "FinishScreen.jsx").read_text(encoding="utf-8")
    assert "<input" not in finish


def test_the_preferences_still_carry_the_cadence_and_the_budget(out: dict) -> None:
    first_run = out["steps"]["firstRun"]
    assert first_run["cadence_days"] == 7 and first_run["budget_usd_per_session"] == 0.5
    # Values saved before (when the wizard still asked) are kept as they are.
    edit = out["steps"]["edit"]
    assert edit["cadence_days"] == 3 and edit["budget_usd_per_session"] == 1.25


def test_the_one_model_choice_starts_on_the_saved_target_else_codex_and_finish_sends_it(out: dict) -> None:
    choice = out["modelChoice"]
    assert choice["savedClaude"] == "claude_cli" and choice["savedOllama"] == "ollama_local"
    assert choice["noConfig"] == "codex_cli" and choice["unknownSaved"] == "codex_cli"
    assert choice["sent"] == "claude_cli"
    assert choice["labels"] == ["Ollama (local)", "Codex (codex CLI)", "Claude (claude CLI)", "OpenRouter (API)"]


def test_the_wizard_has_one_model_select_labelled_model_for_scout() -> None:
    resume = (WIZARD_SRC / "ResumeScreen.jsx").read_text(encoding="utf-8")
    assert resume.count("<select") == resume.count('id="wz-model-target"') == 1
    assert "Model for Scout" in resume
    assert "Which model reads the resume?" not in resume
