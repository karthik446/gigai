"""0110-003 P1: ``scout/resume_privacy.py``, the one strip every model-bound resume text goes through."""

from __future__ import annotations

import pytest

from gigai.scout import resume_privacy
from gigai.scout.find_jobs import rank_digest
from gigai.scout.resume_privacy import is_name_line, model_resume, redact_inline
from gigai.scout.tailored_resume import resume_lines

_RESUME = """Zephyrine Quillfeather
Chief Zeppelin Wrangler
Xanadu Springs, ZZ | VISA: ZQ-9 | linkedin.com/in/zq-7731 | github.com/zq-7731 | zq7731.example | zq7731@example.test | 555-013-7731

## Summary
Operated the Glimmerfall ledger for 6 years.
Built Python services on Kubernetes and Kafka; write to zq7731@example.test or call 555-013-7731.
Founded Quillfeather Labs; code at https://github.com/zq-7731/ledger and a socket.io chat; Engineer @Stripe.

## Experience
**Staff Engineer -- Acme** (2019-2023), Denver, CO
- Built Python services on AWS with Postgres.
zq7731@example.test | 555-013-7731
"""


def test_the_header_name_and_contact_lines_are_withheld_and_the_headline_is_kept() -> None:
    model = model_resume(_RESUME)
    assert model.withheld == frozenset({1, 3, 11})
    assert model.lines[0] == (2, "Chief Zeppelin Wrangler")  # Q1: the headline is professional content
    for value in ("Zephyrine", "Quillfeather", "Xanadu", "ZQ-9", "linkedin", "github", "zq7731", "zq-7731", "555", "013-7731"):
        assert value.lower() not in model.text.lower(), value


def test_the_body_keeps_its_text_with_inline_contact_redacted_and_the_name_removed() -> None:
    shown = dict(model_resume(_RESUME).lines)
    assert shown[5] == "Operated the Glimmerfall ledger for 6 years."
    assert shown[6] == "Built Python services on Kubernetes and Kafka; write to or call ."
    # The narrow link rule: scheme/github links go, a tech name like socket.io and an "@Company" stay.
    assert shown[7] == "Founded Labs; code at and a socket.io chat; Engineer @Stripe."
    # Q3: a job location in the body stays.
    assert shown[9] == "**Staff Engineer -- Acme** (2019-2023), Denver, CO"
    assert shown[10] == "- Built Python services on AWS with Postgres."


def test_numbering_is_the_tailor_numbering_so_kept_lines_keep_their_original_r_numbers() -> None:
    for text in (_RESUME, "# Jane Doe\njane@example.test | Denver, CO\n\n## Experience\nAcme\n", "One line.\n", "\n\n  A\n\n B \n"):
        model = model_resume(text)
        numbered = resume_lines(text)
        assert [number for number, _ in model.lines] == [n for n in range(1, len(numbered) + 1) if n not in model.withheld]
        assert model.withheld <= set(range(1, len(numbered) + 1))


def test_a_resume_with_nothing_to_strip_goes_out_byte_for_byte() -> None:
    for text in ("Software engineer with Python service experience.\n", "Karthik built Python services for 6 years.\nOperated Kubernetes clusters in production.\n"):
        model = model_resume(text)
        assert model.text == text and model.withheld == frozenset()


def test_a_name_then_headline_first_line_keeps_the_headline_and_drops_the_name_everywhere() -> None:
    text = "# Priya Natarajan — Staff AI/ML Engineer\n\n## Summary\nPriya led the ranking team.\n"
    model = model_resume(text)
    assert model.withheld == frozenset()
    assert model.lines[0] == (1, "# Staff AI/ML Engineer")
    assert "Priya" not in model.text and "Natarajan" not in model.text and "led the ranking team." in model.text
    assert dict(model_resume("Jane Doe | Senior Backend Engineer\nBuilt APIs.\n").lines)[1] == "Senior Backend Engineer"


def test_a_markdown_name_and_a_work_authorization_or_location_line_are_withheld() -> None:
    model = model_resume("# Jane Doe\nUS citizen, no sponsorship needed\nDenver, CO\n\n## Skills\nPython, Go\n")
    assert model.withheld == frozenset({1, 2, 3}) and model.text == "## Skills\nPython, Go"


def test_the_header_block_ends_at_a_heading_a_long_sentence_or_the_first_blank_line() -> None:
    # No heading: the block is the first paragraph; a contact line further down is still dropped (body rule).
    no_heading = model_resume("Jane Doe\njane@example.test\n\nBuilt services for Jane's team.\nDenver, CO 80202\n")
    assert no_heading.withheld == frozenset({1, 2, 4})
    assert no_heading.text == "Built services for 's team."
    # A long non-contact sentence ends the block, so a name-shaped line after it is body text.
    long = "Backend engineer with nine years of building payment platforms in Python and Go on AWS."
    assert model_resume(f"{long}\nJane Doe\n\n## Skills\nPython\n").withheld == frozenset()


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Jane Doe", True), ("JANE DOE", True), ("Robin T. Sample", True), ("Mary-Jane O'Neil", True), ("Sean McDonald", True),
        ("# Jane Doe", True), ("**Jane Doe**", True),
        ("Jane", False), ("Senior Engineer Sam Example", False), ("Chief Zeppelin Wrangler", False), ("Staff Software Engineer", False),
        ("GraphQL Expert", False), ("Software engineer with Python.", False), ("Jane Doe, PhD", False), ("R2 D2", False),
    ],
)
def test_the_name_shape_is_strict(line: str, expected: bool) -> None:
    assert is_name_line(line) is expected


def test_the_inline_redaction_is_narrow() -> None:
    assert redact_inline("Mail a@b.co, see www.x.dev or https://x.dev/p, call +1 (555) 010-4477.") == "Mail , see or call ."  # a link takes its trailing punctuation with it
    for kept in ("Built a socket.io chat and Node.js APIs.", "Staff Engineer @Stripe", "Cut p99 latency by 40% in 2021-2023."):
        assert redact_inline(kept) == kept


def test_the_rank_digest_reexports_the_shared_helpers() -> None:
    for name in ("split_resume_header", "guard_private", "guard_name", "_name_tokens", "_is_contact_line", "_is_clean"):
        assert getattr(rank_digest, name) is getattr(resume_privacy, name), name
    assert rank_digest.DIGEST_VERSION == "digest-v4", "the digest output is unchanged, so its version is too"
