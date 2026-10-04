"""0.1.10.9 master P4: the rules of the master resume in the tailor path (``tailor_master``), without a model.

The candidate set of a job (three sizes), the numbering that ties every line to its master id, the Skills
line code assembles, the operator's decision 2 in a set wider than 2 pages, the fit's cut order and its
record, Picked / Left out, and the kept read of the stored master.  Synthetic data only: the spike's
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
    assert candidates.skills_line is not None and 0 < len(candidates.skills) <= ms.MAX_SKILLS < len(master.skills())


def test_the_three_candidate_sets_are_the_view_the_pick_before_its_fit_and_the_evidence_bullets(master: Master) -> None:
    profile, posting = _profile(), _posting(POSTINGS[0])
    view, shortlist, evidence = (tm.job_candidates(master, profile, posting, mode=mode, today=TODAY) for mode in tm.CANDIDATE_MODES)
    selected = ms.select(master, profile, posting, today=TODAY)

    # view: exactly the selector's 2-page selection.
    assert view.markdown == selected.markdown and view.item_ids() == frozenset(selected.item_ids())
    # shortlist: the selector's pick before its page fit: every role, the caps it picks under, about twice what fits.
    assert set(shortlist.entries) >= set(OLD_ROLES) | set(RECENT_ROLES)
    assert [len(shortlist.entries[role]) for role in RECENT_ROLES] == [9, 6, 6, 5] and all(len(shortlist.entries[role]) == 3 for role in OLD_ROLES)
    assert shortlist.bullet_count == 44 > view.bullet_count
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
def test_an_old_roles_line_is_cut_when_a_recent_role_names_the_same_must_have_and_kept_when_none_does(master: Master, profile_id: str) -> None:
    # Core infrastructure asks for Linux: a recent role's line names it, so it is offered in the old line's place.
    with_stand_in = tm.job_candidates(master, _profile(profile_id), _posting(POSTINGS[1]), mode=tm.MODE_SHORTLIST, today=TODAY)
    ((added, (old_line, terms)),) = with_stand_in.stand_ins.items()
    assert master.items[added].entry_id in RECENT_ROLES and master.items[old_line].entry_id in OLD_ROLES and terms == ("Linux",)
    assert added in with_stand_in.entries[master.items[added].entry_id or ""]
    settled, ctx = _copied(with_stand_in)
    fitted = tm.fit_selected(tm.ensure_skills_line(settled, with_stand_in, ctx), with_stand_in, master, today=TODAY)
    assert tm.measure_pages(fitted) == 2 and not set(_roles(fitted)) & set(OLD_ROLES), "every old role went: the recent line shows Linux"
    assert added in _shown_ids(fitted) and mentions(render_markdown(fitted), "Linux")
    record = tm.selection_record(master, with_stand_in, fitted, today=TODAY)
    assert next(line for line in record.picked if line.id == added).code == "shown_instead"

    # The ML platform posting asks for SQL: only an old role's line names it, so that line stays and its role is shortened to it.
    only_old = tm.job_candidates(master, _profile(profile_id), _posting(POSTINGS[2]), mode=tm.MODE_SHORTLIST, today=TODAY)
    assert only_old.stand_ins == {}
    settled, ctx = _copied(only_old)
    fitted = tm.fit_selected(tm.ensure_skills_line(settled, only_old, ctx), only_old, master, today=TODAY)
    roles = _roles(fitted)
    kept = [role for role in roles if role in OLD_ROLES]
    assert tm.measure_pages(fitted) == 2 and kept == ["r-cas"] and len(roles["r-cas"]) == 1
    assert mentions(master.items[roles["r-cas"][0]].text, "SQL")


@pytest.mark.parametrize("mode", [tm.MODE_SHORTLIST, tm.MODE_EVIDENCE])
@pytest.mark.parametrize("posting_id", POSTINGS)
def test_the_fit_reaches_two_pages_oldest_roles_first_with_every_recent_role_at_its_floor_or_above(master: Master, mode: str, posting_id: str) -> None:
    candidates = tm.job_candidates(master, _profile("profile-swe"), _posting(posting_id), mode=mode, today=TODAY)
    settled, ctx = _copied(candidates)
    whole = tm.ensure_skills_line(settled, candidates, ctx)
    cuts, refill = tm.cut_order(whole, candidates, master, today=TODAY)
    fitted = tm.fit_selected(whole, candidates, master, today=TODAY)

    assert tm.measure_pages(fitted) == 2 and fitted.length is not None and fitted.length.status == STATUS_CUT
    assert fitted.length.pages == 2 < fitted.length.full_pages  # type: ignore[operator]
    roles = _roles(fitted)
    for index, role in enumerate(RECENT_ROLES):
        assert len(roles[role]) >= ms.FLOORS[index], f"{role} is under its floor"
    # The order: every cut of an old role comes before any cut of a recent role or a project, oldest role first.
    by_line = {line.id: tm.line_item_id(line) for section in whole.sections for line in section.all_lines()}
    owners = [master.items[by_line[target]].entry_id if kind == "bullet" else by_line[target] for kind, target in cuts]  # type: ignore[index]
    old_cuts = [owner for owner in owners if owner in OLD_ROLES]
    assert owners[: len(old_cuts)] == old_cuts and old_cuts == sorted(old_cuts, key=OLD_ROLES.index)
    assert all(master.items[by_line[target]].entry_id not in OLD_ROLES for target in refill)  # type: ignore[index]
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


def test_the_cut_order_on_a_small_master_oldest_role_from_the_end_the_only_evidence_kept_then_the_lowest_value_line() -> None:
    master = parse_master(SMALL)
    candidates = tm.job_candidates(master, ms.SelectionProfile(titles=("Staff Platform Engineer",)), SMALL_POSTING, mode=tm.MODE_SHORTLIST, today=TODAY)
    assert candidates.selected.keywords is not None and set(candidates.selected.keywords.must) >= {"Kubernetes", "Fortran"}
    assert candidates.stand_ins == {}, "no recent role names Fortran, so nothing can stand in for the old line"
    settled, _ctx = _copied(candidates)
    ids = {line.id: tm.line_item_id(line) for section in settled.sections for line in section.all_lines()}
    cuts, refill = tm.cut_order(settled, candidates, master, today=TODAY)
    named = [(kind, ids[target]) for kind, target in cuts]

    # OlderCo: its bullets from the END of the tailoring's list, then the role. OldCo: its bullets from the end, but
    # the Fortran line is the only line naming a must-have, so it stays and the role with it.
    older = list(candidates.entries["r-older"])
    old = [bullet for bullet in candidates.entries["r-old"] if bullet != "b-o1"]
    assert named[: len(older) + 1 + len(old)] == [*(("bullet", bullet) for bullet in older[::-1]), ("role", "r-older"), *(("bullet", bullet) for bullet in old[::-1])]
    assert ("bullet", "b-o1") not in named and ("role", "r-old") not in named
    # Then the recent roles down to their floors (3 for the newest, 2 for the next), never an old role's line again.
    recent = named[len(older) + 1 + len(old) :]
    assert sorted(master.items[item].entry_id or "" for _kind, item in recent) == ["r-mid", "r-new"] and {kind for kind, _item in recent} == {"bullet"}
    # The line that goes first is the one worth least for the posting, whichever recent role it ends.
    values = candidates.selected.values
    assert [values[item] for _kind, item in recent] == sorted(values[item] for _kind, item in recent)
    assert refill == {target for kind, target in cuts if ids[target] in {item for _kind, item in recent}}

    # A budget of 9 bullets: only the oldest role goes. Of 5: both recent roles sit at their floors and Fortran is still shown.
    nine = tm.fit_selected(settled, candidates, master, today=TODAY, measure=_by_bullets(9))
    assert list(_roles(nine)) == ["r-new", "r-mid", "r-old"] and [role.entry.heading[0].text for role in nine.length.cut] == ["### OlderCo"]  # type: ignore[union-attr]
    five = tm.fit_selected(settled, candidates, master, today=TODAY, measure=_by_bullets(6))
    assert {role: len(bullets) for role, bullets in _roles(five).items()} == {"r-new": 3, "r-mid": 2, "r-old": 1}
    assert _roles(five)["r-old"] == ["b-o1"]
    record = tm.selection_record(master, candidates, five, today=TODAY)
    codes = {line.id: line.code for line in record.left_out}
    assert codes["b-p1"] == codes["b-p2"] == "cut_oldest_role_dropped" and codes["b-o2"] == codes["b-o3"] == "cut_oldest_role_shortened"
    assert {codes[item] for _kind, item in recent} == {"cut_lowest_value"}
    assert [cut.id for cut in record.cut_for_length if cut.kind == "role"] == ["r-older"]
    # A budget nothing reaches: every cut stays applied and the record says the resume is still over.
    over = tm.fit_selected(settled, candidates, master, today=TODAY, measure=lambda _result: 3)
    assert over.length is not None and over.length.over() and {role: len(bullets) for role, bullets in _roles(over).items()} == {"r-new": 3, "r-mid": 2, "r-old": 1}
    # No renderer: nothing is cut on a guess.
    unmeasured = tm.fit_selected(settled, candidates, master, today=TODAY, measure=lambda _result: None)
    assert unmeasured.length is not None and unmeasured.length.status == "unmeasured" and _shown_ids(unmeasured) == _shown_ids(settled)


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
    assert runner.invoke(cli, ["setup", "--non-interactive", "--home", str(home), "--workpad-root", str(tmp_path / "workpads"), "--editor", "/usr/bin/true", "--json"]).exit_code == 0
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
