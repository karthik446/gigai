"""0.1.11.4 Q4 (ASSESS-100 re-run: 7 untruthful suggestions of 290, 1 invented city): what CODE guarantees about a suggestion's claim.

Offline, synthetic: every posting, line, company and city below is invented and only has the SHAPE of a case the
re-run counted. A scripted binding stands in for the model; the end outcome is the assessment ``assess_once`` returns
through the product parser (what is stored and shown), and the counts the stored ``checks`` record carries.

1. EVERY TERM. A "silent / does not mention" claim that names several terms is false as soon as the master states one
   of them: the suggestion is dropped whole. A name of several words is one term.
2. THE ROLES. "X is only in Skills", "X is only listed", "X is not shown in a role", "no bullet mentions X" are false
   when a bullet of a role or a project states X.
3. A CITY NOBODY NAMED. A suggestion or a question that names, in a location wording, a city that no text the prompt
   showed names is dropped.

Code only removes: no row's status, class, evidence or sources change, and a term the master does not have keeps its
suggestion and its question.
"""

from __future__ import annotations

import pytest

from gigai.scout import suggestion_check as check
from gigai.scout.assessment_core import AssessJob, assess_once, build_assess_context
from gigai.scout.find_jobs.assess_contracts import AssessChecks
from gigai.scout.quick_assess import _checks_of, _parse_body

from tests.behaviors.scout_find_jobs.test_silent_claims_and_single_line_citations import MATCHED, _Binding, _gap, _row

MASTER = """<!-- gigai-master:1 -->

## Summary

- Platform engineer with ten years on distributed services. <!-- id:sum-000001 -->

## Experience

### NORTHWIND, Lisbon <!-- id:r-000001 -->
**Staff Engineer | 2022 - Present**
- Built a service SDK in Go that adds tracing to forty functions. <!-- id:b-000001 -->
- Wrote the billing dashboard in React with a Node layer. <!-- id:b-000002 -->

### CONTOSO <!-- id:r-000002 -->
**Engineer | 2019 - 2022**
- Moved a batch ingest job to a Kafka queue worker, raising uptime to 99.9%. <!-- id:b-000003 -->
- Rewrote the JavaScript build so a release takes four minutes. <!-- id:b-000004 -->

## Projects

### Homelab <!-- id:p-000001 -->
- Ran a three-node Nomad cluster for home services. <!-- id:b-000005 -->

## Skills

- Go, Node, React, Kafka, Terraform, Kubernetes, JavaScript <!-- id:s-000001 -->

## Education

### State University <!-- id:e-000001 -->
- Capstone: a Haskell type checker. <!-- id:b-000006 -->
"""
IDS = ("sum-000001", "b-000001", "b-000002", "b-000003", "b-000004", "b-000005", "b-000006", "s-000001")
LINES = check.parse_master_lines(MASTER)
JOB = AssessJob(
    "Staff Engineer", "Acme Robotics", "Austin, TX",
    "Acme Robotics builds warehouse robots. Requirements: Go services; Kafka and Flink streaming; React; Terraform; Java. "
    "Nice to have: Helm. Hybrid from our Austin office two days a week.",
)
ROWS = [
    _row("Go services", "met", ["b-000001"], ["Go SDK"], "hard"),
    _row("Kafka and Flink streaming", "unclear", [], []),
    _row("React", "met", ["b-000002"], ["x"]),
    _row("Terraform", "unclear", [], []),
    _row("Java", "unclear", [], []),
    _row("Helm", "unclear", [], []),
    _row("Hybrid from the Austin office", "unclear", [], []),
]


def _ctx(**more: object):
    return build_assess_context(resume_text=MASTER, resume_ids=IDS, pick_lines=20, location="Lisbon, Portugal", countries=("us",), **more)


def _assess(suggested: list[object] | None = None, questions: list[object] | None = None, *, rows: list[dict[str, object]] | None = None, verdict: str = MATCHED, outputs: int = 1):
    answer = {"verdict": verdict, "matrix": rows or ROWS, "questions": questions or [], "suggestions": suggested or [], "not_a_match_reason": None}
    binding = _Binding(*([answer] * outputs))
    attempt = assess_once(binding, JOB, _ctx(), parse=_parse_body)
    assert attempt.ok, attempt
    return attempt, binding


def _reasons(attempt) -> list[str]:
    return [reason for _kind, reason in attempt.extras.checked_suggestions]


# --- 1. every term of a "silent" claim --------------------------------------------------------------------------

EVERY_TERM_UNTRUE = [
    _gap("The resume does not mention Flink or Kafka.", "Kafka and Flink streaming"),  # the FIRST term is not there, the second is
    _gap("The resume is silent on Helm, Flink and Terraform.", "Terraform"),  # the third is in the skills line
    _gap("There is no mention of Flink, Kafka or Helm.", "Kafka and Flink streaming"),
    _gap("The resume does not mention building with React.", "React"),  # a "doing it" word names nothing
    _gap("Helm is covered by your answer, but Terraform is not mentioned.", "Terraform"),  # the second claim of the sentence
]
EVERY_TERM_TRUE = [
    _gap("The resume does not mention Flink or Helm.", "Kafka and Flink streaming"),  # neither is in the master
    _gap("The resume is silent on Kafka Streams.", "Kafka and Flink streaming"),  # one name of two words: a line says Kafka, none says Kafka Streams
    _gap("The resume does not mention React Native.", "React"),
    _gap("The resume does not mention Java.", "Java"),  # JavaScript is not Java
    _gap("The resume is silent on Java or Flink.", "Java"),
    _gap("The resume does not mention Rust.", "Go services"),
    _gap("The resume does not state 6 years of Go or Kafka.", "Go services"),  # a number: read as before, and no line says 6 years
    _gap("The resume does not show leading a Kafka migration.", "Kafka and Flink streaming"),  # about more than a term
]


@pytest.mark.parametrize("suggestion", EVERY_TERM_UNTRUE, ids=[str(item["why"]) for item in EVERY_TERM_UNTRUE])
def test_a_silent_claim_is_false_when_the_master_states_any_one_of_the_terms_it_names(suggestion: dict[str, object]) -> None:
    attempt, _binding = _assess([suggestion])
    assert attempt.extras.structured_suggestions == () and suggestion["why"] not in attempt.parsed.suggestions
    assert _reasons(attempt) == ["silent_but_in_master"] and attempt.extras.dropped_suggestions == 1


@pytest.mark.parametrize("suggestion", EVERY_TERM_TRUE, ids=[str(item["why"]) for item in EVERY_TERM_TRUE])
def test_a_claim_whose_terms_the_master_does_not_state_keeps_its_suggestion(suggestion: dict[str, object]) -> None:
    attempt, _binding = _assess([suggestion])
    assert [item.why for item in attempt.extras.structured_suggestions] == [suggestion["why"]]
    assert suggestion["why"] in attempt.parsed.suggestions and attempt.extras.dropped_suggestions == 0


def test_go_the_verb_is_not_go_the_language() -> None:
    lines = check.parse_master_lines("## Experience\n\n### A <!-- id:r-000001 -->\n- Helped the team go live with a Java service. <!-- id:b-000001 -->\n")
    assert check.false_claim("The resume does not mention Go.", lines) is None  # "go live" is the verb
    assert check.false_claim("Go is only listed in Skills.", lines) is None
    assert check.false_claim("The resume does not mention Go or Java.", lines) == "silent_but_in_master"  # Java is there
    assert check.false_claim("The resume does not mention JavaScript.", lines) is None  # Java is not JavaScript


# --- 2. "only in Skills" / "only listed" / "not shown in a role" ----------------------------------------------

ROLE_UNTRUE = [
    _gap("Kafka is only in Skills.", "Kafka and Flink streaming"),
    _gap("Kafka appears only in your Skills line; add a bullet that shows it.", "Kafka and Flink streaming"),
    _gap("Kafka is listed only in the Skills section.", "Kafka and Flink streaming"),
    _gap("Kafka is only listed as a skill.", "Kafka and Flink streaming"),
    _gap("Kafka is in your Skills line only.", "Kafka and Flink streaming"),
    _gap("Kafka is only listed, not shown in use.", "Kafka and Flink streaming"),
    _gap("Kafka is only listed.", "Kafka and Flink streaming"),
    _gap("Kafka is not shown in a role.", "Kafka and Flink streaming"),
    _gap("Kafka is not mentioned in any of your roles.", "Kafka and Flink streaming"),
    _gap("Kafka is not shown in your experience bullets.", "Kafka and Flink streaming"),
    _gap("The resume does not show Kafka in any role.", "Kafka and Flink streaming"),
    _gap("No bullet mentions Kafka.", "Kafka and Flink streaming"),
    _gap("None of your roles mention Kafka or Flink.", "Kafka and Flink streaming"),  # every term: false about Kafka
    _gap("Your bullets do not mention Kafka.", "Kafka and Flink streaming"),
    _gap("Only the Skills line mentions Kafka.", "Kafka and Flink streaming"),
    _gap("Your resume lists Kafka only in Skills.", "Kafka and Flink streaming"),
    _gap("Nomad is not shown in a role.", "Helm"),  # a project bullet holds it
    _gap("It is only in Skills.", "Kafka and Flink streaming", posting_phrase="Kafka"),  # a pointer: the claim is about the phrase
    {"kind": "order", "line": "b-000002", "requirement": "React", "why": "React is only in Skills; lead with this line."},
]
ROLE_TRUE = [
    _gap("Terraform is only in Skills.", "Terraform"),  # true: no bullet says Terraform
    _gap("Terraform is not shown in a role.", "Terraform"),
    _gap("Kubernetes appears only in your Skills line.", "Terraform"),
    _gap("No bullet mentions Terraform.", "Terraform"),
    _gap("Haskell is not shown in a role.", "Helm"),  # an Education line is no role
    _gap("Helm is only listed.", "Helm"),  # not in the master at all
    _gap("Kafka is not shown in the first role.", "Kafka and Flink streaming"),  # one named place: not judged (and true)
    _gap("Kafka is only listed once.", "Kafka and Flink streaming"),  # says something else
    _gap("Kafka is the only skill the posting repeats.", "Kafka and Flink streaming"),
    _gap("Kafka leadership is only in Skills.", "Kafka and Flink streaming"),  # about more than a term
    _gap("Java is only in Skills.", "Java"),  # JavaScript is in a bullet, Java is in no line
]


@pytest.mark.parametrize("suggestion", ROLE_UNTRUE, ids=[str(item["why"]) for item in ROLE_UNTRUE])
def test_a_claim_that_a_term_is_only_in_skills_or_in_no_role_is_dropped_when_a_role_or_project_bullet_states_it(suggestion: dict[str, object]) -> None:
    attempt, _binding = _assess([suggestion])
    assert attempt.extras.structured_suggestions == () and suggestion["why"] not in attempt.parsed.suggestions
    assert _reasons(attempt) == ["only_skills_but_in_role"] and attempt.extras.dropped_suggestions == 1


@pytest.mark.parametrize("suggestion", ROLE_TRUE, ids=[str(item["why"]) for item in ROLE_TRUE])
def test_a_true_claim_about_the_roles_keeps_its_suggestion(suggestion: dict[str, object]) -> None:
    attempt, _binding = _assess([suggestion])
    assert [item.why for item in attempt.extras.structured_suggestions] == [suggestion["why"]]
    assert suggestion["why"] in attempt.parsed.suggestions and attempt.extras.dropped_suggestions == 0


def test_a_plain_string_suggestion_gets_the_same_checks_and_no_row_is_changed() -> None:
    plain, _binding = _assess()
    both, _binding = _assess([
        "Kafka is only in Skills.", "The resume does not mention Flink or Kafka.", "Terraform is only in Skills.",
        "If you can commute to the Denver office, say so.", ROLE_UNTRUE[0], ROLE_TRUE[0],
    ])
    assert list(both.parsed.suggestions) == ["Terraform is only in Skills.", ROLE_TRUE[0]["why"]]
    assert both.parsed.matrix == plain.parsed.matrix and both.parsed.verdict == plain.parsed.verdict  # a met row is never made worse


# --- 3. a city nobody named ------------------------------------------------------------------------------------

UNKNOWN_CITY = [
    "The role is based in Denver; say whether you can be there.",
    "Are you able to commute to the Denver office twice a week?",
    "The posting asks for people located in Denver, CO.",
    "Can you work from Denver two days a week?",
    "Are you already Denver-based, or would you relocate?",
    "Would you relocate to San Mateo for this role?",  # "San" is a part of many names; "Mateo" is nobody's
    "The team is hybrid in the Denver area.",
]
KNOWN_OR_NO_PLACE = [
    "Are you able to commute to the Austin office twice a week?",  # the posting names it
    "The role is based in Austin, TX; you are located in Lisbon.",  # the posting's city, and the setup's own
    "You are based in Lisbon, Portugal: say whether you would relocate.",
    "Lead with the Initech migration if you ran it.",  # a capitalised company name, in no location wording
    "Say whether you worked with the Globex team on warehouse robots.",
    "The Acme Robotics office expects two days a week on site.",  # the company, which the posting names
    "Are you authorized to work in the United States?",  # a country is never judged
    "Are you based in Germany or elsewhere in Europe?",
    "Can you work from New York or California?",  # a state is never judged
    "Are you able to work from NYC?",  # short capitals are never judged
    "If you have experience with Microsoft Office, say so.",  # "Office" the product is no office
    "Have you moved a service to Kubernetes in production?",
    "Is your experience based in Go or in Java?",  # a tool of the alias table is no city
]


@pytest.mark.parametrize("text", UNKNOWN_CITY)
def test_a_suggestion_or_a_question_that_names_a_city_nobody_gave_is_dropped(text: str) -> None:
    question = {"question_id": "location:city", "question": text, "requirement": "Helm"}  # on a nice-to-have row: nothing holds for it
    attempt, _binding = _assess([_gap(text, "Helm"), text], [question, text])
    assert attempt.extras.structured_suggestions == () and text not in attempt.parsed.suggestions
    assert _reasons(attempt) == ["place_not_in_posting"]
    assert text not in attempt.parsed.questions and attempt.parsed.structured_questions == ()
    assert attempt.extras.unplaced_questions == 2
    checks = _checks_of(attempt.extras)
    assert checks is not None and checks.questions_removed == (("place_not_in_posting", 2),) and dict(checks.suggestions_dropped) == {"place_not_in_posting": 1}
    assert "Denver" not in str(checks.to_json()) and AssessChecks.from_json(checks.to_json()) == checks


@pytest.mark.parametrize("text", KNOWN_OR_NO_PLACE)
def test_a_place_the_posting_the_master_or_the_setup_names_and_a_capitalised_company_name_stay(text: str) -> None:
    question = {"question_id": "location:city", "question": text, "requirement": "Helm"}
    attempt, _binding = _assess([_gap(text, "Helm"), text], [question, text])
    assert [item.why for item in attempt.extras.structured_suggestions] == [text] and attempt.parsed.suggestions.count(text) == 2
    assert [item.question for item in attempt.parsed.structured_questions] == [text] and attempt.parsed.questions.count(text) == 2
    assert attempt.extras.unplaced_questions == 0 and attempt.extras.dropped_suggestions == 0
    checks = _checks_of(attempt.extras)
    assert checks is not None and checks.questions_removed == () and "questions_removed" not in checks.to_json()


def test_a_must_have_row_whose_question_named_an_unknown_city_is_asked_in_the_rows_own_words() -> None:
    rows = [_row("Go services", "met", ["b-000001"], ["Go SDK"], "hard"), _row("Hybrid from the Austin office", "unclear", [], [], "hard")]
    invented = {"question_id": "location:onsite", "question": "Can you be in the Denver office two days a week?", "requirement": "Hybrid from the Austin office"}
    attempt, binding = _assess(questions=[invented], rows=rows, verdict="pending_user_answers", outputs=2)
    assert binding.port.outputs == []  # the first answer is refused (the row holds with no question): the one retry
    asked = [item.question for item in attempt.parsed.structured_questions]
    assert len(asked) == 1 and "Denver" not in asked[0] and "Hybrid from the Austin office" in asked[0]
    assert all("Denver" not in text for text in attempt.parsed.questions) and attempt.parsed.verdict.value == "pending_user_answers"
    assert [(row.requirement, row.status.value) for row in attempt.parsed.matrix] == [("Go services", "met"), ("Hybrid from the Austin office", "unclear")]
    # The same answer with the posting's own city is kept as the model wrote it, with no retry.
    true = {**invented, "question": "Can you be in the Austin office two days a week?"}
    kept, binding = _assess(questions=[true], rows=rows, verdict="pending_user_answers", outputs=2)
    assert len(binding.port.outputs) == 1 and [item.question for item in kept.parsed.structured_questions] == [true["question"]]


def test_the_place_check_reads_only_location_wordings_and_needs_the_prompts_text() -> None:
    known = "Acme builds robots in Austin. The candidate lives in Lisbon."
    assert check.names_unknown_place("Are you based in Denver?", known)
    assert not check.names_unknown_place("Are you based in Denver?", "")  # outside assess_once: no check
    assert not check.names_unknown_place("Are you based in austin?", known) and not check.names_unknown_place("Are you based in Austin?", known)
    assert not check.names_unknown_place("Denver is where the Initech team met Globex.", known)  # no location wording
    assert not check.names_unknown_place("Are you based in the Bay Area?", known)  # parts of many names only
    assert check.without_unknown_places(["Based in Denver?", {"question": "Based in Austin?"}, {"question": "Based in Denver?"}, 7], "question", known) == (
        [{"question": "Based in Austin?"}, 7], 2,
    )
