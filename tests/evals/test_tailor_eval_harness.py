"""Q3 (v0.1.9): the tailor eval harness runs end to end with the fake model, and its
detector catches PLANTED fabrications -- offline.

Integration lane (temp home, the ``bindings.py`` model seam): every tailor
and judge call goes through ``proposal_execution.resolve_model_adapter``
(patched onto ``bindings._test_model_handler``) and the shared
``invoke_json_once`` loop with the packaged ``tailor.md`` / the eval's
``fabrication_judge.md`` -- no live model, nothing written outside
``tmp_path``.

The self-test: with ``TEST_MODEL_FABRICATE_MARKER`` in the posting text the
fixture answers with three planted fabrications (an unsupported number, a
posting-only skill, a resume ref past the end) on both attempts; the
product's validator must reject the whole answer, and each planted line,
checked on its own, must be rejected for ITS reason -- so the eval's
``fabricated_claims == 0`` can never be a vacuous pass.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gigai.scout import proposal_execution
from gigai.scout.find_jobs import bindings
from gigai.scout.find_jobs.contracts import NotAssessedReason
from gigai.scout.tailored_resume import (
    TAILOR_INSTRUCTIONS_DIGEST,
    TailorContext,
    TailorJob,
    TailorValidationError,
    posting_terms,
    resume_lines,
    tailor_once,
    validate_tailored_output,
)

from tests.evals import run_assess_eval as assess_harness
from tests.evals import run_tailor_eval as harness

_SEAM = "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL"


@pytest.fixture(autouse=True)
def _isolate_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proposal_execution, "resolve_model_adapter", proposal_execution.resolve_model_adapter)
    monkeypatch.delenv(_SEAM, raising=False)


def _run(tmp_path: Path, *extra: str) -> dict:
    report = tmp_path / "report.json"
    assert harness.main(["--fake-model", "--report", str(report), "--quiet", *extra]) == 0
    return json.loads(report.read_text(encoding="utf-8"))


def test_fake_model_run_goes_end_to_end_through_the_shipped_path(tmp_path: Path) -> None:
    report = _run(tmp_path, "--max-calls", "3")
    assert report["schema"] == harness.REPORT_SCHEMA
    run = report["run"]
    assert run["fake_model"] is True and run["judge"] is True and run["model_target"] == "ollama_local"
    assert run["instructions_digest"] == TAILOR_INSTRUCTIONS_DIGEST
    assert run["max_calls"] == 3 and len(run["skipped_rows"]) == len(assess_harness.load_labels()) - 3
    # Rule E: excluded label rows are listed, never planned, never scored.
    excluded = [label for label in assess_harness.load_labels(include_excluded=True) if label.excluded]
    assert run["excluded_rows"] == [{"resume_id": label.resume_id, "posting_id": label.posting_id} for label in excluded]
    assert all(not row["excluded"] for row in report["rows"])

    rows = report["rows"]
    assert len(rows) == 3 and all(row["clean_fit"] for row in rows)
    for row in rows:
        assert row["ok"] is True and row["attempts"] == 1 and row["retried"] is False
        # The fixture: header copy R1, summary = R1 rewritten, skills copy R1.
        assert row["sections"] == ["summary", "skills"]
        assert row["copy_lines"] == 2 and row["rewritten_lines"] == 1
        header, summary, skills = row["lines"]
        assert header["where"] == "header[1]" and header["kind"] == "copy" and header["verbatim"] is True
        assert summary["where"] == "summary line 1" and summary["kind"] == "rewritten"
        assert summary["sources"] == [{"label": "R1", "text": header["text"]}]  # every accepted line lists its cited source text
        assert summary["guard_hit"] is False and summary["judge"] == {"supported": True, "unsupported_span": None, "judge_ok": True, "judge_attempts": 1}
        assert skills["kind"] == "copy" and skills["verbatim"] is True
        assert row["fabricated_lines"] == [] and row["judge_calls"] == 1 and row["judge_failures"] == 0
        assert row["markdown"].startswith("# ") and "<!-- R1 -->" in row["markdown"]
        assert row["usage"] == {"input_tokens": 10, "output_tokens": 18, "total_tokens": 28}

    metrics = report["metrics"]
    assert metrics["calls"] == {"planned": len(assess_harness.load_labels()), "made": 3, "max_calls": 3, "stopped_at_cap": True}
    assert metrics["lines"] == {"total": 9, "copy": 6, "rewritten": 3, "answer_refs": 0}
    fab = metrics["fabrication"]
    assert fab["fabricated_claims"] == 0 and fab["fabrication_rate"] == 0.0 and fab["lines"] == []
    assert fab["judge_calls"] == 3 and fab["judge_unsupported"] == 0 and fab["judge_failures"] == 0
    assert metrics["bars"] == {"fabricated_claims_bar": 0, "fabricated_claims_bar_met": True, "invalid_after_retry_bar_met": True}
    assert metrics["reliability"]["invalid_after_retry"] == 0 and metrics["reliability"]["valid_output_rate"] == 1.0
    assert _SEAM not in os.environ


def test_no_judge_run_and_answers_reach_the_row(tmp_path: Path) -> None:
    # A planned (non-excluded) resume with fixed answers: the answers are rendered
    # into the prompt as A <question_id> sources and listed on the row. The fixture
    # model cites cloud:gcp only, which no eval resume has, so no answer ref is
    # expected here (the api-e2e journey proves the answer-ref path).
    answers = harness.load_answers()
    planned = assess_harness.plan_rows(assess_harness.load_labels())
    label = next(item for item in planned if answers.get(item.resume_id))
    report = _run(tmp_path, "--resume", label.resume_id, "--posting", label.posting_id, "--max-calls", "1", "--no-judge")
    assert report["run"]["judge"] is False
    row = report["rows"][0]
    assert row["resume_id"] == label.resume_id
    assert row["answers"] == [item.question_id for item in answers[label.resume_id]]
    assert row["judge_calls"] == 0 and row["lines"][1]["judge"] is None
    assert report["metrics"]["fabrication"]["judge_enabled"] is False


def test_dry_run_plans_rows_without_any_call(capsys: pytest.CaptureFixture[str]) -> None:
    assert harness.main(["--dry-run", "--max-calls", "4"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == len(assess_harness.load_labels())
    assert lines[0].split()[1] == "call" and lines[-1].split()[1] == "skip"


def test_answers_fixture_covers_every_expected_question_id_in_the_labels() -> None:
    answers = harness.load_answers()
    for label in assess_harness.load_labels():
        have = {item.question_id for item in answers.get(label.resume_id, ())}
        missing = set(label.expected_question_ids) - have
        assert not missing, f"{label.resume_id} lacks a fixed answer for {sorted(missing)}"
    for items in answers.values():
        assert all(item.answer.strip() for item in items)


# --- the detector self-test: planted fabrications are rejected ----------------------------------

_POSTING = (
    "Acme is hiring a Software Engineer to build Python services on Kubernetes and Terraform. "
    "Requirements: Python; Kubernetes; Terraform. " + bindings.TEST_MODEL_FABRICATE_MARKER
)
_RESUME = "Software engineer with Python service experience.\nRan a small team.\n"
_JOB = TailorJob(title="Software Engineer", company="Acme", location="Denver, CO", posting_text=_POSTING)
_CTX = TailorContext(resume_lines=resume_lines(_RESUME))


def _fixture_binding(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    config = assess_harness.build_fake_config(home)
    return assess_harness.resolve_binding(config, "ollama_local", home_root=home)


def test_planted_fabrications_are_rejected_by_the_product_on_both_attempts(tmp_path: Path) -> None:
    with assess_harness.seam_env(**{_SEAM: "1"}):
        binding = _fixture_binding(tmp_path)
        try:
            attempt = tailor_once(binding, _JOB, _CTX)
        finally:
            binding.close()
    assert not attempt.ok and attempt.not_assessed_reason is NotAssessedReason.MODEL_OUTPUT_INVALID
    assert attempt.attempts == 2
    assert attempt.validation_error == 'summary line 1 contains the number "8" that appears in none of its cited sources (R1)'


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (
            {"text": bindings.TEST_MODEL_FABRICATED_NUMBER_LINE, "refs": [{"kind": "resume", "line": 1}]},
            'summary line 1 contains the number "8" that appears in none of its cited sources (R1)',
        ),
        (
            {"text": bindings.TEST_MODEL_FABRICATED_TERM_LINE, "refs": [{"kind": "resume", "line": 1}]},
            'summary line 1 contains the posting term "kubernetes" that appears in none of its cited sources (R1)',
        ),
        (
            {"text": "Software engineer with Python service experience.", "refs": [{"kind": "resume", "line": 999}]},
            "summary line 1 cites resume line 999; the resume has 2 lines",
        ),
    ],
)
def test_each_planted_fabrication_is_rejected_for_its_own_reason(line: dict, message: str) -> None:
    with pytest.raises(TailorValidationError) as info:
        validate_tailored_output({"header": [{"copy": 1}], "sections": [{"heading": "summary", "lines": [line]}]}, _JOB, _CTX)
    assert str(info.value) == message


def test_the_evals_own_detector_flags_a_planted_line_and_passes_a_supported_one() -> None:
    terms = posting_terms(_POSTING, exclude=("Software Engineer", "Acme", "Denver, CO"))
    source = "Software engineer with Python service experience."
    planted = harness.detect_line(bindings.TEST_MODEL_FABRICATED_NUMBER_LINE + " " + bindings.TEST_MODEL_FABRICATED_TERM_LINE, [source], terms)
    assert planted["guard_hit"] is True
    assert planted["numeric_hits"] == ["8", "12"] and planted["term_hits"] == ["kubernetes", "terraform"]
    clean = harness.detect_line("Python service engineer.", [source], terms)
    assert clean == {"numeric_hits": [], "term_hits": [], "guard_hit": False}


def test_judge_prompt_starts_with_the_fixture_marker_and_parses_strictly() -> None:
    prompt = harness.render_judge_prompt([("R1", "Built Python services."), ("A cloud:gcp", "cloud gcp Yes.")], "Built services.")
    assert prompt.startswith(bindings.TEST_MODEL_JUDGE_MARKER + "\n")
    assert "SOURCES:\nR1: Built Python services.\nA cloud:gcp: cloud gcp Yes.\n\nCLAIM:\nBuilt services." in prompt
    assert "{{" not in prompt
    assert harness.parse_judge_answer({"supported": False, "unsupported_span": "eight years"}) == {"supported": False, "unsupported_span": "eight years"}
    assert harness.parse_judge_answer({"supported": True, "unsupported_span": "ignored"}) == {"supported": True, "unsupported_span": None}
    with pytest.raises(ValueError):
        harness.parse_judge_answer({"supported": "yes"})


def test_summarize_counts_a_judge_unsupported_line_as_fabricated() -> None:
    accepted = {"where": "summary line 1", "kind": "rewritten", "text": "x", "sources": [{"label": "R1", "text": "x"}], "numeric_hits": [], "term_hits": [], "guard_hit": False, "judge": {"supported": False, "unsupported_span": "x", "judge_ok": True, "judge_attempts": 1}, "fabricated": True}
    row = {
        "resume_id": "r", "posting_id": "p", "clean_fit": True, "excluded": False, "ok": True, "attempts": 1, "retried": False,
        "validation_error": None, "not_assessed_reason": None, "elapsed_seconds": 0.1, "usage": None,
        "lines": [accepted], "copy_lines": 0, "rewritten_lines": 1, "fabricated_lines": [accepted], "judge_calls": 1, "judge_failures": 0,
    }
    invalid = {**row, "ok": False, "attempts": 2, "retried": True, "not_assessed_reason": "model_output_invalid", "validation_error": "bad", "lines": [], "rewritten_lines": 0, "fabricated_lines": [], "judge_calls": 0}
    excluded_row = {**row, "excluded": True, "fabricated_lines": [accepted, accepted]}
    metrics = harness.summarize([row, invalid, excluded_row], planned=2, max_calls=2, judge=True)
    assert metrics["calls"]["made"] == 2, "an excluded row enters no metric"
    assert metrics["fabrication"]["fabricated_claims"] == 1 and metrics["fabrication"]["judge_unsupported"] == 1
    assert metrics["fabrication"]["lines"][0]["where"] == "summary line 1"
    assert metrics["bars"]["fabricated_claims_bar_met"] is False
    assert metrics["reliability"]["invalid_after_retry"] == 1 and metrics["bars"]["invalid_after_retry_bar_met"] is False
    assert metrics["reliability"]["rejections"][0]["validation_error"] == "bad"
