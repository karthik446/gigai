"""0110-015: a hard-wrapped paragraph copied line by line is stored and printed once.

A copy line is stored EXPANDED to the lines it wraps onto (``_resume_ref``),
so a model that copies every physical line of a hard-wrapped paragraph
(R8, R9, R10) used to store line 1 = R8..R10, line 2 = R9..R10, line 3 = R10:
the stored result, the API ``markdown`` and the ``.md`` printed the tail of
the paragraph again for every line.  Validation now drops a copy that an
earlier copy's wrapped span already holds, and ``render_markdown`` skips such
a copy in an OLDER stored result.  Fixtures are invented.
"""

from __future__ import annotations

from datetime import date

from gigai.scout.tailored_resume import (
    TailoredResume,
    TailorJob,
    apply_no_loss,
    render_markdown,
    resume_continuations,
    tailor_context,
    tailor_line_stats,
    validate_tailored_output,
)

_RESUME = (
    "# Jordan Sample\n"  # R1
    "jordan@example.test\n"  # R2
    "\n"
    "## Summary\n"  # R3
    "Support analyst with eight years keeping clinic\n"  # R4 -> R5, R6
    "systems running for small teams and\n"  # R5 -> R6
    "their patients.\n"  # R6
    "\n"
    "## Experience\n"  # R7
    "**Support Analyst — Maple Clinic** (2019–present)\n"  # R8
    "- Rebuilt the clinic backup plan across\n"  # R9 -> R10
    "three offices and two vendors.\n"  # R10
    "- Automated Python reports for finance.\n"  # R11
    "- Taught Python to new analysts.\n"  # R12
    "\n"
    "## Skills\n"  # R13
    "Ticket triage · Backup planning · Asset\n"  # R14 -> R15, R16
    "tracking · Printer fleets · Wiki\n"  # R15 -> R16
    "writing · Access reviews\n"  # R16
)
_SKILLS = ("Ticket triage", "Backup planning", "Asset tracking", "Printer fleets", "Wiki writing", "Access reviews")
_JOB = TailorJob(title="Support Analyst", company="Acme", location="Remote", posting_text="Ticket triage and backups.")
_TODAY = date(2026, 9, 30)


def _settled(payload: dict[str, object]) -> TailoredResume:
    ctx = tailor_context(_RESUME)
    return apply_no_loss(validate_tailored_output(payload, _JOB, ctx), _JOB, ctx, today=_TODAY)


def _copies(*numbers: int) -> list[dict[str, int]]:
    return [{"copy": number} for number in numbers]


def _flat_markdown(result: TailoredResume) -> str:
    # The .md prints a wrapped span on one line; joining the rows makes a skill split across R14/R15 findable.
    return " ".join(render_markdown(result).split())


def test_the_fixture_wraps_the_summary_the_bullet_and_the_skills_paragraph() -> None:
    assert resume_continuations(_RESUME) == {4: (5, 6), 5: (6,), 9: (10,), 14: (15, 16), 15: (16,)}


def test_a_wrapped_paragraph_copied_line_by_line_is_stored_and_printed_once() -> None:
    result = _settled(
        {
            "sections": [
                {"heading": "summary", "lines": _copies(4, 5, 6)},
                {"heading": "experience", "entries": [{"heading_ref": [{"copy": 8}], "bullets": _copies(9, 10, 11)}]},
                {"heading": "skills", "lines": _copies(14, 15, 16)},
            ]
        }
    )
    summary, experience, skills = result.sections
    # The symptom first: each skill once in the stored skills lines and once in the .md.
    stored_skills = " ".join(line.text for line in skills.lines)
    flat = _flat_markdown(result)
    for skill in _SKILLS:
        assert (stored_skills.count(skill), flat.count(skill)) == (1, 1), (skill, render_markdown(result))
    assert flat.count("their patients.") == 1
    assert flat.count("three offices and two vendors.") == 1
    (summary_line,) = summary.lines
    assert summary_line.text == "Support analyst with eight years keeping clinic systems running for small teams and their patients."
    assert summary_line.refs[0].continued_lines == (5, 6)
    (skills_line,) = skills.lines
    assert skills_line.refs[0].line == 14 and skills_line.refs[0].continued_lines == (15, 16)
    backup, reports = experience.entries[0].bullets
    assert backup.refs[0].continued_lines == (10,) and reports.refs[0].line == 11
    # Ids are contiguous over the lines that remain (heading L2, bullets L3-L4, skills L5).
    assert [line.id for section in result.sections for line in section.all_lines()] == ["L1", "L2", "L3", "L4", "L5"]
    stats = tailor_line_stats(result)
    assert (stats.rewritable_lines, stats.copied) == (4, 4)


def test_an_older_stored_result_with_the_repeated_tails_renders_each_line_once() -> None:
    def ref(line: int, text: str, *continued: int) -> dict[str, object]:
        out: dict[str, object] = {"kind": "resume", "line": line, "text": text}
        if continued:
            out["continued_lines"] = list(continued)
        return out

    r14, r15, r16 = "Ticket triage · Backup planning · Asset", "tracking · Printer fleets · Wiki", "writing · Access reviews"
    stored = {
        "schema_version": "scout-tailored-resume:1",
        "header": [],
        "sections": [
            {
                "heading": "skills",
                "lines": [
                    {"kind": "copy", "text": f"{r14} {r15} {r16}", "refs": [ref(14, f"{r14} {r15} {r16}", 15, 16)], "id": "L1", "origin": "model"},
                    {"kind": "copy", "text": f"{r15} {r16}", "refs": [ref(15, f"{r15} {r16}", 16)], "id": "L2", "origin": "model"},
                    {"kind": "copy", "text": r16, "refs": [ref(16, r16)], "id": "L3", "origin": "model"},
                ],
            }
        ],
    }
    result = TailoredResume.from_json(stored)
    assert len(result.sections[0].lines) == 3  # the stored result still parses as it was
    assert render_markdown(result) == f"## Skills\n\n- {r14} {r15} {r16} <!-- R14 -->\n"


def test_a_single_line_copy_and_a_word_repeated_on_different_lines_are_untouched() -> None:
    result = _settled(
        {
            "sections": [
                {"heading": "experience", "entries": [{"heading_ref": [{"copy": 8}], "bullets": _copies(11, 12)}]},
                {"heading": "skills", "lines": _copies(14)},
            ]
        }
    )
    reports, teaching = result.sections[0].entries[0].bullets
    assert (reports.text, teaching.text) == ("- Automated Python reports for finance.", "- Taught Python to new analysts.")
    markdown = render_markdown(result)
    assert "- Automated Python reports for finance. <!-- R11 -->" in markdown
    assert "- Taught Python to new analysts. <!-- R12 -->" in markdown
    assert markdown.count("Python") == 2


def test_an_older_line_whose_text_repeats_inside_the_one_above_but_cites_another_line_is_printed() -> None:
    stored = {
        "schema_version": "scout-tailored-resume:1",
        "header": [],
        "sections": [
            {
                "heading": "skills",
                "lines": [
                    {"kind": "copy", "text": "Python, SQL, Excel", "refs": [{"kind": "resume", "line": 20, "text": "Python, SQL, Excel"}]},
                    {"kind": "copy", "text": "Python", "refs": [{"kind": "resume", "line": 21, "text": "Python"}]},
                ],
            }
        ],
    }
    assert render_markdown(TailoredResume.from_json(stored)) == "## Skills\n\n- Python, SQL, Excel <!-- R20 -->\n- Python <!-- R21 -->\n"
