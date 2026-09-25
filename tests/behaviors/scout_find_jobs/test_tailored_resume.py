"""Q3 (v0.1.9): the tailored resume -- every line traces to the resume or an answer.

Pure-function cases (the numeric and posting-term guards BOTH WAYS, the
prompt renderer, the validator's line-naming messages, the copy-only rule,
the bounds, the markdown renderer, the fixture-marker contract) plus the
real-gig cases (``build_gig_with_resume`` + a scripted binding at the C1
seam): storage of JSON + ``.md``, answers cited, the stored matrix as
context, ``created_at`` kept on a re-run, the list filters, the ephemeral
path, and the planted-fabrication -> ``model_output_invalid`` path with
nothing stored.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gigai.adapters.port import InvocationResult, ModelInvocationError, NormalizedUsage
from gigai.config import Endpoint, GigAIConfig
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout import tailored_resume
from gigai.scout.experience_answers import record_answer
from gigai.scout.find_jobs import bindings
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.contracts import ModelTarget, NotAssessedReason
from gigai.scout.profile_records import selected_profile
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.tailored_resume import (
    MAX_HEADER_LINES,
    MAX_REFS_PER_LINE,
    MAX_SECTIONS,
    MAX_TEXT_CHARS,
    MAX_TOTAL_LINES,
    TAILOR_INSTRUCTIONS_DIGEST,
    TAILOR_PROMPT_HEADER,
    AnswerSource,
    MatrixRow,
    TailorContext,
    TailorError,
    TailorJob,
    TailorRequest,
    TailorResponse,
    TailorValidationError,
    guard_terms,
    list_tailored_resumes,
    load_tailor_instructions,
    matrix_terms,
    numeric_values,
    posting_terms,
    render_markdown,
    render_tailor_prompt,
    resume_lines,
    run_tailored_resume,
    tailor_once,
    unsupported_numbers,
    unsupported_posting_terms,
    validate_tailored_output,
)
from gigai.setup import build_config

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

# --- fixed inputs ------------------------------------------------------------------------

_RESUME_TEXT = (
    "# Jane Doe\n"
    "jane@example.test | Denver, CO\n"
    "\n"
    "## Experience\n"
    "Acme Corp — Senior Engineer (2019–2023)\n"
    "Built Python services for six years; cut p99 latency by 40%.\n"
    "Operated k8s clusters backed by PostgreSQL.\n"
    "\n"
    "## Skills\n"
    "Python, Kubernetes, PostgreSQL\n"
)
_LINES = resume_lines(_RESUME_TEXT)
_POSTING = (
    "Acme is hiring a Staff AI Engineer to own Python inference services. "
    "Requirements: 5+ years of Python in production; Kubernetes; Terraform; GCP experience is a plus. "
    "Remote within the United States."
)
_JOB = TailorJob(title="Staff AI Engineer", company="Acme", location="Remote", posting_text=_POSTING)
_GCP = AnswerSource(question_id="cloud:gcp", answer="Yes, two years on GCP.", revision_id="rev_1", prompt="Have you run workloads on GCP?")
_CTX = TailorContext(resume_lines=_LINES, answers={"cloud:gcp": _GCP})

_VALID: dict[str, object] = {
    "header": [{"copy": 1}, {"copy": 2}],
    "sections": [
        {
            "heading": "summary",
            "lines": [
                {
                    "text": "Engineer with six years of Python services; ran Kubernetes clusters with PostgreSQL.",
                    "refs": [{"kind": "resume", "line": 5}, {"kind": "resume", "line": 6}],
                }
            ],
        },
        {
            "heading": "experience",
            "entries": [
                {
                    "heading_ref": [{"copy": 4}],
                    "bullets": [{"text": "Cut p99 latency by 40% for Python services.", "refs": [{"kind": "resume", "line": 5}]}],
                }
            ],
        },
        {"heading": "skills", "lines": [{"copy": 8}]},
        {"heading": "other", "lines": [{"text": "Two years on GCP.", "refs": [{"kind": "answer", "question_id": "cloud:gcp"}]}]},
    ],
}


def _one_line(text: str, refs: list[dict[str, object]]) -> dict[str, object]:
    return {"header": [{"copy": 1}], "sections": [{"heading": "summary", "lines": [{"text": text, "refs": refs}]}]}


def _reject(payload: dict[str, object], ctx: TailorContext = _CTX, job: TailorJob = _JOB) -> str:
    with pytest.raises(TailorValidationError) as info:
        validate_tailored_output(payload, job, ctx)
    return str(info.value)


# --- the fixture-marker contract ----------------------------------------------------------


def test_prompt_header_is_the_fixture_marker_and_the_instructions_start_with_it() -> None:
    assert TAILOR_PROMPT_HEADER == bindings.TEST_MODEL_TAILOR_MARKER
    assert load_tailor_instructions().startswith(TAILOR_PROMPT_HEADER + "\n")
    assert render_tailor_prompt(_JOB, _CTX).startswith(TAILOR_PROMPT_HEADER)
    assert TAILOR_INSTRUCTIONS_DIGEST.startswith("sha256:")


# --- the numeric guard, both ways -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("5 years of Python", "five years of Python"),
        ("five years of Python", "5 years of Python"),
        ("5+ years of Python", "5 years of Python"),
        ("$1.2M ARR", "grew ARR to 1.2 million"),
        ("1.2 million ARR", "$1.2M ARR"),
        ("2019–2023 at Acme", "Acme (2019-2023)"),
        ("twenty-five engineers", "25 engineers"),
        ("25 engineers", "twenty five engineers"),
        ("one of the first hires", "an early hire"),  # "one" as a pronoun is not a number
        ("cut latency 40%", "latency down by 40 percent"),
        ("1,200 users", "1200 users"),
        ("5k requests/s", "5,000 requests per second"),
        ("Python 3 services", "Python3 services"),
    ],
)
def test_numeric_guard_accepts_the_same_number_in_another_form(text: str, source: str) -> None:
    assert unsupported_numbers(text, [source]) == ()


@pytest.mark.parametrize(
    ("text", "source", "span"),
    [
        ("8 years of Python", "five years of Python", "8"),
        ("eight years of Python", "5 years of Python", "eight"),
        ("2019–2023 at Acme", "Acme, 2019 to present", "2023"),
        ("$1.5M ARR", "1.2 million ARR", "1.5M"),
        ("Led 12 engineers", "led a team of engineers", "12"),
        ("cut latency 60%", "cut latency 40%", "60"),
    ],
)
def test_numeric_guard_rejects_a_number_no_source_states(text: str, source: str, span: str) -> None:
    hits = unsupported_numbers(text, [source])
    assert [hit.span for hit in hits] == [span]


def test_numeric_values_normalize_suffixes_ranges_and_words() -> None:
    values = {mention.span: mention.value for mention in numeric_values("$1.2M, 1,200,000, 5k, 2019–2023, one hundred, 3rd, 24/7")}
    assert values["1.2M"] == values["1,200,000"] == 1_200_000
    assert values["5k"] == 5_000 and values["one hundred"] == 100 and values["3"] == 3  # "3rd" states 3
    assert values["2019"] == 2019 and values["2023"] == 2023 and values["24"] == 24 and values["7"] == 7


# --- the posting-term guard: a reject AND an allowed-variant pass ---------------------------------


def test_posting_terms_are_the_technical_and_mid_sentence_capitalized_tokens() -> None:
    terms = posting_terms(_POSTING, exclude=("Staff AI Engineer", "Acme", "Remote"))
    assert {"python", "kubernetes", "terraform", "gcp"} <= terms
    # Stop words, sentence-initial "Remote" and the excluded title/company never qualify.
    assert not {"requirements", "remote", "acme", "staff", "ai", "engineer", "united", "states"} & terms
    # A Capitalized word only ever at a sentence start ("Build ...") is not a term; after a list separator it is.
    assert posting_terms("Build services. Ship code.") == frozenset()
    assert posting_terms("Requirements: Kafka; Spark, Airflow\n- Snowflake") == {"kafka", "spark", "airflow", "snowflake"}
    # Technical on their face, wherever they sit (k8s canonicalizes to kubernetes).
    assert {"c++", "kubernetes", "node.js", "ci/cd", "aws"} <= posting_terms("C++ and k8s. Node.js, CI/CD and AWS too.")
    assert "k8s" not in posting_terms("C++ and k8s. Node.js, CI/CD and AWS too.")


def test_posting_term_guard_rejects_a_borrowed_skill_and_passes_an_alias_variant() -> None:
    terms = posting_terms(_POSTING, exclude=("Staff AI Engineer", "Acme", "Remote"))
    # Reject: the line borrows Terraform from the posting; its source never mentions it.
    assert unsupported_posting_terms("Deep Terraform experience", ["Built Python services for six years"], terms) == ("terraform",)
    # Allowed variants: Kubernetes vs k8s, PostgreSQL vs Postgres, case-insensitive, plural.
    assert unsupported_posting_terms("Ran Kubernetes clusters", ["operated k8s clusters"], terms) == ()
    assert unsupported_posting_terms("PostgreSQL and python", ["Postgres; PYTHON services"], terms | {"postgresql"}) == ()
    assert unsupported_posting_terms("Kubernetes cluster", ["Kubernetes clusters"], terms) == ()
    # A term the line does not use is never a hit, and a copy of the source is always clean.
    assert unsupported_posting_terms("Built Python services", ["Built Python services"], terms) == ()


def test_matrix_requirement_names_replace_the_posting_tokens_when_an_assessment_is_stored() -> None:
    ctx = TailorContext(resume_lines=_LINES, matrix=(MatrixRow("Terraform", "unmet"), MatrixRow("5+ years of Python", "met")))
    assert guard_terms(_JOB, ctx) == matrix_terms(ctx.matrix, exclude=(_JOB.title, _JOB.company, _JOB.location))
    assert guard_terms(_JOB, ctx) == {"terraform", "python"}
    # GCP is in the posting but not in the matrix: with a matrix, it is not guarded.
    assert "gcp" not in guard_terms(_JOB, ctx)
    assert "gcp" in guard_terms(_JOB, _CTX)


# --- the prompt ----------------------------------------------------------------------------


def test_prompt_numbers_the_sources_and_drops_empty_paragraphs() -> None:
    ctx = TailorContext(resume_lines=_LINES, answers={"cloud:gcp": _GCP}, matrix=(MatrixRow("Python", "met"),))
    prompt = render_tailor_prompt(_JOB, ctx)
    assert "R1: # Jane Doe\nR2: jane@example.test | Denver, CO\nR3: ## Experience" in prompt
    assert f"R{len(_LINES)}: Python, Kubernetes, PostgreSQL" in prompt
    assert "A cloud:gcp: Yes, two years on GCP." in prompt
    assert "M1: Python [met]" in prompt
    assert "ROLE: Staff AI Engineer\nCOMPANY: Acme\nPOSTING TEXT:\n" + _POSTING in prompt
    assert "A previous attempt" not in prompt

    bare = render_tailor_prompt(_JOB, TailorContext(resume_lines=_LINES))
    assert "ANSWERS (" not in bare and "REQUIREMENT MATRIX (" not in bare and "{{" not in bare

    retry = render_tailor_prompt(_JOB, ctx, 'summary line 1 contains the number "8" that appears in none of its cited sources (R5)')
    assert retry.startswith(prompt)
    assert retry.endswith(". Return corrected JSON only, matching the schema exactly.")
    assert 'rejected by the validator: summary line 1 contains the number "8"' in retry


def test_substituted_text_is_never_rescanned_for_placeholders() -> None:
    job = TailorJob(title="{{resume_lines}}", company="{{answers}}", location="", posting_text="{{validation_error}}")
    prompt = render_tailor_prompt(job, TailorContext(resume_lines=("{{title}}",)))
    assert "ROLE: {{resume_lines}}" in prompt and "R1: {{title}}" in prompt and "POSTING TEXT:\n{{validation_error}}" in prompt


# --- the validator: whole answer rejected, every message names the line ------------------------------


def test_a_valid_answer_yields_copy_lines_verbatim_and_rewritten_lines_with_source_text() -> None:
    result = validate_tailored_output(_VALID, _JOB, _CTX)
    assert [line.kind for line in result.header] == ["copy", "copy"]
    assert [line.text for line in result.header] == ["# Jane Doe", "jane@example.test | Denver, CO"]
    assert result.header[0].refs[0].to_json() == {"kind": "resume", "line": 1, "text": "# Jane Doe"}
    experience = result.sections[1]
    assert experience.entries[0].heading[0].text == "Acme Corp — Senior Engineer (2019–2023)"
    assert experience.entries[0].heading[0].kind == "copy"
    bullet = experience.entries[0].bullets[0]
    assert bullet.kind == "rewritten" and bullet.refs[0].text == _LINES[4]
    other = result.sections[3].lines[0]
    assert other.refs[0].to_json() == {"kind": "answer", "question_id": "cloud:gcp", "text": _GCP.guard_text}
    assert result.sections[2].lines[0].kind == "copy" and result.sections[2].lines[0].text == "Python, Kubernetes, PostgreSQL"
    assert len(result.rewritten_lines()) == 3 and result.line_count() == 5


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"header": [{"copy": 99}], "sections": [{"heading": "skills", "lines": [{"copy": 8}]}]}, "header[1] copies resume line 99; the resume has 8 lines"),
        (_one_line("Six years of Python.", [{"kind": "resume", "line": 98}]), "summary line 1 cites resume line 98; the resume has 8 lines"),
        (_one_line("Two years on GCP.", [{"kind": "answer", "question_id": "cloud:aws"}]), 'summary line 1 cites "cloud:aws", which is not an answered question'),
        (_one_line("Six years of Python.", []), "summary line 1 has no refs; every rewritten line cites 1 to 4 sources"),
        (_one_line("Eight years of Python.", [{"kind": "resume", "line": 5}]), 'summary line 1 contains the number "Eight" that appears in none of its cited sources (R5)'),
        (_one_line("Led 12 engineers.", [{"kind": "resume", "line": 5}, {"kind": "answer", "question_id": "cloud:gcp"}]), 'summary line 1 contains the number "12" that appears in none of its cited sources (R5, A cloud:gcp)'),
        (_one_line("Deep Terraform experience.", [{"kind": "resume", "line": 6}]), 'summary line 1 contains the posting term "terraform" that appears in none of its cited sources (R6)'),
        ({"header": [{"copy": 1}], "sections": [{"heading": "experience", "entries": [{"heading_ref": [{"text": "Acme Corp", "refs": [{"kind": "resume", "line": 4}]}], "bullets": []}]}]}, 'experience entry 1 heading[1] must be a copy line ({"copy": <resume line number>})'),
        ({"header": [{"text": "Jane", "refs": []}], "sections": [{"heading": "skills", "lines": [{"copy": 8}]}]}, 'header[1] must be a copy line ({"copy": <resume line number>})'),
        ({"header": [], "sections": [{"heading": "experience", "entries": [{"heading_ref": [{"copy": 4}], "bullets": [{"text": "Ran k8s.", "refs": [{"kind": "resume", "line": 999}]}]}]}]}, "experience entry 1 bullet 1 cites resume line 999; the resume has 8 lines"),
        ({"header": [], "sections": [{"heading": "experience", "lines": [{"copy": 4}]}]}, "experience section must hold entries (heading_ref + bullets), not lines"),
        ({"header": [], "sections": [{"heading": "summary", "entries": []}]}, "summary section must hold lines, not entries"),
        ({"header": [], "sections": [{"heading": "career", "lines": [{"copy": 4}]}]}, "sections[1] heading must be one of summary, experience, skills, education, projects, other"),
        ({"header": [], "sections": [{"heading": "skills", "lines": [{"copy": 8}]}, {"heading": "skills", "lines": [{"copy": 8}]}]}, "sections[2] repeats the skills heading; each section appears at most once"),
        ({"header": [], "sections": []}, "sections has 0 items; at least 1 section is required"),
        ({"header": [], "sections": [{"heading": "skills", "lines": [{"copy": 8}]}], "markdown": "# no"}, "the answer has unknown top-level key(s) ['markdown']; only header and sections are allowed"),
    ],
)
def test_the_validator_names_the_offending_line_and_the_reason(payload: dict[str, object], message: str) -> None:
    assert _reject(payload) == message


def test_answer_refs_are_matched_after_normalizing_the_question_id() -> None:
    result = validate_tailored_output(_one_line("Two years on GCP.", [{"kind": "answer", "question_id": "Cloud: GCP"}]), _JOB, _CTX)
    assert result.sections[0].lines[0].refs[0].question_id == "cloud:gcp"


def test_bounds_are_enforced_with_count_and_limit_messages() -> None:
    too_many_sections = {"header": [], "sections": [{"heading": "skills", "lines": [{"copy": 8}]}] * (MAX_SECTIONS + 1)}
    assert _reject(too_many_sections) == f"sections has {MAX_SECTIONS + 1} items; at most {MAX_SECTIONS} allowed"
    header = {"header": [{"copy": 1}] * (MAX_HEADER_LINES + 1), "sections": [{"heading": "skills", "lines": [{"copy": 8}]}]}
    assert _reject(header) == f"header has {MAX_HEADER_LINES + 1} copy lines; at most {MAX_HEADER_LINES} allowed"
    long_text = _one_line("x" * (MAX_TEXT_CHARS + 1), [{"kind": "resume", "line": 5}])
    assert _reject(long_text) == f"summary line 1 text has {MAX_TEXT_CHARS + 1} characters; at most {MAX_TEXT_CHARS} allowed"
    refs = _one_line("Python.", [{"kind": "resume", "line": 5}] * (MAX_REFS_PER_LINE + 1))
    assert _reject(refs) == f"summary line 1 has {MAX_REFS_PER_LINE + 1} refs; at most {MAX_REFS_PER_LINE} allowed"
    total = {"header": [], "sections": [{"heading": "skills", "lines": [{"copy": 8}] * (MAX_TOTAL_LINES + 1)}]}
    assert _reject(total) == f"the sections hold more than {MAX_TOTAL_LINES} lines in total; at most {MAX_TOTAL_LINES} allowed"
    # Exactly the bound is fine.
    at_bound = {"header": [], "sections": [{"heading": "skills", "lines": [{"copy": 8}] * MAX_TOTAL_LINES}]}
    assert validate_tailored_output(at_bound, _JOB, _CTX).line_count() == MAX_TOTAL_LINES


# --- markdown, rendered by code ---------------------------------------------------------------


def test_markdown_is_rendered_from_the_validated_json_with_refs_per_line() -> None:
    result = validate_tailored_output(_VALID, _JOB, _CTX)
    markdown = render_markdown(result)
    assert markdown == (
        "# Jane Doe <!-- R1 -->\n"
        "jane@example.test | Denver, CO <!-- R2 -->\n"
        "\n"
        "## Summary\n"
        "\n"
        "- Engineer with six years of Python services; ran Kubernetes clusters with PostgreSQL. <!-- R5, R6 -->\n"
        "\n"
        "## Experience\n"
        "\n"
        "### Acme Corp — Senior Engineer (2019–2023) <!-- R4 -->\n"
        "\n"
        "- Cut p99 latency by 40% for Python services. <!-- R5 -->\n"
        "\n"
        "## Skills\n"
        "\n"
        "- Python, Kubernetes, PostgreSQL <!-- R8 -->\n"
        "\n"
        "## Other\n"
        "\n"
        "- Two years on GCP. <!-- A cloud:gcp -->\n"
    )
    # Round trip: the JSON keeps every ref (with its source text) for the Q4 job page.
    again = tailored_resume.TailoredResume.from_json(result.to_json())
    assert again == result


# --- tailor_once through the shared loop -------------------------------------------------------


class _ScriptedPort:
    def __init__(self, outputs: list[object]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []
        self.name = "fixture"
        self.timed_out = False

    def invoke(self, request):
        self.prompts.append(request.prompt)
        item = self._outputs.pop(0)
        if isinstance(item, BaseException):
            raise item
        return InvocationResult(
            status="success", output_text=item, resolved_model="fixture", raw_usage={},
            normalized_usage=NormalizedUsage(10, 20, 30), cost_status="unavailable",
        )


class _ScriptedBinding:
    def __init__(self, outputs: list[object]) -> None:
        self.port = _ScriptedPort(outputs)
        self.closed = False

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        self.closed = True


_FABRICATED = json.dumps(_one_line("Led 8 engineers on Terraform.", [{"kind": "resume", "line": 5}]))


def test_tailor_once_retries_once_with_the_error_fed_back_then_gives_up() -> None:
    binding = _ScriptedBinding([_FABRICATED, json.dumps(_VALID)])
    attempt = tailor_once(binding, _JOB, _CTX)
    assert attempt.ok and attempt.attempts == 2
    first, second = binding.port.prompts
    assert first == render_tailor_prompt(_JOB, _CTX)
    assert 'rejected by the validator: summary line 1 contains the number "8"' in second
    assert attempt.parsed.line_count() == 5

    binding = _ScriptedBinding([_FABRICATED, "```json\n" + _FABRICATED + "\n```"])
    attempt = tailor_once(binding, _JOB, _CTX)
    assert not attempt.ok and attempt.not_assessed_reason is NotAssessedReason.MODEL_OUTPUT_INVALID
    assert attempt.attempts == 2
    assert attempt.validation_error == 'summary line 1 contains the number "8" that appears in none of its cited sources (R5)'

    denied = ModelInvocationError("policy")
    denied.code = "model_denied"  # type: ignore[attr-defined]
    attempt = tailor_once(_ScriptedBinding([denied]), _JOB, _CTX)
    assert not attempt.ok and attempt.not_assessed_reason is NotAssessedReason.MODEL_DENIED and attempt.attempts == 0


# --- the real-gig flow ----------------------------------------------------------------------


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME_TEXT.encode("utf-8"))


def _config_with_ollama(home: Path) -> GigAIConfig:
    return build_config(
        home_root=home,
        workpad_root=home.parent / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="ollama", adapter="ollama_local", base_url="http://127.0.0.1:11434"),
        ),
        model_targets=(
            ConfigModelTarget(name="offline-default", endpoint="offline", model="fixture-v1", capabilities=("text",), max_output_tokens=64),
            ConfigModelTarget(
                name="ollama-default", endpoint="ollama", model="fixture-model", capabilities=("text",),
                max_output_tokens=512, model_digest="sha256:" + "c" * 64,
            ),
        ),
    )


def _install(monkeypatch: pytest.MonkeyPatch, outputs: list[object]) -> _ScriptedBinding:
    binding = _ScriptedBinding(outputs)

    def resolve(config, adapter_target, **_kwargs):
        return binding

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return binding


def _run(fx: ProfileFixtureGig, request: TailorRequest) -> TailorResponse:
    return run_tailored_resume(request, home_root=fx.home_root, target=fx.target, config=_config_with_ollama(fx.home_root))


def _pasted(**overrides) -> TailorRequest:
    return TailorRequest(job=AssessJobInput(job_text=_POSTING, title="Staff AI Engineer", company="Acme"), **overrides)


_PENDING_ASSESSMENT = json.dumps(
    {
        "verdict": "pending_user_answers",
        "matrix": [
            {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
            {"requirement": "Terraform", "class": "askable", "status": "unclear", "resume_evidence": []},
        ],
        "suggestions": [],
        "questions": [{"question_id": "cloud:gcp", "question": "Have you run workloads on GCP?", "requirement": "GCP"}],
        "not_a_match_reason": None,
    }
)


def test_run_stores_json_and_markdown_cites_answers_uses_the_matrix_and_keeps_created_at(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    record_answer(home_root=fx.home_root, requested_target=fx.target, gig_id=fx.resolved.gig_id, question_id="cloud:gcp", prompt="Have you run workloads on GCP?", answer="Yes, two years on GCP.")
    # A stored quick assessment for the same pasted job: its matrix is context in the prompt.
    _install(monkeypatch, [_PENDING_ASSESSMENT])
    assessed = run_quick_assessment(AssessRequest(job=AssessJobInput(job_text=_POSTING)), home_root=fx.home_root, target=fx.target, config=_config_with_ollama(fx.home_root))
    binding = _install(monkeypatch, [json.dumps(_VALID)])
    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None

    response = _run(fx, _pasted())

    prompt = binding.port.prompts[0]
    assert "R5: Built Python services for six years; cut p99 latency by 40%." in prompt
    assert "A cloud:gcp: Yes, two years on GCP." in prompt
    assert "M1: 5+ years of Python [met]\nM2: Terraform [unclear]" in prompt
    assert binding.closed is True
    assert response.resume.profile_id == selected.profile_id
    assert response.job.fetch_kind == "pasted" and response.job.title == "Staff AI Engineer"
    assert response.sources.assessment_stored_path == assessed.stored_path
    assert response.sources.resume_line_count == len(_LINES)
    assert list(response.sources.answers) == ["cloud:gcp"] and response.sources.answers["cloud:gcp"].startswith("revision_")
    assert response.producer.callable == "scout.tailor" and response.producer.actor == "scout-tailor"
    assert response.producer.model_target is ModelTarget.OLLAMA_LOCAL
    assert response.usage is not None and response.usage.input_tokens == 10
    assert response.instructions_digest == TAILOR_INSTRUCTIONS_DIGEST
    assert response.created_at == response.updated_at
    cited = [ref.to_json() for line in response.result.rewritten_lines() for ref in line.refs]
    assert {"kind": "answer", "question_id": "cloud:gcp", "text": _GCP.guard_text} in cited

    stored = Path(response.stored_path)
    markdown = Path(response.markdown_path)
    assert stored.is_file() and markdown.is_file() and markdown == stored.with_suffix(".md")
    assert stored.parent.name == selected.profile_id and stored.parent.parent.name == "resumes"
    assert stored.parent.parent.parent == fx.home_root / "scout" / stored.parent.parent.parent.name
    on_disk = json.loads(stored.read_text(encoding="utf-8"))
    assert on_disk["sources"]["answers"] == [{"question_id": "cloud:gcp", "revision_id": response.sources.answers["cloud:gcp"]}]
    assert TailorResponse.from_json(on_disk) == TailorResponse.from_json(response.to_json())
    assert TailorResponse.from_json(on_disk).sources == response.sources
    assert markdown.read_text(encoding="utf-8") == response.markdown == render_markdown(response.result)
    # The posting text is never echoed; the resume-derived lines are the product.
    assert _POSTING not in stored.read_text(encoding="utf-8") and "text" not in on_disk["job"]
    assert "- Python, Kubernetes, PostgreSQL <!-- R8 -->" in response.markdown  # the copied skills line

    # Re-run: created_at kept, updated_at bumped, same files, latest wins.
    _install(monkeypatch, [json.dumps(_VALID)])
    again = _run(fx, _pasted())
    assert again.stored_path == response.stored_path and again.created_at == response.created_at
    assert again.updated_at >= response.updated_at

    # Listing: newest first, profile/job filters, the ephemeral segment.
    _install(monkeypatch, [json.dumps(_VALID)])
    ephemeral = _run(fx, _pasted(resume=AssessResumeInput(resume_text=_RESUME_TEXT)))
    assert Path(ephemeral.stored_path).parent.name == "ephemeral" and ephemeral.resume.profile_id is None
    items = list_tailored_resumes(fx.home_root, fx.target)
    assert [item.stored_path for item in items] == [ephemeral.stored_path, again.stored_path]
    assert [item.stored_path for item in list_tailored_resumes(fx.home_root, fx.target, profile_id=selected.profile_id)] == [again.stored_path]
    assert [item.stored_path for item in list_tailored_resumes(fx.home_root, fx.target, profile_id="ephemeral")] == [ephemeral.stored_path]
    assert len(list_tailored_resumes(fx.home_root, fx.target, job_identity=again.job.job_identity)) == 2
    assert list_tailored_resumes(fx.home_root, fx.target, job_identity="text:sha256:" + "0" * 64) == ()
    with pytest.raises(TailorError) as info:
        list_tailored_resumes(fx.home_root, fx.target, profile_id="../etc")
    assert info.value.code == "invalid_value"


def test_run_rejects_planted_fabrications_after_one_retry_and_stores_nothing(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding = _install(monkeypatch, [_FABRICATED, _FABRICATED])

    with pytest.raises(TailorError) as info:
        _run(fx, _pasted())

    assert info.value.code == "model_output_invalid"
    assert str(info.value) == (
        "the model's answer was invalid after one retry: "
        'summary line 1 contains the number "8" that appears in none of its cited sources (R5)'
    )
    assert len(binding.port.prompts) == 2 and binding.closed is True
    assert list_tailored_resumes(fx.home_root, fx.target) == ()
    assert not list((fx.home_root / "scout").rglob("resumes/*/*")), "nothing is stored for a rejected answer"


def test_run_maps_the_input_and_model_errors_to_typed_codes(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [json.dumps(_VALID)])
    with pytest.raises(TailorError) as info:
        _run(fx, _pasted(resume=AssessResumeInput(profile_id="profile_00000000-0000-4000-8000-00000000dead")))
    assert info.value.code == "profile_not_found"

    with pytest.raises(TailorError) as info:
        _run(fx, _pasted(resume=AssessResumeInput(resume_text="   \n  ")))
    assert info.value.code == "resume_input_invalid"

    _install(monkeypatch, [OSError("connection reset")])
    with pytest.raises(TailorError) as info:
        _run(fx, _pasted())
    assert info.value.code == "model_unavailable"

    denied = ModelInvocationError("policy")
    denied.code = "model_denied"  # type: ignore[attr-defined]
    _install(monkeypatch, [denied])
    with pytest.raises(TailorError) as info:
        _run(fx, _pasted())
    assert info.value.code == "model_denied"


def test_request_dto_parses_and_rejects_unknown_keys() -> None:
    request = TailorRequest.from_json({"job": {"job_text": "x"}, "resume": {"profile_id": "profile_1"}, "model_target": "codex_cli"})
    assert request.model_target is ModelTarget.CODEX_CLI and request.resume.profile_id == "profile_1"
    assert TailorRequest.from_json(request.to_json()) == request
    with pytest.raises(Exception) as info:
        TailorRequest.from_json({"job": {"job_text": "x"}, "preferences": {}})
    assert getattr(info.value, "code", "") == "unknown_key"
