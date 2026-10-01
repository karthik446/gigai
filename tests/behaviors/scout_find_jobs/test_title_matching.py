"""0110-021: whole-word, punctuation/filler-insensitive title prefilter.

Synthetic titles only.
"""

from __future__ import annotations

import pytest

from gigai.scout.find_jobs.ats_board_clients import matches_roles

ROLE = ("Director of Engineering",)


@pytest.mark.parametrize(
    "title",
    [
        "Director, Engineering",
        "Director Engineering",
        "Senior Director, Engineering",
        "Director, Software Engineering",
        "Sr. Director, Back-End Engineering",
        "Director, Machine Learning Engineering",
        "Engineering Director",
        "Director, Engineering (Platform)",
        "Director of Engineering",
        "DIRECTOR OF ENGINEERING",
    ],
)
def test_director_of_engineering_matches_comma_style_titles(title: str) -> None:
    assert matches_roles(title, ROLE)


def test_mechanical_engineering_director_passes_the_prefilter() -> None:
    # Documented: a prefilter, ranking/assessment judge fit.
    assert matches_roles("Director, Mechanical Engineering", ROLE)


@pytest.mark.parametrize("title", ["Director of Sales", "Engineering Manager", "Product Director"])
def test_missing_role_word_does_not_match(title: str) -> None:
    assert not matches_roles(title, ROLE)


def test_short_role_word_matches_whole_words_only() -> None:
    assert not matches_roles("Maintain the detail", ("ai",))
    assert not matches_roles("Airflow Engineer", ("ai",))
    assert matches_roles("Director, AI Platform", ("ai",))
    assert matches_roles("Director of AI/ML", ("ai",))


def test_seniority_in_role_is_not_required() -> None:
    assert matches_roles("Director, Engineering", ("Senior Director of Engineering",))
    assert matches_roles("Sr. Engineer", ("Senior Engineer",))


def test_seniority_only_role_still_requires_its_word() -> None:
    assert not matches_roles("Director, Engineering", ("Senior",))


def test_fail_closed_cases() -> None:
    assert not matches_roles("Director, Engineering", ())
    assert not matches_roles("Director, Engineering", ("",))
    assert not matches_roles(None, ROLE)  # type: ignore[arg-type]


def _old(title: str, roles: tuple[str, ...]) -> bool:
    lower = title.lower()
    for role in roles:
        tokens = [t for t in role.lower().split() if t]
        if tokens and all(t in lower for t in tokens):
            return True
    return False


_SYNTHETIC = [
    "Senior Software Engineer",
    "Software Engineering Manager",
    "Staff Platform Engineer",
    "Engineer, Software Platform",
    "Data Engineers",
    "Director of Engineering",
    "Principal Machine Learning Engineer",
    "Backend Software Engineer II",
]
_ROLES = [("software engineer",), ("platform engineer", "data engineer"), ("Director of Engineering",), ("machine learning engineer",)]


def test_titles_matched_by_the_old_substring_rule_still_match() -> None:
    for roles in _ROLES:
        for title in _SYNTHETIC:
            if _old(title, roles):
                assert matches_roles(title, roles), (title, roles)


class _Row:
    def __init__(self, title: str, company: str = "Acme", location: str = "Remote") -> None:
        self.title, self.company, self.location = title, company, location


def test_second_check_role_match_uses_the_shared_matcher() -> None:
    from gigai.scout.find_jobs.market_acquisition import _role_match

    assert _role_match(_Row("Director, Engineering"), ("Director of Engineering",))
    assert _role_match(_Row("Senior Director, Engineering"), ("Director of Engineering",))
    assert not _role_match(_Row("Maintain the detail"), ("ai",))
    assert not _role_match(_Row("Director of Sales"), ("Director of Engineering",))
    assert not _role_match(_Row("Director, Engineering"), ())


def test_second_check_keeps_old_verbatim_title_matches() -> None:
    from gigai.scout.find_jobs.market_acquisition import _role_match

    for roles in _ROLES:
        for title in _SYNTHETIC:
            if _old(title, roles) and any(r.lower() in title.lower() for r in roles):
                assert _role_match(_Row(title), roles), (title, roles)


def test_url_lookup_wildcard_matches_any_title() -> None:
    from gigai.scout.find_jobs.ats_board_clients import MATCH_ANY_TITLE_ROLE

    assert matches_roles("Anything At All", (MATCH_ANY_TITLE_ROLE,))
