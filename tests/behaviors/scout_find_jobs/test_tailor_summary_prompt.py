"""0110-006 A2: the summary is a posting-targeted rewrite by default.

Live proof L1 showed the tailor copying the summary verbatim for every
posting.  The prompt's SUMMARY paragraph now makes a targeted rewrite the
default when a matrix row or a posting phrase asks for something the
summary already says, and a copy when nothing does.  These tests render the
SHIPPED prompt for a synthetic resume and posting, pin the paragraph, and
run the paragraph's own worked example through the product's validator and
no-loss pass, so the example the model is shown is one GigAI accepts.
"""

from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path

import pytest

from gigai.scout.tailor_no_loss import lost_items
from gigai.scout.tailored_resume import (
    LineReason,
    MatrixRow,
    TailorJob,
    TailoredResume,
    apply_no_loss,
    load_tailor_instructions,
    render_tailor_prompt,
    tailor_context,
    validate_tailored_output,
)

_EXAMPLE_SOURCE = (
    "Hospital IT analyst with 8 years on lab and radiology systems at a 400-bed hospital; "
    "built 30+ HL7 interfaces and owned the results feed end to end."
)
_RESUME = (
    "# Alex Example\n"  # R1 withheld (name)
    "## Summary\n"  # R2
    f"{_EXAMPLE_SOURCE}\n"  # R3: the paragraph's example line, at the example's number
    "\n"
    "## Experience\n"  # R5
    "**Interface Analyst — Example Health** (2019–present)\n"  # R6
    "- Maintained the admission feed for 3 clinics.\n"  # R7
)
_POSTING = "Example Clinic is hiring an interface analyst. Requirements: HL7 interfaces; lab systems."
_JOB = TailorJob(title="Interface Analyst", company="Example Clinic", location="Remote", posting_text=_POSTING)
_MATRIX = (MatrixRow("Vendor certification", "missing"), MatrixRow("HL7 interface development", "met"))
_CTX = tailor_context(_RESUME, matrix=_MATRIX)
_TODAY = date(2026, 9, 30)


def _summary_block(prompt: str) -> str:
    blocks = [block for block in prompt.split("\n\n") if block.startswith("SUMMARY:")]
    assert len(blocks) == 1
    return blocks[0]


def _example(block: str) -> tuple[str, dict[str, object]]:
    """The example's source line and its rewritten line, parsed out of the shipped paragraph."""

    source = block[block.index('EXAMPLE (invented): R3 "') + len('EXAMPLE (invented): R3 "') : block.index('" with M2:')]
    start = block.index('{"text": ')
    end = block.index("}}: 8, 400") + 2
    return source, json.loads(block[start:end])


def _settle(payload: dict[str, object], job: TailorJob = _JOB, ctx=_CTX) -> TailoredResume:
    return apply_no_loss(validate_tailored_output(payload, job, ctx), job, ctx, today=_TODAY)


def test_the_summary_paragraph_makes_a_targeted_rewrite_the_default_and_a_copy_the_no_reason_case() -> None:
    prompt = render_tailor_prompt(_JOB, _CTX)
    block = _summary_block(prompt)
    assert block.startswith("SUMMARY: the summary is the one place where a rewrite is the default.")
    assert "When a REQUIREMENT MATRIX row (M<n>) or a posting phrase of at most 60 characters asks for something the candidate's summary lines already say" in block
    assert "write the summary as ONE rewritten line of 1 or 2 sentences built only from those summary lines" in block
    assert "lead with what the posting asks for, change only the order and the joining words, cite every summary line it replaces (do not also copy them)" in block
    assert '"reason": {"kind": "summary", "requirement": "M<n>", "posting_phrase": "<copied from the posting>" or null}' in block
    assert "It keeps every number, named technology, scope word and ownership verb of the lines it cites and adds no fact and no posting word they do not contain." in block
    # The no-reason case: the candidate's summary, unchanged.
    assert "If nothing in the posting gives such a reason, or the summary does not fit one line of 400 characters and 4 refs, copy the summary lines unchanged." in block
    assert block.endswith('For a posting that asks for nothing R3 says, the summary is {"copy": 3}.')
    # The paragraph sits after SECTIONS and before the posting; the bullet rules stay copy-by-default.
    assert prompt.index("SECTIONS:") < prompt.index("SUMMARY:") < prompt.index("BOUNDS:") < prompt.index("POSTING TEXT:")
    assert "and it is the default for every bullet, skills and other line (the summary follows SUMMARY)." in prompt
    assert "A rewrite is the exception (the summary: see SUMMARY)." in prompt
    assert "never shorten a bullet" in prompt
    # With no matrix the paragraph still ships (a posting phrase can give the reason).
    assert _summary_block(render_tailor_prompt(_JOB, tailor_context(_RESUME))) == block


def test_the_shipped_example_passes_the_validator_and_the_no_loss_check() -> None:
    source, example = _example(_summary_block(render_tailor_prompt(_JOB, _CTX)))
    assert source == _EXAMPLE_SOURCE and _CTX.resume_lines[2] == source
    assert example["refs"] == [{"kind": "resume", "line": 3}]
    assert example["reason"] == {"kind": "summary", "requirement": "M2", "posting_phrase": None}
    text = str(example["text"])
    assert text != source and text.startswith("Built 30+ HL7 interfaces")
    # The paragraph names the kept items; the product check agrees nothing is lost.
    for item in ("8", "400", "30+", "HL7", "built", "owned", "end to end"):
        assert item.lower() in text.lower() and item.lower() in source.lower()
    assert lost_items(text, [source]) == {}

    (line,) = _settle({"sections": [{"heading": "summary", "lines": [example]}]}).sections[0].lines
    assert line.kind == "rewritten" and line.origin == "model" and line.text == text
    assert line.reason == LineReason("summary", "M2", None)
    assert line.alternative is not None and line.alternative.text == source


def test_a_summary_rewrite_without_a_posting_reason_or_with_a_loss_falls_back_to_the_candidates_line() -> None:
    _, example = _example(_summary_block(render_tailor_prompt(_JOB, _CTX)))
    # No matrix, and the quoted phrase is not in this posting: no reason, so the copy wins.
    plain = TailorJob(title="Interface Analyst", company="Example Clinic", location="Remote", posting_text="Requirements: scheduling systems.")
    unanchored = dict(example, reason={"kind": "summary", "requirement": None, "posting_phrase": "HL7 interfaces"})
    (line,) = _settle({"sections": [{"heading": "summary", "lines": [unanchored]}]}, plain, tailor_context(_RESUME)).sections[0].lines
    assert line.kind == "copy" and line.text == _EXAMPLE_SOURCE and line.origin == "fallback"
    assert line.alternative is not None and line.alternative.reason_invalid is True
    # A targeted summary that drops "end to end" is replaced by the original line.
    lossy = dict(example, text="Built 30+ HL7 interfaces and owned the results feed as a hospital IT analyst with 8 years on lab and radiology systems at a 400-bed hospital.")
    (line,) = _settle({"sections": [{"heading": "summary", "lines": [lossy]}]}).sections[0].lines
    assert line.kind == "copy" and line.text == _EXAMPLE_SOURCE
    assert line.alternative is not None and line.alternative.lost_dict() == {"scope": ["end to end"]}


def test_the_summary_paragraph_and_this_file_carry_no_denylisted_name() -> None:
    denylist = os.environ.get("GIGAI_FIXTURE_DENYLIST")
    if not denylist:
        pytest.skip("GIGAI_FIXTURE_DENYLIST is not set (CI): the operator's private denylist is local only")
    entries = [line.strip().lower() for line in Path(denylist).read_text(encoding="utf-8").splitlines() if line.strip()]
    assert entries, "the denylist file is empty"
    texts = {"tailor.md SUMMARY": _summary_block(load_tailor_instructions()).lower(), "this test": Path(__file__).read_text(encoding="utf-8").lower()}
    hits = [f"{name} (denylist line {index})" for name, text in texts.items() for index, entry in enumerate(entries, 1) if entry in text]
    assert not hits, "real operator data in the summary example (entry text withheld):\n" + "\n".join(hits)
