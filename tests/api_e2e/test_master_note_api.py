"""0.1.11 N1b: a line's note over HTTP, against the real supervised server (SPEC 5.4).

A synthetic master (an invented person, no contact data) is stored with ``master init --from``; everything after
that goes through HTTP, no model call:

- ``GET /api/master`` returns ``note`` with every line and entry (null when there is none);
- ``PUT /api/master/lines`` and ``PUT /api/master/entries`` accept ``note``: a string sets it, ``""`` removes it;
  each is one revision whose writer is named, the line's ``mark`` stays, and ``master.md`` says what is stored;
- a note that looks like contact data is 422 ``personal_info_refused`` and never echoed; one a 0.1.10 reader would
  misread, or one that is too long, is 422 ``master_note_invalid``; a note that is not a string is ``wrong_type``;
  ``use: retire`` with a note is ``invalid_value``; nothing is written by any of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

PLANTED = "jane.quillfeather@example.com"
LEAD = "agentic roles: lead with this"
SHORTER = "shorter version: keep the first two bullets"
RUNTIME = "Built the durable runtime that resumes an agent run after a crash."
MASTER = f"""<!-- gigai-master:1 -->

## Summary

- Platform engineer with 9 years of experience in payment systems. <!-- id:sum-platform -->

## Experience

### Edge Lab <!-- id:r-edge -->
Founder | Jan 2024 - Present
- {RUNTIME} <!-- id:b-runtime tags:ai -->
- Ran the on-call rotation for 4 teams. <!-- id:b-oncall -->

## Skills

- Cloud: Kubernetes, Docker <!-- id:s-cloud -->
"""


def test_a_note_is_set_read_and_removed_over_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    source = tmp_path / "master-in.md"
    source.write_text(MASTER, encoding="utf-8")
    made = CliRunner().invoke(cli, ["scout", "resume", "master", "init", "--from", str(source), "--home", str(home), "--target", str(target), "--json"])
    assert made.exit_code == 0, made.output
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        browser = {"Origin": server.base_url}  # what a page of this server sends on every write
        file = Path(client.get("/api/resumes-folder").json()["path"]) / "master.md"

        def master() -> dict:
            return client.get("/api/master").json()["master"]

        def line(item_id: str) -> dict:
            return next(item for item in master()["items"] if item["id"] == item_id)

        def entry(entry_id: str) -> dict:
            return next(item for item in master()["entries"] if item["id"] == entry_id)

        first = master()
        assert first["revision"] == 1 and all(item["note"] is None for item in (*first["items"], *first["entries"]))
        mark = line("b-runtime")["mark"]

        # --- a note on a line: one revision by the agent (no Origin, no actor), the line itself as it was -----
        noted = client.put("/api/master/lines", json={"revision": 1, "id": "b-runtime", "note": f"  {LEAD}  "})
        assert noted.status_code == 200, noted.text
        assert noted.headers["X-GigAI-Labels"] == "user-private"
        body = noted.json()
        assert (body["action"], body["status"], body["written"], body["id"], body["changes"]) == ("edit", "revised", True, "b-runtime", {"added": 0, "removed": 0, "changed": 1})
        assert (body["master"]["revision"], body["master"]["written_by"]) == (2, "agent")
        assert body["master"]["content_sha256"] != first["content_sha256"]  # part of the master's content
        assert body["profiles"] == {"synced": [], "offers": []}
        shown = line("b-runtime")
        assert (shown["note"], shown["text"], shown["tags"], shown["mark"]) == (LEAD, RUNTIME, ["ai"], mark)
        assert f"- {RUNTIME} <!-- id:b-runtime tags:ai --> <!-- note: {LEAD} -->" in file.read_text(encoding="utf-8").splitlines()

        # --- a note on an entry, by the user in the browser; with a new sub-line in the same write ----------------
        role = client.put(
            "/api/master/entries", json={"revision": 2, "id": "r-edge", "note": SHORTER, "sublines": ["Founder and engineer | Jan 2024 - Present"]}, headers=browser,
        )
        assert role.status_code == 200, role.text
        assert (role.json()["master"]["revision"], role.json()["master"]["written_by"]) == (3, "operator")
        assert (entry("r-edge")["note"], entry("r-edge")["sublines"], line("b-runtime")["note"]) == (SHORTER, ["Founder and engineer | Jan 2024 - Present"], LEAD)
        assert f"### Edge Lab <!-- id:r-edge --> <!-- note: {SHORTER} -->" in file.read_text(encoding="utf-8").splitlines()

        # --- refused: nothing written, the note never echoed ---------------------------------------------------------
        for path, item_id in (("/api/master/lines", "b-oncall"), ("/api/master/entries", "r-edge")):
            for bad, status, code in (
                ({"note": f"ask {PLANTED} about this"}, 422, "personal_info_refused"),
                ({"note": "zebra-quartz use with id:b-oncall"}, 422, "master_note_invalid"),
                ({"note": "zebra-quartz tags:ai"}, 422, "master_note_invalid"),
                ({"note": "zebra-quartz --> more"}, 422, "master_note_invalid"),
                ({"note": "zebra-quartz " + "x" * 300}, 422, "master_note_invalid"),
                ({"note": 7}, 422, "wrong_type"),
                ({"note": ["zebra-quartz"]}, 422, "wrong_type"),
                ({"note": "zebra-quartz", "use": "retire"}, 422, "invalid_value"),
                ({"note": "", "use": "restore"}, 422, "invalid_value"),
                ({"note": "zebra-quartz", "notes": "x"}, 422, "unknown_key"),
            ):
                wrong = client.put(path, json={"revision": 3, "id": item_id, **bad}, headers=browser)
                assert wrong.status_code == status and wrong.json()["error"]["code"] == code, (path, bad, wrong.text)
                assert PLANTED not in wrong.text and "zebra-quartz" not in wrong.json()["error"]["message"]
        stale = client.put("/api/master/lines", json={"revision": 2, "id": "b-oncall", "note": "ops roles"})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict" and stale.json()["error"]["current"]["revision"] == 3
        assert master()["revision"] == 3 and line("b-oncall")["note"] is None and PLANTED not in file.read_text(encoding="utf-8")

        # --- "" removes a note; the same again writes nothing ----------------------------------------------------------
        cleared = client.put("/api/master/lines", json={"revision": 3, "id": "b-runtime", "note": ""}, headers=browser).json()
        assert (cleared["status"], cleared["master"]["revision"], cleared["changes"]["changed"]) == ("revised", 4, 1)
        assert line("b-runtime")["note"] is None and line("b-runtime")["mark"] == mark and entry("r-edge")["note"] == SHORTER
        again = client.put("/api/master/lines", json={"revision": 4, "id": "b-runtime", "note": "   "}, headers=browser).json()
        assert (again["status"], again["written"], again["master"]["revision"]) == ("unchanged", False, 4)
        gone = client.put("/api/master/entries", json={"revision": 4, "id": "r-edge", "note": ""}, headers=browser).json()
        assert (gone["master"]["revision"], entry("r-edge")["note"]) == (5, None)
        assert "note:" not in file.read_text(encoding="utf-8")

        history = client.get("/api/master/history").json()
        assert [(item["revision"], item["written_by"], item["changed"]) for item in history["revisions"]] == [
            (5, "operator", 1), (4, "operator", 1), (3, "operator", 1), (2, "agent", 1), (1, "operator", 0),
        ]
        assert history["retired"] == []
    finally:
        stop_server(server)
    assert_clean_and_healthy(workpad, home)
