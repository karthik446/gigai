"""uat-bug-044: a resume whose entry headings are already markdown headings.

The ``.md`` must carry one ``### `` per entry heading (never ``### ###``), the
cross-entry guard's entry map must still key the ``###`` block, and the refs
comment on every line stays as before.
"""

from __future__ import annotations

import pytest

from gigai.scout.tailored_resume import (
    TailorContext,
    TailorJob,
    TailorValidationError,
    render_markdown,
    resume_entries,
    resume_lines,
    validate_tailored_output,
)

_RESUME = (
    "# Riley Example\n"
    "kar@example.test\n"
    "\n"
    "## Experience\n"
    "### **Example Corp** — Systems Analyst (2019–2023)\n"
    "Built Python services for the tutoring platform.\n"
    "### Northwind — Help Desk Lead (2016–2019)\n"
    "Operated PostgreSQL clusters.\n"
    "\n"
    "## Education\n"
    "### EXAMPLE CORP\n"
    "BS Computer Science\n"
)
_LINES = resume_lines(_RESUME)
_JOB = TailorJob(title="Engineer", company="Acme", location="Remote", posting_text="Python and PostgreSQL work.")
_PAYLOAD = {
    "header": [{"copy": 1}],
    "sections": [
        {
            "heading": "experience",
            "entries": [
                {
                    "heading_ref": [{"copy": 4}],
                    "bullets": [{"text": "Built Python services.", "refs": [{"kind": "resume", "line": 5}]}],
                }
            ],
        },
        {"heading": "education", "entries": [{"heading_ref": [{"copy": 9}], "bullets": []}]},
    ],
}


def test_a_markdown_heading_resume_renders_one_marker_per_entry_heading() -> None:
    result = validate_tailored_output(_PAYLOAD, _JOB, TailorContext(resume_lines=_LINES, entries=resume_entries(_RESUME)))
    markdown = render_markdown(result)
    assert "### Example Corp — Systems Analyst (2019–2023) <!-- R4 -->" in markdown
    assert "### EXAMPLE CORP <!-- R9 -->" in markdown
    assert "### ###" not in markdown
    assert "####" not in markdown


def test_the_entry_map_still_keys_markdown_heading_blocks_and_the_guard_still_fires() -> None:
    entries = resume_entries(_RESUME)
    assert entries == {4: 4, 5: 4, 6: 6, 7: 6, 9: 9, 10: 9}
    ctx = TailorContext(resume_lines=_LINES, entries=entries)
    mixed = {
        "header": [{"copy": 1}],
        "sections": [{"heading": "summary", "lines": [{"text": "Built Python and ran PostgreSQL.", "refs": [{"kind": "resume", "line": 5}, {"kind": "resume", "line": 7}]}]}],
    }
    with pytest.raises(TailorValidationError, match="cross_entry_citation"):
        validate_tailored_output(mixed, _JOB, ctx)
