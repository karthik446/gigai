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
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gigai.canonical import parse_json_bytes
from gigai.scout import assessment_core, proposals
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
    answer["matrix"].append({"id": "elig-sponsorship", "requirement": "No sponsorship", "class": "hard", "status": "met", "resume_evidence": []})
    attempt, _binding = _assess(answer, ctx=_ctx(requirements=LISTED))
    assert attempt.ok and attempt.attempts == 1
    by_id = {row.id: row for row in attempt.parsed.matrix}
    go, kubernetes = by_id["req-aaaaaa"], by_id["req-bbbbbb"]
    assert (go.requirement, go.class_basis, go.alternatives) == ("Go in production", "Requirements: Go in production", ())
    assert go.requirement_class is RequirementClass.HARD and go.class_from == "disclaimer"
    assert (kubernetes.requirement, kubernetes.alternatives) == ("Kubernetes (EKS or GKE)", ("EKS", "GKE"))
    assert kubernetes.requirement_class is RequirementClass.ASKABLE and kubernetes.class_from is None
    assert by_id["elig-sponsorship"].requirement == "No sponsorship"  # a row about the candidate is the assessment's own
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
