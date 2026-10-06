"""0.1.11.3 packet 14: the title rule matches the ROLE AS A WHOLE, "titles to avoid" is a real filter, sponsorship never drops.

Synthetic only. The END outcome is asserted: the rows ``search_postings`` serves for the profile (what the Jobs page
and ``gigai scout jobs list`` show), on a fresh home with fixture Lever boards.

- role as a whole: "Staff Training Engineer" is not listed for the profile title "Staff Engineer"; the legitimate
  shapes ("Staff Software Engineer, ML Training Infrastructure", "Senior Staff Engineer, Platform", "Staff Engineer
  (Backend)", "Staff Engineer - Payments") still are;
- titles to avoid: a posting whose title holds an avoid word or phrase (whole words, any case) is not in that
  profile's list; another profile still lists it; "Trainer" does not drop "Training", "intern" does not drop "Internal";
- sponsorship is a label: a "no sponsorship" posting is listed and kept by acquire for a profile that needs a visa.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.scout import posting_search, profile_records
from gigai.scout.find_jobs.ats_board_clients import TITLE_DENY_WORDS, matches_roles
from gigai.scout.find_jobs.contracts import ATSProvider, FindJobsConfig, PostingRow, SourceKind, SponsorshipStatus
from gigai.scout.find_jobs.filters import exclusion_reason
from gigai.scout.find_jobs.posting_tags import default_store, tag_new_titles
from gigai.scout.find_jobs.title_query import TitleMatcher, title_avoided

from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, lever_job
from tests.support.scout_profile_fixtures import uuids

SLUG = "tern-works"


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    return build_postings_fixture(tmp_path, monkeypatch, deleted=False)


def _seed(fx: PostingsFixture, titles: tuple[str, ...], *, text: str | None = None) -> None:
    fx.seed(SLUG, [lever_job(SLUG, n, title=title, text=text) for n, title in enumerate(titles, start=1)], seen_at=days_ago(1))


def _listed(fx: PostingsFixture, profile_id: str) -> set[str]:
    rows = posting_search.search_postings(fx.home_root, fx.target, profile_ids=[profile_id], now=NOW, limit=200)["postings"]["rows"]  # type: ignore[index]
    return {row["title"] for row in rows}


def _profile(fx: PostingsFixture, label: str, n: int, *, titles: tuple[str, ...], avoid: tuple[str, ...] = ()) -> str:
    resolved = fx.base.gig.resolved
    default = next(item for item in profile_records.list_profiles(resolved) if item.profile_id == fx.default_profile_id)
    record = profile_records.create_profile(
        resolved, label=label, titles=titles, titles_to_avoid=avoid, queries=titles, resume_ref=default.resume_ref,
        uuid_factory=uuids(n),
    )
    return record.profile_id


# --- the role as a whole ---------------------------------------------------------------------

#: Listed for the profile title "Staff Engineer".
STAFF_ENGINEER_KEPT = (
    "Staff Engineer",
    "Senior Staff Engineer, Platform",
    "Staff Engineer (Backend)",
    "Staff Engineer - Payments",
    "Staff Software Engineer, ML Training Infrastructure",
    "Staff Software Engineer",
    "Staff Backend Engineer",
    "Sr. Staff Engineer II",
    "Platform - Staff Engineer",
    "Software Engineer, Staff",
    "Principal/Staff Engineer",
    "Staff Engineer, Training Infrastructure",
)
#: The role's words are all there, but not as that role: not listed.
STAFF_ENGINEER_OUT = (
    "Staff Training Engineer",
    "Staff Engineer in Training",
    "Engineering Training Staff Lead",
    "Software Engineering Trainer, Staff",
    "Staff Application Engineer, Salesforce",
    "Staff Sales Engineer",
    "Staff Technical Program Manager, Engineering Onboarding",
    "Staff Engineering Manager",
    "Director, Staff Engineering",
)
#: Never had the role's words.
UNRELATED = ("Training Coordinator", "Technical Trainer", "Training Specialist")


def test_a_title_with_the_roles_words_but_another_role_is_not_in_the_profiles_list(fx: PostingsFixture) -> None:
    _seed(fx, STAFF_ENGINEER_KEPT + STAFF_ENGINEER_OUT + UNRELATED)

    listed = _listed(fx, fx.second_profile_id)  # titles: staff engineer

    assert listed & set(STAFF_ENGINEER_OUT) == set()
    assert listed == set(STAFF_ENGINEER_KEPT)


def test_a_function_specific_title_keeps_its_suffixed_and_levelled_shapes(fx: PostingsFixture) -> None:
    profile = _profile(fx, "Staff SWE", 70, titles=("Staff Software Engineer",))
    kept = (
        "Staff Software Engineer, ML Training Infrastructure",
        "Senior Staff Software Engineer",
        "Staff Software Engineer (Backend)",
        "Staff Software Engineer - Payments",
        "Staff Backend Software Engineer",
    )
    out = ("Staff Engineer", "Staff Software Training Engineer", "Staff Software Engineering Manager", "Staff Software Sales Engineer")
    _seed(fx, kept + out)

    assert _listed(fx, profile) == set(kept)


def test_a_tag_does_not_bring_back_a_title_the_rule_rejected(fx: PostingsFixture) -> None:
    """The rules tag "Staff Training Engineer" staff + software, the pair of "Staff Engineer": the tag query must not re-add it."""

    titles = ("Staff Engineer", "Staff Training Engineer", "Staff Software Engineer")
    _seed(fx, titles)
    tag_new_titles(default_store(fx.home_root), titles)

    assert _listed(fx, fx.second_profile_id) == {"Staff Engineer", "Staff Software Engineer"}


@pytest.mark.parametrize(
    ("role", "title", "matches"),
    [
        # The operator's shapes.
        ("Staff Engineer", "Staff Training Engineer", False),
        ("Staff Software Engineer", "Staff Software Engineer, ML Training Infrastructure", True),
        ("Staff Engineer", "Staff Software Engineer, ML Training Infrastructure", True),
        ("Staff Engineer", "Senior Staff Engineer, Platform", True),
        ("Staff Engineer", "Staff Engineer (Backend)", True),
        ("Staff Engineer", "Staff Engineer - Payments", True),
        # Level, seniority and discipline words may sit inside or in front.
        ("Staff Engineer", "Staff Software Engineer", True),
        ("Staff Engineer", "Staff Machine Learning Engineer", True),
        ("Staff Engineer", "Staff Site Reliability Engineer", True),
        ("Staff Engineer", "Staff Full-Stack Engineer", True),
        ("Staff Engineer", "Staff AI/ML Engineer", True),
        ("Staff Engineer", "Sr. Staff Engineer II", True),
        ("Staff Engineer", "Senior/Staff Engineer", True),
        ("Staff Engineer", "Staff Security Engineer", True),
        ("Software Engineer", "Senior Software Engineer", True),
        ("Software Engineer", "SOFTWARE ENGINEER II", True),
        ("Software Engineer", "Backend Software Engineer II", True),
        ("Software Engineer", "Payments Software Engineer", True),
        ("Software Engineer", "Software Development Engineer in Test", True),
        ("Senior Engineer", "Sr. Engineer", True),
        # What follows the role names the team or area.
        ("Staff Engineer", "Staff Engineer Payments Platform", True),
        ("Staff Engineer", "Staff Engineer, Training Infrastructure", True),
        ("Staff Engineer", "Platform - Staff Engineer", True),
        ("Staff Engineer", "Staff Engineer: Developer Tools", True),
        ("Staff Engineer", "Staff Engineer | Remote", True),
        # The comma-inverted form, read strictly.
        ("Staff Software Engineer", "Software Engineer, Staff", True),
        ("Software Engineer", "Engineer, Software Platform", True),
        ("Director of Engineering", "Director, Engineering", True),
        ("Director of Engineering", "Engineering Director", True),
        ("Director of Engineering", "Sr. Director, Back-End Engineering", True),
        ("Director of Engineering", "Director, Machine Learning Engineering", True),
        ("Director of Engineering", "Director, Engineering (Platform)", True),
        ("Engineering Manager", "Senior Engineering Manager, Platform", True),
        ("Engineering Manager", "Manager, Software Engineering", True),
        # Any other word between the role's words is the kind of the same role (denylist, not allowlist).
        ("Staff Engineer", "Staff Payments Engineer", True),
        ("Staff Engineer", "Staff Growth Engineer", True),
        ("Staff Engineer", "Staff Search Engineer", True),
        ("Director of Engineering", "Director, Payments Engineering", True),
        ("Product Manager", "Product Marketing Manager", True),
        # A deny word, or another role's noun, between the role's words.
        ("Staff Engineer", "Staff Sales Engineer", False),
        ("Staff Engineer", "Staff Application Engineer, Salesforce", False),
        ("Staff Engineer", "Staff Pre-Sales Engineer", False),
        ("Staff Engineer", "Engineering Training Staff Lead", False),
        ("Staff Engineer", "Staff Technical Program Manager, Engineering Onboarding", False),
        # The inverted form's first part holds the role's words, level and discipline words only.
        ("Staff Engineer", "Staff Accountant, Engineering", False),
        ("Staff Engineer", "Chief of Staff, Engineering", False),
        ("Staff Engineer", "Member of Technical Staff, Engineering", True),
        ("Director of Engineering", "Director of Sales Engineering", False),
        ("Engineering Manager", "Manager, Sales Engineering", False),
        # Another role right after it, or in front of it.
        ("Staff Engineer", "Staff Engineering Manager", False),
        ("Staff Engineer", "Staff Engineer in Training", False),
        ("Staff Engineer", "Software Engineering Trainer, Staff", False),
        ("Software Engineer", "Software Engineering Manager", False),
        ("Software Engineer", "Director, Software Engineering", False),
        ("Software Engineer", "Manager of Software Engineering", False),
        ("Software Engineer", "Software Engineer Recruiter", False),
        ("Software Engineer", "Software Engineer Intern", False),
        ("Security Engineer", "Application Security Engineer", True),
        ("Senior Engineer", "Senior Sales Engineer", False),
        ("Senior Engineer", "Staff Training Engineer", False),
        ("Engineering Manager", "Sales Engineering Manager", False),
        ("Director of Engineering", "Sales Engineering Director", False),
        # A role that names the word itself keeps it.
        ("Sales Engineer", "Senior Sales Engineer", True),
        ("Sales Engineer", "Field Sales Engineer", True),
        ("Training Engineer", "Staff Training Engineer", True),
        ("Engineering Manager", "Staff Engineering Manager", True),
        # Roles of other functions: what is in front narrows, it does not change the role.
        ("Account Executive", "Enterprise Account Executive", True),
        ("Marketing Manager", "Field Marketing Manager", True),
        ("Product Manager", "Senior Technical Product Manager", True),
        ("Designer", "Senior Product Designer", True),
        # A missing word is still no match.
        ("Staff Software Engineer", "Staff Engineer", False),
        ("Staff Engineer", "Training Coordinator", False),
        ("Staff Engineer", "Technical Trainer", False),
        ("Director of Engineering", "Director of Sales", False),
    ],
)
def test_role_rule_table(role: str, title: str, matches: bool) -> None:
    assert matches_roles(title, (role,)) is matches
    assert TitleMatcher((role,), None).matches(title) is matches


def test_the_deny_words_are_the_operators_list() -> None:
    assert TITLE_DENY_WORDS == (
        "training", "trainer", "sales", "presales", "support", "solutions", "customer", "field", "application", "program",
        "recruiting", "recruiter", "coordinator", "intern",
    )


@pytest.mark.parametrize("word", TITLE_DENY_WORDS)
def test_each_deny_word_between_the_roles_words_is_another_role(word: str) -> None:
    title = f"Staff {word.title()} Engineer"
    assert matches_roles(title, ("Staff Engineer",)) is False
    # The word after the role, past a comma, names the team: kept. A profile title that says the word: kept.
    assert matches_roles(f"Staff Engineer, {word.title()} Platform", ("Staff Engineer",)) is True
    assert matches_roles(title, (title,)) is True
    assert matches_roles(f"Senior {title}", (f"{word} engineer",)) is True


def test_each_deny_word_title_is_not_in_the_profiles_list(fx: PostingsFixture) -> None:
    denied = tuple(f"Staff {word.title()} Engineer" for word in TITLE_DENY_WORDS)
    _seed(fx, ("Staff Engineer", "Staff Payments Engineer", *denied))

    assert _listed(fx, fx.second_profile_id) == {"Staff Engineer", "Staff Payments Engineer"}


# --- titles to avoid -------------------------------------------------------------------------

AVOID = ("Trainer", "developer tools", "PAYROLL", "intern")
AVOIDED = (
    "Staff Engineer, Trainer Platform",   # the word
    "Staff Engineer - Developer Tools",   # the phrase, its words in a row
    "Staff Engineer (Developer-Tools)",   # punctuation between the phrase's words does not hide it
    "Staff Engineer, payroll",            # any case
    "Staff Engineer, Intern Program",
)
NOT_AVOIDED = (
    "Staff Engineer",
    "Staff Engineer, Training Platform",        # "Trainer" is not "Training"
    "Staff Engineer, Internal Tools",           # "intern" is not inside "Internal"
    "Staff Engineer, Tools for Developer Teams",  # the phrase's words, not in a row
    "Staff Engineer, Trainers Guild",           # a plural is another word
)


def test_a_title_with_an_avoid_word_or_phrase_is_not_in_that_profiles_list(fx: PostingsFixture) -> None:
    avoiding = _profile(fx, "Avoids", 71, titles=("staff engineer",), avoid=AVOID)
    _seed(fx, AVOIDED + NOT_AVOIDED)

    assert _listed(fx, avoiding) == set(NOT_AVOIDED)
    # The second profile has the same title and avoids nothing: its list is whole.
    assert _listed(fx, fx.second_profile_id) == set(AVOIDED + NOT_AVOIDED)


def test_changing_titles_to_avoid_changes_the_list(fx: PostingsFixture) -> None:
    profile = _profile(fx, "Edits", 72, titles=("staff engineer",))
    _seed(fx, ("Staff Engineer", "Staff Engineer, Payroll"))
    assert _listed(fx, profile) == {"Staff Engineer", "Staff Engineer, Payroll"}

    profile_records.write_profile(fx.base.gig.resolved, profile_id=profile, titles_to_avoid=("payroll",), uuid_factory=uuids(73))

    assert _listed(fx, profile) == {"Staff Engineer"}


@pytest.mark.parametrize(
    ("title", "avoid", "avoided"),
    [
        ("Technical Trainer", ("Trainer",), True),
        ("Training Coordinator", ("Trainer",), False),
        ("Training Coordinator", ("training",), True),
        ("Staff Software Engineer, ML Training Infrastructure", ("training",), True),  # the whole title is read
        ("Staff Application Engineer, Salesforce", ("application engineer",), True),
        ("Staff Engineer, Application Platform", ("application engineer",), False),
        ("Engineer, Application", ("application engineer",), False),  # order matters in a phrase
        ("Sr. C++ Engineer", ("c++",), True),
        ("Sr. C Engineer", ("c++",), False),
        ("Staff Engineer", (), False),
        ("Staff Engineer", ("", "  ", "!!"), False),  # nothing to match: avoids nothing
    ],
)
def test_avoid_rule_table(title: str, avoid: tuple[str, ...], avoided: bool) -> None:
    assert title_avoided(title, avoid) is avoided
    assert TitleMatcher(("engineer", "trainer", "coordinator"), None, avoid).matches(title) is (not avoided and matches_roles(title, ("engineer", "trainer", "coordinator")))


# --- sponsorship is a label ------------------------------------------------------------------

NO_SPONSORSHIP = "Own the platform. Requirements: Python in production. We do not sponsor visas for this role."


def _require_visa(fx: PostingsFixture) -> None:
    path = fx.target / "find-jobs.json"
    config = replace(FindJobsConfig.from_json(parse_json_bytes(path.read_bytes())), visa_sponsorship_required=True)
    path.write_bytes(canonical_json_bytes(config.to_json()))


def test_a_no_sponsorship_posting_is_listed_for_a_profile_that_needs_a_visa(fx: PostingsFixture) -> None:
    _require_visa(fx)
    _seed(fx, ("Staff Engineer",), text=NO_SPONSORSHIP)

    assert _listed(fx, fx.second_profile_id) == {"Staff Engineer"}


def test_no_rule_excludes_a_no_sponsorship_posting_for_a_visa_required_config(fx: PostingsFixture) -> None:
    _require_visa(fx)
    config = FindJobsConfig.from_json(parse_json_bytes((fx.target / "find-jobs.json").read_bytes()))
    row = PostingRow(
        url="https://job-boards.greenhouse.io/acme/jobs/1", normalized_url="https://job-boards.greenhouse.io/acme/jobs/1",
        provider=ATSProvider.GREENHOUSE, board_token="acme", company="acme", title="Staff Engineer",
        location="Denver, CO", published_at=None, content_sha256=None, source_kind=SourceKind.ATS,
        query_key="staff engineer", sponsorship=SponsorshipStatus.NOT_OFFERED,
    )

    assert config.visa_sponsorship_required is True
    assert exclusion_reason(row, config) is None
