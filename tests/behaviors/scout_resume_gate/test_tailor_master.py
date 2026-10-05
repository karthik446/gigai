"""0.1.10.9 master P4: the rules of the master resume in the tailor path (``tailor_master``), without a model.

The candidate set of a job (three sizes), the numbering that ties every line to its master id, the Skills
line code assembles, a requirement's evidence in a set wider than 2 pages, the fit's cut order (0110-10-15:
lowest value for the posting first, never age alone) and its record, Picked / Left out with its conflicts,
and the kept read of the stored master.  Synthetic data only: the spike's
invented person (``tests/evals/fixtures/master``) and one small hand-written master.  The END outcome of a
whole tailoring is in ``test_master_tailor_outcomes.py``.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import subprocess

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import master_selection as ms
from gigai.scout import tailor_master as tm
from gigai.scout.master_resume import Master, parse_master
from gigai.scout.posting_keywords import mentions
from gigai.scout.tailor_length import STATUS_CUT, STATUS_RESTORED, cut_again, restore_cut
from gigai.scout.tailor_skills import finish_tailoring
from gigai.scout.tailored_resume import (
    SourceRef,
    TailorJob,
    TailoredResume,
    apply_no_loss,
    render_markdown,
    resume_lines,
    validate_tailored_output,
)
from gigai.scout.target_resolution import home_scout_target
from gigai.workpad import resolve_workpad

from tests.support.setup_home import setup_home

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "master"
TODAY = date(2026, 10, 3)
OLD_ROLES = ("r-tes", "r-bri", "r-cas")  # oldest first
RECENT_ROLES = ("r-lum", "r-kes", "r-hex", "r-fin")
POSTINGS = ("p1-staff-ai-agent-platform", "p2-staff-swe-core-infrastructure", "p3-staff-engineer-ml-platform")


@pytest.fixture(scope="module")
def master() -> Master:
    return parse_master((FIXTURES / "master.md").read_text(encoding="utf-8"))


def _profile(profile_id: str = "profile-ai") -> ms.SelectionProfile:
    raw = next(item for item in json.loads((FIXTURES / "profiles.json").read_text(encoding="utf-8"))["profiles"] if item["profile_id"] == profile_id)
    return ms.SelectionProfile(tuple(raw["titles"]), tuple(raw["focus_tags"]), None, raw["profile_id"], raw["label"])


def _posting(posting_id: str) -> ms.SelectionPosting:
    head, _, body = (FIXTURES / "postings" / f"{posting_id}.md").read_text(encoding="utf-8").partition("\n\n")
    meta = dict(line.split(": ", 1) for line in head.splitlines())
    return ms.SelectionPosting(meta["title"], body.strip() + "\n", meta["company"], meta.get("location", ""))


def _job(posting: ms.SelectionPosting) -> TailorJob:
    return TailorJob(posting.title, posting.company, posting.location, posting.text)


def _copied(candidates: tm.JobCandidates, *, today: date = TODAY) -> tuple[TailoredResume, object]:
    """The settled answer of a tailoring that copies every candidate line in the order listed, and its context."""

    job, ctx = _job(candidates.posting), candidates.context()
    settled = finish_tailoring(apply_no_loss(validate_tailored_output(candidates.copy_all(), job, ctx), job, ctx, today=today), job, ctx)
    return settled, ctx


def _shown_ids(result: TailoredResume) -> list[str]:
    return [item for section in result.sections for line in section.body_lines() if (item := tm.line_item_id(line)) is not None]


def _roles(result: TailoredResume) -> dict[str, list[str]]:
    """Shown experience role id -> its shown bullets' master ids."""

    section = next(section for section in result.sections if section.heading == "experience")
    return {tm.line_item_id(entry.heading[0]) or "": [tm.line_item_id(line) or "" for line in entry.bullets] for entry in section.entries}


# --- the candidate set ------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", tm.CANDIDATE_MODES)
def test_every_numbered_line_of_a_candidate_set_is_the_master_line_its_id_names(master: Master, mode: str) -> None:
    candidates = tm.job_candidates(master, _profile(), _posting(POSTINGS[0]), mode=mode, today=TODAY)
    numbered = resume_lines(candidates.markdown)
    assert [line.number for line in candidates.lines] == list(range(1, len(numbered) + 1))
    assert "<!--" not in candidates.markdown, "the tailor call reads no id"
    for line in candidates.lines:
        text = numbered[line.number - 1]
        if line.kind == "section":
            assert text.startswith("## ") and line.item_id is None
        elif line.kind == "entry":
            assert text == f"### {master.entries[line.item_id].heading}"  # type: ignore[index]
        elif line.kind == "subline":
            assert text in master.entries[line.item_id].sublines  # type: ignore[index]
        elif line.kind == "skills":
            assert line.item_id is None and text == "- " + ", ".join(candidates.skills)
        else:
            assert text == f"- {master.items[line.item_id].text}"  # type: ignore[index]
    # The same ids reach the tailoring's context, so a ref to a line carries its master id.
    ctx = candidates.context()
    assert dict(ctx.line_ids) == candidates.line_ids and ctx.withheld == frozenset()
    assert candidates.skills_line is not None and ms.SKILLS_WHOLE <= len(candidates.skills) <= len(master.skills())


def test_the_three_candidate_sets_are_the_view_the_pick_before_its_fit_and_the_evidence_bullets(master: Master) -> None:
    profile, posting = _profile(), _posting(POSTINGS[0])
    view, shortlist, evidence = (tm.job_candidates(master, profile, posting, mode=mode, today=TODAY) for mode in tm.CANDIDATE_MODES)
    selected = ms.select(master, profile, posting, today=TODAY)

    # view: exactly the selector's 2-page selection.
    assert view.markdown == selected.markdown and view.item_ids() == frozenset(selected.item_ids())
    # shortlist: the selector's pick before its page fit: every role, under the caps it picks under (a line the
    # posting's requirements are about passes the cap for general lines, up to the hard cap), about twice what fits.
    assert set(shortlist.entries) >= set(OLD_ROLES) | set(RECENT_ROLES)
    assert all(len(shortlist.entries[role]) >= min(ms.PICK_CAPS[index], len(master.entries[role].bullets)) for index, role in enumerate(RECENT_ROLES))
    assert all(set(view.entries.get(role, ())) <= set(shortlist.entries[role]) for role in RECENT_ROLES)
    assert all(len(shortlist.entries[role]) == 3 for role in OLD_ROLES)
    assert shortlist.bullet_count > 1.5 * view.bullet_count
    # The projects, the Other lines and the skills offered are the ones the 2-page selection shows: the fit cannot remove them.
    assert {entry for entry in shortlist.entries if master.entries[entry].section == "projects"} == {entry for entry in selected.entries if master.entries[entry].section == "projects"}
    assert (shortlist.other, shortlist.skills) == (selected.other, selected.skills)
    # evidence: the evidence view's bullets for every role, under the same summary, skills, Other lines and projects.
    assert evidence.bullet_count > shortlist.bullet_count
    assert (evidence.summary, evidence.skills, evidence.other) == (shortlist.summary, shortlist.skills, shortlist.other)
    assert {entry for entry in evidence.entries if master.entries[entry].section == "projects"} <= {entry for entry in shortlist.entries if master.entries[entry].section == "projects"}
    assert len(evidence.markdown) < 12_000
    with pytest.raises(ValueError, match="mode must be one of"):
        tm.job_candidates(master, profile, posting, mode="whole", today=TODAY)


def test_a_copy_of_every_candidate_line_is_a_valid_tailoring_and_each_ref_carries_its_master_id(master: Master) -> None:
    candidates = tm.job_candidates(master, _profile(), _posting(POSTINGS[0]), mode=tm.MODE_SHORTLIST, today=TODAY)
    settled, _ctx = _copied(candidates)
    refs = [ref for section in settled.sections for line in section.all_lines() for ref in line.refs]
    assert len(refs) == len([line for line in candidates.lines if line.kind != "section"])
    for ref in refs:
        assert ref.item_id == candidates.line_ids.get(ref.line)  # type: ignore[arg-type]
        stored = SourceRef.from_json(ref.to_json())
        assert stored == ref and ("item_id" in ref.to_json()) == (ref.item_id is not None)
    # A ref with no master id is stored exactly as it always was.
    assert SourceRef("resume", 4, None, "text").to_json() == {"kind": "resume", "line": 4, "text": "text"}
    assert "<!-- id:" not in render_markdown(settled), "the markdown names sources by R number, as before"


# --- decision 2 in a wider set, and the fit ---------------------------------------------------------


@pytest.mark.parametrize("profile_id", ["profile-ai", "profile-swe"])
def test_a_requirements_evidence_is_offered_and_stays_through_the_fit_wherever_its_role_stands(master: Master, profile_id: str) -> None:
    # Core infrastructure asks for Linux: lines of a recent role name it as strongly as an old role's, so a recent
    # one is its evidence. It is offered, it is shown, and the old line is not needed for it.
    infra = tm.job_candidates(master, _profile(profile_id), _posting(POSTINGS[1]), mode=tm.MODE_SHORTLIST, today=TODAY)
    assert infra.stand_ins == {}, "job-candidates:1's stand-ins are gone: the evidence is in the selector's pick itself"
    asked = next(requirement for requirement in infra.selected.requirements if requirement.text == "Linux")
    evidence = asked.supporters[0]
    assert master.items[evidence].entry_id in RECENT_ROLES and evidence in infra.entries[master.items[evidence].entry_id or ""]
    settled, ctx = _copied(infra)
    fitted = tm.fit_selected(tm.ensure_skills_line(settled, infra, ctx), infra, master, today=TODAY)
    assert tm.measure_pages(fitted) == 2 and evidence in _shown_ids(fitted) and mentions(render_markdown(fitted), "Linux")
    record = tm.selection_record(master, infra, fitted, today=TODAY)
    assert record.conflicts == () and next(line for line in record.picked if line.id == evidence).code == "requirement_evidence"

    # The ML platform posting asks for SQL: only an old role's line names it. That line stays, its role with it,
    # while lines of recent roles that are worth less for the posting are cut.
    only_old = tm.job_candidates(master, _profile(profile_id), _posting(POSTINGS[2]), mode=tm.MODE_SHORTLIST, today=TODAY)
    asked = next(requirement for requirement in only_old.selected.requirements if requirement.text == "SQL")
    assert asked.supporters == ("b-cas-08",) and "b-cas-08" in only_old.entries["r-cas"]
    settled, ctx = _copied(only_old)
    fitted = tm.fit_selected(tm.ensure_skills_line(settled, only_old, ctx), only_old, master, today=TODAY)
    roles = _roles(fitted)
    assert tm.measure_pages(fitted) == 2 and [role for role in roles if role in OLD_ROLES] == ["r-cas"] and "b-cas-08" in roles["r-cas"]
    record = tm.selection_record(master, only_old, fitted, today=TODAY)
    assert record.conflicts == ()
    recent_cut = [cut.id for cut in record.cut_for_length if cut.kind == "bullet" and master.items[cut.id].entry_id in RECENT_ROLES]
    assert recent_cut and all(only_old.selected.values[item] < only_old.selected.values["b-cas-08"] for item in recent_cut)


@pytest.mark.parametrize("mode", [tm.MODE_SHORTLIST, tm.MODE_EVIDENCE])
@pytest.mark.parametrize("posting_id", POSTINGS)
def test_the_fit_reaches_two_pages_lowest_value_first_with_every_recent_role_present_and_no_empty_role(master: Master, mode: str, posting_id: str) -> None:
    candidates = tm.job_candidates(master, _profile("profile-swe"), _posting(posting_id), mode=mode, today=TODAY)
    settled, ctx = _copied(candidates)
    whole = tm.ensure_skills_line(settled, candidates, ctx)
    cuts, refill = tm.cut_order(whole, candidates, master, today=TODAY)
    fitted = tm.fit_selected(whole, candidates, master, today=TODAY)

    assert tm.measure_pages(fitted) == 2 and fitted.length is not None and fitted.length.status == STATUS_CUT
    assert fitted.length.pages == 2 < fitted.length.full_pages  # type: ignore[operator]
    roles = _roles(fitted)
    for index, role in enumerate(RECENT_ROLES):
        assert len(roles[role]) >= ms.FLOORS[index] >= 1, f"{role} is under its floor"
    assert all(bullets for bullets in roles.values()), "no role is printed without a bullet"
    # The order: bullets by the selector's value for the posting, lowest first; the lines that are a requirement's
    # evidence among what the tailoring shows come last of all, and none of them was cut here.
    by_line = {line.id: tm.line_item_id(line) for section in whole.sections for line in section.all_lines()}
    values = candidates.selected.values
    evidence = set(tm.shown_evidence(_shown_ids(whole), candidates.selected))
    bullets = [by_line[target] for kind, target in cuts if kind == "bullet"]
    free = [item for item in bullets if item not in evidence]
    assert bullets[: len(free)] == free and [values[item] for item in free] == sorted(values[item] for item in free)  # type: ignore[index]
    assert evidence and evidence <= set(_shown_ids(fitted))
    assert tm.selection_record(master, candidates, fitted, today=TODAY).conflicts == ()
    # The cut that would leave an old role without a bullet removes the role; room left never brings back an old
    # role's line that supports nothing the posting asks for.
    supported = {item for requirement in candidates.selected.requirements for item in requirement.supporters}
    assert {by_line[target] for kind, target in cuts if kind == "role"} <= set(OLD_ROLES)
    assert all(master.items[by_line[target]].entry_id not in OLD_ROLES or by_line[target] in supported for target in refill)  # type: ignore[index]
    # What is shown and what the record holds are together exactly what the tailoring answered with (the bullets the
    # old-role rule had already left out of an old role included: one record, one Restore).
    answered = sorted(_shown_ids(restore_cut(settled)))
    record = fitted.length
    left = [tm.line_item_id(line) for role in record.cut for line in role.entry.bullets] + [tm.line_item_id(line) for role in record.trimmed for line in role.bullets]
    assert sorted([*_shown_ids(fitted), *left]) == answered and len(answered) == len(set(answered))
    # One Restore puts everything back; cutting again is the same resume.
    restored = restore_cut(fitted)
    assert restored.length is not None and restored.length.status == STATUS_RESTORED
    assert sorted(_shown_ids(restored)) == answered and cut_again(restored) == fitted


def test_a_tailoring_of_the_two_page_view_is_not_cut_and_a_missing_skills_line_is_put_back(master: Master) -> None:
    candidates = tm.job_candidates(master, _profile(), _posting(POSTINGS[0]), mode=tm.MODE_VIEW, today=TODAY)
    settled, ctx = _copied(candidates)
    assert tm.fit_selected(settled, candidates, master, today=TODAY) == settled and settled.length is None
    assert tm.ensure_skills_line(settled, candidates, ctx) == settled

    without = TailoredResume(settled.header, tuple(section for section in settled.sections if section.heading != "skills"))
    shown = tm.ensure_skills_line(without, candidates, ctx)
    headings = [section.heading for section in shown.sections]
    assert headings.index("skills") == headings.index("projects") + 1 < headings.index("education")
    (line,) = next(section for section in shown.sections if section.heading == "skills").lines
    assert (line.kind, line.text, line.refs[0].line) == ("copy", "- " + ", ".join(candidates.skills), candidates.skills_line)
    assert line.id not in {other.id for section in without.sections for other in section.all_lines()}


SMALL = """<!-- gigai-master:1 -->

## Summary

- Platform engineer with 15 years on control planes. <!-- id:sum-a -->

## Experience

### NewCo <!-- id:r-new -->
Staff Engineer | Jan 2022 - Present
- Ran the control plane on Kubernetes for 900 clusters. <!-- id:b-n1 -->
- Cut deploy time from 40 minutes to 5 with Kubernetes operators. <!-- id:b-n2 -->
- Wrote the on-call handbook used by 30 engineers. <!-- id:b-n3 -->
- Mentored 4 engineers. <!-- id:b-n4 -->

### MidCo <!-- id:r-mid -->
Senior Engineer | Jan 2019 - Dec 2021
- Built a Kubernetes admission controller. <!-- id:b-m1 -->
- Moved 60 services to Kubernetes. <!-- id:b-m2 -->
- Kept the build green. <!-- id:b-m3 -->

### OldCo <!-- id:r-old -->
Engineer | Jan 2010 - Dec 2013
- Maintained the Fortran solver behind the pricing engine. <!-- id:b-o1 -->
- Wrote release notes. <!-- id:b-o2 -->
- Fixed the nightly build. <!-- id:b-o3 -->

### OlderCo <!-- id:r-older -->
Junior Engineer | Jan 2006 - Dec 2009
- Wrote Kubernetes-free shell scripts. <!-- id:b-p1 -->
- Answered support tickets. <!-- id:b-p2 -->

## Skills

- Platform: Kubernetes, Fortran <!-- id:s-1 -->
"""
SMALL_POSTING = ms.SelectionPosting("Staff Platform Engineer", "Requirements:\n- Kubernetes in production.\n- Fortran for the legacy solver.\n", "Acme")


def _by_bullets(per_two_pages: int):
    """A stand-in for the layout: 2 pages hold ``per_two_pages`` bullets. No renderer, the same answer everywhere."""

    def measure(result: TailoredResume) -> int:
        bullets = sum(len(entry.bullets) for section in result.sections for entry in section.entries)
        return 2 if bullets <= per_two_pages else 3

    return measure


def test_the_cut_order_on_a_small_master_lowest_value_first_the_evidence_last_and_a_conflict_said() -> None:
    master = parse_master(SMALL)
    candidates = tm.job_candidates(master, ms.SelectionProfile(titles=("Staff Platform Engineer",)), SMALL_POSTING, mode=tm.MODE_SHORTLIST, today=TODAY)
    assert candidates.selected.keywords is not None and set(candidates.selected.keywords.must) >= {"Kubernetes", "Fortran"}
    fortran = [requirement for requirement in candidates.selected.requirements if "Fortran" in requirement.text]
    assert fortran and all(requirement.mandatory and requirement.supporters == ("b-o1",) for requirement in fortran), "one line of the master, in an old role, names Fortran"
    settled, _ctx = _copied(candidates)
    ids = {line.id: tm.line_item_id(line) for section in settled.sections for line in section.all_lines()}
    cuts, refill = tm.cut_order(settled, candidates, master, today=TODAY)
    named = [(kind, ids[target]) for kind, target in cuts]

    # Lowest value for the posting first, whatever the role's age: the lines that support nothing (an old role's
    # before a recent role's of the same strength), then further lines for Kubernetes. The cut that would empty
    # OlderCo removes the role; each recent role keeps its best line. The evidence comes last: Kubernetes' in the
    # newest role is that role's last line and stays; Fortran's is the old role's only line left, so the role goes with it.
    assert named == [
        ("bullet", "b-p2"), ("bullet", "b-o3"), ("bullet", "b-o2"), ("bullet", "b-m3"), ("bullet", "b-n4"), ("bullet", "b-n3"),
        ("role", "r-older"), ("bullet", "b-m1"), ("bullet", "b-n2"), ("role", "r-old"),
    ]
    values = candidates.selected.values
    free = [item for kind, item in named[:6]]
    assert [values[item] for item in free] == sorted(values[item] for item in free) and max(values[item] for item in free) < values["b-o1"]
    assert {ids[target] for target in refill} == {"b-m3", "b-n4", "b-n3", "b-m1", "b-n2"}, "an old role's line that supports nothing never comes back"

    # A budget of 9 bullets: three lines that support nothing go, two of them the old role's. Of 4: OlderCo goes
    # whole, both recent roles are cut to what supports the posting, and Fortran is still shown, in its old role.
    nine = tm.fit_selected(settled, candidates, master, today=TODAY, measure=_by_bullets(9))
    assert {role: len(bullets) for role, bullets in _roles(nine).items()} == {"r-new": 4, "r-mid": 3, "r-old": 1, "r-older": 1}
    assert _roles(nine)["r-old"] == ["b-o1"] and nine.length is not None and nine.length.cut == ()
    four = tm.fit_selected(settled, candidates, master, today=TODAY, measure=_by_bullets(4))
    assert _roles(four) == {"r-new": ["b-n1", "b-n2"], "r-mid": ["b-m2"], "r-old": ["b-o1"]}
    assert [role.entry.heading[0].text for role in four.length.cut] == ["### OlderCo"]  # type: ignore[union-attr]
    record = tm.selection_record(master, candidates, four, today=TODAY)
    codes = {line.id: line.code for line in record.left_out}
    assert codes["b-p1"] == codes["b-p2"] == "cut_role_dropped" and codes["b-o2"] == codes["b-o3"] == codes["b-m1"] == "cut_lowest_value"
    assert [cut.id for cut in record.cut_for_length if cut.kind == "role"] == ["r-older"] and record.conflicts == ()
    assert next(line for line in record.picked if line.id == "b-o1").code == "requirement_evidence"
    # A budget nothing reaches: every cut stays applied, each recent role keeps its best line, and the record says
    # which requirement lost its evidence and that the resume is still over. Nothing mandatory goes silently.
    over = tm.fit_selected(settled, candidates, master, today=TODAY, measure=lambda _result: 3)
    assert over.length is not None and over.length.over() and _roles(over) == {"r-new": ["b-n1"], "r-mid": ["b-m2"]}
    record = tm.selection_record(master, candidates, over, today=TODAY)
    assert [conflict.kind for conflict in record.conflicts] == ["mandatory_evidence", "mandatory_evidence", "over_budget"]
    assert {conflict.requirement for conflict in record.conflicts[:2]} == {"Fortran for the legacy solver.", "Fortran"}
    assert all(conflict.ids == ("b-o1",) and "page limit" in conflict.reason for conflict in record.conflicts[:2])
    stored = record.to_json()
    assert stored["conflicts"][0]["kind"] == "mandatory_evidence" and tm.TailorSelection.from_json(stored) == record
    # No renderer: nothing is cut on a guess.
    unmeasured = tm.fit_selected(settled, candidates, master, today=TODAY, measure=lambda _result: None)
    assert unmeasured.length is not None and unmeasured.length.status == "unmeasured" and _shown_ids(unmeasured) == _shown_ids(settled)


def test_a_tailoring_that_leaves_out_a_requirements_evidence_keeps_the_next_best_line_and_the_record_says_what_is_missing() -> None:
    """The tailor call may not show every line. The evidence the fit protects is the best line SHOWN; a mandatory
    requirement the master supports with no line shown at all is a conflict on the record."""

    master = parse_master(SMALL)
    candidates = tm.job_candidates(master, ms.SelectionProfile(titles=("Staff Platform Engineer",)), SMALL_POSTING, mode=tm.MODE_SHORTLIST, today=TODAY)
    kubernetes = next(requirement for requirement in candidates.selected.requirements if requirement.text == "Kubernetes")
    assert kubernetes.supporters[:2] == ("b-n1", "b-n2")
    job, ctx = _job(candidates.posting), candidates.context()
    gone = {line.number for line in candidates.lines if line.item_id in ("b-n1", "b-o1")}
    answer = candidates.copy_all(withheld=frozenset(gone))
    settled = finish_tailoring(apply_no_loss(validate_tailored_output(answer, job, ctx), job, ctx, today=TODAY), job, ctx)
    shown = _shown_ids(settled)
    assert "b-n1" not in shown and "b-o1" not in shown

    assert "b-n2" in tm.shown_evidence(shown, candidates.selected), "the next best line shown is the evidence now"
    fitted = tm.fit_selected(settled, candidates, master, today=TODAY, measure=_by_bullets(3))
    assert "b-n2" in _shown_ids(fitted)
    record = tm.selection_record(master, candidates, fitted, today=TODAY)
    missing = [conflict for conflict in record.conflicts if conflict.kind == "mandatory_evidence"]
    assert {conflict.requirement for conflict in missing} == {"Fortran for the legacy solver.", "Fortran"}
    assert all("the tailoring did not show one" in conflict.reason for conflict in missing)


def test_a_record_without_a_conflict_is_stored_as_before_and_an_old_record_still_reads() -> None:
    master = parse_master(SMALL)
    candidates = tm.job_candidates(master, ms.SelectionProfile(titles=("Staff Platform Engineer",)), SMALL_POSTING, mode=tm.MODE_SHORTLIST, today=TODAY)
    settled, _ctx = _copied(candidates)
    record = tm.selection_record(master, candidates, tm.fit_selected(settled, candidates, master, today=TODAY, measure=_by_bullets(9)), today=TODAY)

    stored = record.to_json()
    assert record.conflicts == () and "conflicts" not in stored, "no key is added to a record that has nothing to report"
    assert tm.TailorSelection.from_json(stored) == record
    # A record stored by 0.1.10.10 (selector sel-1, its cut codes, no conflicts key) reads as it was written.
    old = {**stored, "selector_version": "sel-1", "candidates_version": "job-candidates:1"}
    old["cut_for_length"] = [{"id": "r-older", "kind": "role", "code": "cut_oldest_role_dropped", "reason": "cut for length: oldest role dropped"}]
    read = tm.TailorSelection.from_json(old)
    assert read.selector_version == "sel-1" and read.conflicts == () and read.to_json() == {**old, "counts": {**stored["counts"], "cut_for_length": 1}}
    with pytest.raises(Exception, match="conflict kind"):
        tm.TailorSelection.from_json({**stored, "conflicts": [{"kind": "other", "ids": [], "reason": "x"}]})


def test_a_tailoring_made_again_is_compared_with_the_stored_one_on_separate_checks() -> None:
    """The re-make rule for a job's tailoring (``compare_tailorings``): both selections against the same current
    master and requirements; the stored one is kept only when it is still valid and the new one regresses."""

    master = parse_master(SMALL)
    candidates = tm.job_candidates(master, ms.SelectionProfile(titles=("Staff Platform Engineer",)), SMALL_POSTING, mode=tm.MODE_SHORTLIST, today=TODAY)
    settled, _ctx = _copied(candidates)
    pages = lambda _markdown: (2, 0.5)  # noqa: E731 - both selections print on 2 pages here
    good = tm.selection_record(master, candidates, tm.fit_selected(settled, candidates, master, today=TODAY, measure=_by_bullets(9)), today=TODAY)
    thin = tm.selection_record(master, candidates, tm.fit_selected(settled, candidates, master, today=TODAY, measure=lambda _result: 3), today=TODAY)
    assert "b-o1" in [line.id for line in good.picked] and "b-o1" not in [line.id for line in thin.picked]

    same = tm.compare_tailorings(master, candidates, good, good, measure=pages)
    assert (same.decision, same.regressions) == ("new", ())
    kept = tm.compare_tailorings(master, candidates, good, thin, measure=pages)
    assert kept.decision == "previous" and kept.regressions[0].startswith("mandatory coverage: 2 requirement(s) with no line now") and kept.problems == ()
    better = tm.compare_tailorings(master, candidates, thin, good, measure=pages)
    assert better.decision == "new" and better.previous.lost and better.new.lost == ()
    # The stored tailoring shows a line the master corrected since: it cannot be kept, and the new one regresses: unresolved.
    stuck = tm.compare_tailorings(master, candidates, good, thin, stale=("b-n2",), measure=pages)
    assert stuck.decision == "unresolved" and "b-n2" in stuck.problems[0] and stuck.regressions


def test_room_the_last_cut_left_goes_back_to_a_recent_roles_line() -> None:
    master = parse_master(SMALL)
    candidates = tm.job_candidates(master, ms.SelectionProfile(titles=("Staff Platform Engineer",)), SMALL_POSTING, mode=tm.MODE_SHORTLIST, today=TODAY)
    settled, _ctx = _copied(candidates)

    def measure(result: TailoredResume) -> int:
        # The OlderCo heading costs as much as two lines: once the role is gone, one more line fits than before.
        lines = sum(len(entry.bullets) for section in result.sections for entry in section.entries)
        lines += 2 * sum(tm.line_item_id(entry.heading[0]) == "r-older" for section in result.sections for entry in section.entries)
        return 2 if lines <= 7 else 3

    fitted = tm.fit_selected(settled, candidates, master, today=TODAY, measure=measure)
    assert measure(fitted) == 2 and sum(len(bullets) for bullets in _roles(fitted).values()) == 7, "the page is full, not one line short"


# --- the record's contract ----------------------------------------------------------------------------


def test_the_selection_record_and_the_master_source_round_trip_and_refuse_a_broken_shape(master: Master) -> None:
    candidates = tm.job_candidates(master, _profile(), _posting(POSTINGS[0]), mode=tm.MODE_SHORTLIST, today=TODAY)
    settled, ctx = _copied(candidates)
    fitted = tm.fit_selected(tm.ensure_skills_line(settled, candidates, ctx), candidates, master, today=TODAY)
    record = tm.selection_record(master, candidates, fitted, picked_by=tm.PICKED_BY_CODE, fallback="model_unavailable", pins=("b-lum-01",), today=TODAY)
    assert tm.TailorSelection.from_json(json.loads(json.dumps(record.to_json()))) == record
    assert [line.id for line in record.picked] == list(dict.fromkeys(_shown_ids(fitted)))
    assert len(record.picked) + len(record.left_out) == sum(item.kind != "skills" for item in master.items.values())
    assert (record.candidates, record.candidates_version, record.selector_version) == (tm.MODE_SHORTLIST, tm.CANDIDATES_VERSION, ms.SELECTOR_VERSION)
    for broken in ({"picked_by": "someone"}, {"candidates": "whole"}, {"fallback": "Not A Code"}, {"picked": "b-lum-01"}):
        with pytest.raises(Exception, match="tailor_response.selection"):
            tm.TailorSelection.from_json({**record.to_json(), **broken})
    source = tm.MasterSource("revision_0", 2, "sha256:" + "a" * 64)
    assert tm.MasterSource.from_json(source.to_json()) == source
    with pytest.raises(Exception, match="revision must be a positive integer"):
        tm.MasterSource.from_json({**source.to_json(), "revision": 0})


# --- the stored master: one kept read -----------------------------------------------------------------


def test_the_stored_master_is_read_once_per_journal_head_and_a_home_without_one_adds_nothing_to_a_tailoring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    runner = CliRunner()
    setup_home(home, workpad_root=tmp_path / "workpads")
    assert runner.invoke(cli, ["scout", "install", "--home", str(home), "--json"]).exit_code == 0
    scout = home_scout_target(home)
    spawned: list[object] = []
    real = subprocess.Popen

    class Counting(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            spawned.append(args[0] if args else kwargs.get("args"))
            super().__init__(*args, **kwargs)

    resolved = resolve_workpad(home_root=home, requested_target=scout, gig_id=None, allow_semantic_state=True)
    monkeypatch.setattr(subprocess, "Popen", Counting)
    assert tm.stored_master(home, scout, resolved=resolved) is None
    spawned.clear()
    # What every tailoring and every pipeline digest pays on a home without a master: two small file reads.
    assert tm.stored_master(home, scout, resolved=resolved) is None and tm.digest_parts(home, scout, object(), resolved=resolved) == ()
    assert spawned == [], "a second look at an unchanged journal starts no subprocess"
    assert tm.tailoring_for_resume(type("Resume", (), {"profile_id": "profile_x"})(), TailorJob("t", "c", "", "text"), home_root=home, target=scout) is None
    assert tm.tailoring_for_resume(type("Resume", (), {"profile_id": None})(), TailorJob("t", "c", "", "text"), home_root=home, target=scout) is None

    done = runner.invoke(cli, ["scout", "resume", "master", "init", "--from", str(FIXTURES / "master.md"), "--home", str(home), "--json"])
    assert done.exit_code == 0, done.output
    stored = tm.stored_master(home, scout, resolved=resolved)
    assert stored is not None and stored.revision.revision == 1 and len(stored.master.items) == 176
    spawned.clear()
    assert tm.stored_master(home, scout, resolved=resolved) is stored and spawned == []
    assert tm.stored_master(home, scout) is stored, "resolved here: the same kept read"
    # A folder no gig is bound to has no master.
    assert tm.stored_master(home, tmp_path / "elsewhere") is None
