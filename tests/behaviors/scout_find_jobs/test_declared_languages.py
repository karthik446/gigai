"""0.1.11.12 dossier-A (gigai-zu4): T8, a declared programming language. No model.

Operator decision (2026-10-30): a language the candidate declares on the skills line of the master (or in a saved
answer) is a fact about the candidate, not a dated one; a mandatory row that needs only that language is ``met``
whatever the recency of the roles that name it (T1/T2 do not lower it), and the question on it is dropped. Years
rows ("8+ years of Java") stay T3, a row that needs a framework or infrastructure beside the language stays T1/T2, an
``unmet`` row stays (T7), and a cited answer that denies the language keeps the row as it came.
"""

from __future__ import annotations

import pytest

from gigai.scout.assess_master import strict_master
from gigai.scout.assess_strict import ANSWERED, strict_rows
from tests.behaviors.scout_find_jobs.test_assess_strict import MASTER, TODAY, _row
from tests.behaviors.scout_find_jobs.test_evidence_suggestion_location_checks import MATCHED, PENDING, _answer, _assess
from tests.behaviors.scout_find_jobs.test_evidence_suggestion_location_checks import _row as _live_row

GO_DEEP = "Strong experience building and operating production backend systems in Go, our primary backend language"


def _check(row: dict):
    return row, strict_rows([row], strict_master(MASTER, today=TODAY))


# --- the declared set --------------------------------------------------------------------------------------


def test_the_master_keeps_the_text_of_every_skills_line_with_or_without_an_id() -> None:
    assert strict_master(MASTER, today=TODAY).skills == ("Languages: Go/Ruby/Python",)
    view = "# Skills\n- Go, Rust\n- Tools: Docker <!-- id: sk-2 -->\n# Other\n- Python club <!-- id: o-9 -->\n"
    assert strict_master(view, today=TODAY).skills == ("Go, Rust", "Tools: Docker")


# --- T8: the language is declared ----------------------------------------------------------------------------


def test_t8_a_level_word_on_a_declared_language_keeps_met_and_the_sources_and_says_why() -> None:
    # docker: "Fluency in Go and/or Rust", Go on a 2019-2022 role and a project only; T2 lowered it, T8 reads the skills line.
    row, changes = _check(_row("Fluency in Go and/or Rust", alternatives=["Go", "Rust"], sources=["b-mid1", "b-p1"]))
    assert row["status"] == "met" and [(change.step, change.before, change.after) for change in changes] == [("T8", "met", "met")]
    assert row["code_note"] == "Strict read (T8): Go is on your skills line." and row["sources"] == ["b-mid1", "b-p1"]


def test_t8_lifts_a_model_unclear_row_that_names_the_language_in_its_own_words() -> None:
    # grafana: no ``alternatives``, the model asked ``language:go``; the row's own words name Go.
    row, changes = _check(_row(GO_DEEP, "unclear", sources=["b-mid1"], cls="askable"))
    assert row["status"] == "met" and [(change.step, change.before, change.after) for change in changes] == [("T8", "unclear", "met")]
    assert row["code_note"] == "Strict read (T8): Go is on your skills line."


def test_t8_an_or_list_needs_one_declared_language_an_and_list_needs_them_all() -> None:
    row, changes = _check(_row("Proficient in Go or Rust", alternatives=["Go", "Rust"], sources=["b-mid1"]))
    assert row["status"] == "met" and changes[0].step == "T8"
    row, changes = _check(_row("Proficient in Go or Kubernetes", "unclear", alternatives=["Go", "Kubernetes"], sources=["b-mid1"]))
    assert row["status"] == "unclear" and changes == ()  # a tool that is no language beside the language: no T8 lift, the model's reading stands
    row, changes = _check(_row("Proficient in Go, Kubernetes and Terraform", alternatives=["Go", "Kubernetes", "Terraform"], sources=["b-mid1"]))
    assert row["status"] == "unclear" and changes[0].step in ("T1", "T2")  # Terraform is no language and is required: T1/T2 as before
    row, changes = _check(_row("Experience with Go and Rust", alternatives=["Go", "Rust"], sources=["b-mid1"]))
    assert row["status"] == "unclear" and changes[0].step == "T1" and "Rust on no line" in row["code_note"]  # Rust is not declared


def test_t8_a_slashed_name_list_needs_every_language() -> None:
    # As the stated check reads "React/Vue" (test_stated_more_end_outcome): a slash joins names that are all required.
    row, changes = _check(_row("Experience with Go/Rust", "unclear", alternatives=["Go", "Rust"]))
    assert row["status"] == "unclear" and changes == ()  # Rust is not declared
    row, changes = _check(_row("Experience with Go/Ruby", "unclear", alternatives=["Go", "Ruby"]))
    assert row["status"] == "met" and changes[0].step == "T8" and row["code_note"] == "Strict read (T8): Go and Ruby are on your skills line."


@pytest.mark.parametrize(
    "requirement",
    [
        "Experience with payments systems in Go",
        "Experience building reporting software in Go",
        "Experience leading teams using Go",
        "Go for high-frequency trading systems",
    ],
)
def test_t8_a_row_that_adds_a_domain_beside_the_language_is_no_language_row(requirement: str) -> None:
    row, changes = _check(_row(requirement, "unclear", alternatives=["Go"], cls="askable"))
    assert row["status"] == "unclear" and changes == ()


@pytest.mark.parametrize(
    "requirement",
    [
        GO_DEEP,  # grafana
        "Fluency in Go and/or Rust",  # docker
        "Go",  # circleci
        "Strong programming fundamentals and experience building production systems in languages such as Go, Python, Java, C++, Rust, or similar",  # cohere
        "You have strong programming fundamentals and experience building production systems in languages such as Go, Python, Java, C++, Rust, or similar.",
        "Hands-on experience writing production-quality, well-tested Go code",
    ],
)
def test_t8_filler_words_around_the_language_still_make_a_language_row(requirement: str) -> None:
    alternatives = [name for name in ("Go", "Rust", "Python", "Java", "C++") if name in requirement] or None
    row, changes = _check(_row(requirement, "unclear", alternatives=alternatives, cls="askable"))
    assert row["status"] == "met" and changes[0].step == "T8" and "Go" in row["code_note"]


@pytest.mark.parametrize(
    ("requirement", "alternatives"),
    [
        ("Backend development experience", ["Go", "Python", "Java"]),
        ("Experience building production backend services", ["Go"]),
        ("Strong software engineering fundamentals", ["Python"]),
    ],
)
def test_t8_needs_the_language_in_the_requirements_own_words_not_only_in_the_models_alternatives(requirement: str, alternatives: list[str]) -> None:
    # Fix round 2 (independent read): the model's guessed alternatives alone do not make a row a language row.
    row, changes = _check(_row(requirement, "unclear", alternatives=alternatives))
    assert row["status"] == "unclear" and changes == ()
    row, changes = _check(_row("Experience with Go (or Python)", "unclear", alternatives=["Go", "Python", "Java"]))
    assert row["status"] == "met" and changes[0].step == "T8"  # Java, named only by the model, is not needed


def test_t8_leaves_an_undeclared_language_years_rows_and_optional_rows_to_the_other_steps() -> None:
    row, changes = _check(_row("Strong experience with Rust", alternatives=["Rust"], sources=["b-mid1"]))
    assert row["status"] == "unclear" and changes[0].step == "T1"
    row, changes = _check(_row("Strong experience with Rust", "unclear", alternatives=["Rust"], sources=["b-mid1"]))
    assert row["status"] == "unclear" and changes == ()
    row, changes = _check(_row("8+ years of Go experience", alternatives=["Go"], sources=["b-mid1"]))
    assert row["status"] == "unclear" and changes[0].step == "T3"
    row, changes = _check(_row("Fluency in Go", "unclear", alternatives=["Go"], cls="nice_to_have"))
    assert row["status"] == "unclear" and changes == ()


def test_t8_a_saved_answer_declares_the_language_unless_it_denies_it() -> None:
    row, changes = _check(_row("Strong Rust experience", "unclear", alternatives=["Rust"], evidence=["Your answer: yes, Rust for three years at Old Co"]))
    assert row["status"] == "met" and changes[0].step == "T8" and row["code_note"] == "Strict read (T8): Rust is in your answer."
    row, changes = _check(_row("Rust", alternatives=["Rust"], sources=["A language:rust"]))
    assert row["status"] == "met" and changes[0].step == "T8" and row["status"] != ANSWERED
    row, changes = _check(_row("Strong Rust experience", "unclear", alternatives=["Rust"], evidence=["Your answer: no, I have not used Rust"]))
    assert row["status"] == "unclear" and changes == ()
    # Declared on the skills line, but the cited answer says otherwise: the row keeps the model's status.
    row, changes = _check(_row("Fluency in Go", "unclear", alternatives=["Go"], evidence=["Your answer: not Go, I moved off it in 2019"]))
    assert row["status"] == "unclear" and changes == ()


def test_t8_never_touches_an_unmet_row() -> None:
    row, changes = _check(_row("Fluency in Go", "unmet", alternatives=["Go"], evidence=["Your answer: no"]))
    assert row["status"] == "unmet" and changes == ()
    row, changes = _check(_row("Fluency in Go", "unmet", alternatives=["Go"], evidence=["Story bank story:go_once: wrote Go once"]))
    assert row["status"] == "unclear" and changes[0].step == "T7"


# --- the live path: the question goes with the row, the verdict is read again ------------------------------


def _live(status: str, questions: list[dict[str, object]], verdict: str):
    # The shared master's skills line is "Go, Node, SQL"; SQL is on no role line, so T1 alone reads this row as unclear.
    row = {**_live_row("Strong SQL experience", status, ["s-000001"], ["Go, Node, SQL"]), "alternatives": ["SQL"]}
    return _assess(_answer([row], questions=questions, verdict=verdict)).parsed


def test_a_met_row_on_the_skills_line_alone_stays_met_and_asks_nothing() -> None:
    parsed = _live("met", [], MATCHED)
    assert parsed.matrix[0].status.value == "met" and parsed.matrix[0].code_note == "Strict read (T8): SQL is on your skills line."
    assert not parsed.structured_questions and not parsed.questions and parsed.verdict.value == MATCHED


def test_a_lifted_rows_own_question_is_dropped_and_a_pending_verdict_is_read_again() -> None:
    own = [{"question_id": "language:sql", "question": "How much production SQL have you written?", "requirement": "Strong SQL experience"}]
    parsed = _live("unclear", own, PENDING)
    assert parsed.matrix[0].status.value == "met" and parsed.matrix[0].code_note == "Strict read (T8): SQL is on your skills line."
    assert not parsed.structured_questions and not parsed.questions and parsed.verdict.value == MATCHED
