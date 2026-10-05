"""0.1.11 N3 (SPEC 1.5): the gate. One pure function decides whether a resume is suggested, and the verdict asks it.

One test per row of the accepted table, the two defaults (OD1: an unmet ``askable`` row of a v9 assessment holds the
resume and the job reads "Has a gap"; OD2: an optional row of a v9 assessment never holds the verdict), what stays
exactly as it was for every matrix made before 0.1.11, and the property the design rests on: over generated
matrices, the verdict that is stored and the gate never disagree.

0.1.11 N3b (orchestrator decision #11): an unresolved must-have HOLDS and asks.  On v9 rows a ``hard`` or ``askable``
row that is ``unclear`` holds the verdict with or without a question on it; the answer without one spends the one
retry (the error names the row), and when the retry's answer still asks nothing the assessment is not failed: code
asks the row's own question and the verdict is pending.  An optional row never holds, and a v8 matrix is what it was.
"""

from __future__ import annotations

import itertools
import json
import random
from types import SimpleNamespace

import pytest

from gigai.scout import assessment_core, posting_search, proposals, question_ids, requirements_list, resume_gate
from gigai.scout import requirement_weights as weights
from gigai.scout.assessment_basis import posting_sha256
from gigai.scout.assessment_core import AssessJob, Boundary, assess_once, build_assess_context
from gigai.scout.find_jobs import job_state
from gigai.scout.find_jobs.assess_contracts import GateReason, GateRecord
from gigai.scout.find_jobs.contracts import (
    AssessmentQuestion,
    FindJobsContractError,
    MatrixStatus,
    NotAssessedReason,
    RequirementClass,
    RequirementMatrixRow,
)
from gigai.scout.quick_assess import _parse_body
from gigai.scout.requirements_list import ListedRequirement
from gigai.scout.resume_gate import HOLD_QUESTION, HOLD_UNMET, NOT_A_MATCH, SUGGEST, gate

MATCHED, PENDING, FAILED = "matched_above_threshold", "pending_user_answers", "not_a_match"


def _row(requirement: str, klass: str | None, status: str, *, v9: bool = True, **more: object) -> dict[str, object]:
    row: dict[str, object] = {"requirement": requirement, "status": status, "resume_evidence": [], **more}
    if klass is not None:
        row["class"] = klass
    if v9:
        row.setdefault("class_basis", f"Requirements: {requirement}")
    return row


def _question(requirement: str | None) -> dict[str, object]:
    slug = (requirement or "other").lower().replace(" ", "-")
    return {"question_id": f"tooling:{slug}", "question": f"{requirement}?", "requirement": requirement}


# --- the table, one test per row -------------------------------------------------------------------------------


def test_row_1_mandatory_supported_optional_gaps_remain_a_resume_is_suggested() -> None:
    rows = [
        _row("8+ years", "hard", "met"), _row("Kafka", "askable", "met"),
        _row("Helm", "list_item", "unclear"), _row("Istio", "list_item", "unmet"), _row("Rust", "nice_to_have", "unmet"),
    ]
    questions = [_question("Helm"), _question("Istio"), _question("Rust")]
    found = gate(rows, questions, MATCHED)
    assert found.decision == SUGGEST and found.suggests and found.reasons == ()
    # OD2: whatever the count of open optional rows, and whatever the model answered.
    assert weights.settled_verdict(PENDING, rows, questions) == MATCHED
    assert weights.blocking_question_count(rows, questions) == 0
    assert weights.minor_gap_text(weights.minor_gaps(rows)) == "3 minor gaps: Helm, Istio, Rust"


def test_row_2_a_mandatory_requirement_unresolved_holds_for_the_answer() -> None:
    rows = [_row("8+ years", "hard", "met"), _row("Kafka", "askable", "unclear", id="req-aaaaaa"), _row("Helm", "list_item", "unclear")]
    found = gate(rows, [_question("Kafka"), _question("Helm")], MATCHED)
    assert found.decision == HOLD_QUESTION
    assert [(reason.code, reason.requirement_id, reason.requirement) for reason in found.reasons] == [("question_open", "req-aaaaaa", "Kafka")]
    assert weights.settled_verdict(MATCHED, rows, [_question("Kafka")]) == PENDING
    # A question that names no row of the matrix holds too: nothing says it is minor.
    assert gate(rows, [_question("Something else")], MATCHED).decision == HOLD_QUESTION
    assert gate(rows, [_question(None)], MATCHED).decision == HOLD_QUESTION


def test_row_3_a_hard_requirement_confirmed_unmet_is_not_a_match() -> None:
    rows = [_row("Security clearance", "hard", "unmet", id="req-bbbbbb"), _row("Kafka", "askable", "unmet")]
    found = gate(rows, [], FAILED)
    assert found.decision == NOT_A_MATCH and [(reason.code, reason.requirement_id) for reason in found.reasons] == [("hard_unmet", "req-bbbbbb")]
    # The rows decide, whatever verdict word came with them; a row with no class is an old one and reads hard.
    assert gate(rows, [_question("Kafka")], MATCHED).decision == NOT_A_MATCH
    assert gate([_row("Old shape", None, "unmet", v9=False)], [], MATCHED).decision == NOT_A_MATCH
    assert gate([_row("Kafka", "askable", "met")], [], FAILED).decision == NOT_A_MATCH


def test_row_3_od1_an_askable_requirement_confirmed_unmet_holds_the_resume_and_keeps_the_verdict() -> None:
    rows = [_row("8+ years", "hard", "met"), _row("Kubernetes in production", "askable", "unmet", id="req-cccccc"), _row("Helm", "list_item", "unclear")]
    found = gate(rows, [_question("Helm")], MATCHED)
    assert found.decision == HOLD_UNMET and not found.suggests
    assert [(reason.code, reason.requirement_id, reason.requirement) for reason in found.reasons] == [("askable_unmet", "req-cccccc", "Kubernetes in production")]
    assert found.record() == GateRecord("hold_unmet", (GateReason("askable_unmet", "req-cccccc"),))
    # The verdict is stored as the model returned it (v8 answers matched here), and the validator accepts it.
    assert weights.settled_verdict(MATCHED, rows, [_question("Helm")]) == MATCHED
    proposals.validate_assessment_bounds({"verdict": MATCHED, "matrix": rows, "suggestions": [], "questions": ["Helm?"], "structured_questions": [_question("Helm")]})
    # A question on a mandatory row is asked first: the hold is for the answer, not yet for the gap.
    assert gate([*rows, _row("Kafka", "askable", "unclear")], [_question("Kafka")], MATCHED).decision == HOLD_QUESTION


def test_row_4_alternatives_one_supported_counts_as_supported() -> None:
    rows = [_row("Cassandra or MongoDB", "askable", "met", alternatives=["Cassandra", "MongoDB"], sources=["b-23b6dc"])]
    assert gate(rows, [], MATCHED).decision == SUGGEST
    assert resume_gate.uses_v9_rules(rows)


# --- every matrix made before 0.1.11 is read by the rules it was made under --------------------------------------


def test_an_older_matrix_keeps_the_minor_gap_threshold_and_never_reads_has_a_gap() -> None:
    rows = [_row("8+ years", "hard", "met", v9=False), _row("Kafka", "askable", "unmet", v9=False), _row("Helm", "list_item", "unclear", v9=False), _row("Istio", "list_item", "unclear", v9=False)]
    assert not resume_gate.uses_v9_rules(rows)
    # One open one-of-a-list question holds nothing; two hold the verdict (0110-10-03).
    assert gate(rows, [_question("Helm")], MATCHED).decision == SUGGEST
    assert gate(rows, [_question("Helm"), _question("Istio")], MATCHED).decision == HOLD_QUESTION
    assert weights.settled_verdict(MATCHED, rows, [_question("Helm"), _question("Istio")]) == PENDING
    assert weights.blocking_question_count(rows, [_question("Helm"), _question("Istio")]) == 2
    # An unmet askable row never held a v8 match (assess.md v8 rule 7): it still does not.
    assert gate(rows, [], MATCHED).decision == SUGGEST
    # The typed rows of a stored assessment read the same.
    typed = [RequirementMatrixRow(str(row["requirement"]), (), MatrixStatus(row["status"]), RequirementClass(row["class"])) for row in rows]
    assert gate(typed, [_question("Helm"), _question("Istio")], MATCHED).decision == HOLD_QUESTION
    with_ids = [RequirementMatrixRow(row.requirement, (), row.status, row.requirement_class, id=f"req-{index:06x}") for index, row in enumerate(typed)]
    assert resume_gate.uses_v9_rules(with_ids) and gate(with_ids, [_question("Helm"), _question("Istio")], MATCHED).decision == HOLD_UNMET


@pytest.mark.parametrize("field", [{"id": "req-aaaaaa"}, {"class_basis": "Requirements"}, {"alternatives": ["a", "b"]}, {"sources": ["b-1"]}])
def test_one_v9_field_on_one_row_makes_the_matrix_a_v9_one(field: dict[str, object]) -> None:
    rows = [_row("8+ years", "hard", "met", v9=False), _row("Helm", "list_item", "unclear", v9=False, **field), _row("Istio", "list_item", "unclear", v9=False)]
    assert resume_gate.uses_v9_rules(rows)
    assert gate(rows, [_question("Helm"), _question("Istio")], PENDING).decision == SUGGEST


# --- N3b (decision #11): an unresolved must-have holds and asks ---------------------------------------------------


class _Port:
    name = "scripted"

    def __init__(self, outputs: list[object]) -> None:
        self.outputs = [output if isinstance(output, str) else json.dumps(output) for output in outputs]
        self.prompts: list[str] = []

    def invoke(self, request):
        self.prompts.append(request.prompt)
        return SimpleNamespace(output_text=self.outputs.pop(0), normalized_usage=None)


class _Binding:
    def __init__(self, *outputs: object) -> None:
        self.port = _Port(list(outputs))

    def request(self, *, role: str, prompt: str):
        return SimpleNamespace(role=role, prompt=prompt)


JOB = AssessJob("Staff Engineer", "Acme", "Remote", "Requirements: Go in production; Kubernetes in production. Nice to have: Helm.")
POSTING_SHA = posting_sha256(JOB.title, JOB.posting_text)
GO, KUBERNETES, HELM = "Go in production", "Kubernetes in production", "Helm"
#: The ids a first assessment's rows are given (``requirements_list``), so what the stored rows carry.
GO_ID, KUBERNETES_ID, HELM_ID = requirements_list.assign_ids(POSTING_SHA, [GO, KUBERNETES, HELM])
KUBERNETES_QUESTION = {"question_id": "tooling:kubernetes", "question": "Have you run Kubernetes in production?", "requirement": KUBERNETES}
CODE_QUESTION = "The posting asks for: Kubernetes in production. Do you have this? Say where."


def _answer(*, kubernetes: tuple[str, str] = ("askable", "unclear"), helm: str = "met", questions: tuple[dict[str, object], ...] = (), verdict: str = MATCHED, v9: bool = True, go: str = "met") -> dict[str, object]:
    """A model's answer: Go (hard), Kubernetes (the row under test) and Helm (one of a list), with ``questions`` and the verdict it said."""

    rows = [_row(GO, "hard", go, v9=v9), _row(KUBERNETES, kubernetes[0], kubernetes[1], v9=v9), _row(HELM, "list_item", helm, v9=v9)]
    return {"verdict": verdict, "matrix": rows, "questions": list(questions), "suggestions": [], "not_a_match_reason": "No Go." if verdict == FAILED else None}


def _assess(*outputs: object, ctx=None):
    binding = _Binding(*outputs)
    return assess_once(binding, JOB, ctx or build_assess_context(resume_text="- Built Go services"), parse=_parse_body), binding


def _stored_gate(attempt) -> resume_gate.ResumeGate:
    body = attempt.parsed
    return gate(body.matrix, body.structured_questions, body.verdict)


def test_an_unclear_must_have_with_its_question_holds_for_the_answer() -> None:
    attempt, binding = _assess(_answer(questions=(KUBERNETES_QUESTION,)))
    assert attempt.ok and attempt.attempts == 1 and len(binding.port.prompts) == 1
    # The model said matched; the question is on a must-have row, so the verdict is pending and the gate holds for the answer.
    assert attempt.parsed.verdict.value == PENDING and _stored_gate(attempt).decision == HOLD_QUESTION
    assert [question.to_json() for question in attempt.parsed.structured_questions] == [KUBERNETES_QUESTION]  # the model's own, nothing added
    assert attempt.extras.asked_by_code == ()


@pytest.mark.parametrize("klass", ["askable", "hard", None])
def test_an_unclear_must_have_with_no_question_holds_in_the_gate_and_is_refused_by_the_validator(klass: str | None) -> None:
    rows = [_row(GO, "hard", "met"), _row(KUBERNETES, klass, "unclear", id="req-aaaaaa"), _row(HELM, "list_item", "unclear")]
    assert [row["requirement"] for row in resume_gate.unasked_rows(rows, [])] == [KUBERNETES]
    # The gate: it holds for the answer, and names the row. The verdict is settled by the same rule.
    for questions in ([], [_question(HELM)]):
        found = gate(rows, questions, MATCHED)
        assert found.decision == HOLD_QUESTION and not found.suggests
        assert [(reason.code, reason.requirement_id, reason.requirement) for reason in found.reasons] == [("question_open", "req-aaaaaa", KUBERNETES)]
        assert weights.settled_verdict(MATCHED, rows, questions) == PENDING == weights.settled_verdict(PENDING, rows, questions)
    # A question that names no row is on none: the row is still not asked about.
    assert resume_gate.unasked_rows(rows, [_question(None), _question("Something else")]) == [rows[1]]
    assert resume_gate.unasked_rows(rows, [_question(KUBERNETES.upper())]) == []  # a question finds its row by its words, as the gate reads it
    # The validator: neither verdict is stored with such a row, and the message names the row by its id.
    for said in (MATCHED, PENDING):
        payload = {"verdict": said, "matrix": rows, "suggestions": [], "questions": [f"{HELM}?"], "structured_questions": [_question(HELM)]}
        with pytest.raises(FindJobsContractError) as refused:
            proposals.validate_assessment_bounds(payload)
        assert str(refused.value).endswith(
            "unclear mandatory row req-aaaaaa has no question: every hard or askable row whose status is unclear needs its own entry "
            "in questions, with requirement set to that row's requirement text; or settle the row met or unmet"
        )
    # A row that carries no id is named by its place; several rows are counted and named.
    more = [_row(GO, "hard", "unclear"), _row(KUBERNETES, klass, "unclear", id="req-aaaaaa"), _row("Terraform", "askable", "unclear", id="elig-region")]
    with pytest.raises(FindJobsContractError) as refused:
        proposals.validate_assessment_bounds({"verdict": MATCHED, "matrix": more, "suggestions": [], "questions": []})
    assert "unclear mandatory row matrix[0] has no question (and 2 more: req-aaaaaa, elig-region): every hard or askable row" in str(refused.value)
    assert len(resume_gate.unasked_message([f"req-{index:06x}" for index in range(40)])) < 300  # what the retry prompt keeps of it
    # Beside a hard gap the answer is not a match and keeps no question: nothing is asked for.
    gap = [_row(GO, "hard", "unmet"), _row(KUBERNETES, klass, "unclear", id="req-aaaaaa")]
    assert gate(gap, [], FAILED).decision == NOT_A_MATCH
    proposals.validate_assessment_bounds({"verdict": FAILED, "matrix": gap, "suggestions": [], "questions": []})


def test_the_first_answer_with_no_question_spends_the_retry_and_the_retry_fixes_it() -> None:
    attempt, binding = _assess(_answer(), _answer(questions=(KUBERNETES_QUESTION,)))
    assert attempt.ok and attempt.attempts == 2
    # The error names the row by the id its stored row carries, and is fed back on the one retry.
    assert attempt.validation_error.startswith(f"unclear mandatory row {KUBERNETES_ID} has no question: every hard or askable row")
    assert f"unclear mandatory row {KUBERNETES_ID} has no question" in binding.port.prompts[1] and "has no question" not in binding.port.prompts[0]
    assert attempt.parsed.verdict.value == PENDING and _stored_gate(attempt).decision == HOLD_QUESTION
    assert [question.to_json() for question in attempt.parsed.structured_questions] == [KUBERNETES_QUESTION] and attempt.extras.asked_by_code == ()
    # A retry that settles the row instead fixes it too: nothing is left to ask.
    attempt, _binding = _assess(_answer(), _answer(kubernetes=("askable", "met")))
    assert attempt.ok and attempt.attempts == 2 and attempt.parsed.verdict.value == MATCHED and _stored_gate(attempt).decision == SUGGEST
    assert attempt.parsed.structured_questions == () and attempt.extras.asked_by_code == ()


def test_no_question_after_the_retry_is_not_a_failure_code_asks_the_rows_own_question() -> None:
    question_id = f"requirement:{KUBERNETES_ID.replace('-', '.')}"
    attempt, binding = _assess(_answer(), _answer())
    assert attempt.ok and attempt.not_assessed_reason is None and attempt.attempts == 2 and len(binding.port.prompts) == 2
    body = attempt.parsed
    # The row's own question, from its requirement words, in both lists; the verdict stored is pending and the gate holds.
    assert [question.to_json() for question in body.structured_questions] == [{"question_id": question_id, "question": CODE_QUESTION, "requirement": KUBERNETES}]
    assert body.questions == (CODE_QUESTION,) and body.verdict.value == PENDING
    assert _stored_gate(attempt).decision == HOLD_QUESTION and [reason.requirement for reason in _stored_gate(attempt).reasons] == [KUBERNETES]
    assert weights.blocking_question_count(body.matrix, body.structured_questions) == 1
    assert attempt.extras.asked_by_code == (question_id,)
    # The id: derived from the row's id, so the same every time; in the form every question store takes, and its own normal form.
    assert assessment_core.row_question_id(KUBERNETES_ID) == question_id and assessment_core.ROW_QUESTION_CATEGORY == "requirement"
    again, _binding = _assess(_answer(), _answer())
    assert [question.question_id for question in again.parsed.structured_questions] == [question_id]
    assert question_ids.normalize_question_id(question_id) == question_id and question_ids.is_valid_question_id(question_id)
    assert AssessmentQuestion.from_json(body.structured_questions[0].to_json()).question_id == question_id
    for row_id in ("req-0a1b2c", "req-" + "f" * 64, "elig-location", "elig-region", "elig-work-mode", "elig-sponsorship"):
        made = assessment_core.row_question_id(row_id)
        assert question_ids.normalize_question_id(made) == made and question_ids.is_valid_question_id(made) and proposals._QUESTION_ID_RE.fullmatch(made)
    # The model said pending with nothing to answer: the same.
    attempt, _binding = _assess(_answer(verdict=PENDING), _answer(verdict=PENDING))
    assert attempt.ok and attempt.parsed.verdict.value == PENDING and attempt.extras.asked_by_code == (question_id,)
    # The retry was spent on something else (an answer that is no JSON): the next answer is still the last, and code asks.
    attempt, _binding = _assess("I could not decide.", _answer())
    assert attempt.ok and attempt.attempts == 2 and attempt.parsed.verdict.value == PENDING and attempt.extras.asked_by_code == (question_id,)
    # Every unclear must-have row gets its own; a row the model did ask about keeps the model's question.
    both = _answer(kubernetes=("hard", "unclear"), go="unclear", questions=(_question(GO),))
    attempt, _binding = _assess(_answer(kubernetes=("hard", "unclear"), go="unclear"), both)
    assert attempt.validation_error.startswith(f"unclear mandatory row {GO_ID} has no question (and 1 more: {KUBERNETES_ID}): every hard")
    assert [question.question_id for question in attempt.parsed.structured_questions] == ["tooling:go_in_production", question_id]
    # The words of the question: one line, no doubled full stop, never over the bound of a question.
    assert assessment_core.row_question("req-0a1b2c", "  8+ years of\n Go.  ")["question"] == "The posting asks for: 8+ years of Go. Do you have this? Say where."
    assert len(assessment_core.row_question("req-0a1b2c", "x" * 1200)["question"]) < proposals._MAX_QUESTION


def test_a_listed_row_is_named_and_asked_about_by_the_lists_id() -> None:
    listed = (ListedRequirement("req-aaaaaa", GO, "hard", f"Requirements: {GO}"), ListedRequirement("req-bbbbbb", KUBERNETES, "askable", f"Requirements: {KUBERNETES}"))
    answer = _answer()
    answer["matrix"] = [{**row, "id": row_id} for row, row_id in zip(answer["matrix"][:2], ("req-aaaaaa", "req-bbbbbb"))]
    attempt, binding = _assess(answer, answer, ctx=build_assess_context(resume_text="- Built Go services", requirements=listed))
    assert attempt.ok and attempt.attempts == 2 and attempt.validation_error.startswith("unclear mandatory row req-bbbbbb has no question")
    assert [question.to_json() for question in attempt.parsed.structured_questions] == [
        {"question_id": "requirement:req.bbbbbb", "question": CODE_QUESTION, "requirement": KUBERNETES},
    ]
    assert _stored_gate(attempt).record() == GateRecord("hold_question", (GateReason("question_open", "req-bbbbbb"),))


def test_an_answer_given_under_the_rows_own_question_id_is_asked_for_once() -> None:
    # The model asks again under the id code made (it is in PRIOR ANSWERS by then), naming no row: that question is the row's.
    mine = {"question_id": f"requirement:{KUBERNETES_ID.replace('-', '.')}", "question": "Where did you run Kubernetes?", "requirement": None}
    attempt, _binding = _assess(_answer(questions=(mine,)), _answer(questions=(mine,)))
    assert attempt.ok and attempt.attempts == 2
    assert [question.to_json() for question in attempt.parsed.structured_questions] == [{**mine, "requirement": KUBERNETES}]
    assert attempt.extras.asked_by_code == () and _stored_gate(attempt).decision == HOLD_QUESTION


@pytest.mark.parametrize("klass", ["list_item", "nice_to_have"])
def test_an_optional_row_unclear_with_no_question_stays_as_it_is_and_never_holds(klass: str) -> None:
    rows = [_row(GO, "hard", "met"), _row(KUBERNETES, klass, "unclear", id="req-aaaaaa")]
    assert resume_gate.unasked_rows(rows, []) == [] and gate(rows, [], MATCHED).decision == SUGGEST
    assert weights.settled_verdict(MATCHED, rows, []) == MATCHED
    attempt, binding = _assess(_answer(kubernetes=(klass, "unclear"), helm="unclear"))
    assert attempt.ok and attempt.attempts == 1 and len(binding.port.prompts) == 1  # no retry is spent on it
    assert attempt.parsed.verdict.value == MATCHED and attempt.parsed.structured_questions == () and attempt.parsed.questions == ()
    assert _stored_gate(attempt).decision == SUGGEST and attempt.extras.asked_by_code == ()


def test_a_v8_matrix_with_an_unclear_must_have_and_no_question_is_what_it_was() -> None:
    answer = _answer(v9=False)
    assert not resume_gate.uses_v9_rules(answer["matrix"]) and resume_gate.unasked_rows(answer["matrix"], []) == []
    assert gate(answer["matrix"], [], MATCHED).decision == SUGGEST
    assert weights.settled_verdict(MATCHED, answer["matrix"], []) == MATCHED and weights.settled_verdict(PENDING, answer["matrix"], []) == PENDING
    attempt, binding = _assess(answer)
    assert attempt.ok and attempt.attempts == 1 and len(binding.port.prompts) == 1 and attempt.validation_error is None
    # Stored as it came, key for key: matched, no question, no v9 key.
    assert json.dumps(attempt.parsed.to_json(), sort_keys=True) == json.dumps({
        "matrix": [
            {"requirement": GO, "resume_evidence": [], "status": "met", "class": "hard"},
            {"requirement": KUBERNETES, "resume_evidence": [], "status": "unclear", "class": "askable"},
            {"requirement": HELM, "resume_evidence": [], "status": "met", "class": "list_item"},
        ],
        "suggestions": [], "questions": [], "verdict": MATCHED,
    }, sort_keys=True)
    assert attempt.extras == assessment_core.AssessExtras()
    # The validator's words for a v8 answer are the ones it had (a pending verdict with nothing to answer is still refused as before).
    with pytest.raises(FindJobsContractError) as refused:
        proposals.validate_assessment_bounds({**assessment_core._normalize_assessment_payload(answer), "verdict": PENDING})
    assert str(refused.value).endswith(
        "verdict pending_user_answers but no question holds it (rule 7: no hard unmet row, and no question but at most one on a list_item row -> matched_above_threshold)"
    )


def test_beside_a_hard_gap_nothing_is_asked_for_and_no_retry_is_spent() -> None:
    attempt, binding = _assess(_answer(go="unmet", verdict=FAILED))
    assert attempt.ok and attempt.attempts == 1 and attempt.parsed.verdict.value == FAILED and attempt.parsed.structured_questions == ()
    assert _stored_gate(attempt).decision == NOT_A_MATCH and attempt.extras.asked_by_code == ()
    # Outside ``assess_once`` the boundary neither refuses nor asks: the answer is left as it came (the validator refuses it).
    payload = assessment_core._normalize_assessment_payload(_answer())
    assert "structured_questions" not in payload and payload["questions"] == [] and payload["verdict"] == PENDING
    # A failed attempt for another reason is still a failed attempt.
    attempt, _binding = _assess("no JSON", "still none")
    assert not attempt.ok and attempt.not_assessed_reason is NotAssessedReason.MODEL_OUTPUT_INVALID


# --- the verdict and the gate never disagree ----------------------------------------------------------------------

_CONSISTENT = {(MATCHED, SUGGEST), (MATCHED, HOLD_UNMET), (PENDING, HOLD_QUESTION), (FAILED, NOT_A_MATCH)}


def _generated(seed: int):
    rng = random.Random(seed)
    for number in range(4000):
        v9 = rng.random() < 0.6
        count = rng.randint(1, 7)
        rows = [
            _row(f"requirement {index}", rng.choice(["hard", "askable", "list_item", "nice_to_have", None]), rng.choice(["met", "met", "unmet", "unclear"]), v9=v9)
            for index in range(count)
        ]
        if v9 and rng.random() < 0.3:
            for row in rows:
                row.pop("class_basis")
            rows[rng.randrange(count)]["id"] = "elig-location"  # a v9 answer whose only marker is one fixed id
        asked = [row for row in rows if row["status"] == "unclear" and rng.random() < 0.8]
        questions = [_question(str(row["requirement"])) for row in asked]
        if rng.random() < 0.1:
            questions.append(_question("a requirement no row states"))
        yield number, rows, questions, rng.choice([MATCHED, PENDING, FAILED])


@pytest.mark.parametrize("seed", [11, 2026])
def test_the_stored_verdict_and_the_gate_never_disagree_over_generated_matrices(seed: int) -> None:
    accepted = {pair: 0 for pair in _CONSISTENT}
    refused = 0
    unasked_refused = asked_by_code = v8_unasked_stored = 0
    for number, rows, questions, said in _generated(seed):
        answer = {"verdict": said, "matrix": rows, "questions": questions, "suggestions": [], "not_a_match_reason": "x" if said == FAILED else None}
        payload = assessment_core._normalize_assessment_payload(answer)
        try:
            proposals.validate_assessment_bounds(payload)
        except FindJobsContractError as exc:
            refused += 1  # an answer the product refuses is never stored: nothing to agree with
            if "has no question" not in str(exc):
                continue
            # N3b: refused for an unclear must-have row nothing asks about (v9 rows only). As the retry's answer the same
            # words are NOT refused: code asks each such row's question, and the verdict and the gate agree on the hold.
            unasked_refused += 1
            assert resume_gate.uses_v9_rules(rows) and str(exc).startswith("unclear mandatory row "), (seed, number, str(exc))
            boundary = Boundary(retry=True, posting_sha256=POSTING_SHA)
            payload = assessment_core._normalize_and_strip(answer, boundary=boundary)[0]
            proposals.validate_assessment_bounds(payload)
            assert boundary.extras.asked_by_code and len(set(boundary.extras.asked_by_code)) == len(boundary.extras.asked_by_code), (seed, number)
            assert all(question_ids.normalize_question_id(made) == made for made in boundary.extras.asked_by_code), (seed, number)
            asked_by_code += len(boundary.extras.asked_by_code)
            assert (payload["verdict"], gate(payload["matrix"], payload["structured_questions"], payload["verdict"]).decision) == (PENDING, HOLD_QUESTION), (seed, number)
        stored = payload["verdict"]
        decided = gate(payload["matrix"], payload.get("structured_questions", []), stored).decision
        assert (stored, decided) in _CONSISTENT, (seed, number, said, stored, decided, rows, questions)
        if decided == HOLD_UNMET:
            assert resume_gate.uses_v9_rules(payload["matrix"]), (seed, number)
        # N3b: no stored v9 assessment the gate does not fail has an unclear must-have row with no question on it.
        if decided != NOT_A_MATCH:
            assert resume_gate.unasked_rows(payload["matrix"], payload.get("structured_questions", [])) == [], (seed, number)
            # ...and a v8 one with such a row is stored as it always was.
            v8_unasked_stored += not resume_gate.uses_v9_rules(payload["matrix"]) and any(
                row["status"] == "unclear" and row.get("class") in ("hard", "askable", None)
                and row["requirement"] not in {question["requirement"] for question in payload.get("structured_questions", [])}
                for row in payload["matrix"]
            )
        # The validator, the pipeline's label and the gate count the same holding questions.
        holding = weights.blocking_question_count(payload["matrix"], payload.get("structured_questions", []))
        assert (holding > 0) == (decided == HOLD_QUESTION), (seed, number)
        accepted[(stored, decided)] += 1
    # Every consistent pair was really met, and a fair share of the answers was refused (the generator is not tame).
    assert all(count > 5 for count in accepted.values()), accepted
    assert refused > 200, refused
    # The N3b case was really met: answers refused for it, rows asked about in code, and v8 answers it leaves alone.
    assert unasked_refused > 100 and asked_by_code >= unasked_refused and v8_unasked_stored > 20, (unasked_refused, asked_by_code, v8_unasked_stored)


def test_every_combination_of_two_rows_is_settled_and_gated_alike() -> None:
    classes = ["hard", "askable", "list_item", "nice_to_have"]
    statuses = ["met", "unmet", "unclear"]
    for v9 in (True, False):
        for (class_a, status_a), (class_b, status_b) in itertools.product(itertools.product(classes, statuses), repeat=2):
            rows = [_row("first", class_a, status_a, v9=v9), _row("second", class_b, status_b, v9=v9)]
            every = [_question(str(row["requirement"])) for row in rows if row["status"] == "unclear"]
            # Every unclear row asked about, none, and (N3b) each one alone.
            for questions, said in itertools.product([every, [], *([question] for question in every)], (MATCHED, PENDING)):
                stored = weights.settled_verdict(said, rows, questions)
                decided = gate(rows, questions, stored).decision
                if decided == NOT_A_MATCH:
                    assert any(row["class"] == "hard" and row["status"] == "unmet" for row in rows)
                    continue  # the validator refuses a matched or pending verdict beside a hard gap: never stored
                unasked = resume_gate.unasked_rows(rows, questions)
                if questions or unasked:
                    assert (stored, decided) in _CONSISTENT, (v9, rows, said, stored, decided)
                if unasked:  # v9 only: an unclear must-have row holds, asked about or not
                    assert v9 and (stored, decided) == (PENDING, HOLD_QUESTION), (rows, questions, said)
                elif not v9 and not questions:
                    assert stored == said and decided == SUGGEST, (rows, said)  # a v8 matrix with no question: as it came


# --- the job state the gate gives (OD1) -------------------------------------------------------------------------------


def _fact(verdict: str | None, gate_decision: str | None) -> job_state.AssessmentFact:
    return job_state.AssessmentFact(at="2026-10-05T10:00:00Z", verdict=verdict, gate=gate_decision)


def test_a_matched_assessment_the_gate_holds_for_a_gap_is_served_as_has_gap() -> None:
    state = job_state.derive_job_state(assessments=(_fact(MATCHED, "hold_unmet"),))
    assert state.state == job_state.HAS_GAP == "has_gap" and state.next_events == ("applied",)
    assert state.to_json() == {"state": "has_gap", "since": "2026-10-05T10:00:00Z", "next_events": ["applied"]}
    # Only that decision, and only beside a matched verdict; an assessment that stores no gate is what it was.
    assert job_state.derive_job_state(assessments=(_fact(MATCHED, "suggest"),)).state == "matched"
    assert job_state.derive_job_state(assessments=(_fact(MATCHED, None),)).state == "matched"
    assert job_state.derive_job_state(assessments=(_fact(PENDING, "hold_question"),)).state == "needs_answers"
    assert job_state.derive_job_state(assessments=(_fact(FAILED, "not_a_match"),)).state == "not_a_match"
    # The precedence above it is unchanged: a stored resume (a draft the user asked for) and an application still win.
    assert job_state.derive_job_state(assessments=(_fact(MATCHED, "hold_unmet"),), has_tailored_resume=True).state == "tailored"
    applied = [{"event_id": "e1", "event_kind": "applied", "occurred_at": "2026-10-06T00:00:00Z"}]
    assert job_state.derive_job_state(assessments=(_fact(MATCHED, "hold_unmet"),), events=applied).state == "applied"


def test_has_gap_is_served_and_accepted_but_not_yet_among_the_states_the_page_has_words_for() -> None:
    # The switch for N6: move HAS_GAP from GATE_STATES into JOB_STATES (after MATCHED) with the page's label.
    assert job_state.GATE_STATES == ("has_gap",) and "has_gap" not in job_state.JOB_STATES
    assert job_state.next_events("has_gap") == ("applied",)
    job_state.check_transition(events=[], event_kind="applied")
    # The Jobs list filter and the terminal's words know it.
    assert "has_gap" in posting_search.STATES
    from gigai.scout import scout_new

    assert scout_new._VERDICT_WORDS["has_gap"] == "Has a gap"
    assert scout_new._VERDICT_ORDER.get("has_gap", 2) == 2  # after Needs your answers, before a weak fit: the read model's own order
