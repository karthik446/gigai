"""0110-10-05 C: ``tailor_length``, the length rule as a pure function of a result and a page count.

No renderer here: ``measure`` is a counting function (one page per
``_LINES_PER_PAGE`` printed lines), so every case is decided by the rule
alone.  The end-to-end symptom (a 3-page resume through
``run_tailored_resume``, the stored files, the CLI) is in
``test_tailor_part_c_outcomes.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from gigai.scout.find_jobs.contracts import FindJobsContractError
from gigai.scout.tailor_length import (
    LengthFit,
    apply_length_use,
    cut_again,
    fit_to_pages,
    length_note,
    oldest_first,
    restore_cut,
    role_label,
    shown_whole,
)
from gigai.scout.tailored_resume import (
    LENGTH_RULE,
    TailorJob,
    TailoredResume,
    apply_line_edit,
    apply_no_loss,
    render_markdown,
    tailor_context,
    tailor_line_stats,
    validate_tailored_output,
)

_TODAY = date(2026, 10, 3)
_JOB = TailorJob(title="Engineer", company="Acme", location="Remote", posting_text="Acme needs Python and SQL.")
_LINES_PER_PAGE = 10


def _resume(roles: list[tuple[str, int]], *, project_bullets: int = 0) -> str:
    """``roles``: ``(heading, bullets)``; the summary is R2, the roles follow, then Projects and Skills."""

    lines = ["## Summary", "Engineer with many years of Python and SQL.", "", "## Experience"]
    for index, (heading, bullets) in enumerate(roles):
        lines.append(heading)
        lines.extend(f"- Did thing {index}-{bullet} with Python." for bullet in range(bullets))
        lines.append("")
    if project_bullets:
        lines += ["## Projects", "**Side project** (2010)"]
        lines.extend(f"- Built part {bullet}." for bullet in range(project_bullets))
        lines.append("")
    lines += ["## Skills", "- Python, SQL"]
    return "\n".join(lines) + "\n"


def _settled(resume: str) -> TailoredResume:
    """The resume copied whole and settled (``apply_no_loss``), as a model that tailors nothing returns it."""

    ctx = tailor_context(resume)
    sections: dict[str, list] = {}
    order: list[str] = []
    current = ""
    for number, line in enumerate(ctx.resume_lines, 1):
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = []
            order.append(current)
        elif current in ("experience", "projects") and line.startswith("**"):
            sections[current].append({"heading_ref": [{"copy": number}], "bullets": []})
        elif current in ("experience", "projects"):
            sections[current][-1]["bullets"].append({"copy": number})
        else:
            sections[current].append({"copy": number})
    payload = {"sections": [{"heading": name, ("entries" if name in ("experience", "projects") else "lines"): sections[name]} for name in order]}
    return apply_no_loss(validate_tailored_output(payload, _JOB, ctx), _JOB, ctx, today=_TODAY)


def _measure(result: TailoredResume) -> int:
    return -(-result.line_count() // _LINES_PER_PAGE)  # ceil


def _roles(result: TailoredResume) -> list[str]:
    return [role_label(entry) for section in result.sections if section.heading == "experience" for entry in section.entries]


_RECENT = [
    ("**Staff Engineer — Alder** (2024–Present)", 6),
    ("**Senior Engineer — Birch** (2022–2024)", 6),
    ("**Engineer — Cedar** (2020–2022)", 6),
    ("**Engineer — Dogwood** (2019–2020)", 6),
]


# --- which role is the oldest ---------------------------------------------------------------------


def test_the_oldest_role_is_the_one_that_ended_first_whatever_the_page_order() -> None:
    shuffled = [
        ("**Engineer — Cedar** (2020–2022)", 1),
        ("**Staff Engineer — Alder** (2024–Present)", 1),
        ("**Engineer — Elm** (2019–2020)", 1),
        ("**Engineer — Dogwood** (2019–2020)", 1),
        ("**Senior Engineer — Birch** (2021–2024)", 1),
    ]
    entries = next(section for section in _settled(_resume(shuffled)).sections if section.heading == "experience").entries
    # Ended 2020 twice (the one lower on the page first), then 2022, 2024, and the ongoing role last.
    assert [role_label(entries[index]) for index in oldest_first(entries)] == [
        "Engineer — Dogwood (2019–2020)", "Engineer — Elm (2019–2020)", "Engineer — Cedar (2020–2022)",
        "Senior Engineer — Birch (2021–2024)", "Staff Engineer — Alder (2024–Present)",
    ]
    # One role names no year: the page order decides, from the bottom up.
    undated = [("**Staff Engineer — Alder** (2024–Present)", 1), ("**Engineer — Birch**", 1), ("**Engineer — Cedar** (2020–2022)", 1)]
    entries = next(section for section in _settled(_resume(undated)).sections if section.heading == "experience").entries
    assert oldest_first(entries) == [2, 1, 0]


# --- the cut --------------------------------------------------------------------------------------


def test_a_resume_that_fits_is_returned_unchanged_with_no_record() -> None:
    settled = _settled(_resume(_RECENT[:2]))  # 2 + 14 + 1 = 17 lines = 2 pages
    assert _measure(settled) == 2 and settled.length is None
    fitted = fit_to_pages(settled, measure=_measure)
    assert fitted == settled and "length" not in fitted.to_json()


def test_over_the_limit_whole_roles_go_oldest_first_until_it_fits_and_nothing_else_is_touched() -> None:
    settled = _settled(_resume(_RECENT, project_bullets=3))  # 1 + 28 + 4 + 1 = 34 lines = 4 pages
    assert _measure(settled) == 4
    fitted = fit_to_pages(settled, measure=_measure)

    assert _roles(fitted) == ["Staff Engineer — Alder (2024–Present)", "Senior Engineer — Birch (2022–2024)"]
    length = fitted.length
    assert length is not None and (length.status, length.max_pages, length.pages, length.full_pages) == ("cut", 2, 2, 4)
    assert [(role.position, role_label(role.entry)) for role in length.cut] == [(2, "Engineer — Cedar (2020–2022)"), (3, "Engineer — Dogwood (2019–2020)")]
    assert all(len(role.entry.bullets) == 6 for role in length.cut)  # cut whole, bullets and all
    # It stopped as soon as it fit: one cut left 27 lines (3 pages), two leave 20.
    assert fitted.line_count() == 20
    # Summary, projects and skills are exactly what they were; the roles that stay keep every bullet.
    for heading in ("summary", "projects", "skills"):
        assert next(s for s in fitted.sections if s.heading == heading) == next(s for s in settled.sections if s.heading == heading)
    assert [len(entry.bullets) for section in fitted.sections if section.heading == "experience" for entry in section.entries] == [6, 6]
    assert "Cedar" not in render_markdown(fitted) and "Dogwood" not in render_markdown(fitted)
    # Idempotent, and the same from any state.
    assert fit_to_pages(fitted, measure=_measure) == fitted
    assert fit_to_pages(restore_cut(fitted), measure=_measure) == fitted


def test_the_newest_role_always_stays_and_a_cut_that_cannot_fit_cuts_nothing() -> None:
    settled = _settled(_resume([("**Staff Engineer — Alder** (2024–Present)", 25), ("**Engineer — Birch** (2021–2024)", 2)]))
    fitted = fit_to_pages(settled, measure=_measure)  # 4 pages; the newest role alone is still 3
    assert fitted.sections == settled.sections
    assert fitted.length == LengthFit(2, 4, 4, "over")
    assert length_note(fitted.length) == "Length: 4 pages, over the 2-page limit; leaving out older roles would not fix it, so no role was cut."
    # Projects are never cut for length, however long.
    projects = _settled(_resume([("**Staff Engineer — Alder** (2024–Present)", 2)], project_bullets=3))
    assert fit_to_pages(projects, measure=lambda _result: 3).sections == projects.sections


def test_pages_that_cannot_be_measured_are_flagged_and_nothing_is_cut() -> None:
    settled = _settled(_resume(_RECENT))
    fitted = fit_to_pages(settled, measure=lambda _result: None)
    assert fitted.sections == settled.sections and fitted.length == LengthFit(2, None, None, "unmeasured")
    assert length_note(fitted.length) == "Length not checked: the pages could not be measured, so no role was cut (the 2-page limit applies)."

    # The renderer fails part-way: the roles stay.
    calls = iter([4, None])
    fitted = fit_to_pages(settled, measure=lambda _result: next(calls))
    assert fitted.sections == settled.sections and fitted.length == LengthFit(2, 4, 4, "over")


# --- the old-role bullets, and the one way back ---------------------------------------------------

_WITH_OLD = [*_RECENT[:2], ("**Engineer — Cedar** (2014–2016)", 5), ("**Junior Engineer — Dogwood** (2011–2014)", 5)]


def test_old_role_bullets_left_out_are_on_record_and_one_restore_puts_roles_and_bullets_back() -> None:
    assert LENGTH_RULE.old_role_bullets == 3 and LENGTH_RULE.old_role_years == 8
    settled = _settled(_resume(_WITH_OLD))
    # ``apply_no_loss`` trims the two old roles to 3 bullets (as before) and now records the bullets it left out.
    experience = next(section for section in settled.sections if section.heading == "experience")
    assert [len(entry.bullets) for entry in experience.entries] == [6, 6, 3, 3]
    assert settled.length is not None and settled.length.status == "cut" and settled.length.pages is None
    assert [(role.role, [line.text for line in role.bullets]) for role in settled.length.trimmed] == [
        ("Engineer — Cedar (2014–2016)", ["- Did thing 2-3 with Python.", "- Did thing 2-4 with Python."]),
        ("Junior Engineer — Dogwood (2011–2014)", ["- Did thing 3-3 with Python.", "- Did thing 3-4 with Python."]),
    ]
    assert [len(entry.dropped) for entry in experience.entries] == [0, 0, 2, 2]

    fitted = fit_to_pages(settled, measure=_measure)  # 1 + 14 + 8 + 1 = 24 lines = 3 pages -> cut the oldest
    assert _roles(fitted) == ["Staff Engineer — Alder (2024–Present)", "Senior Engineer — Birch (2022–2024)", "Engineer — Cedar (2014–2016)"]
    length = fitted.length
    assert length is not None and (length.status, length.pages, length.full_pages) == ("cut", 2, 3)
    assert length.roles() == ("Junior Engineer — Dogwood (2011–2014)",) and length.trimmed_count() == 4
    assert length_note(length) == (
        "Cut for length (3 pages -> 2): Junior Engineer — Dogwood (2011–2014); "
        "4 older bullets (2 of Engineer — Cedar (2014–2016), 2 of Junior Engineer — Dogwood (2011–2014))."
    )

    restored = restore_cut(fitted)
    whole = next(section for section in restored.sections if section.heading == "experience")
    assert [role_label(entry) for entry in whole.entries] == [role_label(entry) for entry in experience.entries]
    assert [len(entry.bullets) for entry in whole.entries] == [6, 6, 5, 5]
    assert [line.text for line in whole.entries[3].bullets] == [f"- Did thing 3-{bullet} with Python." for bullet in range(5)]
    assert all(not entry.dropped for entry in whole.entries) and tailor_line_stats(restored).dropped_bullets == 0
    ids = [line.id for section in restored.sections for line in section.all_lines()]
    assert all(ids) and len(set(ids)) == len(ids)
    assert restored.length is not None and restored.length.status == "restored" and restored.length.shown_pages() == 3
    assert length_note(restored.length).startswith("Put back (was cut for length): Junior Engineer — Dogwood (2011–2014); 4 older bullets")
    assert length_note(restored.length).endswith("The resume is 3 pages, over the 2-page limit.")
    assert shown_whole(fitted).sections == restored.sections and shown_whole(fitted).length is None

    # Restore again: nothing changes.  Cut again: exactly what it was.
    assert restore_cut(restored) is restored and apply_length_use(restored, "restore") is restored
    assert cut_again(restored) == fitted == apply_length_use(restored, "cut")
    assert cut_again(fitted) is fitted
    with pytest.raises(ValueError):
        apply_length_use(fitted, "shrink")


@dataclass(frozen=True)
class _Stored:
    """What ``apply_line_edit`` needs of a stored response: the result, and a markdown it re-renders."""

    result: TailoredResume
    markdown: str = ""


def test_a_line_edited_while_restored_is_kept_when_the_cut_is_applied_again() -> None:
    fitted = fit_to_pages(_settled(_resume(_WITH_OLD)), measure=_measure)
    restored = restore_cut(fitted)
    dogwood = next(section for section in restored.sections if section.heading == "experience").entries[3]
    assert dogwood.bullets[0].id is not None
    edited = apply_line_edit(_Stored(restored), dogwood.bullets[0].id, "Did the first thing with Python and SQL.")  # type: ignore[arg-type]
    again = cut_again(edited.result)
    assert again.length is not None and again.length.cut[0].entry.bullets[0].text == "Did the first thing with Python and SQL."
    assert again.sections == fitted.sections  # what is shown is what the cut showed before
    assert restore_cut(again).sections == edited.result.sections  # and the edit comes back with the role


# --- the stored contract --------------------------------------------------------------------------


def test_the_length_record_round_trips_and_an_older_result_parses_as_before() -> None:
    fitted = fit_to_pages(_settled(_resume(_WITH_OLD)), measure=_measure)
    as_json = fitted.to_json()
    assert TailoredResume.from_json(as_json) == fitted
    assert set(as_json["length"]) == {"max_pages", "pages", "full_pages", "status", "cut", "trimmed"}  # type: ignore[arg-type]
    assert as_json["length"]["cut"][0]["role"] == "Junior Engineer — Dogwood (2011–2014)"  # type: ignore[index]

    older = {key: value for key, value in as_json.items() if key != "length"}
    assert TailoredResume.from_json(older).length is None and "length" not in TailoredResume.from_json(older).to_json()

    for bad in (
        {**as_json["length"], "status": "trimmed"},  # type: ignore[dict-item]
        {**as_json["length"], "max_pages": 0},  # type: ignore[dict-item]
        {**as_json["length"], "pages": "2"},  # type: ignore[dict-item]
        {**as_json["length"], "cut": [], "trimmed": []},  # type: ignore[dict-item]  # "cut" with nothing left out
        {**as_json["length"], "status": "over"},  # type: ignore[dict-item]  # "over" never holds a cut
        {**as_json["length"], "extra": 1},  # type: ignore[dict-item]
    ):
        with pytest.raises(FindJobsContractError):
            TailoredResume.from_json({**as_json, "length": bad})


# --- 0.1.10.9 master P5: "older" is said only of old roles' bullets --------------------------------


def test_bullets_cut_from_a_recent_role_are_not_called_older() -> None:
    """The master resume's fit also takes the lowest-value lines of RECENT roles (``fit_by_cuts``): those are "bullets"."""

    from datetime import date

    from gigai.scout.tailor_length import TrimmedRole, fit_by_cuts, trimmed_bullets_note

    settled = _settled(_resume(_RECENT))
    experience = next(section for section in settled.sections if section.heading == "experience")
    newest, second = experience.entries[0], experience.entries[1]
    cuts = [("bullet", line.id) for line in (newest.bullets[-1], second.bullets[-1], second.bullets[-2])]
    total = sum(len(entry.bullets) for entry in experience.entries)

    def measure(result: TailoredResume) -> int:  # 3 pages until all three lines are cut
        shown = sum(len(entry.bullets) for section in result.sections for entry in section.entries)
        return 2 if shown <= total - 3 else 3

    fitted = fit_by_cuts(settled, cuts, measure=measure)
    length = fitted.length
    assert length is not None and length.status == "cut" and length.trimmed_count() == 3 and not length.cut
    note = length_note(length)
    assert "older bullet" not in note, note
    assert note == f"Cut for length (3 pages -> 2): 3 bullets (1 of {role_label(newest)}, 2 of {role_label(second)})."
    assert length_note(restore_cut(fitted).length).startswith("Put back (was cut for length): 3 bullets (")

    # One recent role among old ones: still not "older". Every role old: "older", as before.
    old = TrimmedRole("L90", "Engineer — Cedar (2014–2016)", (newest.bullets[0],))
    recent = TrimmedRole("L91", "Staff Engineer — Alder (2024–Present)", (newest.bullets[1],))
    today = date(2026, 10, 4)
    assert trimmed_bullets_note([old], today=today) == "1 older bullet (1 of Engineer — Cedar (2014–2016))"
    assert trimmed_bullets_note([old, recent], today=today) == "2 bullets (1 of Engineer — Cedar (2014–2016), 1 of Staff Engineer — Alder (2024–Present))"
    # "Old" is the length rule's own: ended more than 8 years ago; a role with no year, or an ongoing one, never is.
    edge = TrimmedRole("L92", "Engineer — Birch (2016–2018)", (newest.bullets[0],))
    assert trimmed_bullets_note([edge], today=date(2026, 10, 4)).startswith("1 bullet (")
    assert trimmed_bullets_note([edge], today=date(2027, 1, 1)).startswith("1 older bullet (")
    assert trimmed_bullets_note([TrimmedRole("L93", "Consultant — Elm", (newest.bullets[0],))], today=today).startswith("1 bullet (")
