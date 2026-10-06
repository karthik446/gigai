"""0.1.11.3 Q2 (T1): the rule of ``stated_check``, case by case. Pure, offline, synthetic (nothing here is a real resume).

The end outcome (the stored assessment) is in ``test_stated_questions_end_outcome``; this file pins what counts as a
key term, what counts as the master stating it, and how years are read.
"""

from __future__ import annotations

from datetime import date

import pytest

from gigai.scout import stated_check as check

TODAY = date(2026, 10, 6)

MASTER = """## Summary

- Platform engineer with ten years on distributed services. <!-- id:sum-000001 -->

## Experience

### NORTHWIND <!-- id:r-000001 -->
**Staff Engineer | Jun 2021 - Present**
<!-- private note: mention Terraform and Helm first -->

- Ran the on-call rota for six services on AWS and cut resolution time by half. <!-- id:b-000001 -->
- Built the checkout UI in React and TypeScript for two product teams. <!-- id:b-000002 -->
<!-- private note: Svelte was a prototype only -->

### CONTOSO <!-- id:r-000002 -->
Engineer | 2016 - 2021

- Moved a batch ingest job to a Go queue worker on AWS, raising uptime to 99.9%. <!-- id:b-000003 -->
- Wrote the REST API layer for the billing service in JavaScript. <!-- id:b-000004 -->
- Launched the partner portal in Spring 2019 after two years of Postgres tuning. <!-- id:b-000007 -->

### FABRIKAM <!-- id:r-000003 -->
Support Analyst | Mar 2013 - Jan 2016

- Answered consumer-facing support tickets and worked with leadership on escalations. <!-- id:b-000005 -->

## Projects

### Toolbox <!-- id:p-000001 -->
2020

- Built a small agent runtime with approval gates; basic exposure to Rust. <!-- id:b-000006 -->
- Migrated off Jenkins to hosted runners. <!-- id:b-000008 -->
- Ownership of the release checklist for the runtime. <!-- id:b-000010 -->

## Skills

- Go, JavaScript, C++, C#, SQL, Kubernetes, Docker, AWS (EC2, S3), Distributed Systems, dbt

## Education

### State University <!-- id:e-000001 -->
B.S. Computer Science | 2009 - 2013

- Thesis on Erlang schedulers. <!-- id:b-000009 -->
"""
FACTS = check.read_master(MASTER, today=TODAY)
SKILLS = "Go, JavaScript, C++, C#, SQL, Kubernetes, Docker, AWS (EC2, S3), Distributed Systems, dbt"


def _stated(requirement: str, *, alternatives: bool = False, facts: check.MasterFacts = FACTS):
    return check.stated(requirement, facts, alternatives=alternatives)


# --- the master as the check reads it ---------------------------------------------------------------------


def test_the_master_is_read_as_lines_a_skills_line_and_dated_roles() -> None:
    assert [line.id for line in FACTS.lines] == ["sum-000001", "b-000001", "b-000002", "b-000003", "b-000004", "b-000007", "b-000005", "b-000006", "b-000008", "b-000010"]
    assert [line.text for line in FACTS.skills] == [SKILLS] and FACTS.skills[0].id is None
    assert {"go", "c++", "c#", "aws", "ec2", "s3", "distributed systems", "dbt"} <= FACTS.skill_names and FACTS.lower_names == {"dbt"}
    # A private note is never read, an Education line is not evidence of a tool, a project is not a dated role.
    assert not any("Terraform" in line.text or "Svelte" in line.text or "Erlang" in line.text for line in (*FACTS.lines, *FACTS.skills))
    assert [role.dated for role in FACTS.roles] == ["Staff Engineer | Jun 2021 - Present", "Engineer | 2016 - 2021", "Support Analyst | Mar 2013 - Jan 2016"]


def test_a_roles_months_are_the_products_years_refined_by_the_months_it_names() -> None:
    spans = {role.dated: role.end - role.start for role in FACTS.roles}
    assert spans["Engineer | 2016 - 2021"] == 5 * 12  # years only: by subtraction
    assert spans["Support Analyst | Mar 2013 - Jan 2016"] == 34  # both months named
    assert spans["Staff Engineer | Jun 2021 - Present"] == 5 * 12 + 4  # to today
    # An ongoing role that names no start month covers whole years; a role that names no year is not dated.
    assert check._months(["Engineer | 2020 - Present"], TODAY) == (2020 * 12, 2026 * 12)
    assert check._months(["Engineer"], TODAY) is None
    from gigai.scout.master_resume import MasterEntry

    entry = MasterEntry(id="r-1", section="experience", heading="X", sublines=("Engineer | 2016 - 2021",))
    assert (entry.start, entry.end) == (2016, 2021) and check._months(entry.sublines, TODAY) == (2016 * 12, 2021 * 12)  # the years the selector reads


def test_overlapping_roles_are_counted_once() -> None:
    master = "## Experience\n\n### A <!-- id:r-1 -->\nEngineer | 2018 - 2022\n\n- Ran Kafka. <!-- id:b-1 -->\n\n### B <!-- id:r-2 -->\nEngineer | 2019 - 2021\n\n- Ran Kafka too. <!-- id:b-2 -->\n"
    facts = check.read_master(master, today=TODAY)
    assert _stated("4+ years of Kafka", facts=facts) is not None
    assert _stated("5+ years of Kafka", facts=facts) is None  # 2018-2022 holds 2019-2021: four years, not six


# --- stated: positive -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requirement", "rule", "sources", "evidence"),
    [
        ("Experience with React", "master_line", ("b-000002",), None),
        ("Proficiency in React and TypeScript.", "master_line", ("b-000002",), None),  # both in one line: cited once
        ("React", "master_line", ("b-000002",), None),
        ("Strong Kubernetes experience", "skills_line", (), (SKILLS,)),
        ("experience with kubernetes", "skills_line", (), (SKILLS,)),  # a long lower-case word is the name
        ("Experience with dbt", "skills_line", (), (SKILLS,)),  # the master writes it in lower case itself
        ("Hands-on experience with C++ and C#", "skills_line", (), (SKILLS,)),
        ("Deep experience with distributed systems at scale", "skills_line", (), (SKILLS,)),  # strength and scale words are ignored
        ("Experience with EC2", "skills_line", (), (SKILLS,)),  # a name inside the brackets of a skill
        ("Experience with K8s", "skills_line", (), (SKILLS,)),  # one name, two spellings
        ("Experience with Golang", "master_line", ("b-000003",), None),
        ("Experience with PostgreSQL", "master_line", ("b-000007",), None),
        ("Experience with REST APIs", "master_line", ("b-000004",), None),  # a plural
        ("Experience building applications with Go and Docker", "master_line", ("b-000003",), None),  # two lines, one each
        ("Python or Go", "master_line", ("b-000003",), None),
        ("Familiarity with tools such as Docker, Podman or LXC", "skills_line", (), (SKILLS,)),
        ("8+ years on AWS", "years_from_roles", ("b-000001", "b-000003"), None),
        ("3+ years of Go", "years_from_roles", ("b-000003",), ("Engineer | 2016 - 2021", "Moved a batch ingest job to a Go queue worker on AWS, raising uptime to 99.9%.")),
        ("2 years of Postgres", "years_stated", ("b-000007",), None),  # the line states the years of the term
        ("8+ years of software engineering experience", "years_stated", ("sum-000001",), None),
        ("At least 10 years of professional software development experience", "years_stated", ("sum-000001",), None),
        ("Bachelor's degree in Physics, or 8+ years of software engineering experience", "years_stated", ("sum-000001",), None),
    ],
)
def test_a_requirement_made_only_of_stated_key_terms_is_settled(requirement, rule, sources, evidence) -> None:
    found = _stated(requirement)
    assert found is not None, requirement
    assert (found.rule, found.sources) == (rule, sources)
    if evidence is not None:
        assert found.evidence == evidence
    # Every evidence string is a line of the master, word for word: nothing is written by code.
    assert all(any(text == line.strip().lstrip("- ").split(" <!--")[0].strip("*") for line in MASTER.splitlines()) for text in found.evidence)


def test_a_row_with_alternatives_is_met_by_any_one_of_them() -> None:
    found = _stated("Experience with AWS, GCP, or similar cloud platforms", alternatives=True)
    assert found is not None and found.sources == ("b-000001",)
    # The same words with "and": every one is required.
    assert _stated("Experience with AWS, GCP and Azure", alternatives=True) is None


def test_career_years_without_a_summary_line_come_from_the_roles_with_an_engineering_title() -> None:
    facts = check.read_master(MASTER.replace("with ten years on", "working on"), today=TODAY)
    found = _stated("8+ years of software engineering experience", facts=facts)
    assert found is not None and found.rule == "years_from_roles" and found.sources == ()
    assert found.evidence == ("Staff Engineer | Jun 2021 - Present", "Engineer | 2016 - 2021")  # the Support Analyst years are not counted
    assert _stated("11+ years of software engineering experience", facts=facts) is None  # 10 years 4 months as an engineer


# --- stated: negative -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "requirement",
    [
        # a term inside another word, or in another sense
        "Experience with Java",  # JavaScript
        "Experience with C",  # C++, C#
        "Experience with Script",
        "Experience presenting to C-level stakeholders",
        "Willingness to go on call",  # the verb
        "Ability to react quickly",
        "Go above and beyond for customers",
        "Experience with Spring",  # "in Spring 2019" is a season
        "Strong leadership",  # a line says "worked with leadership"
        "Leadership experience",
        "Ownership of production systems",  # a line may START with any capitalised word
        "Experience with Fabrikam products",  # a company heading
        "Experience as a Staff Engineer",  # a role title is not a line
        "Experience with Erlang",  # an Education line
        "Experience with Terraform",  # only a private note names it
        # a line that takes the claim back
        "Experience with Rust",  # "basic exposure to Rust"
        "Experience with Jenkins",  # "Migrated off Jenkins"
        # the requirement asks for more than its key terms
        "Experience building consumer-facing products",
        "Experience building consumer-facing products in React",
        "Experience leading teams using React",
        "Experience leading teams using React or Vue",
        "Experience with payments systems in Go",
        "Experience running Kubernetes clusters",
        "Proficiency in Python, with exposure to Go or Rust",
        # a term the master does not state
        "Experience with Helm",
        "Experience with Docker and Helm",
        "Experience with Node.js",  # JavaScript is not Node
        "Experience with SQL and NoSQL databases",
        "Experience with CI/CD",
        "Experience with AWS/GCP",  # a slashed name is read whole
        "Python 3",
        # no key term at all
        "Experience with cloud platforms",
        "Strong communication skills",
        "",
        # years are strict
        "12+ years on AWS",
        "9+ years of Go",
        "5+ years of experience with Kubernetes",  # only the skills line names it: no years shown
        "3+ years of Postgres",  # the line states two, whatever the role's dates
        "Familiarity with tools such as Docker, Podman or containerd",  # a lower-case word may be substance: not read
        "10+ years of experience",  # of what?
        "5+ years of experience with cloud platforms",
        "7+ years of engineering leadership experience",
        "12+ years of software engineering experience",
        "3+ years of Go and 5+ years of AWS",  # two numbers
        "Manage a team of 10 engineers",
        "Bachelor's degree in Physics or equivalent experience",
        "Bachelor's degree in Physics, or 8+ years of experience",
        "5+ years developing or operating payment systems",  # one phrase about payments, not a track
    ],
)
def test_anything_else_is_left_as_it_came(requirement: str) -> None:
    assert _stated(requirement) is None, requirement


def test_c_is_met_only_by_c_itself() -> None:
    with_c = check.read_master(MASTER.replace("- Go, JavaScript, C++, C#,", "- Go, JavaScript, C, C++, C#,"), today=TODAY)
    found = _stated("Experience with C", facts=with_c)
    assert found is not None and found.rule == "skills_line"
    assert _stated("Experience presenting to C-level stakeholders", facts=with_c) is None
    objective = check.read_master("## Skills\n\n- Objective-C, C++, C#, Swift\n", today=TODAY)
    assert _stated("Experience with C", facts=objective) is None


def test_a_capitalised_name_must_be_written_the_same_way_in_the_master() -> None:
    facts = check.read_master("## Experience\n\n### A <!-- id:r-1 -->\n2020 - 2024\n\n- Helped teams react to incidents and go live faster. <!-- id:b-1 -->\n", today=TODAY)
    assert _stated("Experience with React", facts=facts) is None and _stated("Experience with Go", facts=facts) is None


# --- the answer: rows and questions -------------------------------------------------------------------------


def _row(requirement: str, status: str = "unclear", **more: object) -> dict[str, object]:
    return {"requirement": requirement, "class": "askable", "class_basis": "Requirements", "status": status, "resume_evidence": ["Not shown."], **more}


def _question(requirement: str) -> dict[str, object]:
    return {"question_id": "tooling:x", "question": "Do you?", "requirement": requirement}


def test_only_unclear_rows_are_settled_and_only_their_questions_are_dropped() -> None:
    rows = [
        _row("Experience with React"),
        _row("Experience with Helm"),
        _row("Experience with Docker", "met", sources=["b-000001"]),
        _row("Experience with Kubernetes", "unmet"),
    ]
    before = [dict(row) for row in rows]
    questions = [_question("experience with  REACT"), _question("Experience with Helm"), _question("Experience with Docker"), "a plain question"]
    kept, dropped, settled = check.settle_stated(rows, questions, FACTS)
    assert [(row["requirement"], rule) for row, rule in settled] == [("Experience with React", "master_line")]
    assert rows[0] == {**before[0], "status": "met", "resume_evidence": ["Built the checkout UI in React and TypeScript for two product teams."], "sources": ["b-000002"]}
    assert rows[1:] == before[1:]  # the unstated row, the met row and the unmet row are byte for byte what they were
    assert dropped == [questions[0]] and kept == questions[1:]


def test_a_settled_row_cited_by_the_skills_line_drops_sources_it_no_longer_rests_on() -> None:
    rows = [_row("Strong Kubernetes experience", sources=["b-000001"])]
    check.settle_stated(rows, [], FACTS)
    assert rows[0]["status"] == "met" and rows[0]["resume_evidence"] == [SKILLS] and "sources" not in rows[0]


@pytest.mark.parametrize(
    "row",
    [
        {"id": "elig-location", "requirement": "Experience with React"},  # a setup row, whatever its words
        {"id": "elig-sponsorship", "requirement": "Experience with React"},
        {"requirement": "Visa sponsorship for React engineers"},  # sponsorship is a label, never settled here
        {"requirement": "Work authorization in the US; React"},
    ],
)
def test_eligibility_and_sponsorship_rows_are_never_settled(row: dict[str, object]) -> None:
    rows = [{**_row(str(row["requirement"])), **row}]
    before = [dict(item) for item in rows]
    kept, dropped, settled = check.settle_stated(rows, [_question(str(row["requirement"]))], FACTS)
    assert rows == before and settled == [] and dropped == [] and len(kept) == 1
