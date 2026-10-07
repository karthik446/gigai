"""0.1.11 N3 (SPEC section 3): ``pick.settle``, what code makes of the assessment's pick.

No model: every pick here is hand-written, the way the eval's fixtures are.  Two masters, both synthetic:

- a small one written here, with ids a person can read (``a1`` .. ``a6``, ``b1`` .. ``b6``), settled under a small
  CAP of bullets (``max_bullets``), so the order of 3.2 is checked line by line;
- the pick eval's medium master (``tests/evals/fixtures/pick``, an invented person), under the shipped cap:
  the seven picks the spec names (a good one, unknown ids, Skills lines, the current role omitted, only old lines,
  every line of the master, empty).

What is pinned: V1 to V8, what code fixes whatever the model returned, the protected lines and the order lines are
left out in, that the Other lines, the Skills and the roles' heading lines are never left out, the conflict codes, the
fill, every fallback code, that every line of a selection is a copy of a master line, and (0.1.11.5 item 1c) that a
selection counts no page and needs no renderer.
"""

from __future__ import annotations

from datetime import date

import pytest

from gigai.scout import assess_master, pick, suggestions
from gigai.scout import master_selection as ms
from gigai.scout import tailor_master as tm
from gigai.scout.find_jobs.assess_contracts import AssessmentBody, AssessmentPick
from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass, RequirementMatrixRow, Verdict
from gigai.scout.master_resume import parse_master, skill_names
from gigai.scout.tailored_resume import shown_text
from tests.evals import run_pick_eval as ev

TODAY = date(2026, 10, 5)
MASTER = parse_master("""## Summary

- Platform engineer with ten years on Go services and Kubernetes. <!-- id:sum-a -->
- Engineering lead who grows teams and ships platforms. <!-- id:sum-b -->

## Experience

### Alpha Systems <!-- id:r-alpha -->
Staff Engineer | Jan 2023 - Present
- Built the Go control plane for the deploy platform. <!-- id:a1 -->
- Cut deploy time 40% by rebuilding the rollout controller in Go. <!-- id:a2 -->
- Wrote the on-call handbook the platform team uses. <!-- id:a3 -->
- Ran the Kubernetes upgrade across 12 clusters with no downtime. <!-- id:a4 -->
- Maintained the Go services behind the rollout API. <!-- id:a5 -->
- Organised the weekly platform review. <!-- id:a6 -->

### Beta Labs <!-- id:r-beta -->
Senior Engineer | Mar 2020 - Dec 2022
- Wrote the billing export job. <!-- id:b1 -->
- Reviewed designs for the data team. <!-- id:b2 -->
- Operated Kafka for 30 services carrying 2 billion messages a day. <!-- id:b3 -->
- Built the internal status page. <!-- id:b4 -->
- Kept the release checklist up to date. <!-- id:b5 -->
- Wrote Terraform modules for the staging clusters. <!-- id:b6 -->

### Gamma Data <!-- id:r-gamma -->
Engineer | Jun 2012 - Jun 2015
- Built reporting jobs in Perl. <!-- id:c1 -->

## Projects

### driftwatch <!-- id:p-drift -->
- A Go tool that diffs Kubernetes manifests against a live cluster. <!-- id:p1 -->

## Skills

- Go, Kubernetes, Kafka, Terraform, Perl, COBOL <!-- id:s-1 -->

## Education

### Varnholt Technical University <!-- id:e-var -->
B.S. Computer Science | 2005 - 2009

## Other

- Speaker at a platform engineering meetup. <!-- id:o1 -->
- Volunteer mentor at a coding non-profit. <!-- id:o2 -->
""")
PROFILE = ms.SelectionProfile(titles=("Staff Platform Engineer",))
POSTING = ms.SelectionPosting(
    "Staff Platform Engineer", "Requirements:\n- Go in production\n- Kafka at scale\n\nNice to have:\n- Terraform\n", "Acme", "Remote",
)
EVERY_LINE = ("a1", "a2", "a3", "a4", "a5", "a6", "b1", "b2", "b3", "b4", "b5", "b6", "p1", "c1", "o1", "o2")
GO, KAFKA, TERRAFORM, UPGRADES = "req-000001", "req-000002", "req-000003", "req-000004"


def _row(row_id: str, requirement: str, klass: str, sources: tuple[str, ...], status: str = "met") -> RequirementMatrixRow:
    return RequirementMatrixRow(requirement, (), MatrixStatus(status), RequirementClass(klass), id=row_id, class_basis="Requirements", sources=sources)


ROWS = (
    _row(GO, "Go in production", "hard", ("a2", "a5")),
    _row(KAFKA, "Kafka at scale", "askable", ("b3",)),
    _row(TERRAFORM, "Terraform", "list_item", ("b6",)),
)


def _body(lines: tuple[str, ...] | None, *, rows: tuple[RequirementMatrixRow, ...] = ROWS, summary: str | None = "sum-a", order: tuple[str, ...] = ("experience", "projects")) -> AssessmentBody:
    chosen = None if lines is None else AssessmentPick(summary, order, lines)
    return AssessmentBody(rows, (), (), verdict=Verdict.MATCHED_ABOVE_THRESHOLD, pick=chosen)


def _bullets(result) -> list[str]:
    return [item for section in result.sections for entry in section.entries for line in entry.bullets if (item := tm.line_item_id(line)) is not None]


def _others(result) -> list[str]:
    return [item for section in result.sections if section.heading == "other" for line in section.lines if (item := tm.line_item_id(line)) is not None]


def _skills(result) -> list[str]:
    return [name for section in result.sections if section.heading == "skills" for line in section.lines for name in skill_names(line.text.lstrip("-*• ").strip())[1]]


def _settle(lines: tuple[str, ...] | None, cap: int = 99, *, profile: ms.SelectionProfile = PROFILE, **body):
    """The selection of a pick under a cap of ``cap`` bullets (0.1.11.5: the cap is the only limit; no page is counted)."""

    return pick.settle(MASTER, _body(lines, **body), None, TODAY, profile=profile, posting=POSTING, max_bullets=cap)


def _validated(lines: tuple[str, ...], *, summary: str | None = "sum-a", order: tuple[str, ...] = ("experience", "projects"), rows=ROWS, **more):
    return pick.validate_pick(MASTER, AssessmentPick(summary, order, lines), suggestions.requirement_rows(rows), today=TODAY, code_summary="sum-b", **more)


def _codes(found) -> list[tuple[str, str | None]]:
    return [(problem.code, problem.id) for problem in found.problems]


# --- V1 to V8 (pure: nothing is measured) --------------------------------------------------------------------------


def test_v1_v2_v3_an_unknown_id_a_skills_line_an_entry_and_a_repeat_are_dropped_and_recorded() -> None:
    found = _validated(("a1", "b-zzzzzz", "s-1", "r-alpha", "a1", *EVERY_LINE[1:]))
    assert found.refused is None and found.lines == EVERY_LINE and found.added == ()
    assert _codes(found) == [("unknown_id", "b-zzzzzz"), ("not_selectable", "s-1"), ("not_selectable", "r-alpha"), ("duplicate", "a1")]


def test_v4_the_summary_is_the_models_when_it_is_one_and_the_selectors_otherwise() -> None:
    assert _validated(EVERY_LINE, summary="sum-a").summary == "sum-a" and _codes(_validated(EVERY_LINE, summary="sum-a")) == []
    for wrong in (None, "sum-zzzzzz", "a1", "r-alpha"):
        found = _validated(EVERY_LINE, summary=wrong)
        assert found.summary == "sum-b" and _codes(found) == [("summary_by_code", wrong)]
    # A summary id among the lines is the summary the model means; it is not a line of a role.
    found = _validated(("sum-a", *EVERY_LINE), summary=None)
    assert found.summary == "sum-a" and found.lines == EVERY_LINE and _codes(found) == []


def test_v5_the_two_sections_the_model_orders_each_once_or_code_orders_them() -> None:
    assert _validated(EVERY_LINE, order=("projects", "experience")).section_order == ("projects", "experience")
    for wrong in ((), ("projects",), ("experience",)):
        found = _validated(EVERY_LINE, order=wrong)
        assert found.section_order == ("experience", "projects") and _codes(found) == [("section_order_by_code", None)]


def test_v6_a_pick_with_too_few_bullets_is_refused_whole() -> None:
    found = _validated(("a1", "a2", "b3", "o1", "o2", "b-zzzzzz", "p1", "c1", "b1"))  # 6 bullets of roles and projects
    assert found.refused == "pick_too_small" == pick.FALLBACK_TOO_SMALL and found.added == ()
    assert _codes(found) == [("unknown_id", "b-zzzzzz"), ("pick_too_small", None)]
    assert _validated(("a1", "a2", "a3", "b1", "b2", "b3", "p1", "c1")).refused is None  # eight: MIN_PICK


def test_v7_every_met_mandatory_row_keeps_a_line_it_names_the_strongest_one() -> None:
    lines = ("a1", "a3", "a4", "a6", "b1", "b2", "b4", "b5", "p1")  # no source of any row
    found = _validated(lines)
    # Go: a2 states a number, a5 does not. Kafka: its one line. Terraform is optional: nothing is added for it.
    assert [(item.id, item.code, item.requirement) for item in found.added] == [("a2", "added_for_coverage", GO), ("b3", "added_for_coverage", KAFKA)]
    assert found.lines == (*lines, "a2", "b3")
    # A row one of whose lines is picked needs nothing, whichever line it is; nor does a row that is not met.
    assert _validated((*lines, "a5")).added == (pick.Added("b3", "added_for_coverage", KAFKA),)
    unclear = (*ROWS[:1], _row(KAFKA, "Kafka at scale", "askable", ("b3",), status="unclear"))
    assert [item.id for item in _validated((*lines, "a5"), rows=unclear).added] == []
    # A backed line is stronger than one that states a number; a row the SHOWN summary supports is covered by it.
    backed = parse_master(MASTER.markdown().replace("<!-- id:a5 -->", "<!-- id:a5 backed:story:rollout -->"))
    added = pick.validate_pick(backed, AssessmentPick("sum-a", ("experience", "projects"), lines), suggestions.requirement_rows(ROWS[:1]), today=TODAY).added
    assert [(item.id, item.requirement) for item in added] == [("a5", GO)]
    by_summary = (_row(GO, "Go in production", "hard", ("sum-a", "a2")),)
    assert _validated(lines, rows=by_summary).added == ()
    assert [item.id for item in _validated(lines, rows=by_summary, summary="sum-b").added] == ["a2"]


def test_v8_a_recent_role_always_shows_a_line_and_an_old_one_is_never_forced_in() -> None:
    lines = ("a1", "a2", "a3", "a4", "a5", "a6", "p1", "c1", "o1", "o2")  # nothing of Beta Labs, a recent role
    found = _validated(lines, rows=ROWS[:1])
    assert len(found.added) == 1 and found.added[0].code == "recent_role_present" and MASTER.items[found.added[0].id].entry_id == "r-beta"
    # Beta Labs holds no line a row names: the selector's best line of it (by the values it is given) stands in.
    assert [(item.id, item.code) for item in _validated(lines, rows=ROWS[:1], values={"b4": 9.0, "b1": 2.0}).added] == [("b4", "recent_role_present")]
    # With a row that names one of its lines, that line is the one (V7 has added it already when the row is met and mandatory).
    optional = (ROWS[0], _row(TERRAFORM, "Terraform", "list_item", ("b6",)))
    assert [(item.id, item.code) for item in _validated(lines, rows=optional).added] == [("b6", "recent_role_present")]
    assert [(item.id, item.code) for item in _validated(lines).added] == [("b3", "added_for_coverage")]
    # Gamma Data ended eleven years ago: a pick without it gets nothing of it.
    assert _validated(("a1", "a2", "a3", "a4", "a5", "a6", "p1", "b1"), rows=ROWS[:1]).added == ()


def test_a_pinned_line_the_pick_left_out_is_added() -> None:
    lines = tuple(line for line in EVERY_LINE if line not in ("b5", "o2"))
    found = _validated(lines, pins=("b5", "o2", "s-1", "b-zzzzzz", "a1"))
    assert [(item.id, item.code) for item in found.added] == [("b5", "pinned_line"), ("o2", "pinned_line")]


# --- the selection: what code fixes, and the record ------------------------------------------------------------------


def test_a_good_pick_is_the_selection_every_line_a_copy_roles_in_date_order() -> None:
    lines = ("b3", "p1", "a2", "a1", "b6", "a5", "c1", "o2", "b1", "a4", "b2")
    settled = _settle(lines, 10, order=("projects", "experience"))  # ten bullets, a cap of ten: nothing is left out, nothing added
    assert (settled.picked_by, settled.fallback, settled.draft, settled.problems, settled.conflicts) == ("model", None, False, (), ())
    result = settled.result
    # The model orders the two sections; roles print newest first whatever the list says; inside a role, the model's order.
    assert [section.heading for section in result.sections] == ["summary", "projects", "experience", "skills", "education", "other"]
    roles = next(section for section in result.sections if section.heading == "experience").entries
    assert [[tm.line_item_id(line) for line in entry.bullets] for entry in roles] == [["a2", "a1", "a5", "a4"], ["b3", "b6", "b1", "b2"], ["c1"]]
    assert settled.printed == ("sum-a", "p1", "a2", "a1", "a5", "a4", "b3", "b6", "b1", "b2", "c1", "o2")
    # Nothing reworded: every line is a copy of a master line, the Skills section is whole, the degree is there.
    for section in result.sections:
        for line in section.body_lines():
            if section.heading != "skills":
                assert line.kind == "copy" and shown_text(line) == MASTER.items[tm.line_item_id(line)].text, line.id
    assert sorted(_skills(result)) == sorted(MASTER.skills()) and len(_skills(result)) == 6
    assert "Varnholt Technical University" in settled.markdown and settled.result.length is None
    # Picked / Left out, in the stored job resume's own record: no enum of it grows.
    record = settled.record
    assert (record.picked_by, record.candidates, record.fallback, record.selector_version) == ("model", "evidence", None, ms.SELECTOR_VERSION)
    reasons = {line.id: (line.code, line.reason) for line in (*record.picked, *record.left_out)}
    assert reasons["a2"] == ("supports_requirement", f"supports {GO}") and reasons["a1"] == ("picked_by_assessment", "picked by the assessment")
    assert reasons["a3"] == ("not_picked", "not picked by the assessment") and reasons["sum-b"][0] == "summary_other_variant"
    assert tm.TailorSelection.from_json(record.to_json()) == record
    # What the suggestion record keeps of it.
    assert settled.check.ready and settled.check.lost() == () and settled.line_marks == {item_id: MASTER.items[item_id].mark for item_id in settled.printed}
    stored = settled.selection_json(made_at="2026-10-05T10:00:00Z", result_digest="sha256:" + "0" * 64, master_revision_id="revision_1")
    assert stored["picked_by"] == "model" and stored["pick_rules_version"] == pick.PICK_RULES_VERSION == "pick-rules:1"
    assert stored["model_pick"] == {"summary": "sum-a", "section_order": ["projects", "experience"], "lines": list(lines)}
    assert stored["line_marks"][0] == {"id": "sum-a", "mark": MASTER.items["sum-a"].mark} 
    # No page is counted: ``pages`` stays in the record (null) for older readers, ``max_pages`` is the resume's limit.
    assert stored["pages"] is None and stored["max_pages"] == 2 and stored["max_bullets"] == 10
    assert set(stored) == {
        "picked_by", "fallback", "draft", "pick_rules_version", "selector_version", "made_at", "made_from", "model_pick", "problems", "added_by_code",
        "line_marks", "pages", "max_pages", "max_bullets", "conflicts", "resume",
    }
    checks = pick.checks(MASTER, settled, suggestions.requirement_rows(ROWS))
    assert (checks.lost, checks.weak, checks.omitted, checks.empty_entries, checks.roles_in_date_order, checks.fits) == ((), (), (), (), True, True)


# --- the cap (3.2): what is protected, and the order of everything else ------------------------------------------------


@pytest.mark.parametrize(
    ("cap", "left_out"),
    [
        (14, ()),
        # 1. lines that are the source of no row, the model's last-ranked first (an old role that loses its last line keeps
        #    its heading line; the one line of a project stays).
        (13, ("c1",)),
        (10, ("c1", "b5", "b4", "b2")),
        (6, ("c1", "b5", "b4", "b2", "b1", "a6", "a4", "a3")),
        # 2. then a further source of a row: the optional row's line and the weaker second line of Go.
        (5, ("c1", "b5", "b4", "b2", "b1", "a6", "a4", "a3", "a1")),
        (4, ("c1", "b5", "b4", "b2", "b1", "a6", "a4", "a3", "a1", "b6")),
        (3, ("c1", "b5", "b4", "b2", "b1", "a6", "a4", "a3", "a1", "b6", "a5")),
    ],
)
def test_the_cap_leaves_out_what_supports_nothing_first_the_models_last_ranked_first(cap: int, left_out: tuple[str, ...]) -> None:
    settled = _settle(EVERY_LINE, cap)
    assert set(_bullets(settled.result)) == set(EVERY_LINE[:14]) - set(left_out) and len(_bullets(settled.result)) == cap
    assert _others(settled.result) == ["o1", "o2"] and settled.conflicts == () and settled.check.ready
    # The last printed source of a met mandatory row is never among them.
    assert {"a2", "b3", "p1"} <= set(settled.printed)
    left = {line.id: line.code for line in settled.record.left_out}
    assert all(left[item_id] == "over_cap" for item_id in left_out)
    # Nothing is "cut for length" (0.1.11.5): no cut on the record, no length record on the result; what is over the cap is
    # under Left out, where the job page adds a line back.
    assert settled.record.cut_for_length == () and settled.result.length is None and settled.pages is None
    # The old role that lost its one line is still listed, by its heading alone.
    roles = next(section for section in settled.result.sections if section.heading == "experience").entries
    assert [tm.line_item_id(entry.heading[0]) for entry in roles] == ["r-alpha", "r-beta", "r-gamma"]
    assert [len(entry.bullets) for entry in roles][2] == (0 if left_out else 1)


def test_the_other_lines_the_skills_and_the_heading_lines_are_never_left_out_for_the_cap() -> None:
    """Until 0.1.11.5 the page fit dropped Other lines, then Skills (``skills_do_not_fit``), then heading lines. None counts toward the cap."""

    settled = _settle(EVERY_LINE, 3)
    assert set(_bullets(settled.result)) == {"a2", "b3", "p1"} and _others(settled.result) == ["o1", "o2"]
    assert sorted(_skills(settled.result)) == sorted(MASTER.skills()) and len(_skills(settled.result)) == 6
    assert settled.record.skills_left_out == () and settled.conflicts == () and settled.check.ready
    assert "o2" not in {line.id for line in settled.record.left_out}
    assert "Engineer, Gamma Data | Jun 2012 - Jun 2015" in settled.markdown  # the role with no line left: its one heading line


def test_a_pinned_line_is_left_out_only_after_everything_that_is_not_protected() -> None:
    pinned = ms.SelectionProfile(titles=PROFILE.titles, pins=("b5",))
    settled = _settle(EVERY_LINE, 10, profile=pinned)
    assert set(EVERY_LINE[:14]) - set(_bullets(settled.result)) == {"c1", "b4", "b2", "b1"} and "b5" in settled.printed


def test_when_the_protected_lines_alone_are_over_the_cap_one_is_left_out_and_the_loss_is_a_conflict() -> None:
    rows = (_row(GO, "Go in production", "hard", ("a2",)), _row(KAFKA, "Kafka at scale", "askable", ("b3",)), _row(UPGRADES, "Kubernetes upgrades", "hard", ("a4",)))
    settled = _settle(EVERY_LINE, 3, rows=rows)
    # Alpha Systems held two protected lines; the model's lower-ranked one went. The cap holds; nothing is silent.
    assert set(_bullets(settled.result)) == {"a2", "b3", "p1"} and settled.pages is None
    conflicts = [conflict.to_json() for conflict in settled.conflicts]
    assert conflicts == [{"code": "mandatory_evidence_does_not_fit", "requirement": UPGRADES, "lines": ["a4"], "cut": True}]  # no page-driven code
    assert not settled.check.ready and ("lost_mandatory_evidence", UPGRADES) in [(reason.code, reason.requirement) for reason in settled.check.reasons]
    checks = pick.checks(MASTER, settled, suggestions.requirement_rows(rows))
    assert checks.lost == (UPGRADES,) and checks.fits
    assert {line.id: line.code for line in settled.record.left_out}["a4"] == "cut_conflict"
    # A must-cover line is never left out for a better-ranked line that is not one: at 4 every protected line is in.
    roomier = _settle(EVERY_LINE, 4, rows=rows)
    assert {"a2", "a4", "b3"} <= set(_bullets(roomier.result)) and roomier.conflicts == () and roomier.check.ready


def test_a_pick_under_the_cap_is_filled_with_the_unpicked_evidence_first() -> None:
    lines = ("a1", "a2", "a3", "b1", "b2", "b3", "p1", "c1")
    assert set(_bullets(_settle(lines, 8).result)) == set(lines)  # at the cap: the pick as it is
    settled = _settle(lines, 9)
    assert set(_bullets(settled.result)) == {*lines, "a5"}  # the unpicked line a mandatory row names, before any other
    assert settled.added_by_code == (pick.Added("a5", "room_left"),)
    assert {line.id: line.code for line in settled.record.picked}["a5"] == "room_left"
    roomy = _settle(lines, 99)
    assert set(lines) | {"a5"} <= set(_bullets(roomy.result)) and all(item.code == "room_left" for item in roomy.added_by_code)
    assert all(MASTER.items[item.id].entry_id in ("r-alpha", "r-beta") for item in roomy.added_by_code)  # a mandatory row's line, or a recent role's


# --- the fallback (3.4): the code selector, with the reason ------------------------------------------------------------


def test_no_pick_a_pick_too_small_and_a_draft_are_the_code_selectors_selection_with_the_reason() -> None:
    rows = suggestions.requirement_rows(ROWS)
    none = pick.settle(MASTER, _body(None), None, TODAY, profile=PROFILE, posting=POSTING)
    assert (none.picked_by, none.fallback, none.draft, none.model_pick) == ("code", "no_pick", False, None)
    assert (none.record.picked_by, none.record.candidates, none.record.fallback) == ("code", "view", "no_pick")
    assert none.pages is None and none.result.length is None and none.check.ready and pick.checks(MASTER, none, rows).fits
    assert {"a2", "b3"} <= set(none.printed)  # the rows' sources are the selector's citations
    small = pick.settle(MASTER, _body(("a1", "b-zzzzzz", "b3")), None, TODAY, profile=PROFILE, posting=POSTING)
    assert (small.picked_by, small.fallback) == ("code", "pick_too_small") and small.model_pick is not None and small.model_pick.lines == ("a1", "b-zzzzzz", "b3")
    assert [problem.to_json() for problem in small.problems] == [{"code": "unknown_id", "id": "b-zzzzzz"}, {"code": "pick_too_small", "id": None}]
    assert small.printed == none.printed
    draft = pick.settle(MASTER, _body(EVERY_LINE), None, TODAY, profile=PROFILE, posting=POSTING, fallback=pick.FALLBACK_DRAFT, draft=True)
    assert (draft.picked_by, draft.fallback, draft.draft) == ("code", "draft_requested", True) and draft.printed == none.printed
    assert draft.selection_json(made_at="t", result_digest=None, master_revision_id=None)["draft"] is True
    assert set(pick.FALLBACKS) == {"no_pick", "pick_too_small", "master_revision_unreadable", "pick_failed", "draft_requested"}


def test_a_pick_that_cannot_be_settled_never_fails_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object, **_kwargs: object):
        raise RuntimeError("no line of this message is shown")

    monkeypatch.setattr(pick, "_pick_selection", broken)
    settled = pick.settle(MASTER, _body(EVERY_LINE), None, TODAY, profile=PROFILE, posting=POSTING)
    assert (settled.picked_by, settled.fallback) == ("code", "pick_failed") and settled.model_pick is not None and settled.printed


def test_a_selection_needs_no_renderer_nothing_is_laid_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Until 0.1.11.5 a pick with no renderer was refused (``pages_unmeasured``); now no page is counted at all."""

    from gigai.scout import resume_pdf

    def no_layout(*_args: object, **_kwargs: object):
        raise AssertionError("a selection must not lay out a page")

    for name in ("pages_at", "measure_markdown", "fewest_pages", "_estimate", "_render"):
        monkeypatch.setattr(resume_pdf, name, no_layout)
    monkeypatch.setattr(tm, "measure_pages", no_layout)
    picked = pick.settle(MASTER, _body(EVERY_LINE), None, TODAY, profile=PROFILE, posting=POSTING)
    assert (picked.picked_by, picked.fallback, picked.pages, picked.layouts) == ("model", None, None, 0) and len(_bullets(picked.result)) == 14
    capped = _settle(EVERY_LINE, 6)
    assert len(_bullets(capped.result)) == 6 and capped.pages is None
    code = pick.settle(MASTER, _body(None), None, TODAY, profile=PROFILE, posting=POSTING)
    assert (code.picked_by, code.fallback, code.pages) == ("code", "no_pick", None)


# --- the seven picks of the spec, on the eval's medium master, under the shipped cap -----------------------------------


def _eval_case():
    case = ev.master_case("medium", "base")
    master = parse_master(case.markdown)
    citations = assess_master.MasterCitations(master)
    matrix = []
    for number, row in enumerate(ev.assessment("agentic"), 1):
        lines, _skills_named = citations.row(row["evidence"])
        matrix.append(RequirementMatrixRow(
            row["requirement"], tuple(row["evidence"]), MatrixStatus(row["status"]), RequirementClass(row["class"]), id=f"req-{number:06x}",
            class_basis="Requirements", sources=tuple(lines),
        ))
    prior = ev.profile("medium")
    post = ev.postings()["agentic"]
    return (
        master, tuple(matrix), ms.SelectionProfile(titles=tuple(prior["titles"]), base_ids=tuple(prior["base_ids"])),
        ms.SelectionPosting(post["title"], post["text"], post["company"], post.get("location", "")), ev.date_of(ev._load("sizes.json")["today"]),
    )


def test_the_seven_picks_each_end_within_the_cap_with_no_empty_role_and_nothing_silent() -> None:
    master, matrix, profile, posting, today = _eval_case()
    rows = suggestions.requirement_rows(matrix)
    by_entry = {entry.id: list(entry.bullets) for entry in master.entries.values() if entry.section in ("experience", "projects")}
    bullets = [bullet for entry_bullets in by_entry.values() for bullet in entry_bullets]
    sources = [source for row in rows if row.status == "met" for source in row.master_sources() if master.items[source].kind == "bullet"]
    roles = sorted(master.entries_in("experience"), key=lambda entry: (-(9999 if entry.ongoing else (entry.end or 0)), -(entry.start or 0)))
    current = roles[0]
    old = [entry for entry in roles if ms.is_old_role(entry, today)]
    assert old and len(bullets) > 40
    good = tuple(dict.fromkeys([*sources, *current.bullets, *(bullet for entry in roles[1:3] for bullet in entry.bullets[:4])]))
    picks: dict[str, AssessmentPick | None] = {
        "good": AssessmentPick(None, ("experience", "projects"), good),
        "unknown_ids": AssessmentPick("sum-zzzzzz", ("experience", "projects"), ("b-zzzzzz", "b-yyyyyy", *good)),
        "skills_lines": AssessmentPick(None, ("experience", "projects"), (*(item.id for item in master.in_section("skills")), *good)),
        "current_role_omitted": AssessmentPick(None, ("experience", "projects"), tuple(bullet for bullet in bullets if bullet not in current.bullets)[:30]),
        "only_old_lines": AssessmentPick(None, ("experience", "projects"), tuple(bullet for entry in old for bullet in entry.bullets)),
        "every_line": AssessmentPick(None, ("projects", "experience"), tuple(bullets)),
        "empty": None,
    }
    expected = {"good": None, "unknown_ids": None, "skills_lines": None, "current_role_omitted": None, "only_old_lines": None, "every_line": None, "empty": "no_pick"}
    for name, chosen in picks.items():
        body = AssessmentBody(matrix, (), (), verdict=Verdict.MATCHED_ABOVE_THRESHOLD, pick=chosen)
        settled = pick.settle(master, body, None, today, profile=profile, posting=posting)
        checks = pick.checks(master, settled, rows)
        assert settled.fallback == expected[name], (name, settled.fallback, [problem.to_json() for problem in settled.problems])
        shown = sum(len(entry.bullets) for section in settled.result.sections if section.heading in ("experience", "projects") for entry in section.entries)
        assert shown <= ms.MAX_PICK_BULLETS and settled.pages is None and settled.result.length is None and checks.fits, (name, shown, checks.to_json())
        if name == "every_line":
            assert shown == ms.MAX_PICK_BULLETS == 20, shown  # more lines than the cap: exactly the cap is shown
        assert all(item_id in master.items for item_id in settled.printed), name
        # No met mandatory row loses every line it names unless a conflict says so.
        assert not checks.lost or settled.conflicts, (name, checks.lost)
        assert settled.check.ready == (not settled.check.reasons), name
        shown_roles = {master.items[item_id].entry_id for item_id in settled.printed}
        assert all(entry.id in shown_roles for entry in roles if entry not in old), name  # A3: every recent role shows a line
        codes = [problem.code for problem in settled.problems]
        if name == "unknown_ids":
            assert codes.count("unknown_id") == 2 and "summary_by_code" in codes
        if name == "skills_lines":
            assert codes.count("not_selectable") == len(master.in_section("skills"))
        if name == "current_role_omitted":
            assert any(master.items[item.id].entry_id == current.id for item in settled.added_by_code), name
        if name == "only_old_lines":
            assert "added_for_coverage" in {item.code for item in settled.added_by_code}, name


@pytest.mark.parametrize("budget", [99, 8, 6])
def test_the_order_the_model_gives_is_the_order_printed_inside_a_role_even_under_the_cap(budget: int) -> None:
    """The v9.1 prompt promises it: within one role the lines print in the pick's order; the cap leaves lines out, never reorders."""

    lines = ("a4", "a1", "a5", "a2", "b6", "b3", "b1", "a3")
    reversed_lines = tuple(reversed(lines))
    for given in (lines, reversed_lines):
        settled = _settle(given, budget)
        printed = {entry_id: [tm.line_item_id(line) for line in entry.bullets] for section in settled.result.sections for entry in section.entries
                   if (entry_id := tm.line_item_id(entry.heading[0]))}
        assert printed["r-alpha"] and printed["r-beta"]
        for role in ("r-alpha", "r-beta"):
            in_pick = [item for item in given if MASTER.items[item].entry_id == role]
            shown = printed[role]
            # The picked lines print in the pick's order. A line CODE adds (room left under the cap, coverage) is not the
            # model's and comes after them: the promise is about the lines the model gave.
            assert [item for item in shown if item in in_pick] == [item for item in in_pick if item in shown], (budget, role, shown, in_pick)


# --- 0.1.11 C1: a picked line is never displaced by an unpicked one ------------------------------------------------------


def _row_optional(row_id: str, requirement: str, sources: tuple[str, ...]) -> RequirementMatrixRow:
    return _row(row_id, requirement, "list_item", sources)


def _c1(lines: tuple[str, ...], rows: tuple[RequirementMatrixRow, ...], budget: int, *, profile: ms.SelectionProfile = PROFILE):
    return pick.settle(MASTER, _body(lines, rows=rows), None, TODAY, profile=profile, posting=POSTING, max_bullets=budget)


C1_ROWS = (_row(GO, "Go in production", "hard", ("a2", "a5")),)
# No line of Beta Labs (a recent role): V8 gives that role one, the best of its lines by the selector's worth.
C1_PICK = ("a1", "a2", "a3", "a4", "a5", "a6", "p1", "c1")


def test_c1_a_line_code_added_goes_before_any_line_the_model_picked() -> None:
    """The judge's T12 shape: the pick leaves a recent role out, code adds it one line, and a picked line is cut to keep it."""

    # With room the line code gave the recent role prints: C1 is a cut order, not a ban.
    roomy = _c1(C1_PICK, C1_ROWS, 9)
    assert [item.code for item in roomy.added_by_code] == ["recent_role_present"] and set(C1_PICK) <= set(roomy.printed)
    given = roomy.added_by_code[0].id
    # Nine lines, room for eight, and a row rests on EVERY picked line: the line the model did not give is the one that goes, its role with it.
    leaning = (_row(GO, "Go in production", "hard", C1_PICK),)
    settled = _c1(C1_PICK, leaning, 8)
    assert set(_bullets(settled.result)) == set(C1_PICK), _bullets(settled.result)
    assert given not in settled.printed and settled.added_by_code == () and settled.conflicts == ()


def test_c1_the_line_given_to_a_recent_role_outlives_a_picked_line_no_row_rests_on() -> None:
    """The employment gap: a role gone between two printed ones is the worse loss, so a bare picked line goes first."""

    roomy = _c1(C1_PICK, C1_ROWS, 9)
    given = roomy.added_by_code[0].id
    settled = _c1(C1_PICK, C1_ROWS, 8)
    shown = _bullets(settled.result)
    assert given in shown and len(shown) == 8 and {"a2", "a5"} <= set(shown), shown
    assert len(set(C1_PICK) - set(shown)) == 1 and not set(C1_PICK) - set(shown) & {"a2", "a5"}
    # Down to the lines a row rests on, the recent role's line still outlives the bare ones; it goes only before a line a row rests on.
    tight = _c1(C1_PICK, C1_ROWS, 3)
    assert {"a2", "a5"} <= set(_bullets(tight.result)), _bullets(tight.result)


def test_c1_a_pinned_line_the_model_did_not_pick_stays_with_the_picked_ones() -> None:
    settled = _c1(C1_PICK, C1_ROWS, 8, profile=ms.SelectionProfile(titles=PROFILE.titles, pins=("b5",)))
    assert "b5" in settled.printed and len(_bullets(settled.result)) == 8


def test_c1_the_cut_follows_the_pick_order_not_the_strength_of_a_line_an_optional_row_rests_on() -> None:
    """The judge's T20 shape: two lines of two optional rows; the earlier in the pick (a stated line) outlives the later (a quantified one)."""

    rows = (
        _row(GO, "Go in production", "hard", ("a5",)),
        _row_optional("req-000010", "Control planes", ("a1",)),  # stated, 2nd in the pick
        _row_optional("req-000011", "Faster rollouts", ("a2",)),  # quantified, 3rd in the pick
    )
    lines = ("a5", "a1", "a2", "b6", "b4", "b5", "p1", "c1")
    for budget in (5, 4):
        settled = _c1(lines, rows, budget)
        assert {"a1", "a5", "b6", "p1"} <= set(settled.printed), (budget, settled.printed)
        assert ("a2" in settled.printed) == (budget == 5), (budget, settled.printed)
        assert settled.added_by_code == ()
