"""0110-035: a LAUNCHED find-jobs run assesses with the run's own sealed config.

The bug: ``bindings._assess_bound`` gives the assess node the TARGET folder,
and the node looked for ``runs/<run_id>/sealed/find-jobs-run-input.json``
under it. A launched run seals that file in the WORKPAD, so the node found
no config: every launched run's assess prompt said ``visa sponsorship
required = no``, eligible countries ``any``, the candidate's own location
``unknown`` and target titles ``unspecified``, whatever the profile's search
settings were. The job page's quick assessment sent all four.

These tests use the launched layout (``test_story_bank_run_assess``'s
harness: sealed input and acquire output in the workpad, the target folder
passed as ``target``), a real gig and a fake model on the C1 seam.

The A/B uses a fake model that answers ONLY from the prompt text it is given:
it reads the CANDIDATE CONSTRAINTS line and the posting, and applies
``assess.md`` rules 4 and 5 the way they are written. "Before" is the same
run with no sealed input the node can find, which is byte for byte the prompt
a launched run got before the fix; "after" is the run with its sealed config.
On HEAD + 0110-034 the two columns are equal (both "before"), so the first
two tests fail there.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re

import pytest

from gigai.scout.assessment_core import ASSESS_PROMPT_VERSION_NO_WORK_MODE, build_assess_context, constraints_digest, render_assess_prompt
from gigai.scout.find_jobs.contracts import FindJobsConfig, PostingRow, SourceToggles
from gigai.scout.proposal_execution import _assess_job

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

from .test_story_bank_run_assess import _RESUME, _Binding, _assess, _posting, _run_id

_CONSTRAINTS = re.compile(
    r"CANDIDATE CONSTRAINTS: visa sponsorship required = (?P<visa>yes|no); .*?: (?P<countries>[^;]*); "
    r"the candidate's own location .*?: (?P<location>[^;]*); target titles the candidate is looking for = (?P<titles>.*)\.$",
    re.MULTILINE,
)
_COUNTRY_CODES = {"poland": "PL", "united states": "US", "canada": "CA"}
_PYTHON_ROW = {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]}


def _constraints(prompt: str) -> dict[str, str]:
    found = _CONSTRAINTS.search(prompt)
    assert found is not None, "the prompt has no CANDIDATE CONSTRAINTS line"
    return {key: value.strip() for key, value in found.groupdict().items()}


def _model_that_applies_rules_4_and_5(prompt: str) -> str:
    """A fake model that decides location and sponsorship rows from the prompt text alone.

    It knows nothing but what the prompt says: the posting (between POSTING
    TEXT: and RESUME:) and the CANDIDATE CONSTRAINTS line.
    """

    facts = _constraints(prompt)
    posting = prompt.split("POSTING TEXT:", 1)[1].split("\nRESUME:", 1)[0]
    countries = {item.strip().upper() for item in facts["countries"].split(",")} if facts["countries"] != "any" else None
    location = facts["location"]
    rows: list[dict[str, object]] = [dict(_PYTHON_ROW)]
    questions: list[dict[str, str]] = []
    reason: str | None = None

    def hard(requirement: str, status: str, evidence: str = "") -> None:
        rows.append({"requirement": requirement, "class": "hard", "status": status, "resume_evidence": [evidence] if evidence else []})

    # Rule 5: sponsorship.
    no_sponsorship = re.search(r"(we do not sponsor[^.]*|no h-?1b[^.]*|no visa sponsorship[^.]*)", posting, re.IGNORECASE)
    if no_sponsorship is not None:
        requirement = no_sponsorship.group(1).strip()
        if facts["visa"] == "yes":
            hard(requirement, "unmet", "visa sponsorship required = yes")
            reason = reason or f"The posting does not sponsor: {requirement}."
        else:
            hard(requirement, "met", "visa sponsorship required = no")

    # Rule 4: a remote role restricted to one country.
    country = re.search(r"Remote - (?P<name>[A-Za-z ]+?) only", posting)
    if country is not None:
        code = _COUNTRY_CODES[country.group("name").strip().lower()]
        requirement = f"Remote - {country.group('name').strip()} only"
        if countries is None or code in countries:
            hard(requirement, "met", f"eligible countries: {facts['countries']}")
        else:
            hard(requirement, "unmet", f"eligible countries: {facts['countries']}")
            reason = reason or f"The role is restricted to {country.group('name').strip()}."

    # Rule 4: a remote role restricted to named states of an eligible country.
    states = re.search(r"residents of these US states only: (?P<list>[A-Z, ]+)\.", posting)
    if states is not None:
        allowed = {item.strip() for item in states.group("list").split(",")}
        requirement = f"Remote for residents of these US states only: {states.group('list').strip()}"
        own = re.search(r",\s*([A-Z]{2})\b", location)
        if own is None:
            hard(requirement, "unclear")
            questions.append({"question_id": "location:us_region", "question": "Which US state do you live in?", "requirement": requirement})
        elif own.group(1) in allowed:
            hard(requirement, "met", f"candidate location: {location}")
        else:
            hard(requirement, "unmet", f"candidate location: {location}")
            reason = reason or f"The role is open to {states.group('list').strip()} residents only."

    # Rule 4: hybrid or in-office presence in a named city.
    office = re.search(r"Hybrid: (?:\d days a week )?in our (?P<city>[A-Za-z ]+?) office", posting)
    if office is not None:
        city = office.group("city").strip()
        requirement = f"Hybrid in the {city} office"
        if city.lower() in location.lower():
            hard(requirement, "met", f"candidate location: {location}")
        else:
            hard(requirement, "unclear")
            questions.append({"question_id": f"location:{city.lower().replace(' ', '_')}", "question": f"Can you work from the {city} office?", "requirement": requirement})

    if reason is not None:
        verdict, questions = "not_a_match", []
    elif questions:
        verdict = "pending_user_answers"
    else:
        verdict = "matched_above_threshold"
    return json.dumps({"verdict": verdict, "matrix": rows, "suggestions": [], "questions": questions, "not_a_match_reason": reason})


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME)


@pytest.fixture
def binding(monkeypatch: pytest.MonkeyPatch) -> _Binding:
    fake = _Binding(_model_that_applies_rules_4_and_5)

    def resolve(config, adapter_target, **_kwargs):
        return fake

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return fake


def _candidate(*, location: str = "Houston, TX", visa: bool = True, countries: tuple[str, ...] = ("US",)) -> FindJobsConfig:
    """The run's sealed config: the profile's own search settings, as ``POST /api/run`` seals them."""

    return FindJobsConfig(
        roles=("staff platform engineer", "software engineer"),
        merged_queries=("staff platform engineer OR software engineer",),
        location=location,
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=False, ats=False, hiringcafe=False),
        countries=countries,
        visa_sponsorship_required=visa,
    )


def _job(number: int, company: str, location: str, text: str) -> PostingRow:
    posting = _posting(f"https://boards.greenhouse.io/fixture/jobs/{number}", f"{text} Requirements: 5+ years of Python in production.")
    return replace(posting, company=company, location=location, board_token="fixture")


# Synthetic postings only. Each names the operator's UAT shape it stands for.
_POSTINGS: dict[str, tuple[str, str, str]] = {
    # A hybrid New York role that does not sponsor (the "Hypr" shape).
    "ny_hybrid_no_sponsorship": ("Halden", "New York, NY", "Hybrid: 3 days a week in our New York office. No H1B sponsorship for this role."),
    # A US-only role that requires existing work authorization.
    "us_only_no_sponsorship": ("Corvid", "Remote - United States", "Remote - United States only. We do not sponsor work visas for this position."),
    # A remote role in a country the candidate cannot work from.
    "remote_poland": ("Wisla", "Remote - Poland", "Remote - Poland only."),
    # A US remote role open to named states only.
    "remote_named_states": ("Lumen", "Remote - United States", "Remote for residents of these US states only: NY, CA."),
    # A hybrid role in the candidate's own city.
    "hybrid_houston": ("Bayou", "Houston, TX", "Hybrid: 2 days a week in our Houston office."),
    # A remote role with no restriction at all (the "Opswat" shape: nothing should constrain it).
    "remote_americas": ("Opal", "Remote - Americas", "Fully remote across the Americas."),
}
#: What a launched run decided BEFORE the fix: its prompt said sponsorship "no", countries "any", location "unknown".
_BEFORE = {
    "ny_hybrid_no_sponsorship": "pending_user_answers",  # sponsorship read as met; asks about the New York office
    "us_only_no_sponsorship": "matched_above_threshold",  # sponsorship read as met: a fit
    "remote_poland": "matched_above_threshold",  # "any" country: a fit
    "remote_named_states": "pending_user_answers",  # location unknown: asks the state
    "hybrid_houston": "pending_user_answers",  # location unknown: asks about Houston
    "remote_americas": "matched_above_threshold",
}
#: What it decides now, for a candidate in Houston, TX, eligible in the US only, who needs sponsorship.
_AFTER = {
    "ny_hybrid_no_sponsorship": "not_a_match",  # rule 5: needs sponsorship, the posting gives none
    "us_only_no_sponsorship": "not_a_match",  # rule 5
    "remote_poland": "not_a_match",  # rule 4: Poland is not an eligible country
    "remote_named_states": "not_a_match",  # rule 4: TX is not on the list; no question
    "hybrid_houston": "matched_above_threshold",  # rule 4: the candidate is in Houston; no question
    "remote_americas": "matched_above_threshold",  # unchanged
}


def _verdicts(fx: ProfileFixtureGig, first_run: int, *, config: FindJobsConfig | None, seal: bool) -> dict[str, str]:
    """One run per posting (a run of one), so each verdict is that posting's own."""

    verdicts: dict[str, str] = {}
    for offset, (name, (company, location, text)) in enumerate(_POSTINGS.items()):
        output = _assess(fx, _run_id(first_run + offset), profile_id=None, postings=[_job(first_run + offset, company, location, text)], config=config, seal=seal)
        assert len(output.assessments) == 1, (name, output.not_assessed)
        verdicts[name] = output.assessments[0].verdict.value
    return verdicts


# --- fail-before / pass-after ---------------------------------------------------------------------


def test_a_launched_runs_assess_prompt_carries_the_runs_sealed_constraints(fx: ProfileFixtureGig, binding: _Binding) -> None:
    company, location, text = _POSTINGS["remote_americas"]

    _assess(fx, _run_id(101), profile_id=None, postings=[_job(101, company, location, text)], config=_candidate())

    assert len(binding.port.prompts) == 1
    assert _constraints(binding.port.prompts[0]) == {
        "visa": "yes",
        "countries": "US",
        "location": "Houston, TX",
        "titles": "staff platform engineer, software engineer",
    }, "the run's prompt lacks the candidate constraints its own sealed config holds"


def test_the_sealed_constraints_change_what_a_run_decides(fx: ProfileFixtureGig, binding: _Binding) -> None:
    after = _verdicts(fx, 200, config=_candidate(), seal=True)

    assert after == _AFTER, f"a launched run decided {after}"
    # What changed, named: four misfits were fits or questions, one question is settled, one posting is untouched.
    changed = sorted(name for name in _POSTINGS if _BEFORE[name] != _AFTER[name])
    assert changed == ["hybrid_houston", "ny_hybrid_no_sponsorship", "remote_named_states", "remote_poland", "us_only_no_sponsorship"]


def test_the_before_column_is_what_a_run_with_no_config_decides(fx: ProfileFixtureGig, binding: _Binding) -> None:
    """The A/B's other side, executed: a run the node finds no sealed input for (the pre-fix launched run)."""

    before = _verdicts(fx, 300, config=None, seal=False)

    assert before == _BEFORE
    assert _constraints(binding.port.prompts[0]) == {"visa": "no", "countries": "any", "location": "unknown", "titles": "unspecified"}


def test_with_no_work_mode_a_hybrid_role_elsewhere_stays_a_question(fx: ProfileFixtureGig, binding: _Binding) -> None:
    """A candidate in Austin with NO work mode in the config and a Houston hybrid role: a location question.

    Rule 4 asks whether the candidate can be at a named office unless their
    location places them there. Since 0110-038 a work mode in the run's
    config adds a CANDIDATE WORK MODE paragraph (``test_assess_work_mode``);
    this config has none (``remote=True`` alone is never a work mode), so the
    prompt is the one v4 rendered.
    """

    company, location, text = _POSTINGS["hybrid_houston"]
    austin = _assess(fx, _run_id(401), profile_id=None, postings=[_job(401, company, location, text)], config=_candidate(location="Austin, TX", visa=False))
    assert austin.assessments[0].verdict.value == "pending_user_answers"
    assert [item.question_id for item in austin.assessments[0].structured_questions] == ["location:houston"]
    assert "work mode" not in binding.port.prompts[-1].lower() and "remote only" not in binding.port.prompts[-1].lower()


# --- one builder, and what the run seals -----------------------------------------------------------


def test_the_run_and_quick_assess_render_the_same_prompt_from_the_same_inputs(fx: ProfileFixtureGig, binding: _Binding) -> None:
    company, location, text = _POSTINGS["remote_named_states"]
    posting = _job(501, company, location, text)
    config = _candidate()

    _assess(fx, _run_id(501), profile_id=None, postings=[posting], config=config)

    # The builder quick assess calls, given the values the run sealed.
    context = build_assess_context(
        resume_text=_RESUME.decode("utf-8"), visa_sponsorship_required=config.visa_sponsorship_required,
        countries=config.countries, titles=config.roles, location=config.location or "", bank=None,
    )
    assert binding.port.prompts[-1] == render_assess_prompt(_assess_job(posting, posting.text.encode("utf-8")), context)


def test_the_run_seals_the_constraints_digest_and_never_the_constraints(fx: ProfileFixtureGig, binding: _Binding) -> None:
    company, location, text = _POSTINGS["remote_americas"]
    config = _candidate()

    output = _assess(fx, _run_id(601), profile_id=None, postings=[_job(601, company, location, text)], config=config)

    assert output.prompt_version == ASSESS_PROMPT_VERSION_NO_WORK_MODE  # 0110-038: no work mode in this config, the v4 prompt
    assert output.constraints_digest == constraints_digest(visa_sponsorship_required=True, countries=("US",), location="Houston, TX")
    sealed = json.dumps(output.to_json())
    assert "Houston" not in sealed, "the output holds a digest of the constraints, not the candidate's location"
    # Order and case of the countries, and spacing of the location, do not change the digest; a real change does.
    same = constraints_digest(visa_sponsorship_required=True, countries=("us",), location=" Houston,  TX ")
    assert same == output.constraints_digest
    assert constraints_digest(visa_sponsorship_required=False, countries=("US",), location="Houston, TX") != output.constraints_digest
    assert constraints_digest(visa_sponsorship_required=True, countries=("US", "CA"), location="Houston, TX") != output.constraints_digest
    assert constraints_digest(visa_sponsorship_required=True, countries=("US",), location="Austin, TX") != output.constraints_digest
