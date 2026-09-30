"""0110-006: the tailor copies by default, a rewrite may not lose facts, the user picks per line.

Pure-function cases: ``tailor_no_loss.lost_items`` rule by rule (and the
equivalents that must NOT count as a loss), the reason check, the settle
pass ``apply_no_loss`` (fallback copies, spans, duplicates, merges, the
summary's prose-only rule, answer-only lines, the length rule and the
dropped-bullet record, ids), the additive stored contract (old results
still parse), ``apply_line_choice``, ``tailor_line_stats`` and the copy
bullet's display in the markdown and the PDF.  The end-to-end symptom
(the operator's Staff and DSAR bullets through ``run_tailored_resume``,
the ``.md`` and the PDF) is in ``test_tailored_resume.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
from pathlib import Path

import pytest

from gigai.scout.find_jobs.contracts import FindJobsContractError
from gigai.scout.tailor_no_loss import lost_items
from gigai.scout.tailored_resume import (
    LENGTH_RULE,
    LineAlternative,
    LineReason,
    MatrixRow,
    TailorError,
    TailorJob,
    TailorResponse,
    TailoredLine,
    TailoredResume,
    apply_line_choice,
    apply_no_loss,
    load_tailor_instructions,
    render_markdown,
    render_tailor_prompt,
    tailor_context,
    tailor_line_stats,
    validate_tailored_output,
)

# --- fixed inputs ------------------------------------------------------------------------

_STAFF = "- Led a team of 7 engineers delivering platform and product systems end to end, owning technical direction, roadmap, and delivery across 3 domains."
_STAFF_WEAK = "Led 7 engineers delivering platform and product systems, with technical direction, roadmap, and delivery across 3 domains."
_DSAR = "- Built the data-subject access request (DSAR) pipeline handling access and deletion requests end to end under CCPA/CPRA and CO privacy requirements."
_DSAR_WEAK = "Built a pipeline for data-subject access and deletion requests under CCPA/CPRA"
_EKS = "- Owns the shared Kubernetes platform (EKS) for 40 services."
_RESUME = (
    "# Kar Ohm\n"  # R1 withheld (name)
    "kar@example.test\n"  # R2 withheld (contact)
    "\n"
    "## Summary\n"  # R3
    "Staff engineer building fault-tolerant distributed systems,\n"  # R4 -> R5
    "owning architecture and delivery of cloud-native architectures on AWS/GCP.\n"  # R5
    "\n"
    "## Experience\n"  # R6
    "**Staff Software Engineer — Guild Education** (2021–present)\n"  # R7
    f"{_STAFF}\n"  # R8
    f"{_DSAR}\n"  # R9
    f"{_EKS}\n"  # R10
    "**Senior Engineer — Northwind** (2012–2016)\n"  # R11: an OLD role (ended 10 years before 2026)
    "- Built the billing service in Go.\n"  # R12
    "- Ran the on-call rotation for 12 hosts.\n"  # R13
    "- Designed the Kafka event bus.\n"  # R14
    "- Mentored four engineers.\n"  # R15
    "- Wrote the incident runbooks.\n"  # R16
    "\n"
    "## Skills\n"  # R17
    "Python, Go, Kubernetes, Advanced SQL\n"  # R18
)
_POSTING = (
    "Acme is hiring a Staff Engineer to lead platform and product systems, privacy deletion requests "
    "and distributed systems on AWS. Requirements: Python; Kubernetes; Go; Kafka."
)
_JOB = TailorJob(title="Staff Engineer", company="Acme", location="Remote", posting_text=_POSTING)
_MATRIX = (MatrixRow("Kubernetes platform ownership", "met"), MatrixRow("Distributed systems", "met"))
_CTX = tailor_context(_RESUME, matrix=_MATRIX)
_TODAY = date(2026, 9, 30)
_SPAN = "Staff engineer building fault-tolerant distributed systems, owning architecture and delivery of cloud-native architectures on AWS/GCP."


def _surface(phrase: str | None = None, requirement: str | None = None) -> dict[str, object]:
    return {"kind": "surface", "requirement": requirement, "posting_phrase": phrase}


def _rewrite(text: str, *lines: int, reason: dict[str, object] | None = None, answers: tuple[str, ...] = ()) -> dict[str, object]:
    refs: list[dict[str, object]] = [{"kind": "resume", "line": line} for line in lines]
    refs += [{"kind": "answer", "question_id": qid} for qid in answers]
    out: dict[str, object] = {"text": text, "refs": refs}
    if reason is not None:
        out["reason"] = reason
    return out


def _experience(*bullets: dict[str, object], heading: int = 7) -> dict[str, object]:
    return {"sections": [{"heading": "experience", "entries": [{"heading_ref": [{"copy": heading}], "bullets": list(bullets)}]}]}


def _settle(payload: dict[str, object], ctx=_CTX, job: TailorJob = _JOB) -> TailoredResume:
    return apply_no_loss(validate_tailored_output(payload, job, ctx), job, ctx, today=_TODAY)


def _bullets(result: TailoredResume) -> tuple[TailoredLine, ...]:
    return result.sections[0].entries[0].bullets


# --- lost_items, rule by rule ------------------------------------------------------------------


def test_the_operators_staff_and_dsar_pairs_lose_exactly_the_uat_items() -> None:
    assert lost_items(_STAFF_WEAK, [_STAFF]) == {"ownership": ["own"], "scope": ["end to end"]}
    # "CO" is a stop word for the posting-term guard; the all-caps rule still names it.
    assert lost_items(_DSAR_WEAK, [_DSAR]) == {"entities": ["co", "dsar"], "scope": ["end to end"]}


@pytest.mark.parametrize(
    ("rewrite", "source", "lost"),
    [
        ("Cut latency for the API.", "Cut latency by 40% for the API.", {"numbers": ["40"]}),
        ("Grew revenue to $1.2 million.", "Grew revenue to $1.2M and 3 regions.", {"numbers": ["3"]}),
        ("Owns the shared EKS platform.", "Owns the shared Kubernetes platform (EKS).", {"entities": ["kubernetes"]}),
        ("Built production classification models.", "Built production statistical and ML models.", {"entities": ["ml"]}),
        ("Ran the migration with the team.", "Owned the migration end-to-end.", {"ownership": ["own"], "scope": ["end to end"]}),
        ("Built low-latency backend services.", "Built highly available, low-latency distributed backend services.", {"scope": ["distributed", "highly available"]}),
        ("Shipped the payments API.", "Shipped the payments API to production at scale.", {"scope": ["at scale", "production"]}),
        ("Mentored engineers.", "Mentored engineers across 4 teams.", {"numbers": ["4"], "scope": ["across"]}),
    ],
)
def test_each_rule_names_what_the_rewrite_dropped(rewrite: str, source: str, lost: dict[str, list[str]]) -> None:
    assert lost_items(rewrite, [source]) == lost


@pytest.mark.parametrize(
    ("rewrite", "source"),
    [
        ("Owned the payments migration.", "Owns the payments migration."),  # same ownership family
        ("Ran Kubernetes clusters backed by Postgres.", "Ran k8s clusters backed by PostgreSQL."),  # aliases; "k8s" states no number 8
        ("Built the end-to-end pipeline.", "Built the pipeline end to end."),  # a dash is a space
        ("Drove adoption across the organization.", "Drove org-wide adoption."),  # equivalent scope
        ("Services with high availability.", "Highly available services."),
        ("Deploys daily.", "Deploys every day."),
        ("Realtime scoring.", "Real-time scoring."),
        ("Moved to a fully managed queue.", "Moved to a fully managed queue."),
        ("Reduced cost by 20% to drive efficiency.", "Reduced cost by 20%."),  # "drive" is not an ownership family
        ("Kafka and Go services, built by me.", "Built Go and Kafka services."),  # a pure reorder
        ("Cut p99 latency by 40%.", "Cut p99 latency by 40%."),
    ],
)
def test_equivalents_and_reorders_are_not_a_loss(rewrite: str, source: str) -> None:
    assert lost_items(rewrite, [source]) == {}


def test_fully_managed_is_not_people_management_but_managed_a_team_is() -> None:
    assert lost_items("Moved to a queue.", ["Moved to a fully managed queue."]) == {}
    assert lost_items("Ran a team of 5.", ["Managed a team of 5."]) == {"ownership": ["manage"]}


def test_the_skills_rule_allows_a_reorder_and_names_a_dropped_skill() -> None:
    source = "Skills: Python, Go, Kubernetes, Advanced SQL"
    assert lost_items("Kubernetes, Go, Python, Advanced SQL", [source], skills=True) == {}
    assert lost_items("Kubernetes, Go, Python", [source], skills=True) == {"entities": ["advanced", "sql"], "skills": ["advanced sql"]}
    # Off by default: a bullet is judged by the other rules only.
    assert "skills" not in lost_items("Kubernetes, Go, Python", [source])


# --- the reason check and the settle pass ------------------------------------------------------------


def test_a_lossless_rewrite_with_an_anchored_reason_is_shown_with_its_original_as_the_alternative() -> None:
    lossless = "Owns the shared Kubernetes platform (EKS) for 40 services."
    result = _settle(_experience(_rewrite(lossless, 10, reason=_surface(requirement="M1"))))
    (line,) = _bullets(result)
    assert line.kind == "rewritten" and line.text == lossless and line.origin == "model"
    assert line.reason == LineReason("surface", "M1", None)
    assert line.alternative == LineAlternative("copy", _EKS, line.refs)
    assert result.sections[0].entries[0].heading[0].origin == "model"


@pytest.mark.parametrize(
    "reason",
    [
        None,  # no reason at all
        {"kind": "because"},  # malformed: parsed as no reason, never a rejection
        _surface(requirement="M9"),  # no such matrix row
        _surface(phrase="Rust and Elixir"),  # the posting never says it
        _surface(phrase="x" * 61),  # over the 60-character bound: dropped at parse
        _surface(requirement="M2"),  # "Distributed systems" shares no word with the line and its source
        {"kind": "answer", "requirement": "M1", "posting_phrase": None},  # an answer reason with no answer ref
    ],
)
def test_a_missing_or_unanchored_reason_falls_back_to_the_original_line(reason: dict[str, object] | None) -> None:
    lossless = "Owns the shared Kubernetes platform (EKS) for 40 services."
    result = _settle(_experience(_rewrite(lossless, 10, reason=reason)))
    (line,) = _bullets(result)
    assert line.kind == "copy" and line.text == _EKS and line.origin == "fallback"
    assert line.alternative is not None and line.alternative.text == lossless
    assert line.alternative.reason_invalid is True and line.alternative.lost_dict() == {}


def test_a_valid_reason_stores_only_the_anchors_that_hold_and_at_most_sixty_characters_of_posting() -> None:
    lossless = "Owns the shared Kubernetes platform (EKS) for 40 services."
    result = _settle(_experience(_rewrite(lossless, 10, reason=_surface(phrase="Rust", requirement="m1"))))
    assert _bullets(result)[0].reason == LineReason("surface", "M1", None)
    phrase = "lead platform  and product\nsystems"  # whitespace and case never matter
    result = _settle(_experience(_rewrite(lossless, 10, reason=_surface(phrase=phrase))))
    assert _bullets(result)[0].reason == LineReason("surface", None, phrase)
    assert _POSTING not in json.dumps(result.to_json())


def test_a_lossy_rewrite_falls_back_to_a_copy_of_its_span_and_keeps_what_it_lost() -> None:
    result = _settle(_experience(_rewrite(_STAFF_WEAK, 8, reason=_surface(phrase="platform and product systems"))))
    (line,) = _bullets(result)
    assert line.kind == "copy" and line.text == _STAFF and line.refs[0].line == 8 and line.origin == "fallback"
    assert line.alternative is not None and line.alternative.kind == "rewritten" and line.alternative.text == _STAFF_WEAK
    assert line.alternative.lost_dict() == {"ownership": ["own"], "scope": ["end to end"]}
    assert line.alternative.reason == LineReason("surface", None, "platform and product systems")
    assert line.alternative.reason_invalid is False
    assert line.to_json()["alternative"]["lost"] == {"ownership": ["own"], "scope": ["end to end"]}


def test_a_summary_rewrite_owes_its_prose_lines_only_and_falls_back_to_the_whole_wrapped_span() -> None:
    reason = {"kind": "summary", "requirement": "M2", "posting_phrase": None}
    # Cites the wrapped summary (R4+R5) and one role line (R8); keeps every item of the prose, not of R8.
    keeps = "Staff engineer building fault-tolerant distributed systems, owning architecture and delivery of cloud-native architectures on AWS/GCP; led a team of 7 engineers."
    kept = _settle({"sections": [{"heading": "summary", "lines": [_rewrite(keeps, 4, 8, reason=reason)]}]})
    (line,) = kept.sections[0].lines
    assert line.kind == "rewritten" and line.alternative is not None and line.alternative.text == _SPAN
    # The UAT summary: "fault tolerant" and the owning clause dropped -> the candidate's own summary.
    weak = "Staff engineer building distributed systems on AWS/GCP."
    fell = _settle({"sections": [{"heading": "summary", "lines": [_rewrite(weak, 4, reason=reason)]}]})
    (line,) = fell.sections[0].lines
    assert line.kind == "copy" and line.text == _SPAN and line.refs[0].continued_lines == (5,)
    assert line.alternative is not None and line.alternative.lost_dict() == {"ownership": ["own"], "scope": ["fault tolerant"]}
    # "architectures" is a noun: only the verb forms ("architected") are the architect family.
    assert lost_items("Staff engineer.", ["Staff engineer; cloud-native architectures."]) == {}
    assert lost_items("Staff engineer.", ["Staff engineer; architected the platform."]) == {"ownership": ["architect"]}


def test_a_summary_sentence_citing_only_a_role_and_an_answer_only_line_with_no_reason_are_dropped_and_recorded() -> None:
    ctx = tailor_context(_RESUME, matrix=_MATRIX)
    payload = {
        "sections": [
            {"heading": "summary", "lines": [{"copy": 4}, _rewrite("Led a team of 7 engineers.", 8)]},
        ]
    }
    result = _settle(payload, ctx)
    summary = result.sections[0]
    assert [line.text for line in summary.lines] == [_SPAN]  # the copy stays; the role-only sentence has no original to show
    (dropped,) = summary.dropped
    assert dropped.kind == "rewritten" and dropped.text == "Led a team of 7 engineers." and dropped.reason_invalid is True
    # A section whose every line is dropped renders nothing (and keeps its record).
    empty = replace(result, sections=(replace(summary, lines=()),))
    assert render_markdown(empty) == "\n"


def test_a_fallback_never_repeats_a_line_the_entry_already_copies() -> None:
    result = _settle(_experience({"copy": 8}, _rewrite(_STAFF_WEAK, 8, reason=_surface(phrase="platform"))))
    (line,) = _bullets(result)
    assert line.kind == "copy" and line.text == _STAFF and line.origin == "fallback"
    assert line.alternative is not None and line.alternative.dropped_duplicate is True and line.alternative.text == _STAFF_WEAK
    stats = tailor_line_stats(result)
    assert stats.fallbacks == 1 and stats.fallbacks_by_rule["dropped_duplicate"] == 1 and stats.model_rewrites == 1


def test_a_bullet_merging_two_resume_lines_falls_back_to_both_originals() -> None:
    merged = "Led a team of 7 engineers delivering platform and product systems end to end, owning technical direction, roadmap, and delivery across 3 domains; owns the shared Kubernetes platform (EKS) for 40 services."
    result = _settle(_experience(_rewrite(merged, 8, 10, reason=_surface(requirement="M1"))))
    first, second = _bullets(result)
    assert (first.text, second.text) == (_STAFF, _EKS) and first.origin == second.origin == "fallback"
    assert first.alternative is not None and first.alternative.merge is True and first.alternative.lost_dict() == {}
    assert second.alternative is None


def test_a_copy_line_outside_a_heading_copies_its_wrapped_span_but_an_entry_heading_never_expands() -> None:
    wrapped = "- Terraform-managed AWS landing zone with\nguardrails across 40 accounts.\n"
    resume = "## Experience\n**Platform Engineer — Hexa** (2021–present)\n" + wrapped
    ctx = tailor_context(resume)
    result = _settle(_experience({"copy": 3}, heading=2), ctx)
    (line,) = _bullets(result)
    assert line.text == "- Terraform-managed AWS landing zone with guardrails across 40 accounts." and line.refs[0].continued_lines == (4,)
    # A heading_ref copy is the one line: "Acme Corp — Engineer (2019–2023)" runs on into R2 here, yet stays one line.
    ctx = tailor_context("Acme Corp — Engineer (2019–2023)\nBuilt Python services.\n")
    heading = validate_tailored_output(_experience(heading=1), _JOB, ctx).sections[0].entries[0].heading[0]
    assert heading.text == "Acme Corp — Engineer (2019–2023)" and heading.refs[0].continued_lines == ()


def test_the_skills_line_may_be_reordered_never_cut() -> None:
    reason = _surface(phrase="Kubernetes")
    reordered = _settle({"sections": [{"heading": "skills", "lines": [_rewrite("Kubernetes, Python, Go, Advanced SQL", 18, reason=reason)]}]})
    assert reordered.sections[0].lines[0].kind == "rewritten"
    cut = _settle({"sections": [{"heading": "skills", "lines": [_rewrite("Kubernetes, Python, Go", 18, reason=reason)]}]})
    (line,) = cut.sections[0].lines
    assert line.kind == "copy" and line.text == "Python, Go, Kubernetes, Advanced SQL"
    assert line.alternative is not None and line.alternative.lost_dict()["skills"] == ["advanced sql"]


# --- the length rule and the dropped-bullet record (Q4) ------------------------------------------------


def test_an_old_role_keeps_its_first_three_bullets_whole_and_every_unshown_bullet_is_recorded() -> None:
    assert LENGTH_RULE.max_pages == 2 and LENGTH_RULE.old_role_years == 8 and LENGTH_RULE.old_role_bullets == 3
    payload = {
        "sections": [
            {
                "heading": "experience",
                "entries": [
                    {"heading_ref": [{"copy": 7}], "bullets": [{"copy": 8}]},  # the current role shows R8 only
                    {"heading_ref": [{"copy": 11}], "bullets": [{"copy": 14}, {"copy": 12}, {"copy": 15}, {"copy": 13}]},
                ],
            }
        ]
    }
    result = _settle(payload)
    current, old = result.sections[0].entries
    assert [line.refs[0].line for line in current.bullets] == [8]
    assert [(item.kind, item.refs[0].line) for item in current.dropped] == [("copy", 9), ("copy", 10)]
    # The old role: the model's first three (its posting order), dropped WHOLE, never shortened.
    assert [line.refs[0].line for line in old.bullets] == [14, 12, 15]
    assert [item.text for item in old.dropped] == ["- Ran the on-call rotation for 12 hosts.", "- Wrote the incident runbooks."]
    assert tailor_line_stats(result).dropped_bullets == 4
    # A current role is never trimmed, whatever its length.
    assert not _is_old("**Staff — Guild** (2014–present)") and _is_old("**Engineer — X** (2010–2017)") and not _is_old("**Engineer — X** (2019–2023)")
    assert not _is_old("**Engineer — X**")


def _is_old(heading: str) -> bool:
    from gigai.scout.tailored_resume import _is_old_role

    return _is_old_role((TailoredLine("copy", heading, ()),), _TODAY)


def test_the_prompt_states_the_length_rule_from_the_one_constant() -> None:
    prompt = render_tailor_prompt(_JOB, _CTX)
    assert "LENGTH: the resume fits in 2 pages; a role that ended more than 8 years ago keeps only its 3 most posting-relevant bullets" in prompt
    assert "{{" not in prompt and "{{max_pages}}" in load_tailor_instructions()


# --- ids, the stored contract, the per-line choice, the counts -------------------------------------------


def _mixed() -> TailoredResume:
    return _settle(
        {
            "sections": [
                {"heading": "summary", "lines": [{"copy": 4}]},
                {
                    "heading": "experience",
                    "entries": [
                        {
                            "heading_ref": [{"copy": 7}],
                            "bullets": [
                                _rewrite(_STAFF_WEAK, 8, reason=_surface(phrase="platform")),
                                _rewrite("Owns the shared Kubernetes platform (EKS) for 40 services.", 10, reason=_surface(requirement="M1")),
                                {"copy": 9},
                            ],
                        }
                    ],
                },
                {"heading": "skills", "lines": [{"copy": 18}]},
            ]
        }
    )


def test_every_body_line_gets_an_id_in_document_order_and_the_result_round_trips() -> None:
    result = _mixed()
    ids = [line.id for section in result.sections for line in section.all_lines()]
    assert ids == [f"L{n}" for n in range(1, 7)]
    assert TailoredResume.from_json(json.loads(json.dumps(result.to_json()))) == result


def test_an_older_stored_result_without_the_new_keys_still_parses() -> None:
    fixture = Path(__file__).parent / "fixtures" / "resume_pdf_result.json"
    old = TailoredResume.from_json(json.loads(fixture.read_text()))
    assert all(line.id is None and line.origin is None and line.alternative is None for s in old.sections for line in s.all_lines())
    assert TailoredResume.from_json(old.to_json()) == old and "origin" not in json.dumps(old.to_json())


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda line: {**line, "origin": "robot"}, "bad_enum"),
        (lambda line: {**line, "surprise": 1}, "unknown_key"),
        (lambda line: {**line, "alternative": {**line["alternative"], "lost": {"vibes": ["x"]}}}, "invalid_value"),
        (lambda line: {**line, "alternative": {**line["alternative"], "merge": "yes"}}, "wrong_type"),
        (lambda line: {**line, "reason": {"kind": "surface", "posting_phrase": "x" * 61}}, "invalid_value"),
    ],
)
def test_the_new_keys_are_still_a_closed_contract(mutate, code: str) -> None:
    line = _mixed().sections[1].entries[0].bullets[0].to_json()
    with pytest.raises(FindJobsContractError) as info:
        TailoredLine.from_json(mutate(line))
    assert info.value.code == code


def _response(result: TailoredResume) -> TailorResponse:
    from gigai.scout.find_jobs.contracts import ModelTarget, Producer

    from gigai.scout.find_jobs.assess_contracts import ResolvedJob, ResolvedResume
    from gigai.scout.tailored_resume import TAILOR_INSTRUCTIONS_DIGEST, TailorSources

    digest = "sha256:" + "a" * 64
    return TailorResponse(
        job=ResolvedJob.from_json(
            {
                "schema_version": ResolvedJob.schema_version, "job_identity": "text:" + digest, "source_url": None,
                "normalized_url": None, "fetch_kind": "pasted", "title": "Staff Engineer", "company": "Acme",
                "location": "", "text": "", "text_sha256": digest,
            }
        ),
        resume=ResolvedResume(None, None, digest),
        sources=TailorSources(digest, 18, {}, None),
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


def test_apply_line_choice_swaps_a_line_with_its_alternative_and_back_idempotently() -> None:
    stored = _response(_mixed())
    fallback, rewrite, _dsar = stored.result.sections[1].entries[0].bullets
    assert (fallback.id, fallback.kind, rewrite.id, rewrite.kind) == ("L3", "copy", "L4", "rewritten")

    # "Use rewrite anyway" on the fallback: the rejected rewrite is shown, the pair keeps its lost items.
    chosen = apply_line_choice(stored, "L3", "rewritten")
    line = chosen.result.sections[1].entries[0].bullets[0]
    assert line.id == "L3" and line.kind == "rewritten" and line.text == _STAFF_WEAK and line.origin == "user"
    assert line.reason == LineReason("surface", None, "platform")
    assert line.alternative is not None and line.alternative.kind == "copy" and line.alternative.text == _STAFF
    assert line.alternative.lost_dict() == {"ownership": ["own"], "scope": ["end to end"]}
    assert f"- {_STAFF_WEAK} <!-- R8 -->" in chosen.markdown and chosen.updated_at == stored.updated_at
    assert apply_line_choice(chosen, "L3", "rewritten") is chosen  # already shown: no-op

    # Undo: the original is back, with the rewrite as its alternative again.
    undone = apply_line_choice(chosen, "L3", "original")
    back = undone.result.sections[1].entries[0].bullets[0]
    assert back.kind == "copy" and back.text == _STAFF and back.alternative is not None and back.alternative.text == _STAFF_WEAK
    assert back.alternative.lost_dict() == {"ownership": ["own"], "scope": ["end to end"]} and back.origin == "user"
    assert f"- {_STAFF[2:]} <!-- R8 -->" in undone.markdown

    # "Keep original" on a shown rewrite.
    kept = apply_line_choice(stored, "L4", "original")
    assert kept.result.sections[1].entries[0].bullets[1].text == _EKS
    assert tailor_line_stats(kept.result).kept_original_by_user == 1

    # A line with no alternative, and an unknown id / choice.
    assert apply_line_choice(stored, "L5", "rewritten") is stored
    for line_id, use in (("L99", "original"), ("L3", "both")):
        with pytest.raises(TailorError) as info:
            apply_line_choice(stored, line_id, use)
        assert info.value.code == "invalid_value"


def test_tailor_line_stats_count_from_the_stored_result_alone() -> None:
    stats = tailor_line_stats(_mixed())
    assert stats.to_json() == {
        "rewritable_lines": 5,  # summary copy, three bullets, skills copy (the entry heading is not rewritable)
        "shown_rewritten": 1,
        "copied": 4,
        "answer_only_lines": 0,
        "model_rewrites": 2,
        "fallbacks": 1,
        "fallbacks_by_rule": {"numbers": 0, "entities": 0, "ownership": 1, "scope": 1, "skills": 0, "reason_invalid": 0, "dropped_duplicate": 0, "merge": 0},
        "kept_original_by_user": 0,
        "dropped_bullets": 0,
        "model_rewrite_rate": 0.4,
        "final_rewrite_rate": 0.2,
    }


# --- the copy bullet prints once, in the markdown and the PDF -------------------------------------------


def test_a_copied_or_fallback_bullet_prints_without_its_own_marker_in_the_markdown_and_the_pdf() -> None:
    import io
    from datetime import datetime, timezone

    from pypdf import PdfReader

    from gigai.scout.resume_display import PdfHeader
    from gigai.scout.resume_pdf import render_pdf

    result = _mixed()
    markdown = render_markdown(result)
    assert f"- {_STAFF[2:]} <!-- R8 -->\n" in markdown and f"- {_DSAR[2:]} <!-- R9 -->\n" in markdown and "- - " not in markdown
    data = render_pdf(result, PdfHeader("Kar Ohm"), company="Acme", timestamp=datetime(2026, 9, 30, tzinfo=timezone.utc))
    text = " ".join("\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages).split())
    assert _STAFF[2:] in text and _DSAR[2:] in text and _STAFF_WEAK not in text


def test_the_fixture_models_lossy_marker_weakens_any_resume_and_the_settle_pass_restores_the_line() -> None:
    from gigai.scout.find_jobs import bindings

    resume = "Software engineer with Python service experience.\n"
    ctx = tailor_context(resume)
    job = TailorJob("Software Engineer", "Acme", "", "Acme is hiring a Software Engineer. Python. " + bindings.TEST_MODEL_LOSSY_MARKER)
    reply = bindings._test_model_tailor_reply(render_tailor_prompt(job, ctx))
    other = next(section for section in reply["sections"] if section["heading"] == "other")
    assert other["lines"][0]["text"] == "Software engineer with service experience."  # the named word dropped
    result = apply_no_loss(validate_tailored_output(reply, job, ctx), job, ctx, today=_TODAY)
    (line,) = next(section for section in result.sections if section.heading == "other").lines
    assert line.kind == "copy" and line.text == resume.strip() and line.origin == "fallback"
    assert line.alternative is not None and line.alternative.lost_dict() == {"entities": ["python"]}
    # Without the marker every fixture rewrite carries an anchored reason and is kept.
    plain = TailorJob("Software Engineer", "Acme", "", "Acme is hiring a Software Engineer. Python.")
    reply = bindings._test_model_tailor_reply(render_tailor_prompt(plain, ctx))
    settled = apply_no_loss(validate_tailored_output(reply, plain, ctx), plain, ctx, today=_TODAY)
    assert [section.heading for section in settled.sections] == ["summary", "skills"]
    assert settled.sections[0].lines[0].kind == "rewritten" and settled.sections[0].lines[0].origin == "model"
