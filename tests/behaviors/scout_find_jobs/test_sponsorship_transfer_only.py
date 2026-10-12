"""0.1.11.10 fix A: "no NEW sponsorship, H-1B transfers taken" is its own label, ``transfer_only``, not a flat no.

A real posting said "We cannot provide new visa sponsorship for this role but are open to facilitating H-1B visa
transfers for eligible candidates" and was labelled ``not_offered``.  Both layers now tell the two apart:

- the RULE layer (``filters.sponsorship_from_text``, the label a posting gets when it is acquired);
- the MODEL boundary (``assessment_core``): the prompt names the value, its synonyms are read, a row the model made
  of the sentence gives the label, and a flat ``not_offered`` for a posting whose own text takes transfers is read
  as ``transfer_only``.

It stays a LABEL (rule 5): no row, no question, no hold, no ``not_a_match``.  ``resume_gate`` reads the flat no only,
and ``transfer_only`` is not one.  Values stored before this one parse as they did.  Every sentence here is synthetic.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from gigai.scout import assessment_core, resume_gate
from gigai.scout.assessment_core import AssessJob, Boundary, assess_once, build_assess_context, render_assess_prompt
from gigai.scout.find_jobs.assess_contracts import AssessmentBody
from gigai.scout.find_jobs.contracts import SponsorshipStatus
from gigai.scout.find_jobs.filters import sponsorship_from_text
from gigai.scout.quick_assess import _parse_body

MATCHED = "matched_above_threshold"
TRANSFER_SENTENCE = (
    "This position is open to candidates currently authorized to work in the U.S. We cannot provide new visa sponsorship "
    "for this role but are open to facilitating H-1B visa transfers for eligible candidates."
)
FLAT_NO = "We are unable to sponsor visas for this role, now or in the future."


# --- the rule layer ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    TRANSFER_SENTENCE,
    "We are unable to sponsor new visas. H-1B transfers are welcome.",
    "No visa sponsorship is available for new petitions; however, we will support an H-1B transfer.",
    "Sponsorship: H-1B transfers only.",
    "We can only consider H-1B transfers at this time.",
    "We do not offer new H-1B sponsorship but we accept the transfer of an existing H-1B.",
    "Acme cannot sponsor new work visas, though visa transfers are considered case by case.",
])
def test_no_new_sponsorship_with_transfers_taken_is_transfer_only(text: str) -> None:
    assert sponsorship_from_text(text) is SponsorshipStatus.TRANSFER_ONLY


@pytest.mark.parametrize("text", [
    FLAT_NO,
    "We are unable to sponsor visas and do not support H-1B transfers.",
    "No sponsorship. We cannot accept H-1B transfers or OPT extensions.",
    "Acme does not provide visa sponsorship, including H-1B transfers.",
    "We cannot provide new visa sponsorship for this role.",
    "Candidates must work without sponsorship; we will not process a visa transfer.",
])
def test_a_flat_no_stays_not_offered_also_when_it_refuses_transfers_too(text: str) -> None:
    assert sponsorship_from_text(text) is SponsorshipStatus.NOT_OFFERED


@pytest.mark.parametrize(("text", "expected"), [
    ("We sponsor visas and support H-1B transfers.", SponsorshipStatus.OFFERED),
    ("Visa sponsorship is available, including H-1B transfers.", SponsorshipStatus.OFFERED),
    ("You will own data transfers between regions and wire transfers for Visa card programs.", SponsorshipStatus.UNKNOWN),
    ("We are open to H-1B transfers.", SponsorshipStatus.UNKNOWN),
    ("", SponsorshipStatus.UNKNOWN),
])
def test_a_transfer_alone_changes_nothing_that_was_read_before(text: str, expected: SponsorshipStatus) -> None:
    assert sponsorship_from_text(text) is expected


# --- the stored value -------------------------------------------------------------------------------------------------


def test_the_value_is_additive_and_every_stored_value_still_parses() -> None:
    assert [status.value for status in SponsorshipStatus] == ["offered", "not_offered", "transfer_only", "unknown"]
    row = {"requirement": "Go in production", "resume_evidence": ["Built Go services"], "status": "met", "class": "hard"}
    for stored in ("offered", "not_offered", "unknown", "transfer_only"):
        body = AssessmentBody.from_json({"matrix": [row], "suggestions": [], "questions": [], "verdict": MATCHED, "sponsorship": stored})
        assert body.sponsorship is SponsorshipStatus(stored) and body.to_json()["sponsorship"] == stored


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
    assert '"sponsorship": "offered|not_offered|transfer_only|unknown"' in text
    assert '"transfer_only" when it says it gives no NEW sponsorship but takes H-1B or visa transfers' in text
    assert 'that is not a flat no, so never "not_offered"' in text
    # Rule 5 itself is unchanged: a label, never a gate.
    assert "5. Sponsorship and work authorization NEVER gate." in text
    assert "add NO row for it, ask NO question about it" in text
    assert '"transfer_only" adds no row, no question and no hold either' in text
    prompt = render_assess_prompt(_job(TRANSFER_SENTENCE), build_assess_context(resume_text="- Built Go services", visa_sponsorship_required=True))
    assert "offered|not_offered|transfer_only|unknown" in prompt


@pytest.mark.parametrize("said", ["transfer_only", "Transfer only", "H-1B transfer only", "h1b transfers only", "transfers_only"])
def test_the_models_own_transfer_label_is_read(said: str) -> None:
    attempt, _binding = _assess(_answer(sponsorship=said), TRANSFER_SENTENCE)
    assert attempt.ok and attempt.parsed.sponsorship is SponsorshipStatus.TRANSFER_ONLY


def test_a_flat_no_from_the_model_is_read_as_transfer_only_when_the_posting_takes_transfers() -> None:
    attempt, binding = _assess(_answer(sponsorship="not_offered"), TRANSFER_SENTENCE)
    assert attempt.ok and attempt.attempts == 1 and len(binding.port.prompts) == 1  # a rule of the boundary, not a retry
    assert attempt.parsed.sponsorship is SponsorshipStatus.TRANSFER_ONLY
    # A posting that says the flat no keeps the flat no, and every other label is left as the model gave it.
    attempt, _binding = _assess(_answer(sponsorship="not_offered"), FLAT_NO)
    assert attempt.parsed.sponsorship is SponsorshipStatus.NOT_OFFERED
    attempt, _binding = _assess(_answer(sponsorship="offered"), TRANSFER_SENTENCE)
    assert attempt.parsed.sponsorship is SponsorshipStatus.OFFERED
    attempt, _binding = _assess(_answer(), TRANSFER_SENTENCE)
    assert attempt.parsed.sponsorship is None  # the model said nothing: nothing is claimed for it


def test_a_row_the_model_made_of_the_sentence_is_removed_and_gives_the_transfer_label() -> None:
    answer = _answer()
    answer["matrix"] = [*answer["matrix"], {
        "id": "elig-sponsorship", "requirement": "We cannot provide new visa sponsorship but are open to facilitating H-1B visa transfers", "class": "hard",
        "class_basis": "Visa Support: We cannot provide new visa sponsorship", "status": "unmet", "resume_evidence": [], "sources": [],
    }]
    answer["verdict"], answer["not_a_match_reason"] = "not_a_match", "Needs sponsorship."
    attempt, _binding = _assess(answer, TRANSFER_SENTENCE)
    body = attempt.parsed
    assert attempt.ok and [row.requirement for row in body.matrix] == ["Go in production", "Kubernetes"]
    assert body.sponsorship is SponsorshipStatus.TRANSFER_ONLY and body.verdict.value == MATCHED


# --- it is a label: nothing gates on it --------------------------------------------------------------------------------


def test_transfer_only_is_not_a_flat_no_and_never_gates_holds_asks_or_rejects() -> None:
    """A candidate who NEEDS sponsorship, a posting that takes transfers only: Matched, a resume is suggested, nothing is asked."""

    attempt, _binding = _assess(_answer(sponsorship="transfer_only"), TRANSFER_SENTENCE, visa_required=True)
    body = attempt.parsed
    assert body.sponsorship is SponsorshipStatus.TRANSFER_ONLY
    assert body.verdict.value == MATCHED and body.structured_questions == () and body.questions == ()
    assert resume_gate.gate(body.matrix, body.structured_questions, body.verdict).decision == "suggest"
    # The gate's own reader of the flat no does not read the transfer sentence's label as one: the boundary gives it
    # ``transfer_only`` before ``says_no_sponsorship`` is asked (the test above), and resume_gate never names the value.
    source = Path(resume_gate.__file__).read_text(encoding="utf-8")
    assert "transfer_only" not in source and "TRANSFER_ONLY" not in source
    assert resume_gate.says_no_sponsorship([{"requirement": FLAT_NO}]) is True
    # Without a boundary's posting text (a stored answer normalized again) the model's flat no is kept: nothing is rewritten.
    payload, _ids, _dropped = assessment_core._normalize_and_strip(_answer(sponsorship="not_offered"), boundary=Boundary())
    assert payload["sponsorship"] == "not_offered"
