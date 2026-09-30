"""0110-013: the setup wizard's "Resume display" step, UI side.

The step's rules (wizardState.js, wizardFinish.js) run under the system
``node`` (LOUD skip when it is not on PATH); the JSX is checked by reading it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
WIZARD_SRC = UI_SRC / "wizard"

NODE_SCRIPT = """
const input = JSON.parse(process.argv[1]);
const state = await import(input.stateUrl);
const { finishSetup } = await import(input.finishUrl);
const model = await import(input.modelUrl);
const c = (kind, value) => ({ kind, value });

const base = state.initialFields({ prefs: {}, config: null, selectedProfile: null, resumes: [] });
const filled = { ...base, profileName: "Staff", resumeText: "Jane", titles: ["Staff Engineer"] };
const draft = model.draftFromResponse({ saved: false, name: "", title: "", contact: [], suggested: { name: "Jane Doe", title: "Engineer", contact: [c("email", "j@x.io")] } });

async function run(fields) {
  const calls = [];
  const api = {
    storeResume: async () => ({ resume_ref: { record_id: "r", revision_id: "v" } }),
    getProfiles: async () => ({ profiles: [], selected_profile_id: null }),
    createProfile: async (body) => ({ profile: { profile_id: "profile_1", label: body.label } }),
    updateProfile: async () => ({ profile: { profile_id: "profile_1" } }),
    selectProfile: async (id) => ({ selected_profile_id: id }),
    putSetup: async (body) => { calls.push(["putSetup"]); return { prefs: body }; },
    putResumeDisplay: async (body) => { calls.push(["putResumeDisplay", body]); return {}; },
  };
  await finishSetup({ fields, selectedProfile: null, existingPrefs: null }, api);
  return calls;
}

const opened = { ...filled, display: { ...draft, title: "Staff Engineer" } };
const skipped = { ...opened, displaySkipped: true };
console.log(JSON.stringify({
  steps: state.STEPS,
  start: { display: base.display, skipped: base.displaySkipped },
  complete: [1, 2, 3, 4, 5].map((step) => state.screenIsComplete(step, base)),
  shouldSave: [state.shouldSaveDisplay(filled), state.shouldSaveDisplay(opened), state.shouldSaveDisplay(skipped)],
  body: state.displayBody(opened, "profile_1"),
  bodySkipped: state.displayBody(skipped, "profile_1"),
  rowOpened: state.reviewRows(opened, []).find(([key]) => key === "PDF header"),
  rowSkipped: state.reviewRows(skipped, []).find(([key]) => key === "PDF header"),
  rowNever: state.reviewRows(filled, []).find(([key]) => key === "PDF header"),
  rowEmpty: state.reviewRows({ ...opened, display: { name: "", title: "", contact: [], prefilled: false } }, []).find(([key]) => key === "PDF header"),
  finishOpened: await run(opened),
  finishSkipped: await run(skipped),
  finishNever: await run(filled),
}));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the wizard's Resume display step was NOT checked")
    payload = {
        "stateUrl": (WIZARD_SRC / "wizardState.js").as_uri(),
        "finishUrl": (WIZARD_SRC / "wizardFinish.js").as_uri(),
        "modelUrl": (UI_SRC / "resumeDisplayModel.js").as_uri(),
    }
    done = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT, "--", json.dumps(payload)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert done.returncode == 0, f"node failed (rc {done.returncode}):\n{done.stderr}"
    return json.loads(done.stdout)


def test_the_step_follows_resume_and_is_never_a_gate(out: dict) -> None:
    assert out["steps"][:2] == ["Resume", "Resume display"]
    assert out["start"] == {"display": None, "skipped": False}
    # step 3 (Target) needs titles, which the bare fields lack; step 2 never gates
    assert out["complete"] == [False, True, False, True, True]


def test_finish_saves_the_step_for_the_saved_profile_only_when_opened_and_not_skipped(out: dict) -> None:
    assert out["shouldSave"] == [False, True, False]
    expected = {
        "name": "Jane Doe",
        "contact": [{"kind": "email", "value": "j@x.io"}],
        "titles": {"profile_1": "Staff Engineer"},
    }
    assert out["body"] == expected and out["bodySkipped"] is None
    # PUT /api/resume-display is the only call the step adds, after the setup save
    assert out["finishOpened"] == [["putSetup"], ["putResumeDisplay", expected]]
    assert out["finishSkipped"] == [["putSetup"]]
    assert out["finishNever"] == [["putSetup"]]


def test_the_review_shows_the_pdf_header(out: dict) -> None:
    assert out["rowOpened"] == ["PDF header", "Jane Doe · Staff Engineer · j@x.io"]
    assert out["rowSkipped"][1].startswith("(skipped")
    assert out["rowNever"][1].startswith("(skipped")
    assert out["rowEmpty"] == ["PDF header", "(empty)"]


def test_the_wizard_renders_the_step_with_a_skip_and_loads_the_saved_header() -> None:
    wizard = (WIZARD_SRC / "SetupWizard.jsx").read_text(encoding="utf-8")
    assert "step === 2 && <ResumeDisplayScreen" in wizard and "const TOTAL_STEPS = 5;" in wizard
    assert "getResumeDisplay(" in wizard and "putResumeDisplay" in wizard and "onSkip={skipDisplay}" in wizard
    screen = (WIZARD_SRC / "ResumeDisplayScreen.jsx").read_text(encoding="utf-8")
    assert "<h2>Resume display</h2>" in screen and 'data-role="skip-resume-display"' in screen
    assert "PRIVACY_NOTE" in screen
    # the header data goes to /api/resume-display only, never a model endpoint
    for name in ("ResumeDisplayScreen.jsx", "wizardFinish.js"):
        source = (WIZARD_SRC / name).read_text(encoding="utf-8")
        assert "extractResume" not in source and "model_target" not in source
