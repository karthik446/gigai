"""0.1.11.12 dossier-C (gigai-6po): up to three red flags beside the fit score, computed from the stored matrix at read time.

Pure rules (``gigai.scout.red_flags.red_flags``), on synthetic rows shaped as the stored matrix is (``RequirementMatrixRow``
JSON: ``requirement``, ``class``, ``status``, ``id``, ``alternatives``). Three kinds, one flag per row, in the order
location, language, level, at most three. Nothing here calls a model, writes a store or changes a fit, a cap or a verdict.
"""

from __future__ import annotations

import copy
import json

from gigai.scout.find_jobs.contracts import RequirementMatrixRow
from gigai.scout.red_flags import MAX_RED_FLAGS, RedFlag, red_flags, red_flags_json


def _row(requirement: str, klass: str | None = "hard", status: str = "unclear", *, row_id: str | None = None, alternatives: list[str] | None = None) -> dict[str, object]:
    row: dict[str, object] = {"requirement": requirement, "status": status, "resume_evidence": ["ten years on it"] if status == "met" else []}
    if klass is not None:
        row["class"] = klass
    if row_id is not None:
        row["id"] = row_id
    if alternatives is not None:
        row["alternatives"] = alternatives
    return row


SNORKEL_LOCATION = _row("San Francisco, CA - Hybrid - US; New York, NY - Hybrid", "hard", "unclear", row_id="elig-location")
TOAST_LANGUAGE = _row(
    "8+ years of back-end experience in Java, Kotlin, or other object-oriented languages", "hard", "unclear",
    row_id="req-5770c0", alternatives=["Java", "Kotlin", "other object-oriented languages"],
)
DECLARED = frozenset({"go", "ruby", "typescript", "python", "c#"})


# --- location -------------------------------------------------------------------------------------------------


def test_an_unclear_hard_location_row_is_a_location_flag_worded_as_the_row() -> None:
    assert red_flags([SNORKEL_LOCATION], DECLARED) == (RedFlag("location", "San Francisco, CA - Hybrid - US; New York, NY - Hybrid"),)


def test_an_unmet_location_or_work_mode_row_flags_whatever_its_class_and_a_long_text_is_cut() -> None:
    hybrid = _row("Hybrid, three days a week in the Boston office; relocation is not offered to anyone", "askable", "unmet", row_id="elig-work-mode")
    assert red_flags([_row("Remote within the United States", "askable", "unmet", row_id="elig-location")], DECLARED) == (RedFlag("location", "Remote within the United States"),)
    flags = red_flags([hybrid], DECLARED)
    assert flags[0].kind == "location" and len(flags[0].text) <= 60 and flags[0].text.endswith("…") and flags[0].text.startswith("Hybrid, three days a week in the Boston office")


def test_an_unclear_askable_location_a_met_location_and_the_other_eligibility_rows_are_no_flag() -> None:
    rows = [
        _row("Remote within the United States", "askable", "unclear", row_id="elig-location"),
        _row("Remote within the United States", "hard", "met", row_id="elig-location"),
        _row("Visa sponsorship is not available", "hard", "unmet", row_id="elig-sponsorship"),
        _row("EMEA time zones", "hard", "unmet", row_id="elig-region"),
    ]
    assert red_flags(rows, DECLARED) == ()


# --- language -------------------------------------------------------------------------------------------------


def test_a_must_have_row_that_asks_for_languages_the_resume_does_not_declare_is_one_language_flag() -> None:
    assert red_flags([TOAST_LANGUAGE], DECLARED) == (RedFlag("language", "Asks for Java, Kotlin"),)


def test_a_language_the_resume_declares_is_no_language_flag_and_aliases_fold() -> None:
    # Toast's row with Java and Kotlin declared: the row still says 8+ years on a hard row, so it is a level flag, not a language one.
    assert red_flags([TOAST_LANGUAGE], DECLARED | {"java", "kotlin"}) == (RedFlag("level", "Asks for 8+ years of back-end experience in Java, Kotlin, or other…"),)
    golang = _row("Strong Golang and TypeScript", "askable", "unclear", alternatives=["Golang", "TS"])
    assert red_flags([golang], DECLARED) == ()
    assert red_flags([golang], frozenset()) == (RedFlag("language", "Asks for Golang, TS"),)


def test_a_language_row_that_is_met_minor_or_names_no_language_is_no_flag() -> None:
    rows = [
        _row("Rust", "hard", "met", alternatives=["Rust"]),
        _row("Rust", "list_item", "unmet", alternatives=["Rust"]),  # one of a list: a minor gap, not a flag
        _row("Scala", "nice_to_have", "unclear", alternatives=["Scala"]),
        _row("Experience with Kafka and Postgres", "askable", "unclear", alternatives=["Kafka", "Postgres"]),
    ]
    assert red_flags(rows, DECLARED) == ()


def test_a_row_without_alternatives_names_its_languages_in_its_own_words() -> None:
    assert red_flags([_row("Production services written in Rust", "askable", "unclear")], DECLARED) == (RedFlag("language", "Asks for Rust"),)


# --- level ----------------------------------------------------------------------------------------------------


def test_a_hard_row_with_years_or_a_seniority_word_that_is_not_met_is_a_level_flag() -> None:
    years = _row("12+ years of experience building distributed systems", "hard", "unmet")
    staff = _row("Prior experience as a Staff or Principal engineer", "hard", "unclear")
    assert red_flags([years], DECLARED) == (RedFlag("level", "Asks for 12+ years of experience building distributed systems"),)
    assert red_flags([staff], DECLARED) == (RedFlag("level", "Asks for Prior experience as a Staff or Principal engineer"),)


def test_years_on_an_askable_row_a_met_hard_row_or_a_hard_row_without_a_level_are_no_level_flag() -> None:
    rows = [
        _row("5+ years with Kubernetes", "askable", "unclear"),
        _row("10+ years of back-end experience", "hard", "met"),
        _row("Experience with payment systems", "hard", "unclear"),
    ]
    assert red_flags(rows, DECLARED) == ()


# --- fix round 1 (independent read F2-F4) -------------------------------------------------------------------


def test_an_or_list_naming_a_declared_language_is_no_language_flag_an_and_list_flags_the_missing_one() -> None:
    """F2: the strict check's own reading of a list (``requires_all`` / ``slash_joined``): "or" means any one is enough."""
    either = _row("Experience building payment systems in Python or Java", "hard", "unclear", alternatives=["Python", "Java"])
    guessed = _row("Backend experience", "hard", "unclear", alternatives=["Python", "Go", "Java"])
    assert red_flags([either, guessed], DECLARED) == ()
    both = _row("Production services in Python and Java", "hard", "unclear", alternatives=["Python", "Java"])
    assert red_flags([both], DECLARED) == (RedFlag("language", "Asks for Java"),)
    slashed = _row("Java/Kotlin services at scale", "hard", "unclear", alternatives=["Java", "Kotlin"])
    assert red_flags([slashed], DECLARED | {"java"}) == (RedFlag("language", "Asks for Kotlin"),)
    # Toast's or-list with neither language declared still names both.
    assert red_flags([TOAST_LANGUAGE], DECLARED) == (RedFlag("language", "Asks for Java, Kotlin"),)


def test_a_seniority_word_flags_only_as_a_title_and_a_degree_row_is_no_level_flag() -> None:
    """F3: "lead", "director" and "VP" as verbs or stakeholders are not a level ask; a degree row's years are not either."""
    clean = [
        _row("Lead the design and delivery of cross-functional initiatives", "hard", "unclear"),
        _row("Partner with Director-level stakeholders on roadmaps", "hard", "unclear"),
        _row("Report to the VP of Engineering", "hard", "unclear"),
        _row("Bachelor's degree in CS or 4 years equivalent experience", "hard", "unclear"),
        _row("MS or PhD in Computer Science or 6+ years of experience", "hard", "unclear"),
    ]
    assert red_flags(clean, DECLARED) == ()
    flagged = [
        _row("8+ years building distributed systems", "hard", "unmet"),
        _row("Staff-level ownership of a product area", "hard", "unclear"),
        _row("Prior experience as a tech lead or engineering lead", "hard", "unclear"),
        _row("Lead engineer for a platform team", "hard", "unclear"),
        _row("Director of Engineering experience", "hard", "unclear"),
    ]
    assert [flag.kind for flag in red_flags(flagged, DECLARED)] == ["level", "level", "level"]
    assert [flag.kind for flag in red_flags(flagged[3:], DECLARED)] == ["level", "level"]


def test_only_an_unmet_or_unclear_row_flags_whatever_its_kind() -> None:
    """F4: an answered row (the user said yes), a met one and a legacy status flag nothing; location included."""
    for status in ("answered", "met", "partial", "gap", "", None):
        rows = [
            {**SNORKEL_LOCATION, "status": status},
            {**TOAST_LANGUAGE, "status": status},
            _row("8+ years building distributed systems", "hard", status),  # type: ignore[arg-type]
        ]
        assert red_flags(rows, DECLARED) == (), status
    assert [flag.kind for flag in red_flags([{**SNORKEL_LOCATION, "status": "unmet"}, {**TOAST_LANGUAGE, "status": "unmet"}], DECLARED)] == ["location", "language"]


# --- the set --------------------------------------------------------------------------------------------------


def test_at_most_three_flags_in_the_order_location_language_level_one_per_row() -> None:
    rows = [
        _row("Prior experience as a Director of Engineering", "hard", "unclear"),
        TOAST_LANGUAGE,
        _row("Deep Swift experience", "askable", "unmet", alternatives=["Swift"]),
        SNORKEL_LOCATION,
        _row("15+ years in the field", "hard", "unmet"),
    ]
    flags = red_flags(rows, DECLARED)
    assert len(flags) == MAX_RED_FLAGS == 3
    assert [flag.kind for flag in flags] == ["location", "language", "language"]
    assert [flag.text for flag in flags] == ["San Francisco, CA - Hybrid - US; New York, NY - Hybrid", "Asks for Java, Kotlin", "Asks for Swift"]


def test_a_matrix_of_met_rows_has_no_flag_and_the_json_shape_is_kind_and_text() -> None:
    assert red_flags([_row("Python", "hard", "met", alternatives=["Python"]), _row("Remote in the US", "hard", "met", row_id="elig-location")], DECLARED) == ()
    assert red_flags([], frozenset()) == ()
    assert red_flags_json([SNORKEL_LOCATION, TOAST_LANGUAGE], DECLARED) == [
        {"kind": "location", "text": "San Francisco, CA - Hybrid - US; New York, NY - Hybrid"},
        {"kind": "language", "text": "Asks for Java, Kotlin"},
    ]


def test_contract_rows_give_the_same_flags_as_their_json_and_neither_is_changed() -> None:
    rows = [SNORKEL_LOCATION, TOAST_LANGUAGE, _row("Python", "askable", "met", alternatives=["Python"])]
    before = copy.deepcopy(rows)
    stored = [RequirementMatrixRow.from_json(row) for row in rows]
    assert red_flags(stored, DECLARED) == red_flags(rows, DECLARED) != ()
    assert rows == before, "a read-time derivation changed the matrix it read"
    assert [row.to_json() for row in stored] == [json.loads(json.dumps(row)) for row in before]
