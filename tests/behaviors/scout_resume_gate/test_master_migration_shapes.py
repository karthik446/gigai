"""0.1.10.9 MIGFIX: the migration reads the resume shapes people have, and counts every line it leaves out.

``gigai scout resume master init`` builds the master from the resumes the profiles hold. The END outcomes:

* a summary paragraph that stands above the first heading is the Summary line; the name, the contact
  lines, a title and a headline above it never are;
* a bold title line under an employer (``**Senior Software Engineer** | 2021 - present``) is the
  employer's role line, so the entry has its dates and the oldest-first rule can order it;
* every content line of a resume is in the master, folded into a line the master holds, or counted as
  left out by line number and reason (``migration.source_lines``): never silently gone.

Read on a set of shapes (``tests/evals/fixtures/master/shapes``, all invented: one person, fictional
``example.test`` / ``555-01xx`` contact values) and on the demo persona's resume
(``tools/media/persona.RESUME_MARKDOWN``). No model is called. No operator data is read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

from click.testing import CliRunner
import pytest

from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.scout import master_migration as mm
from gigai.scout.master_resume import Master, parse_master, skill_names
from gigai.scout.master_store import strip_contact
from gigai.scout.target_resolution import home_scout_target

from tests.support.contact_scan import contact_values_in
from tests.support.scout_profile_fixtures import default_find_jobs_config
from tests.support.setup_home import setup_home
from tools.media import persona

SHAPES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "master" / "shapes"
#: What a fixture's header holds: the invented person's name and contact values. None of it may reach a master.
CONTACT = ("Jordan", "example.test", "555", "linkedin", "Example State")
SUMMARY = "Platform engineer with 11 years of experience running Kubernetes control planes and the delivery tooling around them"


@dataclass(frozen=True)
class Shape:
    """One resume and what a master made of it holds."""

    name: str
    sections: tuple[str, ...]
    #: ``(section, heading, role lines, first year, last year (None: ongoing or none), ongoing, bullets)`` in the file's order.
    entries: tuple[tuple[str, str, tuple[str, ...], int | None, int | None, bool, int], ...]
    summary: tuple[str, ...] = ()
    #: ``(label, skills)`` per Skills line.
    skills: tuple[tuple[str, tuple[str, ...]], ...] = ()
    other: tuple[str, ...] = ()
    #: The lines left out: reason -> file lines.
    left_out: dict[str, list[int]] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return persona.RESUME_MARKDOWN if self.name == "persona" else (SHAPES / f"{self.name}.md").read_text(encoding="utf-8")


QUILLMARK = ("experience", "Quillmark Systems", ("Staff Platform Engineer | Mar 2020 - Present",), 2020, None, True)
SHAPE_LIST = (
    Shape(
        "gigai-format",
        ("summary", "experience", "skills", "education", "projects", "other"),
        (
            (*QUILLMARK, 3),
            ("experience", "Pendle Works", ("Senior Software Engineer | Jun 2015 - Feb 2020",), 2015, 2020, False, 2),
            ("education", "Northfield State University", ("B.S. Computer Science | 2010 - 2014",), 2010, 2014, False, 0),
            ("projects", "Driftwatch: a drift detector for Terraform state", (), None, None, False, 1),
        ),
        summary=(SUMMARY + ".",),
        skills=(("Languages", ("Go", "Python", "SQL")), ("Cloud and infrastructure", ("Kubernetes", "Terraform", "AWS", "GCP")), ("Data", ("PostgreSQL", "Kafka", "Redis"))),
        other=("Certified Kubernetes Administrator (2021)",),
    ),
    # The summary has no heading; the title is a bold line under the employer; a second bold title is the next role there.
    Shape(
        "bold-title",
        ("summary", "experience", "skills", "education"),
        (
            (*QUILLMARK, 2),
            ("experience", "Quillmark Systems", ("Senior Platform Engineer | Jan 2018 - Feb 2020",), 2018, 2020, False, 1),
            ("experience", "Pendle Works", ("Senior Software Engineer | Jun 2015 - Dec 2017",), 2015, 2017, False, 2),
            ("education", "Northfield State University", ("B.S. Computer Science | 2010 - 2014",), 2010, 2014, False, 0),
        ),
        summary=(SUMMARY + ", for teams of 6 to 40 engineers.",),
        skills=(("", ("Go", "Python", "SQL", "Kubernetes", "Terraform", "PostgreSQL", "Kafka")),),
        left_out={"contact": [1, 3, 4], "above_first_section": [6]},
    ),
    # No markdown at all: headings in capitals, entries and role lines as plain lines, a Skills block of 'Category: a, b' lines.
    Shape(
        "plain-headings",
        ("summary", "experience", "skills", "education", "other"),
        (
            ("experience", "Quillmark Systems", ("Staff Platform Engineer, March 2020 to Present",), 2020, None, True, 2),
            ("experience", "Pendle Works", ("Senior Software Engineer, 06/2015 - 02/2020",), 2015, 2020, False, 2),
            ("education", "Northfield State University", ("B.S. Computer Science, 2010 - 2014",), 2010, 2014, False, 0),
            ("education", "Pendle Evening College", ("Certificate in Data Engineering, 2016",), 2016, 2016, False, 0),
        ),
        summary=(SUMMARY + ".",),
        skills=(("Languages", ("Go", "Python", "SQL")), ("Cloud", ("Kubernetes", "Terraform", "AWS", "GCP")), ("Data", ("PostgreSQL", "Kafka", "Redis"))),
        other=("Certified Kubernetes Administrator (2021)", "HashiCorp Certified: Terraform Associate (2019)"),
        left_out={"contact": [1, 2]},
    ),
    # Bullets marked '*', '-' and '1.', and lines wrapped in the summary, the bullets and the skills.
    Shape(
        "bullets-and-wraps",
        ("summary", "experience", "skills"),
        (
            (*QUILLMARK, 3),
            ("experience", "Pendle Works", ("Senior Software Engineer | Jun 2015 - Feb 2020",), 2015, 2020, False, 3),
        ),
        summary=(SUMMARY + ", who likes small teams and short feedback loops.",),
        skills=(("", ("Go", "Python", "SQL")), ("", ("Kubernetes", "Terraform", "AWS", "GCP"))),
    ),
    Shape(
        "skills-categories",
        ("experience", "skills"),
        (("experience", "Quillmark Systems", ("Staff Platform Engineer | 2020 - Present",), 2020, None, True, 1),),
        skills=(
            ("Languages", ("Go", "Python", "SQL")),
            ("Cloud and infrastructure", ("Kubernetes", "Terraform", "AWS", "GCP", "Azure")),
            ("Data", ("PostgreSQL", "Kafka", "Redis")),
            ("Practices", ("incident response", "capacity planning")),
            ("Observability", ("Prometheus", "Grafana", "OpenTelemetry")),
        ),
    ),
    # Certifications, Awards and Volunteering are Other lines; a section nobody knows is too, and its heading is counted.
    Shape(
        "education-certifications",
        ("experience", "education", "other"),
        (
            ("experience", "Quillmark Systems", ("Staff Platform Engineer | 2020 - Present",), 2020, None, True, 1),
            ("education", "Northfield State University", ("M.S. Computer Science | 2014 - 2016",), 2014, 2016, False, 1),
            ("education", "Pendle Evening College", ("2010 - 2014",), 2010, 2014, False, 1),
        ),
        other=(
            "Certified Kubernetes Administrator (2021)", "AWS Certified Solutions Architect (2019)",
            "Quillmark engineering award for the 2022 migration", "Northfield hackathon winner (2015)",
            "Mentors 4 students a year at the Northfield coding club.", "Chaired the architecture review of 12 teams.",
        ),
        left_out={"contact": [1], "empty_section": [32], "unknown_section": [34]},
    ),
    Shape(
        "date-forms",
        ("experience",),
        (
            (*QUILLMARK, 1),
            ("experience", "Pendle Works", ("Senior Software Engineer | 06/2015 – 02/2020",), 2015, 2020, False, 1),
            ("experience", "Harrow Lane Labs", ("Software Engineer, September 2012 to May 2015",), 2012, 2015, False, 1),
            ("experience", "Tessel Yard", ("2010-2012",), 2010, 2012, False, 1),
            ("experience", "Orchard Row", ("Engineering Intern | Summer 2009",), 2009, 2009, False, 1),
            ("experience", "Fenwick Mill", ("Contract Engineer | 2021 - current",), 2021, None, True, 1),
            ("experience", "Bramble Court", ("Volunteer developer",), None, None, False, 1),  # the resume gives it no date
        ),
    ),
    # The demo persona's resume: the shape P5 found the bug on.
    Shape(
        "persona",
        ("summary", "experience", "skills", "education"),
        (
            ("experience", "Larkspur Freight", ("Senior Software Engineer | 2021 - present",), 2021, None, True, 3),
            ("experience", "Mossbank Analytics", ("Software Engineer | 2017 - 2021",), 2017, 2021, False, 2),
            ("education", "B.S. Computer Science", ("2016",), 2016, 2016, False, 0),
        ),
        summary=("Senior backend engineer with nine years building Python services, data pipelines and the platforms they run on.",),
        skills=(("", ("Python", "Postgres", "Kubernetes", "Kafka", "Terraform", "Docker", "GitHub Actions")),),
    ),
)
SHAPES_BY_NAME = pytest.mark.parametrize("shape", SHAPE_LIST, ids=[shape.name for shape in SHAPE_LIST])


def _stripped(text: str) -> tuple[str, tuple[int, ...]]:
    """``text`` as the migration hands it to the merge: the privacy strip first (``master_profiles._resume_sources``)."""

    clean, gone = strip_contact(text)
    return clean, tuple(sorted({line for _kind, line in gone.lines}))


def _master(shape: Shape) -> Master:
    return mm.plan_migration([mm.SourceResume("only", _stripped(shape.text)[0], ("default",))]).master


def _squash(line: str) -> str:
    """A line's letters and digits alone (no marks, no list number): what must be found in the master for the line to be in it."""

    return re.sub(r"[^a-z0-9]", "", re.sub(r"\A\s*\d{1,2}[.)]\s+", "", line).casefold())


@SHAPES_BY_NAME
def test_a_resume_shape_reads_as_sections_and_dated_entries(shape: Shape) -> None:
    master = _master(shape)
    assert master.sections == shape.sections
    assert [item.text for item in master.in_section("summary")] == list(shape.summary)
    assert [
        (entry.section, entry.heading, entry.sublines, entry.start, entry.end, entry.ongoing, len(entry.bullets)) for entry in master.entries.values()
    ] == list(shape.entries)
    assert [skill_names(item.text) for item in master.in_section("skills")] == list(shape.skills)
    assert [item.text for item in master.in_section("other")] == list(shape.other)
    # Every entry the resume dates has its dates, so the oldest-first rule can order the roles.
    dated = [entry for entry in master.entries.values() if entry.section == "experience" and entry.heading != "Bramble Court"]
    assert dated and all(entry.start is not None for entry in dated)
    # What is stored is a master: it reads back line for line.
    assert parse_master(master.markdown()).to_json() == master.to_json()
    # No contact data, and no wrapped line broken in two.
    text = master.markdown(ids=False)
    assert not [value for value in CONTACT if value in text]
    assert all(not item.text.endswith(",") for item in master.items.values())


@SHAPES_BY_NAME
def test_every_line_of_a_resume_is_kept_or_counted(shape: Shape) -> None:
    clean, contact_lines = _stripped(shape.text)
    plan = mm.plan_migration([mm.SourceResume("only", clean, ("default",), contact_lines)])
    lines = plan.to_json()["source_lines"]
    (resume,) = lines["resumes"]
    assert resume["profiles"] == ["default"]
    left = {row["reason"]: row["lines"] for row in resume["left_out"]}
    assert left == shape.left_out
    assert all(row["why"] == mm.LEFT_OUT_REASONS[row["reason"]] for row in resume["left_out"])
    assert lines["left_out_by_reason"] == {reason: len(shape.left_out.get(reason, [])) for reason in mm.LEFT_OUT_REASONS}
    # The count is closed: every content line of the file is kept, folded or left out; one resume folds nothing.
    content = [number for number, line in enumerate(shape.text.splitlines(), 1) if line.strip()]
    assert lines["in"] == resume["in"] == len(content)
    assert (lines["folded"], lines["in"]) == (0, lines["kept"] + lines["left_out"])
    assert lines["left_out"] == sum(len(numbers) for numbers in left.values())
    # Counted here a second way, from the text: a line that is not named as left out is in the master word for word
    # (marks and wraps aside), or is the heading of one of its sections.
    stored = _squash(plan.master.markdown(ids=False))
    named = {number for numbers in left.values() for number in numbers}
    lost = [
        number for number in content
        if number not in named
        and _squash(shape.text.splitlines()[number - 1]) not in stored
        and mm._section_of(shape.text.splitlines()[number - 1].strip().lstrip("#").strip()) not in plan.master.sections
    ]
    assert lost == []
    # And a contact line is not in the master.
    assert [number for number in shape.left_out.get("contact", []) if _squash(shape.text.splitlines()[number - 1]) in stored] == []


def test_a_summary_above_the_first_heading_is_kept_and_a_name_a_title_and_contact_data_never_are() -> None:
    body = "\n## Experience\n\n### Acme\nEngineer | 2020 - 2022\n- Built the billing service.\n"
    sentence = "Backend engineer with nine years of building payment services in Go."

    def summary(above: str) -> tuple[list[str], dict[str, list[int]]]:
        reading = mm.read_resume_lines(above + body)
        left: dict[str, list[int]] = {}
        for reason, line in reading.left_out:
            left.setdefault(reason, []).append(line)
        return [item.text for section in reading.draft.sections if section.name == "summary" for item in section.items], left

    assert summary(f"{sentence}\n") == ([sentence], {})
    # Wrapped over two lines it is one line; two paragraphs are two lines.
    assert summary("Backend engineer with nine years of building\npayment services in Go.\n") == ([sentence], {})
    assert summary(f"{sentence}\n\nLed the migration of 40 services to Kubernetes in 2022.\n")[0] == [sentence, "Led the migration of 40 services to Kubernetes in 2022."]
    # A title heading, a name, a headline and a line of fields are never the summary: each is counted.
    assert summary(f"# Resume\n\nSenior Backend Engineer\n\n{sentence}\n") == ([sentence], {"above_first_section": [1, 3]})
    assert summary("Senior Backend Engineer | Payments | Distributed systems | Go and Python | Remote\n") == ([], {"above_first_section": [1]})
    assert summary("Pat Q. Sample\n") == ([], {"contact": [1]})
    # A line that looks like contact data is never kept, although nothing stripped it first.
    held = summary(f"Pat Q. Sample\npat@example.test | (555) 010-0142\n\n{sentence} Reach me at pat@example.test.\n")
    assert held == ([], {"contact": [1, 2, 4]})
    # A resume that also has a Summary section keeps both, the paragraph first.
    both = mm.read_resume(f"{sentence}\n\n## Summary\n\n- Staff engineer who builds agent platforms.\n" + body)
    assert [item.text for item in both.sections[0].items] == [sentence, "Staff engineer who builds agent platforms."]
    # Without any section there is nothing to hang a summary on: refused as before, never by its text.
    with pytest.raises(mm.MasterResumeError) as refused:
        mm.read_resume(f"{sentence}\n")
    assert "no resume sections" in str(refused.value) and "Backend" not in str(refused.value)


def test_a_title_line_under_an_employer_is_its_role_line() -> None:
    def entries(experience: str) -> list[tuple[str, list[str], int]]:
        draft = mm.read_resume("## Experience\n" + experience)
        return [(entry.heading, entry.sublines, len(entry.bullets)) for entry in draft.sections[0].entries]

    role = ("Acme", ["Staff Engineer | 2021 - Present"], 1)
    assert entries("### Acme\n**Staff Engineer** | 2021 - Present\n- Runs the platform.\n") == [role]
    assert entries("### Acme\n\n**Staff Engineer** | 2021 - Present\n\n- Runs the platform.\n") == [role]
    assert entries("### Acme\n*Staff Engineer* | 2021 - Present\n- Runs the platform.\n") == [role]
    assert entries("### Acme\n#### Staff Engineer | 2021 - Present\n- Runs the platform.\n") == [role]
    assert entries("**Acme**\n**Staff Engineer** | 2021 - Present\n- Runs the platform.\n") == [role]
    # Two roles at one employer are two entries of that employer, each with its own dates.
    assert entries("### Acme\n**Staff Engineer** | 2021 - Present\n- Runs the platform.\n**Engineer** | 2018 - 2021\n- Built billing.\n") == [
        role, ("Acme", ["Engineer | 2018 - 2021"], 1),
    ]
    # Bold lines that each open an entry stay entries: a list of schools, and titles with their dates in the line.
    assert entries("**Staff Engineer, Acme** (2021 - Present)\n- Runs the platform.\n\n**Engineer, Borealis** (2016 - 2021)\n- Built billing.\n") == [
        ("Staff Engineer, Acme", ["2021 - Present"], 1), ("Engineer, Borealis", ["2016 - 2021"], 1),
    ]
    assert entries("**BS Computer Science, Example State** (2016)\n**MS Statistics, Example Tech** (2018)\n") == [
        ("BS Computer Science, Example State", ["2016"], 0), ("MS Statistics, Example Tech", ["2018"], 0),
    ]


def test_the_dates_a_heading_ends_with_become_the_role_line_and_no_word_is_lost() -> None:
    def entries(experience: str) -> list[tuple[str, list[str]]]:
        return [(entry.heading, entry.sublines) for entry in mm.read_resume("## Experience\n" + experience).sections[0].entries]

    # The heading's words stay as written and the dates as written; only the mark between them goes.
    for heading, kept, dates in (
        ("Staff Engineer, Acme Cloud (2021 - Present)", "Staff Engineer, Acme Cloud", "2021 - Present"),
        ("Acme Cloud — Staff Engineer, Jun 2019 – Jan 2023", "Acme Cloud — Staff Engineer", "Jun 2019 – Jan 2023"),
        ("Acme Cloud | Staff Engineer | 03/2018 - 11/2020", "Acme Cloud | Staff Engineer", "03/2018 - 11/2020"),
        ("B.S. Computer Science, Example State, 2016", "B.S. Computer Science, Example State", "2016"),
        ("Borealis (Platform), September 2012 to current", "Borealis (Platform)", "September 2012 to current"),
    ):
        assert entries(f"### {heading}\n- Built it.\n") == [(kept, [dates])]
        assert kept in heading and dates in heading and not re.sub(r"[\s,|()–—-]", "", heading.replace(kept, "", 1).replace(dates, "", 1))
        master = mm.plan_migration([mm.SourceResume("only", f"## Experience\n### {heading}\n- Built it.\n")])
        (entry,) = master.master.entries.values()
        assert entry.start is not None and master.to_json()["source_lines"]["kept"] == master.to_json()["source_lines"]["in"] == 3
    # A role line that already names the years, a year inside the heading, and a bracket the dates would leave open: untouched.
    assert entries("### Summit 2019 Organizers\nLead | 2018 - 2019\n- Ran it.\n") == [("Summit 2019 Organizers", ["Lead | 2018 - 2019"])]
    assert entries("### Route 2020 Logistics\n- Ran it.\n") == [("Route 2020 Logistics", [])]
    assert entries("### Acme (Remote, 2021 - Present)\n- Ran it.\n") == [("Acme (Remote, 2021 - Present)", [])]


def test_the_count_says_what_the_merge_folded_and_what_each_resume_left_out() -> None:
    newer = (
        "Platform engineer with eleven years of experience running control planes.\n\n"
        "## Experience\n\n### Acme\nStaff Engineer | 2021 - Present\n- Runs the control plane for 4,000 clusters.\n- Cut deploy time from 40 minutes\n  to 6 minutes.\n\n"
        "## Skills\n\n- Languages: Go, Python\n"
    )
    older = (
        "\n\nSenior Engineer\n\n"  # line 1 held the name: the caller's strip blanked it
        "## Experience\n\n### Acme\nSenior Staff Engineer | 2021 - Present\n- Runs the control plane for 4,000 clusters.\n- Runs the control plane for 3,000 clusters today.\n"
        "- Wrote the ledger.\n\n## Skills\n\n- Languages: Go, Rust\n\n## Interests\n"
    )
    plan = mm.plan_migration([mm.SourceResume("new", newer, ("New",)), mm.SourceResume("old", older, ("Old",), (1,))])
    lines = plan.to_json()["source_lines"]
    # The older resume: its title line and its empty section are left out, and the contact line the caller stripped is counted.
    assert [(resume["profiles"], resume["in"], {row["reason"]: row["lines"] for row in resume["left_out"]}) for resume in lines["resumes"]] == [
        (["New"], 9, {}), (["Old"], 11, {"contact": [1], "above_first_section": [3], "empty_section": [17]}),
    ]
    assert lines["folded_by_reason"] == {
        "exact_duplicate": 1, "near_duplicate": 0, "conflict": 1, "same_entry": 1, "role_line": 1, "skills_joined": 1,
    }
    assert (lines["in"], lines["kept"], lines["folded"], lines["left_out"]) == (20, 12, 5, 3)
    assert lines["in"] == lines["kept"] + lines["folded"] + lines["left_out"]
    (question,) = plan.questions
    # Answered b, the newer resume's line is the one that goes; answered both, nothing is folded there.
    for answer, kept, conflict in (("a", 12, 1), ("b", 12, 1), ("both", 13, 0)):
        answered = mm.plan_migration(
            [mm.SourceResume("new", newer, ("New",)), mm.SourceResume("old", older, ("Old",), (1,))], answers={question.question_id: answer},
        ).to_json()["source_lines"]
        assert (answered["kept"], answered["folded_by_reason"]["conflict"]) == (kept, conflict)
        assert answered["in"] == answered["kept"] + answered["folded"] + answered["left_out"]
    # The spike's pair of resumes: closed too, and nothing is left out of either.
    fixtures = SHAPES.parent
    pair = mm.plan_migration([
        mm.SourceResume("ai", (fixtures / "legacy-ai.md").read_text(encoding="utf-8"), ("AI",)),
        mm.SourceResume("swe", (fixtures / "legacy-swe.md").read_text(encoding="utf-8"), ("SWE",)),
    ]).to_json()["source_lines"]
    assert pair["in"] == pair["kept"] + pair["folded"] and pair["left_out"] == 0
    assert (pair["folded_by_reason"]["exact_duplicate"], pair["folded_by_reason"]["near_duplicate"], pair["folded_by_reason"]["conflict"]) == (4, 2, 1)


def _setup(tmp_path: Path) -> Path:
    return setup_home(tmp_path / "home", workpad_root=tmp_path / "workpads")


def _init(home: Path, *args: str) -> dict:
    result = CliRunner().invoke(cli, ["scout", "resume", "master", *args, "--home", str(home), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def test_master_init_keeps_the_summary_and_the_role_lines_and_says_what_it_left_out(tmp_path: Path) -> None:
    """The real CLI on a scratch home, on the resume shape the bug was found on (a headline, a summary with no heading, bold titles)."""

    home = _setup(tmp_path)
    runner = CliRunner()
    source = SHAPES / "bold-title.md"
    assert runner.invoke(cli, ["scout", "resume", "add", str(source), "--home", str(home), "--json"]).exit_code == 0
    (home_scout_target(home) / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))

    dry = _init(home, "init", "--dry-run")
    assert (dry["status"], dry["written"]) == ("dry_run", False)
    lines = dry["migration"]["source_lines"]
    (resume,) = lines["resumes"]
    # The import already took the name and the contact lines. The place and the headline above the summary are left
    # out, and said so by line number (of the resume as it is stored).
    (left,) = resume["left_out"]
    assert (left["reason"], left["lines"], lines["left_out"]) == ("above_first_section", [1, 3], 2)
    assert lines["in"] == lines["kept"] + lines["folded"] + lines["left_out"] and lines["left_out_by_reason"]["unread"] == 0
    text = runner.invoke(cli, ["scout", "resume", "master", "init", "--dry-run", "--home", str(home)]).output
    counted = f"Of {lines['in']} lines of resume text (headings, role lines and wrapped lines counted): {lines['kept']} kept, "
    assert counted + "0 folded into a line the master holds, 2 left out (2 above first section)." in text
    assert "Left out of the resume of default: line 1, 3: above the first section and not a summary paragraph (a name, a title or a headline)." in text
    assert "Nothing was written" in text and "Platform engineer with" not in text

    done = _init(home, "init")
    assert (done["status"], done["written"], done["migration"]["source_lines"]) == ("created", True, lines)
    shown = _init(home, "show")["master"]
    assert [item["text"] for item in shown["items"] if item["kind"] == "summary"] == [SUMMARY + ", for teams of 6 to 40 engineers."]
    assert [(entry["heading"], entry["sublines"], entry["start"], entry["end"], entry["ongoing"], len(entry["bullets"])) for entry in shown["entries"]] == [
        ("Quillmark Systems", ["Staff Platform Engineer | Mar 2020 - Present"], 2020, None, True, 2),
        ("Quillmark Systems", ["Senior Platform Engineer | Jan 2018 - Feb 2020"], 2018, 2020, False, 1),
        ("Pendle Works", ["Senior Software Engineer | Jun 2015 - Dec 2017"], 2015, 2017, False, 2),
        ("Northfield State University", ["B.S. Computer Science | 2010 - 2014"], 2010, 2014, False, 0),
    ]
    # No contact data in the master as it is shown. Its record id, revision id and updated_at are new on every run and
    # hold "555" on about one run in 80, so they are not read as text (0.1.10.10 FK); said here on every run.
    assert contact_values_in(shown, CONTACT) == []
    by_chance = {**shown, "revision_id": "revision_2e315558-53d0-4925-be4a-d01b35ba0555", "updated_at": "2026-10-04T23:22:40.555208Z"}
    assert "555" in json.dumps(by_chance) and contact_values_in(by_chance, CONTACT) == []
    summary = next(item for item in shown["items"] if item["kind"] == "summary")
    leaked = {**shown, "items": [{**summary, "text": summary["text"] + " Call (555) 010-0142."}]}
    assert contact_values_in(leaked, CONTACT) == ["555"]
