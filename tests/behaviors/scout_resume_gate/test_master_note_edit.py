"""0.1.11 N1b: a note is written with ``master edit --note`` and through ``master.md`` (SPEC 5.4).

The END outcomes, through the real CLI on a scratch home:

* ``gigai scout resume master edit ID --note "..."`` sets the note of a line or an entry and ``--clear-note`` removes
  it: each is ONE new revision (a new content digest), ``master show`` reads the note back, the line's mark and
  who wrote its text stay, and ``master.md`` says what is stored;
* a note that looks like contact data, or that a 0.1.10 reader would misread, is refused and stored nowhere;
* ``master.md`` round-trips notes through ``master sync``: a note typed, changed or deleted in the file is a change
  of that line; a bad note in the file refuses the import by line number and nothing is imported;
* a note edit prints no profile's resume again and makes no selection stale (a note is not part of a mark);
* a retired line comes back with the note it had.

Everything is synthetic (the invented master of ``test_master_edit.py``; example.test values). Every test runs
against a temp ``--home``; no model is called and nothing reads the operator's home or resumes.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.scout import master_edit, profile_records
from gigai.scout.find_jobs.resume_input import resume_for_profile  # the reader every assessment and tailoring uses
from gigai.scout.master_edit import MasterEditError
from gigai.scout.master_resume import parse_master
from gigai.scout.target_resolution import home_scout_target
from gigai.workpad import resolve_workpad

from tests.behaviors.scout_resume_gate.test_master_edit import DEPLOY, EMAIL, LINK, PHONE, RESUME, SMALL, _everything_stored, _head, _items, _master, _plain, _setup, _stored
from tests.support.scout_profile_fixtures import default_find_jobs_config

LEAD = "agentic roles: lead with this"
SHORTER = "shorter version: keep the first two bullets"
DEPLOY_LINE = f"- {DEPLOY} <!-- id:b-deploy tags:delivery -->"


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    """A scratch home whose master is ``SMALL`` at revision 1 (written by the operator)."""

    made = _setup(tmp_path)
    source = tmp_path / "master-in.md"
    source.write_text(SMALL, encoding="utf-8")
    assert _master(made, "init", "--from", str(source))["status"] == "created"
    return made


def _file(home: Path) -> Path:
    return home / "resumes" / "master.md"


def _entries(home: Path) -> dict[str, dict]:
    return {entry["id"]: entry for entry in _master(home, "show")["master"]["entries"]}


def test_edit_note_sets_a_note_and_clear_note_removes_it_each_as_one_revision(home: Path) -> None:
    first = _master(home, "show")["master"]
    before = _items(home)["b-deploy"]
    assert before["note"] is None and _file(home).read_text(encoding="utf-8") == SMALL

    # A note on a line: one revision, written by the agent; the line itself did not move.
    noted = _master(home, "edit", "b-deploy", "--note", f"  {LEAD}  ", "--revision", "1", "--as", "agent")
    assert (noted["action"], noted["status"], noted["written"], noted["changes"]) == ("edit", "revised", True, {"added": 0, "removed": 0, "changed": 1})
    assert (noted["master"]["revision"], noted["master"]["written_by"]) == (2, "agent")
    assert noted["master"]["content_sha256"] != first["content_sha256"]  # the note is part of the master's content
    (item,) = noted["items"]
    assert (item["id"], item["note"], item["text"], item["tags"], item["mark"]) == ("b-deploy", LEAD, DEPLOY, ["delivery"], before["mark"])
    shown = _items(home)["b-deploy"]
    assert (shown["note"], shown["mark"], shown["written_by"]) == (LEAD, before["mark"], before["written_by"])  # who wrote the TEXT stays
    assert f"{DEPLOY_LINE} <!-- note: {LEAD} -->" in _file(home).read_text(encoding="utf-8").splitlines()
    assert _file(home).read_text(encoding="utf-8") == _stored(home).master.markdown()
    assert f"(note: {LEAD})" in _plain(home, "show")

    # The same note again writes nothing.
    again = _master(home, "edit", "b-deploy", "--note", LEAD, "--revision", "2")
    assert (again["status"], again["written"], again["master"]["revision"]) == ("unchanged", False, 2)

    # A note on an entry, with the plain output saying it.
    said = _plain(home, "edit", "r-example", "--note", SHORTER, "--revision", "2")
    assert "Changed the entry r-example: Example Corp" in said and f"Its note (never printed in a resume): {SHORTER}" in said
    assert "The master is at revision 3" in said
    assert _entries(home)["r-example"]["note"] == SHORTER and _entries(home)["r-north"]["note"] is None
    assert f"### Example Corp <!-- id:r-example --> <!-- note: {SHORTER} -->" in _file(home).read_text(encoding="utf-8").splitlines()

    # A note set together with a new text: both in one revision; the note of the entry is not touched.
    both = _master(home, "edit", "b-oncall", "--text", "Ran the on-call rotation for 5 teams.", "--note", "ops roles only", "--revision", "3")
    assert (both["master"]["revision"], both["items"][0]["text"], both["items"][0]["note"]) == (4, "Ran the on-call rotation for 5 teams.", "ops roles only")
    assert _entries(home)["r-example"]["note"] == SHORTER

    # --clear-note: one revision each; with every note gone and the text back, the content is the first revision's again.
    cleared = _plain(home, "edit", "b-deploy", "--clear-note", "--revision", "4")
    assert "Changed b-deploy under r-example" in cleared and "Its note was removed." in cleared
    assert _items(home)["b-deploy"]["note"] is None
    _master(home, "edit", "r-example", "--clear-note", "--revision", "5")
    last = _master(home, "edit", "b-oncall", "--text", "Ran the on-call rotation for 4 teams.", "--clear-note", "--revision", "6")
    assert (last["master"]["revision"], last["master"]["content_sha256"]) == (7, first["content_sha256"])
    assert _file(home).read_text(encoding="utf-8") == SMALL
    # Clearing a note that is not there writes nothing.
    none = _master(home, "edit", "b-deploy", "--clear-note", "--revision", "7")
    assert (none["status"], none["written"]) == ("unchanged", False)
    history = _master(home, "history")["revisions"]
    assert [(entry["revision"], entry["changed"]) for entry in history] == [(7, 1), (6, 1), (5, 1), (4, 1), (3, 1), (2, 1), (1, 0)]


@pytest.mark.parametrize(
    ("note", "code", "says"),
    [
        (f"ask {EMAIL} about this one", "personal_info_refused", "the note looks like it holds personal information"),
        (f"call {PHONE} first", "personal_info_refused", "the note looks like it holds personal information"),
        (f"see {LINK}", "personal_info_refused", "the note looks like it holds personal information"),
        ("zebra-quartz use with id:b-oncall", "master_note_invalid", "id:, tags:, backed: or gigai-master:"),
        ("zebra-quartz tags:ai", "master_note_invalid", "id:, tags:, backed: or gigai-master:"),
        ("backed:story:incident-guide zebra-quartz", "master_note_invalid", "id:, tags:, backed: or gigai-master:"),
        ("zebra-quartz gigai-master:2", "master_note_invalid", "id:, tags:, backed: or gigai-master:"),
        ("zebra-quartz --> and more", "master_note_invalid", "comment mark"),
        ("zebra-quartz <!-- and more", "master_note_invalid", "comment mark"),
        ("zebra-quartz " + "x" * 300, "master_note_invalid", "at most 300 characters"),
        ("zebra-quartz\nsecond line", "master_note_invalid", "one line"),
        ("   ", "master_note_invalid", "--clear-note"),
    ],
)
def test_a_note_that_is_contact_data_or_that_0_1_10_would_misread_is_refused_and_stored_nowhere(home: Path, note: str, code: str, says: str) -> None:
    head, stored, file = _head(home), _everything_stored(home), _file(home).read_bytes()
    for item_id in ("b-deploy", "r-example"):
        refused = _master(home, "edit", item_id, "--note", note, "--revision", "1", ok=False)
        assert refused["error"]["code"] == code and says in refused["error"]["message"], refused
        for planted in (EMAIL, PHONE, LINK, "zebra-quartz"):
            assert planted not in refused["error"]["message"]
    assert (_head(home), _everything_stored(home), _file(home).read_bytes()) == (head, stored, file)
    assert _master(home, "show")["master"]["revision"] == 1


def test_note_and_clear_note_together_and_a_note_on_what_is_not_there_are_refused(home: Path) -> None:
    both = _master(home, "edit", "b-deploy", "--note", LEAD, "--clear-note", "--revision", "1", ok=False)
    assert both["error"]["code"] == "master_edit_invalid" and "--note or --clear-note, not both" in both["error"]["message"]
    missing = _master(home, "edit", "b-nope", "--note", LEAD, "--revision", "1", ok=False)
    assert missing["error"]["code"] == "master_item_not_found"
    assert _master(home, "edit", "b-deploy", "--note", LEAD, ok=False)["error"]["code"] == "revision_required"
    assert _master(home, "edit", "b-deploy", "--note", LEAD, "--revision", "9", ok=False)["error"]["code"] == "revision_conflict"
    assert _master(home, "show")["master"]["revision"] == 1
    # The function the routes call says the same.
    with pytest.raises(MasterEditError) as raised:
        master_edit.edit(home_root=home, target=home_scout_target(home), item_id="b-deploy", revision=1, note=LEAD, clear_note=True)
    assert raised.value.code == "master_edit_invalid"


def test_master_md_round_trips_notes_through_master_sync(home: Path) -> None:
    file = _file(home)
    _master(home, "edit", "b-deploy", "--note", LEAD, "--revision", "1")
    written = file.read_text(encoding="utf-8")
    assert f"{DEPLOY_LINE} <!-- note: {LEAD} -->" in written.splitlines()
    assert _master(home, "sync")["status"] == "unchanged"  # the file GigAI wrote says what is stored

    # The user's editor: one note reworded, one typed on a line (before its id) and one on an entry heading, in the file only.
    mine = (
        written.replace(f"<!-- note: {LEAD} -->", "<!-- note: agentic roles: lead with this one -->")
        .replace("- Ran the on-call rotation for 4 teams. <!-- id:b-oncall -->", "- Ran the on-call rotation for 4 teams. <!-- note: ops roles: keep --> <!-- id:b-oncall -->")
        .replace("### Northwind Labs <!-- id:r-north -->", f"### Northwind Labs <!-- id:r-north --> <!-- note: {SHORTER} -->")
    )
    file.write_text(mine, encoding="utf-8")
    assert _items(home)["b-oncall"]["note"] is None  # nothing reads the file by itself
    imported = _master(home, "sync")
    assert (imported["status"], imported["written"], imported["changes"]) == ("imported", True, {"added": 0, "removed": 0, "changed": 3})
    assert sorted(row["id"] for row in imported["changed"]) == ["b-deploy", "b-oncall", "r-north"] and imported["retired"] == [] and imported["added"] == []
    assert imported["ids"] == {"kept": 9, "assigned": 0, "restored": 0}
    items = _items(home)
    assert (items["b-deploy"]["note"], items["b-oncall"]["note"], _entries(home)["r-north"]["note"]) == ("agentic roles: lead with this one", "ops roles: keep", SHORTER)
    assert items["b-oncall"]["text"] == "Ran the on-call rotation for 4 teams." and items["b-deploy"]["tags"] == ["delivery"]
    # The file is written again in the one stored form (the note after the id), and reads back as the stored master.
    canonical = file.read_text(encoding="utf-8")
    assert "- Ran the on-call rotation for 4 teams. <!-- id:b-oncall --> <!-- note: ops roles: keep -->" in canonical.splitlines()
    assert canonical == _stored(home).master.markdown() and parse_master(canonical).markdown() == canonical
    assert _master(home, "sync")["status"] == "unchanged"

    # A note deleted in the file is removed from the line; the line stays.
    file.write_text(canonical.replace(" <!-- note: ops roles: keep -->", ""), encoding="utf-8")
    deleted = _master(home, "sync")
    assert (deleted["status"], deleted["changes"], [row["id"] for row in deleted["changed"]]) == ("imported", {"added": 0, "removed": 0, "changed": 1}, ["b-oncall"])
    assert _items(home)["b-oncall"]["note"] is None and _master(home, "show")["master"]["revision"] == 4


@pytest.mark.parametrize(
    ("note", "code", "says"),
    [
        (f"ask {EMAIL} about this one", "personal_info_refused", "line 12: email"),  # the check every import of the file runs
        ("zebra-quartz use with id:b-guide", "master_markdown_invalid", "line 12: a note may not hold a word that starts with id:"),
        ("zebra-quartz " + "x" * 300, "master_markdown_invalid", "line 12: a note has at most 300 characters"),
        ("zebra-quartz --> and more", "master_markdown_invalid", "line 12: a note ends at its '-->'"),
    ],
)
def test_a_bad_note_in_master_md_refuses_the_import_by_line_number_and_nothing_is_imported(home: Path, note: str, code: str, says: str) -> None:
    file = _file(home)
    mine = SMALL.replace("- Ran the on-call rotation for 4 teams. <!-- id:b-oncall -->", f"- Ran the on-call rotation for 4 teams. <!-- id:b-oncall --> <!-- note: {note} -->")
    assert mine.splitlines()[11].startswith("- Ran the on-call rotation")
    file.write_text(mine, encoding="utf-8")
    head = _head(home)
    refused = _master(home, "sync", ok=False)
    assert refused["error"]["code"] == code and says in refused["error"]["message"], refused
    assert EMAIL not in refused["error"]["message"] and "zebra-quartz" not in refused["error"]["message"]
    assert _head(home) == head and _master(home, "show")["master"]["revision"] == 1 and file.read_text(encoding="utf-8") == mine
    assert _items(home)["b-oncall"]["note"] is None


def test_a_retired_line_and_a_retired_entry_come_back_with_the_note_they_had(home: Path) -> None:
    _master(home, "edit", "b-deploy", "--note", LEAD, "--revision", "1")
    _master(home, "edit", "r-north", "--note", SHORTER, "--revision", "2")
    _master(home, "edit", "b-billing", "--note", "finance roles", "--revision", "3")
    _master(home, "remove", "b-deploy", "--revision", "4")
    _master(home, "remove", "r-north", "--revision", "5")
    assert "b-deploy" not in _items(home) and "r-north" not in _entries(home)
    _master(home, "add", "--restore", "b-deploy", "--revision", "6")
    _master(home, "add", "--restore", "r-north", "--revision", "7")
    assert (_items(home)["b-deploy"]["note"], _entries(home)["r-north"]["note"], _items(home)["b-billing"]["note"]) == (LEAD, SHORTER, "finance roles")


def test_a_note_edit_prints_no_profiles_resume_again_and_makes_no_selection_stale(tmp_path: Path) -> None:
    home = _setup(tmp_path)
    assert CliRunner().invoke(cli, ["scout", "install", "--home", str(home), "--json"]).exit_code == 0
    scout = home_scout_target(home)
    resume = tmp_path / "resume.md"
    resume.write_text(RESUME, encoding="utf-8")
    assert CliRunner().invoke(cli, ["scout", "resume", "add", str(resume), "--home", str(home), "--json"]).exit_code == 0
    (scout / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))
    resolved = resolve_workpad(home_root=home, requested_target=scout, gig_id=None, allow_semantic_state=True)

    def profile() -> profile_records.ProfileRecord:
        found = profile_records.selected_profile(resolved, home_root=home, target=scout)
        assert found is not None
        return found

    def resume_text() -> str:
        return resume_for_profile(profile(), resolved=resolved, home_root=home, target=scout).text

    assert _master(home, "init")["status"] == "created"
    before, text = profile(), resume_text()
    master = _stored(home).master
    deploy = next(item for item in master.items.values() if item.text.startswith("Cut deploy time"))
    assert before.master_selection is not None and deploy.entry_id is not None
    assert deploy.id in before.master_selection.item_ids  # the profile's resume shows the line

    noted = _master(home, "edit", deploy.id, "--note", LEAD, "--revision", "1", "--as", "agent")
    assert (noted["status"], noted["master"]["revision"], noted["items"][0]["note"]) == ("revised", 2, LEAD)
    assert noted["profiles"] == {"synced": [], "offers": []}  # nothing printed again, nothing offered
    assert profile().resume_ref == before.resume_ref and resume_text() == text and LEAD not in text
    status = _master(home, "selection", "status")["profiles"][0]
    assert status["stale"] is False and not status["offer"]
    # A note on the role's heading: the same.
    role = _master(home, "edit", deploy.entry_id, "--note", SHORTER, "--revision", "2")
    assert role["profiles"] == {"synced": [], "offers": []} and profile().resume_ref == before.resume_ref and resume_text() == text
