"""0.1.11.4 A3: more questions the master already answers are not asked. The END outcome: the assessment that is stored.

Offline, synthetic (the master, the posting and every answer are invented; they only have the SHAPE of the kinds the
re-run of the 100 measured: a tool in a line that also says "replaced", a stated phrase, a slashed name, a kind of
tool with examples, an alternative track, a question on a row that is met already). A scripted binding stands in for
the model; ``assess_once`` + the product's parser give the ``AssessmentBody`` the product stores and the job page shows.
The rule case by case is in ``test_stated_more_rules``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gigai.scout import resume_gate
from gigai.scout.assessment_core import AssessJob, assess_once, build_assess_context
from gigai.scout.quick_assess import _checks_of, _parse_body

from tests.behaviors.scout_find_jobs import test_stated_more_rules as rules

MATCHED, PENDING = "matched_above_threshold", "pending_user_answers"
MASTER = rules.MASTER
IDS = ("sum-000001", "r-000001", "r-000002", "e-000001", *(f"b-{number:06d}" for number in range(1, 11)))
JOB = AssessJob("Staff Engineer", "Acme", "Remote", "Requirements: Terraform on AWS. React. Infrastructure administration. A relational database.")
DEGREE = "Bachelor's degree in Computer Science or equivalent practical experience"


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


def _row(requirement: str, status: str = "unclear", klass: str = "hard", **more: object) -> dict[str, object]:
    evidence = [rules.TERRAFORM] if status == "met" else ["The resume does not show this."]
    return {"requirement": requirement, "class": klass, "class_basis": f"Requirements: {requirement}"[:200], "status": status, "resume_evidence": evidence, **more}


def _question(requirement: str, slug: str = "it") -> dict[str, object]:
    return {"question_id": f"tooling:{slug}", "question": f"Do you have this: {requirement}?", "requirement": requirement}


BASE_ROW = _row("Terraform on AWS", "met", sources=["b-000005"])


def _assess(rows: list[dict[str, object]], questions: list[dict[str, object]], *, verdict: str = PENDING):
    answer = {"verdict": verdict, "matrix": rows, "questions": questions, "suggestions": [], "not_a_match_reason": None}
    attempt = assess_once(_Binding(answer, answer), JOB, build_assess_context(resume_text=MASTER, resume_ids=IDS, pick_lines=20), parse=_parse_body)
    assert attempt.ok, attempt
    return attempt


def _stored(attempt, requirement: str):
    return next(row for row in attempt.parsed.matrix if row.requirement == requirement)


def _open(attempt) -> list[str]:
    return [item.requirement for item in attempt.parsed.structured_questions]


def _decision(attempt) -> str:
    body = attempt.parsed
    return resume_gate.gate(body.matrix, body.structured_questions, body.verdict).decision


def _rules(attempt) -> list[str]:
    return [rule for _row_id, rule in getattr(attempt.extras, "met_by_master", ())]


# --- the kinds measured: the question is not asked, the row is met with the master's own line(s) -------------


@pytest.mark.parametrize(
    ("requirement", "more", "rule", "evidence", "sources"),
    [
        # the tool stands in a line that takes back something ELSE ("Replaced a legacy jQuery admin UI with React")
        ("Experience with React", {}, "line_takes_back_other", [rules.REACT], ["b-000001"]),
        ("Strong Kubernetes experience", {}, "line_takes_back_other", [rules.KUBERNETES], ["b-000002"]),
        # a phrase a line holds word for word
        ("Experience with infrastructure administration", {}, "master_phrase", [rules.ADMIN], ["b-000003"]),
        # one plain word beside the tool, both in one line
        ("Experience building billing software in TypeScript", {}, "word_beside_term", [rules.TYPESCRIPT], ["b-000004"]),
        # a slashed name: both names stated, one line each
        ("Experience with React/TypeScript", {}, "compound_name", [rules.REACT, rules.TYPESCRIPT], ["b-000001", "b-000004"]),
        # a kind of tool with an example the master states
        ("Experience with relational databases such as PostgreSQL", {}, "kind_words", [rules.POSTGRES], ["b-000006"]),
        ("Proficiency in React or a similar frontend framework", {}, "kind_words", [rules.REACT], ["b-000001"]),
        # an alternative track: the row's own "any one of", one of them stated
        ("Hands-on experience with cloud infrastructure tooling", {"alternatives": ["Pulumi", "Terraform"]}, "alternative", [rules.TERRAFORM], ["b-000005"]),
        ("5+ years with a major cloud provider", {"alternatives": ["GCP", "AWS"]}, "alternative", ["Engineer | 2016 - 2021", rules.TERRAFORM], ["b-000005"]),
    ],
)
def test_a_stated_requirement_is_met_with_the_masters_own_line_and_not_asked(requirement, more, rule, evidence, sources) -> None:
    attempt = _assess([BASE_ROW, _row(requirement, **more)], [_question(requirement)])
    row = _stored(attempt, requirement)
    assert row.status.value == "met"
    assert list(row.resume_evidence) == evidence and list(row.sources) == sources  # the master's line(s), word for word, by id
    assert _open(attempt) == [] and list(attempt.parsed.questions) == []
    assert attempt.parsed.verdict.value == MATCHED and _decision(attempt) == "suggest"  # the gate's reading, not the model's "pending"
    assert attempt.attempts == 1 and _rules(attempt) == [rule]


@pytest.mark.parametrize(
    ("row", "rule"),
    [
        # the model met React with its own line and asked about it all the same
        (_row("Experience with React", "met", resume_evidence=[rules.REACT], sources=["b-000001"]), "met_row_question"),
        # the degree track is met by a line the prompt showed; the question can only be about the other track
        (_row(DEGREE, "met", resume_evidence=["B.S. Computer Science | 2009 - 2013"], sources=["e-000001"]), "alternative_track_question"),
        (_row("A major cloud provider", "met", sources=["b-000005"], alternatives=["GCP", "Azure", "AWS"]), "met_row_question"),
        (_row("A build system", "met", sources=["b-000005"], alternatives=["Bazel", "Buck"]), "alternative_track_question"),
    ],
)
def test_a_question_on_a_row_that_is_met_already_is_not_asked_and_the_row_is_what_it_was(row, rule) -> None:
    requirement = str(row["requirement"])
    asked = _assess([BASE_ROW, dict(row)], [_question(requirement)])
    plain = _assess([BASE_ROW, dict(row)], [], verdict=MATCHED)  # the same answer, had the model not asked
    assert _open(asked) == [] and list(asked.parsed.questions) == []
    assert _stored(asked, requirement) == _stored(plain, requirement) and _stored(asked, requirement).status.value == "met"
    assert asked.parsed.verdict.value == MATCHED and _decision(asked) == "suggest"
    assert _rules(asked) == [rule] and asked.extras.stated_questions == ("tooling:it",)


def test_each_rule_is_counted_under_its_own_name_and_a_dropped_question_is_not_a_settled_row() -> None:
    rows = [
        BASE_ROW,
        _row("Experience with React"),
        _row("Experience with infrastructure administration"),
        _row("Experience building billing software in TypeScript"),
        _row("Experience with React/TypeScript"),
        _row("Experience with relational databases such as PostgreSQL"),
        _row("Hands-on experience with cloud infrastructure tooling", alternatives=["Pulumi", "Terraform"]),
        _row("Experience with Docker"),
        _row("Experience with Terraform", "met", sources=["b-000005"]),
        _row(DEGREE, "met", resume_evidence=["B.S. Computer Science | 2009 - 2013"], sources=["e-000001"]),
    ]
    questions = [_question("Experience with React", "a"), _question("Experience with Terraform", "b"), _question(DEGREE, "c")]
    checks = _checks_of(_assess(rows, questions).extras)
    assert checks is not None and checks.settled_rows == 7 and checks.questions_dropped == 3
    assert checks.settled_by_rule == (
        ("alternative", 1), ("alternative_track_question", 1), ("compound_name", 1), ("kind_words", 1), ("line_takes_back_other", 1),
        ("master_phrase", 1), ("met_row_question", 1), ("skills_line", 1), ("word_beside_term", 1),
    )
    assert all(text not in json.dumps(checks.to_json()) for text in ("React", "Terraform", "infrastructure administration", "b-000005"))


# --- negatives: what the master does not state is still asked, and nothing is made worse ---------------------


@pytest.mark.parametrize(
    ("requirement", "more"),
    [
        ("Experience with Helm", {}),  # not in the master
        ("Experience with jQuery", {}),  # only in the line that replaced it
        ("Experience with Oracle", {}),  # "the Oracle cluster was retired"
        ("Experience with Riak", {}),  # "No production use of Cassandra, Riak or Couchbase"
        ("Experience with Pulsar", {}),  # "did not adopt it"
        ("Experience with Java", {}),  # the master says JavaScript
        ("Experience with Java/JavaScript", {}),
        ("Willingness to go on call", {}),  # "go" the verb; the master lists Go
        ("Experience with React/Vue", {}),  # a slashed name: every name is required
        ("Experience with infrastructure management", {}),  # not the master's words
        ("Experience building reporting software in TypeScript", {}),  # "reporting" and TypeScript are in two different lines
        ("Experience building payments software in TypeScript", {}),
        ("Experience leading teams using React", {}),  # asks for more than the tool
        ("Experience with payments systems in Go", {}),
        ("Experience managing platforms such as AWS", {}),
        ("Experience with relational databases such as MySQL", {}),
        ("Hands-on experience with cloud infrastructure tooling", {"alternatives": ["Pulumi", "Ansible"]}),  # no alternative is stated
        ("Experience leading teams that build with React or Vue", {"alternatives": ["React", "Vue"]}),  # people, not a tool
        ("AWS or GCP certification", {"alternatives": ["AWS", "GCP"]}),
        ("9+ years with a major cloud provider", {"alternatives": ["GCP", "AWS"]}),  # the roles that hold AWS cover five
        ("Experience with Terraform, Pulumi and Ansible", {"alternatives": ["Terraform", "Pulumi", "Ansible"]}),  # a SET
    ],
)
def test_a_requirement_the_master_does_not_settle_is_left_exactly_as_it_came(requirement: str, more: dict[str, object]) -> None:
    attempt = _assess([BASE_ROW, _row(requirement, **more)], [_question(requirement)])
    row = _stored(attempt, requirement)
    assert row.status.value == "unclear" and list(row.resume_evidence) == [] and list(row.sources) == []
    assert _open(attempt) == [requirement] and list(attempt.parsed.questions) == [f"Do you have this: {requirement}?"]
    assert attempt.parsed.verdict.value == PENDING and _decision(attempt) == "hold_question"
    assert _rules(attempt) == []


@pytest.mark.parametrize(
    "requirement",
    [
        "Experience with frameworks such as React, Vue, Svelte",  # named examples with no "or": the master shows one of three
        "Experience with tools like Docker, Podman, LXC",
    ],
)
def test_a_named_set_the_master_shows_only_part_of_is_not_settled(requirement: str) -> None:
    # 0.1.11.3 met these by ONE item (the re-run's one row a reader did not accept). Now every named item is required.
    attempt = _assess([BASE_ROW, _row(requirement)], [_question(requirement)])
    assert _stored(attempt, requirement).status.value == "unclear" and _open(attempt) == [requirement]
    assert attempt.parsed.verdict.value == PENDING and _rules(attempt) == []


@pytest.mark.parametrize(
    "row",
    [
        _row("Experience with Helm", "met", sources=["b-000002"]),  # met by the model, not stated, not a track
        _row("Experience with Vue or Angular", "met"),  # a track, but no line of the prompt is cited
        _row("Experience with Kubernetes", "unmet", "askable"),  # an unmet row is never this check's
    ],
)
def test_a_question_this_check_cannot_answer_stays_and_the_row_is_never_touched(row) -> None:
    requirement = str(row["requirement"])
    asked = _assess([BASE_ROW, dict(row)], [_question(requirement)])
    plain = _assess([BASE_ROW, dict(row)], [], verdict=MATCHED)
    assert _stored(asked, requirement) == _stored(plain, requirement)  # a met row is never made worse (or better); nor an unmet one
    assert _open(asked) == [requirement] and asked.parsed.verdict.value == PENDING and _rules(asked) == []


def test_a_prompt_that_showed_no_ids_is_read_as_before() -> None:
    answer = {"verdict": PENDING, "matrix": [BASE_ROW, _row("Experience with React")], "questions": [_question("Experience with React")], "suggestions": [], "not_a_match_reason": None}
    attempt = assess_once(_Binding(answer, answer), JOB, build_assess_context(resume_text=MASTER), parse=_parse_body)
    assert attempt.ok and _stored(attempt, "Experience with React").status.value == "unclear" and _open(attempt) == ["Experience with React"]
