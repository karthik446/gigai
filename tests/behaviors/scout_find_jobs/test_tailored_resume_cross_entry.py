"""uat-bug-036: the cross-entry citation guard, built from the two lines the
tailor-confirm run flagged (``2026-09-29-tailor-confirm``).

- r1-ml-staff summary "Built fraud-scoring models and real-time feature
  infrastructure ..." cites R12 (Fintra Labs: fraud models) + R8 (Northwind:
  a feature store for ranking models): two roles in one claim.  The guard
  catches this.
- cf-analytics-engineer-pl bullet "... pair-debug with teammates in shared VS
  Code sessions" cites ONE line (R11) and fuses two clauses inside it.  That
  is a same-entry fusion: the deterministic guard does NOT catch it (it is the
  prompt's framing sentence and the judge's job); a test pins that honestly.

Resumes are read from the eval fixtures; no model is called.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.scout.find_jobs.contracts import NotAssessedReason
from gigai.scout.tailored_resume import (
    TailorContext,
    TailorJob,
    TailorValidationError,
    check_single_entry,
    render_tailor_prompt,
    resume_entries,
    resume_lines,
    tailor_once,
    validate_tailored_output,
)

_FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "resumes"


def _ctx(name: str) -> tuple[TailorContext, tuple[str, ...]]:
    text = (_FIXTURES / f"{name}.md").read_text(encoding="utf-8")
    lines = resume_lines(text)
    return TailorContext(resume_lines=lines, entries=resume_entries(text)), lines


def _n(lines: tuple[str, ...], fragment: str) -> int:
    (number,) = [i for i, line in enumerate(lines, 1) if fragment in line]
    return number


_JOB = TailorJob(title="Staff ML Engineer", company="Affirm", location="Remote", posting_text="Build ML systems.")
_MERGE = "Built fraud-scoring models and real-time feature infrastructure, and worked on online inference."


def _summary(text: str, *numbers: int) -> dict[str, object]:
    return {
        "header": [{"copy": 1}],
        "sections": [{"heading": "summary", "lines": [{"text": text, "refs": [{"kind": "resume", "line": n} for n in numbers]}]}],
    }


def test_entries_are_derived_from_the_resume_headings() -> None:
    ctx, lines = _ctx("r1-ml-staff")
    northwind, fintra, cascade = (_n(lines, key) for key in ("Northwind Analytics**", "Fintra Labs**", "Cascade Data Co.**"))
    feature_store, fraud = _n(lines, "real-time feature store"), _n(lines, "fraud-scoring")
    assert ctx.entries[feature_store] == northwind and ctx.entries[northwind] == northwind
    assert ctx.entries[fraud] == fintra
    assert ctx.entries[_n(lines, "NLP pipelines")] == cascade
    # Section prose (name, summary, skills, education headings and their lines) is in no entry.
    assert not {1, _n(lines, "Staff-level ML engineer"), _n(lines, "Python, PyTorch"), _n(lines, "University of Waterloo")} & set(ctx.entries)
    # A resume without entry headings keys nothing: the guard cannot fire.
    assert resume_entries("Built X.\nBuilt Y.\n## Skills\nGo") == {}


def test_fail_before_the_confirm_run_r12_r8_summary_merge_is_rejected_as_cross_entry_citation() -> None:
    ctx, lines = _ctx("r1-ml-staff")
    fraud, feature_store = _n(lines, "fraud-scoring"), _n(lines, "real-time feature store")
    payload = _summary(_MERGE, fraud, feature_store)
    # Fail-before: without the entry map (the pre-036 context) the line is accepted.
    assert validate_tailored_output(payload, _JOB, TailorContext(resume_lines=lines)).sections[0].lines[0].text == _MERGE
    with pytest.raises(TailorValidationError) as info:
        validate_tailored_output(payload, _JOB, ctx)
    message = str(info.value)
    assert message.startswith("summary line 1 is a cross_entry_citation:")
    assert f'R{fraud} in "Senior ML Engineer — Fintra Labs' in message
    assert f'R{feature_store} in "Staff ML Engineer — Northwind Analytics' in message


def test_a_bullet_citing_two_roles_is_rejected_too() -> None:
    ctx, lines = _ctx("r1-ml-staff")
    payload = {
        "header": [{"copy": 1}],
        "sections": [
            {
                "heading": "experience",
                "entries": [
                    {
                        "heading_ref": [{"copy": _n(lines, "Northwind Analytics**")}],
                        "bullets": [{"text": _MERGE, "refs": [{"kind": "resume", "line": _n(lines, "fraud-scoring")}, {"kind": "resume", "line": _n(lines, "real-time feature store")}]}],
                    }
                ],
            }
        ],
    }
    with pytest.raises(TailorValidationError, match="experience entry 1 bullet 1 is a cross_entry_citation"):
        validate_tailored_output(payload, _JOB, ctx)


def test_several_lines_of_one_entry_are_accepted() -> None:
    ctx, lines = _ctx("r1-ml-staff")
    feature_store, online = _n(lines, "real-time feature store"), _n(lines, "online inference")
    text = "Led a real-time feature store serving 200k QPS and owned the move from batch to online inference."
    result = validate_tailored_output(_summary(text, feature_store, online), _JOB, ctx)
    assert [ref.line for ref in result.sections[0].lines[0].refs] == [feature_store, online]


def test_summary_rule_section_prose_and_answers_never_conflict_but_two_roles_do() -> None:
    ctx, lines = _ctx("r1-ml-staff")
    summary_line, feature_store, fraud = _n(lines, "Staff-level ML engineer"), _n(lines, "real-time feature store"), _n(lines, "fraud-scoring")
    # A summary line (section prose) plus ONE role is fine: the summary is the candidate's own generalisation.
    check_single_entry("summary line 1", _refs(ctx, summary_line, feature_store), ctx)
    # Prose + two roles is still two roles.
    with pytest.raises(TailorValidationError, match="cross_entry_citation"):
        check_single_entry("summary line 1", _refs(ctx, summary_line, feature_store, fraud), ctx)


def _refs(ctx: TailorContext, *numbers: int):
    from gigai.scout.tailored_resume import SourceRef

    return tuple(SourceRef("resume", n, None, ctx.resume_lines[n - 1]) for n in numbers)


def test_the_same_entry_fusion_r11_shared_vs_code_sessions_is_not_caught_by_the_guard() -> None:
    """Honest limit: R11 fuses "VS Code ... daily IDE" and "pair-debug over shared sessions" inside ONE
    entry and ONE cited line.  The cross-entry guard passes it; the tailor prompt's framing sentence and
    the judge are what address it."""

    ctx, lines = _ctx("cf-analytics-engineer-pl")
    r11 = _n(lines, "shared sessions")
    text = "Use GitHub pull requests, branch protection, code review and CI checks on every dbt change; pair-debug with teammates in shared VS Code sessions."
    result = validate_tailored_output(_summary(text, r11), _JOB, ctx)
    assert result.sections[0].lines[0].text == text


# --- the retry path ------------------------------------------------------------------------


class _Port:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []
        self.name = "fixture"
        self.timed_out = False

    def invoke(self, request):
        self.prompts.append(request.prompt)
        return InvocationResult(
            status="success", output_text=self._outputs.pop(0), resolved_model="fixture", raw_usage={},
            normalized_usage=NormalizedUsage(1, 1, 2), cost_status="unavailable",
        )


class _Binding:
    def __init__(self, outputs: list[str]) -> None:
        self.port = _Port(outputs)

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        pass


def test_a_cross_entry_line_feeds_its_named_reason_into_the_one_retry_then_fails_like_any_guard() -> None:
    ctx, lines = _ctx("r1-ml-staff")
    fraud, feature_store, online = _n(lines, "fraud-scoring"), _n(lines, "real-time feature store"), _n(lines, "online inference")
    bad = json.dumps(_summary(_MERGE, fraud, feature_store))
    good = json.dumps(_summary("Owned the move from batch to online inference for the core recommender (PyTorch, Triton).", online))

    binding = _Binding([bad, good])
    attempt = tailor_once(binding, _JOB, ctx)
    assert attempt.ok and attempt.attempts == 2
    assert "rejected by the validator: summary line 1 is a cross_entry_citation:" in binding.port.prompts[1]

    binding = _Binding([bad, bad])
    attempt = tailor_once(binding, _JOB, ctx)
    assert not attempt.ok and attempt.attempts == 2
    assert attempt.not_assessed_reason is NotAssessedReason.MODEL_OUTPUT_INVALID
    assert attempt.validation_error and "cross_entry_citation" in attempt.validation_error


def test_the_prompt_carries_the_framing_sentence_and_explains_the_named_reason() -> None:
    ctx, _ = _ctx("r1-ml-staff")
    prompt = render_tailor_prompt(_JOB, ctx)
    assert prompt.count("Never combine facts from different roles or projects into one claim.") == 1
    framing = prompt[prompt.index("FRAMING:") : prompt.index("SECTIONS:")]
    assert framing.rstrip().endswith("Never combine facts from different roles or projects into one claim.")
    retry = render_tailor_prompt(_JOB, ctx, "summary line 1 is a cross_entry_citation: x")
    assert '"cross_entry_citation" means one rewritten line cited resume lines from different roles or projects' in retry
