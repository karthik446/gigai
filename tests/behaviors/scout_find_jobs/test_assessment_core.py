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
# P3 (v0.1.9) INTENTIONAL CHANGE: added rule 6 ("A question whose id has a
# prior answer is resolved by that answer, never re-asked.") and a
# {{prior_answers}} paragraph (dropped from the rendered prompt when
# ``AssessContext.prior_answers`` is empty, exactly like
# {{validation_error}} -- so this golden, captured with the P1-era ``_ctx()``
# helper that still passes none, is unchanged except for the new rule 6
# line). Goldens below were re-captured for rule 6.
GOLDEN_PROMPT = (
    "You are assessing one real job posting against one candidate's resume for GigAI Scout. "
    "Return a workflow-state verdict, not a grader score: the verdict decides what GigAI does "
    "next, so pick the state that describes the right NEXT ACTION, not just how good the fit "
    "looks.\n\n"
    "STATES (pick exactly one):\n"
    '- "matched_above_threshold": every hard requirement is met or the posting states none; a '
    "reasonable person would apply today without more info.\n"
    '- "pending_user_answers": the posting is otherwise plausible, but at least one '
    "requirement's status can only be resolved by asking the candidate something the resume "
    "does not already confirm OR rule out. This includes named tools/cloud "
    "platforms/technologies AND hard requirements like years of experience or seniority level, "
    "whenever the resume is merely silent — not stated, not contradicted. Never re-ask "
    "something the resume already states one way or the other.\n"
    '- "not_a_match": at least one requirement is unambiguously unmet by clear, explicit resume '
    'evidence (a stated gap, e.g. resume says "3 years" and posting requires "8+"), OR the '
    "posting is a clear domain/seniority mismatch that the resume's own words directly "
    'contradict (e.g. resume title/level literally says "Intern" against a posting for '
    '"Staff"). Do NOT use not_a_match for silence — silence is always a question, never a '
    "verdict, regardless of how important the requirement is.\n\n"
    "REQUIREMENT CLASSES (classify each requirement you extract from the posting before you "
    "can pick a verdict):\n"
    "- HARD: seniority/level, required years of experience, explicit clearance, an explicitly "
    "excluded domain (posting or candidate side), and — ONLY WHEN THE POSTING TEXT ITSELF "
    "states a hard constraint — location/remote policy or visa sponsorship.\n"
    "- ASKABLE: a named tool, cloud platform, or specific technology the resume neither "
    "confirms nor rules out (e.g. posting wants GCP, resume only shows AWS — this is a "
    "QUESTION, not a gap: clouds/tools are learnable, and the candidate may have unlisted "
    "experience); ALSO any HARD requirement above whose status the resume simply does not "
    "address (see rule 1).\n"
    '- NICE_TO_HAVE: anything the posting phrases as "bonus", "plus", or "preferred but not '
    'required", or a soft culture/stack-neighbor fit signal.\n\n'
    "RULES:\n"
    "1. Test every HARD requirement against the resume TEXT, not against your overall "
    "impression:\n"
    "   - Resume explicitly satisfies it -> met.\n"
    "   - Resume explicitly and directly contradicts it (its own words state a lower "
    "level/fewer years/wrong domain) -> unmet -> not_a_match.\n"
    "   - Resume is simply silent (does not mention the topic at all) -> this is NOT unmet and "
    "NOT met. Reclassify this specific requirement as ASKABLE and write a question for it. "
    "Silence is never grounds for not_a_match, no matter how central the requirement looks.\n"
    "2. For every ASKABLE requirement (named tool/platform/tech, or a HARD requirement "
    "reclassified under rule 1), write ONE specific question tied to that exact requirement, "
    'with a stable question_id slug in the form "<category>:<value>" (lowercase, e.g. '
    '"cloud:gcp", "years:python", "clearance:secret", "seniority:staff") that names the '
    "underlying fact, not the posting — the same real-world fact asked the same way across "
    "different postings should reuse the same question_id. Never ask a question the resume "
    "already answers — quote the resume text you checked in resume_evidence (empty list only "
    "if truly silent) before writing each question.\n"
    '3. verdict = "matched_above_threshold" only if there are zero not_a_match findings AND '
    "zero unresolved askable questions.\n"
    '4. verdict = "not_a_match" if any requirement is unmet per rule 1\'s explicit-contradiction '
    "test.\n"
    '5. verdict = "pending_user_answers" only when there is no not_a_match finding but at least '
    "one askable question remains.\n"
    "6. A question whose id has a prior answer is resolved by that answer, never re-asked.\n\n"
    "Return JSON only (no prose, no markdown fences):\n"
    '{"verdict": "matched_above_threshold|pending_user_answers|not_a_match",\n'
    ' "matrix": [{"requirement": "<from the posting>", "class": "hard|askable|nice_to_have",\n'
    ' "status": "met|unmet|unclear", "resume_evidence": ["<quote or paraphrase, or empty>"]}],\n'
    ' "questions": [{"question_id": "<category>:<value>", "question": "<specific>", '
    '"requirement": "<matches a matrix requirement>"}],\n'
    ' "not_a_match_reason": "<one sentence, or null if verdict is not not_a_match>"}\n\n'
    "ROLE: Senior Backend Engineer\nCOMPANY: Acme Corp\nLOCATION: Denver, CO\n"
    "POSTING TEXT:\n"
    "We need 5+ years of Python. Remote OK.\nNo visa sponsorship available for this role.\n\n\n"
    "RESUME:\n"
    "Karthik built Python services for 6 years.\nOperated Kubernetes clusters in production.\n\n\n"
    "CANDIDATE CONSTRAINTS: visa sponsorship required = no; the candidate is eligible to work "
    "from these countries (this is a fact about the candidate, exactly like a resume statement "
    "-- treat a posting's location/remote-region requirement as MET whenever the posting's own "
    "location matches one of these countries, and only ask a location question when the "
    "posting's location does not match any of them and the resume itself gives no other "
    "answer): any; target titles the candidate is looking for = unspecified."
)

# Same fixed inputs, visa required = yes, retry with the validation error fed back.
GOLDEN_RETRY_PROMPT = (
    GOLDEN_PROMPT.replace("visa sponsorship required = no;", "visa sponsorship required = yes;")
    + "\n\nYour previous answer did not match the required JSON shape: "
    + _VALIDATION_ERROR
    + ". Return corrected JSON only, matching the schema exactly."
)

# sha256 of the P3 prompts, recorded by the capture script above (the
# strings above are the source of truth; the digests guard the
# transcription).
GOLDEN_SHA256 = "ae583af5e4103f290a415859d08a34c37e5653697cf6189321d8530a291280a2"
GOLDEN_RETRY_SHA256 = "8e308590841a04a6a121caf0cc5d741e63e1833e18b787a739813ae8d88afcdb"
# 13,000-byte posting text and resume plus a 400-char validation error, P3:
# the three ``_MAX_PROMPT_*`` bounds (12_000 / 12_000 / 300) produce this exact prompt.
GOLDEN_BOUNDED_SHA256 = "3fd8f0b07fcf340e391695549b707c0cb2a78546a8a36ddd158473ab055ab064"
GOLDEN_BOUNDED_LEN = 29_391

# Digest of the shipped ``assess.md`` bytes; bump ONLY when the template changes on purpose.
# P3 (v0.1.9) INTENTIONAL CHANGE: bumped for rule 6 + the {{prior_answers}} placeholder.
SHIPPED_INSTRUCTIONS_DIGEST = "sha256:0ed0f4410fda6fa6fa8d90bf38700d3487f51e3648a8840e7aee12c593f6a00d"


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
    assert "Your previous answer" not in prompt
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
    assert "PRIOR ANSWERS" not in empty_prompt

    ctx = AssessContext(
        resume_text=_RESUME.decode("utf-8"),
        visa_sponsorship_required=False,
        prior_answers=(PriorAnswer(question_id="cloud:gcp", prompt="Have you used GCP?", answer="Yes, two years."),),
    )
    prompt = render_assess_prompt(_job(), ctx)
    assert "PRIOR ANSWERS" in prompt
    assert "cloud:gcp: Yes, two years." in prompt
    # The one rule (assess.md, not a code rule) telling the model never to re-ask an answered id.
    assert "6. A question whose id has a prior answer is resolved by that answer, never re-asked." in prompt


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
    assert second.startswith(GOLDEN_PROMPT + "\n\nYour previous answer did not match the required JSON shape: ")
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
    for placeholder in ("title", "company", "location", "visa_required", "posting_text", "resume_text", "validation_error"):
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
