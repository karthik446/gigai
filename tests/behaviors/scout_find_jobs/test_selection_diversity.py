"""B2 (0.1.8.1 live UAT): candidate diversity for assess selection.

Evidence: a live run selected 5/5 assess picks from ClickHouse alone (batch
had clickhouse=36, coupang=31, gen-digital=2 role-matched rows), 3 sharing
the exact title "Senior Software Engineer - Cloud Infrastructure" (AMER).
``select_for_assessment`` is the pure fix; these tests exercise it directly
against fixtures shaped like that batch, independent of acquire/assess
wiring (coordinator decision: this module owns the algorithm, the acquire
selection loop wires it in separately).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from gigai.scout.find_jobs.selection import (
    DEFAULT_PER_COMPANY_CAP,
    normalize_title,
    select_for_assessment,
)


@dataclass(frozen=True)
class _Row:
    normalized_url: str
    company: str
    title: str
    location: str = "Remote, United States"
    published_at: str | None = "2026-09-20T00:00:00Z"


def _uat_batch() -> list[_Row]:
    """36 ClickHouse + 31 Coupang + 2 gen-digital role-matched rows.

    Not every ClickHouse row is identical: some share the exact UAT title,
    others don't, so the dedupe and per-company cap steps are both
    exercised independently.
    """

    rows: list[_Row] = []
    for i in range(36):
        title = "Senior Software Engineer - Cloud Infrastructure" if i < 20 else f"Backend Engineer {i}"
        rows.append(
            _Row(
                normalized_url=f"https://boards.example/clickhouse/{i}",
                company="ClickHouse",
                title=title,
                published_at=f"2026-09-{(i % 28) + 1:02d}T00:00:00Z",
            )
        )
    for i in range(31):
        rows.append(
            _Row(
                normalized_url=f"https://boards.example/coupang/{i}",
                company="Coupang",
                title=f"Software Engineer {i}",
                location="Seoul, South Korea",
                published_at=f"2026-09-{(i % 28) + 1:02d}T00:00:00Z",
            )
        )
    for i in range(2):
        rows.append(
            _Row(
                normalized_url=f"https://boards.example/gen-digital/{i}",
                company="Gen Digital",
                title=f"Platform Engineer {i}",
                published_at=f"2026-09-{(i % 28) + 1:02d}T00:00:00Z",
            )
        )
    return rows


def test_uat_shape_caps_per_company_and_fills_diversely() -> None:
    rows = _uat_batch()
    result = select_for_assessment(rows, cap=5)

    assert len(result.selected) == 5
    companies = [row.company for row in result.selected]
    assert companies.count("ClickHouse") <= DEFAULT_PER_COMPANY_CAP
    # Round-robin across all three companies represented in the batch.
    assert set(companies) == {"ClickHouse", "Coupang", "Gen Digital"}


def test_uat_shape_has_no_duplicate_titles_in_selection() -> None:
    rows = _uat_batch()
    result = select_for_assessment(rows, cap=5)
    titles = [normalize_title(row.title) for row in result.selected]
    assert len(titles) == len(set(titles))


def test_every_dropped_row_has_a_reason() -> None:
    rows = _uat_batch()
    result = select_for_assessment(rows, cap=5)
    selected_urls = {row.normalized_url for row in result.selected}
    all_urls = {row.normalized_url for row in rows}
    assert set(result.dropped) == all_urls - selected_urls
    assert all(reason in {"duplicate", "company_cap", "over_cap"} for reason in result.dropped.values())


def test_selection_is_deterministic_regardless_of_input_order() -> None:
    rows = _uat_batch()
    shuffled = list(reversed(rows))
    a = select_for_assessment(rows, cap=5)
    b = select_for_assessment(shuffled, cap=5)
    assert {row.normalized_url for row in a.selected} == {row.normalized_url for row in b.selected}


def test_same_company_and_title_dedupes_keeping_newest() -> None:
    older = _Row("https://boards.example/a/old", "Acme", "Senior Engineer", published_at="2026-09-01T00:00:00Z")
    newer = _Row("https://boards.example/a/new", "Acme", "senior engineer!", published_at="2026-09-15T00:00:00Z")
    result = select_for_assessment([older, newer], cap=5)
    assert [row.normalized_url for row in result.selected] == [newer.normalized_url]
    assert result.dropped[older.normalized_url] == "duplicate"


def test_same_title_different_country_is_not_a_duplicate() -> None:
    us_row = _Row("https://boards.example/a/us", "Acme", "Senior Engineer", location="Remote, United States")
    india_row = _Row("https://boards.example/a/in", "Acme", "Senior Engineer", location="Bengaluru, India")
    result = select_for_assessment([us_row, india_row], cap=5, per_company=5)
    selected_urls = {row.normalized_url for row in result.selected}
    assert selected_urls == {us_row.normalized_url, india_row.normalized_url}
    assert not result.dropped


def test_per_company_cap_is_configurable() -> None:
    rows = [
        _Row(f"https://boards.example/acme/{i}", "Acme", f"Role {i}", published_at=f"2026-09-{i + 1:02d}T00:00:00Z")
        for i in range(5)
    ]
    result = select_for_assessment(rows, cap=10, per_company=1)
    assert len(result.selected) == 1
    # Newest kept.
    assert result.selected[0].normalized_url == rows[-1].normalized_url


def test_cap_larger_than_pool_selects_everything_eligible() -> None:
    rows = [_Row(f"https://boards.example/acme/{i}", "Acme", f"Role {i}") for i in range(3)]
    result = select_for_assessment(rows, cap=10, per_company=10)
    assert len(result.selected) == 3
    assert not result.dropped


@pytest.mark.parametrize("cap", [0, -1])
def test_non_positive_cap_selects_nothing(cap: int) -> None:
    rows = [_Row("https://boards.example/acme/1", "Acme", "Role")]
    result = select_for_assessment(rows, cap=cap)
    assert result.selected == ()
    assert result.dropped == {"https://boards.example/acme/1": "over_cap"}


def test_normalize_title_is_case_whitespace_and_punctuation_insensitive() -> None:
    a = normalize_title("Senior Software Engineer - Cloud Infrastructure")
    b = normalize_title("senior   software engineer, cloud infrastructure!")
    assert a == b
