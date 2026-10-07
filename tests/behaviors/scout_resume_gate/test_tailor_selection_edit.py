"""0.1.10.9 master P5: Add and Remove one master line on a job's tailored resume (``tailor_selection_edit``).

The END outcome on a temp home: a resume tailored from the master (made with
``gigai scout resume master init`` from two profiles' synthetic resumes; the
model is a script that copies the lines it is shown) is changed through
``change_stored_selection`` (what ``PUT /api/tailored-resumes/selection``
calls), and the tests read what is STORED: the JSON, the markdown, Picked /
Left out.

0.1.11.5 item 1c: an Add or a Remove counts NO page and lays out nothing (the
page is the user's to fit, with the spacing of the job's preview).  An Add
always applies; nothing is ever cut to make room for it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from gigai.scout import profile_records
from gigai.scout import tailor_master as tm
from gigai.scout import tailor_selection_edit as edits
from gigai.scout.tailor_length import cut_again, restore_cut
from gigai.scout.tailored_resume import TailorError, TailorJob, render_markdown, save_tailor_response

from tests.behaviors.scout_resume_gate.test_master_tailor_outcomes import _body_lines, _Home, _posting
from tests.support.master_tailor import copies_what_it_is_shown, install_prompt_model


@pytest.fixture()
def tailored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``(home, response)``: the older profile's resume tailored from the master, as the product stores it now: fitted
    to no page, so with no length record and nothing cut for length (0.1.11.5 item 1c)."""

    home = _Home(tmp_path)
    home.migrate()
    install_prompt_model(monkeypatch, copies_what_it_is_shown)
    response, _on_disk = home.tailor(home.swe_id)
    assert response.selection is not None and response.result.length is None and response.selection.cut_for_length == ()
    return home, response


@pytest.fixture()
def cut_before(tailored):
    """``(home, response)``: the same resume as a GigAI before 0.1.11.5 stored it: cut to its pages by the tailor call's
    fit (``fit_selected``; here 2 pages hold 12 bullets), with the cut on its length record and under Left out."""

    home, response = tailored
    profile = next(record for record in profile_records.list_profiles(home.resolved) if record.profile_id == home.swe_id)
    plan = tm.master_tailoring(
        home_root=home.home, target=home.scout, profile=profile, resolved=home.resolved, job_identity=response.job.job_identity,
        job=TailorJob(response.job.title, response.job.company, response.job.location, _posting()["text"]),
    )
    assert plan is not None

    def two_pages_hold_twelve(result) -> int:
        return 2 if sum(len(entry.bullets) for section in result.sections for entry in section.entries) <= 12 else 3

    fitted = tm.fit_selected(response.result, plan.candidates, plan.master, today=plan.today, measure=two_pages_hold_twelve)
    old = replace(response, result=fitted, markdown=render_markdown(fitted), selection=tm.selection_record(plan.master, plan.candidates, fitted, today=plan.today))
    save_tailor_response(old, home_root=home.home)
    stored = home.stored(home.swe_id)
    assert stored.result.length is not None and stored.result.length.status == "cut" and stored.result.length.pages == 2 and stored.selection.cut_for_length
    return home, stored


def _change(home: _Home, response, use: str, item_id: str, **more: object) -> edits.SelectionEdit:
    return edits.change_stored_selection(
        home.home, home.scout, profile_id=home.swe_id, job_identity=response.job.job_identity, use=use, item_id=item_id,
        updated_at=response.updated_at, **more,  # type: ignore[arg-type]
    )


@pytest.fixture()
def no_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    """From here on any page count or layout fails the test: an Add or a Remove makes none."""

    def refuse(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("an Add or a Remove laid out a page")

    monkeypatch.setattr(tm, "measure_pages", refuse)
    for name in ("pages_at", "measure_markdown", "fewest_pages", "_estimate"):
        monkeypatch.setattr(f"gigai.scout.resume_pdf.{name}", refuse)


def _item_ids(stored) -> list[str]:
    return [ref["item_id"] for line in _body_lines({"result": stored.result.to_json()}) for ref in line["refs"] if ref.get("item_id")]


def _codes(stored, where: str) -> dict[str, str]:
    lines = stored.selection.picked if where == "picked" else stored.selection.left_out
    return {line.id: line.code for line in lines}


def test_remove_takes_a_picked_line_off_this_resume_and_says_why_under_left_out(tailored, no_layout: None) -> None:
    home, response = tailored
    master = home.master()
    victim = next(line.id for line in response.selection.picked if master["kinds"].get(line.id) == "bullet")
    text = master["items"][victim]
    assert text in response.markdown

    edit = _change(home, response, "remove", victim)
    stored = home.stored(home.swe_id)
    # (``pages`` was 2 until 0.1.11.5: a Remove counts no page now; the field stays, null.)
    assert (edit.applied, edit.changed, edit.pages) == (True, True, None) and edit.response == stored
    assert text not in stored.markdown and victim not in _item_ids(stored)
    assert victim not in _codes(stored, "picked") and _codes(stored, "left_out")[victim] == "removed_by_you"
    assert stored.updated_at == response.updated_at  # the same tailoring: a line choice made a moment later is not refused
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
    # Neither change gave the resume a length record: nothing is measured, so nothing is ever "over" or "cut".
    assert home.stored(home.swe_id).result.length is None and home.stored(home.swe_id).selection.cut_for_length == ()


def test_add_brings_back_a_line_the_fit_cut_as_it_was(cut_before, no_layout: None) -> None:
    home, response = cut_before
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


def test_an_add_always_applies_counts_no_page_and_moves_nothing_else(cut_before, no_layout: None) -> None:
    """0.1.11.5 item 1c.  Until then (``test_an_add_that_needs_room_asks_first_then_cuts_the_fits_own_next_lines_or_keeps_both``)
    an Add that pushed a 2-page resume to a 3rd page stored NOTHING and answered ``needs_choice`` with the lines to cut
    (``would_cut``); ``fit="cut"`` then cut them and ``fit="keep"`` kept both and marked the resume over its limit.  Now
    the Add is stored at once whatever ``fit`` says, no page is counted (``no_layout`` fails the test on any layout),
    no line is cut to make room and nothing is marked over a limit.  On a resume a GigAI before 0.1.11.5 cut to its
    pages (``cut_before``), where the old behaviour had the most to do."""

    home, response = cut_before
    master = home.master()
    # A line the tailoring was never offered: a summary variant the resume does not show (this Add made a 3rd page).
    added = next(line.id for line in response.selection.left_out if master["kinds"].get(line.id) == "summary")

    edit = _change(home, response, "add", added)  # ``fit`` left at its default, "ask"
    stored = home.stored(home.swe_id)
    assert (edit.applied, edit.changed, edit.pages, edit.max_pages) == (True, True, None, 2) and edit.response == stored
    assert edit.would_cut == () == edit.cut
    assert edit.to_json() | {"item_id": None} == {
        "use": "add", "item_id": None, "applied": True, "changed": True, "needs_choice": False, "pages": None, "max_pages": 2, "would_cut": [], "cut": [],
    }
    assert master["items"][added] in stored.markdown and _codes(stored, "picked")[added] == "added_by_you"
    # Nothing else moved: every line that was shown is shown, with the reason it had; nothing was cut to make room.
    assert [item for item in _item_ids(stored) if item != added] == _item_ids(response)
    assert {line.id: line.code for line in response.selection.picked} == {key: code for key, code in _codes(stored, "picked").items() if key != added}
    assert "cut_to_make_room" not in _codes(stored, "left_out").values()
    assert [(cut.id, cut.code) for cut in stored.selection.cut_for_length] == [(cut.id, cut.code) for cut in response.selection.cut_for_length]
    # What the OLDER fit of this tailoring had cut stays on the length record with the numbers it had, so one Restore
    # still puts it back and cutting again is the same resume; the Add marked nothing as over a limit.
    before, after = response.result.length, stored.result.length
    assert after is not None and (after.status, after.pages, after.full_pages, after.max_pages) == ("cut", before.pages, before.full_pages, before.max_pages)
    assert (after.cut, after.trimmed) == (before.cut, before.trimmed) and not after.over()
    assert cut_again(restore_cut(stored.result)) == stored.result

    # ``fit`` is still accepted and changes nothing: a second and a third Add apply the same way, and the first stays.
    more = [line.id for line in stored.selection.left_out if line.code in ("role_limit", "over_cap", "not_offered", "left_out_by_tailoring")][:2]
    assert len(more) == 2
    for fit, item_id in zip(("cut", "keep"), more):
        again = _change(home, stored, "add", item_id, fit=fit)
        assert (again.applied, again.pages, again.would_cut, again.cut) == (True, None, (), ()) and again.to_json()["needs_choice"] is False
    both = home.stored(home.swe_id)
    assert all(master["items"][item_id] in both.markdown for item_id in (added, *more))
    assert set(_item_ids(stored)) <= set(_item_ids(both)) and "cut_to_make_room" not in _codes(both, "left_out").values()
    assert both.result.length is not None and both.result.length.status == "cut" and not both.result.length.over()


def test_a_line_of_a_role_that_was_cut_whole_brings_the_role_back_with_that_line(cut_before, no_layout: None) -> None:
    home, response = cut_before
    master = home.master()
    record = response.result.length
    # A role an older fit left with no line.  0.1.11.4 item 9: it keeps its heading (one line under "Earlier
    # experience") and its lines are on the length record.  When even that heading line did not fit, the role is on
    # the record whole (``length.cut``).  The Add brings the role back with the one line from either shape, so this
    # test takes whichever the fixture's fit made (which of the two a fit makes is pinned in test_tailor_master.py).
    experience = next(section for section in response.result.sections if section.heading == "experience")
    bare = [entry for entry in experience.entries if not entry.bullets]
    assert bare or record.cut, "the older fit leaves an old role with no line"
    if bare:
        entry = bare[-1]
        line = next(trimmed for trimmed in record.trimmed if trimmed.heading == entry.heading[0].id).bullets[0]
    else:
        entry = record.cut[-1].entry
        line = entry.bullets[0]
    item_id = next(ref.item_id for ref in line.refs if ref.item_id)
    heading = entry.heading[0].text.removeprefix("### ")
    assert f"### {heading}" not in response.markdown
    assert (f", {heading} | " in response.markdown.split("### Earlier experience")[1]) if bare else (heading not in response.markdown)

    edit = _change(home, response, "add", item_id, fit="keep")
    stored = home.stored(home.swe_id)
    assert edit.applied and f"### {heading}" in stored.markdown and stored.markdown.count(heading) == 1 and master["items"][item_id] in stored.markdown
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
