"""0.1.11.3 Q1 + Q3 (ASSESS-100 tickets T3, T7): what CODE guarantees about a "the resume is silent" claim and a citation.

Offline, synthetic: every posting, line and answer below is invented and only has the SHAPE of a case the 100-job
measurement counted (a suggestion that says the resume is silent on a tool a line names; evidence that joins two lines
with ``;``, cuts one with ``...``, puts a role title or a label in front of a line, or quotes part of a named set).
A scripted binding stands in for the model. The end outcome is the assessment as it is stored and what the job page
reads from it (the suggestion record).

Q1: a suggestion whose claim names only tools, skills or numbers that a master line states is dropped: not stored, not
shown, not counted. A term the master does not have keeps its suggestion, and so does a claim about anything more.
Q3: every evidence item is ONE line of the master as it is written (or one listed answer), or the row cites nothing.
No row's status, class or sources change.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gigai.scout import suggestion_check as check
from gigai.scout import suggestions
from gigai.scout.assessment_core import AssessJob, PriorAnswer, assess_once, build_assess_context
from gigai.scout.find_jobs.assess_contracts import AssessmentSuggestion
from gigai.scout.quick_assess import _parse_body

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import (  # noqa: F401 - `fx` is the fixture
    _JOB, KUBERNETES_LINE, PYTHON_LINE, REQUIREMENTS, TERRAFORM_LINE, _assess as _assess_stored, _ids, _patch_v9_template, _v9_answer, fx,
)
from tests.support.posting_fixtures import PostingsFixture

MATCHED = "matched_above_threshold"

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
- Moved a batch ingest job to a Kafka queue worker on three cloud queues, raising uptime to 99.9%. <!-- id:b-000003 -->
- Wrote the billing dashboard in ReactJS over 4 years, with a REST API layer in Node. <!-- id:b-000004 -->

## Skills

- Go, Node, SQL, Kubernetes <!-- id:s-000001 -->
"""
IDS = ("sum-000001", "b-000001", "b-000002", "b-000003", "b-000004", "s-000001")
LINES = check.parse_master_lines(MASTER)
TEXT = {line_id: line.text for line_id, line in LINES.items()}
JOB = AssessJob("Staff Engineer", "Acme", "Remote", "Requirements: Go services; Kafka and Flink streaming; React. Nice to have: Helm.")


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


def _row(requirement: str, status: str, sources: list[str], evidence: list[str], klass: str = "nice_to_have") -> dict[str, object]:
    return {"requirement": requirement, "class": klass, "class_basis": f"Requirements: {requirement}", "status": status, "resume_evidence": evidence, "sources": sources}


def _assess(rows: list[dict[str, object]], suggested: list[object] | None = None, *, ctx=None):
    answer = {"verdict": MATCHED, "matrix": rows, "questions": [], "suggestions": suggested or [], "not_a_match_reason": None}
    attempt = assess_once(_Binding(answer), JOB, ctx or _ctx(), parse=_parse_body)
    assert attempt.ok, attempt
    return attempt


ROWS = [
    _row("Go services", "met", ["b-000002"], ["Go SDK"], "hard"),
    _row("Kafka and Flink streaming", "unclear", [], []),
    _row("React", "unclear", [], []),
]


def _gap(why: str, requirement: str = "React", **more: object) -> dict[str, object]:
    return {"kind": "gap", "requirement": requirement, "why": why, **more}


# --- Q1: a "silent" claim about a term a master line states is dropped ----------------------------------------


UNTRUE = [
    _gap("The resume is silent on React; if you have used it, say so."),  # the line says ReactJS: another spelling of the same word
    _gap("Kafka is not mentioned in the resume.", "Kafka and Flink streaming"),
    _gap("The resume does not mention Go or SQL.", "Go services"),
    _gap("There is no mention of hands-on Kafka experience at scale.", "Kafka and Flink streaming"),  # a scope word names nothing
    _gap("The resume does not state 4 years of React.", posting_phrase="React"),  # the number and the tool are in one line
    _gap("The resume is silent on this.", "Kafka and Flink streaming", posting_phrase="Kafka"),  # a pointer: the claim is about the phrase
    _gap("The master lacks Kubernetes."),
    _gap("The resume lacks any mention of React.js."),
    _gap("Golang is not listed.", "Go services"),
    {"kind": "keyword", "line": "b-000003", "posting_phrase": "Kafka", "why": "The resume doesn't mention Kafka."},
    {"kind": "master_line", "requirement": "React", "why": "ReactJS is missing from the resume."},
    # 0.1.11.4 Q4: every term of the claim is checked, and it is false about Kafka (a line names it) though Flink is not there.
    _gap("The resume does not mention Kafka or Flink.", "Kafka and Flink streaming"),
]
TRUE = [
    _gap("The resume is silent on Flink; if you have run it, say so.", "Kafka and Flink streaming"),  # not in the master
    _gap("The resume does not show leading a Kafka migration.", "Kafka and Flink streaming"),  # about more than a named term
    _gap("The resume does not state 6 years of React."),  # the master says 4
    _gap("The resume does not state 4 years of Kafka.", "Kafka and Flink streaming"),  # "4 years" is in another line than Kafka
    _gap("The resume is silent on EKS."),  # the table lists EKS under Kubernetes: another thing, not another spelling
    _gap("If you ran Kafka with Flink, a line would cover it.", "Kafka and Flink streaming"),  # no "silent" claim at all
    _gap("The resume does not show the rest of the stack.", "Kafka and Flink streaming"),  # "rest" is a word here, not REST (a line says "REST API")
    _gap("The resume does not show Kafka at the scale the posting names (1M events).", "Kafka and Flink streaming"),  # a number no line states
    # A claim about one PLACE of the resume, and advice, are not a claim that the resume is silent.
    {"kind": "keyword", "line": "sum-000001", "posting_phrase": "distributed services", "why": "The summary does not mention Kafka; the Contoso line does."},
    {"kind": "order", "line": "b-000003", "why": "Kafka is not mentioned in the first role; lead the Contoso bullets with this line."},
    _gap("Do not mention Kafka unless you ran it in production.", "Kafka and Flink streaming"),
    # A keyword suggestion is about the posting's own spelling: "React" as such is in no line (the line says ReactJS).
    {"kind": "keyword", "line": "b-000004", "posting_phrase": "React", "why": "The resume does not mention React by that name."},
]


@pytest.mark.parametrize("suggestion", UNTRUE, ids=[str(item["why"]) for item in UNTRUE])
def test_a_suggestion_that_says_silent_on_a_term_a_master_line_states_is_not_stored_shown_or_counted(suggestion: dict[str, object]) -> None:
    attempt = _assess(ROWS, [suggestion])
    assert attempt.extras.structured_suggestions == ()  # what is stored as structured_suggestions, and what the page shows
    assert suggestion["why"] not in attempt.parsed.suggestions  # the plain list a reader of strings shows
    assert [reason for _kind, reason in attempt.extras.checked_suggestions] == ["silent_but_in_master"] and attempt.extras.dropped_suggestions == 1


@pytest.mark.parametrize("suggestion", TRUE, ids=[str(item["why"]) for item in TRUE])
def test_a_term_that_is_not_in_the_master_or_a_claim_about_more_keeps_its_suggestion(suggestion: dict[str, object]) -> None:
    attempt = _assess(ROWS, [suggestion])
    assert [item.why for item in attempt.extras.structured_suggestions] == [suggestion["why"]]
    assert suggestion["why"] in attempt.parsed.suggestions and attempt.extras.dropped_suggestions == 0


def test_the_kept_ones_keep_their_order_and_no_row_is_changed_by_a_dropped_suggestion() -> None:
    plain = _assess(ROWS)
    both = _assess(ROWS, [UNTRUE[0], TRUE[0], UNTRUE[1], "The resume is silent on Kafka.", "Lead with the Go SDK."])
    assert [item.why for item in both.extras.structured_suggestions] == [TRUE[0]["why"]]
    assert list(both.parsed.suggestions) == ["Lead with the Go SDK.", TRUE[0]["why"]]  # the untrue plain string is gone too
    assert both.parsed.matrix == plain.parsed.matrix and both.parsed.verdict == plain.parsed.verdict  # a met row is never made worse


def test_the_rule_reads_only_the_claim_and_only_the_master() -> None:
    # A line that names the term in a longer word does not state it; a role header is not a line.
    assert not check.silent_but_stated("The resume is silent on Reactor.", LINES)
    assert not check.silent_but_stated("Contoso is not mentioned.", LINES)
    assert not check.silent_but_stated("The resume is silent on React.", {})  # a prompt that showed no ids: no check
    # "Go" is matched as written (two letters): the verb is not the language.
    assert check.silent_but_stated("The resume does not mention Go.", LINES) and not check.silent_but_stated("The resume does not mention Rust.", LINES)
    # Without the alias table's spellings only the exact word counts.
    assert check.silent_but_stated("The resume is silent on React.", LINES, variants=True)
    assert not check.silent_but_stated("The resume is silent on React.", LINES, variants=False)
    item = AssessmentSuggestion(kind="gap", why="The resume is silent on Kafka.", requirement="req-aaaaaa")
    assert check.check_suggestion(item, {}, LINES, {}) == (None, "silent_but_in_master")


# --- Q3: a citation is one line of the master, as it is written -----------------------------------------------


CITABLE = frozenset(TEXT.values())


def test_every_citation_is_one_verbatim_line_or_the_row_cites_nothing() -> None:
    stretched = "Built ten agents with retrieval memory across ten domains."
    rows = [
        # a `;` join of two real lines -> each line on its own
        _row("Operate services", "met", [], ["Ran the on-call rota for six services; Built a service SDK in Go that adds tracing to forty functions"], "hard"),
        # a `...` cut of one line -> the line
        _row("On-call", "unclear", [], ["Ran the on-call rota for six services ... used by two teams."]),
        # a role title merged with a bullet -> the bullet
        _row("Tracing", "met", [], ["Staff Engineer, Northwind: Built a service SDK in Go that adds tracing to forty functions."]),
        # an added label and a partial named set -> the skills line
        _row("Go, Node and Rust", "unclear", [], ["Skills: Go, Node"]),
        # a paraphrase no line says, on a met row with no source -> nothing
        _row("Agents", "met", [], [stretched]),
        # a sentence about the resume -> nothing
        _row("Flink", "unclear", [], ["The master shows streaming only inside one Kafka line."]),
        # a quote cut short (the prompt's 160 characters) -> the line
        _row("Streaming", "unclear", [], ["Moved a batch ingest job to a Kafka queue worker"]),
        # already one line -> as it is
        _row("Dashboards", "met", [], [TEXT["b-000004"]]),
        # a met row with a source: C2 wrote the line already
        _row("Uptime", "met", ["b-000003"], ["Kept uptime high."], "hard"),
    ]
    before = [(row["requirement"], row["status"], row["class"], row["sources"]) for row in rows]
    attempt = _assess(rows)
    matrix = attempt.parsed.matrix
    evidence = {row.requirement: list(row.resume_evidence) for row in matrix}
    assert evidence == {
        "Operate services": [TEXT["b-000001"], TEXT["b-000002"]],
        "On-call": [TEXT["b-000001"]],
        "Tracing": [TEXT["b-000002"]],
        "Go, Node and Rust": [TEXT["s-000001"]],
        "Agents": [],
        "Flink": [],
        "Streaming": [TEXT["b-000003"]],
        "Dashboards": [TEXT["b-000004"]],
        "Uptime": [TEXT["b-000003"]],
    }
    # The deterministic check: 100% of the citations are a line of the master, word for word.
    assert all(item in CITABLE for row in matrix for item in row.resume_evidence)
    # Only the evidence moved: no status, class or source did (a met row stays met).
    assert sorted((row.requirement, row.status.value, row.requirement_class.value, list(row.sources)) for row in matrix) == sorted(before)  # must-haves are listed first
    assert sorted(attempt.extras.checked_citations) == ["no_line", "no_line", "split_join", "trimmed_to_line", "trimmed_to_line", "trimmed_to_line", "trimmed_to_line"]


def test_a_quote_of_a_listed_answer_is_that_answer_and_an_eligibility_row_keeps_the_setup_wording() -> None:
    answer = "No Cassandra in production; MongoDB at two employers."
    ctx = _ctx(bank=SimpleNamespace(prior_answers=(PriorAnswer("datastore:cassandra", "Cassandra?", answer),), bank_answers=()), countries=("us",))
    rows = [
        _row("Go services", "met", ["b-000002"], ["Go SDK"], "hard"),
        _row("Cassandra", "unmet", [], ["No Cassandra in production"]),
        {"id": "elig-location", "requirement": "Based in the US", "class": "hard", "class_basis": "Location: US", "status": "met", "resume_evidence": ["Candidate is in the US."], "sources": []},
    ]
    matrix = {row.requirement: row for row in _assess(rows, ctx=ctx).parsed.matrix}
    assert list(matrix["Cassandra"].resume_evidence) == [f"Your answer: {answer}"] and matrix["Cassandra"].status.value == "unmet"
    assert list(matrix["Based in the US"].resume_evidence) == ["Your search settings say you can work from US."]


def test_a_piece_that_several_lines_hold_names_no_line_and_a_prompt_without_ids_is_read_as_before() -> None:
    citable = check._Citable(LINES, {}, {})  # noqa: SLF001
    assert check.single_line_cites("Go", citable) == ([], "no_line")  # the SDK line and the skills line both say Go
    assert check.single_line_cites("Kubernetes", citable) == ([TEXT["s-000001"]], "trimmed_to_line")  # one line names it
    assert check.single_line_cites(TEXT["b-000001"], citable) == ([TEXT["b-000001"]], "verbatim")
    assert check.single_line_cites("ran the ON-CALL rota for six services", citable) == ([TEXT["b-000001"]], "trimmed_to_line")
    # A join whose other half is no line's: trimmed to the one line it is built from.
    assert check.single_line_cites("Built a service SDK in Go; led a team of forty", citable) == ([TEXT["b-000002"]], "trimmed_to_line")
    plain = build_assess_context(resume_text="- Built Go services")
    attempt = assess_once(
        _Binding({"verdict": MATCHED, "matrix": [_row("Go services", "met", [], ["Built Go services; shipped them."], "hard")], "questions": [], "suggestions": [], "not_a_match_reason": None}),
        JOB, plain, parse=_parse_body,
    )
    assert list(attempt.parsed.matrix[0].resume_evidence) == ["Built Go services; shipped them."] and attempt.extras.checked_citations == ()


# --- the stored assessment and the job page's record ----------------------------------------------------------


def test_the_stored_assessment_and_the_job_pages_suggestions_hold_no_false_silent_claim_and_only_verbatim_lines(
    fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_v9_template(monkeypatch)
    answer = json.loads(_v9_answer(fx))
    untrue = "The resume is silent on Terraform; say so if you have used it."
    true = "The resume does not mention Helm; say so if you have written charts."
    answer["suggestions"] += [
        {"kind": "gap", "requirement": REQUIREMENTS[2], "why": untrue},
        {"kind": "gap", "requirement": REQUIREMENTS[3], "why": true},
    ]
    answer["matrix"][1].update(sources=[], resume_evidence=["Operated Kubernetes clusters ... Wrote the Terraform modules"])  # met, no source, a `...` join of two lines
    answer["matrix"][3]["resume_evidence"] = ["The resume shows Kubernetes but nothing about charts."]  # unclear: a sentence, not a line
    stored, _prompt = _assess_stored(fx, json.dumps(answer))
    whys = [item.why for item in stored.result.structured_suggestions]
    assert true in whys and untrue not in whys and untrue not in stored.result.suggestions and true in stored.result.suggestions
    record = suggestions.read_suggestions(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    assert record is not None
    shown = [item.why for item in record.suggestions]
    assert true in shown and untrue not in shown and len(shown) == len(whys)  # "Suggestions (N open)" counts what is stored
    by_requirement = {row.requirement: row for row in stored.result.matrix}
    assert list(by_requirement[REQUIREMENTS[1]].resume_evidence) == [KUBERNETES_LINE, TERRAFORM_LINE] and by_requirement[REQUIREMENTS[1]].status.value == "met"
    assert list(by_requirement[REQUIREMENTS[3]].resume_evidence) == [] and by_requirement[REQUIREMENTS[3]].status.value == "unclear"
    lines = set(_ids(fx))
    assert all(item in lines for row in stored.result.matrix for item in row.resume_evidence)
    assert list(by_requirement[REQUIREMENTS[0]].resume_evidence) == [PYTHON_LINE]
