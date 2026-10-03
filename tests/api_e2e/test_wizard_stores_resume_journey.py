"""uat-bug-020: the setup wizard stores the resume itself, over HTTP.

Every journey starts from a FRESH home (``gigai setup`` + ``gigai init``,
then the real supervised server: no resume, no profile, no preferences, the
starter ``find-jobs.json``) and ends with the state the wizard's Finish must
leave: one profile, selected, whose pinned resume is the resume the user
gave, and a Jobs page that says to run Update sources.

1. a PASTED resume (``POST /api/resumes {"text"}``);
2. an UPLOADED file (``{"file_name", "content_base64"}``), the route's
   refusals, and the same file added from the CLI afterwards (one import
   path: nothing new is stored);
3. Finish twice: one resume, one profile;
4. (A1) a resume stored with ``gigai scout resume add`` BEFORE the wizard
   ran, and nothing pasted: Finish pins that resume on the profile;
5. the wizard's own code (``ui/src/wizard/wizardState.js`` +
   ``wizardFinish.js`` through ``wizardApi.js``), run under ``node`` against
   the real server: a pasted resume, Finish twice; and the resume the CLI
   stored, nothing pasted. LOUD skip when ``node`` is not on PATH.

Journeys 1-4 send the requests ``wizardFinish.finishSetup`` sends, in its
order (``_finish`` below); journey 5 runs that function itself. Hermetic:
the fake model transport answers the extraction, nothing else calls out.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai import secrets_store
from gigai.cli import cli
from gigai.private_records import list_imports, read_record
from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.bindings import TEST_MODEL_EXTRACT_REPLY
from gigai.workpad import resolve_workpad

from tests.api_e2e.after_journey import assert_clean_and_healthy, timed_request
from tests.api_e2e.harness import add_resume, resolve_workpad_path, setup_and_init, start_server, stop_server

WIZARD_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src" / "wizard"

# 0110-046: no name or contact line (an import strips those: test_resume_import_strips_contact.py), so the
# pinned bytes are the bytes sent.
PASTED = "Staff engineer: nine years of Go, Kafka and Kubernetes on AWS.\nBuilt the billing pipeline.\n"
UPLOADED_NAME = "Jordan Rivera résumé (2026).md"
UPLOADED_STORED_NAME = "Jordan-Rivera-r-sum-2026.md"
UPLOADED = "# Staff engineer — Go, Kafka, Kubernetes.\n\nBuilt the billing pipeline.\n".encode("utf-8")
LABEL = "Staff platform engineer"
TITLES = ["Staff Software Engineer", "Staff Backend Engineer"]
# What ``harness.add_resume`` stores through `gigai scout resume add`.
CLI_RESUME = b"Software engineer with Python service experience.\n"
CLI_RESUME_NAME = "resume.md"
UPDATE_SOURCES_MESSAGE = "No company postings are stored on this machine yet. Run Update sources, then search again."


def _setup_body(titles: list[str]) -> dict[str, object]:
    """``wizardState.setupBody`` for a first run with the wizard's defaults."""

    return {
        "roles": titles,
        "titles_to_avoid": [],
        "countries": ["US"],
        "work_mode": "remote",
        "city": None,
        "visa_sponsorship_required": False,
        "exclude_companies": [],
        "watch_companies": [],
        "company_stage_size": None,
        "industries_include": [],
        "industries_exclude": [],
        "must_have_stack": [],
        "dealbreaker_stack": [],
        "cadence_days": 7,
        "budget_usd_per_session": 0.5,
        "max_age_days": 60,
    }


def _finish(
    client: httpx.Client,
    *,
    resume: dict[str, object] | None,
    existing: dict[str, str] | None = None,
    label: str = LABEL,
    titles: list[str] = TITLES,
) -> dict:
    """The requests ``wizardFinish.finishSetup`` sends, in its order, for a
    wizard opened with the profile that is selected right now (the wizard's
    load) and the resume given on screen 1: ``resume`` (pasted or uploaded,
    stored first), or ``existing`` (a stored one, chosen: nothing to store)."""

    at_load = client.get("/api/profiles").json()
    selected_at_load = at_load["selected_profile_id"]

    stored = None
    if resume is not None:
        stored = client.post("/api/resumes", json=resume)
        assert stored.status_code in (200, 201), stored.text
        ref = stored.json()["resume_ref"]
    else:
        assert existing is not None
        ref = existing

    current = client.get("/api/profiles").json()
    body = {
        "label": label,
        "titles": titles,
        "titles_to_avoid": [],
        "resume_record_id": ref["record_id"],
        "resume_revision_id": ref["revision_id"],
    }
    # wizardState.profileToUpdate: the selected profile ("Update" is the
    # wizard's default when one is selected), else a profile with this name
    # and this resume, else a new one.
    same = [
        item
        for item in current["profiles"]
        if item["state"] != "archived"
        and item["label"] == label
        and item["resume_ref"]["record_id"] == ref["record_id"]
        and item["resume_ref"]["revision_id"] == ref["revision_id"]
    ]
    profile_id = selected_at_load or (same[0]["profile_id"] if same else None)
    if profile_id:
        saved = client.put(f"/api/profiles/{profile_id}", json=body)
        assert saved.status_code == 200, saved.text
    else:
        saved = client.post("/api/profiles", json=body)
        assert saved.status_code == 201, saved.text
    profile = saved.json()["profile"]

    if not current["selected_profile_id"]:
        selection = client.post("/api/profiles/selection", json={"profile_id": profile["profile_id"]})
        assert selection.status_code == 200, selection.text

    # uat-bug-024: with the profile this Finish saved.
    prefs = client.put("/api/setup", json={**_setup_body(titles), "profile_id": profile["profile_id"]})
    assert prefs.status_code == 200, prefs.text
    return {"stored": stored, "profile": profile, "resume_ref": ref}


def _gig_id(home: Path, target: Path) -> str:
    return resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True).gig_id


def _stored_resumes(home: Path, target: Path) -> list[dict[str, object]]:
    imports = list_imports(home_root=home, requested_target=target, family="reference", gig_id=_gig_id(home, target))
    return [item for item in imports if item.get("kind") == "resume"]


def _pinned_bytes(home: Path, target: Path, resume_ref: dict[str, str]) -> bytes:
    record = read_record(
        home_root=home,
        requested_target=target,
        record_id=resume_ref["record_id"],
        revision_id=resume_ref["revision_id"],
        content=True,
        gig_id=_gig_id(home, target),
    )
    content = record["content"]
    assert isinstance(content, bytes)
    return content


def _assert_fresh(client: httpx.Client) -> None:
    setup = client.get("/api/setup")
    assert setup.status_code == 404 and setup.json()["error"]["code"] == "prefs_missing", setup.text
    profiles = client.get("/api/profiles")
    assert profiles.status_code == 200, profiles.text
    assert profiles.json()["profiles"] == [] and profiles.json()["selected_profile_id"] is None
    config = client.get("/api/config")
    assert config.status_code == 200, config.text
    assert config.json()["resume_preview"] is None


def _assert_set_up(
    client: httpx.Client, home: Path, target: Path, *, content: bytes, resume_label: str, label: str = LABEL
) -> dict[str, object]:
    """One profile, selected, pinned to ``content``; Jobs answers and says
    to run Update sources. Returns the profile."""

    profiles = client.get("/api/profiles").json()
    assert len(profiles["profiles"]) == 1, profiles
    profile = profiles["profiles"][0]
    assert profiles["selected_profile_id"] == profile["profile_id"]
    assert profile["label"] == label and profile["titles"] == TITLES and profile["state"] == "active"

    assert _pinned_bytes(home, target, profile["resume_ref"]) == content
    resumes = _stored_resumes(home, target)
    assert len(resumes) == 1, resumes
    assert resumes[0]["label"] == resume_label

    config = client.get("/api/config")
    assert config.status_code == 200, config.text
    config_body = config.json()
    assert config_body["resume_preview"]["record_id"] == profile["resume_ref"]["record_id"]
    assert config_body["resume_preview"]["revision_id"] == profile["resume_ref"]["revision_id"]
    assert config_body["resume_label"] == resume_label
    assert config_body["resume_missing_hint"] is None
    assert config_body["config"]["roles"] == TITLES

    setup = client.get("/api/setup")
    assert setup.status_code == 200, setup.text
    assert setup.json()["prefs"]["roles"] == TITLES

    # What the Jobs page reads when it opens: the profile's runs (none yet)
    # and the stored company postings (none yet: "Run Update sources").
    runs = client.get("/api/runs", params={"profile_id": profile["profile_id"]})
    assert runs.status_code == 200, runs.text
    assert runs.json()["runs"] == []
    sources = client.get("/api/sources/update")
    assert sources.status_code == 200, sources.text
    index = sources.json()["index"]
    assert index["needs_update"] is True and index["message"] == UPDATE_SOURCES_MESSAGE
    return profile


def test_a_pasted_resume_becomes_the_profiles_pinned_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        _assert_fresh(client)

        # Screen 1: the pasted text is analysed; nothing is stored by that.
        extracted = client.post("/api/resume/extract", json={"resume_text": PASTED})
        assert extracted.status_code == 200, extracted.text
        assert extracted.json()["titles"] == TEST_MODEL_EXTRACT_REPLY["titles"] == TITLES
        assert _stored_resumes(home, target) == []

        # The symptom: a profile cannot be made while no resume is stored,
        # and the wizard had no way to store one.
        refused = client.post("/api/profiles", json={"label": LABEL, "titles": TITLES, "titles_to_avoid": []})
        assert refused.status_code == 400, refused.text
        assert "no default resume" in refused.json()["error"]["field_errors"]["resume_record_id"]

        stored, stored_latency = timed_request("POST /api/resumes", lambda: client.post("/api/resumes", json={"text": PASTED}))
        assert stored.status_code == 201, stored.text
        stored_body = stored.json()
        assert stored_body["schema_version"] == "scout-resume-import-response:1"
        assert stored_body["created"] is True and stored_body["label"] == "pasted-resume.txt"
        assert set(stored_body["resume_ref"]) == {"record_id", "revision_id", "content_sha256"}
        # Never the resume's own text.
        assert "Jordan" not in stored.text and "Kafka" not in stored.text
        stored_latency.assert_within_budget()

        finished = _finish(client, resume={"text": PASTED})
        assert finished["stored"].status_code == 200  # the resume stored a moment ago
        assert finished["resume_ref"] == stored_body["resume_ref"]

        profile = _assert_set_up(client, home, target, content=PASTED.encode("utf-8"), resume_label="pasted-resume.txt")
        assert profile["profile_id"] == finished["profile"]["profile_id"]
        assert profile["resume_ref"] == stored_body["resume_ref"]
        assert profile["origin"] == "operator_created"

        # The stored resume is the one the rest of Scout reads.
        by_profile = client.post("/api/resume/extract", json={"profile_id": profile["profile_id"]})
        assert by_profile.status_code == 200, by_profile.text
        assert by_profile.json()["resume"]["content_sha256"] == stored_body["resume_ref"]["content_sha256"]

        # The last screen's key state: booleans, per request, never a value.
        keys, keys_latency = timed_request("GET /api/secrets/status", lambda: client.get("/api/secrets/status"))
        assert keys.status_code == 200, keys.text
        keys_body = keys.json()
        assert set(keys_body) == {"schema_version", "keys"}
        assert keys_body["schema_version"] == "scout-secrets-status:1"
        assert set(keys_body["keys"]) == {"exa", "openai", "openrouter"}
        assert all(isinstance(value, bool) for value in keys_body["keys"].values())
        # The harness exports an Exa key; OpenAI and OpenRouter have none. Jev is not a known service.
        assert keys_body["keys"]["exa"] is True
        assert keys_body["keys"]["openai"] is False and keys_body["keys"]["openrouter"] is False
        keys_latency.assert_within_budget()
        secrets_store.set("OPENAI_API_KEY", "openai-journey-secret-value", home_root=home)
        secrets_store.set("JEV_API_KEY", "jev-left-behind-value", home_root=home)  # unknown now: never reported
        try:
            after_add = client.get("/api/secrets/status")
        finally:
            secrets_store.remove("OPENAI_API_KEY", home_root=home)
            secrets_store.remove("JEV_API_KEY", home_root=home)
        assert after_add.json()["keys"] == {"exa": True, "openai": True, "openrouter": False}
        assert "openai-journey-secret-value" not in after_add.text and "api-e2e-test-key" not in after_add.text
        assert "jev-left-behind-value" not in after_add.text
        assert client.get("/api/secrets/status").json()["keys"]["openai"] is False

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def _assert_error(response: httpx.Response, *, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code, body
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_an_uploaded_file_becomes_the_profiles_pinned_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        _assert_fresh(client)
        encoded = base64.b64encode(UPLOADED).decode("ascii")

        # Refusals: typed, and nothing is stored by any of them.
        for body, status, code in (
            ({}, 422, "resume_input_invalid"),
            ({"text": PASTED, "file_name": "resume.md", "content_base64": encoded}, 422, "resume_input_invalid"),
            ({"text": "   \n"}, 422, "resume_input_invalid"),
            ({"text": 7}, 422, "wrong_type"),
            ({"text": PASTED, "profile_id": "x"}, 422, "unknown_key"),
            ({"file_name": "resume.md"}, 422, "resume_input_invalid"),
            ({"file_name": "resume.md", "content_base64": "not base64!"}, 422, "resume_input_invalid"),
            ({"file_name": "resume.pdf", "content_base64": encoded}, 422, "resume_media_type_unsupported"),
            ({"file_name": "resume.md", "content_base64": base64.b64encode(b"\xff\xfe\x00r").decode("ascii")}, 422, "resume_invalid_utf8"),
            ({"text": "x" * (1_048_576 + 1)}, 413, "resume_too_large"),
        ):
            _assert_error(client.post("/api/resumes", json=body), status=status, code=code)
        _assert_error(
            httpx.post(f"{server.base_url}/api/resumes", json={"text": PASTED}, headers={"Origin": "http://evil.example.test"}),
            status=403, code="forbidden_origin",
        )
        _assert_error(
            httpx.post(f"{server.base_url}/api/resumes", content=b'{"text": "x"}', headers={"Content-Type": "text/plain"}),
            status=415, code="unsupported_media_type",
        )
        assert _stored_resumes(home, target) == []

        finished = _finish(client, resume={"file_name": UPLOADED_NAME, "content_base64": encoded})
        assert finished["stored"].status_code == 201
        assert finished["stored"].json()["label"] == UPLOADED_STORED_NAME
        assert "Jordan Rivera" not in finished["stored"].text

        profile = _assert_set_up(client, home, target, content=UPLOADED, resume_label=UPLOADED_STORED_NAME)
        assert profile["resume_ref"] == finished["resume_ref"]

        # One import path: the same file added with `gigai scout resume add`
        # is the resume the wizard stored. Nothing new, the same record.
        source = tmp_path / UPLOADED_STORED_NAME
        source.write_bytes(UPLOADED)
        added = CliRunner().invoke(
            cli, ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target), "--json"]
        )
        assert added.exit_code == 0, added.output
        cli_body = json.loads(added.output)
        assert cli_body["reference_created"] is False and cli_body["record_created"] is False
        assert cli_body["record_id"] == profile["resume_ref"]["record_id"]
        assert cli_body["revision_id"] == profile["resume_ref"]["revision_id"]
        assert cli_body["profile_id"] == profile["profile_id"]
        assert len(_stored_resumes(home, target)) == 1

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_finish_twice_stores_one_resume_and_one_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        _assert_fresh(client)

        # A Finish that stopped after the profile was saved: the resume and
        # the profile exist, nothing is selected, no preferences are saved.
        stored = client.post("/api/resumes", json={"text": PASTED})
        assert stored.status_code == 201, stored.text
        ref = stored.json()["resume_ref"]
        created = client.post(
            "/api/profiles",
            json={
                "label": LABEL,
                "titles": TITLES,
                "titles_to_avoid": [],
                "resume_record_id": ref["record_id"],
                "resume_revision_id": ref["revision_id"],
            },
        )
        assert created.status_code == 201, created.text
        assert client.get("/api/profiles").json()["selected_profile_id"] is None

        # Finish pressed again: the same resume, the same profile, now selected.
        first = _finish(client, resume={"text": PASTED})
        assert first["stored"].status_code == 200 and first["resume_ref"] == ref
        assert first["profile"]["profile_id"] == created.json()["profile"]["profile_id"]

        # And the whole wizard run once more.
        second = _finish(client, resume={"text": PASTED})
        assert second["stored"].status_code == 200 and second["stored"].json()["created"] is False
        assert second["resume_ref"] == first["resume_ref"]
        assert second["profile"]["profile_id"] == first["profile"]["profile_id"]
        # Nothing about the profile's content changed, so no new revision.
        assert second["profile"]["revision"] == first["profile"]["revision"]

        _assert_set_up(client, home, target, content=PASTED.encode("utf-8"), resume_label="pasted-resume.txt")

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_a_resume_stored_from_the_cli_is_pinned_without_pasting_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A1: `gigai scout resume add <file>`, then the wizard with nothing
    pasted. No profile exists yet, so nothing pins that resume; the wizard
    reads it from ``GET /api/config`` and Finish pins it."""

    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        profiles = client.get("/api/profiles").json()
        assert profiles["profiles"] == [] and profiles["selected_profile_id"] is None

        # What the wizard's "Choose existing" is built from.
        config = client.get("/api/config").json()
        offered = config["resume_preview"]
        assert offered is not None and config["resume_label"] == CLI_RESUME_NAME
        assert _pinned_bytes(home, target, offered) == CLI_RESUME

        # Without the resume's ids the profile is refused: what Finish hit.
        refused = client.post("/api/profiles", json={"label": LABEL, "titles": TITLES, "titles_to_avoid": []})
        assert refused.status_code == 400, refused.text
        assert "no default resume" in refused.json()["error"]["field_errors"]["resume_record_id"]

        finished = _finish(client, resume=None, existing=offered)
        assert finished["stored"] is None  # nothing pasted, nothing stored

        profile = _assert_set_up(client, home, target, content=CLI_RESUME, resume_label=CLI_RESUME_NAME)
        assert profile["profile_id"] == finished["profile"]["profile_id"]
        assert profile["resume_ref"]["record_id"] == offered["record_id"]
        assert profile["resume_ref"]["revision_id"] == offered["revision_id"]

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


NODE_SCRIPT = """
const input = JSON.parse(process.argv[1]);
const realFetch = globalThis.fetch;
globalThis.fetch = (path, init) => realFetch(input.baseUrl + path, init);

const api = await import(input.wizardApiUrl);
const state = await import(input.wizardStateUrl);
const { finishSetup } = await import(input.wizardFinishUrl);

// SetupWizard.jsx's load, then screens 1-4 filled in, then Finish.
async function openWizardAndFinish() {
  let prefs = null;
  let savedPrefs = null;
  try {
    const response = await api.getSetup();
    prefs = response.prefs;
    savedPrefs = response.prefs;
  } catch (error) {
    if (error.status !== 404 || error.code !== "prefs_missing") {
      throw error;
    }
    prefs = error.prefill || {};
  }
  const config = await api.getConfig();
  const profiles = await api.getProfiles();
  const selected = (profiles.profiles || []).find((item) => item.profile_id === profiles.selected_profile_id) || null;
  const resumes = state.existingResumes({ profiles: profiles.profiles || [], config });
  const fields = { ...state.initialFields({ prefs, config, selectedProfile: selected, resumes }), ...input.fields };
  if (!state.screenIsComplete(1, fields)) {
    throw new Error("screen 1 is not complete: the wizard has no resume");
  }
  const result = await finishSetup(
    { fields, selectedProfile: selected, existingPrefs: savedPrefs },
    {
      storeResume: api.storeResume,
      getProfiles: api.getProfiles,
      createProfile: api.createProfile,
      updateProfile: api.updateProfile,
      selectProfile: api.selectProfile,
      putSetup: api.putSetup,
    },
  );
  return {
    profileMode: fields.profileMode,
    resumeMode: fields.resumeMode,
    offered: resumes.map((item) => item.name),
    profileId: result.profile.profile_id,
    selected: result.selectedProfileId,
    resumeRef: result.resumeRef,
  };
}

const runs = [];
for (let index = 0; index < input.times; index += 1) {
  runs.push(await openWizardAndFinish());
}
console.log(JSON.stringify({ runs }));
"""


def _run_the_wizard(node: str, server, *, fields: dict[str, object], times: int) -> list[dict]:
    payload = {
        "baseUrl": server.base_url,
        "wizardApiUrl": (WIZARD_SRC / "wizardApi.js").as_uri(),
        "wizardStateUrl": (WIZARD_SRC / "wizardState.js").as_uri(),
        "wizardFinishUrl": (WIZARD_SRC / "wizardFinish.js").as_uri(),
        "times": times,
        "fields": fields,
    }
    completed = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT, "--", json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)["runs"]


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the wizard's own code was not run against the server")
    return node


def test_the_wizards_own_finish_code_stores_the_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    node = _node()
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        _assert_fresh(client)

        runs = _run_the_wizard(
            node,
            server,
            times=2,
            fields={
                "profileName": f"  {LABEL} ",
                "resumeMode": "paste",
                "resumeText": PASTED,
                "titles": TITLES,
                "workMode": "remote",
            },
        )

        # First Finish: a fresh home, so a new profile, which it selects.
        # Second Finish: the wizard opens on the selected profile ("update").
        assert [run["profileMode"] for run in runs] == ["new", "update"]
        assert runs[0]["offered"] == [] and runs[1]["offered"] == ["pasted-resume.txt"]
        assert runs[0]["profileId"] == runs[1]["profileId"] == runs[0]["selected"] == runs[1]["selected"]
        assert runs[0]["resumeRef"] == runs[1]["resumeRef"]

        profile = _assert_set_up(client, home, target, content=PASTED.encode("utf-8"), resume_label="pasted-resume.txt")
        assert profile["profile_id"] == runs[0]["profileId"]

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_the_wizards_own_code_uses_the_resume_the_cli_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A1, with the wizard's own code: nothing is pasted or chosen by hand."""

    node = _node()
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        assert client.get("/api/profiles").json()["profiles"] == []

        (run,) = _run_the_wizard(
            node, server, times=1, fields={"profileName": LABEL, "titles": TITLES, "workMode": "remote"}
        )
        assert run["offered"] == [CLI_RESUME_NAME]
        assert run["resumeMode"] == "existing" and run["profileMode"] == "new"
        assert run["selected"] == run["profileId"]

        profile = _assert_set_up(client, home, target, content=CLI_RESUME, resume_label=CLI_RESUME_NAME)
        assert profile["profile_id"] == run["profileId"]
        assert profile["resume_ref"]["record_id"] == run["resumeRef"]["record_id"]

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
