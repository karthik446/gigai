"""0.1.10.9 master P5: one line or one entry of the master resume, changed by id (``master_edit``).

The END outcome, on a temp home whose master was made the way the operator
will make it (``gigai scout resume master init`` from two profiles' synthetic
resumes): after each change, what ``gigai scout resume master show --json``
and ``history --json`` print, and what the profiles' resumes say.  Nothing is
mocked; no model is called.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout import master_edit, master_profiles, profile_records
from gigai.scout.master_edit import MasterEditError
from gigai.scout.master_store import MasterStoreError, load_master, master_history
from gigai.scout.find_jobs.resume_input import resume_for_profile  # the reader every assessment and tailoring uses

from tests.behaviors.scout_resume_gate.test_master_tailor_outcomes import _cli, _Home

PLANTED = "jane.quillfeather@example.com"


@pytest.fixture()
def home(tmp_path: Path) -> _Home:
    made = _Home(tmp_path)
    made.migrate()
    return made


def _shown(home: _Home) -> dict:
    return _cli(home.home, "scout", "resume", "master", "show")["master"]


def _role(home: _Home) -> dict:
    """The newest role of the master (both profiles' resumes hold it)."""

    return next(entry for entry in _shown(home)["entries"] if entry["section"] == "experience")


def _resume_text(home: _Home, profile_id: str) -> str:
    profile = next(item for item in profile_records.list_profiles(home.resolved) if item.profile_id == profile_id)
    return resume_for_profile(profile, resolved=home.resolved, home_root=home.home, target=home.scout).text


def test_a_line_is_added_edited_retired_and_restored_each_as_one_revision(home: _Home) -> None:
    role = _role(home)
    kw = {"home_root": home.home, "target": home.scout}

    added = master_edit.add_line(revision=1, actor="agent", entry_id=role["id"], text="- Cut the deploy time of 40 services from 50 to 12 minutes.", tags=["delivery"], **kw)
    assert (added.status, added.stored.revision.revision, added.change.added) == ("revised", 2, 1)
    line_id = added.id
    shown = _shown(home)
    line = next(item for item in shown["items"] if item["id"] == line_id)
    # The bullet marker is dropped, the line is under its role, last, and its strength is derived (it states numbers).
    assert (line["text"], line["entry_id"], line["tags"], line["strength"]) == ("Cut the deploy time of 40 services from 50 to 12 minutes.", role["id"], ["delivery"], "quantified")
    assert next(entry for entry in shown["entries"] if entry["id"] == role["id"])["bullets"][-1] == line_id
    assert shown["revision"] == 2 and shown["written_by"] == "agent"

    edited = master_edit.edit_line(revision=2, item_id=line_id, text="Cut the deploy time of 40 services from 50 to 11 minutes.", **kw)
    assert (edited.status, edited.id, edited.change.changed) == ("revised", line_id, 1)
    assert next(item for item in _shown(home)["items"] if item["id"] == line_id)["text"].endswith("to 11 minutes.")  # the id stays

    # The same text again writes nothing.
    again = master_edit.edit_line(revision=3, item_id=line_id, text="Cut the deploy time of 40 services from 50 to 11 minutes.", **kw)
    assert again.status == "unchanged" and again.stored.revision.revision == 3 and again.profiles is None

    retired = master_edit.retire(revision=3, item_id=line_id, **kw)
    assert retired.change.removed == 1 and line_id not in {item["id"] for item in _shown(home)["items"]}
    gone = master_edit.retired(master_edit.revisions(home.home, home.scout))
    assert [(item.id, item.what, item.last_revision, item.retired_in, item.entry_heading) for item in gone] == [(line_id, "line", 3, 4, role["heading"])]
    assert gone[0].item is not None and gone[0].item.text.endswith("to 11 minutes.")  # as the last revision that held it had it

    back = master_edit.restore(revision=4, item_id=line_id, **kw)
    shown = _shown(home)
    assert back.change.added == 1 and next(entry for entry in shown["entries"] if entry["id"] == role["id"])["bullets"][-1] == line_id
    assert next(item for item in shown["items"] if item["id"] == line_id)["tags"] == ["delivery"]
    assert master_edit.retired(master_edit.revisions(home.home, home.scout)) == []

    history = master_history(home_root=home.home, target=home.scout)
    assert [(entry.revision.revision, entry.revision.written_by) for entry in history] == [(1, "operator"), (2, "agent"), (3, "operator"), (4, "operator"), (5, "operator")]


def test_a_write_names_the_revision_it_read_and_a_stale_one_writes_nothing(home: _Home) -> None:
    role = _role(home)
    kw = {"home_root": home.home, "target": home.scout}
    first = role["bullets"][0]
    master_edit.add_line(revision=1, entry_id=role["id"], text="Mentored 3 engineers to senior.", **kw)  # the agent wrote meanwhile: revision 2

    with pytest.raises(MasterStoreError) as stale:
        master_edit.edit_line(revision=1, item_id=first, text="Something else entirely.", **kw)
    assert stale.value.code == "revision_conflict" and stale.value.current is not None and stale.value.current.revision == 2
    with pytest.raises(MasterStoreError) as missing:
        master_edit.retire(revision=None, item_id=first, **kw)
    assert missing.value.code == "revision_required"
    # The revision is checked before the id: a writer with an old picture is told so, whatever it asked for.
    with pytest.raises(MasterStoreError) as both:
        master_edit.retire(revision=1, item_id="no-such-line", **kw)
    assert both.value.code == "revision_conflict"
    assert load_master(home_root=home.home, target=home.scout).revision.revision == 2  # type: ignore[union-attr]


def test_text_that_looks_like_contact_data_is_refused_whole_and_never_echoed(home: _Home) -> None:
    role = _role(home)
    kw = {"home_root": home.home, "target": home.scout}
    for change in (
        lambda: master_edit.add_line(revision=1, entry_id=role["id"], text=f"Reach me at {PLANTED} for the details.", **kw),
        lambda: master_edit.edit_line(revision=1, item_id=role["bullets"][0], text=f"Led the platform team; write to {PLANTED}.", **kw),
        lambda: master_edit.add_line(revision=1, section="other", text="Portfolio: https://example.com/jane-quillfeather", **kw),
    ):
        with pytest.raises(MasterEditError) as refused:
            change()
        assert refused.value.code == "personal_info_refused" and PLANTED not in str(refused.value) and "quillfeather" not in str(refused.value)
    stored = load_master(home_root=home.home, target=home.scout)
    assert stored is not None and stored.revision.revision == 1  # an edit never retires a line by having it stripped
    assert role["bullets"][0] in stored.master.items and PLANTED not in stored.master.markdown()


def test_bad_input_is_refused_by_rule_and_writes_nothing(home: _Home) -> None:
    role = _role(home)
    kw = {"home_root": home.home, "target": home.scout, "revision": 1}
    cases = [
        (lambda: master_edit.add_line(text="  ", entry_id=role["id"], **kw), "invalid_value"),
        (lambda: master_edit.add_line(text="one\ntwo", entry_id=role["id"], **kw), "invalid_value"),
        (lambda: master_edit.add_line(text="x" * 401, entry_id=role["id"], **kw), "invalid_value"),
        (lambda: master_edit.add_line(text="A line <!-- id:b-evil -->", entry_id=role["id"], **kw), "invalid_value"),
        (lambda: master_edit.add_line(text=7, entry_id=role["id"], **kw), "wrong_type"),
        (lambda: master_edit.add_line(text="A line.", **kw), "invalid_value"),  # nowhere to put it
        (lambda: master_edit.add_line(text="A line.", entry_id=role["id"], section="other", **kw), "invalid_value"),
        (lambda: master_edit.add_line(text="A line.", section="experience", **kw), "invalid_value"),  # a role's line names its entry
        (lambda: master_edit.add_line(text="A line.", entry_id="r-nope", **kw), "master_entry_not_found"),
        (lambda: master_edit.add_line(text="A line.", entry_id=role["id"], tags=["two words"], **kw), "invalid_value"),
        (lambda: master_edit.add_line(text="A line.", entry_id=role["id"], backed=["blog:1"], **kw), "invalid_value"),
        (lambda: master_edit.edit_line(item_id=role["bullets"][0], **kw), "invalid_value"),  # nothing to change
        (lambda: master_edit.edit_line(item_id="b-nope", text="A line.", **kw), "master_line_not_found"),
        (lambda: master_edit.restore(item_id=role["bullets"][0], **kw), "master_line_not_retired"),
        (lambda: master_edit.restore(item_id="b-never", **kw), "master_line_not_found"),
        (lambda: master_edit.add_entry(section="skills", heading="Acme", **kw), "invalid_value"),
        (lambda: master_edit.add_entry(section="experience", heading="Acme", sublines=["a", "b", "c", "d"], **kw), "invalid_value"),
        (lambda: master_edit.edit_entry(entry_id="r-nope", heading="Acme", **kw), "master_entry_not_found"),
        (lambda: master_edit.add_line(text="A line.", entry_id=role["id"], actor="robot", **kw), "master_actor_invalid"),
    ]
    for change, code in cases:
        with pytest.raises((MasterEditError, MasterStoreError)) as refused:
            change()
        assert refused.value.code == code, (code, refused.value.code, str(refused.value))
    # The same line twice is refused; a near-duplicate is only a warning.
    text = next(item["text"] for item in _shown(home)["items"] if item["id"] == role["bullets"][0])
    with pytest.raises(MasterEditError) as twice:
        master_edit.add_line(text=text, entry_id=role["id"], **kw)
    assert twice.value.code == "master_line_exists"
    assert load_master(home_root=home.home, target=home.scout).revision.revision == 1  # type: ignore[union-attr]
    near = master_edit.add_line(text=text.replace(" a ", " the ").rstrip(".") + ", end to end.", entry_id=role["id"], **kw)
    assert near.status == "revised" and near.near_duplicate is not None and near.near_duplicate["id"] == role["bullets"][0]
    assert 0.62 <= near.near_duplicate["similarity"] <= 1  # type: ignore[operator]


def test_a_role_is_added_edited_retired_with_its_lines_and_restored_with_them(home: _Home) -> None:
    kw = {"home_root": home.home, "target": home.scout}
    before = _shown(home)
    made = master_edit.add_entry(revision=1, section="experience", heading="Orbital Works", sublines=["Staff Engineer | Jun 2025 - Present"], **kw)
    line = master_edit.add_line(revision=2, entry_id=made.id, text="Built the launch scheduling service on Go and Postgres.", **kw)
    shown = _shown(home)
    roles = [entry for entry in shown["entries"] if entry["section"] == "experience"]
    # A new role goes first (a resume lists the newest first) and reads as ongoing.
    assert (roles[0]["id"], roles[0]["heading"], roles[0]["ongoing"], roles[0]["bullets"]) == (made.id, "Orbital Works", True, [line.id])

    master_edit.edit_entry(revision=3, entry_id=made.id, sublines=["Staff Engineer | Jun 2025 - Sep 2026"], **kw)
    edited = next(entry for entry in _shown(home)["entries"] if entry["id"] == made.id)
    assert (edited["heading"], edited["end"], edited["bullets"]) == ("Orbital Works", 2026, [line.id])  # the id and the lines stay

    # Retire an old role that has lines: they go with it, and come back with it, where they stood.
    old = roles[2]
    gone = master_edit.retire(revision=4, item_id=old["id"], **kw)
    assert gone.change.removed == 1 + len(old["bullets"])
    listed = master_edit.retired(master_edit.revisions(home.home, home.scout))
    assert [(item.id, item.what) for item in listed] == [(old["id"], "entry")]  # its lines are listed under it, not one by one
    back = master_edit.restore(revision=5, item_id=old["id"], **kw)
    assert back.change.added == 1 + len(old["bullets"])
    after = _shown(home)
    restored = next(entry for entry in after["entries"] if entry["id"] == old["id"])
    assert restored["bullets"] == old["bullets"] and restored["sublines"] == old["sublines"]
    order = [entry["id"] for entry in after["entries"] if entry["section"] == "experience"]
    assert order == [made.id, *(entry["id"] for entry in before["entries"] if entry["section"] == "experience")]

    # A line of a retired role restored alone brings the role back with that one line.
    master_edit.retire(revision=6, item_id=old["id"], **kw)
    master_edit.restore(revision=7, item_id=old["bullets"][1], **kw)
    alone = next(entry for entry in _shown(home)["entries"] if entry["id"] == old["id"])
    assert alone["bullets"] == [old["bullets"][1]] and alone["heading"] == old["heading"]


def test_every_write_ends_with_the_profiles_a_shown_line_is_printed_again_and_a_new_one_is_only_offered(home: _Home) -> None:
    kw = {"home_root": home.home, "target": home.scout}
    statuses = {status.profile_id: status for status in master_profiles.selection_statuses(home_root=home.home, target=home.scout)}
    assert all(status.selection is not None and status.attached for status in statuses.values())
    both = [item_id for item_id in statuses[home.ai_id].selection.item_ids if item_id in statuses[home.swe_id].selection.item_ids and item_id.startswith("b-")]  # type: ignore[union-attr]
    only_ai = [item_id for item_id in statuses[home.ai_id].selection.item_ids if item_id not in statuses[home.swe_id].selection.item_ids and item_id.startswith("b-")]  # type: ignore[union-attr]
    assert both and only_ai
    resolved_before = {profile.profile_id: profile.resume_ref for profile in profile_records.list_profiles(home.resolved)}

    # A line only one profile shows: that profile's resume says the new wording, the other profile is not written.
    edited = master_edit.edit_line(revision=1, item_id=only_ai[0], text="Rewrote this line with the number 4242 in it.", **kw)
    assert [change["profile_id"] for change in edited.profiles["synced"]] == [home.ai_id]  # type: ignore[index]
    assert "4242" in _resume_text(home, home.ai_id) and "4242" not in _resume_text(home, home.swe_id)
    now = {profile.profile_id: profile.resume_ref for profile in profile_records.list_profiles(home.resolved)}
    assert now[home.ai_id] != resolved_before[home.ai_id] and now[home.swe_id] == resolved_before[home.swe_id]

    # A new line: offered to both, added to neither (a selection is sticky).
    role = _role(home)
    added = master_edit.add_line(revision=2, entry_id=role["id"], text="Introduced the number 9191 to the on-call rota.", **kw)
    assert added.profiles["synced"] == []  # type: ignore[index]
    assert {offer["profile_id"]: offer["offer"] for offer in added.profiles["offers"]} == {  # type: ignore[index]
        home.ai_id: "1 new master line: refresh?", home.swe_id: "1 new master line: refresh?",
    }
    assert "9191" not in _resume_text(home, home.ai_id) and "9191" not in _resume_text(home, home.swe_id)

    # A retired line both profiles show leaves both resumes.
    text = next(item["text"] for item in _shown(home)["items"] if item["id"] == both[0])
    assert text in _resume_text(home, home.ai_id)
    retired = master_edit.retire(revision=3, item_id=both[0], **kw)
    assert sorted(change["profile_id"] for change in retired.profiles["synced"]) == sorted([home.ai_id, home.swe_id])  # type: ignore[index]
    assert text not in _resume_text(home, home.ai_id) and text not in _resume_text(home, home.swe_id)


def test_the_last_line_cannot_be_retired(tmp_path: Path) -> None:
    home = _Home(tmp_path)
    source = tmp_path / "tiny.md"
    source.write_text("## Summary\n\n- Backend engineer with nine years of Python.\n", encoding="utf-8")
    stored = _cli(home.home, "scout", "resume", "master", "init", "--from", str(source))
    assert stored["status"] == "created"
    only = _shown(home)["items"][0]["id"]
    with pytest.raises(MasterEditError) as refused:
        master_edit.retire(home_root=home.home, target=home.scout, revision=1, item_id=only)
    assert refused.value.code == "master_would_be_empty"
    # A first line of a section the master does not have yet opens that section, in the shipped order.
    master_edit.add_line(home_root=home.home, target=home.scout, revision=1, section="skills", text="Languages: Python, Go")
    master_edit.add_entry(home_root=home.home, target=home.scout, revision=2, section="experience", heading="Acme")
    assert _shown(home)["sections"] == ["summary", "experience", "skills"]
