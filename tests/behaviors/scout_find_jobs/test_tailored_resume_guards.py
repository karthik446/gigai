"""tailor-r1 (v0.1.9): the guard false positives seen in the 2026-09-25 live
tailor eval, fixed BOTH WAYS.

(a) the posting-term guard reads a cited SOURCE's hyphenated compounds as the
compound and its parts (``Terraform-managed`` supports ``terraform``; the
exact r5 x cloudflare bullet is accepted) while a term absent from the sources
is still rejected; (b) a Capitalized word that opens a sentence-like posting
bullet (``- Improve platform reliability ...``) is not a term, while a bare
list item (``- Snowflake``), an alias-table word (``- Kubernetes and ...``) and
a word Capitalized elsewhere mid-sentence stay terms; (c) the markdown
renderer drops BOTH ends of a copied heading's ``**...**`` while the JSON keeps
the text verbatim; (d) the prompt carries the role-framing rule with one
example each way.
"""

from __future__ import annotations

import pytest

from gigai.scout.tailored_resume import (
    TailorContext,
    TailorJob,
    TailorValidationError,
    guard_terms,
    posting_terms,
    render_markdown,
    render_tailor_prompt,
    resume_lines,
    source_terms,
    text_terms,
    unsupported_posting_terms,
    validate_tailored_output,
)

# --- (a) hyphenated compounds in a cited source -------------------------------------------------

_R5_BULLET = "Manages a multi-account AWS landing zone with Terraform and SOC2-relevant guardrails."
_R5_SOURCE = "- Terraform-managed multi-account AWS landing zone; SOC2-relevant guardrails."


def test_a_source_states_the_parts_of_its_hyphenated_compounds_and_the_compound() -> None:
    assert {"terraform-managed", "terraform", "managed", "aws"} <= source_terms(_R5_SOURCE)
    # The line side is unchanged: a line keeps its compounds whole.
    assert "terraform" not in text_terms("Terraform-managed zones")
    # An alias spelled with a hyphen stays whole (and canonical): no "ci"/"cd" parts.
    assert "ci/cd" in source_terms("owned the CI-CD pipeline") and "cd" not in source_terms("owned the CI-CD pipeline")
    assert "scikit-learn" in source_terms("scikit-learn models")


def test_the_r5_x_cloudflare_bullet_is_accepted_and_a_term_absent_from_its_sources_is_still_rejected() -> None:
    terms = {"terraform", "aws", "kubernetes"}
    assert unsupported_posting_terms(_R5_BULLET, [_R5_SOURCE], terms) == ()
    assert unsupported_posting_terms(_R5_BULLET + " Runs Kubernetes.", [_R5_SOURCE], terms) == ("kubernetes",)
    assert unsupported_posting_terms("Deep Terraform experience", ["Built Python services for six years"], terms) == ("terraform",)


_R5_RESUME = (
    "# Priya Raman\n"
    "priya@example.test\n"
    "\n"
    "## Experience\n"
    "**Senior Platform Engineer — Hexa Cloud** (2021–present)\n"
    + _R5_SOURCE + "\n"
    "- Ran the on-call rotation for the edge fleet.\n"
)
_R5_LINES = resume_lines(_R5_RESUME)
_R5_POSTING = (
    "Cloudflare is hiring a Senior Platform SRE.\n"
    "Requirements: Terraform; AWS; Kubernetes at scale.\n"
    "- Improve platform reliability through automation and observability.\n"
)
_R5_JOB = TailorJob(title="Senior Platform SRE", company="Cloudflare", location="Remote", posting_text=_R5_POSTING)
_R5_CTX = TailorContext(resume_lines=_R5_LINES)
_R5_SOURCE_NUMBER = _R5_LINES.index(_R5_SOURCE) + 1


def _entry_payload(bullet: str, line: int) -> dict[str, object]:
    return {
        "header": [{"copy": 1}],
        "sections": [
            {
                "heading": "experience",
                "entries": [{"heading_ref": [{"copy": 4}], "bullets": [{"text": bullet, "refs": [{"kind": "resume", "line": line}]}]}],
            }
        ],
    }


def test_the_validator_accepts_the_r5_x_cloudflare_bullet_citing_r9_and_rejects_a_borrowed_term() -> None:
    assert "terraform" in guard_terms(_R5_JOB, _R5_CTX)
    result = validate_tailored_output(_entry_payload(_R5_BULLET, _R5_SOURCE_NUMBER), _R5_JOB, _R5_CTX)
    assert result.sections[0].entries[0].bullets[0].text == _R5_BULLET
    with pytest.raises(TailorValidationError) as info:
        validate_tailored_output(_entry_payload("Ran the on-call rotation with Terraform.", _R5_SOURCE_NUMBER + 1), _R5_JOB, _R5_CTX)
    assert str(info.value) == (
        f'experience entry 1 bullet 1 contains the posting term "terraform" that appears in none of its cited sources (R{_R5_SOURCE_NUMBER + 1})'
    )


# --- (b) a Capitalized word at a posting bullet start ----------------------------------------------


@pytest.mark.parametrize(
    "posting",
    [
        "- Improve platform reliability through automation and observability.",
        "* Build dashboards for the finance team every quarter.",
        "* Automate dashboards for the finance team every quarter.",
        "1. Improve platform reliability through automation.",
        "• Automate platform reliability checks through observability tooling.",
        "Responsibilities:\n  - Improve platform reliability through automation.\n",
    ],
)
def test_a_word_capitalized_only_at_a_sentence_like_bullet_start_is_not_a_term(posting: str) -> None:
    assert not {"improve", "build", "automate"} & posting_terms(posting)


@pytest.mark.parametrize(
    ("posting", "expected"),
    [
        ("- Snowflake", {"snowflake"}),
        ("- Apache Kafka", {"apache", "kafka"}),
        ("- Terraform (3+ years)", {"terraform"}),
        ("- Terraform (3+ years).", {"terraform"}),
        ("- Snowflake, Airflow, Dbt", {"snowflake", "airflow", "dbt"}),
        ("Requirements: Kafka; Spark, Airflow\n- Snowflake", {"kafka", "spark", "airflow", "snowflake"}),
    ],
)
def test_a_bare_list_item_bullet_keeps_its_first_word_as_a_term(posting: str, expected: set[str]) -> None:
    assert posting_terms(posting) == frozenset(expected)


def test_a_sentence_like_bullet_keeps_an_alias_table_word_or_a_word_capitalized_elsewhere_mid_sentence() -> None:
    # Kubernetes is in the alias table; Terraform is mid-sentence.
    assert posting_terms("- Kubernetes and Terraform at scale, every day of the week") == {"kubernetes", "terraform"}
    # "Improve" opens a sentence-like bullet but is Capitalized mid-sentence elsewhere.
    assert "improve" in posting_terms("- Improve platform reliability through automation.\nOur motto: Improve daily.")
    # Still not a term when only ever sentence-initial, bullet or not.
    assert "improve" not in posting_terms("- Improve platform reliability through automation.\nImprove daily.")
    # Sentence starts without a bullet are unchanged.
    assert posting_terms("Build services. Ship code.") == frozenset()


def test_the_validator_accepts_a_rewritten_line_that_uses_a_verb_the_posting_only_capitalizes_at_a_bullet_start() -> None:
    assert "improve" not in guard_terms(_R5_JOB, _R5_CTX)
    line = "Ran the on-call rotation for the edge fleet to improve reliability."
    result = validate_tailored_output(_entry_payload(line, _R5_SOURCE_NUMBER + 1), _R5_JOB, _R5_CTX)
    assert result.sections[0].entries[0].bullets[0].text == line


# --- (c) a copied heading's paired emphasis ---------------------------------------------------------

_EMPHASIS_RESUME = (
    "# Dana Okafor\n"
    "dana@example.test\n"
    "\n"
    "## Experience\n"
    "**Analytics Engineer II — Northwind Data** (2022–2024)\n"
    "- Built the dbt layer.\n"
    "\n"
    "## Skills\n"
    "- __Advanced SQL__; dbt; **Snowflake**\n"
    "\n"
    "## Education\n"
    "**B.Sc. Computer Science**, Warsaw University of Technology **\n"
)
_EMPHASIS_LINES = resume_lines(_EMPHASIS_RESUME)
_EMPHASIS_JOB = TailorJob(title="Analytics Engineer", company="Northwind", location="Remote", posting_text="Northwind is hiring an Analytics Engineer with dbt.")


def test_the_renderer_drops_both_ends_of_a_copied_headings_emphasis_and_the_json_keeps_the_text_verbatim() -> None:
    heading = _EMPHASIS_LINES.index("**Analytics Engineer II — Northwind Data** (2022–2024)") + 1
    skills = _EMPHASIS_LINES.index("- __Advanced SQL__; dbt; **Snowflake**") + 1
    education = _EMPHASIS_LINES.index("**B.Sc. Computer Science**, Warsaw University of Technology **") + 1
    payload = {
        "header": [{"copy": 1}],
        "sections": [
            {"heading": "experience", "entries": [{"heading_ref": [{"copy": heading}], "bullets": []}]},
            {"heading": "skills", "lines": [{"copy": skills}]},
            {"heading": "education", "entries": [{"heading_ref": [{"copy": education}], "bullets": []}]},
        ],
    }
    result = validate_tailored_output(payload, _EMPHASIS_JOB, TailorContext(resume_lines=_EMPHASIS_LINES))
    markdown = render_markdown(result)
    assert f"### Analytics Engineer II — Northwind Data (2022–2024) <!-- R{heading} -->\n" in markdown
    assert "**" not in markdown.split("## Skills")[0]
    assert f"- Advanced SQL; dbt; Snowflake <!-- R{skills} -->\n" in markdown
    # An unpaired marker is not a pair: only the leading run is stripped, as before.
    assert f"### B.Sc. Computer Science, Warsaw University of Technology ** <!-- R{education} -->\n" in markdown
    # The JSON (and every ref's source text) is the resume line verbatim.
    assert result.sections[0].entries[0].heading[0].text == "**Analytics Engineer II — Northwind Data** (2022–2024)"
    assert result.sections[0].entries[0].heading[0].refs[0].text == "**Analytics Engineer II — Northwind Data** (2022–2024)"
    assert result.sections[1].lines[0].text == "- __Advanced SQL__; dbt; **Snowflake**"
    assert result.to_json()["sections"][0]["entries"][0]["heading"][0]["text"] == "**Analytics Engineer II — Northwind Data** (2022–2024)"


# --- (d) the framing rule in the prompt --------------------------------------------------------------


def test_the_prompt_tells_the_model_to_keep_the_sources_framing_with_one_example_each_way() -> None:
    prompt = render_tailor_prompt(_R5_JOB, _R5_CTX)
    framing = [block for block in prompt.split("\n\n") if block.startswith("FRAMING:")]
    assert len(framing) == 1
    rule = framing[0]
    assert "Never upgrade participation into initiation or ownership" in rule
    assert '"with SLOs" does not support "introducing SLOs"' in rule
    assert '"worked on the Kubernetes migration" does not support "led the Kubernetes migration"' in rule
    assert '"part of the payments team" does not support "owned payments"' in rule
    assert 'Not allowed: R7 "Worked on the Kubernetes migration with the platform team" rewritten as "Led the Kubernetes migration"' in rule
    assert 'Allowed: the same R7 rewritten as "Worked on the platform team\'s Kubernetes migration"' in rule
    # The rule sits between the line kinds and the sections, before the posting.
    assert prompt.index("LINE KINDS:") < prompt.index("FRAMING:") < prompt.index("SECTIONS:") < prompt.index("POSTING TEXT:")
