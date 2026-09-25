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

tailor-r2 (e): a cited resume line that ends mid-sentence is expanded to its
continuation lines before the guards run (``resume_continuations``), so a
fact on the next numbered line is supported; a neighbour that is not a
continuation (the previous line ends a sentence, a blank raw line between, a
bullet or a heading next) is not pulled in; the stored refs carry the
expansion; the markdown comment stays as cited; the prompt asks the model to
cite every line a fact spans.
"""

from __future__ import annotations

import pytest

from gigai.scout.tailored_resume import (
    TailorContext,
    TailorJob,
    TailorValidationError,
    TailoredResume,
    guard_terms,
    posting_terms,
    render_markdown,
    render_tailor_prompt,
    resume_continuations,
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


# --- (e) tailor-r2: a wrapped resume line cites its continuation ------------------------------------

_WRAP_RESUME = (
    "# Priya Raman\n"  # R1: a heading never expands
    "priya@example.test\n"  # R2: unterminated, but a blank RAW line follows
    "\n"
    "## Summary\n"  # R3
    "Senior platform/SRE engineer with eight years of experience. Owns cloud infrastructure, Kubernetes\n"  # R4 -> R5, R6
    "platform, and reliability for multi-team orgs. Deep Terraform, AWS/GCP, on-call and\n"  # R5 -> R6
    "incident response experience; 12 years of Ansible. Not an application engineer.\n"  # R6 ends a sentence
    "Ships Go services; 3 years of Go\n"  # R7: unterminated, blank raw line follows
    "\n"
    "Kafka clusters with 300 brokers.\n"  # R8
    "## Experience\n"  # R9
    "**Senior Platform Engineer — Hexa Cloud** (2021–present)\n"  # R10: an entry heading never expands
    "- Terraform-managed multi-account AWS landing zone; SOC2-relevant\n"  # R11 -> R12 (a wrapped bullet)
    "guardrails across 40 accounts.\n"  # R12
    "- Ran the on-call rotation for the edge fleet with\n"  # R13: the next line is a bullet
    "- Prometheus alerting for 200 hosts.\n"  # R14
    "Mentored five engineers on\n"  # R15: the next line is a heading
    "## Skills\n"  # R16
    "Python, Go\n"  # R17: the last line
)
_WRAP_LINES = resume_lines(_WRAP_RESUME)
_WRAP_CONTINUATIONS = resume_continuations(_WRAP_RESUME)
_WRAP_POSTING = (
    "Cloudflare is hiring a Senior Platform SRE.\n"
    "Requirements: Terraform; Ansible; Kubernetes; Kafka; Prometheus; Go.\n"
)
_WRAP_JOB = TailorJob(title="Senior Platform SRE", company="Cloudflare", location="Remote", posting_text=_WRAP_POSTING)
_WRAP_CTX = TailorContext(resume_lines=_WRAP_LINES, continuations=_WRAP_CONTINUATIONS)
_R5_R6 = _WRAP_LINES[4] + " " + _WRAP_LINES[5]


def _summary_payload(*lines: tuple[str, int], header: int = 1) -> dict[str, object]:
    return {
        "header": [{"copy": header}],
        "sections": [{"heading": "summary", "lines": [{"text": text, "refs": [{"kind": "resume", "line": line}]} for text, line in lines]}],
    }


def test_the_continuation_rule_is_decided_on_the_raw_resume_before_numbering() -> None:
    assert len(_WRAP_LINES) == 17 and _WRAP_LINES[4].startswith("platform, and")
    assert _WRAP_CONTINUATIONS == {4: (5, 6), 5: (6,), 11: (12,)}
    # Not continuations: a heading (R1, R10), a blank raw line between (R2, R7), a
    # sentence end (R6, R8), a bullet next (R13), a heading next (R15), the last line (R17).
    assert not {1, 2, 6, 7, 8, 10, 13, 15, 17} & set(_WRAP_CONTINUATIONS)
    # Closing quotes/brackets after the terminal punctuation still end the sentence; a
    # bare closing bracket or a trailing "and" does not.
    assert resume_continuations('Ran the fleet (edge).\nGo daily.') == {}
    assert resume_continuations('Ran the fleet "edge."\nGo daily.') == {}
    assert resume_continuations("Ran the fleet (edge)\nGo daily.") == {1: (2,)}
    assert resume_continuations("1. Ran the fleet and\n2. Go daily.") == {}
    assert resume_continuations("- Ran the fleet and\n  the edge.\n") == {1: (2,)}


def test_a_fact_on_the_continuation_line_passes_every_guard_when_the_wrapped_line_is_cited() -> None:
    assert {"ansible", "terraform", "go"} <= guard_terms(_WRAP_JOB, _WRAP_CTX)
    line = "Experienced in on-call and incident response; 12 years of Ansible."  # "12" and "Ansible" live on R6
    result = validate_tailored_output(_summary_payload((line, 5)), _WRAP_JOB, _WRAP_CTX)
    ref = result.sections[0].lines[0].refs[0]
    assert ref.line == 5 and ref.continued_lines == (6,) and ref.text == _R5_R6 and ref.label() == "R5"
    # The wrapped bullet: the number on R12 is supported by citing R11.
    bullet = "Manages a Terraform-managed AWS landing zone with guardrails across 40 accounts."
    result = validate_tailored_output(_summary_payload((bullet, 11)), _WRAP_JOB, _WRAP_CTX)
    assert result.sections[0].lines[0].refs[0].text == _WRAP_LINES[10] + " " + _WRAP_LINES[11]
    # Without the continuation map (the pre-r2 context) the same line is rejected:
    # the expansion is what accepts it, inside validation, before the guards.
    with pytest.raises(TailorValidationError) as info:
        validate_tailored_output(_summary_payload((line, 5)), _WRAP_JOB, TailorContext(resume_lines=_WRAP_LINES))
    assert str(info.value) == 'summary line 1 contains the number "12" that appears in none of its cited sources (R5)'


@pytest.mark.parametrize(
    ("text", "line", "message"),
    [
        # R6 ends with "." so R7 is not pulled in.
        ("Three years of Go.", 6, 'summary line 1 contains the number "Three" that appears in none of its cited sources (R6)'),
        # A blank raw line separates R7 and R8.
        ("Runs Kafka clusters with 300 brokers.", 7, 'summary line 1 contains the number "300" that appears in none of its cited sources (R7)'),
        # R14 is a bullet.
        ("Ran on-call with Prometheus alerting for 200 hosts.", 13, 'summary line 1 contains the number "200" that appears in none of its cited sources (R13)'),
        # R16 is a heading; R15's own words stay supported.
        ("Mentored five engineers on Go.", 15, 'summary line 1 contains the posting term "go" that appears in none of its cited sources (R15)'),
        # R10 is an entry heading: it never expands into R11.
        ("Senior Platform Engineer with Terraform.", 10, 'summary line 1 contains the posting term "terraform" that appears in none of its cited sources (R10)'),
    ],
)
def test_a_non_continuation_neighbour_is_not_pulled_in_so_a_fact_only_there_is_still_rejected(text: str, line: int, message: str) -> None:
    with pytest.raises(TailorValidationError) as info:
        validate_tailored_output(_summary_payload((text, line)), _WRAP_JOB, _WRAP_CTX)
    assert str(info.value) == message


def test_a_three_line_continuation_joins_all_three_lines() -> None:
    line = "Owns Kubernetes platform reliability with Terraform, on-call and incident response; 12 years of Ansible."
    result = validate_tailored_output(_summary_payload((line, 4)), _WRAP_JOB, _WRAP_CTX)
    ref = result.sections[0].lines[0].refs[0]
    assert ref.line == 4 and ref.continued_lines == (5, 6) and ref.text == " ".join(_WRAP_LINES[3:6])


def test_the_stored_refs_carry_the_expansion_and_round_trip_while_copy_lines_and_unexpanded_refs_are_unchanged() -> None:
    payload = _summary_payload(("Experienced in on-call and incident response; 12 years of Ansible.", 5), ("Not an application engineer.", 6), header=5)
    result = validate_tailored_output(payload, _WRAP_JOB, _WRAP_CTX)
    stored = result.to_json()
    lines = stored["sections"][0]["lines"]
    assert lines[0]["refs"] == [{"kind": "resume", "line": 5, "text": _R5_R6, "continued_lines": [6]}]
    assert lines[1]["refs"] == [{"kind": "resume", "line": 6, "text": _WRAP_LINES[5]}]  # no continued_lines key when nothing was joined
    # A copy line is one resume line verbatim, never the span (header copies R5 here).
    assert stored["header"][0] == {"kind": "copy", "text": _WRAP_LINES[4], "refs": [{"kind": "resume", "line": 5, "text": _WRAP_LINES[4]}]}
    assert TailoredResume.from_json(stored) == result
    with pytest.raises(Exception, match="continued_lines"):
        TailoredResume.from_json({**stored, "sections": [{"heading": "summary", "lines": [{"kind": "rewritten", "text": "x", "refs": [{"kind": "resume", "line": 5, "text": "x", "continued_lines": [4]}]}]}]})


def test_the_markdown_comment_stays_as_cited() -> None:
    result = validate_tailored_output(_summary_payload(("Experienced in on-call and incident response; 12 years of Ansible.", 5)), _WRAP_JOB, _WRAP_CTX)
    markdown = render_markdown(result)
    assert "- Experienced in on-call and incident response; 12 years of Ansible. <!-- R5 -->\n" in markdown
    assert "R6" not in markdown


def test_the_prompt_asks_for_every_line_a_fact_spans_and_keeps_its_numbering() -> None:
    prompt = render_tailor_prompt(_WRAP_JOB, _WRAP_CTX)
    sources = [block for block in prompt.split("\n\n") if block.startswith("SOURCES:")]
    assert len(sources) == 1
    assert "A resume sentence may wrap across several numbered lines (R4 ends mid-sentence, R5 finishes it): when a fact spans several numbered lines, cite every line it spans." in sources[0]
    assert f"R5: {_WRAP_LINES[4]}\nR6: {_WRAP_LINES[5]}\n" in prompt
