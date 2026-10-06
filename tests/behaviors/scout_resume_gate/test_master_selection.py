"""0.1.10.9 master P2: the code selector, on the SYNTHETIC master of the master-resume spike.

``master_selection.select`` picks the lines a resume shows from the whole master and fits them to 2
pages by measuring with the shipped PDF template; ``gigai scout resume master selection show`` is its
CLI. Everything here is synthetic (``tests/evals/fixtures/master``: an invented person, 2 profiles, 3
postings) and nothing calls a model: the one model-shaped thing, a stored assessment's posting, is
made with the scripted test transport. Every CLI test runs against a temp ``--home``.

The golden cases pin ``today`` to 2026-10-03 (which roles are "old" depends on the year); what they
expect is ``selection-golden.json``, written for ``SELECTOR_VERSION`` ``sel-5`` (0110-10-15: requirement
coverage, then evidence strength, then pins, recency only as the tie-break; the Skills section kept whole;
a role or project the posting's title names keeps its best line).
The labelled eval of that rule is ``tests/evals/run_pick_eval.py`` (``test_pick_eval.py``).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import master_selection as ms
from gigai.scout.master_resume import Master, parse_master
from gigai.scout.posting_keywords import mentions
from gigai.scout.resume_pdf import ResumeMarkdownError, measure_markdown, parse_resume_markdown, render_markdown_pdf

from tests.support.setup_home import setup_home

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "master"
TODAY = date(2026, 10, 3)
STAMP = datetime(2026, 10, 3, tzinfo=timezone.utc)
PROFILE_IDS = ("profile-ai", "profile-swe")
POSTING_IDS = ("p1-staff-ai-agent-platform", "p2-staff-swe-core-infrastructure", "p3-staff-engineer-ml-platform")
#: Oldest first: the three roles of the synthetic master that ended more than 8 years before 2026.
OLD_ROLES = ("r-tes", "r-bri", "r-cas")
RECENT_ROLES = ("r-lum", "r-kes", "r-hex", "r-fin")


@pytest.fixture(scope="module")
def master() -> Master:
    return parse_master((FIXTURES / "master.md").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def golden() -> dict[str, dict[str, object]]:
    data = json.loads((FIXTURES / "selection-golden.json").read_text(encoding="utf-8"))
    assert data["selector_version"] == ms.SELECTOR_VERSION and data["today"] == TODAY.isoformat()
    return data["cases"]


def _profile(profile_id: str) -> ms.SelectionProfile:
    raw = next(item for item in json.loads((FIXTURES / "profiles.json").read_text(encoding="utf-8"))["profiles"] if item["profile_id"] == profile_id)
    return ms.SelectionProfile(tuple(raw["titles"]), tuple(raw["focus_tags"]), None, raw["profile_id"], raw["label"])


def _posting_parts(posting_id: str) -> tuple[dict[str, str], str]:
    head, _, body = (FIXTURES / "postings" / f"{posting_id}.md").read_text(encoding="utf-8").partition("\n\n")
    return dict(line.split(": ", 1) for line in head.splitlines()), body.strip() + "\n"


def _posting(posting_id: str) -> ms.SelectionPosting:
    meta, body = _posting_parts(posting_id)
    return ms.SelectionPosting(meta["title"], body, meta["company"], meta.get("location", ""))


_SELECTED: dict[tuple[str, str | None], ms.Selected] = {}


def _selected(master: Master, profile_id: str, posting_id: str | None) -> ms.Selected:
    """One real selection (the shipped template measures it), made once per module."""

    key = (profile_id, posting_id)
    if key not in _SELECTED:
        _SELECTED[key] = ms.select(master, _profile(profile_id), _posting(posting_id) if posting_id else None, today=TODAY)
    return _SELECTED[key]


def _by_bullets(per_page: int) -> ms.Measure:
    """A stand-in for the layout: ``per_page`` bullet lines to a page. No renderer, same answer everywhere."""

    def measure(markdown: str) -> tuple[int, float]:
        bullets = sum(line.startswith("- ") for line in markdown.splitlines())
        pages = max(1, math.ceil(bullets / per_page))
        return pages, (bullets - (pages - 1) * per_page) / per_page

    return measure


def _shown_lines(markdown: str) -> list[str]:
    return [line[2:] for line in markdown.splitlines() if line.startswith("- ")]


# --- golden: 2 profiles x 3 postings fit 2 pages ----------------------------------------------


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
@pytest.mark.parametrize("posting_id", POSTING_IDS)
def test_a_job_selection_from_the_whole_master_fits_two_pages(master: Master, golden: dict, profile_id: str, posting_id: str) -> None:
    selected = _selected(master, profile_id, posting_id)

    # The END outcome: the PDF the shipped renderer makes of this markdown (auto fit, as a user's PDF) has 2 pages.
    assert selected.pages_before_fit > 2, "the pick must have needed the fit, or this proves nothing"
    assert selected.fits and selected.pages == 2
    rendered = render_markdown_pdf(selected.markdown, None, timestamp=STAMP)
    assert rendered.pages == 2
    assert rendered.spacing_scale >= ms.FIT_SCALE, "the budget is 2 pages at 0.9x spacing or looser"

    # Exactly the golden pick (a change here is a selector change: bump SELECTOR_VERSION, regenerate the file).
    expected = golden[f"{profile_id}/{posting_id}"]
    assert list(selected.summary) == expected["summary"]
    assert {key: list(value) for key, value in selected.entries.items()} == expected["entries"]
    assert list(selected.other) == expected["other"]
    assert list(selected.skills) == expected["skills"]
    assert [cut.id for cut in selected.cut_for_length] == expected["cut_for_length"]
    assert selected.pages_before_fit == expected["pages_before_fit"]

    # Nothing is reworded: every printed line is a master line, word for word, under its own id.
    texts = {item.text for item in master.items.values()}
    printed = [line for line in _shown_lines(selected.markdown) if line != ", ".join(selected.skills)]
    assert printed and all(line in texts for line in printed)
    assert [master.items[item_id].text for item_id in selected.item_ids()] == printed
    # The Skills section: this master lists 79 names, more than prints whole (``SKILLS_WHOLE``). What the posting
    # asks for and what the shown lines name are all there; only names past that count that nothing asks for
    # were cut, and each cut is on the record.
    left_out = [skill for skill in selected.skill_reasons if not skill.picked]
    assert set(selected.skills) <= set(master.skills()) and ms.SKILLS_WHOLE <= len(selected.skills) < 79
    assert left_out and {skill.code for skill in left_out} == {"cut_for_length"}
    assert {skill.name for skill in left_out} == {cut.id for cut in selected.cut_for_length if cut.kind == "skill"}
    assert {skill.code for skill in selected.skill_reasons if skill.picked and skill.code != "listed"} <= {"posting_must", "posting_nice", "named_by_line"}
    shown_text = " ".join(master.items[item_id].text for item_id in selected.item_ids())
    assert not any(mentions(shown_text, skill.name) or mentions(_posting(posting_id).text, skill.name) for skill in left_out)
    assert selected.conflicts == () and [conflict for conflict in expected["conflicts"]] == []
    assert {key: list(value) for key, value in selected.evidence_for.items()} == expected["evidence_for"]
    # Every must-have of the posting that the master can show at all is on the resume.
    assert selected.coverage["must_missing"] == selected.coverage["must_missing_in_master"] == ()
    assert selected.coverage["nice_missing"] == ()


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
def test_a_profiles_standing_selection_fits_two_pages(master: Master, golden: dict, profile_id: str) -> None:
    selected = _selected(master, profile_id, None)

    assert selected.pages_before_fit > 2 and selected.fits
    assert render_markdown_pdf(selected.markdown, None, timestamp=STAMP).pages == 2
    expected = golden[f"{profile_id}/base"]
    assert list(selected.summary) == expected["summary"]
    assert {key: list(value) for key, value in selected.entries.items()} == expected["entries"]
    assert selected.keywords is None and selected.coverage == {}


def test_the_same_input_gives_the_same_selection(master: Master) -> None:
    first = _selected(master, "profile-ai", POSTING_IDS[0])
    again = ms.select(master, _profile("profile-ai"), _posting(POSTING_IDS[0]), today=TODAY)

    assert again == first
    assert again.to_json(master) == first.to_json(master)


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
def test_most_of_a_job_selection_is_not_in_the_profiles_standing_selection(master: Master, profile_id: str) -> None:
    """Why a job selection starts from the whole master (DESIGN 4.1): the profile's 2 pages do not hold it."""

    base = set(_selected(master, profile_id, None).item_ids())
    for posting_id in POSTING_IDS:
        bullets = [item for item in _selected(master, profile_id, posting_id).item_ids() if item.startswith("b-")]
        outside = [item for item in bullets if item not in base]
        assert len(outside) / len(bullets) > 0.3, (posting_id, len(outside), len(bullets))


# --- the summary fault of the spike (DESIGN 9.2) ----------------------------------------------


def test_the_ai_agent_posting_gets_the_ai_summary_not_the_infrastructure_one(master: Master) -> None:
    """The spike's first selector chose the summary by keyword value and showed the infrastructure summary for the
    AI agent posting. The posting's title and the profile decide; keywords only break ties."""

    posting = _posting("p1-staff-ai-agent-platform")
    assert posting.title == "Staff AI Engineer, Agent Platform"
    for profile_id in PROFILE_IDS:
        # The fault's cause is present in this fixture: counted by the posting's keywords alone, the
        # infrastructure summary names more of them (weighted: a must-have 2, a nice-to-have 1).
        keywords = ms.select(master, _profile(profile_id), posting, today=TODAY, measure=_by_bullets(40)).keywords
        assert keywords is not None
        must, nice = keywords.must, keywords.nice

        def keyword_weight(item_id: str) -> int:
            text = master.items[item_id].text
            return 2 * sum(mentions(text, term) for term in must) + sum(mentions(text, term) for term in nice)

        assert keyword_weight("sum-backend") > keyword_weight("sum-ai")

        selected = _selected(master, profile_id, "p1-staff-ai-agent-platform")
        assert selected.summary == ("sum-ai",), profile_id
        assert selected.markdown.startswith("## Summary\n\n- Staff engineer with 16 years of experience who builds LLM agent platforms")
        reasons = {line.id: line for line in selected.lines}
        assert reasons["sum-ai"].picked and not reasons["sum-backend"].picked
        assert reasons["sum-backend"].code == "summary_other_variant"
        # The evidence view shows one summary too, chosen the same way.
        assert ms.evidence_view(master, _profile(profile_id), posting, today=TODAY).summary == ("sum-ai",)


def test_each_posting_gets_the_summary_its_title_names(master: Master) -> None:
    chosen = {(profile_id, posting_id): _selected(master, profile_id, posting_id).summary[0] for profile_id in PROFILE_IDS for posting_id in POSTING_IDS}

    assert chosen[("profile-swe", "p2-staff-swe-core-infrastructure")] == "sum-backend"
    assert chosen[("profile-ai", "p3-staff-engineer-ml-platform")] == chosen[("profile-swe", "p3-staff-engineer-ml-platform")] == "sum-mlplat"
    # With no posting the profile alone decides.
    assert _selected(master, "profile-ai", None).summary == ("sum-ai",)
    assert _selected(master, "profile-swe", None).summary == ("sum-backend",)


# --- the cut order (0110-10-15: value for the posting, never age alone) ------------------------


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
@pytest.mark.parametrize("posting_id", POSTING_IDS)
def test_recent_roles_always_appear_and_what_supports_nothing_goes_first(master: Master, profile_id: str, posting_id: str) -> None:
    selected = _selected(master, profile_id, posting_id)

    # The page constraint: every recent role is present, and no role or project is printed without a bullet.
    for index, role in enumerate(RECENT_ROLES):
        assert len(selected.entries[role]) >= ms.FLOORS[index] >= 1, role
    assert all(bullets for entry_id, bullets in selected.entries.items() if master.entries[entry_id].section != "education")
    # No requirement's evidence was cut, and nothing mandatory is left without a line: no conflict.
    cut = {item.id for item in selected.cut_for_length}
    evidence = {requirement.supporters[0] for requirement in selected.requirements if requirement.mandatory and requirement.supporters}
    assert evidence and not evidence & cut and evidence <= set(selected.item_ids())
    assert selected.conflicts == ()
    # The bullets were cut lowest value first (``values``: a line's place in the keep order), so every bullet cut
    # is worth less than any requirement's evidence; a line that supports no requirement at all was among them.
    bullets = [item.id for item in selected.cut_for_length if item.kind == "bullet"]
    places = [selected.values[item_id] for item_id in bullets]
    assert places == sorted(places) and max(places) < min(selected.values[item_id] for item_id in evidence)
    supports = {item_id for requirement in selected.requirements for item_id in requirement.supporters}
    assert any(item_id not in supports for item_id in bullets)
    # Whatever is shown beyond the evidence is worth more than whatever of the same kind was cut for length.
    shown_bullets = [item_id for entry_id, shown in selected.entries.items() if master.entries[entry_id].section == "projects" for item_id in shown]
    cut_projects = [item_id for item_id in bullets if master.entries[master.items[item_id].entry_id].section == "projects" and master.items[item_id].entry_id in selected.entries]
    assert not shown_bullets or not cut_projects or min(selected.values[item_id] for item_id in shown_bullets) > max(selected.values[item_id] for item_id in cut_projects)
    # An old role goes whole when none of its lines is evidence; each cut says why, in the words the user reads.
    for role in selected.roles_dropped:
        assert role in OLD_ROLES and role not in selected.entries
        assert not set(master.entries[role].bullets) & evidence
    reasons = {line.id: line for line in selected.lines}
    assert {"r-tes", "r-bri"} <= set(selected.roles_dropped)
    assert all(reasons[bullet].code in {"cut_role_dropped", "role_dropped"} for bullet in master.entries["r-tes"].bullets)
    assert {item.code for item in selected.cut_for_length} <= {"cut_role_dropped", "cut_lowest_value", "cut_for_length"}
    assert {item.kind for item in selected.cut_for_length if item.code == "cut_for_length"} == {"skill"}


def test_a_tighter_budget_only_applies_more_of_the_same_cut_order(master: Master) -> None:
    """One fixed order: what a looser budget cut is the start of what a tighter one cuts."""

    profile, posting = _profile("profile-swe"), _posting("p2-staff-swe-core-infrastructure")
    cuts = [
        [(cut.kind, cut.id) for cut in ms.select(master, profile, posting, today=TODAY, measure=_by_bullets(per_page), fill=False).cut_for_length if cut.kind != "role"]
        for per_page in (30, 22, 16, 12)
    ]

    assert [len(item) for item in cuts] == sorted(len(item) for item in cuts) and len(cuts[0]) < len(cuts[-1])
    for looser, tighter in zip(cuts, cuts[1:]):
        assert tighter[: len(looser)] == looser


def test_a_pick_that_cannot_fit_says_so_keeps_every_recent_role_and_reports_the_conflict(master: Master) -> None:
    selected = ms.select(master, _profile("profile-ai"), _posting("p1-staff-ai-agent-platform"), today=TODAY, measure=lambda _markdown: (3, 0.5))

    assert not selected.fits and selected.pages == 3 and selected.to_json(master)["fits"] is False
    assert selected.added_to_fill == ()
    # Every cut the rules allow was made: each recent role keeps its one best line, the old roles, the projects,
    # the Other lines and the skills are gone.
    assert {role: len(selected.entries[role]) for role in RECENT_ROLES} == {role: 1 for role in RECENT_ROLES}
    assert set(selected.entries) == set(RECENT_ROLES) | {entry.id for entry in master.entries_in("education")}
    assert selected.other == () and selected.skills == ()
    assert {skill.code for skill in selected.skill_reasons} == {"cut_for_length"}
    # Nothing mandatory went silently: the result names every requirement that lost its evidence, and the page count.
    kinds = [conflict.kind for conflict in selected.conflicts]
    assert kinds.count("over_budget") == 1 and "mandatory_evidence" in kinds
    lost = [conflict for conflict in selected.conflicts if conflict.kind == "mandatory_evidence"]
    assert all(conflict.requirement and conflict.ids and conflict.ids[0] not in selected.item_ids() for conflict in lost)
    assert any(not conflict.covered for conflict in lost)
    out = selected.to_json(master)
    assert out["counts"]["conflicts"] == len(selected.conflicts) and out["conflicts"][0]["reason"]
    assert {cut.code for cut in selected.cut_for_length} >= {"cut_conflict", "cut_for_length"}


# --- a requirement's evidence stays, wherever its role stands in time -------------------------


def test_an_old_roles_line_that_is_the_only_evidence_is_kept_and_its_role_stays(master: Master) -> None:
    """ML platform posting: SQL is a must-have and one bullet of the master names it, in a role that ended in 2015."""

    naming = [item.id for item in master.items.values() if item.kind == "bullet" and mentions(item.text, "SQL")]
    assert naming == ["b-cas-08"] and master.items["b-cas-08"].entry_id == "r-cas"
    for profile_id in PROFILE_IDS:
        selected = _selected(master, profile_id, "p3-staff-engineer-ml-platform")
        assert selected.keywords is not None and "SQL" in selected.keywords.must
        asked = next(requirement for requirement in selected.requirements if requirement.text == "SQL")
        assert asked.mandatory and asked.supporters == ("b-cas-08",)
        # The old role is shown for that line while recent lines that support nothing were cut; the older two roles went.
        assert "b-cas-08" in selected.entries["r-cas"] and selected.roles_dropped == ("r-bri", "r-tes")
        assert asked.id in selected.evidence_for["b-cas-08"]
        reason = next(line for line in selected.lines if line.id == "b-cas-08")
        assert reason.picked and reason.code == "requirement_evidence" and reason.reason == "the strongest evidence for: SQL"
        cut = [item.id for item in selected.cut_for_length]
        assert "b-cas-08" not in cut
        recent_cut = [item_id for item_id in cut if item_id in master.items and master.items[item_id].entry_id in RECENT_ROLES]
        assert recent_cut and all(selected.values[item_id] < selected.values["b-cas-08"] for item_id in recent_cut)


def test_a_recent_line_is_the_evidence_when_it_supports_a_must_have_as_well_as_an_old_one(master: Master) -> None:
    """Core infrastructure posting: Linux is a must-have. A 2012-2015 role names it and so do lines of a 2019-2023
    role: of lines of equal strength the recent one is the evidence, and the old line is not needed for it."""

    for profile_id in PROFILE_IDS:
        selected = _selected(master, profile_id, "p2-staff-swe-core-infrastructure")
        assert selected.keywords is not None and "Linux" in selected.keywords.must
        asked = next(requirement for requirement in selected.requirements if requirement.text == "Linux")
        assert set(asked.supporters) == {"b-hex-12", "b-hex-28", "b-cas-07"}
        evidence = asked.supporters[0]
        assert master.items[evidence].entry_id == "r-hex" and master.items["b-cas-07"].entry_id == "r-cas"
        assert master.items[evidence].strength == master.items["b-cas-07"].strength, "recency only breaks the tie"
        assert evidence in selected.entries["r-hex"] and asked.id in selected.evidence_for[evidence]
        assert "b-cas-07" not in selected.item_ids()
        # sel-1's stand-in fields are no longer filled: the evidence is chosen from the whole master up front.
        assert selected.shown_instead == {} and selected.only_evidence == {}


# --- the reason per line ----------------------------------------------------------------------


@pytest.mark.parametrize("posting_id", [None, *POSTING_IDS])
def test_every_line_of_the_master_is_picked_or_left_out_with_a_reason(master: Master, posting_id: str | None) -> None:
    selected = _selected(master, "profile-ai", posting_id)

    selectable = [item.id for item in master.items.values() if item.kind != "skills"]
    assert [line.id for line in selected.lines] == selectable and len(selectable) == 166
    assert {line.id for line in selected.lines if line.picked} == set(selected.item_ids())
    assert all(line.code and line.reason for line in selected.lines)
    # The fallback reason is never needed: each left-out line has a specific one.
    assert "not_picked" not in {line.code for line in selected.lines}
    assert {line.code for line in selected.lines if line.picked} <= {
        "requirement_evidence", "names_keywords", "posting_wording", "profile_focus", "strongest_remaining", "general", "room_left",
        "summary_variant", "pinned", "title_entry", "posting_title",
    }
    # Every skill of the master is accounted for too, once: shown, or cut for length and said so.
    assert sorted(skill.name for skill in selected.skill_reasons) == sorted(master.skills())
    assert [skill.name for skill in selected.skill_reasons if skill.picked] == list(selected.skills)
    assert len(selected.skills) >= ms.SKILLS_WHOLE and {skill.code for skill in selected.skill_reasons if not skill.picked} <= {"cut_for_length"}

    out = selected.to_json(master)
    assert [row["id"] for row in out["picked"]] == list(selected.item_ids())
    assert len(out["picked"]) + len(out["left_out"]) == 166
    assert all(row["text"] == master.items[row["id"]].text and row["reason"] for row in (*out["picked"], *out["left_out"]))
    assert out["counts"]["picked"] == len(out["picked"]) and out["counts"]["left_out"] == len(out["left_out"])


def test_the_skills_section_is_kept_whole_what_the_posting_asks_for_first(master: Master) -> None:
    selected = _selected(master, "profile-ai", "p1-staff-ai-agent-platform")
    assert selected.keywords is not None
    shown_text = " ".join(master.items[bullet].text for bullets in selected.entries.values() for bullet in bullets)

    assert len(master.skills()) == 79 > len(selected.skills) >= ms.SKILLS_WHOLE
    for skill in (skill for skill in selected.skill_reasons if skill.picked):
        atoms = ms.skill_atoms(skill.name)
        if skill.code == "posting_must":
            assert any(mentions(atom, term) for atom in atoms for term in selected.keywords.must), skill.name
        elif skill.code == "posting_nice":
            assert any(mentions(atom, term) for atom in atoms for term in selected.keywords.nice), skill.name
        elif skill.code == "named_by_line":
            assert any(mentions(shown_text, atom) for atom in atoms), skill.name
        else:
            assert skill.code == "listed"
    # Must-haves come first, then nice-to-haves; what no line and no posting names comes last.
    codes = [skill.code for skill in selected.skill_reasons if skill.picked]
    order = ["posting_must", "posting_nice", "named_by_line", "listed"]
    asked = [code for code in codes if code in order[:2]]
    assert asked == sorted(asked, key=order.index) and codes[: len(asked)] == asked
    # Too long to print whole: the names cut are ones nothing asks for and no picked line names, never another.
    cut = [skill for skill in selected.skill_reasons if not skill.picked]
    assert cut and all(skill.code == "cut_for_length" for skill in cut)
    assert not any(mentions(atom, term) for skill in cut for atom in ms.skill_atoms(skill.name) for term in (*selected.keywords.must, *selected.keywords.nice))


_GROUPED = """<!-- gigai-master:1 -->

## Experience

### Newco <!-- id:r-new -->
Staff Engineer | Jun 2021 - Present
- Ran the payments API in Go on Kubernetes for 40 teams. <!-- id:b-new-1 -->
- Mentored 3 engineers. <!-- id:b-new-2 -->

## Skills

- Python/Go/TypeScript · PostgreSQL/Redis · CI/CD · Model Context Protocol (MCP) <!-- id:s-1 -->
"""


def test_a_skill_is_matched_inside_its_group_whatever_the_master_groups() -> None:
    """``Python/Go/TypeScript`` is one name on the Skills line and three skills to match: a posting that asks for Go
    reads ``Go`` as a keyword, and the group prints whole."""

    assert ms.skill_atoms("Python/Go/TypeScript") == ("Python", "Go", "TypeScript")
    assert ms.skill_atoms("Model Context Protocol (MCP)") == ("Model Context Protocol", "MCP")
    assert ms.skill_atoms("CI/CD") == ("CI/CD",) and ms.skill_atoms("A/B testing") == ("A/B testing",)
    grouped = parse_master(_GROUPED)
    flat = parse_master(_GROUPED.replace("Python/Go/TypeScript · PostgreSQL/Redis", "Python, Go, TypeScript, PostgreSQL, Redis"))
    posting = ms.SelectionPosting("Staff Engineer", "Requirements:\n- Go services in production.\n- Redis.\n\nNice to have:\n- MCP.\n")

    one = ms.select(grouped, ms.SelectionProfile(), posting, today=TODAY, measure=_by_bullets(40))
    two = ms.select(flat, ms.SelectionProfile(), posting, today=TODAY, measure=_by_bullets(40))
    assert one.keywords == two.keywords and one.keywords is not None
    assert one.keywords.must == ("Go", "Redis") and one.keywords.nice == ("MCP",)
    # In the posting's order (Go, Redis, then the nice-to-have MCP), then the rest; each group printed whole.
    assert one.skills == ("Python/Go/TypeScript", "PostgreSQL/Redis", "Model Context Protocol (MCP)", "CI/CD")
    assert [skill.code for skill in one.skill_reasons] == ["posting_must", "posting_must", "posting_nice", "listed"]
    assert one.item_ids() == two.item_ids()


# --- the pure rules, on small inputs (no renderer) --------------------------------------------

_SMALL = """<!-- gigai-master:1 -->

## Summary

- Platform engineer who runs payment systems. <!-- id:sum-platform tags:backend -->
- Data engineer who builds pipelines. <!-- id:sum-data tags:data -->

## Experience

### Newco <!-- id:r-new -->
Staff Engineer | Jun 2021 - Present
- Cut deploy time from 40 minutes to 6 with a staged pipeline on Kubernetes. <!-- id:b-new-1 tags:backend -->
- Ran the on-call rotation for 4 teams. <!-- id:b-new-2 -->
- Wrote the incident review guide. <!-- id:b-new-3 -->
- Mentored 3 engineers. <!-- id:b-new-4 -->

### Oldco <!-- id:r-old -->
Engineer | Jan 2010 - Dec 2013
- Wrote the Fortran solver that prices 2 million contracts a night. <!-- id:b-old-1 -->
- Kept the build green. <!-- id:b-old-2 -->

## Skills

- Languages: Python, Fortran, Kubernetes <!-- id:s-lang -->

## Other

- Certification: Certified Kubernetes Administrator (2020) <!-- id:o-cka tags:certification -->
"""


def test_a_profile_is_just_its_titles_when_it_has_no_focus_tags() -> None:
    small = parse_master(_SMALL)

    assert ms.select(small, ms.SelectionProfile(titles=("Staff Data Engineer",)), None, today=TODAY, measure=_by_bullets(40)).summary == ("sum-data",)
    assert ms.select(small, ms.SelectionProfile(titles=("Staff Backend Engineer",)), None, today=TODAY, measure=_by_bullets(40)).summary == ("sum-platform",)
    # A stored selection (P3) is the prior when a profile has one.
    stored = ms.SelectionProfile(titles=("Staff Backend Engineer",), base_ids=("sum-data",))
    assert ms.select(small, stored, None, today=TODAY, measure=_by_bullets(40)).summary == ("sum-data",)


def test_an_old_role_goes_whole_unless_one_of_its_lines_is_evidence_and_recency_only_breaks_ties() -> None:
    small = parse_master(_SMALL)
    profile = ms.SelectionProfile(titles=("Staff Backend Engineer",))
    plain = ms.SelectionPosting("Staff Engineer", "Requirements:\n- Kubernetes in production.\n- On-call experience.\n")
    fortran = ms.SelectionPosting("Staff Engineer", "Requirements:\n- Kubernetes in production.\n- Fortran.\n")
    tight = _by_bullets(3)  # 9 bullet lines in the first pick: 3 pages

    dropped = ms.select(small, profile, plain, today=TODAY, measure=tight, fill=False)
    assert dropped.pages_before_fit == 3 and dropped.fits
    # Lines that support nothing go first: the stated ones before the quantified one (strength), and of two stated
    # lines the old role's before the recent role's (recency, the tie-break). No heading is left without a bullet.
    assert [(cut.kind, cut.id) for cut in dropped.cut_for_length] == [("bullet", "b-old-2"), ("bullet", "b-new-3"), ("bullet", "b-old-1"), ("role", "r-old")]
    assert "r-old" not in dropped.entries and dropped.roles_dropped == ("r-old",)
    assert "### Oldco" not in dropped.markdown
    # The evidence of both requirements is shown.
    assert dropped.evidence_for.keys() == {"b-new-1", "b-new-2"} and dropped.conflicts == ()

    kept = ms.select(small, profile, fortran, today=TODAY, measure=tight, fill=False)
    asked = next(requirement for requirement in kept.requirements if requirement.text == "Fortran")
    assert asked.mandatory and asked.supporters == ("b-old-1",)
    # The old role holds the only line that names Fortran: it stays while recent lines that support nothing are cut.
    assert kept.entries["r-old"] == ("b-old-1",) and kept.roles_dropped == ()
    assert kept.evidence_for["b-old-1"] == (asked.id,)
    assert [(cut.kind, cut.id) for cut in kept.cut_for_length] == [("bullet", "b-old-2"), ("bullet", "b-new-3"), ("bullet", "b-new-4")]
    assert len(kept.entries["r-new"]) == 2 >= ms.FLOORS[0]

    # With room for everything nothing is cut.
    roomy = ms.select(small, profile, plain, today=TODAY, measure=_by_bullets(40), fill=False)
    assert roomy.cut_for_length == () and set(roomy.entries["r-old"]) == {"b-old-1", "b-old-2"}


def test_a_pin_is_kept_when_everything_else_that_supports_nothing_goes_and_reported_when_it_cannot_be() -> None:
    small = parse_master(_SMALL)
    posting = ms.SelectionPosting("Staff Engineer", "Requirements:\n- Kubernetes in production.\n- On-call experience.\n")
    pinned = ms.SelectionProfile(titles=("Staff Backend Engineer",), pins=("b-old-2",))

    kept = ms.select(small, pinned, posting, today=TODAY, measure=_by_bullets(3), fill=False)
    assert "b-old-2" in kept.entries["r-old"] and kept.conflicts == ()
    assert next(line for line in kept.lines if line.id == "b-old-2").code == "pinned"
    # With no room even for the requirements' evidence and the pin, the pin goes before the evidence and the result says so.
    none = ms.select(small, pinned, posting, today=TODAY, measure=lambda markdown: (3 if "Kept the build green" in markdown else 2, 0.5), fill=False)
    assert "b-old-2" not in none.item_ids() and none.fits
    assert [(conflict.kind, conflict.ids) for conflict in none.conflicts] == [("must_keep", ("b-old-2",))]
    assert none.evidence_for.keys() == {"b-new-1", "b-new-2"}


# --- with a stored assessment: coverage comes from the lines it cites (0110-10-15, the real-data gate) ---


def _cited(row_id: str, text: str, *lines: str, mandatory: bool = True, met: bool = True) -> ms.CitedRequirement:
    return ms.CitedRequirement(row_id, text, mandatory, tuple(lines), met=met)


def test_a_line_the_assessment_cites_is_kept_whatever_words_it_shares_and_reported_when_it_cannot_be() -> None:
    small = parse_master(_SMALL)
    profile = ms.SelectionProfile(titles=("Staff Backend Engineer",))
    text = "Requirements:\n- Kubernetes in production.\n- On-call experience.\n"
    tight = _by_bullets(3)

    # BY WORDS (no assessment): the lines that share the requirements' words are the evidence, and the old role goes.
    by_words = ms.select(small, profile, ms.SelectionPosting("Staff Engineer", text), today=TODAY, measure=tight, fill=False)
    assert by_words.evidence_for.keys() == {"b-new-1", "b-new-2"} and by_words.roles_dropped == ("r-old",)
    assert not {"b-new-3", "b-old-2"} & set(by_words.item_ids()) and not any(requirement.cited for requirement in by_words.requirements)

    # THE ASSESSMENT cites, by meaning, a line that shares NO word with "On-call experience" and an OLD role's line
    # that shares none with "Kubernetes in production": each is the only evidence of its row.
    cited = (_cited("r1", "Kubernetes in production.", "b-old-2"), _cited("r2", "On-call experience.", "b-new-3"))
    assessed = ms.select(small, profile, ms.SelectionPosting("Staff Engineer", text, cited=cited), today=TODAY, measure=tight, fill=False)
    assert assessed.selector_version == "sel-5" and assessed.fits and assessed.conflicts == ()
    # The requirements ARE the assessment's rows, each supported by exactly the line it cites: the posting's two lines
    # and its Kubernetes keyword are not matched by words at all, so no other line is "the evidence" in their place.
    assert [(requirement.id, requirement.cited, requirement.supporters) for requirement in assessed.requirements] == [("r1", True, ("b-old-2",)), ("r2", True, ("b-new-3",))]
    assert assessed.evidence_for == {"b-old-2": ("r1",), "b-new-3": ("r2",)}
    assert {"b-new-3", "b-old-2"} <= set(assessed.item_ids()) and assessed.roles_dropped == ()
    # What went for length is what the assessment did not cite, the lines that share the words included.
    cut = {cut.id for cut in assessed.cut_for_length}
    assert len(cut) == 3 and cut <= {"b-old-1", "b-new-1", "b-new-2", "b-new-4"} and {"b-new-2", "b-old-1"} <= cut
    reason = next(line for line in assessed.lines if line.id == "b-old-2")
    assert (reason.picked, reason.code) == (True, "requirement_evidence") and reason.reason == "the line your assessment cites for: Kubernetes in production."
    as_json = assessed.to_json(small)["requirements"]
    assert [(row["id"], row["cited"], row["shown"]) for row in as_json] == [("r1", True, ["b-old-2"]), ("r2", True, ["b-new-3"])]

    # When a cited line cannot be shown within the page limit, the result says so; it is never dropped silently.
    none = ms.select(
        small, profile, ms.SelectionPosting("Staff Engineer", text, cited=cited), today=TODAY,
        measure=lambda markdown: (3 if "Kept the build green" in markdown else 2, 0.5), fill=False,
    )
    assert "b-old-2" not in none.item_ids() and "b-new-3" in none.item_ids()
    (conflict,) = none.conflicts
    assert (conflict.kind, conflict.ids, conflict.requirement_id, conflict.covered) == ("mandatory_evidence", ("b-old-2",), "r1", False)
    assert conflict.reason.startswith("the line the assessment cites for this requirement does not fit the page limit") and conflict.reason.endswith("no line it cites is shown now")


def test_cited_rows_come_first_a_met_mandatory_one_last_to_be_cut_and_words_only_add_what_no_row_is_about() -> None:
    small = parse_master(_SMALL)
    profile = ms.SelectionProfile(titles=("Staff Backend Engineer",), pins=("b-new-4",))
    text = "Requirements:\n- Kubernetes in production.\n- On-call experience.\n- Fortran.\n\nNice to have:\n- Incident reviews.\n"
    cited = (
        _cited("r1", "Kubernetes in production.", "b-old-2"),
        _cited("r2", "On-call experience.", "b-new-3", "b-new-2", met=False),  # unclear: still mandatory
        _cited("r4", "Incident reviews.", "b-new-3", mandatory=False),
        _cited("r5", "A line the master no longer holds.", "b-gone"),
    )
    selected = ms.select(small, profile, ms.SelectionPosting("Staff Engineer", text, cited=cited), today=TODAY, measure=_by_bullets(40), fill=False)

    by_id = {requirement.id: requirement for requirement in selected.requirements}
    # The assessment's rows in its order, then what the posting asks for that no cited row is about: Fortran (matched
    # by words, as before). A row none of whose cited lines the master holds now cites nothing and is not a requirement.
    assert [requirement.id for requirement in selected.requirements][:3] == ["r1", "r2", "r4"] and "r5" not in by_id
    words = [requirement for requirement in selected.requirements if not requirement.cited]
    assert [(requirement.text, requirement.supporters) for requirement in words] == [("Fortran", ("b-old-1",))]
    # Of the lines a row cites the strongest is its evidence: here the one that states a number.
    assert by_id["r2"].supporters == ("b-new-2", "b-new-3") and not by_id["r4"].mandatory
    assert selected.evidence_for == {"b-old-2": ("r1",), "b-new-2": ("r2",), "b-old-1": (by_id[words[0].id].id,)}
    # The keep order: a met mandatory row's cited line stays longest, then the other requirements' evidence (an
    # unclear row's, a requirement matched by words), then a pin, then everything else.
    rank = selected.values
    assert rank["b-old-2"] > max(rank["b-new-2"], rank["b-old-1"]) > min(rank["b-new-2"], rank["b-old-1"]) > rank["b-new-4"] > max(
        rank[item] for item in ("b-new-1", "b-new-3", "o-cka")
    )


def test_a_row_the_shown_summary_covers_needs_no_line_and_a_repeated_line_is_stood_for_by_its_better_twin() -> None:
    twice = _SMALL.replace(
        "- Ran the on-call rotation for 4 teams. <!-- id:b-new-2 -->",
        "- Ran the on-call rotation for 4 teams. <!-- id:b-new-2 -->\n- Ran the on-call rotation for all 4 teams. <!-- id:b-new-9 -->",
    )
    master = parse_master(twice)
    profile = ms.SelectionProfile(titles=("Staff Backend Engineer",))
    text = "Requirements:\n- Payments experience.\n- On-call experience.\n- Pipelines.\n"
    plain = ms.select(master, profile, ms.SelectionPosting("Platform Engineer", text), today=TODAY, measure=_by_bullets(40), fill=False)
    assert plain.summary == ("sum-platform",) and plain.duplicates == {"b-new-9": "b-new-2"}

    cited = (
        _cited("r1", "Payments experience.", "sum-platform", "b-new-4"),  # the summary shown covers it by itself
        _cited("r2", "On-call experience.", "b-new-9"),  # a line a better line repeats: that line stands for it
        _cited("r3", "Pipelines.", "sum-data"),  # only a summary this resume does not show
    )
    selected = ms.select(master, profile, ms.SelectionPosting("Platform Engineer", text, cited=cited), today=TODAY, measure=_by_bullets(40), fill=False)
    by_id = {requirement.id: requirement for requirement in selected.requirements}
    assert selected.summary == ("sum-platform",)
    assert by_id["r1"].supporters == ("sum-platform", "b-new-4") and "b-new-4" not in selected.evidence_for
    assert by_id["r2"].supporters == ("b-new-2",) and selected.evidence_for["b-new-2"] == ("r2",)
    assert by_id["r3"].supporters == ("sum-data",)
    # The one thing that cannot be shown is said: the assessment cited a summary variant this resume does not print.
    assert [(conflict.kind, conflict.requirement_id, conflict.ids, conflict.covered) for conflict in selected.conflicts] == [("mandatory_evidence", "r3", ("sum-data",), False)]
    assert "cites a summary this resume does not show" in selected.conflicts[0].reason
    # The re-make checks read a cited row like any requirement: covered by the summary, by a line, or lost.
    shown = (*selected.summary, *(item for entry_id, bullets in selected.entries.items() for item in (entry_id, *bullets)), *selected.other)
    checks = ms.check_selection(master, selected.requirements, shown, selected.skills, measure=_by_bullets(40))
    assert set(checks.covered) >= {"r1", "r2"} and checks.lost == ("r3",)


def test_the_selector_s_word_rule_for_one_requirement_is_readable_on_its_own() -> None:
    small = parse_master(_SMALL)
    assert ms.word_supporters(small, "Kubernetes in production.")[0] == "b-new-1"
    assert set(ms.word_supporters(small, "Ran an on-call rotation.")) == {"b-new-2"}
    assert ms.word_supporters(small, "Experience with quantum annealing.") == ()


def test_an_assessment_s_evidence_quotes_trace_to_master_lines_word_for_word_or_as_a_paraphrase() -> None:
    from gigai.scout import assess_master
    from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass, RequirementMatrixRow

    small = parse_master(_SMALL)
    matrix = (
        RequirementMatrixRow("Kubernetes in production", ("Cut deploy time from 40 minutes to 6",), MatrixStatus.MET, RequirementClass.HARD),
        RequirementMatrixRow("Leading people", ("mentored three engineers ... wrote the incident review guide",), MatrixStatus.UNCLEAR, RequirementClass.ASKABLE),
        RequirementMatrixRow("Numerical code", ("A Fortran solver prices two million contracts each night",), MatrixStatus.MET, RequirementClass.NICE_TO_HAVE),
        RequirementMatrixRow("Python", ("Languages: Python",), MatrixStatus.MET, RequirementClass.LIST_ITEM),
        RequirementMatrixRow("Rust", (), MatrixStatus.UNMET, RequirementClass.HARD),
        RequirementMatrixRow("Payments", ("Platform engineer who runs payment systems",), MatrixStatus.MET, None),
    )
    rows = assess_master.cited_requirements(small, matrix)
    # r<n> is the row's place in the assessment. A row that names only a skill, or quotes nothing, cites no line.
    assert [(row.id, row.mandatory, row.met, row.lines) for row in rows] == [
        ("r1", True, True, ("b-new-1",)),  # word for word (a piece of the line)
        ("r2", True, False, ("b-new-3",)),  # two pieces joined by ...: the one long enough to name a line
        ("r3", False, True, ("b-old-1",)),  # a paraphrase: the words they share
        ("r6", True, True, ("sum-platform",)),  # a summary can be cited
    ]
    traced = assess_master.MasterCitations(small)
    assert traced.row(("Languages: Python",)) == ((), ("Python",)) and traced.row(("nothing the master says",)) == ((), ())
    assert traced.row(("Cut deploy time from 40 minutes to 6", "Languages: Python")) == (("b-new-1",), ())


# --- a role or project the posting's title names keeps its best line (0.1.10.11 PICK v5, sel-4) ----------

_TITLED = """<!-- gigai-master:1 -->

## Summary

- Backend engineer who runs platforms. <!-- id:sum-one -->

## Experience

### Newco <!-- id:r-new -->
Staff Engineer | Jun 2021 - Present
- Cut deploy time from 40 minutes to 6 with a staged pipeline on Kubernetes. <!-- id:n-1 -->
- Ran the on-call rotation for 4 teams. <!-- id:n-2 -->
- Moved 30 services to a shared build cache. <!-- id:n-3 -->

### Midco <!-- id:r-mid -->
Senior Engineer | Feb 2018 - May 2021
- Built the billing export that 200 merchants download every night. <!-- id:m-1 -->
- Halved the nightly batch from 6 hours to 3. <!-- id:m-2 -->

### Oldco <!-- id:r-old -->
Engineer | Jan 2010 - Dec 2013
- Wrote the solver that prices 2 million contracts a night. <!-- id:o-1 -->
- Trained 40 support agents on the new console. <!-- id:o-2 -->

## Projects

### Loomhand: an agent runtime <!-- id:p-loom -->
- Published the tool as open source. <!-- id:l-1 -->
- Added checkpoints so a long agent run survives a restart. <!-- id:l-2 -->
- Built a local agent runtime in which a coordinator plans work and hands it to workers. <!-- id:l-3 -->

### Plotwise: a garden planner <!-- id:p-plot -->
- Drew 300 planting plans from soil and frost data. <!-- id:g-1 -->
- Added a watering calendar. <!-- id:g-2 -->

## Skills

- Languages: Python, Kubernetes <!-- id:s-lang -->
"""
_TITLED_TEXT = "Requirements:\n- Kubernetes in production.\n- On-call experience.\n"


def test_a_project_the_posting_s_title_names_keeps_its_best_line_while_lines_that_cover_nothing_go() -> None:
    master = parse_master(_TITLED)
    profile = ms.SelectionProfile(titles=("Staff Backend Engineer",))
    untitled = ms.SelectionPosting("Staff Backend Engineer", _TITLED_TEXT)
    titled = ms.SelectionPosting("Staff Backend Engineer, Agent Runtime", _TITLED_TEXT)
    tight = _by_bullets(3)  # 12 bullet lines in the first pick: 4 pages

    # A TITLE THAT NAMES NO ENTRY: the project's lines cover nothing and state no number, so they are the first three
    # cuts (strength before recency), and the project goes whole. (This is every pick ``sel-3`` made, title or not.)
    before = ms.select(master, profile, untitled, today=TODAY, measure=tight, fill=False)
    assert before.title_entries == {} and [cut.id for cut in before.cut_for_length][:3] == ["l-3", "l-2", "l-1"]
    assert "p-loom" not in before.entries and before.conflicts == () and before.fits

    # THE TITLE NAMES THE PROJECT (its heading holds "agent" and "runtime"): its best line stays, the one that
    # holds most of the title's words, while every line that is not a requirement's evidence goes.
    after = ms.select(master, profile, titled, today=TODAY, measure=tight, fill=False)
    assert after.title_entries == {"p-loom": "l-3"} and after.entries["p-loom"] == ("l-3",)
    assert after.fits and after.conflicts == () and after.evidence_for.keys() == {"n-1", "n-2"}
    assert next(line for line in after.lines if line.id == "l-3").code == "title_entry"
    # What went instead: the other project and the old role whole, and the recent roles down to their evidence or
    # their one line. Neither that project nor that role is named by the title ("40 support agents" is one line of two).
    assert set(after.entries) == {"r-new", "r-mid", "p-loom"} and after.entries["r-new"] == ("n-1", "n-2") and len(after.entries["r-mid"]) == ms.FLOORS[1]
    assert "p-loom" not in after.roles_dropped and after.roles_dropped == ("r-old",)
    # The keep order: evidence, then the entry's one line, then the project's other line that holds a word of the
    # title (before the quantified lines of every role), then the rest as before. A title word in a line of an entry
    # the title does not name counts for nothing (o-2).
    rank = after.values
    assert sorted(rank, key=rank.__getitem__, reverse=True) == ["n-1", "n-2", "l-3", "l-2", "n-3", "g-1", "m-1", "m-2", "o-1", "o-2", "g-2", "l-1"]
    assert next(line for line in ms.select(master, profile, titled, today=TODAY, measure=_by_bullets(40), fill=False).lines if line.id == "l-2").code == "posting_title"
    as_json = after.to_json(master)
    assert as_json["title_entries"] == [{"id": "p-loom", "line": "l-3", "shown": True}]

    # A profile's standing pick has no posting, so no title: nothing changes there.
    assert ms.select(master, profile, None, today=TODAY, measure=tight, fill=False).title_entries == {}


def test_the_one_line_of_an_entry_the_title_names_goes_before_any_evidence_and_the_result_says_so() -> None:
    master = parse_master(_TITLED)
    profile = ms.SelectionProfile(titles=("Staff Backend Engineer",), pins=("m-2",))
    titled = ms.SelectionPosting("Staff Backend Engineer, Agent Runtime", _TITLED_TEXT, cited=(_cited("r1", "Kubernetes in production.", "o-1"),))

    # No page takes the project's best line: it is cut after every line that is not evidence or a pin, BEFORE the pin
    # and before the line the assessment cites, and the result carries the conflict. Nothing cited is dropped for it.
    none = ms.select(master, profile, titled, today=TODAY, measure=lambda markdown: (3 if "Built a local agent runtime" in markdown else 2, 0.5), fill=False)
    assert none.fits and "p-loom" not in none.entries and none.title_entries == {"p-loom": "l-3"}
    (conflict,) = none.conflicts
    assert (conflict.kind, conflict.ids, conflict.requirement_id) == ("title_entry", ("p-loom", "l-3"), "")
    assert conflict.reason.startswith("the posting's title names this role or project")
    assert {"o-1", "m-2"} <= set(none.item_ids()) and none.evidence_for["o-1"] == ("r1",)
    cut = [cut for cut in none.cut_for_length if cut.id == "l-3"]
    assert [(item.kind, item.code) for item in cut] == [("bullet", "cut_conflict")] and none.cut_for_length[-1].id == "l-3"
    assert next(line for line in none.lines if line.id == "l-3").code == "cut_conflict"
    # In the keep order it stands below the cited line and the pin, above everything else.
    rank = none.values
    assert rank["o-1"] > rank["m-2"] > rank["l-3"] > max(value for item, value in rank.items() if item not in ("o-1", "m-2", "l-3", "n-2"))


def test_the_title_names_a_role_by_its_own_title_and_an_entry_by_half_its_lines() -> None:
    # A ROLE, and an old one: its own title ("Agent Platform Engineer") holds a word of the posting's title.
    roles = _TITLED.replace("Engineer | Jan 2010 - Dec 2013", "Agent Platform Engineer | Jan 2010 - Dec 2013").replace("### Loomhand: an agent runtime", "### Loomhand")
    master = parse_master(roles)
    profile = ms.SelectionProfile(titles=("Staff Backend Engineer",))
    posting = ms.SelectionPosting("Staff Backend Engineer, Agent Runtime (Remote, US)", _TITLED_TEXT)
    kept = ms.select(master, profile, posting, today=TODAY, measure=_by_bullets(4), fill=False)
    # The project has a bare name now, and two of its three lines hold a word of the title: it is named by its lines.
    assert kept.title_entries == {"r-old": "o-2", "p-loom": "l-3"}
    # Both keep a line, the old role too (it would have gone whole), while the garden project goes whole and the
    # recent roles lose the lines that cover nothing.
    assert kept.entries["r-old"] == ("o-2",) and "l-3" in kept.entries["p-loom"] and "p-plot" not in kept.entries
    assert kept.roles_dropped == () and kept.fits and kept.conflicts == ()
    assert {cut.id for cut in kept.cut_for_length} == {"l-1", "g-1", "g-2", "o-1", "m-2", "n-3"}
    # One line less of room: the entries' lines go last of what is not evidence, the one lower in the keep order
    # first, and the result names the entry it could not show.
    tighter = ms.select(master, profile, posting, today=TODAY, measure=_by_bullets(3), fill=False)
    assert tighter.entries["p-loom"] == ("l-3",) and "r-old" not in tighter.entries and tighter.fits
    assert [(conflict.kind, conflict.ids) for conflict in tighter.conflicts] == [("title_entry", ("r-old", "o-2"))]
    # What a title is ABOUT: no rank word, no place, no level ("Remote" and "US" name nothing).
    assert ms._title_subject("Staff Backend Engineer, Agent Runtime (Remote, US)") == {"backend", "agent", "runtim"}  # noqa: SLF001
    assert ms._title_subject("Senior Software Engineering Lead II") == set()  # noqa: SLF001

    # ONE line of several that happens to hold the word names nothing: "support agents" in one line of two.
    assert "r-old" not in ms.select(parse_master(_TITLED), profile, posting, today=TODAY, measure=_by_bullets(3), fill=False).title_entries


def test_a_line_that_supports_by_words_what_a_cited_row_answered_stays_before_a_stronger_or_more_recent_one() -> None:
    master = parse_master(_TITLED.replace("- Added a watering calendar. <!-- id:g-2 -->", "- Wrote the on-call guide for the garden club. <!-- id:g-2 -->"))
    profile = ms.SelectionProfile(titles=("Staff Backend Engineer",))
    cited = (_cited("r1", "Kubernetes in production.", "n-1"), _cited("r2", "On-call experience.", "n-2"))
    assessed = ms.select(master, profile, ms.SelectionPosting("Staff Backend Engineer", _TITLED_TEXT, cited=cited), today=TODAY, measure=_by_bullets(40), fill=False)
    # The assessment answered "On-call experience" with n-2. g-2 shares the requirement's words: it covers nothing
    # (it is no requirement's supporter), and it is still about the posting, so among the lines that cover nothing
    # it stays before the lines that state a number and before the more recent ones.
    assert [(requirement.id, requirement.supporters) for requirement in assessed.requirements] == [("r1", ("n-1",)), ("r2", ("n-2",))]
    rank = assessed.values
    rest = [item for item in sorted(rank, key=rank.__getitem__, reverse=True) if item not in ("n-1", "n-2")]
    assert rest[0] == "g-2" and next(line for line in assessed.lines if line.id == "g-2").code == "posting_wording"
    # Without the assessment the same line is a second line for that requirement, as it always was.
    plain = ms.select(master, profile, ms.SelectionPosting("Staff Backend Engineer", _TITLED_TEXT), today=TODAY, measure=_by_bullets(40), fill=False)
    assert "g-2" in next(requirement for requirement in plain.requirements if requirement.text == "On-call experience.").supporters


def test_a_line_that_says_what_a_better_line_says_is_left_out() -> None:
    twice = _SMALL.replace(
        "- Ran the on-call rotation for 4 teams. <!-- id:b-new-2 -->",
        "- Ran the on-call rotation for 4 teams. <!-- id:b-new-2 -->\n- Ran the on-call rotation for all 4 teams. <!-- id:b-new-9 -->\n- Ran the on-call rotation for 9 teams. <!-- id:b-new-8 -->",
    )
    selected = ms.select(parse_master(twice), ms.SelectionProfile(), None, today=TODAY, measure=_by_bullets(40))

    # The same words and the same numbers: one of the two is shown. Another number is another fact.
    assert selected.duplicates == {"b-new-9": "b-new-2"}
    assert "b-new-2" in selected.item_ids() and "b-new-8" in selected.item_ids() and "b-new-9" not in selected.item_ids()
    reason = next(line for line in selected.lines if line.id == "b-new-9")
    assert not reason.picked and reason.code == "near_duplicate"


def test_room_left_on_the_page_goes_to_recent_roles() -> None:
    lines = "\n".join(f"- Shipped service number {n} to production. <!-- id:b-new-{n} -->" for n in range(1, 13))
    small = parse_master(f"<!-- gigai-master:1 -->\n\n## Experience\n\n### Newco <!-- id:r-new -->\nStaff Engineer | Jun 2021 - Present\n{lines}\n")
    profile = ms.SelectionProfile()

    filled = ms.select(small, profile, None, today=TODAY, measure=_by_bullets(40))
    assert len(filled.entries["r-new"]) == ms.HARD_CAPS[0] and len(filled.added_to_fill) == ms.HARD_CAPS[0] - ms.PICK_CAPS[0]
    assert {line.code for line in filled.lines if line.id in filled.added_to_fill} == {"room_left"}
    assert len(ms.select(small, profile, None, today=TODAY, measure=_by_bullets(40), fill=False).entries["r-new"]) == ms.PICK_CAPS[0]
    # No room: the page holds exactly the first pick.
    full = ms.select(small, profile, None, today=TODAY, measure=_by_bullets(ms.PICK_CAPS[0]), max_pages=1)
    assert full.fits and full.added_to_fill == () and len(full.entries["r-new"]) == ms.PICK_CAPS[0]


def test_the_same_lines_in_another_order_give_the_same_pick() -> None:
    """No tie is broken by where a line stands in the file: a master reordered by hand keeps its selections."""

    small = parse_master(_SMALL)
    lines = _SMALL.splitlines()
    first, last = lines.index("- Cut deploy time from 40 minutes to 6 with a staged pipeline on Kubernetes. <!-- id:b-new-1 tags:backend -->"), lines.index("- Mentored 3 engineers. <!-- id:b-new-4 -->")
    lines[first : last + 1] = reversed(lines[first : last + 1])
    turned = parse_master("\n".join(lines) + "\n")
    posting = ms.SelectionPosting("Staff Engineer", "Requirements:\n- Kubernetes in production.\n- On-call experience.\n")
    for measure in (_by_bullets(3), _by_bullets(4), _by_bullets(40)):
        one = ms.select(small, ms.SelectionProfile(), posting, today=TODAY, measure=measure, fill=False)
        two = ms.select(turned, ms.SelectionProfile(), posting, today=TODAY, measure=measure, fill=False)
        assert set(one.item_ids()) == set(two.item_ids()) and one.skills == two.skills
        assert [cut.id for cut in one.cut_for_length] == [cut.id for cut in two.cut_for_length]


def test_a_posting_written_as_prose_is_read_whole() -> None:
    small = parse_master(_SMALL)
    prose = ms.SelectionPosting("Engineer", "We need someone to run the on-call rotation and write incident review guides for our teams.\n")

    selected = ms.select(small, ms.SelectionProfile(), prose, today=TODAY, measure=_by_bullets(40))
    reasons = {line.id: line for line in selected.lines}
    assert reasons["b-new-2"].code == "posting_wording" and reasons["b-new-3"].code == "posting_wording"


# --- measuring markdown (the renderer's public function) --------------------------------------


def test_measure_markdown_is_the_shipped_layout_headerless(master: Master) -> None:
    markdown = master.markdown(ids=False)

    pages, fill = measure_markdown(markdown, spacing_scale=1.0)
    assert pages == 8 and 0.0 < fill <= 1.0
    # The same page count the renderer reports for the PDF it compiles at that spacing, headerless.
    for scale in (0.9, 1.0):
        assert measure_markdown(markdown, spacing_scale=scale)[0] == render_markdown_pdf(markdown, None, timestamp=STAMP, spacing_scale=scale, auto_fit=False).pages
    # Tighter spacing never ends later; an out-of-range scale is clamped to the slider's range.
    assert measure_markdown(markdown, spacing_scale=0.7) <= measure_markdown(markdown, spacing_scale=1.4)
    assert measure_markdown(markdown, spacing_scale=0.1) == measure_markdown(markdown, spacing_scale=0.7)
    # The id comments are not printed, so they do not change the layout.
    assert measure_markdown(master.markdown(), spacing_scale=1.0) == (pages, fill)
    with pytest.raises(ResumeMarkdownError) as refused:
        measure_markdown("no sections here\n")
    assert refused.value.code == "resume_markdown_invalid"


def test_a_selection_is_resume_markdown_the_shipped_parser_reads(master: Master) -> None:
    selected = _selected(master, "profile-swe", "p2-staff-swe-core-infrastructure")

    _name, sections = parse_resume_markdown(selected.markdown)
    assert [section["heading"] for section in sections] == ["SUMMARY", "EXPERIENCE", "PROJECTS", "SKILLS", "EDUCATION", "OTHER"]
    assert next(section for section in sections if section["heading"] == "SKILLS")["tags"] == list(selected.skills)
    assert "<!--" not in selected.markdown
    # With ids, each line names the master line it is; the two forms print the same.
    ids = re.findall(r"<!-- id:([A-Za-z0-9_-]+) -->", selected.markdown_with_ids)
    assert [item for item in ids if item in master.items] == list(selected.item_ids())
    assert parse_resume_markdown(selected.markdown_with_ids) == parse_resume_markdown(selected.markdown)


def test_a_pick_too_large_for_the_renderer_counts_as_over_any_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    import gigai.scout.resume_pdf as renderer

    def too_large(_markdown: str, **_kwargs: object) -> tuple[int, float]:
        raise ResumeMarkdownError("resume_markdown_too_large", "markdown has more than 600 lines")

    monkeypatch.setattr(renderer, "measure_markdown", too_large)
    assert ms._shipped_measure("## Summary\n\n- x\n") == (ms.UNMEASURED_PAGES, 1.0)


# --- the evidence view ------------------------------------------------------------------------


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
@pytest.mark.parametrize("posting_id", POSTING_IDS)
def test_the_evidence_view_holds_the_most_relevant_lines_within_the_assess_cap(master: Master, profile_id: str, posting_id: str) -> None:
    posting = _posting(posting_id)
    view = ms.evidence_view(master, _profile(profile_id), posting, today=TODAY)

    assert view.cap == 12_000 and view.within_cap and len(view.markdown) <= 12_000
    assert 65 <= view.bullets <= 75 and view.bullets_total == 148  # the spike measured 69 to 73
    assert len(view.summary) == 1
    assert list(view.skills) == master.skills() and list(view.other) == [item.id for item in master.in_section("other")]
    assert {entry.id for entry in master.entries.values() if entry.section in ("experience", "education")} <= set(view.entries)
    texts = {item.text for item in master.items.values()}
    assert all(line in texts for line in _shown_lines(view.markdown) if line != ", ".join(view.skills))
    parse_resume_markdown(view.markdown)
    # It evidences at least the must-haves the 2-page selection evidences, outside the skills line.
    selected = _selected(master, profile_id, posting_id)
    assert selected.keywords is not None
    must = selected.keywords.must

    def evidenced(ids: tuple[str, ...]) -> set[str]:
        text = "\n".join(master.items[item_id].text for item_id in ids)
        return {term for term in must if mentions(text, term)}

    assert evidenced(selected.item_ids()) <= evidenced(view.item_ids())
    assert ms.evidence_view(master, _profile(profile_id), posting, today=TODAY) == view


def test_a_smaller_cap_shows_fewer_bullets_and_says_when_even_the_frame_is_over(master: Master) -> None:
    profile, posting = _profile("profile-ai"), _posting("p1-staff-ai-agent-platform")

    half = ms.evidence_view(master, profile, posting, today=TODAY, cap=6_000)
    assert half.within_cap and 0 < half.bullets < 40
    none = ms.evidence_view(master, profile, posting, today=TODAY, cap=500)
    assert none.bullets == 0 and not none.within_cap and none.to_json()["within_cap"] is False


# --- the CLI on a scratch home ----------------------------------------------------------------


def _setup(tmp_path: Path) -> Path:
    return setup_home(tmp_path / "home", workpad_root=tmp_path / "workpads")


def _home_with_master(tmp_path: Path) -> Path:
    home = _setup(tmp_path)
    result = CliRunner().invoke(cli, ["scout", "resume", "master", "init", "--from", str(FIXTURES / "master.md"), "--home", str(home), "--json"])
    assert result.exit_code == 0, result.output
    return home


def _show(home: Path, *args: str, ok: bool = True) -> dict:
    result = CliRunner().invoke(cli, ["scout", "resume", "master", "selection", "show", *args, "--home", str(home), "--json"])
    assert result.exit_code == (0 if ok else 1), result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _posting_file(tmp_path: Path, posting_id: str) -> tuple[Path, str, str]:
    meta, body = _posting_parts(posting_id)
    path = tmp_path / f"{posting_id}.txt"
    path.write_text(body, encoding="utf-8")
    return path, meta["title"], meta["company"]


def _journal_state(home: Path) -> tuple[str, str]:
    from gigai.scout.target_resolution import home_scout_target
    from gigai.workpad import resolve_workpad

    workpad = resolve_workpad(home_root=home, requested_target=home_scout_target(home), gig_id=None, allow_semantic_state=True).path

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(workpad), *args], capture_output=True, check=True, text=True).stdout

    return git("rev-parse", "HEAD"), git("status", "--porcelain")


_AI = ("--profile-title", "Staff AI Engineer", "--profile-title", "Principal AI Engineer", "--focus", "ai", "--focus", "llm", "--focus", "agents")


def test_selection_show_for_a_job_lists_picked_and_left_out_with_reasons(tmp_path: Path) -> None:
    home = _home_with_master(tmp_path)
    posting, title, company = _posting_file(tmp_path, "p1-staff-ai-agent-platform")
    before = _journal_state(home)

    out = _show(home, *_AI, "--job-text", str(posting), "--title", title, "--company", company)

    selection = out["selection"]
    assert out["ok"] is True and selection["selector_version"] == "sel-5"
    assert selection["fits"] is True and selection["pages"] == 2 and selection["pages_before_fit"] > 2 and selection["max_pages"] == 2
    assert render_markdown_pdf(selection["markdown"], None, timestamp=STAMP).pages == 2
    assert selection["master"]["revision"] == 1 and selection["master"]["content_sha256"].startswith("sha256:")
    assert selection["profile"] == {"profile_id": None, "label": "Staff AI Engineer", "titles": ["Staff AI Engineer", "Principal AI Engineer"], "focus_tags": ["ai", "llm", "agents"]}
    assert selection["job"]["title"] == title and selection["job"]["company"] == company and selection["job"]["source"] == "text"
    assert selection["job"]["job_identity"] == "text:" + selection["job"]["text_sha256"]
    # Picked / Left out: every selectable line of the master is in exactly one, with its text and a reason.
    picked, left = selection["picked"], selection["left_out"]
    assert len(picked) + len(left) == 166 and not {row["id"] for row in picked} & {row["id"] for row in left}
    assert all(row["reason"] and row["code"] and row["text"] for row in (*picked, *left))
    assert selection["summary"] == ["sum-ai"]
    assert {"r-lum", "r-kes", "r-hex"} <= {entry["id"] for entry in selection["entries"] if entry["shown"]}
    assert selection["cut_for_length"] and all(cut["reason"].startswith("cut for length") for cut in selection["cut_for_length"])
    assert selection["keywords"]["must_missing"] == [] and "Python" in selection["keywords"]["must"]
    # The Skills section is kept whole; what the posting asks for, and its evidence, are listed; no conflict.
    assert len(selection["skills"]["picked"]) >= 40 and {skill["code"] for skill in selection["skills"]["left_out"]} == {"cut_for_length"}
    asked = selection["requirements"]
    assert asked and all(row["id"] and row["text"] and isinstance(row["mandatory"], bool) for row in asked)
    assert all(row["shown"] for row in asked if row["mandatory"] and row["supporters"])
    assert selection["evidence_for"] and selection["conflicts"] == [] and selection["counts"]["conflicts"] == 0
    # A read: nothing was committed or left behind in the journal.
    assert _journal_state(home) == before

    plain = CliRunner().invoke(cli, [
        "scout", "resume", "master", "selection", "show", *_AI, "--job-text", str(posting), "--title", title, "--company", company, "--home", str(home),
    ])
    assert plain.exit_code == 0, plain.output
    assert plain.output.startswith(f"Selection for {title} at {company}, profile Staff AI Engineer: 2 pages (")
    assert "Picked " in plain.output and "left out " in plain.output and "Selector sel-5, master revision 1." in plain.output
    assert "    + sum-ai  Staff engineer with 16 years" in plain.output
    assert "    - sum-backend  " in plain.output and "another summary fits this posting better" in plain.output
    assert "r-tes  Tessel Robotics" in plain.output and ": not shown" in plain.output
    assert "cut for length: no line of this older role is evidence for this posting" in plain.output
    assert "the strongest evidence for: " in plain.output
    assert "## Skills: " in plain.output and "the posting asks for it" in plain.output

    markdown = CliRunner().invoke(cli, [
        "scout", "resume", "master", "selection", "show", *_AI, "--job-text", str(posting), "--title", title, "--markdown", "--home", str(home),
    ])
    assert markdown.exit_code == 0 and markdown.output.startswith("## Summary\n\n- Staff engineer")
    parse_resume_markdown(markdown.output)


def test_selection_show_without_a_job_is_the_profiles_standing_pick(tmp_path: Path) -> None:
    home = _home_with_master(tmp_path)

    selection = _show(home, "--profile-title", "Staff Software Engineer", "--focus", "backend", "--focus", "infrastructure")["selection"]
    assert selection["job"] is None and selection["keywords"] is None and selection["fits"] is True
    assert selection["summary"] == ["sum-backend"]
    # No profile named and none stored: a selection with no profile prior, said plainly.
    bare = _show(home)["selection"]
    assert bare["profile"] is None and bare["fits"] is True
    plain = CliRunner().invoke(cli, ["scout", "resume", "master", "selection", "show", "--home", str(home)])
    assert plain.output.startswith("Selection for no profile, no posting (the profile's standing pick): 2 pages")


def test_selection_show_evidence_is_the_view_an_assessment_would_read(tmp_path: Path) -> None:
    home = _home_with_master(tmp_path)
    posting, title, _company = _posting_file(tmp_path, "p3-staff-engineer-ml-platform")

    evidence = _show(home, *_AI, "--job-text", str(posting), "--title", title, "--evidence")["evidence"]
    assert evidence["within_cap"] is True and evidence["chars"] <= evidence["cap"] == 12_000
    assert 60 <= evidence["bullets"] <= 80 and evidence["bullets_total"] == 148 and len(evidence["skills"]) == 79
    assert evidence["markdown"].startswith("## Summary\n") and evidence["master"]["revision"] == 1

    assert _show(home, "--evidence", ok=False)["error"]["code"] == "job_input_invalid"


def test_selection_show_refusals_name_the_reason_and_make_no_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("selection show must not open a connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    empty = _setup(tmp_path)
    assert _show(empty, ok=False)["error"]["code"] == "master_not_found"

    home = _home_with_master(tmp_path / "second")
    posting, title, _company = _posting_file(tmp_path, "p1-staff-ai-agent-platform")
    # A URL Scout holds no posting for: named, and not fetched.
    missing = _show(home, "--job-url", "https://boards.greenhouse.io/example/jobs/1", ok=False)["error"]
    assert missing["code"] == "job_not_stored" and "--job-text" in missing["message"]
    assert _show(home, "--job-url", "https://boards.greenhouse.io/example/jobs/1", "--job-text", str(posting), ok=False)["error"]["code"] == "job_input_invalid"
    assert _show(home, "--job-text", str(tmp_path / "absent.txt"), ok=False)["error"]["code"] == "job_input_invalid"
    assert _show(home, "--title", title, ok=False)["error"]["code"] == "job_input_invalid"
    assert _show(home, "--profile", "profile_00000000-0000-4000-8000-000000000000", ok=False)["error"]["code"] == "profile_not_found"
    assert _show(home, "--profile", "p", "--profile-title", "Staff AI Engineer", ok=False)["error"]["code"] == "profile_input_invalid"


def test_selection_show_reads_a_stored_profiles_titles(tmp_path: Path) -> None:
    """``--profile ID`` gives a stored profile's titles, and with no profile option the selected profile is used."""

    from gigai.canonical import canonical_json_bytes
    from gigai.scout.profile_records import selected_profile
    from gigai.scout.target_resolution import home_scout_target
    from gigai.workpad import resolve_workpad
    from tests.support.scout_profile_fixtures import default_find_jobs_config

    home = _home_with_master(tmp_path)
    scout = home_scout_target(home)
    # A profile needs a committed resume and titles: a synthetic resume, and the fixture's find-jobs.json.
    resume = tmp_path / "resume.md"
    resume.write_text("## Summary\n\n- Staff AI engineer. (fixture only.)\n", encoding="utf-8")
    added = CliRunner().invoke(cli, ["scout", "resume", "add", str(resume), "--home", str(home), "--json"])
    assert added.exit_code == 0, added.output
    (scout / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))

    # First, as a user would: no profile option on a home that has a resume and titles. The selected profile is used.
    by_default = _show(home)["selection"]
    resolved = resolve_workpad(home_root=home, requested_target=scout, gig_id=None, allow_semantic_state=True)
    profile = selected_profile(resolved, home_root=home, target=scout)
    assert profile is not None and profile.titles == ("staff ai engineer", "principal machine learning engineer")
    by_id = _show(home, "--profile", profile.profile_id)["selection"]
    assert by_default["profile"] == by_id["profile"] == {
        "profile_id": profile.profile_id, "label": profile.label, "titles": list(profile.titles), "focus_tags": [],
    }
    assert by_id["fits"] is True and by_default["picked"] == by_id["picked"]
    # The titles alone steer the pick: an AI profile's standing summary is the AI one.
    assert by_id["summary"] == ["sum-ai"]


def test_selection_show_job_url_reads_the_posting_scout_already_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--job-url``: the index's posting text, else the text a stored assessment was made on. Never a request.

    The index is seeded and the posting assessed as ``test_reassess_keeps_posting_text`` does (a synthetic
    Greenhouse board, the fixture model: no model call, every HTTP request answered and written down here)."""

    from gigai.scout.find_jobs import job_source
    from tests.api_e2e.harness import add_resume, setup_and_init, write_offline_find_jobs_config
    from tests.behaviors.scout_find_jobs.test_reassess_keeps_posting_text import _URL, _record_requests, _seed_index, _stored

    home, target = setup_and_init(tmp_path)
    runner = CliRunner()
    base = ["--home", str(home), "--target", str(target), "--json"]
    assert runner.invoke(cli, ["scout", "install", *base]).exit_code == 0
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    _seed_index(home, target)
    asked = _record_requests(monkeypatch)
    batch = runner.invoke(cli, ["scout", "new", "--yes", *base])
    assert batch.exit_code == 0, batch.output
    stored = _stored(home, target)
    assert "Kubernetes" in (stored.posting_text or "")
    init = runner.invoke(cli, ["scout", "resume", "master", "init", "--from", str(FIXTURES / "master.md"), *base])
    assert init.exit_code == 0, init.output
    asked.clear()

    def show(*args: str) -> dict:
        result = runner.invoke(cli, ["scout", "resume", "master", "selection", "show", *args, *base])
        assert result.exit_code == 0, result.output
        return json.loads(result.output.strip().splitlines()[-1])["selection"]

    from_index = show("--job-url", _URL)
    assert from_index["job"] == {
        "title": "Software Engineer", "company": stored.job.company, "location": "United States - Remote",
        "job_identity": stored.job.job_identity, "text_sha256": stored.job.text_sha256, "source": "index",
    }
    assert from_index["fits"] is True and {"Python", "Kubernetes", "Helm"} <= set(from_index["keywords"]["must"])
    assert from_index["keywords"]["must_missing"] == []

    # The index no longer holds it: the posting the stored assessment was made on is used. Same text, same pick.
    monkeypatch.setattr(job_source, "index_posting", lambda *_args, **_kwargs: None)
    from_assessment = show("--job-url", _URL)
    assert from_assessment["job"]["source"] == "assessment" and from_assessment["job"]["text_sha256"] == stored.job.text_sha256
    assert from_assessment["picked"] == from_index["picked"]
    # --title overrides the stored title (it decides the summary).
    assert show("--job-url", _URL, "--title", "Staff AI Engineer, Agent Platform")["summary"] == ["sum-ai"]
    assert asked == [], "selection show made a request"

    # 0110-10-15: the job's stored assessment for the profile says which lines evidence a requirement. Here it cites
    # nothing of the master (the fixture model quotes the profile's resume), so the pick above was made by words.
    assert not any(row["cited"] for row in from_index["requirements"])
    left = next(line for line in from_index["left_out"] if line["kind"] == "bullet" and line["code"] in ("cut_lowest_value", "role_limit", "cut_role_dropped") and len(line["text"]) > 40)
    path = Path(stored.stored_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["result"]["matrix"][0].update({"resume_evidence": [left["text"][:40]], "status": "met"})
    path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.undo()
    with_assessment = show("--job-url", _URL)
    row = with_assessment["requirements"][0]
    assert (row["id"], row["cited"], row["supporters"], row["shown"]) == ("r1", True, [left["id"]], [left["id"]])
    picked = {line["id"]: line for line in with_assessment["picked"]}
    assert left["id"] in picked and picked[left["id"]]["code"] == "requirement_evidence" and picked[left["id"]]["reason"].startswith("the line your assessment cites for: ")
    assert with_assessment["conflicts"] == [] and with_assessment["fits"] is True


# --- re-making a pick: the previous selection and the new one on the same current sources (0110-10-15) ---


def _stored(selected: ms.Selected) -> tuple[str, ...]:
    """What a stored selection holds of a ``Selected``: every entry and line it shows."""

    return (*selected.summary, *(item for entry_id, bullets in selected.entries.items() for item in (entry_id, *bullets)), *selected.other)


def test_a_pick_made_again_replaces_the_previous_one_only_when_it_regresses_on_no_check() -> None:
    small = parse_master(_SMALL)
    profile = ms.SelectionProfile(titles=("Staff Backend Engineer",))
    posting = ms.SelectionPosting("Staff Engineer", "Requirements:\n- Kubernetes in production.\n- On-call experience.\n")
    page = _by_bullets(3)
    good = ms.select(small, profile, posting, today=TODAY, measure=page, fill=False)
    assert good.fits and good.conflicts == () and {"b-new-1", "b-new-2"} <= set(good.item_ids())

    # The same selection made again: nothing regresses, the new one stands.
    same = ms.compare_selections(small, good, _stored(good), good.skills, measure=page)
    assert (same.decision, same.regressions, same.problems) == ("new", (), ())
    assert same.previous.valid and same.previous.fits and same.previous.lost == () == same.new.lost and same.new.weak == ()

    # A new selection that lost the evidence of a mandatory requirement (here: made under a budget nothing fits), while
    # the previous one is still valid and still fits: the previous one is kept, and the result says on which check.
    worse = ms.select(small, profile, posting, today=TODAY, measure=lambda _markdown: (3, 0.5), fill=False)
    assert "b-new-2" not in worse.item_ids() and any(conflict.kind == "mandatory_evidence" for conflict in worse.conflicts)
    kept = ms.compare_selections(small, worse, _stored(good), good.skills, measure=page)
    assert kept.decision == "previous" and kept.problems == ()
    assert len(kept.regressions) == 1 and kept.regressions[0].startswith("mandatory coverage: no line now for On-call experience.")
    # No blended score: the worse selection is not saved by anything else it has more of. Give the previous one no skill at all.
    assert ms.compare_selections(small, worse, _stored(good), (), measure=page).decision == "previous"

    # The previous one cannot be kept when it shows a line the master corrected since, or one the master no longer
    # has, or when it no longer fits: then neither is chosen, and the result says what is unresolved.
    corrected = ms.compare_selections(small, worse, _stored(good), good.skills, stale=("b-new-2",), measure=page)
    assert corrected.decision == "unresolved" and corrected.regressions and "b-new-2" in corrected.problems[0]
    retired = ms.compare_selections(small, worse, (*_stored(good), "b-gone"), good.skills, measure=page)
    assert retired.decision == "unresolved" and retired.previous.invalid == ("b-gone",)
    too_long = ms.compare_selections(small, worse, _stored(ms.select(small, profile, posting, today=TODAY, measure=_by_bullets(40))), good.skills, measure=lambda markdown: (3 if "Oldco" in markdown else 2, 0.5))
    assert too_long.decision == "unresolved" and "no longer meets the page limit" in too_long.problems[0]
    # A previous selection that is invalid is still replaced when the new one regresses on nothing.
    assert ms.compare_selections(small, good, (*_stored(good), "b-gone"), good.skills, measure=page).decision == "new"

    out = kept.to_json()
    assert out["decision"] == "previous" and out["previous"]["lost"] == [] and out["new"]["lost"] and out["new"]["fits"] is True


def test_a_pick_made_again_is_checked_for_strength_and_pins_too() -> None:
    small = parse_master(_SMALL)
    posting = ms.SelectionPosting("Staff Engineer", "Requirements:\n- Kubernetes in production.\n")
    roomy = _by_bullets(40)
    new = ms.select(small, ms.SelectionProfile(pins=("b-new-4",)), posting, today=TODAY, measure=roomy)
    evidence = next(requirement for requirement in new.requirements if requirement.mandatory).supporters[0]
    assert evidence == "b-new-1"

    checks = ms.check_selection(small, new.requirements, ("r-new", "b-new-2", "o-cka"), ("Kubernetes",), pins=("b-new-4",), measure=roomy)
    # The certification names Kubernetes, so the requirement is covered; its strongest line and the pin are not shown.
    assert checks.lost == () and checks.weak and checks.pins_missing == ("b-new-4",) and checks.fits and checks.valid
    # Against a previous selection that showed the pin, a new one that had no room for it regresses on that check
    # (and says so itself: a conflict), so the previous one is kept.
    thin = ms.select(small, ms.SelectionProfile(pins=("b-new-4",)), posting, today=TODAY, measure=lambda markdown: (3 if "Mentored" in markdown else 2, 0.5), fill=False)
    assert "b-new-4" not in thin.item_ids() and [conflict.kind for conflict in thin.conflicts] == ["must_keep"]
    again = ms.compare_selections(small, thin, _stored(new), new.skills, pins=("b-new-4",), measure=roomy)
    assert again.decision == "previous" and again.regressions == ("must-keep lines no longer shown: b-new-4",)
    # A previous selection that showed only a weaker line for the requirement loses nothing to a new one that shows the strongest.
    assert ms.compare_selections(small, new, ("r-new", "b-new-2", "o-cka"), ("Kubernetes",), measure=roomy).decision == "new"
    # A heading shown with none of its lines breaks the page constraint.
    empty = ms.check_selection(small, new.requirements, ("r-new", "r-old", "b-new-1"), (), measure=roomy)
    assert empty.empty_entries == ("r-old",) and not empty.fits
