"""Prompt v9.2 wording (0.1.11 fourth set, FOURTH-SET-PLAN 'PROMPT v9.2'): what only the prompt can say.

Offline: reads the shipped ``assess.md`` text. What is pinned is the presence of each rule and the absence of the two
leftovers; how a model follows the rules is measured by the live eval, never here.
"""

from __future__ import annotations

import re

from gigai.scout.assessment_core import load_assess_instructions


def _prompt() -> str:
    return load_assess_instructions()


def _paragraph(text: str, start: str) -> str:
    match = re.search(rf"^.*{re.escape(start)}.*$", text, flags=re.M)
    assert match is not None, start
    return match.group(0)


def test_numbers_and_named_sets_are_strict() -> None:
    line = _paragraph(_prompt(), "Numbers and named tools are strict")
    assert "not met by general years" in line
    assert "Protobuf alone does not meet Parquet, Arrow and Iceberg" in line
    assert '"unclear" and you ask' in line


def test_a_plainly_settled_must_have_is_met_and_never_asked() -> None:
    line = _paragraph(_prompt(), "Numbers and named tools are strict")
    assert 'the row is "met" and never asked' in line


def test_a_scope_or_strength_word_does_not_turn_stated_work_into_a_question() -> None:
    line = _paragraph(_prompt(), "The standard for \"met\" is the reasonable reader")
    assert "recruiter reading the cited line(s)" in line
    for word in ("at scale", "large, complex", "high-volume", "significant", "proven"):
        assert word in line
    assert "does not turn stated work into a question" in line
    assert "A strength word is part of the requirement" not in _prompt()


def test_a_who_you_are_requirement_heading_still_gives_rows() -> None:
    text = _prompt()
    assert '"who you are" statements of capability' in text
    assert 'never a reason to emit "No stated requirements"' in text
    assert "NO requirement section of any kind" in text


def test_no_stated_requirements_is_only_for_a_posting_with_no_section() -> None:
    assert _prompt().count('"No stated requirements"') == 2  # the heading rule's negation, and the one allowed row


def test_the_soft_bullet_list_no_longer_contradicts_coverage() -> None:
    text = _prompt()
    assert "uses AI tools responsibly" not in text
    assert "using AI or generative-AI tools as a force multiplier" in text


def test_resume_line_ids_do_not_say_sponsorship_meets_a_row() -> None:
    line = _paragraph(_prompt(), "A row met only by CANDIDATE CONSTRAINTS")
    assert "(a country, a region, a work mode)" in line and "sponsorship" not in line
