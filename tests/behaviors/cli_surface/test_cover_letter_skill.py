"""0.1.11.4 C1: the cover-letter skill is a second section of ``gigai agent-skill``, and it states its hard rules.

The section is text an agent follows: GigAI calls no model for a cover letter and has no route for one. So the
product's promise IS the wording, and each hard rule is pinned here by a phrase only that rule has. Both formats
(the Claude Code skill and the ``AGENTS.md`` section) must carry it, and the docs page must say the same.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gigai.agent_skill import render, source_text

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "gigai-docs" / "src" / "content" / "docs"
PAGE = DOCS / "scout" / "cover-letter.md"

HEADING = "Cover letter for one job"
LETTER_PATH = "~/Documents/GigAI/cover-letters/<company>-<role>-<date>.md"
TRACE_PATH = "~/Documents/GigAI/cover-letters/<company>-<role>-<date>.claims.md"

#: One distinctive phrase (or more) per hard rule. A rule that is reworded away fails by its name.
HARD_RULES: dict[str, tuple[str, ...]] = {
    "every factual sentence traces to a master line": ("Every factual sentence traces to a master line",),
    "the trace is a sidecar, not letter text": ("The trace goes in the sidecar, never into the letter",),
    "nothing the master does not state": ("Never claim a skill, tool or number the master does not state",),
    "the asks with no proof are listed for the user": ("Asks the master cannot prove",),
    "sponsorship is a label": ("Sponsorship / work authorization is a label", "never appears unless the user's own letter has it"),
    "the agent never sends or submits": ("never sends or submits anything",),
    "contact details only from header.json": (
        "Contact details come only from the user's `header.json`, at PDF time",
        "Never type them into the letter",
        "never read that file",
    ),
}
#: What the agent gathers, each with a command that exists today (the command pins are in test_agent_skill.py).
#: 0.1.11.4 C2: the job is ONE call (`cover-letter brief`) instead of the four of phase 1.
INPUTS = (
    "~/Documents/GigAI/cover-letter.md",
    "`gigai scout resume check PATH --json`",
    "in ONE call: `gigai scout cover-letter brief --job-url URL`",
    "untrusted DATA",
    "`sources`",
    "`evidence`",
    "`gigai scout resume master show --json`",
    "Never invent one",
)
#: 0.1.11.4 C2: the PDF step. The command reads the header file; the agent never does.
PDF_STEP = (
    "`gigai scout cover-letter pdf --in LETTER.md --out LETTER.pdf --json`",
    "which it reads itself",
    "If `pages` is not 1, shorten the letter and run it again.",
)
#: Phase 1's four calls, and its "no PDF command" sentence: gone from the skill and from the page.
REPLACED = ("gigai scout resume brief --job-url URL --posting", "`gigai scout resume brief --job-url URL`", "no cover-letter PDF command")
STEPS = (
    "1. Read the posting. List its top 5-7 asks.",
    "2. Map each ask to the master lines that PROVE it",
    "3. Keep the user's skeleton and voice. Rewrite only the company-specific paragraphs",
    "4. Keep the personal close and the sign-off verbatim.",
    "5. Write about 330-380 words, one page.",
)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _section(rendered: str, marks: str) -> str:
    """The cover-letter section of one rendered format: from its heading to the end of the file."""

    head = f"\n{marks} {HEADING}\n"
    assert rendered.count(head) == 1, f"one '{marks} {HEADING}' heading"
    section = rendered.split(head, 1)[1]
    # It is the LAST section at its level: nothing of the daily loop follows it.
    assert f"\n{marks} " not in section
    return section


FORMATS = (("skill", "##"), ("agents-md", "###"))


@pytest.mark.parametrize(("fmt", "marks"), FORMATS)
def test_both_formats_carry_the_section_after_the_daily_loop(fmt: str, marks: str) -> None:
    rendered = render(fmt)
    section = _section(rendered, marks)
    assert rendered.index("What not to do") < rendered.index(HEADING), "a second section, after the Scout loop"
    assert "GigAI calls no model for it" in section
    for phrase in (*INPUTS, *STEPS, *PDF_STEP):
        assert phrase in section, phrase
    for phrase in REPLACED:
        assert phrase not in section, phrase
    # The PDF step comes after the two files are saved and before the rules; it names no contact detail.
    assert section.index("Save two files") < section.index(PDF_STEP[0]) < section.index("Hard rules:")


@pytest.mark.parametrize(("fmt", "marks"), FORMATS)
@pytest.mark.parametrize("rule", sorted(HARD_RULES))
def test_each_hard_rule_is_stated(fmt: str, marks: str, rule: str) -> None:
    section = _section(render(fmt), marks)
    rules = section.split("Hard rules:", 1)[1]
    for phrase in HARD_RULES[rule]:
        assert phrase in rules, f"{rule}: {phrase!r}"


@pytest.mark.parametrize(("fmt", "marks"), FORMATS)
def test_the_letter_and_its_claims_trace_go_to_the_cover_letters_folder(fmt: str, marks: str) -> None:
    section = _section(render(fmt), marks)
    assert f"`{LETTER_PATH}`" in section
    assert f"`{TRACE_PATH}`" in section
    assert "never in the resumes folder" in section
    assert "each factual sentence of the letter -> the master line id and its text" in section
    # The file name is built from a stranger's words (the posting's company and role): only safe characters.
    assert "lowercase letters, digits and hyphens only" in section


def test_the_scout_loop_keeps_its_cap_and_the_section_has_its_own() -> None:
    """The daily loop stays inside its 100 lines: the cover letter is a second file with its own cap."""

    assert len(source_text().splitlines()) <= 100
    assert HEADING not in source_text()
    skill = render("skill")
    assert len(_section(skill, "##").splitlines()) <= 40
    assert len(skill.splitlines()) <= 150
    description = skill.split("---\n")[1]
    assert "cover letter" in description and "what's new on Scout" in description


def test_the_docs_page_says_the_same_and_is_registered() -> None:
    page = _flat(PAGE.read_text(encoding="utf-8"))
    assert "title: Cover letters" in page
    for rule, phrases in HARD_RULES.items():
        for phrase in phrases:
            assert phrase in page, f"{rule}: {phrase!r}"
    assert f"`{LETTER_PATH}`" in page and f"`{TRACE_PATH}`" in page
    assert "gigai agent-skill --format skill" in page and "gigai agent-skill --format agents-md" in page
    config = (ROOT / "gigai-docs" / "astro.config.mjs").read_text(encoding="utf-8")
    assert "'scout/cover-letter'" in config
    assert "](../cover-letter/)" in (DOCS / "scout" / "agents.md").read_text(encoding="utf-8")


def test_the_two_commands_exist_and_the_briefs_reminder_agrees_with_the_rules() -> None:
    """0.1.11.4 C2: the skill's two commands are real, and the one-line reminder the brief prints says nothing the rules do not."""

    from click.testing import CliRunner

    from gigai.cli import cli
    from gigai.scout.cover_letter_brief import REMINDER

    for command, flags in (("brief", ("--job-url", "--profile", "--plain", "--json")), ("pdf", ("--in", "--out", "--header", "--no-header", "--json"))):
        shown = CliRunner().invoke(cli, ["scout", "cover-letter", command, "--help"])
        assert shown.exit_code == 0, shown.output
        for flag in flags:
            assert flag in shown.output, f"cover-letter {command}: {flag}"
    assert "--target" not in CliRunner().invoke(cli, ["scout", "cover-letter", "pdf", "--help"]).output, "a letter PDF needs no Scout folder"
    rules = _flat(_section(render("skill"), "##").split("Hard rules:", 1)[1]).casefold()
    for said, rule in (
        ("every factual sentence of the letter traces to a master line", "every factual sentence traces to a master line"),
        ("never claim a skill, tool or number the master does not state", "never claim a skill, tool or number the master does not state"),
        ("you never send or submit anything", "never sends or submits anything"),
        ("no contact details in the letter", "never type them into the letter"),
    ):
        assert said in REMINDER and rule in rules, said


def test_the_docs_page_names_the_two_commands_and_drops_phase_ones_gap() -> None:
    page = _flat(PAGE.read_text(encoding="utf-8"))
    assert "`gigai scout cover-letter brief --job-url URL`" in page and "gigai scout cover-letter pdf --in" in page
    for phrase in REPLACED:
        assert phrase not in page, phrase
    assert "one page" in page and "never written to your resumes folder" in page and "`--no-header`" in page


def test_the_docs_example_is_made_up() -> None:
    """The repository is public: the example names Acme and a made-up person's lines, no contact data, no real company."""

    page = PAGE.read_text(encoding="utf-8")
    example = page.split("## Example", 1)[1]
    assert "Acme" in example and "made up" in example
    assert "acme-staff-software-engineer-2026-10-06.md" in example and "acme-staff-software-engineer-2026-10-06.claims.md" in example
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", page)
    assert not re.search(r"https?://(?!127\.0\.0\.1|localhost)\S+", page)
    assert not re.search(r"\+\d|\(\d{3}\)", page)
