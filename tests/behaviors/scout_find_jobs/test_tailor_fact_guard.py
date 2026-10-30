"""0.1.11.12 (dossier-B, gigai-hn4): the model-free fact guard over a tailored resume.

``tailor_fact_guard.unverified_facts(markdown, master_text, posting_text)`` lists the words and numbers a tailored
resume states that neither the master nor the posting states: a tool, a place, a number, a credential. Flags only,
for the user to read: it never rewrites, drops or blocks anything. Synthetic master, posting and resume; no model,
no home, no network.
"""

from __future__ import annotations

from gigai.scout.tailor_fact_guard import unverified_facts

MASTER = """## Summary

- Engineer with nine years on Python inference services.

## Experience

### Northwind Labs
Staff Engineer | 2023 - Present

- Own the Python inference services behind 40 product teams.
- Built Python services for six years; cut p99 latency by 40%.
- Served 4,000 requests a second from PostgreSQL.

## Skills

- Platform: Python, Kubernetes, PostgreSQL, Terraform
"""

POSTING = (
    "Harborlight is hiring a Staff AI Engineer to own Python inference services. Requirements: 5+ years of Python in "
    "production; Kubernetes; Helm charts for every service. Remote within the United States."
)

CLEAN = """Kar Ohm
kar@example.com | Seattle, WA

## Summary

- Engineer with nine years on Python inference services. <!-- R1 -->

## Experience

### Northwind Labs
Staff Engineer | 2023 - Present

- Own the Python inference services behind 40 product teams. <!-- R2 -->
- Built Python services for six years; cut p99 latency by 40%. <!-- R3 -->
- Served 4000 requests a second from Helm-managed Kubernetes. <!-- R4 -->

## Skills

- Platform: Python, Kubernetes, PostgreSQL, Terraform <!-- R5 -->
"""

INVENTED = """Kar Ohm
kar@example.com | Seattle, WA

## Experience

### Northwind Labs
Staff Engineer | 2023 - Present

- Own the Python inference services behind 40 product teams, moving them to Rust in Washington. <!-- R2 -->
- Built Python services for six years; cut p99 latency by 73%. <!-- R3 -->
- Certified in HIPAA controls for the Rust services. <!-- R3 -->

## Skills

- Platform: Python, Kubernetes, Rust, Datadog <!-- R5 -->
"""


def test_a_clean_tailoring_has_nothing_to_flag() -> None:
    # Verbatim master lines; a posting-only tool (Helm, hyphenated); 4000 for the master's 4,000; the header's city.
    assert unverified_facts(CLEAN, MASTER, POSTING) == ()


def test_an_invented_tool_place_number_and_credential_are_flagged_once_each_in_order() -> None:
    assert unverified_facts(INVENTED, MASTER, POSTING) == ("Rust", "Washington", "73%", "Certified", "HIPAA")


def test_a_sentence_initial_capital_is_not_a_claim_but_a_technical_token_is() -> None:
    resume = "## Experience\n\n### Northwind Labs\n\n- Delivered the services. Operated them too; moved them to k8s.\n- HIPAA controls were mine.\n"
    assert unverified_facts(resume, MASTER, None) == ("HIPAA",)


def test_headings_header_lines_dates_and_skills_lines_are_never_flagged() -> None:
    resume = (
        "Kar Ohm\nkar@example.com | Lisbon\n\n"
        "## Experience\n\n### Contoso\nPrincipal Engineer | Mar 2024 - Present\n\n- Joined in 2021.\n\n"
        "## Skills\n\n- Platform: Rust, Datadog\n\n## Languages\n\n- Portuguese\n"
    )
    assert unverified_facts(resume, MASTER, POSTING) == ()


def test_month_and_day_names_i_and_ok_never_flag() -> None:
    resume = "## Experience\n\n- Shipped the services in March, on a Monday, and I said OK.\n"
    assert unverified_facts(resume, MASTER, POSTING) == ()


def test_a_pasted_job_has_no_posting_text_so_the_master_alone_is_the_source() -> None:
    resume = "## Experience\n\n- Served 4000 requests a second from Helm-managed Kubernetes.\n"
    assert unverified_facts(resume, MASTER, POSTING) == ()
    assert unverified_facts(resume, MASTER, None) == ("Helm",)
    assert unverified_facts(resume, MASTER, "") == ("Helm",)


def test_numbers_match_by_value_and_number_words_count() -> None:
    resume = "## Experience\n\n- Forty product teams; six years; 4,000.0 requests; 12 regions.\n"
    assert unverified_facts(resume, MASTER, POSTING) == ("12",)


def test_plurals_aliases_and_possessives_meet_their_source() -> None:
    resume = "## Experience\n\n- Ran Postgres and the Kubernetes clusters' Terraform modules.\n"
    assert unverified_facts(resume, MASTER, POSTING) == ()


def test_a_number_after_the_same_digits_inside_a_term_is_still_a_number() -> None:
    # Fix round 1, F1: the mention's own offset, not the first place its digits appear on the line.
    assert unverified_facts("## Experience\n\n- Cut p99 latency across 99 regions.\n", MASTER, POSTING) == ("99",)
    assert unverified_facts("## Experience\n\n- Ran k8s for 8 teams.\n", MASTER, POSTING) == ("8",)


def test_a_bracketed_or_quoted_name_is_a_claim() -> None:
    # Fix round 1, F2: a bracket or a quote opens no sentence; only . ! ? do.
    resume = '## Experience\n\n- Rewrote the service (Rust) with a [Zig] module; "Go" too. Built it. Owned it!\n'
    assert unverified_facts(resume, MASTER, POSTING) == ("Rust", "Zig", "Go")


def test_every_credential_word_is_supported_by_its_own_family_only() -> None:
    # Fix round 1, F3 + N3: accredited / chartered families; `licence` meets `licensed`.
    resume = "## Experience\n\n- Fully accredited and chartered, with a licence and a patent.\n"
    assert unverified_facts(resume, MASTER, POSTING) == ("accredited", "chartered", "licence", "patent")
    assert unverified_facts(resume, MASTER + "\n- Accreditation, Chartered Engineer, licensed, patents.\n", POSTING) == ()


def test_only_a_bare_four_digit_year_is_exempt() -> None:
    # Fix round 1, N4: a bare 19xx/20xx span (2021, 2048) passes as a year; 2,000 dollars and two thousand users are numbers.
    resume = "## Experience\n\n- Joined in 2021; paid 2,000 dollars across 2048 shards for two thousand users.\n"
    assert unverified_facts(resume, MASTER, POSTING) == ("2,000", "two thousand")


def test_sections_open_at_h2_only_and_h4_is_a_sub_heading() -> None:
    # Fix round 1, N7: a legacy `# Name` header and its contact line are not a section; `####` is an entry heading.
    resume = (
        "# Kar Ohm\nkar@example.com | Lisbon | linkedin.com/in/karohm | +1 415 555 0100\n\n"
        "## Experience\n\n#### Contoso\nPrincipal Engineer | 2024 - Present\n\n- Own the Python inference services behind 40 product teams.\n"
    )
    assert unverified_facts(resume, MASTER, POSTING) == ()
