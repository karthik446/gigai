"""0.1.11.3 Q2 (T1): a question the master already answers is not asked. The END outcome: the assessment that is stored.

Offline, synthetic: the master, the postings and every answer below are invented and only have the SHAPE of the kinds
measured (a tool a bullet names, a tool in the skills line, years a line states, years the role dates cover, an
alternative track). A scripted binding stands in for the model; ``assess_once`` + the product's parser give the
``AssessmentBody`` the product stores and the job page shows.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gigai.scout import resume_gate
from gigai.scout.assessment_core import AssessJob, assess_once, build_assess_context
from gigai.scout.quick_assess import _parse_body

MATCHED, PENDING = "matched_above_threshold", "pending_user_answers"

# The evidence view as the assess prompt shows it (``master_selection.evidence_view(ids=True)``): the skills line has no id.
MASTER = """## Summary

- Platform engineer with ten years on distributed services. <!-- id:sum-000001 -->

## Experience

### NORTHWIND <!-- id:r-000001 -->
**Staff Engineer | Jun 2021 - Present**
<!-- private note: lead with the Helm work -->

- Ran the on-call rota for six services on AWS and cut resolution time by half. <!-- id:b-000001 -->
- Built the checkout UI in React and TypeScript for two product teams. <!-- id:b-000002 -->

### CONTOSO <!-- id:r-000002 -->
Engineer | 2016 - 2021

- Moved a batch ingest job to a Go queue worker on AWS, raising uptime to 99.9%. <!-- id:b-000003 -->
- Wrote the REST API layer for the billing service in JavaScript. <!-- id:b-000004 -->

### FABRIKAM <!-- id:r-000003 -->
Support Analyst | 2013 - 2016

- Answered consumer-facing support tickets and worked with leadership on escalations. <!-- id:b-000005 -->

## Skills

- Go, JavaScript, C++, C#, SQL, Kubernetes, Docker, AWS (EC2, S3)

## Education

### State University <!-- id:e-000001 -->
B.S. Computer Science | 2009 - 2013
"""
IDS = ("sum-000001", "b-000001", "b-000002", "b-000003", "b-000004", "b-000005")
SKILLS_LINE = "Go, JavaScript, C++, C#, SQL, Kubernetes, Docker, AWS (EC2, S3)"
REACT_LINE = "Built the checkout UI in React and TypeScript for two product teams."
JOB = AssessJob("Staff Engineer", "Acme", "Remote", "Requirements: Go services. React. Kubernetes. 8+ years on AWS.")


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


def _row(requirement: str, status: str = "unclear", klass: str = "askable", **more: object) -> dict[str, object]:
    evidence = ["Moved a batch ingest job to a Go queue worker."] if status == "met" else ["The resume does not show this."]
    return {"requirement": requirement, "class": klass, "class_basis": f"Requirements: {requirement}"[:200], "status": status, "resume_evidence": evidence, **more}


def _question(requirement: str, slug: str) -> dict[str, object]:
    return {"question_id": f"tooling:{slug}", "question": f"Do you have this: {requirement}?", "requirement": requirement}


GO_ROW = _row("Go services", "met", "hard", sources=["b-000003"])


def _assess(rows: list[dict[str, object]], questions: list[dict[str, object]], *, verdict: str = PENDING, ctx=None, outputs: int = 1):
    answer = {"verdict": verdict, "matrix": rows, "questions": questions, "suggestions": [], "not_a_match_reason": None}
    attempt = assess_once(_Binding(*[answer] * outputs), JOB, ctx or build_assess_context(resume_text=MASTER, resume_ids=IDS, pick_lines=20), parse=_parse_body)
    assert attempt.ok, attempt
    return attempt


def _stored(attempt, requirement: str):
    return next(row for row in attempt.parsed.matrix if row.requirement == requirement)


def _open(attempt) -> list[str]:
    return [item.requirement for item in attempt.parsed.structured_questions]


def _settled(attempt) -> tuple[object, ...]:
    # Read with a default so the negative tests say the same thing on a tree from before the check.
    return (*getattr(attempt.extras, "met_by_master", ()), *getattr(attempt.extras, "stated_questions", ()))


def _decision(attempt) -> str:
    body = attempt.parsed
    return resume_gate.gate(body.matrix, body.structured_questions, body.verdict).decision


# --- the kinds measured: the question is not asked, the row is met with the master's own line ----------------


def test_a_tool_a_bullet_names_is_met_with_that_line_and_its_question_is_not_asked() -> None:
    attempt = _assess([GO_ROW, _row("Experience with React", klass="hard")], [_question("Experience with React", "react")])
    row = _stored(attempt, "Experience with React")
    assert row.status.value == "met"
    assert list(row.resume_evidence) == [REACT_LINE] and list(row.sources) == ["b-000002"]  # the line, word for word, by id
    assert _open(attempt) == [] and list(attempt.parsed.questions) == []
    assert attempt.parsed.verdict.value == MATCHED and _decision(attempt) == "suggest"  # the gate's reading, not the model's "pending"
    assert attempt.attempts == 1


def test_a_tool_in_the_skills_line_is_met_with_the_skills_line() -> None:
    attempt = _assess([GO_ROW, _row("Strong Kubernetes experience")], [_question("Strong Kubernetes experience", "kubernetes")])
    row = _stored(attempt, "Strong Kubernetes experience")
    assert row.status.value == "met" and list(row.resume_evidence) == [SKILLS_LINE]
    assert list(row.sources) == []  # the view prints the skills line without an id
    assert _open(attempt) == [] and attempt.parsed.verdict.value == MATCHED


def test_years_a_line_states_are_met_with_that_line() -> None:
    requirement = "8+ years of software engineering experience"
    attempt = _assess([GO_ROW, _row(requirement, klass="hard")], [_question(requirement, "years")])
    row = _stored(attempt, requirement)
    assert row.status.value == "met" and list(row.resume_evidence) == ["Platform engineer with ten years on distributed services."]
    assert list(row.sources) == ["sum-000001"] and _open(attempt) == [] and attempt.parsed.verdict.value == MATCHED


def test_years_the_role_dates_cover_are_met_with_the_dated_lines_and_the_bullets() -> None:
    attempt = _assess([GO_ROW, _row("8+ years on AWS", klass="hard")], [_question("8+ years on AWS", "aws")])
    row = _stored(attempt, "8+ years on AWS")
    assert row.status.value == "met"
    assert list(row.resume_evidence) == [
        "Staff Engineer | Jun 2021 - Present",
        "Ran the on-call rota for six services on AWS and cut resolution time by half.",
        "Engineer | 2016 - 2021",
        "Moved a batch ingest job to a Go queue worker on AWS, raising uptime to 99.9%.",
    ]
    assert list(row.sources) == ["b-000001", "b-000003"]
    assert _open(attempt) == [] and attempt.parsed.verdict.value == MATCHED


def test_an_alternative_track_met_by_the_years_is_not_asked() -> None:
    requirement = "Master's degree in Robotics, or 8+ years of software engineering experience"
    attempt = _assess([GO_ROW, _row(requirement, klass="hard")], [_question(requirement, "degree")])
    assert _stored(attempt, requirement).status.value == "met" and _open(attempt) == []
    assert list(_stored(attempt, requirement).resume_evidence) == ["Platform engineer with ten years on distributed services."]


def test_one_stated_alternative_of_a_list_is_enough() -> None:
    requirement = "Experience with AWS, GCP, or similar cloud platforms"
    attempt = _assess([GO_ROW, _row(requirement, alternatives=["AWS", "GCP"])], [_question(requirement, "cloud")])
    row = _stored(attempt, requirement)
    assert row.status.value == "met" and list(row.sources) == ["b-000001"] and _open(attempt) == []


def test_an_unclear_must_have_the_master_states_does_not_spend_the_retry_or_get_a_code_question() -> None:
    # No question on the row: before, the first answer was refused (the one retry) and code then asked the row's question.
    attempt = _assess([GO_ROW, _row("Experience with React", klass="hard")], [], verdict=MATCHED, outputs=2)
    assert attempt.attempts == 1 and _open(attempt) == [] and attempt.extras.asked_by_code == ()
    assert _stored(attempt, "Experience with React").status.value == "met" and attempt.parsed.verdict.value == MATCHED


def test_a_kept_question_takes_the_place_a_stated_one_frees_under_the_cap() -> None:
    # Five must-have questions, at most four are asked: the stated one goes, so all four others are asked now.
    others = [f"Experience with Tool{letter}" for letter in "VWXY"]
    rows = [GO_ROW, _row("Experience with React", klass="hard"), *(_row(text) for text in others)]
    questions = [_question("Experience with React", "react"), *(_question(text, f"tool{index}") for index, text in enumerate(others))]
    attempt = _assess(rows, questions)
    assert _open(attempt) == others and attempt.extras.capped_mandatory_questions == ()
    assert list(attempt.parsed.questions) == [f"Do you have this: {text}?" for text in others]
    assert attempt.parsed.verdict.value == PENDING


def test_what_was_settled_is_recorded_beside_the_answer() -> None:
    attempt = _assess([GO_ROW, _row("Experience with React"), _row("Strong Kubernetes experience")], [_question("Experience with React", "react")])
    assert [rule for _row_id, rule in attempt.extras.met_by_master] == ["master_line", "skills_line"]
    assert attempt.extras.stated_questions == ("tooling:react",)


# --- negatives: what the master does not state is still asked, and nothing is made worse ---------------------


def test_a_term_the_master_does_not_state_keeps_its_question_and_the_job_stays_pending() -> None:
    rows = [GO_ROW, _row("Experience with React", klass="hard"), _row("Experience with Helm", klass="hard")]
    attempt = _assess(rows, [_question("Experience with React", "react"), _question("Experience with Helm", "helm")])
    helm = _stored(attempt, "Experience with Helm")
    assert helm.status.value == "unclear" and list(helm.resume_evidence) == ["The resume does not show this."]
    assert _open(attempt) == ["Experience with Helm"] and list(attempt.parsed.questions) == ["Do you have this: Experience with Helm?"]
    assert attempt.parsed.verdict.value == PENDING and _decision(attempt) == "hold_question"
    assert _stored(attempt, "Experience with React").status.value == "met"  # the private note's "Helm" is never read


@pytest.mark.parametrize(
    "requirement",
    [
        "Experience with Java",  # the master says JavaScript
        "Experience with C",  # the master says C++ and C#
        "Willingness to go on call",  # "go" the verb; the master lists Go
        "Go above and beyond for customers",
        "Experience building consumer-facing products",  # a scope word is not a key term, though a line holds it
        "Strong leadership",  # "leadership" is in a line, in another sense
        "Experience with Fabrikam systems",  # a company heading is not evidence
        "Experience leading teams using React",  # asks for more than the tool
        "Experience with React and Vue",  # every term is required
        "25+ years on AWS",  # the role dates do not cover it
        "10+ years of experience",  # of what? not this check's to read
        "5+ years of experience with cloud platforms",  # no key term: the role dates do not show the subject
        "Master's degree in Robotics or equivalent experience",
    ],
)
def test_a_requirement_the_master_does_not_settle_is_left_exactly_as_it_came(requirement: str) -> None:
    attempt = _assess([GO_ROW, _row(requirement, klass="hard")], [_question(requirement, "it")])
    row = _stored(attempt, requirement)
    assert row.status.value == "unclear" and list(row.resume_evidence) == ["The resume does not show this."]
    assert _open(attempt) == [requirement] and attempt.parsed.verdict.value == PENDING
    assert _settled(attempt) == ()


def test_a_met_row_and_an_unmet_row_are_never_touched() -> None:
    # The model met Go with its own source; it found React unmet (nice to have). Neither is this check's.
    rows = [GO_ROW, _row("Experience with React", "unmet", "nice_to_have"), _row("Experience with Docker", "met", sources=["b-000001"])]
    attempt = _assess(rows, [], verdict=MATCHED)
    assert _stored(attempt, "Experience with React").status.value == "unmet"
    docker = _stored(attempt, "Experience with Docker")
    assert docker.status.value == "met" and list(docker.sources) == ["b-000001"]
    assert list(docker.resume_evidence) == ["Ran the on-call rota for six services on AWS and cut resolution time by half."]
    assert list(_stored(attempt, "Go services").sources) == ["b-000003"] and _settled(attempt) == ()


def test_a_prompt_that_showed_no_ids_is_read_as_before() -> None:
    plain = build_assess_context(resume_text=MASTER)
    attempt = _assess([GO_ROW, _row("Experience with React", klass="hard")], [_question("Experience with React", "react")], ctx=plain)
    assert _stored(attempt, "Experience with React").status.value == "unclear" and _open(attempt) == ["Experience with React"]
    assert attempt.parsed.verdict.value == PENDING
