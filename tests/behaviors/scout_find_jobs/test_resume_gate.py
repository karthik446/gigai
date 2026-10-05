"""0.1.11 N3 (SPEC 1.5): the gate. One pure function decides whether a resume is suggested, and the verdict asks it.

One test per row of the accepted table, the two defaults (OD1: an unmet ``askable`` row of a v9 assessment holds the
resume and the job reads "Has a gap"; OD2: an optional row of a v9 assessment never holds the verdict), what stays
exactly as it was for every matrix made before 0.1.11, and the property the design rests on: over generated
matrices, the verdict that is stored and the gate never disagree.
"""

from __future__ import annotations

import itertools
import random

import pytest

from gigai.scout import assessment_core, posting_search, proposals, resume_gate
from gigai.scout import requirement_weights as weights
from gigai.scout.find_jobs import job_state
from gigai.scout.find_jobs.assess_contracts import GateReason, GateRecord
from gigai.scout.find_jobs.contracts import FindJobsContractError, MatrixStatus, RequirementClass, RequirementMatrixRow
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
    rows = [_row("US work authorization", "hard", "unmet", id="req-bbbbbb"), _row("Kafka", "askable", "unmet")]
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
    for number, rows, questions, said in _generated(seed):
        answer = {"verdict": said, "matrix": rows, "questions": questions, "suggestions": [], "not_a_match_reason": "x" if said == FAILED else None}
        payload = assessment_core._normalize_assessment_payload(answer)
        try:
            proposals.validate_assessment_bounds(payload)
        except FindJobsContractError:
            refused += 1  # an answer the product refuses is never stored: nothing to agree with
            continue
        stored = payload["verdict"]
        decided = gate(payload["matrix"], payload.get("structured_questions", []), stored).decision
        assert (stored, decided) in _CONSISTENT, (seed, number, said, stored, decided, rows, questions)
        if decided == HOLD_UNMET:
            assert resume_gate.uses_v9_rules(payload["matrix"]), (seed, number)
        # The validator, the pipeline's label and the gate count the same holding questions.
        holding = weights.blocking_question_count(payload["matrix"], payload.get("structured_questions", []))
        assert (holding > 0) == (decided == HOLD_QUESTION), (seed, number)
        accepted[(stored, decided)] += 1
    # Every consistent pair was really met, and a fair share of the answers was refused (the generator is not tame).
    assert all(count > 5 for count in accepted.values()), accepted
    assert refused > 200, refused


def test_every_combination_of_two_rows_is_settled_and_gated_alike() -> None:
    classes = ["hard", "askable", "list_item", "nice_to_have"]
    statuses = ["met", "unmet", "unclear"]
    for v9 in (True, False):
        for (class_a, status_a), (class_b, status_b) in itertools.product(itertools.product(classes, statuses), repeat=2):
            rows = [_row("first", class_a, status_a, v9=v9), _row("second", class_b, status_b, v9=v9)]
            questions = [_question(str(row["requirement"])) for row in rows if row["status"] == "unclear"]
            for said in (MATCHED, PENDING):
                stored = weights.settled_verdict(said, rows, questions)
                decided = gate(rows, questions, stored).decision
                if decided == NOT_A_MATCH:
                    assert any(row["class"] == "hard" and row["status"] == "unmet" for row in rows)
                    continue  # the validator refuses a matched or pending verdict beside a hard gap: never stored
                if questions:
                    assert (stored, decided) in _CONSISTENT, (v9, rows, said, stored, decided)


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
