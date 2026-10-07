"""0.1.11.5 TITLE-01: the tag query never pulls a whole family for a role that names one job of it.

Synthetic only. The END outcome is asserted: the rows ``search_postings`` serves for the profile (what the Jobs page
and ``gigai scout jobs list`` show), on a fresh home with fixture Lever boards whose titles are all tagged.

- a role of a WIDE family (``title_query.WIDE_FUNCTIONS``: every family but ``software``) takes no part in the tag
  query: "Forward Deployed Engineer" (mid + solutions) no longer lists every mid + solutions title;
- a QUALIFIED role ("Staff Software Engineer, Agent Infrastructure") takes no part in it either: the qualifier's
  words must be in the posting's title, as the whole-word rule asks;
- the case the tag query exists for stays: "Dir. of Engineering" is listed for "Director of Engineering";
- a role with no function matches exactly as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout import posting_search, profile_records
from gigai.scout.find_jobs.ats_board_clients import matches_roles
from gigai.scout.find_jobs.posting_tags import FUNCTIONS, default_store, tag_new_titles, tag_title
from gigai.scout.find_jobs.title_query import TitleMatcher, function_levels_for_roles, tag_query_for_roles

from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, lever_job
from tests.support.scout_profile_fixtures import uuids

SLUG = "tern-works"


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    return build_postings_fixture(tmp_path, monkeypatch, deleted=False)


def _seed_tagged(fx: PostingsFixture, titles: tuple[str, ...]) -> None:
    """One posting per title, and every title tagged by the rules (what Update sources leaves behind)."""

    fx.seed(SLUG, [lever_job(SLUG, n, title=title) for n, title in enumerate(titles, start=1)], seen_at=days_ago(1))
    tag_new_titles(default_store(fx.home_root), titles)


def _listed(fx: PostingsFixture, profile_id: str) -> set[str]:
    rows = posting_search.search_postings(fx.home_root, fx.target, profile_ids=[profile_id], now=NOW, limit=200)["postings"]["rows"]  # type: ignore[index]
    return {row["title"] for row in rows}


def _profile(fx: PostingsFixture, label: str, n: int, *, titles: tuple[str, ...]) -> str:
    resolved = fx.base.gig.resolved
    default = next(item for item in profile_records.list_profiles(resolved) if item.profile_id == fx.default_profile_id)
    record = profile_records.create_profile(
        resolved, label=label, titles=titles, titles_to_avoid=(), queries=titles, resume_ref=default.resume_ref,
        uuid_factory=uuids(n),
    )
    return record.profile_id


# --- a wide family ---------------------------------------------------------------------------

FDE_ROLES = ("Forward Deployed Engineer", "Staff AI Engineer", "Senior AI Native Engineer")
#: The roles' own words, as that role: listed.
FDE_KEPT = (
    "Forward Deployed Engineer",
    "Senior Forward Deployed Engineer",
    "Forward Deployed Software Engineer",
    "Forward Deployed Engineer, Payments",
    "Staff AI Engineer",
    "Senior Staff AI Engineer (Search)",
    "AI Native Engineer",
)
#: Tagged mid + solutions like "Forward Deployed Engineer", without its words: another job.
SOLUTIONS_FAMILY = (
    "Solutions Architect",
    "Solutions Architect - North Region",
    "Implementation Consultant",
    "Implementation Consultant - West Region",
    "Field Architect",
    "Delivery Solutions Architect",
    "Customer Engineer",
    "Sales Engineer",
    "Professional Services Consultant",
)
#: Tagged staff + ai_ml like "Staff AI Engineer" (or senior + ai_ml like "Senior AI Native Engineer"), without its words.
AI_FAMILY = (
    "Staff AI Security Lead",
    "Staff Product Designer, AI",
    "Staff Account Executive, AI",
    "Senior, ML Engineer - Tracking",
    "Senior Machine Learning Researcher",
)


def test_the_fixture_titles_carry_the_tag_of_the_role_they_must_not_follow() -> None:
    assert tag_title("Forward Deployed Engineer") == tag_title("Solutions Architect")
    for title in SOLUTIONS_FAMILY:
        assert (tag_title(title).level, tag_title(title).function) == ("mid", "solutions"), title
        assert not matches_roles(title, FDE_ROLES), title
    pairs = {(tag_title(role).level, tag_title(role).function) for role in FDE_ROLES}
    for title in AI_FAMILY:
        assert (tag_title(title).level, tag_title(title).function) in pairs, title
        assert not matches_roles(title, FDE_ROLES), title


def test_a_solutions_role_lists_its_own_titles_and_not_the_solutions_family(fx: PostingsFixture) -> None:
    profile = _profile(fx, "FDE", 70, titles=FDE_ROLES)
    _seed_tagged(fx, FDE_KEPT + SOLUTIONS_FAMILY + AI_FAMILY)

    listed = _listed(fx, profile)

    assert listed & set(SOLUTIONS_FAMILY) == set()
    assert listed & set(AI_FAMILY) == set()
    assert listed == set(FDE_KEPT)


def test_a_solutions_role_alone_lists_only_titles_with_its_words(fx: PostingsFixture) -> None:
    profile = _profile(fx, "FDE only", 71, titles=("Forward Deployed Engineer",))
    _seed_tagged(fx, FDE_KEPT + SOLUTIONS_FAMILY)

    assert _listed(fx, profile) == {
        "Forward Deployed Engineer", "Senior Forward Deployed Engineer", "Forward Deployed Software Engineer",
        "Forward Deployed Engineer, Payments",
    }


# --- a qualified role ------------------------------------------------------------------------

QUALIFIED_ROLES = ("Staff Software Engineer, Agent Infrastructure", "Staff Software Engineer, AI Platform")
QUALIFIED_KEPT = (
    "Staff Software Engineer, Agent Infrastructure",
    "Senior Staff Software Engineer (Agent Infrastructure)",
    "Staff Software Engineer - AI Platform",
    "Staff Software Engineer, AI Platform Reliability",
)
#: Tagged staff + software (the pair of "Staff Software Engineer, Agent Infrastructure"), without the qualifier's words.
QUALIFIED_OUT = (
    "Staff Software Engineer",
    "Staff Software Engineer, Billing Platform",
    "Staff Software Engineer - Payments",
    "Staff Backend Developer",
    "Staff Engineer, Storage Infrastructure",
    "Staff Platform Architect",
)


def test_a_qualified_role_does_not_list_the_unqualified_family(fx: PostingsFixture) -> None:
    for title in QUALIFIED_OUT:
        assert (tag_title(title).level, tag_title(title).function) == ("staff", "software"), title
    assert tag_title(QUALIFIED_ROLES[0]) == tag_title("Staff Backend Developer")
    profile = _profile(fx, "Agent infra", 72, titles=QUALIFIED_ROLES)
    _seed_tagged(fx, QUALIFIED_KEPT + QUALIFIED_OUT)

    listed = _listed(fx, profile)

    assert listed & set(QUALIFIED_OUT) == set()
    assert listed == set(QUALIFIED_KEPT)


def test_the_same_role_without_a_qualifier_still_lists_its_family(fx: PostingsFixture) -> None:
    """The tag query is narrowed for the QUALIFIED role only: "Staff Software Engineer" keeps its staff + software adds."""

    profile = _profile(fx, "Staff SWE", 73, titles=("Staff Software Engineer",))
    _seed_tagged(fx, QUALIFIED_KEPT + QUALIFIED_OUT)

    assert _listed(fx, profile) == set(QUALIFIED_KEPT) | set(QUALIFIED_OUT)


# --- what must not change ----------------------------------------------------------------------

DIRECTOR_TITLES = (
    "Director of Engineering", "Engineering Director", "Director, Engineering", "Dir. of Engineering",
    "Director, Software Development", "VP Engineering", "Head of Engineering", "Director of Marketing",
    "Senior Software Engineer",
)


@pytest.mark.parametrize("role", ["Director of Engineering", "Sr. Director of Engineering"])
def test_dir_of_engineering_is_still_listed_for_director_of_engineering(fx: PostingsFixture, role: str) -> None:
    profile = _profile(fx, "Director", 74, titles=(role,))
    _seed_tagged(fx, DIRECTOR_TITLES)

    assert _listed(fx, profile) == {
        "Director of Engineering", "Engineering Director", "Director, Engineering", "Dir. of Engineering",
        "Director, Software Development",
    }


def test_a_role_with_no_function_matches_as_before(fx: PostingsFixture) -> None:
    role = "Barista"
    assert tag_title(role).function is None
    titles = ("Barista", "Senior Barista", "Barista - Night Shift", "Lead Barista Trainer", "Roaster", "Staff Software Engineer")
    profile = _profile(fx, "Barista", 75, titles=(role,))
    _seed_tagged(fx, titles)

    listed = _listed(fx, profile)

    assert listed == {title for title in titles if matches_roles(title, (role,))}
    assert listed == {"Barista", "Senior Barista", "Barista - Night Shift"}


# --- the rule, as a table ----------------------------------------------------------------------


def test_every_function_is_either_wide_or_the_tag_query_family() -> None:
    from gigai.scout.find_jobs.title_query import TAG_QUERY_FUNCTIONS, WIDE_FUNCTIONS

    assert TAG_QUERY_FUNCTIONS == {"software"}
    assert WIDE_FUNCTIONS | TAG_QUERY_FUNCTIONS == set(FUNCTIONS) and not WIDE_FUNCTIONS & TAG_QUERY_FUNCTIONS
    assert "solutions" in WIDE_FUNCTIONS and "ai_ml" in WIDE_FUNCTIONS


@pytest.mark.parametrize(
    ("role", "qualifier"),
    [
        ("Staff Software Engineer, Agent Infrastructure", ("agent", "infrastructure")),
        ("Staff Software Engineer - Payments", ("payment",)),
        ("Staff Software Engineer – Payments", ("payment",)),
        ("Software Engineer (Backend)", ("backend",)),
        ("Software Engineer: Developer Tools", ("developer", "tool")),
        ("Software Engineer | Billing", ("billing",)),
        ("Software Engineer / Billing", ("billing",)),
        ("Software Engineer II, Search", ("search",)),
        ("Director of Engineering, Platform", ("platform",)),
        ("Director, Engineering", ("engineer",)),
        # No qualifier: no separator, a level after it, or a separator inside a word.
        ("Staff Software Engineer", ()),
        ("Director of Engineering", ()),
        ("Dir. of Engineering", ()),
        ("Software Engineer, Staff", ()),
        ("Software Engineer - Senior", ()),
        ("Software Engineer, II", ()),
        ("Full-Stack Engineer", ()),
        ("Principal/Staff Engineer", ()),
        ("Front-End/Back-End Developer", ()),
        ("(Senior) Software Engineer", ()),
        ("", ()),
    ],
)
def test_role_qualifier_table(role: str, qualifier: tuple[str, ...]) -> None:
    from gigai.scout.find_jobs.title_query import role_qualifier

    assert role_qualifier(role) == qualifier


@pytest.mark.parametrize(
    ("roles", "pairs"),
    [
        (("Director of Engineering",), {("director", "software")}),
        (("Staff Software Engineer", "Senior Backend Developer"), {("staff", "software"), ("senior", "software")}),
        (("Software Engineer, Staff",), {("staff", "software")}),
        # A wide family: rule only.
        (("Forward Deployed Engineer",), set()),
        (("Solutions Architect", "Sales Engineer", "Customer Success Manager"), set()),
        (("Staff AI Engineer", "Director of AI", "Data Engineer", "Security Engineer", "Product Manager"), set()),
        (("Mechanical Engineer", "Technical Program Manager", "Account Executive", "Research Scientist"), set()),
        # A qualifier: rule only, whatever the family.
        (("Staff Software Engineer, Agent Infrastructure",), set()),
        (("Staff Software Engineer (Backend)", "Director of Engineering - Platform"), set()),
        # Each role is judged on its own.
        (("Forward Deployed Engineer", "Staff Software Engineer, Payments", "Staff Software Engineer"), {("staff", "software")}),
        (("zzz", ""), set()),
    ],
)
def test_tag_query_table(roles: tuple[str, ...], pairs: set[tuple[str, str]]) -> None:
    assert tag_query_for_roles(roles).pairs == pairs


def test_a_wide_or_qualified_role_keeps_its_function_for_the_generic_title_veto(tmp_path: Path) -> None:
    """0110-8-05 is unchanged: "Staff AI Engineer" still says the profile wants ai_ml, though it adds no tag match."""

    titles = ("Staff Machine Learning Engineer", "Staff Security Engineer", "Staff Platform Engineer", "Staff ML Researcher")
    store = default_store(tmp_path)
    tag_new_titles(store, titles)
    matcher = TitleMatcher(("Staff Engineer", "Staff AI Engineer"), store)

    assert matcher.decide("Staff Machine Learning Engineer").by == "rule"  # generic rule, ai_ml is the profile's function
    assert matcher.decide("Staff Security Engineer").by == "vetoed"
    assert matcher.decide("Staff Platform Engineer").by == "rule"
    assert matcher.decide("Staff ML Researcher").matched is False  # staff + ai_ml, no "engineer": tag only before
    assert matcher.counts.matched_by_tag == 0
    # The model still tags the titles of those roles' levels: its function is what the veto above reads.
    assert function_levels_for_roles(("Staff AI Engineer", "Director of Engineering, Platform", "Barista")) == ("director", "staff")
