"""P7 (v0.1.9): the eval harness runs end to end through the shipped path with the fake model.

Integration lane (temp home, the ``bindings.py`` model/Jev seams): every call
goes through ``proposal_execution.resolve_model_adapter`` (patched by
``bindings._patch_test_model_transport`` onto ``bindings._test_model_handler``)
and ``assessment_core.assess_once`` with the packaged ``assess.md`` -- no live
model, no live Jev, nothing written outside ``tmp_path``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gigai.scout import proposal_execution
from gigai.scout.assessment_core import INSTRUCTIONS_DIGEST

from tests.evals import run_assess_eval as harness

_SEAMS = ("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "GIGAI_SCOUT_FIND_JOBS_TEST_JEV")


@pytest.fixture(autouse=True)
def _isolate_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    # The transport patch installs itself on the module attribute for the
    # process lifetime; re-setting the attribute through monkeypatch makes
    # pytest restore the original after each test.
    monkeypatch.setattr(proposal_execution, "resolve_model_adapter", proposal_execution.resolve_model_adapter)
    for name in _SEAMS:
        monkeypatch.delenv(name, raising=False)


def _run(tmp_path: Path, *extra: str) -> dict:
    report = tmp_path / "report.json"
    assert harness.main(["--fake-model", "--report", str(report), "--quiet", *extra]) == 0
    return json.loads(report.read_text(encoding="utf-8"))


def test_fake_model_run_goes_end_to_end_through_the_shipped_path(tmp_path: Path) -> None:
    report = _run(tmp_path, "--max-calls", "3")
    assert report["schema"] == "gigai-assess-eval-report:1"
    run = report["run"]
    assert run["fake_model"] is True and run["model_target"] == "ollama_local"
    assert run["adapter_target"] == "ollama_local"
    assert run["instructions_digest"] == INSTRUCTIONS_DIGEST
    assert run["max_calls"] == 3 and len(run["skipped_rows"]) == len(harness.load_labels()) - 3

    rows = report["rows"]
    assert len(rows) == 3 and all(row["clean_fit"] for row in rows)
    for row in rows:
        assert row["ok"] is True and row["attempts"] == 1 and row["retried"] is False
        # bindings._test_model_handler's fixed answer: pending on one cloud:gcp question.
        assert row["verdict"] == "pending_user_answers"
        assert row["question_ids"] == ["cloud:gcp"] and row["question_ids_normalized"] == ["cloud:gcp"]
        assert row["expected_hits_normalized"] == [] and row["expected_hits_exact"] == []
        assert [item["status"] for item in row["matrix"]] == ["met", "unclear"]
        assert row["usage"] == {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
        assert row["agreement"] is False

    metrics = report["metrics"]
    assert metrics["calls"] == {"planned": len(harness.load_labels()), "made": 3, "max_calls": 3, "stopped_at_cap": True}
    assert metrics["clean_fit"]["matched"] == 0 and len(metrics["clean_fit"]["failures"]) == 3
    assert metrics["clean_fit"]["failures"][0]["questions"][0]["question_id"] == "cloud:gcp"
    assert metrics["false_asks"]["clean_fit_questions"] == 3 and metrics["false_asks"]["bar_zero_met"] is False
    assert metrics["reliability"]["valid_output_rate"] == 1.0
    assert metrics["reliability"]["invalid_after_retry"] == 0 and metrics["reliability"]["invalid_after_retry_bar_met"] is True
    assert metrics["reliability"]["model_cost_usd"] == "unavailable"
    assert metrics["question_recall"]["hit_normalized"] == 0 and metrics["question_recall"]["recall_normalized"] is None
    assert metrics["jev"] is None
    # The seam env vars were restored after the run.
    assert all(name not in os.environ for name in _SEAMS)


def _stored_row(expected_ids: tuple[str, ...], observed: tuple[str, ...]) -> dict:
    return {
        "resume_id": "r1-ml-staff", "posting_id": "gh:affirm:7822387003", "expected_verdict": "pending_user_answers",
        "expected_question_ids": list(expected_ids), "clean_fit": False, "uncertain": False, "excluded": False,
        "ok": True, "attempts": 1, "retried": False, "validation_error": None, "not_assessed_reason": None,
        "elapsed_seconds": 1.0, "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "verdict": "pending_user_answers",
        "questions": [{"question_id": item, "question": f"{item}?", "requirement": None} for item in observed],
        "matrix": [], "not_a_match_reason": None,
    }


def test_normalized_recall_counts_vocabulary_drift_as_exact() -> None:
    """assess-prompt-v3: expected ``tool:x`` vs observed ``tooling:x`` is an exact hit after normalization.

    The first live eval scored raw exact 0/15 although the model asked the
    labelled facts under drifted slugs (``tooling:`` for ``tool:``/``technology:``,
    reordered tokens). Both sides now go through ``normalize_question_id``; the
    headline ``recall_normalized`` is what says an answer recorded on one
    posting's rendering of the id is found on another's. Raw exact stays 0 here.
    """

    row = harness.annotate_row(_stored_row(("tool:x", "technology:ml_lifecycle_tooling", "years:ml"), ("tooling:x", "tooling:lifecycle-ml")), "resume text")
    assert row["question_ids_normalized"] == ["tooling:x", "tooling:lifecycle_ml"]
    assert row["expected_hits_exact"] == []
    assert row["expected_hits_normalized"] == ["technology:ml_lifecycle_tooling", "tool:x"]
    assert row["expected_hits_category"] == ["technology:ml_lifecycle_tooling", "tool:x"]
    recall = harness.summarize([row], planned=1, max_calls=1)["question_recall"]
    assert (recall["expected_ids"], recall["hit_exact"], recall["hit_normalized"], recall["hit_category"]) == (3, 0, 2, 2)
    assert (recall["recall_exact"], recall["recall_normalized"], recall["recall_category"]) == (0.0, 0.6667, 0.6667)
    assert recall["per_row"][0]["observed_normalized"] == ["tooling:x", "tooling:lifecycle_ml"]


def test_rescore_reannotates_a_stored_report_without_any_call(tmp_path: Path) -> None:
    """``--rescore`` re-scores stored rows under the current code: no model, no Jev; the same rows round-trip."""

    first = _run(tmp_path, "--max-calls", "2")
    # A stored row with pre-v3 fields only (no normalized keys) and a drifted observed id.
    source = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    drifted = _stored_row(("tool:x",), ("tooling:x",))
    source["rows"].append(drifted)
    stored = tmp_path / "stored.json"
    stored.write_text(json.dumps(source), encoding="utf-8")
    out = tmp_path / "rescored.json"
    assert harness.main(["--rescore", str(stored), "--report", str(out), "--quiet"]) == 0
    rescored = json.loads(out.read_text(encoding="utf-8"))
    assert rescored["schema"] == first["schema"]
    assert rescored["run"]["rescored_from"]["instructions_digest"] == first["run"]["instructions_digest"]
    assert rescored["run"]["rescored_at"] and "rescored_head" in rescored["run"]
    assert rescored["rows"][:2] == first["rows"]  # unchanged rows re-annotate to themselves
    assert rescored["rows"][2]["expected_hits_normalized"] == ["tool:x"] and rescored["rows"][2]["expected_hits_exact"] == []
    recall = rescored["metrics"]["question_recall"]
    assert (recall["rows"], recall["hit_exact"], recall["hit_normalized"]) == (1, 0, 1)
    assert rescored["metrics"]["calls"] == first["metrics"]["calls"] | {"made": 3}
    assert not (tmp_path / "stored-rescored.json").exists()


def test_fake_jev_prefilter_ranks_every_fixture_posting_per_resume(tmp_path: Path) -> None:
    report = _run(tmp_path, "--clean-fit-only", "--max-calls", "2", "--with-jev", "--fake-jev")
    jev = report["metrics"]["jev"]
    assert jev["top_n"] == harness.DEFAULT_TOP_N and jev["postings_ranked"] == 15
    # The first two planned clean fits are the US ones: the Poland clean fit is excluded (US-only, 2026-09-25).
    assert set(jev["per_resume"]) == {"cf-senior-analytics-engineer-us", "cf-analytics-intern-sf"}
    for entry in jev["per_resume"].values():
        assert len(entry["ranking"]) == 15
        assert all(item["fit"] == "strong" and item["score"] == 89 for item in entry["ranking"])
        assert entry["cost_usd"] == pytest.approx(0.0075)
    # Every fake score ties at 89, so the top-N follows fixture order and the intern posting
    # (12th) sits outside it: check the pre-filter bookkeeping, not the tie order.
    checks = [check for entry in jev["per_resume"].values() for check in entry["labels"] if check["must_keep"]]
    assert jev["must_keep"] == len(checks) == 2
    assert all(check["in_top_n"] == (check["rank"] <= harness.DEFAULT_TOP_N) for check in checks)
    assert jev["kept"] == sum(check["in_top_n"] for check in checks) == 1 and jev["prefilter_rate"] == 0.5
    assert report["metrics"]["reliability"]["jev_cost_usd"] == pytest.approx(0.015)
    assert report["run"]["fake_jev"] is True


def test_dry_run_plans_rows_without_any_call(capsys: pytest.CaptureFixture[str]) -> None:
    assert harness.main(["--dry-run", "--max-calls", "4"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == len(harness.load_labels())
    assert lines[0].split()[1] == "call" and lines[0].split()[2] == "clean"
    assert lines[-1].split()[1] == "skip"
