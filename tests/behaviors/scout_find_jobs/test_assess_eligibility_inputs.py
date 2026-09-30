"""0110-003 P1 follow-up: eligibility inputs reach the model from STRUCTURED config, not the resume header.

P1 withholds the resume's name, contact, location and work-authorization header lines from every
model-bound text. The assess and rank prompts still state where the candidate lives, which
countries they may work in and whether they need sponsorship, because those come from
find-jobs.json (``location``, ``countries``, ``visa_sponsorship_required``). Deterministic: no model.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gigai.scout.assessment_core import AssessContext, AssessJob, render_assess_prompt
from gigai.scout.find_jobs.rank_digest import resume_digest
from gigai.scout.find_jobs.rank_run import rank_prefs

_NAME = "Dana Whitfield"
_HEADER_LINES = ("Denver, Colorado | VISA: H1B | US citizen",)
_RESUME = f"""# {_NAME} — Staff Platform Engineer

Denver, Colorado | VISA: H1B | US citizen
dana.whitfield@example.test | 303-555-0142

## Summary
Platform engineer with 9 years running Kubernetes and Postgres in production.

## Experience
**Staff Platform Engineer — Acme** (2020-present)
- Ran the Kubernetes fleet and Postgres clusters behind checkout.
"""
_JOB = AssessJob(title="Platform Engineer", company="Acme", location="Remote US", posting_text="Run Kubernetes. Python preferred.")


def _assert_header_withheld(text: str) -> None:
    assert _NAME not in text and "Whitfield" not in text
    for line in _HEADER_LINES:
        assert line not in text
    for value in ("H1B", "US citizen", "dana.whitfield@example.test", "303-555-0142"):
        assert value not in text


@pytest.mark.parametrize(("needs_visa", "expected"), [(True, "yes"), (False, "no")])
def test_assess_prompt_states_config_eligibility_and_not_the_resume_header(needs_visa: bool, expected: str) -> None:
    ctx = AssessContext(
        resume_text=_RESUME,
        visa_sponsorship_required=needs_visa,
        countries=("US",),
        titles=("Platform Engineer",),
        location="Denver, CO",
    )
    prompt = render_assess_prompt(_JOB, ctx)

    _assert_header_withheld(prompt)
    assert "Kubernetes fleet and Postgres clusters" in prompt  # the body still goes out
    constraints = next(line for line in prompt.splitlines() if line.startswith("CANDIDATE CONSTRAINTS:"))
    assert f"visa sponsorship required = {expected};" in constraints  # {{visa_required}}
    assert "eligible to work from these countries" in constraints and "US" in constraints.split("countries", 1)[1]  # {{countries}}
    assert "Denver, CO" in prompt  # {{candidate_location}}


def test_a_missing_config_location_renders_unknown_and_never_falls_back_to_the_withheld_header() -> None:
    prompt = render_assess_prompt(_JOB, AssessContext(resume_text=_RESUME, visa_sponsorship_required=False))
    _assert_header_withheld(prompt)
    assert "Denver" not in prompt
    assert "unknown" in prompt


@pytest.mark.parametrize("needs_visa", [True, False])
def test_rank_digest_states_config_eligibility_and_not_the_resume_header(needs_visa: bool) -> None:
    config = SimpleNamespace(
        work_mode=None,
        remote=False,
        roles=["Platform Engineer"],
        countries=["US"],
        visa_sponsorship_required=needs_visa,
        location="Denver, CO",
    )
    digest = resume_digest(_RESUME, rank_prefs(config))

    _assert_header_withheld(digest)
    assert "skills: Kubernetes, Postgres" in digest  # the body still feeds the digest
    assert "countries: US" in digest
    assert f"needs visa sponsorship: {'yes' if needs_visa else 'no'}" in digest
    assert "location: Denver, CO" in digest
