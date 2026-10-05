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


def test_a_number_or_a_scope_is_met_only_by_that_scoped_evidence() -> None:
    line = _paragraph(_prompt(), "A number or a scope in a must-have")
    assert "General years of experience do not meet years in a named area" in line
    assert "one named example does not meet a named set" in line
    assert '"unclear" and you ask' in line


def test_a_plainly_settled_must_have_is_met_and_never_asked() -> None:
    line = _paragraph(_prompt(), "A number or a scope in a must-have")
    assert 'the row is "met" and is never asked' in line


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
