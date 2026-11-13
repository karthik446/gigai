"""0.3.2 (gigai-39o): a resume PICKED BY CODE fits the page limit at the default spacing.

The operator's case (2026-11-12), on synthetic data: with no usable model pick, Scout's own rules stored a draft that
printed on 3 pages at the default spacing, nothing listed as cut for length. A draft picked by code is now cut to
``MAX_PAGES`` (2) at ``SPACING_DEFAULT`` before it is stored:

- the lowest-scored lines go first and are listed under Cut for length, each with its reason; a line that backs a
  met must-have row and a pinned line stay;
- a draft that already fits is stored exactly as it was made;
- when the lines that always stay are over the limit on their own, every free cut stays applied, the record says it
  is over (an ``over_budget`` conflict) and the draft is still ready (the page limit never holds a resume back);
- a renderer that cannot lay the draft out changes nothing;
- the fit leaves room for a header of a title line and a contact line that wraps onto a second line
  (``tm.DRAFT_HEADER_LINES``): with that header the PDF prints the fitted draft on 2 pages at the default spacing,
  as the estimate says, and a draft that already had that room is cut as before;
- a model's pick is never cut for a page (``test_pick_cap``).

And the left-out reason of a role's lines past its share no longer names a line limit the role does not keep
(the "11 lines vs limit 9" report): it says what is true, the line scores lower than the lines the role shows.

No model: every pick here is the fallback of an answer with no pick. Invented person, employers and products.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json

import pytest

from gigai.scout import master_selection as ms
from gigai.scout import pick, resume_pdf
from gigai.scout import tailor_master as tm
from gigai.scout.find_jobs.assess_contracts import AssessmentBody
from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass, RequirementMatrixRow, Verdict
from gigai.scout.master_resume import Master, parse_master
from gigai.scout.resume_display import ContactItem, PdfHeader
from gigai.scout.resume_pdf import _body, _render
from gigai.scout.tailor_length import STATUS_CUT

from tests.support import pick_cap_fixture as fx

TODAY = date(2026, 10, 6)
PROFILE = ms.SelectionProfile(titles=("Staff Software Engineer",))
POSTING = ms.SelectionPosting(fx.TITLE, fx.POSTING, "Larkspur Table Systems", "Remote")
#: One more printed line on every role line: the fixture's draft then runs to 3 pages at the default spacing.
LONG = " Worked with product, design, finance and support on every step and wrote the runbook the on-call team still uses today."
LONG_MASTER = parse_master("\n".join(
    line.replace(" <!-- id:r", LONG + " <!-- id:r", 1) if line.startswith("- ") and "id:r" in line else line
    for line in fx.master_markdown().splitlines()
))
SHORT_MASTER = parse_master(fx.master_markdown())
#: A little more on every role line: the draft fits 2 pages with room for 2 header lines, not for 3.
WRAP = " Partnered with product and finance on the plan and wrote the design doc."
WRAP_MASTER = parse_master("\n".join(
    line.replace(" <!-- id:r", WRAP + " <!-- id:r", 1) if line.startswith("- ") and "id:r" in line else line
    for line in fx.master_markdown().splitlines()
))
STAMP = datetime(2026, 10, 6, tzinfo=timezone.utc)
#: Invented header values on reserved domains.  A title line and ONE contact line that fits the page.
ONE_LINE_HEADER = PdfHeader("Avery Example", "Staff Software Engineer", (
    ContactItem("Denver, CO", None), ContactItem("avery@example.com", "mailto:avery@example.com"), ContactItem("555-0100", None),
    ContactItem("github.com/avery-example", "https://github.com/avery-example"),
))
#: A title line and a contact line that wraps onto a second line (six items and a work authorization).
WRAPPED_HEADER = PdfHeader("Avery Quinn Example-Longname", "Staff Software Engineer, Platform and Infrastructure", (
    ContactItem("Denver, Colorado, United States", None), ContactItem("avery.quinn.example@example.com", "mailto:avery@example.com"),
    ContactItem("+1 555-0100", None), ContactItem("github.com/avery-quinn-example", "https://github.com/avery-quinn-example"),
    ContactItem("linkedin.com/in/avery-quinn-example", "https://linkedin.com/in/avery-quinn-example"),
    ContactItem("averyquinn.example.com", "https://averyquinn.example.com"),
), work_authorization="US citizen, no sponsorship needed")


def _row(number: int, source: str) -> RequirementMatrixRow:
    return RequirementMatrixRow(f"Requirement {number}", (), MatrixStatus("met"), RequirementClass("hard"), id=f"req-{number:06d}", class_basis="Requirements", sources=(source,))


def _settle(master: Master, *, rows: tuple[RequirementMatrixRow, ...] = (), profile: ms.SelectionProfile = PROFILE) -> pick.Settled:
    body = AssessmentBody(rows, (), (), verdict=Verdict.MATCHED_ABOVE_THRESHOLD, pick=None)
    return pick.settle(master, body, None, TODAY, profile=profile, posting=POSTING)


def _unfitted(monkeypatch: pytest.MonkeyPatch, master: Master, **more) -> pick.Settled:
    """The same fallback as made before 0.3.2: the draft as ``code_only`` made it, not fitted."""

    with monkeypatch.context() as patch:
        patch.setattr(tm, "fit_draft", lambda result, *_args, **_kwargs: result)
        return _settle(master, **more)


def _shown_bullets(settled: pick.Settled) -> list[str]:
    return [
        item for section in settled.result.sections if section.heading in ("experience", "projects")
        for entry in section.entries for line in entry.bullets if (item := tm.line_item_id(line)) is not None
    ]


def _values(master: Master, profile: ms.SelectionProfile = PROFILE) -> dict[str, float]:
    candidates = tm.job_candidates(master, profile, POSTING, mode=tm.MODE_VIEW, today=TODAY, max_bullets=ms.MAX_PICK_BULLETS)
    return dict(candidates.selected.values)


def _two_line_room(result) -> int:
    """The estimate before V13 read B: room for ``resume_pdf.HEADER_RESERVE_LINES`` (2) header lines."""
    return resume_pdf.pages_at(result, resume_pdf.SPACING_DEFAULT)


def _printed(result, header: PdfHeader) -> int:
    """The pages the PDF with ``header`` prints at the default spacing, held there (the job page's slider at 1.0)."""
    return _render(
        _body(result), header, company="Larkspur Table Systems", timestamp=STAMP, spacing_scale=resume_pdf.SPACING_DEFAULT, auto_fit=False,
        count_pages=True, max_pages=tm.MAX_PAGES, fixed=True,
    ).pages


def test_a_code_draft_over_two_pages_is_cut_to_two_by_its_lowest_scored_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    before = _unfitted(monkeypatch, LONG_MASTER)
    assert before.picked_by == "code" and before.fallback == pick.FALLBACK_NO_PICK
    assert tm.draft_pages(before.result) == 3 and before.record.cut_for_length == (), "the fixture: the draft as made prints on 3 pages"

    settled = _settle(LONG_MASTER)
    assert (settled.picked_by, settled.fallback) == ("code", pick.FALLBACK_NO_PICK)
    assert tm.draft_pages(settled.result) <= tm.MAX_PAGES and resume_pdf.pages_at(settled.result, resume_pdf.SPACING_DEFAULT) <= 2
    length = settled.result.length
    assert length is not None and length.status == STATUS_CUT and length.max_pages == 2 and length.full_pages == 3
    # Every line it cut scores below every line it kept.
    values = _values(LONG_MASTER)
    kept = set(_shown_bullets(settled))
    gone = set(_shown_bullets(before)) - kept
    assert gone and kept < set(_shown_bullets(before))
    assert max(values[item] for item in gone) < min(values[item] for item in kept)
    # Cut for length lists them, each with a reason that names the page limit; no conflict holds the draft.
    cut = settled.record.cut_for_length
    assert cut and {line.code for line in cut} <= {"cut_lowest_value", "cut_role_dropped"}
    assert all(line.reason.startswith("cut to fit 2 pages") for line in cut)
    assert {line.id for line in cut if line.code == "cut_lowest_value"} <= gone
    assert settled.conflicts == () and settled.check.ready
    # Every role heading stays: an older role that lost its last line is still listed.
    headings = [tm.line_item_id(entry.heading[0]) for section in settled.result.sections if section.heading == "experience" for entry in section.entries]
    assert headings == [tm.line_item_id(entry.heading[0]) for section in before.result.sections if section.heading == "experience" for entry in section.entries]
    # What is stored says it: the length record on the resume, the cut in Picked / Left out's counts.
    stored = json.loads(json.dumps(settled.result.to_json()))
    assert stored["length"]["status"] == STATUS_CUT and stored["length"]["pages"] <= 2
    record = settled.record.to_json()
    assert record["counts"]["cut_for_length"] == len(cut) > 0 and len(record["cut_for_length"]) == len(cut)
    assert settled.layouts >= 2 and settled.pages is None


def test_a_must_have_rows_evidence_and_a_pinned_line_stay_when_the_draft_is_cut() -> None:
    first = _settle(LONG_MASTER)
    gone = [line.id for line in first.record.cut_for_length if line.code == "cut_lowest_value"]
    assert len(gone) >= 2, "the fixture: at least two lines go when nothing protects them"
    evidence, pinned = gone[0], gone[1]
    profile = ms.SelectionProfile(titles=PROFILE.titles, pins=(pinned,))
    settled = _settle(LONG_MASTER, rows=(_row(1, evidence),), profile=profile)
    shown = set(_shown_bullets(settled))
    assert {evidence, pinned} <= shown, "a must-have row's evidence and a pin are never cut for length"
    assert not {evidence, pinned} & {line.id for line in settled.record.cut_for_length}
    assert settled.result.length is not None and settled.result.length.status == STATUS_CUT and settled.result.length.pages <= 2
    assert settled.conflicts == () and settled.check.ready


def test_a_code_draft_that_fits_already_is_stored_as_it_was_made(monkeypatch: pytest.MonkeyPatch) -> None:
    before = _unfitted(monkeypatch, SHORT_MASTER)
    settled = _settle(SHORT_MASTER)
    assert tm.draft_pages(settled.result) <= 2
    assert settled.result == before.result and settled.result.length is None
    assert settled.record.cut_for_length == () and settled.printed == before.printed and settled.layouts == 1


def test_a_draft_still_over_after_every_free_cut_keeps_what_always_stays_says_so_and_is_still_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tm, "draft_pages", lambda _result: 5)
    settled = _settle(LONG_MASTER)
    length = settled.result.length
    assert length is not None and length.over() and length.pages == 5 and length.max_pages == 2
    assert settled.record.cut_for_length and len(_shown_bullets(settled)) < ms.MAX_PICK_BULLETS, "every free cut stays applied"
    assert "over_budget" in {conflict.kind for conflict in settled.record.conflicts}
    assert settled.conflicts == () and settled.check.ready, "the page limit never holds a resume back"


def test_a_renderer_that_cannot_lay_the_draft_out_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    before = _unfitted(monkeypatch, LONG_MASTER)

    def broken(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("no renderer")

    monkeypatch.setattr(resume_pdf, "pages_at", broken)
    settled = _settle(LONG_MASTER)
    assert settled.result == before.result and settled.result.length is None
    assert settled.record.cut_for_length == () and settled.printed == before.printed
    assert settled.conflicts == () and settled.check.ready and settled.layouts == 1


def test_a_code_draft_leaves_room_for_a_title_and_a_contact_line_that_wraps(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(tm, "draft_pages", _two_line_room)
        before = _settle(WRAP_MASTER)
    assert _two_line_room(before.result) == 2 and _printed(before.result, WRAPPED_HEADER) == 3, (
        "the fixture: room for 2 header lines lets through a draft the wrapped header prints on 3 pages"
    )
    # The fit's estimate counts what that PDF prints, before the cut and after it.
    assert tm.draft_pages(before.result) == 3
    settled = _settle(WRAP_MASTER)
    assert len(settled.record.cut_for_length) > len(before.record.cut_for_length)
    assert tm.draft_pages(settled.result) == _printed(settled.result, WRAPPED_HEADER) == 2
    assert _printed(settled.result, ONE_LINE_HEADER) == 2 and settled.conflicts == () and settled.check.ready


def test_a_code_draft_with_room_for_the_wrapped_header_is_cut_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(tm, "draft_pages", _two_line_room)
        before = _settle(LONG_MASTER)
    settled = _settle(LONG_MASTER)
    assert settled.record.cut_for_length, "the fixture: a draft the fit cuts"
    assert settled.result == before.result and settled.record.cut_for_length == before.record.cut_for_length
    assert _printed(settled.result, ONE_LINE_HEADER) == _printed(settled.result, WRAPPED_HEADER) == 2


def test_a_roles_lines_past_its_share_say_they_score_lower_and_name_no_limit() -> None:
    master = parse_master(fx.master_markdown(per_role=14, roles=2, earlier=False))
    selected = ms.select(master, PROFILE, POSTING, today=TODAY)
    limited = [line for line in selected.lines if line.code == "role_limit"]
    assert limited, "the fixture: a role of 14 lines leaves some out for its share"
    for line in limited:
        assert line.reason.startswith("scores lower for ") and line.reason.endswith(" than the lines this role shows"), line.reason
        assert not any(char.isdigit() for char in line.reason), line.reason
    settled = _settle(master)
    for line in settled.record.left_out:
        assert "over the limit of" not in line.reason and "limit of" not in line.reason, line.reason
