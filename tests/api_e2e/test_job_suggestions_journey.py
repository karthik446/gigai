"""0.1.11 N5 (SPEC 4.4): ``GET /api/jobs/brief``, ``GET`` / ``POST /api/jobs/suggestions`` and ``POST /api/job-resumes/pick``
over HTTP against the real supervised server. Synthetic data only; no route here calls a model.

1. The brief needs a stored assessment (404 ``assessment_missing``), then answers its two parts in two responses that
   never mix: ``part=yours`` holds the user's own resume line and no word of the posting, ``part=posting`` the posting
   inside GigAI's untrusted-text markers and nothing the user wrote. Each response says its one label in ``label`` and
   ``_labels``; the operation (and so ``X-GigAI-Labels``) carries both, because it can answer either part.
2. ``GET /api/jobs`` links to the pick, the two parts of the brief and the suggestions, and no longer to a tailoring.
   With a record (seeded as an assessment made on 0.1.11 writes it), a suggestion is added, set to done and dismissed
   over HTTP; a write that names no writer is the agent's, one from the Scout UI the operator's; the brief then
   names each suggestion by id, with the assessment's ``why`` in the posting part and an agent's in the private one.
3. The suggestions and the pick answer by code: a job whose assessment wrote no record (the offline model answers in
   the shape of before 0.1.11) is 404 ``suggestions_not_found``, no proposal waits (404 ``no_proposed_resume``), and
   a GigAI without the step that picks a stored job again answers 501 ``pick_not_available``. Never a 500.
4. Shape errors are typed 422s that name the allowed keys; a foreign Origin is 403 and changes nothing.

The record itself (add, resolve, dismiss, what a new assessment keeps) is driven on the real store in
``tests/behaviors/scout_pipeline/test_job_suggestions_actions.py``.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from gigai.scout import suggestions as sg
from gigai.scout.find_jobs.assess_contracts import AssessmentSuggestion

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import add_resume, resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

_URL = "https://boards.greenhouse.io/acme/jobs/101"
_RESUME_LINE = "Software engineer with Python service experience."  # harness.add_resume's one line
_HEADER = "X-GigAI-Labels"
_ROW = "req-0000a1"
_ASSESSMENT_WHY = "The posting asks for a message queue and no line names one."
_PHRASE = "experience with a durable message queue"
#: Whether this tree (the server runs the same one) holds the pick's step for a stored assessment.
HAS_PICK_STEP = hasattr(importlib.import_module("gigai.scout.pick"), "settle_stored")


def _error(response: httpx.Response, status: int, code: str) -> dict[str, object]:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text
    return response.json()["error"]


def test_job_brief_suggestions_and_pick_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target, sources_live=True)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        url = quote(_URL, safe="")
        brief, suggestions, pick = f"/api/jobs/brief?url={url}", f"/api/jobs/suggestions?url={url}", "/api/job-resumes/pick"

        # ---- 1. the brief: a stored assessment first, then two parts that never mix ----------------
        assert "gigai scout jobs assess" in str(_error(client.get(brief), 404, "assessment_missing")["message"])
        assert client.post("/api/assess", json={"job": {"job_url": _URL}}).status_code == 200
        job = client.get(f"/api/jobs?url={url}").json()
        posting = job["posting"]
        assert posting["title"] and posting["text"] and posting["title"] not in _RESUME_LINE

        yours = client.get(brief)
        assert yours.status_code == 200, yours.text
        mine = yours.json()
        assert (mine["schema_version"], mine["part"], mine["label"]) == ("scout-job-brief:1", "yours", "user-private")
        assert set(mine["_labels"].values()) == {"user-private"} and mine["job_identity"] == _URL
        assert yours.headers[_HEADER] == "user-private, public-untrusted"  # the operation's labels: it can answer either part
        assert len(mine["rules"]) == 9 and mine["commands"]["store"].startswith(f"gigai scout resume store --in FILE --job-url {_URL} --profile ")
        assert mine["state"]["verdict"] and mine["resume"] is None and mine["basis"] == "profile_resume" and mine["master"] is None
        assert mine["requirements"] and all(set(row) == {"id", "class", "status", "sources", "in_resume", "coverage"} for row in mine["requirements"])
        private = yours.text
        assert posting["title"] not in private and posting["text"][:60] not in private
        assert all(str(row["requirement"]) not in private for row in job["assessments"][0]["matrix"])

        theirs = client.get(f"{brief}&part=posting")
        assert theirs.status_code == 200, theirs.text
        other = theirs.json()
        assert (other["part"], other["label"]) == ("posting", "public-untrusted") and set(other["_labels"].values()) == {"public-untrusted"}
        assert other["posting"].startswith("<<<UNTRUSTED_POSTING_TEXT\n") and other["posting"].endswith("\nEND_UNTRUSTED_POSTING_TEXT>>>")
        assert posting["title"] in other["posting"] and posting["text"][:60] in other["posting"]
        assert [row["id"] for row in other["requirements"]] == [row["id"] for row in mine["requirements"]]
        assert [row["text"] for row in other["requirements"]] == [row["requirement"] for row in job["assessments"][0]["matrix"]]
        assert _RESUME_LINE not in theirs.text and "rules" not in other and "resume" not in other and "sources" not in other
        assert client.get(f"{brief}&part=yours").json() == mine  # the default part, and a read changes nothing

        _error(client.get(f"{brief}&part=both"), 422, "invalid_value")
        assert _error(client.get(f"{brief}&bogus=1"), 422, "unknown_key")["allowed_keys"] == ["part", "profile_id", "url"]
        _error(client.get("/api/jobs/brief"), 422, "invalid_value")
        _error(client.get("/api/jobs/brief?url=not%20a%20link"), 422, "invalid_value")

        # ---- 2. the job's action links ---------------------------------------------------------------
        links = job["links"]
        assert "tailor" not in links
        assert links["pick"] == {"method": "POST", "path": pick, "body": {"job_url": _URL, "action": "refresh"}}
        assert links["brief"] == {"method": "GET", "path": f"{brief}&part=yours"} and links["brief_posting"] == {"method": "GET", "path": f"{brief}&part=posting"}
        assert links["suggestions"] == {"method": "GET", "path": suggestions}
        assert client.get(links["brief"]["path"]).status_code == 200

        # ---- 3. suggestions and the pick say by code what this GigAI holds ------------------------------
        post = "/api/jobs/suggestions"
        no_record = "suggestions_not_found"
        _error(client.get(suggestions), 404, no_record)
        add = {"job_url": _URL, "action": "add", "kind": "gap", "why": "Say which queue the service used.", "requirement": mine["requirements"][0]["id"]}
        _error(client.post("/api/jobs/suggestions", json=add), 404, no_record)
        _error(client.post("/api/jobs/suggestions", json={"job_url": _URL, "action": "dismiss", "suggestion_id": "sg-1"}), 404, no_record)
        _error(client.post(pick, json={"job_url": _URL, "action": "use_proposed"}), 404, "no_proposed_resume")
        _error(client.post(pick, json={"job_url": _URL, "action": "dismiss_proposed"}), 404, "no_proposed_resume")
        refreshed = client.post(pick, json={"job_url": _URL, "action": "refresh"})
        if HAS_PICK_STEP:
            assert refreshed.status_code < 500, refreshed.text
        else:
            message = str(_error(refreshed, 501, "pick_not_available")["message"])
            assert "scout.pick.settle_stored" in message and "re-assess" in message
        _error(client.get("/api/jobs/suggestions?url=https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F999"), 404, "assessment_missing")

        # ---- 3b. with a record (seeded as an assessment made on 0.1.11 writes it): add, resolve, dismiss, list ---
        profile_id, record_path = mine["profile_id"], sg.suggestions_path(home, target, mine["profile_id"], _URL)
        with sg.record_write_lock(record_path):
            sg.save_record(sg.merged(
                None, profile_id=profile_id, job_identity=_URL, stored_path=str(record_path), now="2026-10-05T10:00:00Z", basis={},
                gate={"decision": "hold_question", "ready": False, "reasons": []}, requirements=(sg.CoverageRow(_ROW, "askable", "unclear", ()),),
                suggested=[AssessmentSuggestion(kind="gap", why=_ASSESSMENT_WHY, requirement=_ROW, posting_phrase=_PHRASE)],
            ))
        listed = client.get(suggestions)
        assert listed.status_code == 200, listed.text
        assert listed.headers[_HEADER] == "user-private, public-untrusted"
        assert listed.json()["counts"] == {"open": 1, "done": 0, "dismissed": 0} and listed.json()["gate"]["decision"] == "hold_question"
        assert [(item["id"], item["source"]) for item in listed.json()["suggestions"]] == [("sg-1", "assessment")]

        mine_add = {"job_url": _URL, "action": "add", "kind": "gap", "why": "Say which queue the service used.", "requirement": _ROW}
        by_agent = client.post(post, json=mine_add)  # a loopback write that names no writer and is not from the Scout UI
        assert by_agent.status_code == 200, by_agent.text
        assert by_agent.json()["added"] == "sg-2" and by_agent.json()["suggestions"][-1]["source"] == "agent"
        from_ui = client.post(post, json={**mine_add, "why": "And how long."}, headers={"Origin": server.base_url})
        assert from_ui.json()["added"] == "sg-3" and from_ui.json()["suggestions"][-1]["source"] == "operator"
        named = client.post(post, json={**mine_add, "why": "And the scale.", "actor": "operator"})
        assert named.json()["added"] == "sg-4" and named.json()["suggestions"][-1]["source"] == "operator"
        _error(client.post(post, json={**mine_add, "requirement": "req-00ffff"}), 422, "unknown_requirement")
        _error(client.post(post, json={**mine_add, "why": "Ask zora.quillfeather@zq.example.invalid."}), 422, "personal_info_refused")
        _error(client.post(post, json={"job_url": _URL, "action": "add", "kind": "gap", "why": "It names nothing."}), 422, "invalid_value")
        _error(client.post(post, json={**mine_add, "kind": "rewrite"}), 422, "bad_enum")

        resolved = client.post(post, json={"job_url": _URL, "action": "resolve", "suggestion_id": "sg-2", "how": "answer", "ref": "tooling:queues", "actor": "agent"})
        assert resolved.status_code == 200 and resolved.json()["resolved"] == "sg-2", resolved.text
        done = next(item for item in resolved.json()["suggestions"] if item["id"] == "sg-2")
        assert done["status"] == "done" and done["resolved"] == {"by": "agent", "at": done["resolved"]["at"], "how": "answer", "ref": "tooling:queues"}
        dismissed = client.post(post, json={"job_url": _URL, "action": "dismiss", "suggestion_id": "sg-3"})
        assert dismissed.json()["dismissed"] == "sg-3" and dismissed.json()["counts"] == {"open": 2, "done": 1, "dismissed": 1}
        _error(client.post(post, json={"job_url": _URL, "action": "resolve", "suggestion_id": "sg-9", "how": "answer"}), 404, "suggestion_not_found")
        _error(client.post(post, json={"job_url": _URL, "action": "resolve", "suggestion_id": "sg-1", "how": "dismissed"}), 422, "invalid_value")
        assert [item["id"] for item in client.get(f"{suggestions}&status=open").json()["suggestions"]] == ["sg-1", "sg-4"]
        _error(client.get(f"{suggestions}&status=closed"), 422, "invalid_value")

        # The brief of the job, in its two parts: the assessment's words are the posting part's, the user's and the agent's the private part's.
        private_now, public_now = client.get(brief), client.get(f"{brief}&part=posting")
        by_id = {item["id"]: item for item in private_now.json()["suggestions"]}
        assert by_id["sg-1"]["why"] is None and by_id["sg-4"]["why"] == "And the scale." and by_id["sg-2"]["status"] == "done"
        assert _ASSESSMENT_WHY not in private_now.text and _PHRASE not in private_now.text
        assert public_now.json()["suggestions"] == [{"id": "sg-1", "posting_phrase": _PHRASE, "why": _ASSESSMENT_WHY}]
        assert "And the scale." not in public_now.text and "Say which queue" not in public_now.text
        assert private_now.json()["state"]["gate"]["decision"] == "hold_question"

        # ---- 4. shape errors, and the write guards ------------------------------------------------------
        assert "allowed_keys" in _error(client.post(post, json={**add, "bogus": 1}), 422, "unknown_key")
        _error(client.post(post, json={"job_url": _URL}), 422, "invalid_value")
        _error(client.post(post, json={"job_url": _URL, "action": "delete", "suggestion_id": "sg-1"}), 422, "invalid_value")
        _error(client.post(post, json={**add, "suggestion_id": "sg-1"}), 422, "invalid_value")  # a key of another action is never ignored
        _error(client.post(post, json={**add, "why": 7}), 422, "wrong_type")
        _error(client.post(post, json={**add, "actor": "robot"}), 422, "invalid_value")
        _error(client.post(post, json=[1]), 422, "wrong_type")
        _error(client.post(pick, json={"job_url": _URL, "action": "tailor"}), 422, "invalid_value")
        _error(client.post(pick, json={"job_url": _URL, "action": "refresh", "bogus": 1}), 422, "unknown_key")
        _error(client.post(pick, json={"action": "refresh"}), 422, "invalid_value")
        _error(client.get(f"{suggestions}&status=open&bogus=1"), 422, "unknown_key")
        for route, body in ((post, add), (pick, {"job_url": _URL, "action": "refresh"})):
            foreign = httpx.post(f"{server.base_url}{route}", json=body, headers={"Origin": "http://evil.example.test"})
            assert foreign.status_code == 403 and foreign.json()["error"]["code"] == "forbidden_origin"

        # The served spec names the four routes with their labels.
        paths = client.get("/api/openapi.json").json()["paths"]
        for path, method in (("/api/jobs/brief", "get"), ("/api/jobs/suggestions", "get"), ("/api/jobs/suggestions", "post"), ("/api/job-resumes/pick", "post")):
            assert paths[path][method]["x-gigai-labels"] == ["user-private", "public-untrusted"], (path, method)
            assert paths[path][method]["x-gigai-external"] == "none"
        assert "GET /api/jobs/brief?url=<posting url>&part=posting" in client.get("/llms.txt").text
        assert json.loads(yours.text)["sends"]["nothing"].startswith("The brief, the hand-back")

        workpad = resolve_workpad_path(home, target)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
