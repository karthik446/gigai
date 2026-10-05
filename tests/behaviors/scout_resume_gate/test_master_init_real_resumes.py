"""0.1.10.11 MI (ticket 0110-10-09): ``master init`` accepts the resumes people have.

The UAT of 0.1.10.9 ran ``gigai scout resume master init`` on two real resumes and was refused three times:

* the migration: ``migration_resume_unreadable`` "text after an entry's bullets" at a ``---`` rule between two roles;
* ``init --from FILE``: ``master_markdown_invalid`` "unknown section" at ``## WORK EXPERIENCE`` and at
  ``## AGENTIC AI PROJECTS``, and no accepted form for a project written as ``**Name** — tagline`` plus a line;
* ``init --from FILE --dry-run``: ``master_option_invalid`` (the trial had to be made on a scratch home).

The END outcomes, through the real CLI on a scratch home: both forms store the resume, every heading read
as another section and every line left out is said by line number, a dry run of ``--from`` says the same and
leaves the home byte for byte as it was, and contact data is still never stored.

Fixtures: ``tests/evals/fixtures/master/shapes`` (all invented: the shapes the ticket names, one invented
person, ``example.test`` / ``555-01xx`` contact values). No operator data is read. No model is called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re

from click.testing import CliRunner
import pytest

from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.scout import master_migration as mm
from gigai.scout.master_resume import Master, MasterResumeError, assign_ids, build_master, draft_master, parse_master, skill_names
from gigai.scout.master_store import import_master, load_master, strip_contact
from gigai.scout.target_resolution import home_scout_target

from tests.support.contact_scan import contact_values_in
from tests.support.scout_profile_fixtures import default_find_jobs_config
from tests.support.setup_home import setup_home

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "master"
SHAPES = FIXTURES / "shapes"
#: What a fixture's header holds: the invented person's name and contact values. None of it may reach a master.
CONTACT = ("Jordan", "JORDAN", "example.test", "555", "linkedin")
SUMMARY = "Platform engineer with 11 years of experience running Kubernetes control planes and the delivery tooling around them"
SIX = ("summary", "experience", "skills", "education", "projects", "other")
#: A line that is only a rule or an underline, as this file sees it (not the reader's own pattern).
MARKS_ONLY = re.compile(r"\A[\s=*_\-—–─━]+\Z")


@dataclass(frozen=True)
class Shape:
    """One resume and what a master made of it holds (file line numbers: the file as it is on disk)."""

    name: str
    sections: tuple[str, ...]
    #: ``(section, heading, role lines, first year, last year (None: ongoing or none), ongoing, bullets)`` in the file's order.
    entries: tuple[tuple[str, str, tuple[str, ...], int | None, int | None, bool, int], ...]
    summary: tuple[str, ...] = ()
    skills: tuple[tuple[str, tuple[str, ...]], ...] = ()
    other: tuple[str, ...] = ()
    left_out: dict[str, list[int]] = field(default_factory=dict)
    #: ``(line, the heading's words, the section, how)`` for every heading that is not the section's own name.
    headings: tuple[tuple[int, str, str, str], ...] = ()
    read_as: dict[str, list[int]] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return SHAPES / f"{self.name}.md"

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")


QUILLMARK = ("experience", "Quillmark Systems", ("Staff Platform Engineer | Mar 2020 - Present",), 2020, None, True)
SHAPE_LIST = (
    # The ticket's resume: '---' rules (one between two roles, one right under a bullet), sections in capitals that
    # are not the six names, projects as a bold title with a tagline and a second line, bold skill labels.
    Shape(
        "rules-and-capital-sections",
        ("summary", "experience", "projects", "skills", "education"),
        (
            (*QUILLMARK, 2),
            ("experience", "Pendle Works", ("Senior Software Engineer | Jun 2015 - Feb 2020",), 2015, 2020, False, 2),
            ("projects", "Driftwatch — a drift detector for Terraform state", ("Go, PostgreSQL and a planner agent with approval gates | 2023 - Present",), 2023, None, True, 2),
            ("projects", "Ledgerlens — an agent that explains billing anomalies", ("Python and Kafka",), None, None, False, 1),
            ("education", "B.S. Computer Science — Northfield State University", ("2010 - 2014",), 2010, 2014, False, 0),
        ),
        summary=(SUMMARY + ", now building agent workflows on top.",),
        skills=(
            ("Languages", ("Go", "Python", "SQL")),
            ("Cloud and infrastructure", ("Kubernetes", "Terraform", "AWS", "GCP")),
            ("Agents", ("tool calling", "evaluation harnesses", "human approval gates")),
        ),
        left_out={"contact": [1, 3]},
        headings=((12, "WORK EXPERIENCE", "experience", "name"), (29, "AGENTIC AI PROJECTS", "projects", "closest"), (44, "TECHNICAL SKILLS", "skills", "name")),
    ),
    # Section names beyond the six: two that are one section, one found by a word of it, one nobody knows.
    Shape(
        "section-names",
        ("summary", "experience", "projects", "skills", "education", "other"),
        (
            (*QUILLMARK, 1),
            ("experience", "Northfield Systems Lab", ("Research Assistant | 2013 - 2014",), 2013, 2014, False, 1),
            ("projects", "Driftwatch", (), None, None, False, 1),
            ("projects", "Kubelint", (), None, None, False, 1),
            ("education", "Northfield State University", ("B.S. Computer Science | 2010 - 2014",), 2010, 2014, False, 0),
        ),
        summary=(SUMMARY + ".",),
        skills=(("Languages", ("Go", "Python", "SQL")), ("Cloud", ("Kubernetes", "Terraform", "AWS")), ("", ("Grafana", "Prometheus", "OpenTelemetry"))),
        other=(
            "Fair scheduling under a fixed power budget, Northfield Systems Workshop (2014).",
            "Quillmark engineering award for the 2022 migration.",
            "Certified Kubernetes Administrator (2021)",
            "Mentors 4 engineers a year through the promotion cycle.",
        ),
        left_out={"contact": [1, 2], "unknown_section": [56]},
        headings=(
            (4, "About", "summary", "name"), (8, "Professional Experience", "experience", "name"),
            (14, "Research Experience", "experience", "closest"), (20, "Selected Projects", "projects", "name"),
            (25, "Open Source", "projects", "name"), (30, "Technical Skills", "skills", "name"),
            (35, "Tools I Use Daily", "skills", "closest"), (39, "Education & Training", "education", "name"),
            (44, "Publications", "other", "name"), (48, "Awards", "other", "name"), (52, "Certifications", "other", "name"),
            (56, "Leadership & Mentoring", "other", "default"),
        ),
    ),
    # No '###' anywhere: underlined headings, a bold employer with an italic title, a description under the title, a
    # second title after the bullets, text after the bullets, labels above their skills, schools as a list, and a
    # list in an Experience-like section that has no employer.
    Shape(
        "plain-roles-and-lists",
        ("summary", "experience", "skills", "education", "other"),
        (
            (
                "experience", "Quillmark Systems — Northfield (remote)",
                ("Staff Platform Engineer | 2020-03 – Present", "The platform group of 14 engineers that owns the control plane and the release tooling for three clouds."),
                2020, None, True, 2,
            ),
            ("experience", "Quillmark Systems — Northfield (remote)", ("Senior Platform Engineer | 01.2018 – 02.2020",), 2018, 2020, False, 2),
            ("experience", "Pendle Works", ("Senior Software Engineer, Jun. 2015 through Dec. 2017",), 2015, 2017, False, 1),
            ("education", "B.S. Computer Science, Northfield State University", ("2010 - 2014",), 2010, 2014, False, 0),
            ("education", "Certificate in Data Engineering, Pendle Evening College", ("2016",), 2016, 2016, False, 0),
        ),
        summary=(SUMMARY + ".",),
        skills=(("Languages", ("Go", "Python", "SQL")), ("Cloud", ("Kubernetes", "Terraform", "AWS"))),
        other=("Chaired the architecture review of 12 teams for 3 years.", "Mentors 4 engineers a year through the promotion cycle."),
        left_out={"contact": [1, 2], "section_in_other": [48]},
        headings=((10, "PROFESSIONAL EXPERIENCE", "experience", "name"), (48, "LEADERSHIP EXPERIENCE", "experience", "closest")),
        read_as={"entry_line": [24], "listed_entry": [44, 46], "kept_in_other": [51, 52]},
    ),
)
SHAPES_BY_NAME = pytest.mark.parametrize("shape", SHAPE_LIST, ids=[shape.name for shape in SHAPE_LIST])


def _read(text: str) -> tuple[Master, dict[str, object]]:
    """``text`` as ``master init --from`` reads it: the privacy strip, the reader, ids; the master and the count."""

    clean, gone = strip_contact(text)
    draft, reading = mm.read_file(clean)
    assert reading is not None
    assign_ids(draft, None)
    return build_master(draft), mm.file_source_lines(reading, tuple(sorted({line for _kind, line in gone.lines}))).to_json()


def _squash(line: str) -> str:
    """A line's letters and digits alone (no marks, no list number): what must be found in the master for the line to be in it."""

    return re.sub(r"[^a-z0-9]", "", re.sub(r"\A\s*\d{1,2}[.)]\s+", "", line).casefold())


def _entries(master: Master) -> list[tuple[object, ...]]:
    return [(entry.section, entry.heading, entry.sublines, entry.start, entry.end, entry.ongoing, len(entry.bullets)) for entry in master.entries.values()]


@SHAPES_BY_NAME
def test_a_real_resume_reads_as_sections_and_dated_entries(shape: Shape) -> None:
    master, _lines = _read(shape.text)
    assert master.sections == shape.sections
    assert [item.text for item in master.in_section("summary")] == list(shape.summary)
    assert _entries(master) == list(shape.entries)
    assert [skill_names(item.text) for item in master.in_section("skills")] == list(shape.skills)
    assert [item.text for item in master.in_section("other")] == list(shape.other)
    # Every role has its dates, so the oldest-first rule can order the roles.
    assert all(entry.start is not None for entry in master.entries.values() if entry.section == "experience")
    # What is stored is a master: it reads back line for line, strictly.
    assert parse_master(master.markdown()).to_json() == master.to_json()
    text = master.markdown(ids=False)
    # No contact data, no rule or underline as text, no emphasis mark left on a Skills line, no wrapped line broken in two.
    assert not [value for value in CONTACT if value in text]
    assert not [line for line in text.splitlines() if line.strip() and MARKS_ONLY.match(line.removeprefix("- "))]
    assert "---" not in text and "===" not in text and "***" not in text
    assert all("*" not in item.text for item in master.in_section("skills"))
    assert all(not item.text.endswith(",") for item in master.items.values())


@SHAPES_BY_NAME
def test_every_line_of_a_real_resume_is_kept_or_said(shape: Shape) -> None:
    master, lines = _read(shape.text)
    (resume,) = lines["resumes"]  # type: ignore[misc]
    left = {row["reason"]: row["lines"] for row in resume["left_out"]}
    assert left == shape.left_out
    assert all(row["why"] == mm.LEFT_OUT_REASONS[row["reason"]] for row in resume["left_out"])
    # What was mapped is said: every heading that is not the section's own name, with the section it became and how.
    assert [(row["line"], row["heading"], row["section"], row["how"]) for row in resume["sections"]] == list(shape.headings)
    assert all(row["why"] == mm.SECTION_HOW[row["how"]] for row in resume["sections"])
    assert {row["how"]: row["lines"] for row in resume["read_as"]} == shape.read_as
    assert all(row["why"] == mm.READ_AS[row["how"]] for row in resume["read_as"])
    # The count is closed. A content line is one with a letter or a digit in it: a rule and an underline hold no text.
    source = shape.text.splitlines()
    content = [number for number, line in enumerate(source, 1) if line.strip() and not MARKS_ONLY.match(line)]
    assert lines["in"] == resume["in"] == len(content)
    assert (lines["folded"], lines["in"]) == (0, lines["kept"] + lines["left_out"])  # type: ignore[operator]
    assert lines["left_out"] == sum(len(numbers) for numbers in left.values()) and lines["left_out_by_reason"]["unread"] == 0  # type: ignore[index]
    # Counted a second way, from the text: a line that is not named as left out is in the master word for word
    # (marks and wraps aside), or is a section heading (one of the six names, or one said above).
    stored = _squash(master.markdown(ids=False))
    named = {number for numbers in left.values() for number in numbers}
    headings = {row["line"] for row in resume["sections"]} | {number for number in content if source[number - 1].strip("# :").casefold() in SIX}
    assert [number for number in content if number not in named and number not in headings and _squash(source[number - 1]) not in stored] == []
    assert [number for number in shape.left_out.get("contact", []) if _squash(source[number - 1]) in stored] == []
    # The migration reads the same resume the same way (it gets it after `resume add` stripped it: the same strip).
    clean, gone = strip_contact(shape.text)
    plan = mm.plan_migration([mm.SourceResume("only", clean, ("default",), tuple(sorted({line for _kind, line in gone.lines})))])
    assert plan.master.to_json() == master.to_json()
    merged = plan.to_json()["source_lines"]
    assert {**merged["resumes"][0], "profiles": []} == resume  # type: ignore[index]
    assert (merged["in"], merged["kept"], merged["folded"], merged["left_out"]) == (lines["in"], lines["kept"], 0, lines["left_out"])  # type: ignore[index]


# --- the reader's rules, case by case ------------------------------------------------------------


def _draft(text: str) -> mm.ResumeReading:
    return mm.read_resume_lines(text)


def _experience(body: str) -> list[tuple[str, list[str], list[str]]]:
    reading = _draft("## Experience\n" + body)
    return [(entry.heading, entry.sublines, [bullet.text for bullet in entry.bullets]) for section in reading.draft.sections if section.name == "experience" for entry in section.entries]


@pytest.mark.parametrize("rule", ["---", "***", "___", "- - -", "* * *", "-----------", "———", "==="])
def test_a_horizontal_rule_is_a_blank_line_wherever_it_stands(rule: str) -> None:
    role = "### Acme\nEngineer | 2020 - 2022\n- Built the billing service.\n- Ran the on-call rotation.\n"
    wanted = [("Acme", ["Engineer | 2020 - 2022"], ["Built the billing service.", "Ran the on-call rotation."]), ("Borealis", ["Analyst | 2018 - 2020"], ["Wrote the reports."])]
    after = "### Borealis\nAnalyst | 2018 - 2020\n- Wrote the reports.\n"
    # The ticket's line: a rule after a blank line under an entry's bullets (it was "text after an entry's bullets").
    assert _experience(f"{role}\n{rule}\n\n{after}") == wanted
    # Right under the last bullet, where it was read as the rest of the bullet.
    assert _experience(f"{role}{rule}\n{after}") == wanted
    # Between the heading and the first entry, and at the very end.
    assert _experience(f"\n{rule}\n\n{role}\n{after}\n{rule}\n") == wanted
    # Between sections, and around a summary that has no heading: a rule ends a paragraph and is never a line.
    sentence = "Backend engineer with nine years of building payment services in Go."
    reading = _draft(f"{rule}\n{sentence}\n{rule}\n## Experience\n{role}\n{rule}\n\n## Skills\n{rule}\nGo, Python\n{rule}\n")
    assert [(section.name, [item.text for item in section.items]) for section in reading.draft.sections if section.items] == [
        ("summary", [sentence]), ("skills", ["Go, Python"]),
    ]
    # A rule is not a content line: nothing is left out and the count is closed without it.
    assert reading.left_out == () and reading.lines == 8 == reading.section_lines + sum(reading.spans.values())


def test_an_underline_makes_a_heading_only_where_the_line_above_is_one() -> None:
    sentence = "Backend engineer with nine years of building payment services in Go."
    text = (
        f"Curriculum Vitae\n================\n\nSummary\n-------\n{sentence}\n\n"
        "Work Experience\n---------------\n\nAcme\nEngineer | 2020 - 2022\n- Built the billing service.\n\n"
        "Skills\n------\nGo, Python\n---\n"
    )
    reading = _draft(text)
    assert [section.name for section in reading.draft.sections] == ["summary", "experience", "skills"]
    assert [item.text for item in reading.draft.sections[0].items] == [sentence]
    assert [(entry.heading, entry.sublines) for entry in reading.draft.sections[1].entries] == [("Acme", ["Engineer | 2020 - 2022"])]
    # '---' under a line that names no section is a rule: the line stays what it was (here: the Skills line).
    assert [item.text for item in reading.draft.sections[2].items] == ["Go, Python"]
    # The '===' line above is a title: left out, by line number. The underlines are not content lines.
    assert reading.sections == ((8, "Work Experience", "experience", "name"),) and reading.left_out == (("above_first_section", 1),)
    assert reading.lines == 9 == reading.section_lines + sum(reading.spans.values()) + 1
    # A sentence between two '===' rules is a sentence; a section may be underlined by '===' as well.
    same = _draft(f"===\n{sentence}\n===\n\nEXPERIENCE\n==========\n\nAcme\nEngineer | 2020 - 2022\n- Built the billing service.\n")
    assert [(section.name, [item.text for item in section.items]) for section in same.draft.sections] == [("summary", [sentence]), ("experience", [])]
    assert same.left_out == ()


@pytest.mark.parametrize(
    ("heading", "section", "how"),
    [
        ("Summary", "summary", "name"), ("Profile", "summary", "name"), ("About", "summary", "name"), ("ABOUT ME", "summary", "name"),
        ("Professional Summary", "summary", "name"), ("Career Objective", "summary", "closest"),
        ("WORK EXPERIENCE", "experience", "name"), ("Professional Experience", "experience", "name"), ("Employment History", "experience", "name"),
        ("Industry Experience", "experience", "closest"), ("Career Timeline", "experience", "closest"),
        ("Technical Skills", "skills", "name"), ("Core Competencies", "skills", "name"), ("Tech Stack", "skills", "name"),
        ("Skills & Tools", "skills", "name"), ("Languages and Technologies", "skills", "closest"), ("Technical Proficiencies", "skills", "closest"),
        ("Education", "education", "name"), ("Education & Training", "education", "name"), ("Academic Background", "education", "name"),
        ("Education and Certifications", "education", "closest"), ("Academic Qualifications", "education", "closest"),
        ("Selected Projects", "projects", "name"), ("Open Source", "projects", "name"), ("AGENTIC AI PROJECTS", "projects", "closest"),
        ("Open-Source Contributions", "projects", "closest"), ("Project Highlights", "projects", "closest"),
        ("Publications", "other", "name"), ("Awards", "other", "name"), ("Certifications", "other", "name"), ("Honors & Awards", "other", "name"),
        ("Speaking", "other", "default"), ("Leadership Notes", "other", "default"),
    ],
)
def test_a_section_that_is_not_one_of_the_six_is_read_as_the_closest_and_said(heading: str, section: str, how: str) -> None:
    body = "### Thing One\nDetail | 2020 - 2022\n- Did the first thing.\n" if section in ("experience", "education", "projects") else "- Did the first thing.\n"
    for marks, written, words in (("##", heading, heading), ("#", heading, heading), ("##", f"{heading}:", heading), ("##", f"**{heading}**", heading), ("##", heading.upper(), heading.upper())):
        # A section of its own name stands beside it, so the file has one heading every reader knows.
        reading = _draft(f"{marks} {written}\n\n{body}\n{marks} Other\n- Another line.\n")
        assert reading.draft.sections[0].name == section, written
        assert [(entry.heading, entry.sublines) for entry in reading.draft.sections[0].entries] == ([("Thing One", ["Detail | 2020 - 2022"])] if "Thing" in body else [])
        assert reading.sections == (() if words.casefold() == section else ((1, words, section, how),)), written
        # Only a heading nobody knows is left out (its own words are not kept), and that is said too.
        assert reading.left_out == ((("unknown_section", 1),) if how == "default" else ())
        assert reading.lines == reading.section_lines + sum(reading.spans.values()) + len(reading.left_out)


def test_sections_in_capitals_are_found_without_markdown_and_a_title_is_not_a_section() -> None:
    text = (
        "PROFILE\n\nBackend engineer with nine years of building payment services in Go.\n\n"
        "WORK EXPERIENCE\n\nAcme\nProject Lead, 2020 - 2022\n- Built the billing service.\n\n"
        "AGENTIC AI PROJECTS:\n\nDriftwatch\n- Finds drift in 4 minutes.\n\n"
        "TOOLING AND STACK\n\nGo, Python\n"
    )
    reading = _draft(text)
    assert [section.name for section in reading.draft.sections] == ["summary", "experience", "projects", "skills"]
    # 'Project Lead' is a role line, not the Projects section: only a line written as a heading is read by a word of it.
    assert [(entry.heading, entry.sublines) for entry in reading.draft.sections[1].entries] == [("Acme", ["Project Lead, 2020 - 2022"])]
    assert reading.sections == (
        (1, "PROFILE", "summary", "name"), (5, "WORK EXPERIENCE", "experience", "name"),
        (11, "AGENTIC AI PROJECTS", "projects", "closest"), (16, "TOOLING AND STACK", "skills", "closest"),
    )
    assert reading.left_out == ()


def test_two_sections_that_are_one_section_are_merged_in_the_files_order() -> None:
    reading = _draft(
        "## Selected Projects\n### Driftwatch\n- Finds drift.\n\n## Experience\n### Acme\nEngineer | 2020 - 2022\n- Built billing.\n\n"
        "## Open Source\n### Kubelint\n- Lints manifests.\n\n## Awards\n- Award one.\n\n## Certifications\n- Certificate one.\n"
    )
    assert [(section.name, [entry.heading for entry in section.entries], [item.text for item in section.items]) for section in reading.draft.sections] == [
        ("projects", ["Driftwatch", "Kubelint"], []), ("experience", ["Acme"], []), ("other", [], ["Award one.", "Certificate one."]),
    ]


def test_a_bold_title_with_a_tagline_and_a_second_line_is_an_entry() -> None:
    def projects(body: str) -> list[tuple[str, list[str], list[str]]]:
        reading = _draft("## Projects\n" + body)
        return [(entry.heading, entry.sublines, [bullet.text for bullet in entry.bullets]) for entry in reading.draft.sections[0].entries]

    wanted = [("Driftwatch — a drift detector", ["Go and PostgreSQL | 2023 - Present"], ["Finds drift.", "Opens a pull request."])]
    # The ticket's shape, and with blank lines between its parts.
    assert projects("**Driftwatch** — a drift detector\nGo and PostgreSQL | 2023 - Present\n- Finds drift.\n- Opens a pull request.\n") == wanted
    assert projects("**Driftwatch** — a drift detector\n\nGo and PostgreSQL | 2023 - Present\n\n- Finds drift.\n- Opens a pull request.\n") == wanted
    assert projects("__Driftwatch__ — a drift detector\n*Go and PostgreSQL* | 2023 - Present\n* Finds drift.\n• Opens a pull request.\n") == wanted
    # Two of them, a rule between: two entries.
    two = projects("**Driftwatch** — a drift detector\nGo\n- Finds drift.\n\n---\n\n**Ledgerlens**: explains anomalies\nPython\n- Explains them.\n")
    assert two == [("Driftwatch — a drift detector", ["Go"], ["Finds drift."]), ("Ledgerlens: explains anomalies", ["Python"], ["Explains them."])]
    # A title with nothing but bullets, and one with no bullets at all.
    assert projects("**Driftwatch**\n- Finds drift.\n\n**Ledgerlens** (2022)\n") == [("Driftwatch", [], ["Finds drift."]), ("Ledgerlens", ["2022"], [])]


def test_text_under_an_entry_is_kept_as_role_lines_and_then_as_a_line_never_refused() -> None:
    # A description longer than a heading holds (it was refused at its fourth line): the rest is a line of the entry.
    reading = _draft(
        "## Experience\n### Acme\nStaff Engineer | 2021 - Present\nPayments group.\nReports to the CTO.\nScope: 40 services.\nAnd 12 teams.\n- Runs the platform.\n"
    )
    (entry,) = reading.draft.sections[0].entries
    assert (entry.sublines, [bullet.text for bullet in entry.bullets]) == (
        ["Staff Engineer | 2021 - Present", "Payments group.", "Reports to the CTO."], ["Scope: 40 services. And 12 teams.", "Runs the platform."],
    )
    assert reading.read_as == (("entry_line", 6),) and reading.left_out == ()
    # A wrapped description is ONE role line, not one per line of the file.
    wrapped = _experience("### Acme\nStaff Engineer | 2021 - Present\nThe payments group of 14 engineers that owns the ledger and the\nsettlement tooling for three regions.\n- Runs the platform.\n")
    assert wrapped == [("Acme", ["Staff Engineer | 2021 - Present", "The payments group of 14 engineers that owns the ledger and the settlement tooling for three regions."], ["Runs the platform."])]
    three = _experience("### Acme\nStaff Engineer | 2021 - Present\nThe payments group of 14 engineers that owns the ledger, the\nsettlement tooling for three regions and the on-call rotation,\nwith 40 services in all.\n- Runs the platform.\n")
    assert three == [("Acme", ["Staff Engineer | 2021 - Present", "The payments group of 14 engineers that owns the ledger, the settlement tooling for three regions and the on-call rotation, with 40 services in all."], ["Runs the platform."])]
    # Text after an entry's bullets (it was refused): a line of that entry, said by line number.
    after = _draft("## Experience\n### Acme\nEngineer | 2020 - 2022\n- Built billing.\n\nStack: Go, PostgreSQL.\n\n### Borealis\n- Wrote reports.\n")
    assert [[bullet.text for bullet in entry.bullets] for entry in after.draft.sections[0].entries] == [["Built billing.", "Stack: Go, PostgreSQL."], ["Wrote reports."]]
    assert after.read_as == (("entry_line", 6),) and after.left_out == ()
    # Text above the first entry of its section has no entry to belong to: a line of Other, said.
    above = _draft("## Projects\n\nThings built outside of work,\nmostly in Go.\n\n### Driftwatch\n- Finds drift.\n")
    assert [(section.name, [item.text for item in section.items]) for section in above.draft.sections] == [("projects", []), ("other", ["Things built outside of work, mostly in Go."])]
    assert above.read_as == (("kept_in_other", 3),) and above.left_out == ()
    assert above.lines == above.section_lines + sum(above.spans.values()) == 5


def test_role_lines_without_a_heading_mark() -> None:
    role = ("Acme", ["Staff Engineer | 2021 - Present"], ["Runs the platform."])
    # A bold employer with an italic or a plain title; a plain employer and title (no mark at all).
    assert _experience("**Acme**\n*Staff Engineer* | 2021 - Present\n- Runs the platform.\n") == [role]
    assert _experience("**Acme**\nStaff Engineer | 2021 - Present\n- Runs the platform.\n") == [role]
    assert _experience("Acme\nStaff Engineer | 2021 - Present\n- Runs the platform.\n") == [role]
    # A second dated title after the bullets, with bullets of its own: the next role at the same employer.
    second = ("Acme", ["Engineer | 2018 - 2021"], ["Built billing."])
    assert _experience("**Acme**\n*Staff Engineer* | 2021 - Present\n- Runs the platform.\n\n*Engineer* | 2018 - 2021\n- Built billing.\n") == [role, second]
    assert _experience("### Acme\nStaff Engineer | 2021 - Present\n- Runs the platform.\n\nEngineer | 2018 - 2021\n- Built billing.\n") == [role, second]
    # When the heading above carries its own dates, a dated line after its bullets is an entry of its own.
    own = _experience("**Staff Engineer, Acme** (2021 - Present)\n- Runs the platform.\n\nEngineer, Borealis (2016 - 2021)\n- Built billing.\n")
    assert own == [("Staff Engineer, Acme", ["2021 - Present"], ["Runs the platform."]), ("Engineer, Borealis", ["2016 - 2021"], ["Built billing."])]
    # A title, a company and the dates on one bold line.
    assert _experience("**Staff Engineer** — Acme | Mar 2021 - Present\n- Runs the platform.\n") == [("Staff Engineer — Acme", ["Mar 2021 - Present"], ["Runs the platform."])]
    assert _experience("**Staff Engineer**, Acme — *Mar 2021 – Present*\n- Runs the platform.\n") == [("Staff Engineer, Acme", ["Mar 2021 – Present"], ["Runs the platform."])]


@pytest.mark.parametrize("mark", ["-", "*", "•", "+", "▪", "◦", "‣", "●", "–", "—", "·", "1.", "2)", "➤"])
def test_bullets_with_any_mark_and_wrapped_over_lines(mark: str) -> None:
    body = f"### Acme\nEngineer | 2020 - 2022\n{mark} Built the billing service that\n  handles 9 million entries a day.\n{mark} Ran the on-call rotation.\n"
    assert _experience(body) == [("Acme", ["Engineer | 2020 - 2022"], ["Built the billing service that handles 9 million entries a day.", "Ran the on-call rotation."])]
    reading = _draft(f"## Skills\n{mark} Go, Python,\n  SQL\n{mark} Kubernetes\n\n## Other\n{mark} Certified Kubernetes Administrator (2021)\n")
    assert [[item.text for item in section.items] for section in reading.draft.sections] == [["Go, Python, SQL", "Kubernetes"], ["Certified Kubernetes Administrator (2021)"]]


def test_skills_lines_lose_their_marks_and_a_label_above_its_skills_is_their_label() -> None:
    def skills(body: str) -> list[tuple[str, tuple[str, ...]]]:
        reading = _draft("## Technical Skills\n" + body)
        assert reading.left_out == () and reading.lines == reading.section_lines + sum(reading.spans.values())
        return [skill_names(item.text) for item in reading.draft.sections[0].items]

    wanted = [("Languages", ("Go", "Python")), ("Cloud", ("Kubernetes", "AWS"))]
    assert skills("- **Languages:** Go, Python\n- **Cloud**: Kubernetes, AWS\n") == wanted
    assert skills("**Languages** - Go, Python\n**Cloud** — Kubernetes, AWS\n") == wanted
    assert skills("* __Languages:__ Go, Python\n* *Cloud:* Kubernetes, AWS\n") == wanted
    # The label on a line of its own: a deeper heading or a bold line, its skills on the next line or as a list.
    assert skills("### Languages\nGo, Python\n\n### Cloud\n- Kubernetes\n- AWS\n") == wanted
    assert skills("**Languages**\nGo,\nPython\n\n**Cloud**\n\n* Kubernetes\n* AWS\n") == wanted
    # A bold skill is a skill, not a label; a label with nothing under it stays a line.
    assert skills("- **Go**, Python, **Kubernetes**\n") == [("", ("Go", "Python", "Kubernetes"))]
    assert skills("### Languages\n\n### Cloud\nKubernetes\n") == [("", ("Languages",)), ("Cloud", ("Kubernetes",))]


def test_schools_and_projects_as_a_list_are_entries_and_a_list_with_no_employer_is_kept_in_other() -> None:
    reading = _draft(
        "## Education\n- **B.S. Computer Science**, Example Tech\n  (2010 - 2014)\n- Certificate in Statistics, Evening College, 2016\n\n"
        "## Projects\n* Driftwatch: a drift detector for Terraform state\n* Kubelint: a linter for manifests (2022)\n\n"
        "## Experience\n- Chaired the review of 12 teams.\n- Mentors 4 engineers a year\n  through promotion.\n"
    )
    assert [(section.name, [(entry.heading, entry.sublines) for entry in section.entries], [item.text for item in section.items]) for section in reading.draft.sections] == [
        ("education", [("B.S. Computer Science, Example Tech", ["2010 - 2014"]), ("Certificate in Statistics, Evening College", ["2016"])], []),
        ("projects", [("Driftwatch: a drift detector for Terraform state", []), ("Kubelint: a linter for manifests", ["2022"])], []),
        ("other", [], ["Chaired the review of 12 teams.", "Mentors 4 engineers a year through promotion."]),
    ]
    assert reading.read_as == (("listed_entry", 2), ("listed_entry", 4), ("listed_entry", 7), ("listed_entry", 8), ("kept_in_other", 11), ("kept_in_other", 12))
    # The Experience heading holds nothing of its own any more: left out, by line number and reason.
    assert reading.left_out == (("section_in_other", 10),)
    assert reading.lines == reading.section_lines + sum(reading.spans.values()) + 1 == 11
    # A list item wrapped over three lines is one entry.
    long = _draft("## Education\n- B.S. Computer Science with a minor in\n  statistics, Example Tech,\n  2010 - 2014\n- Evening College, 2016\n")
    assert [(entry.heading, entry.sublines) for entry in long.draft.sections[0].entries] == [
        ("B.S. Computer Science with a minor in statistics, Example Tech", ["2010 - 2014"]), ("Evening College", ["2016"]),
    ]
    assert long.left_out == () and long.lines == long.section_lines + sum(long.spans.values()) == 5
    # A list whose items have a list of their own: the outer items are the roles, also in Experience.
    nested = _experience("- **Staff Engineer**, Acme (2021 - Present)\n  - Runs the platform.\n  - Cut deploy time.\n- Engineer, Borealis (2016 - 2021)\n  - Built billing.\n")
    assert nested == [
        ("Staff Engineer, Acme", ["2021 - Present"], ["Runs the platform.", "Cut deploy time."]), ("Engineer, Borealis", ["2016 - 2021"], ["Built billing."]),
    ]


@pytest.mark.parametrize(
    ("dates", "first", "last", "ongoing"),
    [
        ("Mar 2020 - Present", 2020, None, True), ("March 2020 – Present", 2020, None, True), ("Sept. 2019 — Jan. 2023", 2019, 2023, False),
        ("06/2015 - 02/2020", 2015, 2020, False), ("06.2015 – 02.2020", 2015, 2020, False), ("2015-06 – 2020-02", 2015, 2020, False),
        ("2015/06 to 2020/02", 2015, 2020, False), ("Jun 2015 through Dec 2017", 2015, 2017, False), ("2010-2012", 2010, 2012, False),
        ("Summer 2009", 2009, 2009, False), ("Q3 2020 - Q1 2022", 2020, 2022, False), ("2021 - current", 2021, None, True), ("2016", 2016, 2016, False),
    ],
)
def test_dates_in_the_forms_resumes_use(dates: str, first: int, last: int | None, ongoing: bool) -> None:
    # As a role line, at the end of a heading (they move to a role line, as written), and in brackets.
    for body, heading, sublines in (
        (f"### Acme\nEngineer | {dates}\n- Built it.\n", "Acme", (f"Engineer | {dates}",)),
        (f"### Engineer, Acme, {dates}\n- Built it.\n", "Engineer, Acme", (dates,)),
        (f"**Engineer, Acme** ({dates})\n- Built it.\n", "Engineer, Acme", (dates,)),
    ):
        clean = "## Experience\n" + body
        draft = mm.read_resume(clean)
        assign_ids(draft, None)
        (entry,) = build_master(draft).entries.values()
        assert (entry.heading, entry.sublines, entry.start, entry.end, entry.ongoing) == (heading, sublines, first, last, ongoing), body


def test_a_file_in_gigais_own_format_is_held_to_it_and_any_other_file_is_a_resume() -> None:
    marked = "<!-- gigai-master:1 -->\n\n## Experience\n\n### Advent of Code 2022 <!-- id:r-aoc -->\n- Solved 50 puzzles. <!-- id:b-aoc -->\n"
    assert mm.is_master_file(marked) and mm.is_master_file("\n\n  <!-- gigai-master:2 a note -->\n## Summary\n") and not mm.is_master_file("# Resume\n<!-- gigai-master:1 -->\n")
    draft, reading = mm.read_file(marked)
    # Read strictly: nothing to say about how, the heading stays as it is written, and what breaks the format is refused.
    assert reading is None and [(entry.heading, entry.sublines, entry.id) for entry in draft.all_entries()] == [("Advent of Code 2022", [], "r-aoc")]
    for broken in (marked + "\n## Hobbies\n- Zymurgy.\n", marked + "\nQuixotic trailing paragraph.\n", marked.replace("### Advent", "---\n### Advent").replace("- Solved", "---\n- Solved")):
        with pytest.raises(MasterResumeError):
            mm.read_file(broken)
    # The same lines without the marker are a resume: read, and the ids a line carries are kept.
    plain = marked.split("\n", 2)[2]
    draft, reading = mm.read_file(plain + "\n## Hobbies\n- Zymurgy.\n")
    assert reading is not None and reading.sections == ((6, "Hobbies", "other", "default"),)
    # An entry that carries an id is a master's own: its heading is not taken apart; one without an id is.
    assert [(entry.heading, entry.sublines, entry.id) for entry in draft.all_entries()] == [("Advent of Code 2022", [], "r-aoc")]
    assert [item.id for item in draft.all_items()] == ["b-aoc", None]
    draft, _reading = mm.read_file("## Projects\n### Advent of Code 2022\n- Solved 50 puzzles.\n")
    assert [(entry.heading, entry.sublines) for entry in draft.all_entries()] == [("Advent of Code", ["2022"])]
    # What GigAI stores reads the same through the resume reader as through the strict one.
    stored = (FIXTURES / "master.md").read_text(encoding="utf-8")
    unmarked = stored.split("\n", 1)[1]
    lenient, strict = mm.read_resume(unmarked), draft_master(stored)
    assert build_master(lenient).to_json() == build_master(strict).to_json()


def test_contact_data_is_removed_as_before_and_what_stood_under_a_removed_heading_is_kept() -> None:
    """The privacy strip is the import's own and takes a whole line: also a heading that holds a link (a known limit, said in the count)."""

    text = (
        "Jordan Example\njordan.example@example.test | (555) 010-0142\n\n"
        "## Projects\n\n### Kubelint\n- Lints manifests.\n\n"
        "### [Driftwatch](https://github.com/example-org/driftwatch)\n- Finds drift in 4 minutes.\n\n"
        "## Education\n\n### [Example Tech](https://example.test/cs)\n- B.S. Computer Science, 2014\n"
    )
    master, lines = _read(text)
    (resume,) = lines["resumes"]  # type: ignore[misc]
    # The name, the contact line and the two headings with a link: not imported, each counted by line number.
    assert {row["reason"]: row["lines"] for row in resume["left_out"]} == {"contact": [1, 2, 9, 14]}
    stored = master.markdown(ids=False)
    assert not [value for value in (*CONTACT, "github.com", "https://", "Driftwatch", "Example Tech") if value in stored]
    # Nothing under them is lost: a bullet after another entry is a line of that entry (as it always was), and a
    # list with no entry above it is an entry of its own, which is said.
    assert _entries(master) == [
        ("projects", "Kubelint", (), None, None, False, 2), ("education", "B.S. Computer Science", ("2014",), 2014, 2014, False, 0),
    ]
    assert [item.text for item in master.items.values()] == ["Lints manifests.", "Finds drift in 4 minutes."]
    assert {row["how"]: row["lines"] for row in resume["read_as"]} == {"listed_entry": [15]}
    assert lines["in"] == lines["kept"] + lines["left_out"] == 10  # type: ignore[operator]


# --- the real CLI on a scratch home --------------------------------------------------------------


def _home(tmp_path: Path, name: str = "home") -> Path:
    return setup_home(tmp_path / name, workpad_root=tmp_path / f"{name}-workpads")


def _cli(home: Path, *args: str, ok: bool = True) -> dict:
    result = CliRunner().invoke(cli, ["scout", "resume", "master", *args, "--home", str(home), "--json"])
    assert result.exit_code == (0 if ok else 1), result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _text(home: Path, *args: str, ok: bool = True) -> str:
    result = CliRunner().invoke(cli, ["scout", "resume", "master", *args, "--home", str(home)])
    assert result.exit_code == (0 if ok else 1), result.output
    return result.output


def _with_profile(home: Path, source: Path) -> None:
    """A profile that holds ``source`` as its resume, as ``gigai scout resume add`` stores it."""

    assert CliRunner().invoke(cli, ["scout", "resume", "add", str(source), "--home", str(home), "--json"]).exit_code == 0
    (home_scout_target(home) / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))


def _shown(home: Path) -> tuple[list[tuple[object, ...]], list[tuple[str, str, str]]]:
    master = _cli(home, "show")["master"]
    assert contact_values_in({"entries": master["entries"], "items": master["items"]}, CONTACT) == []
    return (
        [(entry["section"], entry["heading"], tuple(entry["sublines"]), entry["start"], entry["end"], entry["ongoing"], len(entry["bullets"])) for entry in master["entries"]],
        [(item["id"], item["section"], item["text"]) for item in master["items"]],
    )


def _snapshot(root: Path) -> dict[str, str]:
    """Every file, folder and link below ``root``: its bytes (their digest), or what it is."""

    return {
        str(path.relative_to(root)): "link" if path.is_symlink() else "folder" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
    }


def _settled(home: Path, root: Path) -> dict[str, str]:
    """``root`` as it is once a read-only command has run after the last write.

    The first read of the journal after a write (``master show`` here; any command that reads) brings the
    workpad's own read caches up to date (files in its ``scratch`` folder: ``journal-publishers.sqlite``,
    ``workpad-layout-check.json``) and touches nothing else: asserted. After it, a command that writes
    nothing leaves every byte below ``root`` as it is."""

    before = _snapshot(root)
    CliRunner().invoke(cli, ["scout", "resume", "master", "show", "--home", str(home), "--json"])
    after = _snapshot(root)
    assert [key for key in set(before) | set(after) if before.get(key) != after.get(key) and "/scratch/" not in key] == []
    return after


def _stored_as_read(home: Path, shape: Shape) -> None:
    """The master ``home`` shows is the one the shape's table describes, line for line and id for id; no contact value is in the home."""

    entries, items = _shown(home)
    assert entries == list(shape.entries)
    assert [text for _id, section, text in items if section == "summary"] == list(shape.summary)
    assert [skill_names(text) for _id, section, text in items if section == "skills"] == list(shape.skills)
    assert [text for _id, section, text in items if section == "other"] == list(shape.other)
    # The same resume is the same master by either road (the profiles' resumes, or the file): the same lines under the same ids.
    master, _lines = _read(shape.text)
    assert items == [(item.id, item.section, item.text) for item in master.items.values()]
    # No contact value in any file of the home (the Scout folder and the resumes folder are inside it).
    planted = ("example.test", "010-0142", "jordan-example")
    assert [str(path) for path in home.rglob("*") if path.is_file() and not path.is_symlink() and any(value.encode() in path.read_bytes() for value in planted)] == []


@SHAPES_BY_NAME
def test_master_init_merges_a_profiles_real_resume(shape: Shape, tmp_path: Path) -> None:
    """The ticket's first refusal, through the real CLI: ``migration_resume_unreadable`` at a rule between two roles."""

    home = _home(tmp_path)
    _with_profile(home, shape.path)
    dry = _cli(home, "init", "--dry-run")
    assert (dry["status"], dry["written"]) == ("dry_run", False)
    merged = _cli(home, "init")
    assert (merged["status"], merged["written"], merged["migration"]["source_lines"]) == ("created", True, dry["migration"]["source_lines"])
    lines = merged["migration"]["source_lines"]
    assert lines["in"] == lines["kept"] + lines["folded"] + lines["left_out"] and lines["left_out_by_reason"]["unread"] == 0
    # `resume add` took the name and the contact lines before the merge saw the resume, so its line numbers are those
    # of the stored resume; the headings read as another section are the same ones, in the same order.
    (resume,) = lines["resumes"]
    assert [(row["heading"], row["section"], row["how"]) for row in resume["sections"]] == [found[1:] for found in shape.headings]
    assert {row["how"]: len(row["lines"]) for row in resume["read_as"]} == {how: len(numbers) for how, numbers in shape.read_as.items()}
    _stored_as_read(home, shape)


@SHAPES_BY_NAME
def test_master_init_from_stores_a_real_resume(shape: Shape, tmp_path: Path) -> None:
    """The ticket's second refusal, through the real CLI: ``master_markdown_invalid`` "unknown section", and no form for a bold project title."""

    home = _home(tmp_path)
    stored = _cli(home, "init", "--from", str(shape.path))
    assert (stored["status"], stored["written"], stored["would"], stored["master"]["revision"]) == ("created", True, None, 1)
    (read,) = stored["source_lines"]["resumes"]
    assert {row["reason"]: row["lines"] for row in read["left_out"]} == shape.left_out
    assert [(row["line"], row["heading"], row["section"], row["how"]) for row in read["sections"]] == list(shape.headings)
    assert {row["how"]: row["lines"] for row in read["read_as"]} == shape.read_as
    assert {row["line"] for row in stored["contact_removed"]["lines"]} == set(shape.left_out["contact"])
    lines = stored["source_lines"]
    assert lines["in"] == lines["kept"] + lines["left_out"] and (lines["folded"], lines["left_out_by_reason"]["unread"]) == (0, 0)
    _stored_as_read(home, shape)


def test_the_text_says_what_was_mapped_and_left_out_and_prints_no_resume_line(tmp_path: Path) -> None:
    shape = SHAPE_LIST[1]  # section-names
    merged_home, file_home = _home(tmp_path, "merged"), _home(tmp_path, "file")
    _with_profile(merged_home, shape.path)
    merged = _text(merged_home, "init", "--dry-run")
    assert "Nothing was written" in merged
    assert (
        '  Section headings of the resume of default: line 1 "About" as Summary; line 5 "Professional Experience" as Experience; '
        'line 11 "Research Experience" as Experience (the closest section); line 17 "Selected Projects" as Projects; line 22 "Open Source" as Projects; '
        'line 27 "Technical Skills" as Skills; line 32 "Tools I Use Daily" as Skills (the closest section); line 36 "Education & Training" as Education; '
        'line 41 "Publications" as Other; line 45 "Awards" as Other; line 49 "Certifications" as Other; '
        'line 53 "Leadership & Mentoring" as Other (not a section GigAI has).'
    ) in merged
    assert "Left out of the resume of default: line 53: a section heading this reader does not know: its lines are in Other, its own words are not kept." in merged

    said = _text(file_home, "init", "--from", str(shape.path))
    assert "Master resume stored as revision 1: 12 lines, 5 entries, 9 skills; 17 new ids, 17 added, 0 removed, 0 changed." in said
    assert "Of 34 lines of section-names.md (headings, role lines and wrapped lines counted): 31 kept, 3 left out (2 contact, 1 unknown section)." in said
    assert "  Left out of section-names.md: line 56: a section heading this reader does not know: its lines are in Other, its own words are not kept." in said
    assert '  Section headings of section-names.md: line 4 "About" as Summary; line 8 "Professional Experience" as Experience; ' in said
    assert 'line 56 "Leadership & Mentoring" as Other (not a section GigAI has).' in said
    assert "Not imported: line 1: name, line 2: email, line 2: phone." in said
    # The lines kept where they had no place of their own, by line number.
    lists = _text(_home(tmp_path, "lists"), "init", "--from", str(SHAPE_LIST[2].path))
    assert "  In plain-roles-and-lists.md: line 24: plain text under an entry that is not a role line: kept as a line of that entry." in lists
    assert "  In plain-roles-and-lists.md: line 44, 46: a list item with no entry above it: read as an entry of its own." in lists
    assert "  In plain-roles-and-lists.md: line 51, 52: in Experience, Projects or Education with no entry above it: kept as a line of Other." in lists
    assert "  Left out of plain-roles-and-lists.md: line 48: a section heading whose lines are all kept in Other: its own words are not kept." in lists
    # A heading's own words are said; no line of the resume is printed, and no contact value.
    for output in (merged, said, lists):
        assert "Platform engineer with" not in output and "Mentors 4" not in output and "Quillmark" not in output
        assert not [value for value in CONTACT if value in output]


def test_init_from_dry_run_says_what_a_write_would_store_and_leaves_the_home_byte_for_byte(tmp_path: Path) -> None:
    """The ticket's third refusal: ``--from FILE --dry-run`` was ``master_option_invalid``."""

    shape = SHAPE_LIST[0]
    home = _home(tmp_path)
    _with_profile(home, SHAPES / "gigai-format.md")  # Scout is installed and a profile holds a resume
    before = _settled(home, tmp_path)

    dry = _cli(home, "init", "--from", str(shape.path), "--dry-run")
    assert _snapshot(tmp_path) == before, "a dry run writes nothing: no revision, no reference, no file in the resumes folder"
    assert (dry["status"], dry["written"], dry["would"], dry["scout_installed"], dry["file"]) == ("dry_run", False, "created", False, None)
    assert (dry["master"]["revision"], dry["master"]["revision_id"], dry["master"]["updated_at"], dry["master"]["parent_revision"]) == (1, None, None, None)
    assert dry["profiles"] == {"synced": [], "offers": []}
    assert _cli(home, "show", ok=False)["error"]["code"] == "master_not_found"
    text = _text(home, "init", "--from", str(shape.path), "--dry-run")
    assert _snapshot(tmp_path) == before
    assert "Would store rules-and-capital-sections.md as revision 1 of the master resume: 11 lines, 5 entries, 10 skills; 16 new ids, 16 added, 0 removed, 0 changed. Nothing was written." in text
    assert "Of 27 lines of rules-and-capital-sections.md (headings, role lines and wrapped lines counted): 25 kept, 2 left out (2 contact)." in text
    assert 'line 29 "AGENTIC AI PROJECTS" as Projects (the closest section)' in text and "Not imported: line 1: name, line 3: email" in text
    assert f"To store it: `gigai scout resume master init --from {shape.path}`." in text and "Next:" not in text

    # The write stores exactly what the dry run said it would.
    written = _cli(home, "init", "--from", str(shape.path))
    same = ("ids_assigned", "ids_restored", "changes", "contact_removed", "source_lines")
    assert (written["status"], {key: written[key] for key in same}) == ("created", {key: dry[key] for key in same})
    assert (written["master"]["content_sha256"], written["master"]["counts"], written["master"]["record_id"]) == (
        dry["master"]["content_sha256"], dry["master"]["counts"], dry["master"]["record_id"],
    )
    assert written["master"]["revision_id"] and written["file"]["wrote"] == "master.md"

    # With a master stored, a dry run goes through the checks a write goes through, and still writes nothing.
    edited = tmp_path / "edited.md"
    edited.write_text(shape.text.replace("3,200 customer clusters", "3,300 customer clusters"), encoding="utf-8")
    before = _settled(home, tmp_path)
    assert _cli(home, "init", "--from", str(edited), "--dry-run", ok=False)["error"]["code"] == "master_exists"
    assert _cli(home, "init", "--from", str(edited), "--dry-run", "--revision", "4", ok=False)["error"]["code"] == "revision_conflict"
    again = _cli(home, "init", "--from", str(shape.path), "--dry-run")
    assert (again["status"], again["would"], again["master"]["revision"], again["master"]["revision_id"]) == ("dry_run", "unchanged", 1, written["master"]["revision_id"])
    would = _cli(home, "init", "--from", str(edited), "--dry-run", "--revision", "1")
    assert (would["would"], would["master"]["revision"], would["master"]["parent_revision"], would["changes"]) == (
        "revised", 2, written["master"]["revision_id"], {"added": 1, "removed": 1, "changed": 0},
    )
    said = _text(home, "init", "--from", str(edited), "--dry-run", "--revision", "1")
    assert "Would store edited.md as revision 2 of the master resume" in said and f"--from {edited} --revision 1`." in said
    # The folder's own master.md, changed by hand: a dry run of it does not write the file again.
    visible = home / "resumes" / "master.md"
    visible.write_text(visible.read_text(encoding="utf-8").replace("3,200 customer clusters", "3,400 customer clusters"), encoding="utf-8")
    before_visible = _snapshot(tmp_path)
    assert _cli(home, "init", "--from", str(visible), "--dry-run", "--revision", "1")["changes"] == {"added": 0, "removed": 0, "changed": 1}
    assert _snapshot(tmp_path) == before_visible
    visible.write_text(visible.read_text(encoding="utf-8").replace("3,400 customer clusters", "3,200 customer clusters"), encoding="utf-8")
    assert _snapshot(tmp_path) == before
    assert _cli(home, "history")["revision"] == 1
    # --answer still belongs to the merge of the profiles' resumes.
    assert _cli(home, "init", "--from", str(edited), "--answer", "mq-000000000000=a", ok=False)["error"]["code"] == "master_option_invalid"


def test_a_dry_run_does_not_print_a_profiles_resume_again_and_the_write_then_does(tmp_path: Path) -> None:
    """``after_master_write`` (a profile's resume printed again from the master) is a write: a dry run never runs it."""

    shape = SHAPE_LIST[0]
    home = _home(tmp_path)
    _with_profile(home, shape.path)
    assert _cli(home, "init")["status"] == "created"  # the profile's selection is its own resume
    edited = tmp_path / "edited.md"
    edited.write_text(shape.text.replace("3,200 customer clusters", "3,300 customer clusters"), encoding="utf-8")
    before = _settled(home, tmp_path)
    dry = _cli(home, "init", "--from", str(edited), "--dry-run", "--revision", "1")
    assert (dry["would"], dry["profiles"]) == ("revised", {"synced": [], "offers": []}) and _snapshot(tmp_path) == before
    written = _cli(home, "init", "--from", str(edited), "--revision", "1")
    assert written["status"] == "revised" and len(written["profiles"]["synced"]) == 1, "the write prints the profile's resume again"
    assert _snapshot(tmp_path) != before


def test_a_dry_run_on_a_home_that_never_ran_scout_stores_nothing(tmp_path: Path) -> None:
    home = _home(tmp_path)
    dry = _cli(home, "init", "--from", str(SHAPE_LIST[0].path), "--dry-run")
    assert (dry["status"], dry["would"], dry["scout_installed"], dry["master"]["revision"], dry["master"]["record_id"]) == ("dry_run", "created", False, 1, None)
    # The Scout folder is there, as after any `gigai scout` command (the shared target resolution makes it); the
    # dry run added nothing of its own: no starter config, no resumes folder, no master.
    after = _snapshot(tmp_path)
    assert not (home_scout_target(home) / "find-jobs.json").exists() and not (home / "resumes").exists()
    assert _cli(home, "show", ok=False)["error"]["code"] == "master_not_found"
    assert _snapshot(tmp_path) == after, "what a read-only command leaves is what the dry run left"
    assert not [path for path in tmp_path.rglob("*") if path.is_file() and b"Runs the control plane" in path.read_bytes()]


def test_a_refusal_names_the_line_the_rule_and_the_fix_and_the_terminal_shows_the_line(tmp_path: Path) -> None:
    home = _home(tmp_path)
    marked = "<!-- gigai-master:1 -->\n\n## Experience\n\n### Example Corp\nStaff Engineer | Jun 2019 - Present\n- Cut deploy time from 40 minutes to 6.\n"
    broken = tmp_path / "broken.md"
    broken.write_text(marked + "\n## Hobbies\n\n- Zymurgy at weekends.\n", encoding="utf-8")
    # A file that says it is in GigAI's format is held to it: the line number, the rule and the fix, never the text...
    result = CliRunner().invoke(cli, ["scout", "resume", "master", "init", "--from", str(broken), "--home", str(home), "--json"])
    error = json.loads(result.output.strip().splitlines()[-1])["error"]
    assert (result.exit_code, error["code"], error["line"]) == (1, "master_markdown_invalid", 9)
    assert error["message"] == "line 9: unknown section; use ## Summary, ## Experience, ## Skills, ## Education, ## Projects, ## Other"
    assert "Hobbies" not in result.output and "Zymurgy" not in result.output
    # ...and on the terminal, the line itself under the message. A dry run refuses the same way.
    for extra in ((), ("--dry-run",)):
        said = _text(home, "init", "--from", str(broken), *extra, ok=False)
        assert "Error: line 9: unknown section; use ## Summary" in said and "\n  Line 9 reads: ## Hobbies\n" in said and "Zymurgy" not in said
    assert _cli(home, "show", ok=False)["error"]["code"] == "master_not_found"
    # The same lines without the marker are a resume: nothing is refused, Hobbies is kept in Other and said.
    resume = tmp_path / "resume.md"
    resume.write_text(marked.split("\n", 2)[2] + "\n## Hobbies\n\n- Zymurgy at weekends.\n", encoding="utf-8")
    stored = _cli(home, "init", "--from", str(resume))
    (read,) = stored["source_lines"]["resumes"]
    assert stored["status"] == "created" and read["sections"] == [
        {"line": 7, "heading": "Hobbies", "section": "other", "how": "default", "why": mm.SECTION_HOW["default"]},
    ]
    assert [item["text"] for item in _cli(home, "show", "--section", "other")["master"]["items"]] == ["Zymurgy at weekends."]

    # The migration: a profile's resume that cannot be read names the line of the STORED resume; the terminal shows it.
    long_home = _home(tmp_path, "long")
    long_resume = tmp_path / "long.md"
    long_resume.write_text("## Experience\n\n### Example Corp\nStaff Engineer | Jun 2019 - Present\n- Quixotic " + "very " * 420 + "long line.\n", encoding="utf-8")
    _with_profile(long_home, long_resume)
    refused = CliRunner().invoke(cli, ["scout", "resume", "master", "init", "--dry-run", "--home", str(long_home), "--json"])
    error = json.loads(refused.output.strip().splitlines()[-1])["error"]
    assert (refused.exit_code, error["code"], error["line"]) == (1, "migration_resume_unreadable", 5)
    assert "line 5: a line has at most 2000 characters" in error["message"] and "Quixotic" not in refused.output
    said = _text(long_home, "init", "--dry-run", ok=False)
    assert "line 5: a line has at most 2000 characters" in said and "\n  Line 5 reads: - Quixotic very very" in said
    assert len(said.split("Line 5 reads: ")[1].splitlines()[0]) == 160, "the line is cut, not printed whole"


# --- master_store.import_master: the two new parameters change nothing for a caller that does not pass them ---


def _installed(tmp_path: Path, name: str) -> tuple[Path, Path]:
    from gigai.scout import scout_cli

    home = _home(tmp_path, name)
    target = scout_cli._resolved_target(None, home, as_json=True).expanduser().resolve(strict=True)  # as every `gigai scout` command
    scout_cli.install_scout(home_root=home, requested_target=target)
    return home, target


@pytest.mark.parametrize("source", [FIXTURES / "master.md", SHAPES / "gigai-format.md"], ids=["with-ids", "without-ids"])
def test_import_master_stores_the_same_with_and_without_the_new_parameters(source: Path, tmp_path: Path) -> None:
    def facts(written, home: Path) -> tuple[object, ...]:  # noqa: ANN001 - a MasterImport
        stored = load_master(home_root=home, target=home_scout_target(home))
        assert stored is not None and stored.master.to_json() == written.stored.master.to_json()
        return (
            written.status, written.dry_run, written.ids_assigned, written.ids_restored, written.change, written.contact_removed,
            written.stored.revision.revision, written.stored.revision.content_sha256, written.stored.revision.written_by,
            stored.master.markdown(), tuple(stored.master.entries), tuple(stored.master.items),
            (home / "resumes" / "master.md").read_bytes(), {key: value for key, value in written.file.items() if key != "path"},
        )

    # As every caller before 0.1.10.11 calls it (the migration, `master sync`, the routes): no new parameter.
    plain_home, plain_target = _installed(tmp_path, "plain")
    plain = import_master(home_root=plain_home, target=plain_target, source=source)
    # With the parameters at their defaults, and with the reader named that it always used.
    default_home, default_target = _installed(tmp_path, "default")
    default = import_master(home_root=default_home, target=default_target, source=source, reader=None, dry_run=False)
    named_home, named_target = _installed(tmp_path, "named")
    named = import_master(home_root=named_home, target=named_target, source=source, reader=draft_master, dry_run=False)
    assert facts(plain, plain_home) == facts(default, default_home) == facts(named, named_home)
    assert plain.status == "created" and plain.stored.revision.revision_id and plain.stored.revision.updated_at
    # On the home that was written without them: the same file again, with them, is the same content and writes nothing.
    before = _settled(plain_home, tmp_path)
    again = import_master(home_root=plain_home, target=plain_target, source=source, reader=draft_master, dry_run=False)
    assert (again.status, again.stored.revision.revision_id, again.dry_run) == ("unchanged", plain.stored.revision.revision_id, False)
    # A dry run of it too: every check, no write, and it says so.
    dry = import_master(home_root=plain_home, target=plain_target, source=source, dry_run=True)
    assert (dry.status, dry.dry_run, dry.file, dry.stored.revision.revision_id) == ("unchanged", True, None, plain.stored.revision.revision_id)
    assert _snapshot(tmp_path) == before
