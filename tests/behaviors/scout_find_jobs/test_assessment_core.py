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

# Captured verbatim from the pre-P1 ``_assess_prompt`` (visa required = no, no retry).
GOLDEN_PROMPT = (
    "You are assessing one real job posting against one candidate's resume for GigAI Scout.\n\n"
    "Return JSON only (no prose, no markdown fences) matching exactly this shape:\n"
    '{"matrix": [{"requirement": "<one concrete requirement drawn from the posting>", '
    '"resume_evidence": ["<short quote or paraphrase from the resume>"], '
    '"status": "met|partial|gap"}], '
    '"suggestions": ["<short actionable suggestion>"], '
    '"questions": ["<short clarifying question, if any>"], '
    '"sponsorship": "offered|not_offered|unknown"}\n'
    "Example:\n"
    '{"matrix": [{"requirement": "5+ years backend Python", "resume_evidence": '
    '["Built and operated Python services for 6 years"], "status": "met"}, '
    '{"requirement": "Kubernetes production experience", "resume_evidence": [], "status": "gap"}], '
    '"suggestions": ["Call out the on-call rotation experience explicitly."], '
    '"questions": ["Is the Kubernetes requirement negotiable?"], "sponsorship": "unknown"}\n'
    "Derive 5 to 12 concrete requirements FROM THE POSTING TEXT below (skills, years of "
    "experience, clearance, location/remote terms, tooling) — do not invent generic "
    "requirements not stated or clearly implied by the posting.\n\n"
    "ROLE: Senior Backend Engineer\nCOMPANY: Acme Corp\nLOCATION: Denver, CO\n\n"
    "CANDIDATE CONSTRAINT: visa sponsorship required = no. "
    "Read the posting text for its own sponsorship stance and report it "
    'as "sponsorship": "offered", "not_offered", or "unknown".\n\n'
    "POSTING TEXT (may be truncated):\n"
    "We need 5+ years of Python. Remote OK.\nNo visa sponsorship available for this role.\n\n\n"
    "RESUME (may be truncated):\n"
    "Karthik built Python services for 6 years.\nOperated Kubernetes clusters in production.\n"
)

# Captured verbatim from the pre-P1 ``_assess_prompt`` (visa required = yes, retry with error).
GOLDEN_RETRY_PROMPT = (
    GOLDEN_PROMPT.replace("visa sponsorship required = no.", "visa sponsorship required = yes.")
    + "\n\nYour previous answer did not match the required JSON shape: "
    + _VALIDATION_ERROR
    + ". Return corrected JSON only, matching the schema exactly."
)

# sha256 of the pre-P1 prompts, recorded by the capture script (the strings
# above are the source of truth; the digests guard the transcription).
GOLDEN_SHA256 = "cac149fe1117234554864fc32e405a0820a44104b11af0be2733d089831cb8fa"
GOLDEN_RETRY_SHA256 = "83877b9445e9da0b53d3f3054d649bfa7cfef971ccca794e222fc789455c96b2"
# 13,000-byte posting text and resume plus a 400-char validation error, pre-P1:
# the three ``_MAX_PROMPT_*`` bounds (12_000 / 12_000 / 300) produce this exact prompt.
GOLDEN_BOUNDED_SHA256 = "825a00d5a138a16288975fb8d86abfe390dfd63e4d53c209d50a99f41540950a"
GOLDEN_BOUNDED_LEN = 25_856

# Digest of the shipped ``assess.md`` bytes; bump ONLY when the template changes on purpose.
SHIPPED_INSTRUCTIONS_DIGEST = "sha256:9c3ded47473b118269cd7908fad3599ea95da086906f5f969bfa0c8a9544506b"


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
