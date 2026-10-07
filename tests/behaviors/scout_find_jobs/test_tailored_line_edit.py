"""0110-032: a tailored resume line edited by hand -- ``apply_line_edit`` (``PUT /api/tailored-resumes/lines``
``use: "custom"``), the way back through ``apply_line_choice``, the personal-info check and the renderers.

What is pinned:

* an edit makes the line ``kind: custom``, ``origin: user`` with NO refs, reason or alternative (no source
  and no no-loss claim), and ``edited_from`` keeps the line it replaced; the JSON round-trips;
* the markdown marks it ``<!-- edited -->`` and the PDF prints the new text, as a bullet where the line
  it replaced was one;
* ``use: original`` / ``use: rewritten`` bring back the replaced line's versions, and asking for a
  version it never had is a clear error;
* a name or contact line is refused (``personal_info_refused``) and nothing changes;
* the stats count an edited line as edited, not copied or rewritten.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from pypdf import PdfReader

from gigai.scout.find_jobs.assess_contracts import ResolvedJob, ResolvedResume
from gigai.scout.find_jobs.contracts import FindJobsContractError, ModelTarget, Producer
from gigai.scout.resume_display import PdfHeader
from gigai.scout.resume_pdf import render_markdown_pdf, render_pdf
from gigai.scout.tailored_resume import (
    MAX_CUSTOM_TEXT_CHARS,
    TAILOR_INSTRUCTIONS_DIGEST,
    LineAlternative,
    SourceRef,
    TailoredEntry,
    TailoredLine,
    TailoredResume,
    TailoredSection,
    TailorError,
    TailorResponse,
    TailorSources,
    apply_line_choice,
    apply_line_edit,
    custom_line_text,
    personal_info_found,
    render_markdown,
    tailor_line_stats,
)

STAMP = datetime(2026, 9, 29, tzinfo=timezone.utc)
HEADER = PdfHeader("Riley Example", "", ())

SUMMARY = "Platform engineer with nine years building billing systems."
HEADING = "**Staff Engineer — Northwind Health** (2020–present)"
ORIGINAL = "- Rebuilt the scheduling service on Python and Postgres, cutting p95 latency by 40%."
REWRITE = "Rebuilt the scheduling service on Python and Postgres."
PLAIN = "- Ran the release calendar for 4 teams."
SKILLS = "- Python, PostgreSQL, Kubernetes"
EDIT = "Rebuilt the scheduling service on Python and Postgres for 4 hospital teams, cutting p95 latency by 40%."


def _ref(number: int, text: str) -> SourceRef:
    return SourceRef("resume", number, None, text)


def _result() -> TailoredResume:
    """L1 summary (copy) · L2 entry heading · L3 a fallback (original shown, rewrite kept) · L4 a plain copy · L5 skills."""

    return TailoredResume(
        (),
        (
            TailoredSection("summary", (TailoredLine("copy", SUMMARY, (_ref(1, SUMMARY),), "L1", None, "model"),)),
            TailoredSection(
                "experience",
                (),
                (
                    TailoredEntry(
                        (TailoredLine("copy", HEADING, (_ref(2, HEADING),), "L2", None, "model"),),
                        (
                            TailoredLine(
                                "copy", ORIGINAL, (_ref(3, ORIGINAL),), "L3", None, "fallback",
                                LineAlternative("rewritten", REWRITE, (_ref(3, ORIGINAL),), None, (("numbers", ("40%",)),)),
                            ),
                            TailoredLine("copy", PLAIN, (_ref(4, PLAIN),), "L4", None, "model"),
                        ),
                    ),
                ),
            ),
            TailoredSection("skills", (TailoredLine("copy", SKILLS, (_ref(5, SKILLS),), "L5", None, "model"),)),
        ),
    )


def _stored(result: TailoredResume | None = None) -> TailorResponse:
    digest = "sha256:" + "a" * 64
    result = result or _result()
    return TailorResponse(
        job=ResolvedJob.from_json(
            {
                "schema_version": ResolvedJob.schema_version, "job_identity": "text:" + digest, "source_url": None,
                "normalized_url": None, "fetch_kind": "pasted", "title": "Staff Engineer", "company": "Acme",
                "location": "", "text": "", "text_sha256": digest,
            }
        ),
        resume=ResolvedResume(None, None, digest),
        sources=TailorSources(digest, 5, {}, None),
        result=result,
        markdown=render_markdown(result),
        producer=Producer("scout.tailor", "1", "scout-tailor", ModelTarget.OLLAMA_LOCAL, "fixture"),
        usage=None,
        instructions_digest=TAILOR_INSTRUCTIONS_DIGEST,
        created_at="2026-09-30T00:00:00Z",
        updated_at="2026-09-30T00:00:01Z",
        stored_path="/x/a.json",
        markdown_path="/x/a.md",
    )


def _line(stored: TailorResponse, line_id: str) -> TailoredLine:
    return next(line for section in stored.result.sections for line in section.all_lines() if line.id == line_id)


def _pdf_text(result: TailoredResume) -> str:
    data = render_pdf(result, HEADER, company="Acme", timestamp=STAMP)
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def test_an_edit_claims_no_source_keeps_the_replaced_line_and_round_trips() -> None:
    stored = _stored()
    edited = apply_line_edit(stored, "L3", f"- {EDIT}")
    line = _line(edited, "L3")
    assert (line.kind, line.text, line.refs, line.reason, line.alternative, line.origin, line.id) == ("custom", EDIT, (), None, None, "user", "L3")
    assert line.edited_from == _line(stored, "L3"), "the replaced line is kept whole, alternative and lost items included"
    assert edited.updated_at == stored.updated_at
    assert _line(edited, "L4") == _line(stored, "L4"), "no other line changes"

    payload = line.to_json()
    assert payload["kind"] == "custom" and payload["refs"] == [] and "alternative" not in payload and "reason" not in payload
    assert payload["edited_from"]["text"] == ORIGINAL and payload["edited_from"]["alternative"]["text"] == REWRITE
    assert TailorResponse.from_json(edited.to_json()) == edited

    # The same text again changes nothing; another edit keeps the FIRST replaced line.
    assert apply_line_edit(edited, "L3", EDIT) is edited
    again = apply_line_edit(edited, "L3", "A second wording of the same bullet.")
    assert _line(again, "L3").text == "A second wording of the same bullet." and _line(again, "L3").edited_from == _line(stored, "L3")


def test_the_markdown_and_the_pdf_print_the_edit() -> None:
    stored = _stored()
    edited = apply_line_edit(apply_line_edit(stored, "L3", EDIT), "L4", "Ran the release calendar and on-call rota for 4 teams.")
    assert f"- {EDIT} <!-- edited -->" in edited.markdown and "<!-- R3 -->" not in edited.markdown
    assert "- Ran the release calendar and on-call rota for 4 teams. <!-- edited -->" in edited.markdown
    text = " ".join(_pdf_text(edited.result).split())
    assert EDIT in text and "Ran the release calendar and on-call rota for 4 teams." in text
    assert ORIGINAL[2:] not in text and "Ran the release calendar for 4 teams." not in text and "edited" not in text
    # The stored markdown renders (through the markdown route) to the same bytes as the stored result.
    assert render_markdown_pdf(edited.markdown, HEADER, company="Acme", timestamp=STAMP).pdf == render_pdf(edited.result, HEADER, company="Acme", timestamp=STAMP)

    # An edited summary stays prose and an edited skills line still prints as tags: the shape is the replaced line's.
    summary = apply_line_edit(stored, "L1", "Platform engineer with nine years in healthcare billing.")
    skills = apply_line_edit(stored, "L5", "Python, PostgreSQL, Terraform")
    assert "• Platform engineer" not in _pdf_text(summary.result) and "Platform engineer with nine years in healthcare billing." in _pdf_text(summary.result)
    assert "Python, PostgreSQL, Terraform" in _pdf_text(skills.result)


def test_switching_back_to_the_original_or_the_rewrite() -> None:
    stored = _stored()
    edited = apply_line_edit(stored, "L3", EDIT)

    # The replaced line showed the original: "original" restores it exactly as it was.
    back = apply_line_choice(edited, "L3", "original")
    assert back == stored and back.markdown == stored.markdown

    # "rewritten" shows the rewrite that line kept as its alternative (the usual swap: origin user).
    rewrite = apply_line_choice(edited, "L3", "rewritten")
    line = _line(rewrite, "L3")
    assert (line.kind, line.text, line.origin, line.edited_from) == ("rewritten", REWRITE, "user", None)
    assert line.alternative is not None and line.alternative.text == ORIGINAL and line.alternative.lost == (("numbers", ("40%",)),)
    assert rewrite == apply_line_choice(stored, "L3", "rewritten")

    # A plain copy has no rewrite: asking for one names the version it has, and nothing changes.
    plain = apply_line_edit(stored, "L4", "Ran the release calendar and on-call rota for 4 teams.")
    with pytest.raises(TailorError) as caught:
        apply_line_choice(plain, "L4", "rewritten")
    assert caught.value.code == "invalid_value" and str(caught.value) == "line 'L4' has no rewritten version; use original"
    assert apply_line_choice(plain, "L4", "original") == stored


def test_what_cannot_be_edited() -> None:
    stored = _stored()
    with pytest.raises(TailorError, match="no line 'L99'"):
        apply_line_edit(stored, "L99", EDIT)
    with pytest.raises(TailorError, match="no line 'L2'"):  # an entry heading is copy-only
        apply_line_edit(stored, "L2", "Principal Engineer at Northwind Health")
    for bad, message in (
        (None, "text must be a string"),
        ("   ", "text must not be empty"),
        ("- ", "text must not be empty"),
        ("two\nlines", "text must be one line without control characters"),
        ("x" * (MAX_CUSTOM_TEXT_CHARS + 1), f"text is longer than {MAX_CUSTOM_TEXT_CHARS} characters"),
    ):
        with pytest.raises(TailorError) as caught:
            apply_line_edit(stored, "L3", bad)
        assert caught.value.code == "invalid_value" and str(caught.value) == message
    assert custom_line_text("x" * MAX_CUSTOM_TEXT_CHARS) == "x" * MAX_CUSTOM_TEXT_CHARS


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Reach me at riley@example.test for references.", ["email"]),
        ("Call 555-010-0100 after 5pm.", ["phone"]),
        ("Code samples at github.com/riley-example", ["links"]),
        ("Portfolio: https://riley.example.test/work", ["links"]),
        ("Based at 12 Example Street, Columbus", ["address"]),
        ("Riley Example", ["name"]),
        ("riley@example.test | 555-010-0100", ["email", "phone"]),
    ],
)
def test_a_name_or_contact_line_is_refused_and_nothing_changes(text: str, found: list[str]) -> None:
    stored = _stored()
    assert personal_info_found(text) == found
    with pytest.raises(TailorError) as caught:
        apply_line_edit(stored, "L3", text)
    assert caught.value.code == "personal_info_refused"
    assert f"({', '.join(found)})" in str(caught.value) and "Generate PDF form" in str(caught.value)
    assert text not in str(caught.value), "the refusal never echoes the text"


def test_the_saved_name_is_what_the_name_check_looks_for() -> None:
    # With the header's name known, a line holding it is refused wherever it sits ...
    assert personal_info_found("Mentored by riley   example on the platform team.", names=("Riley Example",)) == ["name"]
    # ... and a short title-cased skills line is not mistaken for a name.
    assert personal_info_found("Apache Kafka", names=("Riley Example",)) == []
    assert personal_info_found("Apache Kafka") == ["name"], "no name known: the strict name shape is the fallback"
    assert personal_info_found(EDIT) == [] and personal_info_found("Processed 50000 claims for the Austin, TX office.") == []


def test_stats_count_an_edit_as_edited() -> None:
    stored = _stored()
    before = tailor_line_stats(stored.result)
    assert before.edited == 0 and "edited" not in before.to_json(), "a result without edits serializes as before"
    after = tailor_line_stats(apply_line_edit(stored, "L3", EDIT).result)
    assert (after.rewritable_lines, after.copied, after.shown_rewritten, after.edited) == (before.rewritable_lines, before.copied - 1, 0, 1)
    assert (after.model_rewrites, after.fallbacks) == (before.model_rewrites, before.fallbacks) == (1, 1), "what the model proposed is still counted"
    assert after.to_json()["edited"] == 1
    assert apply_line_edit(stored, "L3", EDIT).result.rewritten_lines() == ()


def test_the_contract_rejects_a_custom_line_that_claims_a_source() -> None:
    good = apply_line_edit(_stored(), "L3", EDIT)
    payload = _line(good, "L3").to_json()
    for mutate in (
        lambda p: {**p, "refs": [_ref(3, ORIGINAL).to_json()]},
        lambda p: {**p, "edited_from": p},
        lambda p: {**p["edited_from"], "edited_from": p["edited_from"]},  # a copy line never carries edited_from
    ):
        with pytest.raises(FindJobsContractError) as caught:
            TailoredLine.from_json(mutate(payload))
        assert caught.value.code == "invalid_value"
    with pytest.raises(FindJobsContractError):
        TailoredLine.from_json({**payload, "kind": "typed"})
    # 0110-10-05 B: a custom line of an attached edited resume that replaced no line has no edited_from.
    added = TailoredLine.from_json({key: value for key, value in payload.items() if key != "edited_from"})
    assert (added.kind, added.refs, added.edited_from) == ("custom", (), None) and TailoredLine.from_json(added.to_json()) == added
