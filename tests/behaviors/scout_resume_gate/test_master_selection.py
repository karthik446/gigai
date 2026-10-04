"""0.1.10.9 master P2: the code selector, on the SYNTHETIC master of the master-resume spike.

``master_selection.select`` picks the lines a resume shows from the whole master and fits them to 2
pages by measuring with the shipped PDF template; ``gigai scout resume master selection show`` is its
CLI. Everything here is synthetic (``tests/evals/fixtures/master``: an invented person, 2 profiles, 3
postings) and nothing calls a model: the one model-shaped thing, a stored assessment's posting, is
made with the scripted test transport. Every CLI test runs against a temp ``--home``.

The golden cases pin ``today`` to 2026-10-03 (which roles are "old" depends on the year); what they
expect is ``selection-golden.json``, written for ``SELECTOR_VERSION`` ``sel-1``.
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
    assert set(selected.skills) <= set(master.skills())
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


# --- the cut order (decision 1) ---------------------------------------------------------------


def _cut_stage(master: Master, cut: ms.LengthCut) -> tuple[int, int]:
    """Where a cut belongs in the one allowed order: (0, the old role's rank oldest first), then (1, 0) recent
    roles and projects, then (2, 0) Other lines."""

    if cut.kind == "other":
        return (2, 0)
    entry_id = cut.id if cut.kind == "role" else master.items[cut.id].entry_id
    return (0, OLD_ROLES.index(entry_id)) if entry_id in OLD_ROLES else (1, 0)


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
@pytest.mark.parametrize("posting_id", POSTING_IDS)
def test_recent_roles_always_appear_and_the_oldest_roles_go_first(master: Master, profile_id: str, posting_id: str) -> None:
    selected = _selected(master, profile_id, posting_id)

    # Recent roles are always present, at or above their floors (3 bullets for the newest, 2 for the others).
    for index, role in enumerate(RECENT_ROLES):
        assert len(selected.entries[role]) >= ms.FLOORS[index], role
    # The cuts were made oldest role first; a role is dropped only after its own bullets; nothing recent is
    # touched before every old role is finished.
    stages = [_cut_stage(master, cut) for cut in selected.cut_for_length]
    assert stages == sorted(stages)
    for role in OLD_ROLES:
        ids = [cut.id for cut in selected.cut_for_length]
        if role in ids:
            own = [index for index, cut in enumerate(selected.cut_for_length) if cut.kind == "bullet" and master.items[cut.id].entry_id == role]
            assert own and max(own) < ids.index(role)
            assert role not in selected.entries and role in selected.roles_dropped
    # The two oldest roles are gone in every case; each cut says why, in the words the user reads.
    assert {"r-tes", "r-bri"} <= set(selected.roles_dropped)
    reasons = {line.id: line for line in selected.lines}
    assert all(reasons[bullet].reason == "cut for length: oldest role dropped" for bullet in ("b-tes-01", "b-tes-02", "b-tes-03"))
    assert {cut.code for cut in selected.cut_for_length} <= {
        "cut_oldest_role_dropped", "cut_oldest_role_shortened", "cut_lowest_value", "cut_shown_by_recent_role",
    }


def test_a_tighter_budget_only_applies_more_of_the_same_cut_order(master: Master) -> None:
    """One fixed order: what a looser budget cut is the start of what a tighter one cuts."""

    profile, posting = _profile("profile-swe"), _posting("p2-staff-swe-core-infrastructure")
    cuts = [
        [cut.id for cut in ms.select(master, profile, posting, today=TODAY, measure=_by_bullets(per_page), fill=False).cut_for_length]
        for per_page in (30, 22, 16, 12)
    ]

    assert [len(item) for item in cuts] == sorted(len(item) for item in cuts) and len(cuts[0]) < len(cuts[-1])
    for looser, tighter in zip(cuts, cuts[1:]):
        assert tighter[: len(looser)] == looser


def test_a_pick_that_cannot_fit_says_so_and_keeps_every_recent_role(master: Master) -> None:
    selected = ms.select(master, _profile("profile-ai"), _posting("p1-staff-ai-agent-platform"), today=TODAY, measure=lambda _markdown: (3, 0.5))

    assert not selected.fits and selected.pages == 3 and selected.to_json(master)["fits"] is False
    assert selected.added_to_fill == ()
    # Every cut the rules allow was made, and no more: recent roles stay at their floors or above, each shown
    # project keeps a line, every Other line went.
    for index, role in enumerate(RECENT_ROLES):
        only = [bullet for bullet in selected.entries[role] if bullet in selected.only_evidence or bullet in selected.shown_instead]
        assert len(selected.entries[role]) == max(ms.FLOORS[index], len(only)) or len(selected.entries[role]) - len(only) <= ms.FLOORS[index], role
    assert selected.other == ()
    assert all(len(selected.entries[entry.id]) >= 1 for entry in master.entries_in("projects") if entry.id in selected.entries)


# --- the only evidence of a must-have (decision 2) --------------------------------------------


def test_an_old_roles_only_evidence_line_is_kept_and_its_role_shortened_to_it(master: Master) -> None:
    """ML platform posting: SQL is a must-have and one bullet of the master names it, in a role that ended in 2015."""

    naming = [item.id for item in master.items.values() if item.kind == "bullet" and mentions(item.text, "SQL")]
    assert naming == ["b-cas-08"] and master.items["b-cas-08"].entry_id == "r-cas"
    for profile_id in PROFILE_IDS:
        selected = _selected(master, profile_id, "p3-staff-engineer-ml-platform")
        assert selected.keywords is not None and "SQL" in selected.keywords.must
        # The role is shortened to that one line, not dropped; the older two roles are dropped.
        assert selected.entries["r-cas"] == ("b-cas-08",)
        assert selected.roles_dropped == ("r-bri", "r-tes")
        assert selected.only_evidence["b-cas-08"] == ("SQL",)
        reason = next(line for line in selected.lines if line.id == "b-cas-08")
        assert reason.picked and reason.code == "only_evidence" and reason.reason == "kept: the only line shown that names SQL"
        assert "b-cas-08" not in [cut.id for cut in selected.cut_for_length]
        assert [cut.code for cut in selected.cut_for_length if cut.kind == "bullet" and master.items[cut.id].entry_id == "r-cas"] == ["cut_oldest_role_shortened"] * 2


def test_a_recent_roles_line_is_shown_in_place_of_an_old_roles_only_evidence(master: Master) -> None:
    """Core infrastructure posting: Linux is a must-have. The first pick shows it only in a 2012-2015 role; a 2019-2023
    role has an unshown line naming it. That line is shown instead, and the old role goes like the others."""

    for profile_id in PROFILE_IDS:
        selected = _selected(master, profile_id, "p2-staff-swe-core-infrastructure")
        assert selected.keywords is not None and "Linux" in selected.keywords.must
        assert selected.shown_instead == {"b-hex-28": ("b-cas-07", ("Linux",))}
        assert master.items["b-cas-07"].entry_id == "r-cas" and master.items["b-hex-28"].entry_id == "r-hex"
        assert mentions(master.items["b-cas-07"].text, "Linux") and mentions(master.items["b-hex-28"].text, "Linux")
        assert "b-hex-28" in selected.entries["r-hex"]
        assert "r-cas" not in selected.entries and "r-cas" in selected.roles_dropped
        # Linux is still evidenced by a shown bullet, not only by the skills line.
        assert [bullet for bullets in selected.entries.values() for bullet in bullets if mentions(master.items[bullet].text, "Linux")] == ["b-hex-28"]
        reasons = {line.id: line for line in selected.lines}
        assert reasons["b-hex-28"].picked and reasons["b-hex-28"].code == "shown_instead"
        assert reasons["b-hex-28"].reason == "names Linux; shown in place of an older role's line"
        assert not reasons["b-cas-07"].picked and reasons["b-cas-07"].code == "cut_shown_by_recent_role"
        assert reasons["b-cas-07"].reason == "cut for length: oldest role dropped; a recent role's line now shows Linux"
        # The line now shown was not cut again: it is the only evidence left.
        assert "b-hex-28" not in [cut.id for cut in selected.cut_for_length]


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
        "names_keywords", "posting_wording", "profile_focus", "strongest_remaining", "room_left", "only_evidence", "shown_instead",
    }
    # Every skill of the master is accounted for too, once.
    assert sorted(skill.name for skill in selected.skill_reasons) == sorted(master.skills())
    assert [skill.name for skill in selected.skill_reasons if skill.picked] == list(selected.skills)
    assert len(selected.skills) <= ms.MAX_SKILLS

    out = selected.to_json(master)
    assert [row["id"] for row in out["picked"]] == list(selected.item_ids())
    assert len(out["picked"]) + len(out["left_out"]) == 166
    assert all(row["text"] == master.items[row["id"]].text and row["reason"] for row in (*out["picked"], *out["left_out"]))
    assert out["counts"]["picked"] == len(out["picked"]) and out["counts"]["left_out"] == len(out["left_out"])


def test_a_skill_is_shown_because_the_posting_asks_for_it_or_a_shown_line_names_it(master: Master) -> None:
    selected = _selected(master, "profile-ai", "p1-staff-ai-agent-platform")
    assert selected.keywords is not None
    shown_text = " ".join(master.items[bullet].text for bullets in selected.entries.values() for bullet in bullets)

    for skill in selected.skill_reasons:
        if skill.code == "posting_must":
            assert any(mentions(skill.name, term) for term in selected.keywords.must), skill.name
        elif skill.code == "posting_nice":
            assert any(mentions(skill.name, term) for term in selected.keywords.nice), skill.name
        elif skill.code == "named_by_line":
            assert mentions(shown_text, skill.name), skill.name
        else:
            assert not skill.picked and skill.code in {"not_asked", "skills_limit"}
    # Must-haves come first, in the posting's order.
    codes = [skill.code for skill in selected.skill_reasons if skill.picked]
    assert codes == sorted(codes, key=["posting_must", "posting_nice", "named_by_line"].index)


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


def test_an_old_role_is_dropped_before_any_recent_line_unless_it_holds_the_only_evidence() -> None:
    small = parse_master(_SMALL)
    profile = ms.SelectionProfile(titles=("Staff Backend Engineer",))
    plain = ms.SelectionPosting("Staff Engineer", "Requirements:\n- Kubernetes in production.\n- On-call experience.\n")
    fortran = ms.SelectionPosting("Staff Engineer", "Requirements:\n- Kubernetes in production.\n- Fortran.\n")
    tight = _by_bullets(3)  # 8 bullet lines in the first pick: 3 pages

    dropped = ms.select(small, profile, plain, today=TODAY, measure=tight, fill=False)
    assert dropped.pages_before_fit == 3 and dropped.fits
    assert [(cut.kind, cut.id) for cut in dropped.cut_for_length][:3] == [("bullet", "b-old-2"), ("bullet", "b-old-1"), ("role", "r-old")]
    assert "r-old" not in dropped.entries and len(dropped.entries["r-new"]) >= ms.FLOORS[0]

    kept = ms.select(small, profile, fortran, today=TODAY, measure=tight, fill=False)
    assert kept.entries["r-old"] == ("b-old-1",) and kept.roles_dropped == ()
    assert kept.only_evidence == {"b-old-1": ("Fortran",)}
    assert [(cut.kind, cut.id) for cut in kept.cut_for_length][0] == ("bullet", "b-old-2")
    assert ("role", "r-old") not in [(cut.kind, cut.id) for cut in kept.cut_for_length]

    # The same role is not old for someone reading it in 2020: nothing is cut "oldest first" then.
    earlier = ms.select(small, profile, plain, today=date(2020, 1, 1), measure=_by_bullets(40), fill=False)
    assert earlier.cut_for_length == () and set(earlier.entries["r-old"]) == {"b-old-1", "b-old-2"}


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
    assert out["ok"] is True and selection["selector_version"] == "sel-1"
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
    assert selection["cut_for_length"] and selection["cut_for_length"][0]["reason"].startswith("cut for length: oldest role")
    assert selection["keywords"]["must_missing"] == [] and "Python" in selection["keywords"]["must"]
    assert len(selection["skills"]["picked"]) <= 28 and selection["skills"]["left_out"]
    # A read: nothing was committed or left behind in the journal.
    assert _journal_state(home) == before

    plain = CliRunner().invoke(cli, [
        "scout", "resume", "master", "selection", "show", *_AI, "--job-text", str(posting), "--title", title, "--company", company, "--home", str(home),
    ])
    assert plain.exit_code == 0, plain.output
    assert plain.output.startswith(f"Selection for {title} at {company}, profile Staff AI Engineer: 2 pages (")
    assert "Picked " in plain.output and "left out " in plain.output and "Selector sel-1, master revision 1." in plain.output
    assert "    + sum-ai  Staff engineer with 16 years" in plain.output
    assert "    - sum-backend  " in plain.output and "another summary fits this posting better" in plain.output
    assert "r-tes  Tessel Robotics" in plain.output and ": not shown" in plain.output
    assert "cut for length: oldest role dropped" in plain.output
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
