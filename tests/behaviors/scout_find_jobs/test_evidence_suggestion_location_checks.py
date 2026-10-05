"""0.1.11 C2, C3, C4 (orchestrator #51): what CODE guarantees about evidence, suggestions and a location verdict.

Offline, synthetic: every posting, line and answer below is invented and only has the SHAPE of a case a blind judge
failed (a paraphrase that widened a claim, a reword that brings a word the line lacks, a suggestion on a row the line is
no source of, a header country against a body that says "anywhere"). A scripted binding stands in for the model.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gigai.scout import suggestion_check as check
from gigai.scout.assessment_core import AssessJob, assess_once, build_assess_context, says_worldwide
from gigai.scout.assessment_core import PriorAnswer
from gigai.scout.find_jobs.assess_contracts import AssessmentSuggestion
from gigai.scout.quick_assess import _parse_body

MATCHED, PENDING, NOT_A_MATCH = "matched_above_threshold", "pending_user_answers", "not_a_match"

MASTER = """<!-- gigai-master:1 -->

## Summary

- Platform engineer with ten years on distributed services. <!-- id:sum-000001 -->

## Experience

### NORTHWIND <!-- id:r-000001 -->
**Staff Engineer | 2022 - Present**
- Ran the on-call rota for six services and cut resolution time by half with an ops bot used by two teams. <!-- id:b-000001 -->
- Built a service SDK in Go that adds tracing to forty functions. <!-- id:b-000002 -->

### CONTOSO <!-- id:r-000002 -->
**Engineer | 2019 - 2022**
- Moved a batch ingest job to a Go queue worker on three cloud queues, raising uptime to 99.9%. <!-- id:b-000003 -->
- Wrote the REST API layer for the billing service in Node. <!-- id:b-000004 -->

## Projects

### Toolbox <!-- id:p-000001 -->
- Built a small agent runtime with approval gates and a run journal. <!-- id:b-000005 -->

## Skills

- Go, Node, SQL <!-- id:s-000001 -->
"""
IDS = ("sum-000001", "b-000001", "b-000002", "b-000003", "b-000004", "b-000005", "s-000001")
JOB = AssessJob("Staff Engineer", "Acme", "Remote", "Requirements: Go services; tracing. Nice to have: Helm.")


class _Port:
    name = "scripted"

    def __init__(self, outputs: list[object]) -> None:
        self.outputs = [json.dumps(output) for output in outputs]

    def invoke(self, request):
        return SimpleNamespace(output_text=self.outputs.pop(0), normalized_usage=None)


class _Binding:
    def __init__(self, *outputs: object) -> None:
        self.port = _Port(list(outputs))

    def request(self, *, role: str, prompt: str):
        return SimpleNamespace(role=role, prompt=prompt)


def _ctx(**more: object):
    return build_assess_context(resume_text=MASTER, resume_ids=IDS, pick_lines=20, **more)


def _row(requirement: str, status: str, sources: list[str], evidence: list[str], klass: str = "askable") -> dict[str, object]:
    return {"requirement": requirement, "class": klass, "class_basis": f"Requirements: {requirement}", "status": status, "resume_evidence": evidence, "sources": sources}


def _assess(answer: dict[str, object], *, job: AssessJob = JOB, ctx=None):
    attempt = assess_once(_Binding(answer), job, ctx or _ctx(), parse=_parse_body)
    assert attempt.ok, attempt
    return attempt


def _answer(rows: list[dict[str, object]], suggestions: list[dict[str, object]] | None = None, verdict: str = MATCHED, **more: object) -> dict[str, object]:
    return {"verdict": verdict, "matrix": rows, "questions": [], "suggestions": suggestions or [], "not_a_match_reason": None, **more}


# --- the master as code reads it ----------------------------------------------------------------------


def test_the_master_lines_are_read_by_id_with_their_role() -> None:
    lines = check.parse_master_lines(MASTER)
    assert lines["b-000001"].text.startswith("Ran the on-call rota") and "<!--" not in lines["b-000001"].text
    assert (lines["b-000001"].role, lines["b-000001"].first_role) == ("r-000001", True)
    assert (lines["b-000003"].role, lines["b-000003"].first_role) == ("r-000002", False)
    assert lines["sum-000001"].role is None and lines["s-000001"].role is None
    assert "r-000001" not in lines and "p-000001" not in lines  # a header is not a line


# --- C2: the evidence is the cited line --------------------------------------------------------------------


def test_a_met_rows_evidence_is_the_cited_lines_by_id_and_not_the_models_paraphrase() -> None:
    # The model widened the claim: "retrieval memory" for all agents, "across ten domains", "since 2012".
    stretched = "Built ten agents with retrieval memory across ten domains, in production since 2012."
    answer = _answer([
        _row("Go services", "met", ["b-000003", "b-000002"], [stretched], "hard"),
        _row("Tracing", "unclear", [], ["The master shows tracing only inside one SDK line."]),
    ])
    answer["questions"] = [{"question_id": "tooling:tracing", "question": "Have you run tracing?", "requirement": "Tracing"}]
    matrix = _assess(answer).parsed.matrix
    met, unclear = matrix[0], matrix[1]
    assert list(met.resume_evidence) == [check.parse_master_lines(MASTER)["b-000003"].text, check.parse_master_lines(MASTER)["b-000002"].text]
    assert stretched not in met.resume_evidence and "retrieval" not in " ".join(met.resume_evidence)
    assert list(unclear.resume_evidence) == ["The master shows tracing only inside one SDK line."]  # a row that is not met keeps its words


def test_an_answer_a_row_cites_is_shown_as_the_answers_own_text() -> None:
    ctx = _ctx(bank=SimpleNamespace(prior_answers=(PriorAnswer("tooling:helm", "Helm?", "Pinned Helm charts applied through Terraform."),), bank_answers=()))
    answer = _answer([_row("Helm", "met", ["A tooling:helm", "b-000002"], ["Authored many Helm charts."])])
    evidence = list(_assess(answer, ctx=ctx).parsed.matrix[0].resume_evidence)
    assert evidence == ["Your answer: Pinned Helm charts applied through Terraform.", check.parse_master_lines(MASTER)["b-000002"].text]


def test_a_met_row_without_a_usable_source_keeps_the_models_evidence() -> None:
    answer = _answer([_row("Go services", "met", [], ["Built Go services."], "hard")])
    assert list(_assess(answer).parsed.matrix[0].resume_evidence) == ["Built Go services."]
    # A prompt that showed no ids has no lines: the answer is read as before.
    plain = build_assess_context(resume_text="- Built Go services")
    assert list(_assess(_answer([_row("Go services", "met", ["b-000003"], ["Built Go services."], "hard")]), ctx=plain).parsed.matrix[0].resume_evidence) == ["Built Go services."]


# --- C3: the suggestion check, rule by rule -------------------------------------------------------------


LINES = check.parse_master_lines(MASTER)
ANSWERS = {"tooling:helm": "Pinned Helm charts applied through Terraform."}


def _rows(**by_id: dict[str, object]) -> dict[str, dict[str, object]]:
    return {row_id: {"id": row_id, **row} for row_id, row in by_id.items()}


ROWS = _rows(
    **{
        "req-aaaaaa": {"requirement": "Operate services in production", "status": "met", "sources": ["b-000001", "b-000003"]},
        "req-bbbbbb": {"requirement": "Helm charts", "status": "met", "sources": ["A tooling:helm"]},
        "req-cccccc": {"requirement": "Kafka and Flink streaming", "status": "unclear", "sources": []},
        "req-dddddd": {"requirement": "Ingest pipelines", "status": "met", "sources": ["b-000003"]},
    }
)


def _kept(kind: str, **fields: object):
    item = AssessmentSuggestion(kind=kind, why=str(fields.pop("why", "Because.")), **fields)  # type: ignore[arg-type]
    return check.check_suggestion(item, ROWS, LINES, ANSWERS)


def test_a_reword_whose_phrase_brings_a_word_the_line_lacks_is_dropped() -> None:
    # "the Go queue worker ... kafka-based": the line names queues, not Kafka; "tools" is not "agents".
    assert _kept("reword", line="b-000003", requirement="req-dddddd", posting_phrase="Kafka based ingest")[0] is None
    assert _kept("reword", line="b-000003", requirement="req-dddddd", posting_phrase="uptime and ingest")[1] == "ok"
    assert _kept("keyword", line="b-000001", requirement="req-aaaaaa", posting_phrase="on-call schedule")[0] is None
    assert _kept("keyword", line="b-000001", requirement="req-aaaaaa", posting_phrase="on-call")[0] is not None
    assert _kept("reword", line="b-000005", posting_phrase="agent tools")[0] is None
    assert _kept("reword", line="b-000005", posting_phrase="agent runtime")[0] is not None


def test_a_phrase_may_use_words_of_an_answer_the_row_cites() -> None:
    item = AssessmentSuggestion(kind="keyword", why="Name Helm.", line="b-000002", requirement="req-bbbbbb", posting_phrase="Helm charts")
    rows = _rows(**{"req-bbbbbb": {"requirement": "Helm charts", "status": "met", "sources": ["b-000002", "A tooling:helm"]}})
    assert check.check_suggestion(item, rows, LINES, ANSWERS)[0] is not None
    assert check.check_suggestion(item, rows, LINES, {})[0] is None  # without the answer's words the line has no Helm


def test_the_row_named_must_be_one_the_line_is_a_source_of() -> None:
    assert _kept("reword", line="b-000004", requirement="req-aaaaaa", posting_phrase="billing")[1] == "line_not_a_source_of_row"
    assert _kept("order", line="b-000004", requirement="req-aaaaaa")[1] == "line_not_a_source_of_row"
    assert _kept("order", line="b-000001", requirement="req-aaaaaa")[0] is not None
    assert _kept("order", line="b-000001")[0] is not None  # no row named: nothing to contradict


def test_reword_keyword_and_order_must_name_a_line_of_the_master() -> None:
    for kind in ("reword", "keyword", "order"):
        assert _kept(kind, requirement="req-aaaaaa")[1] == "no_master_line"
        assert _kept(kind, line="b-999999")[1] == "no_master_line"


def test_an_order_suggestion_moves_a_line_inside_its_own_role() -> None:
    # A line of the SECOND role cannot lead the whole Experience section; one of the first role can; a summary or skills line is in no role.
    assert _kept("order", line="b-000003", why="Put this at the top of the experience section.")[1] == "order_across_roles"
    assert _kept("order", line="b-000003", why="Lead the Contoso bullets with this.")[0] is not None
    assert _kept("order", line="b-000001", why="Lead the Experience section with this line.")[0] is not None
    assert _kept("order", line="sum-000001")[1] == "order_line_in_no_role"


def test_a_master_line_suggestion_needs_an_answer_or_story_else_it_is_a_gap_or_nothing() -> None:
    kept, why = _kept("master_line", requirement="req-bbbbbb", why="Add the Helm line your answer supports.")
    assert kept is not None and kept.kind == "master_line" and why == "ok"
    kept, why = _kept("master_line", requirement="req-cccccc", why="If you ran streaming jobs, a line would cover it.", posting_phrase="Kafka streaming")
    assert kept is not None and (kept.kind, kept.line, why) == ("gap", None, "master_line_to_gap")
    # A met row has nothing missing; a master_line that names no row has nothing to ask about.
    assert _kept("master_line", requirement="req-aaaaaa")[0] is None
    assert _kept("master_line", line="b-000001")[0] is None


def test_a_gap_phrase_must_belong_to_the_row_it_is_filed_under() -> None:
    assert _kept("gap", requirement="req-cccccc", posting_phrase="Kafka streaming")[0] is not None
    assert _kept("gap", requirement="req-aaaaaa", posting_phrase="blameless post mortem process")[1] == "gap_phrase_not_of_row"
    assert _kept("gap", requirement="req-cccccc")[0] is not None  # no phrase: nothing to compare


def test_an_empty_list_is_a_good_answer_and_the_kept_ones_keep_their_order() -> None:
    assert check.check_suggestions([], list(ROWS.values()), LINES, ANSWERS) == ([], [])
    good = AssessmentSuggestion(kind="order", why="Lead with it.", line="b-000001", requirement="req-aaaaaa")
    bad = AssessmentSuggestion(kind="reword", why="Say it.", line="b-000004", requirement="req-aaaaaa")
    kept, dropped = check.check_suggestions([bad, good], list(ROWS.values()), LINES, ANSWERS)
    assert kept == [good] and [reason for _item, reason in dropped] == ["line_not_a_source_of_row"]


def test_the_boundary_drops_what_the_check_refuses_and_counts_it() -> None:
    answer = _answer(
        [_row("Operate services", "met", ["b-000001"], ["x"], "hard"), _row("Billing APIs", "met", ["b-000004"], ["y"])],
        [
            {"kind": "reword", "why": "Lead with it.", "line": "b-000001", "requirement": "Operate services", "posting_phrase": "on-call"},
            {"kind": "reword", "why": "Lead with it.", "line": "b-000004", "requirement": "Operate services", "posting_phrase": "billing"},
            {"kind": "keyword", "why": "Say it.", "line": "b-000001", "requirement": "Operate services", "posting_phrase": "incident command"},
        ],
    )
    attempt = _assess(answer)
    assert [(item.kind, item.line) for item in attempt.extras.structured_suggestions] == [("reword", "b-000001")]
    assert [reason for _kind, reason in attempt.extras.checked_suggestions] == ["line_not_a_source_of_row", "phrase_not_in_line"]
    assert attempt.extras.dropped_suggestions == 2


# --- C4: a header's country against a body that says "anywhere" ---------------------------------------------


WORLDWIDE_BODY = "Location: Poland.\nWe are a globally distributed, remote-first team. The pay band for this role is listed for Seattle."
LOCATION_ROW = {"id": "elig-location", "requirement": "Based in Poland", "class": "hard", "class_basis": "Location: Poland", "status": "unmet", "resume_evidence": [], "sources": []}


def _location_answer(**more: object) -> dict[str, object]:
    rows = [_row("Go services", "met", ["b-000003"], ["z"], "hard"), dict(LOCATION_ROW)]
    return _answer(rows, verdict=NOT_A_MATCH, not_a_match_reason="The role is in Poland.", **more)


def test_an_unmet_location_with_a_worldwide_body_is_unclear_with_one_question_and_not_a_no() -> None:
    attempt = _assess(_location_answer(), job=AssessJob("Staff Engineer", "Acme", "Poland", WORLDWIDE_BODY))
    body = attempt.parsed
    location = next(row for row in body.matrix if row.id == "elig-location")
    assert location.status.value == "unclear" and body.verdict.value == PENDING
    assert [question.requirement for question in body.structured_questions] == ["Based in Poland"] and len(body.questions) == 1
    assert body.not_a_match_reason is None


def test_the_models_own_location_question_is_the_one_question() -> None:
    own = {"question_id": "eligible:location", "question": "Where are you based?", "requirement": "Based in Poland"}
    attempt = _assess(_location_answer(questions=[own, {**own, "question_id": "eligible:other", "question": "And where?"}]), job=AssessJob("Staff Engineer", "Acme", "Poland", WORLDWIDE_BODY))
    assert [question.question_id for question in attempt.parsed.structured_questions] == ["eligible:location"]


def test_without_a_worldwide_statement_the_unmet_location_stays_not_a_match() -> None:
    body = "Location: Poland. You must live in Poland and work from our Krakow office."
    attempt = _assess(_location_answer(), job=AssessJob("Staff Engineer", "Acme", "Poland", body))
    assert attempt.parsed.verdict.value == NOT_A_MATCH and not attempt.parsed.structured_questions
    location = next(row for row in attempt.parsed.matrix if row.id == "elig-location")
    assert location.status.value == "unmet"


@pytest.mark.parametrize(
    ("text", "worldwide"),
    [
        ("Work from anywhere in the world.", True),
        ("A globally distributed team.", True),
        ("We hire worldwide.", True),
        ("We are remote-first. Pay band: $150k for our Seattle office.", True),
        ("We are remote-first.", False),  # alone it says nothing about another country
        ("We do not hire worldwide.", False),
        ("Candidates must be based in Poland.", False),
        ("The role is not open to work from anywhere in the world.", False),
    ],
)
def test_what_counts_as_a_worldwide_statement(text: str, worldwide: bool) -> None:
    assert says_worldwide(text) is worldwide


def test_another_unmet_hard_row_keeps_the_no_and_a_met_location_is_untouched() -> None:
    answer = _location_answer()
    answer["matrix"][0] = {**answer["matrix"][0], "status": "unmet", "sources": [], "resume_evidence": []}  # type: ignore[index]
    attempt = _assess(answer, job=AssessJob("Staff Engineer", "Acme", "Poland", WORLDWIDE_BODY))
    assert attempt.parsed.verdict.value == NOT_A_MATCH  # Go is unmet too: the location row alone did not decide it
