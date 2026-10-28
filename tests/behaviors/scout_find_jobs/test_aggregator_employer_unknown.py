"""F5c (gigai-18i): a posting an aggregator republishes names the aggregator, not the employer.

A job whose company is on ``filters.AGGREGATORS`` (exact name, or the name plus a legal suffix: never a
substring, so a real employer is never flagged) carries ``employer_unknown: true``; the assess prompt says
``COMPANY: unknown (posted through jobgether)``; nothing excludes it. Synthetic data only.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from gigai.scout import proposal_execution
from gigai.scout.assessment_core import AssessContext, AssessJob, render_assess_prompt
from gigai.scout.find_jobs import filters
from gigai.scout.find_jobs.contracts import ATSProvider, FindJobsConfig, PostingRow, SourceKind, SourceToggles

_UNKNOWN_LINE = "COMPANY: unknown (posted through jobgether)\n"


def _row(**overrides: object) -> PostingRow:
    values: dict[str, object] = {
        "url": "https://jobs.lever.co/jobgether/1",
        "normalized_url": "https://jobs.lever.co/jobgether/1",
        "provider": ATSProvider.LEVER,
        "board_token": "jobgether",
        "company": "Jobgether",
        "title": "Senior Backend Engineer",
        "location": "Remote, United States",
        "published_at": None,
        "content_sha256": None,
        "source_kind": SourceKind.ATS,
        "query_key": "backend engineer",
    }
    values.update(overrides)
    return PostingRow(**values)  # type: ignore[arg-type]


def _job(company: str) -> AssessJob:
    return AssessJob(title="Senior Backend Engineer", company=company, location="Remote", posting_text="Build APIs in Python.")


def _ctx() -> AssessContext:
    return AssessContext(resume_text="Python engineer, 8 years.", visa_sponsorship_required=False)


def _config() -> FindJobsConfig:
    return FindJobsConfig(
        roles=("backend engineer",),
        merged_queries=("backend engineer",),
        location="Denver, CO",
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
    )


@pytest.mark.parametrize("company", ["Jobgether", "jobgether", "JOBGETHER", "Jobgether Inc.", "Jobgether, Inc.", "Jobgether LLC", "jobgether-inc"])
def test_aggregator_names_match_exactly_or_with_a_legal_suffix(company: str) -> None:
    assert filters.is_aggregator(company)
    assert filters.aggregator_name(company) == "jobgether"


@pytest.mark.parametrize(
    "company",
    ["Acme", "Jobgether Talent", "NotJobgether", "Jobgetherly", "Acme (via Jobgether)", "Acme Jobgether", "Inc.", "", "   "],
)
def test_a_real_employer_is_never_flagged(company: str) -> None:
    # False positives are the costly error: a substring or a word inside a longer name never matches.
    assert not filters.is_aggregator(company)
    assert filters.aggregator_name(company) is None


@pytest.mark.parametrize("company", ["Jobgether", "Jobgether Inc."])
def test_aggregator_posting_is_employer_unknown(company: str) -> None:
    assert _row(company=company).employer_unknown is True
    assert _job(company).employer_unknown is True


def test_normal_posting_is_not_employer_unknown() -> None:
    assert _row(company="acme").employer_unknown is False
    assert _job("Acme").employer_unknown is False


@pytest.mark.parametrize("company", ["Jobgether", "Jobgether Inc."])
def test_prompt_names_the_employer_unknown(company: str) -> None:
    prompt = render_assess_prompt(_job(company), _ctx())
    assert _UNKNOWN_LINE in prompt
    assert f"COMPANY: {company}\n" not in prompt


def test_prompt_from_stored_row_names_the_employer_unknown() -> None:
    prompt = proposal_execution._assess_prompt(_row(company="Jobgether Inc."), b"Python engineer.", b"Build APIs in Python.", False)
    assert _UNKNOWN_LINE in prompt
    assert "COMPANY: Jobgether Inc.\n" not in prompt


def test_a_stored_flag_never_overrides_the_list() -> None:
    # The list is the only source of truth: a stored true for a company not on it is ignored (and the prompt names
    # the company), a stored false for an aggregator is corrected.
    stale = {**_row(company="acme").to_json(), "employer_unknown": True}
    row = PostingRow.from_json(stale)
    assert row.employer_unknown is False
    assert "employer_unknown" not in row.to_json()
    assert proposal_execution._assess_job(row, b"text").prompt_company == "acme"

    missing = {**_row(company="Jobgether").to_json(), "employer_unknown": False}
    assert PostingRow.from_json(missing).employer_unknown is True


def test_the_flag_follows_the_company_through_replace() -> None:
    assert replace(_row(company="Jobgether"), company="Acme").employer_unknown is False
    assert replace(_row(company="Acme"), company="Jobgether").employer_unknown is True
    assert replace(_job("Jobgether"), company="Acme").prompt_company == "Acme"


def test_the_flag_cannot_be_passed_in() -> None:
    with pytest.raises(TypeError):
        _row(employer_unknown=True)
    with pytest.raises(TypeError):
        AssessJob(title="t", company="Acme", location="r", posting_text="x", employer_unknown=True)  # type: ignore[call-arg]


def test_normal_company_prompt_line_unchanged() -> None:
    prompt = render_assess_prompt(_job("Acme"), _ctx())
    assert "ROLE: Senior Backend Engineer\nCOMPANY: Acme\nLOCATION: Remote\nPOSTING TEXT:\nBuild APIs in Python." in prompt
    assert "posted through" not in prompt


def test_flag_round_trips_and_is_omitted_when_false() -> None:
    normal = _row(company="acme")
    assert "employer_unknown" not in normal.to_json()
    assert PostingRow.from_json(normal.to_json()) == normal

    flagged = _row(company="Jobgether")
    value = flagged.to_json()
    assert value["employer_unknown"] is True
    assert PostingRow.from_json(value) == flagged


def test_old_rows_without_the_key_still_parse() -> None:
    old = {key: value for key, value in _row(company="acme").to_json().items() if key != "employer_unknown"}
    assert PostingRow.from_json(old).employer_unknown is False
    old_aggregator = {key: value for key, value in _row().to_json().items() if key != "employer_unknown"}
    assert PostingRow.from_json(old_aggregator).employer_unknown is True


def test_from_json_rejects_a_non_boolean_flag() -> None:
    value = _row(company="acme").to_json()
    value["employer_unknown"] = "yes"
    with pytest.raises(ValueError):
        PostingRow.from_json(value)


def test_aggregator_posting_is_still_listed() -> None:
    row = _row()
    assert row.employer_unknown is True
    assert filters.exclusion_reason(row, _config()) is None
    assert filters.exclusion_reason(row, _config()) == filters.exclusion_reason(_row(company="acme", board_token="acme"), _config())
