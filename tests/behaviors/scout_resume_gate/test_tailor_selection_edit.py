"""0.1.10.9 master P5: Add and Remove one master line on a job's tailored resume (``tailor_selection_edit``).

The END outcome on a temp home: a resume tailored from the master (made with
``gigai scout resume master init`` from two profiles' synthetic resumes; the
model is a script that copies the lines it is shown) is changed through
``change_stored_selection`` (what ``PUT /api/tailored-resumes/selection``
calls), and the tests read what is STORED: the JSON, the markdown, the PDF's
page count, Picked / Left out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout import tailor_selection_edit as edits
from gigai.scout.tailor_length import cut_again, restore_cut
from gigai.scout.tailored_resume import TailorError

from tests.behaviors.scout_resume_gate.test_master_tailor_outcomes import _body_lines, _Home
from tests.support.master_tailor import copies_what_it_is_shown, install_prompt_model


@pytest.fixture()
def tailored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``(home, response)``: the older profile's resume tailored from the master; it needed the fit, so lines are cut for length."""

    home = _Home(tmp_path)
    home.migrate()
    install_prompt_model(monkeypatch, copies_what_it_is_shown)
    response, _on_disk = home.tailor(home.swe_id)
    assert response.selection is not None and response.result.length is not None and response.result.length.pages == 2
    return home, response


def _change(home: _Home, response, use: str, item_id: str, **more: object) -> edits.SelectionEdit:
    return edits.change_stored_selection(
        home.home, home.scout, profile_id=home.swe_id, job_identity=response.job.job_identity, use=use, item_id=item_id,
        updated_at=response.updated_at, **more,  # type: ignore[arg-type]
    )


def _item_ids(stored) -> list[str]:
    return [ref["item_id"] for line in _body_lines({"result": stored.result.to_json()}) for ref in line["refs"] if ref.get("item_id")]


def _codes(stored, where: str) -> dict[str, str]:
    lines = stored.selection.picked if where == "picked" else stored.selection.left_out
    return {line.id: line.code for line in lines}


def test_remove_takes_a_picked_line_off_this_resume_and_says_why_under_left_out(tailored) -> None:
    home, response = tailored
    master = home.master()
    victim = next(line.id for line in response.selection.picked if master["kinds"].get(line.id) == "bullet")
    text = master["items"][victim]
    assert text in response.markdown

    edit = _change(home, response, "remove", victim)
    stored = home.stored(home.swe_id)
    assert (edit.applied, edit.changed, edit.pages) == (True, True, 2) and edit.response == stored
    assert text not in stored.markdown and victim not in _item_ids(stored)
    assert victim not in _codes(stored, "picked") and _codes(stored, "left_out")[victim] == "removed_by_you"
    assert stored.updated_at == response.updated_at  # the same tailoring: a line choice made a moment later is not refused
    assert home.pages(home.swe_id) <= 2
    # Nothing else moved: every other picked line is still picked, with the reason it had.
    assert {line.id: line.code for line in response.selection.picked if line.id != victim} == _codes(stored, "picked")
    # The master and the profile's own selection are untouched.
    assert home.master()["revision"] == 1 and victim in home.master()["items"]

    # Removing it again: it is not on the resume.
    with pytest.raises(TailorError) as refused:
        _change(home, response, "remove", victim)
    assert refused.value.code == "selection_line_not_shown"
    # Add puts it back, as a copy of the master line under its role.
    back = _change(home, response, "add", victim)
    assert back.applied and text in home.stored(home.swe_id).markdown and _codes(home.stored(home.swe_id), "picked")[victim] == "added_by_you"


def test_add_brings_back_a_line_the_fit_cut_as_it_was(tailored) -> None:
    home, response = tailored
    cut = next(item.id for item in response.selection.cut_for_length if item.kind == "bullet")
    # The line as the tailoring settled it, kept whole on the length record (a single line, or inside a role cut whole).
    record = response.result.length
    kept = [line for role in record.trimmed for line in role.bullets] + [line for role in record.cut for line in role.entry.bullets]
    held = next((line for line in kept if any(ref.item_id == cut for ref in line.refs)), None)
    assert held is not None
    text = home.master()["items"][cut]
    assert text not in response.markdown and cut in _codes(response, "left_out")

    edit = _change(home, response, "add", cut, fit="keep")
    stored = home.stored(home.swe_id)
    assert edit.applied and edit.changed and text in stored.markdown
    assert _codes(stored, "picked")[cut] == "added_by_you" and cut not in _codes(stored, "left_out")
    assert cut not in {item.id for item in stored.selection.cut_for_length}
    # The very line the tailoring settled comes back: its id and its sources.
    assert next(line for section in stored.result.sections for line in section.body_lines() if any(ref.item_id == cut for ref in line.refs)) == held
    # Adding it again changes nothing.
    again = _change(home, response, "add", cut)
    assert (again.applied, again.changed) == (True, False) and home.stored(home.swe_id) == stored


def test_an_add_that_needs_room_asks_first_then_cuts_the_fits_own_next_lines_or_keeps_both(tailored) -> None:
    home, response = tailored
    master = home.master()
    path = Path(response.stored_path)
    before = path.read_bytes()
    # A line the tailoring was never offered: a summary variant the resume does not show.
    added = next(line.id for line in response.selection.left_out if master["kinds"].get(line.id) == "summary")

    asked = _change(home, response, "add", added)
    assert (asked.applied, asked.changed, asked.pages, asked.max_pages) == (False, False, 3, 2)
    assert path.read_bytes() == before, "an Add that asks stores nothing"
    assert asked.would_cut and all(item.id != added for item in asked.would_cut)
    assert all(item.kind == "bullet" and item.text and item.role for item in asked.would_cut)
    assert asked.to_json()["needs_choice"] is True

    made = _change(home, response, "add", added, fit="cut")
    stored = home.stored(home.swe_id)
    assert made.applied and made.pages == 2 and [item.id for item in made.cut] == [item.id for item in asked.would_cut]
    assert master["items"][added] in stored.markdown and home.pages(home.swe_id) == 2
    for item in made.cut:
        assert master["items"][item.id] not in stored.markdown
        assert _codes(stored, "left_out")[item.id] == "cut_to_make_room"
        assert any(cut.id == item.id and cut.code == "cut_to_make_room" for cut in stored.selection.cut_for_length)
    assert _codes(stored, "picked")[added] == "added_by_you"
    # What was cut is on the resume's own length record: one Restore puts it back, and cutting again is the same resume.
    assert stored.result.length is not None and stored.result.length.status == "cut" and stored.result.length.pages == 2
    restored = restore_cut(stored.result)
    assert all(master["items"][item.id] in "\n".join(line.text for section in restored.sections for line in section.all_lines()) for item in made.cut)
    assert cut_again(restored) == stored.result

    # A second Add never cuts the line the user added first.
    second = next(line.id for line in stored.selection.left_out if line.code == "role_limit")
    asked_again = _change(home, stored, "add", second)
    if not asked_again.applied:
        assert added not in [item.id for item in asked_again.would_cut]
        kept = _change(home, stored, "add", second, fit="keep")
        both = home.stored(home.swe_id)
        assert kept.applied and kept.pages == 3 and kept.cut == ()
        assert master["items"][second] in both.markdown and master["items"][added] in both.markdown
        assert both.result.length is not None and both.result.length.over()  # the callout says it is over the limit


def test_a_line_of_a_role_that_was_cut_whole_brings_the_role_back_with_that_line(tailored) -> None:
    home, response = tailored
    master = home.master()
    record = response.result.length
    assert record.cut, "the older profile's tailoring leaves whole roles out"
    role = record.cut[-1]
    line = role.entry.bullets[0]
    item_id = next(ref.item_id for ref in line.refs if ref.item_id)
    heading = role.entry.heading[0].text.removeprefix("### ")
    assert heading not in response.markdown

    edit = _change(home, response, "add", item_id, fit="keep")
    stored = home.stored(home.swe_id)
    assert edit.applied and heading in stored.markdown and master["items"][item_id] in stored.markdown
    experience = next(section for section in stored.result.sections if section.heading == "experience")
    back = next(entry for entry in experience.entries if entry.heading[0].text.removeprefix("### ") == heading)
    assert [line.id for line in back.bullets] == [line.id]  # the role, with that one line
    assert heading not in [cut.entry.heading[0].text.removeprefix("### ") for cut in stored.result.length.cut]
    assert not any(cut.kind == "role" and master["entries"].get(cut.id) == heading for cut in stored.selection.cut_for_length)


def test_refusals_change_nothing(tailored, tmp_path: Path) -> None:
    home, response = tailored
    path = Path(response.stored_path)
    before = path.read_bytes()
    skills_line = next(item_id for item_id, kind in home.master()["kinds"].items() if kind == "skills")
    cases = [
        (lambda: _change(home, response, "add", "b-no-such-line"), "master_line_not_found"),
        (lambda: _change(home, response, "add", skills_line), "selection_line_unsupported"),
        (lambda: _change(home, response, "remove", "b-no-such-line"), "selection_line_not_shown"),
        (lambda: _change(home, response, "swap", "b-x"), "invalid_value"),
        (lambda: _change(home, response, "add", "b-x", fit="maybe"), "invalid_value"),
        (lambda: edits.change_stored_selection(home.home, home.scout, profile_id=home.swe_id, job_identity=response.job.job_identity, use="remove", item_id="b-x", updated_at="2020-01-01T00:00:00Z"), "tailored_resume_changed"),
        (lambda: edits.change_stored_selection(home.home, home.scout, profile_id=home.swe_id, job_identity="https://example.com/nope", use="remove", item_id="b-x"), "tailored_resume_not_found"),
    ]
    for change, code in cases:
        with pytest.raises(TailorError) as refused:
            change()
        assert refused.value.code == code, (code, refused.value.code)
    assert path.read_bytes() == before
