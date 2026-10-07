"""0.1.11.5 item 1 (c): the pick is BY SCORE UP TO A CAP OF 20 BULLETS, and it never counts pages.

The operator's case, on synthetic data: real picks stopped at 14 bullets "cut for length (3 pages to 2)", because the
pick fitted itself to 2 pages with one PDF layout a trial and kept room for a header.  The pick must not know the page
limit: the user fits the page, with the spacing of the job's preview (part (a)).

THE END OUTCOME, on a master in the shape of a real one (``tests/support/pick_cap_fixture.py``: five roles of nine
lines, four earlier roles, a project, two degrees, 36 skills: 51 candidate bullets), for the assessment's pick AND for
the code selector's own selection:

- the resume holds exactly ``MAX_PICK_BULLETS`` (20) bullets, every must-cover line among them, and every block is
  there: the summary, the roles, the earlier roles by their heading line, the project, the whole Skills section, both
  degrees;
- nothing is "cut for length": no length record on the resume, no cut in Picked / Left out, no page-driven conflict;
  what the cap left out is under Left out, by a code and a sentence that name the cap;
- NO PAGE IS COUNTED AND NOTHING IS RENDERED: the pick is made with every layout call of the PDF module set to raise;
- the PDF of that pick, WITH a header, at the automatic spacing (part (a)'s render) is 2 pages on this fixture;
- a must-cover line the model ranked last stays while better-ranked lines go; a pinned line stays;
- a master with fewer candidate bullets than the cap shows them all;
- a smaller cap only leaves out more of the same order;
- "SHORTEN AUTOMATICALLY" IS RETIRED: the step reads nothing, writes nothing and answers one plain sentence that
  points at the spacing slider and at removing a point;
- A 3-PAGE RENDER IS NEVER "NOT READY": a pick stored before 0.1.11.5 whose only conflicts came from the page limit
  reads as ready (the stored file is not written), and a real conflict beside them still holds it.

No model: every pick here is hand-written.  Invented person, employers and products.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path

import pytest

from gigai.scout import master_selection as ms
from gigai.scout import job_actions, pick, resume_pdf, suggestions
from gigai.scout import tailor_master as tm
from gigai.scout.find_jobs.assess_contracts import AssessmentBody, AssessmentPick
from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass, RequirementMatrixRow, Verdict
from gigai.scout.master_resume import Master, parse_master
from gigai.scout.resume_display import form_header, parse_header_form
from gigai.scout.tailored_resume import EARLIER_HEADING, render_markdown

from tests.support import pick_cap_fixture as fx

TODAY = date(2026, 10, 6)
STAMP = datetime(2026, 10, 6, tzinfo=timezone.utc)
MASTER = parse_master(fx.master_markdown())
PROFILE = ms.SelectionProfile(titles=("Staff Software Engineer",))
POSTING = ms.SelectionPosting(fx.TITLE, fx.POSTING, "Larkspur Table Systems", "Remote")
#: A header at its largest (a contact line that wraps): what the PDF prints over the pick.
FORM = {
    "name": "Zora Quillfeather", "email": "zora.quillfeather@example.invalid", "phone": "+1 (555) 010-0142", "location": "Nowhere Springs, Colorado",
    "work_authorization": "VISA: H1B", "linkedin": "linkedin.com/in/zora-quillfeather-staff-engineer",
    "links": [{"label": "GitHub", "url": "github.com/zora-quillfeather"}],
}
#: The technology each must-have row of the posting names, in the posting's order.
MUST_WORDS = ("Python", "Kafka", "PostgreSQL", "Kubernetes", "Terraform", "React")


def _bullet_ids(master: Master) -> list[str]:
    return [item.id for item in master.items.values() if item.kind == "bullet" and item.section in ("experience", "projects")]


def _naming(master: Master, word: str, *, last: bool = False) -> str:
    found = [item.id for item in master.items.values() if item.kind == "bullet" and item.section == "experience" and word in item.text]
    return found[-1] if last else found[0]


def _row(number: int, text: str, *sources: str, klass: str = "hard") -> RequirementMatrixRow:
    return RequirementMatrixRow(text, (), MatrixStatus("met"), RequirementClass(klass), id=f"req-{number:06d}", class_basis="Requirements", sources=sources)


#: Each must-have row rests on ONE line of the master: the LAST line that names its technology (a late line of a late
#: role: by rank alone it would be the first to go).
MUST_COVER = tuple(_naming(MASTER, word, last=True) for word in MUST_WORDS)
ROWS = tuple(_row(number, text, source) for number, (text, source) in enumerate(zip(fx.REQUIREMENTS, MUST_COVER), 1))
#: The model's pick: six lines of each role in turn and a line of the project, with the must-cover lines put LAST of all.
_TAKEN = [*(fx.line_id(role, line) for line in range(6) for role in range(5)), "p1-00"]
PICK_LINES = tuple([item_id for item_id in _TAKEN if item_id not in MUST_COVER] + list(MUST_COVER))


def _body(lines: tuple[str, ...] | None, rows: tuple[RequirementMatrixRow, ...] = ROWS) -> AssessmentBody:
    chosen = None if lines is None else AssessmentPick("sum-1", ("experience", "projects"), lines)
    return AssessmentBody(rows, (), (), verdict=Verdict.MATCHED_ABOVE_THRESHOLD, pick=chosen)


def _settle(lines: tuple[str, ...] | None = PICK_LINES, *, master: Master = MASTER, profile: ms.SelectionProfile = PROFILE, **more) -> pick.Settled:
    return pick.settle(master, _body(lines), None, TODAY, profile=profile, posting=POSTING, **more)


def _shown(settled: pick.Settled) -> list[str]:
    """The master ids of the bullets the resume shows under its roles and projects."""

    return [
        item for section in settled.result.sections if section.heading in ("experience", "projects")
        for entry in section.entries for line in entry.bullets if (item := tm.line_item_id(line)) is not None
    ]


@pytest.fixture
def no_layout(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every way the PDF module lays a resume out is set to raise: a pick made under this renders nothing and counts no page."""

    called: list[str] = []

    def refuse(name: str):
        def raises(*_args: object, **_kwargs: object) -> None:
            called.append(name)
            raise AssertionError(f"a pick must lay out nothing: {name} was called")

        return raises

    for name in ("_compile", "_compile_images", "_end", "_estimate", "_render", "pages_at", "fewest_pages", "measure_markdown", "render_markdown_pdf"):
        monkeypatch.setattr(resume_pdf, name, refuse(name))
    monkeypatch.setattr(tm, "measure_pages", refuse("tailor_master.measure_pages"))
    monkeypatch.setattr(ms, "_shipped_measure", refuse("master_selection._shipped_measure"))
    return called


def _assert_whole_and_capped(settled: pick.Settled, *, picked_by: str) -> None:
    shown = _shown(settled)
    assert settled.picked_by == picked_by
    assert len(_bullet_ids(MASTER)) == 51, "the fixture: far more candidate bullets than a resume shows"
    assert len(shown) == ms.MAX_PICK_BULLETS == 20 == settled.max_bullets, "the pick holds exactly the cap"
    assert len(set(shown)) == 20
    # Every block is there.
    markdown = render_markdown(settled.result)
    assert "## Summary" in markdown and "Staff engineer with fourteen years" in markdown
    for company, *_rest in fx.ROLES:
        assert company in markdown, f"the role {company} is on the resume"
    block = markdown[markdown.index(f"### {EARLIER_HEADING}"):].split("\n## ")[0]
    for company, title, dates in fx.EARLIER:
        assert f"{title}, {company} | {dates}" in block, "an earlier role is listed by its one heading line"
    assert "## Projects" in markdown and fx.PROJECT[0] in markdown
    assert "## Education" in markdown and "Example State University" in markdown and "Example Institute of Technology" in markdown
    skills = next(line for line in markdown.splitlines() if line.startswith("- Python") or "Capacity planning" in line)
    assert all(name.strip() in skills for name in fx.SKILLS.split(", ")), "the Skills section is whole: no skill is cut"
    assert [skill.name for skill in settled.record.skills_left_out] == []
    # Nothing is "cut for length", and no page was counted.
    assert settled.result.length is None, "a pick carries no length record: there is nothing to Restore"
    assert settled.record.cut_for_length == () and settled.record.to_json()["counts"]["cut_for_length"] == 0
    assert settled.pages is None and settled.layouts == 0 and settled.selection_json(made_at="t", result_digest=None, master_revision_id=None)["pages"] is None
    assert settled.conflicts == () and settled.check.ready
    reasons = [line.reason for line in (*settled.record.picked, *settled.record.left_out)]
    assert not [reason for reason in reasons if "cut for length" in reason or "page" in reason.lower()], "no reason speaks of length or pages"
    # What the cap left out is under Left out, and says so.
    over = [line for line in settled.record.left_out if line.code == "over_cap"]
    assert over and all("20 best lines" in line.reason for line in over)


def test_the_assessments_pick_holds_exactly_twenty_bullets_every_must_cover_line_and_every_block_with_no_layout(no_layout: list[str]) -> None:
    settled = _settle()
    _assert_whole_and_capped(settled, picked_by="model")
    shown = _shown(settled)
    assert set(MUST_COVER) <= set(shown), "every must-cover line is on the resume"
    # MUST-COVER OVER RANK: the model put the six must-cover lines last of its pick; every line left out is a better-ranked one.
    # (Left out: the lines over the cap, and an older role's lines past its own limit of three.)
    left = {line.id: line.code for line in settled.record.left_out if line.id in PICK_LINES}
    assert len(left) == len(PICK_LINES) - 20 and set(left.values()) == {"over_cap", "old_role_limit"} and not set(left) & set(MUST_COVER)
    assert all(PICK_LINES.index(item_id) < PICK_LINES.index(MUST_COVER[0]) for item_id in left)
    assert settled.check.lost() == () and pick.checks(MASTER, settled, pick.suggestions.requirement_rows(ROWS)).fits
    assert no_layout == []


def test_the_code_selectors_own_selection_holds_exactly_twenty_bullets_and_every_block_with_no_layout(no_layout: list[str]) -> None:
    settled = _settle(None)  # no pick in the answer: the fallback
    assert settled.fallback == pick.FALLBACK_NO_PICK
    _assert_whole_and_capped(settled, picked_by="code")
    assert set(MUST_COVER) <= set(_shown(settled)), "the lines the assessment's rows cite are the selector's must-cover lines"
    selected = ms.select(MASTER, PROFILE, POSTING, today=TODAY)
    assert sum(len(bullets) for entry_id, bullets in selected.entries.items() if MASTER.entries[entry_id].section != "education") == 20
    assert (selected.pages, selected.pages_before_fit, selected.layout_queries, selected.cut_for_length, selected.fits) == (None, None, 0, (), True)
    assert len(selected.skills) == len(MASTER.skills()) and len(selected.earlier) == len(fx.EARLIER)
    assert no_layout == []


def test_the_pdf_of_the_capped_pick_with_a_header_is_two_pages_at_the_automatic_spacing() -> None:
    """Part (a)'s render of part (c)'s pick: the page is fitted by the spacing, not by the pick."""

    header = form_header(parse_header_form(FORM))
    for settled in (_settle(), _settle(None)):
        rendered = resume_pdf.render_markdown_pdf(render_markdown(settled.result), header, timestamp=STAMP)
        assert rendered.pages == 2, f"20 bullets, a header and every block: 2 pages at the automatic spacing ({rendered.spacing_scale:g})"
        assert resume_pdf.SPACING_MIN <= rendered.spacing_scale <= 1.0
        # At the loosest spacing of the default layout it is 3 pages, and that is the user's to see and change.
        assert resume_pdf.render_markdown_pdf(render_markdown(settled.result), header, timestamp=STAMP, spacing_scale=1.0, auto_fit=False).pages == 3


def test_a_pinned_line_stays_under_the_cap_whatever_its_rank() -> None:
    # A late line of the newest role that the model did not pick and no requirement rests on.
    pinned = next(item_id for item_id in (fx.line_id(0, line) for line in (8, 7, 6)) if item_id not in MUST_COVER)
    assert pinned not in PICK_LINES
    profile = ms.SelectionProfile(titles=PROFILE.titles, pins=(pinned,))
    settled = _settle(profile=profile)
    shown = _shown(settled)
    assert pinned in shown and set(MUST_COVER) <= set(shown) and len(shown) == 20 and settled.conflicts == ()
    assert next(line.code for line in settled.record.picked if line.id == pinned) == pick.ADDED_PINNED
    # The code selector keeps it too.
    by_code = _settle(None, profile=profile)
    assert pinned in _shown(by_code) and len(_shown(by_code)) == 20 and by_code.conflicts == ()


def test_a_master_with_fewer_candidate_bullets_than_the_cap_shows_them_all() -> None:
    small = parse_master(fx.master_markdown(per_role=4, roles=3, earlier=False))  # 3 roles x 4 lines + 2 project lines
    every = set(_bullet_ids(small))
    assert len(every) == 14 < ms.MAX_PICK_BULLETS
    by_code = _settle(None, master=small)
    assert set(_shown(by_code)) == every, "the code selector shows every candidate line: nothing is left out for a cap of its own"
    assert by_code.result.length is None and not [line for line in by_code.record.left_out if line.id in every]
    # A model's pick of eight of them is kept whole (nothing of it is left out) and filled toward the cap.
    lines = tuple(fx.line_id(role, line) for line in range(3) for role in range(3))[:8]
    by_model = _settle(lines, master=small)
    assert by_model.picked_by == "model" and set(lines) <= set(_shown(by_model)) and len(_shown(by_model)) <= ms.MAX_PICK_BULLETS


def test_a_smaller_cap_leaves_out_more_of_the_same_order_and_the_must_cover_lines_last() -> None:
    full, shorter = _shown(_settle()), _shown(_settle(max_bullets=19))
    assert len(shorter) == 19 and set(shorter) < set(full), "one line fewer, and no line comes in"
    assert set(MUST_COVER) <= set(shorter)
    # Down to the must-cover lines and each recent role's own line: still every must-cover line, still no conflict.
    tight = _settle(max_bullets=8)
    assert set(MUST_COVER) <= set(_shown(tight)) and len(_shown(tight)) == 8 and tight.conflicts == ()
    # Below the must-cover lines themselves: the loss is said, never silent.
    over = _settle(max_bullets=4)
    assert len(_shown(over)) == 4 and {conflict.code for conflict in over.conflicts} == {pick.CONFLICT_EVIDENCE}
    assert not over.check.ready and {line.code for line in over.record.left_out if line.id in MUST_COVER} == {"cut_conflict"}


def _stored_record(path: Path, conflicts: list[dict[str, object]], reasons: list[dict[str, object]]) -> suggestions.SuggestionRecord:
    """A suggestion record as a pick of before 0.1.11.5 stored it: page-driven conflicts, and a gate that is not ready for them."""

    record = suggestions.SuggestionRecord(
        profile_id="prof_1", job_identity="https://jobs.example.invalid/larkspur/1", created_at="2026-10-01T10:00:00Z", updated_at="2026-10-01T10:00:00Z",
        stored_path=str(path), basis={"kind": "master"}, gate={"decision": "suggest", "ready": False, "reasons": reasons},
        selection={"picked_by": "model", "selector_version": "sel-6", "pages": 3, "max_pages": 2, "conflicts": conflicts},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_json()), encoding="utf-8")
    return record


def test_a_stored_pick_that_only_the_page_limit_held_reads_as_ready_and_a_real_conflict_still_holds(tmp_path: Path) -> None:
    conflict = {"code": suggestions.REASON_CONFLICT, "requirement": None}
    page = [{"code": code, "requirement": None, "lines": [], "cut": code != "over_page_limit"} for code in sorted(suggestions.PAGE_CONFLICTS)]
    assert {item["code"] for item in page} == {"skills_do_not_fit", "earlier_roles_do_not_fit", "over_page_limit"}

    path = tmp_path / "suggestions" / "prof_1" / "only-pages.json"
    _stored_record(path, page, [conflict, conflict, conflict])
    before = path.read_bytes()
    read = suggestions.read_record(path)
    assert read is not None and read.gate == {"decision": "suggest", "ready": True, "reasons": []}, "3 pages is the user's to fit: the resume is ready"
    assert read.selection is not None and len(read.selection["conflicts"]) == 3, "the stored selection is as it was (a re-pick drops them)"
    assert path.read_bytes() == before, "reading writes nothing"

    # A must-cover line that is not shown is a real conflict: beside the page-driven ones it still holds the resume.
    evidence = {"code": pick.CONFLICT_EVIDENCE, "requirement": "req-000001", "lines": ["r1-00"], "cut": True}
    held = tmp_path / "suggestions" / "prof_1" / "evidence-too.json"
    _stored_record(held, [*page, evidence], [conflict, conflict, conflict, {"code": suggestions.REASON_CONFLICT, "requirement": "req-000001"}])
    still = suggestions.read_record(held)
    assert still is not None and still.gate["ready"] is False
    assert still.gate["reasons"] == [{"code": suggestions.REASON_CONFLICT, "requirement": "req-000001"}]

    # The check itself: a page-driven conflict is no reason, a real one is; and what a re-pick of today makes has none.
    rows = suggestions.requirement_rows(ROWS)
    printed = _settle().printed
    assert suggestions.check_selection(rows, printed, conflicts=[pick.PickConflict(code) for code in sorted(suggestions.PAGE_CONFLICTS)]).ready
    assert not suggestions.check_selection(rows, printed, conflicts=[pick.PickConflict(pick.CONFLICT_PIN, None, ("r1-08",))]).ready
    kept, reasons = suggestions.live_selection({"conflicts": page}, printed)
    assert reasons == () and kept is not None and len(kept["conflicts"]) == 3


def test_shorten_is_retired_it_reads_and_writes_nothing_and_says_what_to_do_instead(tmp_path: Path) -> None:
    """The coordinator's decision for 0.1.11.5: the slider and Remove-a-point replace "Shorten automatically"."""

    home = tmp_path / "home"  # not even a GigAI home: the refusal comes before anything is read
    with pytest.raises(pick.PickError) as refused:
        pick.shorten_stored(home, tmp_path / "project", "prof_1", "https://jobs.example.invalid/larkspur/1", now="2026-10-06T00:00:00Z")
    with pytest.raises(job_actions.JobActionError) as by_route:  # what POST /api/job-resumes/pick and `resume pick --shorten` call
        job_actions.pick_action(home, tmp_path / "project", "https://jobs.example.invalid/larkspur/1", "shorten")
    for error in (refused.value, by_route.value):
        sentence = str(error)
        assert error.code == pick.REFUSED_SHORTEN_RETIRED == "shorten_retired"
        assert sentence == pick.MESSAGES["shorten_retired"] and "spacing slider" in sentence and "remove a point" in sentence
        assert "nothing was changed" in sentence and "`" not in sentence and "shorten_retired" not in sentence and "pick." not in sentence
    assert not home.exists(), "nothing was created"
    assert "no_resume_to_shorten" not in pick.MESSAGES and "resume_short_already" not in pick.MESSAGES
