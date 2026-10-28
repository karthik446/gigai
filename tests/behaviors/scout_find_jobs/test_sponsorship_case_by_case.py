"""0.1.11.12 F5a: sponsorship decided "case by case" is its own label, ``case_by_case``.

A posting said visa sponsorship is considered on a case-by-case basis and was labelled ``unknown`` (or ``offered``
when the sentence also said "available").  Both layers now read it:

- the RULE layer (``filters.sponsorship_from_text``, the label a posting gets when it is acquired), after an explicit
  no and the transfer-only read, before "offered";
- the MODEL boundary (``assessment_core``): the prompt names the value, the model's own value is kept, a row the model
  made of the sentence gives the label, and a flat ``not_offered`` for a posting whose own text says case by case is
  read as ``case_by_case``.

It stays a LABEL (rule 5): no row, no question, no hold, no ``not_a_match``.  Every sentence here is synthetic.
"""

from __future__ import annotations

from types import SimpleNamespace
import json

import pytest

from gigai.scout import assessment_core, resume_gate
from gigai.scout.assessment_core import AssessJob, Boundary, assess_once, build_assess_context, render_assess_prompt
from gigai.scout.find_jobs.assess_contracts import AssessmentBody
from gigai.scout.find_jobs.contracts import SponsorshipStatus
from gigai.scout.find_jobs.filters import sponsorship_from_text
from gigai.scout.find_jobs.rank_digest import constraint_flags
from gigai.scout.quick_assess import _parse_body

CASE = "case_by_case"
MATCHED = "matched_above_threshold"
CASE_SENTENCE = "Visa sponsorship is considered on a case-by-case basis for exceptional candidates."
FLAT_NO = "We are unable to sponsor visas for this role, now or in the future."


# --- the rule layer ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    CASE_SENTENCE,
    "Sponsorship: case by case.",
    "H-1B sponsorship is decided case-by-case.",
    "Visa sponsorship requests are evaluated individually.",
    "We consider immigration sponsorship on an individual basis.",
    "Visa sponsorship is available on a case-by-case basis.",
    "Visa sponsorship for U.S. roles is decided case by case.",
    "Visa sponsorship is not guaranteed; it is considered case by case.",
    "About us\n\nWe build things.\n\nVisa sponsorship: considered case by case\nBenefits: PTO",
    # A short heading on its own line is read with the line after it.
    "Visa sponsorship\nConsidered on a case-by-case basis.",
    "Visa sponsorship:\ncase by case",
    "Benefits\n\nVisa Sponsorship\n\nDecided on an individual basis.\nPTO: unlimited.",
    # Typographic hyphens (U+2010, U+2011).
    "Visa sponsorship is considered case\u2011by\u2011case.",
    "Visa sponsorship is considered case\u2010by\u2010case.",
    # "No guarantee" is not a refusal.
    "There is no guarantee of sponsorship; it is decided case by case.",
])
def test_case_by_case_sponsorship_is_its_own_label(text: str) -> None:
    assert sponsorship_from_text(text).value == CASE


@pytest.mark.parametrize(("text", "expected"), [
    # An explicit no comes first, whatever exception follows it.
    (FLAT_NO, "not_offered"),
    ("We are unable to sponsor visas, except case by case for senior roles.", "not_offered"),
    ("We do not sponsor visas, except on a case-by-case basis.", "unknown"),
    # The transfer-only read comes next.
    ("We cannot provide new visa sponsorship but are open to facilitating H-1B visa transfers.", "transfer_only"),
    ("Acme cannot sponsor new work visas, though visa transfers are considered case by case.", "transfer_only"),
    ("We cannot provide new sponsorship but are open to transfers.", "not_offered"),
    # Offered and silent postings, and a case-by-case sentence about something else, read as before.
    ("Visa sponsorship available for qualified candidates.", "offered"),
    ("Relocation assistance is decided case by case. Visa sponsorship is available.", "offered"),
    ("Relocation assistance is decided case by case.", "unknown"),
    ("Bonuses are evaluated individually.", "unknown"),
    # Only sponsorship of a work visa counts: not the verb alone, a company-sponsored perk or a travel visa.
    ("Company-sponsored relocation is decided case by case.", "unknown"),
    ("Travel visa costs for client visits are reimbursed case by case.", "unknown"),
    ("We sponsor conference travel on a case-by-case basis.", "offered"),
    # A heading line that is a sentence of its own, or too long to be a heading, is not joined.
    ("Visa sponsorship is not something we discuss here.\nConsidered on a case-by-case basis.", "unknown"),
    ("Questions about visa sponsorship or anything else\nare considered case by case.", "unknown"),
    # Any sentence that refuses sponsoring keeps the case-by-case read away (the plain refusal reads as before).
    ("We do not sponsor visas.", "unknown"),
    ("We do not sponsor visas. Visa sponsorship for other roles is considered case by case.", "unknown"),
    ("We won't sponsor visas. Immigration support is handled on an individual basis.", "unknown"),
    ("Great benefits and PTO.", "unknown"),
    ("", "unknown"),
])
def test_the_reads_before_case_by_case_keep_their_answers(text: str, expected: str) -> None:
    assert sponsorship_from_text(text).value == expected


def test_the_rank_digest_flags_case_by_case_not_no_sponsor() -> None:
    assert constraint_flags("Visa sponsorship is not guaranteed; it is considered case by case.") == [CASE]
    assert constraint_flags("Build things. " + CASE_SENTENCE) == [CASE]
    assert constraint_flags("We are unable to sponsor visas.") == ["no_sponsor"]
    assert constraint_flags("Relocation assistance is decided case by case.") == []


# --- the stored value -------------------------------------------------------------------------------------------------


def test_the_value_is_additive_and_every_stored_value_still_parses() -> None:
    assert [status.value for status in SponsorshipStatus] == ["offered", "not_offered", "transfer_only", CASE, "unknown"]
    row = {"requirement": "Go in production", "resume_evidence": ["Built Go services"], "status": "met", "class": "hard"}
    for stored in ("offered", "not_offered", "transfer_only", CASE, "unknown"):
        body = AssessmentBody.from_json({"matrix": [row], "suggestions": [], "questions": [], "verdict": MATCHED, "sponsorship": stored})
        assert body.sponsorship is SponsorshipStatus(stored) and body.to_json()["sponsorship"] == stored
    # A stored assessment written without the field still parses and claims nothing (shown as not stated).
    body = AssessmentBody.from_json({"matrix": [row], "suggestions": [], "questions": [], "verdict": MATCHED})
    assert body.sponsorship is None and "sponsorship" not in body.to_json()


# --- the assess prompt and the model boundary ---------------------------------------------------------------------------


class _Port:
    name = "scripted"

    def __init__(self, output: object) -> None:
        self.output, self.prompts = json.dumps(output), []

    def invoke(self, request):
        from gigai.adapters.port import InvocationResult, NormalizedUsage

        self.prompts.append(request.prompt)
        return InvocationResult(
            status="success", output_text=self.output, resolved_model="fixture-model", raw_usage={},
            normalized_usage=NormalizedUsage(1, 1, 2), cost_status="unavailable",
        )


class _Binding:
    def __init__(self, output: object) -> None:
        self.port = _Port(output)

    def request(self, *, role: str, prompt: str):
        return SimpleNamespace(role=role, prompt=prompt)


def _job(visa_text: str) -> AssessJob:
    return AssessJob("Staff Engineer", "Acme", "Remote", f"Requirements: Go in production; Kubernetes. Visa Support: {visa_text}")


def _answer(**more: object) -> dict[str, object]:
    rows = [
        {"requirement": "Go in production", "class": "hard", "class_basis": "Requirements: Go in production", "status": "met",
         "resume_evidence": ["Built Go services"], "sources": ["b-aaaaaa"]},
        {"requirement": "Kubernetes", "class": "askable", "class_basis": "Requirements: Kubernetes", "status": "met",
         "resume_evidence": ["Ran Kubernetes"], "sources": ["b-bbbbbb"]},
    ]
    return {"verdict": MATCHED, "matrix": rows, "questions": [], "suggestions": [], "not_a_match_reason": None, **more}


def _assess(answer: dict[str, object], visa_text: str, *, visa_required: bool = True):
    ctx = build_assess_context(resume_text="- Built Go services", resume_ids=("b-aaaaaa", "b-bbbbbb"), pick_lines=20, visa_sponsorship_required=visa_required)
    binding = _Binding(answer)
    return assess_once(binding, _job(visa_text), ctx, parse=_parse_body), binding


def test_the_prompt_names_the_value_and_keeps_rule_5() -> None:
    text = " ".join(assessment_core.load_assess_instructions().split())
    assert '"sponsorship": "offered|not_offered|transfer_only|case_by_case|unknown"' in text
    assert '"case_by_case" when it says sponsorship is decided case by case or individually' in text
    assert "5. Sponsorship and work authorization NEVER gate." in text
    assert '"transfer_only" and "case_by_case" add no row, no question and no hold either' in text
    prompt = render_assess_prompt(_job(CASE_SENTENCE), build_assess_context(resume_text="- Built Go services", visa_sponsorship_required=True))
    assert "offered|not_offered|transfer_only|case_by_case|unknown" in prompt


@pytest.mark.parametrize("said", ["case_by_case", "Case by case", "case-by-case"])
def test_the_models_own_case_by_case_label_is_kept(said: str) -> None:
    assert assessment_core._normalize_sponsorship(said) == CASE
    attempt, _binding = _assess(_answer(sponsorship=said), CASE_SENTENCE)
    assert attempt.ok and attempt.parsed.sponsorship.value == CASE


def test_a_flat_no_from_the_model_is_read_as_case_by_case_when_the_posting_says_so() -> None:
    attempt, binding = _assess(_answer(sponsorship="not_offered"), CASE_SENTENCE)
    assert attempt.ok and attempt.attempts == 1 and len(binding.port.prompts) == 1  # a rule of the boundary, not a retry
    assert attempt.parsed.sponsorship.value == CASE
    # A posting that says the flat no, or refuses before its exception, keeps the flat no.
    for posting in (FLAT_NO, "We do not sponsor visas, except on a case-by-case basis."):
        attempt, _binding = _assess(_answer(sponsorship="not_offered"), posting)
        assert attempt.parsed.sponsorship is SponsorshipStatus.NOT_OFFERED
    # A transfer-only posting keeps its own correction.
    attempt, _binding = _assess(_answer(sponsorship="not_offered"), "We cannot provide new visa sponsorship but are open to facilitating H-1B visa transfers.")
    assert attempt.parsed.sponsorship is SponsorshipStatus.TRANSFER_ONLY
    # Every other answer is left as the model gave it.
    attempt, _binding = _assess(_answer(sponsorship="offered"), CASE_SENTENCE)
    assert attempt.parsed.sponsorship is SponsorshipStatus.OFFERED
    attempt, _binding = _assess(_answer(), CASE_SENTENCE)
    assert attempt.parsed.sponsorship is None
    # Without a boundary's posting text (a stored answer normalized again) nothing is rewritten.
    payload, _ids, _dropped = assessment_core._normalize_and_strip(_answer(sponsorship="not_offered"), boundary=Boundary())
    assert payload["sponsorship"] == "not_offered"


def test_a_row_the_model_made_of_the_sentence_is_removed_and_gives_the_case_by_case_label() -> None:
    answer = _answer()
    answer["matrix"] = [*answer["matrix"], {
        "id": "elig-sponsorship", "requirement": "Visa sponsorship is considered on a case-by-case basis", "class": "hard",
        "class_basis": "Visa Support: considered on a case-by-case basis", "status": "unclear", "resume_evidence": [], "sources": [],
    }]
    attempt, _binding = _assess(answer, CASE_SENTENCE)
    body = attempt.parsed
    assert attempt.ok and [row.requirement for row in body.matrix] == ["Go in production", "Kubernetes"]
    assert body.sponsorship.value == CASE and body.verdict.value == MATCHED


def test_case_by_case_never_gates_holds_asks_or_rejects() -> None:
    """A candidate who NEEDS sponsorship, a posting that decides it case by case: Matched, a resume is suggested."""

    attempt, _binding = _assess(_answer(sponsorship=CASE), CASE_SENTENCE, visa_required=True)
    body = attempt.parsed
    assert body.sponsorship.value == CASE
    assert body.verdict.value == MATCHED and body.structured_questions == () and body.questions == ()
    assert resume_gate.gate(body.matrix, body.structured_questions, body.verdict).decision == "suggest"


# --- review fix round: a model's correct flat no is kept; nothing gates ------------------------------------------------


@pytest.mark.parametrize("posting", [
    "We do not sponsor visas. Company-sponsored relocation is decided case by case.",
    "We don't sponsor H-1B visas.\nRelocation and visa support for spouses are decided case by case.",
    "We do not sponsor work visas. We sponsor conference travel on a case-by-case basis.",
    "Acme does not sponsor visas for this role. Visa sponsorship for other roles is considered case by case.",
    "We will not sponsor visas. Visa sponsorship\nConsidered on a case-by-case basis.",
])
def test_a_correct_flat_no_from_the_model_is_never_overwritten(posting: str) -> None:
    assert sponsorship_from_text(posting) is not SponsorshipStatus.CASE_BY_CASE
    assert assessment_core._transfer_label({"sponsorship": "not_offered"}, Boundary(posting_text=posting))["sponsorship"] == "not_offered"
    attempt, _binding = _assess(_answer(sponsorship="not_offered"), posting)
    assert attempt.ok and attempt.parsed.sponsorship is SponsorshipStatus.NOT_OFFERED


def test_a_flat_no_is_read_as_case_by_case_under_a_heading() -> None:
    attempt, _binding = _assess(_answer(sponsorship="not_offered"), "\nVisa sponsorship\nConsidered on a case-by-case basis.")
    assert attempt.parsed.sponsorship.value == CASE


_CASE_ROW = {
    "id": "elig-sponsorship", "requirement": "Visa sponsorship is considered on a case-by-case basis", "class": "hard",
    "class_basis": "Visa Support: considered on a case-by-case basis", "resume_evidence": [], "sources": [],
}


def test_a_not_a_match_resting_on_a_case_by_case_row_does_not_gate() -> None:
    answer = _answer(verdict="not_a_match", not_a_match_reason="The candidate needs sponsorship, decided only case by case.")
    answer["matrix"] = [*answer["matrix"], {**_CASE_ROW, "status": "unmet"}]
    attempt, _binding = _assess(answer, CASE_SENTENCE)
    body = attempt.parsed
    assert attempt.ok and [row.requirement for row in body.matrix] == ["Go in production", "Kubernetes"]
    assert body.verdict.value == MATCHED and body.not_a_match_reason is None and body.sponsorship.value == CASE
    assert resume_gate.gate(body.matrix, body.structured_questions, body.verdict).decision == "suggest"


def test_a_pending_answer_resting_on_a_case_by_case_question_does_not_hold() -> None:
    answer = _answer(verdict="pending_user_answers")
    answer["matrix"] = [*answer["matrix"], {**_CASE_ROW, "class": "askable", "status": "unclear"}]
    answer["questions"] = [{
        "question_id": "authorization:visa_sponsorship",
        "question": "Can you work without sponsorship, given the case-by-case policy?",
        "requirement": _CASE_ROW["requirement"],
    }]
    attempt, _binding = _assess(answer, CASE_SENTENCE)
    body = attempt.parsed
    assert attempt.ok and body.structured_questions == () and body.questions == ()
    assert body.verdict.value == MATCHED and body.sponsorship.value == CASE
    assert resume_gate.gate(body.matrix, body.structured_questions, body.verdict).decision == "suggest"


def test_dropped_rows_are_read_as_separate_sentences() -> None:
    """A row about work authorization reviewed individually and a row naming sponsorship are not one sentence."""

    answer = _answer()
    answer["matrix"] = [*answer["matrix"],
        {"id": "elig-authorization", "requirement": "Work authorization", "class": "hard",
         "class_basis": "Work authorization is reviewed individually", "status": "unclear", "resume_evidence": [], "sources": []},
        {"id": "elig-sponsorship", "requirement": "Visa sponsorship", "class": "hard",
         "class_basis": "Visa Support: see the careers page", "status": "unclear", "resume_evidence": [], "sources": []}]
    attempt, _binding = _assess(answer, "See the careers page.")
    body = attempt.parsed
    assert attempt.ok and [row.requirement for row in body.matrix] == ["Go in production", "Kubernetes"]
    assert body.sponsorship is None
