"""P1 (v0.1.9): the extracted assessment core is byte-for-byte the old assess loop.

Golden prompt strings below were captured from the PRE-P1
``proposal_execution._assess_prompt`` at HEAD 393fd85 on fixed inputs (EXECUTED
before any code moved), so a template that drifts by a single byte fails here.
The retry / fenced-JSON / exception-mapping cases exercise ``assess_once`` with
a scripted binding (no live provider); the digest and resource cases read the
shipped ``assess.md`` through ``importlib.resources`` and, under the installed
lane, through the wheel venv's own interpreter.
"""

from __future__ import annotations

import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from gigai.adapters.port import InvocationResult, ModelInvocationError, NormalizedUsage
from gigai.canonical import digest_imported_bytes
from gigai.scout import assessment_core, proposal_execution
from gigai.scout.assessment_core import (
    INSTRUCTIONS_DIGEST,
    AssessAttempt,
    AssessContext,
    AssessJob,
    PriorAnswer,
    assess_once,
    load_assess_instructions,
    render_assess_prompt,
)
from gigai.scout.find_jobs.contracts import NotAssessedReason
from gigai.scout.proposals import parse_assessment_proposal
from tests.scenarios import InstalledGigAI


# --- fixed inputs (identical to the pre-P1 capture script) -------------------

_TITLE = "Senior Backend Engineer"
_COMPANY = "Acme Corp"
_LOCATION = "Denver, CO"
_RESUME = b"Karthik built Python services for 6 years.\nOperated Kubernetes clusters in production.\n"
_POSTING_TEXT = b"We need 5+ years of Python. Remote OK.\nNo visa sponsorship available for this role.\n"
_VALIDATION_ERROR = "matrix[0].status must be one of met|partial|gap"

# P2 (v0.1.9) INTENTIONAL CHANGE: the template body was replaced with S29
# r1's verdict instructions (plan section "P2"; operator answers 5 and 10),
# so these goldens were RE-CAPTURED from the new ``render_assess_prompt`` on
# the same fixed inputs the pre-P1 capture used (visa required = no, no
# retry; countries/titles default empty since ``_ctx()`` below still omits
# them). The old pre-P1 goldens they replace lived at these same sha256/len
# values with the met|partial|gap prompt; that prompt is gone from the live
# path (P2 rules 3-5 vocabulary is met|unmet|unclear plus a verdict).
#
# P2-r2 (v0.1.9) INTENTIONAL CHANGE #2: the "fair test" live acceptance (5
# real codex calls, original round-1 resumes that never state a location,
# with countries/titles set the way a real find-jobs.json + profile would)
# found 0/5 known-match pairs reached matched_above_threshold -- the bare
# "countries = {{countries}}" fact list did not tell the model those
# countries are where the CANDIDATE is eligible to work, so a posting's own
# remote-region location kept getting reclassified ASKABLE under rule 1
# ("resume is simply silent"). The CANDIDATE CONSTRAINTS line was reworded
# (assess.md only; no verdict-consistency code rule touched) to say
# explicitly that a posting location matching one of these countries is MET,
# not askable. Goldens below were re-captured again for the reworded line.
#
# P3 (v0.1.9) INTENTIONAL CHANGE: added a "never re-ask an answered id" rule
# and a {{prior_answers}} paragraph (dropped from the rendered prompt when
# ``AssessContext.prior_answers`` is empty, exactly like
# {{validation_error}}).
#
# assess-prompt-v2 (v0.1.9) INTENTIONAL CHANGE (operator-approved
# 2026-09-25; orchestrator/research/assess-prompt-review/review.md, F1-F9):
# the instruction body was rewritten -- WHAT COUNTS AS A REQUIREMENT, class =
# importance / status = evidence, rules 1-8 (years-first seniority, countries
# + state/province regions decided from the candidate's own location, visa
# yes/no, titles context-only, verdict from rows, prior answers never
# re-asked), OUTPUT BOUNDS (1-12 rows, 0-12 questions, id charset), a new
# {{candidate_location}} placeholder on the CANDIDATE CONSTRAINTS line
# (renders "unknown" when ``AssessContext.location`` is empty, as ``_ctx()``
# leaves it) and a retry tail that names the violated bound/rule and tells
# the model it cannot see the rejected attempt. Goldens below were
# RE-CAPTURED (EXECUTED) from ``render_assess_prompt`` on the same fixed
# inputs; the sha256 constants guard the transcription.
#
# assess-prompt-v3 (v0.1.9) INTENTIONAL CHANGE (operator-approved 2026-09-25,
# msg_7fe22ed8286e as amended by msg_40ceefd7c540; evidence in
# orchestrator/research/evals/2026-09-25-first-live.md): three gaps the first
# live eval found. Rule 4 fails a posting whose remote/residency/office country
# is outside the eligible countries as a HARD unmet row with not_a_match_reason
# naming the country (never a location ask; the old ask case (a) is gone). Rule 1
# gives judgement guidance on explicit resume disclaimers ("has not worked on
# X" against a core HARD requirement usually means unmet; partial evidence such
# as "writes little product code" stays an ask) and makes degree/enrolment
# requirements ASKABLE, never unmet on the resume alone (stated enrolment meets
# it; otherwise asked once as "education:enrolled"). Goldens and the shipped digest below were RE-CAPTURED
# (EXECUTED) from ``render_assess_prompt`` on the same fixed inputs; the retry
# tail and every placeholder are unchanged.
#
# assess-prompt-v3-r1 (v0.1.9) INTENTIONAL CHANGE (operator-approved 2026-09-25,
# msg_e4de88fcfa29; evidence in orchestrator/research/evals/2026-09-25-prompt-v3.md):
# two sentences. Rule 1: a disclaimer that covers an area answers every row about
# that area, HARD or ASKABLE (unmet, never asked) -- the five remaining hard false
# asks all sat on sibling rows of a disclaimed area. Rule 2: when the verdict is
# not_a_match, ask no questions (the code strips any that arrive anyway, see
# ``assessment_core._strip_not_a_match_questions``). Goldens and the shipped
# digest below were RE-CAPTURED (EXECUTED) from ``render_assess_prompt`` on the
# same fixed inputs; the retry tail and every placeholder are unchanged.
GOLDEN_PROMPT = (
    "You are assessing one real job posting against one candidate's resume for GigAI Scout. Return a "
    'workflow-state verdict, not a grader score: the verdict decides what GigAI does next, so pick the '
    "state that describes the right NEXT ACTION, not just how good the fit looks. The candidate's facts "
    'come from three places of equal authority: the RESUME text, the CANDIDATE CONSTRAINTS line near the '
    "end (eligible countries, the candidate's own location, sponsorship need, target titles) and, when "
    'present, the PRIOR ANSWERS list. "The candidate\'s facts" below always means all of them together.\n'
    '\n'
    'STATES (pick exactly one):\n'
    '- "matched_above_threshold": every HARD requirement is met (or the posting states none) and no '
    'question is open; a reasonable person would apply today without more info.\n'
    '- "pending_user_answers": no HARD requirement is unmet, but at least one requirement\'s status can '
    'only be resolved by asking the candidate something their facts neither confirm nor rule out. Never '
    "re-ask something the candidate's facts already state one way or the other.\n"
    '- "not_a_match": at least one HARD requirement is unmet by clear, explicit evidence in the '
    'candidate\'s facts (a stated gap, e.g. resume says "3 years" and posting requires "8+"; or the '
    'resume\'s own title/level literally says "Intern" against a posting that requires "Staff"). Never use'
    ' not_a_match for silence: silence is always a question, never a verdict, no matter how central the '
    'requirement looks.\n'
    '\n'
    "WHAT COUNTS AS A REQUIREMENT (the ROLE line is the posting's title, not a requirement):\n"
    '- Extract rows only from the posting\'s requirement sections ("Requirements", "What we look for", '
    '"Required skills and experience", "Qualifications", "Nice to have", "Preferred", "Bonus" and the '
    'like) plus any explicit location, residency, in-office, clearance or sponsorship statement anywhere '
    'in the posting.\n'
    '- Do NOT create rows for duties ("What you\'ll do"), company boilerplate, pay, benefits, start dates,'
    ' contract or internship length, onboarding trips, travel cadence (e.g. "quarterly in-person '
    'sessions"), application rules, or behavioral and soft bullets (communication, ownership, curiosity, '
    '"seeks feedback", "comfortable with ambiguity", "uses AI tools responsibly", "familiar with standard'
    ' IDEs and debugging practices"). None of these is ever a question.\n'
    '- One bullet is ONE requirement, even when it lists several examples or sub-clauses. Test its '
    'substance, not every example word: "advanced SQL ... joins, window functions, aggregations" is met '
    'by demonstrated advanced SQL; "dbt, Airflow, Snowflake, or similar" is met by any comparable tool; '
    '"statistical or ML models" is met by either.\n'
    '- If the posting states no requirements at all, emit one row: requirement "No stated requirements", '
    'class "nice_to_have", status "met", empty resume_evidence.\n'
    '\n'
    'REQUIREMENT CLASSES (assign exactly one to each row; the class says how important the row is, the '
    'status says what the evidence shows):\n'
    '- HARD: required years of experience; a level the posting states as a requirement (see rule 3); an '
    'explicit clearance; an explicitly excluded domain; and a location/residency, in-office or '
    'sponsorship statement the posting itself makes (see rules 4 and 5).\n'
    '- ASKABLE: a named tool, cloud platform, language, framework, database, domain or specific '
    'technology the posting requires, and any degree, enrolment or student-status requirement (see rule '
    '1). Lacking it is never disqualifying by itself (tools are learnable, and the candidate may have '
    'unlisted experience), so an ASKABLE row is "met", "unclear" (ask about it) or "unmet" (the '
    "candidate's facts rule it out; this does NOT force not_a_match).\n"
    '- NICE_TO_HAVE: anything the posting phrases as "bonus", "plus", "preferred", "nice to have", or '
    'lists under such a heading. A "preferred" option inside a required bullet ("Spark preferred; '
    'Ray/Dask or similar", "GitHub preferred") does not make the bullet nice_to_have: the bullet stays '
    'required and the preferred tool is just one way to meet it. NICE_TO_HAVE rows never change the '
    'verdict and never produce a question.\n'
    '\n'
    'RULES:\n'
    "1. Status is decided from the candidate's facts (resume text plus CANDIDATE CONSTRAINTS plus PRIOR "
    'ANSWERS), never from your overall impression:\n'
    '   - The facts satisfy the requirement\'s substance, stated or clearly paraphrased -> "met". Put the '
    'quote or close paraphrase you relied on in resume_evidence.\n'
    '   - The facts explicitly contradict it (their own words state a lower level, fewer years, the wrong'
    ' domain, or that the candidate lacks it) -> "unmet". Quote the contradicting text in '
    'resume_evidence. An explicit disclaimer in the resume ("has not worked on X", "does not do X", "no '
    'experience with X") is a judgement call, not a keyword match: weigh how explicit the statement is '
    'and how central the requirement is to the role. A clear disclaimer against a core HARD requirement '
    'usually means "unmet" (a resume that says "has not worked on cloud infrastructure, SRE, or platform '
    'engineering" is unmet for an SRE posting\'s production-infrastructure requirement; do not ask about '
    'it), while partial or adjacent evidence is not a contradiction ("writes little product code" or '
    '"minimal application-level coding" against a required language does not rule the language out: that '
    'row stays "unclear" and you ask). A disclaimer that covers an area ("has not worked on X") answers '
    'every row about that area, HARD or ASKABLE: mark each of them unmet and ask nothing about it. A '
    'degree, enrolment or student-status requirement ("currently pursuing a degree", "enrolled student") '
    'is ASKABLE and is never "unmet" on the resume alone: a resume that states current enrolment meets '
    'it; a completed degree or years of full-time work do not settle whether the candidate is enrolled '
    'now, so otherwise the row is "unclear" and you ask once, with the question_id "education:enrolled".\n'
    '   - The facts are simply silent (the topic is not mentioned at all) -> "unclear", with an empty '
    'resume_evidence list. Silence is never "unmet", no matter how central the requirement looks.\n'
    '2. Questions: every HARD or ASKABLE row with status "unclear" gets exactly ONE question, and every '
    "question points at exactly one such row (copy that row's requirement string verbatim into the "
    'question\'s "requirement"). No question for a "met" or "unmet" row, and none for a NICE_TO_HAVE row. '
    'If one underlying fact would resolve several rows, ask it once and reference the first of those '
    'rows. When the verdict is "not_a_match", ask no questions.\n'
    '3. Seniority and level: when the posting states years of experience, years are the level test, and '
    'the job title\'s suffix ("II", "Senior", "Staff", "Principal") is not a separate requirement. Only '
    'when the posting requires a level in words ("Staff-level", "must be at Principal level") is level '
    "itself a HARD row: met by the resume's stated level or by years that clearly exceed the posting's "
    "stated minimum, unmet only when the resume's own title or level literally states a lower level, "
    "unclear otherwise. Never infer the candidate's level from the target titles in CANDIDATE "
    'CONSTRAINTS.\n'
    "4. Location, residency and remote region: the eligible countries and the candidate's location in "
    'CANDIDATE CONSTRAINTS are facts about the candidate, exactly like a resume statement. A posting '
    'whose stated country (its LOCATION line, remote region, residency requirement or office country) is '
    'one of the eligible countries has its country requirement MET; when the posting names several '
    'countries, one match is enough, and a posting that names no country states no country requirement. '
    'If the country list says "any", the candidate has declared no country restriction and every location'
    ' is met. A posting that restricts remote work, residency or the office to a country that is NOT one '
    'of the eligible countries (e.g. "Remote - Poland" against eligible countries "US, GB") is one HARD '
    'row with status "unmet" and the verdict is "not_a_match", with not_a_match_reason naming that '
    'country: never ask a location question about it, because the candidate has already stated where they'
    ' can work. When the posting restricts a remote role to named states or provinces inside an eligible '
    "country, that restriction is one HARD row decided from the candidate's location: if the candidate's "
    'location names a state or province, the row is "met" when that region is on the posting\'s list and '
    '"unmet" when it is not; if the candidate\'s location is "unknown" or names no state or province, the '
    'row is "unclear" and you ask ONCE, with the question_id "location:<country>_region" where <country> '
    'is the lowercase two-letter code of the posting\'s country (e.g. "location:ca_region", '
    '"location:us_region"). Ask a location question in only one other case: the posting requires '
    'in-office or hybrid presence in a named city inside an eligible country and neither the resume, the '
    'candidate\'s location nor the constraints place the candidate there (question_id "location:<city>", '
    'e.g. "location:san_francisco"). Travel cadence, onboarding trips and "remote-first" policy '
    'statements are not requirements and are never asked about.\n'
    '5. Work authorization and sponsorship: "visa sponsorship required = yes" means the candidate needs '
    'sponsorship. If the posting states it does not sponsor, or requires existing authorization with no '
    'sponsorship, that is a HARD "unmet" row and the verdict is "not_a_match". If the posting says '
    'nothing about sponsorship, do not ask about it. "visa sponsorship required = no" means the candidate'
    ' needs no sponsorship: any sponsorship, work-authorization or export-control statement is "met", and'
    ' you never ask about work authorization.\n'
    '6. Target titles in CANDIDATE CONSTRAINTS describe what the candidate is looking for. They are '
    'context only: never a requirement, never a reason for "not_a_match", and never evidence for or '
    'against a level or a domain.\n'
    '7. Verdict, computed from the rows only:\n'
    '   - any HARD row "unmet" -> "not_a_match" (not_a_match_reason is one sentence naming that row);\n'
    '   - otherwise, any question -> "pending_user_answers";\n'
    '   - otherwise -> "matched_above_threshold".\n'
    '   An ASKABLE or NICE_TO_HAVE row with status "unmet" never forces "not_a_match".\n'
    '8. A question whose id has a prior answer (PRIOR ANSWERS) is resolved by that answer, never '
    're-asked: set the row\'s status from the answer ("met" or "unmet"), cite the answer in '
    'resume_evidence, and emit no question for it.\n'
    '\n'
    'OUTPUT BOUNDS (the validator rejects anything outside them, and you get exactly one retry):\n'
    '- matrix: 1 to 12 rows. If the posting yields more, keep every HARD row, then ASKABLE rows, and drop'
    ' NICE_TO_HAVE rows first; merge closely related bullets into one row rather than exceed 12.\n'
    '- questions: 0 to 12 objects, each with all three keys "question_id", "question", "requirement"; '
    'never a bare string.\n'
    '- question_id: exactly one colon, lowercase letters, digits and underscores only, in the form '
    '"<category>:<value>". category is one of: years, seniority, clearance, domain, location, '
    'authorization, cloud, language, framework, tool, database, skill, education, other. value names the '
    'underlying real-world fact, not this posting, so the same fact asked across different postings '
    'reuses the same id (e.g. "cloud:gcp", "years:python", "clearance:secret", "seniority:staff", '
    '"location:san_francisco", "location:ca_region").\n'
    '- Every string is under 700 characters; requirement text is copied or closely paraphrased from the '
    'posting.\n'
    '\n'
    'Return JSON only (no prose, no markdown fences):\n'
    '{"verdict": "matched_above_threshold|pending_user_answers|not_a_match",\n'
    ' "matrix": [{"requirement": "<from the posting>", "class": "hard|askable|nice_to_have",\n'
    ' "status": "met|unmet|unclear", "resume_evidence": ["<quote or paraphrase, or empty>"]}],\n'
    ' "questions": [{"question_id": "<category>:<value>", "question": "<specific>", "requirement": "<the '
    'matrix row\'s requirement, verbatim>"}],\n'
    ' "not_a_match_reason": "<one sentence, or null if verdict is not not_a_match>"}\n'
    '\n'
    'ROLE: Senior Backend Engineer\n'
    'COMPANY: Acme Corp\n'
    'LOCATION: Denver, CO\n'
    'POSTING TEXT:\n'
    'We need 5+ years of Python. Remote OK.\n'
    'No visa sponsorship available for this role.\n'
    '\n'
    '\n'
    'RESUME:\n'
    'Karthik built Python services for 6 years.\n'
    'Operated Kubernetes clusters in production.\n'
    '\n'
    '\n'
    'CANDIDATE CONSTRAINTS: visa sponsorship required = no; the candidate is eligible to work from these '
    'countries (a fact about the candidate, applied by rule 4; "any" means no country restriction): any; '
    'the candidate\'s own location (city, state/province, country as they wrote it; "unknown" when not '
    'given; applied by rule 4): unknown; target titles the candidate is looking for = unspecified.'
)

# Same fixed inputs, visa required = yes, retry with the validation error fed back.
GOLDEN_RETRY_PROMPT = (
    GOLDEN_PROMPT.replace("visa sponsorship required = no;", "visa sponsorship required = yes;")
    + "\n\nA previous attempt at this same prompt was rejected by the validator: "
    + _VALIDATION_ERROR
    + '. You cannot see that attempt, so produce a fresh answer that avoids the named problem: "at most '
    '12 allowed" or "at least 1 row is required" means a list broke OUTPUT BOUNDS (drop NICE_TO_HAVE '
    'rows first, merge related bullets); "question_id ... is invalid" means an id broke the id form; '
    '"rule 7" means the verdict contradicted the rows or questions (recompute it from the rows: any '
    "HARD unmet -> not_a_match, else any question -> pending_user_answers, else "
    'matched_above_threshold); "no JSON object" means the answer was not bare JSON. Return corrected '
    "JSON only, matching the schema exactly."
)

# sha256 of the assess-prompt-v2 prompts, recorded by the capture script
# above (the strings above are the source of truth; the digests guard the
# transcription).
GOLDEN_SHA256 = "e6842a9ca942773b56fe6aa4493372de4856194e0af7c54c216765da91a6d849"
GOLDEN_RETRY_SHA256 = "fe4504699c7c91a70ac49c38f075a5ec9a7d9684ad45de85af3188a656adbfc8"
# 13,000-byte posting text and resume plus a 400-char validation error:
# the three ``_MAX_PROMPT_*`` bounds (12_000 / 12_000 / 300) produce this exact prompt.
GOLDEN_BOUNDED_SHA256 = "f5d4b16436d818f59fe11f00160f3beb3fcee0eec73dafbd8b32af624d5cbdc5"
GOLDEN_BOUNDED_LEN = 37_389

# Digest of the shipped ``assess.md`` bytes; bump ONLY when the template changes on purpose.
# assess-prompt-v2 (v0.1.9) INTENTIONAL CHANGE: bumped for the rewritten body (see above).
# assess-prompt-v3 (v0.1.9) INTENTIONAL CHANGE: bumped again for the three rules (see above).
# assess-prompt-v3-r1 (v0.1.9) INTENTIONAL CHANGE: bumped for the two sentences (see above).
SHIPPED_INSTRUCTIONS_DIGEST = "sha256:ec8deb8d9e09c8795a3af275bb0ceeddfabade8a0afa283b7945bfe950f81d6c"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _job(**overrides: object) -> AssessJob:
    values: dict[str, object] = {
        "title": _TITLE,
        "company": _COMPANY,
        "location": _LOCATION,
        "posting_text": _POSTING_TEXT.decode("utf-8"),
    }
    values.update(overrides)
    return AssessJob(**values)  # type: ignore[arg-type]


def _ctx(*, visa: bool = False, resume: bytes = _RESUME) -> AssessContext:
    return AssessContext(resume_text=resume.decode("utf-8"), visa_sponsorship_required=visa)


def _posting() -> SimpleNamespace:
    return SimpleNamespace(to_json=lambda: {"title": _TITLE, "company": _COMPANY, "location": _LOCATION})


def _valid_output() -> str:
    return json.dumps({
        "matrix": [{"requirement": "5+ years Python", "resume_evidence": ["Built Python services for 6 years"], "status": "met"}],
        "suggestions": ["Call out the backend ownership."],
        "questions": [],
        "sponsorship": "not_offered",
    })


def _parse(normalized: dict[str, object]) -> object:
    posting = {
        "normalized_url": "https://boards.greenhouse.io/acme/jobs/101",
        "url": "https://boards.greenhouse.io/acme/jobs/101",
        "content_sha256": "sha256:" + "a" * 64,
        "role_match": True,
    }
    return parse_assessment_proposal({**normalized, "posting": posting, "proposal_revision_ref": None})


class _ScriptedPort:
    def __init__(self, outputs: list[object]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []

    def invoke(self, request: object) -> InvocationResult:
        self.prompts.append(request.prompt)
        item = self._outputs.pop(0)
        if isinstance(item, BaseException):
            raise item
        return InvocationResult(
            status="success", output_text=item, resolved_model="fixture",
            raw_usage={}, normalized_usage=NormalizedUsage(1, 1, 2), cost_status="unavailable",
        )


class _ScriptedBinding:
    def __init__(self, outputs: list[object]) -> None:
        self.port = _ScriptedPort(outputs)
        self.roles: list[str] = []

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        self.roles.append(role)
        return SimpleNamespace(prompt=prompt, role=role)


class _CodedError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- prompt: byte-identical to the pre-P1 hand-built string -----------------

def test_render_is_byte_identical_to_the_pre_p1_prompt() -> None:
    assert _sha256(GOLDEN_PROMPT) == GOLDEN_SHA256, "golden transcription drifted from the capture"
    assert render_assess_prompt(_job(), _ctx()) == GOLDEN_PROMPT


def test_render_with_validation_error_is_byte_identical_to_the_pre_p1_retry_prompt() -> None:
    assert _sha256(GOLDEN_RETRY_PROMPT) == GOLDEN_RETRY_SHA256, "golden transcription drifted from the capture"
    assert render_assess_prompt(_job(), _ctx(visa=True), _VALIDATION_ERROR) == GOLDEN_RETRY_PROMPT


def test_assess_prompt_wrapper_renders_the_same_bytes() -> None:
    assert proposal_execution._assess_prompt(_posting(), _RESUME, _POSTING_TEXT, False) == GOLDEN_PROMPT
    assert (
        proposal_execution._assess_prompt(_posting(), _RESUME, _POSTING_TEXT, True, _VALIDATION_ERROR)
        == GOLDEN_RETRY_PROMPT
    )


def test_prompt_bounds_match_the_pre_p1_constants() -> None:
    big = b"x" * 13_000
    prompt = proposal_execution._assess_prompt(_posting(), big, big, False, "e" * 400)
    assert len(prompt) == GOLDEN_BOUNDED_LEN
    assert _sha256(prompt) == GOLDEN_BOUNDED_SHA256
    assert (assessment_core._MAX_PROMPT_POSTING_TEXT, assessment_core._MAX_PROMPT_RESUME_TEXT, assessment_core._MAX_PROMPT_VALIDATION_ERROR) == (12_000, 12_000, 300)


def test_empty_location_renders_unspecified_and_empty_error_omits_the_retry_paragraph() -> None:
    prompt = render_assess_prompt(_job(location=""), _ctx(), "")
    assert "LOCATION: unspecified" in prompt
    assert "A previous attempt" not in prompt
    assert proposal_execution._assess_prompt(
        SimpleNamespace(to_json=lambda: {"title": _TITLE, "company": _COMPANY, "location": None}),
        _RESUME, _POSTING_TEXT, False,
    ) == prompt


def test_substituted_text_is_never_rescanned_for_placeholders() -> None:
    prompt = render_assess_prompt(_job(posting_text="literal {{resume_text}} in a posting"), _ctx())
    assert "literal {{resume_text}} in a posting" in prompt
    assert prompt.count(_RESUME.decode("utf-8")) == 1


# --- P3: prior answers render into the prompt --------------------------------

def test_prior_answers_render_into_the_prompt_and_are_omitted_when_empty() -> None:
    empty_prompt = render_assess_prompt(_job(), _ctx())
    assert "PRIOR ANSWERS (from earlier assessments" not in empty_prompt

    ctx = AssessContext(
        resume_text=_RESUME.decode("utf-8"),
        visa_sponsorship_required=False,
        prior_answers=(PriorAnswer(question_id="cloud:gcp", prompt="Have you used GCP?", answer="Yes, two years."),),
    )
    prompt = render_assess_prompt(_job(), ctx)
    assert "PRIOR ANSWERS (from earlier assessments" in prompt
    assert "cloud:gcp: Yes, two years." in prompt
    # The one rule (assess.md, not a code rule) telling the model never to re-ask an answered id.
    assert "8. A question whose id has a prior answer (PRIOR ANSWERS) is resolved by that answer, never re-asked" in prompt


def test_an_answered_question_id_is_never_re_asked_in_the_fixture_reply() -> None:
    """The fixture model, told a fact is already answered, does not ask about it again."""

    ctx = AssessContext(
        resume_text=_RESUME.decode("utf-8"),
        visa_sponsorship_required=False,
        prior_answers=(PriorAnswer(question_id="cloud:gcp", prompt="Have you used GCP?", answer="Yes, two years on GCP."),),
    )
    output = json.dumps({
        "verdict": "matched_above_threshold",
        "matrix": [{"requirement": "GCP", "class": "askable", "resume_evidence": ["Yes, two years on GCP."], "status": "met"}],
        "suggestions": [],
        "questions": [],
        "sponsorship": "not_offered",
    })
    binding = _ScriptedBinding([output])
    outcome = assess_once(binding, _job(), ctx, parse=_parse)
    assert outcome.ok
    assert "cloud:gcp: Yes, two years on GCP." in binding.port.prompts[0]
    assert outcome.parsed.questions == ()


# --- assess-prompt-v3: the three rules are in the shipped prompt --------------------

def test_prompt_carries_the_v3_guidance() -> None:
    """Hermetic check that the three assess-prompt-v3 rules render (no model call).

    (1) rule D: a remote/residency/office country outside the eligible countries is a
    HARD unmet row with a not_a_match_reason naming the country, never a location ask;
    (2) explicit disclaimers are judgement guidance, partial evidence stays an ask;
    (3) enrolment is ASKABLE and asked once as ``education:enrolled`` (a fixed point of
    ``normalize_question_id``), so the eval's flipped intern row can be asked, not failed.
    """

    prompt = render_assess_prompt(_job(), _ctx())
    assert 'to a country that is NOT one of the eligible countries (e.g. "Remote - Poland" against eligible countries "US, GB") is one HARD row with status "unmet"' in prompt
    assert "with not_a_match_reason naming that country: never ask a location question about it" in prompt
    assert "Ask a location question in only one other case" in prompt and "(a) the posting's country matches none" not in prompt
    assert "is a judgement call, not a keyword match: weigh how explicit the statement is and how central the requirement is" in prompt
    assert '"writes little product code" or "minimal application-level coding" against a required language does not rule the language out' in prompt
    assert 'A degree, enrolment or student-status requirement ("currently pursuing a degree", "enrolled student") is ASKABLE and is never "unmet" on the resume alone: a resume that states current enrolment meets it' in prompt
    assert 'you ask once, with the question_id "education:enrolled"' in prompt
    assert "any degree, enrolment or student-status requirement (see rule 1)" in prompt
    from gigai.scout.question_ids import normalize_question_id

    assert normalize_question_id("education:enrolled") == "education:enrolled" == normalize_question_id("education:enrolled_degree")


# --- assess-prompt-v3-r1: not_a_match keeps no questions; the two sentences render ----
#
# Operator decision (2026-09-25, msg_e4de88fcfa29): every remaining hard false
# ask in the v3 live eval sat on a row whose verdict was already not_a_match.
# The prompt now says so (rule 2) and closes the disclaimer-sibling gap (rule
# 1); the CODE strips whatever questions still arrive on a not_a_match answer
# at the one place every caller passes through (``assess_once`` ->
# ``_normalize_and_strip``), never refusing the answer, and records the count
# on the attempt for the eval harness. The three product callers (the graph's
# assess node, quick assess, the eval harness) all go through ``assess_once``.

def test_prompt_carries_the_v3_r1_sentences() -> None:
    prompt = render_assess_prompt(_job(), _ctx())
    disclaimer = (
        'A disclaimer that covers an area ("has not worked on X") answers every row about that area, '
        "HARD or ASKABLE: mark each of them unmet and ask nothing about it."
    )
    no_questions = 'When the verdict is "not_a_match", ask no questions.'
    assert disclaimer in prompt and no_questions in prompt
    rule_1, rest = prompt.split("\n2. Questions:", 1)
    rule_2 = rest.split("\n3. Seniority", 1)[0]
    assert disclaimer in rule_1 and no_questions in rule_2  # each sentence sits in its own rule
    assert prompt.count(disclaimer) == 1 and prompt.count(no_questions) == 1


def _not_a_match_with_questions(rows: int = 3) -> str:
    matrix = [
        {"requirement": "10+ years", "class": "hard", "status": "unmet", "resume_evidence": ["6 years"]},
        {"requirement": "PhD", "class": "askable", "status": "unclear", "resume_evidence": []},
        {"requirement": "Lives in a listed US state", "class": "hard", "status": "unclear", "resume_evidence": []},
    ]
    matrix += [{"requirement": f"extra {i}", "class": "nice_to_have", "status": "met", "resume_evidence": []} for i in range(rows - 3)]
    return json.dumps({
        "verdict": "not_a_match",
        "matrix": matrix,
        "suggestions": [],
        "questions": [
            {"question_id": "education:phd", "question": "Do you hold a PhD?", "requirement": "PhD"},
            {"question_id": "location:us_region", "question": "Which US state do you live in?", "requirement": "Lives in a listed US state"},
        ],
        "not_a_match_reason": "The resume states 6 years against a 10+ year requirement.",
    })


def _pending_with_one_question() -> str:
    return json.dumps({
        "verdict": "pending_user_answers",
        "matrix": [
            {"requirement": "5+ years Python", "class": "hard", "status": "met", "resume_evidence": ["6 years"]},
            {"requirement": "GCP", "class": "askable", "status": "unclear", "resume_evidence": []},
        ],
        "suggestions": [],
        "questions": [{"question_id": "cloud:gcp", "question": "Have you run workloads on GCP?", "requirement": "GCP"}],
        "not_a_match_reason": None,
    })


def test_not_a_match_questions_are_stripped_before_validation_and_counted() -> None:
    binding = _ScriptedBinding([_not_a_match_with_questions()])
    outcome = assess_once(binding, _job(), _ctx(), parse=_parse)
    # Accepted exactly as it came: one call, no validation error, no retry spent on the questions.
    assert outcome.ok and outcome.attempts == 1 and outcome.validation_error is None
    assert len(binding.port.prompts) == 1
    assert outcome.parsed.verdict.value == "not_a_match"
    assert outcome.parsed.structured_questions == () and outcome.parsed.questions == ()
    assert outcome.parsed.not_a_match_reason == "The resume states 6 years against a 10+ year requirement."
    # The matrix is untouched: the unclear rows stay unclear, only the questions go.
    assert [row.status.value for row in outcome.parsed.matrix] == ["unmet", "unclear", "unclear"]
    assert outcome.dropped_questions == 2
    assert outcome.dropped_question_ids == ("education:phd", "location:us_region")
    # The stored shape (what a run's outputs/ and the quick-assess store persist) carries none either.
    stored = outcome.parsed.to_json()
    assert stored["questions"] == [] and not stored.get("structured_questions")
    assert "education:phd" not in json.dumps(stored)


def test_pending_and_matched_answers_are_untouched_by_the_strip() -> None:
    outcome = assess_once(_ScriptedBinding([_pending_with_one_question()]), _job(), _ctx(), parse=_parse)
    assert outcome.ok and outcome.parsed.verdict.value == "pending_user_answers"
    assert [item.question_id for item in outcome.parsed.structured_questions] == ["cloud:gcp"]
    assert outcome.parsed.questions == ("Have you run workloads on GCP?",)
    assert outcome.dropped_questions == 0 and outcome.dropped_question_ids == ()
    outcome = assess_once(_ScriptedBinding([_valid_output()]), _job(), _ctx(), parse=_parse)  # no verdict at all
    assert outcome.ok and outcome.dropped_questions == 0 and outcome.dropped_question_ids == ()


def test_dropped_count_describes_the_successful_attempt_only() -> None:
    # First answer: not_a_match with two questions but 13 rows -> rejected on OUTPUT BOUNDS (not on the
    # questions); the retry answers pending with one question. The attempt reports the retry's count.
    binding = _ScriptedBinding([_not_a_match_with_questions(rows=13), _pending_with_one_question()])
    outcome = assess_once(binding, _job(), _ctx(), parse=_parse)
    assert outcome.ok and outcome.attempts == 2
    assert "matrix has 13 rows; at most 12 allowed" in outcome.validation_error
    assert outcome.parsed.verdict.value == "pending_user_answers"
    assert outcome.dropped_questions == 0 and outcome.dropped_question_ids == ()
    # And a strip on a failed second attempt is never reported as if it had happened.
    binding = _ScriptedBinding(["not json", _not_a_match_with_questions(rows=13)])
    outcome = assess_once(binding, _job(), _ctx(), parse=_parse)
    assert not outcome.ok and outcome.dropped_questions == 0 and outcome.dropped_question_ids == ()


def test_normalizer_strips_the_plain_question_list_too_and_only_for_not_a_match() -> None:
    raw = {
        "verdict": "Not_A_Match",  # the synonym map canonicalizes the verdict before the strip looks at it
        "matrix": [{"requirement": "10+ years", "class": "hard", "status": "unmet", "resume_evidence": ["6 years"]}],
        "questions": ["a bare pre-P2 question?", {"question_id": "Cloud:GCP", "question": "GCP?", "requirement": "GCP"}],
        "not_a_match_reason": "6 < 10",
    }
    normalized = assessment_core._normalize_assessment_payload(dict(raw))
    assert normalized["verdict"] == "not_a_match"
    assert normalized["questions"] == [] and "structured_questions" not in normalized
    assert normalized["not_a_match_reason"] == "6 < 10" and normalized["matrix"][0]["status"] == "unmet"
    payload, dropped_ids, dropped_count = assessment_core._normalize_and_strip(dict(raw))
    assert payload == normalized
    assert dropped_ids == ("cloud:gcp",) and dropped_count == 2  # the bare string has no id but is counted
    for verdict in ("pending_user_answers", "matched_above_threshold"):
        kept = assessment_core._normalize_assessment_payload({**raw, "verdict": verdict})
        assert kept["questions"] == ["a bare pre-P2 question?", "GCP?"]
        assert [item["question_id"] for item in kept["structured_questions"]] == ["cloud:gcp"]
        assert assessment_core._normalize_and_strip({**raw, "verdict": verdict})[1:] == ((), 0)
    absent = assessment_core._normalize_and_strip({key: value for key, value in raw.items() if key != "verdict"})
    assert absent[0]["questions"] == ["a bare pre-P2 question?", "GCP?"] and absent[1:] == ((), 0)
    no_questions = assessment_core._normalize_and_strip({**raw, "questions": []})
    assert no_questions[0]["questions"] == [] and no_questions[1:] == ((), 0)


def test_the_strip_is_the_single_place_every_assess_caller_passes_through() -> None:
    """The graph's assess node, quick assess and the eval harness all call ``assess_once``; the
    validator in ``proposals`` runs AFTER the strip and never sees the dropped questions."""

    import inspect

    from gigai.scout import quick_assess

    assert "assess_once(" in inspect.getsource(proposal_execution._assess_node_body)
    assert "assess_once(" in inspect.getsource(quick_assess.run_quick_assessment)
    assert "_normalize_and_strip(" in inspect.getsource(assessment_core.assess_once)


# --- assess-prompt-v2: the candidate's own location and rule 4's region paths ----------
#
# Operator decision (2026-09-25): a posting that restricts a remote role to
# named states/provinces is decided from the candidate's OWN location
# ({{candidate_location}}, find-jobs.json's ``location``), not always-met and
# not always-asked. The model does the deciding; these hermetic cases pin
# what the shipped path guarantees around it: the location reaches the
# prompt (or renders "unknown"), rule 4 names the stable id, a region
# verdict in each direction validates, the asked id survives normalization
# unchanged, and a prior answer for that id is rendered so it is never
# re-asked.

_REGION_POSTING = (
    "Remote Canada. This remote role is open only to candidates residing in Alberta, "
    "British Columbia, Ontario or Saskatchewan.\nWe need 5+ years of Python."
)


def _region_job() -> AssessJob:
    return _job(location="Remote Canada", posting_text=_REGION_POSTING)


def _region_ctx(location: str, prior: tuple[PriorAnswer, ...] = ()) -> AssessContext:
    return AssessContext(
        resume_text=_RESUME.decode("utf-8"), visa_sponsorship_required=False,
        countries=("CA",), location=location, prior_answers=prior,
    )


def _constraints_line(prompt: str) -> str:
    lines = [line for line in prompt.splitlines() if line.startswith("CANDIDATE CONSTRAINTS")]
    assert len(lines) == 1, prompt
    return lines[0]


def test_candidate_location_renders_on_the_constraints_line_and_rule_4_names_the_region_id() -> None:
    prompt = render_assess_prompt(_region_job(), _region_ctx("Toronto, ON, Canada"))
    assert "the candidate's own location (city, state/province, country as they wrote it; " in _constraints_line(prompt)
    assert "applied by rule 4): Toronto, ON, Canada; target titles" in _constraints_line(prompt)
    assert 'with the question_id "location:<country>_region"' in prompt
    assert '"location:ca_region", "location:us_region"' in prompt
    # Unknown: the placeholder never leaks and the word the rule keys on is rendered.
    unknown = render_assess_prompt(_region_job(), _region_ctx(""))
    assert "{{candidate_location}}" not in unknown
    assert "applied by rule 4): unknown; target titles" in _constraints_line(unknown)
    assert render_assess_prompt(_region_job(), _region_ctx("   ")) == unknown


def test_region_known_and_listed_validates_as_a_match_with_no_question() -> None:
    output = json.dumps({
        "verdict": "matched_above_threshold",
        "matrix": [
            {"requirement": "Residing in Alberta, British Columbia, Ontario or Saskatchewan", "class": "hard",
             "status": "met", "resume_evidence": ["candidate location: Toronto, ON, Canada"]},
            {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["6 years"]},
        ],
        "questions": [], "suggestions": [], "not_a_match_reason": None,
    })
    binding = _ScriptedBinding([output])
    outcome = assess_once(binding, _region_job(), _region_ctx("Toronto, ON, Canada"), parse=_parse)
    assert outcome.ok and outcome.attempts == 1
    assert "Toronto, ON, Canada" in binding.port.prompts[0]
    assert outcome.parsed.verdict.value == "matched_above_threshold"
    assert outcome.parsed.structured_questions == ()


def test_region_known_but_not_listed_validates_as_not_a_match_on_the_hard_row() -> None:
    output = json.dumps({
        "verdict": "not_a_match",
        "matrix": [
            {"requirement": "Residing in Alberta, British Columbia, Ontario or Saskatchewan", "class": "hard",
             "status": "unmet", "resume_evidence": ["candidate location: Montreal, QC, Canada"]},
            {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["6 years"]},
        ],
        "questions": [], "suggestions": [],
        "not_a_match_reason": "The candidate lives in Quebec, which is not one of the listed provinces.",
    })
    binding = _ScriptedBinding([output])
    outcome = assess_once(binding, _region_job(), _region_ctx("Montreal, QC, Canada"), parse=_parse)
    assert outcome.ok and outcome.attempts == 1
    assert "Montreal, QC, Canada" in binding.port.prompts[0]
    assert outcome.parsed.verdict.value == "not_a_match"
    assert outcome.parsed.matrix[0].status.value == "unmet"


def test_region_unknown_asks_once_with_the_stable_region_id() -> None:
    output = json.dumps({
        "verdict": "pending_user_answers",
        "matrix": [
            {"requirement": "Residing in Alberta, British Columbia, Ontario or Saskatchewan", "class": "hard",
             "status": "unclear", "resume_evidence": []},
            {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["6 years"]},
        ],
        "questions": [{
            "question_id": "location:ca_region",
            "question": "Which Canadian province do you live in?",
            "requirement": "Residing in Alberta, British Columbia, Ontario or Saskatchewan",
        }],
        "suggestions": [], "not_a_match_reason": None,
    })
    binding = _ScriptedBinding([output])
    outcome = assess_once(binding, _region_job(), _region_ctx(""), parse=_parse)
    assert outcome.ok
    assert "applied by rule 4): unknown;" in binding.port.prompts[0]
    assert outcome.parsed.verdict.value == "pending_user_answers"
    # The id the prompt names is a fixed point of the boundary normalizer
    # (question_ids.normalize_question_id), so P3's answer join finds it.
    assert [q.question_id for q in outcome.parsed.structured_questions] == ["location:ca_region"]


def test_region_unknown_with_a_prior_answer_is_resolved_not_re_asked() -> None:
    prior = (PriorAnswer(question_id="location:ca_region", prompt="Which Canadian province do you live in?", answer="Ontario"),)
    output = json.dumps({
        "verdict": "matched_above_threshold",
        "matrix": [
            {"requirement": "Residing in Alberta, British Columbia, Ontario or Saskatchewan", "class": "hard",
             "status": "met", "resume_evidence": ["prior answer location:ca_region: Ontario"]},
            {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["6 years"]},
        ],
        "questions": [], "suggestions": [], "not_a_match_reason": None,
    })
    binding = _ScriptedBinding([output])
    outcome = assess_once(binding, _region_job(), _region_ctx("", prior), parse=_parse)
    assert outcome.ok
    prompt = binding.port.prompts[0]
    assert "applied by rule 4): unknown;" in prompt
    assert "PRIOR ANSWERS (from earlier assessments" in prompt and "- location:ca_region: Ontario" in prompt
    assert "8. A question whose id has a prior answer (PRIOR ANSWERS) is resolved by that answer, never re-asked" in prompt
    assert outcome.parsed.verdict.value == "matched_above_threshold"
    assert outcome.parsed.structured_questions == ()


# --- assess_once: retry, tolerant extraction, exception mapping --------------

def test_assess_once_parses_on_the_first_try() -> None:
    binding = _ScriptedBinding([_valid_output()])
    outcome = assess_once(binding, _job(), _ctx(), parse=_parse)
    assert outcome.ok and outcome.not_assessed_reason is None
    assert outcome.attempts == 1 and outcome.validation_error is None
    assert outcome.usage == NormalizedUsage(1, 1, 2)
    assert outcome.parsed.matrix[0].requirement == "5+ years Python"
    assert binding.roles == ["reviewer"]
    assert binding.port.prompts == [GOLDEN_PROMPT]


def test_assess_once_retries_once_and_feeds_the_validation_error_back() -> None:
    binding = _ScriptedBinding(['{"matrix": "not-a-list", "suggestions": []}', _valid_output()])
    outcome = assess_once(binding, _job(), _ctx(), parse=_parse)
    assert outcome.ok and outcome.attempts == 2
    assert outcome.validation_error  # the first attempt's error, surfaced
    first, second = binding.port.prompts
    assert first == GOLDEN_PROMPT
    assert second.startswith(GOLDEN_PROMPT + "\n\nA previous attempt at this same prompt was rejected by the validator: ")
    assert outcome.validation_error[:300] in second
    assert second.endswith(". Return corrected JSON only, matching the schema exactly.")


def test_assess_once_gives_up_after_the_second_invalid_answer() -> None:
    binding = _ScriptedBinding(["no json here at all", "still not json"])
    outcome = assess_once(binding, _job(), _ctx(), parse=_parse)
    assert not outcome.ok and outcome.parsed is None and outcome.usage is None
    assert outcome.not_assessed_reason is NotAssessedReason.MODEL_OUTPUT_INVALID
    assert outcome.attempts == 2
    assert len(binding.port.prompts) == 2


def test_assess_once_accepts_fenced_json() -> None:
    binding = _ScriptedBinding(["Here you go:\n```json\n" + _valid_output() + "\n```\nHope that helps."])
    outcome = assess_once(binding, _job(), _ctx(), parse=_parse)
    assert outcome.ok and outcome.attempts == 1
    assert outcome.parsed.sponsorship.value == "not_offered"


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (ModelInvocationError("refused"), NotAssessedReason.MODEL_UNAVAILABLE),
        (OSError("connection reset"), NotAssessedReason.MODEL_UNAVAILABLE),
        (TimeoutError("slow"), NotAssessedReason.MODEL_UNAVAILABLE),
        (_CodedError("model_unavailable"), NotAssessedReason.MODEL_UNAVAILABLE),
        (_CodedError("network_denied"), NotAssessedReason.MODEL_UNAVAILABLE),
        (_CodedError("model_denied"), NotAssessedReason.MODEL_DENIED),
    ],
)
def test_assess_once_maps_transport_failures(raised: BaseException, expected: NotAssessedReason) -> None:
    binding = _ScriptedBinding([raised])
    outcome = assess_once(binding, _job(), _ctx(), parse=_parse)
    assert not outcome.ok and outcome.not_assessed_reason is expected
    assert outcome.attempts == 0
    assert len(binding.port.prompts) == 1  # no retry after a transport failure


@pytest.mark.parametrize("code", ["network_denied", "model_denied", "credential_denied"])
def test_assess_once_maps_a_coded_invocation_error_to_denied(code: str) -> None:
    error = ModelInvocationError("policy")
    error.code = code  # type: ignore[attr-defined]
    outcome = assess_once(_ScriptedBinding([error]), _job(), _ctx(), parse=_parse)
    assert not outcome.ok and outcome.not_assessed_reason is NotAssessedReason.MODEL_DENIED


def test_assess_once_reraises_an_unknown_exception() -> None:
    with pytest.raises(_CodedError):
        assess_once(_ScriptedBinding([_CodedError("something_else")]), _job(), _ctx(), parse=_parse)
    with pytest.raises(RuntimeError):
        assess_once(_ScriptedBinding([RuntimeError("boom")]), _job(), _ctx(), parse=_parse)


def test_assess_once_uses_the_parser_it_is_given() -> None:
    seen: list[dict[str, object]] = []

    def parse(normalized: dict[str, object]) -> object:
        seen.append(normalized)
        return "parsed-by-caller"

    outcome = assess_once(_ScriptedBinding(['{"matrix": [], "suggestions": "one", "extra": 1}']), _job(), _ctx(), parse=parse)
    assert outcome.ok and outcome.parsed == "parsed-by-caller"
    assert seen == [{"matrix": [], "suggestions": ["one"], "questions": []}]  # normalized, unknown key dropped


def test_assess_attempt_is_internally_consistent() -> None:
    with pytest.raises(ValueError):
        AssessAttempt(True, None, None, None, 1, None)
    with pytest.raises(ValueError):
        AssessAttempt(False, None, None, None, 0, None)


# --- the moved names stay importable by their old path -----------------------

def test_moved_helpers_are_re_exported_from_proposal_execution() -> None:
    for name in (
        "_extract_json_object", "_normalize_assessment_payload", "_normalize_status",
        "_normalize_sponsorship", "_normalize_string_list", "_STATUS_SYNONYMS", "_SPONSORSHIP_SYNONYMS",
        "_MAX_PROMPT_POSTING_TEXT", "_MAX_PROMPT_RESUME_TEXT", "_MAX_PROMPT_VALIDATION_ERROR",
    ):
        assert getattr(proposal_execution, name) is getattr(assessment_core, name), name
    assert "resolve_model_adapter" not in vars(assessment_core)  # C1: the seam stays on proposal_execution


# --- the instruction file ships and its digest is stable ---------------------

def test_instructions_load_from_the_package_and_the_digest_is_stable() -> None:
    shipped = resources.files("gigai").joinpath("scout/data/instructions/assess.md").read_bytes()
    assert INSTRUCTIONS_DIGEST == digest_imported_bytes(shipped) == SHIPPED_INSTRUCTIONS_DIGEST
    text = load_assess_instructions()
    assert text == shipped.decode("utf-8")[:-1] and shipped.endswith(b"\n")
    for placeholder in (
        "title", "company", "location", "visa_required", "posting_text", "resume_text", "countries",
        "candidate_location", "titles", "prior_answers", "validation_error",
    ):
        assert "{{" + placeholder + "}}" in text, placeholder
    assert load_assess_instructions() == text


def test_installed_interpreter_reads_the_shipped_instructions(installed_gigai: InstalledGigAI) -> None:
    """Under ``make test-wheel`` this runs the wheel venv's Python; in the source lane, the dev venv's."""

    python = installed_gigai.command.executable.parent / "python"
    if not python.exists():
        pytest.skip("no interpreter next to the gigai console script")
    probe = (
        "from importlib import resources\n"
        "from gigai.canonical import digest_imported_bytes\n"
        "from gigai.scout.assessment_core import INSTRUCTIONS_DIGEST, load_assess_instructions\n"
        "raw = resources.files('gigai').joinpath('scout/data/instructions/assess.md').read_bytes()\n"
        "assert digest_imported_bytes(raw) == INSTRUCTIONS_DIGEST\n"
        "assert '{{validation_error}}' in load_assess_instructions()\n"
        "print(INSTRUCTIONS_DIGEST)\n"
    )
    result = subprocess.run(
        [os.fspath(python), "-c", probe], capture_output=True, text=True, check=False, shell=False,
        cwd=Path(python).parent,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == SHIPPED_INSTRUCTIONS_DIGEST


@pytest.fixture
def installed_gigai() -> InstalledGigAI:
    return InstalledGigAI.current()
