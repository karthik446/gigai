"""0.1.11.4 item 9d (packet H2): education must not be missing without a word.

The operator's master has no Education section and nothing said so: a resume picked from it prints no degree.
Two END outcomes, through the real CLI on a scratch home (no model, no network):

* ``master init --from FILE`` (and the merge of the profiles' resumes) SAYS when the file has lines that look like
  education and did not become an Education entry: by line number and the section that holds them, in the text, in
  ``source_lines.resumes[].education`` and never by the text. What is stored is the same as before (a report only);
  a file that gave an Education entry reports nothing;
* ``master show`` says ``Your master has no education.`` with the command that adds a school, and stops saying it
  once the master has one.

Every resume below is invented (an invented person's lines, an invented school). No operator data is read.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import master_migration as mm

from tests.support.setup_home import setup_home

SUMMARY = "Platform engineer with 11 years of experience running Kubernetes control planes and the delivery tooling around them."
EXPERIENCE = (
    "## Experience\n\n### Northwind Labs\nStaff Engineer | 2019 - Present\n"
    "- Built the deploy pipeline for 40 services.\n- Cut build time from 50 to 12 minutes.\n"
)
SCHOOL = "Lakeside University"
DEGREE = f"B.S. Computer Science, {SCHOOL}, 2010"
ADD = 'gigai scout resume master add --heading SCHOOL --role "DEGREE | YEAR" --section education'
NOTICE = "Your master has no education."

#: name -> (the file, the blocks said: (first line, last line, the section that holds them)).
LOOKS_LIKE_EDUCATION: dict[str, tuple[str, list[tuple[int, int, str]]]] = {
    # A degree under a heading this reader does not know: the heading is read as Other and the degree is a line of it.
    "a degree under a Schooling heading": (f"## Summary\n{SUMMARY}\n\n{EXPERIENCE}\n## Schooling\n\n{DEGREE}\n", [(13, 13, "other")]),
    # A bare degree line after the last role's bullets: it is kept as a line of that role.
    "a bare degree line": (f"## Summary\n{SUMMARY}\n\n{EXPERIENCE}\n{DEGREE}\n\n## Skills\n- Languages: Go, Python\n", [(11, 11, "experience")]),
    # The school on one line and the degree with its year on the next, under Certifications (read as Other).
    "a school and a degree on two lines": (
        f"{EXPERIENCE}\n## Certifications\n\n### {SCHOOL}\nMaster of Science in Data Systems | 2012\n", [(10, 11, "other")],
    ),
    # A bold line that is only the word Education inside Skills: the lines under it became a Skills line.
    "a bold Education line inside Skills": (
        f"{EXPERIENCE}\n## Skills\n- Languages: Go, Python\n\n**Education**\n{SCHOOL}\nB.A. Economics | 2008\n", [(11, 13, "skills")],
    ),
    # No markdown heading at all: the degree after a role's bullets opens no section and stays in Experience.
    "a degree in a resume of plain lines": (
        f"EXPERIENCE\n\nNorthwind Labs\nStaff Engineer | 2019 - Present\n- Built the deploy pipeline for 40 services.\n\n{DEGREE}\n\nSKILLS\nLanguages: Go, Python\n",
        [(7, 7, "experience")],
    ),
}
#: Files that say nothing: an Education entry was read, or nothing looks like education.
NOTHING_TO_SAY: dict[str, str] = {
    "a normal Education section": f"{EXPERIENCE}\n## Education\n\n### {SCHOOL}\nB.S. Computer Science | 2010\n",
    "an Education section of one plain line": f"{EXPERIENCE}\n## Education\n\n{DEGREE}\n",
    "a heading read as Education by a word of it": f"{EXPERIENCE}\n## Academic Qualifications\n- {DEGREE}\n",
    "a degree line beside a real Education entry": f"{EXPERIENCE}\n{DEGREE}\n\n## Education\n\n### Harbor College\nB.A. Economics | 2006\n",
    "skills that only look like degree letters": f"{EXPERIENCE}\n## Skills\n- Languages: Go, Python\n- Tools: MS SQL, BA dashboards\n",
    "a role at a university": "## Experience\n\n### Lakeside University\nResearch Engineer | 2015 - 2017\n- Ran the MS SQL cluster for 12 labs.\n",
}


def _sentence(first: int, last: int, section: str, of: str = "your file") -> str:
    lines, verb = (f"line {first}", "was") if first == last else (f"lines {first}-{last}", "were")
    return f"education: {lines} of {of} {verb} kept in {section.capitalize()}, not as a degree"


def _home(tmp_path: Path) -> Path:
    return setup_home(tmp_path / "home", workpad_root=tmp_path / "workpads")


def _master(home: Path, *args: str, as_json: bool = False) -> str:
    result = CliRunner().invoke(cli, ["scout", "resume", "master", *args, "--home", str(home), *(["--json"] if as_json else [])])
    assert result.exit_code == 0, result.output
    return result.output


def _init(home: Path, tmp_path: Path, text: str, *args: str) -> tuple[str, dict]:
    """``master init --from FILE``: its text output, and its JSON (a dry run: the same reading, nothing written)."""

    source = tmp_path / "resume.md"
    source.write_text(text, encoding="utf-8")
    said = _master(home, "init", "--from", str(source), *args)
    payload = json.loads(_master(home, "init", "--from", str(source), "--dry-run", as_json=True).strip().splitlines()[-1])
    return said, payload


@pytest.mark.parametrize("name", list(LOOKS_LIKE_EDUCATION))
def test_init_says_which_lines_look_like_education_and_were_not_read_as_a_degree(name: str, tmp_path: Path) -> None:
    text, blocks = LOOKS_LIKE_EDUCATION[name]
    home = _home(tmp_path)
    before = _init(home, tmp_path, text, "--dry-run")[1]["master"]["counts"]
    said, payload = _init(home, tmp_path, text)
    (resume,) = payload["source_lines"]["resumes"]
    sentences = [_sentence(*block) for block in blocks]
    # THE END OUTCOME: the person at the terminal reads where the lines went, by number, and how to add the degree.
    for sentence in sentences:
        assert f"  {sentence}." in said, said
    assert f"`{ADD}`" in said
    assert resume.get("education") == [
        {"first": first, "last": last, "section": section, "message": _sentence(first, last, section)} for first, last, section in blocks
    ]
    # Never the text: neither the school nor the degree is in what the command says or sends.
    assert SCHOOL not in said and "Computer Science" not in said and "Economics" not in said
    assert SCHOOL not in json.dumps(payload["source_lines"])
    # A report only: no Education entry is made up, and the master holds exactly the lines it held before this packet.
    shown = json.loads(_master(home, "show", as_json=True).strip().splitlines()[-1])["master"]
    assert not [entry for entry in shown["entries"] if entry["section"] == "education"]
    assert shown["counts"] == before
    held = " ".join([item["text"] for item in shown["items"]] + [line for entry in shown["entries"] for line in (entry["heading"], *entry["sublines"])])
    assert SCHOOL in held, "the lines that look like education are still in the master, where the reader put them"
    lines = payload["source_lines"]
    assert lines["in"] == lines["kept"] + lines["left_out"]


@pytest.mark.parametrize("name", list(NOTHING_TO_SAY))
def test_a_file_that_gave_an_education_entry_or_holds_no_degree_reports_nothing(name: str, tmp_path: Path) -> None:
    said, payload = _init(_home(tmp_path), tmp_path, NOTHING_TO_SAY[name])
    (resume,) = payload["source_lines"]["resumes"]
    assert resume["education"] == [] and "education:" not in said and "add it as a degree" not in said


def test_the_merge_of_the_profiles_resumes_says_it_by_profile_and_only_when_the_master_has_no_education() -> None:
    schooling, blocks = LOOKS_LIKE_EDUCATION["a degree under a Schooling heading"]
    alone = mm.plan_migration([mm.SourceResume("one", schooling, ("Staff Engineer",))])
    (resume,) = alone.source_lines.to_json()["resumes"]  # type: ignore[misc]
    assert [row["message"] for row in resume["education"]] == [_sentence(*blocks[0], of="the resume of Staff Engineer")]
    assert not [entry for entry in alone.master.entries.values() if entry.section == "education"]
    # Another resume gives the master its Education entry: the master has its education, so nothing is said.
    both = mm.plan_migration([
        mm.SourceResume("one", schooling, ("Staff Engineer",)), mm.SourceResume("two", NOTHING_TO_SAY["a normal Education section"], ("Platform Engineer",)),
    ])
    assert [resume["education"] for resume in both.source_lines.to_json()["resumes"]] == [[], []]  # type: ignore[union-attr]
    assert [entry.heading for entry in both.master.entries.values() if entry.section == "education"] == [SCHOOL]


def test_master_show_says_the_master_has_no_education_and_how_to_add_it_until_it_has_one(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _init(home, tmp_path, f"## Summary\n{SUMMARY}\n\n{EXPERIENCE}\n## Skills\n- Languages: Go, Python\n")
    line = f"{NOTICE} Add it: `{ADD}`."
    assert line in _master(home, "show").splitlines()
    assert line in _master(home, "show", "--section", "education").splitlines()
    # Asked for something else, the output is about that alone.
    assert NOTICE not in _master(home, "show", "--section", "experience")
    # The command the notice names, filled in: the master has its education and nothing more is said.
    _master(home, "add", "--heading", SCHOOL, "--role", "B.S. Computer Science | 2010", "--section", "education")
    shown = _master(home, "show")
    assert NOTICE not in shown and "## Education" in shown and SCHOOL in shown
