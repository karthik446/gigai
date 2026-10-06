"""0.1.11.4 A3: what ``stated_check`` reads beyond a requirement made only of names. Pure, offline, synthetic.

The re-run of the 100 real assessments (numbers and kinds only) showed the 0.1.11.3 rule settling a handful of rows:
the stated tool stood in a line that also says "replaced" or "without", in a slashed name, behind words that describe
a kind of tool, in a row's ``alternatives``, or the row was ``met`` already and still asked. Each case here has its
negative beside it; the end outcome (the stored assessment) is in ``test_stated_more_end_outcome``.
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

- Replaced a legacy jQuery admin UI with React, cutting page load time by half. <!-- id:b-000001 -->
- Moved 40 services to Kubernetes with no downtime and without a feature freeze. <!-- id:b-000002 -->
- Owned infrastructure administration for the build fleet, on call one week in four. <!-- id:b-000003 -->
- Wrote the TypeScript client for the billing API. <!-- id:b-000004 -->

### CONTOSO <!-- id:r-000002 -->
Engineer | 2016 - 2021

- Provisioned staging environments with Terraform on AWS. <!-- id:b-000005 -->
- Tuned PostgreSQL queries for the reporting service; the Oracle cluster was retired. <!-- id:b-000006 -->
- Built a machine learning ranking job in Python. <!-- id:b-000007 -->
- Evaluated Pulsar but did not adopt it. <!-- id:b-000008 -->
- No production use of Cassandra, Riak or Couchbase. <!-- id:b-000009 -->
- Deprecated the SOAP gateway in favour of gRPC. <!-- id:b-000010 -->

## Skills

- Go, JavaScript, SQL, Docker, AWS (EC2, S3)

## Education

### State University <!-- id:e-000001 -->
B.S. Computer Science | 2009 - 2013
"""
FACTS = check.read_master(MASTER, today=TODAY)
REACT = "Replaced a legacy jQuery admin UI with React, cutting page load time by half."
KUBERNETES = "Moved 40 services to Kubernetes with no downtime and without a feature freeze."
ADMIN = "Owned infrastructure administration for the build fleet, on call one week in four."
TYPESCRIPT = "Wrote the TypeScript client for the billing API."
TERRAFORM = "Provisioned staging environments with Terraform on AWS."
POSTGRES = "Tuned PostgreSQL queries for the reporting service; the Oracle cluster was retired."
ML = "Built a machine learning ranking job in Python."
GRPC = "Deprecated the SOAP gateway in favour of gRPC."


def _stated(requirement: str, *, alternatives: bool = False, facts: check.MasterFacts = FACTS):
    return check.stated(requirement, facts, alternatives=alternatives)


def _is_master_line(text: str) -> bool:
    return any(text == line.strip().lstrip("- ").split(" <!--")[0].strip("*") for line in MASTER.splitlines())


def test_every_id_the_view_shows_is_known() -> None:
    assert {"sum-000001", "r-000001", "b-000010", "e-000001"} <= FACTS.ids and len(FACTS.ids) == 14


# --- a tool in ANY line, though the line takes something else back ------------------------------------------


@pytest.mark.parametrize(
    ("requirement", "rule", "evidence", "sources"),
    [
        ("Experience with React", "line_takes_back_other", (REACT,), ("b-000001",)),  # "Replaced a ... jQuery UI WITH React"
        ("Strong Kubernetes experience", "line_takes_back_other", (KUBERNETES,), ("b-000002",)),  # "... with no downtime" comes after it
        ("Experience with PostgreSQL", "line_takes_back_other", (POSTGRES,), ("b-000006",)),  # the other half of the line retires Oracle
        ("Experience with gRPC", "line_takes_back_other", (GRPC,), ("b-000010",)),  # what the gateway was deprecated in favour OF
        ("Experience with Python", "line_takes_back_other", (ML,), ("b-000007",)),  # "machine learning" is not "learning Python"
        ("Experience with Terraform", "master_line", (TERRAFORM,), ("b-000005",)),  # a clean line: the 0.1.11.3 rule, under its own name
        ("Experience with Docker", "skills_line", ("Go, JavaScript, SQL, Docker, AWS (EC2, S3)",), ()),
        # a phrase the master holds word for word
        ("Experience with infrastructure administration", "master_phrase", (ADMIN,), ("b-000003",)),
        ("Hands-on machine learning experience", "master_phrase", (ML,), ("b-000007",)),
        ("Experience with machine learning in Python", "master_phrase", (ML,), ("b-000007",)),  # a phrase beside a term: one line holds both
        ("5+ years of infrastructure administration", "master_phrase", ("Staff Engineer | Jun 2021 - Present", ADMIN), ("b-000003",)),
        # one plain word beside a key term: a line holds BOTH
        ("Experience building billing software in TypeScript", "word_beside_term", (TYPESCRIPT,), ("b-000004",)),
        ("5+ years building billing software in TypeScript", "word_beside_term", (TYPESCRIPT, "Staff Engineer | Jun 2021 - Present"), ("b-000004",)),
        # a slashed name is each of its names; a suffix is not part of the name
        ("Experience with Terraform/AWS", "compound_name", (TERRAFORM,), ("b-000005",)),
        ("Experience with React/TypeScript", "compound_name", (REACT, TYPESCRIPT), ("b-000001", "b-000004")),
        ("Experience building Terraform-based environments", "compound_name", (TERRAFORM,), ("b-000005",)),
        # words that describe a kind, before a category noun with examples or after "other" / "similar"
        ("Experience with infrastructure tools such as Terraform", "kind_words", (TERRAFORM,), ("b-000005",)),
        ("Experience with relational databases such as PostgreSQL", "kind_words", (POSTGRES,), ("b-000006",)),
        ("Proficiency in React or a similar frontend framework", "kind_words", (REACT,), ("b-000001",)),
        ("Experience with a modern frontend framework (React, Vue or Angular)", "kind_words", (REACT,), ("b-000001",)),
        ("Experience with container orchestration platforms like Kubernetes", "kind_words", (KUBERNETES,), ("b-000002",)),
        ("PostgreSQL or another relational database", "kind_words", (POSTGRES,), ("b-000006",)),
    ],
)
def test_a_stated_term_settles_the_row_with_the_masters_own_line(requirement, rule, evidence, sources) -> None:
    found = _stated(requirement)
    assert found is not None, requirement
    assert (found.rule, found.evidence, found.sources) == (rule, evidence, sources)
    assert all(_is_master_line(text) for text in found.evidence)  # nothing is written by code


@pytest.mark.parametrize(
    "requirement",
    [
        # what the line takes back is not evidence
        "Experience with jQuery",  # replaced
        "Experience with Oracle",  # "the Oracle cluster was retired"
        "Experience with SOAP",  # deprecated
        "Experience with Pulsar",  # "did not adopt it"
        "Experience with Cassandra",  # "No production use of Cassandra, Riak or Couchbase"
        "Experience with Riak",  # the "No" reaches every item of its list
        "Experience with Couchbase",
        # a name inside another name, a verb, another spelling
        "Experience with Java",  # the master says JavaScript
        "Experience with Java/JavaScript",
        "Experience with Script",
        "Willingness to go on call",  # the verb; the master lists Go
        "Ability to react quickly",
        # a slashed name: EVERY name is required
        "Experience with React/Vue",
        "Experience with AWS/GCP",
        "Experience with CI/CD",  # one name, and the master does not state it
        # a named SET where the master shows only part (the one row of the re-run a reader did not accept)
        "Experience with frameworks such as React, Vue, Svelte",
        "Experience with frameworks like React, Angular and Vue",
        "Experience with React, Redux and RTK",
        "Experience with tools like Docker, Podman, LXC",
        "Modern front-end development: React, TypeScript, Redux",
        # one plain word is the requirement's own substance; a phrase must be the master's, word for word
        "Experience with infrastructure",
        "Experience with infrastructure management",
        "Experience with database administration",
        "Experience leading teams using React",
        "Experience leading teams using tools such as React",
        "8+ years of infrastructure administration",  # the role that holds the phrase covers five
        # one plain word: only a line that holds it TOGETHER with a key term of the row states it
        "Experience building reporting software in TypeScript",  # "reporting" is in another line than TypeScript
        "Experience building payments software in TypeScript",  # no line says "payments"
        "Experience building billing software in Vue",  # the term itself is not stated
        "Experience with billing",  # no key term at all
        "8+ years building billing software in TypeScript",
        "Experience building admin software in React",  # "admin" is what the React line replaced
        "Experience with infrastructure administration in Terraform",  # the phrase and the term are in two different lines
        "Experience with machine learning in Go",
        # kind words only before a category noun with examples, never something done
        "Experience with payments systems in Go",
        "Experience managing platforms such as AWS",
        "Experience scaling databases such as PostgreSQL",
        "Experience with provisioning tools such as Terraform",  # an "-ing" word may be something done: not a kind word
        "Experience with regulated healthcare platforms in AWS",
        # a term the master does not state at all
        "Experience with Helm",
        "Experience with relational databases such as MySQL",
        "Vue or a similar frontend framework",
    ],
)
def test_what_the_master_does_not_state_is_left_as_it_came(requirement: str) -> None:
    assert _stated(requirement) is None, requirement


@pytest.mark.parametrize(
    ("rule", "requirement"),
    [
        ("master_phrase", "Experience with infrastructure administration"),
        ("word_beside_term", "Experience building billing software in TypeScript"),
        ("compound_name", "Experience with Terraform/AWS"),
        ("kind_words", "Experience with infrastructure tools such as Terraform"),
        ("line_takes_back_other", "Experience with React"),
    ],
)
def test_each_reading_is_independent_and_can_be_taken_out_alone(rule: str, requirement: str, monkeypatch: pytest.MonkeyPatch) -> None:
    others = [
        "Experience with infrastructure administration", "Experience building billing software in TypeScript", "Experience with Terraform/AWS",
        "Experience with infrastructure tools such as Terraform", "Experience with React",
    ]
    found = _stated(requirement)
    assert found is not None and found.rule == rule
    monkeypatch.setattr(check, "ACTIVE_RULES", check.ACTIVE_RULES - {rule})
    assert _stated(requirement) is None  # read as 0.1.11.3 read it
    assert all(_stated(other) is not None for other in others if other != requirement)  # the others are as they were
    assert _stated("Experience with Terraform").rule == "master_line"


def test_react_native_does_not_state_react() -> None:
    facts = check.read_master(MASTER.replace(REACT, "Shipped the field app in React Native to both stores."), today=TODAY)
    assert _stated("Experience with React", facts=facts) is None
    found = _stated("Experience with React Native", facts=facts)
    assert found is not None and found.evidence == ("Shipped the field app in React Native to both stores.",)


@pytest.mark.parametrize(
    ("line", "term", "weak"),
    [
        ("Replaced a legacy jQuery admin UI with React.", "React", False),
        ("Replaced a legacy jQuery admin UI with React.", "jQuery", True),
        ("Migrated off Jenkins to hosted runners.", "Jenkins", True),
        ("Rebuilt billing on Kafka without a freeze.", "Kafka", False),
        ("Ran the fleet without Kubernetes.", "Kubernetes", True),
        ("No experience with Go, Rust or Zig.", "Rust", True),
        ("Built it in Go. No Rust was used.", "Go", False),
        ("Kafka was later retired.", "Kafka", True),
        ("Kafka (deprecated) feeds for the old site.", "Kafka", True),
        ("Led the Angular replacement project in React.", "Angular", True),
        ("Led the Angular replacement project in React.", "React", False),
        ("Moved off Jenkins, Travis and CircleCI to hosted runners.", "Travis", True),
        ("Deprecated Angular and Backbone in favour of React.", "Backbone", True),
        ("Deprecated Angular and Backbone in favour of React.", "React", False),
        ("Evaluated Kafka, and chose not to adopt it.", "Kafka", True),
        ("We didn't ship the Kafka pipeline.", "Kafka", True),
        ("Used Kafka, not RabbitMQ.", "Kafka", True),  # "not" anywhere takes the whole line back, as before
        ("Basic exposure to Rust.", "Rust", True),
        ("Wrote Visual Basic macros for the Excel team.", "Excel", False),
        ("Currently learning Rust.", "Rust", True),
        ("Built deep learning models in PyTorch.", "PyTorch", False),
    ],
)
def test_a_line_takes_back_only_what_it_says_it_of(line: str, term: str, weak: bool) -> None:
    start = line.index(term)
    assert check._weak_at(line, start, start + len(term)) is weak


# --- a row with alternatives: one stated alternative is enough ----------------------------------------------


@pytest.mark.parametrize(
    ("requirement", "options", "evidence", "sources"),
    [
        ("Hands-on experience with cloud infrastructure tooling", ["Pulumi", "Terraform"], (TERRAFORM,), ("b-000005",)),
        ("Experience operating container platforms such as Nomad or Kubernetes", ["Nomad", "Kubernetes"], (KUBERNETES,), ("b-000002",)),
        ("5+ years with a major cloud provider", ["GCP", "AWS"], ("Engineer | 2016 - 2021", TERRAFORM), ("b-000005",)),
    ],
)
def test_one_stated_alternative_settles_a_row_whatever_its_head_says(requirement, options, evidence, sources) -> None:
    assert _stated(requirement, alternatives=True) is None  # the head is not made of names: the 0.1.11.3 rule leaves it
    found = check.stated_alternative(requirement, options, FACTS)
    assert found is not None and (found.rule, found.evidence, found.sources) == ("alternative", evidence, sources)


@pytest.mark.parametrize(
    ("requirement", "options"),
    [
        ("Hands-on experience with cloud infrastructure tooling", ["Pulumi", "Ansible"]),  # none is stated
        ("Hands-on experience with cloud infrastructure tooling", ["Java"]),  # JavaScript is not Java
        ("Hands-on experience with cloud infrastructure tooling", ["go", "react"]),  # verbs, as written
        ("Hands-on experience with cloud infrastructure tooling", ["jQuery", "Oracle"]),  # only in lines that take them back
        ("Hands-on experience with cloud infrastructure tooling", []),
        ("Experience with Terraform, Pulumi and Ansible", ["Terraform", "Pulumi", "Ansible"]),  # a SET: "and" joins them
        ("Experience leading teams that build with React or Vue", ["React", "Vue"]),  # people, not a tool
        ("Experience mentoring engineers on infrastructure tooling", ["Terraform", "Pulumi"]),
        ("AWS or GCP certification", ["AWS", "GCP"]),  # a credential
        ("Expert-level knowledge of a cloud provider", ["AWS", "GCP"]),  # a level
        ("Experience with at least two of the following", ["Terraform", "Docker", "AWS"]),  # a number
        ("Experience with all of the following", ["Terraform", "Docker", "AWS"]),
        ("9+ years with a major cloud provider", ["GCP", "AWS"]),  # the years are required of the alternative
        ("5+ years with an infrastructure tool", ["Docker"]),  # only the skills line names it: no years shown
        ("Experience with Ruby and one container tool", ["Docker", "Podman"]),  # the head names a tool the master does not state
    ],
)
def test_alternatives_the_master_does_not_settle_leave_the_row_as_it_came(requirement, options) -> None:
    assert check.stated_alternative(requirement, options, FACTS) is None


def test_a_tool_the_head_names_is_cited_beside_the_alternative() -> None:
    found = check.stated_alternative("Experience with Docker and one infrastructure tool", ["Pulumi", "Terraform"], FACTS)
    assert found is not None and found.evidence == ("Go, JavaScript, SQL, Docker, AWS (EC2, S3)", TERRAFORM) and found.sources == ("b-000005",)


# --- the answer: rows and questions -------------------------------------------------------------------------


def _row(requirement: str, status: str = "unclear", **more: object) -> dict[str, object]:
    return {"requirement": requirement, "class": "askable", "class_basis": "Requirements", "status": status, "resume_evidence": ["Not shown."], **more}


def _question(requirement: str, slug: str = "x") -> dict[str, object]:
    return {"question_id": f"tooling:{slug}", "question": "Do you?", "requirement": requirement}


def test_an_unclear_row_with_a_stated_alternative_is_met_and_its_question_dropped() -> None:
    rows = [_row("Hands-on experience with cloud infrastructure tooling", alternatives=["Pulumi", "Terraform"]), _row("A build tool", alternatives=["Bazel", "Buck"])]
    before = dict(rows[1])
    questions = [_question("Hands-on experience with cloud infrastructure tooling"), _question("A build tool")]
    kept, dropped, settled = check.settle_stated(rows, questions, FACTS)
    assert [rule for _row_, rule in settled] == ["alternative"] and dropped == [questions[0]] and kept == [questions[1]]
    assert rows[0]["status"] == "met" and rows[0]["resume_evidence"] == [TERRAFORM] and rows[0]["sources"] == ["b-000005"]
    assert rows[1] == before  # no alternative of it is stated: the row and its question are as they came


def test_a_question_on_a_row_that_is_met_already_is_dropped_and_the_row_is_not_touched() -> None:
    degree = "Bachelor's degree in Computer Science or equivalent practical experience"
    rows = [
        _row("Experience with React", "met", resume_evidence=[REACT], sources=["b-000001"]),  # the check finds it stated itself
        _row(degree, "met", resume_evidence=["B.S. Computer Science | 2009 - 2013"], sources=["e-000001"]),  # an alternative track, met by a shown line
        _row("A cloud provider", "met", resume_evidence=[TERRAFORM], sources=["b-000005"], alternatives=["GCP", "Azure"]),  # the same, by the field
        _row("Experience with Helm", "met", resume_evidence=[KUBERNETES], sources=["b-000002"]),  # not stated, not a track: its question stays
        _row("Experience with Vue or Angular", "met", resume_evidence=["Front-end work."]),  # a track with no line cited: stays
        _row("Experience with Nomad or Mesos", "met", sources=["b-999999"]),  # a source the view never showed: stays
        _row("Experience with Kubernetes", "unmet"),  # an unmet row is never this check's
    ]
    before = [dict(row) for row in rows]
    questions = [_question(str(row["requirement"]), str(index)) for index, row in enumerate(rows)] + [_question("A row the matrix does not hold", "none"), "a plain question"]
    kept, dropped, settled = check.settle_stated(rows, questions, FACTS)
    assert rows == before  # byte for byte: a met row is never made worse, or better
    assert [(row["requirement"], rule) for row, rule in settled] == [
        ("Experience with React", "met_row_question"), (degree, "alternative_track_question"), ("A cloud provider", "alternative_track_question"),
    ]
    assert dropped == questions[:3] and kept == questions[3:]
    assert {rule for _row_, rule in settled} <= check.QUESTION_ONLY_RULES


@pytest.mark.parametrize(
    ("rule", "at"),
    [("alternative", 0), ("met_row_question", 1), ("alternative_track_question", 2)],
)
def test_each_row_rule_is_independent_and_can_be_taken_out_alone(rule: str, at: int, monkeypatch: pytest.MonkeyPatch) -> None:
    def run():
        rows = [
            _row("Hands-on experience with cloud infrastructure tooling", alternatives=["Pulumi", "Terraform"]),
            _row("Experience with Terraform", "met", sources=["b-000005"]),
            _row("A degree in Physics or equivalent practical experience", "met", sources=["e-000001"]),
        ]
        questions = [_question(str(row["requirement"]), str(index)) for index, row in enumerate(rows)]
        _kept, dropped, settled = check.settle_stated(rows, questions, FACTS)
        return [rule for _row_, rule in settled], [questions.index(item) for item in dropped], rows[0]["status"]

    assert run() == (["alternative", "met_row_question", "alternative_track_question"], [0, 1, 2], "met")
    monkeypatch.setattr(check, "ACTIVE_RULES", check.ACTIVE_RULES - {rule})
    rules, dropped, status = run()
    assert rule not in rules and len(rules) == 2 and dropped == [index for index in range(3) if index != at]
    assert status == ("unclear" if rule == "alternative" else "met")


def test_two_rows_with_the_same_words_keep_the_question_while_one_of_them_is_open() -> None:
    rows = [_row("Experience with React", "met", sources=["b-000001"]), _row("Experience with React", "unmet")]
    questions = [_question("Experience with React")]
    kept, dropped, _settled = check.settle_stated(rows, questions, FACTS)
    assert kept == questions and dropped == []


def test_eligibility_and_sponsorship_rows_keep_their_questions_though_met() -> None:
    rows = [
        {**_row("Remote in the US or Canada", "met", sources=["b-000001"]), "id": "elig-location"},
        _row("Visa sponsorship for React or Vue engineers", "met", sources=["b-000001"]),
    ]
    before = [dict(row) for row in rows]
    questions = [_question("Remote in the US or Canada"), _question("Visa sponsorship for React or Vue engineers")]
    kept, dropped, settled = check.settle_stated(rows, questions, FACTS)
    assert rows == before and kept == questions and dropped == [] and settled == []
