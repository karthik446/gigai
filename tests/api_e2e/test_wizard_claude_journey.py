"""uat-bug-035: the setup wizard with Claude (the ``claude`` CLI) as the model.

A FRESH home (real ``gigai setup`` + ``gigai init``) with a fake ``claude``
first on ``PATH`` (``tests/support/fake_claude.py``): setup finds it the way
it finds a real install and configures ``claude-default``. The real
supervised server inherits that ``PATH``, so the real ``ClaudeCLIAdapter``
runs the fake; no live model call.

The wizard's own code (``wizardState.js``/``wizardApi.js``/
``wizardFinish.js``) runs under ``node`` against the server: it offers
"Claude (claude CLI)", the Claude choice extracts the resume through the
claude CLI (plan mode, the adapter's default), and Finish completes. A quick
assessment with ``model_target: claude_cli`` then goes through the same CLI.
With no ``claude`` on ``PATH`` the extraction says so (503
``model_target_unavailable``, the adapter's message). LOUD skip when
``node`` is not on ``PATH``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.bindings import TEST_MODEL_EXTRACT_REPLY

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    poll_until_terminal,
    resolve_workpad_path,
    run_request_body,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)
from tests.support.fake_claude import calls, write_fake_claude

WIZARD_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src" / "wizard"

PASTED = "Jordan Rivera\nStaff engineer: nine years of Python, Kafka and Kubernetes on AWS.\n"
LABEL = "Claude profile"
PLAN_ARGV = ["-p", "--output-format", "json", "--no-session-persistence", "--permission-mode", "plan", "--tools", ""]
POSTING = (
    "Acme is hiring a Software Engineer to build reliable Python services. "
    "Requirements: Python in production; GCP experience is a plus. Remote within the US."
)

NODE_SCRIPT = """
const input = JSON.parse(process.argv[1]);
const realFetch = globalThis.fetch;
globalThis.fetch = (path, init) => realFetch(input.baseUrl + path, init);

const api = await import(input.wizardApiUrl);
const state = await import(input.wizardStateUrl);
const { finishSetup } = await import(input.wizardFinishUrl);

// SetupWizard.jsx's load; screen 1 with the model chosen; its Extract
// button (handleExtract); Next copies the suggested titles; then Finish.
let prefs = {};
try {
  prefs = (await api.getSetup()).prefs;
} catch (error) {
  if (error.status !== 404 || error.code !== "prefs_missing") throw error;
  prefs = error.prefill || {};
}
const config = await api.getConfig();
const profiles = await api.getProfiles();
const offered = state.MODEL_TARGETS.map((target) => [target, state.modelTargetLabel(target), state.MODEL_TARGET_HINTS[target]]);
let fields = {
  ...state.initialFields({ prefs, config, selectedProfile: null, resumes: [] }),
  profileName: input.label,
  resumeMode: "paste",
  resumeText: input.resumeText,
  modelTarget: input.modelTarget,
  workMode: "remote",
};
const out = { offered };
try {
  const result = await api.extractResume(state.extractBody(fields, profiles.profiles || []));
  fields = { ...fields, extraction: result, stack: result.stack || [], suggestedTitles: result.titles || [] };
  fields = { ...fields, titles: [...fields.suggestedTitles], titlesSeeded: true };
  out.extraction = result;
  out.review = state.reviewRows(fields, []);
} catch (error) {
  out.extractError = { status: error.status, code: error.code, message: error.message };
}
if (input.finish) {
  const finished = await finishSetup(
    { fields, selectedProfile: null, existingPrefs: null },
    {
      storeResume: api.storeResume,
      getProfiles: api.getProfiles,
      createProfile: api.createProfile,
      updateProfile: api.updateProfile,
      selectProfile: api.selectProfile,
      putSetup: api.putSetup,
    },
  );
  out.finished = { profileId: finished.profile.profile_id, selected: finished.selectedProfileId, titles: finished.profile.titles };
}
console.log(JSON.stringify(out));
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the wizard's own code was not run against the server")
    return node


def _run_the_wizard(node: str, server, *, finish: bool) -> dict:
    payload = {
        "baseUrl": server.base_url,
        "wizardApiUrl": (WIZARD_SRC / "wizardApi.js").as_uri(),
        "wizardStateUrl": (WIZARD_SRC / "wizardState.js").as_uri(),
        "wizardFinishUrl": (WIZARD_SRC / "wizardFinish.js").as_uri(),
        "label": LABEL,
        "resumeText": PASTED,
        "modelTarget": "claude_cli",
        "finish": finish,
    }
    completed = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT, "--", json.dumps(payload)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def _put_on_path(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")


def test_the_wizard_chooses_claude_extracts_through_it_and_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    node = _node()
    record = write_fake_claude(tmp_path / "fake-bin")
    _put_on_path(monkeypatch, tmp_path / "fake-bin")
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        out = _run_the_wizard(node, server, finish=True)

        # The select offers Claude by its label, with a hint naming Anthropic.
        assert ["claude_cli", "Claude (claude CLI)"] in [row[:2] for row in out["offered"]]
        hint = dict((row[0], row[2]) for row in out["offered"])["claude_cli"]
        assert "Anthropic" in hint and "default model" in hint

        # The extraction went to the claude CLI, in plan mode.
        assert "extractError" not in out, out.get("extractError")
        assert out["extraction"]["model_target"] == "claude_cli"
        # A CLI port has no ``name``: the route names the adapter kind (codex the same).
        assert out["extraction"]["resolved_target"] == "claude_cli"
        assert out["extraction"]["titles"] == TEST_MODEL_EXTRACT_REPLY["titles"]
        assert ["Extracted by", out["extraction"]["extractor"] + " (claude_cli)"] in out["review"]
        [extract_call] = calls(record)
        assert extract_call["kind"] == "extract" and extract_call["argv"] == PLAN_ARGV

        # Finish: one profile, selected, with the extracted titles.
        assert out["finished"]["selected"] == out["finished"]["profileId"]
        assert out["finished"]["titles"] == TEST_MODEL_EXTRACT_REPLY["titles"]
        profiles = client.get("/api/profiles").json()
        assert [item["profile_id"] for item in profiles["profiles"]] == [out["finished"]["profileId"]]
        assert client.get("/api/setup").status_code == 200

        # uat-bug-038: Finish saved the wizard's one model choice as the
        # default model target (the starter config's is ollama_local).
        saved_config = json.loads((target / "find-jobs.json").read_text(encoding="utf-8"))
        assert saved_config["default_model_target"] == "claude_cli"

        # A quick assessment with Claude goes through the same CLI, plan mode.
        assessed = client.post(
            "/api/assess", json={"job": {"job_text": POSTING}, "model_target": "claude_cli", "origin": "quick_assess"}
        )
        assert assessed.status_code == 200, assessed.text
        assert assessed.json()["result"]["verdict"] == "pending_user_answers"
        assess_call = calls(record)[-1]
        assert assess_call["kind"] == "assess" and assess_call["argv"] == PLAN_ARGV

        # ... and a run started the way the run dialog starts one (its select
        # opens on the config's default target) uses it: sealed and ranked.
        write_offline_find_jobs_config(target, sources_live=True)
        config_body = client.get("/api/config").json()
        assert config_body["config"]["default_model_target"] == "claude_cli"
        run_response = client.post(
            "/api/run",
            json=run_request_body(
                config_body["config_digest"], model_target=config_body["config"]["default_model_target"]
            ),
        )
        assert run_response.status_code == 202, run_response.text
        run_id = run_response.json()["run_id"]
        status_body = poll_until_terminal(client, run_id)
        assert status_body["status"] == "succeeded", status_body
        workpad = resolve_workpad_path(home, target)
        run_dir = workpad / "runs" / run_id
        sealed = json.loads((run_dir / "sealed" / "find-jobs-run-input.json").read_text(encoding="utf-8"))
        assert sealed["model_target"] == "claude_cli"
        sealed_config = json.loads((run_dir / "sealed" / "find-jobs-config.json").read_text(encoding="utf-8"))
        assert sealed_config["default_model_target"] == "claude_cli"
        assess = json.loads((run_dir / "outputs" / "assess.json").read_text(encoding="utf-8"))
        assert assess["model_target"] == "claude_cli" and assess["producer"]["adapter"] == "claude_cli"
        # (No rank.json here: the ranking pass runs only over the import cap;
        # test_claude_cli_target.py pins rank.json's claude_cli target.)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_without_claude_on_path_the_wizard_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    node = _node()
    # setup still configures claude-default (the fake is on PATH for setup) ...
    write_fake_claude(tmp_path / "fake-bin")
    _put_on_path(monkeypatch, tmp_path / "fake-bin")
    home, target = setup_and_init(tmp_path)
    # ... then claude is gone: the system dirs plus node's and git's, no claude.
    git = shutil.which("git")
    assert git is not None
    dirs = dict.fromkeys([str(Path(node).parent), str(Path(git).parent), "/usr/bin", "/bin"])
    without_claude = os.pathsep.join(dirs)
    if shutil.which("claude", path=without_claude) is not None:
        pytest.skip("a real claude sits next to node or git; cannot build a PATH without claude")
    monkeypatch.setenv("PATH", without_claude)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        out = _run_the_wizard(node, server, finish=False)
        assert out["extractError"] == {
            "status": 503,
            "code": "model_target_unavailable",
            "message": "claude executable is not available on PATH",
        }
        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
