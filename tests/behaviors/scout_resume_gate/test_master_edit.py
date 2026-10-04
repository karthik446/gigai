"""0.1.10.9 master P6: ``gigai scout resume master add | edit | remove``, through the real CLI on a scratch home.

The END outcomes:

* an agent adds a line from a chat (``--as agent``, ``--source``): ``master show`` reads it back under a new,
  stable id with who wrote it and where its evidence came from;
* every write names the revision it read (``revision_required``, ``revision_conflict`` with the current one);
* text that looks like contact data is refused and stored nowhere (no file, no journal object);
* a removed line is retired: no resume selects it, ``show --retired`` still lists it with its text, and
  ``add --restore`` puts it back under its own id;
* a line the master already has in other words is asked about, and nothing is written until ``--force``;
* a story or an answer is promoted to a line that carries the ``backed`` link;
* a profile whose resume shows the line follows the write; a new line is only offered.

Everything is synthetic: a small invented master written here (fictional example.test / 555-01xx values).
Every test runs against a temp ``--home``; no model is called and nothing reads the operator's home or resumes.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from click.testing import CliRunner
import pytest

from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.scout import master_edit, profile_records
from gigai.scout.find_jobs.resume_input import resume_for_profile
from gigai.scout.master_resume import parse_master
from gigai.scout.master_store import load_master
from gigai.scout.target_resolution import home_scout_target
from gigai.workpad import resolve_workpad

from tests.support.scout_profile_fixtures import default_find_jobs_config

EMAIL = "jordan.testwell@example.test"
PHONE = "(555) 010-0142"
LINK = "linkedin.com/in/jordan-testwell"
PLANTED = (EMAIL, "jordan.testwell", PHONE, "010-0142", LINK, "jordan-testwell")

DEPLOY = "Cut deploy time from 40 minutes to 6 with a staged pipeline."
GUIDE = "Wrote the incident review guide the group still uses."
SMALL = f"""<!-- gigai-master:1 -->

## Summary

- Platform engineer with 9 years of experience in payment systems. <!-- id:sum-platform tags:backend -->

## Experience

### Example Corp <!-- id:r-example -->
Staff Engineer | Jun 2019 - Present
- {DEPLOY} <!-- id:b-deploy tags:delivery -->
- Ran the on-call rotation for 4 teams. <!-- id:b-oncall -->
- {GUIDE} <!-- id:b-guide tags:writing backed:story:incident-guide -->

### Northwind Labs <!-- id:r-north -->
Senior Engineer | Mar 2014 - May 2019
- Built the billing export used by 30 finance analysts. <!-- id:b-billing -->

## Skills

- Languages: Python, Go, SQL <!-- id:s-lang -->
- Cloud: Kubernetes, Docker <!-- id:s-cloud -->
"""
HELM = "Moved 12 services to Helm charts released through ArgoCD."


def _setup(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    result = CliRunner().invoke(
        cli, ["setup", "--non-interactive", "--home", str(home), "--workpad-root", str(tmp_path / "workpads"), "--editor", "/usr/bin/true", "--json"],
    )
    assert result.exit_code == 0, result.output
    return home


def _master(home: Path, *args: str, ok: bool = True) -> dict:
    result = CliRunner().invoke(cli, ["scout", "resume", "master", *args, "--home", str(home), "--json"])
    assert result.exit_code == (0 if ok else 1), result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _plain(home: Path, *args: str) -> str:
    result = CliRunner().invoke(cli, ["scout", "resume", "master", *args, "--home", str(home)])
    assert result.exit_code == 0, result.output
    return result.output


def _scout(home: Path, *args: str) -> dict:
    result = CliRunner().invoke(cli, ["scout", *args, "--home", str(home), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    """A scratch home whose master is ``SMALL`` at revision 1 (written by the operator)."""

    made = _setup(tmp_path)
    source = tmp_path / "master-in.md"
    source.write_text(SMALL, encoding="utf-8")
    assert _master(made, "init", "--from", str(source))["status"] == "created"
    return made


def _workpad(home: Path) -> Path:
    return resolve_workpad(home_root=home, requested_target=home_scout_target(home), gig_id=None, allow_semantic_state=True).path


def _head(home: Path) -> str:
    return subprocess.run(["git", "-C", str(_workpad(home)), "rev-parse", "HEAD"], capture_output=True, check=True, text=True).stdout


def _everything_stored(home: Path) -> bytes:
    """Every file under the home and the workpads, and every object of the workpad's journal."""

    data = b""
    for root in (home, home.parent / "workpads"):
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink() and ".git" not in path.parts:
                data += path.read_bytes()
    return data + subprocess.run(
        ["git", "-C", str(_workpad(home)), "cat-file", "--batch-all-objects", "--batch"], capture_output=True, check=True,
    ).stdout


def _items(home: Path, *args: str) -> dict[str, dict]:
    return {item["id"]: item for item in _master(home, "show", *args)["master"]["items"]}


def _stored(home: Path):
    stored = load_master(home_root=home, target=home_scout_target(home))
    assert stored is not None
    return stored


# --- add: an agent writes a line from a chat ----------------------------------------------------


def test_an_agent_adds_a_line_and_show_reads_it_back_with_who_wrote_it_and_where_from(home: Path) -> None:
    before = _items(home)

    added = _master(home, "add", "--entry", "r-example", "--text", HELM, "--tag", "delivery", "--as", "agent", "--source", "from the user's chat on 3 Oct")

    assert (added["action"], added["status"], added["written"]) == ("add", "revised", True)
    assert added["changes"] == {"added": 1, "removed": 0, "changed": 0}
    assert (added["master"]["revision"], added["master"]["written_by"]) == (2, "agent")
    assert added["master"]["parent_revision"] == _master(home, "history")["revisions"][-1]["revision_id"]
    (line,) = added["items"]
    new_id = line["id"]
    assert new_id.startswith("b-") and new_id not in before, "a new line gets a new id"
    assert (line["text"], line["entry_id"], line["tags"], line["kind"]) == (HELM, "r-example", ["delivery"], "bullet")
    assert (line["written_by"], line["source"]) == ("agent", "from the user's chat on 3 Oct")
    assert added["near_duplicates"] == [] and added["retired"] == [] and added["warnings"] == []

    after = _items(home)
    assert after[new_id]["text"] == HELM and (after[new_id]["written_by"], after[new_id]["source"]) == ("agent", "from the user's chat on 3 Oct")
    # Every line that was there is there under its own id, word for word, with the mark it had.
    assert {key: (value["text"], value["mark"], value["tags"], value["backed"]) for key, value in after.items() if key != new_id} == {
        key: (value["text"], value["mark"], value["tags"], value["backed"]) for key, value in before.items()
    }
    assert all(value["written_by"] is None and value["source"] is None for key, value in after.items() if key != new_id), "an imported line names no writer"
    # The stored master is the canonical markdown: it reads back, and the line is the entry's last bullet.
    stored = _stored(home)
    assert parse_master(stored.master.markdown()) == stored.master
    assert stored.master.entries["r-example"].bullets == ("b-deploy", "b-oncall", "b-guide", new_id)
    assert f"{new_id}  [quantified]  {HELM} #delivery  (agent: from the user's chat on 3 Oct)" in _plain(home, "show")
    history = _master(home, "history")
    assert [(item["revision"], item["written_by"], item["added"]) for item in history["revisions"]] == [(2, "agent", 1), (1, "operator", 9)]


def test_without_as_the_write_is_the_operators_as_for_answers(home: Path) -> None:
    added = _master(home, "add", "--section", "other", "--text", "Certification: Certified Kubernetes Administrator (2020)")
    assert added["master"]["written_by"] == "operator" and added["items"][0]["written_by"] == "operator"
    assert (added["items"][0]["kind"], added["items"][0]["section"], added["items"][0]["id"][:2]) == ("other", "other", "o-")
    assert _stored(home).master.sections == ("summary", "experience", "skills", "other"), "a section the master did not have is made, in the format's order"


def test_an_edit_keeps_the_id_and_changes_only_that_line(home: Path) -> None:
    before = _items(home)

    edited = _master(home, "edit", "b-oncall", "--text", "Ran the on-call rotation for 5 teams.", "--revision", "1", "--as", "agent", "--source", "the user said so")

    assert (edited["action"], edited["status"], edited["changes"]) == ("edit", "revised", {"added": 0, "removed": 0, "changed": 1})
    assert [item["id"] for item in edited["items"]] == ["b-oncall"]
    after = _items(home)
    assert set(after) == set(before)
    assert after["b-oncall"]["text"] == "Ran the on-call rotation for 5 teams." and after["b-oncall"]["mark"] != before["b-oncall"]["mark"]
    assert (after["b-oncall"]["written_by"], after["b-oncall"]["source"]) == ("agent", "the user said so")
    assert all(after[key]["mark"] == before[key]["mark"] for key in before if key != "b-oncall")
    assert _stored(home).master.entries["r-example"].bullets == ("b-deploy", "b-oncall", "b-guide")

    # Tags alone (the operator): the text is the agent's still, so who wrote it and its source stay.
    tagged = _master(home, "edit", "b-oncall", "--tag", "leadership", "--tag", "on-call", "--revision", "2")
    assert tagged["items"][0]["tags"] == ["leadership", "on-call"] and tagged["master"]["written_by"] == "operator"
    assert (tagged["items"][0]["written_by"], tagged["items"][0]["source"], tagged["items"][0]["mark"]) == ("agent", "the user said so", after["b-oncall"]["mark"])
    assert _master(home, "edit", "b-oncall", "--tag", "", "--revision", "3")["items"][0]["tags"] == []

    # A source alone writes no revision: it says where the line's evidence came from.
    sourced = _master(home, "edit", "b-billing", "--source", "from the user's repo, at the user's request", "--revision", "4", "--as", "agent")
    assert (sourced["status"], sourced["written"], sourced["master"]["revision"]) == ("unchanged", False, 4)
    billing = _items(home)["b-billing"]
    assert (billing["written_by"], billing["source"]) == (None, "from the user's repo, at the user's request"), "the agent named a source; it did not write the line"
    assert "b-billing  [quantified]  Built the billing export used by 30 finance analysts.  (source: from the user's repo, at the user's request)" in _plain(home, "show")

    # An entry: its heading and the lines under it; its id and its bullets stay.
    entry = _master(home, "edit", "r-north", "--heading", "Northwind Laboratories", "--role", "Senior Engineer | Mar 2014 - Jun 2019", "--revision", "4")
    assert entry["items"][0]["kind"] == "entry" and entry["items"][0]["heading"] == "Northwind Laboratories"
    north = _stored(home).master.entries["r-north"]
    assert (north.heading, north.sublines, north.bullets) == ("Northwind Laboratories", ("Senior Engineer | Mar 2014 - Jun 2019",), ("b-billing",))


def test_a_text_that_changed_by_a_file_import_no_longer_names_the_agent_as_its_writer(home: Path, tmp_path: Path) -> None:
    new_id = _master(home, "add", "--entry", "r-example", "--text", HELM, "--as", "agent", "--source", "chat")["items"][0]["id"]
    assert _items(home)[new_id]["written_by"] == "agent"
    path = tmp_path / "edited.md"
    path.write_text(_stored(home).master.markdown().replace("12 services", "14 services"), encoding="utf-8")
    assert _master(home, "init", "--from", str(path), "--revision", "2")["changes"]["changed"] == 1

    line = _items(home)[new_id]
    assert "14 services" in line["text"] and (line["written_by"], line["source"]) == (None, None)


# --- the revision it read -----------------------------------------------------------------------


def test_every_write_names_the_revision_it_read(home: Path) -> None:
    head = _head(home)

    for args in (("edit", "b-oncall", "--text", "Ran on-call."), ("remove", "b-oncall")):
        refused = _master(home, *args, ok=False)["error"]
        assert refused["code"] == "revision_required" and refused["current"]["revision"] == 1 and "--revision 1" in refused["message"]

    assert _master(home, "add", "--entry", "r-example", "--text", HELM)["master"]["revision"] == 2  # an add goes on top of the current revision
    head = _head(home)
    for args in (
        ("edit", "b-oncall", "--text", "Ran on-call.", "--revision", "1"),
        ("remove", "b-oncall", "--revision", "1"),
        ("add", "--entry", "r-example", "--text", "Mentored 3 engineers.", "--revision", "1"),
        ("add", "--skill", "Helm", "--revision", "9"),
    ):
        refused = _master(home, *args, ok=False)["error"]
        assert refused["code"] == "revision_conflict" and refused["current"]["revision"] == 2, args
    assert _head(home) == head and len(_master(home, "history")["revisions"]) == 2, "a refused write stores nothing"

    assert _master(home, "edit", "b-oncall", "--text", "Ran on-call.", "--revision", "2")["master"]["revision"] == 3
    # The same change again is not a new revision.
    again = _master(home, "edit", "b-oncall", "--text", "Ran on-call.", "--revision", "3")
    assert (again["status"], again["written"], again["master"]["revision"], again["changes"]) == ("unchanged", False, 3, {"added": 0, "removed": 0, "changed": 0})


def test_what_is_not_there_is_refused_by_name(home: Path, tmp_path: Path) -> None:
    cases = {
        ("edit", "b-nope", "--text", "x y z", "--revision", "1"): "master_item_not_found",
        ("remove", "b-nope", "--revision", "1"): "master_item_not_found",
        ("remove", "--revision", "1"): "master_item_not_found",
        ("remove", "--skill", "Fortran", "--revision", "1"): "master_skill_not_found",
        ("add", "--entry", "r-nope", "--text", "A new line."): "master_entry_not_found",
        ("add", "--text", "A line with no place."): "master_place_invalid",
        ("add", "--text", "A line.", "--section", "experience"): "master_place_invalid",
        ("add", "--text", "A line.", "--entry", "r-example", "--section", "other"): "master_place_invalid",
        ("add",): "master_add_invalid",
        ("add", "--text", "A line.", "--skill", "Helm"): "master_add_invalid",
        ("add", "--heading", "Acme"): "master_add_invalid",
        ("add", "--heading", "Acme", "--section", "skills"): "master_place_invalid",
        ("add", "--skill", "Helm, ArgoCD"): "master_skill_invalid",
        ("add", "--skill", "Helm", "--to", "s-nope"): "master_item_not_found",
        ("add", "--skill", "Helm"): "master_skills_line_required",
        ("add", "--restore", "b-never"): "master_item_not_found",
        ("add", "--restore", "b-oncall"): "master_line_exists",
        ("add", "--entry", "r-example", "--text", "x" * 401): "master_text_invalid",
        ("add", "--entry", "r-example", "--text", "A line <!-- id:b-mine -->"): "master_text_invalid",
        ("add", "--entry", "r-example", "--text", "A line.", "--tag", "two words"): "master_tag_invalid",
        ("add", "--entry", "r-example", "--text", "A line.", "--from-story", "story:nope"): "story_not_found",
        ("add", "--entry", "r-example", "--text", "A line.", "--from-answer", "skill:nope"): "answer_not_found",
        ("edit", "b-oncall", "--revision", "1"): "master_edit_empty",
        ("edit", "b-oncall", "--heading", "Acme", "--revision", "1"): "master_edit_invalid",
        ("edit", "r-example", "--text", "A line.", "--revision", "1"): "master_edit_invalid",
        ("show", "--retired", "--section", "skills"): "master_option_invalid",
        ("show", "--revision", "7"): "master_revision_not_found",
    }
    head = _head(home)
    for args, code in cases.items():
        assert _master(home, *args, ok=False)["error"]["code"] == code, args
    assert _head(home) == head

    empty = _setup(tmp_path / "empty")
    for args in (("add", "--section", "other", "--text", "A line."), ("edit", "b-x", "--text", "A line.", "--revision", "1"), ("remove", "b-x", "--revision", "1"), ("show", "--retired")):
        error = _master(empty, *args, ok=False)["error"]
        assert error["code"] == "master_not_found" and "master init" in error["message"], args


# --- privacy: contact data is refused and stored nowhere ----------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ("add", "--entry", "r-example", "--text", f"Reach me at {EMAIL} for the write-up."),
        ("add", "--entry", "r-example", "--text", f"Ran the hotline {PHONE} for the release."),
        ("add", "--section", "other", "--text", f"Profile: {LINK}"),
        ("add", "--section", "summary", "--text", f"Engineer; see https://{LINK} for more."),
        ("add", "--section", "experience", "--heading", f"Example Corp ({EMAIL})", "--role", "Engineer | 2020 - 2021"),
        ("add", "--section", "experience", "--heading", "Example Corp", "--role", f"Engineer | 2020 - 2021 | {PHONE}"),
        ("add", "--skill", EMAIL),
        ("add", "--entry", "r-example", "--text", "Shipped the export.", "--source", f"mailed from {EMAIL}"),
        ("edit", "b-oncall", "--text", f"Ran on-call; call {PHONE}.", "--revision", "1"),
        ("edit", "r-north", "--heading", f"Northwind ({LINK})", "--revision", "1"),
        ("edit", "b-oncall", "--source", f"see {LINK}", "--revision", "1"),
    ],
)
def test_text_that_looks_like_contact_data_is_refused_and_stored_nowhere(home: Path, args: tuple[str, ...]) -> None:
    head = _head(home)

    result = CliRunner().invoke(cli, ["scout", "resume", "master", *args, "--as", "agent", "--home", str(home), "--json"])

    assert result.exit_code == 1, result.output
    error = json.loads(result.output.strip().splitlines()[-1])["error"]
    assert error["code"] == "personal_info_refused", error
    assert not any(value in result.output for value in PLANTED), "a refusal names the kind, never the value"
    stored = _everything_stored(home)
    assert not any(value.encode() in stored for value in PLANTED), "nothing of the refused text is in a file or in the journal"
    assert _head(home) == head and _master(home, "show")["master"]["revision"] == 1
    plain = CliRunner().invoke(cli, ["scout", "resume", "master", *args, "--home", str(home)])
    assert plain.exit_code == 1 and not any(value in plain.output for value in PLANTED)


# --- remove: retired, never lost ----------------------------------------------------------------


def test_a_removed_line_is_retired_and_can_be_put_back_under_its_own_id(home: Path) -> None:
    before = _items(home)["b-guide"]

    removed = _master(home, "remove", "b-guide", "--revision", "1", "--as", "agent")

    assert (removed["action"], removed["status"], removed["changes"]) == ("remove", "revised", {"added": 0, "removed": 1, "changed": 0})
    assert removed["retired"] == [{
        "id": "b-guide", "kind": "bullet", "section": "experience", "entry_id": "r-example", "text": GUIDE,
        "last_revision": 1, "retired_in": 2, "retired_by": "agent",
    }]
    assert "b-guide" not in _items(home), "no resume can select a retired line: the master does not hold it"
    assert _stored(home).master.entries["r-example"].bullets == ("b-deploy", "b-oncall")
    # History keeps it: the revision before still holds the line, and --retired lists it with its text.
    assert _items(home, "--revision", "1")["b-guide"]["text"] == GUIDE
    retired = _master(home, "show", "--retired")
    assert retired["retired"] == removed["retired"] and retired["master"]["revision"] == 2
    said = _plain(home, "show", "--retired")
    assert "1 retired line or entry" in said and f"b-guide  [bullet of r-example; in revision 1, retired by agent in revision 2]  {GUIDE}" in said
    text = CliRunner().invoke(cli, ["scout", "resume", "master", "remove", "b-oncall", "--revision", "2", "--home", str(home)]).output
    assert "Retired b-oncall" in text and "Revision 2 of the master still holds it" in text and "add --restore b-oncall" in text

    # Put back: its own id, text, tags and evidence, where it stood (after b-oncall is gone: after b-deploy).
    restored = _master(home, "add", "--restore", "b-guide", "--revision", "3")
    assert (restored["action"], restored["status"], restored["changes"]) == ("restore", "revised", {"added": 1, "removed": 0, "changed": 0})
    back = _items(home)["b-guide"]
    assert {key: back[key] for key in ("text", "tags", "backed", "entry_id", "mark", "strength")} == {
        key: before[key] for key in ("text", "tags", "backed", "entry_id", "mark", "strength")
    }
    assert _stored(home).master.entries["r-example"].bullets == ("b-deploy", "b-guide")
    assert [item["id"] for item in _master(home, "show", "--retired")["retired"]] == ["b-oncall"]
    assert _master(home, "add", "--restore", "b-oncall")["status"] == "revised"
    assert _stored(home).master.entries["r-example"].bullets == ("b-deploy", "b-oncall", "b-guide"), "each line is back where it stood"
    assert _master(home, "show", "--retired")["retired"] == []


def test_an_entry_is_retired_with_its_lines_and_comes_back_with_them(home: Path) -> None:
    removed = _master(home, "remove", "r-example", "--revision", "1")
    assert [(item["id"], item["kind"]) for item in removed["retired"]] == [
        ("r-example", "entry"), ("b-deploy", "bullet"), ("b-oncall", "bullet"), ("b-guide", "bullet"),
    ]
    assert list(_stored(home).master.entries) == ["r-north"]
    # A line of a retired entry needs its entry.
    orphan = _master(home, "add", "--restore", "b-deploy", ok=False)["error"]
    assert orphan["code"] == "master_entry_not_found" and "--restore r-example" in orphan["message"]

    restored = _master(home, "add", "--restore", "r-example")
    assert [item["id"] for item in restored["items"]] == ["r-example", "b-deploy", "b-oncall", "b-guide"]
    master = _stored(home).master
    assert list(master.entries) == ["r-example", "r-north"], "the entry is back where it stood"
    assert parse_master(SMALL).markdown() == master.markdown(), "the master is what it was before the entry was removed"
    # The last line of the master cannot go.
    for item_id in ("r-north", "sum-platform", "s-lang", "s-cloud"):
        _master(home, "remove", item_id, "--revision", str(_stored(home).revision.revision))
    last = _master(home, "remove", "r-example", "--revision", str(_stored(home).revision.revision), ok=False)["error"]
    assert last["code"] == "master_empty"


def test_a_line_dropped_by_a_file_import_is_listed_as_retired_too(home: Path, tmp_path: Path) -> None:
    path = tmp_path / "shorter.md"
    path.write_text("".join(line for line in SMALL.splitlines(keepends=True) if "id:b-oncall" not in line), encoding="utf-8")
    assert _master(home, "init", "--from", str(path), "--revision", "1", "--as", "agent")["changes"]["removed"] == 1

    (retired,) = _master(home, "show", "--retired")["retired"]
    assert (retired["id"], retired["last_revision"], retired["retired_in"], retired["retired_by"]) == ("b-oncall", 1, 2, "agent")
    assert _master(home, "add", "--restore", "b-oncall")["items"][0]["text"] == "Ran the on-call rotation for 4 teams."


# --- the near-duplicate warning -----------------------------------------------------------------


def test_a_line_the_master_already_has_in_other_words_is_asked_about_and_nothing_is_written(home: Path) -> None:
    head = _head(home)
    reworded = "Cut the deploy time from 40 minutes to 6 using a staged pipeline."

    asked = _master(home, "add", "--entry", "r-north", "--text", reworded, "--as", "agent")

    assert (asked["status"], asked["written"], asked["items"], asked["master"]["revision"]) == ("near_duplicate", False, [], 1)
    (near,) = asked["near_duplicates"]
    assert (near["id"], near["text"], near["entry_id"], near["same_numbers"]) == ("b-deploy", DEPLOY, "r-example", True)
    assert near["similarity"] >= master_edit.NEAR_DUPLICATE
    assert _head(home) == head, "asked means nothing was written"
    said = _plain(home, "add", "--entry", "r-north", "--text", reworded)
    assert "Not added" in said and "b-deploy (similarity" in said and "master edit b-deploy" in said and "--force" in said

    # The same line with other numbers: one of the two is out of date, and the answer says so.
    other = _master(home, "add", "--entry", "r-example", "--text", "Cut deploy time from 55 minutes to 9 with a staged pipeline.")
    assert other["status"] == "near_duplicate" and other["near_duplicates"][0]["same_numbers"] is False
    assert "different numbers" in _plain(home, "add", "--entry", "r-example", "--text", "Cut deploy time from 55 minutes to 9 with a staged pipeline.")

    # A line that is about something else is simply added.
    assert _master(home, "add", "--entry", "r-example", "--text", HELM)["status"] == "revised"
    # --force: the user wants both lines.
    forced = _master(home, "add", "--entry", "r-north", "--text", reworded, "--force")
    assert forced["status"] == "revised" and forced["items"][0]["text"] == reworded and forced["near_duplicates"] == []
    # Word for word in the same place is never a second line, forced or not.
    exists = _master(home, "add", "--entry", "r-example", "--text", DEPLOY.upper(), "--force", ok=False)["error"]
    assert exists["code"] == "master_line_exists" and "b-deploy" in exists["message"]


def test_near_lines_is_the_migrations_measure_and_compares_one_kind() -> None:
    master = parse_master(SMALL)
    assert [line.id for line in master_edit.near_lines(master, DEPLOY, kind="bullet")] == ["b-deploy"]
    assert master_edit.near_lines(master, DEPLOY, kind="bullet")[0].similarity == 1.0
    assert master_edit.near_lines(master, HELM, kind="bullet") == ()
    assert master_edit.near_lines(master, DEPLOY, kind="summary") == (), "a bullet is not measured against a summary"
    from gigai.scout.master_migration import NEAR_DUPLICATE, near_duplicate

    assert master_edit.NEAR_DUPLICATE is NEAR_DUPLICATE
    reworded = "Cut the deploy time from 40 minutes to 6 using a staged pipeline."
    assert master_edit.near_lines(master, reworded, kind="bullet")[0].similarity == round(near_duplicate(DEPLOY, reworded), 2)


# --- a story or an answer becomes a line --------------------------------------------------------


def test_a_story_and_an_answer_are_promoted_to_lines_that_carry_the_backed_link(home: Path) -> None:
    _scout(home, "answers", "save", "skill:helm", "--answer-text", "Yes: 3 years, charts for 40 services, released through ArgoCD.", "--as", "agent")
    story = _scout(
        home, "story", "save", "--title", "Cut CI time 60%", "--raw-text", "We cut CI from 50 minutes to 20 by caching and splitting the suite.",
        "--result", "CI went from 50 minutes to 20.", "--as", "agent",
    )["story"]["story_id"]
    assert story.startswith("story:")

    # An answer becomes a bullet under the right role...
    line = _master(home, "add", "--entry", "r-example", "--text", "Packaged 40 services as Helm charts released through ArgoCD.", "--from-answer", "skill:helm", "--as", "agent")
    assert line["warnings"] == [] and line["items"][0]["backed"] == ["answer:skill:helm"] and line["items"][0]["strength"] == "backed"
    # ...and a skill, once: every profile and every job can select it from now on.
    skill = _master(home, "add", "--skill", "Helm", "--skill", "ArgoCD", "--skill", "Docker", "--to", "s-cloud", "--from-answer", "skill:helm", "--as", "agent", "--source", "the Helm answer")
    assert skill["skills"] == {"line": "s-cloud", "added": ["Helm", "ArgoCD"], "removed": [], "already_listed": ["Docker"]}
    cloud = _items(home)["s-cloud"]
    assert (cloud["text"], cloud["skills"], cloud["backed"]) == ("Cloud: Kubernetes, Docker, Helm, ArgoCD", ["Kubernetes", "Docker", "Helm", "ArgoCD"], ["answer:skill:helm"])
    assert [(item["name"], item["written_by"], item["source"], item["backed"]) for item in cloud["skill_sources"]] == [
        ("ArgoCD", "agent", "the Helm answer", "answer:skill:helm"), ("Helm", "agent", "the Helm answer", "answer:skill:helm"),
    ]
    assert "(agent: ArgoCD)" in _plain(home, "show", "--section", "skills")
    # A link added to the Skills line later leaves what is kept per skill as it is.
    relinked = _master(home, "edit", "s-cloud", "--from-story", story, "--revision", str(skill["master"]["revision"]))
    assert relinked["items"][0]["backed"] == ["answer:skill:helm", story] and len(relinked["items"][0]["skill_sources"]) == 2

    # A story becomes a line; a number the story does not state is a warning to check, not a refusal.
    told = _master(home, "add", "--entry", "r-example", "--text", "Cut CI time from 50 minutes to 20 by caching and splitting the suite.", "--from-story", story, "--as", "agent")
    assert told["status"] == "revised" and told["warnings"] == [] and told["items"][0]["backed"] == [story]
    more = _master(home, "add", "--entry", "r-north", "--text", "Cut CI time 60% for 9 teams.", "--from-story", story, "--force", "--as", "agent")
    assert more["status"] == "revised" and len(more["warnings"]) == 1 and "9" in more["warnings"][0] and story in more["warnings"][0]
    # An existing line is linked to its evidence with edit; a link is added once.
    linked = _master(home, "edit", "b-deploy", "--from-story", story, "--revision", str(more["master"]["revision"]))
    assert linked["items"][0]["backed"] == [story] and linked["items"][0]["strength"] == "backed"
    assert _master(home, "edit", "b-deploy", "--from-story", story, "--revision", str(linked["master"]["revision"]))["status"] == "unchanged"
    # The stored markdown carries the links in the format's own comment.
    markdown = _stored(home).master.markdown()
    assert f"backed:{story} -->" in markdown and "backed:answer:skill:helm -->" in markdown

    # Evidence that is not there: nothing is written.
    head = _head(home)
    assert _master(home, "add", "--entry", "r-example", "--text", "Another line entirely.", "--from-story", "story:never_told", ok=False)["error"]["code"] == "story_not_found"
    assert _head(home) == head


# --- skills and entries -------------------------------------------------------------------------


def test_skills_are_added_to_a_line_by_id_or_label_and_removed_by_name(home: Path) -> None:
    labelled = _master(home, "add", "--skill", "Terraform", "--label", "cloud")
    assert labelled["skills"]["line"] == "s-cloud" and _items(home)["s-cloud"]["text"] == "Cloud: Kubernetes, Docker, Terraform"
    # A label the master does not have makes a new Skills line with a new id.
    made = _master(home, "add", "--skill", "Kafka", "--skill", "Flink", "--label", "Data")
    new_id = made["skills"]["line"]
    assert new_id.startswith("s-") and new_id not in ("s-lang", "s-cloud") and _items(home)[new_id]["text"] == "Data: Kafka, Flink"
    # Every skill already listed: nothing is written.
    again = _master(home, "add", "--skill", "python", "--to", "s-lang")
    assert (again["status"], again["written"], again["skills"]["already_listed"]) == ("unchanged", False, ["python"])
    # A whole line by --text; a label that exists is refused towards --skill.
    assert _master(home, "add", "--section", "skills", "--text", "Cloud: Helm", ok=False)["error"]["code"] == "master_line_exists"
    practices = _master(home, "add", "--section", "skills", "--text", "Practices: Incident review, Go, Mentoring")
    assert practices["skills"]["added"] == ["Incident review", "Mentoring"] and practices["skills"]["already_listed"] == ["Go"]
    assert practices["items"][0]["text"] == "Practices: Incident review, Mentoring", "a skill is listed once in the master"

    revision = _stored(home).revision.revision
    gone = _master(home, "remove", "--skill", "terraform", "--skill", "SQL", "--revision", str(revision))
    assert gone["skills"]["removed"] == ["SQL", "Terraform"] and gone["retired"] == []
    assert sorted(item["id"] for item in gone["items"]) == ["s-cloud", "s-lang"], "the lines that changed are returned as they are now"
    items = _items(home)
    assert (items["s-lang"]["text"], items["s-cloud"]["text"]) == ("Languages: Python, Go", "Cloud: Kubernetes, Docker")
    # The last skills of a line retire the line.
    emptied = _master(home, "remove", new_id, "--skill", "Kafka", "--skill", "Flink", "--revision", str(revision + 1))
    assert [item["id"] for item in emptied["retired"]] == [new_id] and new_id not in _items(home)
    assert _master(home, "add", "--restore", new_id)["items"][0]["text"] == "Data: Kafka, Flink"


def test_a_new_entry_is_placed_by_its_dates_and_takes_lines(home: Path) -> None:
    newest = _master(home, "add", "--section", "experience", "--heading", "Acme Robotics", "--role", "Principal Engineer | Feb 2024 - Present", "--as", "agent")
    oldest = _master(home, "add", "--section", "experience", "--heading", "First Job Inc", "--role", "Engineer | 2010 - 2013")
    middle = _master(home, "add", "--section", "experience", "--heading", "Between Co", "--role", "Engineer | 2016 - 2018")
    ids = [item["items"][0]["id"] for item in (newest, oldest, middle)]
    assert all(item.startswith("r-") for item in ids) and len(set(ids)) == 3
    master = _stored(home).master
    # Example Corp (2019 - Present) and Acme (2024 - Present) are both ongoing: the one that started later is first.
    assert list(master.entries) == [ids[0], "r-example", "r-north", ids[2], ids[1]]
    project = _master(home, "add", "--section", "projects", "--heading", "Taskloom")["items"][0]
    assert project["id"].startswith("p-") and project["section"] == "projects"
    assert _stored(home).master.sections == ("summary", "experience", "skills", "projects")
    assert _master(home, "add", "--section", "experience", "--heading", "Acme Robotics", "--role", "Principal Engineer | Feb 2024 - Present", ok=False)["error"]["code"] == "master_entry_exists"

    line = _master(home, "add", "--entry", ids[0], "--text", "Led the controls group of 6 engineers.")
    assert line["items"][0]["entry_id"] == ids[0] and _stored(home).master.entries[ids[0]].bullets == (line["items"][0]["id"],)


# --- a profile that shows the line follows; a new line is only offered --------------------------


RESUME = """## Summary

Platform engineer with 9 years of experience in payment systems.

## Experience

### Example Corp
Staff Engineer | Jun 2019 - Present
- Cut deploy time from 40 minutes to 6 with a staged pipeline.
- Ran the on-call rotation for 4 teams.

## Skills

- Languages: Python, Go, SQL
"""


def test_a_profile_that_shows_an_edited_or_retired_line_follows_and_a_new_line_is_only_offered(tmp_path: Path) -> None:
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

    # The migration: the master is the profile's resume, and the profile's selection is its own resume.
    assert _master(home, "init")["status"] == "created"
    before = profile()
    master = _stored(home).master
    deploy = next(item for item in master.items.values() if item.text.startswith("Cut deploy time"))
    oncall = next(item for item in master.items.values() if item.text.startswith("Ran the on-call"))
    assert {deploy.id, oncall.id} <= set(before.master_selection.item_ids)

    # 1. A new line: offered, and the profile's resume does not move.
    added = _master(home, "add", "--entry", deploy.entry_id, "--text", HELM, "--as", "agent")
    assert added["profiles"]["synced"] == []
    assert [(offer["profile_id"], offer["new_lines"], offer["offer"]) for offer in added["profiles"]["offers"]] == [
        (before.profile_id, [added["items"][0]["id"]], "1 new master line: refresh?"),
    ]
    assert profile().resume_ref == before.resume_ref and "Helm" not in resume_text()
    assert "2 new master lines: refresh?" in _plain(home, "add", "--entry", deploy.entry_id, "--text", "Mentored 3 engineers to senior scope.")

    # 2. A line the profile shows is edited: its resume says the new wording, with no refresh.
    edited = _master(home, "edit", deploy.id, "--text", "Cut deploy time from 40 minutes to 4 with a staged pipeline.", "--revision", "3", "--as", "agent")
    (synced,) = edited["profiles"]["synced"]
    assert (synced["profile_id"], synced["action"], synced["changed"], synced["retired"]) == (before.profile_id, "synced", [deploy.id], [])
    assert profile().resume_ref != before.resume_ref
    text = resume_text()
    assert "from 40 minutes to 4 with" in text and "from 40 minutes to 6 with" not in text and "Helm" not in text

    # 3. A line the profile shows is retired: its resume no longer shows it; a skill the same.
    removed = _master(home, "remove", oncall.id, "--revision", "4")
    assert removed["profiles"]["synced"][0]["retired"] == [oncall.id]
    assert "on-call" not in resume_text() and "from 40 minutes to 4 with" in resume_text()
    assert "SQL" in resume_text()
    skills = _master(home, "remove", "--skill", "SQL", "--revision", "5")
    assert skills["profiles"]["synced"][0]["retired"] == ["skill:SQL"] and "SQL" not in resume_text()
    status = _master(home, "selection", "status")["profiles"][0]
    assert status["stale"] is False and status["offer"] == "2 new master lines: refresh?"


# --- the file beside the master -----------------------------------------------------------------


def test_who_wrote_a_line_is_kept_in_one_small_file_and_a_broken_file_only_loses_that(home: Path) -> None:
    scout = home_scout_target(home)
    new_id = _master(home, "add", "--entry", "r-example", "--text", HELM, "--as", "agent", "--source", "chat")["items"][0]["id"]
    path = master_edit.lines_path(home, scout)
    assert path.parent.name == "master" and path.parent.parent.parent == home / "scout"
    kept = json.loads(path.read_text(encoding="utf-8"))
    assert kept["schema_version"] == "scout-master-lines:1" and set(kept["lines"]) == {new_id}
    assert kept["lines"][new_id]["written_by"] == "agent" and kept["lines"][new_id]["source"] == "chat" and kept["lines"][new_id]["revision"] == 2
    assert HELM not in path.read_text(encoding="utf-8"), "the file holds who and where from, never a line of the master"

    path.write_text("{not json", encoding="utf-8")
    assert _items(home)[new_id]["written_by"] is None, "an unreadable file means nothing is known, and the master still reads"
    assert _master(home, "edit", new_id, "--text", "Moved 14 services to Helm charts.", "--revision", "2", "--as", "agent")["items"][0]["written_by"] == "agent"
