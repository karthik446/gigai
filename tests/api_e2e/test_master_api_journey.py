"""0.1.10.9 master P5: the master resume's routes over HTTP, against the real supervised server.

Two profiles hold two synthetic resumes (the master-resume spike's invented
person: a newer 2-page resume and an older 3-page one that words two lines
differently and states older numbers on a third).  Everything below goes
through HTTP, no model call:

- before a master exists the reads say so with 200 (``master: null``);
- ``GET /api/master/migration`` shows the merge and its ONE question and
  writes nothing; ``POST`` without the answer writes nothing; with it the
  master is made (201) and each profile keeps the resume it had;
- ``POST`` / ``PUT /api/master/lines`` and ``/entries`` add, edit, retire and
  restore by id, each one revision; a stale ``revision`` is 409 with the
  current one; contact-shaped text is 422 and never echoed; the writer is
  the body's ``actor``, else the ``X-GigAI-Actor`` header, else operator for
  a browser page of this server (``Origin``) and agent for anything else;
- ``GET /api/master/history`` lists the revisions and what is retired;
- ``GET`` / ``POST /api/master/selection``: a new line is offered ("1 new
  master line: refresh?"), a refresh makes the profile's resume its selection;
- ``POST /api/profiles`` with a master stored answers at once and the new
  profile's own selection lands after the answer (``pending``);
- ``PUT /api/tailored-resumes/selection`` removes a picked line from a
  resume tailored from the master and adds it back.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout import profile_records
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessResumeInput
from gigai.scout.find_jobs.contracts import PinnedResume
from gigai.scout.resume_import import import_resume_file
from gigai.scout.tailored_resume import TailorRequest, run_tailored_resume
from gigai.workpad import resolve_workpad

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config
from tests.support.latency import latency_bound
from tests.support.master_tailor import copies_what_it_is_shown, install_prompt_model
from tests.support.tailor_cases import ollama_config

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "master"
PLANTED = "jane.quillfeather@example.com"


def _posting() -> dict[str, str]:
    head, _, body = (FIXTURES / "postings" / "p1-staff-ai-agent-platform.md").read_text(encoding="utf-8").partition("\n\n")
    return {**dict(line.split(": ", 1) for line in head.splitlines()), "text": body.strip() + "\n"}


def _wait_not_pending(client, profile_id: str) -> dict:
    deadline = time.monotonic() + latency_bound(60.0)
    while True:
        body = client.get("/api/master/selection").json()
        if profile_id not in body["pending"]:
            return body
        assert time.monotonic() < deadline, "the new profile's first selection never landed"
        time.sleep(0.1)


def test_the_master_resume_over_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    added = CliRunner().invoke(cli, ["scout", "resume", "add", str(FIXTURES / "legacy-ai.md"), "--home", str(home), "--target", str(target), "--json"])
    assert added.exit_code == 0, added.output
    write_offline_find_jobs_config(target)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    default = profile_records.selected_profile(resolved, home_root=home, target=target)
    assert default is not None
    older = import_resume_file(home_root=home, requested_target=target, source=FIXTURES / "legacy-swe.md")
    swe = profile_records.create_profile(
        resolved, label="Staff Software Engineer", titles=("staff software engineer",), titles_to_avoid=(), queries=("staff software engineer",),
        resume_ref=PinnedResume(older.record_id, older.revision_id, older.content_sha256),
    )
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        browser = {"Origin": server.base_url}  # what a page of this server sends on every write

        # --- no master yet: a state, not an error ---------------------------------------------------
        empty = client.get("/api/master")
        assert empty.status_code == 200 and empty.json() == {"schema_version": "scout-master:1", "master": None, "current_revision": None, "profiles": [], "shown_by": {}}
        assert empty.headers["X-GigAI-Labels"] == "user-private"
        assert client.get("/api/master/history").json() == {"schema_version": "scout-master-history:1", "revision": None, "revisions": [], "retired": []}
        assert client.get("/api/master/selection").json() == {"schema_version": "scout-master-selection:1", "master": None, "profiles": [], "pending": []}
        refused = client.post("/api/master/lines", json={"revision": 0, "section": "other", "text": "A line."})
        assert refused.status_code == 404 and refused.json()["error"]["code"] == "master_not_found"

        # --- the migration: asked, then made -----------------------------------------------------------
        plan = client.get("/api/master/migration")
        assert plan.status_code == 200, plan.text
        plan = plan.json()
        assert (plan["schema_version"], plan["status"], plan["written"], plan["master"], plan["blocked"]) == ("scout-master-migration:1", "needs_answers", False, None, None)
        (question,) = plan["questions"]
        assert question["choices"] == ["a", "b", "both"] and [option["key"] for option in question["options"]] == ["a", "b"]
        assert {label for option in question["options"] for label in option["profiles"]} == {default.label, swe.label}
        unanswered = client.post("/api/master/migration", json={})
        assert unanswered.status_code == 200 and (unanswered.json()["status"], unanswered.json()["written"]) == ("needs_answers", False)
        for bad, code in (({"answers": {question["question_id"]: "c"}}, "migration_answer_invalid"), ({"answers": {"mq-nope": "a"}}, "migration_answer_unknown")):
            wrong = client.post("/api/master/migration", json=bad)
            assert wrong.status_code == 422 and wrong.json()["error"]["code"] == code
        assert client.get("/api/master").json()["master"] is None  # nothing was written by any of the above
        before = {item["profile_id"]: item for item in client.get("/api/profiles").json()["profiles"]}

        made = client.post("/api/master/migration", json={"answers": {question["question_id"]: "a"}}, headers=browser)
        assert made.status_code == 201, made.text
        made = made.json()
        assert (made["status"], made["written"], made["master"]["revision"], made["master"]["written_by"], made["questions"]) == ("created", True, 1, "operator", [])
        after = {item["profile_id"]: item for item in client.get("/api/profiles").json()["profiles"]}
        assert {key: item["resume_ref"] for key, item in after.items()} == {key: item["resume_ref"] for key, item in before.items()}  # nothing goes stale
        again = client.get("/api/master/migration").json()
        assert again["migration"] is None and again["status"] == "unchanged"  # every profile has its selection

        # --- the master, by id ----------------------------------------------------------------------------
        body = client.get("/api/master").json()
        master = body["master"]
        assert (master["revision"], body["current_revision"], master["revisions"]) == (1, 1, 1)
        assert {profile["profile_id"] for profile in body["profiles"]} == {default.profile_id, swe.profile_id}
        role = next(entry for entry in master["entries"] if entry["section"] == "experience")
        first = role["bullets"][0]
        assert sorted(body["shown_by"][first]) == sorted([default.profile_id, swe.profile_id])
        assert {item["strength"] for item in master["items"]} <= {"backed", "quantified", "stated"}

        # A line is added by the agent (no Origin, no actor: any other loopback caller is the agent).
        added_line = client.post("/api/master/lines", json={"revision": 1, "entry_id": role["id"], "text": "Cut the deploy time of 40 services from 50 to 12 minutes."})
        assert added_line.status_code == 201, added_line.text
        wrote = added_line.json()
        line_id = wrote["id"]
        assert (wrote["status"], wrote["master"]["revision"], wrote["master"]["written_by"], wrote["changes"]) == ("revised", 2, "agent", {"added": 1, "removed": 0, "changed": 0})
        assert {offer["profile_id"]: offer["offer"] for offer in wrote["profiles"]["offers"]} == {
            default.profile_id: "1 new master line: refresh?", swe.profile_id: "1 new master line: refresh?",
        }

        # A stale revision: 409 with the revision the master is at, and nothing written.
        stale = client.put("/api/master/lines", json={"revision": 1, "id": line_id, "text": "Something else."})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"
        assert (stale.json()["error"]["current"]["revision"], stale.json()["error"]["current"]["written_by"]) == (2, "agent")
        # Contact-shaped text: 422, never echoed, nothing written.
        contact = client.put("/api/master/lines", json={"revision": 2, "id": line_id, "text": f"Write to {PLANTED} for the details."})
        assert contact.status_code == 422 and contact.json()["error"]["code"] == "personal_info_refused" and PLANTED not in contact.text
        for bad_body, code in (
            ({"revision": 2, "id": line_id, "text": "x", "colour": "red"}, "unknown_key"),
            ({"id": line_id, "text": "x"}, "invalid_value"),  # no revision
            ({"revision": 2, "text": "x"}, "invalid_value"),  # no id
            ({"revision": 2, "id": line_id, "use": "delete"}, "invalid_value"),
            ({"revision": 2, "id": line_id, "use": "retire", "text": "x"}, "invalid_value"),
            ({"revision": 2, "id": line_id, "text": "x", "actor": "robot"}, "invalid_value"),
        ):
            wrong = client.put("/api/master/lines", json=bad_body)
            assert wrong.status_code == 422 and wrong.json()["error"]["code"] == code, (bad_body, wrong.text)
        assert "allowed_keys" in client.put("/api/master/lines", json={"revision": 2, "id": line_id, "colour": "red"}).json()["error"]
        missing = client.put("/api/master/lines", json={"revision": 2, "id": "b-nope", "text": "x"})
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "master_item_not_found"
        # The rules are the CLI's (one implementation): its refusals come back by their own code, with a status.
        for bad_body, status, code in (
            ({"revision": 2, "id": line_id, "text": "x" * 401}, 422, "master_text_invalid"),
            ({"revision": 2, "id": line_id, "tags": ["two words"]}, 422, "master_tag_invalid"),
            ({"revision": 2, "id": line_id, "tags": "delivery"}, 422, "wrong_type"),
            ({"revision": 2, "id": line_id}, 422, "master_edit_empty"),
            ({"revision": 2, "id": role["id"], "text": "A line."}, 422, "master_edit_invalid"),
            ({"revision": 2, "id": line_id, "use": "restore"}, 409, "master_line_exists"),
        ):
            wrong = client.put("/api/master/lines", json=bad_body)
            assert wrong.status_code == status and wrong.json()["error"]["code"] == code, (bad_body, wrong.text)
        # A line the master already has in other words is asked about: 200, nothing written, the line it looks like named.
        reworded = {"revision": 2, "entry_id": role["id"], "text": "Cut the deploy time of 40 services from 50 minutes to 12 minutes."}
        asked = client.post("/api/master/lines", json=reworded)
        assert asked.status_code == 200, asked.text
        assert (asked.json()["status"], asked.json()["written"], asked.json()["id"], asked.json()["master"]["revision"]) == ("near_duplicate", False, None, 2)
        assert [(near["id"], near["same_numbers"]) for near in asked.json()["near_duplicates"]] == [(line_id, True)]
        assert client.post("/api/master/lines", json={**reworded, "force": "yes"}).json()["error"]["code"] == "wrong_type"
        assert client.get("/api/master").json()["master"]["revision"] == 2
        # Who wrote a line is what `master show --json` says: the agent wrote this one; an imported line names no writer.
        by_id = {item["id"]: item for item in client.get("/api/master").json()["master"]["items"]}
        assert (by_id[line_id]["written_by"], by_id[first]["written_by"], by_id[first]["source"]) == ("agent", None, None)

        # Edit (the user, in the browser), retire, restore: one revision each; who wrote is recorded.
        edited = client.put("/api/master/lines", json={"revision": 2, "id": line_id, "text": "Cut the deploy time of 40 services from 50 to 11 minutes."}, headers=browser).json()
        assert (edited["master"]["revision"], edited["master"]["written_by"], edited["changes"]["changed"]) == (3, "operator", 1)
        by_header = client.put("/api/master/lines", json={"revision": 3, "id": line_id, "use": "retire"}, headers={**browser, "X-GigAI-Actor": "agent"}).json()
        assert (by_header["master"]["revision"], by_header["master"]["written_by"]) == (4, "agent")
        assert line_id not in {item["id"] for item in by_header["master"]["items"]}
        history = client.get("/api/master/history").json()
        assert [(entry["revision"], entry["written_by"]) for entry in history["revisions"]] == [(4, "agent"), (3, "operator"), (2, "agent"), (1, "operator")]
        assert [(gone["id"], gone["what"], gone["last_revision"], gone["retired_in"], gone["text"]) for gone in history["retired"]] == [
            (line_id, "line", 3, 4, "Cut the deploy time of 40 services from 50 to 11 minutes."),
        ]
        restored = client.put("/api/master/lines", json={"revision": 4, "id": line_id, "use": "restore", "actor": "operator"}).json()
        assert restored["master"]["revision"] == 5 and line_id in {item["id"] for item in restored["master"]["items"]}
        assert client.get("/api/master/history").json()["retired"] == []
        # An earlier revision is still readable, as a tailored resume names it.
        old = client.get("/api/master", params={"revision": 4}).json()
        assert old["master"]["revision"] == 4 and old["current_revision"] == 5 and line_id not in {item["id"] for item in old["master"]["items"]}
        assert client.get("/api/master", params={"revision": 99}).status_code == 404

        # Entries.
        entry = client.post("/api/master/entries", json={"revision": 5, "section": "experience", "heading": "Orbital Works", "sublines": ["Staff Engineer | Jun 2025 - Present"]}, headers=browser)
        assert entry.status_code == 201, entry.text
        entry_id = entry.json()["id"]
        renamed = client.put("/api/master/entries", json={"revision": 6, "id": entry_id, "heading": "Orbital Works Ltd"}, headers=browser).json()
        assert next(item for item in renamed["master"]["entries"] if item["id"] == entry_id)["heading"] == "Orbital Works Ltd"
        gone = client.put("/api/master/entries", json={"revision": 7, "id": entry_id, "use": "retire"}, headers=browser).json()
        assert entry_id not in {item["id"] for item in gone["master"]["entries"]}
        back = client.put("/api/master/entries", json={"revision": 8, "id": entry_id, "use": "restore"}, headers=browser).json()
        assert back["master"]["revision"] == 9 and entry_id in {item["id"] for item in back["master"]["entries"]}

        # --- the profiles' selections ------------------------------------------------------------------
        selection = client.get("/api/master/selection").json()
        assert selection["master"]["revision"] == 9 and selection["pending"] == []
        status = {item["profile_id"]: item for item in selection["profiles"]}
        assert status[swe.profile_id]["offer"] == "1 new master line: refresh?" and status[swe.profile_id]["new_lines"] == [line_id]
        assert (status[swe.profile_id]["has_selection"], status[swe.profile_id]["attached"], status[swe.profile_id]["stale"]) == (True, True, False)
        would = client.post("/api/master/selection", json={"profile_id": swe.profile_id, "dry_run": True}).json()
        assert would["dry_run"] is True and would["changes"][0]["written"] is False
        assert client.get("/api/profiles").json()["profiles"] and after[swe.profile_id]["resume_ref"] == {item["profile_id"]: item for item in client.get("/api/profiles").json()["profiles"]}[swe.profile_id]["resume_ref"]
        refreshed = client.post("/api/master/selection", json={"profile_id": swe.profile_id, "use": "refresh"}, headers=browser)
        assert refreshed.status_code == 200, refreshed.text
        change = refreshed.json()["changes"][0]
        assert (change["action"], change["written"], change["pages"], change["fits"]) == ("refreshed", True, 2, True)
        now = {item["profile_id"]: item for item in client.get("/api/profiles").json()["profiles"]}
        assert now[swe.profile_id]["resume_ref"] == change["resume_ref"] != after[swe.profile_id]["resume_ref"]  # its resume is the selection
        assert now[default.profile_id]["resume_ref"] == after[default.profile_id]["resume_ref"]
        assert refreshed.json()["profiles"][0]["offer"] is None  # the offer is taken up
        assert client.post("/api/master/selection", json={"profile_id": swe.profile_id, "use": "sync"}).json()["changes"] == []
        assert client.post("/api/master/selection", json={"profile_id": "profile_nope"}).status_code == 404
        assert client.post("/api/master/selection", json={"profile_id": swe.profile_id, "use": "sync", "dry_run": True}).status_code == 422

        # --- a new profile: the answer comes at once, its own selection after it ------------------------
        created = client.post("/api/profiles", json={"label": "Platform", "titles": ["platform engineer"]}, headers=browser)
        assert created.status_code == 201, created.text
        new = created.json()["profile"]
        assert new["resume_ref"] == now[default.profile_id]["resume_ref"] or new["resume_ref"] == now[swe.profile_id]["resume_ref"]  # the selected profile's, for now
        landed = _wait_not_pending(client, new["profile_id"])
        mine = next(item for item in landed["profiles"] if item["profile_id"] == new["profile_id"])
        assert (mine["has_selection"], mine["attached"], mine["pending"]) == (True, True, False) and mine["source"] in ("index", "titles")
        listed = {item["profile_id"]: item for item in client.get("/api/profiles").json()["profiles"]}
        assert listed[new["profile_id"]]["resume_ref"] != new["resume_ref"]  # its own view now
        assert listed[default.profile_id]["resume_ref"] == now[default.profile_id]["resume_ref"]  # nobody else's resume moved

        # --- one job: Remove a picked line, Add it back ---------------------------------------------------
        posting = _posting()
        install_prompt_model(monkeypatch, copies_what_it_is_shown)
        tailored = run_tailored_resume(
            TailorRequest(job=AssessJobInput(job_text=posting["text"], title=posting["title"], company=posting["company"]), resume=AssessResumeInput(profile_id=default.profile_id)),
            home_root=home, target=target, config=ollama_config(home),
        )
        assert tailored.selection is not None
        key = {"profile_id": default.profile_id, "job_identity": tailored.job.job_identity, "updated_at": tailored.updated_at}
        texts = {item["id"]: item["text"] for item in client.get("/api/master").json()["master"]["items"] if item["kind"] == "bullet"}
        victim = next(line.id for line in tailored.selection.picked if line.id in texts)
        removed = client.put("/api/tailored-resumes/selection", json={**key, "use": "remove", "item_id": victim}, headers=browser)
        assert removed.status_code == 200, removed.text
        removed = removed.json()
        assert removed["selection_change"] == {
            "use": "remove", "item_id": victim, "applied": True, "changed": True, "needs_choice": False,
            "pages": removed["selection_change"]["pages"], "max_pages": 2, "would_cut": [], "cut": [],
        }
        assert texts[victim] not in removed["markdown"] and removed["updated_at"] == tailored.updated_at
        assert {line["id"]: line["code"] for line in removed["selection"]["left_out"]}[victim] == "removed_by_you"
        stored = client.get("/api/tailored-resumes", params={"profile_id": default.profile_id, "job_identity": tailored.job.job_identity}).json()["items"][0]
        assert stored["markdown"] == removed["markdown"]
        put_back = client.put("/api/tailored-resumes/selection", json={**key, "use": "add", "item_id": victim, "fit": "keep"}).json()
        assert put_back["selection_change"]["applied"] is True and texts[victim] in put_back["markdown"]
        assert {line["id"]: line["code"] for line in put_back["selection"]["picked"]}[victim] == "added_by_you"
        for bad_body, status_code, code in (
            ({**key, "use": "remove", "item_id": "b-nope"}, 409, "selection_line_not_shown"),
            ({**key, "use": "add", "item_id": "b-nope"}, 404, "master_line_not_found"),
            ({**key, "updated_at": "2020-01-01T00:00:00Z", "use": "remove", "item_id": victim}, 409, "tailored_resume_changed"),
            ({**key, "use": "swap", "item_id": victim}, 422, "invalid_value"),
            ({**key, "use": "add"}, 422, "invalid_value"),
            ({**key, "job_identity": "https://example.com/none", "use": "add", "item_id": victim}, 404, "tailored_resume_not_found"),
        ):
            wrong = client.put("/api/tailored-resumes/selection", json=bad_body)
            assert (wrong.status_code, wrong.json()["error"]["code"]) == (status_code, code), (bad_body, wrong.text)
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
