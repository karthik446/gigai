"""0.1.11.12 F1: the strict-met post-check (``assess_strict``) over a synthetic master and v9 rows. No model.

One test per step T1-T7 against a hand-written master (ids made up; nothing from a real resume): each shows the
lenient row the model would return, and what the check makes of it. Also pinned: the ``code_note`` names the line
found and why it does not count; the examples / parenthesis rules; the names the row carries when the model gave no
``alternatives`` (acronyms excluded); the language / collaboration rows T5 leaves alone; and the stored ``answered``
row: it round-trips through the row contract, counts as nothing in the fit number, and never fails the gate by itself.
"""

from __future__ import annotations

from datetime import date

import pytest

from gigai.scout.assess_master import LINE_ROLE, strict_master
from gigai.scout.assess_strict import ANSWERED, MAX_CODE_NOTE_CHARS, named_tools, names_in, practice_alternatives, strict_rows, tokens
from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementMatrixRow
from gigai.scout.fit import fit_percent
from gigai.scout.resume_gate import NOT_A_MATCH, gate

TODAY = date(2026, 10, 1)

MASTER = """\
# Summary
- Backend engineer fluent in Kotlin and Java, strong written communication. <!-- id: sum-1 -->

# Skills
- Languages: Go/Ruby/Python <!-- id: sk-1 -->

# Experience
### Senior Engineer, Current Co <!-- id: r-cur -->
Jun 2022 - Present
- Built Ruby on Rails services with Rails generators and Datadog dashboards. <!-- id: b-cur1 -->
- Node/Nest APIs in TypeScript, reviewed by the team. <!-- id: b-cur2 -->

### Engineer, Middle Co <!-- id: r-mid -->
Jan 2019 - May 2022
- Go services on Kubernetes. <!-- id: b-mid1 -->

### Developer, Old Co <!-- id: r-old -->
Jan 2010 - Dec 2012
- Java and Kotlin Android apps for a healthcare client. <!-- id: b-old1 -->

# Projects
### Side tool <!-- id: p-1 -->
- A Go CLI for Playwright test runs. <!-- id: b-p1 -->

# Education
### B.S. Computer Science, State University <!-- id: e-1 -->
2009

# Other
- Volunteer: taught Python to beginners. <!-- id: o-1 -->
"""


def _master():
    return strict_master(MASTER, today=TODAY)


def _row(requirement: str, status: str = "met", *, alternatives: list[str] | None = None, sources: list[str] | None = None, evidence: list[str] | None = None, row_id: str = "req-f1a001", cls: str = "hard") -> dict:
    row = {"id": row_id, "requirement": requirement, "status": status, "class": cls, "resume_evidence": evidence or ["a quote"]}
    if alternatives is not None:
        row["alternatives"] = alternatives
    if sources is not None:
        row["sources"] = sources
    return row


def _check(row: dict):
    changes = strict_rows([row], _master())
    return row, changes


# --- the master as read ------------------------------------------------------------------------------------


def test_the_master_knows_clear_and_buried_lines_and_the_window() -> None:
    master = _master()
    assert [entry.id for entry in master.roles] == ["r-cur", "r-mid", "r-old"]
    assert master.entry_of("b-cur1").current and master.entry_of("b-mid1").in_window and not master.entry_of("b-old1").in_window
    assert master.entry_of("r-old").kind == LINE_ROLE and master.entry_of("r-old").old
    assert all(master.is_clear(line_id) for line_id in ("b-cur1", "r-cur", "b-mid1", "b-p1", "p-1"))
    assert not any(master.is_clear(line_id) for line_id in ("sum-1", "sk-1", "b-old1", "r-old", "o-1", "e-1"))
    assert [degree.id for degree in master.degrees] == ["e-1"]


def test_tokens_drop_generic_words_plurals_and_give_dotted_stems() -> None:
    assert tokens("Ruby on Rails") == {"ruby", "rail"}
    assert tokens("New Relic") == {"relic"}
    assert tokens("Node.js") == {"node.js", "nodejs", "node"}
    assert tokens("Next.js") == {"next.js", "nextjs"}
    assert tokens("modern cloud platform") == frozenset()


def test_named_tools_come_from_alternatives_less_the_open_entries_else_from_the_requirement() -> None:
    assert named_tools(_row("x", alternatives=["Datadog", "New Relic", "other observability tools", "a comparable framework"])) == ("Datadog", "New Relic")
    assert names_in("Strong experience building production backend systems in Go") == ("Go",)
    assert names_in("Fluency at the system level: eventing (Kafka, SNS, SQS), orchestration (Temporal, DAGs)") == ("Kafka", "Temporal")
    assert names_in("Security mindset, SRE mindset, work with PMs and the SDLC; LLM and AI products") == ()
    assert names_in("Experience with Node.js and New Relic. Kafka too") == ("Node.js", "New Relic")
    assert names_in("Postgres") == ("Postgres",) and names_in("AWS") == () and names_in("Postgres and Redis") == ("Redis",)
    assert named_tools(_row("Proficient in Go")) == ("Go",)


def test_names_in_reads_past_a_list_opener_a_glued_level_word_and_names_the_c_languages() -> None:
    # R-B1 / R-M1: "e.g." and ":" open a list, they end no sentence.
    assert names_in("Proficient with modern data stores (e.g. Postgres, DynamoDB)") == ("Postgres", "DynamoDB")
    assert names_in("Nice to have: Go") == ("Go",) and names_in("Strong candidates will have: Go") == ("Go",)
    row, changes = _check(_row("Proficient with modern observability tools (e.g. Datadog, Honeycomb)", sources=["b-cur1"]))
    assert row["status"] == "met" and changes[0].step == "T2"
    row, changes = _check(_row("Nice to have: Go", sources=["b-p1"]))
    assert row["status"] == "met" and changes[0].step == "T1"
    # R-M2: a level word glued to the tool opens the run; the tool is still named and the level word still governs it.
    assert names_in("Strong Datadog experience, and familiarity with Go") == ("Datadog", "Go")
    row, changes = _check(_row("Strong Kubernetes experience", sources=["b-mid1"]))
    assert row["status"] == "unclear" and changes[0].step == "T2"
    # R-M4: C#, C++ and C are languages, not acronyms.
    assert names_in("Experience with C#, C++ and Go") == ("C#", "C++", "Go") and names_in("Experience with C and CS") == ("C",)


def test_a_clear_practice_line_carries_the_level_word_of_an_or_list() -> None:
    # R-M3: "Deep experience with Kubernetes, Nomad, or event streaming", Kubernetes on one 2019-2022 role, the practice on the current role's cited line.
    alternatives = ["Kubernetes", "Nomad", "event streaming"]
    row, changes = _check(_row("Deep experience with Kubernetes, Nomad, or event streaming", alternatives=alternatives, sources=["b-mid1", "b-cur2"]))
    assert row["status"] == "met" and changes[0].step == "T1"
    # Citing only the tool's own line, the level word still asks for depth.
    row, changes = _check(_row("Deep experience with Kubernetes, Nomad, or event streaming", alternatives=alternatives, sources=["b-mid1"]))
    assert row["status"] == "unclear" and changes[0].step == "T2"


def test_named_tools_keep_only_tool_shaped_alternatives_and_drop_any_language() -> None:
    # Ruling 4 / M2: a practice in ``alternatives`` is no tool; an "any ..." entry is open (M7a).
    practices = ["payment systems", "unified APIs", "backend", "consistency, caching, queues", "Python backend and distributed systems"]
    assert named_tools(_row("x", alternatives=practices)) == ()
    assert named_tools(_row("x", alternatives=["Java", "any object-oriented language", "C++", "Ruby on Rails", "k8s", "gRPC", ".NET"])) == ("Java", "C++", "Ruby on Rails", "k8s", "gRPC", ".NET")
    # A row left with no tool follows T5: the practices on an old role alone are unclear (D11), on a clear line kept.
    row, changes = _check(_row("Strong systems-design instincts: consistency, caching, queues", alternatives=["consistency, caching, queues"], sources=["b-old1"]))
    assert row["status"] == "unclear" and changes[0].step == "T5" and "found only on b-old1 (old role, ended 2012)" in row["code_note"]
    row, changes = _check(_row("Python backend and distributed systems", alternatives=["Python backend and distributed systems"], sources=["b-cur2"]))
    assert row["status"] == "met" and changes == ()


def test_an_or_list_whose_named_tools_are_on_no_line_rests_on_its_practice_alternative() -> None:
    # strict28 case 22: "MLOps, LLMOps, or production deployment pipelines" is met by the pipelines on a clear line.
    alternatives = ["MLOps", "LLMOps", "production deployment pipelines"]
    assert named_tools(_row("x", alternatives=alternatives)) == ("MLOps", "LLMOps") and practice_alternatives(_row("x", alternatives=alternatives)) == ("production deployment pipelines",)
    row, changes = _check(_row("Experience with MLOps, LLMOps, or production deployment pipelines", alternatives=alternatives, sources=["b-cur1"]))
    assert row["status"] == "met" and changes == ()
    row, changes = _check(_row("Experience with MLOps, LLMOps, or production deployment pipelines", alternatives=alternatives, sources=["b-old1"]))
    assert row["status"] == "unclear" and changes[0].step == "T5"
    # With no practice among the alternatives the named tools decide (T1).
    row, changes = _check(_row("Experience with MLOps or LLMOps", alternatives=["MLOps", "LLMOps"], sources=["b-cur1"]))
    assert row["status"] == "unclear" and changes[0].step == "T1"


# --- T1 / T2: named tools -----------------------------------------------------------------------------------


def test_t1_a_named_tool_only_on_summary_skills_or_an_old_role_is_unclear_and_the_note_says_where() -> None:
    row, changes = _check(_row("8+ years of back-end experience in Java, Kotlin, or Go", alternatives=["Java", "Kotlin"], sources=["sum-1", "b-old1"]))
    assert row["status"] == "unclear" and changes[0].step == "T1" and changes[0].lowered
    assert "Java only on sum-1 (summary) and b-old1 (old role, ended 2012)" in row["code_note"]
    assert "Kotlin only on sum-1 (summary) and b-old1 (old role, ended 2012)" in row["code_note"]
    assert row["code_note"].startswith("Strict read (T1)") and len(row["code_note"]) <= MAX_CODE_NOTE_CHARS


def test_t1_a_tools_word_on_a_clear_line_keeps_met_and_cites_the_line() -> None:
    row, changes = _check(_row("Experience with Ruby on Rails", alternatives=["Ruby on Rails"], sources=["sum-1"]))
    assert row["status"] == "met" and changes[0].step == "T1" and not changes[0].lowered
    assert row["sources"] == ["sum-1", "b-cur1"] and "Ruby on Rails on b-cur1 (current role)" in row["code_note"]


def test_t1_a_dotted_name_is_named_by_its_stem_and_a_skills_list_is_not_an_alias() -> None:
    row, _ = _check(_row("Node.js services", alternatives=["Node.js"], sources=["sk-1"]))
    assert row["status"] == "met" and row["sources"] == ["sk-1", "b-cur2"]
    row, _ = _check(_row("Python services", alternatives=["Python"], sources=["sk-1"]))
    assert row["status"] == "unclear" and "Python only on sk-1 (skills line) and o-1 (Other)" in row["code_note"]


def test_t1_tools_joined_by_and_are_all_required_unless_examples_or_in_a_parenthesis() -> None:
    row, _ = _check(_row("TypeScript and Playwright experience", alternatives=["TypeScript", "Playwright"], sources=["b-cur2"]))
    assert row["status"] == "met"
    row, _ = _check(_row("TypeScript and Kotlin experience", alternatives=["TypeScript", "Kotlin"], sources=["b-cur2"]))
    assert row["status"] == "unclear" and "every tool is required" in row["code_note"]
    row, _ = _check(_row("Production engineering, including Kotlin and TypeScript", alternatives=["Kotlin", "TypeScript"], sources=["b-cur2"]))
    assert row["status"] == "met"
    row, _ = _check(_row("Strong TypeScript (and modern Kotlin) plus Node.js experience", alternatives=["TypeScript", "Kotlin", "Node.js"], sources=["b-cur2"]))
    assert row["status"] == "met"


def test_t2_a_level_word_needs_the_current_role_or_two_roles_in_the_window() -> None:
    row, changes = _check(_row("Fluency in Go and/or Rust", alternatives=["Go", "Rust"], sources=["b-mid1", "b-p1"]))
    assert row["status"] == "unclear" and changes[0].step == "T2"
    assert "'fluency' asks for depth" in row["code_note"] and "Go on b-mid1 (role, ended 2022)" in row["code_note"]
    row, changes = _check(_row("Strong Datadog experience", alternatives=["Datadog"], sources=["b-cur1"]))
    assert row["status"] == "met" and changes[0].step == "T2"
    row, _ = _check(_row("Go on Kubernetes", alternatives=["Go"], sources=["b-mid1"]))
    assert row["status"] == "met"  # no level word: one in-window role is enough


def test_t2_the_level_word_governs_an_example_list_and_a_colon_list_but_not_past_a_semicolon_or_a_stop() -> None:
    # B4 (D7): the dots of "e.g." are no clause break; ruling 1: a ":" is none either.
    row, changes = _check(_row("Proficient with modern container platforms (e.g. Kubernetes, Nomad)", alternatives=["Kubernetes", "Nomad"], sources=["b-mid1"]))
    assert row["status"] == "unclear" and changes[0].step == "T2" and "'proficient' asks for depth" in row["code_note"]
    row, changes = _check(_row("Fluency at the system level: orchestration (Kubernetes, Nomad)", alternatives=["Kubernetes"], sources=["b-mid1"]))
    assert row["status"] == "unclear" and changes[0].step == "T2"
    row, changes = _check(_row("Deep Datadog knowledge; experience with Kubernetes", alternatives=["Kubernetes"], sources=["b-mid1"]))
    assert row["status"] == "met" and changes[0].step == "T1"
    row, changes = _check(_row("Strong Datadog experience, and familiarity with Kubernetes", alternatives=["Kubernetes"], sources=["b-mid1"]))
    assert row["status"] == "met" and changes[0].step == "T1"
    row, changes = _check(_row("Strong knowledge of Playwright, Datadog, and Go", alternatives=["Go"], sources=["b-mid1"]))
    assert row["status"] == "unclear" and changes[0].step == "T2"  # ", and Go" continues the list the level word governs


def test_t2_a_level_word_on_a_skill_or_on_an_understanding_of_something_else_does_not_govern_the_tool() -> None:
    # M1 (D6) and the brief's "deep understanding of system design".
    row, changes = _check(_row("Strong communication skills and experience with Go", alternatives=["Go"], sources=["b-mid1"]))
    assert row["status"] == "met" and changes[0].step == "T1"
    row, changes = _check(_row("A deep understanding of distributed systems and experience with Go", alternatives=["Go"], sources=["b-mid1"]))
    assert row["status"] == "met" and changes[0].step == "T1"
    row, changes = _check(_row("Strong knowledge of Go and Playwright", alternatives=["Go", "Playwright"], sources=["b-mid1", "b-p1"]))
    assert row["status"] == "unclear" and changes[0].step == "T2"


def test_t2_reads_the_level_word_from_class_basis_when_the_row_text_lost_it() -> None:
    # Ruling 3 (27 line 19): the model split "strong knowledge of AWS, (...), containers (Docker or Kubernetes), and Postgres".
    basis = "Requirements: strong knowledge of Datadog, infrastructure as code (Terraform), containers (Docker or Kubernetes), and Postgres"
    row = _row("Kubernetes", alternatives=["Kubernetes"], sources=["b-mid1"])
    row["class_basis"] = basis
    row, changes = _check(row)
    assert row["status"] == "unclear" and changes[0].step == "T2" and "'strong' asks for depth" in row["code_note"]
    row = _row("Datadog", alternatives=["Datadog"], sources=["b-cur1"])
    row["class_basis"] = basis
    row, changes = _check(row)
    assert row["status"] == "met" and changes[0].step == "T2"
    # Never for a practices row: the level word in the basis does not reach T5.
    row = _row("containers", alternatives=["containers"], sources=["b-mid1"])
    row["class_basis"] = basis
    row, changes = _check(row)
    assert row["status"] == "met" and changes == ()


def test_t1_a_role_heading_is_read_by_its_sublines_not_the_company_name() -> None:
    # M7c: a company named like a tool is no line naming the tool.
    row, changes = _check(_row("Experience with Current Co", alternatives=["Current Co"], sources=["r-cur"]))
    assert row["status"] == "unclear" and changes[0].step == "T1" and "Current Co on no line of your master" in row["code_note"]


# --- T3 / T4: years and degrees -----------------------------------------------------------------------------


def test_t3_years_are_summed_from_the_roles_dates_and_cite_the_r_ids() -> None:
    row, changes = _check(_row("5+ years of software engineering experience", sources=["sum-1"]))
    assert row["status"] == "met" and changes[0].step == "T3" and row["sources"] == ["r-cur", "r-mid", "r-old"]
    assert "10 years" in row["code_note"] and "any role" in row["code_note"]
    row, _ = _check(_row("12+ years of software engineering experience", sources=["sum-1"]))
    assert row["status"] == "unclear" and row["code_note"].startswith("Strict read (T3)")


def test_t3_a_named_tool_in_the_years_clause_counts_only_the_in_window_roles_naming_it() -> None:
    row, _ = _check(_row("3+ years of Go experience", alternatives=["Go"], sources=["b-mid1"]))
    assert row["status"] == "met" and row["sources"] == ["r-mid"]
    row, _ = _check(_row("1+ years working on Healthcare systems", sources=["b-old1"]))
    assert row["status"] == "unclear" and "Healthcare" in row["code_note"]
    row, _ = _check(_row("4+ years of Go experience", alternatives=["Go"], sources=["b-mid1"]))
    assert row["status"] == "unclear"


def test_t3_a_comma_list_in_the_years_clause_counts_the_roles_naming_any_of_its_tools() -> None:
    # B1 (D2): "3+ years in Scala, Ruby or C++" with Ruby 4y4m in the current role.
    row, changes = _check(_row("3+ years of experience in Scala, Ruby or C++", alternatives=["Scala", "Ruby", "C++"], sources=["b-cur1"]))
    assert row["status"] == "met" and changes[0].step == "T3" and row["sources"] == ["r-cur"]
    assert "4 years 4 months with Scala, Ruby and C++" in row["code_note"]
    row, changes = _check(_row("5+ years of experience in Scala, Ruby or C++", alternatives=["Scala", "Ruby", "C++"], sources=["b-cur1"]))
    assert row["status"] == "unclear" and changes[0].step == "T3" and "with Scala, Ruby and C++" in row["code_note"]


def test_t4_a_degree_row_is_met_by_the_matching_degree_and_cites_its_e_id() -> None:
    row, changes = _check(_row("Bachelor's degree in Computer Science or a related field", alternatives=["Bachelor's degree", "related field"], sources=["sum-1"]))
    assert row["status"] == "met" and changes[0].step == "T4" and row["sources"] == ["e-1"]
    row, _ = _check(_row("Master's degree in Physics", sources=["sum-1"]))
    assert row["status"] == "unclear" and row["code_note"].startswith("Strict read (T4)")


def test_t4_a_degree_row_that_offers_years_instead_is_read_by_its_years_when_the_degree_does_not_hold() -> None:
    # "A master's degree in Robotics, or 8+ years of experience": the degree fails (B.S. Computer Science), the years track is read over the roles.
    row, changes = _check(_row("Master's degree in Physics, or 5+ years of software engineering experience", sources=["e-1"]))
    assert row["status"] == "met" and changes[0].step == "T3" and row["sources"] == ["r-cur", "r-mid", "r-old"]
    row, changes = _check(_row("Master's degree in Physics, or 12+ years of software engineering experience", sources=["e-1"]))
    assert row["status"] == "unclear" and changes[0].step == "T3"
    # "years" that are no alternative (after the degree, not after an "or") leave the degree read alone.
    row, _ = _check(_row("Master's degree in Physics and 5+ years of software engineering experience", sources=["e-1"]))
    assert row["status"] == "unclear" and row["code_note"].startswith("Strict read (T4)")


def test_t4_a_degree_row_without_a_field_is_met_by_any_degree_and_a_degree_of_ownership_is_no_degree_row() -> None:
    # B3 (D4): "Bachelor's degree required" names no field; B2 (D3): "a high degree of ownership" is a practices row.
    row, changes = _check(_row("Bachelor's degree required", sources=["sum-1"]))
    assert row["status"] == "met" and changes[0].step == "T4" and row["sources"] == ["e-1"]
    row, changes = _check(_row("Bachelor's degree preferred, or equivalent practical experience", sources=["sum-1"]))
    assert row["status"] == "met" and changes[0].step == "T4"
    row, changes = _check(_row("A high degree of ownership over production systems", sources=["b-cur1"]))
    assert row["status"] == "met" and changes == ()
    row, changes = _check(_row("A high degree of ownership over production systems", sources=["sum-1"]))
    assert row["status"] == "unclear" and changes[0].step == "T5"


# --- T5 / T6 / T7: practices, answers, silences --------------------------------------------------------------


def test_t5_a_practices_row_on_buried_lines_alone_is_unclear_but_one_clear_line_keeps_it() -> None:
    row, changes = _check(_row("Experience reviewing code with a team", sources=["sum-1", "o-1"]))
    assert row["status"] == "unclear" and changes[0].step == "T5"
    assert "found only on sum-1 (summary) and o-1 (Other)" in row["code_note"]
    row, changes = _check(_row("Experience reviewing code with a team", sources=["sum-1", "b-cur2"]))
    assert row["status"] == "met" and changes == ()


def test_t5_leaves_a_language_or_collaboration_row_alone_but_a_named_tool_row_is_still_lowered() -> None:
    for requirement in ("Strong English communication is required.", "Excellent written and verbal communication", "A self-starter who thrives in teamwork"):
        row, changes = _check(_row(requirement, sources=["sum-1"]))
        assert row["status"] == "met" and changes == (), requirement
    row, _ = _check(_row("Strong Go experience with clear communication", alternatives=["Go"], sources=["sum-1"]))
    assert row["status"] == "unclear" and "'strong' asks for depth" in row["code_note"]


def test_t6_a_row_resting_on_answers_or_stories_alone_is_answered() -> None:
    row, changes = _check(_row("Next.js", alternatives=["Next.js"], sources=["A framework:nextjs"]))
    assert row["status"] == ANSWERED and changes[0].step == "T6" and "you said yes" in row["code_note"]
    row, changes = _check(_row("Experience working remotely with clear asynchronous communication", sources=["A work:remote", "A story:remote-1"]))
    assert row["status"] == ANSWERED and changes[0].step == "T6"


def test_t7_an_unmet_row_that_cites_nothing_is_unclear_but_a_quoted_line_or_the_candidates_own_no_keeps_it_unmet() -> None:
    row, changes = _check(_row("Deep experience with Playwright", "unmet", evidence=["Story bank story:ran_playwright_once: ran Playwright once"]))
    assert row["status"] == "unclear" and changes[0].step == "T7"
    row, changes = _check(_row("Deep experience with Rust", "unmet", evidence=["b-mid1: Go services on Kubernetes (no Rust)"]))
    assert row["status"] == "unmet" and changes == ()
    # Fix 3 (pick harness m12-answered-backend): the candidate's own no is a contradicting fact, a gap and not a silence — as
    # "Your answer: ...", as the prompt's "Story bank <question_id>: <the answer>" (assess.md), or as an answer source.
    row, changes = _check(_row("Deep experience with Rust", "unmet", evidence=["Your answer: never shipped Rust in production"]))
    assert row["status"] == "unmet" and changes == ()
    row, changes = _check(_row("Deep experience with Rust", "unmet", evidence=["Story bank tooling:rust: never shipped Rust in production"]))
    assert row["status"] == "unmet" and changes == ()
    row, changes = _check(_row("Deep experience with Rust", "unmet", evidence=["Story bank story:rust_toy: a Rust toy"], sources=["A tooling:rust"]))
    assert row["status"] == "unmet" and changes == ()


def test_eligibility_optional_and_nice_rows_are_never_touched() -> None:
    rows = [
        _row("Must be in the EU", alternatives=["EU"], sources=["sum-1"], row_id="elig-region"),
        _row("Nice: Kotlin", alternatives=["Kotlin"], sources=["sum-1"], cls="nice"),
        _row("Kotlin", alternatives=["Kotlin"], sources=["sum-1"], cls="optional"),
    ]
    assert strict_rows(rows, _master()) == () and all(row["status"] == "met" and "code_note" not in row for row in rows)


# --- the stored row ----------------------------------------------------------------------------------------


def test_an_answered_row_round_trips_counts_as_nothing_in_the_fit_and_does_not_fail_the_gate() -> None:
    stored = {"requirement": "Next.js", "resume_evidence": ["Your answer: yes"], "status": "answered", "class": "askable", "id": "req-f1a001", "code_note": "Strict read (T6): you said yes."}
    row = RequirementMatrixRow.from_json(dict(stored))
    assert row.status is MatrixStatus.ANSWERED and row.code_note == stored["code_note"] and row.to_json() == stored
    plain = RequirementMatrixRow.from_json({"requirement": "Go", "resume_evidence": ["b-mid1"], "status": "met", "class": "hard"})
    assert "code_note" not in plain.to_json()
    assert fit_percent([row, plain]) == 50
    assert gate([row, plain], [], "matched_above_threshold").decision != NOT_A_MATCH
