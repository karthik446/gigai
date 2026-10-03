"""0110-038: the candidate's work mode reaches the assess prompt (assess-prompt-v5).

The bug: the profile's work mode (remote only / hybrid / on-site, plus the
area) was an input to acquire's filter only. No assess prompt carried it, so
a remote-only candidate could be "matched" on a role that needs presence
whenever the filter let the posting through (a pasted URL, a posting whose
work mode the filter could not tell).

Now the ONE shared builder (``assessment_core.build_assess_context``) takes
the work mode, and the prompt gets one CANDIDATE WORK MODE paragraph for a
candidate who has one. A candidate with none (or "any") gets a prompt that is
byte for byte what v4 rendered, under the v4 name.

Payload capture on all three paths (the job page's quick assessment,
assess-all, a launched run's assess node), then an A/B with a fake model that
answers ONLY from the prompt text: 0110-035's fake model (rules 4 and 5) plus
the work-mode paragraph as it is written. "Before" is the same candidate with
no work mode, which is the v4 prompt.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re

import pytest

from gigai.canonical import canonical_json_bytes
from gigai.scout import assessment_core, quick_assess
from gigai.scout.assessment_core import (
    ASSESS_PROMPT_VERSION,
    ASSESS_PROMPT_VERSION_NO_WORK_MODE,
    AssessContext,
    AssessJob,
    assess_prompt_version,
    build_assess_context,
    constraints_digest,
    normalize_work_mode,
    render_assess_prompt,
)
from gigai.scout.find_jobs import assess_all, market_acquisition
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.contracts import AcquireInput, FindJobsConfig, RowOutcome, SelectionRule, WorkModePreference
from gigai.scout.find_jobs.market_acquisition import acquire_node
from gigai.scout.profile_records import ProfileSearchSettings, create_profile, selected_profile

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

from .test_run_assess_sealed_config import _candidate, _job, _model_that_applies_rules_4_and_5
from .test_story_bank_run_assess import _ATS, _RESUME, _Binding, _Exa, _Watchlist, _assess, _config, _context, _run_id, _seal_run_input

_WORK_MODE = re.compile(r"^CANDIDATE WORK MODE: (?P<mode>remote only|hybrid|on-site)\b.*?the candidate's own area: (?P<area>[^)]*)\)", re.MULTILINE)
_LOCATION_LINE = re.compile(r"^LOCATION: (?P<location>.*)$", re.MULTILINE)
_PRESENCE = re.compile(r"(?P<kind>Hybrid|On-site): (?:\d days a week )?in our (?P<city>[A-Za-z ]+?) office")
_CONTACT_VALUES = ("@", "linkedin.com", "github.com", "555-")


def _work_mode(prompt: str) -> dict[str, str] | None:
    paragraphs = [block for block in prompt.split("\n\n") if block.startswith("CANDIDATE WORK MODE")]
    assert len(paragraphs) <= 1, "a prompt carries at most one CANDIDATE WORK MODE paragraph"
    if not paragraphs:
        return None
    found = _WORK_MODE.search(paragraphs[0])
    assert found is not None, paragraphs[0][:200]
    return {"mode": found.group("mode"), "area": found.group("area").strip()}


def _slug(city: str) -> str:
    return city.strip().lower().replace(" ", "_")


def _model_that_also_applies_the_work_mode(prompt: str) -> str:
    """0110-035's fake model, plus the CANDIDATE WORK MODE paragraph as it is written.

    It knows nothing but the prompt: with no work-mode paragraph it decides as
    a v4 prompt says (rule 4: a question about presence in a named city the
    candidate is not in; silence about the work mode is no requirement).
    """

    answer = json.loads(_model_that_applies_rules_4_and_5(prompt))
    candidate = _work_mode(prompt)
    posting = prompt.split("POSTING TEXT:", 1)[1].split("\nRESUME:", 1)[0]
    posting_location = _LOCATION_LINE.search(prompt).group("location").strip()  # type: ignore[union-attr]
    own_location = re.search(r"the candidate's own location .*?: (?P<location>[^;]*);", prompt).group("location").strip()  # type: ignore[union-attr]
    rows: list[dict[str, object]] = [row for row in answer["matrix"] if not str(row["requirement"]).startswith("Hybrid in the ")]
    questions: list[dict[str, str]] = [item for item in answer["questions"] if not item["question_id"].startswith("location:") or item["question_id"].endswith("_region")]
    reason = answer["not_a_match_reason"]
    presence = _PRESENCE.search(posting)
    states_remote = "remote" in posting.lower() or "remote" in posting_location.lower()

    def hard(requirement: str, status: str, evidence: str = "") -> None:
        rows.append({"requirement": requirement, "class": "hard", "status": status, "resume_evidence": [evidence] if evidence else []})

    def ask(city: str, requirement: str) -> None:
        hard(requirement, "unclear")
        questions.append({"question_id": f"location:{_slug(city)}", "question": f"Can you work from {city}?", "requirement": requirement})

    if presence is not None:
        city = presence.group("city").strip()
        requirement = f"{presence.group('kind')} in the {city} office"
        if candidate is not None and candidate["mode"] == "remote only":
            # The paragraph: presence is unmet for a remote-only candidate, wherever the office is; no question.
            hard(requirement, "unmet", "candidate work mode: remote only")
            reason = reason or f"The role needs presence in the {city} office and the candidate works remotely only."
        elif city.lower() in own_location.lower():
            hard(requirement, "met", f"candidate location: {own_location}")
        else:
            ask(city, requirement)  # rule 4, with or without a hybrid / on-site paragraph
    elif candidate is not None and candidate["mode"] == "remote only" and not states_remote and "," in posting_location:
        # The paragraph: a named city and no stated work mode is a question, never a match and never a rejection.
        city = posting_location.split(",", 1)[0]
        ask(city, f"Work location: {posting_location} (the posting does not say whether the role is remote)")

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
    """One fake model on both seams: the run's adapter resolution and quick assess's binding."""

    fake = _Binding(_model_that_also_applies_the_work_mode)

    def resolve(config, adapter_target, **_kwargs):
        return fake

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    monkeypatch.setattr(quick_assess, "_resolve_binding", lambda config, model_target, *, home_root: fake)
    return fake


def _with_mode(mode: WorkModePreference | None, *, location: str = "Austin, TX") -> FindJobsConfig:
    """A run's sealed config: a candidate in ``location``, eligible in the US, needing no sponsorship."""

    return replace(_candidate(location=location, visa=False), work_mode=mode, remote=mode is WorkModePreference.REMOTE)


def _write_find_jobs(fx: ProfileFixtureGig, config: FindJobsConfig) -> None:
    (fx.target / "find-jobs.json").write_bytes(canonical_json_bytes(config.to_json()))


_QUICK_POSTING = "Bayou is hiring a Software Engineer. Hybrid: 2 days a week in our Houston office. Requirements: 5+ years of Python in production."


def _quick(fx: ProfileFixtureGig, *, profile_id: str | None = None):
    resume = AssessResumeInput(profile_id=profile_id) if profile_id is not None else AssessResumeInput()
    return quick_assess.run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_text=_QUICK_POSTING, title="Software Engineer", company="Bayou"), resume=resume),
        home_root=fx.home_root, target=fx.target, config=_config(fx.home_root),
    )


# --- the prompt itself ------------------------------------------------------------------------------


def _ctx(work_mode: object = "", location: str = "Austin, TX") -> AssessContext:
    return build_assess_context(resume_text=_RESUME.decode("utf-8"), countries=("US",), location=location, work_mode=work_mode)


_JOB = AssessJob(title="Software Engineer", company="Bayou", location="Houston, TX", posting_text=_QUICK_POSTING)


@pytest.mark.parametrize("none", ["", None, "any", WorkModePreference.ANY, "sometimes"])
def test_no_work_mode_renders_the_v4_prompt_byte_for_byte_under_the_v4_name(none: object) -> None:
    plain = render_assess_prompt(_JOB, AssessContext(resume_text=_RESUME.decode("utf-8"), visa_sponsorship_required=False, countries=("US",), location="Austin, TX"))

    assert render_assess_prompt(_JOB, _ctx(none)) == plain
    assert "CANDIDATE WORK MODE" not in plain and "{{" not in plain
    assert normalize_work_mode(none) == ""
    assert assess_prompt_version(none) == ASSESS_PROMPT_VERSION_NO_WORK_MODE == "assess-prompt-v4"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("remote", {"mode": "remote only", "area": "Austin, TX"}),
        (WorkModePreference.REMOTE, {"mode": "remote only", "area": "Austin, TX"}),
        ("hybrid", {"mode": "hybrid", "area": "Austin, TX"}),
        (WorkModePreference.ONSITE, {"mode": "on-site", "area": "Austin, TX"}),
    ],
)
def test_a_work_mode_adds_exactly_one_paragraph_after_the_constraints_line(mode: object, expected: dict[str, str]) -> None:
    plain = render_assess_prompt(_JOB, _ctx())
    prompt = render_assess_prompt(_JOB, _ctx(mode))

    assert _work_mode(prompt) == expected
    # Decision #207: the hybrid paragraph's words changed in 0110-048, so a hybrid prompt has its own name.
    hybrid = normalize_work_mode(mode) == "hybrid"
    assert assess_prompt_version(mode) == ("assess-prompt-v6" if hybrid else ASSESS_PROMPT_VERSION)
    assert ASSESS_PROMPT_VERSION == "assess-prompt-v5"
    blocks = prompt.split("\n\n")
    paragraph = next(block for block in blocks if block.startswith("CANDIDATE WORK MODE"))
    assert blocks[blocks.index(paragraph) - 1].lstrip("\n").startswith("CANDIDATE CONSTRAINTS:")  # the resume ends with a newline
    # Everything else is the prompt with no work mode, in the same order.
    assert prompt.replace("\n\n" + paragraph, "") == plain
    assert "{{" not in prompt
    # A retry keeps it before the validation paragraph.
    retry = render_assess_prompt(_JOB, _ctx(mode), "matrix has 14 rows; at most 12 allowed")
    assert retry.index("CANDIDATE WORK MODE") < retry.index("A previous attempt at this same prompt was rejected")


def test_the_remote_only_paragraph_states_the_rule() -> None:
    paragraph = next(block for block in render_assess_prompt(_JOB, _ctx("remote")).split("\n\n") if block.startswith("CANDIDATE WORK MODE"))

    for needle in (
        "The candidate takes remote roles only.",
        'A posting that requires on-site, in-office or hybrid presence is one HARD row with status "unmet" and the verdict is "not_a_match"',
        "never ask a location question about it",
        "the candidate's own city included",
        'is never "matched_above_threshold" on that silence and never "not_a_match" for it',
        'with the question_id "location:<city>"',
        "Travel cadence, onboarding trips and occasional team gatherings do not make a remote role hybrid.",
    ):
        assert needle in paragraph, needle


def test_the_hybrid_and_onsite_paragraph_states_the_rule_and_an_unknown_area() -> None:
    paragraph = next(block for block in render_assess_prompt(_JOB, _ctx("hybrid", location="")).split("\n\n") if block.startswith("CANDIDATE WORK MODE"))

    assert _work_mode(paragraph) == {"mode": "hybrid", "area": "unknown"}
    for needle in (
        "hybrid (remote roles, and hybrid or on-site roles in their own area)",
        "(that city or its metro area) has that requirement MET, and you never ask a location question about it",
        'is decided by rule 4 as before (the "location:<city>" question)',
        "A remote posting always has its work mode met.",
    ):
        assert needle in paragraph, needle
    assert "on-site (remote roles, and hybrid or on-site roles in their own area)" in render_assess_prompt(_JOB, _ctx("onsite"))


def test_the_constraints_digest_counts_a_work_mode_and_is_unchanged_without_one() -> None:
    base = constraints_digest(visa_sponsorship_required=False, countries=("US",), location="Austin, TX")

    # No work mode digests exactly as 0110-035 did: such an assessment still stands.
    for none in ("", None, "any", WorkModePreference.ANY):
        assert constraints_digest(visa_sponsorship_required=False, countries=("US",), location="Austin, TX", work_mode=none) == base
    remote = constraints_digest(visa_sponsorship_required=False, countries=("US",), location="Austin, TX", work_mode="remote")
    hybrid = constraints_digest(visa_sponsorship_required=False, countries=("US",), location="Austin, TX", work_mode=WorkModePreference.HYBRID)
    assert len({base, remote, hybrid}) == 3
    assert remote == constraints_digest(visa_sponsorship_required=False, countries=("us",), location=" Austin,  TX ", work_mode=WorkModePreference.REMOTE)


# --- payload capture, path 1: a launched run's assess node ------------------------------------------


def test_a_runs_assess_prompt_carries_the_runs_sealed_work_mode(fx: ProfileFixtureGig, binding: _Binding) -> None:
    posting = _job(101, "Bayou", "Houston, TX", "Hybrid: 2 days a week in our Houston office.")
    config = _with_mode(WorkModePreference.REMOTE)

    output = _assess(fx, _run_id(101), profile_id=None, postings=[posting], config=config)

    assert len(binding.port.prompts) == 1
    assert _work_mode(binding.port.prompts[0]) == {"mode": "remote only", "area": "Austin, TX"}
    assert output.prompt_version == "assess-prompt-v5"
    assert output.constraints_digest == constraints_digest(visa_sponsorship_required=False, countries=("US",), location="Austin, TX", work_mode="remote")
    assert "remote" not in json.dumps({key: value for key, value in output.to_json().items() if key in ("prompt_version", "constraints_digest", "story_bank")})
    assert not [value for value in _CONTACT_VALUES if value in binding.port.prompts[0].split("CANDIDATE WORK MODE", 1)[1].split("\n\n", 1)[0]]


@pytest.mark.parametrize("mode", [None, WorkModePreference.ANY])
def test_a_runs_assess_prompt_has_no_work_mode_paragraph_when_the_run_has_none(fx: ProfileFixtureGig, binding: _Binding, mode: WorkModePreference | None) -> None:
    posting = _job(111, "Bayou", "Houston, TX", "Hybrid: 2 days a week in our Houston office.")
    config = _with_mode(mode)

    output = _assess(fx, _run_id(111), profile_id=None, postings=[posting], config=config)

    assert _work_mode(binding.port.prompts[0]) is None
    assert output.prompt_version == "assess-prompt-v4"
    assert output.constraints_digest == constraints_digest(visa_sponsorship_required=False, countries=("US",), location="Austin, TX")
    # The same bytes the builder gives with no work mode at all.
    from gigai.scout.proposal_execution import _assess_job

    context = build_assess_context(resume_text=_RESUME.decode("utf-8"), countries=config.countries, titles=config.roles, location="Austin, TX", bank=None)
    assert binding.port.prompts[0] == render_assess_prompt(_assess_job(posting, posting.text.encode("utf-8")), context)


# --- payload capture, path 2: the job page's quick assessment ---------------------------------------


def test_quick_assess_carries_the_default_profiles_work_mode(fx: ProfileFixtureGig, binding: _Binding) -> None:
    _write_find_jobs(fx, _with_mode(WorkModePreference.REMOTE))

    response = _quick(fx)

    assert _work_mode(binding.port.prompts[-1]) == {"mode": "remote only", "area": "Austin, TX"}
    assert response.result.verdict.value == "not_a_match"


@pytest.mark.parametrize("mode", [None, WorkModePreference.ANY])
def test_quick_assess_has_no_work_mode_paragraph_when_the_profile_has_none(fx: ProfileFixtureGig, binding: _Binding, mode: WorkModePreference | None) -> None:
    _write_find_jobs(fx, _with_mode(mode))

    response = _quick(fx)

    assert _work_mode(binding.port.prompts[-1]) is None
    assert response.result.verdict.value == "pending_user_answers"  # rule 4 as before: asks about the Houston office


def test_quick_assess_reads_each_profiles_own_work_mode(fx: ProfileFixtureGig, binding: _Binding) -> None:
    """Per profile since 0110-022: a second person's assessment uses THEIR work mode and area, not the default's."""

    _write_find_jobs(fx, _with_mode(WorkModePreference.REMOTE))
    default = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert default is not None
    other = create_profile(
        fx.resolved, label="other person", titles=("staff backend engineer",), titles_to_avoid=(), queries=("staff backend engineer",),
        resume_ref=default.resume_ref,
        search_settings=ProfileSearchSettings(location="Houston, TX", work_mode="hybrid", countries=("US",), max_age_days=None),
    )

    theirs = _quick(fx, profile_id=other.profile_id)
    assert _work_mode(binding.port.prompts[-1]) == {"mode": "hybrid", "area": "Houston, TX"}
    assert theirs.result.verdict.value == "matched_above_threshold"

    mine = _quick(fx, profile_id=default.profile_id)
    assert _work_mode(binding.port.prompts[-1]) == {"mode": "remote only", "area": "Austin, TX"}
    assert mine.result.verdict.value == "not_a_match"


# --- payload capture, path 3: assess-all ------------------------------------------------------------


def _assess_all_one(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> str | None:
    """Assess-all's own per-posting call (``assess_all.quick_assess_one``), the posting page served from memory."""

    real = quick_assess.resolve_job

    def from_memory(job, *, client, home_root=None):
        return real(AssessJobInput(job_text=_QUICK_POSTING, title=job.title, company=job.company), client=client, home_root=home_root)

    monkeypatch.setattr(quick_assess, "resolve_job", from_memory)
    monkeypatch.setattr(quick_assess, "load_config", lambda home_root: _config(Path(home_root)))
    default = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert default is not None
    assess_one = assess_all.quick_assess_one(home_root=fx.home_root, target=fx.target, profile_id=default.profile_id, model_target="ollama_local")
    url = "https://boards.greenhouse.io/fixture/jobs/777"
    return assess_one(assess_all.QueueItem(normalized_url=url, url=url, title="Software Engineer", company="Bayou"))


def test_assess_all_carries_the_work_mode(fx: ProfileFixtureGig, binding: _Binding, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_find_jobs(fx, _with_mode(WorkModePreference.REMOTE))

    verdict = _assess_all_one(fx, monkeypatch)

    assert _work_mode(binding.port.prompts[-1]) == {"mode": "remote only", "area": "Austin, TX"}
    assert verdict == "not_a_match"


def test_assess_all_has_no_work_mode_paragraph_when_the_profile_has_none(fx: ProfileFixtureGig, binding: _Binding, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_find_jobs(fx, _with_mode(None))

    verdict = _assess_all_one(fx, monkeypatch)

    assert _work_mode(binding.port.prompts[-1]) is None
    assert verdict == "pending_user_answers"


def test_the_three_paths_render_the_same_prompt_from_the_same_inputs(fx: ProfileFixtureGig, binding: _Binding) -> None:
    config = _with_mode(WorkModePreference.REMOTE)
    _write_find_jobs(fx, config)
    posting = _job(121, "Bayou", "Houston, TX", "Hybrid: 2 days a week in our Houston office.")

    _assess(fx, _run_id(121), profile_id=None, postings=[posting], config=config)
    run_paragraph = next(block for block in binding.port.prompts[-1].split("\n\n") if block.startswith("CANDIDATE WORK MODE"))
    _quick(fx)
    quick_paragraph = next(block for block in binding.port.prompts[-1].split("\n\n") if block.startswith("CANDIDATE WORK MODE"))

    assert run_paragraph == quick_paragraph


# --- the A/B: what the same candidate is told, before and after --------------------------------------

# Synthetic postings only: 0110-035's shapes that touch presence, plus the work-mode cases.
_POSTINGS: dict[str, tuple[str, str, str]] = {
    "hybrid_houston": ("Bayou", "Houston, TX", "Hybrid: 2 days a week in our Houston office."),
    "hybrid_austin": ("Colorado Bend", "Austin, TX", "Hybrid: 3 days a week in our Austin office."),
    "onsite_dallas": ("Trinity", "Dallas, TX", "On-site: 5 days a week in our Dallas office."),
    "dallas_mode_not_stated": ("Cedar", "Dallas, TX", "Join our platform team."),
    "remote_us": ("Corvid", "Remote - United States", "Remote - United States only."),
    "remote_americas": ("Opal", "Remote - Americas", "Fully remote across the Americas."),
    "remote_poland": ("Wisla", "Remote - Poland", "Remote - Poland only."),
    "no_place_no_mode": ("Lumen", "United States", "Join our platform team."),
}
_M, _P, _N = "matched_above_threshold", "pending_user_answers", "not_a_match"
#: v4 (no work mode in the prompt): a candidate in Austin, TX, eligible in the US, needing no sponsorship.
_BEFORE = {
    "hybrid_houston": _P,  # asks about the Houston office
    "hybrid_austin": _M,  # the candidate is in Austin
    "onsite_dallas": _P,  # asks about the Dallas office
    "dallas_mode_not_stated": _M,  # the bug: silence about the work mode read as a fit
    "remote_us": _M,
    "remote_americas": _M,
    "remote_poland": _N,  # rule 4, unchanged
    "no_place_no_mode": _M,
}
#: v5, the same candidate, remote only.
_AFTER_REMOTE_ONLY = {
    "hybrid_houston": _N,  # needs presence; no question
    "hybrid_austin": _N,  # needs presence, their own city included
    "onsite_dallas": _N,
    "dallas_mode_not_stated": _P,  # a question (location:dallas), never a match on silence
    "remote_us": _M,
    "remote_americas": _M,
    "remote_poland": _N,
    "no_place_no_mode": _M,  # no city, no stated mode: no work-mode row
}
#: v5, the same candidate, hybrid in Austin: nothing changes against v4 (rule 4 already placed them in Austin).
_AFTER_HYBRID = dict(_BEFORE)


def _verdicts(fx: ProfileFixtureGig, first_run: int, mode: WorkModePreference | None) -> tuple[dict[str, str], dict[str, list[str]]]:
    verdicts: dict[str, str] = {}
    asked: dict[str, list[str]] = {}
    for offset, (name, (company, location, text)) in enumerate(_POSTINGS.items()):
        output = _assess(fx, _run_id(first_run + offset), profile_id=None, postings=[_job(first_run + offset, company, location, text)], config=_with_mode(mode))
        assert len(output.assessments) == 1, (name, output.not_assessed)
        verdicts[name] = output.assessments[0].verdict.value
        asked[name] = [item.question_id for item in output.assessments[0].structured_questions]
    return verdicts, asked


def test_before_a_candidate_with_no_work_mode_in_the_prompt(fx: ProfileFixtureGig, binding: _Binding) -> None:
    verdicts, asked = _verdicts(fx, 200, None)

    assert verdicts == _BEFORE
    assert asked["hybrid_houston"] == ["location:houston"] and asked["dallas_mode_not_stated"] == []


def test_after_a_remote_only_candidate(fx: ProfileFixtureGig, binding: _Binding) -> None:
    verdicts, asked = _verdicts(fx, 300, WorkModePreference.REMOTE)

    assert verdicts == _AFTER_REMOTE_ONLY, f"a remote-only candidate was told {verdicts}"
    assert asked["dallas_mode_not_stated"] == ["location:dallas"]
    assert asked["hybrid_houston"] == [] and asked["hybrid_austin"] == [] and asked["onsite_dallas"] == []
    changed = sorted(name for name in _POSTINGS if _BEFORE[name] != _AFTER_REMOTE_ONLY[name])
    assert changed == ["dallas_mode_not_stated", "hybrid_austin", "hybrid_houston", "onsite_dallas"]


def test_after_a_hybrid_candidate_in_their_own_area(fx: ProfileFixtureGig, binding: _Binding) -> None:
    verdicts, asked = _verdicts(fx, 400, WorkModePreference.HYBRID)

    assert verdicts == _AFTER_HYBRID
    assert asked["hybrid_austin"] == [] and asked["hybrid_houston"] == ["location:houston"]


# --- the unchanged skip ---------------------------------------------------------------------------


def _acquire(fx: ProfileFixtureGig, run_id: str, posting, config: FindJobsConfig):
    _seal_run_input(fx, run_id, profile_id=None, config=config)
    acquire_input = AcquireInput(config, "sha256:" + "e" * 64, None, (posting,), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH)
    output = acquire_node(
        _context(fx, run_id, "acquire"), acquire_input,
        http_client=None, exa=_Exa(), ats=_ATS(), watchlist=_Watchlist(), home_root=fx.home_root, target=fx.target,
    )
    outputs = fx.created.workpad / "runs" / run_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "acquire.json").write_bytes(canonical_json_bytes(output.to_json()))
    return output


def _run(fx: ProfileFixtureGig, number: int, posting, config: FindJobsConfig):
    """One run: acquire, then assess what acquire selected, then the sealed ``outputs/assess.json``."""

    run_id = _run_id(number)
    acquired = _acquire(fx, run_id, posting, config)
    if not acquired.selected_postings:
        return acquired, None
    assessed = _assess(fx, run_id, profile_id=None, postings=[item.posting for item in acquired.rows], config=config)
    (fx.created.workpad / "runs" / run_id / "outputs" / "assess.json").write_bytes(canonical_json_bytes(assessed.to_json()))
    return acquired, assessed


def test_an_unchanged_posting_is_assessed_again_when_the_work_mode_is_set_or_changed(
    fx: ProfileFixtureGig, binding: _Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The rank step is another model call (fails open); this test is about the selection.
    monkeypatch.setattr(market_acquisition, "_rank_candidates", lambda *args, **kwargs: ())
    # A posting the work-mode FILTER lets through for every mode (a bare country: "unknown", kept and labelled).
    posting = _job(900, "Lumen", "United States", "Join our platform team. Our Dallas studio is open to visitors.")
    none, remote, hybrid = _with_mode(None), _with_mode(WorkModePreference.REMOTE), _with_mode(WorkModePreference.HYBRID)

    # Run 1: no work mode. Assessed under the v4 name.
    first_acquire, first = _run(fx, 901, posting, none)
    assert first is not None and first.prompt_version == "assess-prompt-v4"
    # Run 2: nothing changed: carried forward, no model call. A v4 assessment of a candidate with no work mode stands.
    second_acquire, second = _run(fx, 902, posting, none)
    assert {row.outcome for row in second_acquire.rows} == {RowOutcome.UNCHANGED}
    assert second is None and len(second_acquire.carried_forward_assessments) == 1
    assert len(binding.port.prompts) == 1

    # Run 3: the profile is now remote only. The posting did not change, the prompt for it did: assessed again.
    third_acquire, third = _run(fx, 903, posting, remote)
    assert {row.outcome for row in third_acquire.rows} == {RowOutcome.UNCHANGED}
    assert third_acquire.carried_forward_assessments == (), "a verdict made without the work mode is not served"
    assert third is not None and third.prompt_version == "assess-prompt-v5"
    assert _work_mode(binding.port.prompts[-1]) == {"mode": "remote only", "area": "Austin, TX"}
    assert len(binding.port.prompts) == 2

    # Run 4: nothing changed since: run 3's verdict is carried, no model call.
    fourth_acquire, fourth = _run(fx, 904, posting, remote)
    assert fourth is None and len(fourth_acquire.carried_forward_assessments) == 1
    assert len(binding.port.prompts) == 2

    # Run 5: remote only -> hybrid: assessed again.
    fifth_acquire, fifth = _run(fx, 905, posting, hybrid)
    assert fifth is not None and fifth_acquire.carried_forward_assessments == ()
    assert _work_mode(binding.port.prompts[-1]) == {"mode": "hybrid", "area": "Austin, TX"}
    assert len(binding.port.prompts) == 3


def test_the_shipped_version_names() -> None:
    assert assessment_core.CURRENT_ASSESS_PROMPT_VERSIONS == {"assess-prompt-v4", "assess-prompt-v5", "assess-prompt-v6"}
