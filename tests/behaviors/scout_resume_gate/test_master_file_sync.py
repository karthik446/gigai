"""0.1.10.9 master P8: the visible master file (``master.md`` in the resumes folder) and its explicit import.

The END outcomes, through the real CLI on a scratch home:

* every change of the master (a file import, ``add`` / ``edit`` / ``remove`` / ``--restore``, the migration, the
  functions the Master page's routes call) leaves ``master.md`` in the resumes folder saying exactly what is stored;
* a file the user changed is NEVER replaced: a later change of the master goes beside it (``master-2.md``) and the
  command says so; ``scout status`` and ``master show`` say "changes not imported yet"; nothing reads the file by
  itself (the stored master never holds the user's edit until ``master sync``);
* ``gigai scout resume master sync`` imports the file as the next revision: a new line gets an id, every other id is
  kept (also one whose id comment was deleted), a removed line is retired and can be restored, the output names what
  changed, the file is written again with every id, and a profile that shows an edited line follows;
* refused with nothing imported and the file left as it is: contact data (by line number and kind, never the value),
  a file that does not read as a master, a master that moved on since the file was written (``revision_conflict``;
  ``--revision N`` imports it anyway and retires what was added since), and a ``master.md`` GigAI never wrote
  (``revision_required``).

The resumes folder of a scratch home is ``<home>/resumes`` (only ``~/.gigai`` uses ``~/Documents``). Everything is
synthetic (the invented master of ``test_master_edit.py``; example.test / 555-01xx values). No model is called.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import master_edit, master_file, profile_records, resumes_folder
from gigai.scout.find_jobs.resume_input import resume_for_profile  # the reader every assessment and tailoring uses
from gigai.scout.master_resume import parse_master
from gigai.scout.master_store import MasterStoreError, import_master, load_master
from gigai.scout.resume_pii import detect_contact_details
from gigai.scout.target_resolution import home_scout_target

from tests.behaviors.scout_resume_gate.test_master_edit import DEPLOY, GUIDE, HELM, SMALL, _head, _master, _plain, _scout, _setup, _workpad
from tests.behaviors.scout_resume_gate.test_master_tailor_outcomes import _Home

EMAIL = "jordan.testwell@example.test"
PHONE = "(555) 010-0142"
PLANTED = (EMAIL, "jordan.testwell", PHONE, "010-0142", "Jordan Testwell")
ONCALL = "- Ran the on-call rotation for 4 teams. <!-- id:b-oncall -->"
TYPED = "Wrote the paging policy for 3 regions."


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    """A scratch home whose master is ``SMALL`` at revision 1."""

    made = _setup(tmp_path)
    source = tmp_path / "master-in.md"
    source.write_text(SMALL, encoding="utf-8")
    assert _master(made, "init", "--from", str(source))["status"] == "created"
    return made


def _folder(home: Path) -> Path:
    return home / "resumes"


def _file(home: Path) -> Path:
    return _folder(home) / "master.md"


def _names(home: Path) -> list[str]:
    return sorted(path.name for path in _folder(home).iterdir())


def _stored(home: Path) -> str:
    """The stored master's markdown, read from the journal (never from the folder)."""

    stored = load_master(home_root=home, target=home_scout_target(home))
    assert stored is not None
    return stored.master.markdown()


def _texts(home: Path) -> dict[str, str]:
    return {item["id"]: item["text"] for item in _master(home, "show")["master"]["items"]}


def _hidden(home: Path) -> bytes:
    """Everything GigAI stores that is not the user's folder: the home without ``resumes/``, the workpads, every journal object."""

    data = b""
    for root in (home, home.parent / "workpads"):
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink() and ".git" not in path.parts and _folder(home) not in path.parents:
                data += path.read_bytes()
    return data + subprocess.run(["git", "-C", str(_workpad(home)), "cat-file", "--batch-all-objects", "--batch"], capture_output=True, check=True).stdout


def test_every_change_of_the_master_leaves_master_md_saying_what_is_stored(home: Path, tmp_path: Path) -> None:
    file = _file(home)
    # The first import wrote it: the stored markdown, byte for byte, ids and all; a master the product reads back.
    assert _names(home) == ["master.md"] and file.read_text(encoding="utf-8") == _stored(home) == SMALL
    assert len(parse_master(file.read_text(encoding="utf-8")).items) == 7
    status = _scout(home, "status")["master_file"]
    assert status == {"name": "master.md", "path": str(file), "state": "current", "not_imported": False, "revision": 1, "beside": None}
    shown = _master(home, "show")["file"]
    assert (shown["state"], shown["not_imported"], shown["revision"], shown["master_revision"], shown["behind"], shown["action"]) == ("current", False, 1, 1, False, None)
    assert "master.md" not in _plain(home, "show")  # nothing to say while the file says what is stored

    # add, edit, remove, restore (the CLI), and the functions the page's routes call: each is one revision and one file write.
    added = _master(home, "add", "--entry", "r-north", "--text", HELM, "--as", "agent")
    assert added["file"] == {"name": "master.md", "path": str(file), "state": "current", "not_imported": False, "revision": 2, "beside": None, "written": True, "wrote": "master.md"}
    assert file.read_text(encoding="utf-8") == _stored(home) and HELM in file.read_text(encoding="utf-8")
    new_id = added["items"][0]["id"]
    edited = _master(home, "edit", new_id, "--text", HELM.replace("12", "14"), "--revision", "2")
    assert edited["file"]["revision"] == 3 and file.read_text(encoding="utf-8") == _stored(home)
    removed = _master(home, "remove", new_id, "--revision", "3")
    assert removed["file"]["revision"] == 4 and "Helm" not in file.read_text(encoding="utf-8") and file.read_text(encoding="utf-8") == _stored(home)
    restored = _master(home, "add", "--restore", new_id)
    assert restored["file"]["revision"] == 5 and "14 services to Helm" in file.read_text(encoding="utf-8")
    through_route = master_edit.add_line(home_root=home, target=home_scout_target(home), section="other", text="Certification: Certified Kubernetes Administrator (2020)", revision=5)
    assert through_route.to_json()["file"]["wrote"] == "master.md" and file.read_text(encoding="utf-8") == _stored(home)
    # A write that changes nothing writes no revision and no file.
    before = file.stat().st_mtime_ns
    same = _master(home, "edit", "b-deploy", "--text", DEPLOY, "--revision", "6")
    assert (same["status"], same["file"]) == ("unchanged", None) and file.stat().st_mtime_ns == before
    # A whole file imported again (`init --from`) is a change like any other.
    source = tmp_path / "again.md"
    source.write_text(SMALL, encoding="utf-8")
    again = _master(home, "init", "--from", str(source), "--revision", "6")
    assert again["file"]["revision"] == 7 and file.read_text(encoding="utf-8") == SMALL
    assert _names(home) == ["master.md"]
    index = json.loads((home / "scout" / "resumes-folder-index.json").read_text(encoding="utf-8"))
    assert set(index["files"]) == {"master.md"} and index["files"]["master.md"]["key"] == "master" and index["files"]["master.md"]["revision"] == 7
    assert "Example Corp" not in json.dumps(index)  # names, digests and revision numbers: no line of the master


def test_the_migration_writes_the_file_too(tmp_path: Path) -> None:
    made = _Home(tmp_path)
    done = made.migrate()
    file = _file(made.home)
    assert done["file"]["wrote"] == "master.md" and done["file"]["revision"] == 1
    assert file.read_text(encoding="utf-8") == _stored(made.home)
    # The folder is GigAI's visible place: no contact data, and no file named after a person.
    assert detect_contact_details(file.read_text(encoding="utf-8")) == []


def test_a_file_the_user_changed_is_never_replaced_and_is_shown_as_not_imported(home: Path) -> None:
    file = _file(home)
    mine = file.read_text(encoding="utf-8").replace(ONCALL, ONCALL.replace("4 teams", "5 teams") + f"\n- {TYPED}")
    file.write_text(mine, encoding="utf-8")

    # Said everywhere, read nowhere: the stored master does not hold the edit.
    status = _scout(home, "status")["master_file"]
    assert (status["state"], status["not_imported"], status["revision"]) == ("changed", True, 1)
    plain = CliRunner().invoke(cli, ["scout", "status", "--home", str(home)])
    assert "master.md has changes not imported yet" in plain.output and "gigai scout resume master sync" in plain.output
    shown = _master(home, "show")
    assert (shown["file"]["not_imported"], shown["file"]["action"], shown["file"]["behind"]) == (True, "import", False)
    said = _plain(home, "show")
    assert "master.md has changes not imported yet" in said and "master sync" in said
    for command in (("show",), ("history",), ("show", "--retired"), ("selection", "status")):
        _master(home, *command)
    assert TYPED not in " ".join(_texts(home).values()) and _texts(home)["b-oncall"] == "Ran the on-call rotation for 4 teams."
    assert _master(home, "show")["master"]["revision"] == 1 and file.read_text(encoding="utf-8") == mine

    # A change of the master made elsewhere leaves the user's file alone: the revision goes beside it, and the command says so.
    added = _master(home, "add", "--entry", "r-north", "--text", HELM, "--as", "agent")
    assert added["file"]["state"] == "changed" and added["file"]["not_imported"] is True
    assert (added["file"]["wrote"], added["file"]["beside"], added["file"]["revision"]) == ("master-2.md", "master-2.md", 1)
    assert file.read_text(encoding="utf-8") == mine, "the user's file was replaced"
    assert _names(home) == ["master-2.md", "master.md"] and (_folder(home) / "master-2.md").read_text(encoding="utf-8") == _stored(home)
    printed = CliRunner().invoke(cli, ["scout", "resume", "master", "edit", "b-billing", "--text", "Built the billing export used by 32 finance analysts.", "--revision", "2", "--home", str(home)])
    assert printed.exit_code == 0 and "has changes not imported yet, so it was left as it is: this revision is in master-2.md" in printed.output
    # The next revision replaces GigAI's own beside file; it does not pile up.
    assert _names(home) == ["master-2.md", "master.md"] and (_folder(home) / "master-2.md").read_text(encoding="utf-8") == _stored(home)
    assert file.read_text(encoding="utf-8") == mine
    shown = _master(home, "show")["file"]
    assert (shown["revision"], shown["master_revision"], shown["behind"], shown["beside"], shown["action"]) == (1, 3, True, "master-2.md", "import")
    assert "The master changed since that file was written (revision 1, now 3; revision 3 is in master-2.md)" in _plain(home, "show")
    # A beside file the user changed is theirs too: the next revision takes the next free name.
    beside = _folder(home) / "master-2.md"
    kept = beside.read_text(encoding="utf-8") + "\nmy own note\n"
    beside.write_text(kept, encoding="utf-8")
    _master(home, "edit", "b-billing", "--text", "Built the billing export used by 33 finance analysts.", "--revision", "3")
    assert _names(home) == ["master-2.md", "master-3.md", "master.md"] and beside.read_text(encoding="utf-8") == kept
    assert (_folder(home) / "master-3.md").read_text(encoding="utf-8") == _stored(home) and file.read_text(encoding="utf-8") == mine


def test_sync_imports_the_file_keeps_every_id_and_says_what_changed(home: Path) -> None:
    file = _file(home)
    before = _master(home, "show")["master"]
    text = file.read_text(encoding="utf-8")
    edited = (
        text.replace(ONCALL, ONCALL.replace("4 teams", "5 teams") + f"\n- {TYPED}")  # a line reworded (its id comment kept), a line typed with no id
        .replace(f"- {DEPLOY} <!-- id:b-deploy tags:delivery -->", f"- {DEPLOY}")  # an id comment deleted, the text left
        .replace("- Built the billing export used by 30 finance analysts. <!-- id:b-billing -->\n", "")  # a line removed
    )
    assert edited.count("\n") == text.count("\n") and TYPED in edited and "b-deploy" not in edited and "b-billing" not in edited
    file.write_text(edited, encoding="utf-8")
    head = _head(home)

    done = _master(home, "sync", "--as", "agent")

    assert (done["action"], done["status"], done["written"]) == ("sync", "imported", True)
    assert (done["master"]["revision"], done["master"]["written_by"], done["master"]["parent_revision"]) == (2, "agent", before["revision_id"])
    assert done["changes"] == {"added": 1, "removed": 1, "changed": 2}  # reworded, and the line whose comment (and tag) went
    new_id = done["added"][0]["id"]
    assert done["added"] == [{"id": new_id, "what": "line", "section": "experience", "entry_id": "r-example", "text": TYPED}] and new_id.startswith("b-")
    assert {line["id"]: line["text"] for line in done["changed"]} == {"b-oncall": "Ran the on-call rotation for 5 teams.", "b-deploy": DEPLOY}
    assert done["retired"] == [{"id": "b-billing", "what": "line", "section": "experience", "entry_id": "r-north", "text": "Built the billing export used by 30 finance analysts."}]
    assert done["ids"] == {"kept": 8, "assigned": 1, "restored": 1}  # 9 ids before, 1 retired; the deleted comment's id came back
    assert done["profiles"] == {"synced": [], "offers": []}
    assert _head(home) != head

    # Read back: every line that was there has the id it had; only what the user changed differs.
    after = {item["id"]: item for item in _master(home, "show")["master"]["items"]}
    was = {item["id"]: item for item in before["items"]}
    assert set(after) == (set(was) - {"b-billing"}) | {new_id}
    assert after["b-oncall"]["text"] == "Ran the on-call rotation for 5 teams." and after["b-deploy"]["text"] == DEPLOY and after[new_id]["text"] == TYPED
    untouched = set(was) - {"b-billing", "b-oncall", "b-deploy"}
    assert all((after[key]["text"], after[key]["mark"], after[key]["tags"], after[key]["backed"]) == (was[key]["text"], was[key]["mark"], was[key]["tags"], was[key]["backed"]) for key in untouched)
    assert after["b-guide"]["backed"] == ["story:incident-guide"] and GUIDE == after["b-guide"]["text"]
    # The file is written again with every id (the user asked for the import), and now says what is stored.
    assert done["file"] == {"name": "master.md", "path": str(file), "state": "current", "not_imported": False, "revision": 2, "beside": None, "written": True, "wrote": "master.md"}
    assert file.read_text(encoding="utf-8") == _stored(home) and f"- {TYPED} <!-- id:{new_id} -->" in file.read_text(encoding="utf-8")
    assert f"- {DEPLOY} <!-- id:b-deploy -->" in file.read_text(encoding="utf-8") and _names(home) == ["master.md"]
    assert _scout(home, "status")["master_file"]["not_imported"] is False
    # The removed line is retired, never lost: listed, and put back under its own id.
    assert [gone["id"] for gone in _master(home, "show", "--retired")["retired"]] == ["b-billing"]
    assert _master(home, "add", "--restore", "b-billing")["items"][0]["text"] == "Built the billing export used by 30 finance analysts."

    # Nothing to import: no revision, no write.
    head = _head(home)
    again = _master(home, "sync")
    assert (again["status"], again["written"], again["master"]["revision"], again["changes"]) == ("unchanged", False, 3, {"added": 0, "removed": 0, "changed": 0})
    assert _head(home) == head and "Nothing to import: master.md says what the master holds (revision 3)." in _plain(home, "sync")

    # The plain output of an import names the lines by id.
    file.write_text(file.read_text(encoding="utf-8").replace("5 teams", "6 teams"), encoding="utf-8")
    said = _plain(home, "sync")
    assert "Imported master.md as revision 4 of the master: 0 added, 1 changed, 0 retired; 10 ids kept, 0 new ids, 0 restored." in said
    assert "Changed b-oncall: Ran the on-call rotation for 6 teams." in said

    # A file that only looks different (spacing, a blank line) is no revision; it is written again in the stored form.
    file.write_text(file.read_text(encoding="utf-8").replace("## Skills\n", "## Skills\n\n\n"), encoding="utf-8")
    assert _scout(home, "status")["master_file"]["not_imported"] is True
    spaced = _master(home, "sync")
    assert (spaced["status"], spaced["master"]["revision"], spaced["file"]["written"], spaced["file"]["state"]) == ("unchanged", 4, True, "current")
    assert file.read_text(encoding="utf-8") == _stored(home)

    # A missing file is written, nothing is imported.
    file.unlink()
    assert _scout(home, "status")["master_file"]["state"] == "missing" and _master(home, "show")["file"]["action"] == "write"
    wrote = _master(home, "sync")
    assert (wrote["status"], wrote["written"], wrote["file"]["wrote"], wrote["master"]["revision"]) == ("written", False, "master.md", 4)
    assert file.read_text(encoding="utf-8") == _stored(home)


def test_a_profile_that_shows_an_edited_line_follows_the_import(tmp_path: Path) -> None:
    made = _Home(tmp_path)
    made.migrate()

    def profile() -> profile_records.ProfileRecord:
        return next(item for item in profile_records.list_profiles(made.resolved) if item.profile_id == made.ai_id)

    def resume_text() -> str:
        return resume_for_profile(profile(), resolved=made.resolved, home_root=made.home, target=made.scout).text

    before = profile()
    master = _master(made.home, "show")["master"]
    line = next(item for item in master["items"] if item["kind"] == "bullet" and item["id"] in before.master_selection.item_ids)
    assert line["text"] in resume_text()
    reworded = line["text"].rstrip(".") + ", measured over a year."
    file = _file(made.home)
    file.write_text(file.read_text(encoding="utf-8").replace(line["text"], reworded), encoding="utf-8")

    done = _master(made.home, "sync")

    assert done["status"] == "imported" and [changed["id"] for changed in done["changed"]] == [line["id"]]
    # sync ends with after_master_write: the profile that shows the line has its resume printed again, with no refresh.
    assert made.ai_id in [change["profile_id"] for change in done["profiles"]["synced"]]
    assert profile().resume_ref != before.resume_ref and reworded in resume_text() and line["text"] not in resume_text()


def test_sync_refuses_contact_data_and_a_file_that_does_not_read_and_changes_nothing(home: Path) -> None:
    file = _file(home)
    text = file.read_text(encoding="utf-8")
    head = _head(home)

    def refused(changed: str, code: str) -> dict:
        file.write_text(changed, encoding="utf-8")
        result = _master(home, "sync", ok=False)
        assert result["error"]["code"] == code, result
        assert _head(home) == head and file.read_text(encoding="utf-8") == changed, "a refused import wrote something"
        assert _master(home, "show")["master"]["revision"] == 1 and _names(home) == ["master.md"]
        return result["error"]

    # Contact data is refused whole, by line number and kind; the value is in the user's file and nowhere else.
    with_contact = text.replace("## Summary", f"# Jordan Testwell\n{EMAIL} | {PHONE}\n\n## Summary").replace(ONCALL, ONCALL + f"\n- Reach me at {EMAIL} for the runbooks.")
    error = refused(with_contact, "personal_info_refused")
    assert "line 3" in error["message"] and "email" in error["message"] and "Nothing was imported" in error["message"]
    assert not any(value in json.dumps(error) for value in PLANTED)
    plain = CliRunner().invoke(cli, ["scout", "resume", "master", "sync", "--home", str(home)])
    assert plain.exit_code == 1 and not any(value in plain.output for value in PLANTED)
    hidden = _hidden(home)
    assert not any(value.encode() in hidden for value in PLANTED), "a contact value from the file reached GigAI's store"
    # The same through the function: it is the import's own refusal, not the CLI's.
    with pytest.raises(MasterStoreError) as contact:
        master_file.sync(home_root=home, target=home_scout_target(home))
    assert contact.value.code == "master_contact_data"

    # A file that does not read as a master: the line is named, its text is not.
    broken = refused(text.replace("## Skills", "## Hobbies and secret plans"), "master_markdown_invalid")
    assert "line " in broken["message"] and "secret plans" not in broken["message"]
    # A link in place of the file is not read through.
    file.unlink()
    elsewhere = home.parent / "elsewhere.md"
    elsewhere.write_text(text.replace("4 teams", "9 teams"), encoding="utf-8")
    file.symlink_to(elsewhere)
    assert _master(home, "sync", ok=False)["error"]["code"] == "master_file_unreadable"
    assert _head(home) == head and file.is_symlink() and _texts(home)["b-oncall"] == "Ran the on-call rotation for 4 teams."
    # ...and a write of the master never writes through it either.
    _master(home, "add", "--entry", "r-north", "--text", HELM)
    assert file.is_symlink() and elsewhere.read_text(encoding="utf-8") == text.replace("4 teams", "9 teams") and "master-2.md" in _names(home)
    file.unlink()

    # With the file as GigAI writes it again, everything is in order.
    assert _master(home, "sync")["status"] == "written" and file.read_text(encoding="utf-8") == _stored(home)


def test_sync_does_not_overwrite_a_master_that_moved_on_unless_told_to(home: Path, tmp_path: Path) -> None:
    file = _file(home)
    mine = file.read_text(encoding="utf-8").replace(ONCALL, ONCALL.replace("4 teams", "5 teams"))
    file.write_text(mine, encoding="utf-8")
    added = _master(home, "add", "--entry", "r-north", "--text", HELM, "--as", "agent")  # the agent writes meanwhile: revision 2
    helm_id = added["items"][0]["id"]
    head = _head(home)

    conflict = _master(home, "sync", ok=False)["error"]
    assert conflict["code"] == "revision_conflict" and conflict["current"]["revision"] == 2 and conflict["current"]["written_by"] == "agent"
    assert "written from revision 1" in conflict["message"] and "master-2.md" in conflict["message"] and "--revision 2" in conflict["message"]
    assert _master(home, "sync", "--revision", "1", ok=False)["error"]["code"] == "revision_conflict"  # a stale revision is no answer
    assert _head(home) == head and file.read_text(encoding="utf-8") == mine and _texts(home)[helm_id] == HELM

    # Told to: the file is imported as it is; what was added since is retired (named, restorable), not lost.
    done = _master(home, "sync", "--revision", "2")
    assert (done["status"], done["master"]["revision"]) == ("imported", 3)
    assert [gone["id"] for gone in done["retired"]] == [helm_id] and [changed["id"] for changed in done["changed"]] == ["b-oncall"]
    assert file.read_text(encoding="utf-8") == _stored(home) and _names(home) == ["master.md"], "GigAI's beside file stays after master.md was imported"
    assert _master(home, "add", "--restore", helm_id)["items"][0]["text"] == HELM

    # A master.md GigAI never wrote (another folder that already holds one): never replaced, and its revision is not known.
    other = tmp_path / "my-resumes"
    other.mkdir()
    theirs = SMALL.replace("4 teams", "7 teams")
    (other / "master.md").write_text(theirs, encoding="utf-8")
    assert CliRunner().invoke(cli, ["scout", "resume", "folder", "--set", str(other), "--home", str(home), "--json"]).exit_code == 0
    status = _master(home, "show")["file"]
    assert (status["state"], status["not_imported"], status["revision"], status["action"]) == ("changed", True, None, "import")
    edited = _master(home, "edit", "b-deploy", "--text", "Cut deploy time from 40 minutes to 5 with a staged pipeline.", "--revision", "4")
    assert edited["file"]["wrote"] == "master-2.md" and (other / "master.md").read_text(encoding="utf-8") == theirs
    required = _master(home, "sync", ok=False)["error"]
    assert required["code"] == "revision_required" and required["current"]["revision"] == 5 and "--revision 5" in required["message"]
    taken = _master(home, "sync", "--revision", "5")
    assert taken["status"] == "imported" and _texts(home)["b-oncall"] == "Ran the on-call rotation for 7 teams."
    assert (other / "master.md").read_text(encoding="utf-8") == _stored(home) and sorted(path.name for path in other.iterdir()) == ["master.md"]


def test_no_master_and_the_guards_of_the_import(tmp_path: Path, home: Path) -> None:
    fresh = _setup(tmp_path / "fresh")
    assert CliRunner().invoke(cli, ["scout", "install", "--home", str(fresh), "--json"]).exit_code == 0
    none = CliRunner().invoke(cli, ["scout", "resume", "master", "sync", "--home", str(fresh), "--json"])
    assert none.exit_code == 1 and json.loads(none.output.strip().splitlines()[-1])["error"]["code"] == "master_not_found"
    assert not (fresh / "resumes" / "master.md").exists()
    assert master_file.file_status(fresh, None)["action"] is None

    # The file saved again between the read of its state and the import: nothing is imported from the newer bytes.
    file = _file(home)
    file.write_text(file.read_text(encoding="utf-8").replace("4 teams", "5 teams"), encoding="utf-8")
    seen = resumes_folder.master_file(home)
    file.write_text(file.read_text(encoding="utf-8").replace("5 teams", "6 teams"), encoding="utf-8")
    with pytest.raises(MasterStoreError) as moved:
        import_master(home_root=home, target=home_scout_target(home), source=file, revision=1, refuse_contact=True, visible_sha256=seen.sha256)
    assert moved.value.code == "master_file_changed" and _master(home, "show")["master"]["revision"] == 1

    # `init --from <the folder's master.md>` is the same explicit import: the file is written again with its ids.
    file.write_text(file.read_text(encoding="utf-8").replace(" <!-- id:b-billing -->", ""), encoding="utf-8")
    assert "b-billing" not in file.read_text(encoding="utf-8")
    made = _master(home, "init", "--from", str(file), "--revision", "1")
    assert (made["status"], made["ids_restored"], made["file"]["wrote"], made["file"]["state"]) == ("revised", 1, "master.md", "current")
    assert file.read_text(encoding="utf-8") == _stored(home) and "id:b-billing" in file.read_text(encoding="utf-8")
    assert _texts(home)["b-oncall"] == "Ran the on-call rotation for 6 teams."

    # The folder itself refuses contact data, whoever calls it, and names no value.
    with pytest.raises(resumes_folder.ResumesFolderError) as contact:
        resumes_folder.save_master(home, markdown=f"## Summary\n\n- Write to {EMAIL}\n", text=f"## Summary\n\n- Write to {EMAIL}\n", revision=9, revision_id="revision_x")
    assert contact.value.code == "contact_data_found" and EMAIL not in str(contact.value)
    assert file.read_text(encoding="utf-8") == _stored(home)
