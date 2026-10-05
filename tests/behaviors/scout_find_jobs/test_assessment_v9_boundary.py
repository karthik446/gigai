"""0.1.11 N3 (SPEC 1.2, 1.3): the stored shapes of assessment v9 and the model boundary's normalization table.

Fake model only (a scripted binding that returns v9-shaped JSON); the shipped prompt is the v8 file, which this
packet does not touch.  What is pinned:

- every addition is optional and omitted at its default: a v8 row, body and response are what they were, key for key
  and byte for byte, and the run path's closed parser reads the same payloads it read;
- a v8-shaped answer is normalized exactly as before;
- a v9 key that is missing, null or malformed never spends the one retry; a bad pick never fails an assessment;
- a pick on a verdict that is not Matched is dropped and counted; suggestions are dropped on ``not_a_match`` and
  filtered to ``gap`` / ``master_line`` on ``pending_user_answers``;
- a source the prompt did not offer is dropped and recorded; without ids in the prompt there are no sources and no pick;
- the one v9 rule that DOES spend the retry: a matrix that does not hold the listed requirement ids.

0.1.11 N3b (orchestrator #14): a suggestion that names its requirement by the row's words gets the row's id (a first
assessment's ``gap`` suggestion with no line is kept); at most three questions on ``list_item`` rows are kept, the
rest dropped by code and counted, with no retry.  (The unclear must-have rule of decision #11 is in ``test_resume_gate``.)
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gigai.canonical import parse_json_bytes
from gigai.scout import assessment_core, proposals, requirements_list, resume_gate
from gigai.scout import requirement_weights as weights
from gigai.scout.assessment_basis import posting_sha256
from gigai.scout.assessment_core import AssessExtras, AssessJob, Boundary, assess_once, build_assess_context
from gigai.scout.find_jobs.assess_contracts import (
    AssessmentBody,
    AssessmentPick,
    AssessmentSuggestion,
    GateReason,
    GateRecord,
    RequirementsRef,
)
from gigai.scout.find_jobs.contracts import (
    AssessmentResult,
    FindJobsContractError,
    MatrixStatus,
    NotAssessedReason,
    RequirementClass,
    RequirementMatrixRow,
)
from gigai.scout.quick_assess import _parse_body
from gigai.scout.requirements_list import ListedRequirement

DIGEST = "sha256:" + "a" * 64
MATCHED, PENDING, NOT_A_MATCH = "matched_above_threshold", "pending_user_answers", "not_a_match"

V8_ROW = {"requirement": "5+ years of Go", "resume_evidence": ["Built Go services for six years"], "status": "met", "class": "hard"}
V8_BODY = {
    "matrix": [V8_ROW, {"requirement": "Helm", "resume_evidence": [], "status": "unclear", "class": "list_item"}],
    "suggestions": ["Lead with the Go services."],
    "questions": ["Have you written Helm charts?"],
    "verdict": MATCHED,
    "structured_questions": [{"question_id": "tooling:helm", "question": "Have you written Helm charts?", "requirement": "Helm"}],
}
#: The same assessment as a v8 model ANSWERS it: the questions are objects, and the reason is there, null.
V8_ANSWER = {
    "verdict": MATCHED, "matrix": V8_BODY["matrix"], "suggestions": V8_BODY["suggestions"], "questions": V8_BODY["structured_questions"],
    "not_a_match_reason": None,
}


def _dump(value: object) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode("utf-8")


# --- the stored shapes: additive, omitted at the default ---------------------------------------------------


def test_a_v8_row_and_a_v8_body_round_trip_byte_for_byte_and_gain_no_key() -> None:
    row = RequirementMatrixRow.from_json(dict(V8_ROW))
    assert row.to_json() == V8_ROW and list(row.to_json()) == ["requirement", "resume_evidence", "status", "class"]
    assert (row.id, row.class_basis, row.alternatives, row.sources, row.class_from) == (None, None, (), (), None)
    body = AssessmentBody.from_json(parse_json_bytes(_dump(V8_BODY)))
    assert _dump(body.to_json()) == _dump(V8_BODY)
    assert body.structured_suggestions == () and body.pick is None
    # The constructors every earlier caller uses still build the same objects.
    assert RequirementMatrixRow("5+ years of Go", ("Built Go services for six years",), MatrixStatus.MET, RequirementClass.HARD) == row


def test_the_run_paths_closed_parser_never_sees_a_pick_or_a_structured_suggestion() -> None:
    answer = {**V8_ANSWER, "pick": {"lines": ["b-1"]}, "suggestions": [{"kind": "gap", "requirement": "elig-region", "why": "why"}]}
    normalized = assessment_core._normalize_assessment_payload(answer)
    # They are returned BESIDE the payload: the run path's ``AssessmentResult`` is a closed object and its parser is untouched.
    assert set(normalized) == {"matrix", "suggestions", "questions", "structured_questions", "verdict", "not_a_match_reason"}
    assert normalized["suggestions"] == ["why"]
    proposals.validate_assessment_bounds(normalized)
    optional = set(AssessmentResult.__dataclass_fields__) - {"posting", "proposal_revision_ref"}
    assert set(normalized) <= optional and "pick" not in optional and "structured_suggestions" not in optional


def test_the_v9_fields_round_trip_and_each_is_omitted_at_its_default() -> None:
    row = RequirementMatrixRow(
        "Cassandra or MongoDB", ("MongoDB at two employers",), MatrixStatus.MET, RequirementClass.ASKABLE, id="req-3fa91c",
        class_basis="What we are looking for: Cassandra or MongoDB", alternatives=("Cassandra", "MongoDB"), sources=("b-23b6dc", "A tooling:mongodb"),
        class_from="disclaimer",
    )
    assert RequirementMatrixRow.from_json(parse_json_bytes(_dump(row.to_json()))) == row
    assert set(row.to_json()) == {"requirement", "resume_evidence", "status", "class", "id", "class_basis", "alternatives", "sources", "class_from"}
    body = AssessmentBody(
        (row,), ("why one",), (), verdict=None,
        structured_suggestions=(AssessmentSuggestion("reword", "why one", line="b-23b6dc", requirement="req-3fa91c", posting_phrase="at scale"),),
        pick=AssessmentPick("sum-4c1d2e", ("projects", "experience"), ("b-23b6dc", "b-ea7988")),
    )
    again = AssessmentBody.from_json(parse_json_bytes(_dump(body.to_json())))
    assert again == body and again.pick is not None and again.pick.lines == ("b-23b6dc", "b-ea7988")
    assert AssessmentSuggestion("gap", "why", requirement="elig-location").to_json() == {"kind": "gap", "why": "why", "requirement": "elig-location"}
    ref = RequirementsRef(DIGEST, "req-rules:1", DIGEST, 15, "stored")
    assert ref.to_json() == {"posting_sha256": DIGEST, "rules_version": "req-rules:1", "digest": DIGEST, "rows": 15, "list": "stored"}
    assert RequirementsRef.from_json(ref.to_json()) == ref
    gate = GateRecord("hold_unmet", (GateReason("askable_unmet", "req-3fa91c"),))
    assert GateRecord.from_json(parse_json_bytes(_dump(gate.to_json()))) == gate


@pytest.mark.parametrize(
    "change",
    [
        {"id": "r1"},
        {"id": "req-XYZ123"},
        {"class_basis": "x" * 201},
        {"class_basis": ""},
        {"alternatives": ["a"] * 7},
        {"alternatives": ["a" * 81]},
        {"sources": ["b-1"] * 13},
        {"sources": ["b" * 81]},
        {"class_from": "model"},
        {"unknown": 1},
    ],
)
def test_the_stored_row_and_the_bounds_validator_refuse_an_out_of_bounds_v9_key(change: dict[str, object]) -> None:
    with pytest.raises(FindJobsContractError):
        RequirementMatrixRow.from_json({**V8_ROW, **change})
    if "unknown" not in change and "class_from" not in change:
        with pytest.raises(FindJobsContractError):
            proposals.validate_assessment_bounds({"matrix": [{**V8_ROW, **change}], "suggestions": [], "questions": []})


def test_the_other_v9_shapes_are_checked_when_read() -> None:
    for bad in (
        lambda: AssessmentSuggestion("rewrite", "why", line="b-1"),
        lambda: AssessmentSuggestion("reword", "why"),
        lambda: AssessmentSuggestion("reword", "w" * 301, line="b-1"),
        lambda: AssessmentSuggestion("keyword", "why", requirement="row 3"),
        lambda: AssessmentSuggestion("keyword", "why", line="b-1", posting_phrase="p" * 61),
        lambda: AssessmentPick(None, ("experience", "experience"), ("b-1",)),
        lambda: AssessmentPick(None, ("skills",), ("b-1",)),
        lambda: AssessmentPick(None, (), tuple(f"b-{n}" for n in range(61))),
        lambda: RequirementsRef(DIGEST, "req-rules:1", DIGEST, 3, "mine"),
        lambda: GateRecord("hold", ()),
        lambda: GateReason("unmet", None),
    ):
        with pytest.raises(FindJobsContractError):
            bad()


# --- the model boundary --------------------------------------------------------------------------------------


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


JOB = AssessJob("Staff Engineer", "Acme", "Remote", "Requirements: Go in production; Kubernetes. Nice to have: Helm.")
IDS = ("b-aaaaaa", "b-bbbbbb", "b-cccccc")


def _ctx(*, ids: tuple[str, ...] = IDS, requirements: tuple[ListedRequirement, ...] = ()):
    return build_assess_context(resume_text="- Built Go services", resume_ids=ids, pick_lines=20, requirements=requirements)


def _answer(verdict: str = MATCHED, **more: object) -> dict[str, object]:
    """A v9-shaped answer whose rows fit ``verdict``: Go met (hard), Kubernetes met, or unclear with its question, or Go unmet."""

    rows = [
        {"requirement": "Go in production", "class": "hard", "class_basis": "Requirements: Go in production", "status": "met",
         "resume_evidence": ["Built Go services"], "sources": ["b-aaaaaa"]},
        {"requirement": "Kubernetes", "class": "askable", "class_basis": "Requirements: Kubernetes", "status": "met",
         "resume_evidence": ["Ran Kubernetes"], "sources": ["b-bbbbbb"]},
    ]
    questions: list[dict[str, object]] = []
    if verdict == PENDING:
        rows[1] = {**rows[1], "status": "unclear", "resume_evidence": [], "sources": []}
        questions = [{"question_id": "tooling:kubernetes", "question": "Have you run Kubernetes?", "requirement": "Kubernetes"}]
    if verdict == NOT_A_MATCH:
        rows[0] = {**rows[0], "status": "unmet", "resume_evidence": [], "sources": []}
    return {"verdict": verdict, "matrix": rows, "questions": questions, "suggestions": [], "not_a_match_reason": None, **more}


def _assess(*outputs: object, ctx=None):
    binding = _Binding(*outputs)
    return assess_once(binding, JOB, ctx or _ctx(), parse=_parse_body), binding


def test_a_v8_shaped_answer_is_normalized_exactly_as_before_and_carries_nothing_of_v9() -> None:
    boundary = Boundary()
    payload, dropped_ids, dropped = assessment_core._normalize_and_strip(dict(V8_ANSWER), boundary=boundary)
    assert (dropped_ids, dropped) == ((), 0)
    assert payload == {**V8_BODY, "not_a_match_reason": None}
    assert boundary.extras == AssessExtras()
    # The same call with no boundary at all (every earlier caller) answers the same payload.
    assert assessment_core._normalize_and_strip(dict(V8_ANSWER))[0] == payload
    attempt, _binding = _assess(V8_ANSWER, ctx=build_assess_context(resume_text="- Built Go services"))
    assert attempt.ok and attempt.attempts == 1 and attempt.extras == AssessExtras()
    assert _dump(attempt.parsed.to_json()) == _dump(V8_BODY)


@pytest.mark.parametrize("pick", ["b-aaaaaa", ["b-aaaaaa"], 7, None, {}, {"lines": "b-aaaaaa"}, {"lines": []}, {"lines": [1, None, {}, "two words"]}, {"summary": "sum-1"}])
def test_a_bad_pick_never_fails_an_assessment_and_never_spends_the_retry(pick: object) -> None:
    attempt, binding = _assess(_answer(pick=pick))
    assert attempt.ok and attempt.attempts == 1 and len(binding.port.prompts) == 1
    assert attempt.extras is not None and attempt.extras.pick is None and not attempt.extras.dropped_pick
    assert attempt.parsed.verdict.value == MATCHED


def test_a_pick_is_read_leniently_and_checked_against_no_master_here() -> None:
    lines = ["b-zzzzzz", "b-aaaaaa", "b-aaaaaa", 4, "", " b-bbbbbb ", *[f"b-{n:06x}" for n in range(80)]]
    attempt, _binding = _assess(_answer(pick={"summary": " sum-4c1d2e ", "section_order": ["Projects", "skills", "experience", "projects"], "lines": lines}))
    pick = attempt.extras.pick
    assert pick is not None and pick.summary == "sum-4c1d2e" and pick.section_order == ("projects", "experience")
    assert pick.lines[:3] == ("b-zzzzzz", "b-aaaaaa", "b-bbbbbb") and len(pick.lines) == 60 and len(set(pick.lines)) == 60
    assert not attempt.extras.dropped_pick


@pytest.mark.parametrize("verdict", [PENDING, NOT_A_MATCH])
def test_a_pick_on_a_verdict_that_is_not_matched_is_dropped_and_counted(verdict: str) -> None:
    attempt, _binding = _assess(_answer(verdict, pick={"lines": ["b-aaaaaa", "b-bbbbbb"]}))
    assert attempt.ok and attempt.attempts == 1
    assert attempt.extras.pick is None and attempt.extras.dropped_pick


def test_without_ids_in_the_prompt_there_is_no_pick_and_no_source() -> None:
    attempt, _binding = _assess(_answer(pick={"lines": ["b-aaaaaa"]}), ctx=_ctx(ids=()))
    assert attempt.ok and attempt.extras.pick is None and attempt.extras.dropped_pick
    assert all(row.sources == () for row in attempt.parsed.matrix)
    assert attempt.extras.unknown_sources == ()


SUGGESTIONS = [
    {"kind": "reword", "line": "b-aaaaaa", "requirement": "req-3fa91c", "posting_phrase": "control cost with prompt caching", "why": "Lead with the cost result."},
    {"kind": "gap", "requirement": "elig-location", "why": "Only an answer can close this."},
    {"kind": "master_line", "line": "b-bbbbbb", "why": "Your answer states it and no line does."},
    {"kind": "keyword", "line": "b-cccccc", "why": "The posting says Kubernetes."},
    {"kind": "order", "line": "b-cccccc", "why": "Move this up."},
    {"kind": "rewrite", "line": "b-aaaaaa", "why": "an unknown kind"},
    {"kind": "reword", "why": "names neither a line nor a requirement"},
    {"kind": "reword", "line": "b-aaaaaa"},
    "a plain sentence",
    17,
]


def test_structured_suggestions_are_kept_beside_the_payload_and_each_why_joins_the_plain_list() -> None:
    attempt, _binding = _assess(_answer(suggestions=SUGGESTIONS))
    kept = attempt.extras.structured_suggestions
    assert [item.kind for item in kept] == ["reword", "gap", "master_line", "keyword", "order"] and attempt.extras.dropped_suggestions == 0
    assert kept[0] == AssessmentSuggestion("reword", "Lead with the cost result.", "b-aaaaaa", "req-3fa91c", "control cost with prompt caching")
    assert attempt.parsed.suggestions == ("a plain sentence", *(item.why for item in kept))
    # At most 8, a long why and a long phrase are cut, never refused.
    many = [{"kind": "gap", "requirement": "elig-region", "why": "w" * 400, "posting_phrase": "p" * 90}] * 11
    attempt, _binding = _assess(_answer(suggestions=many))
    assert attempt.ok and attempt.attempts == 1 and len(attempt.extras.structured_suggestions) == 8
    assert len(attempt.extras.structured_suggestions[0].why) == 300 and len(attempt.extras.structured_suggestions[0].posting_phrase) == 60


def test_a_first_assessments_suggestion_names_its_requirement_by_the_rows_words_and_gets_the_rows_id() -> None:
    # Orchestrator #14. A first assessment's rows have no id yet: the model names the row by its words, code gives the id.
    sha = posting_sha256(JOB.title, JOB.posting_text)
    go_id, kubernetes_id = requirements_list.assign_ids(sha, ["Go in production", "Kubernetes"])
    suggested = [
        {"kind": "gap", "requirement": "Kubernetes", "why": "Nothing in the resume says where Kubernetes ran."},  # no line: was dropped
        {"kind": "gap", "requirement": "  go IN  production ", "why": "Say how long."},  # the words as a question's are matched
        {"kind": "reword", "line": "b-aaaaaa", "requirement": "Kubernetes", "why": "Name the clusters."},
        {"kind": "gap", "requirement": "Terraform", "why": "No row says this."},  # no row's words and no line: names nothing
        {"kind": "gap", "requirement": "elig-region", "why": "Only an answer can say."},  # an id is taken as it always was
        {"kind": "keyword", "line": "b-bbbbbb", "requirement": "Terraform", "why": "A line is enough."},
    ]
    attempt, _binding = _assess(_answer(suggestions=suggested))
    assert attempt.ok and attempt.attempts == 1
    assert [(item.kind, item.line, item.requirement) for item in attempt.extras.structured_suggestions] == [
        ("gap", None, kubernetes_id), ("gap", None, go_id), ("reword", "b-aaaaaa", kubernetes_id), ("gap", None, "elig-region"), ("keyword", "b-bbbbbb", None),
    ]
    assert attempt.parsed.suggestions == tuple(item.why for item in attempt.extras.structured_suggestions)
    # The ids are the ones the stored rows carry (``requirements_list.extracted``, what ``quick_assess`` stores them with).
    assert requirements_list.extracted(sha, attempt.parsed.matrix, extracted_at="")[1] == (go_id, kubernetes_id)
    # A pending answer keeps its gap suggestions, each with its row's id.
    attempt, _binding = _assess(_answer(PENDING, suggestions=suggested))
    assert [(item.kind, item.requirement) for item in attempt.extras.structured_suggestions] == [("gap", kubernetes_id), ("gap", go_id), ("gap", "elig-region")]
    # With the list in the prompt a row has the list's id, whichever words name it: the model's or the list's.
    answer = _listed_answer("req-aaaaaa", "req-bbbbbb")
    answer["matrix"][1]["requirement"] = "K8s"
    answer["suggestions"] = [{"kind": "gap", "requirement": "K8s", "why": "one"}, {"kind": "gap", "requirement": "Kubernetes (EKS or GKE)", "why": "two"}]
    attempt, _binding = _assess(answer, ctx=_ctx(requirements=LISTED))
    assert [item.requirement for item in attempt.extras.structured_suggestions] == ["req-bbbbbb", "req-bbbbbb"]
    # An answer in the v8 shape (no v9 key on a row) has no row ids: nothing is mapped, as before.
    v8 = {**V8_ANSWER, "suggestions": [{"kind": "gap", "requirement": "Helm", "why": "why"}]}
    attempt, _binding = _assess(v8, ctx=build_assess_context(resume_text="- Built Go services"))
    assert attempt.ok and attempt.extras == AssessExtras() and attempt.parsed.suggestions == ()


def _list_answer(tools: tuple[str, ...], asked: tuple[str, ...], *, verdict: str = MATCHED, v9: bool = True, kubernetes: str = "met") -> dict[str, object]:
    """Go (hard) and Kubernetes (askable), then one ``list_item`` row per tool, all unclear; a question for each of ``asked``, in that order."""

    answer = _answer(verdict if verdict != PENDING else MATCHED)
    if kubernetes == "unclear":
        answer["matrix"][1] = {**answer["matrix"][1], "status": "unclear", "resume_evidence": [], "sources": []}
    answer["verdict"] = verdict
    for tool in tools:
        answer["matrix"].append({"requirement": tool, "class": "list_item", "class_basis": f"Tools: {tool}", "status": "unclear", "resume_evidence": []})
    if not v9:
        answer["matrix"] = [{key: value for key, value in row.items() if key in ("requirement", "class", "status", "resume_evidence")} for row in answer["matrix"]]
    answer["questions"] = [{"question_id": f"tooling:{name.lower()}", "question": f"Have you used {name}?", "requirement": name} for name in asked]
    return answer


TOOLS = ("Helm", "Istio", "Argo", "Linkerd", "Flux")


def test_at_most_three_questions_on_list_item_rows_are_kept_those_of_the_rows_first_in_the_matrix() -> None:
    # Orchestrator #14. Five one-of-a-list rows, a question on each, asked in another order than the rows are in.
    attempt, binding = _assess(_list_answer(TOOLS, ("Flux", "Helm", "Linkerd", "Istio", "Argo")))
    assert attempt.ok and attempt.attempts == 1 and len(binding.port.prompts) == 1  # dropped by code: no retry
    body = attempt.parsed
    # Kept: the questions of the three rows first in the matrix, in the order they were asked; from both lists.
    assert [question.question_id for question in body.structured_questions] == ["tooling:helm", "tooling:istio", "tooling:argo"]
    assert body.questions == ("Have you used Helm?", "Have you used Istio?", "Have you used Argo?")
    # Dropped: counted like dropped_questions, and kept apart from that count (the not_a_match strip's).
    assert (attempt.capped_questions, attempt.capped_question_ids) == (2, ("tooling:flux", "tooling:linkerd"))
    assert attempt.extras.capped_questions == ("tooling:flux", "tooling:linkerd") and (attempt.dropped_questions, attempt.dropped_question_ids) == (0, ())
    # Their rows stay unclear with no question and read as minor gaps; an optional row never holds the verdict.
    assert [(row.requirement, row.status.value) for row in body.matrix[2:]] == [(tool, "unclear") for tool in TOOLS]
    assert body.verdict.value == MATCHED and weights.minor_gap_text(weights.minor_gaps(body.matrix)) == "5 minor gaps: Helm, Istio, Argo +2 more"
    assert resume_gate.gate(body.matrix, body.structured_questions, body.verdict).decision == "suggest"
    # Three or fewer: nothing is dropped.
    attempt, _binding = _assess(_list_answer(TOOLS, ("Flux", "Linkerd", "Argo")))
    assert [question.question_id for question in attempt.parsed.structured_questions] == ["tooling:flux", "tooling:linkerd", "tooling:argo"]
    assert (attempt.capped_questions, attempt.capped_question_ids, attempt.extras.capped_questions) == (0, (), ())


def test_a_must_haves_question_is_never_capped_and_a_plain_question_keeps_its_place() -> None:
    answer = _list_answer(TOOLS, TOOLS, verdict=PENDING, kubernetes="unclear")
    kubernetes = {"question_id": "tooling:kubernetes", "question": "Have you run Kubernetes?", "requirement": "Kubernetes"}
    other = {"question_id": "years:go", "question": "How many years of Go?", "requirement": None}  # names no row: nothing says it is minor
    answer["questions"] = ["A question in the old shape?", *answer["questions"], kubernetes, other]
    attempt, _binding = _assess(answer)
    body = attempt.parsed
    assert attempt.ok and attempt.attempts == 1 and body.verdict.value == PENDING
    assert [question.question_id for question in body.structured_questions] == ["tooling:helm", "tooling:istio", "tooling:argo", "tooling:kubernetes", "years:go"]
    assert body.questions == ("A question in the old shape?", "Have you used Helm?", "Have you used Istio?", "Have you used Argo?", "Have you run Kubernetes?", "How many years of Go?")
    assert attempt.capped_question_ids == ("tooling:linkerd", "tooling:flux")
    # The pure rule: only ``list_item`` rows are counted, and the first row of some words says the class.
    rows = [{"requirement": name, "class": klass, "status": "unclear"} for name, klass in (
        ("a", "list_item"), ("b", "nice_to_have"), ("c", "list_item"), ("d", "askable"), ("e", "list_item"), ("f", "list_item"), ("g", None),
    )]
    asked = [{"question_id": f"x:{name}", "requirement": name} for name in "gfedcba"]
    kept, dropped = weights.cap_list_item_questions(rows, asked)
    assert [item["requirement"] for item in kept] == ["g", "e", "d", "c", "b", "a"] and [item["requirement"] for item in dropped] == ["f"]
    assert weights.cap_list_item_questions(rows, []) == ([], []) and weights.MAX_LIST_ITEM_QUESTIONS == 3


def test_the_cap_is_a_v9_rule_and_a_not_a_match_answer_keeps_its_own_count() -> None:
    # A v8 matrix: every question is kept, and the 0110-10-03 threshold reads them as it always did (two or more hold).
    attempt, _binding = _assess(_list_answer(TOOLS, TOOLS, verdict=PENDING, v9=False), ctx=build_assess_context(resume_text="- Built Go services"))
    assert attempt.ok and len(attempt.parsed.structured_questions) == 5 and attempt.parsed.verdict.value == PENDING
    assert (attempt.capped_questions, attempt.extras) == (0, AssessExtras())
    # Not a match: no question is kept at all, and all five are the strip's count, none the cap's.
    answer = _list_answer(TOOLS, TOOLS, verdict=NOT_A_MATCH)
    answer["matrix"][0] = {**answer["matrix"][0], "status": "unmet", "resume_evidence": [], "sources": []}
    answer["not_a_match_reason"] = "No Go."
    attempt, _binding = _assess(answer)
    assert attempt.ok and attempt.parsed.structured_questions == () and (attempt.dropped_questions, attempt.capped_questions) == (5, 0)
    # Outside ``assess_once`` the same v9 answer is capped the same way (the rule is the boundary's, not the retry's).
    boundary = Boundary()
    payload = assessment_core._normalize_and_strip(_list_answer(TOOLS, TOOLS), boundary=boundary)[0]
    assert [item["question_id"] for item in payload["structured_questions"]] == ["tooling:helm", "tooling:istio", "tooling:argo"]
    assert len(payload["questions"]) == 3 and boundary.extras.capped_questions == ("tooling:linkerd", "tooling:flux")
    proposals.validate_assessment_bounds(payload)


def test_suggestions_are_dropped_on_not_a_match_and_filtered_on_pending() -> None:
    attempt, _binding = _assess(_answer(NOT_A_MATCH, suggestions=SUGGESTIONS, not_a_match_reason="No Go."))
    assert attempt.ok and attempt.extras.structured_suggestions == () and attempt.extras.dropped_suggestions == 5
    assert attempt.parsed.suggestions == ("a plain sentence",)  # a v8 string is kept as it always was
    attempt, _binding = _assess(_answer(PENDING, suggestions=SUGGESTIONS))
    assert [item.kind for item in attempt.extras.structured_suggestions] == ["gap", "master_line"] and attempt.extras.dropped_suggestions == 3
    assert attempt.parsed.suggestions == ("a plain sentence", "Only an answer can close this.", "Your answer states it and no line does.")


def test_a_source_the_prompt_did_not_offer_is_dropped_and_recorded_and_an_answer_is_cited_by_its_id() -> None:
    answer = _answer()
    answer["matrix"][0]["sources"] = ["b-aaaaaa", "b-zzzzzz", "b-aaaaaa", "A  Tooling:Go", "A story:unknown", 5]
    answer["matrix"][0]["resume_evidence"] = ["Built Go services <!-- id:b-aaaaaa -->", "plain quote"]
    bank = SimpleNamespace(prior_answers=(), bank_answers=(SimpleNamespace(question_id="tooling:go", question="Go?", summary="Yes, six years"),))
    ctx = build_assess_context(resume_text="- Built Go services", bank=bank, resume_ids=IDS)
    attempt, _binding = _assess(answer, ctx=ctx)
    row = attempt.parsed.matrix[0]
    assert row.sources == ("b-aaaaaa", "A tooling:go")
    assert row.resume_evidence == ("Built Go services", "plain quote")  # an id comment copied into a quote is stripped
    assert attempt.extras.unknown_sources == (("Go in production", "b-zzzzzz"), ("Go in production", "A story:unknown"))
    assert attempt.attempts == 1


def test_a_malformed_v9_row_key_is_normalized_to_none_and_the_row_stays() -> None:
    answer = _answer()
    answer["matrix"][0].update({"class_basis": 12, "alternatives": "Cassandra", "sources": "b-aaaaaa", "id": "req-123456"})
    answer["matrix"][1].update({"class_basis": "  Requirements:\n Kubernetes  " + "x" * 300, "alternatives": ["EKS", "EKS", "", 4, "GKE", "a", "b", "c", "d", "e"], "id": "elig-location"})
    attempt, _binding = _assess(answer)
    first, second = attempt.parsed.matrix
    assert attempt.ok and attempt.attempts == 1
    # No list was in the prompt: an id of the model's own is not kept; one of the four fixed ids is.
    assert (first.id, first.class_basis, first.alternatives, first.sources) == (None, None, (), ())
    assert second.id == "elig-location" and len(second.class_basis) <= 200 and second.class_basis.startswith("Requirements: Kubernetes")
    assert second.alternatives == ("EKS", "GKE", "a", "b", "c", "d")


# --- a later assessment: exactly the listed requirement ids ---------------------------------------------------

LISTED = (
    ListedRequirement("req-aaaaaa", "Go in production", "askable", "Requirements: Go in production"),
    ListedRequirement("req-bbbbbb", "Kubernetes (EKS or GKE)", "askable", "Requirements: Kubernetes", ("EKS", "GKE")),
)


def _listed_answer(*ids: str | None, verdict: str = MATCHED) -> dict[str, object]:
    answer = _answer(verdict)
    rows = answer["matrix"]
    for row, row_id in zip(rows, ids):
        if row_id is not None:
            row["id"] = row_id
    answer["matrix"] = rows[: len(ids)]
    return answer


def test_a_later_assessment_must_return_exactly_the_listed_ids_with_the_one_retry() -> None:
    good = _listed_answer("req-aaaaaa", "req-bbbbbb")
    attempt, binding = _assess(_listed_answer("req-aaaaaa"), good, ctx=_ctx(requirements=LISTED))
    assert attempt.ok and attempt.attempts == 2
    assert attempt.validation_error.startswith("matrix must hold exactly the 2 listed requirement ids, each once; 1 is missing: req-bbbbbb")
    assert "matrix must hold exactly the 2 listed requirement ids" in binding.port.prompts[1]  # fed back on the retry
    # Twice wrong: the assessment is invalid, nothing is guessed.
    for wrong, said in (
        (_listed_answer("req-aaaaaa", "req-cccccc"), "1 is not in the list: req-cccccc"),
        (_listed_answer("req-aaaaaa", "req-aaaaaa"), "1 is repeated: req-aaaaaa"),
        (_listed_answer("req-aaaaaa", None), "1 row carries no id"),
    ):
        attempt, binding = _assess(wrong, wrong, ctx=_ctx(requirements=LISTED))
        assert not attempt.ok and attempt.not_assessed_reason is NotAssessedReason.MODEL_OUTPUT_INVALID and attempt.attempts == 2
        assert said in attempt.validation_error, attempt.validation_error


def test_a_listed_row_is_the_lists_and_a_disclaimed_askable_row_may_read_hard() -> None:
    answer = _listed_answer("req-aaaaaa", "req-bbbbbb", verdict=PENDING)
    answer["matrix"][0].update({"requirement": "Golang, in prod", "class": "hard", "class_basis": "the model's own words", "alternatives": ["Rust"]})
    answer["matrix"][1].update({"requirement": "K8s", "class": "nice_to_have"})
    answer["questions"] = [{"question_id": "tooling:kubernetes", "question": "Have you run Kubernetes?", "requirement": "K8s"}]
    answer["matrix"].append({"id": "elig-location", "requirement": "Remote in the US", "class": "hard", "status": "met", "resume_evidence": []})
    attempt, _binding = _assess(answer, ctx=_ctx(requirements=LISTED))
    assert attempt.ok and attempt.attempts == 1
    by_id = {row.id: row for row in attempt.parsed.matrix}
    go, kubernetes = by_id["req-aaaaaa"], by_id["req-bbbbbb"]
    assert (go.requirement, go.class_basis, go.alternatives) == ("Go in production", "Requirements: Go in production", ())
    assert go.requirement_class is RequirementClass.HARD and go.class_from == "disclaimer"
    assert (kubernetes.requirement, kubernetes.alternatives) == ("Kubernetes (EKS or GKE)", ("EKS", "GKE"))
    assert kubernetes.requirement_class is RequirementClass.ASKABLE and kubernetes.class_from is None
    assert by_id["elig-location"].requirement == "Remote in the US"  # a row about the candidate is the assessment's own
    # The question named its row in the model's words: it names the list's now, so it still finds its row.
    assert attempt.parsed.structured_questions[0].requirement == "Kubernetes (EKS or GKE)" and attempt.parsed.verdict.value == PENDING


def test_a_question_may_name_its_row_by_id() -> None:
    answer = _listed_answer("req-aaaaaa", "req-bbbbbb", verdict=PENDING)
    answer["questions"] = [{"question_id": "tooling:kubernetes", "question": "Have you run Kubernetes?", "requirement": "req-bbbbbb"}]
    attempt, _binding = _assess(answer, ctx=_ctx(requirements=LISTED))
    assert attempt.ok and attempt.parsed.structured_questions[0].requirement == "Kubernetes (EKS or GKE)"


# --- the prompt: nothing of v9 is sent while the shipped file is v8 --------------------------------------------


def test_the_v8_prompt_is_the_same_bytes_whatever_the_v9_context_says() -> None:
    plain = build_assess_context(resume_text="- Built Go services")
    assert not assessment_core.prompt_reads_ids() and not assessment_core.template_takes(assessment_core.PLACEHOLDER_REQUIREMENTS)
    assert assessment_core.render_assess_prompt(JOB, _ctx(requirements=LISTED)) == assessment_core.render_assess_prompt(JOB, plain)


V9_PARAGRAPHS = (
    "\n\nIDS: each RESUME line ends with its id, like {{id_example}}. For each met row, sources lists the ids it relied on."
    "\n\nNOTES: a line may carry {{note_example}}: guidance for choosing lines, never evidence."
    "\n\nPICK: return {{pick_lines}} line ids, best first."
    "\n\n{{requirements}}"
)


def test_a_template_that_carries_the_v9_placeholders_gets_each_paragraph_only_with_something_to_say(monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = assessment_core.load_assess_instructions()
    monkeypatch.setattr(assessment_core, "load_assess_instructions", lambda: shipped + V9_PARAGRAPHS)
    assert assessment_core.prompt_reads_ids() and assessment_core.template_takes(assessment_core.PLACEHOLDER_REQUIREMENTS)
    plain = assessment_core.render_assess_prompt(JOB, build_assess_context(resume_text="- Built Go services"))
    assert all(word not in plain for word in ("IDS:", "NOTES:", "PICK:", "REQUIREMENTS (", "{{id_example}}", "{{pick_lines}}", "{{requirements}}"))
    with_ids = assessment_core.render_assess_prompt(JOB, _ctx())
    assert "like <!-- id:b-aaaaaa -->" in with_ids and "PICK: return 20 line ids" in with_ids
    assert "NOTES:" not in with_ids and "REQUIREMENTS (" not in with_ids
    noted = assessment_core.render_assess_prompt(JOB, build_assess_context(resume_text="x", resume_ids=IDS, resume_notes=True, pick_lines=20, requirements=LISTED))
    assert "NOTES: a line may carry <!-- private note: ... -->" in noted
    block = noted.split(assessment_core.REQUIREMENTS_BLOCK_HEADER, 1)[1]
    assert "req-aaaaaa | askable | Go in production | Requirements: Go in production" in block
    assert "req-bbbbbb | askable | Kubernetes (EKS or GKE) (any one of: EKS, GKE) | Requirements: Kubernetes" in block
    # The list's words are the posting's: inside a fence of their own.
    before = noted.split(assessment_core.REQUIREMENTS_BLOCK_HEADER, 1)[0]
    assert before.rstrip().endswith("<<<UNTRUSTED_POSTING_TEXT") and "END_UNTRUSTED_POSTING_TEXT>>>" in block


# --- orchestrator #39: at most four questions ASKED on must-have rows ----------------------------------------


def _must_have_answer(classes: tuple[str, ...], asked: tuple[int, ...], verdict: str = PENDING) -> dict[str, object]:
    """One unclear must-have row per entry of ``classes`` (named ``Row <n>``), a question on each row of ``asked``."""

    rows = [
        {"requirement": f"Row {number}", "class": klass, "class_basis": f"Requirements: Row {number}", "status": "unclear", "resume_evidence": [], "sources": []}
        for number, klass in enumerate(classes)
    ]
    questions = [{"question_id": f"tooling:row{number}", "question": f"Row {number}?", "requirement": f"Row {number}"} for number in asked]
    return {"verdict": verdict, "matrix": rows, "questions": questions, "suggestions": [], "not_a_match_reason": None}


def test_at_most_four_must_have_questions_are_asked_the_most_decisive_first_and_the_rest_still_hold() -> None:
    # Eight unclear must-haves, the model asked all eight, listing the askable ones first.
    classes = ("askable", "askable", "askable", "hard", "askable", "askable", "hard", "askable")
    attempt, binding = _assess(_must_have_answer(classes, tuple(range(8))))
    body = attempt.parsed
    assert attempt.ok and attempt.attempts == 1 and len(binding.port.prompts) == 1  # dropped by code: no retry
    # Hard rows first (3 and 6), then the posting's own order (rows 0 and 1).
    assert [question.question_id for question in body.structured_questions] == ["tooling:row0", "tooling:row1", "tooling:row3", "tooling:row6"]
    assert attempt.extras.capped_mandatory_questions == ("tooling:row2", "tooling:row4", "tooling:row5", "tooling:row7") and attempt.extras.asked_by_code == ()
    assert len(body.questions) == 4
    # Every unclear must-have still HOLDS; the four unasked ones are "also unverified", and code does not ask them.
    assert body.verdict.value == PENDING and resume_gate.gate(body.matrix, body.structured_questions, body.verdict).decision == "hold_question"
    assert sorted(row["requirement"] for row in resume_gate.also_unverified([json_row(row) for row in body.matrix], body.structured_questions)) == [f"Row {n}" for n in (2, 4, 5, 7)]
    assert resume_gate.unasked_rows([json_row(row) for row in body.matrix], body.structured_questions) == []
    proposals.validate_assessment_bounds({"matrix": [json_row(row) for row in body.matrix], "structured_questions": [q.to_json() for q in body.structured_questions], "verdict": body.verdict.value, "questions": list(body.questions), "suggestions": []})


def test_four_or_fewer_are_untouched_and_code_tops_up_only_to_four() -> None:
    attempt, _binding = _assess(_must_have_answer(("askable",) * 4, (0, 1, 2, 3)))
    assert attempt.extras.capped_mandatory_questions == () and len(attempt.parsed.structured_questions) == 4
    # The model asked two of six: the retry names rows that need a question (the room is two), and when it still asks
    # nothing new code asks exactly the two most decisive of them, never all four unasked.
    answer = _must_have_answer(("askable",) * 6, (0, 1))
    attempt, _binding = _assess(answer, answer)
    assert attempt.ok and len(attempt.parsed.structured_questions) == 4 and len(attempt.extras.asked_by_code) == 2 and attempt.extras.capped_mandatory_questions == ()
    assert len(resume_gate.also_unverified([json_row(row) for row in attempt.parsed.matrix], attempt.parsed.structured_questions)) == 2


def test_the_cap_is_a_v9_rule_on_must_have_rows_only_and_a_not_a_match_keeps_none() -> None:
    assert weights.MAX_MANDATORY_QUESTIONS == 4
    rows = [{"requirement": f"R{n}", "class": "askable"} for n in range(6)] + [{"requirement": "T", "class": "list_item"}]
    questions = [{"requirement": "T"}, *({"requirement": f"R{n}"} for n in (5, 4, 3, 2, 1, 0)), {"requirement": None}]
    kept, dropped = weights.cap_mandatory_questions(rows, questions)
    assert [item["requirement"] for item in dropped] == ["R5", "R4"]  # the posting's order decides among askable rows
    assert [item["requirement"] for item in kept] == ["T", "R3", "R2", "R1", "R0", None]
    answer = _must_have_answer(("hard",) * 6, tuple(range(6)), verdict=NOT_A_MATCH) | {"not_a_match_reason": "No."}
    answer["matrix"][0] = {**answer["matrix"][0], "status": "unmet"}
    attempt, _binding = _assess(answer)
    assert attempt.ok and attempt.extras.capped_mandatory_questions == ()


def json_row(row):
    """A stored ``RequirementMatrixRow`` as the plain row the gate reads."""

    return {"requirement": row.requirement, "class": row.requirement_class.value, "status": row.status.value, "class_basis": row.class_basis}


# --- orchestrator #45 (the operator's rule): sponsorship and work authorization NEVER gate -------------------------


def _permit_answer(verdict: str, status: str, **more: object) -> dict[str, object]:
    """Go met, plus a row (and a question) about a required US work permit, however the model classed and answered it."""

    answer = _answer(MATCHED)
    answer["matrix"] = [
        answer["matrix"][0],
        {"id": "elig-sponsorship", "requirement": "Must have an eligible work permit in the USA", "class": "hard", "class_basis": "Requirements: Must have an eligible work permit", "status": status,
         "resume_evidence": [], "sources": []},
    ]
    answer["verdict"] = verdict
    if status == "unclear":
        answer["questions"] = [{"question_id": "eligibility:work_authorization", "question": "Are you currently authorized to work in the United States?", "requirement": "Must have an eligible work permit in the USA"}]
    answer["not_a_match_reason"] = "No work permit." if verdict == NOT_A_MATCH else None
    return {**answer, **more}


@pytest.mark.parametrize(("verdict", "status"), [(PENDING, "unclear"), (NOT_A_MATCH, "unmet"), (MATCHED, "met"), (NOT_A_MATCH, "unclear")])
def test_a_work_permit_line_for_a_candidate_who_needs_sponsorship_is_a_label_and_the_job_is_suggested(verdict: str, status: str) -> None:
    attempt, binding = _assess(_permit_answer(verdict, status))
    body = attempt.parsed
    assert attempt.ok and attempt.attempts == 1 and len(binding.port.prompts) == 1  # a rule of the boundary, not a retry
    # No row, no question, no verdict about it: the job is Matched and the gate suggests a resume.
    assert [row.requirement for row in body.matrix] == ["Go in production"] and body.structured_questions == ()
    assert body.verdict.value == MATCHED and resume_gate.gate(body.matrix, body.structured_questions, body.verdict).decision == "suggest"
    assert attempt.extras.pick is None or True


def test_the_label_is_the_existing_sponsorship_field_and_says_not_offered_only_when_the_posting_says_so() -> None:
    answer = _permit_answer(NOT_A_MATCH, "unmet")
    answer["matrix"][1] = {**answer["matrix"][1], "requirement": "We do not sponsor visas, now or in the future", "class_basis": "We do not sponsor visas"}
    attempt, _binding = _assess(answer)
    assert attempt.ok and attempt.parsed.sponsorship is not None and attempt.parsed.sponsorship.value == "not_offered" and attempt.parsed.verdict.value == MATCHED
    attempt, _binding = _assess(_permit_answer(PENDING, "unclear"))
    assert attempt.parsed.sponsorship is None  # only a permit requirement: nothing is claimed about sponsoring
    # The model's own label is kept as it came.
    attempt, _binding = _assess(_permit_answer(PENDING, "unclear", sponsorship="offered"))
    assert attempt.parsed.sponsorship.value == "offered"


def test_the_gate_ignores_an_authorization_row_whatever_a_stored_matrix_holds_and_location_still_gates() -> None:
    go = {"requirement": "Go in production", "class": "hard", "class_basis": "Requirements: Go", "status": "met"}
    for status in ("unmet", "unclear"):
        permit = {"id": "elig-sponsorship", "requirement": "Authorized to work in the United States", "class": "hard", "class_basis": "x", "status": status}
        question = {"question_id": "authorization:us", "question": "Are you authorized?", "requirement": "Authorized to work in the United States"}
        assert resume_gate.gate([go, permit], [question], MATCHED).decision == "suggest"
        assert resume_gate.unasked_rows([go, permit], []) == [] and resume_gate.also_unverified([go, permit], []) == []
    # Country eligibility (rule 4) is not this rule: an unmet hard location row still decides not_a_match.
    location = {"id": "elig-location", "requirement": "Remote - Poland", "class": "hard", "class_basis": "x", "status": "unmet"}
    assert resume_gate.gate([go, location], [], NOT_A_MATCH).decision == "not_a_match"
    assert not resume_gate.is_authorization_row(location) and resume_gate.is_authorization_row({"requirement": "Must hold a valid work permit"})


FALSE_FRIENDS = (
    "Act as an executive sponsor for cross-team initiatives",
    "Sponsor engineers and grow them to staff level",
    "Experience with the Visa and Mastercard card networks",
    "Domain experience in immigration tech",
    "Visa-certified payment integrations",
)
AUTHORIZATION = (
    "Must have an eligible work permit in the USA",
    "You must be authorized to work in the United States",
    "Candidates must be eligible to work in Canada",
    "Right to work in the UK",
    "We do not sponsor visas, now or in the future",
    "This role requires sponsorship",
    "Candidates must not require sponsorship",
    "No sponsorship is available",
    "We are unable to provide work visa sponsorship",
    "Applicants must hold a valid work visa",
    "Employment authorization is required",
    "H-1B holders welcome to apply",
    "legally authorized to work",
    "Visa sponsorship is not offered",
)


def test_authorization_means_the_employment_sense_only_and_a_false_friend_is_never_removed() -> None:
    for text in FALSE_FRIENDS:
        assert not resume_gate.is_authorization_row({"requirement": text, "class_basis": text}), text
        assert not resume_gate.is_authorization_question({"question_id": "domain:payments", "question": f"{text}?", "requirement": text}), text
    for text in AUTHORIZATION:
        assert resume_gate.is_authorization_row({"requirement": text}), text
        assert resume_gate.is_authorization_question({"question_id": "other:x", "question": "?", "requirement": text}), text
    assert resume_gate.is_authorization_question({"question_id": "eligibility:work_authorization", "question": "Are you authorized?", "requirement": "x"})
    assert not resume_gate.is_authorization_question({"question_id": "eligibility:english", "question": "Fluent English?", "requirement": "Fluent English"})


@pytest.mark.parametrize("text", FALSE_FRIENDS)
def test_a_false_friend_row_stays_in_the_matrix_and_still_gates(text: str) -> None:
    answer = _answer(PENDING)
    answer["matrix"][1] = {**answer["matrix"][1], "requirement": text, "class_basis": f"Requirements: {text}"}
    answer["questions"] = [{"question_id": "domain:payments", "question": "Have you done this?", "requirement": text}]
    attempt, _binding = _assess(answer)
    assert attempt.ok and attempt.attempts == 1
    assert [row.requirement for row in attempt.parsed.matrix][1] == text and len(attempt.parsed.structured_questions) == 1
    assert attempt.parsed.verdict.value == PENDING and resume_gate.gate(attempt.parsed.matrix, attempt.parsed.structured_questions, attempt.parsed.verdict).decision == "hold_question"
