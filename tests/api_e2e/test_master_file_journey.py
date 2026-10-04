"""0.1.10.9 master P8: the visible master file over HTTP, against the real supervised server.

One profile holds one synthetic resume in a shape with lines the migration leaves out (a place and a headline above
the first section). Everything goes through HTTP; the "user" is this test writing into the resumes folder.

- ``GET /api/master/migration`` counts every line of the resume (``migration.source_lines``): what is kept, folded
  and left out, the left-out lines by number and reason with a sentence saying why, never their text;
- the migration, and every write after it, puts the master in the resumes folder as ``master.md`` and says so
  (``file`` in the reply); ``GET /api/master`` says how the file stands;
- an edit of the file is NOT read by any route: ``file.not_imported`` says it is there; ``POST /api/master/sync``
  imports it (ids kept, the new line gets one, the writer is named as for every write);
- while the file holds changes that are not imported, a write of the master leaves it alone and goes beside it;
  the import is then refused with 409 ``revision_conflict`` and ``error.current``, and ``revision`` imports it;
- contact data in the file is 422 ``personal_info_refused`` by line number, never echoed, and nothing is imported.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import resolve_workpad_path, setup_and_init, start_server, stop_server, write_offline_find_jobs_config

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "master"
PLANTED = "jane.quillfeather@example.com"
TYPED = "Wrote the paging policy for 3 regions."
AGENT_LINE = "Moved 12 services to Helm charts released through ArgoCD."


def test_the_master_file_over_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    added = CliRunner().invoke(cli, ["scout", "resume", "add", str(FIXTURES / "shapes" / "bold-title.md"), "--home", str(home), "--target", str(target), "--json"])
    assert added.exit_code == 0, added.output
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        browser = {"Origin": server.base_url}
        folder = Path(client.get("/api/resumes-folder").json()["path"])
        file = folder / "master.md"

        # --- no master, no file -------------------------------------------------------------------------
        empty = client.get("/api/master").json()
        assert empty["master"] is None
        assert (empty["file"]["schema_version"], empty["file"]["name"], empty["file"]["state"], empty["file"]["not_imported"], empty["file"]["action"]) == (
            "scout-master-file:1", "master.md", "missing", False, None,
        )
        none = client.post("/api/master/sync", json={}, headers=browser)
        assert none.status_code == 404 and none.json()["error"]["code"] == "master_not_found"

        # --- the preview counts every line of the resume ---------------------------------------------------
        plan = client.get("/api/master/migration").json()
        lines = plan["migration"]["source_lines"]
        assert plan["status"] == "ready" and plan["file"] is None
        assert lines["in"] == lines["kept"] + lines["folded"] + lines["left_out"] and lines["left_out"] == 2
        assert lines["left_out_by_reason"]["above_first_section"] == 2 and sum(lines["left_out_by_reason"].values()) == 2
        (resume,) = lines["resumes"]
        assert resume["in"] == lines["in"] and [(row["reason"], row["lines"]) for row in resume["left_out"]] == [("above_first_section", [1, 3])]
        assert resume["left_out"][0]["why"].startswith("above the first section and not a summary paragraph")
        assert not file.exists() and client.get("/api/master").json()["master"] is None  # a preview writes nothing

        # --- the migration writes the file -----------------------------------------------------------------
        made = client.post("/api/master/migration", json={"answers": {}}, headers=browser)
        assert made.status_code == 201, made.text
        assert made.json()["migration"]["source_lines"] == lines
        assert (made.json()["file"]["wrote"], made.json()["file"]["state"], made.json()["file"]["revision"]) == ("master.md", "current", 1)
        stored = file.read_text(encoding="utf-8")
        assert stored.startswith("<!-- gigai-master:1 -->") and "Jordan" not in stored and "example.test" not in stored
        status = client.get("/api/master")
        assert status.headers["X-GigAI-Labels"] == "user-private"
        shown = status.json()["file"]
        assert (shown["state"], shown["not_imported"], shown["revision"], shown["master_revision"], shown["behind"], shown["beside"], shown["action"]) == (
            "current", False, 1, 1, False, None, None,
        )
        assert shown["folder"] == client.get("/api/resumes-folder").json()["shown"]

        # --- a write of the page: the file follows ---------------------------------------------------------
        master = status.json()["master"]
        role = next(entry for entry in master["entries"] if entry["section"] == "experience")
        line = client.post("/api/master/lines", json={"revision": 1, "entry_id": role["id"], "text": "Cut the deploy time of 40 services from 50 to 12 minutes."}, headers=browser)
        assert line.status_code == 201 and (line.json()["file"]["wrote"], line.json()["file"]["revision"]) == ("master.md", 2)
        assert "Cut the deploy time of 40 services" in file.read_text(encoding="utf-8")

        # --- the user edits the file: said, never read -----------------------------------------------------
        first_bullet = role["bullets"][0]
        old_text = next(item["text"] for item in master["items"] if item["id"] == first_bullet)
        reworded = old_text.rstrip(".") + ", measured over a year."
        mine = file.read_text(encoding="utf-8").replace(old_text, reworded)  # a line reworded, its id comment kept
        assert reworded in mine and "## Other" not in mine
        mine = mine.replace("\n## Skills", f"\n## Other\n\n- {TYPED}\n\n## Skills")  # a line typed with no id, in a section the master did not have
        assert TYPED in mine
        file.write_text(mine, encoding="utf-8")
        seen = client.get("/api/master").json()
        assert (seen["file"]["state"], seen["file"]["not_imported"], seen["file"]["action"], seen["master"]["revision"]) == ("changed", True, "import", 2)
        assert TYPED not in str(seen["master"]) and reworded not in str(seen["master"])
        for path in ("/api/master/history", "/api/master/selection", "/api/master/migration"):
            assert client.get(path).status_code == 200
        assert client.get("/api/master").json()["master"]["revision"] == 2 and file.read_text(encoding="utf-8") == mine

        # --- the import: its body is checked, the writer is named ---------------------------------------
        for bad, code in (({"force": True}, "unknown_key"), ({"revision": "two"}, "invalid_value"), ({"actor": 7}, "wrong_type")):
            wrong = client.post("/api/master/sync", json=bad, headers=browser)
            assert wrong.status_code == 422 and wrong.json()["error"]["code"] == code, wrong.text
        assert client.get("/api/master").json()["master"]["revision"] == 2
        imported = client.post("/api/master/sync", json={}, headers={**browser, "X-GigAI-Actor": "agent"})
        assert imported.status_code == 200, imported.text
        assert imported.headers["X-GigAI-Labels"] == "user-private"
        body = imported.json()
        assert (body["schema_version"], body["action"], body["status"], body["written"]) == ("scout-master:1", "sync", "imported", True)
        assert body["changes"] == {"added": 1, "removed": 0, "changed": 1} and body["retired"] == []
        assert [(row["text"], row["what"], row["section"]) for row in body["added"]] == [(TYPED, "line", "other")]
        assert [(row["id"], row["text"]) for row in body["changed"]] == [(first_bullet, reworded)]
        assert body["ids"]["assigned"] == 1 and body["ids"]["kept"] == master["counts"]["ids"] + 1  # every id the master had, and the line the page added
        assert (body["master"]["revision"], body["master"]["written_by"]) == (3, "agent")
        assert (body["file"]["state"], body["file"]["wrote"], body["file"]["revision"], body["file"]["written"]) == ("current", "master.md", 3, True)
        typed_id = body["added"][0]["id"]
        assert f"- {TYPED} <!-- id:{typed_id} -->" in file.read_text(encoding="utf-8")
        assert set(body["profiles"]) == {"synced", "offers"}
        again = client.post("/api/master/sync", json={}, headers=browser).json()
        assert (again["status"], again["written"], again["master"]["revision"]) == ("unchanged", False, 3)

        # --- the master moves on while the file has changes: beside it, then 409, then told to ---------
        mine = file.read_text(encoding="utf-8").replace(reworded, old_text)
        file.write_text(mine, encoding="utf-8")
        other = client.post("/api/master/lines", json={"revision": 3, "entry_id": role["id"], "text": AGENT_LINE, "actor": "agent"})
        assert other.status_code == 201, other.text
        assert (other.json()["file"]["wrote"], other.json()["file"]["not_imported"], other.json()["file"]["beside"]) == ("master-2.md", True, "master-2.md")
        assert file.read_text(encoding="utf-8") == mine and AGENT_LINE in (folder / "master-2.md").read_text(encoding="utf-8")
        behind = client.get("/api/master").json()["file"]
        assert (behind["not_imported"], behind["behind"], behind["revision"], behind["master_revision"], behind["beside"]) == (True, True, 3, 4, "master-2.md")
        conflict = client.post("/api/master/sync", json={}, headers=browser)
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "revision_conflict"
        assert conflict.json()["error"]["current"]["revision"] == 4 and client.get("/api/master").json()["master"]["revision"] == 4
        stale = client.post("/api/master/sync", json={"revision": 3}, headers=browser)
        assert stale.status_code == 409 and stale.json()["error"]["current"]["revision"] == 4
        anyway = client.post("/api/master/sync", json={"revision": 4}, headers=browser)
        assert anyway.status_code == 200, anyway.text
        assert (anyway.json()["status"], anyway.json()["master"]["revision"], anyway.json()["master"]["written_by"]) == ("imported", 5, "operator")
        assert [row["text"] for row in anyway.json()["retired"]] == [AGENT_LINE]  # named, and History can put it back
        assert sorted(path.name for path in folder.glob("master*.md")) == ["master.md"]
        retired = client.get("/api/master/history").json()["retired"]
        assert [gone["text"] for gone in retired] == [AGENT_LINE]

        # --- contact data in the file: refused by line number, nothing imported, never echoed -------------
        good = file.read_text(encoding="utf-8")
        file.write_text(good.rstrip("\n") + f"\n- Write to {PLANTED} for references.\n", encoding="utf-8")
        refused = client.post("/api/master/sync", json={}, headers=browser)
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "personal_info_refused"
        assert PLANTED not in refused.text and "line " in refused.json()["error"]["message"] and "email" in refused.json()["error"]["message"]
        assert client.get("/api/master").json()["master"]["revision"] == 5 and PLANTED in file.read_text(encoding="utf-8")
        file.write_text(good, encoding="utf-8")
        assert client.get("/api/master").json()["file"]["not_imported"] is False
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
