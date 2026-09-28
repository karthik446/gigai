"""uat-bug-024 and the stored-resume analysis, over HTTP against the real server.

Every journey starts from a FRESH home (``gigai setup`` + ``gigai init``,
then the real supervised server) and drives Scout only through HTTP.

1. uat-bug-024, the requests the wizard sends: profile A is set up and
   selected; "Create a new profile" makes B with other titles. A's titles
   and revision are as they were, B has the new titles. Then what ``PUT
   /api/setup`` does with no ``profile_id`` (the selected profile, as
   before) and its refusals.
2. uat-bug-024, the wizard's own code (``wizardFinish.js`` through
   ``wizardApi.js``) under ``node`` against the real server. This is the
   journey that shows the symptom on a tree without the fix: the wizard
   there sends no ``profile_id`` and A's titles become B's.
3. A resume stored with ``gigai scout resume add "My Resume.md"`` (a file
   name with a space: uat-bug-023) before any profile exists: ``POST
   /api/resume/extract`` reads it by ``resume_ref``, and the wizard's own
   code sends that body. LOUD skip of the node half when ``node`` is not on
   PATH.

Hermetic: the fake model transport answers the extraction, nothing else
calls out.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.private_records import list_imports
from gigai.scout.find_jobs.bindings import TEST_MODEL_EXTRACT_REPLY
from gigai.workpad import resolve_workpad

from tests.api_e2e.after_journey import assert_clean_and_healthy, timed_request
from tests.api_e2e.harness import resolve_workpad_path, setup_and_init, start_server, stop_server
from tests.api_e2e.test_wizard_stores_resume_journey import WIZARD_SRC, _node, _run_the_wizard, _setup_body

RESUME_A = "Jordan Rivera\nStaff engineer: nine years of Go, Kafka and Kubernetes on AWS.\n"
RESUME_B = "Jordan Rivera\nEngineering manager: four years leading a platform team of nine.\n"
LABEL_A = "Staff platform engineer"
LABEL_B = "Engineering management"
TITLES_A = ["Staff Software Engineer", "Staff Backend Engineer"]
TITLES_B = ["Engineering Manager", "Director of Engineering"]
UNKNOWN_PROFILE = "profile_00000000-0000-4000-8000-000000000000"
CLI_RESUME_NAME = "My Resume.md"
CLI_RESUME = "Jordan Rivera\nStaff engineer: eleven years of Rust and Postgres.\n".encode("utf-8")


def _assert_error(response: httpx.Response, *, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code, body


def _profiles(client: httpx.Client) -> tuple[dict[str, dict], str | None]:
    body = client.get("/api/profiles").json()
    return {item["profile_id"]: item for item in body["profiles"]}, body["selected_profile_id"]


def _wizard_creates_a_profile(client: httpx.Client, *, resume_text: str, label: str, titles: list[str]) -> dict:
    """The requests ``wizardFinish.finishSetup`` sends for "Create a new
    profile" (or a first profile), in its order."""

    stored = client.post("/api/resumes", json={"text": resume_text})
    assert stored.status_code in (200, 201), stored.text
    ref = stored.json()["resume_ref"]

    current = client.get("/api/profiles").json()
    created = client.post(
        "/api/profiles",
        json={
            "label": label,
            "titles": titles,
            "titles_to_avoid": [],
            "resume_record_id": ref["record_id"],
            "resume_revision_id": ref["revision_id"],
        },
    )
    assert created.status_code == 201, created.text
    profile = created.json()["profile"]

    if not current["selected_profile_id"]:
        selection = client.post("/api/profiles/selection", json={"profile_id": profile["profile_id"]})
        assert selection.status_code == 200, selection.text

    saved, latency = timed_request(
        "PUT /api/setup (profile_id)",
        lambda: client.put("/api/setup", json={**_setup_body(titles), "profile_id": profile["profile_id"]}),
    )
    assert saved.status_code == 200, saved.text
    latency.assert_within_budget()
    return {"profile": profile, "prefs": saved.json()["prefs"]}


def _assert_a_is_untouched_and_b_is_new(client: httpx.Client, a_before: dict, b_id: str) -> None:
    profiles, selected = _profiles(client)
    assert set(profiles) == {a_before["profile_id"], b_id}
    assert selected == a_before["profile_id"]

    # The symptom: the selected profile's titles were overwritten by B's.
    a_after = profiles[a_before["profile_id"]]
    assert a_after["titles"] == TITLES_A
    assert a_after["revision"] == a_before["revision"]
    assert a_after == a_before  # queries, digest, resume, updated_at: nothing wrote to it

    b = profiles[b_id]
    assert b["label"] == LABEL_B and b["titles"] == TITLES_B and b["queries"] == TITLES_B
    assert b["state"] == "active"

    # What a search uses is the selected profile's titles; the shared
    # preferences list every active profile's.
    assert client.get("/api/config").json()["config"]["roles"] == TITLES_A
    assert client.get("/api/setup").json()["prefs"]["roles"] == [*TITLES_A, *TITLES_B]


def test_a_new_profile_leaves_the_selected_profiles_titles_as_they_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        first = _wizard_creates_a_profile(client, resume_text=RESUME_A, label=LABEL_A, titles=TITLES_A)
        profiles, selected = _profiles(client)
        a_before = profiles[first["profile"]["profile_id"]]
        assert selected == a_before["profile_id"] and a_before["titles"] == TITLES_A

        second = _wizard_creates_a_profile(client, resume_text=RESUME_B, label=LABEL_B, titles=TITLES_B)
        b_id = second["profile"]["profile_id"]
        assert second["prefs"]["roles"] == [*TITLES_A, *TITLES_B]
        _assert_a_is_untouched_and_b_is_new(client, a_before, b_id)

        # Refused, and nothing is saved by a refused request.
        prefs_before = client.get("/api/setup").json()
        _assert_error(
            client.put("/api/setup", json={**_setup_body(["VP Engineering"]), "profile_id": UNKNOWN_PROFILE}),
            status=404, code="profile_not_found",
        )
        for bad in ("", 7):
            invalid = client.put("/api/setup", json={**_setup_body(["VP Engineering"]), "profile_id": bad})
            _assert_error(invalid, status=400, code="invalid_value")
            assert set(invalid.json()["error"]["field_errors"]) == {"profile_id"}
        assert client.get("/api/setup").json() == prefs_before
        _assert_a_is_untouched_and_b_is_new(client, a_before, b_id)

        # No profile_id (the Preferences page, every older caller): the
        # SELECTED profile gets the titles, as before. B is left alone.
        edited = ["Principal Engineer"]
        for body in (_setup_body(edited), {**_setup_body(edited), "profile_id": None}):
            saved = client.put("/api/setup", json=body)
            assert saved.status_code == 200, saved.text
        profiles, selected = _profiles(client)
        assert selected == a_before["profile_id"]
        assert profiles[a_before["profile_id"]]["titles"] == edited
        assert profiles[a_before["profile_id"]]["revision"] == a_before["revision"] + 1
        assert profiles[b_id]["titles"] == TITLES_B
        assert profiles[b_id]["revision"] == second["profile"]["revision"]

        # An archived profile takes no titles.
        archived = client.post(f"/api/profiles/{b_id}/archive", json={})
        assert archived.status_code == 200, archived.text
        _assert_error(
            client.put("/api/setup", json={**_setup_body(["VP Engineering"]), "profile_id": b_id}),
            status=409, code="scout_profile_archived",
        )
        assert _profiles(client)[0][b_id]["titles"] == TITLES_B

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def test_the_wizards_own_code_creates_a_profile_next_to_the_selected_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = _node()
    home, target = setup_and_init(tmp_path)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client

        (first,) = _run_the_wizard(
            node,
            server,
            times=1,
            fields={"profileName": LABEL_A, "resumeMode": "paste", "resumeText": RESUME_A, "titles": TITLES_A, "workMode": "remote"},
        )
        profiles, selected = _profiles(client)
        a_before = profiles[first["profileId"]]
        assert selected == first["profileId"] and a_before["titles"] == TITLES_A

        # The wizard again, "Create a new profile", other titles. Twice: a
        # second Finish finds the profile the first one made.
        runs = _run_the_wizard(
            node,
            server,
            times=2,
            fields={
                "profileMode": "new",
                "profileName": LABEL_B,
                "resumeMode": "paste",
                "resumeText": RESUME_B,
                "titles": TITLES_B,
                "titlesToAvoid": [],
                "workMode": "remote",
            },
        )
        assert [run["profileMode"] for run in runs] == ["new", "new"]
        assert runs[0]["profileId"] == runs[1]["profileId"] != a_before["profile_id"]
        assert [run["selected"] for run in runs] == [a_before["profile_id"]] * 2

        _assert_a_is_untouched_and_b_is_new(client, a_before, runs[0]["profileId"])

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


EXTRACT_NODE_SCRIPT = """
const input = JSON.parse(process.argv[1]);
const realFetch = globalThis.fetch;
globalThis.fetch = (path, init) => realFetch(input.baseUrl + path, init);

const api = await import(input.wizardApiUrl);
const state = await import(input.wizardStateUrl);

// SetupWizard.jsx's load, then "Analyze resume" with nothing typed.
const config = await api.getConfig();
const profiles = (await api.getProfiles()).profiles || [];
const resumes = state.existingResumes({ profiles, config });
const fields = state.initialFields({ prefs: {}, config, selectedProfile: null, resumes });
const body = state.extractBody(fields, profiles);
const result = await api.extractResume(body);
console.log(JSON.stringify({ offered: resumes.map((item) => item.name), resumeMode: fields.resumeMode, body, result }));
"""


def _gig_id(home: Path, target: Path) -> str:
    return resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True).gig_id


def test_a_resume_stored_from_the_cli_is_analysed_before_any_profile_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    source = tmp_path / "in" / CLI_RESUME_NAME
    source.parent.mkdir()
    source.write_bytes(CLI_RESUME)
    added = CliRunner().invoke(
        cli, ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target), "--json"]
    )
    assert added.exit_code == 0, added.output  # uat-bug-023: the name has a space
    cli_body = json.loads(added.output)
    assert cli_body["profile_id"] is None  # nothing pins it

    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        assert client.get("/api/profiles").json() == {
            "schema_version": "scout-profiles-response:1",
            "profiles": [],
            "selected_profile_id": None,
        }
        config = client.get("/api/config").json()
        offered = config["resume_preview"]
        assert offered["record_id"] == cli_body["record_id"] and offered["revision_id"] == cli_body["revision_id"]
        # What is stored as the label: the file's own name.
        assert config["resume_label"] == CLI_RESUME_NAME
        gig_id = _gig_id(home, target)
        imports_before = list_imports(home_root=home, requested_target=target, family="reference", gig_id=gig_id)

        ref = {"record_id": offered["record_id"], "revision_id": offered["revision_id"]}
        extracted, latency = timed_request(
            "POST /api/resume/extract (resume_ref)",
            lambda: client.post("/api/resume/extract", json={"resume_ref": ref, "model_target": "ollama_local"}),
        )
        assert extracted.status_code == 200, extracted.text
        body = extracted.json()
        assert body["schema_version"] == "scout-resume-extract-response:1"
        assert body["stack"] == TEST_MODEL_EXTRACT_REPLY["stack"]
        assert body["seniority"] == TEST_MODEL_EXTRACT_REPLY["seniority"]
        assert body["titles"] == TEST_MODEL_EXTRACT_REPLY["titles"]
        assert body["extractor"] == "model" and body["model_target"] == "ollama_local"
        assert body["resume"] == {"profile_id": None, "content_sha256": offered["content_sha256"]}
        for leak in ("eleven years", "Jordan Rivera", "Rust"):
            assert leak not in extracted.text
        latency.assert_within_budget()

        # With the digest GET /api/config gave: the same; with another: refused.
        assert client.post("/api/resume/extract", json={"resume_ref": offered}).status_code == 200
        _assert_error(
            client.post("/api/resume/extract", json={"resume_ref": {**ref, "content_sha256": "sha256:" + "0" * 64}}),
            status=404, code="resume_digest_mismatch",
        )
        _assert_error(
            client.post("/api/resume/extract", json={"resume_ref": {**ref, "revision_id": "revision_missing"}}),
            status=404, code="resume_unavailable",
        )
        _assert_error(
            client.post("/api/resume/extract", json={"resume_ref": ref, "resume_text": "text"}),
            status=422, code="resume_input_invalid",
        )
        _assert_error(client.post("/api/resume/extract", json={"resume_ref": "record"}), status=422, code="wrong_type")
        _assert_error(
            httpx.post(f"{server.base_url}/api/resume/extract", json={"resume_ref": ref}, headers={"Origin": "http://evil.example.test"}),
            status=403, code="forbidden_origin",
        )

        # Analysing stored nothing and made no profile.
        assert list_imports(home_root=home, requested_target=target, family="reference", gig_id=gig_id) == imports_before
        assert client.get("/api/profiles").json()["profiles"] == []

        # The wizard's own code: opened, "Analyze resume" pressed.
        node = _node()
        payload = {
            "baseUrl": server.base_url,
            "wizardApiUrl": (WIZARD_SRC / "wizardApi.js").as_uri(),
            "wizardStateUrl": (WIZARD_SRC / "wizardState.js").as_uri(),
        }
        completed = subprocess.run(
            [node, "--input-type=module", "-e", EXTRACT_NODE_SCRIPT, "--", json.dumps(payload)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
        wizard = json.loads(completed.stdout)
        assert wizard["offered"] == [CLI_RESUME_NAME] and wizard["resumeMode"] == "existing"
        assert wizard["body"] == {"model_target": "ollama_local", "resume_ref": ref}
        assert wizard["result"]["titles"] == TEST_MODEL_EXTRACT_REPLY["titles"]
        assert wizard["result"]["resume"]["content_sha256"] == offered["content_sha256"]

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
